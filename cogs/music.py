"""Музыка в голосовом: очередь треков, панель заказов и библиотека семьи.

Трек берётся тремя путями: из папки `media/` по имени (её наполняют командой
/music_add, куда mp3 прикладывается прямо в сообщении), по прямой ссылке на звук —
такую ffmpeg читает сам, и по ссылке на YouTube — тут уже нужен yt-dlp.
Разбор YouTube идёт по одному запросу за раз и чистит память после каждого: парсер
на пике просит 60-100 МБ, и два параллельных поиска уже бьют по лимиту хостинга.
Нужен ffmpeg: на хостинге его нет в PATH, поэтому берётся статический бинарник
из imageio-ffmpeg (см. _ffmpeg_exe).
"""
import asyncio
import json
import os
import random
import re
import tempfile
import time
from collections import deque
from urllib.parse import unquote, urlparse

import disnake
from disnake.ext import commands

from utils import brand, cdn_assets
from utils.checks import is_admin, load_config
from utils.embeds import card, list_embed, note_embed, panel_embed

# Кулдаун на заказ музыки (секунд на одного пользователя)
PLAY_COOLDOWN = 20
# Сколько треков максимум может стоять в очереди
MAX_QUEUE = 25
# Больше этого файла в библиотеку не кладём: диск хостинга 3 ГБ, а память 256 МБ
MAX_TRACK_MB = 40
# Сколько вложений за раз принимает кнопка «Закинуть трек»
MAX_FILES_IN_MSG = 5
AUDIO_EXT = (".mp3", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wav", ".flac")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Библиотека лежит рядом с main.py и в git не попадает (в .gitignore там "*"),
# поэтому треки переживают деплой и рестарты.
MEDIA_DIR = os.path.join(BASE_DIR, "media")
SETTINGS_FILE = os.path.join(BASE_DIR, "music_settings.json")


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


def _stem(path: str) -> str:
    """Имя трека без пути и расширения — по нему ищут заказ и плейлисты."""
    return os.path.splitext(os.path.basename(str(path)))[0]


def _library() -> list:
    """Абсолютные пути треков: media/ со всеми подпапками плюс корень приложения.

    Перечитываем каждый раз — тогда файл, положенный во вкладку Files на хостинге,
    играет сразу, без рестарта бота. Корень обходим только верхним слоем: рекурсия по
    нему ушла бы в site-packages. Один трек в media/ и в корне даёт одну строку.
    """
    found: dict = {}
    for here, dirs, files in os.walk(MEDIA_DIR):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in files:
            if name.lower().endswith(AUDIO_EXT):
                found.setdefault(_stem(name).lower(), os.path.join(here, name))
    try:
        for name in sorted(os.listdir(BASE_DIR)):
            if name.lower().endswith(AUDIO_EXT):
                found.setdefault(_stem(name).lower(), os.path.join(BASE_DIR, name))
    except OSError:
        pass
    return sorted(found.values(), key=lambda p: _stem(p).lower())


def _library_text() -> str:
    files = _library()
    if not files:
        return (f"Библиотека пуста: в `{MEDIA_DIR}` нет ни одного файла.\n"
                "Кнопка «Закинуть трек» принимает mp3 прямо из сообщения, а ещё можно "
                "положить файл во вкладку Files рядом с `main.py`.")
    lines = [f"`{i + 1}.` {_stem(f)}" for i, f in enumerate(files[:25])]
    extra = f"\n…и ещё {len(files) - 25}" if len(files) > 25 else ""
    return "🎧 Библиотека семьи\n" + "\n".join(lines) + extra


# ---------- плейлисты ----------
# Лежат одним файлом рядом с треками: переживают деплой, но в git не попадают.
PLAYLIST_FILE = os.path.join(MEDIA_DIR, "playlists.json")
MAX_PLAYLISTS = 30
MAX_IN_PLAYLIST = 50


def _playlists() -> dict:
    try:
        with open(PLAYLIST_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): [str(t) for t in v if str(t).strip()]
            for k, v in data.items() if isinstance(v, list)}


def _playlists_write(data: dict) -> str:
    """Пишет плейлисты целиком; файл меняется целиком, чтобы не оставлять обрезанный json."""
    try:
        os.makedirs(MEDIA_DIR, exist_ok=True)
        tmp = PLAYLIST_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PLAYLIST_FILE)
        return ""
    except Exception as e:
        return f"Файл плейлистов не записался: `{e}`"


def _pl_clean(name: str) -> str:
    """Имя плейлиста: одна строка без путей — по нему потом ищем."""
    return re.sub(r"\s+", " ", str(name or "")).strip()[:40]


def _playlists_text(data: dict) -> str:
    have = {_stem(f).lower() for f in _library()}
    lines = []
    for name, tracks in data.items():
        known = sum(1 for t in tracks if t.lower() in have)
        lines.append(f"**{name}** — {len(tracks)} треков" +
                     ("" if known == len(tracks) else f" (в библиотеке {known})"))
    return "🎼 Плейлисты семьи\n" + "\n".join(lines)


def _stem_to_path(stem: str):
    """Путь к треку из плейлиста, если файл до сих пор в библиотеке."""
    want = str(stem or "").strip().lower()
    for f in _library():
        if _stem(f).lower() == want:
            return f
    return None


def _safe_name(name: str) -> str:
    """Имя файла с Discord: без путей, точек подряд и мусора — по нему потом ищем трек."""
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = re.sub(r"[^\w\- ]+", "_", stem, flags=re.UNICODE).strip()
    stem = re.sub(r"\s+", " ", stem)
    return (stem or "track")[:80]


async def _save_track(attachment) -> str:
    """Кладёт вложение в библиотеку и возвращает путь. Не звук / слишком большой — ошибка.

    Файл пишем потоком (`save`), а не `read()` в память: 40 МБ mp3 плюс очередь из
    десяти таких же — и хостинг на 256 МБ убивается OOM-киллером посреди заказа.
    """
    filename = str(attachment.filename or "track")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in AUDIO_EXT:
        raise RuntimeError(f"`{filename}` — не звук. Подходят: " + ", ".join(AUDIO_EXT))
    if attachment.size > MAX_TRACK_MB * 1024 * 1024:
        raise RuntimeError(f"Файл больше {MAX_TRACK_MB} МБ — на этом хостинге не влезет.")
    name = _safe_name(filename) + ext
    os.makedirs(MEDIA_DIR, exist_ok=True)
    path = os.path.join(MEDIA_DIR, name)
    await attachment.save(path)
    _log(f"в библиотеку добавлен {name} ({len(_library())} треков)")
    return path


FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
# -vn -sn -dn: ffmpeg не декодирует видео и субтитры — меньше буферов на слабом хосте
FFMPEG_OPTS = "-vn -sn -dn"


def _before_options(source: str):
    """Сетевые опции — только ссылкам.

    Локальный файл с «-reconnect» ffmpeg не открывает вообще: он отвечает
    «Option reconnect not found → Error opening input file», и трек из библиотеки
    молча не запускался, хотя голосовое подключалось.
    """
    return FFMPEG_BEFORE if str(source).startswith("http") else None

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


# ═════════════════════════ YouTube ═════════════════════════
# Ссылки на YouTube сам ffmpeg не играет: нужен парсер, который достаёт адрес
# аудиопотока. Один запрос за раз + очистка памяти после него — иначе два поиска
# подряд поднимают бота до лимита хостинга посреди эфира.
YT_HOSTS = ("youtube.com", "youtu.be", "music.youtube.com")
# Куки с хостинга (файл рядом с main.py, в git не попадает) — для возрастных треков
COOKIES_FILE = os.path.join(BASE_DIR, "cookies.txt")
_YT_SEM = None


def _is_yt(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in YT_HOSTS)


def _pick_stream(info: dict):
    """Адрес аудиопотока из кучи формат, которые вернул YouTube.

    Берём https-файл, а не плейлист m3u8: ffmpeg переживает обрывы на файле, а
    с плейлистом встаёт на середине трека.
    """
    cands = []
    if info.get("url"):
        cands.append(info["url"])
    for f in (info.get("requested_formats") or info.get("formats") or []):
        if f.get("url") and (f.get("acodec") or "none") != "none":
            cands.append(f["url"])
    for u in cands:
        if u.startswith("https://") and ".m3u8" not in u:
            return u
    return cands[0] if cands else None


def _resolve_sync(target: str) -> dict:
    """Блокирующий разбор через yt-dlp — его уводит в отдельный поток вызывающий."""
    import yt_dlp

    opts = {
        "format": "bestaudio/best",
        "noplaylist": True, "quiet": True, "no_warnings": True,
        "skip_download": True, "socket_timeout": 15, "cachedir": False,
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }
    if os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(target, download=False)
    if (info or {}).get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise RuntimeError("Плейлист пустой")
        info = entries[0]
    stream = _pick_stream(info)
    if not stream:
        raise RuntimeError("в ответе нет аудиопотока — попробуй другую ссылку")
    return {
        "title": (info.get("title") or target)[:120],
        "stream_url": stream,
        "webpage_url": info.get("webpage_url") or target,
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "ts": time.time(),
    }


async def _resolve_youtube(target: str) -> dict:
    global _YT_SEM
    if _YT_SEM is None:
        _YT_SEM = asyncio.Semaphore(1)
    async with _YT_SEM:
        t0 = time.time()
        try:
            track = await asyncio.to_thread(_resolve_sync, target)
        except ImportError:
            raise RuntimeError("На хосте не стоит yt-dlp — добавь его в requirements.txt "
                               "и перезапусти бота")
        except Exception as e:
            raise RuntimeError(f"YouTube не отдал трек: {str(e)[:160]}")
        finally:
            _trim()
    rss = _rss_mb()
    _log(f"YouTube: «{track['title']}» за {time.time() - t0:.1f} с"
         + (f", память {rss} МБ" if rss > 0 else ""))
    return track


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


# ---------- настройки панели ----------
# Один файл на все серверы: {id: {громкость, кулдаун, очередь, диджей, авто-выход}}.
DEFAULT_SETTINGS = {"volume": 50, "cooldown": PLAY_COOLDOWN, "queue": MAX_QUEUE,
                    "dj": False, "leave": True}
COOLDOWNS = (0, 5, 10, 20, 40, 60)
QUEUES = (10, 25, 40, 60)


def _cfg_raw() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _cfg(guild_id) -> dict:
    """Настройки сервера: значения из файла поверх дефолтов, мусор по пути чиним."""
    saved = _cfg_raw().get(str(guild_id)) or _cfg_raw().get(int(guild_id)) or {}
    out = dict(DEFAULT_SETTINGS)
    if isinstance(saved, dict):
        out.update(saved)
    out["volume"] = max(5, min(100, _int(out["volume"], 50)))
    out["cooldown"] = _int(out["cooldown"], PLAY_COOLDOWN)
    out["queue"] = _int(out["queue"], MAX_QUEUE)
    out["dj"] = bool(out["dj"])
    out["leave"] = bool(out["leave"])
    return out


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _cfg_save(guild_id, patch: dict) -> str:
    """Пишет настройки целиком: обрезанный json при записи по кускам читался бы пустотой."""
    data = _cfg_raw()
    key = str(guild_id)
    merged = dict(DEFAULT_SETTINGS)
    merged.update(data.get(key) if isinstance(data.get(key), dict) else {})
    merged.update(patch)
    data[key] = merged
    try:
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_FILE)
        return ""
    except Exception as e:
        return f"Настройки не записались: `{e}`"


class GuildPlayer:
    def __init__(self, guild_id=0):
        cfg = _cfg(guild_id or 0)
        self.queue: deque = deque()
        self.current = None
        self.source = None
        self.volume = cfg["volume"] / 100.0
        self.text_channel = None
        # 🔁 авто-диджей: когда очередь пустеет, берём случайный трек из библиотеки
        self.dj = cfg["dj"]
        self.dj_recent: deque = deque(maxlen=8)
        self.dj_fails = 0
        # 🗳 голоса за скип текущего трека
        self.votes: set = set()


class _PlaylistSelect(disnake.ui.Select):
    """В disnake колбэк у селекта — метод подкласса, не параметр."""

    def __init__(self, options: list):
        super().__init__(placeholder="Выбрать плейлист…", custom_id="music:pl_pick", options=options)

    async def callback(self, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        await inter.response.defer(ephemeral=True)
        await cog.play_playlist(inter, self.values[0])


class PlaylistPicker(disnake.ui.View):
    """Список плейлистов в ephemeral-сообщении: выбираешь один — он ложится в очередь."""

    def __init__(self, playlists: dict):
        super().__init__(timeout=180)
        options = [disnake.SelectOption(label=name[:100],
                                       description=f"{len(tracks)} треков"[:100],
                                       value=name[:100])
                   for name, tracks in list(playlists.items())[:25]]
        self.add_item(_PlaylistSelect(options))


class PlaylistModal(disnake.ui.Modal):
    """Имя для плейлиста, который соберётся из текущей очереди."""

    def __init__(self, tracks: list):
        self.tracks = tracks
        super().__init__(
            title="Как назвать плейлист?",
            custom_id="music:pl_name",
            components=[
                disnake.ui.TextInput(
                    label="Название",
                    custom_id="name",
                    style=disnake.TextInputStyle.short,
                    placeholder="Например: Вечер на капту",
                    max_length=40,
                )
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        await cog.save_playlist(inter, self.tracks, inter.text_values["name"].strip())


class MusicModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(
            title="🎧 Какую музыку включить?",
            custom_id="music_modal",
            components=[
                disnake.ui.TextInput(
                    label="Имя из библиотеки, ссылка на YouTube или на звук",
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
    """Показывается после «В войс» — одна кнопка, чтобы не искать её по всему каналу."""

    def __init__(self, bot=None):
        super().__init__(timeout=300)

    @disnake.ui.button(label="Заказать", style=disnake.ButtonStyle.primary,
                       custom_id="music:say")
    async def pick(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.send_modal(MusicModal())


def _e(bot, name: str, fallback: str) -> str:
    """Эмодзи семьи из бренд-пака; unicode-заглушка — пока пак на сервере не поднят."""
    return brand.emoji(bot, name, fallback)


def _ok(bot, head: str, text: str, guild=None) -> disnake.Embed:
    like = _e(bot, "hz_likeicon", "✅")
    return note_embed(text, "success", title=f"{like}  {head}", guild=guild)


def _err(bot, head: str, text: str, guild=None) -> disnake.Embed:
    warn = _e(bot, "hz_warn", "⚠️")
    return note_embed(text, "error", title=f"{warn}  {head}", guild=guild)


def _cfg_text(cfg: dict) -> dict:
    """Человеческие значения настроек: на экране то, что человек и написал бы словами."""
    cd = cfg["cooldown"]
    return {
        "volume": f"{cfg['volume']}%",
        "cooldown": "выкл" if cd <= 0 else f"{cd} с",
        "queue": f"{cfg['queue']} треков",
        "dj": "включён" if cfg["dj"] else "выключен",
        "leave": "через 60 с" if cfg["leave"] else "не выходит",
    }


def _cycle(values, current):
    """Следующее значение из списка: настройки шагают кнопкой, без модалок и опечаток."""
    try:
        i = values.index(current)
    except ValueError:
        i = 0
    return values[(i + 1) % len(values)]


PANEL_BANNER = os.path.join(BASE_DIR, "assets", "media_banner.jpg")


def _panel_banner(embed: disnake.Embed):
    """Баннер под панелью — как у казны: ссылка на CDN, а если её нет — вложение."""
    if not os.path.isfile(PANEL_BANNER):
        return None
    url = cdn_assets.cached_url("media_banner.jpg", PANEL_BANNER)
    if url:
        embed.set_image(url=url)
        return None
    embed.set_image(url="attachment://media_banner.jpg")
    return disnake.File(PANEL_BANNER, filename="media_banner.jpg")


class MusicPanelView(disnake.ui.View):
    """Постоянная панель в канале музыки: custom_id держат кнопки живыми после рестарта.

    Кнопки без эмодзи и почти все одним стилем: ряд = одна тема, цвет только у того,
    что реально выделяется (заказ и выход).
    """

    def __init__(self, bot=None):
        super().__init__(timeout=None)

    # ---------- ряд 1: заказ ----------
    @disnake.ui.button(label="Заказать", style=disnake.ButtonStyle.primary,
                       custom_id="music:play", row=0)
    async def play(self, button, inter: disnake.MessageInteraction):
        if not inter.author.voice or not inter.author.voice.channel:
            return await inter.response.send_message(
                embed=_err(inter.bot, "СНАЧАЛА В ГОЛОС",
                           f"{_e(inter.bot, 'hz_megaphone', '📢')}  Зайди в голосовой канал — "
                           "бот играет там, где сидят люди.\n\n"
                           f"> {_e(inter.bot, 'hz_pcicon', '🎧')}  Дальше эта же кнопка "
                           "откроет форму заказа."),
                ephemeral=True)
        await inter.response.send_modal(MusicModal())

    @disnake.ui.button(label="В войс", style=disnake.ButtonStyle.secondary,
                       custom_id="music:join", row=0)
    async def join(self, button, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        await inter.response.defer(ephemeral=True)
        vc = await cog.ensure_voice(inter)
        if not vc:
            return
        cog.get_player(inter.guild).text_channel = inter.channel
        pc = _e(inter.bot, "hz_pcicon", "🎧")
        meg = _e(inter.bot, "hz_megaphone", "📢")
        await inter.edit_original_response(
            embed=_ok(inter.bot, "БОТ В ГОЛОСЕ",
                      f"### {pc}  {vc.channel.mention}\n\n"
                      f"{meg}  Жму «Заказать» — или напиши имя трека в канале"),
            view=TalkView(inter.bot))

    @disnake.ui.button(label="Выйти", style=disnake.ButtonStyle.danger,
                       custom_id="music:leave", row=0)
    async def leave(self, button, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        vc = await cog.check_control(inter)
        if not vc:
            return
        await cog.cleanup(inter.guild)
        await inter.response.send_message(
            embed=_ok(inter.bot, "ОТКЛЮЧИЛСЯ",
                      f"{_e(inter.bot, 'hz_pcicon', '🎧')}  Голос выключен, очередь забыта."),
            ephemeral=True)

    # ---------- ряд 2: что играет ----------
    @disnake.ui.button(label="Пауза", style=disnake.ButtonStyle.secondary,
                       custom_id="music:pause", row=1)
    async def pause(self, button, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        vc = await cog.check_control(inter)
        if not vc:
            return
        p = cog.get_player(inter.guild)
        title = (p.current or {}).get("title", "трек")
        if vc.is_paused():
            vc.resume()
            emb = _ok(inter.bot, "ИГРАЕТ ДАЛЬШЕ", f"▶️  **{title}**")
        elif vc.is_playing():
            vc.pause()
            emb = _ok(inter.bot, "ПАУЗА",
                      f"⏸  **{title}**\n\n"
                      f"{_e(inter.bot, 'hz_trainicon', '📜')}  Это же нажатие — продолжить.")
        else:
            emb = _err(inter.bot, "ТИШИНА",
                       f"{_e(inter.bot, 'hz_pcicon', '🎧')}  Сейчас ничего не играет — "
                       "закажи трек кнопкой «Заказать».")
        await inter.response.send_message(embed=emb, ephemeral=True)

    @disnake.ui.button(label="Скип", style=disnake.ButtonStyle.secondary,
                       custom_id="music:skip", row=1)
    async def skip(self, button, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        vc = await cog.check_control(inter)
        if not vc:
            return
        p = cog.get_player(inter.guild)
        if vc.is_playing() or vc.is_paused():
            title = (p.current or {}).get("title", "трек")
            p.votes.clear()
            vc.stop()
            emb = _ok(inter.bot, "ПРОПУСТИЛ",
                      f"⏭  **{title}**\n\n"
                      f"{_e(inter.bot, 'hz_trainicon', '📜')}  Очередь: "
                      f"{len(p.queue)} · дальше следующий трек.")
        else:
            emb = _err(inter.bot, "НЕЧЕГО ПРОПУСКАТЬ", "⏭  Сейчас ничего не играет.")
        await inter.response.send_message(embed=emb, ephemeral=True)

    @disnake.ui.button(label="Стоп", style=disnake.ButtonStyle.secondary,
                       custom_id="music:stop", row=1)
    async def stop(self, button, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        vc = await cog.check_control(inter)
        if not vc:
            return
        p = cog.get_player(inter.guild)
        p.queue.clear()
        p.dj = False
        p.votes.clear()
        if vc.is_playing() or vc.is_paused():
            vc.stop()
        await inter.response.send_message(
            embed=_ok(inter.bot, "СТОП",
                      f"⏹  Очередь очищена\n\n"
                      f"{_e(inter.bot, 'pinkghost', '🔁')}  Авто-диджей выключен, "
                      "иначе он бы сразу подставил случайный трек."),
            ephemeral=True)

    @disnake.ui.button(label="Очередь", style=disnake.ButtonStyle.secondary,
                       custom_id="music:queue", row=1)
    async def queue(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("Music").show_queue(inter)

    @disnake.ui.button(label="Скип голосом", style=disnake.ButtonStyle.secondary,
                       custom_id="music:vote", row=1)
    async def vote(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("Music").vote_skip(inter)

    # ---------- ряд 3: громкость, диджей, настройки ----------
    @disnake.ui.button(label="Тише", style=disnake.ButtonStyle.secondary,
                       custom_id="music:vol_down", row=2)
    async def vol_down(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("Music").change_volume(inter, -0.1)

    @disnake.ui.button(label="Громче", style=disnake.ButtonStyle.secondary,
                       custom_id="music:vol_up", row=2)
    async def vol_up(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("Music").change_volume(inter, +0.1)

    @disnake.ui.button(label="Диджей", style=disnake.ButtonStyle.secondary,
                       custom_id="music:dj", row=2)
    async def dj(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("Music").toggle_dj(inter)

    @disnake.ui.button(label="Настройки", style=disnake.ButtonStyle.secondary,
                       custom_id="music:cfg", row=2)
    async def cfg(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("Music").show_settings(inter)

    # ---------- ряд 4: библиотека и плейлисты ----------
    @disnake.ui.button(label="Закинуть", style=disnake.ButtonStyle.secondary,
                       custom_id="music:add", row=3)
    async def add(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("Music").ask_file(inter)

    @disnake.ui.button(label="Треки", style=disnake.ButtonStyle.secondary,
                       custom_id="music:library", row=3)
    async def library(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("Music").show_library(inter)

    @disnake.ui.button(label="Плейлисты", style=disnake.ButtonStyle.secondary,
                       custom_id="music:pl", row=3)
    async def playlists(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("Music").show_playlists(inter)

    @disnake.ui.button(label="В плейлист", style=disnake.ButtonStyle.secondary,
                       custom_id="music:pl_save", row=3)
    async def pl_save(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("Music").start_playlist_save(inter)


class MusicSettingsView(disnake.ui.View):
    """Экран настроек: каждая кнопка шагает значение и сразу перерисовывает экран."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=600)
        self.guild_id = int(guild_id)

    def embed(self, bot, guild=None) -> disnake.Embed:
        cfg = _cfg(self.guild_id)
        t = _cfg_text(cfg)
        pc = _e(bot, "hz_pcicon", "🎧")
        ghost = _e(bot, "pinkghost", "🔁")
        meg = _e(bot, "hz_megaphone", "📢")
        train = _e(bot, "hz_trainicon", "📜")
        book = _e(bot, "hz_book", "📖")
        bot_i = _e(bot, "hz_botv2", "🤖")
        crown = _e(bot, "red_crown", "👑")
        p = None
        cog = bot.get_cog("Music") if bot else None
        if cog is not None and guild is not None:
            p = cog.players.get(int(guild.id))
        live = (f"**{int(round(p.volume * 100))}%**  сейчас в голосе" if p else
                f"**{t['volume']}**  для новых заказов")
        sections = [
            (f"{pc}  ГРОМКОСТЬ",
             f"{live}\n{meg}  Шаг по 5% — «Тише» и «Громче» на панели крутят этот же звук"),
            (f"{train}  ОЧЕРЕДЬ", f"**{t['queue']}**\n{book}  Сколько треков ждут своей очереди"),
            (f"{meg}  КУЛДАУН ЗАКАЗА",
             f"**{t['cooldown']}**\n{bot_i}  Чтобы один человек не перекрывал всех подряд"),
            (f"{ghost}  АВТО-ДИДЖЕЙ",
             f"**{t['dj']}**\n{pc}  Кончится очередь — возьмёт случайный трек из библиотеки"),
            ("🚪  АВТО-ВЫХОД",
             f"**{t['leave']}**\n{book}  Если в канале не осталось никого, кроме бота"),
        ]
        return panel_embed(
            f"{pc}  НАСТРОЙКИ МУЗЫКИ", sections, guild=guild,
            intro=(f"*{crown}  Один голос на всю семью  {crown}*\n"
                   f"{bot_i}  Значения помнятся после рестарта бота."))

    async def _apply(self, inter, patch: dict) -> None:
        err = _cfg_save(self.guild_id, patch)
        if err:
            return await inter.response.send_message(
                embed=_err(inter.bot, "НЕ СОХРАНИЛОСЬ", err), ephemeral=True)
        cfg = _cfg(self.guild_id)
        cog = inter.bot.get_cog("Music")
        p = cog.players.get(self.guild_id) if cog else None
        if p is not None:
            if "volume" in patch:
                p.volume = cfg["volume"] / 100.0
                if p.source:
                    p.source.volume = p.volume
            if "dj" in patch:
                p.dj = cfg["dj"]
        await inter.response.edit_message(embed=self.embed(inter.bot, inter.guild), view=self)

    @disnake.ui.button(label="Тише", style=disnake.ButtonStyle.secondary,
                       custom_id="music:cfg:vol_down", row=0)
    async def v_down(self, button, inter: disnake.MessageInteraction):
        await self._apply(inter, {"volume": max(5, _cfg(self.guild_id)["volume"] - 5)})

    @disnake.ui.button(label="Громче", style=disnake.ButtonStyle.secondary,
                       custom_id="music:cfg:vol_up", row=0)
    async def v_up(self, button, inter: disnake.MessageInteraction):
        await self._apply(inter, {"volume": min(100, _cfg(self.guild_id)["volume"] + 5)})

    @disnake.ui.button(label="Очередь", style=disnake.ButtonStyle.secondary,
                       custom_id="music:cfg:queue", row=0)
    async def q(self, button, inter: disnake.MessageInteraction):
        await self._apply(inter, {"queue": _cycle(QUEUES, _cfg(self.guild_id)["queue"])})

    @disnake.ui.button(label="Кулдаун", style=disnake.ButtonStyle.secondary,
                       custom_id="music:cfg:cd", row=1)
    async def cd(self, button, inter: disnake.MessageInteraction):
        await self._apply(inter, {"cooldown": _cycle(COOLDOWNS, _cfg(self.guild_id)["cooldown"])})

    @disnake.ui.button(label="Диджей", style=disnake.ButtonStyle.secondary,
                       custom_id="music:cfg:dj", row=1)
    async def dj_def(self, button, inter: disnake.MessageInteraction):
        await self._apply(inter, {"dj": not _cfg(self.guild_id)["dj"]})

    @disnake.ui.button(label="Авто-выход", style=disnake.ButtonStyle.secondary,
                       custom_id="music:cfg:leave", row=1)
    async def leave_def(self, button, inter: disnake.MessageInteraction):
        await self._apply(inter, {"leave": not _cfg(self.guild_id)["leave"]})


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.players: dict = {}
        self._last_play: dict = {}
        # кто нажал «Закинуть»: id → (канал, когда)
        self._pending: dict = {}

    @commands.Cog.listener()
    async def on_ready(self):
        # persistent view: кнопки панели работают и после перезапуска бота
        if not getattr(self.bot, "_music_view_added", False):
            self.bot.add_view(MusicPanelView(self.bot))
            self.bot._music_view_added = True
            files = _library()
            _log(f"библиотека: {len(files)} треков, ищем в {MEDIA_DIR} и {BASE_DIR}")
            if not files:
                _log("медиатека пуста: положи mp3 во вкладку Files или кнопкой «Закинуть»")
            _log(f"ffmpeg: {_ffmpeg_exe() or 'НЕ НАЙДЕН'}")

    # ---------- библиотека ----------
    ASK_SECONDS = 90

    async def ask_file(self, inter):
        """Кнопка «Закинуть»: ждём вложения следующим сообщением в этом же канале."""
        self._pending[inter.author.id] = (inter.channel.id, time.time())
        gift = _e(self.bot, "hz_gifticon", "📥")
        pc = _e(self.bot, "hz_pcicon", "🎧")
        await inter.response.send_message(
            embed=_ok(self.bot, "ЖДУ ФАЙЛ",
                      f"### {gift}  Прикрепи mp3 к сообщению здесь\n\n"
                      f"{pc}  Можно до {MAX_FILES_IN_MSG} треков сразу, файл до {MAX_TRACK_MB} МБ\n"
                      f"⏳  Жду {self.ASK_SECONDS} сек"),
            ephemeral=True)

    async def show_library(self, inter):
        files = _library()
        emb = list_embed(
            f"{_e(self.bot, 'hz_pcicon', '🎧')}  БИБЛИОТЕКА СЕМЬИ",
            [[f"`{i + 1}.` {_stem(f)}", os.path.relpath(f, BASE_DIR)[:100]]
             for i, f in enumerate(files[:25])],
            guild=inter.guild, inline=False,
            empty=(f"{_e(self.bot, 'hz_pcicon', '🎧')}  Пусто: ни в `media/`, ни рядом с `main.py` "
                   "звуков нет.\n"
                   f"{_e(self.bot, 'hz_gifticon', '📥')}  Кнопка «Закинуть» принимает mp3 из сообщения, "
                   "а вкладка Files на хостинге — файлы целиком."),
            note=(f"{_e(self.bot, 'hz_trainicon', '📜')}  Всего {len(files)} треков · "
                  f"{_e(self.bot, 'hz_megaphone', '📢')}  Заказывай по имени трека"))
        await inter.response.send_message(embed=emb, ephemeral=True)

    async def show_queue(self, inter):
        p = self.get_player(inter.guild)
        train = _e(self.bot, "hz_trainicon", "📜")
        ghost = _e(self.bot, "pinkghost", "🔁")
        rows = []
        if p.current:
            rows.append([f"▶️  {p.current['title']}",
                         f"{_fmt_duration(p.current['duration'])} · "
                         f"{p.current.get('requester', '—')}"])
        for i, t in enumerate(list(p.queue)[:12], 1):
            rows.append([f"{i}  {t['title']}",
                         f"{_fmt_duration(t['duration'])} · {t.get('requester', '—')}"])
        emb = list_embed(f"{train}  ОЧЕРЕДЬ", rows, guild=inter.guild, inline=False,
                         empty=f"{train}  Очередь пуста — начни с кнопки «Заказать».",
                         note=(f"{ghost}  Авто-диджей: {'включён' if p.dj else 'выключен'} · "
                               f"лимит {_cfg(getattr(inter.guild, 'id', 0))['queue']} треков"))
        await inter.response.send_message(embed=emb, ephemeral=True)

    async def show_settings(self, inter):
        if not is_admin(inter):
            return await inter.response.send_message(
                embed=_err(self.bot, "НЕ ДЛЯ ВСЕХ",
                           f"{_e(self.bot, 'red_crown', '👑')}  Настройки музыки крутит "
                           "руководство семьи — у тебя не тех прав."),
                ephemeral=True)
        view = MusicSettingsView(inter.guild.id)
        await inter.response.send_message(embed=view.embed(self.bot, inter.guild),
                                          view=view, ephemeral=True)

    # ---------- плейлисты ----------
    async def show_playlists(self, inter):
        data = _playlists()
        if not data:
            return await inter.response.send_message(
                embed=_err(self.bot, "ПЛЕЙЛИСТОВ НЕТ",
                           f"{_e(self.bot, 'hz_merchicon', '📌')}  Собери очередь кнопкой «Заказать» "
                           "и нажми «В плейлист» — сохраню под именем."),
                ephemeral=True)
        book = _e(self.bot, "hz_book", "🎼")
        emb = list_embed(
            f"{book}  ПЛЕЙЛИСТЫ СЕМЬИ",
            [[f"{i}  {name}", f"{len(tracks)} треков"]
             for i, (name, tracks) in enumerate(list(data.items())[:12], 1)],
            guild=inter.guild, inline=False,
            note=(f"{_e(self.bot, 'hz_trainicon', '📜')}  {len(data)} плейлистов · "
                  f"{_e(self.bot, 'hz_megaphone', '📢')}  выбери один в списке ниже"),
        )
        await inter.response.send_message(embed=emb, view=PlaylistPicker(data), ephemeral=True)

    async def start_playlist_save(self, inter):
        p = self.get_player(inter.guild)
        names = [t["title"] for t in list(p.queue)]
        if p.current:
            names.insert(0, p.current["title"])
        if not names:
            return await inter.response.send_message(
                embed=_err(self.bot, "ПУСТО",
                           f"{_e(self.bot, 'hz_merchicon', '📌')}  Сохранять нечего: "
                           "сначала закажи треки кнопкой «Заказать»."),
                ephemeral=True)
        await inter.response.send_modal(PlaylistModal(names[:MAX_IN_PLAYLIST]))

    async def save_playlist(self, inter, tracks: list, name: str):
        name = _pl_clean(name)
        if not name:
            return await inter.response.send_message(
                embed=_err(self.bot, "НУЖНО ИМЯ",
                           "📌  Например «Вечер на капту» — по имени плейлист и найдётся."),
                ephemeral=True)
        data = _playlists()
        if name not in data and len(data) >= MAX_PLAYLISTS:
            return await inter.response.send_message(
                embed=_err(self.bot, "МЕСТА НЕТ",
                           f"📌  Больше {MAX_PLAYLISTS} плейлистов не держу — удали старый "
                           "(файл `media/playlists.json`)."),
                ephemeral=True)
        existed = name in data
        data[name] = tracks
        err = _playlists_write(data)
        if err:
            return await inter.response.send_message(
                embed=_err(self.bot, "НЕ ЗАПИСАЛОСЬ", err), ephemeral=True)
        have = {_stem(f).lower() for f in _library()}
        missing = [t for t in tracks if str(t).lower() not in have]
        pin = _e(self.bot, "hz_merchicon", "📌")
        warn = _e(self.bot, "hz_warn", "⚠️")
        answer = (f"### {pin}  {'Обновлён' if existed else 'Сохранён'}: **{name}**\n\n"
                  f"{_e(self.bot, 'hz_trainicon', '📜')}  {len(tracks)} треков в плейлисте")
        if missing:
            answer += f"\n{warn}  Нет в библиотеке: {', '.join(missing[:5])}"
        await inter.response.send_message(
            embed=_ok(self.bot, "ПЛЕЙЛИСТ", answer) if not missing
            else note_embed(answer, "warning", title=f"{warn}  ПЛЕЙЛИСТ", guild=inter.guild),
            ephemeral=True)

    async def play_playlist(self, inter, name: str):
        tracks = _playlists().get(str(name or "").strip())
        if not tracks:
            return await inter.followup.send(
                embed=_err(self.bot, "ПЛЕЙЛИСТ ПРОПАЛ",
                           f"📌  «{name}» больше нет — треки могли убрать из библиотеки."),
                ephemeral=True)
        vc = await self.ensure_voice(inter)
        if not vc:
            return
        p = self.get_player(inter.guild)
        limit = _cfg(inter.guild.id)["queue"]
        room = limit - len(p.queue)
        if room <= 0:
            return await inter.followup.send(
                embed=_err(self.bot, "ОЧЕРЕДЬ ПОЛНАЯ",
                           f"{_e(self.bot, 'hz_trainicon', '📜')}  Стоит {limit} треков — "
                           "почисти кнопкой «Стоп»."),
                ephemeral=True)
        added, missing = [], []
        for stem in tracks[:room]:
            path = _stem_to_path(stem)
            if path is None:
                missing.append(stem)
                continue
            p.queue.append({"title": stem[:120], "stream_url": path, "webpage_url": stem,
                            "duration": None, "thumbnail": None, "ts": time.time(),
                            "requester": f"📌 {name}"})
            added.append(stem)
        if not added:
            return await inter.followup.send(
                embed=_err(self.bot, "НИ ОДНОГО ТРЕКА",
                           f"{_e(self.bot, 'hz_pcicon', '🎧')}  Из плейлиста «{name}» в библиотеке "
                           "ничего не осталось."),
                ephemeral=True)
        p.text_channel = inter.channel
        warn = _e(self.bot, "hz_warn", "⚠️")
        answer = (f"### 📌  {name}\n\n"
                  f"{_e(self.bot, 'hz_trainicon', '📜')}  {len(added)} треков встало в очередь")
        if missing:
            answer += f"\n{warn}  Нет в библиотеке: {', '.join(missing[:5])}"
        await inter.followup.send(embed=_ok(self.bot, "ПЛЕЙЛИСТ ВКЛЮЧЁН", answer), ephemeral=True)
        if not (vc.is_playing() or vc.is_paused()):
            await self._play_next(inter.guild)

    # ---------- авто-диджей и голосовой скип ----------
    async def toggle_dj(self, inter):
        vc = await self.check_control(inter)
        if not vc:
            return
        p = self.get_player(inter.guild)
        p.dj = not p.dj
        p.dj_fails = 0
        ghost = _e(self.bot, "pinkghost", "🔁")
        pc = _e(self.bot, "hz_pcicon", "🎧")
        if p.dj:
            if not _library():
                p.dj = False
                emb = _err(self.bot, "БИБЛИОТЕКА ПУСТА",
                           f"{pc}  Включать нечего — закинь треки кнопкой «Закинуть».")
            else:
                emb = _ok(self.bot, "ДИДЖЕЙ ВКЛЮЧЁН",
                          f"### {ghost}  Сам берёт случайный трек\n\n"
                          f"{pc}  В библиотеке {len(_library())} треков · "
                          "последние 8 не повторяет")
        else:
            emb = _ok(self.bot, "ДИДЖЕЙ ВЫКЛЮЧЕН",
                      f"{ghost}  После последнего трека — тишина.")
        await inter.response.send_message(embed=emb, ephemeral=True)
        if p.dj and not (vc.is_playing() or vc.is_paused()):
            await self._play_next(inter.guild)

    def _dj_track(self, p: GuildPlayer):
        """Случайный трек, которого не было в последних восьми."""
        files = [f for f in _library() if f not in p.dj_recent]
        if not files:
            files = _library()
        if not files:
            return None
        picked = random.choice(files)
        p.dj_recent.append(picked)
        return {"title": _stem(picked)[:120], "stream_url": picked, "webpage_url": _stem(picked),
                "duration": None, "thumbnail": None, "ts": time.time(),
                "requester": f"{_e(self.bot, 'pinkghost', '🔁')} авто-диджей"}

    async def vote_skip(self, inter):
        vc = await self.check_control(inter)
        if not vc:
            return
        if not (vc.is_playing() or vc.is_paused()):
            return await inter.response.send_message(
                embed=_err(self.bot, "ТИШИНА", "⏭  Сейчас ничего не играет — голос не нужен."),
                ephemeral=True)
        p = self.get_player(inter.guild)
        listeners = [m for m in vc.channel.members if not m.bot]
        need = max(2, (len(listeners) + 1) // 2)
        like = _e(self.bot, "hz_likeicon", "🗳")
        if inter.author.id in p.votes:
            p.votes.discard(inter.author.id)
            return await inter.response.send_message(
                embed=_ok(self.bot, "ГОЛОС УБРАН",
                          f"{like}  За скип: **{len(p.votes)}/{need}**"), ephemeral=True)
        p.votes.add(inter.author.id)
        if len(p.votes) >= need:
            p.votes.clear()
            title = (p.current or {}).get("title", "трек")
            vc.stop()
            return await inter.response.send_message(
                embed=_ok(self.bot, "ПРОПУСТИЛИ",
                          f"### {like}  **{title}**\n\n"
                          f"⏭  {need} из {len(listeners)} голосов — "
                          "большинство решило"),
                ephemeral=True)
        await inter.response.send_message(
            embed=_ok(self.bot, "ГОЛОС ПРИНЯТ",
                      f"{like}  За скип: **{len(p.votes)}/{need}**\n\n"
                      f"⏭  В канале {len(listeners)} слушают — нужно большинство."),
            ephemeral=True)

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
        saved, bad = [], []
        for att in message.attachments[:MAX_FILES_IN_MSG]:
            try:
                saved.append(_stem(await _save_track(att)))
            except Exception as e:
                bad.append(str(e))
        if not saved and not bad:
            return
        _trim()
        pc = _e(self.bot, "hz_pcicon", "🎧")
        warn = _e(self.bot, "hz_warn", "⚠️")
        if saved:
            lines = "\n".join(f"{pc}  **{s}**" for s in saved[:6])
            extra = f"\n{warn}  " + " · ".join(bad[:2]) if bad else ""
            emb = _ok(self.bot, f"В БИБЛИОТЕКЕ {len(saved)}",
                      f"### {lines}{extra}\n\n"
                      f"{_e(self.bot, 'hz_megaphone', '📢')}  Стоя в голосе, жми «Заказать» "
                      "и назови имя трека")
        else:
            emb = _err(self.bot, "НЕ ЗАБРАЛ", "\n".join(f"{warn}  {b}" for b in bad[:3]))
        try:
            await message.reply(embed=emb, mention_author=False)
        except Exception:
            pass

    # ---------- helpers ----------
    def get_player(self, guild: disnake.Guild) -> GuildPlayer:
        if guild.id not in self.players:
            self.players[guild.id] = GuildPlayer(guild.id)
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

    async def _say(self, inter, emb):
        """Ответ туда, где его ещё можно отправить: модалка уже defer'нута — идёт followup.

        Без этого шага любой отказ после «Отложенный ответ» падал бы с
        InteractionResponded и человек не получал бы ничего.
        """
        if inter.response.is_done():
            await inter.followup.send(embed=emb, ephemeral=True)
        else:
            await inter.response.send_message(embed=emb, ephemeral=True)

    async def ensure_voice(self, inter):
        """Заходит в канал пользователя (или переезжает). Возвращает VoiceClient."""
        voice = inter.author.voice
        if not voice or not voice.channel:
            await self._say(inter, _err(
                self.bot, "СНАЧАЛА В ГОЛОС",
                f"{_e(self.bot, 'hz_megaphone', '📢')}  Зайди в голосовой канал — "
                "я играю там, где сидят люди."))
            return None
        vc = inter.guild.voice_client
        try:
            if vc is None:
                vc = await voice.channel.connect(timeout=20)
            elif vc.channel.id != voice.channel.id:
                if vc.is_playing() or vc.is_paused():
                    await self._say(inter, _err(
                        self.bot, "УЖЕ ИГРАЮ",
                        f"{_e(self.bot, 'hz_trainicon', '📜')}  Занят канал "
                        f"**{vc.channel.name}** — зайди туда."))
                    return None
                await vc.move_to(voice.channel)
        except Exception as e:
            await self._say(inter, _err(
                self.bot, "НЕ ЗАШЁЛ В ВОЙС",
                f"`{str(e)[:200]}`\n\n"
                f"{_e(self.bot, 'hz_moderator', '🛡')}  Проверь права бота "
                "(Connect / Speak) и что стоит PyNaCl."))
            return None
        return vc

    async def check_control(self, inter):
        vc = inter.guild.voice_client
        if not vc or not vc.is_connected():
            await self._say(inter, _err(
                self.bot, "БЕЗ ГОЛОСА",
                f"{_e(self.bot, 'hz_pcicon', '🎧')}  Бот сейчас ни в каком канале не сидит — "
                "сначала «В войс»."))
            return None
        voice = inter.author.voice
        if not voice or voice.channel.id != vc.channel.id:
            await self._say(inter, _err(
                self.bot, "ДРУГОЙ КАНАЛ",
                f"{_e(self.bot, 'hz_trainicon', '📜')}  Музыка общая: заходи в "
                f"**{vc.channel.name}**, там и решаем."))
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
        pct = int(round(p.volume * 100))
        await inter.response.send_message(
            embed=_ok(self.bot, "ГРОМКОСТЬ",
                      f"### 🔊  {pct}%\n\n"
                      f"{_e(self.bot, 'hz_botv2', '⚙️')}  «Настройки» хранят это "
                      "как значение по умолчанию."),
            ephemeral=True)

    async def _extract(self, query: str) -> dict:
        """Находит трек: YouTube-ссылка разбор через парсер, прямая ссылка на звук — как есть,
        остальное ищем в библиотеке, а если имени там нет — пробуем найти на YouTube.
        """
        query = query.strip()
        if not query:
            raise RuntimeError("Пустой запрос")
        if query.lower().startswith(("http://", "https://")):
            if _is_yt(query):
                return await _resolve_youtube(query)
            return self._from_link(query)
        try:
            path = self._find_track(query)
        except RuntimeError:
            return await _resolve_youtube("ytsearch1:" + query)
        return {
            "title": _stem(path)[:120],
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
            raise RuntimeError(f"Библиотека пуста — `{MEDIA_DIR}` и корень приложения без звуков. "
                               "Кнопка «Закинуть» принимает mp3 из сообщения.")
        want = re.sub(r"\s+", " ", query.lower()).strip()
        stems = {_stem(f).lower(): f for f in files}
        hit = stems.get(want)
        if hit is None:
            hits = sorted(f for s, f in stems.items() if want in s)
            if not hits:
                raise RuntimeError(f"Нет трека «{query}» (всего {len(files)}, "
                                   "список — кнопка «Треки»)")
            if len(hits) > 1:
                raise RuntimeError("Похоже сразу на несколько: "
                                   + ", ".join(_stem(h) for h in hits[:6]))
            hit = hits[0]
        return hit

    # ---------- core ----------
    async def handle_play(self, inter, query: str):
        """inter уже должен быть defer'нут."""
        if not query:
            return await inter.followup.send(
                embed=_err(self.bot, "ПУСТО", "🎵  Напиши имя трека из библиотеки."),
                ephemeral=True)
        cfg = _cfg(inter.guild.id)
        # кулдаун: не чаще одного заказа в N секунд на человека
        now = time.time()
        wait = cfg["cooldown"] - (now - self._last_play.get(inter.author.id, 0))
        if wait > 0:
            return await inter.followup.send(
                embed=_err(self.bot, "НЕ ТАК БЫСТРО",
                           f"⏳  Следующий заказ через **{int(wait) + 1} сек** — "
                           f"кулдаун крутится в «Настройках»."),
                ephemeral=True)
        self._last_play[inter.author.id] = now
        p = self.get_player(inter.guild)
        if len(p.queue) >= cfg["queue"]:
            return await inter.followup.send(
                embed=_err(self.bot, "ОЧЕРЕДЬ ЗАБИТА",
                           f"{_e(self.bot, 'hz_trainicon', '📜')}  Стоит {cfg['queue']} треков — "
                           "почисти кнопкой «Стоп»."),
                ephemeral=True)
        vc = await self.ensure_voice(inter)
        if not vc:
            return
        p.text_channel = inter.channel
        try:
            track = await self._extract(query)
        except Exception as e:
            return await inter.followup.send(
                embed=_err(self.bot, "НЕ НАШЁЛ",
                           f"{_e(self.bot, 'hz_pcicon', '🎧')}  {e}"),
                ephemeral=True)
        track["requester"] = inter.author.mention
        p.queue.append(track)

        if vc.is_playing() or vc.is_paused():
            emb = _ok(self.bot, "В ОЧЕРЕДИ",
                      f"### {_e(self.bot, 'hz_trainicon', '📜')}  {len(p.queue)} треков ждёт\n\n"
                      f"🎵  **{track['title']}**")
        else:
            emb = _ok(self.bot, "ВКЛЮЧАЮ",
                      f"### 🎵  **{track['title']}**\n\n"
                      f"{_e(self.bot, 'hz_megaphone', '📢')}  Заказ от {inter.author.mention}")
        await inter.followup.send(embed=emb, ephemeral=True)
        await self._play_next(inter.guild)

    def _make_source(self, track: dict, volume: float):
        """Источник ffmpeg + файл, куда он пишет свои ошибки.

        stderr в PIPE без читателя опасен: буфер забивается и ffmpeg встаёт посреди
        трека, поэтому пишем во временный файл и читаем хвост только при ошибке.
        """
        err = tempfile.TemporaryFile()
        try:
            src = disnake.PCMVolumeTransformer(
                disnake.FFmpegPCMAudio(track["stream_url"], executable=_ffmpeg_exe() or "ffmpeg",
                                       before_options=_before_options(track["stream_url"]),
                                       options=FFMPEG_OPTS, stderr=err),
                volume=volume,
            )
        except Exception:
            err.close()
            raise
        return src, err

    @staticmethod
    def _stderr_tail(fh) -> str:
        """Последние строки stderr: по ним видно, почему ffmpeg отказался открывать файл."""
        try:
            fh.seek(0)
            text = fh.read()[-700:].decode("utf-8", "replace")
        except Exception:
            return ""
        return " · ".join(line.strip() for line in text.splitlines() if line.strip())[:400]

    async def _play_failed(self, p: GuildPlayer, track: dict, why: str) -> None:
        ch = p.text_channel
        if ch is None:
            return
        pc = _e(self.bot, "hz_pcicon", "🎧")
        warn = _e(self.bot, "hz_warn", "⚠️")
        emb = _err(self.bot, "НЕ ЗАПУСТИЛОСЬ",
                   f"### {warn}  **{track.get('title', 'трек')}**\n\n{pc}  {str(why)[:400]}\n\n"
                   f"> {_e(self.bot, 'hz_trainicon', '📜')}  Очередь поедет дальше — "
                   "этот трек пропущен.")
        try:
            await ch.send(embed=emb)
        except Exception:
            pass

    async def _play_next(self, guild: disnake.Guild):
        p = self.players.get(guild.id)
        vc = guild.voice_client
        if not p or not vc or not vc.is_connected():
            return
        if vc.is_playing() or vc.is_paused():
            return
        if not p.queue:
            if p.dj:
                picked = self._dj_track(p)
                if picked is None:
                    p.dj = False
                    if p.text_channel:
                        await p.text_channel.send("🔁 Библиотека кончилась — авто-диджей выключен.")
                    return
                p.queue.append(picked)
            else:
                p.current = None
                p.source = None
                return

        track = p.queue.popleft()
        try:
            if track["webpage_url"].startswith("http") and time.time() - track["ts"] > 600:
                # ссылка на чужой стрим могла протухнуть, файл с диска — нет
                fresh = await self._extract(track["webpage_url"])
                track.update(fresh)
            source, err_file = self._make_source(track, p.volume)
        except Exception as e:
            p.dj_fails += 1
            gave_up = p.dj and p.dj_fails >= 4
            if gave_up:
                p.dj = False
            why = (f"`{e}`" if _ffmpeg_exe() else
                   "На хосте нет ffmpeg — добавь `imageio-ffmpeg` в requirements.txt "
                   "и перезапусти бота.")
            await self._play_failed(p, track, why)
            return await self._play_next(guild)

        p.dj_fails = 0
        p.votes.clear()
        p.current = track
        p.source = source

        loop = asyncio.get_running_loop()

        def after(err):
            # ошибка читается из stderr-файла: сам трек мог лежать на диске, а ffmpeg
            # отказаться его открывать — без хвоста в карточке это выглядело бы как тишина
            tail = self._stderr_tail(err_file) if err else ""
            try:
                err_file.close()
            except Exception:
                pass
            if err:
                _log(f"ошибка воспроизведения: {err} | {tail}")
                asyncio.run_coroutine_threadsafe(
                    self._play_failed(p, track, f"`{err}`" + (f"\n{tail}" if tail else "")), loop)
            asyncio.run_coroutine_threadsafe(self._play_next(guild), loop)

        vc.play(source, after=after)

        if p.text_channel:
            link = track["webpage_url"]
            shown = f"[{track['title']}]({link})" if link.startswith("http") else f"**{track['title']}**"
            pc = _e(self.bot, "hz_pcicon", "🎧")
            emb = card(f"{pc}  СЕЙЧАС ИГРАЕТ", guild=guild)
            emb.description = (f"### {shown}\n\n"
                               f"⏱  {_fmt_duration(track['duration'])} · "
                               f"🔊 {int(round(p.volume * 100))}%")
            if track.get("requester"):
                emb.add_field(name="Заказал", value=track["requester"], inline=False)
            if track.get("thumbnail"):
                emb.set_thumbnail(url=track["thumbnail"])
            try:
                await p.text_channel.send(embed=emb)
            except Exception:
                pass

    # ---------- команды ----------
    @commands.slash_command(name="music_panel", description="Отправить панель музыки в этот канал",
                            default_member_permissions=disnake.Permissions(manage_guild=True))
    async def music_panel(self, inter: disnake.ApplicationCommandInteraction):
        emb = self._panel_embed(inter.guild)
        await inter.channel.send(embed=emb, view=MusicPanelView(self.bot),
                                 file=_panel_banner(emb))
        await inter.response.send_message(
            embed=_ok(self.bot, "ПАНЕЛЬ ЗДЕСЬ",
                      f"{_e(self.bot, 'hz_megaphone', '📢')}  Кнопки живут в канале и после "
                      "рестарта бота."),
            ephemeral=True)

    def _panel_embed(self, guild) -> disnake.Embed:
        pc = _e(self.bot, "hz_pcicon", "🎧")
        meg = _e(self.bot, "hz_megaphone", "📢")
        gift = _e(self.bot, "hz_gifticon", "📥")
        train = _e(self.bot, "hz_trainicon", "📜")
        book = _e(self.bot, "hz_book", "📖")
        ghost = _e(self.bot, "pinkghost", "🔁")
        bot_i = _e(self.bot, "hz_botv2", "🤖")
        crown = _e(self.bot, "red_crown", "👑")
        sections = [
            (f"{meg}  ЗАКАЗ",
             f"{pc}  Зайди в голос и жми «Заказать» — имя из библиотеки семьи или прямая "
             "ссылка на звук"),
            (f"{gift}  БИБЛИОТЕКА",
             f"«Закинуть» принимает mp3 из сообщения (до {MAX_FILES_IN_MSG} за раз, "
             f"до {MAX_TRACK_MB} МБ). Файл, положенный во вкладку Files рядом с `main.py`, "
             "тоже играется сразу"),
            (f"{train}  УПРАВЛЕНИЕ",
             "пауза · скип · стоп · громкость · «Скип голосом» — решает большинство "
             "в канале, а не один нахал"),
            (f"{book}  ПЛЕЙЛИСТЫ",
             f"«В плейлист» сохраняет собранную очередь, «Плейлисты» включает её "
             "одним нажатием"),
            (f"{ghost}  АВТО-ДИДЖЕЙ И НАСТРОЙКИ",
             f"кончилась очередь — диджей сам берёт случайный трек; {bot_i} «Настройки» "
             "хранят громкость, кулдаун, размер очереди и авто-выход"),
        ]
        return panel_embed(
            f"{pc}  МУЗЫКА СЕМЬИ", sections, guild=guild,
            intro=(f"*{crown}  Один голос на всю семью  {crown}*\n"
                   f"{pc}  Имя из библиотеки, ссылка на YouTube или на звук — играет всё."))

    @commands.slash_command(name="play", description="Включить трек: имя из библиотеки, ссылка на YouTube или на звук")
    async def play_cmd(self, inter: disnake.ApplicationCommandInteraction, запрос: str):
        await inter.response.defer(ephemeral=True)
        await self.handle_play(inter, запрос.strip())

    @commands.slash_command(name="music_add", description="Закинуть трек в библиотеку: прикрепи mp3")
    async def add_cmd(self, inter: disnake.ApplicationCommandInteraction, файл: disnake.Attachment):
        await inter.response.defer(ephemeral=True)
        try:
            path = await _save_track(файл)
        except Exception as e:
            return await inter.followup.send(
                embed=_err(self.bot, "НЕ ВЗЯЛ", f"{_e(self.bot, 'hz_warn', '⚠️')}  {e}"),
                ephemeral=True)
        _trim()
        pc = _e(self.bot, "hz_pcicon", "🎧")
        meg = _e(self.bot, "hz_megaphone", "📢")
        await inter.followup.send(
            embed=_ok(self.bot, "В БИБЛИОТЕКЕ",
                      f"### {pc}  {_stem(path)}\n\n"
                      f"{meg}  Стоя в голосе, жми «Заказать» и назови это имя"),
            ephemeral=True)

    @commands.slash_command(name="music_list", description="Что есть в библиотеке семьи")
    async def list_cmd(self, inter: disnake.ApplicationCommandInteraction):
        await self.show_library(inter)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # если бот остался один в канале — выходим (настройка «Авто-выход»)
        vc = member.guild.voice_client
        if vc and vc.channel and len([m for m in vc.channel.members if not m.bot]) == 0:
            await asyncio.sleep(60)
            if not _cfg(member.guild.id)["leave"]:
                return
            vc = member.guild.voice_client
            if vc and vc.channel and len([m for m in vc.channel.members if not m.bot]) == 0:
                await self.cleanup(member.guild)


def setup(bot: commands.Bot):
    bot.add_cog(Music(bot))
