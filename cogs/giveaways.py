"""
Розыгрыши: /розыгрыш создать | завершить | реролл

Что внутри:
  • Условия участия: минимальный ранг и минимум часов в голосовых каналах.
  • Билеты: чем выше ранг и больше актив в войсе, тем больше шанс (честная лотерея с весами).
  • Кнопки на карточке: «Участвовать», «Мои шансы», «Участники».
  • Розыгрыш определяет победителей «барабаном»: сообщение на несколько секунд превращается в рулетку.
  • На итоговой карточке у руководства есть кнопка «Реролл» (выбрать нового победителя).
"""
import asyncio
import random
import time
from typing import Dict, List, Optional, Tuple

import disnake
from disnake.ext import commands, tasks

from utils import famq_core as core

# ───────── билеты ─────────
MAX_RANK_BONUS = 3        # +1 билет за каждый ранг выше первого, максимум +3
VOICE_STEP_HOURS = 25     # +1 билет за каждые 25 часов в войсе
MAX_VOICE_BONUS = 4       # максимум +4 за войс (итого до 8 билетов)

MEDALS = ["🥇", "🥈", "🥉"]
_CACHE_TTL = 15           # секунд: кэш билетов для кнопок «Мои шансы» и «Участники»
_weights_cache: Dict[int, Tuple[float, Dict[int, int]]] = {}


async def calc_tickets(member) -> dict:
    """Сколько билетов у участника сейчас и из чего они складываются."""
    rank = core.member_rank(member)
    hours = await core.voice_seconds(member.id) / 3600
    rank_bonus = min(max(rank - 1, 0), MAX_RANK_BONUS)
    voice_bonus = min(int(hours // VOICE_STEP_HOURS), MAX_VOICE_BONUS)
    return {
        "total": 1 + rank_bonus + voice_bonus,
        "rank": rank, "rank_bonus": rank_bonus,
        "hours": hours, "voice_bonus": voice_bonus,
    }


async def entry_weights(guild, giveaway_id: int, exclude: Optional[List[int]] = None,
                        use_cache: bool = False) -> Dict[int, int]:
    """{user_id: билетов} для всех, кто сейчас в розыгрыше и ещё на сервере."""
    if use_cache and not exclude:
        cached = _weights_cache.get(giveaway_id)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return dict(cached[1])
    rows = await core.q_all("SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
    out: Dict[int, int] = {}
    for r in rows:
        uid = r["user_id"]
        if exclude and uid in exclude:
            continue
        m = guild.get_member(uid) if guild else None
        if not m or m.bot:
            continue
        out[uid] = (await calc_tickets(m))["total"]
    if not exclude:
        _weights_cache[giveaway_id] = (time.monotonic(), dict(out))
    return out


def weighted_pick(weights: Dict[int, int], k: int) -> List[int]:
    """k разных победителей; шанс пропорционален числу билетов."""
    pool = dict(weights)
    winners: List[int] = []
    for _ in range(min(k, len(pool))):
        uids = list(pool)
        pick = random.choices(uids, weights=[pool[u] for u in uids], k=1)[0]
        winners.append(pick)
        pool.pop(pick)
    return winners


async def entry_count(giveaway_id: int) -> int:
    row = await core.q_one("SELECT COUNT(*) AS c FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
    return row["c"] if row else 0


# ───────── карточка ─────────

def gw_embed(row: dict, count: int, winners: Optional[List[int]] = None, ended: bool = False,
             tickets: Optional[Dict[int, int]] = None) -> disnake.Embed:
    prize = core.clip(row["prize"], 200)
    if ended:
        e = core.make_embed(f"🎁 {prize}", kind="ok")
        e.description = f"**Розыгрыш завершён** · <t:{row['end_ts']}:R>"
    else:
        e = core.make_embed(f"🎁 {prize}", kind="main")
        e.description = (f"**Нажмите «Участвовать», чтобы попасть в розыгрыш.**\n"
                         f"⏳ Итоги <t:{row['end_ts']}:R> · <t:{row['end_ts']}:f>")
    e.add_field("🏆 Победителей", f"**{row['winners']}**", inline=True)
    e.add_field("👥 Участников", f"**{count}**", inline=True)
    e.add_field("👤 Организатор", f"<@{row['host_id']}>", inline=True)

    cond = []
    if row["min_rank"]:
        cond.append(f"🎖️ Ранг не ниже **{row['min_rank']}** ({core.rank_name(row['min_rank'])})")
    if row["min_voice_hours"]:
        cond.append(f"🎙️ В войсе не менее **{row['min_voice_hours']} ч.**")
    e.add_field("Условия участия", "\n".join(cond) if cond else "Без ограничений — участвуют все", inline=False)

    if ended:
        lines = []
        for i, w in enumerate(winners or []):
            mark = MEDALS[i] if i < len(MEDALS) else f"**{i + 1}.**"
            extra = f" · 🎫 {tickets[w]}" if tickets and w in tickets else ""
            lines.append(f"{mark} <@{w}>{extra}")
        e.add_field("🎉 Победители", "\n".join(lines) if lines else "Никто не участвовал 😔", inline=False)
    else:
        e.add_field(
            "🎫 Как повысить шансы",
            f"Базово — 1 билет\n"
            f"+1 за каждый ранг выше первого (до +{MAX_RANK_BONUS})\n"
            f"+1 за каждые {VOICE_STEP_HOURS} ч в войсе (до +{MAX_VOICE_BONUS})\n"
            f"Кнопка «Мои шансы» покажет ваши билеты.",
            inline=False)
    return e


async def get_row(inter: disnake.MessageInteraction) -> Optional[dict]:
    return await core.q_one("SELECT * FROM giveaways WHERE message_id=?", (inter.message.id,))


# ───────── кнопки ─────────

class GiveawayView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="Участвовать", emoji="🎉", style=disnake.ButtonStyle.green, custom_id="gw:join")
    async def join(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        row = await get_row(inter)
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
            t = await calc_tickets(inter.author)
            text = (f"✅ Вы участвуете! Ваши билеты: **{t['total']}** 🎫\n"
                    f"Нажмите «Мои шансы», чтобы узнать подробности, или кнопку ещё раз, чтобы выйти.")
        _weights_cache.pop(row["id"], None)
        cnt = await entry_count(row["id"])
        await inter.response.edit_message(embed=gw_embed(row, cnt))
        await inter.followup.send(text, ephemeral=True)

    @disnake.ui.button(label="Мои шансы", emoji="🎫", style=disnake.ButtonStyle.secondary, custom_id="gw:odds")
    async def odds(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        row = await get_row(inter)
        if not row or row["status"] != "active":
            return await inter.response.send_message("⏰ Этот розыгрыш уже завершён.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        t = await calc_tickets(inter.author)
        joined = await core.q_one(
            "SELECT 1 AS x FROM giveaway_entries WHERE giveaway_id=? AND user_id=?", (row["id"], inter.author.id))
        weights = await entry_weights(inter.guild, row["id"], use_cache=True)
        others = sum(v for uid, v in weights.items() if uid != inter.author.id)
        share = t["total"] / (others + t["total"]) * 100

        e = core.make_embed("🎫 Ваши шансы", kind="main")
        e.description = ("Вы **участвуете** в розыгрыше." if joined
                         else "Вы пока **не участвуете**. Нажмите «Участвовать», чтобы попасть в розыгрыш.")
        e.add_field("Билеты", f"**{t['total']}**\n"
                              f"база 1 · ранг +{t['rank_bonus']} · войс +{t['voice_bonus']}", inline=True)
        e.add_field("Доля в жеребьёвке", f"**{share:.1f}%**", inline=True)
        e.add_field("Часов в войсе", f"**{t['hours']:.1f}**", inline=True)
        if t["voice_bonus"] < MAX_VOICE_BONUS:
            need = (t["voice_bonus"] + 1) * VOICE_STEP_HOURS - t["hours"]
            e.add_field("До следующего билета", f"ещё **{need:.1f} ч.** в войсе", inline=False)
        e.set_footer(text="Билеты считаются в момент розыгрыша: актив до конца времени тоже учитывается")
        await inter.edit_original_response(embed=e)

    @disnake.ui.button(label="Участники", emoji="👥", style=disnake.ButtonStyle.secondary, custom_id="gw:list")
    async def entrants(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        row = await get_row(inter)
        if not row:
            return await inter.response.send_message("ℹ️ Розыгрыш не найден.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        weights = await entry_weights(inter.guild, row["id"], use_cache=True)
        e = core.make_embed(f"👥 Участники · {len(weights)}", kind="main")
        if weights:
            ordered = sorted(weights.items(), key=lambda kv: -kv[1])[:25]
            lines = [f"<@{uid}> · 🎫 {n}" for uid, n in ordered]
            if len(weights) > 25:
                lines.append(f"…и ещё **{len(weights) - 25}**")
            e.description = "\n".join(lines)
        else:
            e.description = "Пока никто не участвует. Станьте первым!"
        await inter.edit_original_response(embed=e)


class GiveawayEndedView(disnake.ui.View):
    """Кнопка под итогами: только для руководства."""
    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="Реролл (руководство)", emoji="🔁", style=disnake.ButtonStyle.secondary,
                       custom_id="gw:reroll")
    async def reroll(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not core.is_staff(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        row = await get_row(inter)
        if not row or row["status"] != "ended":
            return await inter.response.send_message("ℹ️ Завершённый розыгрыш не найден.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        new = await do_reroll(inter.guild, row)
        if new is None:
            return await inter.edit_original_response(content="😔 Больше некого выбирать.")
        await inter.edit_original_response(content=f"✅ Новый победитель: <@{new}>")


async def do_reroll(guild: disnake.Guild, row: dict) -> Optional[int]:
    """Выбирает одного нового победителя (кроме прежних), обновляет карточку и объявляет."""
    old = [int(x) for x in (row["winner_ids"] or "").split(",") if x.isdigit()]
    weights = await entry_weights(guild, row["id"], exclude=old)
    new = weighted_pick(weights, 1)
    if not new:
        return None
    everyone = old + new
    await core.q_exec("UPDATE giveaways SET winner_ids=? WHERE id=?", (",".join(map(str, everyone)), row["id"]))
    channel = guild.get_channel(row["channel_id"])
    if channel:
        full = await entry_weights(guild, row["id"])
        cnt = await entry_count(row["id"])
        try:
            msg = await channel.fetch_message(row["message_id"])
            await msg.edit(embed=gw_embed(row, cnt, everyone, ended=True, tickets=full), view=GiveawayEndedView())
        except Exception:
            pass
        try:
            await channel.send(f"🔁 Новый победитель розыгрыша **{row['prize']}**: <@{new[0]}>! 🎉",
                               allowed_mentions=disnake.AllowedMentions(users=True))
        except Exception:
            pass
    return new[0]


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
            self.bot.add_view(GiveawayEndedView())
            self._view_added = True
        if not self.check_loop.is_running():
            self.check_loop.start()

    # ───────── завершение ─────────

    async def _reveal(self, msg: disnake.Message, guild: disnake.Guild, row: dict, weights: Dict[int, int]):
        """Короткая «рулетка»: карточка несколько раз меняет имя перед объявлением итогов."""
        names = []
        for uid in weights:
            m = guild.get_member(uid)
            if m:
                names.append(disnake.utils.escape_markdown(m.display_name))
        if len(names) < 2:
            return
        prize = core.clip(row["prize"], 200)
        for _ in range(4):
            e = core.make_embed(f"🎰 {prize}",
                                f"**Крутим барабан…**\n\n▸ **{core.clip(random.choice(names), 40)}** ◂",
                                kind="warn")
            try:
                await msg.edit(embed=e, view=None)
            except Exception:
                return
            await asyncio.sleep(1.4)

    async def finish(self, row: dict, winners: Optional[List[int]] = None, announce: bool = True):
        guild = self.bot.get_guild(row["guild_id"])
        weights = await entry_weights(guild, row["id"]) if guild else {}
        if winners is None:
            winners = weighted_pick(weights, row["winners"])
        await core.q_exec("UPDATE giveaways SET status='ended', winner_ids=? WHERE id=?",
                          (",".join(map(str, winners)), row["id"]))
        channel = guild.get_channel(row["channel_id"]) if guild else None
        if channel is None:
            return
        cnt = await entry_count(row["id"])
        try:
            msg = await channel.fetch_message(row["message_id"])
        except Exception:
            msg = None
        if msg:
            if announce:
                await self._reveal(msg, guild, row, weights)
            try:
                await msg.edit(embed=gw_embed(row, cnt, winners, ended=True, tickets=weights),
                               view=GiveawayEndedView())
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
        await inter.response.defer(ephemeral=True)
        new = await do_reroll(inter.guild, row)
        if new is None:
            return await inter.edit_original_response(content="😔 Больше некого выбирать.")
        await inter.edit_original_response(content=f"✅ Новый победитель: <@{new}>")


def setup(bot: commands.Bot):
    bot.add_cog(Giveaways(bot))
