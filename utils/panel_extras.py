"""Заметки штаба и планировщик сообщений для веб-панели.

Всё хранится в одном файле panel_data.json рядом с ботом. Файл пишется целиком и
атомарно, поэтому при обрыве питания он не остаётся наполовину записанным.
Модуль не зависит от disnake — его можно проверять отдельно.
"""
import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List

PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "panel_data.json")
_lock = threading.Lock()

NOTE_MAX = 2000
NOTES_LIMIT = 200
NOTE_COLORS = ("", "white", "gray", "ok", "warn", "bad")
SCHED_LIMIT = 100
SCHED_TEXT_MAX = 3900
MAX_AHEAD = 366 * 24 * 3600
REPEAT_STEP = {"daily": 86400, "weekly": 7 * 86400}
KEEP_FINISHED = 30


def _load() -> Dict[str, Any]:
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("notes"), list):
        data["notes"] = []
    if not isinstance(data.get("schedule"), list):
        data["schedule"] = []
    return data


def _save(data: Dict[str, Any]) -> None:
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PATH)


# ───────────── заметки ─────────────
def notes_list() -> List[Dict[str, Any]]:
    with _lock:
        notes = _load()["notes"]
    return sorted(notes, key=lambda n: (not n.get("pinned"), -float(n.get("ts", 0))))


def note_add(text: Any, color: Any = "", pinned: Any = False) -> Dict[str, Any]:
    text = str(text or "").strip()
    if not text:
        return {"error": "Пустая заметка — писать нечего"}
    if len(text) > NOTE_MAX:
        return {"error": f"Слишком длинно: {len(text)} знаков, максимум {NOTE_MAX}"}
    color = color if color in NOTE_COLORS else ""
    with _lock:
        data = _load()
        if len(data["notes"]) >= NOTES_LIMIT:
            return {"error": f"Заметок уже {NOTES_LIMIT} — удалите ненужные"}
        note = {"id": uuid.uuid4().hex[:10], "text": text, "color": color,
                "pinned": bool(pinned), "ts": int(time.time())}
        data["notes"].append(note)
        _save(data)
    return {"ok": True, "note": note}


def note_update(nid: Any, fields: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        data = _load()
        note = next((n for n in data["notes"] if n.get("id") == str(nid)), None)
        if note is None:
            return {"error": "Такой заметки уже нет — обновите страницу"}
        if "text" in fields:
            text = str(fields["text"] or "").strip()
            if not text:
                return {"error": "Пустая заметка — писать нечего"}
            if len(text) > NOTE_MAX:
                return {"error": f"Слишком длинно: {len(text)} знаков, максимум {NOTE_MAX}"}
            note["text"] = text
        if "color" in fields and fields["color"] in NOTE_COLORS:
            note["color"] = fields["color"]
        if "pinned" in fields:
            note["pinned"] = bool(fields["pinned"])
        _save(data)
    return {"ok": True, "note": note}


def note_delete(nid: Any) -> Dict[str, Any]:
    with _lock:
        data = _load()
        before = len(data["notes"])
        data["notes"] = [n for n in data["notes"] if n.get("id") != str(nid)]
        if len(data["notes"]) == before:
            return {"error": "Такой заметки уже нет — обновите страницу"}
        _save(data)
    return {"ok": True}


# ───────────── планировщик ─────────────
def sched_list() -> List[Dict[str, Any]]:
    with _lock:
        items = _load()["schedule"]
    wait = sorted((i for i in items if i.get("status") == "wait"), key=lambda i: i.get("at", 0))
    done = sorted((i for i in items if i.get("status") != "wait"),
                  key=lambda i: -float(i.get("done_at", 0)))
    return wait + done


def sched_add(body: Dict[str, Any]) -> Dict[str, Any]:
    text = str(body.get("text") or "").strip()
    if not text:
        return {"error": "Пустое сообщение — писать нечего"}
    if len(text) > SCHED_TEXT_MAX:
        return {"error": f"Слишком длинно: {len(text)} знаков, максимум {SCHED_TEXT_MAX}"}
    channel = str(body.get("channel") or "")
    if not channel.isdigit():
        return {"error": "Не выбран канал"}
    try:
        at = int(float(body.get("at")))
    except (TypeError, ValueError):
        return {"error": "Не указано время отправки"}
    now = time.time()
    if at < now + 20:
        return {"error": "Время уже прошло — выберите момент хотя бы на минуту вперёд"}
    if at > now + MAX_AHEAD:
        return {"error": "Дальше чем на год вперёд планировать нельзя"}
    repeat = str(body.get("repeat") or "")
    if repeat and repeat not in REPEAT_STEP:
        return {"error": "Неизвестное повторение"}
    item = {"id": uuid.uuid4().hex[:10], "channel": channel,
            "channel_name": str(body.get("channel_name") or "")[:100],
            "text": text, "title": str(body.get("title") or "")[:256],
            "color": str(body.get("color") or "")[:16], "ping": bool(body.get("ping")),
            "at": at, "repeat": repeat, "status": "wait", "created": int(now)}
    with _lock:
        data = _load()
        if sum(1 for i in data["schedule"] if i.get("status") == "wait") >= SCHED_LIMIT:
            return {"error": f"В очереди уже {SCHED_LIMIT} сообщений — дождитесь отправки"}
        data["schedule"].append(item)
        _save(data)
    return {"ok": True, "item": item}


def sched_cancel(sid: Any) -> Dict[str, Any]:
    with _lock:
        data = _load()
        before = len(data["schedule"])
        data["schedule"] = [i for i in data["schedule"] if i.get("id") != str(sid)]
        if len(data["schedule"]) == before:
            return {"error": "Такого сообщения уже нет — обновите страницу"}
        _save(data)
    return {"ok": True}


def sched_due(now: float = None) -> List[Dict[str, Any]]:
    now = time.time() if now is None else now
    with _lock:
        return [dict(i) for i in _load()["schedule"]
                if i.get("status") == "wait" and float(i.get("at", 0)) <= now]


def sched_mark(sid: str, result: Dict[str, Any], now: float = None) -> None:
    """Записывает итог отправки. Повторяющееся уходит на следующий срок, остальное — в историю."""
    now = time.time() if now is None else now
    with _lock:
        data = _load()
        item = next((i for i in data["schedule"] if i.get("id") == sid), None)
        if item is None:        # отменили, пока шла отправка
            return
        err = str(result.get("error") or "")
        item["last_error"] = err
        item["last_link"] = str(result.get("link") or "")
        item["last_run"] = int(now)
        step = REPEAT_STEP.get(item.get("repeat") or "")
        if step:
            at = float(item["at"])
            while at <= now:
                at += step
            item["at"] = int(at)
        else:
            item["status"] = "fail" if err else "sent"
            item["done_at"] = int(now)
        finished = [i for i in data["schedule"] if i.get("status") != "wait"]
        if len(finished) > KEEP_FINISHED:
            drop = {i["id"] for i in sorted(finished, key=lambda i: float(i.get("done_at", 0)))
                    [:len(finished) - KEEP_FINISHED]}
            data["schedule"] = [i for i in data["schedule"] if i.get("id") not in drop]
        _save(data)
