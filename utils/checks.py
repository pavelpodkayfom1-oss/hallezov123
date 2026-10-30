import json
import os
import re
from typing import Dict, Any, Optional
import disnake

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")

def load_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Ошибка загрузки config.json: {e}")
        return {}

def save_config(config: Dict[str, Any]):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения config.json: {e}")

def get_text(key: str, default: str = "") -> str:
    config = load_config()
    texts = config.get("texts", {})
    return texts.get(key, default)

_PH_RE = re.compile(r"\{(\w+)\}")

def fmt_text(key: str, default: str = "", **values: Any) -> str:
    """Как get_text, но подставляет {имя} из values.

    Подстановка за один проход: значение не перепроверяется на «скобки», а незнакомое
    {слово}, которое человек вписал в панели, остаётся как есть и бота не роняет.
    Ключ и дефолт должны быть простыми строками: каталог «Тексты» в веб-панели находит
    надписи по этим вызовам и берёт имена подстановок из values.
    """
    text = get_text(key, default)
    if not values:
        return text
    return _PH_RE.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), text)

# Названия рангов, которые важнее config.json (деплой из git не откатит их снимком).
RANK_NAME_OVERRIDES: Dict[int, str] = {4: "Recruit"}

def rank_title(config: Dict[str, Any], rank: int, default: Optional[str] = None) -> str:
    """Название ранга: сперва переопределение из кода, затем config.json["ranks"]."""
    if rank in RANK_NAME_OVERRIDES:
        return RANK_NAME_OVERRIDES[rank]
    name = ((config.get("ranks", {}) or {}).get(str(rank), {}) or {}).get("name")
    return name or (default if default is not None else f"{rank} ранг")

def is_admin(user_or_inter: Any) -> bool:
    """
    Проверяет, является ли пользователь администратором:
    1. Владелец сервера
    2. ID пользователя в admin_user_ids
    3. У пользователя есть роль из admin_role_ids
    """
    # Определяем, передали нам взаимодействие (Interaction) или просто пользователя (Member/User)
    if isinstance(user_or_inter, (disnake.ApplicationCommandInteraction, disnake.MessageInteraction)):
        user = user_or_inter.author
        guild = user_or_inter.guild
    elif isinstance(user_or_inter, disnake.Member):
        user = user_or_inter
        guild = user.guild
    else:
        return False

    if guild is None or user is None:
        return False

    if user.id == guild.owner_id:
        return True

    config = load_config()
    admin_users = config.get("admin_user_ids", [])
    if user.id in admin_users:
        return True

    admin_roles = config.get("admin_role_ids", [])
    if isinstance(user, disnake.Member):
        for role in user.roles:
            if role.id in admin_roles:
                return True

    return False

def is_recruiter(member: disnake.Member) -> bool:
    """
    Проверяет, имеет ли пользователь права рекрутера или админа.
    """
    if member.id == member.guild.owner_id:
        return True
    config = load_config()
    admin_users = config.get("admin_user_ids", [])
    if member.id in admin_users:
        return True
    allowed_roles = set(config.get("admin_role_ids", []) + config.get("recruiter_role_ids", []))
    return any(role.id in allowed_roles for role in member.roles)
