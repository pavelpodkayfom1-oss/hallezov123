"""Звуки саундборда для веб-панели: список, подписи, загрузка, скачивание, прослушать.

Хранилище общее (utils/sound_store.py), поэтому сайт и Discord видят один и тот же
набор и не могут разойтись. После любой правки просим ког «Soundboard» перекрасить
панель в канале: иначе звук появится на сайте, а кнопка в Discord — только после
заново выложенного /sounds_panel, и это выглядит как «сайт не сохранил».
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote

from utils import sound_store as store

MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".m4a": "audio/mp4"}
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)", re.I)


def rows_of(sounds: List[dict]) -> List[dict]:
    out = []
    for s in sounds:
        one = dict(s)
        one["kb"] = max(1, round(int(s.get("bytes") or 0) / 1024))
        one["url"] = "/api/sounds/file?name=" + quote(str(s.get("name") or ""))
        out.append(one)
    return out


def catalog() -> Dict[str, Any]:
    """Всё, что нужно странице: список, лимиты, палитра и спрятанные звуки."""
    cat = store.catalog()
    return {"rows": rows_of(cat["sounds"]), "hidden": cat["hidden"], "total": cat["total"],
            "max": cat["max"], "free": cat["free"], "max_mb": cat["max_mb"],
            "emojis": cat["emojis"], "colors": cat["colors"], "exts": cat["exts"], "dir": cat["dir"]}


def card_of(name: str) -> dict:
    key = store.norm(name)
    return next((i for i in store.items() if i["name"] == key), {"name": key, "title": key})


async def sync(bot) -> str:
    """Перекрашивает панели в Discord и возвращает то, что увидит человек."""
    cog = bot.get_cog("Soundboard") if bot is not None else None
    if cog is None:
        return "Ког «Soundboard» выключен — панель в Discord не обновилась"
    try:
        done = await cog.sync_panels()
    except Exception as e:
        return f"Панель в Discord не обновилась ({e.__class__.__name__})"
    if not done:
        return "Панели в Discord нет — выложи её через /sounds_panel"
    return f"Панель в Discord обновлена ({done})"


async def save(bot, name: str, title: str = "", emoji: str = "", color: str = "") -> Dict[str, Any]:
    """Подпись, знак и цвет одного звука."""
    if store.path_of(name) is None:
        return {"error": "Такого звука нет на диске."}
    ok, err = store.set_meta(name, title=title, emoji=emoji, color=color)
    if not ok:
        return {"error": err}
    card = card_of(name)
    return {"saved": card["name"], "title": card["title"], "panel": await sync(bot),
            "hint": "Кнопка в Discord обновилась."}


async def hide(bot, name: str) -> Dict[str, Any]:
    """Убрать звук: свой файл с диска, звук готового пака — только спрятать."""
    key = store.norm(name)
    ok, err = store.remove(key)
    if not ok:
        return {"error": err}
    return {"removed": key, "panel": await sync(bot),
            "hint": "Свой файл удалён с диска, а звук из готового пака спрятан и ждёт возврата ниже."}


async def restore(bot, name: str) -> Dict[str, Any]:
    key = store.norm(name)
    ok, err = store.restore(key)
    if not ok:
        return {"error": err}
    card = card_of(key)
    return {"saved": card["name"], "title": card["title"], "panel": await sync(bot),
            "hint": "Звук вернулся на панель."}


async def upload(bot, fields: Dict[str, str], files: List[tuple]) -> Dict[str, Any]:
    """Файл из формы: (имя, mime, байты). Кладём как новый звук и сразу подписываем."""
    if not files:
        return {"error": "Не выбран файл со звуком."}
    if len(files) > 1:
        return {"error": "За один раз добавляется один звук."}
    filename, _mime, data = files[0]
    name = str(fields.get("name") or "").strip() or Path(str(filename)).stem
    ok, err, card = store.save_bytes(name, data, Path(str(filename)).suffix.lower())
    if not ok:
        return {"error": err}
    key = str(card.get("name") or store.norm(name))
    store.set_meta(key, title=str(fields.get("title") or ""), emoji=str(fields.get("emoji") or ""))
    return {"saved": key, "title": card_of(key)["title"], "panel": await sync(bot),
            "hint": f"Звуков на панели: {len(store.names())} из {store.MAX_SOUNDS}."}


async def from_url(bot, name: str, url: str, title: str = "") -> Dict[str, Any]:
    """Звук по ссылке: прямой файл или страница вроде myinstants."""
    if not (url or "").strip():
        return {"error": "Вставь ссылку на звук."}
    ok, err, card = await store.add_from_url(name, url)
    if not ok:
        return {"error": err}
    key = str(card.get("name") or store.norm(name))
    if (title or "").strip():
        store.set_meta(key, title=title)
    return {"saved": key, "title": card_of(key)["title"], "panel": await sync(bot),
            "hint": "Звук скачан и добавлен на панель."}


def audio(name: str, rng: str = "") -> Dict[str, Any]:
    """Кусок звука для плеера: {body, mime, headers, status} или {error}.

    Range разбираем сами — без 206 браузер не даёт тянуть ползунок перемотки, и
    прослушать звук на середине нельзя. Файл до 2 МБ, поэтому читаем только запрошенный
    кусок: на 256 МБ хостинге отдать целиком три превью подряд уже заметно.
    """
    path = store.path_of(name)
    if path is None:
        return {"error": "Звука нет на диске — возможно, его спрятали."}
    try:
        total = path.stat().st_size
    except OSError as e:
        return {"error": f"Файл не читается: {e.__class__.__name__}"}
    if not total:
        return {"error": "Файл пустой."}
    if total > store.MAX_BYTES:
        return {"error": "Файл больше 2 МБ — панель его не отдаёт."}
    mime = MIME.get(path.suffix.lower(), "application/octet-stream")
    start, end, partial = 0, total - 1, False
    hit = RANGE_RE.search(rng or "")
    if hit:
        head, tail = hit.group(1), hit.group(2)
        if not head and not tail:
            return {"error": "Запрошен кусок за концом файла."}
        try:
            # bytes=-400 значит «последние 400 байт», bytes=900- значит «хвост с 900-го»
            start = int(head) if head else max(0, total - int(tail))
            end = int(tail) if (head and tail) else total - 1
        except ValueError:
            return {"error": "Непонятный Range в запросе."}
        if start >= total or start > end:
            return {"error": "Запрошен кусок за концом файла."}
        end = min(end, total - 1)
        partial = True
    try:
        with path.open("rb") as f:
            f.seek(start)
            body = f.read(end - start + 1)
    except OSError as e:
        return {"error": f"Файл не читается: {e.__class__.__name__}"}
    headers = {"Accept-Ranges": "bytes"}
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    return {"body": body, "mime": mime, "headers": headers, "status": 206 if partial else 200}
