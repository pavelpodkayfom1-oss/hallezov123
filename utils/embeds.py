import disnake
from typing import Optional
from utils.checks import load_config

def get_color(color_key: str = "embed_color") -> disnake.Color:
    config = load_config()
    raw_color = config.get(color_key, "0x990000")
    if isinstance(raw_color, str):
        return disnake.Color(int(raw_color, 16))
    return disnake.Color(raw_color)

def base_embed(
    title: str,
    description: Optional[str] = None,
    color_key: str = "embed_color",
    guild: Optional[disnake.Guild] = None
) -> disnake.Embed:
    config = load_config()
    bot_name = config.get("bot_name", "Zakonov FAMQ")
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
