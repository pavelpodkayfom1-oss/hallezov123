import asyncio
import functools
import io
import os
import re
import sys
import time
from typing import Optional

import aiohttp
import disnake
from disnake.ext import commands

from utils.checks import is_admin, load_config, save_config
from utils.media_card import render_media_card

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CARD_COLOR = 0xC4161C  # фирменный красный Hallez
TWITCH_URL_RE = re.compile(r"^(?:https?://)?(?:www\.|m\.)?twitch\.tv/(?P<login>[^\s/?#]+)", re.IGNORECASE)
TWITCH_LOGIN_RE = re.compile(r"^\w{3,25}$")
PREVIEW_URL = "https://static-cdn.jtvnw.net/previews-ttv/live_user_{login}-640x360.jpg?t={ts}"


def _can_post(member: disnake.Member) -> bool:
    if is_admin(member):
        return True
    allowed = set(load_config().get("media_role_ids", []))
    return any(role.id in allowed for role in member.roles)


async def _fetch_preview(login: str) -> Optional[bytes]:
    """Живое превью стрима. Если канал оффлайн, Twitch отвечает редиректом на заглушку - тогда None."""
    url = PREVIEW_URL.format(login=login.lower(), ts=int(time.time()))
    try:
        timeout = aiohttp.ClientTimeout(total=6)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=False) as resp:
                if resp.status != 200 or not resp.content_type.startswith("image/"):
                    return None
                return await resp.read()
    except Exception:
        return None


async def _fetch_avatar(member: disnake.abc.User) -> Optional[bytes]:
    try:
        return await member.display_avatar.with_size(128).read()
    except Exception:
        return None


class Media(commands.Cog):
    """Анонсы Twitch-стримов в отдельном канале."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command(name="media", description="Анонсировать стрим на Twitch")
    async def media(
        self,
        inter: disnake.ApplicationCommandInteraction,
        url: str = commands.Param(name="ссылка", description="Ссылка на канал, например https://twitch.tv/kanald"),
        note: str = commands.Param(name="о_чём", description="Что стримишь", default=""),
    ):
        if not _can_post(inter.author):
            return await inter.response.send_message(
                "⛔ Анонсировать стримы могут только участники с ролью из `/media_role`.",
                ephemeral=True,
            )
        match = TWITCH_URL_RE.match(url.strip())
        if not match or not TWITCH_LOGIN_RE.match(match.group("login")):
            return await inter.response.send_message(
                "❌ Нужна ссылка вида `https://twitch.tv/kanald`.", ephemeral=True
            )

        channel_id = load_config().get("channels", {}).get("media_channel_id")
        target = self.bot.get_channel(channel_id) if channel_id else None
        if target is None:
            return await inter.response.send_message(
                "❌ Канал для анонсов не настроен. Попроси администрацию: `/media_channel`.",
                ephemeral=True,
            )

        # рисуем карточку и тянем превью - это дольше 3 секунд, поэтому откладываем ответ
        await inter.response.defer(ephemeral=True)

        login = match.group("login")
        link = f"https://twitch.tv/{login}"
        note = " ".join(note.split())[:600]

        preview, avatar = await asyncio.gather(_fetch_preview(login), _fetch_avatar(inter.author))
        live = preview is not None

        banner_name = load_config().get("recruit_banner_file") or "banner_image.png"
        card_bytes = None
        try:
            card_bytes = await asyncio.get_running_loop().run_in_executor(
                None,
                functools.partial(
                    render_media_card,
                    login=login,
                    note=note,
                    announcer=inter.author.display_name,
                    avatar_bytes=avatar,
                    preview_bytes=preview,
                    banner_path=os.path.join(BASE_DIR, banner_name),
                    live=live,
                ),
            )
        except Exception as exc:  # карточка не должна ломать анонс
            print(f"🎬 media: не удалось нарисовать карточку: {exc!r}", file=sys.stderr, flush=True)

        config = load_config()
        bot_name = config.get("bot_name", "Hallez FAMQ")
        logo_url = (config.get("logo_url") or "").strip() or (inter.guild.icon.url if inter.guild and inter.guild.icon else None)

        status = "в эфире" if live else "анонсирует стрим"
        emb = disnake.Embed(
            description=f"{inter.author.mention} {status}\n### [twitch.tv/{login}]({link})",
            color=disnake.Color(CARD_COLOR),
            timestamp=disnake.utils.utcnow(),
        )
        footer = f"Majestic RP • {bot_name}"
        if logo_url:
            emb.set_footer(text=footer, icon_url=logo_url)
        else:
            emb.set_footer(text=footer)

        file = None
        if card_bytes:
            file = disnake.File(io.BytesIO(card_bytes), filename=f"stream_{login}.png")
            emb.set_image(url=f"attachment://stream_{login}.png")
        elif note:
            emb.add_field(name="📝 От автора", value=note, inline=False)

        view = disnake.ui.View(timeout=None)
        view.add_item(
            disnake.ui.Button(style=disnake.ButtonStyle.url, label="Смотреть на Twitch", emoji="🟣", url=link)
        )

        try:
            if file:
                await target.send(embed=emb, file=file, view=view)
            else:
                await target.send(embed=emb, view=view)
        except disnake.Forbidden:
            return await inter.edit_original_response(content=f"⛔ У бота нет прав писать в {target.mention}.")

        await inter.edit_original_response(content=f"✅ Анонс опубликован в {target.mention}.")

    @commands.slash_command(name="media_channel", description="Канал для анонсов стримов (для руководства)")
    async def media_channel(
        self,
        inter: disnake.ApplicationCommandInteraction,
        channel: disnake.TextChannel = commands.Param(name="канал", description="Сюда бот будет выкладывать анонсы"),
    ):
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Нет доступа.", ephemeral=True)
        config = load_config()
        config.setdefault("channels", {})["media_channel_id"] = channel.id
        save_config(config)
        await inter.response.send_message(f"✅ Канал для анонсов: {channel.mention}", ephemeral=True)

    @commands.slash_command(
        name="media_role",
        description="Выдать или забрать право анонсировать стримы (для руководства)",
    )
    async def media_role(
        self,
        inter: disnake.ApplicationCommandInteraction,
        role: disnake.Role = commands.Param(name="роль", description="Роль, которой переключается право поста"),
    ):
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Нет доступа.", ephemeral=True)
        config = load_config()
        ids = list(config.get("media_role_ids", []))
        if role.id in ids:
            ids.remove(role.id)
            action = "🚫 Забрано"
        else:
            ids.append(role.id)
            action = "✅ Выдано"
        config["media_role_ids"] = ids
        save_config(config)

        if ids:
            current = "\nСейчас могут постить: " + ", ".join(f"<@&{rid}>" for rid in ids)
        else:
            current = "\nСписок пуст — право осталось только у администрации."
        await inter.response.send_message(f"{action} право анонсировать для {role.mention}.{current}", ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(Media(bot))
