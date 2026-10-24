"""
Медиа-анонсы: /media_send присылает в личные панель черновика (ссылка, что стримим, когда,
галки упоминаний), а «Скинуть в группу» публикует анонс в канале медиа с фирменным баннером.
"""
import html
import os
import re
from typing import Optional, Tuple

import aiohttp
import disnake
from disnake.ext import commands

from utils import brand, cdn_assets
from utils.checks import is_admin, load_config, save_config
from utils.embeds import base_embed

TWITCH_COLOR = 0x9146FF
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

TWITCH_URL_RE = re.compile(r"^(?:https?://)?(?:www\.|m\.)?twitch\.tv/(?P<login>[^\s/?#]+)", re.IGNORECASE)
META_RE = re.compile(r'<meta\b[^>]*?(?:property|name)="([^"]+)"[^>]*?content="([^"]*)"', re.IGNORECASE)
# «Название стрима | Streaming irl.» — «for N viewers» бывает не всегда
LIVE_DESC_RE = re.compile(
    r"^\s*(?P<title>.*?)\s*\|\s*Streaming\s+(?P<game>.+?)\s*$", re.IGNORECASE | re.DOTALL
)
VIEWERS_RE = re.compile(r"\s+for\s+(?P<viewers>[\d.,]+)\s+viewers?\.?\s*$", re.IGNORECASE)
TITLE_SUFFIX_RE = re.compile(r"\s+-\s+(?:Live on Twitch|Twitch)\s*$", re.IGNORECASE)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEDIA_BANNER = "media_banner.jpg"
BANNER_PATH = os.path.join(BASE_DIR, "assets", MEDIA_BANNER)


def _can_post(member: disnake.Member) -> bool:
    if is_admin(member):
        return True
    allowed = set(load_config().get("media_role_ids", []))
    return any(role.id in allowed for role in member.roles)


def _login_of(url: str) -> Optional[str]:
    match = TWITCH_URL_RE.match((url or "").strip())
    return match.group("login") if match else None


def _ping_role_id() -> int:
    return int(load_config().get("media_ping_role_id", 0) or 0)


# ─────────────────────────── ФОРМА ЧЕРНОВИКА ───────────────────────────

class DraftFieldModal(disnake.ui.Modal):
    """Правка одного поля черновика: ключ решает, что именно переписываем."""

    def __init__(self, view: "MediaDraftView", key: str, title: str, label: str,
                 placeholder: str = "", paragraph: bool = False):
        self.view, self.key = view, key
        super().__init__(
            title=title[:45],
            components=[disnake.ui.TextInput(
                label=label[:45], custom_id=f"media:{key}", value=view.draft[key][:1800],
                placeholder=placeholder or None, max_length=1800,
                style=disnake.TextInputStyle.paragraph if paragraph else disnake.TextInputStyle.short,
            )],
            timeout=600,
        )

    async def callback(self, inter: disnake.ModalInteraction):
        self.view.draft[self.key] = (inter.text_values.get(f"media:{self.key}") or "").strip()
        await self.view.refresh(inter)


class MediaDraftView(disnake.ui.View):
    """Панель в личных: три поля-кнопки, две галки упоминаний и кнопка публикации."""

    BRAND = {
        "media:btn:url": ("hz_link", "🔗"),
        "media:btn:note": ("hz_youtube", "📹"),
        "media:btn:when": ("hz_starblue", "🕔"),
        "media:btn:all": ("hz_bluecrewmate", "📢"),
        "media:btn:role": ("hz_bluemoderator", "🎗"),
        "media:btn:send": ("hz_booster", "🚀"),
    }

    def __init__(self, cog: "Media", user_id: int, guild_id: int):
        super().__init__(timeout=3 * 3600)
        self.cog, self.user_id, self.guild_id = cog, user_id, guild_id
        self.draft = {"url": "", "note": "", "when": "", "ping_all": False, "ping_role": False}
        brand.paint(cog.bot, self, self.BRAND)

    def guild(self):
        """Панель живёт в личных, поэтому сервер помним отдельно: анонс идёт именно туда."""
        return self.cog.bot.get_guild(self.guild_id)

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:
        if inter.author.id != self.user_id:
            await inter.response.send_message("Это не твоя панель анонса.", ephemeral=True)
            return False
        return True

    async def refresh(self, inter) -> None:
        await inter.response.edit_message(embed=self.cog.draft_embed(self.draft, self.guild()), view=self)

    @disnake.ui.button(label="Ссылка", emoji="🔗", custom_id="media:btn:url",
                       style=disnake.ButtonStyle.primary, row=0)
    async def f_url(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.send_modal(DraftFieldModal(
            self, "url", "Ссылка на канал", "Ссылка", "https://twitch.tv/kanald"))

    @disnake.ui.button(label="Что стримим", emoji="📹", custom_id="media:btn:note",
                       style=disnake.ButtonStyle.primary, row=0)
    async def f_note(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.send_modal(DraftFieldModal(
            self, "note", "Что стримим", "Описание стрима", "Катка GTA RP, вечерние разборки", paragraph=True))

    @disnake.ui.button(label="Когда", emoji="🕔", custom_id="media:btn:when",
                       style=disnake.ButtonStyle.primary, row=0)
    async def f_when(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.send_modal(DraftFieldModal(
            self, "when", "Когда", "Время или «уже в эфире»", "Сегодня в 20:00 МСК"))

    @disnake.ui.button(label="Упомянуть всех", emoji="📢", custom_id="media:btn:all", row=1)
    async def t_all(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await self._toggle(inter, button, "ping_all", disnake.ButtonStyle.danger)

    @disnake.ui.button(label="Упомянуть роль", emoji="🎗", custom_id="media:btn:role", row=1)
    async def t_role(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not self.draft["ping_role"] and not _ping_role_id():
            return await inter.response.send_message(
                "⚠️ Роль для упоминания не настроена — попроси администрацию: `/media_ping`.", ephemeral=True)
        await self._toggle(inter, button, "ping_role", disnake.ButtonStyle.blurple)

    async def _toggle(self, inter: disnake.MessageInteraction, button: disnake.ui.Button,
                      key: str, on_style: disnake.ButtonStyle):
        self.draft[key] = not self.draft[key]
        label = button.label.lstrip("✅ ")
        button.label = ("✅ " if self.draft[key] else "") + label
        button.style = on_style if self.draft[key] else disnake.ButtonStyle.secondary
        await self.refresh(inter)

    @disnake.ui.button(label="Скинуть в группу", emoji="🚀", custom_id="media:btn:send",
                       style=disnake.ButtonStyle.success, row=2)
    async def publish(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        await self.cog.publish_draft(self, inter)


# ───────────────────────────── COG ─────────────────────────────

class Media(commands.Cog):
    """Анонсы Twitch-стримов: быстрый /media и панель-черновик /media_send."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def E(self, text: str) -> str:
        """Токены бренд-пака (`:hz_link:`) → живые эмодзи сервера; незнакомые тихо исчезают."""
        return brand.E(self.bot, text)

    # ───── баннер ─────

    def _media_channel(self, guild) -> Optional[disnake.TextChannel]:
        cid = int((load_config().get("channels") or {}).get("media_channel_id") or 0)
        ch = guild.get_channel(cid) if (cid and guild) else None
        return ch if isinstance(ch, disnake.TextChannel) else None

    def _put_banner(self, embed: disnake.Embed) -> Optional[disnake.File]:
        """Картинка под текстом: есть CDN — ссылка (ноль трафика), нет — файл-вложение."""
        if not os.path.exists(BANNER_PATH):
            return None
        url = cdn_assets.cached_url(MEDIA_BANNER, BANNER_PATH)
        if url:
            embed.set_image(url=url)
            return None
        embed.set_image(url=f"attachment://{MEDIA_BANNER}")
        return disnake.File(BANNER_PATH, filename=MEDIA_BANNER)

    async def _banner_channel(self):
        """Служебное сообщение с баннером льём туда же, куда и остальные бренд-картинки.

        Канал анонсов для этого не годится: сообщение «📌 Служебные картинки бота» мозолит
        глаза всем, кто заходит посмотреть стрим.
        """
        try:
            from cogs.hall_recruit import cdn_upload_channel
            ch = await cdn_upload_channel()
            if ch is not None:
                return ch
        except Exception:
            pass        # cog набора может быть отключён — тогда идём своим путём
        for guild in self.bot.guilds:
            ch = self._media_channel(guild)
            if ch is not None:
                return ch
        return None

    async def ensure_banner(self) -> None:
        """Держит баннер на CDN: иначе каждый анонс тащит ~160 КБ файлом.

        Ссылка живёт ровно столько, сколько живёт служебное сообщение, поэтому проверку
        запускаем на каждом старте, а не один раз в жизни бота.
        """
        if not os.path.exists(BANNER_PATH):
            return
        ch = await self._banner_channel()
        if ch is not None:
            await cdn_assets.ensure(ch, MEDIA_BANNER, BANNER_PATH)

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            await self.ensure_banner()
        except Exception as error:
            print(f"⚠️ Баннер медиа на CDN не залит: {error}", flush=True)

    # ───── тексты ─────

    def draft_embed(self, draft: dict, guild) -> disnake.Embed:
        e = base_embed(self.E(":hz_youtube: Черновик анонса"),
                       draft["note"] or self.E(":hz_gradientarrowright: Нажми «Что стримим» и опиши, что будет на стриме."),
                       guild=guild)
        e.color = disnake.Color(TWITCH_COLOR)
        e.add_field(name=self.E(":hz_link: Ссылка"), value=draft["url"] or self.E("пока нет"), inline=False)
        e.add_field(name=self.E(":hz_starblue: Когда"), value=draft["when"] or self.E("—"), inline=True)
        e.add_field(name=self.E(":hz_starids: Упоминания"), value=self._ping_line(draft), inline=True)
        e.set_footer(text=self.E(":hz_info: «Скинуть в группу» опубликует это в канале медиа"))
        return e

    def _ping_line(self, draft: dict) -> str:
        marks = []
        if draft["ping_all"]:
            marks.append(self.E(":hz_bluecrewmate: всех (@everyone)"))
        if draft["ping_role"]:
            role = _ping_role_id()
            marks.append(self.E(f":hz_bluemoderator: <@&{role}>") if role else self.E(":hz_bluemoderator: роль не настроена"))
        return "\n".join(marks) or self.E("➖ никто")

    def live_embed(self, note: str, login: str, link: str, data: dict, when: str,
                   author, guild) -> disnake.Embed:
        """Карточка стрима: текст автора сверху, ниже — поля, в самом низу баннер."""
        e = base_embed(self.E(":hz_animatedarrowred: СЕЙЧАС В ЭФИРЕ" if data["status"] == "live"
                              else ":hz_youtube: Стрим на Twitch"), note[:900], guild=guild)
        e.color = disnake.Color(TWITCH_COLOR)
        e.add_field(name=self.E(":hz_link: Канал"), value=f"[**twitch.tv/{login}**]({link})", inline=False)
        if when:
            e.add_field(name=self.E(":hz_starblue: Когда"), value=when[:200], inline=True)
        if data["game"]:
            e.add_field(name=self.E(":hz_opalnitro: Категория"), value=data["game"], inline=True)
        if data["viewers"]:
            e.add_field(name=self.E(":hz_blueplanet: Зрителей"),
                        value=f"{data['viewers']:,}".replace(",", " "), inline=True)
        e.add_field(name=self.E(":hz_ownership: Анонсировал"), value=author.mention, inline=True)
        return e

    @staticmethod
    def twitch_button(link: str) -> disnake.ui.View:
        btn = disnake.ui.View(timeout=None)
        btn.add_item(disnake.ui.Button(style=disnake.ButtonStyle.url, label="🔴 Открыть на Twitch", url=link))
        return btn

    # ───── публикация ─────

    @staticmethod
    def _pings(draft: dict) -> Tuple[Optional[str], disnake.AllowedMentions]:
        """Собирает строку упоминаний и разрешает ровно те из них, что включены галками."""
        parts = []
        if draft["ping_all"]:
            parts.append("@everyone")
        role = _ping_role_id()
        if draft["ping_role"] and role:
            parts.append(f"<@&{role}>")
        return (" ".join(parts) or None), disnake.AllowedMentions(
            everyone=draft["ping_all"], roles=bool(draft["ping_role"] and role),
            users=False, replied_user=False)

    async def publish_draft(self, view: MediaDraftView, inter: disnake.MessageInteraction):
        login = _login_of(view.draft["url"])
        if login is None:
            return await inter.followup.send("❌ Нужна ссылка вида `https://twitch.tv/kanald`.", ephemeral=True)
        if not view.draft["note"]:
            return await inter.followup.send("❌ Сначала заполни «Что стримим».", ephemeral=True)
        guild = view.guild()
        target = self._media_channel(guild)
        if target is None:
            return await inter.followup.send("❌ Канал для анонсов не настроен: `/media_channel`.", ephemeral=True)

        link = f"https://twitch.tv/{login}"
        data = await self._gather(link)

        emb = self.live_embed(view.draft["note"], login, link, data, view.draft["when"],
                              inter.author, guild)
        file = self._put_banner(emb)

        pings, allowed = self._pings(view.draft)
        btn = self.twitch_button(link)
        try:
            msg = await target.send(content=pings or None, embed=emb, view=btn, file=file,
                                    allowed_mentions=allowed)
        except disnake.Forbidden:
            return await inter.followup.send(f"⛔ У бота нет прав писать в {target.mention}.", ephemeral=True)

        await inter.followup.send(f"✅ Анонс опубликован: {msg.jump_url}", ephemeral=True)

    # ───── данные Twitch ─────

    async def _bytes(self, session: aiohttp.ClientSession, url: str) -> Optional[bytes]:
        if not url:
            return None
        try:
            async with session.get(url) as resp:
                return await resp.read() if resp.status == 200 else None
        except aiohttp.ClientError:
            return None

    async def _gather(self, url: str) -> dict:
        """Тянет название, категорию и зрителей со страницы канала."""
        data = {"status": "offline", "title": "", "game": "", "viewers": None}
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}, timeout=timeout) as session:
            page = await self._bytes(session, url)
            if page is None:
                return data

            text = page.decode("utf-8", "replace")
            meta = {k.lower(): html.unescape(v) for k, v in META_RE.findall(text)}
            channel_name = TITLE_SUFFIX_RE.sub("", meta.get("og:title", "")).strip()

            live = LIVE_DESC_RE.match(meta.get("og:description", ""))
            if live is None and "live on twitch" not in meta.get("og:title", "").lower():
                return data

            game = live.group("game").strip().rstrip(". ") if live else ""
            viewers = None
            if live:
                if count := VIEWERS_RE.search(game):
                    viewers = int(re.sub(r"[^\d]", "", count.group("viewers") or "0"))
                    game = game[: count.start()].rstrip(". ")

            return {**data, "status": "live",
                    "title": (live.group("title").strip() if live else "") or channel_name,
                    "game": game, "viewers": viewers}

    # ───── команды ────

    @commands.slash_command(name="media_send",
                            description="Панель анонса в личные: ссылка, описание, упоминания")
    async def media_send(self, inter: disnake.ApplicationCommandInteraction):
        if not _can_post(inter.author):
            return await inter.response.send_message(
                "⛔ Анонсировать стримы могут только участники с ролью из `/media_role`.",
                ephemeral=True,
            )
        await inter.response.defer(ephemeral=True)
        view = MediaDraftView(self, inter.author.id, inter.guild_id or 0)
        try:
            dm = await inter.author.create_dm()
            await dm.send(embed=self.draft_embed(view.draft, view.guild()), view=view)
        except disnake.HTTPException:
            return await inter.followup.send(
                "❌ Не получилось написать в личные — открой ЛС боту и повтори.", ephemeral=True)
        await self.ensure_banner()
        await inter.followup.send("📺 Панель анонса — в личных сообщениях.", ephemeral=True)

    @commands.slash_command(name="media", description="Быстро анонсировать стрим на Twitch")
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
        login = _login_of(url)
        if login is None:
            return await inter.response.send_message(
                "❌ Нужна ссылка вида `https://twitch.tv/kanald`.", ephemeral=True)
        target = self._media_channel(inter.guild)
        if target is None:
            return await inter.response.send_message(
                "❌ Канал для анонсов не настроен. Попроси администрацию: `/media_channel`.",
                ephemeral=True,
            )

        link = f"https://twitch.tv/{login}"
        await inter.response.defer(ephemeral=True)
        data = await self._gather(link)

        emb = self.live_embed(note, login, link, data, "", inter.author, inter.guild)
        file = self._put_banner(emb)

        view = self.twitch_button(link)
        try:
            await target.send(embed=emb, view=view, file=file)
        except disnake.Forbidden:
            return await inter.followup.send(f"⛔ У бота нет прав писать в {target.mention}.", ephemeral=True)

        text = f"✅ Анонс опубликован в {target.mention}."
        if data["status"] != "live":
            text += "\nℹ️ Twitch показывает канал офлайн."
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
        await self.ensure_banner()

    @commands.slash_command(name="media_ping",
                            description="Роль, которую бот сможет упомянуть в анонсе (для руководства)")
    async def media_ping(
        self,
        inter: disnake.ApplicationCommandInteraction,
        role: disnake.Role = commands.Param(name="роль", description="Её можно будет пингнуть галкой «Упомянуть роль»"),
    ):
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Нет доступа.", ephemeral=True)
        config = load_config()
        current = int(config.get("media_ping_role_id", 0) or 0)
        config["media_ping_role_id"] = 0 if current == role.id else role.id
        save_config(config)
        action = "🚫 Сброшена" if config["media_ping_role_id"] == 0 else "✅ Роль для упоминания в анонсах"
        await inter.response.send_message(f"{action}: {role.mention}", ephemeral=True)

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
        await inter.response.send_message(f"{action} право анонсировать для {role.mention}.{current}",
                                          ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(Media(bot))
