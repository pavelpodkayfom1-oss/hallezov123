import re
import disnake
from disnake.ext import commands
from utils.checks import load_config, is_recruiter, get_text
from utils.embeds import base_embed

class EventAttendanceView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.yes_users = set()
        self.late_users = set()
        self.no_users = set()

        lbl_yes = get_text("btn_event_yes", "Буду (+)")
        lbl_late = get_text("btn_event_late", "Опоздаю (+-)")
        lbl_no = get_text("btn_event_no", "Не смогу (-)")

        self.yes_btn = disnake.ui.Button(label=lbl_yes[:80], style=disnake.ButtonStyle.success, emoji="🟢", custom_id="codex:event_yes")
        self.late_btn = disnake.ui.Button(label=lbl_late[:80], style=disnake.ButtonStyle.secondary, emoji="🟡", custom_id="codex:event_late")
        self.no_btn = disnake.ui.Button(label=lbl_no[:80], style=disnake.ButtonStyle.danger, emoji="🔴", custom_id="codex:event_no")

        self.add_item(self.yes_btn)
        self.add_item(self.late_btn)
        self.add_item(self.no_btn)

    def update_embed(self, message: disnake.Message, guild: disnake.Guild) -> disnake.Embed:
        emb = message.embeds[0]

        def format_users(user_ids):
            if not user_ids:
                return "*Никого нет*"
            mentions = [f"<@{uid}>" for uid in user_ids]
            return f"**Всего ({len(mentions)}):**\n" + ", ".join(mentions)

        lbl_yes = get_text("btn_event_yes", "Буду (+)")
        lbl_late = get_text("btn_event_late", "Опоздаю (+-)")
        lbl_no = get_text("btn_event_no", "Не смогу (-)")

        emb.set_field_at(0, name=f"🟢 {lbl_yes}", value=format_users(self.yes_users), inline=True)
        emb.set_field_at(1, name=f"🟡 {lbl_late}", value=format_users(self.late_users), inline=True)
        emb.set_field_at(2, name=f"🔴 {lbl_no}", value=format_users(self.no_users), inline=True)
        return emb

    @commands.Cog.listener()
    async def on_button_click(self, inter: disnake.MessageInteraction):
        pass

    async def handle_click(self, inter: disnake.MessageInteraction, action: str):
        user_id = inter.author.id
        if action == "yes":
            self.late_users.discard(user_id)
            self.no_users.discard(user_id)
            self.yes_users.add(user_id)
        elif action == "late":
            self.yes_users.discard(user_id)
            self.no_users.discard(user_id)
            self.late_users.add(user_id)
        elif action == "no":
            self.yes_users.discard(user_id)
            self.late_users.discard(user_id)
            self.no_users.add(user_id)

        emb = self.update_embed(inter.message, inter.guild)
        await inter.response.edit_message(embed=emb, view=self)


class Events(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_button_click(self, inter: disnake.MessageInteraction):
        custom_id = inter.component.custom_id
        if custom_id not in ("codex:event_yes", "codex:event_late", "codex:event_no"):
            return
        action = custom_id.replace("codex:event_", "")
        if not inter.message.embeds or len(inter.message.embeds[0].fields) < 3:
            return await inter.response.send_message("❌ Не удалось прочитать сбор.", ephemeral=True)

        emb = inter.message.embeds[0]
        # списки участников восстанавливаем из полей карточки (переживает перезапуск бота)
        groups = {"yes": [], "late": [], "no": []}
        for key, field in zip(("yes", "late", "no"), emb.fields[:3]):
            groups[key] = [int(x) for x in re.findall(r"<@!?(\d+)>", field.value or "")]
        uid = inter.author.id
        for k in groups:
            groups[k] = [u for u in groups[k] if u != uid]
        groups[action].append(uid)

        def fmt(ids):
            if not ids:
                return "*Пока никто*"
            return f"**Всего ({len(ids)}):**\n" + ", ".join(f"<@{u}>" for u in ids)

        for i, key in enumerate(("yes", "late", "no")):
            emb.set_field_at(i, name=emb.fields[i].name, value=fmt(groups[key])[:1024], inline=True)
        await inter.response.edit_message(embed=emb)

    @commands.slash_command(
        name="event",
        description="Создать сбор семьи на мероприятие (Капт, ВЗП, Дроп, Поставка)"
    )
    async def event_command(
        self,
        inter: disnake.ApplicationCommandInteraction,
        title: str = commands.Param(description="Название мероприятия (например: Дроп 19:30 / ВЗП)"),
        time_str: str = commands.Param(description="Время сбора (например: 19:15 МСК)"),
        info: str = commands.Param(description="Снаряжение и место сбора", default="Особняк семьи, 100% бронежилеты, оружие")
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
            desc_tpl.format(time=time_str, info=info),
            guild=inter.guild
        )

        lbl_yes = get_text("btn_event_yes", "Буду (+)")
        lbl_late = get_text("btn_event_late", "Опоздаю (+-)")
        lbl_no = get_text("btn_event_no", "Не смогу (-)")

        event_emb.add_field(name=f"🟢 {lbl_yes}", value="*Пока никто*", inline=True)
        event_emb.add_field(name=f"🟡 {lbl_late}", value="*Пока никто*", inline=True)
        event_emb.add_field(name=f"🔴 {lbl_no}", value="*Пока никто*", inline=True)

        view = EventAttendanceView()
        await inter.response.send_message(content=f"📢 {mention_str}", embed=event_emb, view=view)


def setup(bot: commands.Bot):
    bot.add_cog(Events(bot))
