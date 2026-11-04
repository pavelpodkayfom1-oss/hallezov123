"""Звуки саундборда для веб-панели: список, подписи, загрузка, скачивание, прослушать.

Хранилище общее (utils/sound_store.py), поэтому сайт и Discord видят один и тот же
набор и не могут разойтись. После любой правки просим ког «Soundboard» перекрасить
панель в канале: иначе звук появится на сайте, а кнопка в Discord — только после
заново выложенного /sounds_panel, и это выглядит как «сайт не сохранил».

Прослушать в браузере и сыграть в голосовой канал — разные вещи: на сайте нет
человека, чей канал можно взять, поэтому адресат выбирается явно (канал из списка)
или по «там, где я» — канал владельца сервера.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from utils import sound_store as store

MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".m4a": "audio/mp4"}
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)", re.I)
AUTO = "auto"              # играть туда, где сидит тот, за кем следим (владелец сервера)
PEOPLE = 6                 # сколько имён показать у канала, чтобы список не распух


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


# ---------- голосовой канал: куда играть ----------


def _guild(bot):
    from utils import web_tools

    return web_tools._voice_guild(bot)


def follow_of(guild):
    """За кем следим: владелец сервера, а если его в голосе нет — первый админ из config.json.

    У панели один пароль, поэтому «кто нажал» определить нечем. Молча взять первый
    попавшийся канал хуже: звук уйдёт туда, где человека нет, и это будет выглядеть
    как сломанная кнопка.
    """
    ids: List[int] = []
    owner = int(getattr(guild, "owner_id", 0) or 0)
    if owner:
        ids.append(owner)
    try:
        from utils.checks import load_config

        ids += [int(x) for x in (load_config().get("admin_user_ids") or [])]
    except Exception:
        pass
    first = None
    for uid in ids:
        member = guild.get_member(uid)
        if member is None:
            continue
        if first is None:
            first = member
        ch = getattr(getattr(member, "voice", None), "channel", None)
        if ch is not None:
            return member, ch
    return first, None


def _channel_of(vc):
    try:
        return vc.channel if vc is not None else None
    except Exception:
        return None


def voice_state(bot) -> Dict[str, Any]:
    """Всё, что нужно полосе над звуками: где бот, кто в каналах, можно ли играть."""
    guild = _guild(bot)
    if guild is None:
        return {"error": "Бот ни на одном сервере"}
    cog = bot.get_cog("Soundboard")
    if cog is None:
        return {"error": "Ког «Soundboard» выключен — играть звуки нечем"}
    from utils.ffmpeg_path import ffmpeg_exe

    try:
        vc = guild.voice_client
    except Exception:
        vc = None
    here = _channel_of(vc)
    rows = []
    for ch in sorted(getattr(guild, "voice_channels", None) or [],
                     key=lambda c: (int(getattr(c, "position", 0) or 0), str(c.name))):
        try:
            people = [m.display_name for m in list(ch.members)[:PEOPLE]]
            count = len(list(ch.members))
        except Exception:
            people, count = [], 0
        rows.append({"id": str(ch.id), "name": ch.name, "people": people, "count": count,
                     "limit": int(getattr(ch, "user_limit", 0) or 0),
                     "here": here is not None and here.id == ch.id})
    who, mine = follow_of(guild)
    try:
        music = bool(vc is not None and vc.is_playing() and cog.now_playing.get(guild.id) is None)
    except Exception:
        music = False
    return {"guild": guild.name, "channels": rows,
            "bot": str(here.name) if here is not None else "",
            "bot_id": str(here.id) if here is not None else "",
            "follow": {"name": who.display_name if who is not None else "",
                       "channel": mine.name if mine is not None else "",
                       "id": str(mine.id) if mine is not None else ""},
            "now": str(cog.now_name.get(guild.id) or ""), "music": music,
            "ffmpeg": bool(ffmpeg_exe())}


async def resolve_channel(guild, choice: Any) -> Tuple[Any, str]:
    """Канал, куда играть: выбранный из списка, «там, где я» или где бот уже сидит."""
    text = str(choice or "").strip()
    if text == AUTO:
        _who, ch = follow_of(guild)
        if ch is None:
            return None, "Тебя нет в голосовом канале — зайди в канал и нажми ещё раз."
        return ch, ""
    if text.isdigit():
        ch = guild.get_channel(int(text))
        if ch is None:
            return None, f"Канала {text} бот не видит — обнови список"
        if getattr(ch, "connect", None) is None:
            return None, "Это не голосовой канал"
        return ch, ""
    try:
        vc = guild.voice_client
    except Exception:
        vc = None
    ch = _channel_of(vc)
    if ch is None:
        return None, "Бот ни в каком канале не сидит — выбери канал из списка."
    return ch, ""


async def play(bot, name: str, choice: Any = "") -> Dict[str, Any]:
    """Сыграть звук в голосовом канале по заказу с сайта."""
    cog = bot.get_cog("Soundboard")
    if cog is None:
        return {"error": "Ког «Soundboard» выключен — играть звуки нечем"}
    guild = _guild(bot)
    if guild is None:
        return {"error": "Бот ни на одном сервере"}
    ch, err = await resolve_channel(guild, choice)
    if err:
        return {"error": err}
    res = await cog.web_play(guild, name, ch)
    res["voice"] = voice_state(bot)
    return res


async def stop(bot, choice: Any = "") -> Dict[str, Any]:
    cog = bot.get_cog("Soundboard")
    if cog is None:
        return {"error": "Ког «Soundboard» выключен"}
    guild = _guild(bot)
    if guild is None:
        return {"error": "Бот ни на одном сервере"}
    res = await cog.web_stop(guild)
    res["voice"] = voice_state(bot)
    return res
