import asyncio
import html
import io
import re
from typing import Optional, Tuple

import aiohttp
import disnake
from disnake.ext import commands

from utils.checks import is_admin, load_config, save_config
from utils.embeds import base_embed
from utils.stream_cover import render_cover

TWITCH_COLOR = 0x9146FF
PREVIEW_URL = "https://static-cdn.jtvnw.net/previews-ttv/live_user_{login}-1280x720.jpg"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

TWITCH_URL_RE = re.compile(r"^(?:https?://)?(?:www\.|m\.)?twitch\.tv/(?P<login>[^\s/?#]+)", re.IGNORECASE)
META_RE = re.compile(r'<meta\b[^>]*?(?:property|name)="([^"]+)"[^>]*?content="([^"]*)"', re.IGNORECASE)
# «RERUN: Играем! | Streaming GTA V for 301 viewers.» — так Twitch отдаёт живой стрим
LIVE_DESC_RE = re.compile(
    r"^\s*(?P<title>.*?)\s*\|\s*Streaming (?P<game>.{1,80}?) for (?P<viewers>[\d.,]+) viewers?\.?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _can_post(member: disnake.Member) -> bool:
    if is_admin(member):
        return True
    allowed = set(load_config().get("media_role_ids", []))
    return any(role.id in allowed for role in member.roles)


class Media(commands.Cog):
    """Анонсы Twitch-стримов с обложкой в отдельном канале."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _bytes(self, session: aiohttp.ClientSession, url: str) -> Optional[bytes]:
        if not url:
            return None
        try:
            async with session.get(url) as resp:
                return await resp.read() if resp.status == 200 else None
        except aiohttp.ClientError:
            return None

    async def _gather(self, url: str, login: str) -> Tuple[dict, Optional[bytes], Optional[bytes]]:
        """Тянет название, категорию и зрителей со страницы канала + превью и аватар."""
        data = {"login": login, "status": "offline", "title": "", "game": "", "viewers": None}
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}, timeout=timeout) as session:
            page = await self._bytes(session, url)
            if page is None:
                return data, None, None

            meta = {k.lower(): html.unescape(v) for k, v in META_RE.findall(page.decode("utf-8", "replace"))}
            avatar = await self._bytes(session, meta.get("og:image", ""))
            data["title"] = meta.get("og:title", "").removesuffix(" - Twitch").strip()

            live = LIVE_DESC_RE.match(meta.get("og:description", ""))
            if live is None:
                return data, None, avatar

            preview = await self._bytes(session, PREVIEW_URL.format(login=login))
            if preview is None:
                return data, None, avatar

            data.update(
                status="live",
                title=live.group("title").strip() or data["title"],
                game=live.group("game").strip(),
                viewers=int(re.sub(r"[^\d]", "", live.group("viewers")) or 0),
            )
            return data, preview, avatar

    @commands.slash_command(name="media", description="Анонсировать стрим на Twitch")
    async def media(
        self,
        inter: disnake.ApplicationCommandInteraction,
        url: str = commands.Param(name="ссылка", description="Ссылка на канал, например https://twitch.tv/kanald"),
        note: str = commands.Param(name="о_чём", description="Подпись от себя", default=""),
    ):
        if not _can_post(inter.author):
            return await inter.response.send_message(
                "⛔ Анонсировать стримы могут только участники с ролью из `/media_role`.",
                ephemeral=True,
            )
        url = url.strip()
        match = TWITCH_URL_RE.match(url)
        if not match:
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

        login = match.group("login")
        link = f"https://twitch.tv/{login}"
        await inter.response.defer(ephemeral=True)

        data, preview, avatar = await self._gather(link, login)
        cover = await asyncio.to_thread(render_cover, {
            **data,
            "author": f"анонс: {inter.author.display_name}"[:40],
            "footer": None,
        }, preview, avatar)

        emb = base_embed(
            "🔴 СЕЙЧАС В ЭФИРЕ" if data["status"] == "live" else "📺 Стрим на Twitch",
            f"[**twitch.tv/{login}**]({link})",
            guild=inter.guild,
        )
        emb.color = disnake.Color(TWITCH_COLOR)
        if note:
            emb.add_field(name="📝 От автора", value=note[:600], inline=False)
        emb.add_field(name="🎮 Категория", value=data["game"] or "—", inline=True)
        if data["viewers"]:
            emb.add_field(name="👀 Зрителей", value=f"{data['viewers']:,}".replace(",", " "), inline=True)
        emb.add_field(name="👤 Анонсировал", value=inter.author.mention, inline=True)
        emb.set_image(url="attachment://stream_cover.jpg")

        view = disnake.ui.View(timeout=None)
        view.add_item(disnake.ui.Button(style=disnake.ButtonStyle.url, label="🔴 Открыть на Twitch", url=link))
        file = disnake.File(io.BytesIO(cover), filename="stream_cover.jpg")

        try:
            await target.send(embed=emb, view=view, file=file)
        except disnake.Forbidden:
            return await inter.followup.send(f"⛔ У бота нет прав писать в {target.mention}.", ephemeral=True)

        text = f"✅ Анонс опубликован в {target.mention}."
        if data["status"] != "live":
            text += "\nℹ️ Twitch показывает канал офлайн — карточка без превью."
        await inter.followup.send(text, ephemeral=True)

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
