"""
cogs/famq_control.py — контроль семьи Hall FAMQ.

Что внутри:
  • /card        — карточка участника с кнопками Повысить / Понизить / Выговор / Уволить
  • /warn        — выговоры (give / list / remove), срок действия, автоуведомление руководству
  • /blacklist   — чёрный список (на базе таблицы fired_members) + авто-проверка новых анкет
  • /livestats   — самообновляющееся сообщение со статистикой семьи
  • /voice       — учёт голосового онлайна (топ недели / месяца / всё время)
  • /recruiters  — статистика рекрутеров

Модуль самодостаточен: сам создаёт недостающие таблицы и не меняет чужие cogs.
"""
import asyncio
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite
import disnake
from disnake.ext import commands, tasks

from database import DB_PATH, get_member, remove_warn, upsert_member
from utils.checks import load_config

# ───────────────────────── Настройки ─────────────────────────
WARN_LIMIT = 3              # при стольких активных выговорах уведомляем руководство
WARN_DEFAULT_DAYS = 30      # срок действия выговора по умолчанию (0 = бессрочно)
STATS_REFRESH_MIN = 5       # как часто обновлять живую статистику
VOICE_FLUSH_MIN = 5         # как часто сбрасывать текущие голосовые сессии в БД
BLACKLIST_POLL_SEC = 30     # как часто проверять новые анкеты по чёрному списку
CARD_TIMEOUT_SEC = 300      # сколько живут кнопки карточки

PERIODS = {"Неделя": "week", "Месяц": "month", "Всё время": "all"}
PERIOD_LABEL = {"week": "за 7 дней", "month": "за 30 дней", "all": "за всё время"}
KIND_LABEL = {"fired": "🚫 уволен", "left": "🚪 покинул семью", "blacklist": "⛔ добавлен в ЧС"}
MEDALS = ["🥇", "🥈", "🥉"]


# ───────────────────────── Утилиты ─────────────────────────
def now_ts() -> int:
    return int(time.time())


def period_since(period: str) -> int:
    day = 86400
    return {"week": now_ts() - 7 * day, "month": now_ts() - 30 * day, "all": 0}[period]


def ts_to_db(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_db_ts(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        return int(datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    except (ValueError, TypeError):
        return None


def fmt_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}ч {seconds % 3600 // 60}м"


def _color(cfg: dict, key: str, default: int) -> int:
    try:
        return int(str(cfg.get(key, default)), 16)
    except (ValueError, TypeError):
        return default


def _footer(embed: disnake.Embed) -> disnake.Embed:
    cfg = load_config()
    text = cfg.get("texts", {}).get("footer_text", "{bot_name}")
    embed.set_footer(text=text.replace("{bot_name}", cfg.get("bot_name", "Hall FAMQ")))
    return embed


def rank_name(cfg: dict, rank: Optional[int]) -> str:
    if rank is None:
        return "—"
    return cfg.get("ranks", {}).get(str(rank), {}).get("name", f"Ранг {rank}")


def is_admin(member) -> bool:
    cfg = load_config()
    if member.id in cfg.get("admin_user_ids", []):
        return True
    if isinstance(member, disnake.Member):
        if member.guild_permissions.administrator:
            return True
        ids = {r.id for r in member.roles}
        return bool(ids & set(cfg.get("admin_role_ids", [])))
    return False


def is_staff(member) -> bool:
    if is_admin(member):
        return True
    cfg = load_config()
    if isinstance(member, disnake.Member):
        ids = {r.id for r in member.roles}
        return bool(ids & set(cfg.get("recruiter_role_ids", [])))
    return False


async def deny(inter: disnake.Interaction):
    msg = "❌ Недостаточно прав для этого действия."
    if inter.response.is_done():
        await inter.followup.send(msg, ephemeral=True)
    else:
        await inter.response.send_message(msg, ephemeral=True)


# ───────────────────────── БД ─────────────────────────
_tables_ready = False


async def ensure_tables():
    global _tables_ready
    if _tables_ready:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("PRAGMA table_info(warns)")
        cols = [r[1] for r in await cur.fetchall()]
        if "expires_at" not in cols:
            await db.execute("ALTER TABLE warns ADD COLUMN expires_at INTEGER")

        # Эти две таблицы обычно создаёт cogs.hall_tools; дублируем схему на случай, если её ещё нет.
        await db.execute("""
        CREATE TABLE IF NOT EXISTS fired_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            nick TEXT, static_id TEXT, rank INTEGER, joined_at TIMESTAMP,
            kind TEXT NOT NULL, reason TEXT, admin_id INTEGER,
            fired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS leaves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            until_ts INTEGER NOT NULL,
            reason TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            message_id INTEGER, reviewer_id INTEGER, verdict_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS voice_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            start_ts INTEGER NOT NULL,
            end_ts INTEGER NOT NULL
        )""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_voice_user ON voice_sessions(user_id, end_ts)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_voice_end ON voice_sessions(end_ts)")
        await db.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS bl_checked (app_id INTEGER PRIMARY KEY)")
        await db.commit()
    _tables_ready = True


async def q_all(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql, params)
        return [dict(r) for r in await cur.fetchall()]


async def q_one(sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    rows = await q_all(sql, params)
    return rows[0] if rows else None


async def q_exec(sql: str, params: tuple = ()) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(sql, params)
        await db.commit()
        return cur.lastrowid if cur.lastrowid else cur.rowcount


async def kv_get(key: str) -> Optional[str]:
    row = await q_one("SELECT value FROM kv WHERE key = ?", (key,))
    return row["value"] if row else None


async def kv_set(key: str, value: str):
    await q_exec("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


async def kv_del(key: str):
    await q_exec("DELETE FROM kv WHERE key = ?", (key,))


# ── выговоры ──
async def db_add_warn(user_id: int, admin_id: int, reason: str, days: int) -> int:
    expires = now_ts() + days * 86400 if days > 0 else None
    return await q_exec(
        "INSERT INTO warns (user_id, admin_id, reason, expires_at) VALUES (?, ?, ?, ?)",
        (user_id, admin_id, reason, expires),
    )


async def db_active_warns(user_id: int) -> List[Dict[str, Any]]:
    return await q_all(
        "SELECT id, admin_id, reason, created_at, expires_at FROM warns "
        "WHERE user_id = ? AND active = 1 AND (expires_at IS NULL OR expires_at > ?) ORDER BY id DESC",
        (user_id, now_ts()),
    )


async def db_expire_warns() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE warns SET active = 0 WHERE active = 1 AND expires_at IS NOT NULL AND expires_at <= ?",
            (now_ts(),),
        )
        await db.commit()
        return cur.rowcount


# ── голос ──
async def db_voice_total(user_id: int, since: int) -> int:
    row = await q_one(
        "SELECT COALESCE(SUM(end_ts - MAX(start_ts, ?)), 0) AS s FROM voice_sessions WHERE user_id = ? AND end_ts > ?",
        (since, user_id, since),
    )
    return int(row["s"]) if row else 0


# ── рекрутеры ──
async def db_recruiter_rows(since: int) -> List[Dict[str, Any]]:
    return await q_all(
        "SELECT reviewer_id, "
        "SUM(CASE WHEN status = 'accepted' THEN 1 ELSE 0 END) AS accepted, "
        "SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected "
        "FROM applications "
        "WHERE status IN ('accepted', 'rejected') AND reviewer_id IS NOT NULL AND closed_at >= ? "
        "GROUP BY reviewer_id ORDER BY (accepted + rejected) DESC, accepted DESC",
        (ts_to_db(since),),
    )


# ── чёрный список ──
async def db_blacklist_find(static_id: str, user_id: int = 0) -> List[Dict[str, Any]]:
    static_id = (static_id or "").strip()
    return await q_all(
        "SELECT * FROM fired_members WHERE (? != '' AND TRIM(static_id) = ?) OR (? != 0 AND user_id = ?) ORDER BY id DESC",
        (static_id, static_id, user_id, user_id),
    )


# ───────────────────────── Модалки и вью карточки ─────────────────────────
class WarnModal(disnake.ui.Modal):
    def __init__(self, cog: "FamqControl", view: "CardView"):
        self.cog, self.view = cog, view
        super().__init__(
            title="Выдать выговор",
            custom_id=f"famq_warn_modal_{view.target_id}",
            components=[
                disnake.ui.TextInput(label="Причина", custom_id="reason", style=disnake.TextInputStyle.paragraph,
                                     max_length=500, required=True),
                disnake.ui.TextInput(label="Срок в днях (0 = бессрочно)", custom_id="days",
                                     value=str(WARN_DEFAULT_DAYS), max_length=3, required=True),
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        await inter.response.defer(ephemeral=True)
        reason = inter.text_values["reason"].strip()
        days_raw = inter.text_values["days"].strip()
        if not days_raw.isdigit():
            await inter.followup.send("❌ Срок должен быть числом (0 — бессрочно).", ephemeral=True)
            return
        count = await self.cog.issue_warn(inter.guild, self.view.target_id, inter.author, reason, int(days_raw))
        await inter.followup.send(f"✅ Выговор выдан. Активных выговоров: **{count}/{WARN_LIMIT}**.", ephemeral=True)
        await self.view.refresh()


class FireModal(disnake.ui.Modal):
    def __init__(self, cog: "FamqControl", view: "CardView"):
        self.cog, self.view = cog, view
        super().__init__(
            title="Уволить из семьи",
            custom_id=f"famq_fire_modal_{view.target_id}",
            components=[
                disnake.ui.TextInput(label="Причина увольнения (попадёт в ЧС)", custom_id="reason",
                                     style=disnake.TextInputStyle.paragraph, max_length=500, required=True),
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        await inter.response.defer(ephemeral=True)
        ok = await self.cog.fire_member(inter.guild, self.view.target_id, inter.author, inter.text_values["reason"].strip())
        if not ok:
            await inter.followup.send("❌ Участник уже не числится в составе.", ephemeral=True)
        else:
            await inter.followup.send("✅ Участник уволен и добавлен в чёрный список.", ephemeral=True)
        await self.view.refresh()


class CardView(disnake.ui.View):
    def __init__(self, cog: "FamqControl", base_inter: disnake.Interaction, target_id: int):
        super().__init__(timeout=CARD_TIMEOUT_SEC)
        self.cog, self.base_inter, self.target_id = cog, base_inter, target_id

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:
        if inter.author.id != self.base_inter.author.id or not is_admin(inter.author):
            await deny(inter)
            return False
        return True

    async def refresh(self):
        row = await get_member(self.target_id)
        try:
            if row is None:
                emb = disnake.Embed(title="Участник не числится в составе", color=_color(load_config(), "warning_color", 0xF39C12))
                await self.base_inter.edit_original_response(embed=_footer(emb), view=None)
                self.stop()
            else:
                emb = await self.cog.build_card(self.base_inter.guild, row)
                await self.base_inter.edit_original_response(embed=emb, view=self)
        except disnake.HTTPException:
            pass

    @disnake.ui.button(label="Повысить", emoji="⬆️", style=disnake.ButtonStyle.success)
    async def promote(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer()
        msg = await self.cog.change_rank(inter.guild, self.target_id, inter.author, +1)
        await inter.followup.send(msg, ephemeral=True)
        await self.refresh()

    @disnake.ui.button(label="Понизить", emoji="⬇️", style=disnake.ButtonStyle.secondary)
    async def demote(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer()
        msg = await self.cog.change_rank(inter.guild, self.target_id, inter.author, -1)
        await inter.followup.send(msg, ephemeral=True)
        await self.refresh()

    @disnake.ui.button(label="Выговор", emoji="⚠️", style=disnake.ButtonStyle.primary)
    async def warn(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.send_modal(WarnModal(self.cog, self))

    @disnake.ui.button(label="Уволить", emoji="🚫", style=disnake.ButtonStyle.danger)
    async def fire(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.send_modal(FireModal(self.cog, self))


# ───────────────────────── Cog ─────────────────────────
class FamqControl(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # user_id -> (start_ts, channel_id) для тех, кто сейчас сидит в голосовых каналах семьи
        self.open_voice: Dict[int, Tuple[int, int]] = {}
        self.stats_loop.start()
        self.voice_loop.start()
        self.blacklist_loop.start()
        self.housekeeping_loop.start()

    def cog_unload(self):
        for loop in (self.stats_loop, self.voice_loop, self.blacklist_loop, self.housekeeping_loop):
            loop.cancel()
        try:
            asyncio.ensure_future(self._flush_voice(final=True))
        except RuntimeError:
            pass

    async def cog_before_slash_command(self, inter: disnake.ApplicationCommandInteraction):
        await ensure_tables()

    # ── общие хелперы ──
    def _vc_ids(self) -> set:
        return {int(v["id"]) for v in load_config().get("voice_channels", [])}

    async def log_event(self, embed: disnake.Embed, ping_admins: bool = False):
        cfg = load_config()
        cid = cfg.get("channels", {}).get("logs_channel_id")
        ch = self.bot.get_channel(cid) if cid else None
        if ch is None:
            return
        content = None
        if ping_admins:
            content = " ".join(f"<@&{r}>" for r in cfg.get("admin_role_ids", [])) or None
        try:
            await ch.send(content=content, embed=_footer(embed),
                          allowed_mentions=disnake.AllowedMentions(roles=True, users=False))
        except disnake.HTTPException:
            pass

    async def search_members(self, query: str) -> List[Dict[str, Any]]:
        q = query.strip()
        m = re.fullmatch(r"<@!?(\d+)>", q)
        if m or (q.isdigit() and len(q) >= 17):
            row = await get_member(int(m.group(1) if m else q))
            return [row] if row else []
        rows = await q_all("SELECT * FROM family_members")
        exact = [r for r in rows if str(r["static_id"]).strip() == q]
        if exact:
            return exact
        needle = q.casefold()
        return [r for r in rows if needle in str(r["nick"]).casefold()][:10]

    async def voice_seconds(self, user_id: int, since: int) -> int:
        total = await db_voice_total(user_id, since)
        open_s = self.open_voice.get(user_id)
        if open_s:
            total += max(0, now_ts() - max(open_s[0], since))
        return total

    async def voice_top(self, since: int, limit: int = 10) -> List[Tuple[int, int]]:
        rows = await q_all(
            "SELECT user_id, SUM(end_ts - MAX(start_ts, ?)) AS sec FROM voice_sessions WHERE end_ts > ? GROUP BY user_id",
            (since, since),
        )
        totals = {r["user_id"]: int(r["sec"]) for r in rows}
        now = now_ts()
        for uid, (start, _) in self.open_voice.items():
            totals[uid] = totals.get(uid, 0) + max(0, now - max(start, since))
        members = {r["user_id"] for r in await q_all("SELECT user_id FROM family_members")}
        ranked = sorted(((u, s) for u, s in totals.items() if u in members and s > 0), key=lambda x: -x[1])
        return ranked[:limit]

    # ═════════════════════ Карточка участника ═════════════════════
    async def build_card(self, guild: Optional[disnake.Guild], row: Dict[str, Any]) -> disnake.Embed:
        cfg = load_config()
        uid, now = row["user_id"], now_ts()
        emb = disnake.Embed(title=f"📇 {row['nick']}", color=_color(cfg, "embed_color", 0x990000))
        member = guild.get_member(uid) if guild else None
        if member:
            emb.set_thumbnail(url=member.display_avatar.url)

        joined = parse_db_ts(row.get("joined_at"))
        joined_txt = f"<t:{joined}:D> (<t:{joined}:R>)" if joined else "—"
        emb.add_field(name="Discord", value=f"<@{uid}>" + ("" if member else " *(нет на сервере)*"), inline=True)
        emb.add_field(name="Статик", value=f"`{row['static_id']}`", inline=True)
        emb.add_field(name="Ранг", value=f"{rank_name(cfg, row['rank'])} ({row['rank']})", inline=True)
        emb.add_field(name="Вступил", value=joined_txt, inline=False)

        warns = await db_active_warns(uid)
        if warns:
            lines = []
            for w in warns[:5]:
                until = f"до <t:{w['expires_at']}:d>" if w["expires_at"] else "бессрочно"
                lines.append(f"`#{w['id']}` {w['reason'][:70]} — {until}")
            if len(warns) > 5:
                lines.append(f"…и ещё {len(warns) - 5}")
            warn_txt = "\n".join(lines)
        else:
            warn_txt = "нет"
        emb.add_field(name=f"⚠️ Выговоры ({len(warns)}/{WARN_LIMIT})", value=warn_txt, inline=False)

        leave = await q_one(
            "SELECT until_ts, reason FROM leaves WHERE user_id = ? AND status = 'approved' AND until_ts > ? ORDER BY until_ts DESC",
            (uid, now),
        )
        leave_txt = f"до <t:{leave['until_ts']}:D> — {leave['reason'][:80]}" if leave else "нет"
        emb.add_field(name="🏖️ Отпуск", value=leave_txt, inline=False)

        w7 = await self.voice_seconds(uid, now - 7 * 86400)
        w30 = await self.voice_seconds(uid, now - 30 * 86400)
        wall = await self.voice_seconds(uid, 0)
        in_vc = " 🟢 *сейчас в голосовом*" if uid in self.open_voice else ""
        emb.add_field(name="🎙️ Голосовой онлайн" + in_vc,
                      value=f"7 дней: **{fmt_duration(w7)}**\n30 дней: **{fmt_duration(w30)}**\nВсего: **{fmt_duration(wall)}**",
                      inline=False)
        return _footer(emb)

    @commands.slash_command(name="famq-card", description="Карточка участника семьи")
    async def card(
        self,
        inter: disnake.ApplicationCommandInteraction,
        query: Optional[str] = commands.Param(default=None, description="Ник, статик или упоминание"),
        member: Optional[disnake.Member] = commands.Param(default=None, description="Участник Discord"),
    ):
        if member is None and query is None:
            target_id = inter.author.id
        elif member is not None:
            target_id = member.id
        else:
            if not is_staff(inter.author):
                return await deny(inter)
            found = await self.search_members(query)
            if not found:
                return await inter.response.send_message("🔎 Никого не нашёл по этому запросу.", ephemeral=True)
            if len(found) > 1:
                lines = "\n".join(f"• **{r['nick']}** — статик `{r['static_id']}` — <@{r['user_id']}>" for r in found)
                return await inter.response.send_message(
                    f"Нашлось несколько человек, уточните запрос (лучше статиком):\n{lines}", ephemeral=True,
                    allowed_mentions=disnake.AllowedMentions.none())
            target_id = found[0]["user_id"]

        if target_id != inter.author.id and not is_staff(inter.author):
            return await deny(inter)

        row = await get_member(target_id)
        if row is None:
            return await inter.response.send_message("🔎 Этот человек не числится в составе семьи.", ephemeral=True)

        await inter.response.defer(ephemeral=True)
        emb = await self.build_card(inter.guild, row)
        if is_admin(inter.author):
            await inter.edit_original_response(embed=emb, view=CardView(self, inter, target_id))
        else:
            await inter.edit_original_response(embed=emb)

    async def change_rank(self, guild: disnake.Guild, uid: int, admin, delta: int) -> str:
        cfg = load_config()
        row = await get_member(uid)
        if row is None:
            return "❌ Участник не числится в составе."
        cur = int(row["rank"])
        max_rank = max(int(k) for k in cfg.get("ranks", {"1": {}}))
        new = cur + delta
        if new < 1:
            return "❌ Это уже минимальный ранг."
        if new > max_rank:
            return "❌ Это уже максимальный ранг."

        await upsert_member(uid, row["nick"], row["static_id"], new)

        note = ""
        member = guild.get_member(uid)
        if member:
            old_id = cfg["ranks"].get(str(cur), {}).get("role_id", 0)
            new_id = cfg["ranks"].get(str(new), {}).get("role_id", 0)
            try:
                old_role = guild.get_role(old_id) if old_id else None
                new_role = guild.get_role(new_id) if new_id else None
                if old_role and old_role in member.roles:
                    await member.remove_roles(old_role, reason=f"Смена ранга ({admin})")
                if new_role:
                    await member.add_roles(new_role, reason=f"Смена ранга ({admin})")
                elif not new_id:
                    note = f"\n⚠️ Для ранга {new} не задана роль в config.json — роль не выдана."
            except disnake.Forbidden:
                note = "\n⚠️ У бота нет прав менять роли (проверьте иерархию ролей)."
        else:
            note = "\n⚠️ Участника нет на сервере — изменена только запись в базе."

        verb = "повышен" if delta > 0 else "понижен"
        emb = disnake.Embed(
            title=f"Ранг изменён: {row['nick']}",
            description=f"<@{uid}> {verb}: **{rank_name(cfg, cur)} ({cur}) ➔ {rank_name(cfg, new)} ({new})**\nКто: {admin.mention}",
            color=_color(cfg, "success_color", 0x2ECC71) if delta > 0 else _color(cfg, "warning_color", 0xF39C12))
        await self.log_event(emb)
        return f"✅ {row['nick']} {verb} до ранга {new} ({rank_name(cfg, new)}).{note}"

    async def fire_member(self, guild: disnake.Guild, uid: int, admin, reason: str) -> bool:
        cfg = load_config()
        row = await get_member(uid)
        if row is None:
            return False
        await q_exec(
            "INSERT INTO fired_members (user_id, nick, static_id, rank, joined_at, kind, reason, admin_id) "
            "VALUES (?, ?, ?, ?, ?, 'fired', ?, ?)",
            (uid, row["nick"], row["static_id"], row["rank"], row["joined_at"], reason, admin.id),
        )
        await q_exec("DELETE FROM family_members WHERE user_id = ?", (uid,))

        member = guild.get_member(uid)
        if member:
            ids = {cfg.get("roles", {}).get("family_role_id", 0)}
            ids |= {v.get("role_id", 0) for v in cfg.get("ranks", {}).values()}
            roles = [r for r in (guild.get_role(i) for i in ids if i) if r and r in member.roles]
            try:
                if roles:
                    await member.remove_roles(*roles, reason=f"Увольнение ({admin}): {reason}")
                if cfg.get("auto_nicknames", True):
                    await member.edit(nick=None, reason="Увольнение из семьи")
            except disnake.Forbidden:
                pass

        emb = disnake.Embed(
            title=f"🚫 Уволен: {row['nick']}",
            description=f"<@{uid}> • статик `{row['static_id']}`\n📝 Причина: {reason}\n👮 Кто: {admin.mention}",
            color=_color(cfg, "error_color", 0xE74C3C))
        await self.log_event(emb)
        return True

    # ═════════════════════ Выговоры ═════════════════════
    async def issue_warn(self, guild: Optional[disnake.Guild], uid: int, admin, reason: str, days: int) -> int:
        cfg = load_config()
        warn_id = await db_add_warn(uid, admin.id, reason, days)
        count = len(await db_active_warns(uid))
        until = f"на {days} дн." if days > 0 else "бессрочно"

        emb = disnake.Embed(
            title=f"⚠️ Выговор #{warn_id}",
            description=f"<@{uid}> получил выговор ({until})\n📝 Причина: {reason}\n👮 Выдал: {admin.mention}\n"
                        f"Активных выговоров: **{count}/{WARN_LIMIT}**",
            color=_color(cfg, "warning_color", 0xF39C12))
        await self.log_event(emb)

        if count >= WARN_LIMIT:
            row = await get_member(uid)
            who = f"**{row['nick']}** (`{row['static_id']}`)" if row else f"<@{uid}>"
            alert = disnake.Embed(
                title=f"🚨 Лимит выговоров: {count}/{WARN_LIMIT}",
                description=f"У {who} ({f'<@{uid}>' if row else 'вне состава'}) набралось {count} активных выговоров.\n"
                            f"Руководству стоит принять решение (понижение / увольнение).",
                color=_color(cfg, "error_color", 0xE74C3C))
            await self.log_event(alert, ping_admins=True)

        user = guild.get_member(uid) if guild else None
        if user:
            dm = disnake.Embed(title="⚠️ Вам выдан выговор",
                               description=f"📝 **Причина:** {reason}\n⏳ **Срок:** {until}\n"
                                           f"📊 Активных выговоров: **{count}/{WARN_LIMIT}**",
                               color=_color(cfg, "warning_color", 0xF39C12))
            try:
                await user.send(embed=_footer(dm))
            except disnake.HTTPException:
                pass
        return count

    @commands.slash_command(name="famq-warn", description="Выговоры участникам семьи")
    async def warn(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @warn.sub_command(name="give", description="Выдать выговор")
    async def warn_give(
        self, inter: disnake.ApplicationCommandInteraction,
        member: disnake.Member = commands.Param(description="Кому"),
        reason: str = commands.Param(description="Причина", max_length=500),
        days: int = commands.Param(default=WARN_DEFAULT_DAYS, min_value=0, max_value=365,
                                   description="Срок действия в днях (0 = бессрочно)"),
    ):
        if not is_admin(inter.author):
            return await deny(inter)
        await inter.response.defer(ephemeral=True)
        count = await self.issue_warn(inter.guild, member.id, inter.author, reason, days)
        await inter.edit_original_response(f"✅ Выговор выдан {member.mention}. Активных: **{count}/{WARN_LIMIT}**.")

    @warn.sub_command(name="list", description="Активные выговоры участника")
    async def warn_list(self, inter: disnake.ApplicationCommandInteraction,
                        member: disnake.Member = commands.Param(description="Чьи выговоры")):
        if member.id != inter.author.id and not is_staff(inter.author):
            return await deny(inter)
        warns = await db_active_warns(member.id)
        if not warns:
            return await inter.response.send_message(f"У {member.mention} нет активных выговоров ✅", ephemeral=True,
                                                     allowed_mentions=disnake.AllowedMentions.none())
        lines = []
        for w in warns:
            until = f"до <t:{w['expires_at']}:d>" if w["expires_at"] else "бессрочно"
            lines.append(f"`#{w['id']}` {w['reason']} — {until} • выдал <@{w['admin_id']}>")
        emb = disnake.Embed(title=f"⚠️ Выговоры {member.display_name} ({len(warns)}/{WARN_LIMIT})",
                            description="\n".join(lines)[:4000], color=_color(load_config(), "warning_color", 0xF39C12))
        await inter.response.send_message(embed=_footer(emb), ephemeral=True)

    @warn.sub_command(name="remove", description="Снять выговор по номеру")
    async def warn_remove(self, inter: disnake.ApplicationCommandInteraction,
                          warn_id: int = commands.Param(description="Номер выговора (#id)", min_value=1)):
        if not is_admin(inter.author):
            return await deny(inter)
        if await remove_warn(warn_id):
            await self.log_event(disnake.Embed(title=f"✅ Выговор #{warn_id} снят",
                                               description=f"Снял: {inter.author.mention}",
                                               color=_color(load_config(), "success_color", 0x2ECC71)))
            await inter.response.send_message(f"✅ Выговор #{warn_id} снят.", ephemeral=True)
        else:
            await inter.response.send_message("❌ Выговор с таким номером не найден.", ephemeral=True)

    # ═════════════════════ Чёрный список ═════════════════════
    @commands.slash_command(name="famq-blacklist", description="Чёрный список (ушедшие и уволенные)")
    async def blacklist(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @blacklist.sub_command(name="list", description="Последние записи чёрного списка")
    async def bl_list(self, inter: disnake.ApplicationCommandInteraction):
        if not is_staff(inter.author):
            return await deny(inter)
        rows = await q_all("SELECT * FROM fired_members ORDER BY id DESC LIMIT 20")
        total = (await q_one("SELECT COUNT(*) AS c FROM fired_members"))["c"]
        if not rows:
            return await inter.response.send_message("Чёрный список пуст ✅", ephemeral=True)
        lines = []
        for r in rows:
            when = parse_db_ts(r["fired_at"])
            lines.append(
                f"`#{r['id']}` **{r['nick'] or '—'}** • статик `{r['static_id'] or '—'}` • "
                f"{KIND_LABEL.get(r['kind'], r['kind'])}" + (f" • <t:{when}:d>" if when else "") +
                f"\n└ {r['reason'] or 'без причины'}")
        emb = disnake.Embed(title=f"⛔ Чёрный список (последние {len(rows)} из {total})",
                            description="\n".join(lines)[:4000], color=_color(load_config(), "error_color", 0xE74C3C))
        await inter.response.send_message(embed=_footer(emb), ephemeral=True)

    @blacklist.sub_command(name="check", description="Проверить статик по чёрному списку")
    async def bl_check(self, inter: disnake.ApplicationCommandInteraction,
                       static_id: str = commands.Param(description="Статик игрока")):
        if not is_staff(inter.author):
            return await deny(inter)
        hits = await db_blacklist_find(static_id)
        if not hits:
            return await inter.response.send_message(f"✅ Статик `{static_id}` в чёрном списке не найден.", ephemeral=True)
        lines = [f"`#{h['id']}` **{h['nick'] or '—'}** • {KIND_LABEL.get(h['kind'], h['kind'])}\n└ {h['reason'] or 'без причины'}"
                 for h in hits]
        emb = disnake.Embed(title=f"⛔ Статик {static_id} найден в списке", description="\n".join(lines)[:4000],
                            color=_color(load_config(), "error_color", 0xE74C3C))
        await inter.response.send_message(embed=_footer(emb), ephemeral=True)

    @blacklist.sub_command(name="add", description="Вручную добавить в чёрный список")
    async def bl_add(
        self, inter: disnake.ApplicationCommandInteraction,
        static_id: str = commands.Param(description="Статик игрока"),
        reason: str = commands.Param(description="Причина", max_length=500),
        nick: Optional[str] = commands.Param(default=None, description="Игровой ник"),
        member: Optional[disnake.User] = commands.Param(default=None, description="Discord-аккаунт (если известен)"),
    ):
        if not is_admin(inter.author):
            return await deny(inter)
        if await db_blacklist_find(static_id):
            return await inter.response.send_message("ℹ️ Такой статик уже есть в списке (`/famq-blacklist check`).", ephemeral=True)
        entry = await q_exec(
            "INSERT INTO fired_members (user_id, nick, static_id, kind, reason, admin_id) VALUES (?, ?, ?, 'blacklist', ?, ?)",
            (member.id if member else 0, nick, static_id.strip(), reason, inter.author.id))
        await self.log_event(disnake.Embed(
            title=f"⛔ Добавлен в ЧС: {nick or static_id}",
            description=f"Статик `{static_id}`\n📝 {reason}\n👮 {inter.author.mention}",
            color=_color(load_config(), "error_color", 0xE74C3C)))
        await inter.response.send_message(f"✅ Запись `#{entry}` добавлена в чёрный список.", ephemeral=True)

    @blacklist.sub_command(name="remove", description="Убрать запись из чёрного списка")
    async def bl_remove(self, inter: disnake.ApplicationCommandInteraction,
                        entry_id: int = commands.Param(description="Номер записи (#id)", min_value=1)):
        if not is_admin(inter.author):
            return await deny(inter)
        row = await q_one("SELECT * FROM fired_members WHERE id = ?", (entry_id,))
        if not row:
            return await inter.response.send_message("❌ Запись не найдена.", ephemeral=True)
        await q_exec("DELETE FROM fired_members WHERE id = ?", (entry_id,))
        await self.log_event(disnake.Embed(
            title=f"✅ Убрано из ЧС: {row['nick'] or row['static_id']}",
            description=f"Запись `#{entry_id}` удалена: {inter.author.mention}",
            color=_color(load_config(), "success_color", 0x2ECC71)))
        await inter.response.send_message(f"✅ Запись `#{entry_id}` удалена.", ephemeral=True)

    @tasks.loop(seconds=BLACKLIST_POLL_SEC)
    async def blacklist_loop(self):
        """Проверяет новые анкеты по статику и Discord ID; при совпадении пишет в ветку заявки."""
        try:
            apps = await q_all(
                "SELECT a.* FROM applications a LEFT JOIN bl_checked c ON c.app_id = a.id "
                "WHERE c.app_id IS NULL AND a.thread_id IS NOT NULL AND a.status IN ('pending', 'in_review')")
            cfg = load_config()
            for app in apps:
                hits = await db_blacklist_find(app["static_id"], app["user_id"])
                if hits:
                    await self._warn_thread(app, hits, cfg)
                await q_exec("INSERT OR IGNORE INTO bl_checked (app_id) VALUES (?)", (app["id"],))
        except Exception as e:
            print(f"[famq_control] blacklist_loop: {e}", file=sys.stderr, flush=True)

    async def _warn_thread(self, app: Dict[str, Any], hits: List[Dict[str, Any]], cfg: dict):
        try:
            thread = self.bot.get_channel(app["thread_id"]) or await self.bot.fetch_channel(app["thread_id"])
        except (disnake.NotFound, disnake.Forbidden):
            return
        lines = []
        for h in hits[:5]:
            match = "статик" if str(h["static_id"] or "").strip() == str(app["static_id"]).strip() else "Discord-аккаунт"
            lines.append(f"• совпал **{match}**: {h['nick'] or '—'} (`{h['static_id'] or '—'}`) — "
                         f"{KIND_LABEL.get(h['kind'], h['kind'])}\n  └ {h['reason'] or 'без причины'}")
        emb = disnake.Embed(
            title="⛔ Внимание: кандидат есть в чёрном списке",
            description=f"Анкета **{app['nick']}** (статик `{app['static_id']}`, <@{app['user_id']}>):\n" + "\n".join(lines),
            color=_color(cfg, "error_color", 0xE74C3C))
        ping = " ".join(f"<@&{r}>" for r in cfg.get("recruiter_role_ids", []))
        try:
            await thread.send(content=ping or None, embed=_footer(emb),
                              allowed_mentions=disnake.AllowedMentions(roles=True, users=False))
        except disnake.HTTPException:
            pass

    @blacklist_loop.before_loop
    async def _bl_before(self):
        await self.bot.wait_until_ready()
        await ensure_tables()

    # ═════════════════════ Голосовой онлайн ═════════════════════
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: disnake.Member, before: disnake.VoiceState, after: disnake.VoiceState):
        if member.bot:
            return
        ids = self._vc_ids()
        b = before.channel.id if before.channel and before.channel.id in ids else None
        a = after.channel.id if after.channel and after.channel.id in ids else None
        now = now_ts()
        if a and not b:
            self.open_voice[member.id] = (now, a)
        elif b and not a:
            start = self.open_voice.pop(member.id, None)
            if start:
                await self._save_session(member.id, start[1], start[0], now)
        elif a and b and a != b and member.id in self.open_voice:
            self.open_voice[member.id] = (self.open_voice[member.id][0], a)

    async def _save_session(self, uid: int, channel_id: int, start: int, end: int):
        if end - start < 5:
            return
        await ensure_tables()
        await q_exec("INSERT INTO voice_sessions (user_id, channel_id, start_ts, end_ts) VALUES (?, ?, ?, ?)",
                     (uid, channel_id, start, end))

    async def _flush_voice(self, final: bool = False):
        now = now_ts()
        for uid, (start, ch) in list(self.open_voice.items()):
            await self._save_session(uid, ch, start, now)
            if final:
                self.open_voice.pop(uid, None)
            else:
                self.open_voice[uid] = (now, ch)

    def _rescan_voice(self):
        ids, now = self._vc_ids(), now_ts()
        for guild in self.bot.guilds:
            for ch in guild.voice_channels:
                if ch.id in ids:
                    for m in ch.members:
                        if not m.bot and m.id not in self.open_voice:
                            self.open_voice[m.id] = (now, ch.id)

    @tasks.loop(minutes=VOICE_FLUSH_MIN)
    async def voice_loop(self):
        try:
            await self._flush_voice()
        except Exception as e:
            print(f"[famq_control] voice_loop: {e}", file=sys.stderr, flush=True)

    @voice_loop.before_loop
    async def _voice_before(self):
        await self.bot.wait_until_ready()
        await ensure_tables()
        self._rescan_voice()  # подхватываем тех, кто уже сидит в голосовых после (пере)запуска

    @commands.slash_command(name="famq-voice", description="Голосовой онлайн в каналах семьи")
    async def voice(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @voice.sub_command(name="top", description="Топ по времени в голосовых")
    async def voice_top_cmd(self, inter: disnake.ApplicationCommandInteraction,
                            period: str = commands.Param(default="week", choices=PERIODS, description="Период")):
        top = await self.voice_top(period_since(period), 10)
        if not top:
            return await inter.response.send_message("За этот период данных пока нет.", ephemeral=True)
        lines = [f"{MEDALS[i] if i < 3 else f'`{i + 1}.`'} <@{uid}> — **{fmt_duration(sec)}**" for i, (uid, sec) in enumerate(top)]
        emb = disnake.Embed(title=f"🎙️ Топ голосового онлайна {PERIOD_LABEL[period]}", description="\n".join(lines),
                            color=_color(load_config(), "embed_color", 0x990000))
        await inter.response.send_message(embed=_footer(emb), allowed_mentions=disnake.AllowedMentions.none())

    @voice.sub_command(name="me", description="Ваш голосовой онлайн (или участника — для руководства)")
    async def voice_me(self, inter: disnake.ApplicationCommandInteraction,
                       member: Optional[disnake.Member] = commands.Param(default=None, description="Участник")):
        target = member or inter.author
        if target.id != inter.author.id and not is_staff(inter.author):
            return await deny(inter)
        now = now_ts()
        w7 = await self.voice_seconds(target.id, now - 7 * 86400)
        w30 = await self.voice_seconds(target.id, now - 30 * 86400)
        wall = await self.voice_seconds(target.id, 0)
        emb = disnake.Embed(title=f"🎙️ Онлайн: {target.display_name}",
                            description=f"7 дней: **{fmt_duration(w7)}**\n30 дней: **{fmt_duration(w30)}**\nВсего: **{fmt_duration(wall)}**",
                            color=_color(load_config(), "embed_color", 0x990000))
        await inter.response.send_message(embed=_footer(emb), ephemeral=True)

    # ═════════════════════ Статистика рекрутеров ═════════════════════
    @commands.slash_command(name="famq-recruiters", description="Статистика рекрутеров")
    async def recruiters(self, inter: disnake.ApplicationCommandInteraction,
                         period: str = commands.Param(default="week", choices=PERIODS, description="Период")):
        if not is_staff(inter.author):
            return await deny(inter)
        rows = await db_recruiter_rows(period_since(period))
        if not rows:
            return await inter.response.send_message("За этот период решений по заявкам нет.", ephemeral=True)
        lines = []
        for i, r in enumerate(rows[:15]):
            total = r["accepted"] + r["rejected"]
            rate = round(r["accepted"] / total * 100) if total else 0
            lines.append(f"{MEDALS[i] if i < 3 else f'`{i + 1}.`'} <@{r['reviewer_id']}> — всего **{total}** "
                         f"(✅ {r['accepted']} / ❌ {r['rejected']}, принято {rate}%)")
        emb = disnake.Embed(title=f"📋 Рекрутеры {PERIOD_LABEL[period]}", description="\n".join(lines),
                            color=_color(load_config(), "embed_color", 0x990000))
        await inter.response.send_message(embed=_footer(emb), allowed_mentions=disnake.AllowedMentions.none())

    # ═════════════════════ Живая статистика ═════════════════════
    async def build_stats_embed(self) -> disnake.Embed:
        cfg = load_config()
        now = now_ts()
        week_db = ts_to_db(now - 7 * 86400)

        rank_rows = await q_all("SELECT rank, COUNT(*) AS c FROM family_members GROUP BY rank ORDER BY rank DESC")
        total = sum(r["c"] for r in rank_rows)
        rank_lines = [f"{rank_name(cfg, r['rank'])} ({r['rank']}): **{r['c']}**" for r in rank_rows] or ["пока никого"]

        new_apps = (await q_one("SELECT COUNT(*) AS c FROM applications WHERE created_at >= ?", (week_db,)))["c"]
        accepted = (await q_one("SELECT COUNT(*) AS c FROM applications WHERE status = 'accepted' AND closed_at >= ?", (week_db,)))["c"]
        rejected = (await q_one("SELECT COUNT(*) AS c FROM applications WHERE status = 'rejected' AND closed_at >= ?", (week_db,)))["c"]
        pending = (await q_one("SELECT COUNT(*) AS c FROM applications WHERE status IN ('pending', 'in_review')"))["c"]
        promos_pending = (await q_one("SELECT COUNT(*) AS c FROM promotions WHERE status = 'pending'"))["c"]
        warns_active = (await q_one("SELECT COUNT(*) AS c FROM warns WHERE active = 1 AND (expires_at IS NULL OR expires_at > ?)", (now,)))["c"]

        leaves = await q_all(
            "SELECT l.user_id, l.until_ts, m.nick FROM leaves l LEFT JOIN family_members m ON m.user_id = l.user_id "
            "WHERE l.status = 'approved' AND l.until_ts > ? ORDER BY l.until_ts", (now,))
        leave_lines = [f"<@{l['user_id']}> — до <t:{l['until_ts']}:d>" for l in leaves[:8]]
        if len(leaves) > 8:
            leave_lines.append(f"…и ещё {len(leaves) - 8}")

        vtop = await self.voice_top(now - 7 * 86400, 3)
        vlines = [f"{MEDALS[i]} <@{u}> — {fmt_duration(s)}" for i, (u, s) in enumerate(vtop)]
        rec = await db_recruiter_rows(now - 7 * 86400)

        emb = disnake.Embed(title=f"📊 Статистика {cfg.get('bot_name', 'Hall FAMQ')}",
                            description=f"Обновлено: <t:{now}:R>", color=_color(cfg, "embed_color", 0x990000))
        emb.add_field(name=f"👥 Состав ({total})", value="\n".join(rank_lines), inline=True)
        emb.add_field(name="📥 Заявки за 7 дней",
                      value=f"Новых: **{new_apps}**\n✅ Принято: **{accepted}**\n❌ Отклонено: **{rejected}**\n"
                            f"⏳ Сейчас на рассмотрении: **{pending}**", inline=True)
        emb.add_field(name="📈 Контроль",
                      value=f"Отчётов на повышение: **{promos_pending}**\nАктивных выговоров: **{warns_active}**", inline=True)
        emb.add_field(name=f"🏖️ В отпуске ({len(leaves)})", value="\n".join(leave_lines) or "никого", inline=False)
        emb.add_field(name="🎙️ Топ голосового за 7 дней", value="\n".join(vlines) or "данных пока нет", inline=True)
        if rec:
            r = rec[0]
            emb.add_field(name="🏆 Рекрутер недели",
                          value=f"<@{r['reviewer_id']}> — **{r['accepted'] + r['rejected']}** решений "
                                f"(✅ {r['accepted']} / ❌ {r['rejected']})", inline=True)
        return _footer(emb)

    async def refresh_live_stats(self):
        cid = await kv_get("stats_channel_id")
        if not cid:
            return
        channel = self.bot.get_channel(int(cid))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(cid))
            except (disnake.NotFound, disnake.Forbidden):
                return
        emb = await self.build_stats_embed()
        mid = await kv_get("stats_message_id")
        if mid:
            try:
                msg = await channel.fetch_message(int(mid))
                await msg.edit(embed=emb)
                return
            except disnake.NotFound:
                pass
        msg = await channel.send(embed=emb)
        await kv_set("stats_message_id", str(msg.id))

    @tasks.loop(minutes=STATS_REFRESH_MIN)
    async def stats_loop(self):
        try:
            await self.refresh_live_stats()
        except Exception as e:
            print(f"[famq_control] stats_loop: {e}", file=sys.stderr, flush=True)

    @stats_loop.before_loop
    async def _stats_before(self):
        await self.bot.wait_until_ready()
        await ensure_tables()

    @tasks.loop(hours=1)
    async def housekeeping_loop(self):
        """Помечает истёкшие выговоры неактивными, чтобы старые запросы (database.get_stats) считали верно."""
        try:
            await db_expire_warns()
        except Exception as e:
            print(f"[famq_control] housekeeping_loop: {e}", file=sys.stderr, flush=True)

    @housekeeping_loop.before_loop
    async def _hk_before(self):
        await self.bot.wait_until_ready()
        await ensure_tables()

    @commands.slash_command(name="famq-livestats", description="Самообновляющееся сообщение со статистикой семьи")
    async def livestats(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @livestats.sub_command(name="setup", description="Создать статистику в канале")
    async def livestats_setup(self, inter: disnake.ApplicationCommandInteraction,
                              channel: disnake.TextChannel = commands.Param(description="Канал для статистики")):
        if not is_admin(inter.author):
            return await deny(inter)
        await inter.response.defer(ephemeral=True)
        await kv_set("stats_channel_id", str(channel.id))
        await kv_del("stats_message_id")
        try:
            await self.refresh_live_stats()
        except disnake.Forbidden:
            return await inter.edit_original_response("❌ У бота нет прав писать в этот канал.")
        await inter.edit_original_response(f"✅ Статистика создана в {channel.mention} и обновляется каждые {STATS_REFRESH_MIN} мин.")

    @livestats.sub_command(name="refresh", description="Обновить статистику прямо сейчас")
    async def livestats_refresh(self, inter: disnake.ApplicationCommandInteraction):
        if not is_admin(inter.author):
            return await deny(inter)
        if not await kv_get("stats_channel_id"):
            return await inter.response.send_message("Сначала настройте: `/famq-livestats setup`.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        await self.refresh_live_stats()
        await inter.edit_original_response("✅ Обновлено.")

    @livestats.sub_command(name="off", description="Отключить автообновление")
    async def livestats_off(self, inter: disnake.ApplicationCommandInteraction):
        if not is_admin(inter.author):
            return await deny(inter)
        await kv_del("stats_channel_id")
        await kv_del("stats_message_id")
        await inter.response.send_message("✅ Автообновление отключено (само сообщение можно удалить вручную).", ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(FamqControl(bot))
