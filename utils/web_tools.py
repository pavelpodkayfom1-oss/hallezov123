"""Взрослые разделы веб-панели: заявки, сообщения, команды, диагностика, снимки.

web_panel.py остаётся про HTTP (маршруты, сессии, защита), а здесь — только дело:
функции берут живой bot и возвращают то, что можно отдать браузеру как JSON. Никаких
своих SQL-запросов и своих текстов: заявки читаются через database, а действия повторяют
шаги тех же h_accept/h_reject из cogs/hall_recruit.py, чтобы веб и Discord не разъехались.

Чужие коги берутся из sys.modules, а не импортом: ког может быть выключен в панели
модулей, и тогда надо честно сказать «модуль выключен», а не уронить панель на старте.
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
import secrets
import sys
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import disnake
from disnake.ext import commands

ROOT = Path(__file__).resolve().parent.parent

STATUS_LABELS = {
    "new": "создана",
    "pending": "новая",
    "in_review": "на рассмотрении",
    "interview_scheduled": "на обзвоне",
    "accepted": "принята",
    "rejected": "отказ",
}
CLOSED = ("accepted", "rejected")

# ссылки на сообщения/ветки пересчитывать дорого (fetch), поэтому держим короткий кэш
_link_cache: Dict[int, tuple] = {}
LINK_TTL = 600


def recruit():
    """Ког набора или None — он делает заявки, без него раздел только читает."""
    return sys.modules.get("cogs.hall_recruit")


def db():
    return sys.modules.get("database")


def _iso(ts: Any) -> Optional[str]:
    return str(ts) if ts else None


def _mentions_to_names(text: Any, names: Dict[str, str]) -> Any:
    if not isinstance(text, str):
        return text
    return re.sub(r"<@!?(\d+)>", lambda m: names.get(m.group(1), "@" + m.group(1)), text)


def guild_of(bot) -> Optional[disnake.Guild]:
    if not bot.guilds:
        return None
    try:
        from utils.checks import load_config

        gid = int(load_config().get("guild_id") or 0)
    except Exception:
        gid = 0
    return bot.get_guild(gid) if gid else bot.guilds[0]


def _names(bot, ids: List[Any]) -> Dict[str, str]:
    """id → ник по всей кэшированной базе, чтобы в анкете не осталось <@123>."""
    out: Dict[str, str] = {}
    for uid in set(str(i) for i in ids if i):
        user = bot.get_user(int(uid)) if str(uid).isdigit() else None
        if user:
            out[str(uid)] = user.display_name
    return out


# ───────────────────────── заявки ─────────────────────────
def _app_row(app: Dict[str, Any], names: Dict[str, str]) -> Dict[str, Any]:
    status = str(app.get("status") or "pending")
    return {
        "id": app.get("id"),
        "nick": app.get("nick"),
        "static_id": app.get("static_id"),
        "user_id": app.get("user_id"),
        "age": app.get("age"),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "closed": status in CLOSED,
        "created_at": _iso(app.get("created_at")),
        "closed_at": _iso(app.get("closed_at")),
        "reviewer": names.get(str(app.get("reviewer_id"))) if app.get("reviewer_id") else None,
        "prev_families": _mentions_to_names(app.get("prev_families"), names),
        "why": _mentions_to_names(app.get("why_hallez"), names),
        "reason": app.get("verdict_reason"),
    }


async def apps_list(status: Optional[str], q: Optional[str], limit: int) -> Dict[str, Any]:
    database = db()
    if database is None:
        return {"error": "Модуль database не загружен"}
    rows = await database.list_applications(status=status or None, q=(q or "").strip() or None,
                                            limit=int(limit or 60))
    all_rows = await database.list_applications(status=None, q=None, limit=500)
    counts: Dict[str, int] = {"все": len(all_rows)}
    for row in all_rows:
        label = STATUS_LABELS.get(str(row.get("status")), str(row.get("status")))
        counts[label] = counts.get(label, 0) + 1
    return {
        "apps": [_app_row(dict(r), {}) for r in rows],
        "counts": counts,
        "total": len(all_rows),
    }


async def app_detail(bot, app_id: int) -> Dict[str, Any]:
    database = db()
    if database is None:
        return {"error": "Модуль database не загружен"}
    app = await database.get_application_by_id(int(app_id))
    if not app:
        return {"error": f"Заявки #{app_id} в базе нет"}
    app = dict(app)
    history = await database.application_history(app["id"])
    answers = None
    raw = await database.get_answers(app["id"])
    if raw:
        try:
            answers = json.loads(raw)
        except Exception:
            answers = {"ответ": raw}
    ids = [app.get("user_id"), app.get("reviewer_id")] + [
        h.get("actor") for h in history if str(h.get("actor") or "").isdigit()]
    if raw:
        ids += re.findall(r"<@!?(\d+)>", raw)
    names = _names(bot, ids)
    row = _app_row(app, names)
    row["link"] = await app_link(bot, app)
    row["answers"] = _walk_names(answers, names) if answers else None
    row["history"] = [{
        "status": h["status"],
        "label": STATUS_LABELS.get(str(h["status"]), str(h["status"])),
        "actor": names.get(str(h["actor"])) if str(h["actor"] or "").isdigit() else h["actor"],
        "note": h["note"],
        "at": _iso(h["created_at"]),
    } for h in history]
    if app.get("static_id"):
        accepted = await database.get_accepted_application_by_static(str(app["static_id"]))
        member = await database.get_member(int(app["user_id"])) if str(app.get("user_id")).isdigit() else None
        row["in_family"] = bool(member)
        row["member_rank"] = member.get("rank") if member else None
        row["repeat"] = bool(accepted and accepted.get("id") != app["id"])
    return row


def _walk_names(node: Any, names: Dict[str, str]) -> Any:
    if isinstance(node, dict):
        return {k: _walk_names(v, names) for k, v in node.items()}
    if isinstance(node, list):
        return [_walk_names(v, names) for v in node]
    return _mentions_to_names(node, names)


async def app_link(bot, app: Dict[str, Any]) -> str:
    """Ссылка в Discord: у новой заявки это приватная ветка, у старой — сообщение-карточка."""
    tid = app.get("thread_id")
    if not tid:
        return ""
    cached = _link_cache.get(int(app["id"]))
    if cached and time.time() - cached[0] < LINK_TTL:
        return cached[1]
    guild = guild_of(bot)
    if guild is None:
        return ""
    link = ""
    th = await _thread_of(bot, guild, int(tid))
    if th is not None:
        link = f"https://discord.com/channels/{guild.id}/{th.id}"
    else:
        msg, ch = await _legacy_card(bot, guild, int(tid))
        if ch is not None:
            link = f"https://discord.com/channels/{guild.id}/{ch.id}/{tid}"
    _link_cache[int(app["id"])] = (time.time(), link)
    return link


async def _thread_of(bot, guild, tid: int):
    """Приватная ветка заявки по id из базы (None — если это старая заявка без ветки)."""
    mod = recruit()
    try:
        return (await mod.get_thread(guild, tid)) if mod else guild.get_thread(tid)
    except Exception:
        return None


async def _legacy_card(bot, guild, mid: int):
    """Старые заявки: в thread_id лежал id сообщения-карточки. Ищем его в канале набора."""
    mod = recruit()
    channels = []
    if mod is not None:
        ch = guild.get_channel(mod.chan_id("review"))
        if ch is not None:
            channels.append(ch)
    channels += [c for c in guild.text_channels[:20] if c not in channels]
    for ch in channels:
        try:
            return await ch.get_partial_message(mid).fetch(), ch
        except Exception:
            continue
    return None, None


def staff_list(bot) -> List[Dict[str, Any]]:
    """Кто может вести заявку: панели нужен выбор, от чьего имени идёт действие."""
    guild = guild_of(bot)
    if guild is None:
        return []
    mod = recruit()
    out = []
    for member in guild.members:
        if member.bot:
            continue
        try:
            allowed = bool(mod.is_staff(member)) if mod else bool(member.guild_permissions.administrator)
        except Exception:
            allowed = bool(member.guild_permissions.administrator)
        if not allowed:
            continue
        out.append({
            "id": str(member.id),
            "name": member.display_name,
            "roles": [r.name for r in sorted(member.roles, key=lambda r: -r.position)[:4]],
            "top_role": member.top_role.name if member.top_role else "",
        })
    out.sort(key=lambda m: m["name"].lower())
    return out[:200]


def voice_choices(bot) -> List[Dict[str, str]]:
    """Каналы обзвона из config.json — те же, что в выпадающем списке на карточке."""
    mod, guild = recruit(), guild_of(bot)
    if mod is None or guild is None:
        return []
    try:
        return [{"id": o.value, "name": o.label} for o in mod.voice_options(guild)]
    except Exception:
        return []


async def _find_card(bot, app: Dict[str, Any]):
    """Карточка заявки в Discord — её надо перекрасить, иначе в канале останется «новая»."""
    tid = app.get("thread_id")
    guild = guild_of(bot)
    if not tid or guild is None:
        return None
    needles = [f"<@{app['user_id']}>", f"Заявка #{app['id']}"]
    th = await _thread_of(bot, guild, int(tid))
    if th is not None:
        try:
            messages = [m async for m in th.history(limit=50, oldest_first=True)]
        except Exception:
            return None
    else:
        msg, _ch = await _legacy_card(bot, guild, int(tid))
        messages = [msg] if msg is not None else []
    for msg in messages:
        try:
            if msg is None or msg.author.id != bot.user.id or not msg.embeds:
                continue
            emb = msg.embeds[0]
            text = (emb.title or "") + (emb.description or "") + (emb.footer.text if emb.footer else "")
            if any(n in text for n in needles):
                return msg
        except Exception:
            continue
    return None


async def _recolor(card, mod, app: Dict[str, Any], status_text: str, color_key: int,
                   reviewer_mention: str, stage: str, reason: Optional[str] = None) -> str:
    """Возвращает, что получилось с карточкой, чтобы это ушло в ответ панели."""
    if card is None:
        return "карточку в Discord не нашёл — статус в базе обновлён, в канале она могла остаться прежней"
    try:
        emb = mod._card_update(card, app["user_id"], status_text, color_key, reviewer_mention, stage=stage)
        if reason:
            mod.set_field(emb, "Причина", str(reason)[:1024], False)
        await card.edit(embed=emb, view=None)
        return "карточка перекрашена"
    except Exception as e:
        return f"карточку не перекрасил ({e.__class__.__name__})"


async def app_action(bot, app_id: int, action: str, actor_id: int,
                     reason: str = "", voice: str = "") -> Dict[str, Any]:
    """Действие по заявке из панели — те же шаги, что делает кнопка карточки в Discord."""
    database, mod = db(), recruit()
    if database is None:
        return {"error": "Модуль database не загружен"}
    app = await database.get_application_by_id(int(app_id))
    if not app:
        return {"error": f"Заявки #{app_id} в базе нет"}
    app = dict(app)
    if app.get("status") in CLOSED:
        return {"error": f"Заявка уже {STATUS_LABELS.get(str(app.get('status')))} — менять поздно"}
    if mod is None:
        return {"error": "Модуль набора (cogs.hall_recruit) выключен, действия недоступны"}
    guild = guild_of(bot)
    if guild is None:
        return {"error": "Бот ни на одном сервере"}
    try:
        actor = guild.get_member(int(actor_id)) or await guild.fetch_member(int(actor_id))
    except Exception:
        return {"error": "Не выбрал, от чьего имени действие (список сотрудников не находится)"}
    if actor is None:
        return {"error": "Этого человека нет на сервере"}
    if not mod.is_staff(actor):
        return {"error": f"{actor.display_name} не числится сотрудником — действия ведутся только от имени рекрутера"}

    steps: List[str] = [f"заявка #{app['id']} «{app['nick']}», действует {actor.display_name}"]
    warnings: List[str] = []
    cfg = mod.get_cfg()
    color = mod.cfg_color

    if action in ("review", "interview"):
        await database.update_application_status(app["id"], "in_review", actor.id, None,
                                                 actor=f"веб-панель:{actor.id}")
        steps.append("статус → на рассмотрении")
        card = await _find_card(bot, app)
        if action == "review":
            th = await mod.get_thread(guild, app["thread_id"])
            where = f"<#{th.id}>" if th else f"«{app['nick']}»"
            try:
                await mod.remind_take(app, actor.id, where)
                steps.append("напоминание взявшему поставлено")
            except Exception as e:
                warnings.append(f"напоминание не поставилось ({e.__class__.__name__})")
            text = f"{mod.em('review')} **На рассмотрении**"
            stage = "review"
        else:
            where = voice or mod.voice_channels_text()
            ok = await mod.dm(app["user_id"], mod.X("interview_dm_title"),
                              mod.X("interview_dm_desc", nick=app["nick"],
                                    reviewer=actor.mention, voice=where),
                              banner=mod.INTERVIEW_ASSET)
            mod.remind_bump(app["id"])
            steps.append("кандидат приглашён на обзвон: " + where + ("" if ok else " (ЛС не открылись)"))
            text = f"{mod.em('interview')} **Вызван на обзвон**"
            stage = "interview"
        warnings.append(await _recolor(card, mod, app, text, color("embed_color", 0x990000),
                                     actor.mention, stage))
        return {"steps": steps, "warnings": [w for w in warnings if w]}

    if action == "accept":
        member = guild.get_member(int(app["user_id"]))
        if member is None:
            try:
                member = await guild.fetch_member(int(app["user_id"]))
            except Exception:
                member = None
        if member is None:
            return {"error": "Кандидат уже не на сервере — принять нельзя"}

        if cfg.get("auto_nicknames", True):
            pattern = cfg.get("texts", {}).get("nickname_format", "[Hallez FAMQ] {nick} | {static}")
            try:
                await member.edit(nick=mod.build_nick(pattern, app["nick"], app["static_id"]),
                                  reason=f"Принят в семью (веб-панель, {actor})")
                steps.append("ник обновлён")
            except Exception as e:
                warnings.append(f"ник не изменён ({e.__class__.__name__}): роль бота должна быть выше")

        role_ids = [cfg.get("roles", {}).get("family_role_id", 0),
                    (cfg.get("ranks", {}).get("1", {}) or {}).get("role_id")
                    or cfg.get("roles", {}).get("rank_1_role_id", 0)]
        roles = [r for r in (guild.get_role(int(rid)) for rid in role_ids if rid) if r]
        if roles:
            try:
                await member.add_roles(*roles, reason=f"Принят в семью (веб-панель, {actor})")
                steps.append("выдано ролей: " + ", ".join(r.name for r in roles))
            except Exception as e:
                warnings.append(f"роли не выданы ({e.__class__.__name__}) — проверь иерархию")
        else:
            warnings.append("роли семьи не настроены в config.json")

        await database.upsert_member(int(app["user_id"]), app["nick"], app["static_id"], 1)
        await database.update_application_status(app["id"], "accepted", actor.id, None,
                                                 actor=f"веб-панель:{actor.id}")
        steps.append("занесён в состав, баллы рекрутеру начислены хуком принятия")
        mod.remind_forget(app["id"])
        await _close_app_thread(bot, mod, guild, app, cfg, "принята", steps, warnings)
        sent = await mod.dm(app["user_id"], mod.X("accept_dm_title"),
                            mod.X("accept_dm_desc", nick=app["nick"], static=app["static_id"],
                                  reviewer=actor.mention),
                            color=color("success_color", 0x2ECC71))
        steps.append("кандидату написано в ЛС" if sent else "ЛС закрыты — сообщение не дошло")
        warnings.append(await _recolor(await _find_card(bot, app), mod, app,
                                       f"{mod.em('accept')} **Принят в семью**",
                                       color("success_color", 0x2ECC71), actor.mention, "decided"))
        return {"steps": steps, "warnings": [w for w in warnings if w]}

    if action == "reject":
        if not reason.strip():
            return {"error": "Нужна причина отказа — она уходит кандидату в ЛС"}
        await database.update_application_status(app["id"], "rejected", actor.id, reason.strip()[:500],
                                                 actor=f"веб-панель:{actor.id}")
        steps.append("статус → отказ")
        mod.remind_forget(app["id"])
        await _close_app_thread(bot, mod, guild, app, cfg, "отклонена", steps, warnings, archive=False)
        sent = await mod.dm(app["user_id"], mod.X("reject_dm_title"),
                            mod.X("reject_dm_desc", nick=app["nick"], reason=reason.strip()[:500]),
                            color=color("error_color", 0xE74C3C), banner=False)
        steps.append("причина отправлена кандидату в ЛС" if sent else "ЛС закрыты — причину не доставить")
        warnings.append(await _recolor(await _find_card(bot, app), mod, app,
                                       f"{mod.em('reject')} **Отказано**",
                                       color("error_color", 0xE74C3C), actor.mention, "decided",
                                       reason=reason.strip()[:500]))
        return {"steps": steps, "warnings": [w for w in warnings if w]}

    return {"error": f"Неизвестное действие «{action}»"}


async def _close_app_thread(bot, mod, guild, app, cfg, verb, steps, warnings, archive: bool = True):
    th = await mod.get_thread(guild, app["thread_id"])
    if th is None:
        return
    if archive and hasattr(mod, "archive_thread_to_logs"):
        try:
            err = await mod.archive_thread_to_logs(th, app, bot.user, verb, 0x990000)
            if err:
                warnings.append(err)
        except Exception as e:
            warnings.append(f"архив ветки не отправлен ({e.__class__.__name__})")
    delete = str(cfg.get("accept_thread_action", "archive")).lower() == "delete" and verb == "принята"
    err = await mod.close_thread(th, app["id"], delete=delete, reason=f"Заявка #{app['id']} {verb}")
    if err:
        warnings.append(err)
    else:
        steps.append("ветка удалена" if delete else "ветка закрыта и в архиве")


# ────────────────────────── 2. сообщения ──────────────────────────

MAX_TEXT = 3500          # эмбед вмещает 4000, оставляем запас на заголовок и переносы
MAX_BROADCAST = 200      # столько ЛС за один запуск: больше — упрёмся в rate limit и в 256 МБ
BETWEEN = 0.9            # пауза между личными, Discord считает их строго


def _hex_color(value: Any, default: int = 0x990000) -> int:
    s = str(value or "").strip().lower().lstrip("#")
    if s.startswith("0x"):
        s = s[2:]
    if not s:
        return default
    try:
        return int(s, 16) & 0xFFFFFF
    except ValueError:
        return default


_PERM_RU = {
    "view_channel": "Просматривать канал",
    "send_messages": "Отправлять сообщения",
    "send_messages_in_threads": "Писать в ветках",
    "embed_links": "Встраивать ссылки",
    "attach_files": "Прикреплять файлы",
    "mention_everyone": "Упоминать @everyone",
    "read_message_history": "Читать историю сообщений",
}


def _missing_perms(bot, ch, need) -> List[str]:
    """Каких прав боту не хватает в канале (по-русски). Пусто — всё есть или проверить нельзя."""
    guild = guild_of(bot)
    me = getattr(guild, "me", None) if guild else None
    if me is None:
        return []
    try:
        perms = ch.permissions_for(me)
    except Exception:
        return []
    return [_PERM_RU.get(n, n) for n in need if not getattr(perms, n, True)]


def _no_perms_text(ch, missing: List[str]) -> str:
    return (f"В канале «{getattr(ch, 'name', '?')}» боту не хватает прав: {', '.join(missing)}. "
            "Откройте настройки канала → Права доступа → роль бота и разрешите это.")


def _discord_error(e: Exception) -> str:
    if isinstance(e, disnake.Forbidden):
        return ("Discord не пускает (403): боту не хватает прав в этом канале или выше роли "
                "человека, либо у человека закрыты ЛС. Проверьте права роли бота.")
    if isinstance(e, disnake.NotFound):
        return "Discord говорит, что этого больше нет — канал удалён или сообщение стёрто"
    if isinstance(e, disnake.HTTPException):
        return f"Discord отказал ({getattr(e, 'status', '?')}): {str(getattr(e, 'text', ''))[:120]}"
    return f"{e.__class__.__name__}: {str(e)[:160]}"


async def _resolve_channel(bot, cid: Any):
    guild = guild_of(bot)
    if guild is None:
        return None, "Бот ни на одном сервере"
    if not str(cid or "").isdigit():
        return None, "Не выбран канал"
    cid = int(cid)
    ch = guild.get_channel(cid) or bot.get_channel(cid)
    if ch is None:
        try:
            ch = await guild.fetch_channel(cid)
        except Exception:
            return None, f"Канала {cid} бот не видит"
    if not isinstance(ch, (disnake.TextChannel, disnake.Thread)):
        return None, "Писать можно только в текстовый канал или ветку"
    return ch, ""


def _hygiene_hint(bot, ch) -> str:
    """Канал заявок живёт только заявками: служебное сюда лить нельзя."""
    mod = recruit()
    if mod is None or ch is None:
        return ""
    try:
        review = int(mod.chan_id("review") or 0)
    except Exception:
        review = 0
    if review and getattr(ch, "id", 0) == review:
        return "это канал заявок: сюда только про набор, служебное — в служебный канал"
    return ""


def channel_rows(bot, q: str = "") -> Dict[str, Any]:
    guild = guild_of(bot)
    if guild is None:
        return {"error": "Бот ни на одном сервере"}
    mod = recruit()
    try:
        review = int(mod.chan_id("review") or 0) if mod else 0
    except Exception:
        review = 0
    needle = (q or "").strip().lower()
    me = guild.me
    rows: List[Dict[str, Any]] = []

    def perms(ch) -> Optional[bool]:
        if me is None:
            return None
        try:
            p = ch.permissions_for(me)
            return bool(getattr(p, "send_messages", False))
        except Exception:
            return None

    def add(ch, kind: str):
        if needle and needle not in (ch.name or "").lower() and needle not in kind:
            return
        rows.append({
            "id": str(ch.id),
            "name": ch.name,
            "kind": kind,
            "category": ch.category.name if getattr(ch, "category", None) else "",
            "topic": (getattr(ch, "topic", "") or "")[:180],
            "nsfw": bool(getattr(ch, "nsfw", False)),
            "slow": int(getattr(ch, "slowmode", 0) or getattr(ch, "slowmode_seconds", 0) or 0),
            "is_review": ch.id == review,
            "can_write": perms(ch),
        })

    for ch in sorted(guild.text_channels, key=lambda c: (c.position, c.name)):
        add(ch, "канал")
    for th in list(getattr(guild, "threads", ()) or ())[:80]:
        add(th, "ветка")
    return {"rows": rows[:250], "total": len(rows)}


def member_rows(bot, q: str = "", limit: int = 50) -> Dict[str, Any]:
    guild = guild_of(bot)
    if guild is None:
        return {"error": "Бот ни на одном сервере"}
    needle = (q or "").strip().lower()
    cap = max(1, min(int(limit or 50), 200))
    out: List[Dict[str, Any]] = []
    for m in guild.members:
        if m.bot:
            continue
        shown = m.nick or str(m)
        if needle and needle not in shown.lower() and needle != str(m.id):
            continue
        if len(out) >= cap:
            break
        top = ""
        try:
            rest = [r for r in m.roles[1:] if r.name != "Server Booster"]
            top = sorted(rest, key=lambda r: r.position)[-1].name if rest else ""
        except Exception:
            top = ""
        voice = ""
        try:
            voice = m.voice.channel.name if m.voice and m.voice.channel else ""
        except Exception:
            voice = ""
        out.append({"id": str(m.id), "name": shown, "top": top, "voice": voice,
                    "joined": m.joined_at.strftime("%d.%m.%Y") if m.joined_at else ""})
    return {"rows": out, "total": len(out)}


async def post_channel(bot, cid: Any, text: str, title: str = "", color: str = "",
                       ping: bool = False) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {"error": "Пустое сообщение — писать нечего"}
    if len(text) > MAX_TEXT:
        return {"error": f"Слишком длинно: {len(text)} знаков, максимум {MAX_TEXT}"}
    ch, err = await _resolve_channel(bot, cid)
    if err:
        return {"error": err}
    emb = None
    if title:
        emb = disnake.Embed(title=str(title)[:256], description=text[:4000],
                            color=disnake.Color(_hex_color(color)))
    need = ["view_channel", "send_messages"] + (["embed_links"] if emb else []) \
        + (["mention_everyone"] if ping else [])
    miss = _missing_perms(bot, ch, need)
    if miss:
        return {"error": _no_perms_text(ch, miss)}
    # без заголовка текст идёт обычным сообщением: раньше он терялся, а Discord отвечал ошибкой
    content = (text if emb is None else None)
    if ping:
        content = "@everyone" + ("\n" + content if content else "")
    mentions = disnake.AllowedMentions(everyone=bool(ping), users=False, roles=False,
                                       replied_user=False)
    try:
        msg = await ch.send(content=content, embed=emb, allowed_mentions=mentions)
    except Exception as e:
        return {"error": _discord_error(e)}
    out = {"link": msg.jump_url, "where": f"#{ch.name}", "message_id": str(msg.id)}
    hint = _hygiene_hint(bot, ch)
    if hint:
        out["hint"] = hint
    if ping:
        out["hint"] = out.get("hint", "") + " | пинг @everyone дошёл до всех на сервере"
    return out


async def post_dm(bot, user_id: Any, text: str, title: str = "", color: str = "") -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {"error": "Пустое сообщение"}
    if len(text) > MAX_TEXT:
        return {"error": f"Слишком длинно: {len(text)} знаков, максимум {MAX_TEXT}"}
    chan, member, err = await _dm_channel(bot, user_id)
    if err:
        return {"error": err}
    emb = None
    if title:
        emb = disnake.Embed(title=str(title)[:256], description=text[:4000],
                            color=disnake.Color(_hex_color(color)))
    try:
        msg = await chan.send(embed=emb, content=None if emb else text)
    except Exception as e:
        return {"error": _discord_error(e)}
    return {"link": msg.jump_url, "where": member.display_name}


async def _dm_channel(bot, user_id: Any):
    """(канал, участник, ошибка) — ЛС человека с сервера."""
    guild = guild_of(bot)
    if guild is None:
        return None, None, "Бот ни на одном сервере"
    if not str(user_id or "").isdigit():
        return None, None, "Не выбран человек"
    try:
        member = guild.get_member(int(user_id)) or await guild.fetch_member(int(user_id))
    except Exception:
        member = None
    if member is None:
        return None, None, "Этого человека нет на сервере"
    try:
        return await member.create_dm(), member, ""
    except Exception as e:
        return None, member, _discord_error(e)


async def broadcast(bot, ids: Any, text: str, title: str = "", color: str = "",
                    dry: bool = True) -> Dict[str, Any]:
    """Рассылка в ЛС. dry=True считает получателей и ничего не отправляет."""
    ids = [i for i in (ids or []) if str(i).isdigit()]
    if not ids:
        return {"error": "Никого не выбрали"}
    if len(ids) > MAX_BROADCAST:
        return {"error": f"Не больше {MAX_BROADCAST} получателей за раз — иначе упрёмся в лимит Discord"}
    text = (text or "").strip()
    if not text:
        return {"error": "Пустое сообщение"}
    if len(text) > MAX_TEXT:
        return {"error": f"Слишком длинно: {len(text)} знаков, максимум {MAX_TEXT}"}
    emb = None
    if title:
        emb = disnake.Embed(title=str(title)[:256], description=text[:4000],
                            color=disnake.Color(_hex_color(color)))
    if dry:
        return {"dry": True, "would": len(ids),
                "hint": f"Это проверка: сообщения не отправлены. {len(ids)} ЛС уйдёт примерно "
                        f"за {int(len(ids) * BETWEEN) + 5} секунд"}
    sent, failed = 0, []
    for uid in ids:
        chan, member, err = await _dm_channel(bot, uid)
        if chan is None:
            failed.append(f"{getattr(member, 'display_name', uid)}: {err}")
            continue
        try:
            await chan.send(embed=emb, content=None if emb else text)
            sent += 1
        except Exception as e:
            failed.append(f"{member.display_name}: {_discord_error(e)}")
        await asyncio.sleep(BETWEEN)
    return {"sent": sent, "failed": failed[:8], "failed_total": len(failed)}


# ────────────────────────── 3. экраны команд ──────────────────────────
# Экран (/hall, /центр, /hr…) думает, что его вызвали из Discord: он читает inter.channel,
# inter.author и рисует эмбеды с кнопками. Всё это перехватывается в _Cap и отдаётся браузеру,
# а живые disnake.ui.View держим в памяти, чтобы клик в окне дёрнул настоящий callback кнопки.

VIEW_MAX = 30
VIEW_TTL = 1800
VIEWS: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


def _actor(bot, actor_id: Any):
    """Кто от имени панели нажал кнопку: панель ведёт дела только от имени сотрудника."""
    guild = guild_of(bot)
    if guild is None:
        return None, "Бот ни на одном сервере"
    if not str(actor_id or "").isdigit():
        return None, "Выбери, от чьего имени действие"
    member = guild.get_member(int(actor_id))
    if member is None:
        return None, "Этого человека нет на сервере"
    mod = recruit()
    if mod is not None:
        try:
            if not mod.is_staff(member):
                return None, f"{member.display_name} не числится сотрудником"
        except Exception:
            pass
    return member, ""


def _emoji_str(e: Any) -> str:
    if e is None:
        return ""
    if isinstance(e, disnake.PartialEmoji):
        return f"<{'a' if e.animated else ':'}{e.name}:{e.id or 0}>"
    return str(e)


def _embed_json(emb: disnake.Embed) -> Dict[str, Any]:
    d = emb.to_dict()
    return {
        "title": d.get("title"),
        "text": d.get("description"),
        "color": "#%06x" % ((d.get("color") or 0x990000) & 0xFFFFFF),
        "author": (d.get("author") or {}).get("name"),
        "footer": (d.get("footer") or {}).get("text"),
        "image": (d.get("image") or {}).get("url"),
        "thumb": (d.get("thumbnail") or {}).get("url"),
        "fields": [{"name": f.get("name", ""), "value": f.get("value", ""),
                    "inline": bool(f.get("inline"))} for f in d.get("fields", [])],
    }


def _field_label(item) -> str:
    """disnake 2.12 помечает .label у TextInput как deprecated — подпись берём из вложенного компонента."""
    inner = getattr(item, "_underlying", None)
    return str(getattr(inner if inner is not None else item, "label", "") or "")


def _component_spec(view) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, ch in enumerate(getattr(view, "children", []) or []):
        if isinstance(ch, disnake.ui.Button):
            out.append({"kind": "button", "index": i, "label": ch.label or "",
                        "emoji": _emoji_str(ch.emoji), "url": ch.url or "",
                        "disabled": bool(ch.disabled), "row": int(ch.row or 0)})
        elif isinstance(ch, disnake.ui.BaseSelect):
            out.append({"kind": "select", "index": i, "label": ch.placeholder or "Выбрать",
                        "disabled": bool(ch.disabled), "row": int(ch.row or 0),
                        "max": int(getattr(ch, "max_values", 1) or 1),
                        "options": [{"label": o.label, "value": o.value,
                                     "emoji": _emoji_str(o.emoji), "hint": o.description or ""}
                                    for o in getattr(ch, "options", [])]})
        elif isinstance(ch, disnake.ui.TextInput):
            out.append({"kind": "input", "index": i, "label": _field_label(ch), "id": ch.custom_id,
                        "placeholder": ch.placeholder or "", "max": int(ch.max_length or 100),
                        "multiline": int(getattr(ch.style, "value", 1) or 1) == 2})
        else:
            out.append({"kind": "other", "index": i, "label": type(ch).__name__})
    return out


def _register(kind: str, obj, channel, comps: List[Dict[str, Any]], token: str = ""):
    """Токен живой панели: по нему клик находит настоящий disnake-объект."""
    if not token:
        token = secrets.token_hex(8)
    VIEWS[token] = {"kind": kind, "obj": obj, "channel": channel, "at": time.time()}
    VIEWS.move_to_end(token)
    while len(VIEWS) > VIEW_MAX:
        _drop(*VIEWS.popitem(last=False))
    return token if comps else ""


def _drop(token: str, entry: Dict[str, Any]) -> None:
    obj = entry.get("obj")
    for name in ("stop", "close"):
        try:
            getattr(obj, name)()
            break
        except Exception:
            continue


def _take_live(token: str):
    entry = VIEWS.get(str(token or ""))
    if entry is None:
        return None
    if time.time() - float(entry.get("at") or 0) > VIEW_TTL:
        _drop(str(token), entry)
        VIEWS.pop(str(token), None)
        return None
    entry["at"] = time.time()
    return entry


class _Data(dict):
    """inter.data: словарь, но поля достаются и через точку — как в настоящом payload."""

    def __getattr__(self, item):
        return self.get(item)


class _Cap:
    """Всё, что экран хотел показать. Отсюда это уходит в браузер как JSON."""

    def __init__(self, bot, channel=None):
        self.bot = bot
        self.channel = channel
        self.kind = "empty"
        self.content: Optional[str] = None
        self.embeds: List[Dict[str, Any]] = []
        self.objects: List[disnake.Embed] = []
        self.components: List[Dict[str, Any]] = []
        self.token = ""
        self.ephemeral = True
        self.modal: Optional[Dict[str, Any]] = None
        self.extra: List[Dict[str, Any]] = []
        self.message = None

    def names(self, text: Any) -> Dict[str, str]:
        ids = re.findall(r"<@!?(\d+)>", str(text or ""))
        return _names(self.bot, ids) if ids else {}

    def fill(self, content=None, embed=None, embeds=None, view=None, modal=None):
        self.content = _mentions_to_names(str(content), self.names(content)) if content else None
        items: List[disnake.Embed] = []
        if isinstance(embed, disnake.Embed):
            items = [embed]
        elif embeds:
            items = [e for e in embeds if isinstance(e, disnake.Embed)]
        self.objects = items
        self.embeds = [_embed_json(e) for e in items]
        self.components = _component_spec(view) if view is not None else []
        if self.components:
            self.token = _register("view", view, self.channel, self.components)
        else:
            self.token = ""
        if modal is not None:
            fields = []
            for row in getattr(modal, "components", []) or []:
                for item in getattr(row, "children", None) or [row]:
                    fields.append({"kind": "input", "label": _field_label(item),
                                   "id": getattr(item, "custom_id", ""),
                                   "placeholder": getattr(item, "placeholder", "") or "",
                                   "max": int(getattr(item, "max_length", 100) or 100),
                                   "multiline": int(getattr(item.style, "value", 1) or 1) == 2})
            self.modal = {"title": getattr(modal, "title", ""), "fields": fields,
                          "token": _register("modal", modal, self.channel,
                                             [{"kind": "button", "index": 0, "label": "Отправить"}])}
        self.kind = "answered" if (self.content or self.embeds or self.components or self.modal) \
            else self.kind

    def note(self, text: str) -> None:
        self.extra.append({"kind": "text", "content": _mentions_to_names(text, self.names(text))})

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "content": self.content, "embeds": self.embeds,
                "components": self.components, "token": self.token, "modal": self.modal,
                "ephemeral": self.ephemeral, "extra": self.extra}


class _Response:
    """inter.response: никуда в Discord не пишет, только заполняет cap."""

    def __init__(self, cap: _Cap):
        self.cap = cap
        self.type = disnake.InteractionResponseType.pong
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False, **kw) -> None:
        self._done = True
        self.cap.ephemeral = bool(ephemeral)
        self.cap.kind = "deferred"
        self.type = (disnake.InteractionResponseType.deferred_message_update if ephemeral
                     else disnake.InteractionResponseType.deferred_channel_message)

    async def send_message(self, content=None, *, embed=None, embeds=None, view=None, **kw) -> None:
        self._done = True
        self.type = disnake.InteractionResponseType.channel_message
        self.cap.fill(content=content, embed=embed, embeds=embeds, view=view)

    async def edit_message(self, content=..., *, embed=..., embeds=..., view=..., **kw) -> None:
        self._done = True
        self.type = disnake.InteractionResponseType.message_update
        self.cap.fill(content=None if content is ... else content,
                      embed=None if embed is ... else embed,
                      embeds=None if embeds is ... else embeds,
                      view=None if view is ... else view)

    async def send_modal(self, modal, *, title=None, components=None, **kw) -> None:
        self._done = True
        self.type = disnake.InteractionResponseType.modal
        self.cap.fill(modal=modal)

    async def delete_message(self, **kw) -> None:
        self._done = True
        self.cap.kind = "deleted"


class _WebMessage:
    """Сообщение экрана: его «правку» запоминаем, в Discord ничего не уходит."""

    def __init__(self, cap: _Cap, channel):
        self.cap = cap
        self.channel = channel
        self.author = cap.bot.user
        self.id = str(int(time.time() * 1000) % 10 ** 17)
        self.guild = getattr(channel, "guild", None)

    @property
    def embeds(self) -> List[disnake.Embed]:
        return list(self.cap.objects)

    @property
    def content(self):
        return self.cap.content

    @property
    def jump_url(self) -> str:
        return ""

    async def edit(self, content=..., *, embed=..., embeds=..., view=..., **kw):
        self.cap.fill(content=None if content is ... else content,
                      embed=None if embed is ... else embed,
                      embeds=None if embeds is ... else embeds,
                      view=None if view is ... else view)
        return self

    async def delete(self, **kw) -> None:
        self.cap.kind = "deleted"

    async def reply(self, content=None, **kw):
        self.cap.note(str(content or ""))
        return self


class WebInter(disnake.MessageInteraction):
    """Подделка под Discord-взаимодействие.

    Наследуется от MessageInteraction, потому что is_admin()/is_staff() проверяют именно
    isinstance, а не «похожесть». Ответы экранов идут в cap, поэтому в Discord не летит ничего.
    """

    def __init__(self, bot, actor, channel, cap: _Cap, *, component=None,
                 values: Optional[List[str]] = None, text_values: Optional[Dict[str, str]] = None):
        self._w_cap = cap
        self._w_component = component
        self._w_values = list(values or [])
        self._w_text = dict(text_values or {})
        self._w_channel = channel
        self.client = bot
        self.author = actor
        self.channel = channel
        self.guild_id = getattr(getattr(channel, "guild", None), "id", 0) or 0
        self.id = int(time.time() * 1000) % 10 ** 17
        self.token = "panel"
        self.version = 1
        self.application_id = getattr(getattr(bot, "application", None), "id", 0) or 0
        self.type = (disnake.InteractionType.component if component is not None
                     else disnake.InteractionType.application_command)
        self.locale = "ru"
        self.guild_locale = "ru"
        self.data = _Data(name="panel", type=2, options=[],
                          component=component, values=self._w_values)
        self._original_response = None
        try:
            me = getattr(getattr(channel, "guild", None), "me", None)
            self._permissions = channel.permissions_for(me) if me else disnake.Permissions.all()
        except Exception:
            self._permissions = disnake.Permissions.all()
        self._app_permissions = self._permissions

    @property
    def response(self):
        return _Response(self._w_cap)

    @property
    def followup(self):
        return _Response(self._w_cap)

    @property
    def me(self):
        return self.client.user

    @property
    def guild(self):
        return getattr(self._w_channel, "guild", None)

    @property
    def permissions(self):
        return self._permissions

    @property
    def app_permissions(self):
        return self._permissions

    @property
    def message(self):
        if self._w_cap.message is None:
            self._w_cap.message = _WebMessage(self._w_cap, self._w_channel)
        return self._w_cap.message

    @property
    def component(self):
        return self._w_component

    @property
    def value(self):
        return self._w_values[0] if self._w_values else None

    @property
    def values(self):
        return self._w_values

    @property
    def text_values(self):
        return self._w_text

    @property
    def expired(self):
        return False

    async def send(self, content=None, *, embed=None, embeds=None, view=None, **kw):
        self._w_cap.note(str(content or ""))
        return _WebMessage(self._w_cap, self._w_channel)

    async def edit_original_response(self, content=..., *, embed=..., embeds=..., view=None, **kw):
        self._w_cap.fill(content=None if content is ... else content,
                         embed=None if embed is ... else embed,
                         embeds=None if embeds is ... else embeds,
                         view=None if view is ... else view)
        return self.message

    async def delete_original_response(self, **kw) -> None:
        self._w_cap.kind = "deleted"


async def _screen_channel(bot, channel_id: Any):
    if str(channel_id or "").isdigit():
        ch, err = await _resolve_channel(bot, channel_id)
        return ch, err
    guild = guild_of(bot)
    if guild is None:
        return None, "Бот ни на одном сервере"
    me = guild.me
    for ch in sorted(guild.text_channels, key=lambda c: c.position):
        try:
            if me is None or ch.permissions_for(me).send_messages:
                return ch, ""
        except Exception:
            continue
    return guild.text_channels[0] if guild.text_channels else None, ""


def command_rows(bot) -> Dict[str, Any]:
    hub = sys.modules.get("cogs.admin_hub")
    if hub is None:
        return {"error": "Каталог экранов держит cogs.admin_hub, а он выключен"}

    def pack(items):
        rows = []
        for (icon, label, ref, cmd) in items:
            rows.append({"icon": icon, "label": label, "ref": ref, "cmd": cmd,
                         "args": hub.args_of(bot, ref),
                         "live": hub.web_target(bot, ref) is not None})
        return rows

    sections = [{"icon": icon, "title": title, "screens": pack(items)}
                for (icon, title, items) in hub.SECTIONS]
    if getattr(hub, "EXTRA_SCREENS", None):
        sections.append({"icon": "🧷", "title": "С аргументами", "only_web": True,
                         "screens": pack(hub.EXTRA_SCREENS)})
    return {"sections": sections,
            "hidden": [str(n) for n in getattr(hub, "HIDDEN", ())],
            "hr": str(getattr(hub, "HR_REF", ""))}


async def run_screen(bot, ref: str, actor_id: Any, channel_id: Any = None,
                     args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    hub = sys.modules.get("cogs.admin_hub")
    if hub is None:
        return {"error": "Каталог экранов держит cogs.admin_hub, а он выключен"}
    actor, err = _actor(bot, actor_id)
    if err:
        return {"error": err}
    label, cmd = hub.screen_of(str(ref))
    target = hub.web_target(bot, str(ref))
    if target is None:
        return {"error": f"«{label}» недоступен: ког выключен или экран не найден ({cmd})"}
    channel, cerr = await _screen_channel(bot, channel_id)
    if cerr:
        return {"error": cerr}
    cap = _Cap(bot, channel)
    try:
        # аргументы экрану подставляет hub: панель отдаёт строки, hub превращает их в
        # участника, канал или число — ровно то, что прислал бы Discord своей подсказкой
        await target(WebInter(bot, actor, channel, cap), dict(args or {}))
    except commands.CommandError as e:
        # экран сам сказал, чего ему не хватило (hub поднимает BadArgument на пустую форму)
        return {"error": f"«{label}»: {e}", "label": label, "cmd": cmd}
    except Exception as e:
        return {"error": f"«{label}» не открылся: {_discord_error(e)}", "label": label, "cmd": cmd}
    out = cap.to_dict()
    out.update({"label": label, "cmd": cmd})
    if out["kind"] == "empty":
        out["hint"] = f"экран ничего не ответил — ему, скорее всего, нужен аргумент: {cmd}"
    return out


async def press(bot, token: Any, index: Any, actor_id: Any,
                values: Optional[List[str]] = None) -> Dict[str, Any]:
    entry = _take_live(str(token or ""))
    if entry is None or entry.get("kind") != "view":
        return {"error": "Ответ устарел — открой экран заново"}
    actor, err = _actor(bot, actor_id)
    if err:
        return {"error": err}
    view = entry["obj"]
    children = list(getattr(view, "children", []) or [])
    try:
        idx = int(index)
    except (TypeError, ValueError):
        return {"error": "Непонятный номер кнопки"}
    if not 0 <= idx < len(children):
        return {"error": "Нет такой кнопки"}
    child = children[idx]
    if getattr(child, "url", None):
        return {"error": f"Это ссылка: {child.url}"}
    if getattr(child, "disabled", False):
        return {"error": "Кнопка неактивна"}
    cap = _Cap(bot, entry.get("channel"))
    inter = WebInter(bot, actor, entry.get("channel"), cap,
                     component=child, values=[str(v) for v in (values or [])])
    try:
        await child.callback(inter)
    except Exception as e:
        return {"error": f"Ничего не вышло: {_discord_error(e)}"}
    out = cap.to_dict()
    if not out["components"]:
        # кнопка ничего не перерисовала — панель остаётся прежней и клики продолжают работать
        out["components"] = _component_spec(view)
        out["token"] = _register("view", view, entry.get("channel"), out["components"],
                                 token=str(token))
    elif out["token"] == "":
        out["hint"] = "панель закрылась: кнопок больше нет"
    if out["kind"] == "empty":
        out["hint"] = "кнопка нажала, но ответить нечем (обычно так молчит кнопка-переключатель)"
    return out


async def submit_modal(bot, token: Any, text_values: Any, actor_id: Any) -> Dict[str, Any]:
    entry = _take_live(str(token or ""))
    if entry is None or entry.get("kind") != "modal":
        return {"error": "Форма устарела — открой экран заново"}
    actor, err = _actor(bot, actor_id)
    if err:
        return {"error": err}
    modal = entry["obj"]
    clean: Dict[str, str] = {}
    for row in getattr(modal, "components", []) or []:
        for item in getattr(row, "children", None) or [row]:
            cid = getattr(item, "custom_id", "")
            if not cid:
                continue
            val = str((text_values or {}).get(cid) or "")
            if getattr(item, "required", True) and not val.strip():
                return {"error": f"Поле «{_field_label(item) or cid}» обязательное"}
            clean[cid] = val[: int(getattr(item, "max_length", 4000) or 4000)]
    cap = _Cap(bot, entry.get("channel"))
    try:
        await modal.callback(WebInter(bot, actor, entry.get("channel"), cap, text_values=clean))
    except Exception as e:
        return {"error": f"Форма не принялась: {_discord_error(e)}"}
    _drop(str(token), entry)
    VIEWS.pop(str(token), None)
    return cap.to_dict()


# ────────────────────────── 4. диагностика ──────────────────────────
# Сервер живёт в 256 МБ, поэтому память смотрим целиком контейнером, а не только ботом:
# панель хостинга показывает именно её. Все счётные функции берём из cogs.memory —
# свои замеры в /proc только продублировали бы чужой код.

def _mem():
    return sys.modules.get("cogs.memory")


def _uptime_str() -> str:
    try:
        with open("/proc/uptime", encoding="utf-8") as f:
            secs = int(float(f.read().split()[0]))
    except Exception:
        return ""
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    return f"{days} д {hours} ч {rem // 60} мин" if days else f"{hours} ч {rem // 60} мин"


async def day_report(hours: int = 24) -> Dict[str, Any]:
    """Что произошло в семье за N часов: заявки, состав, варны, повышения, баллы."""
    database = db()
    if database is None:
        return {"error": "Модуль database не загружен"}
    hours = max(1, min(int(hours or 24), 720))
    rep = await database.report_since(hours)
    rows: List[Dict[str, Any]] = []

    for a in rep.get("applications") or []:
        rows.append({"what": "заявка", "who": a.get("nick"),
                     "state": STATUS_LABELS.get(str(a.get("status")), str(a.get("status"))),
                     "at": a.get("closed_at") or a.get("created_at"), "ref": a.get("id")})
    for m in rep.get("new_members") or []:
        rows.append({"what": "в состав", "who": m.get("nick"), "state": f"{m.get('rank')} ранг",
                     "at": m.get("joined_at"), "ref": m.get("user_id")})
    for w in rep.get("warns") or []:
        rows.append({"what": "варн", "who": str(w.get("user_id")),
                     "state": str(w.get("reason") or "")[:90], "at": w.get("created_at"),
                     "ref": w.get("id")})
    for p in rep.get("promotions") or []:
        rows.append({"what": "повышение", "who": p.get("nick"),
                     "state": f"на {p.get('target_rank')} ранг · {p.get('status')}",
                     "at": p.get("closed_at") or p.get("created_at"), "ref": p.get("id")})
    for pt in rep.get("points") or []:
        rows.append({"what": "баллы", "who": str(pt.get("user_id")),
                     "state": f"{int(pt.get('delta') or 0):+d} · {str(pt.get('reason') or '')[:70]}",
                     "at": pt.get("created_at"), "ref": None})
    rows.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["what"]] = counts.get(r["what"], 0) + 1
    return {"hours": hours, "totals": rep.get("totals") or {}, "counts": counts,
            "rows": rows[:120], "total": len(rows)}


NEEDED_PERMS = [
    ("view_channel", "видеть каналы"),
    ("send_messages", "писать сообщения"),
    ("read_message_history", "читать историю"),
    ("embed_links", "показывать карточки"),
    ("attach_files", "прикладывать файлы"),
    ("external_emojis", "пользоваться чужими эмодзи"),
    ("add_reactions", "ставить реакции"),
    ("manage_roles", "выдавать и снимать роли"),
    ("manage_nicknames", "переименовывать состав"),
    ("manage_channels", "создавать ветки заявок"),
    ("moderate_members", "усыплять нарушителей"),
    ("manage_expressions", "загружать эмодзи и стикеры"),
    ("connect", "заходить в голосовые"),
    ("speak", "говорить в голосовых"),
]


def control_center(bot) -> Dict[str, Any]:
    """Что бот на сервере не может: права, каналы без доступа и экраны с выключенным когом."""
    guild = guild_of(bot)
    if guild is None:
        return {"error": "Бот ни на одном сервере"}
    me = guild.me
    if me is None:
        return {"error": "Бот ещё не разобрал себя на сервере — обнови через пару секунд"}
    top = getattr(me.guild_permissions, "administrator", False)
    missing: List[str] = []
    if not top:
        for attr, label in NEEDED_PERMS:
            if not bool(getattr(me.guild_permissions, attr, False)):
                missing.append(label)
    silent: List[Dict[str, str]] = []
    for ch in sorted(guild.text_channels, key=lambda c: (c.position, c.name)):
        try:
            if not bool(ch.permissions_for(me).send_messages):
                silent.append({"name": ch.name,
                               "category": ch.category.name if getattr(ch, "category", None) else ""})
        except Exception:
            continue
    hub = sys.modules.get("cogs.admin_hub")
    dark: List[str] = []
    if hub is not None:
        refs = [(label, ref) for _, _, items in hub.SECTIONS for (_, label, ref, _) in items]
        refs += [(label, ref) for (_, label, ref, _) in getattr(hub, "EXTRA_SCREENS", [])]
        for label, ref in refs:
            if hub.web_target(bot, ref) is None:
                dark.append(f"{label} ({ref.split('.')[0]})")
    total = int(getattr(guild, "member_count", 0) or 0)
    seen = len(guild.members)
    return {
        "guild": guild.name,
        "administrator": bool(top),
        "missing": missing,
        "silent": silent[:40],
        "silent_total": len(silent),
        "dark": dark,
        "members": {"seen": seen, "total": total,
                    "note": ("бот видит только " + f"{seen} из {total} — без намерения участников "
                             "карточки, варны и рассылки будут слепы"
                             if total and seen and seen * 1.6 < total else "")},
    }


def system_report(bot) -> Dict[str, Any]:
    mem = _mem()
    if mem is None:
        return {"error": "Диагностику держит cogs.memory, а он выключен"}
    proc = mem._proc_status()
    cgroup = mem._cgroup_mb()
    stat = mem._cgroup_stat_mb()
    usage = int(cgroup.get("usage") or 0)
    limit = int(cgroup.get("limit") or 0)
    percent = round(usage * 100 / limit) if limit and usage else 0
    verdict = ("пора перезапустить: до лимита осталось ничего" if percent >= 85
               else "тесно — лишние кэши стоит сбросить" if percent >= 70
               else "запаса хватает")
    return {
        "memory": {"bot": proc.get("VmRSS"), "peak": proc.get("VmHWM"), "virtual": proc.get("VmSize"),
                   "container": usage, "limit": limit, "percent": percent,
                   "free": max(limit - usage, 0) if limit else None,
                   "page_cache": stat.get("file") or stat.get("cache"),
                   "verdict": verdict},
        "processes": [{"pid": pid, "name": name, "mb": rss}
                      for rss, pid, name in mem._top_processes(6)],
        "children": mem._child_processes(),
        "cache": {"members": mem._cached_members(bot), "messages": mem._cached_messages(bot)},
        "uptime": _uptime_str(),
        "latency_ms": round(float(getattr(bot, "latency", 0) or 0) * 1000),
        "guilds": len(getattr(bot, "guilds", None) or []),
        "cogs_on": sorted(getattr(bot, "cogs", {}) or {}),
    }


async def silents(bot, days: int = 7) -> Dict[str, Any]:
    """Молчуны: числятся в составе, но за срок не были в голосовом и не в отпуске."""
    database = db()
    staff = sys.modules.get("cogs.staff_tools")
    if database is None:
        return {"error": "Модуль database не загружен"}
    if staff is None:
        return {"error": "Голосовую статистику держит cogs.staff_tools, а он выключен"}
    days = max(1, min(int(days or 7), 365))
    period = "week" if days <= 7 else ("month" if days <= 30 else "all")
    try:
        members = await database.list_family_members()
    except Exception as e:
        return {"error": f"Состав не читается ({e.__class__.__name__})"}
    guild = guild_of(bot)
    silent: List[Dict[str, Any]] = []
    checked = 0
    for m in members:
        uid = int(m.get("user_id") or 0)
        if not uid:
            continue
        checked += 1
        try:
            secs = int(await staff.voice_seconds(uid, period) or 0)
            leave = bool(await staff.active_leave(uid))
        except Exception:
            continue
        if secs or leave:
            continue
        member = guild.get_member(uid) if guild else None
        silent.append({"user_id": str(uid), "nick": m.get("nick"),
                       "static_id": m.get("static_id"), "rank": m.get("rank"),
                       "on_server": member is not None,
                       "name": (member.nick or str(member)) if member else ""})
    silent.sort(key=lambda r: (not r["on_server"], str(r.get("static_id") or "")))
    label = (staff.PERIODS.get(period) or ("", "", f"{days} дней"))[2]
    return {"days": days, "period": label, "checked": checked,
            "total": len(silent), "silent": silent[:150]}


# ────────────────────────── 5. ревизия и аудит ──────────────────────────

AUDIT_CHANNELS = (("review", "канал заявок"), ("panel", "панель набора"),
                  ("orders", "заказы баллов"), ("points_panel", "панель баллов"))


async def channel_audit(bot, limit: int = 40) -> Dict[str, Any]:
    """Смотрит последние сообщения в служебных каналах: не загазован ли канал заявок."""
    mod = recruit()
    guild = guild_of(bot)
    if guild is None:
        return {"error": "Бот ни на одном сервере"}
    if mod is None:
        return {"error": "Модуль набора (cogs.hall_recruit) выключен — непонятно, где какой канал"}
    rows: List[Dict[str, Any]] = []
    count = max(5, min(int(limit or 40), 100))
    for kind, label in AUDIT_CHANNELS:
        try:
            cid = int(mod.chan_id(kind) or 0)
        except Exception:
            cid = 0
        if not cid:
            rows.append({"channel": label, "state": "канал не настроен"})
            continue
        ch = guild.get_channel(cid)
        if ch is None:
            rows.append({"channel": label, "id": str(cid), "state": "настройки указывают в никуда: "
                                                                    "такого канала нет (удалён?)"})
            continue
        try:
            msgs = [m async for m in ch.history(limit=count)]
        except Exception as e:
            rows.append({"channel": label, "name": f"#{ch.name}",
                         "state": f"историю не прочитать ({e.__class__.__name__})"})
            continue
        mine = [m for m in msgs if m.author.id == bot.user.id]
        cards = [m for m in mine if m.embeds and "заявк" in ((m.embeds[0].title or "") +
                   (m.embeds[0].description or "")).lower()]
        pinned = [m for m in mine if m.pinned]
        pings = [m for m in mine if any(r.id == guild.default_role.id for r in m.role_mentions)]
        service = len(mine) - len(cards)
        if not mine:
            state = "пусто: бот сюда ничего не писал"
        elif kind == "review" and service > len(cards):
            state = (f"в канале заявок {service} служебных сообщений против {len(cards)} заявок — "
                     "это раздувает канал, набранный только для заявок")
        else:
            state = f"заявок {len(cards)}, служебного {service}"
        rows.append({"channel": label, "name": f"#{ch.name}", "id": str(cid), "state": state,
                     "checked": len(msgs), "bot": len(mine), "cards": len(cards),
                     "service": service, "pinned": len(pinned), "pings": len(pings),
                     "last": msgs[0].created_at.strftime("%d.%m %H:%M") if msgs else ""})
    return {"rows": rows, "checked": count}


SETTINGS_JSON = ("config.json", "recruit_plus.json", "hall_settings.json", "staff_settings.json",
                 "teg_settings.json", "logs_settings.json", "panel_settings.json",
                 "golos_settings.json", "golos_guild.json", "emoji_map.json", "cdn_assets.json")
TOKEN_RE = re.compile(r"(?<![\w<]):([A-Za-z0-9_]{2,32}):(?!\d+>)")
_FULL_EMOJI_RE = re.compile(r"<a?:([A-Za-z0-9_]+):\d+>")


def _audit_files():
    for sub in ("cogs", "utils"):
        d = ROOT / sub
        if d.is_dir():
            for f in sorted(d.glob("*.py")):
                yield f
    for name in SETTINGS_JSON:
        f = ROOT / name
        if f.is_file():
            yield f


def emoji_audit(bot) -> Dict[str, Any]:
    """Имена эмодзи, написанные в коде и настройках, против того, что реально есть на сервере.

    Выдуманное имя не показывает ошибку — эмодзи молча исчезает из текста, и на всех экранах
    остаётся пустое место. Здесь оно видно списком, с файлами, где токен сидит.
    """
    guild = guild_of(bot)
    if guild is None:
        return {"error": "Бот ни на одном сервере"}
    live = {e.name.lower(): bool(e.animated) for e in guild.emojis if e.name}
    brand = sys.modules.get("utils.brand")
    aliases = {str(k).lower() for k in (getattr(brand, "ALIAS", {}) or {})}
    used: Dict[str, List[str]] = {}
    scanned = 0
    for f in _audit_files():
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        scanned += 1
        for match in TOKEN_RE.finditer(text):
            used.setdefault(match.group(1).lower(), []).append(f.name)
        for match in _FULL_EMOJI_RE.finditer(text):
            used.setdefault(match.group(1).lower(), []).append(f.name)
    missing = [{"token": name, "count": len(used[name]), "where": sorted(set(used[name]))[:4]}
               for name in sorted(k for k in used if k not in live and k not in aliases)]
    unused = sorted(name for name in live if name not in used)
    return {"scanned": scanned, "server": len(live), "referenced": len(used),
            "missing": missing[:80], "missing_total": len(missing),
            "unused": unused[:60], "unused_total": len(unused),
            "animated": sum(1 for v in live.values() if v)}


# ────────────────────────── 6. снимки и откат ──────────────────────────

def _backup():
    return sys.modules.get("utils.state_backup")


def _stamp_of(name: str) -> str:
    try:
        return datetime.strptime("_".join(name.split("_")[:2]),
                                 "%Y-%m-%d_%H%M%S").strftime("%d.%m %H:%M:%S")
    except ValueError:
        return name


def _snap_files(path: Path) -> List[Path]:
    try:
        return sorted(f for f in path.iterdir() if f.is_file())
    except Exception:
        return []


def snapshot_rows(limit: int = 14) -> Dict[str, Any]:
    sb = _backup()
    if sb is None:
        return {"error": "Снимки держит utils.state_backup, а он выключен"}
    dirs = sb._snap_dirs()[::-1]
    rows = [{"name": d.name, "at": _stamp_of(d.name), "files": len(_snap_files(d)),
             "kb": sum(f.stat().st_size for f in _snap_files(d)) // 1024, "live": i == 0}
            for i, d in enumerate(dirs[:max(1, min(int(limit or 14), 40))])]
    return {"rows": rows, "total": len(dirs), "keep": int(getattr(sb, "KEEP", 8))}


def _brief(value: Any) -> str:
    if isinstance(value, str):
        return value.replace("\n", " ")[:140]
    try:
        return json.dumps(value, ensure_ascii=False)[:140]
    except Exception:
        return str(value)[:140]


def _deep_diff(snap: Any, live: Any, path: str, out: List[Dict[str, Any]], depth: int = 0) -> None:
    """Что вернётся/исчезнет/изменится при откате: snap — из снимка, live — сейчас."""
    if len(out) >= 60 or depth > 7:
        return
    if isinstance(snap, dict) and isinstance(live, dict):
        for key in sorted(set(snap) | set(live), key=str):
            here = f"{path}.{key}" if path else str(key)
            if key not in live:
                out.append({"path": here, "kind": "вернётся", "value": _brief(snap[key])})
            elif key not in snap:
                out.append({"path": here, "kind": "исчезнет", "value": _brief(live[key])})
            elif snap[key] != live[key]:
                _deep_diff(snap[key], live[key], here, out, depth + 1)
        return
    if snap != live:
        out.append({"path": path or "весь файл", "kind": "изменится",
                    "was": _brief(live), "value": _brief(snap)})


def snapshot_diff(name: str) -> Dict[str, Any]:
    sb = _backup()
    if sb is None:
        return {"error": "Снимки держит utils.state_backup, а он выключен"}
    src = Path(sb.BACKUP_DIR) / str(name or "")
    if not src.is_dir():
        return {"error": "Такого снимка нет"}
    changes: List[Dict[str, Any]] = []
    files: List[Dict[str, Any]] = []
    for f in _snap_files(src):
        live = sb.BASE_DIR / f.name
        if not live.exists():
            files.append({"name": f.name, "kind": "вернётся удалённый файл",
                          "changes": [{"path": f.name, "kind": "вернётся", "value": ""}]})
            continue
        if f.suffix == ".json":
            try:
                snap_data = json.loads(f.read_text(encoding="utf-8"))
                live_data = json.loads(live.read_text(encoding="utf-8"))
            except Exception:
                files.append({"name": f.name, "kind": "не читается как json — вернётся копия",
                              "changes": []})
                continue
            out: List[Dict[str, Any]] = []
            _deep_diff(snap_data, live_data, "", out)
            changes.extend(out)
            files.append({"name": f.name, "kind": "изменится" if out else "такой же",
                          "changes": out[:60]})
        else:
            same = sb.sha1_of(f) == sb.sha1_of(live)
            files.append({"name": f.name, "kind": "такой же" if same else "вернётся копия",
                          "changes": [] if same else
                          [{"path": f.name, "kind": "изменится",
                            "was": f"{live.stat().st_size // 1024} КБ",
                            "value": f"{f.stat().st_size // 1024} КБ"}]})
    return {"name": str(name), "at": _stamp_of(str(name)), "files": files,
            "changes": changes[:60], "total": len(changes),
            "note": "снимок ложится поверх живых файлов целиком; то, что сейчас живое, "
                    "уходит в новый снимок, так что откат обратим"}


async def snapshot_restore(name: str, bot) -> Dict[str, Any]:
    sb, rev = _backup(), sys.modules.get("cogs.reverse")
    if sb is None:
        return {"error": "Снимки держит utils.state_backup, а он выключен"}
    if not re.fullmatch(r"[\w.\-]{4,64}", str(name or "")):
        return {"error": "Непохожее имя снимка"}
    src = Path(sb.BACKUP_DIR) / str(name)
    if not src.is_dir():
        return {"error": "Такого снимка нет"}
    files = [f.name for f in _snap_files(src)]
    try:
        done = await asyncio.to_thread(sb.restore, str(name))
    except Exception as e:
        return {"error": f"Откат не получился: {_discord_error(e)}"}
    steps = [f"поднято {len(files)} файлов: " + ", ".join(files[:12]) +
             ("…" if len(files) > 12 else ""),
             f"то, что было живым, ушло в снимок {done['saved_to']}"]
    reloaded: List[str] = []
    exts = list(rev._exts(files)) if rev is not None else []
    for ext in exts:
        try:
            if ext in getattr(bot, "extensions", {}):
                bot.reload_extension(ext)
            else:
                bot.load_extension(ext)
            reloaded.append(ext)
        except Exception as e:
            reloaded.append(f"{ext}: {e.__class__.__name__}")
    steps.append("перезагружено: " + (", ".join(reloaded) if reloaded else "не требуется"))
    mod = recruit()
    if mod is not None:
        try:
            ok, text = await mod.publish_panel("apply")
            steps.append(("панель набора перерисована: " if ok else
                          "панель набора не перерисовалась: ") + str(text)[:140])
        except Exception as e:
            steps.append(f"панель набора не перерисована ({e.__class__.__name__})")
    return {"steps": steps, "reloaded": reloaded, "restored": files}


# ═══════════════════════════════ Тексты бота ═══════════════════════════════
# Надписи живут в трёх местах: config.json["texts"] (тексты набор/повышения/сборов/казны),
# hall_settings.json (приветствие в канал и текст панели АФК) и golos_guild.json
# (приветствие голосом в войсе). Каталог ключей собирается из самих исходников — новый
# get_text (или обёртка над ним вроде _T в казне) в коде появляется в редакторе сам,
# руками список вести нельзя, он отстаёт.

TEXT_SRC_DIRS = ("cogs", "utils")
# Обёртки над get_text: у `_T` первым аргументом идёт бот, поэтому сдвиг на 1.
TEXT_GETTERS = {"get_text": 0, "_T": 1}
MAX_EMBED_TEXT = 4000
MAX_EMBED_TITLE = 256
MAX_BUTTON_LABEL = 80
MAX_FIELD_LABEL = 45
MAX_PLACEHOLDER = 100
MAX_TOPIC = 1024

_HALL_FIELDS = (("rules_text", "Приветствие новичку в канале"),
                ("afk_panel_text", "Текст панели АФК"))
_GOLOS_FIELDS = (("greet_text", "Приветствие голосом (бот говорит в войсе)"),)
_catalog_cache: Optional[Dict[str, Any]] = None


def _text_kind(key: str) -> str:
    k = key.lower()
    if k.startswith("btn_") or "_btn_" in k:
        return "button"
    if k.endswith("_topic"):
        return "topic"
    if "title" in k:
        return "title"
    if k.endswith("_ph") or k.endswith("_hint"):
        return "hint"
    if "label" in k:
        return "field"
    return "text"


def _text_limit(kind: str) -> int:
    return {"button": MAX_BUTTON_LABEL, "title": MAX_EMBED_TITLE, "field": MAX_FIELD_LABEL,
            "hint": MAX_PLACEHOLDER, "topic": MAX_TOPIC}.get(kind, MAX_EMBED_TEXT)


# Раздел в редакторе = где надпись живёт, а не какого она вида: тип показан отдельным
# фильтром. Так «Казна» собирается в одну папку вместе со своими кнопками и полями.
_GROUP_BY_PREFIX = (
    ("kazna_", "Казна"),
    ("modal_recruit", "Набор и заявки"), ("btn_recruit", "Набор и заявки"),
    ("modal_reject", "Набор и заявки"), ("select_voice", "Набор и заявки"),
    ("recruit_", "Набор и заявки"),
    ("modal_promo", "Повышения"), ("btn_promo", "Повышения"), ("select_promo", "Повышения"),
    ("nickname_format", "Повышения"), ("promo_", "Повышения"),
    ("btn_event", "Сборы"), ("event_", "Сборы"),
    ("mod_", "Наказания"),
    ("poll_", "Опросы"),
    ("date_", "Календарь"),
    ("stream_", "Стримы"),
    ("remind_", "Напоминания"),
)
# Порядок разделов в списке: то, что чаще правят, выше.
_GROUP_ORDER = ("Приветствия", "Казна", "Набор и заявки", "Повышения", "Сборы", "Наказания",
               "Опросы", "Календарь", "Стримы", "Напоминания", "Прочее")


def _text_group(key: str) -> str:
    k = key.lower()
    for prefix, title in _GROUP_BY_PREFIX:
        if k.startswith(prefix):
            return title
    return "Прочее"


def _module_strings(tree: ast.Module) -> Dict[str, str]:
    """Строковые константы верхнего уровня файла: на них ссылаются дефолты get_text."""
    out: Dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = [node.target] if isinstance(node, ast.AnnAssign) else list(node.targets)
        value = node.value if isinstance(node.value, ast.Constant) else None
        if value is None or not isinstance(value.value, str):
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                out[t.id] = value.value
    return out


def _scan_get_text() -> "OrderedDict[str, Dict[str, str]]":
    """Ключи get_text("ключ", "дефолт") из исходников — с файлом и строкой.

    Дефолт может быть назван константой модуля (так делают длинные тексты казны) — тогда
    берём её значение: «Как в коде» обязано возвращать настоящую надпись, а не пустоту.
    """
    found: "OrderedDict[str, Dict[str, str]]" = OrderedDict()
    for sub in TEXT_SRC_DIRS:
        folder = ROOT / sub
        if not folder.is_dir():
            continue
        for f in sorted(folder.glob("*.py")):
            try:
                tree = ast.parse(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            consts = _module_strings(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if name not in TEXT_GETTERS:
                    continue
                args = node.args[TEXT_GETTERS[name]:]
                first = args[0] if args else None
                if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                    continue
                key = first.value
                default = ""
                if len(args) > 1:
                    second = args[1]
                    if isinstance(second, ast.Constant):
                        default = str(second.value)
                    elif isinstance(second, ast.Name):
                        default = consts.get(second.id, "")
                one = {"default": default, "src": f"{sub}/{f.name}:{node.lineno}"}
                old = found.get(key)
                # один ключ правят из нескольких мест: берём первый непустой дефолт,
                # иначе «Как в коде» вернул бы пустую строку вместо настоящей надписи
                if old is None or (not old["default"] and default):
                    found[key] = one
    return found


def _read_json_file(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _safe_guild(bot):
    try:
        return guild_of(bot)
    except Exception:
        return None


def _hall_mod():
    return sys.modules.get("cogs.hall_panel")


def _golos_mod():
    return sys.modules.get("cogs.govorit_stay")


def _hall_texts(bot) -> List[Dict[str, Any]]:
    hp = _hall_mod()
    live = _read_json_file(ROOT / "hall_settings.json")
    defaults = getattr(hp, "DEFAULTS", {}) if hp else {}
    if not defaults:
        defaults = {"rules_text": "Привет, {mention}! Добро пожаловать на {server}!"}
    if hp is not None:
        try:
            live = hp.load_settings()
        except Exception:
            pass
    out = []
    for field, label in _HALL_FIELDS:
        default = str(defaults.get(field, ""))
        value = str(live.get(field, default))
        out.append({"id": f"hall::{field}", "where": "hall", "key": field, "kind": "text",
                    "group": "Приветствия", "label": label, "default": default, "value": value,
                    "changed": bool(default) and value != default, "src": "hall_settings.json",
                    "limit": MAX_EMBED_TEXT})
    return out


def _hall_write(field: str, value: str, default_if_missing: bool = False) -> str:
    """Пишет настройку hall_panel: через ког, а если он выключен — прямо в файл."""
    hp = _hall_mod()
    if hp is not None:
        try:
            hp.update_settings(**{field: value})
            return ""
        except Exception as e:
            return f"Ког не принял запись: {e.__class__.__name__}"
    path = ROOT / "hall_settings.json"
    try:
        data = _read_json_file(path)
        data[field] = value
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return ""
    except Exception as e:
        return f"Файл hall_settings.json не записан: {e.__class__.__name__}"


def _golos_texts(bot) -> List[Dict[str, Any]]:
    guild = _safe_guild(bot)
    if guild is None:
        return []
    gs = _golos_mod()
    live = {}
    if gs is not None:
        try:
            live = gs.get_guild(guild.id) or {}
        except Exception:
            live = {}
    if not live:
        live = _read_json_file(ROOT / "golos_guild.json").get(str(guild.id)) or {}
    defaults = getattr(gs, "GUILD_DEFAULT", {}) or {"greet_text": "Привет, {name}! Добро пожаловать."}
    out = []
    for field, label in _GOLOS_FIELDS:
        default = str(defaults.get(field, ""))
        value = str(live.get(field, default))
        out.append({"id": f"golos::{field}", "where": "golos", "key": field, "kind": "text",
                    "group": "Приветствия", "label": label, "default": default, "value": value,
                    "changed": bool(default) and value != default, "src": "golos_guild.json",
                    "limit": MAX_EMBED_TEXT})
    return out


def invalidate_texts() -> None:
    """Каталог пересобирается после переключения модуля: ключи живут в их коде."""
    global _catalog_cache
    _catalog_cache = None


def texts_catalog(bot, force: bool = False) -> Dict[str, Any]:
    """Весь список надписей: ключ, дефолт из кода, живое значение, где берётся."""
    global _catalog_cache
    if _catalog_cache is not None and not force:
        items = _catalog_cache["items"]
    else:
        try:
            from utils.checks import load_config
            live = (load_config() or {}).get("texts") or {}
        except Exception:
            live = {}
        items = []
        for key, meta in _scan_get_text().items():
            kind = _text_kind(key)
            raw = live.get(key)
            changed = isinstance(raw, str)
            items.append({"id": f"cfg::{key}", "where": "cfg", "key": key, "kind": kind,
                          "group": _text_group(key), "label": "", "default": meta["default"],
                          "value": raw if changed else meta["default"], "changed": changed,
                          "src": meta["src"], "limit": _text_limit(kind)})
        items = _hall_texts(bot) + _golos_texts(bot) + items
        _catalog_cache = {"items": items}
    groups: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for it in items:
        groups.setdefault(it["group"], []).append(it)
    # разделы по-человечески: сначала то, что правят чаще, остальные по алфавиту
    order = {title: i for i, title in enumerate(_GROUP_ORDER)}
    titles = sorted(groups, key=lambda g: (order.get(g, len(order)), g))
    return {"items": items,
            "groups": [{"title": g, "items": groups[g]} for g in titles],
            "total": len(items),
            "changed": sum(1 for it in items if it["changed"]),
            "no_default": sum(1 for it in items if not (it["value"] or "").strip())}


def _find_text_item(bot, item_id: str) -> Optional[Dict[str, Any]]:
    for it in texts_catalog(bot, force=True)["items"]:
        if it["id"] == item_id:
            return it
    return None


def _golos_write(bot, guild, key: str, value: str) -> str:
    """Пишет настройку сервера: через ког, а если он выключен — прямо в файл."""
    gs = _golos_mod()
    if gs is not None:
        try:
            data = gs.get_guild(guild.id) or {}
            data[key] = value
            gs.set_guild(guild.id, data)
            return ""
        except Exception as e:
            return f"Ког не принял запись: {e.__class__.__name__}"
    path = ROOT / "golos_guild.json"
    try:
        data = _read_json_file(path)
        entry = data.get(str(guild.id)) or {}
        entry[key] = value
        data[str(guild.id)] = entry
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return ""
    except Exception as e:
        return f"Файл golos_guild.json не записан: {e.__class__.__name__}"


def text_save(bot, item_id: str, value: Any) -> Dict[str, Any]:
    item = _find_text_item(bot, item_id)
    if item is None:
        return {"error": "Такой надписи в каталоге нет — обнови страницу"}
    text = str(value if value is not None else "")
    if len(text) > item["limit"]:
        return {"error": f"Слишком длинно: {len(text)} знаков, для «{item['kind']}» максимум {item['limit']}"}
    where, _, key = item_id.partition("::")
    try:
        if where == "cfg":
            from utils.checks import load_config, save_config
            cfg = load_config() or {}
            texts = cfg.get("texts")
            if not isinstance(texts, dict):
                texts = {}
            texts[key] = text
            cfg["texts"] = texts
            save_config(cfg)
        elif where == "hall":
            err = _hall_write(key, text)
            if err:
                return {"error": err}
        elif where == "golos":
            guild = _safe_guild(bot)
            if guild is None:
                return {"error": "Бот ни на одном сервере — приветствие голосом не сохранить"}
            err = _golos_write(bot, guild, key, text)
            if err:
                return {"error": err}
        else:
            return {"error": "Непонятно, куда это писать"}
    except Exception as e:
        return {"error": f"Не удалось сохранить: {e.__class__.__name__}: {e}"}
    _catalog_cache = None
    return {"ok": True, "where": item["src"], "key": key, "saved": item["label"] or key}


def text_reset(bot, item_id: str) -> Dict[str, Any]:
    item = _find_text_item(bot, item_id)
    if item is None:
        return {"error": "Такой надписи в каталоге нет — обнови страницу"}
    where, _, key = item_id.partition("::")
    default = item["default"]
    try:
        if where == "cfg":
            from utils.checks import load_config, save_config
            cfg = load_config() or {}
            texts = cfg.get("texts") or {}
            texts.pop(key, None)
            cfg["texts"] = texts
            save_config(cfg)
        elif where == "hall":
            if _hall_mod() is None and not default:
                return {"error": "Модуль hall_panel выключен, а дефолт отсюда не взять"}
            err = _hall_write(key, default)
            if err:
                return {"error": err}
        elif where == "golos":
            guild = _safe_guild(bot)
            if guild is None:
                return {"error": "Бот ни на одном сервере"}
            err = _golos_write(bot, guild, key, default)
            if err:
                return {"error": err}
        else:
            return {"error": "Непонятно, откуда брать дефолт"}
    except Exception as e:
        return {"error": f"Не удалось вернуть как в коде: {e.__class__.__name__}"}
    _catalog_cache = None
    return {"ok": True, "value": default, "where": item["src"]}


def _sample_map(bot) -> Dict[str, str]:
    """Чем заменить {placeholders} в предпросмотре — чтобы правка была видна живой."""
    try:
        from utils.checks import load_config
        cfg = load_config() or {}
    except Exception:
        cfg = {}
    guild = _safe_guild(bot)
    return {
        "nick": "Марк", "name": "Марк", "user": "Марк", "member": "Марк", "mention": "@Марк",
        "server": guild.name if guild else "Hallez FAMQ", "guild": guild.name if guild else "Hallez FAMQ",
        "bot_name": str(cfg.get("bot_name") or "Бот"), "title": "Дроп 19:30", "time": "19:15 МСК",
        "info": "Особняк семьи, бронежилеты", "static": "123-456", "age": "21",
        "recruit_channel": "#набор", "channel": "#общий", "reason": "не подошло по времени",
        "rank": "Боец", "id": "1234", "prev": "другая семья", "why": "хочу в семью",
        "days": "3", "hours": "3", "date": "31.10.2026", "role": "Семья",
        # казна: числа и список собранных каналов
        "balance": "128 400", "raised": "96 400", "spent": "34 000", "moves": "17",
        "reports": "9", "amount": "12 000", "purpose": "Печать афиш", "people": "отряду — 8",
        "id": "12", "made": "🗂 #💰-банк-семьи, 🧾-отчёты-казны",
    }


def text_preview(bot, item_id: str, value: Any) -> Dict[str, Any]:
    item = _find_text_item(bot, item_id)
    kind = item["kind"] if item else "text"
    limit = item["limit"] if item else MAX_EMBED_TEXT
    text = str(value if value is not None else "")
    subs = _sample_map(bot)
    names = sorted(set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", text)))
    rendered = re.sub(r"\{(\w+)\}", lambda m: str(subs[m.group(1)]) if m.group(1) in subs else m.group(0), text)
    out = {"kind": kind, "text": "" if kind == "title" else rendered,
           "title": rendered if kind == "title" else "",
           "placeholders": names, "unknown": [n for n in names if n not in subs],
           "len": len(text), "limit": limit, "overflow": len(text) > limit}
    if item:
        out["label"] = item["label"] or item["key"]
        out["default"] = item["default"]
    return out


# ═══════════════════════════ Обновить в Discord ═══════════════════════════
# Сохранённая надпись сразу попадает в новые сообщения, но карточки, которые бот уже
# выложил в каналы, Discord хранит у себя — он их сам не переклеивает. Кнопка сверху
# просит нужный ког переложить его витрину текущими текстами; список того, что умеет,
# коги объявляют сами, поэтому выключенный модуль в меню не показывается.

REFRESH_LABELS = {"kazna": "Казна: витрины в каналах", "afk": "Панель АФК"}


def _refresh_guild(bot):
    guild = _safe_guild(bot)
    if guild is None:
        raise ValueError("Бот ни на одном сервере — обновлять негде")
    return guild


async def refresh_targets(bot) -> Dict[str, Any]:
    """Что панель может переложить в Discord прямо сейчас."""
    guild = _refresh_guild(bot)
    out = []
    treasury = bot.get_cog("Treasury")
    if treasury is not None:
        chans = [f"#{c.name}" for c in treasury.showcase_channels(guild) if c is not None]
        if chans:
            out.append({"ref": "kazna", "title": REFRESH_LABELS["kazna"], "where": ", ".join(chans)})
        else:
            out.append({"ref": "kazna", "title": REFRESH_LABELS["kazna"],
                        "where": "ветки казны ещё нет — собери её в `/казна`", "off": True})
    panel = bot.get_cog("HallPanel")
    if panel is not None:
        hp = _hall_mod()
        st = (hp.load_settings() if hp else {}) or {}
        ch = guild.get_channel(int(st.get("afk_panel_channel_id") or 0))
        out.append({"ref": "afk", "title": REFRESH_LABELS["afk"],
                    "where": (f"#{ch.name}" if ch else "канал для панели ещё не выбран"),
                    "off": ch is None})
    return {"targets": out}


async def refresh_apply(bot, ref: str) -> Dict[str, Any]:
    guild = _refresh_guild(bot)
    if ref == "kazna":
        treasury = bot.get_cog("Treasury")
        if treasury is None:
            return {"error": "Модуль казны не загружен"}
        where = await treasury.apply_texts(guild)
        if not where:
            return {"error": "Ветки казны ещё нет — сначала «🏗 Создать ветку казны» в `/казна`"}
        return {"ok": True, "did": "Переложила карточки казны: " + ", ".join(where)}
    if ref == "afk":
        panel = bot.get_cog("HallPanel")
        if panel is None:
            return {"error": "Модуль панели семьи не загружен"}
        done, note = await panel.publish_afk_panel(guild)
        return {"ok": True, "did": note} if done else {"error": note}
    return {"error": "Не знаю такого обновления"}


# Папка stickers/ лежит в репозитории и на хостинге; в Discord стикером её делает
# hall_recruit, а здесь картинку можно кинуть в канал прямо с сайта — эмбедом,
# чтобы подпись и стиль были как у остальных постов бота.

STICKER_DIR = ROOT / "stickers"
STICKER_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}
STICKER_NAME_RE = re.compile(r"^[\w\- ]{1,80}\.(png|jpg|jpeg|gif|webp)$", re.IGNORECASE | re.UNICODE)


def _sticker_path(name: Any) -> Optional[Path]:
    filename = str(name or "").strip()
    if not STICKER_NAME_RE.match(filename):
        return None
    path = STICKER_DIR / filename
    if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        return None
    return path


def sticker_rows() -> Dict[str, Any]:
    out = []
    try:
        names = sorted(p.name for p in STICKER_DIR.iterdir() if p.is_file())
    except FileNotFoundError:
        return {"rows": [], "error": "Папки stickers/ на хостинге нет"}
    for n in names:
        ext = "." + n.rsplit(".", 1)[-1].lower() if "." in n else ""
        if ext not in STICKER_EXT:
            continue
        path = STICKER_DIR / n
        out.append({"name": n, "title": path.stem.replace("_", " ").replace("-", " "),
                    "size": round(path.stat().st_size / 1024), "mime": STICKER_EXT[ext],
                    "url": "/api/stickers/image?name=" + quote(n)})
    return {"rows": out}


def sticker_image(name: Any):
    path = _sticker_path(name)
    if path is None:
        return None, None
    ext = "." + path.name.rsplit(".", 1)[-1].lower()
    return path.read_bytes(), STICKER_EXT.get(ext, "application/octet-stream")


async def post_sticker(bot, cid: Any, name: Any, text: str = "", title: str = "",
                       color: str = "") -> Dict[str, Any]:
    path = _sticker_path(name)
    if path is None:
        return {"error": "Такого стикера нет в папке stickers/"}
    ch, err = await _resolve_channel(bot, cid)
    if err:
        return {"error": err}
    text = (text or "").strip()
    if len(text) > MAX_EMBED_TEXT:
        return {"error": f"Подпись длинная: {len(text)} знаков, максимум {MAX_EMBED_TEXT}"}
    filename = path.name
    emb = None
    if title or text:
        emb = disnake.Embed(title=str(title)[:MAX_EMBED_TITLE] if title else None,
                            description=text or None, color=disnake.Color(_hex_color(color)))
        emb.set_image(url=f"attachment://{filename}")
    miss = _missing_perms(bot, ch, ["view_channel", "send_messages", "attach_files"]
                          + (["embed_links"] if emb else []))
    if miss:
        return {"error": _no_perms_text(ch, miss)}
    try:
        msg = await ch.send(embed=emb, file=disnake.File(path, filename=filename))
    except Exception as e:
        return {"error": _discord_error(e)}
    out = {"link": msg.jump_url, "where": f"#{ch.name}", "sticker": filename}
    hint = _hygiene_hint(bot, ch)
    if hint:
        out["hint"] = hint
    return out


# ───────────────────────── Эмодзи из папки emojis/ ─────────────────────────
EMOJI_DIR = ROOT / "emojis"
EMOJI_MAP = ROOT / "emoji_map.json"
EMOJI_EXT = {".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
             ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
EMOJI_UPLOAD_MAX = 20     # за один раз: Discord режет создание эмодзи по скорости


def _emoji_name(filename: str) -> str:
    """Имя эмодзи на сервере из имени файла (то же правило, что у /emoji_folder)."""
    base = re.sub(r"\.[^.]+$", "", filename)
    base = re.sub(r"^\d+-", "", base)
    base = re.sub(r"[^A-Za-z0-9_]", "_", base)
    return (base if len(base) >= 2 else "e_" + base)[:32]


def _emoji_files() -> List[Path]:
    try:
        return sorted((p for p in EMOJI_DIR.iterdir()
                       if p.is_file() and p.suffix.lower() in EMOJI_EXT), key=lambda p: p.name.lower())
    except FileNotFoundError:
        return []


def emoji_catalog(bot) -> Dict[str, Any]:
    """Все эмодзи: файлы из папки и то, что уже живёт на сервере. Код вставляется в текст как есть."""
    guild = guild_of(bot)
    live: Dict[str, Any] = {}
    if guild is not None:
        live = {e.name.lower(): e for e in guild.emojis if e.name}
    saved: Dict[str, str] = {}
    if not live:
        try:
            saved = {k.lower(): v for k, v in json.loads(EMOJI_MAP.read_text(encoding="utf-8")).items()}
        except Exception:
            saved = {}

    rows: List[Dict[str, Any]] = []
    seen = set()
    for path in _emoji_files():
        name = _emoji_name(path.name)
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        animated = path.suffix.lower() == ".gif"
        row: Dict[str, Any] = {"name": name, "file": path.name, "animated": animated,
                               "size": round(path.stat().st_size / 1024), "uploaded": False,
                               "code": "", "url": "/api/emojis/image?file=" + quote(path.name)}
        e = live.get(key)
        if e is not None:
            row.update(uploaded=True, animated=bool(e.animated),
                       code=f"<{'a' if e.animated else ''}:{e.name}:{e.id}>",
                       url=f"https://cdn.discordapp.com/emojis/{e.id}.{'gif' if e.animated else 'png'}?size=64")
        elif key in saved:
            code = saved[key]
            m = re.fullmatch(r"<(a?):([A-Za-z0-9_]+):(\d+)>", code)
            if m:
                row.update(uploaded=True, animated=bool(m.group(1)), code=code,
                           url=f"https://cdn.discordapp.com/emojis/{m.group(3)}.{'gif' if m.group(1) else 'png'}?size=64")
        rows.append(row)

    for key, e in live.items():          # на сервере есть, а в папке файла нет
        if key in seen:
            continue
        rows.append({"name": e.name, "file": "", "animated": bool(e.animated), "size": 0, "uploaded": True,
                     "code": f"<{'a' if e.animated else ''}:{e.name}:{e.id}>", "only_server": True,
                     "url": f"https://cdn.discordapp.com/emojis/{e.id}.{'gif' if e.animated else 'png'}?size=64"})

    info: Dict[str, Any] = {"guild": guild.name if guild else None}
    if guild is not None:
        static = sum(1 for e in guild.emojis if not e.animated)
        anim = len(guild.emojis) - static
        limit = int(getattr(guild, "emoji_limit", 50) or 50)
        me = guild.me
        info.update(static=static, animated=anim, limit=limit,
                    can_manage=bool(me and me.guild_permissions.manage_emojis_and_stickers))
    return {"rows": rows, "info": info,
            "missing": sum(1 for r in rows if not r["uploaded"]),
            "total": len(rows)}


def emoji_image(file: Any):
    name = str(file or "")
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None, None
    path = EMOJI_DIR / name
    if not path.is_file() or path.suffix.lower() not in EMOJI_EXT:
        return None, None
    return path.read_bytes(), EMOJI_EXT[path.suffix.lower()]


def _fit_emoji(data: bytes, ext: str) -> Optional[bytes]:
    """Discord принимает эмодзи до 256 КБ. Крупные сжимаем, если есть Pillow и emoji_manager."""
    if len(data) <= 256 * 1024:
        return data
    mod = sys.modules.get("emoji_manager") or sys.modules.get("cogs.emoji_manager")
    fit = getattr(mod, "fit_image", None)
    if fit is None:
        try:
            import emoji_manager as mod  # type: ignore
            fit = getattr(mod, "fit_image", None)
        except Exception:
            fit = None
    return fit(data, ext) if fit else None


def _save_emoji_map(guild) -> None:
    emap = {e.name: f"<{'a' if e.animated else ''}:{e.name}:{e.id}>" for e in guild.emojis}
    EMOJI_MAP.write_text(json.dumps(emap, ensure_ascii=False, indent=2), encoding="utf-8")


async def emoji_upload(bot, names: Any) -> Dict[str, Any]:
    """Загружает на сервер эмодзи из папки, которых там ещё нет. names — список имён или пусто (все)."""
    guild = guild_of(bot)
    if guild is None:
        return {"error": "Бот ни на одном сервере"}
    me = guild.me
    if me is None or not me.guild_permissions.manage_emojis_and_stickers:
        return {"error": "У бота нет права «Управлять эмодзи и стикерами». Выдайте его роли бота на сервере."}
    want = {str(n).lower() for n in names} if isinstance(names, list) and names else None
    live = {e.name.lower() for e in guild.emojis if e.name}
    todo = []
    for path in _emoji_files():
        name = _emoji_name(path.name)
        if name.lower() in live or (want is not None and name.lower() not in want):
            continue
        if name.lower() in {n for n, _ in todo}:
            continue
        todo.append((name.lower(), path))
    if not todo:
        return {"uploaded": [], "failed": [], "left": 0, "message": "Все выбранные эмодзи уже на сервере"}
    batch, rest = todo[:EMOJI_UPLOAD_MAX], max(0, len(todo) - EMOJI_UPLOAD_MAX)

    static = sum(1 for e in guild.emojis if not e.animated)
    anim = len(guild.emojis) - static
    limit = int(getattr(guild, "emoji_limit", 50) or 50)
    done: List[str] = []
    failed: List[Dict[str, str]] = []
    for key, path in batch:
        is_gif = path.suffix.lower() == ".gif"
        if (anim if is_gif else static) >= limit:
            failed.append({"name": _emoji_name(path.name),
                           "error": f"На сервере кончились места под {'анимированные' if is_gif else 'обычные'} эмодзи ({limit})"})
            continue
        try:
            data = _fit_emoji(path.read_bytes(), path.suffix.lower())
            if data is None:
                failed.append({"name": _emoji_name(path.name), "error": "Больше 256 КБ и сжать не вышло"})
                continue
            await guild.create_custom_emoji(name=_emoji_name(path.name), image=data,
                                            reason="Веб-панель: эмодзи из папки emojis")
            done.append(_emoji_name(path.name))
            if is_gif:
                anim += 1
            else:
                static += 1
            await asyncio.sleep(0.7)
        except Exception as e:
            failed.append({"name": _emoji_name(path.name), "error": _discord_error(e)})
            if isinstance(e, disnake.HTTPException) and getattr(e, "status", 0) == 429:
                break
    try:
        _save_emoji_map(guild)
    except Exception:
        pass
    return {"uploaded": done, "failed": failed, "left": rest}


# ───────────────────────── 7. казна: отчёт со скринами ─────────────────────────

def kazna_reporters(bot) -> Dict[str, Any]:
    """Кто подаёт отчёт: список панели совпадает с проверкой прав в казне, а не с «всеми»."""
    guild = guild_of(bot)
    if guild is None:
        return {"rows": [], "error": "Бот ни на одном сервере"}
    cog = bot.get_cog("Treasury")
    if cog is None:
        return {"rows": [], "error": "Казна выключена — ког Treasury не загружен"}
    return cog.reporters(guild)


async def kazna_report(bot, fields: Dict[str, Any], photos: List[tuple]) -> Dict[str, Any]:
    """Отчёт о трате из панели: те же права, та же карточка в приёмке, скрины из формы.

    `photos` — (имя файла, mime, байты); в Discord они уходят из памяти, на 256 МБ хостинга
    писать скрины на диск незачем.
    """
    guild = guild_of(bot)
    if guild is None:
        return {"error": "Бот ни на одном сервере"}
    cog = bot.get_cog("Treasury")
    if cog is None:
        return {"error": "Казна выключена — ког Treasury не загружен"}
    actor = str(fields.get("actor") or "").strip()
    if not actor.isdigit():
        return {"error": "Выбери, кто подаёт отчёт"}
    member = guild.get_member(int(actor))
    if member is None:
        return {"error": "Этого человека нет на сервере"}
    return await cog.panel_report(member, str(fields.get("сумма") or ""),
                                  str(fields.get("на_что") or ""),
                                  str(fields.get("кому") or ""),
                                  str(fields.get("детали") or ""), photos)
