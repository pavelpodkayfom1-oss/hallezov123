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

# Анимированные стрелки грузятся на сервер не всегда (GIF-эмодзи требуют нитро), а текст с
# таким токеном должен оставаться читаемым: не нашли эмодзи — рисуем статичный аналог.
ALIAS = {
    "hz_animatedarrowblue": "hz_bluetick",
    "hz_animatedarrowgreen": "hz_bluecheckmark",
    "hz_animatedarrowpurple": "hz_starids",
    "hz_animatedarrowred": "hz_forbidden",
    "hz_animatedarrowwhite": "hz_gradientarrowright",
    "hz_animatedarrowyellow": "hz_starblue",
}


def _table(bot) -> dict:
    return {e.name.lower(): str(e) for e in getattr(bot, "emojis", ()) or () if e.name}


def found(table: dict, name: str) -> str:
    """Эмодзи по имени, с одной заменой на аналог, если такого на сервере нет."""
    low = (name or "").strip(":").lower()
    for _ in range(2):
        hit = table.get(low)
        if hit:
            return hit
        low = ALIAS.get(low, "")
        if not low:
            return ""
    return ""


def emoji(bot, name: str, fallback: str = "") -> str:
    """`:name:`/`name` → готовый markdown эмодзи сервера, иначе fallback."""
    if not bot or not name:
        return fallback
    return found(_table(bot), name) or fallback


def pick(bot, *names: str) -> str:
    """Первый эмодзи из списка имён, который реально есть на сервере.

    Нужен там, где знак несёт смысл (старшинство в составе): если одного эмодзи нет,
    берём ближайший запасной, а не оставляем в тексте служебный токен. Пустая строка — если
    нет ни одного; вызывающий код тогда рисует без знаков.
    """
    if not bot:
        return ""
    table = _table(bot)
    for name in names:
        hit = found(table, name)
        if hit:
            return hit
    return ""


def E(bot, text: Optional[str]) -> str:
    """Заменяет все `:имя:` в тексте. Незнакомые токены убирает, готовые `<:имя:id>` не трогает."""
    if not text or bot is None:
        return text or ""
    table = _table(bot)
    if not table:
        # до готовности бота списка эмодзи нет — не вычищаем токены в пустоту
        return text
    return _TOKEN_RE.sub(lambda m: found(table, m.group(1)), text)


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

