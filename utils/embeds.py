"""Один стиль карточек для всего бота.

Раньше ответы и панели строились четырьмя способами (base_embed, note_embed, famq_core.make_embed,
локальные make_embed в когах), поэтому в Discord одна и та же семья выглядела четырьмя разными
ботами: у части карточек был логотип и футер, у части — нет. Здесь собираем всё в одно место,
а коги только вызывают эти функции.

config.json перечитывать на каждый эмбед нельзя (256 МБ хост, ответы идут пачками), поэтому
строки бренда кэшируются и сбрасываются по изменению файла — правка из веб-панели всё равно
видно сразу.
"""
import os
import time
from typing import Iterable, Optional, Sequence, Tuple

import disnake

from utils.checks import CONFIG_PATH, load_config

FALLBACK_COLOR = {
    "embed_color": 0x990000,
    "success_color": 0x2ECC71,
    "error_color": 0xE74C3C,
    "warning_color": 0xF39C12,
    # Выплаты — своя полоска: с красным казны карточки выглядели как дубли друг друга.
    "pay_color": 0xBFC6CE,
}

# ─────────────────────────── бренд ───────────────────────────
_brand_cache = {"stamp": None, "vals": None}


def _brand() -> dict:
    """Имя семьи, футер и логотип из config.json (с кэшем по mtime)."""
    try:
        stamp = (os.path.getmtime(CONFIG_PATH), os.path.getsize(CONFIG_PATH))
    except OSError:
        stamp = None
    if stamp is not None and _brand_cache["stamp"] == stamp:
        return _brand_cache["vals"]
    config = load_config()
    texts = config.get("texts", {}) or {}
    name = (config.get("bot_name") or "Hallez FAMQ").strip()
    footer = texts.get("footer_text") or f"Majestic RP • {name}"
    try:
        footer = str(footer).format(bot_name=name)
    except Exception:
        footer = name
    vals = {"name": name, "footer": footer.strip()[:2048],
            "logo": str(config.get("logo_url") or "").strip(),
            "colors": {k: config.get(k, v) for k, v in FALLBACK_COLOR.items()}}
    _brand_cache["stamp"] = stamp
    _brand_cache["vals"] = vals
    return vals


def brand_name() -> str:
    return _brand()["name"]


def stamp(embed: disnake.Embed, guild: Optional[disnake.Guild] = None,
          color_key: str = "embed_color", footer: bool = True) -> disnake.Embed:
    """Дописать бренд уже собранному эмбеду: для когов, где карточка строится своими руками."""
    return _decorate(embed, color_key, guild, footer)


# Иконка сервера, которую запомнил бот: у 350 коротких ответов guild не передают, а карточка
# без знака выглядит чужой. main.py ставит её один раз при готовности.
_default_icon = ""


def set_default_icon(url: str) -> None:
    global _default_icon
    if url:
        _default_icon = str(url)


def _icon(guild: Optional[disnake.Guild]) -> str:
    return _brand()["logo"] or (guild.icon.url if guild and guild.icon else "") or _default_icon


def get_color(color_key: str = "embed_color") -> disnake.Color:
    raw = _brand()["colors"].get(color_key, FALLBACK_COLOR.get(color_key, 0x990000))
    if isinstance(raw, str):
        try:
            return disnake.Color(int(raw, 16))
        except ValueError:
            pass
    else:
        try:
            return disnake.Color(int(raw))
        except Exception:
            pass
    return disnake.Color(FALLBACK_COLOR.get(color_key, 0x990000))


def _decorate(embed: disnake.Embed, color_key: str, guild: Optional[disnake.Guild],
              footer: bool, thumb: bool = True) -> disnake.Embed:
    b = _brand()
    icon = _icon(guild) if thumb else ""
    if icon:
        embed.set_thumbnail(url=icon)
    if footer:
        if icon:
            embed.set_footer(text=b["footer"], icon_url=icon)
        else:
            embed.set_footer(text=b["footer"])
    embed.timestamp = disnake.utils.utcnow()
    return embed


def base_embed(
    title: str,
    description: Optional[str] = None,
    color_key: str = "embed_color",
    guild: Optional[disnake.Guild] = None
) -> disnake.Embed:
    return card(title, description, color_key, guild)


def card(title: Optional[str] = None, description: Optional[str] = None,
         color_key: str = "embed_color", guild: Optional[disnake.Guild] = None,
         footer: bool = True, thumb: bool = True, color: Optional[int] = None) -> disnake.Embed:
    """Каркас любой карточки бота: цвет из конфига, обложка, футер и время.

    Нужен чтобы локальные фабрики в когах (famq_core, hall_tools, famq_control) не рисовали
    свой вариант: когда у половины эмбедов нет футера, семья выглядит как четыре разных бота.
    """
    embed = disnake.Embed(title=clip(title, 256) if title else None,
                          description=clip(description, 4000) if description else None,
                          color=disnake.Color(color) if color is not None else get_color(color_key))
    return _decorate(embed, color_key, guild, footer, thumb)


def success_embed(title: str, description: str, guild: Optional[disnake.Guild] = None) -> disnake.Embed:
    return base_embed(f"✅ {title}", description, "success_color", guild=guild)


def error_embed(title: str, description: str, guild: Optional[disnake.Guild] = None) -> disnake.Embed:
    return base_embed(f"❌ {title}", description, "error_color", guild=guild)


def warning_embed(title: str, description: str, guild: Optional[disnake.Guild] = None) -> disnake.Embed:
    return base_embed(f"⚠️ {title}", description, "warning_color", guild=guild)


# ─────────────── короткий ответ карточкой вместо голого текста ───────────────
_KIND_BY_EMOJI = {
    "✅": "success", "🎉": "success", "👍": "success", "💾": "success", "✨": "success",
    "🌟": "success", "🏆": "success", "💎": "success", "🔓": "success", "📤": "success",
    "❌": "error", "⛔": "error", "🚫": "error", "🔇": "error", "❗": "warning",
    "⚠️": "warning", "🔒": "warning", "⏳": "warning", "🚨": "warning",
    "ℹ️": "embed", "📌": "embed", "🔎": "embed", "📊": "embed", "🕒": "embed",
}
_COLOR_BY_KIND = {
    "success": "success_color",
    "error": "error_color",
    "warning": "warning_color",
    "embed": "embed_color",
}


def note_embed(text: str, kind: Optional[str] = None, title: Optional[str] = None,
               guild: Optional[disnake.Guild] = None) -> disnake.Embed:
    """Карточка ответа. Цвет берётся из конфига; если kind не задан, угадывается по первому эмодзи.

    Вызывается из каждого второго ответа бота, поэтому карточка обязательна: без логотипа,
    футера и времени ответ выглядит как сообщение из другого бота.
    """
    if kind is None:
        kind = next((k for emoji, k in _KIND_BY_EMOJI.items() if text.startswith(emoji)), "embed")
    embed = disnake.Embed(description=clip(text, 4000),
                          color=get_color(_COLOR_BY_KIND.get(kind, "embed_color")))
    if title:
        embed.title = title[:256]
    # Обложку просит только полноценная карточка: у однострочного ответа логотип
    # занимал бы больше места, чем сам ответ.
    return _decorate(embed, _COLOR_BY_KIND.get(kind, "embed_color"), guild, footer=True,
                     thumb=bool(title))


def success_note(text: str, title: Optional[str] = None, guild=None) -> disnake.Embed:
    return note_embed(text, "success", title, guild)


def error_note(text: str, title: Optional[str] = None, guild=None) -> disnake.Embed:
    return note_embed(text, "error", title, guild)


def clip(text, n: int = 1024) -> str:
    """Обрезка с многоточием: молча отрезанный хвост выглядит как баг бота."""
    s = "" if text is None else str(text)
    return s if len(s) <= n else s[: max(1, n - 1)] + "…"


# ─────────────────────────── списки и панели ───────────────────────────
_MEDALS = ("🥇", "🥈", "🥉")


def list_embed(title: str, rows: Sequence[Sequence[str]], color_key: str = "embed_color",
               guild: Optional[disnake.Guild] = None, medals: bool = False,
               empty: str = "Пока пусто.", note: Optional[str] = None,
               inline: bool = True) -> disnake.Embed:
    """Список карточкой: каждая строка — поле, а не строка в простыне текста.

    rows принимает (главное,), (главное, второстепенное) или (число, главное, второстепенное).
    Медальки ставит первых трёх, если ряд отсортирован по убыванию.
    """
    embed = disnake.Embed(title=clip(title, 256), color=get_color(color_key))
    if not rows:
        embed.description = empty
        return _decorate(embed, color_key, guild, footer=True)
    for i, row in enumerate(rows[:25]):
        cells = [str(c) for c in row]
        head = cells[0]
        rest = "\n".join(cells[1:]) or "—"
        if medals:
            head = f"{_MEDALS[i]} {head}" if i < len(_MEDALS) else f"`{i + 1}.` {head}"
        embed.add_field(name=clip(head, 256), value=clip(rest, 1024), inline=inline)
    if note:
        embed.description = clip(note, 4000)
    return _decorate(embed, color_key, guild, footer=True)


def panel_embed(title: str, sections: Sequence[Tuple[str, str]],
                color_key: str = "embed_color", guild: Optional[disnake.Guild] = None,
                intro: Optional[str] = None, empty: str = "— пусто —",
                columns: bool = False) -> disnake.Embed:
    """Панель раздела: заголовок + смысловые блоки через тонкую линейку.

    Разделитель нужен потому, что Discord не умеет отступы между блоками: без него
    шесть секций слипаются в одну кашу, а с ним панель читается с одного взгляда.
    """
    blocks = []
    for name, body in sections:
        text = (body or "").strip() or empty
        blocks.append(f"**{name}**\n{_RULE}\n{text}")
    desc = "\n\n".join(blocks)
    if intro:
        desc = f"{intro}\n\n{desc}" if desc else intro
    embed = disnake.Embed(title=clip(title, 256),
                          description=clip(desc, 4000) if desc else None,
                          color=get_color(color_key))
    if columns:
        embed.description = None
        for name, body in sections[:25]:
            embed.add_field(name=clip(name, 256), value=clip(body or empty, 1024), inline=True)
    return _decorate(embed, color_key, guild, footer=True)


_RULE = "▎"


def bar(done: float, total: float, width: int = 8, empty: str = "░", full: str = "▓") -> str:
    """Полоса прогресса/остатка — то, что видно раньше текста."""
    if total <= 0:
        return full * width
    filled = max(0, min(width, round(done / total * width)))
    return full * filled + empty * (width - filled)


def until(ts) -> str:
    """Живое «через 5 минут» от Discord: `<t:…:R>` обновляется само, без редактирования сообщения."""
    try:
        return f"<t:{int(ts)}:R>"
    except Exception:
        return ""


def countdown(ts, total: Optional[float] = None) -> str:
    """Строка «осталось ▓▓▓░ · через 20 минут» для всего, у чего есть срок."""
    if not ts:
        return ""
    left = max(0, int(ts) - int(time.time()))
    window = int(total) if total else max(3600, left)
    line = f"{bar(window - left, window)} · "
    if left <= 0:
        return line + "истекло"
    d, h = divmod(left, 86400)
    h, m = divmod(h, 3600)
    m, s = divmod(m, 60)
    if d:
        line += f"{d} д {h} ч"
    elif h:
        line += f"{h} ч {m} мин"
    elif m:
        line += f"{m} мин {s} сек"
    else:
        line += f"{s} сек"
    return line + f" · {until(ts)}"


def rows_embed(title: str, rows: Iterable[str], color_key: str = "embed_color", **kw) -> disnake.Embed:
    """Однострочники (состав, список треков) — карточкой, а не голым текстом."""
    return list_embed(title, [[r] for r in rows], color_key=color_key, **kw)
