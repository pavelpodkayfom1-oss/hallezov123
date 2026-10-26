"""Музыка в голосовом: очередь треков, панель заказов и библиотека семьи.

Разбор YouTube выключен совсем: yt-dlp на пике ест 60-100 МБ, а у хостинга 256 МБ на
всё — бот умирал посреди поиска. Теперь трек берётся из папки `media/` рядом с main.py
её наполняют командой /music_add, куда mp3 прикладывается прямо в сообщении) либо
сразу играется по прямой ссылке на звук. ffmpeg при этом читает поток сам, второй
процесс с тяжёлым парсером не нужен и памяти хватает. Нужен ffmpeg: на хостинге его нет
в PATH, поэтому берётся статический бинарник из imageio-ffmpeg (см. _ffmpeg_exe).
"""
import asyncio
import os
import re
import time
from collections import deque
from urllib.parse import unquote, urlparse

import disnake
from disnake.ext import commands

from utils.checks import load_config
from utils.embeds import note_embed

# Кулдаун на заказ музыки (секунд на одного пользователя)
PLAY_COOLDOWN = 20
# Сколько треков максимум может стоять в очереди
MAX_QUEUE = 25
# Больше этого файла в библиотеку не кладём: диск 3 ГБ, а файл грузится в память
MAX_TRACK_MB = 20
AUDIO_EXT = (".mp3", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wav", ".flac")

# Библиотека лежит рядом с main.py и в git не попадает (в .gitignore там "*"),
# поэтому треки переживают деплой и рестарты.
MEDIA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "media")


def _trim():
    """Возвращает освобождённую память операционной системе (на Linux — через malloc)."""
    import gc

    gc.collect()
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def _rss_mb() -> int:
    """Сколько памяти (МБ) занимает бот — для диагностики на слабом хосте."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return -1


def _log(msg: str):
    print(f"[music] {msg} | RSS={_rss_mb()} МБ", flush=True)


def _library() -> list:
    """Что лежит в библиотеке семьи, готовое к воспроизведению."""
    try:
        return sorted(f for f in os.listdir(MEDIA_DIR) if f.lower().endswith(AUDIO_EXT))
    except FileNotFoundError:
        return []


def _library_text() -> str:
    files = _library()
    if not files:
        return (f"Библиотека пуста: в {MEDIA_DIR} нет ни одного файла.\n"
                "Нажми «📥 Закинуть трек» на панели и прикрепи mp3.")
    lines = [f"`{i + 1}.` {os.path.splitext(f)[0]}" for i, f in enumerate(files[:25])]
    extra = f"\n…и ещё {len(files) - 25}" if len(files) > 25 else ""
    return "🎧 Библиотека семьи\n" + "\n".join(lines) + extra


def _safe_name(name: str) -> str:
    """Имя файла с Discord: без путей, точек подряд и мусора — по нему потом ищем трек."""
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = re.sub(r"[^\w\- ]+", "_", stem, flags=re.UNICODE).strip()
    stem = re.sub(r"\s+", " ", stem)
    return (stem or "track")[:80]


async def _save_track(attachment) -> str:
    """Кладёт вложение в библиотеку и возвращает имя файла. Не звук / слишком большой — ошибка."""
    ext = os.path.splitext(attachment.filename)[1].lower()
    if ext not in AUDIO_EXT:
        raise RuntimeError(f"`{attachment.filename}` — не звук. Подходят: " + ", ".join(AUDIO_EXT))
    if attachment.size > MAX_TRACK_MB * 1024 * 1024:
        raise RuntimeError(f"Файл больше {MAX_TRACK_MB} МБ — на этом хостинге не влезет.")
    name = _safe_name(attachment.filename) + ext
    data = await attachment.read()
    os.makedirs(MEDIA_DIR, exist_ok=True)
    with open(os.path.join(MEDIA_DIR, name), "wb") as f:
        f.write(data)
    _log(f"в библиотеку добавлен {name} ({len(_library())} треков)")
    return name


FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
# -vn -sn -dn: ffmpeg не декодирует видео и субтитры — меньше буферов на слабом хосте
FFMPEG_OPTS = "-vn -sn -dn"

_EXE = None


def _ffmpeg_exe():
    """Какой ffmpeg запускать: системный, а на хостинге обычно только из imageio-ffmpeg.

    На бесплатном контейнере нет ни apt, ни бинарника ffmpeg, и без этого трек находится,
    голос подключается, а играть нечем. Колесо imageio-ffmpeg приносит статический
    бинарник с собой — его и берём, если в PATH пусто.
    """
    global _EXE
    if _EXE is None:
        import shutil

        _EXE = shutil.which("ffmpeg") or ""
        if not _EXE:
            try:
                from imageio_ffmpeg import get_ffmpeg_exe

                _EXE = get_ffmpeg_exe()
            except Exception:
                _EXE = ""
        _log(f"ffmpeg: {_EXE or 'не найден'}")
    return _EXE or None


def _color(key: str = "embed_color") -> int:
    try:
        return int(str(load_config().get(key, "0x990000")), 16)
    except Exception:
        return 0x990000


def _fmt_duration(sec) -> str:
    if sec is None:
        return "—"
    if not sec:
        return "live"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class GuildPlayer:
    def __init__(self):
        self.queue: deque = deque()
        self.current = None
        self.source = None
        self.volume = 0.5
        self.text_channel = None


class MusicModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(
            title="Слушаю, сэр! Какую музыку включить?",
            custom_id="music_modal",
            components=[
                disnake.ui.TextInput(
                    label="Название трека / исполнителя / ссылка",
                    custom_id="query",
                    style=disnake.TextInputStyle.short,
                    placeholder="Например: Эксонад музыка",
                    max_length=200,
                )
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        await inter.response.defer(ephemeral=True)
        await cog.handle_play(inter, inter.text_values["query"].strip())


class TalkView(disnake.ui.View):
    """Показывается после 'Позвать в войс' — кнопка для выбора музыки."""

    def __init__(self):
        super().__init__(timeout=300)

    @disnake.ui.button(label="Назвать музыку", emoji="🎵", style=disnake.ButtonStyle.success)
    async def pick(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.send_modal(MusicModal())


class TrackSelect(disnake.ui.StringSelect):
    """Выпадающий список треков из библиотеки: выбрал — сразу играет."""

    def __init__(self, files: list):
        options = [
            disnake.SelectOption(label=os.path.splitext(f)[0][:100], value=f[:100], emoji="🎵")
            for f in files[:25]
        ]
        super().__init__(placeholder="🎧 Выбери трек из библиотеки", options=options,
                         min_values=1, max_values=1)

    async def callback(self, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        await inter.response.defer(ephemeral=True)
        await cog.handle_play(inter, os.path.splitext(self.values[0])[0])


class PlayPanelView(disnake.ui.View):
    """Появляется после «Включить музыку»: выбор трека, свой запрос и загрузка файла."""

    def __init__(self):
        super().__init__(timeout=300)
        files = _library()
        if files:
            self.add_item(TrackSelect(files))

    @disnake.ui.button(label="Загрузить трек", emoji="📥", style=disnake.ButtonStyle.primary, row=1)
    async def upload(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("Music").ask_file(inter)

    @disnake.ui.button(label="По названию / ссылке", emoji="🔎", style=disnake.ButtonStyle.secondary, row=1)
    async def by_name(self, button, inter: disnake.MessageInteraction):
        await inter.response.send_modal(MusicModal())


def _play_panel_embed() -> disnake.Embed:
    files = _library()
    if files:
        text = (f"В библиотеке **{len(files)}** треков. Выбери трек из списка ниже — бот зайдёт "
                "к тебе в войс и сразу включит.\n\n"
                "Нет нужного? Нажми **«📥 Загрузить трек»** и прикрепи mp3 — "
                "или введи название / прямую ссылку на звук через **«🔎»**.")
    else:
        text = ("Библиотека пока пустая.\n\n"
                "Нажми **«📥 Загрузить трек»** и прикрепи mp3 (можно сразу несколько раз подряд), "
                "потом снова открой эту панель.")
    return disnake.Embed(title="🎵 Музыка", description=text, color=_color())


class MusicPanelView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # ---------- ряд 1 ----------
    @disnake.ui.button(label="Включить музыку", emoji="🎵", style=disnake.ButtonStyle.success,
                       custom_id="music:play", row=0)
    async def play(self, button, inter: disnake.MessageInteraction):
        if not inter.author.voice or not inter.author.voice.channel:
            return await inter.response.send_message(
                embed=note_embed("❌ Сначала зайдите в голосовой канал, сэр."), ephemeral=True)
        await inter.response.send_message(embed=_play_panel_embed(), view=PlayPanelView(), ephemeral=True)

    @disnake.ui.button(label="Позвать в войс", emoji="🎙", style=disnake.ButtonStyle.primary,
                       custom_id="music:join", row=0)
    async def join(self, button, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        await inter.response.defer(ephemeral=True)
        vc = await cog.ensure_voice(inter)
        if not vc:
            return
        cog.get_player(inter.guild).text_channel = inter.channel
        await inter.edit_original_response(
            content="🎙 **Слушаю, сэр! Какую музыку желаете?**", view=TalkView())

    @disnake.ui.button(label="Выйти", emoji="🚪", style=disnake.ButtonStyle.danger,
                       custom_id="music:leave", row=0)
    async def leave(self, button, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        vc = await cog.check_control(inter)
        if not vc:
            return
        await cog.cleanup(inter.guild)
        await inter.response.send_message(embed=note_embed("👋 Отключился от голосового канала."), ephemeral=True)

    # ---------- ряд 2 ----------
    @disnake.ui.button(label="Пауза / Продолжить", emoji="⏯", style=disnake.ButtonStyle.secondary,
                       custom_id="music:pause", row=1)
    async def pause(self, button, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        vc = await cog.check_control(inter)
        if not vc:
            return
        if vc.is_paused():
            vc.resume()
            msg = "▶️ Продолжаю."
        elif vc.is_playing():
            vc.pause()
            msg = "⏸ Пауза."
        else:
            msg = "Сейчас ничего не играет."
        await inter.response.send_message(embed=note_embed(msg), ephemeral=True)

    @disnake.ui.button(label="Скип", emoji="⏭", style=disnake.ButtonStyle.secondary,
                       custom_id="music:skip", row=1)
    async def skip(self, button, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        vc = await cog.check_control(inter)
        if not vc:
            return
        if vc.is_playing() or vc.is_paused():
            vc.stop()
            await inter.response.send_message(embed=note_embed("⏭ Трек пропущен."), ephemeral=True)
        else:
            await inter.response.send_message(embed=note_embed("Сейчас ничего не играет."), ephemeral=True)

    @disnake.ui.button(label="Стоп", emoji="⏹", style=disnake.ButtonStyle.secondary,
                       custom_id="music:stop", row=1)
    async def stop(self, button, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        vc = await cog.check_control(inter)
        if not vc:
            return
        cog.get_player(inter.guild).queue.clear()
        if vc.is_playing() or vc.is_paused():
            vc.stop()
        await inter.response.send_message(embed=note_embed("⏹ Остановлено, очередь очищена."), ephemeral=True)

    @disnake.ui.button(label="Очередь", emoji="📜", style=disnake.ButtonStyle.secondary,
                       custom_id="music:queue", row=1)
    async def queue(self, button, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        p = cog.get_player(inter.guild)
        lines = []
        if p.current:
            lines.append(f"▶️ **Сейчас:** {p.current['title']} `{_fmt_duration(p.current['duration'])}`")
        for i, t in enumerate(list(p.queue)[:10], 1):
            lines.append(f"`{i}.` {t['title']} `{_fmt_duration(t['duration'])}`")
        if len(p.queue) > 10:
            lines.append(f"…и ещё {len(p.queue) - 10}")
        await inter.response.send_message(embed=note_embed("\n".join(lines) or "Очередь пуста."), ephemeral=True)

    # ---------- ряд 3 ----------
    @disnake.ui.button(label="Тише", emoji="🔉", style=disnake.ButtonStyle.secondary,
                       custom_id="music:vol_down", row=2)
    async def vol_down(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("Music").change_volume(inter, -0.1)

    @disnake.ui.button(label="Громче", emoji="🔊", style=disnake.ButtonStyle.secondary,
                       custom_id="music:vol_up", row=2)
    async def vol_up(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("Music").change_volume(inter, +0.1)

    # ---------- ряд 4: библиотека ----------
    @disnake.ui.button(label="Закинуть трек", emoji="📥", style=disnake.ButtonStyle.primary,
                       custom_id="music:add", row=3)
    async def add(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("Music").ask_file(inter)

    @disnake.ui.button(label="Треки", emoji="🎧", style=disnake.ButtonStyle.secondary,
                       custom_id="music:library", row=3)
    async def library(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("Music").show_library(inter)


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.players: dict[int, GuildPlayer] = {}
        self._last_play: dict[int, float] = {}
        # кто нажал «📥 Закинуть трек»: id → (канал, когда)
        self._pending: dict[int, tuple[int, float]] = {}

    @commands.Cog.listener()
    async def on_ready(self):
        # persistent view: кнопки панели работают и после перезапуска бота
        if not getattr(self.bot, "_music_view_added", False):
            self.bot.add_view(MusicPanelView())
            self.bot._music_view_added = True
            _log(f"библиотека: {len(_library())} треков в {MEDIA_DIR}")

    # ---------- библиотека без слэш-команд ----------
    ASK_SECONDS = 90

    async def ask_file(self, inter):
        """Кнопка «Закинуть трек»: ждём вложение ответом в этом же канале."""
        self._pending[inter.author.id] = (inter.channel.id, time.time())
        await inter.response.send_message(
            embed=note_embed("📥 Прикрепи mp3 к сообщению здесь — заберу его в библиотеку. "
                             f"Жду {self.ASK_SECONDS} сек."), ephemeral=True)

    async def show_library(self, inter):
        await inter.response.send_message(embed=note_embed(_library_text()), ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        stamp = self._pending.get(message.author.id)
        if not stamp or stamp[0] != message.channel.id:
            return
        if time.time() - stamp[1] > self.ASK_SECONDS:
            self._pending.pop(message.author.id, None)
            return
        if not message.attachments:
            return
        self._pending.pop(message.author.id, None)
        try:
            name = await _save_track(message.attachments[0])
        except Exception as e:
            answer = f"❌ {e}"
        else:
            _trim()
            stem = os.path.splitext(name)[0]
            answer = f"🎧 В библиотеке: **{stem}** — заходи в войс и `/play {stem}`"
        try:
            await message.reply(embed=note_embed(answer), mention_author=False)
        except Exception:
            pass

    # ---------- helpers ----------
    def get_player(self, guild: disnake.Guild) -> GuildPlayer:
        if guild.id not in self.players:
            self.players[guild.id] = GuildPlayer()
        return self.players[guild.id]

    async def cleanup(self, guild: disnake.Guild):
        p = self.players.pop(guild.id, None)
        if p:
            p.queue.clear()
            p.current = None
            p.source = None
        vc = guild.voice_client
        if vc:
            await vc.disconnect(force=True)
        _trim()

    async def _reply(self, inter, text: str):
        if inter.response.is_done():
            await inter.followup.send(embed=note_embed(text), ephemeral=True)
        else:
            await inter.response.send_message(embed=note_embed(text), ephemeral=True)

    async def ensure_voice(self, inter):
        """Заходит в канал пользователя (или переезжает). Возвращает VoiceClient."""
        voice = inter.author.voice
        if not voice or not voice.channel:
            await self._reply(inter, "❌ Сначала зайдите в голосовой канал, сэр.")
            return None
        vc = inter.guild.voice_client
        try:
            if vc is None:
                vc = await voice.channel.connect(timeout=20)
            elif vc.channel.id != voice.channel.id:
                if vc.is_playing() or vc.is_paused():
                    await self._reply(
                        inter, f"❌ Я уже играю в канале **{vc.channel.name}**. Зайдите туда.")
                    return None
                await vc.move_to(voice.channel)
        except Exception as e:
            await self._reply(inter, f"❌ Не удалось зайти в войс: `{e}`\n"
                                     "Проверьте права бота (Connect / Speak) и что установлен PyNaCl.")
            return None
        return vc

    async def check_control(self, inter):
        vc = inter.guild.voice_client
        if not vc or not vc.is_connected():
            await self._reply(inter, "❌ Бот сейчас не в голосовом канале.")
            return None
        voice = inter.author.voice
        if not voice or voice.channel.id != vc.channel.id:
            await self._reply(inter, "❌ Вы должны быть в одном голосовом канале с ботом.")
            return None
        return vc

    async def change_volume(self, inter, delta: float):
        vc = await self.check_control(inter)
        if not vc:
            return
        p = self.get_player(inter.guild)
        p.volume = max(0.05, min(1.0, round(p.volume + delta, 2)))
        if p.source:
            p.source.volume = p.volume
        await inter.response.send_message(embed=note_embed(f"🔊 Громкость: **{int(p.volume * 100)}%**"), ephemeral=True)

    async def _extract(self, query: str) -> dict:
        """Находит трек: прямая ссылка на звук играется как есть, остальное ищем в media/."""
        query = query.strip()
        if not query:
            raise RuntimeError("Пустой запрос")
        if query.lower().startswith(("http://", "https://")):
            return self._from_link(query)
        path = self._find_track(query)
        return {
            "title": os.path.splitext(os.path.basename(path))[0][:120],
            "stream_url": path,
            "webpage_url": query,
            "duration": None,
            "thumbnail": None,
            "ts": time.time(),
        }

    @staticmethod
    def _from_link(url: str) -> dict:
        """Ссылка, которая ведёт прямо на аудиофайл: ffmpeg съест её сам."""
        name = unquote(os.path.basename(urlparse(url).path))
        stem = os.path.splitext(name)[0] if name.lower().endswith(AUDIO_EXT) else ""
        return {
            "title": (stem or url)[:120],
            "stream_url": url,
            "webpage_url": url,
            "duration": None,
            "thumbnail": None,
            "ts": time.time(),
        }

    @staticmethod
    def _find_track(query: str) -> str:
        """Трек из библиотеки: сначала точное имя, потом подстрока в имени файла."""
        files = _library()
        if not files:
            raise RuntimeError(f"Библиотека пуста: в {MEDIA_DIR} нет ни одного файла. "
                               "Нажми «📥 Закинуть трек» на музыкальной панели")
        want = re.sub(r"\s+", " ", query.lower()).strip()
        stems = {os.path.splitext(f)[0].lower(): f for f in files}
        hit = stems.get(want)
        if hit is None:
            hits = sorted(f for s, f in stems.items() if want in s)
            if not hits:
                raise RuntimeError(f"Нет трека «{query}» в библиотеке "
                                   f"(всего {len(files)}, список — кнопка «🎧 Треки»)")
            if len(hits) > 1:
                raise RuntimeError("Похоже сразу на несколько: "
                                   + ", ".join(os.path.splitext(h)[0] for h in hits[:6]))
            hit = hits[0]
        return os.path.join(MEDIA_DIR, hit)

    # ---------- core ----------
    async def handle_play(self, inter, query: str):
        """inter уже должен быть defer'нут."""
        if not query:
            return await inter.followup.send(embed=note_embed("❌ Пустой запрос."), ephemeral=True)
        # кулдаун: не чаще одного заказа в PLAY_COOLDOWN секунд на человека
        now = time.time()
        wait = PLAY_COOLDOWN - (now - self._last_play.get(inter.author.id, 0))
        if wait > 0:
            return await inter.followup.send(
                embed=note_embed(f"⏳ Не так быстро, сэр. Подождите ещё {int(wait) + 1} сек."), ephemeral=True)
        self._last_play[inter.author.id] = now
        p = self.get_player(inter.guild)
        if len(p.queue) >= MAX_QUEUE:
            return await inter.followup.send(
                embed=note_embed(f"️ Очередь забита ({MAX_QUEUE} треков). Почистите её кнопкой ⏹."),
                ephemeral=True)
        vc = await self.ensure_voice(inter)
        if not vc:
            return
        p.text_channel = inter.channel
        try:
            track = await self._extract(query)
        except Exception as e:
            return await inter.followup.send(embed=note_embed(f"❌ Не удалось найти/загрузить: `{e}`"), ephemeral=True)
        track["requester"] = inter.author.mention
        p.queue.append(track)

        if vc.is_playing() or vc.is_paused():
            await inter.followup.send(embed=note_embed(f"➕ Добавлено в очередь: **{track['title']}**"), ephemeral=True)
        else:
            await inter.followup.send(embed=note_embed(f"🎶 Включаю: **{track['title']}**"), ephemeral=True)
            await self._play_next(inter.guild)

    async def _play_next(self, guild: disnake.Guild):
        p = self.players.get(guild.id)
        vc = guild.voice_client
        if not p or not vc or not vc.is_connected():
            return
        if vc.is_playing() or vc.is_paused():
            return
        if not p.queue:
            p.current = None
            p.source = None
            return

        track = p.queue.popleft()
        try:
            if track["webpage_url"].startswith("http") and time.time() - track["ts"] > 600:
                # ссылка на чужой стрим могла протухнуть, файл с диска — нет
                fresh = await self._extract(track["webpage_url"])
                track.update(fresh)
            source = disnake.PCMVolumeTransformer(
                disnake.FFmpegPCMAudio(track["stream_url"], executable=_ffmpeg_exe(),
                                       before_options=FFMPEG_BEFORE, options=FFMPEG_OPTS),
                volume=p.volume,
            )
        except Exception as e:
            if p.text_channel:
                await p.text_channel.send(
                    f"❌ Не смог проиграть **{track['title']}**: `{e}`\n"
                    + ("На хосте нет ffmpeg — добавь `imageio-ffmpeg` в requirements.txt и "
                       "перезапусти бота." if _ffmpeg_exe() is None else
                       "Дальше поможет рестарт: возможно, ffmpeg не успевает стартовать."))
            return await self._play_next(guild)

        p.current = track
        p.source = source

        loop = asyncio.get_running_loop()

        def after(err):
            if err:
                print(f"[music] ошибка воспроизведения: {err}", flush=True)
            asyncio.run_coroutine_threadsafe(self._play_next(guild), loop)

        vc.play(source, after=after)

        if p.text_channel:
            link = track["webpage_url"]
            shown = f"[{track['title']}]({link})" if link.startswith("http") else f"**{track['title']}**"
            embed = disnake.Embed(
                title="🎶 Сейчас играет",
                description=shown,
                color=_color(),
            )
            embed.add_field(name="Длительность", value=_fmt_duration(track["duration"]))
            if track.get("requester"):
                embed.add_field(name="Заказал", value=track["requester"])
            if track.get("thumbnail"):
                embed.set_thumbnail(url=track["thumbnail"])
            try:
                await p.text_channel.send(embed=embed)
            except Exception:
                pass

    # ---------- commands ----------
    @commands.slash_command(name="music_panel", description="Отправить панель музыки в этот канал",
                            default_member_permissions=disnake.Permissions(manage_guild=True))
    async def music_panel(self, inter: disnake.ApplicationCommandInteraction):
        embed = disnake.Embed(
            title="🎧 Музыкальная панель",
            description=(
                "Зайдите в голосовой канал и нажмите **«Включить музыку»**.\n"
                "Бот спросит название из библиотеки семьи (или ссылку на звук), зайдёт к вам "
                "в войс и поставит трек.\n\n"
                "Треки кладутся кнопкой **«📥 Закинуть трек»** — просто прикрепи mp3. "
                "«🎧 Треки» покажет, что уже есть.\n\n"
                "Управление: ⏯ пауза • ⏭ скип • ⏹ стоп • 📜 очередь • 🔉🔊 громкость"
            ),
            color=_color(),
        )
        await inter.channel.send(embed=embed, view=MusicPanelView())
        await inter.response.send_message(embed=note_embed("✅ Панель отправлена."), ephemeral=True)

    @commands.slash_command(name="play", description="Включить трек из библиотеки (имя или прямая ссылка)")
    async def play_cmd(self, inter: disnake.ApplicationCommandInteraction, запрос: str):
        await inter.response.defer(ephemeral=True)
        await self.handle_play(inter, запрос.strip())

    @commands.slash_command(name="music_add", description="Закинуть трек в библиотеку: прикрепи mp3")
    async def add_cmd(self, inter: disnake.ApplicationCommandInteraction, файл: disnake.Attachment):
        await inter.response.defer(ephemeral=True)
        try:
            name = await _save_track(файл)
        except Exception as e:
            return await inter.followup.send(embed=note_embed(f"❌ {e}"), ephemeral=True)
        _trim()
        stem = os.path.splitext(name)[0]
        await inter.followup.send(
            embed=note_embed(f"📥 Готово: **{stem}**.\n"
                             f"Стоя в голосовом, напиши `/play {stem}`"),
            ephemeral=True)

    @commands.slash_command(name="music_list", description="Что есть в библиотеке семьи")
    async def list_cmd(self, inter: disnake.ApplicationCommandInteraction):
        await self.show_library(inter)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # если бот остался один в канале — выходим
        vc = member.guild.voice_client
        if vc and vc.channel and len([m for m in vc.channel.members if not m.bot]) == 0:
            await asyncio.sleep(60)
            vc = member.guild.voice_client
            if vc and vc.channel and len([m for m in vc.channel.members if not m.bot]) == 0:
                await self.cleanup(member.guild)


def setup(bot: commands.Bot):
    bot.add_cog(Music(bot))
