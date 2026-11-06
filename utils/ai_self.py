"""Что бот уже умеет — список, который собирает сам себя из загруженных когов.

Мозг без этого списка на «что ты умеешь», «как подать заявку» или «какие идеи добавить»
льёт общую воду: он не знает, что у него есть казна, жалобы, саундборд и ещё сорок
модулей. Список руками вести бесполезно — через неделю он устареет и начнёт врать,
поэтому берём то, что реально стоит на полке прямо сейчас.
"""
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List

MSK = timezone(timedelta(hours=3))

TTL = 600.0      # пересобирать раз в 10 минут: команды меняются только пулом и рестартом
MAX_CMDS = 12    # команд на один ког: больше всего 9, так что список полный
MAX_SUBS = 6     # подкоманд у одной команды
MAX_TOTAL = 2600 # потолок карточки: дальше модель начинает теряться в списке

RULES = (
    "Как с этим работать: про себя, свои команды и возможности отвечай только по этому "
    "списку. Чего в нём нет — того ты не умеешь, так и скажи и не выдумывай. Спрашивают, "
    "как что-то сделать, — назови команду из списка. Просят идеи, что в тебя добавить, — "
    "предлагай то, чего в списке нет, и опирайся на то, что уже есть. Не извиняйся общими "
    "фразами вроде «если я тебя не понял»: переспроси одно конкретное слово."
)

_cache: Dict[str, object] = {"at": 0.0, "body": ""}


def _subs(cmd) -> List[str]:
    try:
        kids = cmd.children
        kids = list(kids.values()) if isinstance(kids, dict) else list(kids or [])
    except Exception:
        return []
    return [str(k.name) for k in kids if type(k).__name__ == "SubCommand"]


def _all_commands(bot):
    """Слэш-команды, которые бот зарегистрировал. По-тихому: у mock-бота их может не быть."""
    try:
        if hasattr(bot, "application_commands_iterator"):
            return list(bot.application_commands_iterator())
        return list(getattr(bot, "application_commands", {}).values())
    except Exception:
        return []


def _body(bot) -> str:
    groups: Dict[str, List[str]] = {}
    for c in _all_commands(bot):
        if type(c).__name__ != "InvokableSlashCommand":
            continue
        name = str(getattr(c, "name", "") or "")
        if not name:
            continue
        subs = _subs(c)
        frag = f"/{name}" + (("(" + "|".join(subs[:MAX_SUBS]) + ")") if subs else "")
        groups.setdefault(str(getattr(c, "cog_name", "") or "прочее"), []).append(frag)
    lines = []
    for cog in sorted(groups):
        cmds = groups[cog]
        if len(cmds) > MAX_CMDS:
            cmds = cmds[:MAX_CMDS] + [f"и ещё {len(cmds) - MAX_CMDS}"]
        lines.append(f"{cog}: " + " ".join(cmds))
    return "\n".join(lines)


def catalogue(bot) -> str:
    """Пустая строка — если собрать нечем: тогда мозг отвечает как раньше, а не падает."""
    try:
        now = time.time()
        body = str(_cache["body"] or "")
        if not body or now - float(_cache["at"]) >= TTL:
            fresh = _body(bot)
            if fresh:
                _cache.update(body=fresh, at=now)
                body = fresh
        if not body:
            return ""
        # обрезаем список, а не карточку: правила должны дожить до конца промпта
        return (f"Сейчас: {datetime.now(MSK).strftime('%d.%m.%Y %H:%M')} по Москве.\n\n"
                f"Что этот бот умеет прямо сейчас:\n{body[:MAX_TOTAL]}\n\n{RULES}")
    except Exception:
        return ""
