"""Дни рождения, стаж в семье и события — с автопоздравлением.

Раньше даты держались в головах: поздравления писали в чат вручную и часто забывались. Здесь бот
сам ведёт «Скоро» (день рождения бойца, сколько лет/месяцев в семье, общие события) и в
00:05 по Москве отправляет поздравление в выбранный канал — один раз в сутки, без повторов.

Стаж брать неоткуда, кроме Discord: `member.joined_at` уже есть у каждого бойца, поэтому
годовщины считаются сами, а ручная запись нужна только для дней рождения и событий.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import disnake
from disnake.ext import commands, tasks

from utils import brand
from utils.checks import get_text, is_admin, load_config
from utils.embeds import card, clip, list_embed, note_embed, until

MSK = timezone(timedelta(hours=3))
STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "dates.json")
LOOKAHEAD_DAYS = 45
_MD = re.compile(r"^(\d{1,2})[.\/-](\d{1,2})$")


def _load() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("users", {})
    data.setdefault("events", [])
    data.setdefault("posted", {})
    data.setdefault("channel", 0)
    return data


def _save(data: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def _md(raw: str) -> Optional[Tuple[int, int]]:
    """'25.10' → (25, 10) только если такая дата бывает."""
    m = _MD.match(str(raw or "").strip())
    if not m:
        return None
    dd, mo = int(m.group(1)), int(m.group(2))
    if not (1 <= dd <= 31 and 1 <= mo <= 12):
        return None
    try:
        datetime(2000, mo, dd)
    except ValueError:
        return None
    return dd, mo


def _next_year(md: Tuple[int, int]) -> datetime:
    today = datetime.now(MSK).date()
    for year in (today.year, today.year + 1):
        try:
            day = datetime(year, md[1], md[0], tzinfo=MSK)
        except ValueError:
            continue
        if day.date() >= today:
            return day
    return datetime(today.year + 1, md[1], md[0], tzinfo=MSK)


def _years_since(dt: datetime) -> Tuple[int, str]:
    """Сколько полных лет и человекочитаемый стаж («2 г 3 мес»)."""
    today = datetime.now(MSK)
    years = today.year - dt.year - ((today.month, today.day) < (dt.month, dt.day))
    months = (today.year - dt.year) * 12 + (today.month - dt.month)
    if today.day < dt.day:
        months -= 1
    y, m = divmod(max(months, 0), 12)
    parts = [f"{y} г"] if y else []
    if m:
        parts.append(f"{m} мес")
    return years, " ".join(parts) or "только пришёл"


def _anniversary(joined: datetime) -> Optional[datetime]:
    """Ближайшая годовщина вступления (не раньше сегодня)."""
    today = datetime.now(MSK).date()
    for year in (today.year, today.year + 1):
        try:
            day = datetime(year, joined.month, joined.day, tzinfo=MSK)
        except ValueError:
            continue
        if day.date() >= today:
            return day
    return None


def _years_word(n: int) -> str:
    """1 год / 2 года / 5 лет: «год(а)» в поздравлении выглядит служебно."""
    if n % 10 == 1 and n % 100 != 11:
        return "год"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "года"
    return "лет"


def _upcoming(data: dict, guild: Optional[disnake.Guild]) -> List[dict]:
    """Один список «скоро»: дни рождения, годовщины стажа и события, по дате."""
    today = datetime.now(MSK).date()
    out = []
    for uid, info in (data.get("users") or {}).items():
        md = _md(info.get("date", ""))
        if not md:
            continue
        day = _next_year(md)
        age = None
        if info.get("year"):
            try:
                age = day.year - int(info["year"])
            except (TypeError, ValueError):
                age = None
        out.append({"kind": "bday", "ts": day.timestamp(), "user": int(uid),
                    "label": f"{day:%d.%m}", "age": age, "note": info.get("note") or ""})
    if guild:
        for member in guild.members:
            if member.bot or not member.joined_at:
                continue
            joined = member.joined_at.astimezone(MSK)
            day = _anniversary(joined)
            if not day:
                continue
            out.append({"kind": "staff", "ts": day.timestamp(), "user": member.id,
                        "label": f"{day:%d.%m}",
                        # Считаем от самой годовщины: иначе за месяц до неё бот пишет «0 лет».
                        "years": day.year - joined.year,
                        "human": _years_since(joined)[1], "note": ""})
    for ev in data.get("events") or []:
        md = _md(ev.get("date", ""))
        if not md:
            continue
        stored = float(ev.get("ts") or 0)
        if ev.get("repeat"):
            # Повтор каждый год: берём ближайшую дату и часы из записи, иначе сбор
            # превратится в полночь.
            base = datetime.fromtimestamp(stored, MSK) if stored else datetime(2000, 1, 1, 0, 0, tzinfo=MSK)
            day = _next_year(md).replace(hour=base.hour, minute=base.minute)
            ts = day.timestamp()
        else:
            ts = stored
        if ts and datetime.fromtimestamp(ts, MSK).date() < today:
            continue
        out.append({"kind": "event", "ts": ts, "label": f"{datetime.fromtimestamp(ts, MSK):%d.%m}",
                    "title": ev.get("label", "Событие"), "note": ev.get("note") or ""})
    horizon = today + timedelta(days=LOOKAHEAD_DAYS)
    return sorted([x for x in out
                   if x["ts"] and datetime.fromtimestamp(x["ts"], MSK).date() <= horizon],
                  key=lambda x: x["ts"])


def _kind_line(bot, row: dict) -> Tuple[str, str]:
    if row["kind"] == "bday":
        icon = brand.emoji(bot, "hz_gifticon", "🎂")
        head = f"{icon} <@{row['user']}>"
        age = row.get("age")
        body = (f"день рождения {row['label']}"
                + (f" · исполнится **{age} {_years_word(age)}**" if age else ""))
        return head, body + (f"\n{row['note']}" if row["note"] else "")
    if row["kind"] == "staff":
        icon = brand.emoji(bot, "hz_house", "🏠")
        return (f"{icon} <@{row['user']}>",
                f"{row['label']} — {row['years']} {_years_word(row['years'])} в семье · "
                f"сейчас {row['human']}")
    icon = brand.emoji(bot, "hz_book", "📌")
    return (f"{icon} {clip(row['title'], 60)}",
            f"{row['label']} · {until(row['ts'])}" + (f"\n{row['note']}" if row["note"] else ""))


async def _panel(bot, guild: Optional[disnake.Guild]) -> disnake.Embed:
    data = _load()
    rows = _upcoming(data, guild)
    cells = []
    for row in rows[:20]:
        head, body = _kind_line(bot, row)
        cells.append([head, body])
    where = (f"<#{data['channel']}>" if data.get("channel")
             else "не выбран — нажми «📣 Канал для поздравлений»")
    return list_embed(brand.emoji(bot, "hz_book", "🗓") + "  Календарь семьи", cells,
                      guild=guild, inline=False,
                      empty=f"Ближайших {LOOKAHEAD_DAYS} дней пусто: нажми «🎂 Мой день рождения» "
                            "или «📌 Добавить событие» под этим календарём.",
                      note=f"Показаны следующие {LOOKAHEAD_DAYS} дней. Поздравления идут в {where}.")


class BirthdayModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(
            title="Мой день рождения",
            custom_id="hzdate:bday",
            components=[
                disnake.ui.TextInput(label="Дата (25.10)", custom_id="date",
                                     style=disnake.TextInputStyle.short, placeholder="25.10",
                                     max_length=8),
                disnake.ui.TextInput(label="Год рождения (можно пустой)", custom_id="year",
                                     style=disnake.TextInputStyle.short, placeholder="2004",
                                     max_length=6, required=False),
                disnake.ui.TextInput(label="Что сказать в поздравлении (можно пустой)",
                                     custom_id="note", style=disnake.TextInputStyle.short,
                                     placeholder="любит ночные обзвоны", max_length=120,
                                     required=False),
            ])

    async def callback(self, inter: disnake.ModalInteraction):
        cog: "Dates" = inter.bot.get_cog("Dates")
        await cog.set_birthday(inter, inter.text_values["date"].strip(),
                               inter.text_values.get("year", "").strip(),
                               inter.text_values.get("note", "").strip())


class EventModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(
            title="Событие семьи",
            custom_id="hzdate:event",
            components=[
                disnake.ui.TextInput(label="Что", custom_id="label",
                                     style=disnake.TextInputStyle.short,
                                     placeholder="Общий сбор семьи", max_length=80),
                disnake.ui.TextInput(label="Дата (25.10)", custom_id="date",
                                     style=disnake.TextInputStyle.short, placeholder="25.10",
                                     max_length=8),
                disnake.ui.TextInput(label="Время (19:30, можно пустой)", custom_id="time",
                                     style=disnake.TextInputStyle.short, placeholder="19:30",
                                     max_length=5, required=False),
                disnake.ui.TextInput(label="Повторять каждый год? (д/н)", custom_id="repeat",
                                     style=disnake.TextInputStyle.short, placeholder="н",
                                     max_length=2, required=False),
            ])

    async def callback(self, inter: disnake.ModalInteraction):
        cog: "Dates" = inter.bot.get_cog("Dates")
        await cog.add_event(inter, inter.text_values["label"].strip(),
                            inter.text_values["date"].strip(),
                            inter.text_values.get("time", "").strip(),
                            inter.text_values.get("repeat", "").strip())


class EventPickView(disnake.ui.View):
    """Одноразовый селект «какое событие убрать» — лежит под ephemeral-ответом."""

    def __init__(self, events: List[dict]):
        super().__init__(timeout=600)
        # Discord не принимает селект без вариантов — список передаёт вызывающий, он пустым не бывает.
        for i, event in enumerate(events[:25]):
            self.del_pick.add_option(label=clip(event.get("label", "Событие"), 90), value=str(i))

    @disnake.ui.select(custom_id="hzdate:del_pick",
                       placeholder="Какое событие убрать?", max_values=1)
    async def del_pick(self, select: disnake.ui.Select, inter: disnake.MessageInteraction):
        if not is_admin(inter.author):
            return
        data = _load()
        events = data.get("events") or []
        try:
            events.pop(int(select.values[0]))
        except (ValueError, IndexError):
            return await inter.response.edit_message(
                embed=note_embed("❌ Такое событие уже убрали."), view=None)
        data["events"] = events
        _save(data)
        await inter.response.edit_message(embed=note_embed("🗑 Событие убрано из календаря."),
                                          view=None)


class DatesView(disnake.ui.View):
    """Постоянная панель календаря: кнопки и выбор канала живут под сообщением `/даты`."""

    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="Мой день рождения", emoji="🎂", style=disnake.ButtonStyle.secondary,
                       custom_id="hzdate:me", row=0)
    async def me(self, button, inter: disnake.MessageInteraction):
        await inter.response.send_modal(BirthdayModal())

    @disnake.ui.button(label="Добавить событие", emoji="📌", style=disnake.ButtonStyle.secondary,
                       custom_id="hzdate:add", row=0)
    async def add(self, button, inter: disnake.MessageInteraction):
        if not is_admin(inter.author):
            return await inter.response.send_message(
                embed=note_embed("❌ События добавляет руководство семьи." +
                                 " Свой день рождения — кнопкой «🎂 Мой день рождения»."),
                ephemeral=True)
        await inter.response.send_modal(EventModal())

    @disnake.ui.channel_select(placeholder="📣 Канал для поздравлений", custom_id="hzdate:chan",
                               channel_types=[disnake.ChannelType.text], row=1)
    async def pick_channel(self, select: disnake.ui.ChannelSelect, inter: disnake.MessageInteraction):
        if not is_admin(inter.author):
            return await inter.response.send_message(
                embed=note_embed("❌ Канал поздравлений выбирает руководство семьи."),
                ephemeral=True)
        data = _load()
        data["channel"] = select.values[0].id
        _save(data)
        await inter.response.send_message(
            embed=note_embed(f"📣 Поздравления пойдут в {select.values[0].mention}."),
            ephemeral=True)

    @disnake.ui.button(label="Ближайшие", emoji="🗓", style=disnake.ButtonStyle.secondary,
                       custom_id="hzdate:soon", row=2)
    async def soon(self, button, inter: disnake.MessageInteraction):
        await inter.response.send_message(embed=await _panel(inter.bot, inter.guild),
                                          ephemeral=True)

    @disnake.ui.button(label="Удалить событие", emoji="🗑", style=disnake.ButtonStyle.danger,
                       custom_id="hzdate:del", row=2)
    async def delete(self, button, inter: disnake.MessageInteraction):
        if not is_admin(inter.author):
            return await inter.response.send_message(
                embed=note_embed("❌ События удаляет руководство семьи."), ephemeral=True)
        events = _load().get("events") or []
        if not events:
            return await inter.response.send_message(embed=note_embed("Событий нет."),
                                                     ephemeral=True)
        await inter.response.send_message(embed=note_embed("Выбери событие — уберу из календаря."),
                                          view=EventPickView(events), ephemeral=True)

    @disnake.ui.button(label="Убрать мой день рождения", emoji="🚪",
                       style=disnake.ButtonStyle.danger, custom_id="hzdate:forget", row=3)
    async def forget(self, button, inter: disnake.MessageInteraction):
        data = _load()
        if str(inter.author.id) not in (data.get("users") or {}):
            return await inter.response.send_message(
                embed=note_embed("Твой день рождения в календаре не записан."), ephemeral=True)
        data["users"].pop(str(inter.author.id), None)
        _save(data)
        await inter.response.send_message(embed=note_embed("🚪 Убрал. Запишешь снова — напомню."),
                                          ephemeral=True)


class Dates(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def cog_unload(self):
        self.daily.stop()

    @commands.Cog.listener()
    async def on_ready(self):
        if not getattr(self.bot, "_dates_view_added", False):
            self.bot.add_view(DatesView())
            self.bot._dates_view_added = True
        if not self.daily.is_beating():
            self.daily.change_interval(minutes=5)
            self.daily.start()

    @tasks.loop(minutes=5)
    async def daily(self):
        now = datetime.now(MSK)
        if (now.hour, now.minute) < (0, 5):
            return
        today = now.strftime("%Y-%m-%d")
        data = _load()
        done = data.setdefault("posted", {})
        if done.get(today) == "ok":
            return
        guild = self.bot.guilds[0] if self.bot.guilds else None
        rows = _upcoming(data, guild)
        today_key = now.date()
        hits = [r for r in rows
                if datetime.fromtimestamp(r["ts"], MSK).date() == today_key
                and (r["kind"] != "event" or r["ts"] <= now.timestamp() + 900)]
        channel = self.bot.get_channel(int(data.get("channel") or 0))
        if not hits or channel is None:
            if not hits:
                done[today] = "ok"
                _save(data)
            return
        for row in hits:
            embed = await self._congrats(row, guild)
            try:
                await channel.send(embed=embed)
            except disnake.HTTPException:
                pass
        done[today] = "ok"
        _save(data)

    @daily.error
    async def daily_error(self, error):
        print(f"[dates] автопоздравление: {error}")

    async def _congrats(self, row: dict, guild) -> disnake.Embed:
        if row["kind"] == "bday":
            template = get_text(
                "date_bday_text",
                "С днём рождения, {name}! 🎉 {age}\n{note}\n\n"
                "Обнимаем всей семьёй — заходи в голосовой, тебя ждут.")
            age = (f"Сегодня {row['age']} {_years_word(row['age'])} — красиво и вовремя."
                   if row.get("age") else "")
            text = template.format(name=f"<@{row['user']}>", age=age,
                                   note=row["note"] or "Спасибо, что ты с нами.")
            return card("🎂 День рождения в семье", description=text,
                        guild=guild, color_key="embed_color")
        if row["kind"] == "staff":
            years_word = _years_word(row["years"])
            template = get_text("date_staff_text",
                                "{name}, сегодня {years} {word} как ты в семье 🏠\n"
                                "Стаж — {human}. Спасибо, что держишь ряды.")
            text = template.format(name=f"<@{row['user']}>", years=row["years"],
                                   word=years_word, human=row["human"])
            return card("🏠 Годовщина в семье", description=text, guild=guild,
                        color_key="success_color")
        template = get_text("date_event_text", "**{title}** — сегодня, {time}.\n{note}\n\n"
                                              "Сбор по расписанию, не проспите.")
        text = template.format(title=row["title"], time=until(row["ts"]),
                               note=row["note"] or "Подробности в этом канале.")
        return card("📌 Событие семьи", description=text, guild=guild,
                    color_key="warning_color")

    @commands.slash_command(name="даты", description="Календарь семьи: ДР, стаж, события",
                            dm_permission=False)
    async def dates_panel(self, inter: disnake.ApplicationCommandInteraction):
        embed = await _panel(inter.bot, inter.guild)
        await inter.channel.send(embed=embed, view=DatesView())
        await inter.response.send_message(embed=note_embed("✅ Календарь семьи отправлен в чат."),
                                          ephemeral=True)

    async def set_birthday(self, inter, raw_date: str, raw_year: str,
                           note: str) -> None:
        md = _md(raw_date)
        if not md:
            return await inter.response.send_message(
                embed=note_embed("❌ Не понял дату. Формат: `25.10`."), ephemeral=True)
        year = None
        if raw_year:
            if not (raw_year.isdigit() and 1900 <= int(raw_year) <= datetime.now(MSK).year - 3):
                return await inter.response.send_message(
                    embed=note_embed("❌ Год рождения — 4 цифры, от 1900 до "
                                     f"{datetime.now(MSK).year - 3}."), ephemeral=True)
            year = int(raw_year)
        data = _load()
        data["users"][str(inter.author.id)] = {"date": f"{md[0]:02d}.{md[1]:02d}",
                                               "year": year, "note": clip(note, 120)}
        _save(data)
        day = _next_year(md)
        age = f" · исполнится **{day.year - year}**" if year else ""
        await inter.response.send_message(
            embed=note_embed(f"🎂 Записал: {day:%d.%m}{age}. Поздравлю в канале, "
                             "который выберет руководство."), ephemeral=True)

    async def add_event(self, inter, label: str, raw_date: str, raw_time: str,
                        repeat: str) -> None:
        md = _md(raw_date)
        if not md or not label:
            return await inter.response.send_message(
                embed=note_embed("❌ Нужны название (`Общий сбор`) и дата (`25.10`)."),
                ephemeral=True)
        hh, mm = 0, 0
        if raw_time:
            parts = raw_time.split(":")
            if not (len(parts) == 2 and all(p.isdigit() for p in parts)
                    and int(parts[0]) <= 23 and int(parts[1]) <= 59):
                return await inter.response.send_message(
                    embed=note_embed("❌ Время — в формате `19:30`."), ephemeral=True)
            hh, mm = int(parts[0]), int(parts[1])
        year = _next_year(md).year
        ts = datetime(year, md[1], md[0], hh, mm, tzinfo=MSK).timestamp()
        if ts < datetime.now(MSK).timestamp() and repeat.lower() not in ("д", "y", "da"):
            return await inter.response.send_message(
                embed=note_embed("❌ Это уже прошло. Повтор каждый год — напиши `д`."),
                ephemeral=True)
        data = _load()
        data["events"].append({"label": clip(label, 80), "date": f"{md[0]:02d}.{md[1]:02d}",
                               "ts": int(ts), "repeat": repeat.lower() in ("д", "y", "да"),
                               "note": ""})
        data["events"] = data["events"][-60:]
        _save(data)
        await inter.response.send_message(
            embed=note_embed(f"📌 Добавил: **{clip(label, 60)}** — {datetime.fromtimestamp(ts, MSK):%d.%m.%Y %H:%M}"
                             f" · {until(ts)}"), ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(Dates(bot))
