"""Музыкальные вкусы участников: что человек сам заказывал у бота.

Ничего не угадывается и не подслушивается: в память попадает только то, что человек
заказал музыкой через бота (эти заказы и так видны в канале: «Заказал(а) @ник»).
Настроение нигде не хранится — ИИ читает его из свежих реплик самого человека в момент
просьбы. Человек может стереть свои данные фразой «забудь мои вкусы».

Файл ai_taste.json лежит рядом с кодом и в .gitignore: репозиторий публичный.
"""
import json
import os
import time
from typing import Dict, List

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(BASE_DIR, "ai_taste.json")
KEEP = 30           # сколько последних заказов помним на человека
TITLE_MAX = 80


def _load() -> Dict[str, dict]:
    try:
        with open(FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: Dict[str, dict]) -> None:
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, FILE)


def record(user_id: int, title: str, query: str = "") -> None:
    """Запомнить заказ. Повтор того же трека поднимается наверх, а не дублируется."""
    title = " ".join(str(title or "").split())[:TITLE_MAX]
    if not title:
        return
    data = _load()
    me = data.setdefault(str(user_id), {"plays": []})
    plays: List[dict] = [p for p in me.get("plays", []) if isinstance(p, dict)
                         and str(p.get("t", "")).lower() != title.lower()]
    plays.append({"t": title, "q": " ".join(str(query or "").split())[:60], "at": int(time.time())})
    me["plays"] = plays[-KEEP:]
    _save(data)


def recent(user_id: int, n: int = 10) -> List[str]:
    plays = (_load().get(str(user_id)) or {}).get("plays", [])
    return [str(p.get("t")) for p in plays[-n:] if isinstance(p, dict) and p.get("t")][::-1]


def forget(user_id: int) -> bool:
    data = _load()
    if str(user_id) in data:
        data.pop(str(user_id), None)
        _save(data)
        return True
    return False


def summary_for(members, n: int = 5, limit: int = 6) -> str:
    """Вкусы тех, кто сидит в одном войсе: по паре последних треков на человека."""
    data = _load()
    rows = []
    for m in members:
        if getattr(m, "bot", False):
            continue
        plays = (data.get(str(m.id)) or {}).get("plays", [])
        titles = [str(p.get("t")) for p in plays[-n:] if isinstance(p, dict) and p.get("t")][::-1]
        if titles:
            rows.append(f"{m.display_name}: " + "; ".join(titles[:3]))
        if len(rows) >= limit:
            break
    return "\n".join(rows)
