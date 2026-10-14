"""
Учёт отметок «Буду / Опоздаю / Не смогу» для сборов /event.

Состояние хранится в файле event_votes.json (message_id -> списки), поэтому
переживает перезапуск бота и не упирается в лимит 1024 символа поля embed.
Для старых сообщений, которых нет в файле, список берётся из самого embed.
Модуль не зависит от disnake, чтобы его можно было тестировать отдельно.
"""
import json
import os
import re
import time
from typing import Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(BASE_DIR, "event_votes.json")
ACTIONS = ("yes", "late", "no")
KEEP_DAYS = 60
EMPTY_TEXT = "*Пока никто*"

_ID_RE = re.compile(r"<@!?(\d+)>")


def _load() -> dict:
    try:
        with open(STORE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict):
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, STORE)


def parse_ids(value: str) -> List[int]:
    """Достаёт ID из текста поля embed (<@123>, <@!123>), без повторов, по порядку."""
    seen, out = set(), []
    for m in _ID_RE.finditer(value or ""):
        uid = int(m.group(1))
        if uid not in seen:
            seen.add(uid)
            out.append(uid)
    return out


def get_votes(message_id: int, fallback_values: Optional[List[str]] = None) -> Dict[str, List[int]]:
    rec = _load().get(str(message_id))
    if isinstance(rec, dict):
        return {a: [int(x) for x in rec.get(a, [])] for a in ACTIONS}
    if fallback_values:
        votes = {a: [] for a in ACTIONS}
        for a, v in zip(ACTIONS, fallback_values):
            votes[a] = parse_ids(v)
        return votes
    return {a: [] for a in ACTIONS}


def toggle_vote(votes: Dict[str, List[int]], user_id: int, action: str) -> str:
    """Ставит отметку. Нажатие на уже выбранную кнопку снимает отметку.
    Возвращает: 'added' | 'changed' | 'removed'."""
    was = [a for a in ACTIONS if user_id in votes[a]]
    for a in ACTIONS:
        if user_id in votes[a]:
            votes[a].remove(user_id)
    if action in was:
        return "removed"
    votes[action].append(user_id)
    return "changed" if was else "added"


def save_votes(message_id: int, votes: Dict[str, List[int]]):
    data = _load()
    cutoff = time.time() - KEEP_DAYS * 86400
    data = {k: v for k, v in data.items() if isinstance(v, dict) and v.get("ts", 0) >= cutoff}
    data[str(message_id)] = {**{a: votes[a] for a in ACTIONS}, "ts": time.time()}
    _save(data)


def render(ids: List[int], limit: int = 950) -> str:
    """Текст для поля embed (лимит Discord 1024). Если не влезает — «… и ещё N»."""
    if not ids:
        return EMPTY_TEXT
    head = f"**Всего ({len(ids)}):**\n"
    shown, used = [], len(head)
    for i, uid in enumerate(ids):
        piece = f"<@{uid}>"
        extra = len(piece) + (2 if shown else 0)
        tail = f" … и ещё {len(ids) - i}"
        if used + extra + len(tail) > limit and i < len(ids):
            return head + ", ".join(shown) + f" … и ещё {len(ids) - i}"
        shown.append(piece)
        used += extra
    return head + ", ".join(shown)
