import asyncio
import io
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict

import disnake
from disnake.ext import commands

from utils.attendance import ACTIONS, get_votes, render, save_votes, toggle_vote
from utils.checks import load_config, is_recruiter, get_text
from utils.embeds import base_embed
from utils.event_art import make_event_art, normalize_time

_locks: Dict[int, asyncio.Lock] = {}

# ───────────────────── напоминание о сборе ─────────────────────
# «⏰ Напомнить за час» на сообщении сбора: бот пишет лично в ЛС за час до сбора.
# Список сборов лежит в файле, поэтому напоминание доживает до рестарта бота.
MSK = timezone(timedelta(hours=3))
REMIND_BEFORE = 3600
REMIND_TICK = 60
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, "sbor_reminders.json")
_TIME_IN_TEXT = re.compile(r"(\d{1,2})[:.](\d{2})")

_REPLIES = {
    ("yes", "added"): "✅ Отмечено: вы **будете** на сборе.",
    ("yes", "changed"): "✅ Изменено: вы **будете** на сборе.",
    ("late", "added"): "🟡 Отмечено: вы **опоздаете**.",
    ("late", "changed"): "🟡 Изменено: вы **опоздаете**.",
    ("no", "added"): "🔴 Отмечено: вы **не сможете** прийти.",
    ("no", "changed"): "🔴 Изменено: вы **не сможете** прийти.",
}


def _parse_when(raw: str):
    """'19:15 МСК' -> ближайшие 19:15 по Москве; время уже прошло — значит завтра."""
    text = normalize_time(str(raw or ""))
    m = _TIME_IN_TEXT.search(text)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if hh > 23 or mm > 59:
        return None
    now = datetime.now(MSK)
    when = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if when <= now:
        when += timedelta(days=1)
    return when


def _when_from_embed(emb) -> str:
    """Время из карточки сбора: «⏰ Время сбора: 19:15 МСК»."""
    parts = [str(getattr(emb, "description", "") or ""), str(getattr(emb, "title", "") or "")]
    footer = getattr(emb, "footer", None)
    if footer is not None:
        parts.append(str(footer.text or ""))
    m = re.search(r"\d{1,2}[:.]\d{2}(?!\d)", " ".join(parts))
    return m.group(0) if m else ""


def _load_events() -> Dict[str, dict]:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def _save_events(data: Dict[str, dict]) -> str:
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
        return ""
    except Exception as e:
        return f"Файл напоминаний не записался: `{e}`"


def _event_time(emb):
    raw = _when_from_embed(emb)
    return _parse_when(raw), raw


def _waiting(n: int) -> str:
    """«1 человек ждёт», «2 человека ждут», «11 человек ждут»."""
    word = "человек"
    if n % 100 not in (11, 12, 13, 14):
        if n % 10 == 1:
            word = "человек"
        elif n % 10 in (2, 3, 4):
            word = "человека"
    return f"{n} {word} " + ("ждёт" if n == 1 else "ждут")


async def handle_attendance(inter: disnake.MessageInteraction, action: str):
    """Записывает выбор, обновляет списки в сообщении. Повторное нажатие снимает отметку."""
    msg = inter.message
    if not msg.embeds or len(msg.embeds[0].fields) < len(ACTIONS):
        return await inter.response.send_message("⚠️ Это сообщение сбора повреждено.", ephemeral=True)

    if len(_locks) > 500:
        for k in [k for k, v in _locks.items() if not v.locked()]:
            _locks.pop(k, None)
    lock = _locks.setdefault(msg.id, asyncio.Lock())

    async with lock:
        emb = msg.embeds[0]
        votes = get_votes(msg.id, [f.value for f in emb.fields[: len(ACTIONS)]])
        result = toggle_vote(votes, inter.author.id, action)
        save_votes(msg.id, votes)
        for i, a in enumerate(ACTIONS):
            emb.set_field_at(i, name=emb.fields[i].name, value=render(votes[a]), inline=True)
        await inter.response.edit_message(embed=emb)

    note = "↩️ Отметка снята." if result == "removed" else _REPLIES[(action, result)]
    try:
        await inter.followup.send(note, ephemeral=True)
    except disnake.HTTPException:
        pass


class _AttendButton(disnake.ui.Button):
    def __init__(self, action: str, label: str, style: disnake.ButtonStyle, emoji: str):
        super().__init__(label=label[:80], style=style, emoji=emoji, custom_id=f"codex:event_{action}")
        self.action = action

    async def callback(self, inter: disnake.MessageInteraction):
        await handle_attendance(inter, self.action)


async def handle_remind(inter: disnake.MessageInteraction):
    """Кнопка «⏰ Напомнить за час»: подписывает (или отписывает) автора на ЛС-напоминание."""
    msg = inter.message
    if not msg.embeds:
        return await inter.response.send_message("⚠️ Это сообщение сбора повреждено.", ephemeral=True)
    key = str(msg.id)
    events = _load_events()
    entry = events.get(key)
    if entry is None:
        when, raw = _event_time(msg.embeds[0])
        if when is None:
            return await inter.response.send_message(
                "В карточке этого сбора не удалось разобрать время — напоминание не поставить.",
                ephemeral=True)
        entry = {"when": when.isoformat(), "shown": raw,
                 "title": str(msg.embeds[0].title or "сбор")[:200], "people": [],
                 "guild": msg.guild.id if msg.guild else 0, "channel": msg.channel.id,
                 "fired": False}
        if when - datetime.now(MSK) <= timedelta(seconds=REMIND_BEFORE):
            return await inter.response.send_message(
                f"⏰ До сбора «{entry['title']}» меньше часа — напоминать уже поздно.", ephemeral=True)
        events[key] = entry

    people = [int(p) for p in entry.get("people") or []]
    if entry.get("fired"):
        return await inter.response.send_message("⏰ Напоминание по этому сбору уже ушло.", ephemeral=True)
    if inter.author.id in people:
        people.remove(inter.author.id)
        answer = f"↩️ Отписал. Напоминаний в ЛС не будет ({_waiting(len(people))})."
    else:
        people.append(inter.author.id)
        when = datetime.fromisoformat(entry["when"])
        answer = (f"⏰ Напомню в ЛС за час до сбора: **{when.astimezone(MSK).strftime('%d.%m %H:%M')} МСК.**"
                  f"\nЖдут напоминания: {_waiting(len(people))}. Повторное нажатие — отписаться.")
    entry["people"] = people
    events[key] = entry
    err = _save_events(events)
    if err:
        answer += f"\n⚠️ {err}"
    await inter.response.send_message(answer, ephemeral=True)


class _RemindButton(disnake.ui.Button):
    def __init__(self, label: str):
        super().__init__(label=label[:80], style=disnake.ButtonStyle.secondary, emoji="⏰",
                         custom_id="codex:event_remind")

    async def callback(self, inter: disnake.MessageInteraction):
        await handle_remind(inter)


class EventAttendanceView(disnake.ui.View):
    """Постоянное представление: работает и после перезапуска бота (см. main.py)."""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(_AttendButton("yes", get_text("btn_event_yes", "Буду (+)"),
                                    disnake.ButtonStyle.success, "🟢"))
        self.add_item(_AttendButton("late", get_text("btn_event_late", "Опоздаю (+-)"),
                                    disnake.ButtonStyle.secondary, "🟡"))
        self.add_item(_AttendButton("no", get_text("btn_event_no", "Не смогу (-)"),
                                    disnake.ButtonStyle.danger, "🔴"))
        self.add_item(_RemindButton(get_text("btn_event_remind", "Напомнить за час")))


class Events(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._task = None

    @commands.Cog.listener()
    async def on_ready(self):
        if self._task is None or self._task.done():
            self._task = self.bot.loop.create_task(self._remind_loop())

    def cog_unload(self):
        if self._task is not None:
            self._task.cancel()

    async def _remind_loop(self):
        """Раз в минуту смотрим, не пора ли напомнить о сборе."""
        while True:
            try:
                await asyncio.sleep(REMIND_TICK)
                await self._fire_due()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"⏰ Напоминания: `{e}`", flush=True)

    async def _fire_due(self):
        events = _load_events()
        if not events:
            return
        now = datetime.now(MSK)
        changed = False
        for key, entry in list(events.items()):
            try:
                when = datetime.fromisoformat(str(entry.get("when")))
            except Exception:
                events.pop(key, None)
                changed = True
                continue
            if now > when + timedelta(hours=2):          # сбор отзвучал — чистим
                events.pop(key, None)
                changed = True
                continue
            if entry.get("fired"):
                continue
            if now < when - timedelta(seconds=REMIND_BEFORE):
                continue
            await self._remind(key, entry, when, now)
            entry["fired"] = True
            changed = True
        if changed:
            _save_events(events)

    async def _remind(self, key: str, entry: dict, when: datetime, now: datetime):
        left = max(0, int((when - now).total_seconds() // 60))
        title = str(entry.get("title") or "сбор семьи")
        text = (f"⏰ **Через {left} мин сбор:** {title}\n"
                f"Начало в **{when.strftime('%H:%M')} МСК** — выходи уже сейчас.")
        missed = []
        for uid in entry.get("people") or []:
            user = self.bot.get_user(int(uid)) if int(uid) else None
            try:
                if user is None:
                    user = await self.bot.fetch_user(int(uid))
                await user.send(text)
            except Exception:
                missed.append(int(uid))
        sent = len(entry.get("people") or []) - len(missed)
        print(f"⏰ Напоминание о сборе {title}: дошло {sent}, не дошло {len(missed)}", flush=True)
        if not missed:
            return
        ch = self.bot.get_channel(int(entry.get("channel") or 0))
        if ch is None:
            return
        who = " ".join(f"<@{uid}>" for uid in missed[:20])
        try:
            await ch.send(f"{text}\n{who} — в ЛС написать не удалось.")
        except Exception:
            pass

    @commands.slash_command(
        name="event",
        description="Создать сбор семьи на мероприятие (Капт, ВЗП, Дроп, Поставка)"
    )
    async def event_command(
        self,
        inter: disnake.ApplicationCommandInteraction,
        title: str = commands.Param(description="Название мероприятия (например: Дроп 19:30 / ВЗП)"),
        time_str: str = commands.Param(description="Время сбора (например: 19:15 МСК)"),
        info: str = commands.Param(description="Снаряжение и место сбора", default="Особняк семьи, 100% бронежилеты, оружие"),
    ):
        if not is_recruiter(inter.author):
            await inter.response.send_message("❌ У вас нет прав для создания сборов семьи!", ephemeral=True)
            return

        config = load_config()
        family_role_id = config.get("roles", {}).get("family_role_id")
        mention_str = f"<@&{family_role_id}>" if family_role_id else "@everyone"

        title_tpl = get_text("event_card_title", "⚔️ ОБЩИЙ СБОР СЕМЬИ: {title}")
        desc_tpl = get_text(
            "event_card_desc",
            "Руководство объявило сбор бойцов **Hallez FAMQ**!\n\n⏰ **Время сбора:** `{time}`\n📍 **Требования:** {info}\n\nОбязательно прожмите статус присутствия кнопками ниже:"
        )

        event_emb = base_embed(
            title_tpl.format(title=title),
            desc_tpl.format(time=normalize_time(time_str), info=info),
            guild=inter.guild
        )

        lbl_yes = get_text("btn_event_yes", "Буду (+)")
        lbl_late = get_text("btn_event_late", "Опоздаю (+-)")
        lbl_no = get_text("btn_event_no", "Не смогу (-)")

        event_emb.add_field(name=f"🟢 {lbl_yes}", value="*Пока никто*", inline=True)
        event_emb.add_field(name=f"🟡 {lbl_late}", value="*Пока никто*", inline=True)
        event_emb.add_field(name=f"🔴 {lbl_no}", value="*Пока никто*", inline=True)

        # Картинка под каждый сбор: нейросеть рисует новую сцену, название/время/место
        # накладывает бот. Генерация занимает несколько секунд, поэтому сначала «думаем».
        await inter.response.defer()
        art = await make_event_art(title, time_str, info, inter.guild.name if inter.guild else "Hallez FAMQ")
        extra = {}
        if art.data:
            event_emb.set_image(url="attachment://sbor.jpg")
            extra["file"] = disnake.File(io.BytesIO(art.data), filename="sbor.jpg")
        card = await inter.edit_original_response(content=f"📢 {mention_str}", embed=event_emb,
                                                  view=EventAttendanceView(), **extra)

        # ⏰ Заводим карточку сбора в список напоминаний: кнопка на сообщении только
        # подписывает людей, а время и канал бот уже знает.
        when = _parse_when(time_str)
        notes = []
        if when is not None:
            events = _load_events()
            events[str(card.id)] = {"when": when.isoformat(), "shown": normalize_time(time_str),
                                     "title": title[:200], "people": [],
                                     "guild": card.guild.id if card.guild else 0,
                                     "channel": card.channel.id, "fired": False}
            err = _save_events(events)
            if err:
                notes.append(f"⚠️ {err}")
            notes.append(f"⏰ Сбор намечен на **{when.strftime('%d.%m %H:%M')} МСК**. "
                         "Кто нажмёт «⏰ Напомнить за час» — тому бот напишет в ЛС.")
        else:
            notes.append(f"⏰ Время «{time_str}» не разобрал (нужно вроде 19:15) — "
                         "напоминать не буду.")

        # Если нейросеть не сработала, автор сразу видит почему (остальные этого не видят).
        if not art.ai_ok:
            notes.insert(0, f"⚠️ {art.problem_text()}")
        if notes:
            try:
                await inter.followup.send("\n".join(notes), ephemeral=True)
            except disnake.HTTPException:
                pass


def setup(bot: commands.Bot):
    bot.add_cog(Events(bot))
