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
import array
import base64
import hashlib
import io
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from collections import deque
from urllib.parse import unquote, urlparse

import disnake
from disnake.ext import commands

from utils import brand, cdn_assets
from utils.checks import get_text, is_admin, load_config
from utils.embeds import bar, card, list_embed, note_embed, panel_embed

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


def _site_url() -> str:
    """Адрес сайта музыки: сначала заданный вручную, потом тот, что бот увидел сам.

    Кнопка «Сайт» открывает адрес сразу, без сообщения-ссылки, поэтому он читается
    при каждом построении панели. Хостинг отдаёт наружу один адрес, и узнать его
    можно только из живых запросов — их запоминает `cogs.music_web`.
    """
    url = (os.getenv("MUSIC_URL") or load_config().get("music_site_url") or "").strip()
    if url:
        return url
    web_cog = sys.modules.get("cogs.music_web")
    return (getattr(web_cog, "public_url", lambda: "")() or "").strip()


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


_LINK_RE = re.compile(r"^https?://\S+$", re.I)


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


def _clamp(value, lo: int, hi: int) -> int:
    """Число в границах. Непонятное значение = ноль, а не минимум: ручка, уехавшая
    в -12 дБ из-за мусора в запросе, звучит как сломанный плеер."""
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        n = 0
    return max(lo, min(hi, n))


# Ручки пульта = настоящие ffmpeg-фильтры. Звук в голосовом один на весь канал,
# поэтому крутит один человек, а слышат все — страница показывает, кто именно.
FX_LIMITS = {"bass": (-12, 12), "mid": (-12, 12), "high": (-12, 12),
            "filter": (0, 3), "echo": (0, 1)}
# Режимы пульта: каждый — настоящие фильтры ffmpeg, а не наклейка. «Чисто» ничего
# не трогает, «Мягко» снимает резкость верхов, «Плотно» и «Бас-буст» поднимают низ.
FILTER_MODES = (
    ("чисто", ()),
    ("мягко", ("lowpass=f=6000",)),
    ("плотно", ("bass=g=6:f=80", "lowpass=f=7500")),
    ("бас-буст", ("bass=g=10:f=110", "treble=g=-3:f=6000")),
)
FILTER_NAMES = tuple(name for name, _ in FILTER_MODES)
REPEATS = ("off", "one", "all")
REPEAT_NAMES = {"off": "повтор выключен", "one": "трек по кругу", "all": "вся очередь по кругу"}


def _fx_clean(fx) -> dict:
    """Положение ручек после проверки: чужой json не должен уронить ffmpeg."""
    fx = fx if isinstance(fx, dict) else {}
    out = {}
    for key, (lo, hi) in FX_LIMITS.items():
        value = fx.get(key)
        out[key] = _clamp(0 if value is None else value, lo, hi)
    try:
        speed = float(fx.get("speed", 1.0))
    except (TypeError, ValueError):
        speed = 1.0
    out["speed"] = round(max(0.7, min(1.5, speed)), 2)
    return out


def _fx_chain(fx: dict) -> str:
    """Цепочка -af по ручкам. Пустая строка — звук ровно как в файле."""
    parts = []
    for key, tmpl in (("bass", "bass=g=%d:f=110"),
                      ("mid", "equalizer=f=1000:t=q:w=1.2:g=%d"),
                      ("high", "treble=g=%d:f=4000")):
        if fx.get(key):
            parts.append(tmpl % fx[key])
    mode = FILTER_MODES[_clamp(fx.get("filter"), 0, len(FILTER_MODES) - 1)][1]
    parts.extend(mode)
    if fx.get("echo"):
        parts.append("aecho=0.8:0.85:400:0.35")
    speed = round(float(fx.get("speed") or 1.0), 2)
    if abs(speed - 1.0) > 0.009:
        parts.append(f"atempo={speed}")
    return ",".join(parts)


def _fx_line(fx: dict) -> str:
    """Что показать в канале и на странице: только те ручки, которые сдвинуты."""
    bits = [f"{lab} {fx[key]:+d} дБ" for key, lab in
            (("bass", "бас"), ("mid", "середина"), ("high", "верха")) if fx.get(key)]
    if fx.get("filter"):
        bits.append(f"фильтр {FILTER_NAMES[fx['filter']]}")
    if fx.get("echo"):
        bits.append("эхо")
    if abs(float(fx.get("speed") or 1.0) - 1.0) > 0.009:
        bits.append(f"темп {fx['speed']}x")
    return ", ".join(bits)


def _before_options(source: str, at: int = 0):
    """Опции ДО входного файла: перемотка и сетевые страховки.

    Секунду отдаём именно сюда, а не в output: на входном -ss ffmpeg перескакивает
    сразу, а на выходном — декодирует весь трек с нуля до нужного места, и на слабом
    хосте это выглядит как зависший плеер.

    Локальный файл с «-reconnect» ffmpeg не открывает вообще: он отвечает
    «Option reconnect not found → Error opening input file», и трек из библиотеки
    молча не запускался, хотя голосовое подключалось.
    """
    parts = []
    if at:
        parts.append(f"-ss {int(at)}")
    if str(source).startswith("http"):
        parts.append(FFMPEG_BEFORE)
    return " ".join(parts) or None


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


def _cookies_line() -> str:
    """Что видно в консоли хостинга про куки: без них YouTube считает запросы ботом."""
    if not os.path.exists(COOKIES_FILE):
        return (f"cookies: НЕТ — файла {COOKIES_FILE} нет, YouTube будет отвечать "
                "«Sign in to confirm you're not a bot». Положи cookies.txt во вкладку Files")
    days = (time.time() - os.path.getmtime(COOKIES_FILE)) / 86400
    return f"cookies: есть — {COOKIES_FILE}, файлу {days:.0f} дн."


def _yt_problem(raw: str) -> str:
    """Перевод сырой ошибки yt-dlp в то, что можно прочитать в канале семьи."""
    low = raw.lower()
    if "age-restricted" in low or "confirm your age" in low:
        return ("Трек для взрослых — YouTube требует куки аккаунта 18+. Положи cookies.txt "
                "во вкладку Files на хостинге, рестарт не нужен.")
    if "sign in to confirm" in low or "not a bot" in low:
        return ("YouTube не отдаёт трек: он решил, что это робот. Лечится свежим `cookies.txt` "
                "во вкладке Files на хостинге (рядом с `main.py`) — рестарт не нужен.\n\n"
                "А пока можно включить то, что уже лежит в библиотеке семьи: там YouTube не "
                "трогает трек.")
    if "video unavailable" in low or "private video" in low:
        return "Видео недоступно: приватное, удалённое или только по ссылке."
    if "timed out" in low or "http error 5" in low or "too many requests" in low:
        return "YouTube не ответил вовремя — попробуй через минуту или возьми трек из библиотеки."
    return f"YouTube не отдал трек: {raw[:160]}"


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
            raw = str(e)
            _log(f"разбор не удался: {raw[:220]}")
            raise RuntimeError(_yt_problem(raw))
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
# Один файл на все серверы: {id: {громкость, кулдаун, очередь, диджей, авто-выход,
# канал панели и id самой панели — по нему старая карточка убирается перед новой}}.
DEFAULT_SETTINGS = {"volume": 50, "cooldown": PLAY_COOLDOWN, "queue": MAX_QUEUE,
                    "dj": False, "leave": True, "panel": 0, "panel_msg": 0,
                    "fx": {}, "repeat": "off"}
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
    out["panel"] = _int(out["panel"], 0)
    out["panel_msg"] = _int(out["panel_msg"], 0)
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


# ══════════════════ свет от обложки ══════════════════
# Палитру снимает сервер. Страница сама пиксели чужой картинки не прочитает: canvas с
# обложкой YouTube «отравлен» до первого рисования, и getImageData отвечает SecurityError.
_PALETTE: dict = {}
_COVER: dict = {}
MAX_PALETTE = 60
MAX_COVER = 24


def _hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*[max(0, min(255, int(c))) for c in rgb])


def _tame(color: str) -> str:
    """Цвет подсветки: видимый, но не режущий глаз.

    Чистая доминанта с тёмной обложки дала бы чёрное свечение — его просто не видно.
    """
    import colorsys

    r = int(color[1:3], 16) / 255.0
    g = int(color[3:5], 16) / 255.0
    b = int(color[5:7], 16) / 255.0
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l = min(.60, max(.36, l))
    s = min(.80, max(.30, s))
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return _hex((r * 255, g * 255, b * 255))


def _palette_of_bytes(raw: bytes) -> list:
    """5 главных цветов обложки, от самого частого к акценту."""
    from PIL import Image

    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img.thumbnail((64, 64))
    q = img.quantize(colors=5)
    pal = q.getpalette() or []
    counts = sorted(q.getcolors(1 << 20) or [], reverse=True)
    out = []
    for _cnt, idx in counts[:5]:
        part = pal[idx * 3:idx * 3 + 3]
        if len(part) == 3:
            out.append(_tame(_hex(part)))
    return out


def _cover_of_file(path: str):
    """Встроенная обложка mp3: без неё библиотека жила бы в сером.

    Картинка внутри mp3 лежит видеопотком, поэтому отсекаем звук (-an). С -vn ffmpeg
    не находит ни одного потока в выходном файле и молча отдаёт пустоту.
    """
    exe = _ffmpeg_exe()
    if not exe or not os.path.exists(path):
        return None
    out = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    out.close()
    try:
        subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", "-i", path,
                        "-an", "-vcodec", "mjpeg", "-frames:v", "1", "-f", "image2", out.name],
                       timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(out.name, "rb") as fh:
            raw = fh.read()
        return raw if len(raw) > 1024 else None
    except Exception:
        return None
    finally:
        try:
            os.unlink(out.name)
        except OSError:
            pass


def palette_peek(key: str):
    return _PALETTE.get(key) if key else None


def palette_put(key: str, colors: list) -> None:
    if not key or not colors:
        return
    if len(_PALETTE) >= MAX_PALETTE:
        _PALETTE.clear()
    _PALETTE[key] = colors[:5]


def _cover_raw(kind: str, src: str):
    """Байты обложки: ссылку качаем, из mp3 достаёт ffmpeg. Блокирующий — зовут из потока."""
    try:
        if kind == "url":
            import urllib.request

            req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 — адрес даёт Discord
                return resp.read(2000000) or None
        if kind == "file":
            return _cover_of_file(src)
    except Exception:
        return None
    return None


async def cover_bytes(key: str, kind: str, src: str):
    """Обложка один раз в памяти — с неё и картинка на странице, и цвета для подсветки.

    Пустой ответ тоже запоминаем: иначе каждый опрос страницы лезет на диск за тем,
    чего там нет.
    """
    if not key:
        return None
    if key in _COVER:
        return _COVER[key] or None
    raw = await asyncio.to_thread(_cover_raw, kind, src) or b""
    if len(_COVER) >= MAX_COVER:
        _COVER.clear()
    _COVER[key] = raw
    return raw or None


async def palette_for(key: str, kind: str, src: str) -> list:
    if key in _PALETTE:
        return _PALETTE[key]
    raw = await cover_bytes(key, kind, src)
    colors = await asyncio.to_thread(_palette_of_bytes, raw) if raw else []
    palette_put(key, colors or [])
    return colors


# ══════════════════ ритм трека: полосы энергии ══════════════════
# Страница сама звук не слышит: он уходит в голосовой канал, и AnalyserNode в
# браузере анализировал бы тишину. Поэтому полосы снимает сервер с того же файла,
# который сейчас играет, — ffmpeg режет трек на низ/середину/верх, а страница
# двигает фон по кадру, соответствующему фактической секунде трека. Считается один
# раз на файл и живёт на диске, повторный заказ того же трека ffmpeg не зовёт.
AMBIENT_FRAME_MS = 50
AMBIENT_MAX_SECONDS = 900
AMBIENT_BANDS = (("low", 800, "lowpass=f=160"),
                 ("mid", 2000, "highpass=f=160,lowpass=f=1400"),
                 ("high", 4000, "highpass=f=1400"))
AMBIENT_DIR = os.path.join(BASE_DIR, "music_ambient")
MAX_AMBIENT_FILES = 60
_AMBIENT: dict = {}
MAX_AMBIENT = 12
_AMBIENT_BUSY: set = set()


def _ambient_key(path: str) -> str:
    """Ключ полос: файл + его размер и дата. Перезалили трек — ключ другой."""
    st = os.stat(path)
    raw = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _ambient_band(path: str, rate: int, af: str):
    """Одна полоса как с16-моносигнал: ffmpeg сам фильтрует и передискретизирует."""
    exe = _ffmpeg_exe()
    if not exe:
        return None
    out = subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-t", str(AMBIENT_MAX_SECONDS),
         "-i", path, "-vn", "-af", af, "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=240)
    if out.returncode != 0 or not out.stdout:
        return None
    return array.array("h", out.stdout)


def _ambient_frames(samples: array, rate: int) -> list:
    """Средний квадрат амплитуды в кадре. Целые Python не переполняются."""
    step = max(1, int(rate * AMBIENT_FRAME_MS / 1000))
    return [sum(v * v for v in samples[i:i + step]) / step
            for i in range(0, len(samples) - step + 1, step)]


def _ambient_of_file(path: str):
    """Блокирующий расчёт полос. Зовётся только из потока (asyncio.to_thread)."""
    bands = []
    for _name, rate, af in AMBIENT_BANDS:
        try:
            data = _ambient_band(path, rate, af)
        except Exception:
            data = None
        if not data:
            return None
        bands.append(_ambient_frames(data, rate))
        del data
    n = min(len(b) for b in bands)
    if n < 4:
        return None
    peak = max(max(b[:n]) for b in bands)
    if peak <= 0:
        return None
    out = bytearray()
    for band in bands:
        for v in band[:n]:
            out.append(min(255, int(((v / peak) ** .5) * 255)))
    return {"frames": n, "fps": 1000 // AMBIENT_FRAME_MS, "bands": bytes(out)}


def _ambient_file(key: str) -> str:
    return os.path.join(AMBIENT_DIR, key + ".json")


def ambient_cached(key: str):
    """Уже посчитанные полосы: память, потом диск. Пустоту не считаем."""
    if not _is_hex_key(key):
        return None
    if key in _AMBIENT:
        return _AMBIENT[key] or None
    try:
        with open(_ambient_file(key), encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    try:
        res = {"frames": int(data["frames"]), "fps": int(data["fps"]),
               "bands": base64.b64decode(data["bands"])}
    except Exception:
        return None
    if len(_AMBIENT) >= MAX_AMBIENT:
        _AMBIENT.clear()
    _AMBIENT[key] = res
    return res


def _is_hex_key(key) -> bool:
    """Ключ приходит из адреса: без проверки им можно было бы читать любые файлы."""
    return isinstance(key, str) and len(key) == 16 and all(c in "0123456789abcdef" for c in key)


def ambient_store(key: str, res: dict) -> None:
    if len(_AMBIENT) >= MAX_AMBIENT:
        _AMBIENT.clear()
    _AMBIENT[key] = res
    try:
        os.makedirs(AMBIENT_DIR, exist_ok=True)
        tmp = _ambient_file(key) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"frames": res["frames"], "fps": res["fps"],
                       "bands": base64.b64encode(res["bands"]).decode("ascii")}, fh)
        os.replace(tmp, _ambient_file(key))
        names = sorted((f for f in os.listdir(AMBIENT_DIR) if f.endswith(".json")),
                       key=lambda f: os.path.getmtime(os.path.join(AMBIENT_DIR, f)))
        for old in names[:-MAX_AMBIENT_FILES]:
            try:
                os.unlink(os.path.join(AMBIENT_DIR, old))
            except OSError:
                pass
    except Exception:
        pass


async def ambient_for(path: str):
    """Полосы текущего трека или None. Считаем один раз, дальше только читаем."""
    if not path or not os.path.exists(path):
        return None
    try:
        key = _ambient_key(path)
    except OSError:
        return None
    hit = ambient_cached(key)
    if hit:
        return hit
    if key in _AMBIENT_BUSY:
        return None
    _AMBIENT_BUSY.add(key)
    try:
        res = await asyncio.to_thread(_ambient_of_file, path)
    except Exception:
        res = None
    finally:
        _AMBIENT_BUSY.discard(key)
    if not res:
        _AMBIENT[key] = {}
        return None
    ambient_store(key, res)
    return res


def ambient_key_of(path: str) -> str:
    try:
        return _ambient_key(path) if path and os.path.exists(path) else ""
    except OSError:
        return ""


def _web_track(track: dict, guild, elapsed=None):
    """Трек для страницы: только то, что можно показать человеку без Discord.

    У заказа из Discord автор лежит упоминанием `<@id>` — на странице его показывать
    нельзя, поэтому имя достаётся из кэша сервера.
    """
    if not track:
        return None
    who = str(track.get("requester") or "")
    m = re.match(r"<@!?(\d+)>", who)
    if m:
        member = guild.get_member(int(m.group(1)))
        who = member.display_name if member else "кто-то из семьи"
    return {
        "title": str(track.get("title") or "без названия")[:120],
        "who": who or "со страницы",
        "duration": track.get("duration"),
        "url": str(track.get("webpage_url") or "")[:200],
        "thumb": str(track.get("thumbnail") or "")[:200] or None,
        "elapsed": elapsed,
    }


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
        # 🎛 ручки пульта: живут на сервере, потому что звук в канале один на всех
        self.fx = _fx_clean(cfg.get("fx"))
        self.fx_by = ""
        # ↩ «назад» и история для страницы
        self.history: deque = deque(maxlen=12)
        self.repeat = cfg.get("repeat") if cfg.get("repeat") in REPEATS else "off"
        # ⏩ позиция: -ss задаёт отсчёт, mark — секунды, с которых он идёт
        self.base = 0
        self.mark = 0.0
        # источник, который надо поставить сразу после остановки текущего
        self.pending = None
        # True, когда остановка наша (скип/перемотка), а не трек сам кончился
        self.stopping = False


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
    """Форма заказа. Label поля держим до 45 символов: Discord отвечает 400 и кнопка
    «Заказать» молча не открывается — так и было с прошлой длинной подписью."""

    def __init__(self):
        super().__init__(
            title="Какую музыку включить?",
            custom_id="music_modal",
            components=[
                disnake.ui.TextInput(
                    label="Имя трека или ссылка",
                    custom_id="query",
                    style=disnake.TextInputStyle.short,
                    placeholder="Эксонад музыка · ссылка на YouTube · ссылка на звук",
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


def _T(bot, key: str, default: str, **subs) -> str:
    """Текст музыки: сохранённая в панели правка → эмодзи пака → подстановки {имя}.

    `{скобки}` подставляются после brand.E, чтобы правка из веб-панели не могла уронить
    экран: не та подстановка просто останется текстом.
    """
    text = brand.E(bot, get_text(key, default))
    for name, value in subs.items():
        text = text.replace("{" + name + "}", str(value))
    return text


# Тексты панели — константами: так редактор «Тексты» в веб-панели видит настоящий дефолт,
# и «Как в коде» возвращает именно эту надпись, а не пустоту.
T_PANEL_TITLE = ":hz_pcicon:  МУЗЫКА СЕМЬИ"
T_PANEL_INTRO = "*:red_crown:  Один голос на всю семью  :red_crown:*"
T_NOW_HEAD = ":hz_pcicon:  СЕЙЧАС"
T_NOW_IDLE = ":hz_warn:  Бот не в голосовом — жми **«В войс»**"
T_NOW_EMPTY = ":hz_trainicon:  очередь пуста"
# Полоска громкости — она же `bar()`: проценты читаются глазами быстрее, чем цифра.
T_NOW_LINE = (":hz_trainicon:  очередь {q}  ·  :pinkghost:  диджей {dj}\n"
              ":hz_bluemodroleicon:  звук {bar}  {vol}%")
T_STATE_PLAY = "● играет"
T_STATE_PAUSE = "◐ пауза"
T_STATE_QUIET = "○ тишина"
T_ORDER_HEAD = ":hz_megaphone:  КАК ЗАКАЗАТЬ"
T_ORDER_TEXT = (
    "> :hz_megaphone:  **«Заказать»** — имя из библиотеки или ссылка\n"
    "> :hz_house:  Набрал имя в канале, стоя в голосе — тоже заказ"
)
T_ORDER_SITE_BTN = ("> :hz_shareicon:  **«Сайт»** вверху: открывай в **Chrome**, "
                    "в Яндексе не грузится")
T_ORDER_SITE_MENU = ("> :hz_shareicon:  **«Сайт»** в «Ещё»: открывай в **Chrome**, "
                     "в Яндексе не грузится")
T_FILES_HEAD = ":hz_gifticon:  ОТКУДА ТРЕКИ"
T_FILES_TEXT = (
    "> :hz_gifticon:  «Закинуть трек» берёт mp3 из сообщения — до {n} штук по {mb} МБ\n"
    "> :hz_book:  Файл из вкладки Files тоже играется"
)
T_CTRL_HEAD = ":hz_trainicon:  ПУЛЬТ"
T_CTRL_TEXT = (
    "> :hz_pcicon:  Пауза, скип, очередь, громкость — вторым рядом\n"
    "> :hz_moderator:  Стоп, плейлисты, диджей, настройки — в **«Ещё»**"
)
T_BIND_HEAD = ":hz_house:  КАНАЛ МУЗЫКИ"
T_BIND_HERE = ("> {ch}\n"
               "> :hz_megaphone:  Тут панель, заказы и анонсы\n"
               "> :hz_pencil:  Сменить — список ниже")
T_BIND_OTHER = ("> :hz_megaphone:  Анонсы и заказы уходят в {ch}\n"
                "> :hz_pencil:  Сменить — список ниже")
T_BIND_NONE = ("> :hz_warn:  Не выбран — бот пишет там, где нажали\n"
               "> :hz_pencil:  Выбери списком ниже")
T_PANEL_DONE = (
    "{ch}\n\n"
    ":hz_house:  Панель, анонсы и заказы текстом — теперь только тут.\n"
    ":hz_trainicon:  Старая панель удалена: два экрана с кнопками в одном чате — не порядок."
)
T_PANEL_MOVED = (":hz_house:  Канал для панели, заказов и анонсов — {ch}\n"
                 ":hz_pencil:  Старый канал остаётся обычным чатом: музыка больше не мешает.")
T_CFG_CHANNEL_HEAD = ":hz_house:  КАНАЛ ПАНЕЛИ"
T_CFG_CHANNEL_SET = "{ch}\n:hz_trainicon:  Панель, анонсы и заказы текстом — только тут"
T_CFG_CHANNEL_NONE = ":hz_warn:  Не выбран — где вызвали `/music_panel`, там панель и лежит"
T_ANN_HEAD = ":hz_pcicon:  СЕЙЧАС ИГРАЕТ"
T_ANN_LINE = ("> :hz_trainicon:  длительность {dur}\n"
              "> :hz_bluemodroleicon:  звук {bar}  {vol}%")
T_ANN_WHO = "> :hz_megaphone:  Заказал(а) {who}"
T_DJ_OFF = ":pinkghost:  Библиотека кончилась — авто-диджей выключен."
T_SITE_NOBTN = (":hz_warn:  Адрес ещё не виден: бот берёт его из живых входов на сайт "
                "и сам покажет кнопку **«Сайт»** вверху панели. Задать вручную можно "
                "`MUSIC_URL` в `.env`.")


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


def _chan_line(bot, guild, cid: int) -> str:
    """Строка «канал панели» на экране настроек: упоминание, если канал жив."""
    ch = guild.get_channel(cid) if (guild is not None and cid) else None
    if ch is None:
        return _T(bot, "music_cfg_channel_none", T_CFG_CHANNEL_NONE)
    return _T(bot, "music_cfg_channel_set", T_CFG_CHANNEL_SET, ch=ch.mention)


def _cycle(values, current):
    """Следующее значение из списка: настройки шагают кнопкой, без модалок и опечаток."""
    try:
        i = values.index(current)
    except ValueError:
        i = 0
    return values[(i + 1) % len(values)]


PANEL_BANNER = os.path.join(BASE_DIR, "assets", "music_panel.jpg")


def _panel_banner(embed: disnake.Embed):
    """Баннер под панелью — как у казны: ссылка на CDN, а если её нет — вложение."""
    if not os.path.isfile(PANEL_BANNER):
        return None
    url = cdn_assets.cached_url("music_panel.jpg", PANEL_BANNER)
    if url:
        embed.set_image(url=url)
        return None
    embed.set_image(url="attachment://music_panel.jpg")
    return disnake.File(PANEL_BANNER, filename="music_panel.jpg")


MORE_ITEMS = [
    ("stop", "⏹", "Остановить и почистить", "очередь забывается, авто-диджей выключается"),
    ("add", "📥", "Закинуть трек", f"mp3 из сообщения, до {MAX_FILES_IN_MSG} за раз"),
    ("library", "🎧", "Треки семьи", "что лежит в библиотеке"),
    ("pl", "📖", "Плейлисты", "включить сохранённую очередь целиком"),
    ("pl_save", "💾", "Очередь в плейлист", "сохранить то, что сейчас стоит"),
    ("dj", "🔁", "Авто-диджей", "сам берёт случайный трек, когда очередь пуста"),
    ("vote", "🗳", "Скип голосом", "пропускаем большинством в канале"),
    ("cfg", "⚙️", "Настройки", "громкость, кулдаун, размер очереди, авто-выход"),
    # «Сайт» живёт здесь, только пока у сайта нет адреса: с адресом он уезжает кнопкой
    # на верхний ряд панели и открывается одним нажатием.
    ("site", "🌐", "Сайт музыки", "заказ из браузера: вставил ссылку — играет"),
    ("refresh", "🔄", "Обновить панель", "живое «сейчас играет» и свежая очередь"),
]


class MoreMenu(disnake.ui.StringSelect):
    """Всё, что жмут реже, чем паузу: одна строка вместо трёх рядов кнопок."""

    def __init__(self):
        items = [it for it in MORE_ITEMS if it[0] != "site" or not _site_url()]
        super().__init__(
            custom_id="music:more",
            placeholder="Ещё · стоп, библиотека, плейлисты, настройки",
            min_values=1, max_values=1,
            options=[disnake.SelectOption(label=lab, emoji=em, description=desc,
                                          value="music:" + val)
                     for val, em, lab, desc in items],
        )

    async def callback(self, inter: disnake.MessageInteraction):
        cog = inter.bot.get_cog("Music")
        what = self.values[0].partition(":")[2]
        if what == "stop":
            await cog.stop_all(inter)
        elif what == "add":
            await cog.ask_file(inter)
        elif what == "library":
            await cog.show_library(inter)
        elif what == "pl":
            await cog.show_playlists(inter)
        elif what == "pl_save":
            await cog.start_playlist_save(inter)
        elif what == "dj":
            await cog.toggle_dj(inter)
        elif what == "vote":
            await cog.vote_skip(inter)
        elif what == "cfg":
            await cog.show_settings(inter)
        elif what == "site":
            await cog.say_site(inter)
        elif what == "refresh":
            await cog.refresh_panel(inter)
        else:
            await inter.response.defer(ephemeral=True)


class MusicPanelView(disnake.ui.View):
    """Панель музыки в канале: четыре ряда вместо шести рядов кнопок.

    Ряд 0 — как начать (заказ, голос, выход, сайт), ряд 1 — пульт того, что играет,
    ряд 2 — редкое в одном выпадающем списке, ряд 3 — канал, куда бот пишет музыку.
    custom_id держат кнопки живыми после рестарта. Эмодзи на кнопках unicode:
    серверные бренд-эмодзи в кнопках не рисуются.
    """

    def __init__(self, bot=None):
        super().__init__(timeout=None)
        url = _site_url()
        if url:
            # style=link: Discord открывает ссылку сам, нажатие = сайт в браузере.
            # Сообщение со ссылкой, которое надо ещё скопировать, — лишний шаг.
            self.add_item(disnake.ui.Button(label="Сайт · в Chrome  ↗", emoji="🌐",
                                            style=disnake.ButtonStyle.link, url=url, row=0))
        self.add_item(MoreMenu())
        self.add_item(PanelChannelSelect(on_panel=True))

    # ---------- ряд 1: начать ----------
    @disnake.ui.button(label="Заказать", emoji="🎵", style=disnake.ButtonStyle.primary,
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

    @disnake.ui.button(label="В войс", emoji="🎧", style=disnake.ButtonStyle.secondary,
                       custom_id="music:join", row=0)
    async def join(self, button, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        await inter.response.defer(ephemeral=True)
        vc = await cog.ensure_voice(inter)
        if not vc:
            return
        cog.bind_channel(inter.guild, inter.channel)
        pc = _e(inter.bot, "hz_pcicon", "🎧")
        meg = _e(inter.bot, "hz_megaphone", "📢")
        await inter.edit_original_response(
            embed=_ok(inter.bot, "БОТ В ГОЛОСЕ",
                      f"### {pc}  {vc.channel.mention}\n\n"
                      f"{meg}  Жму «Заказать» — или напиши имя трека в канале"),
            view=TalkView(inter.bot))

    @disnake.ui.button(label="Выйти", emoji="🚪", style=disnake.ButtonStyle.danger,
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

    # ---------- ряд 2: пульт ----------
    @disnake.ui.button(label="Пауза", emoji="⏯", style=disnake.ButtonStyle.secondary,
                       custom_id="music:pause", row=1)
    async def pause(self, button, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        vc = await cog.check_control(inter)
        if not vc:
            return
        p = cog.get_player(inter.guild)
        title = (p.current or {}).get("title", "трек")
        if vc.is_paused():
            cog._resume(p, vc)
            emb = _ok(inter.bot, "ИГРАЕТ ДАЛЬШЕ", f"▶️  **{title}**")
        elif vc.is_playing():
            cog._pause(p, vc)
            emb = _ok(inter.bot, "ПАУЗА",
                      f"⏸  **{title}**\n\n"
                      f"{_e(inter.bot, 'hz_trainicon', '📜')}  Это же нажатие — продолжить.")
        else:
            emb = _err(inter.bot, "ТИШИНА",
                       f"{_e(inter.bot, 'hz_pcicon', '🎧')}  Сейчас ничего не играет — "
                       "закажи трек кнопкой «Заказать».")
        await inter.response.send_message(embed=emb, ephemeral=True)

    @disnake.ui.button(label="Скип", emoji="⏭", style=disnake.ButtonStyle.secondary,
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

    @disnake.ui.button(label="Очередь", emoji="📜", style=disnake.ButtonStyle.secondary,
                       custom_id="music:queue", row=1)
    async def queue(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("Music").show_queue(inter)

    @disnake.ui.button(label="Тише", emoji="🔉", style=disnake.ButtonStyle.secondary,
                       custom_id="music:vol_down", row=1)
    async def vol_down(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("Music").change_volume(inter, -0.1)

    @disnake.ui.button(label="Громче", emoji="🔊", style=disnake.ButtonStyle.secondary,
                       custom_id="music:vol_up", row=1)
    async def vol_up(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("Music").change_volume(inter, +0.1)

class PanelChannelSelect(disnake.ui.ChannelSelect):
    """Куда бот пишет музыку: выбор живёт на панели и в настройках сервера, а не в памяти.

    На панели ряд свой, custom_id тоже:persistent-вью одной кнопки на два экрана не
    держит — disnake диспатчит по custom_id и последний выбор перетёр бы первый.
    """

    def __init__(self, guild=None, current: int = 0, *, on_panel: bool = False):
        ch = guild.get_channel(current) if (guild is not None and current) else None
        super().__init__(
            custom_id="music:panel:channel" if on_panel else "music:cfg:channel",
            placeholder="Канал музыки · сюда писать заказы и анонсы",
            channel_types=[disnake.ChannelType.text],
            min_values=1, max_values=1,
            default_values=[ch] if ch else None,
            row=3 if on_panel else 2,
        )

    async def callback(self, inter: disnake.MessageInteraction):
        cog = inter.bot.get_cog("Music")
        if cog is None or inter.guild is None or not self.values:
            return await inter.response.defer(ephemeral=True)
        await cog.bind_panel_channel(inter, self.values[0], self.view)


class MusicSettingsView(disnake.ui.View):
    """Экран настроек: каждая кнопка шагает значение и сразу перерисовывает экран."""

    def __init__(self, guild_id: int, guild=None):
        super().__init__(timeout=600)
        self.guild_id = int(guild_id)
        if guild is not None:
            self.add_item(PanelChannelSelect(guild, _cfg(guild_id)["panel"]))

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
        panel = _chan_line(bot, guild, cfg["panel"])
        sections = [
            (f"{pc}  ГРОМКОСТЬ",
             f"{live}\n{meg}  Шаг по 1% — «Тише» и «Громче» на панели крутят этот же звук"),
            (f"{train}  ОЧЕРЕДЬ", f"**{t['queue']}**\n{book}  Сколько треков ждут своей очереди"),
            (f"{meg}  КУЛДАУН ЗАКАЗА",
             f"**{t['cooldown']}**\n{bot_i}  Чтобы один человек не перекрывал всех подряд"),
            (f"{ghost}  АВТО-ДИДЖЕЙ",
             f"**{t['dj']}**\n{pc}  Кончится очередь — возьмёт случайный трек из библиотеки"),
            (f"{_e(bot, 'hz_warn', '⚠️')}  АВТО-ВЫХОД",
             f"**{t['leave']}**\n{book}  Если в канале не осталось никого, кроме бота"),
            (_T(bot, "music_cfg_channel_head", T_CFG_CHANNEL_HEAD), panel),
        ]
        return panel_embed(
            f"{pc}  НАСТРОЙКИ МУЗЫКИ", sections, color_key="music_color", guild=guild,
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
        await self._apply(inter, {"volume": max(5, _cfg(self.guild_id)["volume"] - 1)})

    @disnake.ui.button(label="Громче", style=disnake.ButtonStyle.secondary,
                       custom_id="music:cfg:vol_up", row=0)
    async def v_up(self, button, inter: disnake.MessageInteraction):
        await self._apply(inter, {"volume": min(100, _cfg(self.guild_id)["volume"] + 1)})

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
        # фоновые расчёты полос: задачи держим, иначе asyncio их молча соберёт мусор
        self._ambient_tasks: set = set()

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
            _log(_cookies_line())

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
        view = MusicSettingsView(inter.guild.id, inter.guild)
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
                           "«Остановить» — в списке «Ещё»."),
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
        self.bind_channel(inter.guild, inter.channel)
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

    def _text_order(self, message) -> str:
        """Сообщение в канале музыки — это заказ, если в нём ссылка или имя из библиотеки.

        Порядок держит человека в том же голосовом канале, где сидит бот, и слушает
        только тот канал, где лежит панель: обычная переписка в чате не должна
        случайно включать музыку.
        """
        guild = message.guild
        text = str(message.content or "").strip()
        if guild is None or not (3 <= len(text) <= 200) or message.attachments:
            return ""
        if text.startswith(("/", "@", "!")):
            return ""
        p = self.players.get(guild.id)
        ch = self.panel_channel(guild) or (getattr(p, "text_channel", None) if p else None)
        if ch is None or ch.id != message.channel.id:
            return ""
        vc = guild.voice_client
        if not vc or not vc.is_connected() or not vc.channel:
            return ""
        voice = getattr(message.author, "voice", None)
        if not voice or not voice.channel or voice.channel.id != vc.channel.id:
            return ""
        if _LINK_RE.match(text) or _stem_to_path(text):
            return text
        return ""

    async def order_text(self, message, query: str):
        """Тот же заказ, что и кнопкой, только набором текста: без слэш-команды."""
        guild = message.guild
        cfg = _cfg(guild.id)
        now = time.time()
        wait = cfg["cooldown"] - (now - self._last_play.get(message.author.id, 0))
        if wait > 0:
            return await message.reply(f"⏳  Следующий заказ через {int(wait) + 1} сек",
                                       mention_author=False)
        p = self.get_player(guild)
        if len(p.queue) >= cfg["queue"]:
            return await message.reply(
                f"📜  Очередь забита: {cfg['queue']} треков. «Остановить» — в списке «Ещё»",
                mention_author=False)
        self._last_play[message.author.id] = now
        try:
            track = await self._extract(query)
        except Exception as e:
            self._last_play.pop(message.author.id, None)
            return await message.reply(f"🎧  Не нашёл: {str(e)[:120]}", mention_author=False)
        track["requester"] = message.author.mention
        p.queue.append(track)
        vc = guild.voice_client
        was_idle = not (vc and (vc.is_playing() or vc.is_paused()))
        emb = _ok(self.bot, "ЗАКАЗ ИЗ КАНАЛА",
                  f"### 🎵  **{track['title']}**\n\n"
                  f"{_e(self.bot, 'hz_megaphone', '📢')}  Заказал(а) {message.author.mention}")
        try:
            await message.reply(embed=emb, mention_author=False)
        except Exception:
            pass
        if was_idle:
            await self._play_next(guild)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        stamp = self._pending.get(message.author.id)
        if not stamp or stamp[0] != message.channel.id:
            query = self._text_order(message)
            if query:
                await self.order_text(message, query)
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
        p = self.players[guild.id]
        if p.text_channel is None:
            p.text_channel = self.panel_channel(guild)
        return p

    def panel_channel(self, guild: disnake.Guild):
        """Канал, который выбран в настройках: панель, анонсы и заказы текстом идут туда."""
        cid = _cfg(guild.id)["panel"] if guild else 0
        return guild.get_channel(cid) if cid else None

    def bind_channel(self, guild, channel) -> None:
        """Куда бот пишет про трек. Настроенный канал важнее того, где человек нажал
        кнопку, — иначе анонсы уезжают в случайный чат и засоряют его."""
        p = self.get_player(guild)
        p.text_channel = self.panel_channel(guild) or channel

    async def bind_panel_channel(self, inter, ch, view=None) -> None:
        """Смена «канала музыки» прямо с экрана: в конфиг и в перерисовку тем же
        нажатием. Человек видит новый канал сразу, а не после рестарта бота."""
        if not is_admin(inter):
            return await inter.response.send_message(
                embed=_err(inter.bot, "НЕ ПО ТЕБЕ",
                           f"{_e(inter.bot, 'hz_warn', '⚠️')}  Канал музыки переставляет "
                           "админ — так панель не утаскивают случайные люди."),
                ephemeral=True)
        cid = int(getattr(ch, "id", 0) or 0)
        if not cid:
            return await inter.response.defer(ephemeral=True)
        err = _cfg_save(inter.guild.id, {"panel": cid})
        if err:
            return await inter.response.send_message(
                embed=_err(inter.bot, "НЕ СОХРАНИЛОСЬ", err), ephemeral=True)
        self.get_player(inter.guild).text_channel = inter.guild.get_channel(cid) or ch
        if isinstance(view, MusicSettingsView):
            return await inter.response.edit_message(
                embed=view.embed(inter.bot, inter.guild), view=view)
        emb = self._panel_embed(inter.guild, inter.channel)
        await inter.response.edit_message(embed=emb, view=MusicPanelView(inter.bot),
                                          file=_panel_banner(emb))
        await inter.followup.send(
            embed=_ok(inter.bot, "КАНАЛ ВЫБРАН",
                      _T(inter.bot, "music_panel_moved", T_PANEL_MOVED,
                         ch=(inter.guild.get_channel(cid) or ch).mention)),
            ephemeral=True)

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

    # ══════════════════ то, чем пользуется страница музыки ══════════════════
    # Со страницы заказ приходит без Discord-взаимодействия: автора знает только
    # словами, поэтому эти методы принимают сервер и ник, а не `inter`.

    def web_state(self, guild: disnake.Guild) -> dict:
        vc = guild.voice_client
        p = self.players.get(guild.id)
        cfg = _cfg(guild.id)
        volume = p.volume if p else cfg["volume"] / 100.0
        elapsed = self._elapsed(p, vc) if p else None
        cover_key, _kind, _src = self.web_cover_source(guild)
        cover = ("/music/api/cover?k=" + hashlib.sha1(cover_key.encode()).hexdigest()[:10]
                 if cover_key else None)
        return {
            "connected": bool(vc and vc.is_connected()),
            "channel": vc.channel.name if vc and vc.channel else None,
            "playing": bool(vc and vc.is_playing()),
            "paused": bool(vc and vc.is_paused()),
            "volume": int(round(volume * 100)),
            "dj": bool(p.dj if p else cfg["dj"]),
            "queue_max": cfg["queue"],
            "cooldown": cfg["cooldown"],
            "repeat": (p.repeat if p else cfg.get("repeat")) or "off",
            "fx": p.fx if p else _fx_clean(cfg.get("fx")),
            "fx_by": (p.fx_by if p else "") or None,
            "cover": cover,
            "palette": palette_peek(cover_key) or [],
            "ambient": self.web_ambient_state(guild),
            "history": [_web_track(t, guild)
                        for t in reversed(list(p.history)[-8:])][:8] if p else [],
            "current": _web_track((p.current if p else None) or {}, guild, elapsed),
            "queue": [_web_track(t, guild) for t in (list(p.queue) if p else [])],
        }

    def web_cover_source(self, guild: disnake.Guild):
        """Откуда брать картинку для подсветки: (ключ кэша, откуда, адрес) или пустота."""
        p = self.players.get(guild.id)
        track = (p.current if p else None) or {}
        thumb = str(track.get("thumbnail") or "")
        if thumb.startswith("http"):
            return thumb, "url", thumb
        stream = str(track.get("stream_url") or "")
        if stream and not stream.startswith("http") and stream.lower().endswith(AUDIO_EXT):
            path = os.path.abspath(stream)
            return path, "file", path
        return "", None, None

    async def web_cover(self, guild: disnake.Guild):
        """Байты обложки и её цвета; пустой трек без картинки — пустой ответ."""
        key, kind, src = self.web_cover_source(guild)
        if not key:
            return None, []
        raw = await cover_bytes(key, kind, src)
        return raw, await palette_for(key, kind, src)

    def web_ambient_state(self, guild: disnake.Guild) -> dict:
        """Что страница знает о полосах текущего трека: ключ и готовность.

        Считать ffmpeg'ом здесь не ждём: страница опрашивает плеер каждые две
        секунды, и висеть из-за анализа она не должна. Заказывает расчёт в фоне и
        в следующем опросе отдаёт ready=True.
        """
        _key, kind, src = self.web_cover_source(guild)
        if kind != "file" or not src:
            return {}
        key = ambient_key_of(src)
        if not key:
            return {}
        res = ambient_cached(key)
        if not res and key not in _AMBIENT_BUSY:
            try:
                task = asyncio.ensure_future(ambient_for(src))
            except RuntimeError:      # цикла нет — офлайн-тест: просто не считаем
                return {"k": key, "ready": False, "fps": 0, "frames": 0}
            self._ambient_tasks.add(task)
            task.add_done_callback(self._ambient_tasks.discard)
        return {"k": key, "ready": bool(res), "fps": (res or {}).get("fps", 0),
                "frames": (res or {}).get("frames", 0)}

    def web_ambient_bands(self, key: str) -> dict:
        """Готовые полосы по ключу; None — если их ещё считает ffmpeg."""
        res = ambient_cached(key)
        if not res:
            return None
        return {"k": key, "fps": res["fps"], "frames": res["frames"],
                "bands": base64.b64encode(res["bands"]).decode("ascii")}

    def web_library(self, query: str = "") -> dict:
        """Библиотека для живого поиска: имя, есть ли обложка, ничего лишнего."""
        want = re.sub(r"\s+", " ", str(query or "")).strip().lower()
        files = _library()
        hits = [f for f in files if not want or want in _stem(f).lower()][:30]
        return {"found": len(hits), "total": len(files),
                "tracks": [{"title": _stem(f)[:120]} for f in hits]}

    def web_playlists(self) -> dict:
        """Плейлисты семьи для страницы: те же, что играет /музыка, без своего хранилища."""
        data = _playlists()
        return {"playlists": [{"name": str(name)[:40],
                               "tracks": [str(t)[:120] for t in (tracks or [])][:MAX_IN_PLAYLIST]}
                              for name, tracks in list(data.items())[:MAX_PLAYLISTS]]}

    async def web_enqueue(self, guild: disnake.Guild, query: str, nick: str,
                          key: str = "") -> dict:
        """Ставит заказ со страницы. `key` — с чем считать кулдаун (обычно IP)."""
        cfg = _cfg(guild.id)
        vc = guild.voice_client
        if not vc or not vc.is_connected():
            return {"error": "Бот сейчас ни в каком канале не сидит: "
                             "сначала «В войс» в /музыка, потом заказ"}
        if not (query or "").strip():
            return {"error": "Пустой запрос — вставь ссылку или назови трек"}
        p = self.get_player(guild)
        if len(p.queue) >= cfg["queue"]:
            return {"error": f"Очередь забита: {cfg['queue']} треков. "
                             "Попроси штаб нажать «Остановить» в списке «Ещё»"}
        who = "web:" + (key or nick or "?")
        now = time.time()
        wait = cfg["cooldown"] - (now - self._last_play.get(who, 0))
        if wait > 0:
            return {"error": f"Не так быстро: следующий заказ через {int(wait) + 1} сек"}
        try:
            track = await self._extract(query)
        except Exception as e:
            return {"error": str(e)[:200]}
        self._last_play[who] = now
        track["requester"] = nick
        p.queue.append(track)
        was_idle = not (vc.is_playing() or vc.is_paused())
        if was_idle:
            await self._play_next(guild)
        await self._web_announce(p, track, nick)
        return {"ok": True,
                "did": ("Включила: " if was_idle else "В очереди: ") + track["title"]}

    async def _web_announce(self, p: GuildPlayer, track: dict, nick: str) -> None:
        """В Discord заказ с сайта иначе не виден: карточка в канал музыки."""
        ch = p.text_channel
        if ch is None:
            return
        try:
            await ch.send(embed=_ok(self.bot, "ЗАКАЗ СО СТРАНИЦЫ",
                                    f"### 🎵  **{track['title']}**\n\n"
                                    f"{_e(self.bot, 'hz_megaphone', '📢')}  Заказал(а) {nick or 'кто-то'}"))
        except Exception:
            pass

    async def web_action(self, guild: disnake.Guild, action: str, value=None,
                         who: str = "") -> dict:
        vc = guild.voice_client
        if not vc or not vc.is_connected():
            return {"error": "Бот сейчас ни в каком канале не сидит"}
        p = self.get_player(guild)
        if action == "pause":
            if vc.is_playing():
                self._pause(p, vc)
                return {"ok": True, "did": "Пауза"}
            if vc.is_paused():
                self._resume(p, vc)
                return {"ok": True, "did": "Играет дальше"}
            return {"error": "Сейчас ничего не играет"}
        if action == "skip":
            if not (vc.is_playing() or vc.is_paused()):
                return {"error": "Пропускать нечего"}
            title = (p.current or {}).get("title", "трек")
            p.votes.clear()
            p.stopping = True
            vc.stop()
            return {"ok": True, "did": f"Пропустила: {title}"}
        if action == "prev":
            if not p.history:
                return {"error": "Назад некуда — это первый трек после включения"}
            back = p.history.pop()
            if p.current:
                p.queue.appendleft(dict(p.current))
            p.queue.appendleft(back)
            p.stopping = True
            if vc.is_playing() or vc.is_paused():
                vc.stop()
            else:
                await self._play_next(guild)
            return {"ok": True, "did": f"Вернула: {back.get('title', 'трек')}"}
        if action == "stop":
            p.queue.clear()
            p.history.clear()
            p.dj = False
            p.votes.clear()
            p.stopping = True
            if vc.is_playing() or vc.is_paused():
                vc.stop()
            _cfg_save(guild.id, {"dj": False})
            return {"ok": True, "did": "Очередь очищена, авто-диджей выключен"}
        if action == "volume":
            try:
                pct = max(5, min(100, int(value)))
            except (TypeError, ValueError):
                return {"error": "Громкость — число от 5 до 100"}
            p.volume = pct / 100.0
            if p.source:
                p.source.volume = p.volume
            _cfg_save(guild.id, {"volume": pct})
            return {"ok": True, "did": f"Громкость {pct}%"}
        if action == "dj":
            p.dj = not p.dj
            _cfg_save(guild.id, {"dj": p.dj})
            return {"ok": True, "did": "Авто-диджей включён" if p.dj else "Авто-диджей выключен"}
        if action == "shuffle":
            if len(p.queue) < 2:
                return {"error": "В очереди меньше двух треков — перемешивать нечего"}
            items = list(p.queue)
            random.shuffle(items)
            p.queue = deque(items)
            return {"ok": True, "did": f"Перемешала: {len(items)} треков в новом порядке"}
        if action == "repeat":
            order = REPEATS
            p.repeat = order[(order.index(p.repeat) + 1) % len(order)] if p.repeat in order else "off"
            _cfg_save(guild.id, {"repeat": p.repeat})
            return {"ok": True, "did": REPEAT_NAMES[p.repeat]}
        if action == "playlist":
            # один запрос на весь плейлист: иначе список из 30 треков упирался бы
            # в лимит записей с одного адреса
            name = re.sub(r"\s+", " ", str(value or "")).strip()[:40]
            saved = _playlists().get(name)
            if not saved:
                return {"error": f"Плейлиста «{name}» нет — возможно, его переименовали"}
            room = _cfg(guild.id)["queue"] - len(p.queue)
            if room <= 0:
                return {"error": f"Очередь полная: {_cfg(guild.id)['queue']} треков"}
            added, missing = [], []
            for stem in saved[:room]:
                path = _stem_to_path(stem)
                if path is None:
                    missing.append(stem)
                    continue
                p.queue.append({"title": str(stem)[:120], "stream_url": path,
                                "webpage_url": str(stem), "duration": None,
                                "thumbnail": None, "ts": time.time(),
                                "requester": f"📌 {name}"})
                added.append(stem)
            if not added:
                return {"error": f"Из «{name}» в библиотеке ничего не осталось"}
            if not (vc.is_playing() or vc.is_paused()):
                await self._play_next(guild)
            back = f", {len(missing)} нет в библиотеке" if missing else ""
            return {"ok": True, "did": f"Плейлист «{name}»: {len(added)} треков{back}"}
        if action == "seek":
            got = await self._rebuild(guild, at=value)
            if got.get("error"):
                return got
            return {"ok": True, "did": "Перемотала"}
        if action == "fx":
            patch = _fx_clean(value)
            same = _fx_chain(patch) == _fx_chain(p.fx)
            p.fx = patch
            p.fx_by = str(who or "")[:32]
            _cfg_save(guild.id, {"fx": patch})
            if same:
                return {"ok": True, "did": "Ничего не изменилось"}
            got = await self._rebuild(guild)
            if got.get("error"):
                return got
            line = _fx_line(patch)
            return {"ok": True, "did": ("Звук: " + line) if line else "Звук как в записи"}
        return {"error": "Не знаю такой кнопки"}

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
                           "«Остановить» — в списке «Ещё»."),
                ephemeral=True)
        vc = await self.ensure_voice(inter)
        if not vc:
            return
        self.bind_channel(inter.guild, inter.channel)
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

    def _make_source(self, track: dict, volume: float, at: int = 0, fx: dict = None):
        """Источник ffmpeg + файл, куда он пишет свои ошибки.

        stderr в PIPE без читателя опасен: буфер забивается и ffmpeg встаёт посреди
        трека, поэтому пишем во временный файл и читаем хвост только при ошибке.
        """
        err = tempfile.TemporaryFile()
        chain = _fx_chain(fx or {})
        opts = f"{FFMPEG_OPTS} -af {chain}" if chain else FFMPEG_OPTS
        try:
            src = disnake.PCMVolumeTransformer(
                disnake.FFmpegPCMAudio(track["stream_url"], executable=_ffmpeg_exe() or "ffmpeg",
                                       before_options=_before_options(track["stream_url"], at),
                                       options=opts, stderr=err),
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

    def _make_after(self, p: GuildPlayer, track: dict, guild, err_file):
        """Хвост воспроизведения: отчитаться, поставить перестроенный поток или идти дальше.

        Ошибка читается из stderr-файла: сам трек мог лежать на диске, а ffmpeg
        отказаться его открывать — без хвоста в карточке это выглядело бы как тишина.
        """
        loop = asyncio.get_running_loop()

        def after(err):
            tail = self._stderr_tail(err_file) if err else ""
            try:
                err_file.close()
            except Exception:
                pass
            if err:
                _log(f"ошибка воспроизведения: {err} | {tail}")
                asyncio.run_coroutine_threadsafe(
                    self._play_failed(p, track, f"`{err}`" + (f"\n{tail}" if tail else "")), loop)
            job = p.pending
            if job:
                p.pending = None
                asyncio.run_coroutine_threadsafe(self._put_source(guild, job), loop)
                return
            if not p.stopping and p.repeat == "one" and p.current:
                p.queue.appendleft(dict(p.current))
            asyncio.run_coroutine_threadsafe(self._play_next(guild), loop)

        return after

    async def _put_source(self, guild, job) -> None:
        """Ставит поток, собранный при перемотке или повороте ручки."""
        source, err_file, was_paused = job
        p = self.players.get(guild.id)
        vc = guild.voice_client
        if not p or not vc or not vc.is_connected():
            try:
                err_file.close()
            except Exception:
                pass
            return
        p.source = source
        p.stopping = False
        p.mark = 0.0 if was_paused else time.monotonic()
        vc.play(source, after=self._make_after(p, p.current or {}, guild, err_file))
        if was_paused:
            vc.pause()

    def _elapsed(self, p: GuildPlayer, vc) -> int:
        """Сколько секунд трека прозвучало для канала.

        Позиции у источника в disnake нет: PCMVolumeTransformer её не пробрасывает, и
        без своего счёта перемотка показывала бы всегда ноль. Поэтому часы у нас:
        base — уже сыгранное, mark — с какого момента счёт идёт дальше (0 = тишина).
        """
        if p is None:
            return 0
        if p.mark and vc is not None and vc.is_playing():
            return int(p.base + (time.monotonic() - p.mark))
        return int(p.base)

    def _pause(self, p: GuildPlayer, vc) -> None:
        """Пауза замораживает счёт: иначе после снятия трек «ускочит» вперёд."""
        p.base = self._elapsed(p, vc)
        p.mark = 0.0
        vc.pause()

    def _resume(self, p: GuildPlayer, vc) -> None:
        vc.resume()
        p.mark = time.monotonic()

    async def _rebuild(self, guild, at=None) -> dict:
        """Перезапускает текущий трек с нужной секунды и с актуальными ручками.

        По-другому звук не поменять: ffmpeg фильтры на лету не принимает, зато -ss на
        входе позволяет не начинать трек сначала.
        """
        p = self.players.get(guild.id)
        vc = guild.voice_client
        if not p or not p.current:
            return {"error": "Сейчас нечего перестраивать"}
        if not vc or not (vc.is_playing() or vc.is_paused()):
            return {"ok": True, "did": "Запомнила: ручки лягут на следующий трек"}
        dur = p.current.get("duration")
        at = self._elapsed(p, vc) if at is None else _clamp(at, 0, 10 ** 6)
        if dur:
            at = min(at, max(0, int(dur) - 1))
        try:
            source, err_file = self._make_source(p.current, p.volume, at=at, fx=p.fx)
        except Exception as e:
            return {"error": f"Так ffmpeg не умеет: {str(e)[:120]}"}
        was_paused = vc.is_paused()
        p.base = at
        p.mark = 0.0
        p.stopping = True
        p.pending = (source, err_file, was_paused)
        vc.stop()
        return {"ok": True}

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
            if p.repeat == "all" and p.history:
                # «вся очередь по кругу»: пустой список добираем последним сыгранным
                p.queue.append(dict(p.history[-1]))
            elif p.dj:
                picked = self._dj_track(p)
                if picked is None:
                    p.dj = False
                    if p.text_channel:
                        await p.text_channel.send(
                            embed=note_embed(_T(self.bot, "music_dj_off", T_DJ_OFF),
                                             guild=guild))
                    return
                p.queue.append(picked)
            else:
                p.current = None
                p.source = None
                return

        track = p.queue.popleft()
        if p.current and p.current.get("title") != track.get("title"):
            p.history.append(dict(p.current))
        try:
            if track["webpage_url"].startswith("http") and time.time() - track["ts"] > 600:
                # ссылка на чужой стрим могла протухнуть, файл с диска — нет
                fresh = await self._extract(track["webpage_url"])
                track.update(fresh)
            source, err_file = self._make_source(track, p.volume, fx=p.fx)
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
        p.base = 0
        p.mark = time.monotonic()
        p.stopping = False

        vc.play(source, after=self._make_after(p, track, guild, err_file))

        if p.text_channel:
            link = track["webpage_url"]
            shown = f"[{track['title']}]({link})" if link.startswith("http") else f"**{track['title']}**"
            body = _T(self.bot, "music_ann_line", T_ANN_LINE,
                      dur=_fmt_duration(track["duration"]), vol=int(round(p.volume * 100)),
                      bar=bar(int(round(p.volume * 100)), 100, width=6))
            if track.get("requester"):
                body += "\n" + _T(self.bot, "music_ann_who", T_ANN_WHO,
                                  who=track["requester"])
            line = _fx_line(p.fx)
            if line:
                # звук крутят с страницы — в канале должно быть видно, почему он не как в записи
                body += f"\n> 🎛  {line}"
            emb = card(_T(self.bot, "music_ann_head", T_ANN_HEAD),
                       color_key="music_color", guild=guild)
            emb.description = f"### {shown}\n\n{body}"
            if track.get("thumbnail"):
                emb.set_thumbnail(url=track["thumbnail"])
            try:
                await p.text_channel.send(embed=emb)
            except Exception:
                pass

    async def stop_all(self, inter):
        """Очистить очередь и заглушить авто-диджей: пункт из списка «Ещё»."""
        vc = await self.check_control(inter)
        if not vc:
            return
        p = self.get_player(inter.guild)
        p.queue.clear()
        p.dj = False
        p.votes.clear()
        if vc.is_playing() or vc.is_paused():
            vc.stop()
        _cfg_save(inter.guild.id, {"dj": False})
        await inter.response.send_message(
            embed=_ok(self.bot, "СТОП",
                      f"⏹  Очередь очищена\n\n"
                      f"{_e(self.bot, 'pinkghost', '🔁')}  Авто-диджей выключен, "
                      "иначе он бы сразу подставил случайный трек."),
            ephemeral=True)

    async def say_site(self, inter):
        url = _site_url()
        if url:
            emb = _ok(self.bot, "САЙТ МУЗЫКИ",
                      f"### {_e(self.bot, 'hz_pcicon', '🎧')}  {url}\n\n"
                      f"{_e(self.bot, 'hz_warn', '⚠️')}  Открывай в **Chrome**: в Яндексе "
                      "и во встроенном окне Дискорда адрес не грузится. Скопируй ссылку "
                      "и вставь в Chrome.\n\n"
                      f"{_e(self.bot, 'hz_megaphone', '📢')}  Открыт без пароля: вставил "
                      "ссылку или имя — трек в общей очереди.")
        else:
            emb = _err(self.bot, "ССЫЛКА НЕ НАСТРОЕНА",
                       _T(self.bot, "music_site_nobtn", T_SITE_NOBTN))
        await inter.response.send_message(embed=emb, ephemeral=True)

    # ---------- команды ----------
    # У команды нет аргумента-канала: человек ошибается в списке, а панель потом лежит
    # неизвестно где. Канал выбирается списком на самой панели — там же, где видно итог.
    @commands.slash_command(name="music_panel",
                            description="Панель музыки: в настроенный канал или в этот",
                            default_member_permissions=disnake.Permissions(manage_guild=True))
    async def music_panel(self, inter: disnake.ApplicationCommandInteraction):
        cfg = _cfg(inter.guild.id)
        ch = self.panel_channel(inter.guild) or inter.channel
        await self._drop_old_panel(inter.guild, cfg)
        err = _cfg_save(inter.guild.id, {"panel": ch.id})
        if err:
            return await inter.response.send_message(
                embed=_err(self.bot, "НЕ СОХРАНИЛОСЬ", err), ephemeral=True)
        emb = self._panel_embed(inter.guild, ch)
        msg = await ch.send(embed=emb, view=MusicPanelView(self.bot),
                            file=_panel_banner(emb))
        _cfg_save(inter.guild.id, {"panel_msg": msg.id})
        self.get_player(inter.guild).text_channel = ch
        await inter.response.send_message(
            embed=_ok(self.bot, "ПАНЕЛЬ ЛЕГЛА",
                      _T(self.bot, "music_panel_done", T_PANEL_DONE, ch=ch.mention)),
            ephemeral=True)

    async def refresh_panel(self, inter) -> None:
        """«Обновить панель»: перерисовать экран, а заодно признать его настоящей панелью.

        Экраны, выложенные до настройки канала, в конфиге не значатся — без этого шага
        такой старый клон висел бы в чате вторым и путал людей.
        """
        emb = self._panel_embed(inter.guild, inter.channel)
        file = _panel_banner(emb)
        await inter.response.edit_message(embed=emb, view=MusicPanelView(inter.bot),
                                          file=file)
        cfg = _cfg(inter.guild.id)
        if cfg["panel"] in (0, inter.channel.id):
            _cfg_save(inter.guild.id, {"panel": inter.channel.id,
                                       "panel_msg": inter.message.id})
            self.get_player(inter.guild).text_channel = inter.channel

    async def _drop_old_panel(self, guild, cfg: dict) -> None:
        """Убрать свою прошлую панель: два экрана с кнопками в чате — люди жмут не туда."""
        if not (cfg["panel_msg"] and cfg["panel"]):
            return
        old = guild.get_channel(cfg["panel"])
        if old is None:
            return
        try:
            msg = await old.fetch_message(cfg["panel_msg"])
        except Exception:
            return
        ids = {c.custom_id for row in msg.components for c in row.children}
        if msg.author == self.bot.user and "music:play" in ids:
            try:
                await msg.delete()
            except Exception:
                pass

    def _now_line(self, guild) -> str:
        """Живая строка панели: что играет сейчас. Обновляется кнопкой, а не циклом."""
        vc = guild.voice_client if guild else None
        if not vc or not vc.is_connected():
            return _T(self.bot, "music_now_idle", T_NOW_IDLE)
        p = self.players.get(guild.id)
        cur = (p.current if p else None) or {}
        vol = int(round((p.volume if p else _cfg(guild.id)["volume"] / 100.0) * 100))
        state = (_T(self.bot, "music_state_pause", T_STATE_PAUSE) if vc.is_paused() else
                 _T(self.bot, "music_state_play", T_STATE_PLAY) if vc.is_playing() else
                 _T(self.bot, "music_state_quiet", T_STATE_QUIET))
        title = str(cur.get("title") or _T(self.bot, "music_now_empty", T_NOW_EMPTY))[:70]
        line = _T(self.bot, "music_now_line", T_NOW_LINE,
                  q=len(p.queue) if p else 0, dj="вкл" if (p and p.dj) else "выкл",
                  vol=vol, bar=bar(vol, 100, width=6))
        return f"{state}  ·  **{title}**\n{line}"

    def _bind_line(self, guild, where=None) -> str:
        """Строка «канал музыки» на панели: куда уходят заказы и анонсы.

        Честный ответ нужен, когда панель осталась в одном чате, а канал выбрали другой:
        иначе человек ищет анонс не там, где он лежит.
        """
        cid = _cfg(guild.id)["panel"] if guild else 0
        ch = guild.get_channel(cid) if (guild and cid) else None
        if ch is None:
            return _T(self.bot, "music_bind_none", T_BIND_NONE)
        if where is not None and getattr(where, "id", cid) != cid:
            return _T(self.bot, "music_bind_other", T_BIND_OTHER, ch=ch.mention)
        return _T(self.bot, "music_bind_here", T_BIND_HERE, ch=ch.mention)

    def _panel_embed(self, guild, where=None) -> disnake.Embed:
        order = _T(self.bot, "music_order_text", T_ORDER_TEXT)
        site_key, site_default = (("music_order_site_btn", T_ORDER_SITE_BTN) if _site_url()
                                  else ("music_order_site_menu", T_ORDER_SITE_MENU))
        sections = [
            (_T(self.bot, "music_now_head", T_NOW_HEAD), self._now_line(guild)),
            (_T(self.bot, "music_order_head", T_ORDER_HEAD),
             f"{order}\n{_T(self.bot, site_key, site_default)}"),
            (_T(self.bot, "music_files_head", T_FILES_HEAD),
             _T(self.bot, "music_files_text", T_FILES_TEXT,
                n=MAX_FILES_IN_MSG, mb=MAX_TRACK_MB)),
            (_T(self.bot, "music_ctrl_head", T_CTRL_HEAD),
             _T(self.bot, "music_ctrl_text", T_CTRL_TEXT)),
            (_T(self.bot, "music_bind_head", T_BIND_HEAD), self._bind_line(guild, where)),
        ]
        return panel_embed(_T(self.bot, "music_panel_title", T_PANEL_TITLE), sections,
                           color_key="music_color", guild=guild,
                           intro=_T(self.bot, "music_panel_intro", T_PANEL_INTRO))

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
