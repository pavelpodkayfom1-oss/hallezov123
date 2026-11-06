"""Кто и куда входит на сайте: у каждого сервера — свой пароль, у разработчика — свой.

Зачем отдельный файл: PANEL_PASSWORD в переменных окружения один на весь бота. Как только
бота добавили на второй сервер, тот же пароль открывал бы чужому человеку чужой сервер,
а «поменять пароль одному серверу» означало бы отключить все остальные.

panel_access.json лежит рядом с ботом на хостинге и НЕ должен попадать в git: репа
публичная. Если файла нет — вход держится на PANEL_PASSWORD, и войти как
разработчик можно всегда (случай «сам себя заблокировал» здесь невозможен).

Файл: {"dev": "<пароль разработчика>",
       "guilds": {"<id сервера>": {"pass": "<пароль>", "name": "<как подписать>"}}}
"""
from __future__ import annotations

import json
import os
import sys
import threading
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACCESS_PATH = os.path.join(BASE_DIR, "panel_access.json")

MIN_PASSWORD_LEN = 8
NAME_MAX = 32
_LOCK = threading.RLock()

DEVELOPER = "dev"


def _err(text: str) -> None:
    print(f"[web_access] {text}", file=sys.stderr, flush=True)


# ────────────────────────── чтение и запись ──────────────────────────

def _read() -> Dict[str, Any]:
    try:
        with open(ACCESS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        _err(f"не удалось прочитать {os.path.basename(ACCESS_PATH)}: {e}")
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: Dict[str, Any]) -> Optional[str]:
    tmp = ACCESS_PATH + ".tmp"
    try:
        with _LOCK:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, ACCESS_PATH)
        return None
    except Exception as e:
        _err(f"не удалось сохранить доступ: {e}")
        return f"Не удалось сохранить: {str(e)[:80]}"


def exists() -> bool:
    return os.path.exists(ACCESS_PATH)


def dev_password() -> str:
    """Пароль разработчика: из файла, а если его нет — PANEL_PASSWORD с хостинга."""
    got = str(_read().get("dev") or "")
    return got or os.getenv("PANEL_PASSWORD", "")


def dev_is_env() -> bool:
    """True, пока пароль разработчика берётся из переменной окружения, а не из файла."""
    return not str(_read().get("dev") or "")


def guild_password(gid: Any) -> str:
    one = (_read().get("guilds") or {}).get(str(gid or "")) or {}
    return str(one.get("pass") or "") if isinstance(one, dict) else ""


def guild_name(gid: Any) -> str:
    one = (_read().get("guilds") or {}).get(str(gid or "")) or {}
    return str(one.get("name") or "") if isinstance(one, dict) else ""


def guild_ids() -> List[str]:
    """Серверы, которым уже выдали пароль, — в порядке добавления."""
    return [str(k) for k in (_read().get("guilds") or {}).keys()]


# ────────────────────────── список для экрана входа ──────────────────────────

def entries(bot) -> List[Dict[str, Any]]:
    """Куда можно войти: каждый сервер бота отдельной плашкой + вход разработчика.

    id серверов наружу не отдаём — экран входа показывает порядковые номера, а пароль
    проверяется уже на стороне бота по этому номеру.
    """
    out: List[Dict[str, Any]] = []
    try:
        guilds = list(getattr(bot, "guilds", None) or [])
    except Exception:
        guilds = []
    homes = {str(g.id) for g in guilds if _is_home(g.id)}
    for g in sorted(guilds, key=lambda g: (str(g.id) not in homes, str(g.name).lower())):
        out.append({
            "scope": str(g.id),
            "name": guild_name(g.id) or str(g.name),
            "members": int(getattr(g, "member_count", 0) or 0),
            "set": bool(guild_password(g.id)),
            "home": str(g.id) in homes,
        })
    out.append({"scope": DEVELOPER, "name": "Разработчик", "members": 0,
                "set": bool(dev_password()), "home": False})
    return out


def scope_at(bot, index: Any) -> str:
    """Плашку экрана входа выбирает человек — по порядковому номеру находим её адресата."""
    try:
        i = int(str(index or "0").strip())
    except (TypeError, ValueError):
        return ""
    one = entries(bot)
    if 1 <= i <= len(one):
        return one[i - 1]["scope"]
    return ""


# ────────────────────────── настройка доступов ──────────────────────────

def set_dev(password: str) -> Optional[str]:
    err = _check_password(password)
    if err:
        return err
    data = _read()
    data["dev"] = str(password)
    return _write(data)


def set_guild(gid: Any, password: str, name: str = "") -> Optional[str]:
    if not str(gid or "").strip().isdigit():
        return "Непонятно, какому серверу выдавать пароль."
    err = _check_password(password)
    if err:
        return err
    data = _read()
    guilds = dict(data.get("guilds") or {})
    one = dict(guilds.get(str(gid)) or {})
    one["pass"] = str(password)
    label = str(name or "").strip()[:NAME_MAX]
    if label:
        one["name"] = label
    guilds[str(gid)] = one
    data["guilds"] = guilds
    return _write(data)


def rename_guild(gid: Any, name: str) -> Optional[str]:
    if not str(gid or "").strip().isdigit():
        return "Непонятный сервер."
    data = _read()
    guilds = dict(data.get("guilds") or {})
    one = dict(guilds.get(str(gid)) or {})
    label = str(name or "").strip()[:NAME_MAX]
    if label:
        one["name"] = label
    else:
        one.pop("name", None)
    guilds[str(gid)] = one
    data["guilds"] = guilds
    return _write(data)


def drop_guild(gid: Any) -> Optional[str]:
    """Сервер без пароля никуда с сайта не войдёт — вход остаётся только у разработчика."""
    data = _read()
    guilds = dict(data.get("guilds") or {})
    if str(gid or "") not in guilds:
        return None
    guilds.pop(str(gid), None)
    data["guilds"] = guilds
    return _write(data)


def _check_password(password: str) -> Optional[str]:
    password = str(password or "")
    if len(password) < MIN_PASSWORD_LEN:
        return f"Пароль короче {MIN_PASSWORD_LEN} символов."
    if len(password) > 64:
        return "Пароль длиннее 64 символов."
    # управляющие символы портят файл и куки, а человек их всё равно не вводил намеренно
    if any(ord(c) < 32 or ord(c) == 127 for c in password):
        return "Пароль содержит недопустимые символы."
    return None


def _is_home(gid: Any) -> bool:
    """Домашний сервер.

    Ключа guild_id в живом config.json исторически нет, и по одному только ему «фама»
    не узнавалась бы никогда — плашка основного сервера врала бы про «основной».
    Спрашиваем guild_cfg: он умеет узнавать сервер по id каналов и ролей из конфига.
    """
    try:
        from utils import guild_cfg

        return bool(guild_cfg.is_home(gid))
    except Exception:
        return False
