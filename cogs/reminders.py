"""Личные напоминания: бот напишет в ЛС, когда придёт срок.

У семьи нет общего календаря, а договорённости живут в чате и теряются. Здесь боец жмёт
«/напомни», пишет срок обычными словами («20м», «19:30», «25.10 18:00») и получает сообщение
в ЛС точно вовремя — с кнопками «Готово» и «Отложить на час», чтобы не плодить напоминания
заново.

Срок хранится в UTC, поэтому переезд между часовыми поясами не сдвигает его; в ЛС время
показывает Discord-метка `<t::R>`, которая считается у клиента.
"""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import disnake
from disnake.ext import commands, tasks

from utils.embeds import card, clip, list_embed, note_embed, until

MSK = timezone(timedelta(hours=3))
STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "reminders.json")
MAX_PER_USER = 20


def _load() -> Dict[str, dict]:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: Dict[str, dict]) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def _mine(data: Dict[str, dict], user_id: int) -> list:
    rows = [r for r in data.values() if int(r.get("user", 0)) == int(user_id)]
    return sorted(rows, key=lambda r: r.get("ts", 0))


_TIME_ONLY = re.compile(r"^(\d{1,2}):(\d{2})$")
_DATE_TIME = re.compile(r"^(\d{1,2})[.\/-](\d{1,2})(?:\s+(?:(\d{1,2})(?::(\d{2}))?))?$")
_OFFSET = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(м|мин|minute|h|ч|час|d|д|day)\s*$")


def _parse_when(raw: str) -> Optional[datetime]:
    """'20м' → через 20 минут, '19:30' → ближайшие 19:30, '25.10 18' → дата и час."""
    # Пробелы внутри «25.10 18:00» значимы, поэтому схлопываем, а не вырезаем.
    text = " ".join(str(raw or "").strip().lower().replace(",", ".").split())
    if not text:
        return None

    offset = _OFFSET.match(text)
    if offset:
        value = float(offset.group(1))
        unit = offset.group(2)
        minutes = value if unit in ("м", "мин", "minute") else \
            value * 60 if unit in ("h", "ч", "час") else value * 1440
        return datetime.now(timezone.utc) + timedelta(minutes=int(round(minutes)))

    clock = _TIME_ONLY.match(text)
    if clock:
        hh, mm = int(clock.group(1)), int(clock.group(2))
        if hh > 23 or mm > 59:
            return None
        now = datetime.now(MSK)
        when = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if when <= now:
            when += timedelta(days=1)
        return when.astimezone(timezone.utc)

    dated = _DATE_TIME.match(text)
    if dated:
        dd, mo = int(dated.group(1)), int(dated.group(2))
        hh = int(dated.group(3) or 0)
        mm = int(dated.group(4) or 0)
        if not (1 <= dd <= 31 and 1 <= mo <= 12 and hh <= 23 and mm <= 59):
            return None
        now = datetime.now(MSK)
        year = now.year
        try:
            when = now.replace(year=year, month=mo, day=dd, hour=hh, minute=mm,
                               second=0, microsecond=0)
        except ValueError:
            return None
        if when < now - timedelta(hours=12):
            when = when.replace(year=year + 1)
        return when.astimezone(timezone.utc)

    if text.isdigit():
        return datetime.now(timezone.utc) + timedelta(minutes=int(text))
    return None


class ReminderModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(
            title="Напоминание",
            custom_id="hzrem:new",
            components=[
                disnake.ui.TextInput(label="О чём напомнить", custom_id="text",
                                     style=disnake.TextInputStyle.short,
                                     placeholder="Собрать документы для заявки", max_length=200),
                disnake.ui.TextInput(label="Когда (20м, 1:30, 19:30, 25.10 18:00)",
                                     custom_id="when", style=disnake.TextInputStyle.short,
                                     placeholder="19:30", max_length=24),
            ])

    async def callback(self, inter: disnake.ModalInteraction):
        cog: "Reminders" = inter.bot.get_cog("Reminders")
        await cog.create(inter, inter.text_values["text"].strip(),
                        inter.text_values["when"].strip())


class ReminderActions(disnake.ui.View):
    """Постоянная панель в ЛС: custom_id не меняем, иначе кнопки умрут после рестарта."""

    def __init__(self, reminder_id: str):
        super().__init__(timeout=None)
        self.add_item(disnake.ui.Button(label="Готово", emoji="✅",
                                        style=disnake.ButtonStyle.success,
                                        custom_id=f"hzrem:done:{reminder_id}"))
        self.add_item(disnake.ui.Button(label="Отложить на час", emoji="⏰",
                                        style=disnake.ButtonStyle.secondary,
                                        custom_id=f"hzrem:late:{reminder_id}"))


class Reminders(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def cog_unload(self):
        self.tick.stop()

    @commands.Cog.listener()
    async def on_ready(self):
        if not getattr(self.bot, "_rem_view_added", False):
            self.bot.add_view(ReminderActions("__warmup__"))
            self.bot._rem_view_added = True
        if not self.tick.is_beating():
            self.tick.change_interval(seconds=30)
            self.tick.start()

    @tasks.loop(seconds=30)
    async def tick(self):
        now = time.time()
        data = _load()
        due = [r for r in data.values() if not r.get("sent") and r.get("ts", 0) <= now]
        if not due:
            return
        for rem in due:
            await self.deliver(rem)
            rem["sent"] = True
        _save(data)

    @tick.error
    async def tick_error(self, error):
        print(f"[reminders] цикл: {error}")

    async def deliver(self, rem: dict) -> None:
        user = self.bot.get_user(int(rem["user"])) or await self.bot.fetch_user(int(rem["user"]))
        when = datetime.fromtimestamp(rem["ts"], MSK)
        embed = card("🔔 Напоминание", guild=None, color_key="warning_color")
        embed.description = (f"**{clip(rem['text'], 200)}**\n\n"
                             f"Напомнил {until(rem['ts'])} · {when.strftime('%d.%m.%Y %H:%M')} МСК\n"
                             + (f"Из чата: <#{rem['channel']}>\n" if rem.get("channel") else ""))
        try:
            await user.send(embed=embed, view=ReminderActions(rem["id"]))
        except disnake.HTTPException:
            # ЛС закрыты — хотя бы не теряем напоминание молча.
            self.bot.loop.create_task(self._fallback(rem))

    async def _fallback(self, rem: dict) -> None:
        if not rem.get("channel"):
            return
        try:
            channel = self.bot.get_channel(int(rem["channel"]))
            if channel:
                await channel.send(embed=note_embed(
                    f"🔔 <@{rem['user']}>: {clip(rem['text'], 200)} — просил напомнить, "
                    "но ЛС закрыты."), allowed_mentions=disnake.AllowedMentions(users=True))
        except disnake.HTTPException:
            pass

    @commands.slash_command(name="напомни", description="Напомнить мне в ЛС о чём-то",
                            dm_permission=False)
    async def remind_me(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.send_modal(ReminderModal())

    @commands.slash_command(name="напоминания", description="Мои напоминания и отмена",
                            dm_permission=False)
    async def my_reminders(self, inter: disnake.ApplicationCommandInteraction):
        rows = _mine(_load(), inter.author.id)
        view = disnake.ui.View(timeout=None)
        cells = []
        for rem in rows[:10]:
            cells.append([clip(rem["text"], 80),
                          f"{until(rem['ts'])} · {datetime.fromtimestamp(rem['ts'], MSK):%d.%m %H:%M}"
                          + ("\nуже отправлено" if rem.get("sent") else "")])
            if not rem.get("sent"):
                view.add_item(disnake.ui.Button(label="Отменить", emoji="🗑",
                                                style=disnake.ButtonStyle.danger,
                                                custom_id=f"hzrem:cancel:{rem['id']}"))
        await inter.response.send_message(
            embed=list_embed("🔔 Мои напоминания", cells, guild=inter.guild, inline=False,
                             empty="Напоминаний нет. Добавь: `/напомни` — напиши срок словами, "
                                   "например `20м`, `19:30` или `25.10 18:00`.",
                             note="Отменю, только пока напоминание не ушло в ЛС."),
            view=view if view.children else None, ephemeral=True)

    async def create(self, inter, text: str, raw_when: str) -> None:
        when = _parse_when(raw_when)
        if when is None:
            return await inter.response.send_message(
                embed=note_embed("❌ Не понял срок. Напиши `20м`, `1:30`, `19:30` или `25.10 18:00`."),
                ephemeral=True)
        ts = when.timestamp()
        if ts < time.time() + 20:
            return await inter.response.send_message(
                embed=note_embed("❌ Это время уже прошло — напиши более поздний срок."),
                ephemeral=True)
        data = _load()
        if len(_mine(data, inter.author.id)) >= MAX_PER_USER:
            return await inter.response.send_message(
                embed=note_embed(f"❌ У тебя уже {MAX_PER_USER} напоминаний — сначала разберись с ними."),
                ephemeral=True)
        rid = str(int(time.time() * 1000))
        data[rid] = {"id": rid, "user": inter.author.id, "text": clip(text, 200),
                     "ts": int(ts), "channel": getattr(inter.channel, "id", 0),
                     "sent": False, "created": int(time.time())}
        _save(data)
        await inter.response.send_message(
            embed=note_embed(f"🔔 Напомню: **{clip(text, 120)}**\n"
                             f"{when.astimezone(MSK):%d.%m.%Y %H:%M} МСК · {until(ts)}"),
            ephemeral=True)

    @commands.Cog.listener()
    async def on_button_click(self, inter: disnake.MessageInteraction):
        cid = str(inter.data.custom_id or "")
        if not cid.startswith("hzrem:"):
            return
        action, _, rid = cid.split(":", 2)
        if rid == "__warmup__":
            return await inter.response.send_message(embed=note_embed("Панель устарела."),
                                                     ephemeral=inter.guild is not None)
        data = _load()
        rem = data.get(rid)
        if not rem:
            return await inter.response.send_message(
                embed=note_embed("Это напоминание уже удалено."),
                ephemeral=inter.guild is not None)
        if str(inter.author.id) != str(rem["user"]) and not _is_owner(inter):
            return await inter.response.send_message(
                embed=note_embed("❌ Это чужое напоминание."),
                ephemeral=inter.guild is not None)

        if action == "done":
            data.pop(rid, None)
            _save(data)
            if inter.guild is None:
                # Кнопка в том же ЛС-сообщении: убираем их, иначе «Готово» жмётся второй раз.
                return await inter.response.edit_message(
                    embed=note_embed("✅ Отметил — больше не напомню.",
                                     title="Напоминание закрыто"), view=None)
            await inter.response.send_message(embed=note_embed("✅ Отметил — больше не напомню."),
                                              ephemeral=True)
        elif action == "late":
            rem["ts"] = int(time.time()) + 3600
            rem["sent"] = False
            _save(data)
            if inter.guild is None:
                return await inter.response.edit_message(
                    embed=note_embed(f"⏰ Напомню через час: {until(rem['ts'])}",
                                     title="Отложено"), view=ReminderActions(rid))
            await inter.response.send_message(
                embed=note_embed(f"⏰ Напомню через час: {until(rem['ts'])}"), ephemeral=True)
        elif action == "cancel":
            data.pop(rid, None)
            _save(data)
            rows = _mine(data, inter.author.id)
            cells = [[clip(r["text"], 80),
                      f"{until(r['ts'])} · {datetime.fromtimestamp(r['ts'], MSK):%d.%m %H:%M}"]
                     for r in rows[:10]]
            view = disnake.ui.View(timeout=None)
            for r in rows[:10]:
                view.add_item(disnake.ui.Button(label="Отменить", emoji="🗑",
                                                style=disnake.ButtonStyle.danger,
                                                custom_id=f"hzrem:cancel:{r['id']}"))
            await inter.response.edit_message(
                embed=list_embed("🔔 Мои напоминания", cells, guild=inter.guild, inline=False,
                                 empty="Напоминаний не осталось.",
                                 note="Отменю, только пока напоминание не ушло в ЛС."),
                view=view if view.children else None)


def _is_owner(inter) -> bool:
    return inter.guild is not None and inter.author.id == inter.guild.owner_id


def setup(bot: commands.Bot):
    bot.add_cog(Reminders(bot))
