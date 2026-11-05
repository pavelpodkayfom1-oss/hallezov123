"""Привязки сервера: у основного сервера — config.json, у всех остальных — свои.

Зачем отдельный файл: config.json исторически один на весь бота. В нём лежат id каналов
и ролей, а id принадлежат ровно одному серверу. Как только бот заходит на второй сервер,
его админы видят каналы и роли первого: заявка улетает в чужой канал, а любая привязка
на новом сервере перезаписывает настройки старых.

Правила, которые здесь главные:
- основной сервер (тот, чьи id лежат в config.json) живёт ровно как раньше — overlay его
  не читает и не пишет, чтобы ни одна настройка Hallez не поехала;
- для чужого сервера берутся ТОЛЬКО его собственные привязки. Наследовать значения
  основного нельзя: «наследовал» означает совать заявки в канал чужого сервера;
- тексты, цвета, названия и баннеры остаются общими — они не привязаны к серверу.

Файл guild_settings.json: {"<id сервера>": {"channels": {...}, "roles": {...},
"role_ids": {...}, "published": {...}, "by": <кто привязал>, "at": "<когда>"}}.
"""
from __future__ import annotations

import contextvars
import json
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_PATH = os.path.join(BASE_DIR, "guild_settings.json")

SECTIONS = ("channels", "roles", "role_ids", "published")
# О привязках спрашивают на экранах; published — служебный (какое сообщение перезаписывать)
BIND_SECTIONS = ("channels", "roles", "role_ids")

_GUILD: contextvars.ContextVar[int] = contextvars.ContextVar("hz_guild_now", default=0)
# RLock, а не Lock: set_one держит блокировку и вызывает _save, который тоже её берёт
_LOCK = threading.RLock()
_CACHE: Dict[str, Any] = {"mt": None, "size": None, "at": 0.0, "data": None}
_BOT: Dict[str, Any] = {"bot": None}

# Что привязывают админы: (ключ, подпись, чем является в Discord)
BIND_CHANNELS = [
    ("panel", "Панель заявки — где кнопка «Подать заявку»", "text"),
    ("review", "Карточки заявок для проверяющих", "text"),
    ("orders", "Заказы и приглашения", "text"),
    ("points_panel", "Панель «Рекрут-центр» (баллы и магазин)", "text"),
    ("rules", "Пост с правилами семьи", "text"),
    ("cdn", "Служебный канал для баннеров", "text"),
    ("recruitment_channel_id", "Канал подачи заявок (из старого конфига)", "text"),
    ("logs_channel_id", "Канал логов семьи", "text"),
    ("promotion_channel_id", "Канал отчётов на повышение", "text"),
    ("promotion_review_channel_id", "Канал приёмки повышений", "text"),
    ("events_channel_id", "Канал сборов на МП", "text"),
]
BIND_ROLES = [
    ("family_role_id", "Роль «семья» — выдаётся при принятии", "role"),
    ("rank_1_role_id", "Роль 1 ранга (Новичок)", "role"),
    ("rank_2_role_id", "Роль 2 ранга (Участник)", "role"),
    ("rank_3_role_id", "Роль 3 ранга (Опытный)", "role"),
    ("recruiter_role_ids", "Роли проверяющих заявки", "roles"),
    ("admin_role_ids", "Роли администраторов бота", "roles"),
]


def _err(text: str):
    print(f"[guild_cfg] {text}", file=sys.stderr, flush=True)


# Экраны показывают 17 пунктов одним списком тяжело; на сайте их режут по этим группам.
CHANNEL_GROUPS = [
    ("Каналы набора", ("panel", "review", "orders", "points_panel", "rules", "cdn",
                        "recruitment_channel_id")),
    ("Каналы семьи", ("logs_channel_id", "promotion_channel_id",
                      "promotion_review_channel_id", "events_channel_id")),
]


def group_of(key: str) -> str:
    for name, keys in CHANNEL_GROUPS:
        if key in keys:
            return name
    return "Роли"


# ────────────────────────── текущий сервер ──────────────────────────

def install(bot) -> None:
    """Ставит на бота хук: любой interaction узнаёт, с какого он сервера.

    Делается здесь, а не в 190 местах кода: disnake вызывает bot.dispatch для каждого
    события, а asyncio.Task копирует контекст при создании. Значит, корутина-обработчик
    уже видит свой сервер, и вложенные вызовы (проверка прав, выбор канала) берут его же.
    После dispatch переменная возвращается — иначе фоновые циклы дорабатывали бы чужой
    сервер, оставшийся от последней кнопки.
    """
    if getattr(bot, "_hz_guild_hook", False):
        return
    _BOT["bot"] = bot
    old = bot.dispatch

    def dispatch(event: str, *args, **kwargs):
        gid = 0
        for one in list(args) + list(kwargs.values()):
            g = getattr(one, "guild", None)
            if g is not None and getattr(g, "id", 0):
                gid = int(g.id)
                break
            gid = int(getattr(one, "guild_id", 0) or 0) or gid
        token = _GUILD.set(gid) if gid else None
        try:
            return old(event, *args, **kwargs)
        finally:
            if token is not None:
                _GUILD.reset(token)

    bot._hz_guild_hook = True
    bot.dispatch = dispatch


def set_guild(gid: Any) -> int:
    """Запоминает сервер текущего запроса (им им пользуются маршруты сайта)."""
    try:
        key = int(str(gid or "").strip())
    except (TypeError, ValueError):
        key = 0
    _GUILD.set(key)
    return key


def guild_now() -> int:
    """Сервер, из которого сейчас выполняется код: кнопка, запрос с сайта или 0."""
    got = _GUILD.get()
    if got:
        return int(got)
    from utils import web_tools

    picked = web_tools.picked()
    return int(picked) if str(picked or "").isdigit() else 0


# ────────────────────────── основной сервер ──────────────────────────

def home_id() -> int:
    try:
        from utils.checks import load_config

        return int(load_config().get("guild_id") or 0)
    except Exception:
        return 0


def is_home(gid: Any) -> bool:
    """Тот ли это сервер, чьи настройки лежат в config.json.

    Ключа guild_id в живом конфиге исторически может не быть, тогда сервер узнаём по
    id каналов и ролей из config.json: они принадлежат ровно одному серверу.
    """
    try:
        key = int(str(gid or "").strip())
    except (TypeError, ValueError):
        return False
    if not key:
        return False
    hid = home_id()
    if hid:
        return key == hid
    try:
        from utils import web_tools
        from utils.checks import load_config

        ids = set(web_tools._config_ids(load_config()))
    except Exception:
        return False
    if not ids:
        return False
    bot = _BOT.get("bot")
    guild = getattr(bot, "get_guild", lambda _: None)(key) if bot is not None else None
    if guild is None:
        return False
    for cid in ids:
        if guild.get_channel(cid) is not None or guild.get_role(cid) is not None:
            return True
    return False


def home_note() -> str:
    return ("Это основной сервер: его привязки лежат в config.json и в recruit_plus.json, "
            "здесь они только для чтения.")


# ────────────────────────── хранилище ──────────────────────────

def _raw() -> Dict[str, Any]:
    try:
        st = os.stat(SETTINGS_PATH)
    except OSError:
        return {}
    c, now = _CACHE, time.monotonic()
    if c["data"] is not None and c["mt"] == st.st_mtime_ns and c["size"] == st.st_size \
            and now - c["at"] < 2.0:
        return c["data"]
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except Exception as e:
        _err(f"не удалось прочитать {os.path.basename(SETTINGS_PATH)}: {e}")
        return c["data"] or {}
    c.update(mt=st.st_mtime_ns, size=st.st_size, at=now, data=data)
    return data


def _save(data: Dict[str, Any]) -> Optional[str]:
    tmp = SETTINGS_PATH + ".tmp"
    try:
        with _LOCK:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, SETTINGS_PATH)
        _CACHE.update(mt=None, size=None, at=0.0, data=None)
        try:
            from utils import state_backup

            state_backup.mark_dirty()
        except Exception:
            pass
        return None
    except Exception as e:
        _err(f"не удалось сохранить привязки: {e}")
        return f"Не удалось сохранить: {str(e)[:80]}"


def where(key: str) -> tuple:
    """(раздел, kind) пункта привязки — экранам не нужно искать ключ в двух списках."""
    for k, _label, kind in BIND_CHANNELS:
        if k == key:
            return ("channels", kind)
    for k, _label, kind in BIND_ROLES:
        if k == key:
            return ("roles" if kind == "role" else "role_ids", kind)
    return ("channels", "text")


def labels() -> List[tuple]:
    """Все пункты привязки одним списком: (ключ, подпись, kind)."""
    return [(k, lbl, kind) for k, lbl, kind in list(BIND_CHANNELS) + list(BIND_ROLES)]


def of(gid: Any) -> Dict[str, Any]:
    """Привязки одного сервера; у основного их нет — там работает config.json."""
    if is_home(gid):
        return {}
    out = _raw().get(str(gid or "")) or {}
    return out if isinstance(out, dict) else {}


def get(gid: Any, section: str, key: str, default: Any = None) -> Any:
    one = of(gid).get(section) or {}
    val = one.get(key)
    return default if val is None else val


def set_one(gid: Any, section: str, key: str, value: Any, by: Any = 0) -> Optional[str]:
    """Пишет привязку чужого сервера. Основной не трогаем никогда."""
    if not str(gid or "").strip().isdigit():
        return "Непонятно, какому серверу привязывать — открой панель на нужном сервере."
    if is_home(gid):
        return home_note()
    if section not in SECTIONS:
        return f"Неизвестный раздел привязок: {section}"
    key = str(key or "").strip()
    if not key:
        return "Не указано, что привязывать."
    data = dict(_raw())
    one = dict(data.get(str(gid)) or {})
    part = dict(one.get(section) or {})
    if value in (0, "", None, []):
        part.pop(key, None)
    else:
        part[key] = value
    one[section] = part
    one["by"] = int(by or 0)
    one["at"] = time.strftime("%d.%m.%Y %H:%M")
    data[str(gid)] = one
    with _LOCK:
        return _save(data)


def drop(gid: Any) -> Optional[str]:
    """Забывает привязки сервера — например, когда бота попросили из него."""
    data = dict(_raw())
    if str(gid or "") not in data:
        return None
    data.pop(str(gid), None)
    with _LOCK:
        return _save(data)


def servers() -> List[str]:
    return sorted(_raw().keys())


# ────────────────────────── удобный доступ ──────────────────────────

def chan(key: str, gid: Any = None, default: int = 0) -> int:
    """Канал привязки сервера. Для основного — 0: там читают config.json сами."""
    gid = int(gid or guild_now() or 0)
    if not gid or is_home(gid):
        return 0
    return int(get(gid, "channels", key, 0) or default or 0)


def role(key: str, gid: Any = None) -> int:
    gid = int(gid or guild_now() or 0)
    if not gid or is_home(gid):
        return 0
    return int(get(gid, "roles", key, 0) or 0)


def role_ids(key: str, gid: Any = None) -> List[int]:
    """Роли по списку: у основного сервера пусто — список берётся из config.json."""
    gid = int(gid or guild_now() or 0)
    if not gid or is_home(gid):
        return []
    val = get(gid, "role_ids", key, []) or []
    return [int(x) for x in val if str(x or "").strip()]


def bound(gid: Any) -> Dict[str, int]:
    """Сколько чего привязано: {раздел: количество} — для подсказок на экранах."""
    one = of(gid)
    return {k: len([v for v in (one.get(k) or {}).values() if v]) for k in BIND_SECTIONS}


def missing(gid: Any) -> List[str]:
    """Что не привязано и потому не работает: подписи из BIND_CHANNELS/BIND_ROLES."""
    one = of(gid)
    ch, ro, rl = (one.get("channels") or {}), (one.get("roles") or {}), (one.get("role_ids") or {})
    out = [label for key, label, _ in BIND_CHANNELS if not ch.get(key)]
    out += [label for key, label, kind in BIND_ROLES
            if not ((rl if kind == "roles" else ro).get(key))]
    return out
