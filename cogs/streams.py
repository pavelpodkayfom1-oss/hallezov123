"""Авто-анонс стримов: семья узнаёт, что кто-то в эфире, без ручной кнопки.

Twitch API требует ключа и не влезает на хост, поэтому проверяем живой признак эфира напрямую:
превью-картинка `live_user_{канал}` отдаётся только пока стрим идёт, в офлайне — 404.
Один HEAD-запрос на канал раз в ~90 секунд, ни ключей, ни лишней памяти.

Бот следит за переходом «офлайн → онлайн»: тогда карточка с последним кадром и пинг роли.
Обратный переход пишет одну короткую строку, чтобы «ушёл» тоже было видно.
"""
import json
import os
import time
from typing import Dict, List, Optional

import disnake
from disnake.ext import commands, tasks

from utils import brand
from utils.checks import get_text, is_admin
from utils.embeds import card, clip, list_embed, note_embed

STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "streams.json")
PREVIEW = "https://static-cdn.jtvnw.net/previews-ttv/live_user_{login}-1280x720.jpg"
CHECK_SECONDS = 90
MAX_CHANNELS = 20


def _status_is_live(status: int) -> Optional[bool]:
    if status == 200:
        return True
    if status == 404:
        return False
    return None


def _load() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("subs", {})
    data.setdefault("channel", 0)
    return data


def _save(data: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def _login(raw: str) -> str:
    """`https://twitch.tv/PodKayfom` → `podkayfom`."""
    text = str(raw or "").strip().lower()
    for cut in ("twitch.tv/", "/"):
        if cut in text:
            text = text.rsplit(cut, 1)[-1]
    return "".join(c for c in text if c.isalnum() or c in "_")[:40]


class StreamModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(
            title="Следить за стримом",
            custom_id="hzstream:add",
            components=[
                disnake.ui.TextInput(label="Канал (twitch.tv/имя)", custom_id="login",
                                     style=disnake.TextInputStyle.short,
                                     placeholder="twitch.tv/podkayfom", max_length=60),
                disnake.ui.TextInput(label="Как показывать (можно пустой)", custom_id="label",
                                     style=disnake.TextInputStyle.short,
                                     placeholder="Фом — Main", max_length=40, required=False),
                disnake.ui.TextInput(label="Роль для пинга (<@&123> или ID, можно пустой)",
                                     custom_id="role", style=disnake.TextInputStyle.short,
                                     placeholder="<@&1529412321456164904>", max_length=30,
                                     required=False),
            ])

    async def callback(self, inter: disnake.ModalInteraction):
        cog: "Streams" = inter.bot.get_cog("Streams")
        await cog.add(inter, inter.text_values["login"].strip(),
                     inter.text_values.get("label", "").strip(),
                     inter.text_values.get("role", "").strip())


class StreamPickView(disnake.ui.View):
    """Одноразовый селект «какой канал перестать следить»."""

    def __init__(self, subs: Dict[str, dict]):
        super().__init__(timeout=600)
        for slug, sub in list(subs.items())[:25]:
            self.del_pick.add_option(label=clip(sub.get("label", slug), 90), value=slug)

    @disnake.ui.select(custom_id="hzstream:del_pick", placeholder="Какой канал убрать?",
                       max_values=1)
    async def del_pick(self, select: disnake.ui.Select, inter: disnake.MessageInteraction):
        if not is_admin(inter.author):
            return
        data = _load()
        removed = (data.get("subs") or {}).pop(select.values[0], None)
        _save(data)
        await inter.response.edit_message(
            embed=note_embed(f"🗑 Перестал следить: {removed.get('label') or select.values[0]}"
                             if removed else "Такого канала уже нет."), view=None)


class StreamsView(disnake.ui.View):
    """Постоянная панель: кнопки должны работать и после рестарта бота."""

    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="Добавить канал", emoji="➕", style=disnake.ButtonStyle.success,
                       custom_id="hzstream:add_btn", row=0)
    async def add_btn(self, button, inter: disnake.MessageInteraction):
        if not is_admin(inter.author):
            return await inter.response.send_message(
                embed=note_embed("❌ Каналы добавляет руководство семьи."), ephemeral=True)
        await inter.response.send_modal(StreamModal())

    @disnake.ui.button(label="Убрать канал", emoji="🗑", style=disnake.ButtonStyle.danger,
                       custom_id="hzstream:del", row=0)
    async def del_btn(self, button, inter: disnake.MessageInteraction):
        if not is_admin(inter.author):
            return await inter.response.send_message(
                embed=note_embed("❌ Каналы убирает руководство семьи."), ephemeral=True)
        subs = _load().get("subs") or {}
        if not subs:
            return await inter.response.send_message(
                embed=note_embed("Следить пока не за чем — сначала добавь канал."), ephemeral=True)
        await inter.response.send_message(embed=note_embed("Выбери канал."),
                                          view=StreamPickView(subs), ephemeral=True)

    @disnake.ui.button(label="Обновить", emoji="🔄", style=disnake.ButtonStyle.secondary,
                       custom_id="hzstream:refresh", row=0)
    async def refresh(self, button, inter: disnake.MessageInteraction):
        await inter.response.edit_message(embed=await _panel(inter.bot, inter.guild),
                                          view=StreamsView())

    @disnake.ui.channel_select(placeholder="📣 Куда анонсировать", custom_id="hzstream:chan",
                               channel_types=[disnake.ChannelType.text], row=1)
    async def pick_channel(self, select: disnake.ui.ChannelSelect, inter: disnake.MessageInteraction):
        if not is_admin(inter.author):
            return await inter.response.send_message(
                embed=note_embed("❌ Канал анонсов выбирает руководство семьи."), ephemeral=True)
        data = _load()
        data["channel"] = select.values[0].id
        _save(data)
        await inter.response.send_message(
            embed=note_embed(f"📣 Анонсы пойдут в {select.values[0].mention}."), ephemeral=True)


async def _panel(bot, guild: Optional[disnake.Guild]) -> disnake.Embed:
    data = _load()
    subs = data.get("subs") or {}
    live = brand.emoji(bot, "hz_megaphone", "🔴")
    rows = []
    for slug, sub in sorted(subs.items(), key=lambda kv: (not kv[1].get("live"),
                                                           kv[1].get("label", ""))):
        mark = f"{live} в эфире" if sub.get("live") else "⚫ офлайн"
        since = ""
        if sub.get("live") and sub.get("since"):
            since = f" · с {time.strftime('%H:%M', time.localtime(sub['since']))}"
        rows.append([clip(sub.get("label") or slug, 60),
                     f"[{slug}](https://twitch.tv/{slug}) · {mark}{since}" +
                     (f"\nпинг <@&{sub['role']}>" if sub.get("role") else "")])
    where = f"<#{data['channel']}>" if data.get("channel") else "не выбран — выбери селектом ниже"
    embed = list_embed("📡 Стримы под наблюдением", rows, guild=guild, inline=False,
                       empty=f"Каналов нет. Добавь до {MAX_CHANNELS} кнопкой «➕ Добавить канал».",
                       note=f"Проверка каждые {CHECK_SECONDS} сек · анонсы в {where}")
    if not data.get("channel"):
        embed.color = disnake.Color(0xF39C12)
    return embed


class Streams(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def cog_unload(self):
        self.watch.stop()

    @commands.Cog.listener()
    async def on_ready(self):
        if not getattr(self.bot, "_streams_view_added", False):
            self.bot.add_view(StreamsView())
            self.bot._streams_view_added = True
        if not self.watch.is_running():
            self.watch.change_interval(seconds=CHECK_SECONDS)
            self.watch.start()

    @commands.slash_command(name="стримы", description="Панель авто-анонсов Twitch-стримов",
                            contexts=disnake.InteractionContextTypes(guild=True),
                            default_member_permissions=disnake.Permissions(manage_guild=True))
    async def streams_panel(self, inter: disnake.ApplicationCommandInteraction):
        await inter.channel.send(embed=await _panel(inter.bot, inter.guild), view=StreamsView())
        await inter.response.send_message(embed=note_embed("✅ Панель стримов отправлена."),
                                          ephemeral=True)

    async def add(self, inter, raw_login: str, label: str, raw_role: str) -> None:
        slug = _login(raw_login)
        if len(slug) < 2:
            return await inter.response.send_message(
                embed=note_embed("❌ Не понял имя канала. Формат: `twitch.tv/имя`."),
                ephemeral=True)
        data = _load()
        subs = data.setdefault("subs", {})
        if len(subs) >= MAX_CHANNELS and slug not in subs:
            return await inter.response.send_message(
                embed=note_embed(f"❌ Максимум {MAX_CHANNELS} каналов — убери лишние."),
                ephemeral=True)
        role = "".join(c for c in str(raw_role or "") if c.isdigit())
        if role:
            if inter.guild.get_role(int(role)) is None:
                return await inter.response.send_message(
                    embed=note_embed("❌ Такой роли на сервере нет."), ephemeral=True)
        subs[slug] = {"label": clip(label or slug, 40), "role": int(role) if role else 0,
                      "live": subs.get(slug, {}).get("live", False),
                      "since": subs.get(slug, {}).get("since", 0),
                      "added": int(time.time())}
        _save(data)
        await inter.response.send_message(
            embed=note_embed(f"📡 Слежу за [twitch.tv/{slug}](https://twitch.tv/{slug}). "
                             "Первый анон — как только увижу эфир."), ephemeral=True)

    @tasks.loop(seconds=CHECK_SECONDS)
    async def watch(self):
        data = _load()
        subs = data.get("subs") or {}
        if not subs:
            return
        session = getattr(self.bot.http, "session", None)
        if session is None:
            return
        for slug, sub in list(subs.items()):
            live = await self._is_live(session, slug)
            if live is None:
                continue  # сеть/ошибка — не трогаем состояние
            if live and not sub.get("live"):
                sub["live"] = True
                sub["since"] = int(time.time())
                _save(data)
                await self._announce(slug, sub)
            elif not live and sub.get("live"):
                sub["live"] = False
                sub["since"] = 0
                _save(data)
                await self._went_offline(slug, sub)

    @watch.error
    async def watch_error(self, error):
        print(f"[streams] цикл проверки: {error}")

    async def _is_live(self, session, slug: str) -> Optional[bool]:
        """True — превью отдаётся, False — 404, None — сеть не ответила или ответ странный.

        HEAD не качает кадр (на 256 МБ это важно); если CDN его не понимает, берём GET
        без чтения тела.
        """
        url = PREVIEW.format(login=slug)
        try:
            async with session.head(url) as resp:
                if resp.status in (405, 501, 400):
                    async with session.get(url, read=False) as fallback:
                        return _status_is_live(fallback.status)
                return _status_is_live(resp.status)
        except Exception:
            return None

    async def _target(self, data: dict):
        ch_id = int(data.get("channel") or 0)
        if not ch_id:
            return None
        try:
            return self.bot.get_channel(ch_id) or await self.bot.fetch_channel(ch_id)
        except disnake.HTTPException:
            return None

    async def _announce(self, slug: str, sub: dict) -> None:
        data = _load()
        channel = await self._target(data)
        if channel is None:
            return
        name = clip(sub.get("label") or slug, 40)
        ping = f"{role_ping(self.bot, sub.get('role'))} " if sub.get("role") else ""
        embed = card(f"🔴 {name} — в эфире", guild=channel.guild, color_key="embed_color")
        embed.description = (f"{ping}[**twitch.tv/{slug}**](https://twitch.tv/{slug}) начал стрим "
                             f"{time.strftime('%H:%M', time.localtime(sub.get('since')))} — "
                             "заходи, пока горячее.")
        embed.set_image(url=PREVIEW.format(login=slug))
        view = disnake.ui.View(timeout=None)
        view.add_item(disnake.ui.Button(label="Смотреть на Twitch", emoji="▶",
                                        style=disnake.ButtonStyle.link,
                                        url=f"https://twitch.tv/{slug}"))
        try:
            await channel.send(embed=embed, view=view)
        except disnake.HTTPException:
            pass

    async def _went_offline(self, slug: str, sub: dict) -> None:
        data = _load()
        channel = await self._target(data)
        if channel is None:
            return
        name = clip(sub.get("label") or slug, 40)
        minutes = max(1, int((time.time() - (sub.get("since") or time.time())) / 60))
        try:
            await channel.send(embed=note_embed(f"⚫ {name} завершил стрим · в эфире был {minutes} мин"))
        except disnake.HTTPException:
            pass


def role_ping(bot, role_id: int) -> str:
    role = bot.get_role(int(role_id)) if role_id else None
    return role.mention if role else ""


def setup(bot: commands.Bot):
    bot.add_cog(Streams(bot))
