"""Опросы семьи на кнопках: голосует один раз, результат видит каждый.

Раньше голосование было возможно только текстом в чате («плюс в комментах»), поэтому итог
никто не считал. Здесь карточка сама пересчитывается после каждого нажатия: полоса, процент
и счётчик голосов живые, а опрос закрывается по сроку сам.

Кнопки не регистрируются как persistent view — их ловит `on_button_click` в этом же коге,
поэтому голосование работает и после перезапуска бота.
"""
import json
import os
import time
from typing import Dict, List, Optional

import disnake
from disnake.ext import commands, tasks

from utils import brand
from utils.checks import get_text, is_admin
from utils.embeds import bar, card, clip, list_embed, note_embed, until

STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "polls.json")
MAX_OPTIONS = 10
OPTION_EMOJI = ("number1", "number2", "number3", "number4", "number5",
                "number6", "number7", "number8", "number9", "number10")
FALLBACK_EMOJI = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")


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


def _upcoming(data: Dict[str, dict]) -> List[dict]:
    return [p for p in data.values() if not p.get("closed")]


def _duration(raw: str) -> Optional[int]:
    """'30м' / '2h' / '1д' / '1:30' → минуты. Без срока — None, опрос manual."""
    text = str(raw or "").strip().lower().replace(",", ".")
    if not text:
        return None
    if ":" in text:
        h, _, m = text.partition(":")
        if h.isdigit() and m[:2].isdigit():
            return int(h) * 60 + int(m[:2])
        return None
    num = "".join(c for c in text if c.isdigit() or c == ".")
    if not num:
        return None
    try:
        value = float(num)
    except ValueError:
        return None
    if any(w in text for w in ("ч", "h")):
        value *= 60
    if any(w in text for w in ("д", "d")):
        value *= 1440
    return max(1, int(value))


def _human(minutes: int) -> str:
    d, rem = divmod(minutes, 1440)
    h, m = divmod(rem, 60)
    if d:
        return f"{d} д {h} ч"
    if h:
        return f"{h} ч {m} мин"
    return f"{m} мин"


def _tally(poll: dict) -> List[int]:
    counts = [0] * len(poll["options"])
    for value in poll["votes"].values():
        if 0 <= int(value) < len(counts):
            counts[int(value)] += 1
    return counts


def render(poll: dict, bot=None) -> disnake.Embed:
    """Карточка результата: имя варианта, живая полоса и процент."""
    counts = _tally(poll)
    total = sum(counts)
    embed = card(poll["question"], guild=None, color_key="embed_color")
    lines = [f"👤 <@{poll['author']}> · голосов: **{total}**"]
    if poll.get("closed"):
        lines.append("🔒 Опрос завершён.")
    elif poll.get("end_ts"):
        lines.append(f"⏳ До конца {_human(int((poll['end_ts'] - time.time()) / 60))} · "
                     f"{until(poll['end_ts'])}")
    else:
        lines.append("⏳ Опрос без срока — закроет кнопкой автор или руководство.")
    if poll.get("note"):
        lines.append(clip(poll["note"], 400))
    embed.description = "\n".join(lines)
    for i, label in enumerate(poll["options"]):
        share = 0 if not total else round(counts[i] / total * 100)
        embed.add_field(name=clip(f"{brand.emoji(bot, OPTION_EMOJI[i], FALLBACK_EMOJI[i])} {label}", 256),
                        value=f"{bar(counts[i], max(total, 1), width=8)} `{share}%` · {counts[i]}",
                        inline=False)
    return embed


class PollView(disnake.ui.View):
    """Кнопки-варианты. Колбэков нет намеренно: clicks ловит cog.on_button_click."""

    def __init__(self, poll: dict):
        super().__init__(timeout=None)
        for i, label in enumerate(poll["options"]):
            self.add_item(disnake.ui.Button(
                style=disnake.ButtonStyle.secondary,
                label=clip(label, 70),
                custom_id=f"hzpoll:v:{poll['id']}:{i}"))
        self.add_item(disnake.ui.Button(label="Завершить опрос", emoji="🔒",
                                        style=disnake.ButtonStyle.danger,
                                        custom_id=f"hzpoll:end:{poll['id']}"))


class PollModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(
            title=get_text("poll_modal_title", "Опрос семьи"),
            custom_id="hzpoll:new",
            components=[
                disnake.ui.TextInput(label="Вопрос", custom_id="question",
                                     style=disnake.TextInputStyle.short,
                                     placeholder="Куда едем на сбор в субботу?", max_length=200),
                disnake.ui.TextInput(label="Варианты (по одному в строке, до 10)",
                                     custom_id="options", style=disnake.TextInputStyle.paragraph,
                                     placeholder="🏙 В город\n🌲 На природу\n🎳 Боулинг",
                                     max_length=1000),
                disnake.ui.TextInput(label="Срок (30м, 2ч, 1д)", custom_id="duration",
                                     style=disnake.TextInputStyle.short, placeholder="2ч",
                                     max_length=8, required=False),
            ])

    async def callback(self, inter: disnake.ModalInteraction):
        cog: "Polls" = inter.bot.get_cog("Polls")
        await cog.create(inter, inter.text_values["question"].strip(),
                        inter.text_values["options"].strip(),
                        inter.text_values.get("duration") and inter.text_values["duration"].strip())


class Polls(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def cog_unload(self):
        self.close_due.stop()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.close_due.is_running():
            self.close_due.change_interval(seconds=30)
            self.close_due.start()

    @tasks.loop(seconds=30)
    async def close_due(self):
        now = time.time()
        for poll in _upcoming(_load()):
            if poll.get("end_ts") and poll["end_ts"] <= now:
                await self.finish(poll["id"], auto=True)

    @close_due.error
    async def close_due_error(self, error):
        print(f"[polls] цикл закрытия: {error}")

    @commands.slash_command(name="опрос", description="Создать опрос с кнопками",
                            contexts=disnake.InteractionContextTypes(guild=True),
                            default_member_permissions=disnake.Permissions(manage_messages=True))
    async def poll_new(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.send_modal(PollModal())

    @commands.slash_command(name="опросы", description="Активные опросы семьи",
                            contexts=disnake.InteractionContextTypes(guild=True))
    async def poll_list(self, inter: disnake.ApplicationCommandInteraction):
        polls = [p for p in _upcoming(_load()) if str(p.get("guild", "")) == str(inter.guild.id)]
        rows = []
        for p in polls[:15]:
            counts = _tally(p)
            total = sum(counts)
            lead = max(range(len(counts)), key=lambda i: counts[i]) if total else None
            rows.append([clip(p["question"], 90),
                         (f"**{clip(p['options'][lead], 45)}** · {counts[lead]}/{total} голосов"
                          if lead is not None else "голосов пока нет") +
                         (f"\n{until(p['end_ts'])}" if p.get("end_ts") else ""),
                         f"[Открыть опрос]({p.get('url') or '#'})" if p.get("url") else "—"])
        await inter.response.send_message(
            embed=list_embed("📊 Активные опросы", rows, guild=inter.guild, inline=False,
                             empty="Активных опросов нет — создай командой `/опрос`.",
                             note="Голосует один раз, выбор можно поменять."),
            ephemeral=True)

    async def create(self, inter, question: str, options_raw: str,
                     duration_raw: Optional[str]) -> None:
        options = [o.strip() for o in options_raw.splitlines()
                   if o.strip() and not o.strip().startswith("#")][:MAX_OPTIONS]
        if len(options) < 2:
            return await inter.response.send_message(
                embed=note_embed("❌ Нужно минимум два варианта — по одному в строке."),
                ephemeral=True)
        if len(set(o.lower() for o in options)) != len(options):
            return await inter.response.send_message(
                embed=note_embed("❌ Варианты повторяются — повторы убери."), ephemeral=True)
        minutes = _duration(duration_raw or "")
        poll = {
            "id": str(int(time.time() * 1000)),
            "guild": inter.guild.id, "channel": inter.channel.id,
            "author": inter.author.id, "question": clip(question, 200),
            "options": [clip(o, 70) for o in options], "note": None,
            "votes": {}, "closed": False,
            "end_ts": (time.time() + minutes * 60) if minutes else 0,
            "created": int(time.time()),
        }
        message = await inter.channel.send(embed=render(poll, self.bot), view=PollView(poll))
        poll["message"] = message.id
        poll["url"] = message.jump_url
        data = _load()
        data[poll["id"]] = poll
        if len(data) > 300:
            for key in sorted(data, key=int)[: len(data) - 300]:
                data.pop(key, None)
        _save(data)
        await inter.response.send_message(
            embed=note_embed(f"✅ Опрос запущен. {minutes and _human(minutes) + ' на голосование' or 'Срока нет'}"),
            ephemeral=True)

    @commands.Cog.listener()
    async def on_button_click(self, inter: disnake.MessageInteraction):
        cid = str(inter.data.custom_id or "")
        if not cid.startswith("hzpoll:"):
            return
        if cid.startswith("hzpoll:v:"):
            _, _, pid, idx = cid.split(":", 3)
            await self.vote(inter, pid, int(idx))
        elif cid.startswith("hzpoll:end:"):
            await self.close_by_button(inter, cid.split(":", 2)[2])

    async def vote(self, inter: disnake.MessageInteraction, pid: str, idx: int) -> None:
        data = _load()
        poll = data.get(pid)
        if not poll:
            return await inter.response.send_message(
                embed=note_embed("❌ Этот опрос уже удалён."), ephemeral=True)
        if poll.get("closed"):
            return await inter.response.send_message(embed=note_embed("🔒 Опрос завершён."),
                                                     ephemeral=True)
        key = str(inter.author.id)
        changed = poll["votes"].get(key) is not None
        if poll["votes"].get(key) == idx:
            poll["votes"].pop(key, None)
            note = "Голос снят."
        else:
            poll["votes"][key] = idx
            note = (f"Выбрано: **{clip(poll['options'][idx], 60)}**" if not changed
                    else f"Изменено: **{clip(poll['options'][idx], 60)}**")
        _save(data)
        await self.redraw(poll)
        await inter.response.send_message(embed=note_embed(note), ephemeral=True)

    async def close_by_button(self, inter: disnake.MessageInteraction, pid: str) -> None:
        poll = _load().get(pid)
        if not poll:
            return await inter.response.send_message(embed=note_embed("❌ Опрос не найден."),
                                                     ephemeral=True)
        if not is_admin(inter.author) and poll["author"] != inter.author.id:
            return await inter.response.send_message(
                embed=note_embed("❌ Завершить может автор опроса или руководство семьи."),
                ephemeral=True)
        await inter.response.defer(ephemeral=True)
        await self.finish(pid, auto=False, actor=inter.author)

    async def redraw(self, poll: dict) -> None:
        channel = self.bot.get_channel(int(poll["channel"]))
        if channel is None or not poll.get("message"):
            return
        try:
            message = await channel.fetch_message(int(poll["message"]))
            embed = render(poll, self.bot)
            if poll.get("closed"):
                await message.edit(embed=embed, view=None)
            else:
                await message.edit(embed=embed, view=PollView(poll))
        except disnake.HTTPException:
            pass

    async def finish(self, pid: str, auto: bool, actor=None) -> None:
        data = _load()
        poll = data.get(pid)
        if not poll or poll.get("closed"):
            return
        poll["closed"] = True
        poll["end_ts"] = time.time()
        _save(data)
        await self.redraw(poll)
        if auto and poll.get("url"):
            counts = _tally(poll)
            total = sum(counts)
            if total:
                best = max(range(len(counts)), key=lambda i: counts[i])
                if counts.count(counts[best]) == 1:
                    line = f"🏆 По итогам опроса: **{poll['options'][best]}** ({counts[best]}/{total})"
                else:
                    line = "🤝 Итог — ничья, решение за руководством."
            else:
                line = "На голосование никто не пришёл."
            embed = note_embed(line, title="Опрос завершён")
            try:
                channel = self.bot.get_channel(int(poll["channel"])) or \
                    await self.bot.fetch_channel(int(poll["channel"]))
                await channel.send(embed=embed)
            except disnake.HTTPException:
                pass


def setup(bot: commands.Bot):
    bot.add_cog(Polls(bot))
