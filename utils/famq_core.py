"""
Общее ядро новых модулей Hallez FAMQ:
  * panel_settings.json  — все настройки, которые меняются из /центр
  * дополнительные таблицы БД (отпуска, войс, казна, розыгрыши)
  * вспомогательные функции (права, ранги, время, логи)
Ничего из старых файлов не меняет.
"""
import os
import re
import json
import time
import copy
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite
import disnake
from disnake.ext import commands

import database
from utils.checks import load_config

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_PATH = os.path.join(BASE_DIR, "panel_settings.json")


class WarnGuardBlocked(commands.CheckFailure):
    """Команда выговора остановлена: у участника роль отпуска/АФК."""


# ───────────────────────── НАСТРОЙКИ ─────────────────────────

DEFAULTS: Dict[str, Any] = {
    "general": {"tz_offset": 5, "ignore_bots": True},
    "logs": {
        "default": 0, "join": 0, "leave": 0, "messages": 0, "voice": 0,
        "roles": 0, "nicknames": 0, "moderation": 0, "server": 0,
        "commands": 0, "bot": 0, "audit": 0, "vacation": 0,
        "treasury": 0, "giveaway": 0,
    },
    "autorole": {"enabled": True, "roles": [], "bots": 0},
    "vacation": {
        "role_id": 0, "afk_role_id": 0, "review_channel_id": 0,
        "panel_channel_id": 0, "panel_message_id": 0,
        "reviewer_roles": [], "min_days": 1, "max_days": 30,
    },
    "warn_guard": {"enabled": True, "keywords": "warn,варн,выговор"},
    "rank_roles": {},
    "rank_sync": {"auto": True},
    "candidates": {
        "enabled": True, "channel_id": 0, "min_days": 14, "min_voice_hours": 3,
        "max_warns": 0, "max_from_rank": 2, "weekday": 6, "hour": 18, "last_run": "",
    },
    "treasury": {
        "channel_id": 0, "weekly_due": 0, "weekday": 6, "hour": 19,
        "report_enabled": True, "dm_debtors": True, "last_run": "",
    },
    "giveaway": {"channel_id": 0, "ping_role_id": 0},
}

_cache: Optional[Dict[str, Any]] = None


def _read_json(name: str) -> Dict[str, Any]:
    try:
        with open(os.path.join(BASE_DIR, name), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _bootstrap(data: Dict[str, Any]) -> None:
    """Первый запуск: подтягиваем роли рангов и АФК из существующих конфигов."""
    cfg = load_config()
    ranks = cfg.get("ranks", {}) or {}
    rr = data["rank_roles"]
    if not rr:
        for k, v in ranks.items():
            rid = int((v or {}).get("role_id", 0) or 0)
            rr[str(k)] = [rid] if rid else []
        top = str(max([int(k) for k in ranks] or [1]))
        extra: List[int] = []
        for fname in ("hall_settings.json", "hallez_settings.json"):
            for item in _read_json(fname).get("roster_roles", []) or []:
                rid = int(item.get("role_id", 0) or 0)
                if rid and rid not in extra:
                    extra.append(rid)
        rr.setdefault(top, [])
        for rid in extra:
            if rid not in rr[top]:
                rr[top].append(rid)
    if not data["vacation"].get("afk_role_id"):
        for fname in ("hall_settings.json", "hallez_settings.json"):
            rid = int(_read_json(fname).get("afk_role_id", 0) or 0)
            if rid:
                data["vacation"]["afk_role_id"] = rid
                break


def _load() -> Dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    exists = os.path.exists(SETTINGS_PATH)
    raw: Dict[str, Any] = {}
    if exists:
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = {}
    data = _merge(DEFAULTS, raw)
    if not exists:
        _bootstrap(data)
    _cache = data
    if not exists:
        _save()
    return _cache


def _save() -> None:
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_PATH)


def get(path: str, default: Any = None) -> Any:
    cur: Any = _load()
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def set_value(path: str, value: Any) -> None:
    data = _load()
    parts = path.split(".")
    cur = data
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value
    _save()


def default_for(path: str) -> Any:
    cur: Any = DEFAULTS
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return [] if path.startswith("rank_roles") else 0
    return copy.deepcopy(cur)


def ids_list(path: str) -> List[int]:
    return [int(x) for x in (get(path, []) or []) if str(x).isdigit() and int(x) > 0]


# ───────────────────────── ОФОРМЛЕНИЕ ─────────────────────────

def _hex(v: Any, default: int) -> int:
    try:
        return int(str(v), 16) if isinstance(v, str) else int(v)
    except Exception:
        return default


def color(kind: str = "main") -> int:
    cfg = load_config()
    key = {"main": "embed_color", "ok": "success_color", "err": "error_color", "warn": "warning_color"}[kind]
    default = {"main": 0x990000, "ok": 0x2ECC71, "err": 0xE74C3C, "warn": 0xF39C12}[kind]
    return _hex(cfg.get(key, default), default)


def make_embed(title: Optional[str] = None, description: Optional[str] = None,
               kind: str = "main", footer: bool = True, col: Optional[int] = None) -> disnake.Embed:
    e = disnake.Embed(title=title, description=description,
                      color=col if col is not None else color(kind),
                      timestamp=disnake.utils.utcnow())
    if footer:
        cfg = load_config()
        bot_name = cfg.get("bot_name", "Hallez FAMQ")
        text = (cfg.get("texts", {}) or {}).get("footer_text", "{bot_name}")
        try:
            text = text.format(bot_name=bot_name)
        except Exception:
            text = bot_name
        e.set_footer(text=text)
    return e


def clip(text: Any, n: int = 1000) -> str:
    s = str(text) if text is not None else ""
    return s if len(s) <= n else s[: n - 1] + "…"


# ───────────────────────── ВРЕМЯ ─────────────────────────

def now_ts() -> int:
    return int(time.time())


def tz() -> timezone:
    try:
        return timezone(timedelta(hours=float(get("general.tz_offset", 5))))
    except Exception:
        return timezone(timedelta(hours=5))


def local_now() -> datetime:
    return datetime.now(tz())


def week_start_ts() -> int:
    n = local_now()
    start = (n - timedelta(days=n.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())


def fmt_seconds(sec: int) -> str:
    sec = max(0, int(sec))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = []
    if d:
        parts.append(f"{d} д.")
    if h:
        parts.append(f"{h} ч.")
    if m and not d:
        parts.append(f"{m} мин.")
    return " ".join(parts) or "меньше минуты"


_UNITS = {
    "d": 86400, "day": 86400, "days": 86400, "д": 86400, "дн": 86400, "день": 86400, "дня": 86400, "дней": 86400,
    "h": 3600, "hr": 3600, "hour": 3600, "hours": 3600, "ч": 3600, "час": 3600, "часа": 3600, "часов": 3600,
    "m": 60, "min": 60, "mins": 60, "м": 60, "мин": 60, "минут": 60, "минуты": 60,
    "s": 1, "sec": 1, "с": 1, "сек": 1,
}


def parse_duration(text: str) -> Optional[int]:
    """'90m', '2h', '1d12h', '3 дня', '45' (минуты) -> секунды."""
    s = (text or "").strip().lower()
    if not s or not re.fullmatch(r"(\s*\d+\s*[a-zа-яё]*\s*)+", s):
        return None
    total = 0
    for num, unit in re.findall(r"(\d+)\s*([a-zа-яё]*)", s):
        mult = 60 if unit == "" else _UNITS.get(unit)
        if mult is None:
            return None
        total += int(num) * mult
    return total or None


# ───────────────────────── ПРАВА И РОЛИ ─────────────────────────

def is_admin(member: Any) -> bool:
    if member is None:
        return False
    cfg = load_config()
    if member.id in (cfg.get("admin_user_ids", []) or []):
        return True
    perms = getattr(member, "guild_permissions", None)
    if perms is not None and perms.administrator:
        return True
    admin_roles = {int(x) for x in (cfg.get("admin_role_ids", []) or []) if int(x) > 0}
    return any(r.id in admin_roles for r in getattr(member, "roles", []))


def is_staff(member: Any) -> bool:
    if is_admin(member):
        return True
    cfg = load_config()
    ids = {int(x) for x in (cfg.get("recruiter_role_ids", []) or []) if int(x) > 0}
    ids |= set(ids_list("vacation.reviewer_roles"))
    return any(r.id in ids for r in getattr(member, "roles", []))


def rank_roles_map() -> Dict[int, set]:
    out: Dict[int, set] = {}
    for k, v in (get("rank_roles", {}) or {}).items():
        try:
            out[int(k)] = {int(x) for x in v if int(x) > 0}
        except Exception:
            continue
    return out


def member_rank(member: Any) -> int:
    """Ранг по ролям Discord (0 — не в семье или роли не настроены)."""
    have = {r.id for r in getattr(member, "roles", [])}
    best = 0
    for n, roles in rank_roles_map().items():
        if have & roles:
            best = max(best, n)
    return best


def rank_name(n: int) -> str:
    cfg = load_config()
    return ((cfg.get("ranks", {}) or {}).get(str(n), {}) or {}).get("name", f"Ранг {n}")


def max_rank() -> int:
    cfg = load_config()
    try:
        return max([int(k) for k in (cfg.get("ranks", {}) or {})] or [1])
    except Exception:
        return 4


def pause_role_ids() -> Dict[str, int]:
    return {
        "vacation": int(get("vacation.role_id", 0) or 0),
        "afk": int(get("vacation.afk_role_id", 0) or 0),
    }


def pause_kind(member: Any) -> Optional[str]:
    """'vacation' / 'afk' / None — есть ли у участника роль отпуска или АФК."""
    have = {r.id for r in getattr(member, "roles", [])}
    ids = pause_role_ids()
    if ids["vacation"] and ids["vacation"] in have:
        return "vacation"
    if ids["afk"] and ids["afk"] in have:
        return "afk"
    return None


def parse_nick(display_name: str) -> Tuple[str, str]:
    m = re.match(r"^\[[^\]]*\]\s*(.+?)\s*\|\s*(\d+)\s*$", display_name or "")
    if m:
        return m.group(1), m.group(2)
    return display_name, "0"


# ───────────────────────── БАЗА ДАННЫХ ─────────────────────────

_DDL = [
    """CREATE TABLE IF NOT EXISTS vacations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        days INTEGER NOT NULL,
        reason TEXT NOT NULL,
        contact TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        reviewer_id INTEGER,
        reject_reason TEXT,
        message_id INTEGER,
        channel_id INTEGER,
        start_ts INTEGER,
        end_ts INTEGER,
        reminded INTEGER NOT NULL DEFAULT 0,
        created_ts INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS voice_daily (
        user_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        seconds INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, day)
    )""",
    """CREATE TABLE IF NOT EXISTS treasury (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        user_id INTEGER,
        admin_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        comment TEXT,
        created_ts INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS giveaways (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        message_id INTEGER,
        host_id INTEGER NOT NULL,
        prize TEXT NOT NULL,
        winners INTEGER NOT NULL DEFAULT 1,
        min_rank INTEGER NOT NULL DEFAULT 0,
        min_voice_hours INTEGER NOT NULL DEFAULT 0,
        end_ts INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        winner_ids TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS giveaway_entries (
        giveaway_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        PRIMARY KEY (giveaway_id, user_id)
    )""",
]

_db_ready = False
_db_lock: Optional[asyncio.Lock] = None


async def ensure_db() -> None:
    global _db_ready, _db_lock
    if _db_ready:
        return
    if _db_lock is None:
        _db_lock = asyncio.Lock()
    async with _db_lock:
        if _db_ready:
            return
        async with aiosqlite.connect(database.DB_PATH) as db:
            for ddl in _DDL:
                await db.execute(ddl)
            await db.commit()
        _db_ready = True


async def q_all(sql: str, args: tuple = ()) -> List[Dict[str, Any]]:
    await ensure_db()
    async with aiosqlite.connect(database.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def q_one(sql: str, args: tuple = ()) -> Optional[Dict[str, Any]]:
    rows = await q_all(sql, args)
    return rows[0] if rows else None


async def q_exec(sql: str, args: tuple = ()) -> int:
    await ensure_db()
    async with aiosqlite.connect(database.DB_PATH) as db:
        cur = await db.execute(sql, args)
        await db.commit()
        return cur.lastrowid or 0


async def add_voice_seconds(user_id: int, seconds: int) -> None:
    if seconds <= 0:
        return
    day = local_now().strftime("%Y-%m-%d")
    await q_exec(
        "INSERT INTO voice_daily (user_id, day, seconds) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, day) DO UPDATE SET seconds = seconds + excluded.seconds",
        (user_id, day, int(seconds)),
    )


async def voice_seconds(user_id: int, days: Optional[int] = None) -> int:
    if days is None:
        row = await q_one("SELECT COALESCE(SUM(seconds),0) AS s FROM voice_daily WHERE user_id = ?", (user_id,))
    else:
        since = (local_now() - timedelta(days=days)).strftime("%Y-%m-%d")
        row = await q_one(
            "SELECT COALESCE(SUM(seconds),0) AS s FROM voice_daily WHERE user_id = ? AND day >= ?",
            (user_id, since),
        )
    return int(row["s"]) if row else 0


async def treasury_balance(guild_id: int) -> int:
    row = await q_one(
        "SELECT COALESCE(SUM(CASE WHEN kind='deposit' THEN amount ELSE -amount END),0) AS b "
        "FROM treasury WHERE guild_id = ?", (guild_id,))
    return int(row["b"]) if row else 0


# ───────────────────────── ЛОГИ ─────────────────────────

async def send_log(guild: Optional[disnake.Guild], category: str, embed: disnake.Embed) -> bool:
    """Отправляет embed в канал категории (или в общий канал логов)."""
    if guild is None:
        return False
    cid = int(get(f"logs.{category}", 0) or 0) or int(get("logs.default", 0) or 0)
    if not cid:
        return False
    channel = guild.get_channel(cid)
    if channel is None:
        return False
    try:
        await channel.send(embed=embed, allowed_mentions=disnake.AllowedMentions.none())
        return True
    except Exception:
        return False


def fmt_value(guild: Optional[disnake.Guild], kind: str, value: Any) -> str:
    """Как показывать значение настройки в панели."""
    if kind == "channel":
        return f"<#{value}>" if value else "—"
    if kind == "role":
        return f"<@&{value}>" if value else "—"
    if kind == "roles":
        return " ".join(f"<@&{x}>" for x in (value or [])) or "—"
    if kind == "bool":
        return "✅ Вкл" if value else "❌ Выкл"
    return str(value) if value not in (None, "") else "—"


def short_value(guild: Optional[disnake.Guild], kind: str, value: Any) -> str:
    """Короткая версия без упоминаний (для описаний в выпадающем списке)."""
    if kind == "channel":
        ch = guild.get_channel(int(value)) if (guild and value) else None
        return f"#{ch.name}" if ch else "не задан"
    if kind == "role":
        r = guild.get_role(int(value)) if (guild and value) else None
        return f"@{r.name}" if r else "не задана"
    if kind == "roles":
        names = []
        for x in (value or []):
            r = guild.get_role(int(x)) if guild else None
            names.append(r.name if r else str(x))
        return clip(", ".join(names), 60) if names else "не заданы"
    if kind == "bool":
        return "включено" if value else "выключено"
    return clip(value, 60) if value not in (None, "") else "не задано"
