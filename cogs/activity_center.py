import disnake
from disnake.ext import commands
from typing import Optional, List, Dict, Any
import time

from utils.checks import load_config, is_admin
from utils.embeds import base_embed, note_embed, success_embed
try:
    from emoji_manager import render_emojis
except ImportError:
    try:
        from cogs.emoji_manager import render_emojis
    except ImportError:
        def render_emojis(text): return text

from database import get_member, upsert_member
from utils.famq_core import voice_seconds

class ActivityCenterView(disnake.ui.View):
    def __init__(self, bot, guild, candidates: List[Dict[str, Any]], index: int = 0):
        super().__init__(timeout=600)
        self.bot = bot
        self.guild = guild
        self.candidates = candidates
        self.index = index
        self.update_embed()

    def update_embed(self):
        if not self.candidates:
            return

        member_data = self.candidates[self.index]
        uid = member_data["user_id"]
        member = self.guild.get_member(uid)

        title = render_emojis("{e:crown} Центр Активности: Проверка")

        now = int(time.time())
        v7 = voice_seconds(uid, now - 7 * 86400)
        v30 = voice_seconds(uid, now - 30 * 86400)

        joined_ts = member_data.get("joined_at")
        days_in_fam = "—"
        if joined_ts:
            try:
                if isinstance(joined_ts, str):
                    import datetime
                    joined_dt = datetime.datetime.strptime(joined_ts[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
                    joined_ts = joined_dt.timestamp()
                days_in_fam = f"{(now - joined_ts) // 86400} дн."
            except:
                days_in_fam = "—"

        def fmt(sec):
            return f"{sec // 3600}ч {sec % 3600 // 60}м"

        # Интеграция критериев повышения
        rank = member_data.get('rank', 1)
        criteria_status = ""

        # Критерий 2 -> 3 ранг (14 дней)
        if rank == 2:
            days_val = 0
            try:
                if isinstance(joined_ts, str):
                    import datetime
                    joined_dt = datetime.datetime.strptime(joined_ts[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
                    joined_ts = joined_dt.timestamp()
                days_val = (now - joined_ts) // 86400
            except:
                pass

            check_mark = "✅" if days_val >= 14 else "❌"
            criteria_status = f"📝 **Критерий (2➔3):** {days_in_fam} (Нужно 14дн.) {check_mark}"
        elif rank == 1:
            criteria_status = "📝 **Критерий (1➔2):** Требуется смена фамилии на Hallez FAMQ (скриншот)"
        elif rank == 3:
            criteria_status = "📝 **Критерий (3➔4):** Индивидуальные нормативы руководства"
        else:
            criteria_status = "🎖️ Максимальный ранг достигнут."

        mention = member.mention if member else f"<@{uid}>"
        desc = (
            f"👤 **Участник:** {mention}\n"
            f"🎖️ **Текущий ранг:** {rank}\n\n"
            f"🎙️ **Актив за 7 дней:** `{fmt(v7)}`\n"
            f"🎙️ **Актив за 30 дней:** `{fmt(v30)}`\n"
            f"📅 **В семье:** {days_in_fam}\n"
            f"{criteria_status}\n\n"
            f"📌 *Кандидат {self.index + 1} из {len(self.candidates)}*"
        )

        emb = base_embed(title, desc, guild=self.guild)
        self.embed = emb

    @disnake.ui.button(label="Повысить", style=disnake.ButtonStyle.success, emoji="✅")
    async def promote_btn(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not is_admin(inter.author):
            return await inter.response.send_message(embed=note_embed("❌ Нет доступа."), ephemeral=True)

        member_data = self.candidates[self.index]
        uid = member_data["user_id"]
        member = self.guild.get_member(uid)
        if not member:
            return await inter.response.send_message(embed=note_embed("❌ Участник не найден на сервере."), ephemeral=True)

        await inter.response.defer(ephemeral=True)

        promo_cog = self.bot.get_cog("Promotions")
        if promo_cog:
            target_rank = member_data.get('rank', 1) + 1
            cfg = load_config()
            max_r = max((int(k) for k in cfg.get("ranks", {})), default=1)
            if target_rank > max_r:
                return await inter.edit_original_response(content="❌ Это уже максимальный ранг!")

            msg, approved_emb = await promo_cog.execute_promotion(member, target_rank, inter.author)

            log_channel_id = cfg.get("channels", {}).get("logs_channel_id")
            if log_channel_id:
                log_ch = self.bot.get_channel(log_channel_id)
                if log_ch:
                    await log_ch.send(embed=approved_emb)

            await inter.edit_original_response(content=msg)
        else:
            await inter.edit_original_response(content="❌ Ошибка: Система повышений не загружена.")

    @disnake.ui.button(label="Позже", style=disnake.ButtonStyle.secondary, emoji="⏳")
    async def later_btn(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not is_admin(inter.author):
            return await inter.response.send_message(embed=note_embed("❌ Нет доступа."), ephemeral=True)

        self.index += 1
        if self.index >= len(self.candidates):
            await inter.response.edit_message(content="✅ Все подходящие кандидаты просмотрены!", embed=None, view=None)
        else:
            self.update_embed()
            await inter.response.edit_message(embed=self.embed, view=self)

class ActivityCenter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def get_candidates(self, guild):
        sync_cog = self.bot.get_cog("FamilySync")
        if not sync_cog:
            return []
        return await sync_cog.build_candidates(guild)

    @commands.slash_command(name="activity_check", description="Проверка активности состава (Для руководства)")
    async def activity_check(self, inter: disnake.ApplicationCommandInteraction):
        if not is_admin(inter.author):
            return await inter.response.send_message(embed=note_embed("❌ Нет доступа."), ephemeral=True)

        await inter.response.defer(ephemeral=True)
        candidates = await self.get_candidates(inter.guild)

        if not candidates:
            return await inter.edit_original_response(content="🔎 Сейчас нет участников, подходящих под критерии повышения.")

        view = ActivityCenterView(self.bot, inter.guild, candidates)
        await inter.edit_original_response(embed=view.embed, view=view)

    @commands.slash_command(name="for_improve", description="Показать список на повышение по активу (Для руководства)")
    async def for_improve(self, inter: disnake.ApplicationCommandInteraction):
        if not is_admin(inter.author):
            return await inter.response.send_message(embed=note_embed("❌ Нет доступа."), ephemeral=True)

        await inter.response.defer(ephemeral=True)
        candidates = await self.get_candidates(inter.guild)

        if not candidates:
            return await inter.edit_original_response(content="🔎 Нет подходящих кандидатов для повышения.")

        view = ActivityCenterView(self.bot, inter.guild, candidates)
        await inter.edit_original_response(embed=view.embed, view=view)

def setup(bot):
    bot.add_cog(ActivityCenter(bot))
