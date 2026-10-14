import asyncio
import io
from typing import Dict

import disnake
from disnake.ext import commands

from utils.attendance import ACTIONS, get_votes, render, save_votes, toggle_vote
from utils.checks import load_config, is_recruiter, get_text
from utils.embeds import base_embed
from utils.event_art import make_event_art, normalize_time

_locks: Dict[int, asyncio.Lock] = {}

_REPLIES = {
    ("yes", "added"): "✅ Отмечено: вы **будете** на сборе.",
    ("yes", "changed"): "✅ Изменено: вы **будете** на сборе.",
    ("late", "added"): "🟡 Отмечено: вы **опоздаете**.",
    ("late", "changed"): "🟡 Изменено: вы **опоздаете**.",
    ("no", "added"): "🔴 Отмечено: вы **не сможете** прийти.",
    ("no", "changed"): "🔴 Изменено: вы **не сможете** прийти.",
}


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


class Events(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

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
        await inter.edit_original_response(content=f"📢 {mention_str}", embed=event_emb,
                                           view=EventAttendanceView(), **extra)

        # Если нейросеть не сработала, автор сразу видит почему (остальные этого не видят).
        if not art.ai_ok:
            try:
                await inter.followup.send(f"⚠️ {art.problem_text()}", ephemeral=True)
            except disnake.HTTPException:
                pass


def setup(bot: commands.Bot):
    bot.add_cog(Events(bot))
