"""
Картинки-ассеты на CDN Discord: файл загружается один раз, дальше только ссылка на URL.

Каждое сообщение с вложением — это ещё сотни килобайт исходящего трафика контейнера, а на
бесплатном лимите в 1 ГБ в сутки это заметно (баннер уезжает в каждое ЛС кандидату).
Загруженный раз файл лежит на cdn.discordapp.com, и Discord сам раздаёт его клиентам.

Служебное сообщение с картинкой удалять нельзя: Discord забирает файл вместе с сообщением,
и эмбеды останутся без картинки — причём молча, просто без изображения. Поэтому ensure()
перед тем как отдать URL, проверяет, что сообщение ещё живо, и при необходимости заливает
файл заново (так же, как и когда сам файл ассета изменился).
"""
import hashlib
import json
import sys
from pathlib import Path
from typing import Optional

import disnake

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_PATH = BASE_DIR / "cdn_assets.json"
NOTE = ("📌 Служебные картинки бота. Не удаляй это сообщение — "
        "иначе в ЛС кандидатам и на панелях перестанут грузиться баннеры.")


_LOAD_CACHE: list = []   # [(mtime_ns, size), данные] — файл перечитывается только после записи


def _load() -> dict:
    """Кэш cdn_assets.json в памяти: его спрашивает каждый эмбед с картинкой."""
    try:
        st = CACHE_PATH.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    if _LOAD_CACHE and _LOAD_CACHE[0] == key:
        return _LOAD_CACHE[1]
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        data = data if isinstance(data, dict) else {}
    except Exception:
        return {}
    _LOAD_CACHE.clear()
    _LOAD_CACHE.extend([key, data])
    return data


def _save(cache: dict) -> None:
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    tmp.replace(CACHE_PATH)
    _LOAD_CACHE.clear()


_SHA_CACHE: dict = {}    # путь -> ((mtime_ns, размер), хэш)


def _sha1(path) -> str:
    """Хэш файла по mtime/размеру. Пересчитывать SHA1 баннера на каждый эмбед — значит
    читать сотни килобайт с диска десятки раз за рендер панели."""
    p = Path(path)
    try:
        st = p.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    if key is not None:
        hit = _SHA_CACHE.get(str(p))
        if hit and hit[0] == key:
            return hit[1]
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    if key is not None:
        _SHA_CACHE[str(p)] = (key, digest)
    return digest


def cached_url(name: str, path) -> Optional[str]:
    """Постоянный URL файла, но только если файл с момента загрузки не менялся."""
    p = Path(path)
    try:
        if not p.exists():
            return None
        entry = _load().get(name)
        if not isinstance(entry, dict) or entry.get("sha1") != _sha1(p):
            return None
        return entry.get("url")
    except Exception:
        return None


async def _url_is_alive(channel, name: str) -> bool:
    """Ссылка на CDN жива, только пока живёт служебное сообщение с файлом.

    Discord отдаёт вложенный файл ровно до тех пор, пока существует сообщение, с которым
    его загрузили. Сообщение удалили (или почистили канал) — и все эмбеды с такой ссылкой
    остаются без картинки, молча и без ошибки.
    """
    entry = _load().get(name)
    mid = entry.get("message_id") if isinstance(entry, dict) else 0
    if not mid:
        return False
    try:
        await channel.fetch_message(int(mid))
        return True
    except disnake.NotFound:
        return False
    except disnake.Forbidden:
        return True     # канал недоступен — не трогаем кэш, чтобы не долбить загрузку заново
    except Exception:
        return True


def _drop(name: str) -> None:
    cache = _load()
    if cache.pop(name, None) is not None:
        _save(cache)


async def _delete_stale(entry, channel, name: str) -> None:
    """Убирает старое служебное сообщение, если картинка переехала в другой канал.

    Иначе при смене канала в чате остаётся висеть старое «📌 Служебные картинки бота»,
    которое никто уже не использует.
    """
    try:
        cid = int(entry.get("channel_id") or 0) if isinstance(entry, dict) else 0
        mid = int(entry.get("message_id") or 0) if isinstance(entry, dict) else 0
        if not cid or not mid or cid == channel.id:
            return
        old_ch = channel.guild.get_channel(cid)
        if old_ch is None:
            return
        await (await old_ch.fetch_message(mid)).delete()
        print(f"🌐 Старое служебное сообщение {name} удалено из прежнего канала", flush=True)
    except Exception:
        pass


async def ensure(channel, name: str, path) -> Optional[str]:
    """Возвращает URL картинки, при необходимости загрузив её в канал.

    Проверяет, что старая ссылка ещё работает, и заливает файл заново, если нет.
    """
    p = Path(path)
    old = _load().get(name)
    url = cached_url(name, p)
    if url and channel is not None:
        if await _url_is_alive(channel, name):
            return url
        _drop(name)
        print(f"🌐 Ссылка на CDN для {name} умерла — заливаем картинку заново", flush=True)
    if channel is None or not p.exists():
        return url
    try:
        msg = await channel.send(content=NOTE, file=disnake.File(str(p), filename=name))
        if not msg.attachments:
            return None
        url = msg.attachments[0].url
        cache = _load()
        cache[name] = {"url": url, "sha1": _sha1(p), "channel_id": channel.id, "message_id": msg.id}
        _save(cache)
        await _delete_stale(old, channel, name)
        print(f"🌐 На CDN залит ассет {name}", flush=True)
        return url
    except Exception as error:
        print(f"[cdn_assets] не удалось залить {name}: {error!r}", file=sys.stderr, flush=True)
        return None
