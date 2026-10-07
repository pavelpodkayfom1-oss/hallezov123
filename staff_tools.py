"""
cogs/staff_tools.py — карточка участника, выговоры, чёрный список,
живая статистика, учёт голосового онлайна, статистика рекрутеров.

Модуль самодостаточный: зависит только от database.py и config.json.
Свои таблицы создаёт сам (CREATE TABLE IF NOT EXISTS), существующие не меняет.

Подключение: добавить "cogs.staff_tools" в список COGS в main.py.

Необязательные ключи config.json:
    "warn_limit": 3            # сколько активных выговоров -> уведомление руководству
    "warn_expire_days": 30     # через сколько дней выговор снимается сам (0 = не снимать)
"""
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import aiosqlite
import disnake
from disnake.ext import commands, tasks

from database import DB_PATH, add_warn, get_active_warns, get_member, remove_warn, upsert_member

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SETTINGS_PATH = os.path.join(BASE_DIR, "staff_settings.json")

PERIODS = {"week": ("-6 days", "-7 days", "7 дней"),
           "month": ("-29 days", "-30 days", "30 дней"),
           "all": ("-100 years", "-100 years", "всё время")}


# ───────────────────────── helpers ─────────────────────────
def cfg() -> Dict[str, Any]:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_settings() -> Dict[str, Any]:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data: Dict[str, Any]):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def embed_color() -> int:
    try:
        return int(str(cfg().get("embed_color", "0x990000")), 16)
    except Exception:
        return 0x990000


def is_admin(user) -> bool:
    c = cfg()
    if user.id in c.get("admin_user_ids", []):
        return True
    return any(r.id in c.get("admin_role_ids", []) for r in getattr(user, "roles", []))


def is_staff(user) -> bool:
    if is_admin(user):
        return True
    rec = cfg().get("recruiter_role_ids", [])
    return any(r.id in rec for r in getattr(user, "roles", []))


def rank_name(rank: int) -> str:
    r = cfg().get("ranks", {}).get(str(rank), {})
    return r.get("name", f"Ранг {rank}")


def max_rank() -> int:
    ranks = cfg().get("ranks", {})
    return max((int(k) for k in ranks), default=1)


def rank_role_ids() -> List[int]:
    return [v["role_id"] for v in cfg().get("ranks", {}).values() if v.get("role_id")]


def to_ts(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    try:
        return int(datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        return None


def fmt_dur(seconds: int) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 3600} ч {seconds % 3600 // 60} мин"


async def q_all(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def q_one(sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    rows = await q_all(sql, params)
    return rows[0] if rows else None


async def q_exec(sql: str, params: tuple = ()) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(sql, params)
        await db.commit()
        return cur.lastrowid


async def init_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблицы fired_members и leaves создаёт другой модуль; схема та же, IF NOT EXISTS безвреден.
        await db.execute("""CREATE TABLE IF NOT EXISTS fired_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, nick TEXT, static_id TEXT,
            rank INTEGER, joined_at TIMESTAMP, kind TEXT NOT NULL, reason TEXT, admin_id INTEGER,
            fired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS leaves (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, until_ts INTEGER NOT NULL,
            reason TEXT NOT NULL, status TEXT DEFAULT 'pending', message_id INTEGER, reviewer_id INTEGER,
            verdict_reason TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS blacklist (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, static_id TEXT, nick TEXT,
            reason TEXT NOT NULL, admin_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        await db.execute("CREATE TABLE IF NOT EXISTS blacklist_alerts (app_id INTEGER PRIMARY KEY)")
        await db.execute("""CREATE TABLE IF NOT EXISTS voice_daily (
            user_id INTEGER NOT NULL, day TEXT NOT NULL, seconds INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day))""")
        await db.commit()


# ───────────────────────── data queries ─────────────────────────
async def voice_seconds(user_id: int, period: str) -> int:
    off = PERIODS[period][0]
    row = await q_one("SELECT COALESCE(SUM(seconds),0) s FROM voice_daily WHERE user_id=? AND day>=date('now',?)",
                      (user_id, off))
    return row["s"] if row else 0


async def active_leave(user_id: int) -> Optional[Dict[str, Any]]:
    return await q_one(
        "SELECT * FROM leaves WHERE user_id=? AND status='approved' AND until_ts>? ORDER BY until_ts DESC",
        (user_id, int(time.time())))


async def blacklist_hits(static_id: str, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Ищет статик/пользователя в ручном чёрном списке и среди уволенных/ушедших."""
    static_id = (static_id or "").strip()
    uid = user_id or -1
    hits: List[Dict[str, Any]] = []
    hits += await q_all(
        "SELECT 'bl' src, nick, static_id, reason, created_at, NULL kind FROM blacklist "
        "WHERE static_id=? OR user_id=?", (static_id, uid))
    hits += await q_all(
        "SELECT 'fired' src, nick, static_id, reason, fired_at created_at, kind FROM fired_members "
        "WHERE static_id=? OR user_id=?", (static_id, uid))
    return hits


def hit_line(h: Dict[str, Any]) -> str:
    if h["src"] == "bl":
        label = "⛔ чёрный список"
    else:
        label = "🚪 уволен" if h.get("kind") == "fired" else "🚪 ушёл из семьи"
    ts = to_ts(h.get("created_at"))
    when = f" · <t:{ts}:d>" if ts else ""
    return f"**{label}**{when}\n└ `{h.get('nick') or '—'}` / `{h.get('static_id') or '—'}` — {h.get('reason') or 'без причины'}"


async def find_members(query: str) -> List[Dict[str, Any]]:
    q = query.strip()
    m = re.fullmatch(r"<@!?(\d+)>", q)
    if m or (q.isdigit() and len(q) >= 15):
        uid = int(m.group(1) if m else q)
        row = await get_member(uid)
        return [row] if row else []
    rows = await q_all("SELECT * FROM family_members WHERE static_id=?", (q,))
    if rows:
        return rows
    return await q_all("SELECT * FROM family_members WHERE nick LIKE ? ORDER BY nick LIMIT 10", (f"%{q}%",))


# ───────────────────────── cog ─────────────────────────
class StaffTools(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: Dict[int, float] = {}   # user_id -> время входа в голос (unix)
        self._started = False
        # при hot-reload on_ready уже не придёт — стартуем сами
        if bot.is_ready():
            self._spawn(self._startup())

    def _spawn(self, coro):
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            self.bot.loop.create_task(coro)

    async def _startup(self):
        if self._started:
            return
        self._started = True
        await init_tables()
        now = time.time()
        for vc in cfg().get("voice_channels", []):
            ch = self.bot.get_channel(vc["id"])
            if ch:
                for m in ch.members:
                    if not m.bot:
                        self.sessions.setdefault(m.id, now)
        for loop in (self.voice_checkpoint, self.warn_expiry, self.blacklist_watch, self.board_update):
            if not loop.is_running():
                loop.start()
        print("✅ staff_tools: фоновые задачи запущены", flush=True)

    def cog_unload(self):
        for loop in (self.voice_checkpoint, self.warn_expiry, self.blacklist_watch, self.board_update):
            loop.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        await self._startup()

    # ── логирование и уведомления ──
    async def log(self, title: str, desc: str, color: Optional[int] = None):
        ch_id = cfg().get("channels", {}).get("logs_channel_id")
        ch = self.bot.get_channel(ch_id) if ch_id else None
        if ch:
            try:
                await ch.send(embed=disnake.Embed(title=title, description=desc, color=color or embed_color(),
                                                  timestamp=datetime.now(timezone.utc)))
            except Exception as e:
                print(f"staff_tools: не удалось записать лог: {e}", file=sys.stderr, flush=True)

    # ───────────── КАРТОЧКА УЧАСТНИКА ─────────────
    async def card_embed(self, guild: disnake.Guild, row: Dict[str, Any]) -> disnake.Embed:
        uid = row["user_id"]
        member = guild.get_member(uid)
        warns = await get_active_warns(uid)
        leave = await active_leave(uid)
        v7, v30 = await voice_seconds(uid, "week"), await voice_seconds(uid, "month")
        joined = to_ts(row.get("joined_at"))

        e = disnake.Embed(title=f"👤 {row['nick']}", color=embed_color(), timestamp=datetime.now(timezone.utc))
        e.description = f"<@{uid}>" + ("" if member else "  ⚠️ *нет на сервере*")
        e.add_field("🎖️ Ранг", f"{rank_name(row['rank'])} ({row['rank']})", inline=True)
        e.add_field("🆔 Статик", f"`{row['static_id']}`", inline=True)
        e.add_field("📅 В семье с", f"<t:{joined}:D> (<t:{joined}:R>)" if joined else "—", inline=True)

        if warns:
            days = int(cfg().get("warn_expire_days", 30))
            lines = []
            for w in warns[:3]:
                ts = to_ts(w["created_at"])
                exp = f" · снимется <t:{ts + days * 86400}:d>" if days and ts else ""
                lines.append(f"`#{w['id']}` {w['reason'][:70]}{exp}")
            more = f"\n… и ещё {len(warns) - 3}" if len(warns) > 3 else ""
            e.add_field(f"⚠️ Выговоры: {len(warns)}", "\n".join(lines) + more, inline=False)
        else:
            e.add_field("⚠️ Выговоры", "нет", inline=True)

        e.add_field("🌴 Отпуск", f"до <t:{leave['until_ts']}:D>" if leave else "нет", inline=True)
        e.add_field("🎙️ Голос", f"7 дн: **{fmt_dur(v7)}**\n30 дн: **{fmt_dur(v30)}**", inline=True)
        if member:
            e.set_thumbnail(url=member.display_avatar.url)
        return e

    @staticmethod
    def card_components(uid: int, admin: bool):
        if not admin:
            return []
        B, S = disnake.ui.Button, disnake.ButtonStyle
        return [disnake.ui.ActionRow(
            B(label="Повысить", emoji="⬆️", style=S.success, custom_id=f"staff:promote:{uid}"),
            B(label="Понизить", emoji="⬇️", style=S.secondary, custom_id=f"staff:demote:{uid}"),
            B(label="Выговор", emoji="⚠️", style=S.primary, custom_id=f"staff:warn:{uid}"),
            B(label="Уволить", emoji="🚪", style=S.danger, custom_id=f"staff:fire:{uid}"),
        )]

    async def apply_rank(self, guild: disnake.Guild, row: Dict[str, Any], new_rank: int) -> str:
        await upsert_member(row["user_id"], row["nick"], row["static_id"], new_rank)
        member = guild.get_member(row["user_id"])
        if not member:
            return "ранг записан в базу (участника нет на сервере, роли не менялись)"
        ranks = cfg().get("ranks", {})
        new_role_id = ranks.get(str(new_rank), {}).get("role_id") or 0
        try:
            remove = [r for r in member.roles if r.id in rank_role_ids() and r.id != new_role_id]
            if remove:
                await member.remove_roles(*remove, reason="Смена ранга")
            if new_role_id:
                role = guild.get_role(new_role_id)
                if role and role not in member.roles:
                    await member.add_roles(role, reason="Смена ранга")
            elif new_rank >= 1:
                return "ранг записан в базу (для этого ранга в config.json не задана роль)"
        except disnake.Forbidden:
            return "ранг записан в базу, но у бота нет прав менять роли (проверьте иерархию ролей)"
        return "роли обновлены"

    async def do_fire(self, guild: disnake.Guild, row: Dict[str, Any], reason: str, admin_id: int):
        await q_exec(
            "INSERT INTO fired_members (user_id,nick,static_id,rank,joined_at,kind,reason,admin_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (row["user_id"], row["nick"], row["static_id"], row["rank"], row.get("joined_at"),
             "fired", reason, admin_id))
        await q_exec("DELETE FROM family_members WHERE user_id=?", (row["user_id"],))
        member = guild.get_member(row["user_id"])
        if member:
            ids = set(rank_role_ids()) | {cfg().get("roles", {}).get("family_role_id", 0)}
            remove = [r for r in member.roles if r.id in ids]
            try:
                if remove:
                    await member.remove_roles(*remove, reason=f"Увольнение: {reason}")
            except disnake.Forbidden:
                pass

    async def issue_warn(self, guild: disnake.Guild, uid: int, admin_id: int, reason: str) -> str:
        warn_id = await add_warn(uid, admin_id, reason)
        active = await get_active_warns(uid)
        limit = int(cfg().get("warn_limit", 3))
        try:
            user = guild.get_member(uid) or await self.bot.fetch_user(uid)
            await user.send(f"⚠️ Вам выдан выговор в семье **{cfg().get('bot_name', 'Hall FAMQ')}**.\n"
                            f"📝 Причина: {reason}\nАктивных выговоров: {len(active)}/{limit}")
        except Exception:
            pass
        await self.log("⚠️ Выдан выговор",
                       f"**Кому:** <@{uid}>\n**Выдал:** <@{admin_id}>\n**Причина:** {reason}\n"
                       f"**Активных:** {len(active)}/{limit} · `#{warn_id}`", 0xF39C12)
        if len(active) >= limit:
            pings = " ".join(f"<@&{r}>" for r in cfg().get("admin_role_ids", []))
            ch_id = cfg().get("channels", {}).get("logs_channel_id")
            ch = self.bot.get_channel(ch_id) if ch_id else None
            if ch:
                await ch.send(f"{pings}\n🚨 У <@{uid}> набралось **{len(active)} выговора(ов)** из {limit}. "
                              f"Требуется решение руководства.",
                              allowed_mentions=disnake.AllowedMentions(roles=True))
        return f"Выговор `#{warn_id}` выдан. Активных: {len(active)}/{limit}."

    # ── обработка кнопок карточки ──
    @commands.Cog.listener()
    async def on_button_click(self, inter: disnake.MessageInteraction):
        cid = inter.data.custom_id
        if not cid.startswith("staff:"):
            return
        _, action, uid_s = cid.split(":")
        uid = int(uid_s)
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        row = await get_member(uid)
        if not row:
            return await inter.response.send_message("❌ Участник уже не числится в семье.", ephemeral=True)

        if action in ("warn", "fire"):
            title = "Выдать выговор" if action == "warn" else "Уволить из семьи"
            return await inter.response.send_modal(disnake.ui.Modal(
                title=title, custom_id=f"staff_{action}:{uid}",
                components=[disnake.ui.TextInput(label="Причина", custom_id="reason",
                                                 style=disnake.TextInputStyle.paragraph,
                                                 min_length=3, max_length=500)]))

        new_rank = row["rank"] + (1 if action == "promote" else -1)
        if not 1 <= new_rank <= max_rank():
            return await inter.response.send_message("❌ Это крайний ранг, дальше менять нельзя.", ephemeral=True)
        await inter.response.defer()
        note = await self.apply_rank(inter.guild, row, new_rank)
        await self.log("🎖️ Смена ранга",
                       f"**Участник:** <@{uid}> (`{row['nick']}`)\n**Ранг:** {row['rank']} ➔ {new_rank} "
                       f"({rank_name(new_rank)})\n**Изменил:** {inter.author.mention}", 0x2ECC71)
        row = await get_member(uid)
        await inter.edit_original_message(embed=await self.card_embed(inter.guild, row),
                                          components=self.card_components(uid, True))
        await inter.followup.send(f"✅ Ранг изменён на **{rank_name(new_rank)}** — {note}.", ephemeral=True)

    @commands.Cog.listener()
    async def on_modal_submit(self, inter: disnake.ModalInteraction):
        cid = inter.data.custom_id
        if not cid.startswith(("staff_warn:", "staff_fire:")):
            return
        action, uid_s = cid.split(":")
        uid = int(uid_s)
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        reason = inter.text_values["reason"].strip()
        row = await get_member(uid)
        if not row:
            return await inter.response.send_message("❌ Участник уже не числится в семье.", ephemeral=True)

        if action == "staff_warn":
            text = "✅ " + await self.issue_warn(inter.guild, uid, inter.author.id, reason)
            embed, comps = await self.card_embed(inter.guild, row), self.card_components(uid, True)
        else:
            await self.do_fire(inter.guild, row, reason, inter.author.id)
            await self.log("🚪 Увольнение",
                           f"**Участник:** <@{uid}> (`{row['nick']}` / `{row['static_id']}`)\n"
                           f"**Уволил:** {inter.author.mention}\n**Причина:** {reason}", 0xE74C3C)
            text = "✅ Участник уволен, роли сняты, запись добавлена в список уволенных."
            embed = disnake.Embed(title=f"🚪 {row['nick']} уволен", description=f"Причина: {reason}",
                                  color=0xE74C3C)
            comps = []
        try:
            await inter.response.edit_message(embed=embed, components=comps)
            await inter.followup.send(text, ephemeral=True)
        except Exception:
            if not inter.response.is_done():
                await inter.response.send_message(text, ephemeral=True)

    # ───────────── КОМАНДЫ ─────────────
    @commands.slash_command(name="staff", description="Инструменты руководства Hall FAMQ")
    @commands.guild_only()
    async def staff(self, inter: disnake.ApplicationCommandInteraction):
        pass

    # --- карточка ---
    @staff.sub_command(name="card", description="Карточка участника (укажите участника или ник/статик)")
    async def card(self, inter: disnake.ApplicationCommandInteraction,
                   user: Optional[disnake.Member] = None, query: Optional[str] = None):
        if not is_staff(inter.author):
            return await inter.response.send_message("❌ Нет доступа.", ephemeral=True)
        if not user and not query:
            return await inter.response.send_message("Укажите участника или введите ник/статик в `query`.",
                                                     ephemeral=True)
        rows = [await get_member(user.id)] if user else await find_members(query)
        rows = [r for r in rows if r]
        if not rows:
            return await inter.response.send_message("🔍 В базе семьи такого участника нет.", ephemeral=True)
        if len(rows) > 1:
            lst = "\n".join(f"• `{r['nick']}` — статик `{r['static_id']}` — <@{r['user_id']}>" for r in rows)
            return await inter.response.send_message(f"Найдено несколько, уточните запрос:\n{lst}",
                                                     ephemeral=True)
        await inter.response.send_message(embed=await self.card_embed(inter.guild, rows[0]),
                                          components=self.card_components(rows[0]["user_id"], is_admin(inter.author)),
                                          ephemeral=True)

    # --- выговоры ---
    @staff.sub_command(name="warn", description="Выдать выговор")
    async def warn(self, inter: disnake.ApplicationCommandInteraction, user: disnake.Member, reason: str):
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        if not await get_member(user.id):
            return await inter.response.send_message("❌ Этого человека нет в базе семьи.", ephemeral=True)
        await inter.response.send_message("✅ " + await self.issue_warn(inter.guild, user.id, inter.author.id, reason),
                                          ephemeral=True)

    @staff.sub_command(name="unwarn", description="Снять выговор по номеру")
    async def unwarn(self, inter: disnake.ApplicationCommandInteraction, warn_id: int):
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        if await remove_warn(warn_id):
            await self.log("✅ Выговор снят", f"**Выговор:** `#{warn_id}`\n**Снял:** {inter.author.mention}", 0x2ECC71)
            await inter.response.send_message(f"✅ Выговор `#{warn_id}` снят.", ephemeral=True)
        else:
            await inter.response.send_message("❌ Выговор с таким номером не найден.", ephemeral=True)

    @staff.sub_command(name="warns", description="Список выговоров участника")
    async def warns(self, inter: disnake.ApplicationCommandInteraction, user: disnake.Member):
        if not is_staff(inter.author):
            return await inter.response.send_message("❌ Нет доступа.", ephemeral=True)
        rows = await q_all("SELECT * FROM warns WHERE user_id=? ORDER BY id DESC LIMIT 15", (user.id,))
        if not rows:
            return await inter.response.send_message("У участника выговоров не было.", ephemeral=True)
        lines = []
        for w in rows:
            ts = to_ts(w["created_at"])
            state = "🟠 активен" if w["active"] else "⚪ снят/истёк"
            lines.append(f"`#{w['id']}` {state} · <t:{ts}:d> · от <@{w['admin_id']}>\n└ {w['reason'][:120]}")
        await inter.response.send_message(
            embed=disnake.Embed(title=f"Выговоры: {user.display_name}", description="\n".join(lines),
                                color=embed_color()), ephemeral=True)

    # --- чёрный список ---
    @staff.sub_command_group(name="blacklist", description="Чёрный список")
    async def blacklist(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @blacklist.sub_command(name="add", description="Добавить в чёрный список")
    async def bl_add(self, inter: disnake.ApplicationCommandInteraction, static: str, reason: str,
                     user: Optional[disnake.User] = None, nick: Optional[str] = None):
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        static = static.strip()
        entry_id = await q_exec("INSERT INTO blacklist (user_id,static_id,nick,reason,admin_id) VALUES (?,?,?,?,?)",
                                (user.id if user else None, static, nick, reason, inter.author.id))
        await self.log("⛔ Чёрный список: добавлен",
                       f"**Статик:** `{static}`\n**Ник:** {nick or '—'}\n**Причина:** {reason}\n"
                       f"**Добавил:** {inter.author.mention} · `#{entry_id}`", 0xE74C3C)
        await inter.response.send_message(f"⛔ Статик `{static}` добавлен в чёрный список (`#{entry_id}`).",
                                          ephemeral=True)

    @blacklist.sub_command(name="remove", description="Убрать запись из чёрного списка по номеру")
    async def bl_remove(self, inter: disnake.ApplicationCommandInteraction, entry_id: int):
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("DELETE FROM blacklist WHERE id=?", (entry_id,))
            await db.commit()
        if cur.rowcount:
            await self.log("✅ Чёрный список: запись удалена", f"`#{entry_id}` · {inter.author.mention}", 0x2ECC71)
            await inter.response.send_message("✅ Запись удалена.", ephemeral=True)
        else:
            await inter.response.send_message("❌ Запись не найдена. Записи об увольнениях "
                                              "удаляются только вручную из базы.", ephemeral=True)

    @blacklist.sub_command(name="list", description="Показать чёрный список и уволенных")
    async def bl_list(self, inter: disnake.ApplicationCommandInteraction):
        if not is_staff(inter.author):
            return await inter.response.send_message("❌ Нет доступа.", ephemeral=True)
        bl = await q_all("SELECT id, nick, static_id, reason FROM blacklist ORDER BY id DESC LIMIT 15")
        fr = await q_all("SELECT nick, static_id, reason, kind FROM fired_members ORDER BY id DESC LIMIT 15")
        e = disnake.Embed(title="⛔ Чёрный список", color=0xE74C3C)
        e.add_field("Ручные записи", "\n".join(
            f"`#{r['id']}` `{r['nick'] or '—'}` / `{r['static_id']}` — {r['reason'][:80]}" for r in bl) or "пусто",
                    inline=False)
        e.add_field("Уволены / ушли", "\n".join(
            f"{'🚪' if r['kind'] == 'fired' else '↩️'} `{r['nick'] or '—'}` / `{r['static_id']}` — "
            f"{(r['reason'] or 'без причины')[:80]}" for r in fr) or "пусто", inline=False)
        await inter.response.send_message(embed=e, ephemeral=True)

    @blacklist.sub_command(name="check", description="Проверить статик")
    async def bl_check(self, inter: disnake.ApplicationCommandInteraction, static: str):
        if not is_staff(inter.author):
            return await inter.response.send_message("❌ Нет доступа.", ephemeral=True)
        hits = await blacklist_hits(static)
        if not hits:
            return await inter.response.send_message(f"✅ Статик `{static}` чист.", ephemeral=True)
        await inter.response.send_message(
            embed=disnake.Embed(title=f"Статик {static}: есть записи", description="\n\n".join(map(hit_line, hits)),
                                color=0xE74C3C), ephemeral=True)

    # --- голосовой онлайн ---
    @staff.sub_command(name="voice", description="Голосовой онлайн участника")
    async def voice(self, inter: disnake.ApplicationCommandInteraction, user: Optional[disnake.Member] = None):
        if not is_staff(inter.author):
            return await inter.response.send_message("❌ Нет доступа.", ephemeral=True)
        user = user or inter.author
        v7, v30, vall = (await voice_seconds(user.id, p) for p in ("week", "month", "all"))
        e = disnake.Embed(title=f"🎙️ Голосовой онлайн: {user.display_name}", color=embed_color())
        e.add_field("7 дней", fmt_dur(v7)).add_field("30 дней", fmt_dur(v30)).add_field("Всего", fmt_dur(vall))
        await inter.response.send_message(embed=e, ephemeral=True)

    @staff.sub_command(name="voicetop", description="Топ по голосовому онлайну")
    async def voicetop(self, inter: disnake.ApplicationCommandInteraction,
                       period: str = commands.Param(default="week",
                                                    choices={"Неделя": "week", "Месяц": "month", "Всё время": "all"})):
        if not is_staff(inter.author):
            return await inter.response.send_message("❌ Нет доступа.", ephemeral=True)
        rows = await q_all(
            "SELECT user_id, SUM(seconds) s FROM voice_daily WHERE day>=date('now',?) "
            "GROUP BY user_id ORDER BY s DESC LIMIT 10", (PERIODS[period][0],))
        medals = ["🥇", "🥈", "🥉"]
        text = "\n".join(f"{medals[i] if i < 3 else f'`{i + 1}.`'} <@{r['user_id']}> — **{fmt_dur(r['s'])}**"
                         for i, r in enumerate(rows)) or "Пока нет данных."
        await inter.response.send_message(
            embed=disnake.Embed(title=f"🎙️ Топ голосового онлайна — {PERIODS[period][2]}", description=text,
                                color=embed_color()), ephemeral=True)

    # --- рекрутеры ---
    @staff.sub_command(name="recruiters", description="Статистика рекрутеров")
    async def recruiters(self, inter: disnake.ApplicationCommandInteraction,
                         period: str = commands.Param(default="week",
                                                      choices={"Неделя": "week", "Месяц": "month", "Всё время": "all"})):
        if not is_staff(inter.author):
            return await inter.response.send_message("❌ Нет доступа.", ephemeral=True)
        rows = await q_all(
            "SELECT reviewer_id, SUM(status='accepted') acc, SUM(status='rejected') rej FROM applications "
            "WHERE reviewer_id IS NOT NULL AND status IN ('accepted','rejected') AND closed_at>=datetime('now',?) "
            "GROUP BY reviewer_id ORDER BY (acc+rej) DESC LIMIT 10", (PERIODS[period][1],))
        medals = ["🥇", "🥈", "🥉"]
        text = "\n".join(f"{medals[i] if i < 3 else f'`{i + 1}.`'} <@{r['reviewer_id']}> — "
                         f"✅ {r['acc']} · ❌ {r['rej']} · всего **{r['acc'] + r['rej']}**"
                         for i, r in enumerate(rows)) or "Пока нет данных."
        await inter.response.send_message(
            embed=disnake.Embed(title=f"📋 Рекрутеры — {PERIODS[period][2]}", description=text,
                                color=embed_color()), ephemeral=True)

    # --- живая статистика ---
    @staff.sub_command(name="board", description="Создать самообновляющееся сообщение со статистикой")
    async def board(self, inter: disnake.ApplicationCommandInteraction, channel: disnake.TextChannel):
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        msg = await channel.send(embed=await self.build_board())
        s = load_settings()
        s["stats_channel_id"], s["stats_message_id"] = channel.id, msg.id
        save_settings(s)
        await inter.edit_original_message(f"✅ Статистика создана в {channel.mention} и обновляется каждые 5 минут.")

    async def build_board(self) -> disnake.Embed:
        ranks = await q_all("SELECT rank, COUNT(*) n FROM family_members GROUP BY rank ORDER BY rank DESC")
        total = sum(r["n"] for r in ranks)
        rank_txt = "\n".join(f"{rank_name(r['rank'])}: **{r['n']}**" for r in ranks) or "—"
        sent = (await q_one("SELECT COUNT(*) n FROM applications WHERE created_at>=datetime('now','-7 days')"))["n"]
        acc = (await q_one("SELECT COUNT(*) n FROM applications WHERE status='accepted' "
                           "AND closed_at>=datetime('now','-7 days')"))["n"]
        rej = (await q_one("SELECT COUNT(*) n FROM applications WHERE status='rejected' "
                           "AND closed_at>=datetime('now','-7 days')"))["n"]
        pend = (await q_one("SELECT COUNT(*) n FROM applications WHERE status IN ('pending','in_review')"))["n"]
        leaves = await q_all("SELECT DISTINCT user_id FROM leaves WHERE status='approved' AND until_ts>?",
                             (int(time.time()),))
        warns = (await q_one("SELECT COUNT(*) n FROM warns WHERE active=1"))["n"]
        vtotal = (await q_one("SELECT COALESCE(SUM(seconds),0) s FROM voice_daily "
                              "WHERE day>=date('now','-6 days')"))["s"]
        e = disnake.Embed(title=f"📊 Статистика {cfg().get('bot_name', 'Hall FAMQ')}", color=embed_color(),
                          timestamp=datetime.now(timezone.utc))
        e.add_field(f"👥 Состав: {total}", rank_txt, inline=True)
        e.add_field("📋 Заявки за 7 дней",
                    f"Подано: **{sent}**\nПринято: **{acc}**\nОтклонено: **{rej}**\nВ очереди: **{pend}**", inline=True)
        e.add_field("🌴 В отпуске", "\n".join(f"<@{r['user_id']}>" for r in leaves[:10]) or "никого", inline=True)
        e.add_field("⚠️ Активных выговоров", f"**{warns}**", inline=True)
        e.add_field("🎙️ Голос за 7 дней", f"**{fmt_dur(vtotal)}** суммарно", inline=True)
        e.set_footer(text="Обновляется автоматически")
        return e

    # ───────────── ФОНОВЫЕ ЗАДАЧИ ─────────────
    @tasks.loop(minutes=5)
    async def board_update(self):
        s = load_settings()
        if not s.get("stats_channel_id"):
            return
        try:
            ch = self.bot.get_channel(s["stats_channel_id"]) or await self.bot.fetch_channel(s["stats_channel_id"])
            embed = await self.build_board()
            try:
                msg = await ch.fetch_message(s["stats_message_id"])
                await msg.edit(embed=embed)
            except disnake.NotFound:
                msg = await ch.send(embed=embed)
                s["stats_message_id"] = msg.id
                save_settings(s)
        except Exception as e:
            print(f"staff_tools: ошибка обновления статистики: {e}", file=sys.stderr, flush=True)

    @tasks.loop(hours=1)
    async def warn_expiry(self):
        days = int(cfg().get("warn_expire_days", 30))
        if days > 0:
            await q_exec("UPDATE warns SET active=0 WHERE active=1 AND created_at<=datetime('now',?)",
                         (f"-{days} days",))

    @tasks.loop(seconds=60)
    async def blacklist_watch(self):
        """Проверяет новые анкеты на совпадение со списком; ничего не требует от модуля recruitment."""
        try:
            apps = await q_all("SELECT * FROM applications WHERE status IN ('pending','in_review') "
                               "AND id NOT IN (SELECT app_id FROM blacklist_alerts)")
            for a in apps:
                hits = await blacklist_hits(a["static_id"], a["user_id"])
                await q_exec("INSERT OR IGNORE INTO blacklist_alerts (app_id) VALUES (?)", (a["id"],))
                if hits:
                    await self.alert_blacklist(a, hits)
        except Exception as e:
            print(f"staff_tools: ошибка проверки чёрного списка: {e}", file=sys.stderr, flush=True)

    async def alert_blacklist(self, app: Dict[str, Any], hits: List[Dict[str, Any]]):
        pings = " ".join(f"<@&{r}>" for r in cfg().get("recruiter_role_ids", []))
        embed = disnake.Embed(
            title="🚨 Внимание: кандидат есть в списках",
            description=f"Кандидат <@{app['user_id']}> · `{app['nick']}` · статик `{app['static_id']}`\n\n"
                        + "\n\n".join(map(hit_line, hits)), color=0xE74C3C)
        target = None
        if app.get("thread_id"):
            try:
                target = self.bot.get_channel(app["thread_id"]) or await self.bot.fetch_channel(app["thread_id"])
            except Exception:
                target = None
        if target is None:
            target = self.bot.get_channel(cfg().get("channels", {}).get("logs_channel_id", 0))
        if target:
            await target.send(content=pings, embed=embed,
                              allowed_mentions=disnake.AllowedMentions(roles=True))

    # ── голосовой учёт ──
    async def _credit(self, uid: int, start: float, end: float):
        cur = start
        while cur < end:
            d = datetime.fromtimestamp(cur, timezone.utc)
            midnight = (d.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()
            seg = min(end, midnight) - cur
            if int(seg) > 0:
                await q_exec("INSERT INTO voice_daily (user_id,day,seconds) VALUES (?,?,?) "
                             "ON CONFLICT(user_id,day) DO UPDATE SET seconds=seconds+excluded.seconds",
                             (uid, d.strftime("%Y-%m-%d"), int(seg)))
            cur += seg

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: disnake.Member, before: disnake.VoiceState,
                                    after: disnake.VoiceState):
        if member.bot:
            return
        ids = {v["id"] for v in cfg().get("voice_channels", [])}
        was = before.channel is not None and before.channel.id in ids
        now_in = after.channel is not None and after.channel.id in ids
        if now_in and not was:
            self.sessions[member.id] = time.time()
        elif was and not now_in:
            start = self.sessions.pop(member.id, None)
            if start:
                await self._credit(member.id, start, time.time())

    @tasks.loop(minutes=5)
    async def voice_checkpoint(self):
        """Сохраняет накопленное время, чтобы при падении бота ничего не терялось."""
        now = time.time()
        for uid in list(self.sessions):
            old = self.sessions.get(uid)
            if old is None:
                continue
            self.sessions[uid] = now
            await self._credit(uid, old, now)

    @voice_checkpoint.before_loop
    @warn_expiry.before_loop
    @blacklist_watch.before_loop
    @board_update.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()


def setup(bot: commands.Bot):
    bot.add_cog(StaffTools(bot))
