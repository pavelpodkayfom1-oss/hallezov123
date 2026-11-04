"""Хранилище звуков саундборда: один код для бота в Discord и для веб-панели.

Раньше всё жило в коге: список файлов, проверка ссылки, скачивание. Сайт не мог
это переиспользовать, потому что ког тянет за собой disnake. Здесь только файлы и
метаданные — никаких Discord-объектов, поэтому панель вызывает эти же функции и
никто не начинает расходиться в двух местах.

Где лежат звуки:
 • `sounds/`     — готовый пак, он в git, при деплое перезаписывается;
 • `media/sounds/` — то, что добавили руками (из Discord или с сайта). Рядом с
   main.py, в git не попадает, поэтому переживает и деплой, и рестарт.
Звуки с тем же именем из `media/` перекрывают пак: так можно «починить» готовый
звук, не трогая репозиторий.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import socket
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

SOUNDS_DIR = Path("sounds")
LOCAL_DIR = Path("media") / "sounds"
META_FILE = Path("sounds_meta.json")

EXTS = {".mp3", ".wav", ".ogg", ".m4a"}
MAX_BYTES = 2 * 1024 * 1024        # 2 МБ на звук: хостинг даёт 256 МБ на всё
MAX_SOUNDS = 23                    # Discord даёт 5 рядов по 5 кнопок: 25 минус «Стоп» и «Обновить»
NAME_RE = re.compile(r"^[a-zа-яё0-9_\-]{1,20}$")

# знаки для кнопок и карточек: обычные unicode-эмодзи, они видны и в Discord, и на
# сайте. Свои эмодзи сервера в кнопке работают только там, где они есть, — на чужом
# сервере кнопка с таким знаком просто не отрисуется.
EMOJIS = ('🔊', '💥', '📯', '🎺', '🎸', '🥁', '🚨', '🔫', '🎉', '⚡',
          '🐐', '💀', '🤡', '👑', '🔥', '🌊', '🎯', '🛎', '📢', '🔔',
          '👀', '🧠', '💎', '🍷', '🐓', '🐄', '📞', '🦗', '😱', '😂')
COLORS = ("#e5484d", "#f76b15", "#ffc53d", "#46a758", "#00b2ff", "#5b5bd6",
          "#e93d82", "#8e4ec6", "#00c38a", "#a08c5a", "#8892a0", "#f2f5f7")

CTYPE_EXT = {"audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/ogg": ".ogg",
             "application/ogg": ".ogg", "audio/wav": ".wav", "audio/x-wav": ".wav",
             "audio/mp4": ".m4a", "audio/x-m4a": ".m4a"}
AUDIO_LINK_RE = re.compile(r"""(?:https://[^"'\s<>]+?\.mp3|/media/[^"'\s<>]+?\.mp3)""")

# угадываем знак по имени: так пак выглядит собраным, пока никто ничего не настроил
_GUESS = (("airhorn", '📯'), ("boom", '💥'), ("bass", '🥁'), ("siren", '🚨'), ("laser", '🔫'), ("rim", '🎯'), ("sad", '😢'),
          ("level", '🎉'), ("cricket", '🦗'), ("horn", '🎺'), ("drum", '🥁'), ("drop", '💥'), ("win", '🏆'), ("fail", '💀'),)


def _clean(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip().lower()


def norm(name: str) -> str:
    """Имя звука как ключ: сайт и бот должны вычислять его одинаково."""
    return _clean(name)


def title_of(stem: str) -> str:
    """«sad_trombone» → «Sad trombone»: имя файла как заголовок, без подчёркиваний."""
    return (_clean(stem).replace("_", " ").replace("-", " ")[:24] or "звук").capitalize()


def guess_emoji(stem: str) -> str:
    low = _clean(stem)
    for key, mark in _GUESS:
        if key in low:
            return mark
    return "🔊"


def pick_color(stem: str) -> str:
    h = sum(ord(c) for c in _clean(stem))
    return COLORS[h % len(COLORS)]


# ═════════ метаданные ═════════
def _raw_meta() -> dict:
    try:
        data = json.loads(META_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def meta(stem: str = "") -> dict:
    """Настройка одного звука или вся таблица: {имя: {title, emoji, color, off}}."""
    raw = _raw_meta()
    out = {}
    for key, val in raw.items():
        if isinstance(val, dict):
            out[_clean(key)] = {"title": str(val.get("title") or "")[:24],
                                "emoji": str(val.get("emoji") or "")[:8],
                                "color": str(val.get("color") or "")[:9],
                                "off": bool(val.get("off"))}
    if stem:
        one = out.get(_clean(stem), {})
        return {"title": one.get("title") or title_of(stem),
                "emoji": one.get("emoji") or guess_emoji(stem),
                "color": one.get("color") or pick_color(stem),
                "off": bool(one.get("off"))}
    return out


def _save_meta(table: dict) -> None:
    tmp = META_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, META_FILE)


def set_meta(stem: str, title: str = "", emoji: str = "", color: str = "",
             off: Optional[bool] = None) -> Tuple[bool, str]:
    """Ставит заголовку, знаку, цвету или скрытости один звук; пустое — «как было»."""
    key = _clean(stem)
    if not key:
        return False, "какой звук?"
    if title:
        title = re.sub(r"\s+", " ", str(title)).strip()
        if not (1 <= len(title) <= 24):
            return False, "заголовок от 1 до 24 знаков"
    if emoji and emoji not in EMOJIS:
        return False, "такого знака в списке нет"
    if color and not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        return False, "цвет — это #rrggbb"
    table = _raw_meta()
    one = table.get(key) if isinstance(table.get(key), dict) else {}
    table[key] = {"title": title or str(one.get("title") or ""),
                  "emoji": emoji or str(one.get("emoji") or ""),
                  "color": (color or str(one.get("color") or "")).lower(),
                  "off": bool(one.get("off")) if off is None else bool(off)}
    _save_meta(table)
    return True, ""


# ═════════ список ═════════
def files() -> Dict[str, Path]:
    """Звуки, которые играются: свои из `media/sounds/` перекрывают пак из `sounds/`.

    Спрятанные в списке нет. Паковый файл нельзя удалить по-хорошему: `git pull`
    на хостинге вернёт его обратно, и человек решит, что кнопка не сработала.
    Поэтому пак прячется меткой, а свой файл удаляется по-настоящему.
    """
    hidden = {k for k, v in meta().items() if v.get("off")}
    got: Dict[str, Path] = {}
    for folder in (LOCAL_DIR, SOUNDS_DIR):
        try:
            rows = [p for p in folder.iterdir()
                    if p.is_file() and p.suffix.lower() in EXTS and not p.name.startswith((".", "_"))]
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            continue
        for p in sorted(rows, key=lambda p: p.stem):
            if p.stem not in hidden:
                got.setdefault(p.stem, p)
    return {k: got[k] for k in sorted(got)}


def _seconds(ext: str, size: int) -> float:
    """Приблизительная длина по весу: считать её ffmpeg'ом на каждый показ — дорого."""
    per = {".mp3": 16000, ".m4a": 16000, ".ogg": 14000, ".wav": 176400}.get(ext)
    return round(size / per, 1) if per and size else 0.0


def items() -> List[dict]:
    """Карточки для сайта и кнопки для Discord — один и тот же порядок."""
    table = meta()
    out = []
    for stem, path in files().items():
        one = table.get(stem, {})
        try:
            size, stamp = path.stat().st_size, path.stat().st_mtime
        except OSError:
            size, stamp = 0, 0
        ext = path.suffix.lower()
        out.append({
            "name": stem,
            "title": (one.get("title") or title_of(stem))[:24],
            "emoji": one.get("emoji") or guess_emoji(stem),
            "color": one.get("color") or pick_color(stem),
            "ext": ext.lstrip("."),
            "bytes": size,
            "seconds": _seconds(ext, size),
            "local": str(path).startswith(str(LOCAL_DIR)),
            "mtime": int(stamp),
        })
    return out


def names() -> List[str]:
    return [i["name"] for i in items()]


# ═════════ добавить / убрать ═════════
def _check_name(stem: str, fresh: bool) -> str:
    key = _clean(stem)
    if not NAME_RE.match(key or ""):
        return "Имя: только буквы, цифры, - и _, до 20 знаков."
    if fresh and key not in files() and len(files()) >= MAX_SOUNDS:
        return f"Уже {MAX_SOUNDS} звуков — убери лишние."
    return ""


def save_bytes(stem: str, data: bytes, ext: str = "") -> Tuple[bool, str, dict]:
    """Кладёт байты в `media/sounds/`. Возвращает (получилось, словами, карточку)."""
    key = _clean(stem)
    err = _check_name(key, True)
    if not err and not data:
        err = "Файл пустой."
    if not err and len(data) > MAX_BYTES:
        err = f"Файл больше {MAX_BYTES // (1024 * 1024)} МБ."
    if not err:
        ext = (ext or ".mp3").lower()
        if not ext.startswith("."):
            ext = "." + ext
        if ext not in EXTS:
            err = "Формат нужен mp3, wav, ogg или m4a."
    if err:
        return False, err, {}
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    old = files().get(key)
    if old and old.suffix.lower() != ext:
        old.unlink(missing_ok=True)
    path = LOCAL_DIR / f"{key}{ext}"
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    if meta(key)["off"]:
        set_meta(key, off=False)      # новый файл с тем же именем — снова играется
    return True, "", next((i for i in items() if i["name"] == key), {})


def remove(stem: str) -> Tuple[bool, str]:
    """Убирает звук из панели. Своё удаляет с диска, пак только прячет."""
    key = _clean(stem)
    path = path_of(key)
    if path is None:
        return False, "Такого звука нет."
    if str(path).startswith(str(LOCAL_DIR)):
        try:
            path.unlink()
        except OSError as e:
            return False, f"Файл не отдаётся: {e.__class__.__name__}"
        table = _raw_meta()
        if table.pop(key, None) is not None:
            _save_meta(table)
        return True, ""
    ok, err = set_meta(key, off=True)
    return ok, "" if ok else err


def restore(stem: str) -> Tuple[bool, str]:
    """Возвращает спрятанный звук пака — ничего скачивать заново не надо."""
    key = _clean(stem)
    if key in files():
        return True, ""
    if not any(p.stem == key for p in _all_paths()):
        return False, "Такого звука нет — возможно, его удалили совсем."
    return set_meta(key, off=False)


def _all_paths() -> List[Path]:
    out: List[Path] = []
    for folder in (LOCAL_DIR, SOUNDS_DIR):
        try:
            out += [p for p in folder.iterdir()
                    if p.is_file() and p.suffix.lower() in EXTS and not p.name.startswith((".", "_"))]
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            continue
    return out


def hidden() -> List[dict]:
    """Спрятанные звуки пака: сайт показывает их списком «вернуть»."""
    table = meta()
    back = []
    for p in _all_paths():
        one = table.get(p.stem, {})
        if one.get("off"):
            back.append({"name": p.stem, "title": one.get("title") or title_of(p.stem),
                         "emoji": one.get("emoji") or guess_emoji(p.stem),
                         "local": str(p).startswith(str(LOCAL_DIR))})
    return back


# ═════════ скачивание по ссылке ═════════
async def _public_host(host: str) -> bool:
    """Только обычные сайты из интернета: свою сеть и localhost бот не открывает."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except Exception:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return bool(infos)


async def _fetch(sess, url: str, limit: int) -> Tuple[bytes, str, str]:
    """(данные, content-type, ошибка). Идёт по перенаправлениям и проверяет каждый адрес."""
    for _ in range(4):
        u = urlparse(url)
        if u.scheme != "https" or not u.hostname or not await _public_host(u.hostname):
            return b"", "", "ссылка должна начинаться с https:// и вести на обычный сайт"
        async with sess.get(url, allow_redirects=False, headers={"User-Agent": "Mozilla/5.0"}) as r:
            if r.status in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                url = urljoin(url, r.headers["Location"])
                continue
            if r.status != 200:
                return b"", "", f"сайт ответил кодом {r.status}"
            data = bytearray()
            async for chunk in r.content.iter_chunked(65536):
                data += chunk
                if len(data) > limit:
                    return b"", "", "файл слишком большой"
            return bytes(data), r.headers.get("Content-Type", "").split(";")[0].strip().lower(), ""
    return b"", "", "слишком много перенаправлений"


async def fetch_audio(url: str) -> Tuple[bytes, str, str]:
    """Находит звук по ссылке: прямой файл или страница вроде myinstants."""
    import aiohttp
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as sess:
            data, ctype, err = await _fetch(sess, url, 3 * 1024 * 1024)
            if not err and ctype.startswith("text/html"):
                m = AUDIO_LINK_RE.search(data.decode("utf-8", "ignore"))
                if not m:
                    return b"", "", "на странице не нашёл mp3 — дай прямую ссылку на файл"
                data, ctype, err = await _fetch(sess, urljoin(url, m.group(0)), MAX_BYTES + 1)
    except Exception as e:
        return b"", "", f"не получилось скачать ({e.__class__.__name__})"
    if err:
        return b"", "", err
    ext = CTYPE_EXT.get(ctype) or Path(urlparse(url).path).suffix.lower()
    if ext not in EXTS:
        return b"", "", "это не звуковой файл (нужен mp3, wav, ogg)"
    if not data:
        return b"", "", "файл пустой"
    if len(data) > MAX_BYTES:
        return b"", "", f"файл больше {MAX_BYTES // (1024 * 1024)} МБ"
    return data, ext, ""


async def add_from_url(stem: str, url: str) -> Tuple[bool, str, dict]:
    data, ext, err = await fetch_audio((url or "").strip())
    if err:
        return False, err, {}
    return save_bytes(stem, data, ext)


# ═════════ для сайта ═════════
def catalog() -> dict:
    """Один ответ и для страницы, и для кога: список, лимиты, чем можно дополнить."""
    got = items()
    free = max(0, MAX_SOUNDS - len(got))
    return {"sounds": got,
            "total": len(got),
            "max": MAX_SOUNDS,
            "free": free,
            "max_mb": MAX_BYTES // (1024 * 1024),
            "emojis": list(EMOJIS),
            "colors": list(COLORS),
            "exts": sorted(e.lstrip(".") for e in EXTS),
            "hidden": hidden(),
            "dir": str(LOCAL_DIR)}


def path_of(stem: str) -> Optional[Path]:
    """Файл звука — нужен сайту, чтобы отдать его на воспроизведение."""
    return files().get(_clean(stem))
