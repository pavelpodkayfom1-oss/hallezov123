import json
import os
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

def is_admin(inter: disnake.ApplicationCommandInteraction | disnake.MessageInteraction) -> bool:
    """
    Проверяет, является ли пользователь администратором:
    1. Владелец сервера
    2. ID пользователя в admin_user_ids
    3. У пользователя есть роль из admin_role_ids
    """
    if inter.guild is None or inter.author is None:
        return False
        
    if inter.author.id == inter.guild.owner_id:
        return True

    config = load_config()
    admin_users = config.get("admin_user_ids", [])
    if inter.author.id in admin_users:
        return True

    admin_roles = config.get("admin_role_ids", [])
    if isinstance(inter.author, disnake.Member):
        for role in inter.author.roles:
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
