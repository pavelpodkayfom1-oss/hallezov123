"""
Брендовые эмодзи в текстах и кнопках: токен `:hz_lock:` подставляется живым эмодзи сервера.

Токены вместо готовых `<:имя:id>` нужны потому, что id меняется при каждой перезагрузке
пака, а имя остаётся. Список эмодзи берётся из кэша бота (он приходит в READY), поэтому
файловых чтений нет — на слабом хосте это важно.

Если эмодзи на сервере нет, токен исчезает из текста, а не показывается пользователю
как `:hz_lock:`; в кнопке в этом случае работает unicode-заглушка.
"""
import re
from typing import Optional

import disnake

_TOKEN_RE = re.compile(r"(?<![\w<]):([A-Za-z0-9_]{2,32}):(?!\d+>)")


def _table(bot) -> dict:
    return {e.name.lower(): str(e) for e in getattr(bot, "emojis", ()) or () if e.name}


def emoji(bot, name: str, fallback: str = "") -> str:
    """`:name:`/`name` → готовый markdown эмодзи сервера, иначе fallback."""
    if not bot or not name:
        return fallback
    return _table(bot).get(name.strip(":").lower(), fallback)


def E(bot, text: Optional[str]) -> str:
    """Заменяет все `:имя:` в тексте. Незнакомые токены убирает, готовые `<:имя:id>` не трогает."""
    if not text or bot is None:
        return text or ""
    table = _table(bot)
    if not table:
        # до готовности бота списка эмодзи нет — не вычищаем токены в пустоту
        return text
    return _TOKEN_RE.sub(lambda m: table.get(m.group(1).lower(), ""), text)


def btn_emoji(bot, name: str, fallback: str = ""):
    """Эмодзи для кнопки: PartialEmoji сервера, либо unicode-строка-заглушка (None если и её нет)."""
    found = emoji(bot, name)
    if found:
        try:
            return disnake.PartialEmoji.from_str(found)
        except Exception:
            pass
    return fallback or None


def paint(bot, view, mapping: dict) -> None:
    """Разукрашивает кнопки вьюхи эмодзи сервера: {custom_id: ("hz_book", "📖")}.

    Вьюхи описываются декораторами, где эмодзи задаётся статично, — так что подменяем
    его уже собранном view. Декораторный unicode остаётся заглушкой, пока бот не знает
    про такой эмодзи (например, до готовности бота или если пак переименовали).
    """
    if bot is None:
        return
    for child in getattr(view, "children", []):
        item = mapping.get(getattr(child, "custom_id", None))
        if item and hasattr(child, "emoji"):
            child.emoji = btn_emoji(bot, item[0], item[1] if len(item) > 1 else "")

