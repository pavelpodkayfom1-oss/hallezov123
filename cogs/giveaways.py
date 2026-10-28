"""
Розыгрыши: /розыгрыш создать | завершить | реролл
Дизайн: Ultra-Premium, динамический прогресс-бар, визуальные эффекты завершения.
"""
import random
from typing import List, Optional
import asyncio
import io
import time
import disnake
from disnake.ext import commands, tasks
from utils import famq_core as core
from utils.event_art import make_event_art

BANNER_NAME = "giveaway_banner.jpg"
BAR_WIDTH = 15
REFRESH_SEC = 60
MAX_RANK_BONUS = 3
VOICE_STEP_HOURS = 20
MAX_VOICE_BONUS = 5

async def calc_tickets(member) -> dict:
    rank = core.member_rank(member)
    hours = await core.voice_seconds(member.id) / 3600
    rank_bonus = min(max(rank - 1, 0), MAX_RANK_BONUS)
    voice_bonus = min(int(hours // VOICE_STEP_HOURS), MAX_VOICE_BONUS)
    return {
        "total": 1 + rank_bonus + voice_bonus,
        "rank": rank, "rank_bonus": rank_bonus,
        "hours": hours, "voice_bonus": voice_bonus,
    }

async def entry_weights(guild, giveaway_id: int, exclude: Optional[List[int]] = None) -> dict:
    rows = await core.q_all("SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
    out = {}
    for r in rows:
        uid = r["user_id"]
        if exclude and uid in exclude: continue
        m = guild.get_member(uid) if guild else None
        if m and not m.bot:
            out[uid] = (await calc_tickets(m))["total"]
    return out

def weighted_pick(weights: dict, k: int) -> List[int]:
    pool = dict(weights)
    winners = []
    for _ in range(min(k, len(pool))):
        uids = list(pool)
        pick = random.choices(uids, weights=[pool[u] for u in uids], k=1)[0]
        winners.append(pick)
        pool.pop(pick)
    return winners

def create_progress_bar(start_ts: Optional[int], end_ts: int, ended: bool = False) -> str:
    if ended: return "🟢 " + "█" * BAR_WIDTH + " 100%"
    if start_ts is None: return "⏳ Ожидание..."
    now = core.now_ts()
    if now >= end_ts: return "🟢 " + "█" * BAR_WIDTH + " 100%"
    total = end_ts - start_ts
    elapsed = now - start_ts
    percent = max(0.0, min(1.0, elapsed / total))
    filled = int(percent * BAR_WIDTH)
    empty = BAR_WIDTH - filled
    return f"🔹 {'█' * filled}{'░' * empty} {int(percent * 100)}%"

def gw_embed(row: dict, count: int, winners: Optional[List[int]] = None, ended: bool = False,
             tickets: Optional[dict] = None, start_ts: Optional[int] = None, art_id: Optional[int] = None) -> disnake.Embed:
    color = 0xFFD700 if not ended else 0x2B2D31
    e = disnake.Embed(color=color)
    if not ended:
        e.set_author(name="💎 ПРЕМИУМ РОЗЫГРЫШ HALLEZ FAMQ 💎")
        prize = core.clip(row["prize"], 200)
        e.title = f"✨ {prize}"
        bar = create_progress_bar(start_ts, row["end_ts"])
        e.description = (
            f"**Присоединяйтесь к розыгрышу, чтобы забрать приз!**\n\n"
            f"🌟 **Прогресс:** `{bar}`\n"
            f"⏰ **Итоги:** <t:{row['end_ts']}:R> | <t:{row['end_ts']}:f>\n"
            f"👥 **Участников:** `{count}`\n"
            f"👤 **Организатор:** <@{row['host_id']}>"
        )
    else:
        e.set_author(name="🏁 РОЗЫГРЫШ ЗАВЕРШЕН")
        e.title = f"🏆 Победители: {core.clip(row['prize'], 200)}"
        e.description = f"Розыгрыш официально закрыт. <t:{row['end_ts']}:R>"
    if art_id:
        e.set_image(url=f"attachment://sbor_{art_id}.jpg")
    elif banner:
        e.set_image(url=f"attachment://{BANNER_NAME}")
    cond = []
    if row["min_rank"]: cond.append(f"🎖️ Ранг: **{row['min_rank']}**+")
    if row["min_voice_hours"]: cond.append(f"🎙️ Войс: **{row['min_voice_hours']}ч**+")
    e.add_field("📋 Требования", "\n".join(cond) if cond else "Без ограничений — участвуют все", inline=False)
    if ended:
        if winners:
            winner_list = "\n".join([f"🌟 <@{w}>" for w in winners])
            e.add_field("🎉 Победители", winner_list, inline=False)
        else:
            e.add_field("🎉 Победители", "Никто не участвовал 😔", inline=False)
        e.set_footer(text="Hallez FAMQ Luxury System")
    else:
        e.add_field("🎫 Как повысить шансы", "Ранг выше 1 -> +1 билет\nВойс каждые 20ч -> +1 билет\n\n*Нажми «Мои шансы», чтобы проверить свои билеты*", inline=False)
        e.set_footer(text="Нажми кнопку ниже, чтобы участвовать! 👇")
    return e

class GiveawayView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @disnake.ui.button(label="УЧАСТВОВАТЬ", emoji="💎", style=disnake.ButtonStyle.blurple, custom_id="gw:join")
    async def join(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        row = await core.q_one("SELECT * FROM giveaways WHERE message_id=?", (inter.message.id,))
        if not row or row["status"] != "active" or row["end_ts"] <= core.now_ts():
            return await inter.response.send_message("⏰ Розыгрыш уже завершён.", ephemeral=True)
        if row["min_rank"]:
            rank = core.member_rank(inter.author)
            if rank < row["min_rank"]:
                return await inter.response.send_message(f"❌ Нужен ранг {row['min_rank']} или выше!", ephemeral=True)
        if row["min_voice_hours"]:
            hours = await core.voice_seconds(inter.author.id) / 3600
            if hours < row["min_voice_hours"]:
                return await inter.response.send_message(f"❌ Нужно {row['min_voice_hours']}ч в войсе!", ephemeral=True)
        exists = await core.q_one("SELECT 1 FROM giveaway_entries WHERE giveaway_id=? AND user_id=?", (row["id"], inter.author.id))
        if exists:
            await core.q_exec("DELETE FROM giveaway_entries WHERE giveaway_id=? AND user_id=?", (row["id"], inter.author.id))
            text = "↩️ Вы вышли из розыгрыша."
        else:
            await core.q_exec("INSERT OR IGNORE INTO giveaway_entries (giveaway_id, user_id) VALUES (?, ?)", (row["id"], inter.author.id))
            t = await calc_tickets(inter.author)
            text = f"✅ Ты в деле! Твои билеты: **{t['total']}** 🎫"
        cnt = (await core.q_one("SELECT COUNT(*) AS c FROM giveaway_entries WHERE giveaway_id=?", (row["id"],)))["c"]
        await inter.response.edit_message(embed=gw_embed(row, cnt, start_ts=int(inter.message.created_at.timestamp()), art_id=row.get("art_id")))
        await inter.followup.send(text, ephemeral=True)

    @disnake.ui.button(label="Мои шансы", emoji="📊", style=disnake.ButtonStyle.secondary, custom_id="gw:odds")
    async def odds(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        row = await core.q_one("SELECT * FROM giveaways WHERE message_id=?", (inter.message.id,))
        if not row or row["status"] != "active": return await inter.response.send_message("⏰ Розыгрыш завершён.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        t = await calc_tickets(inter.author)
        weights = await entry_weights(inter.guild, row["id"])
        total_tickets = sum(weights.values())
        joined = await core.q_one("SELECT 1 FROM giveaway_entries WHERE giveaway_id=? AND user_id=?", (row["id"], inter.author.id))
        share = (t["total"] / total_tickets * 100) if total_tickets > 0 else 0
        e = core.make_embed("🎫 Твои шансы", kind="main")
        e.description = f"Статус: {'✅ Участвуешь' if joined else '❌ Не участвуешь'}\n\nТвои билеты: **{t['total']}**\nОбщее кол-во билетов: **{total_tickets}**\nШанс на победу: **{share:.2f}%**"
        await inter.edit_original_response(embed=e)

    @disnake.ui.button(label="Участники", emoji="👥", style=disnake.ButtonStyle.secondary, custom_id="gw:list")
    async def entrants(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        row = await core.q_one("SELECT * FROM giveaways WHERE message_id=?", (inter.message.id,))
        if not row: return await inter.response.send_message("ℹ️ Розыгрыш не найден.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        weights = await entry_weights(inter.guild, row["id"])
        e = core.make_embed(f"👥 Участники ({len(weights)})", kind="main")
        if weights:
            ordered = sorted(weights.items(), key=lambda x: -x[1])[:25]
            lines = [f"• <@{uid}> — 🎫 {n}" for uid, n in ordered]
            e.description = "\n".join(lines) + (f"\n...и ещё {len(weights)-25}" if len(weights)>25 else "")
        else: e.description = "Пока никто не участвует."
        await inter.edit_original_response(embed=e)

class GiveawayEndedView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @disnake.ui.button(label="Реролл", emoji="🔁", style=disnake.ButtonStyle.secondary, custom_id="gw:reroll")
    async def reroll(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not core.is_staff(inter.author): return await inter.response.send_message("❌ Нет доступа.", ephemeral=True)
        row = await core.q_one("SELECT * FROM giveaways WHERE message_id=?", (inter.message.id,))
        if not row or row["status"] != "ended": return await inter.response.send_message("ℹ️ Завершённый не найден.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        old = [int(x) for x in (row["winner_ids"] or "").split(",") if x.isdigit()]
        weights = await entry_weights(inter.guild, row["id"], exclude=old)
        new = weighted_pick(weights, 1)
        if not new: return await inter.edit_original_response(content="😔 Больше некого выбирать.")
        winner = new[0]
        all_winners = old + [winner]
        await core.q_exec("UPDATE giveaways SET winner_ids=? WHERE id=?", (",".join(map(str, all_winners)), row["id"]))
        channel = inter.guild.get_channel(row["channel_id"])
        if channel:
            cnt = (await core.q_one("SELECT COUNT(*) AS c FROM giveaway_entries WHERE giveaway_id=?", (row["id"],)))["c"]
            try:
                msg = await channel.fetch_message(row["message_id"])
                await msg.edit(embed=gw_embed(row, cnt, all_winners, ended=True, start_ts=int(msg.created_at.timestamp())), view=GiveawayEndedView())
            except Exception: pass
            await channel.send(f"🔁 **Реролл!** Новый победитель розыгрыша `{row['prize']}`: <@{winner}>! 🎉")
        await inter.edit_original_response(content=f"✅ Новый победитель: <@{winner}>")

class Giveaways(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._view_added = False
    def cog_unload(self):
        self.check_loop.cancel()
    @commands.Cog.listener()
    async def on_ready(self):
        if not self._view_added:
            self.bot.add_view(GiveawayView())
            self.bot.add_view(GiveawayEndedView())
            self._view_added = True
        if not self.check_loop.is_running():
            self.check_loop.start()
    async def finish(self, row: dict, winners: Optional[List[int]] = None, announce: bool = True):
        guild = self.bot.get_guild(row["guild_id"])
        if not guild: return
        if winners is None:
            weights = await entry_weights(guild, row["id"])
            winners = weighted_pick(weights, row["winners"])
        await core.q_exec("UPDATE giveaways SET status='ended', winner_ids=? WHERE id=?", (",".join(map(str, winners)), row["id"]))
        channel = guild.get_channel(row["channel_id"])
        if not channel: return
        cnt = (await core.q_one("SELECT COUNT(*) AS c FROM giveaway_entries WHERE giveaway_id=?", (row["id"],)))["c"]
        try:
            msg = await channel.fetch_message(row["message_id"])
            if announce:
                await channel.send("🥁 **Барабанная дробь...**")
                await asyncio.sleep(3)
            await msg.edit(embed=gw_embed(row, cnt, winners, ended=True, start_ts=int(msg.created_at.timestamp()), art_id=row.get("art_id")), view=GiveawayEndedView())
        except Exception: pass
        if announce:
            if winners:
                text = f"🎉 **ПОБЕДИТЕЛИ ОПРЕДЕЛЕНЫ!**\n\nСчастливчики: {' '.join(f'<@{w}>' for w in winners)}\n🎁 Приз: **{row['prize']}**\n\nПоздравляем, ебать! 🥂"
            else:
                text = f"😔 В розыгрыше **{row['prize']}** никто не участвовал. Скучно, бля."
            try: await channel.send(text, allowed_mentions=disnake.AllowedMentions(users=True))
            except Exception: pass
        await core.send_log(guild, "giveaway", core.make_embed("🎁 Розыгрыш завершён", f"**{row['prize']}**\nПобедители: {' '.join(f'<@{w}>' for w in winners) or '—'}", kind="ok"))
    @tasks.loop(seconds=30)
    async def check_loop(self):
        rows = await core.q_all("SELECT * FROM giveaways WHERE status='active' AND end_ts<=?", (core.now_ts(),))
        for row in rows:
            try: await self.finish(row)
            except Exception: await core.q_exec("UPDATE giveaways SET status='ended' WHERE id=?", (row["id"],))
    @check_loop.before_loop
    async def _b(self): await self.bot.wait_until_ready()
    @commands.slash_command(name="розыгрыш", description="🎁 Розыгрыши семьи")
    @commands.guild_only()
    async def giveaway(self, inter: disnake.ApplicationCommandInteraction): pass
    @giveaway.sub_command(name="создать", description="Запустить розыгрыш (руководство)")
    async def create(self, inter: disnake.ApplicationCommandInteraction, prize: str = commands.Param(name="приз", description="Приз", max_length=200), duration: str = commands.Param(name="время", description="Пример: 30m, 2h, 1d", default="1d"), winners: int = commands.Param(name="победителей", description="Кол-во", default=1, ge=1, le=20), min_rank: int = commands.Param(name="мин_ранг", description="Мин. ранг (0-все)", default=0, ge=0, le=10), min_hours: int = commands.Param(name="мин_часов_войса", description="Мин. часов", default=0, ge=0, le=10000), channel: Optional[disnake.TextChannel] = commands.Param(name="канал", description="Куда", default=None)):
        if not core.is_staff(inter.author): return await inter.response.send_message("❌ Только для руководства, ебать.", ephemeral=True)
        secs = core.parse_duration(duration)
        if not secs or secs < 30: return await inter.response.send_message("❌ Время слишком мало (мин 30с).", ephemeral=True)
        cid = int(core.get("giveaway.channel_id", 0) or 0)
        target = channel or (inter.guild.get_channel(cid) if cid else None) or inter.channel
        await inter.response.defer(ephemeral=True)
        end_ts = core.now_ts() + secs
        gid = await core.q_exec("INSERT INTO giveaways (guild_id, channel_id, host_id, prize, winners, min_rank, min_voice_hours, end_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (inter.guild.id, target.id, inter.author.id, prize, winners, min_rank, min_hours, end_ts))

        # AI Art Generation
        art = await make_event_art(title=prize, info=prize, guild_name=inter.guild.name)
        art_id = None
        if art.data:
            from utils.event_art import save_art
            save_art(gid, art.data)
            art_id = gid
            await core.q_exec("UPDATE giveaways SET art_id=? WHERE id=?", (art_id, gid))

        row = await core.q_one("SELECT * FROM giveaways WHERE id=?", (gid,))
        ping_role = int(core.get("giveaway.ping_role_id", 0) or 0)

        # Attach AI art file if available
        from utils.event_art import art_file
        attachment = art_file(gid) if art_id else None

        try:
            msg = await target.send(content=f"<@&{ping_role}>" if ping_role else None,
                                  embed=gw_embed(row, 0, start_ts=core.now_ts(), art_id=art_id),
                                  file=attachment,
                                  view=GiveawayView(),
                                  allowed_mentions=disnake.AllowedMentions(roles=True))
        except Exception as ex:
            await core.q_exec("DELETE FROM giveaways WHERE id=?", (gid,))
            return await inter.edit_original_response(f"❌ Ошибка: {ex}")
        await core.q_exec("UPDATE giveaways SET message_id=? WHERE id=?", (msg.id, gid))
        await inter.edit_original_response(f"✅ Розыгрыш **#{gid}** запущен! {msg.jump_url}")
    @giveaway.sub_command(name="завершить", description="Завершить досрочно")
    async def end_now(self, inter: disnake.ApplicationCommandInteraction, gid: int = commands.Param(name="номер", description="Номер #", ge=1)):
        if not core.is_staff(inter.author): return await inter.response.send_message("❌ Нет доступа.", ephemeral=True)
        row = await core.q_one("SELECT * FROM giveaways WHERE id=? AND guild_id=?", (gid, inter.guild.id))
        if not row or row["status"] != "active": return await inter.response.send_message("ℹ️ Активный не найден.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        await core.q_exec("UPDATE giveaways SET end_ts=? WHERE id=?", (core.now_ts(), gid))
        await self.finish(row)
        await inter.edit_original_response(f"✅ Розыгрыш #{gid} завершён.")
    @giveaway.sub_command(name="реролл", description="Новый победитель")
    async def reroll(self, inter: disnake.ApplicationCommandInteraction, gid: int = commands.Param(name="номер", description="Номер #", ge=1)):
        if not core.is_staff(inter.author): return await inter.response.send_message("❌ Нет доступа.", ephemeral=True)
        row = await core.q_one("SELECT * FROM giveaways WHERE id=? AND guild_id=?", (gid, inter.guild.id))
        if not row or row["status"] != "ended": return await inter.response.send_message("ℹ️ Завершённый не найден.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        old = [int(x) for x in (row["winner_ids"] or "").split(",") if x.isdigit()]
        weights = await entry_weights(inter.guild, row["id"], exclude=old)
        new = weighted_pick(weights, 1)
        if not new: return await inter.edit_original_response(content="😔 Больше некого выбирать.")
        winner = new[0]
        all_winners = old + [winner]
        await core.q_exec("UPDATE giveaways SET winner_ids=? WHERE id=?", (",".join(map(str, all_winners)), row["id"]))
        channel = inter.guild.get_channel(row["channel_id"])
        if channel:
            cnt = (await core.q_one("SELECT COUNT(*) AS c FROM giveaway_entries WHERE giveaway_id=?", (row["id"],)))["c"]
            try:
                msg = await channel.fetch_message(row["message_id"])
                await msg.edit(embed=gw_embed(row, cnt, all_winners, ended=True, start_ts=int(msg.created_at.timestamp())), view=GiveawayEndedView())
            except Exception: pass
            await channel.send(f"🔁 **Реролл!** Новый победитель розыгрыша `{row['prize']}`: <@{winner}>! 🎉")
        await inter.edit_original_response(content=f"✅ Новый победитель: <@{winner}>")

def setup(bot):
    bot.add_cog(Giveaways(bot))
