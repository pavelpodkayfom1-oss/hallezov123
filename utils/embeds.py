import disnake
from typing import Optional
from utils.checks import load_config

FALLBACK_COLOR = {
    "embed_color": 0x990000,
    "success_color": 0x2ECC71,
    "error_color": 0xE74C3C,
    "warning_color": 0xF39C12,
}

def get_color(color_key: str = "embed_color") -> disnake.Color:
    config = load_config()
    raw_color = config.get(color_key, FALLBACK_COLOR.get(color_key, 0x990000))
    if isinstance(raw_color, str):
        try:
            return disnake.Color(int(raw_color, 16))
        except ValueError:
            return disnake.Color(FALLBACK_COLOR.get(color_key, 0x990000))
    return disnake.Color(raw_color)

def base_embed(
    title: str,
    description: Optional[str] = None,
    color_key: str = "embed_color",
    guild: Optional[disnake.Guild] = None
) -> disnake.Embed:
    config = load_config()
    bot_name = config.get("bot_name") or "Hallez FAMQ"
    logo_url = config.get("logo_url", "").strip()
    
    embed = disnake.Embed(
        title=title,
        description=description,
        color=get_color(color_key)
    )
    
    icon_url = None
    if logo_url:
        icon_url = logo_url
    elif guild and guild.icon:
        icon_url = guild.icon.url

    if icon_url:
        embed.set_thumbnail(url=icon_url)
        embed.set_footer(text=f"Majestic RP • {bot_name}", icon_url=icon_url)
    else:
        embed.set_footer(text=f"Majestic RP • {bot_name}")

    embed.timestamp = disnake.utils.utcnow()
    return embed

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


def note_embed(text: str, kind: Optional[str] = None, title: Optional[str] = None) -> disnake.Embed:
    """Карточка ответа. Цвет берётся из конфига; если kind не задан, угадывается по первому эмодзи."""
    if kind is None:
        kind = next((k for emoji, k in _KIND_BY_EMOJI.items() if text.startswith(emoji)), "embed")
    embed = disnake.Embed(description=text[:4000], color=get_color(_COLOR_BY_KIND.get(kind, "embed_color")))
    if title:
        embed.title = title
    return embed
