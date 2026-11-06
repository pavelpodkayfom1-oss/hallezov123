"""Действия ИИ: музыка, роли, мут, кик, сводка по казне и жалобам.

ИИ отвечает JSON-ом {"say": "...", "action": {...} | null}. Здесь этот ответ
разбирается и выполняется. Главное правило: права проверяются у того, кто ПРОСИТ, а не
у ИИ. Обычный участник может только заказать музыку и убавить громкость; мут, роли и
кик — только руководство (is_admin), и только тех, кто ниже по иерархии.
"""
import json
import re
from datetime import timedelta
from typing import Optional, Tuple

import disnake

from utils.checks import is_admin

ACTIONS_HELP = (
    "Ты умеешь не только отвечать, но и ДЕЛАТЬ. Отвечай СТРОГО одним JSON без пояснений:\n"
    '{"say": "что сказать человеку", "action": null}\n'
    "В action — null или одно из действий:\n"
    '- {"type":"music","query":"название, ссылка или жанр, например фонк"} — включить музыку в войсе\n'
    '- {"type":"music_ctl","do":"pause|skip|stop|volume","value":50} — управление музыкой '
    "(value только для volume, 5–100)\n"
    '- {"type":"mute","user":"ник или упоминание","minutes":10,"reason":"причина"} — таймаут\n'
    '- {"type":"unmute","user":"ник"} — снять таймаут\n'
    '- {"type":"role_add","user":"ник","role":"название роли"} и role_remove — выдать/снять роль\n'
    '- {"type":"kick","user":"ник","reason":"причина"} — выгнать с сервера\n'
    "Правила: делай действие только если человек прямо об этом попросил. Если непонятно, кого "
    "или что — action null, а в say коротко переспроси. В say — 1–2 коротких предложения, "
    "без эмодзи. Если просьба не про действия, просто ответь по делу, action null."
)

# ───────────────────────── разбор ответа модели ─────────────────────────


def parse_reply(raw: str) -> Tuple[str, Optional[dict]]:
    """Достаёт say и action. Если это не JSON — весь текст считаем обычным ответом."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    data = None
    for cand in (text, text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""):
        if not cand:
            continue
        try:
            data = json.loads(cand)
            break
        except Exception:
            continue
    if not isinstance(data, dict):
        return (raw or "").strip(), None
    say = str(data.get("say") or "").strip()
    act = data.get("action")
    if not isinstance(act, dict) or not act.get("type"):
        act = None
    return say, act


# ───────────────────────── поиск участника и роли ─────────────────────────


async def find_member(guild: disnake.Guild, ref, message: Optional[disnake.Message] = None):
    """(участник, ошибка). Упоминание в сообщении важнее имени."""
    if message is not None:
        ments = [m for m in message.mentions if not m.bot]
        if len(ments) == 1:
            m = guild.get_member(ments[0].id)
            if m:
                return m, ""
    text = str(ref or "").strip()
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 15:
        m = guild.get_member(int(digits))
        if m:
            return m, ""
    if not text:
        return None, "не понял, кого именно"
    low = text.lower().lstrip("@")
    pool = [m for m in guild.members if not m.bot]
    for pick in (lambda m: low in {m.display_name.lower(), m.name.lower(), (m.nick or "").lower()},
                 lambda m: m.display_name.lower().startswith(low) or m.name.lower().startswith(low),
                 lambda m: low in m.display_name.lower() or low in m.name.lower()):
        hits = [m for m in pool if pick(m)]
        if len(hits) == 1:
            return hits[0], ""
        if len(hits) > 1:
            return None, "под это имя подходит несколько человек: " + ", ".join(
                h.display_name for h in hits[:4]) + " — упомяни нужного через @"
    return None, f"не нашёл на сервере «{text[:30]}»"


def find_role(guild: disnake.Guild, ref, message: Optional[disnake.Message] = None):
    if message is not None and len(message.role_mentions) == 1:
        return message.role_mentions[0], ""
    low = str(ref or "").strip().lower().lstrip("@")
    if not low:
        return None, "не понял, какая роль"
    roles = [r for r in guild.roles if not r.is_default()]
    for pick in (lambda r: r.name.lower() == low, lambda r: low in r.name.lower()):
        hits = [r for r in roles if pick(r)]
        if len(hits) == 1:
            return hits[0], ""
        if len(hits) > 1:
            return None, "подходит несколько ролей: " + ", ".join(h.name for h in hits[:4])
    return None, f"роли «{str(ref)[:30]}» нет"


# ───────────────────────── проверки прав ─────────────────────────


def _can_touch(guild, asker, target) -> str:
    """Пустая строка — можно, иначе причина отказа."""
    if target.bot:
        return "ботов трогать нельзя"
    if target.id == asker.id:
        return "себя трогать нельзя"
    if target.id == guild.owner_id:
        return "владельца сервера трогать нельзя"
    if asker.id != guild.owner_id and target.top_role >= asker.top_role:
        return "он по иерархии равен тебе или старше"
    if target.top_role >= guild.me.top_role:
        return "его роль выше моей — Discord мне не даст"
    return ""


def _need_admin(asker) -> str:
    return "" if is_admin(asker) else "это только для руководства"


# ───────────────────────── выполнение ─────────────────────────


async def run(cog, message: disnake.Message, asker: disnake.Member, act: dict) -> Tuple[bool, str]:
    """Выполнить действие. Возвращает (получилось, короткий текст для человека)."""
    kind = str(act.get("type") or "")
    guild = message.guild
    try:
        if kind == "music":
            return await _music(cog, guild, asker, str(act.get("query") or ""))
        if kind == "music_ctl":
            return await _music_ctl(cog, guild, asker, str(act.get("do") or ""), act.get("value"))
        if kind in ("mute", "unmute", "role_add", "role_remove", "kick"):
            why = _need_admin(asker)
            if why:
                return False, "Нет: " + why
            if kind == "mute":
                return await _mute(cog, guild, asker, message, act)
            if kind == "unmute":
                return await _unmute(guild, asker, message, act)
            if kind in ("role_add", "role_remove"):
                return await _role(guild, asker, message, act, add=(kind == "role_add"))
            return await _kick_prepare(guild, asker, message, act)
    except disnake.Forbidden:
        return False, "Discord не дал это сделать: не хватает прав у меня"
    except Exception as e:
        return False, f"не вышло: {type(e).__name__}: {str(e)[:120]}"
    return False, "не знаю такого действия"


async def _music(cog, guild, asker, query: str) -> Tuple[bool, str]:
    music = cog.bot.get_cog("Music")
    if music is None:
        return False, "музыка на сервере выключена"
    query = query.strip()
    if not query:
        return False, "не понял, что включить"
    voice = getattr(asker, "voice", None)
    if not voice or not voice.channel:
        return False, "зайди в голосовой канал — играю там, где сидят люди"
    vc = guild.voice_client
    if vc is None:
        vc = await voice.channel.connect(timeout=20)
    elif vc.channel.id != voice.channel.id:
        if vc.is_playing() or vc.is_paused():
            return False, f"я уже играю в «{vc.channel.name}» — зайди туда"
        await vc.move_to(voice.channel)
    res = await music.web_enqueue(guild, query, asker.mention, key=str(asker.id))
    if res.get("ok"):
        return True, str(res.get("did") or "включил")
    return False, str(res.get("error") or "не вышло")


async def _music_ctl(cog, guild, asker, do: str, value) -> Tuple[bool, str]:
    music = cog.bot.get_cog("Music")
    if music is None:
        return False, "музыка на сервере выключена"
    vc = guild.voice_client
    voice = getattr(asker, "voice", None)
    if not vc or not voice or not voice.channel or voice.channel.id != vc.channel.id:
        return False, "зайди в тот голосовой канал, где я играю"
    if do not in ("pause", "skip", "stop", "volume"):
        return False, "не понял, что сделать с музыкой"
    if do in ("skip", "stop") and not is_admin(asker):
        return False, "пропускать и останавливать может только руководство"
    res = await music.web_action(guild, do, value, who=asker.display_name)
    if res.get("ok"):
        return True, str(res.get("did") or "готово")
    return False, str(res.get("error") or "не вышло")


async def _mute(cog, guild, asker, message, act) -> Tuple[bool, str]:
    from cogs import modpanel as mp

    member, err = await find_member(guild, act.get("user"), message)
    if not member:
        return False, err
    why = _can_touch(guild, asker, member)
    if why:
        return False, "Нет: " + why
    if not guild.me.guild_permissions.moderate_members:
        return False, "у меня нет права «Управлять участниками»"
    try:
        minutes = int(float(act.get("minutes") or 0))
    except (TypeError, ValueError):
        minutes = 0
    if minutes <= 0:
        return False, "не понял срок, скажи на сколько минут"
    if minutes > mp.MAX_MINUTES:
        return False, "максимум 28 дней — это лимит Discord"
    reason = (str(act.get("reason") or "").strip() or "Решение руководства семьи")[:300]
    await member.edit(timeout=timedelta(minutes=minutes),
                      reason=f"{asker.display_name} через ИИ: {reason}")
    from datetime import datetime, timezone

    mp._log_add({"id": int(datetime.now(timezone.utc).timestamp() * 1000), "guild": guild.id,
                 "target": member.id, "moderator": asker.id, "minutes": minutes,
                 "reason": reason + " (через ИИ)", "at": datetime.now(mp.MSK).strftime("%d.%m.%Y %H:%M")})
    cog_mod = cog.bot.get_cog("ModPanel")
    if cog_mod is not None:
        try:
            await cog_mod._to_log(guild, member, asker, minutes, reason)
        except Exception:
            pass
    return True, f"{member.display_name} в таймауте на {mp._human(minutes)}"


async def _unmute(guild, asker, message, act) -> Tuple[bool, str]:
    member, err = await find_member(guild, act.get("user"), message)
    if not member:
        return False, err
    if not guild.me.guild_permissions.moderate_members:
        return False, "у меня нет права «Управлять участниками»"
    await member.edit(timeout=None, reason=f"Размут: {asker.display_name} через ИИ")
    return True, f"{member.display_name} размучен"


async def _role(guild, asker, message, act, add: bool) -> Tuple[bool, str]:
    member, err = await find_member(guild, act.get("user"), message)
    if not member:
        return False, err
    role, err = find_role(guild, act.get("role"), message)
    if not role:
        return False, err
    why = _can_touch(guild, asker, member) if member.id != asker.id else ""
    if why:
        return False, "Нет: " + why
    if role.managed:
        return False, "эта роль служебная, её выдаёт интеграция"
    if role.permissions.administrator:
        return False, "роли с правами администратора через ИИ не выдаю"
    if asker.id != guild.owner_id and role >= asker.top_role:
        return False, "эта роль выше или равна твоей"
    if role >= guild.me.top_role:
        return False, "эта роль выше моей — Discord не даст"
    if not guild.me.guild_permissions.manage_roles:
        return False, "у меня нет права «Управлять ролями»"
    reason = f"{asker.display_name} через ИИ"
    if add:
        await member.add_roles(role, reason=reason)
        return True, f"выдал роль «{role.name}»: {member.display_name}"
    await member.remove_roles(role, reason=reason)
    return True, f"снял роль «{role.name}»: {member.display_name}"


# ───────────────────────── кик: только с подтверждением ─────────────────────────


class KickConfirm(disnake.ui.View):
    """Кик необратим, а имя могло распознаться не так — поэтому спрашиваем кнопкой."""

    def __init__(self, asker_id: int, target_id: int, reason: str):
        super().__init__(timeout=30)
        self.asker_id, self.target_id, self.reason = asker_id, target_id, reason

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:
        if inter.author.id != self.asker_id:
            await inter.response.send_message("Это не твоя кнопка.", ephemeral=True)
            return False
        return True

    @disnake.ui.button(label="Да, кикнуть", style=disnake.ButtonStyle.danger)
    async def yes(self, button, inter: disnake.MessageInteraction):
        guild = inter.guild
        member = guild.get_member(self.target_id)
        if member is None:
            await inter.response.edit_message(content="Его уже нет на сервере.", view=None)
            return
        why = _can_touch(guild, inter.author, member) or _need_admin(inter.author)
        if why:
            await inter.response.edit_message(content="Нет: " + why, view=None)
            return
        if not guild.me.guild_permissions.kick_members:
            await inter.response.edit_message(content="У меня нет права «Выгонять участников».", view=None)
            return
        try:
            await member.kick(reason=f"{inter.author.display_name} через ИИ: {self.reason}")
        except Exception as e:
            await inter.response.edit_message(content=f"Не вышло: {str(e)[:120]}", view=None)
            return
        await inter.response.edit_message(content=f"{member.display_name} кикнут.", view=None)

    @disnake.ui.button(label="Отмена", style=disnake.ButtonStyle.secondary)
    async def no(self, button, inter: disnake.MessageInteraction):
        await inter.response.edit_message(content="Отменил.", view=None)


async def _kick_prepare(guild, asker, message, act) -> Tuple[bool, str]:
    member, err = await find_member(guild, act.get("user"), message)
    if not member:
        return False, err
    why = _can_touch(guild, asker, member)
    if why:
        return False, "Нет: " + why
    if not guild.me.guild_permissions.kick_members:
        return False, "у меня нет права «Выгонять участников»"
    reason = (str(act.get("reason") or "").strip() or "Решение руководства семьи")[:300]
    await message.reply(f"Кикнуть **{member.display_name}**? Причина: {reason}",
                        view=KickConfirm(asker.id, member.id, reason), mention_author=False,
                        allowed_mentions=disnake.AllowedMentions.none())
    return True, f"жду подтверждения кика: {member.display_name}"


# ───────────────────────── сводка для руководства: казна и жалобы ─────────────────────────


async def staff_context(guild: disnake.Guild) -> str:
    """Казна и жалобы — только для руководства. Любая часть может отсутствовать."""
    lines = []
    try:
        from utils import famq_core as core, guild_cfg

        if guild_cfg.is_home(guild.id):
            ws = core.week_start_ts()
            bal = await core.treasury_balance(guild.id)
            inc = await core.q_one("SELECT COALESCE(SUM(amount),0) AS s FROM treasury "
                                   "WHERE guild_id=? AND kind='deposit' AND created_ts>=?", (guild.id, ws))
            out = await core.q_one("SELECT COALESCE(SUM(amount),0) AS s FROM treasury "
                                   "WHERE guild_id=? AND kind='spend' AND created_ts>=?", (guild.id, ws))
            lines.append(f"Казна семьи: остаток {int(bal)}, за неделю приход {int((inc or {}).get('s') or 0)}, "
                         f"расход {int((out or {}).get('s') or 0)}.")
            due = int(core.get("treasury.weekly_due", 0) or 0)
            if due > 0:
                rows = await core.q_all("SELECT user_id, SUM(amount) AS s FROM treasury WHERE guild_id=? "
                                        "AND kind='deposit' AND created_ts>=? GROUP BY user_id", (guild.id, ws))
                paid = {r["user_id"]: int(r["s"]) for r in rows}
                debt = [m.display_name for m in guild.members
                        if not m.bot and core.member_rank(m) >= 1 and core.pause_kind(m) != "vacation"
                        and paid.get(m.id, 0) < due]
                if debt:
                    lines.append(f"Не сдали взнос ({due}) за неделю: {len(debt)} чел., например "
                                 + ", ".join(debt[:8]) + ".")
                else:
                    lines.append("Все сдали недельный взнос.")
    except Exception:
        pass
    try:
        from cogs.complaints import _srv as cmp_srv

        cases = list((cmp_srv(guild.id).get("cases") or {}).values())
        cases.sort(key=lambda c: -int(c.get("id") or 0))
        words = {"wait": "в работе", "ok": "принята", "no": "отклонена"}
        for c in cases[:5]:
            lines.append(f"Жалоба №{c.get('id')} ({words.get(c.get('status') or 'wait', 'в работе')}) "
                         f"от {c.get('nick') or '?'}: {str(c.get('what') or '')[:120]}")
    except Exception:
        pass
    return "\n".join(lines)
