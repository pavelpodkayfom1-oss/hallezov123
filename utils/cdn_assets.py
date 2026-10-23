"""
Картинки-ассеты на CDN Discord: файл загружается один раз, дальше только ссылка на URL.

Каждое сообщение с вложением — это ещё сотни килобайт исходящего трафика контейнера, а на
бесплатном лимите в 1 ГБ в сутки это заметно (баннер уезжает в каждое ЛС кандидату).
Загруженный раз файл лежит на cdn.discordapp.com, и Discord сам раздаёт его клиентам.

Служебное сообщение с картинкой удалять нельзя: Discord забирает файл вместе с сообщением,
и эмбеды останутся без картинки (сам кэш при этом продолжит отдавать битый URL —
поэтому ensure() перечитывает URL, если файл поменялся).
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


def _load() -> dict:
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(cache: dict) -> None:
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    tmp.replace(CACHE_PATH)


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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


async def ensure(channel, name: str, path) -> Optional[str]:
    """Возвращает URL картинки, при необходимости загрузив её в канал один раз."""
    p = Path(path)
    url = cached_url(name, p)
    if url or channel is None or not p.exists():
        return url
    try:
        msg = await channel.send(content=NOTE, file=disnake.File(str(p), filename=name))
        if not msg.attachments:
            return None
        url = msg.attachments[0].url
        cache = _load()
        cache[name] = {"url": url, "sha1": _sha1(p), "channel_id": channel.id, "message_id": msg.id}
        _save(cache)
        print(f"🌐 На CDN залит ассет {name}", flush=True)
        return url
    except Exception as error:
        print(f"[cdn_assets] не удалось залить {name}: {error!r}", file=sys.stderr, flush=True)
        return None
