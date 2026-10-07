"""
Розыгрыши: /розыгрыш создать | завершить | реролл
Условия участия: минимальный ранг (по ролям) и минимум часов в голосовых каналах.
Победители выбираются автоматически по истечении времени.
"""
import random
from typing import List, Optional

import disnake
from disnake.ext import commands, tasks

from utils import famq_core as core


def gw_embed(row: dict, count: int, winners: Optional[List[int]] = None, ended: bool = False) -> disnake.Embed:
    e = core.make_embed(f"🎁 Розыгрыш: {core.clip(row['prize'], 200)}", kind="ok" if ended else "main")
    lines = [f"🏆 Победителей: **{row['winners']}**",
             f"👤 Организатор: <@{row['host_id']}>",
             f"👥 Участников: **{count}**"]
    if ended:
        lines.append(f"⏰ Завершён <t:{row['end_ts']}:R>")
    else:
        lines.append(f"⏰ Итоги: <t:{row['end_ts']}:F> (<t:{row['end_ts']}:R>)")
    e.description = "\n".join(lines)
    cond = []
    if row["min_rank"]:
        cond.append(f"🎖️ Ранг не ниже **{row['min_rank']}** ({core.rank_name(row['min_rank'])})")
    if row["min_voice_hours"]:
        cond.append(f"🎙️ В войсе не менее **{row['min_voice_hours']} ч.**")
    e.add_field("Условия участия", "\n".join(cond) if cond else "Без ограничений — участвуют все", inline=False)
    if ended:
        e.add_field("🎉 Победители", " ".join(f"<@{w}>" for w in winners) if winners else "Никто не участвовал 😔",
                    inline=False)
    return e


class GiveawayView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="Участвовать", emoji="🎉", style=disnake.ButtonStyle.green, custom_id="gw:join")
    async def join(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        row = await core.q_one("SELECT * FROM giveaways WHERE message_id=?", (inter.message.id,))
        if not row or row["status"] != "active" or row["end_ts"] <= core.now_ts():
            return await inter.response.send_message("⏰ Этот розыгрыш уже завершён.", ephemeral=True)
        if row["min_rank"]:
            rank = core.member_rank(inter.author)
            if rank < row["min_rank"]:
                return await inter.response.send_message(
                    f"❌ Нужен ранг **{row['min_rank']}** ({core.rank_name(row['min_rank'])}) или выше. "
                    f"Ваш ранг: **{rank}**.", ephemeral=True)
        if row["min_voice_hours"]:
            hours = await core.voice_seconds(inter.author.id) / 3600
            if hours < row["min_voice_hours"]:
                return await inter.response.send_message(
                    f"❌ Нужно минимум **{row['min_voice_hours']} ч.** в голосовых каналах. "
                    f"У вас: **{hours:.1f} ч.**", ephemeral=True)
        exists = await core.q_one(
            "SELECT 1 AS x FROM giveaway_entries WHERE giveaway_id=? AND user_id=?", (row["id"], inter.author.id))
        if exists:
            await core.q_exec("DELETE FROM giveaway_entries WHERE giveaway_id=? AND user_id=?",
                              (row["id"], inter.author.id))
            text = "↩️ Вы вышли из розыгрыша."
        else:
            await core.q_exec("INSERT OR IGNORE INTO giveaway_entries (giveaway_id, user_id) VALUES (?, ?)",
                              (row["id"], inter.author.id))
            text = "✅ Вы участвуете! Нажмите ещё раз, чтобы выйти."
        cnt = (await core.q_one("SELECT COUNT(*) AS c FROM giveaway_entries WHERE giveaway_id=?", (row["id"],)))["c"]
        await inter.response.edit_message(embed=gw_embed(row, cnt))
        await inter.followup.send(text, ephemeral=True)


class Giveaways(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._view_added = False

    def cog_unload(self):
        self.check_loop.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._view_added:
            self.bot.add_view(GiveawayView())
            self._view_added = True
        if not self.check_loop.is_running():
            self.check_loop.start()

    # ───────── завершение ─────────

    async def _pick(self, row: dict, exclude: Optional[List[int]] = None) -> List[int]:
        guild = self.bot.get_guild(row["guild_id"])
        entries = [r["user_id"] for r in await core.q_all(
            "SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (row["id"],))]
        pool = []
        for uid in entries:
            if exclude and uid in exclude:
                continue
            m = guild.get_member(uid) if guild else None
            if m and not m.bot:
                pool.append(uid)
        return random.sample(pool, min(row["winners"], len(pool))) if pool else []

    async def finish(self, row: dict, winners: Optional[List[int]] = None, announce: bool = True):
        if winners is None:
            winners = await self._pick(row)
        await core.q_exec("UPDATE giveaways SET status='ended', winner_ids=? WHERE id=?",
                          (",".join(map(str, winners)), row["id"]))
        guild = self.bot.get_guild(row["guild_id"])
        channel = guild.get_channel(row["channel_id"]) if guild else None
        if channel is None:
            return
        cnt = (await core.q_one("SELECT COUNT(*) AS c FROM giveaway_entries WHERE giveaway_id=?", (row["id"],)))["c"]
        try:
            msg = await channel.fetch_message(row["message_id"])
            await msg.edit(embed=gw_embed(row, cnt, winners, ended=True), view=None)
        except Exception:
            pass
        if announce:
            if winners:
                text = f"🎉 Поздравляем {' '.join(f'<@{w}>' for w in winners)}! Приз: **{row['prize']}**"
            else:
                text = f"😔 В розыгрыше **{row['prize']}** никто не участвовал."
            try:
                await channel.send(text, allowed_mentions=disnake.AllowedMentions(users=True))
            except Exception:
                pass
        await core.send_log(guild, "giveaway", core.make_embed(
            "🎁 Розыгрыш завершён",
            f"**{row['prize']}**\nПобедители: {' '.join(f'<@{w}>' for w in winners) or '—'}", kind="ok"))

    @tasks.loop(seconds=20)
    async def check_loop(self):
        rows = await core.q_all("SELECT * FROM giveaways WHERE status='active' AND end_ts<=?", (core.now_ts(),))
        for row in rows:
            try:
                await self.finish(row)
            except Exception:
                await core.q_exec("UPDATE giveaways SET status='ended' WHERE id=?", (row["id"],))

    @check_loop.before_loop
    async def _b(self):
        await self.bot.wait_until_ready()

    # ───────── команды ─────────

    @commands.slash_command(name="розыгрыш", description="🎁 Розыгрыши семьи")
    @commands.guild_only()
    async def giveaway(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @giveaway.sub_command(name="создать", description="Запустить розыгрыш (руководство)")
    async def create(
        self, inter: disnake.ApplicationCommandInteraction,
        prize: str = commands.Param(name="приз", description="Что разыгрываем", max_length=200),
        duration: str = commands.Param(name="длительность", description="Например: 30m, 2h, 1d, 1d12h",
                                       default="1d"),
        winners: int = commands.Param(name="победителей", description="Сколько победителей", default=1, ge=1, le=20),
        min_rank: int = commands.Param(name="мин_ранг", description="Минимальный ранг (0 — без ограничений)",
                                       default=0, ge=0, le=10),
        min_hours: int = commands.Param(name="мин_часов_войса", description="Минимум часов в войсе (0 — нет)",
                                        default=0, ge=0, le=10000),
        channel: Optional[disnake.TextChannel] = commands.Param(name="канал", description="Куда отправить",
                                                                default=None),
    ):
        if not core.is_staff(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        secs = core.parse_duration(duration)
        if not secs or secs < 30 or secs > 60 * 86400:
            return await inter.response.send_message(
                "❌ Неверная длительность. Примеры: `30m`, `2h`, `1d`, `1d12h` (от 30 сек до 60 дней).",
                ephemeral=True)
        cid = int(core.get("giveaway.channel_id", 0) or 0)
        target = channel or (inter.guild.get_channel(cid) if cid else None) or inter.channel
        await inter.response.defer(ephemeral=True)
        end_ts = core.now_ts() + secs
        gid = await core.q_exec(
            "INSERT INTO giveaways (guild_id, channel_id, host_id, prize, winners, min_rank, min_voice_hours, end_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (inter.guild.id, target.id, inter.author.id, prize, winners, min_rank, min_hours, end_ts))
        row = await core.q_one("SELECT * FROM giveaways WHERE id=?", (gid,))
        ping_role = int(core.get("giveaway.ping_role_id", 0) or 0)
        try:
            msg = await target.send(
                content=f"<@&{ping_role}>" if ping_role else None,
                embed=gw_embed(row, 0), view=GiveawayView(),
                allowed_mentions=disnake.AllowedMentions(roles=True))
        except Exception as ex:
            await core.q_exec("DELETE FROM giveaways WHERE id=?", (gid,))
            return await inter.edit_original_response(f"❌ Не удалось отправить в {target.mention}: {ex}")
        await core.q_exec("UPDATE giveaways SET message_id=? WHERE id=?", (msg.id, gid))
        await inter.edit_original_response(f"✅ Розыгрыш **#{gid}** запущен: {msg.jump_url}")

    @giveaway.sub_command(name="завершить", description="Завершить розыгрыш досрочно")
    async def end_now(
        self, inter: disnake.ApplicationCommandInteraction,
        gid: int = commands.Param(name="номер", description="Номер розыгрыша (#)", ge=1),
    ):
        if not core.is_staff(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        row = await core.q_one("SELECT * FROM giveaways WHERE id=? AND guild_id=?", (gid, inter.guild.id))
        if not row or row["status"] != "active":
            return await inter.response.send_message("ℹ️ Активный розыгрыш с таким номером не найден.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        row["end_ts"] = core.now_ts()
        await core.q_exec("UPDATE giveaways SET end_ts=? WHERE id=?", (row["end_ts"], gid))
        await self.finish(row)
        await inter.edit_original_response(f"✅ Розыгрыш #{gid} завершён.")

    @giveaway.sub_command(name="реролл", description="Выбрать нового победителя")
    async def reroll(
        self, inter: disnake.ApplicationCommandInteraction,
        gid: int = commands.Param(name="номер", description="Номер завершённого розыгрыша", ge=1),
    ):
        if not core.is_staff(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        row = await core.q_one("SELECT * FROM giveaways WHERE id=? AND guild_id=?", (gid, inter.guild.id))
        if not row or row["status"] != "ended":
            return await inter.response.send_message("ℹ️ Завершённый розыгрыш не найден.", ephemeral=True)
        old = [int(x) for x in (row["winner_ids"] or "").split(",") if x.isdigit()]
        new = await self._pick({**row, "winners": 1}, exclude=old)
        if not new:
            return await inter.response.send_message("😔 Больше некого выбирать.", ephemeral=True)
        await core.q_exec("UPDATE giveaways SET winner_ids=? WHERE id=?", (",".join(map(str, old + new)), gid))
        channel = inter.guild.get_channel(row["channel_id"])
        if channel:
            await channel.send(f"🔁 Новый победитель розыгрыша **{row['prize']}**: <@{new[0]}>! 🎉",
                               allowed_mentions=disnake.AllowedMentions(users=True))
        await inter.response.send_message(f"✅ Новый победитель: <@{new[0]}>", ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(Giveaways(bot))
