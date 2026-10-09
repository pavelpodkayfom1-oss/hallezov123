import disnake
from disnake.ext import commands
from typing import Optional
from utils.checks import load_config, is_recruiter, get_text
from utils.embeds import base_embed, success_embed, error_embed, warning_embed
from database import (
    create_application,
    get_application_by_thread,
    update_application_status,
    upsert_member,
    DB_PATH
)
import aiosqlite


def build_nick(fmt: str, nick: str, static: str, rank: str = "1") -> str:
    """Собирает ник по формату и укладывает в лимит Discord (32 символа),
    обрезая именно имя, а не хвост со статиком."""
    nick = (nick or "").strip()
    static = (static or "").strip()
    full = fmt.format(rank=rank, nick=nick, static=static)
    if len(full) <= 32:
        return full
    overflow = len(full) - 32
    short = nick[:max(1, len(nick) - overflow)].rstrip()
    return fmt.format(rank=rank, nick=short, static=static)[:32]


class RejectReasonModal(disnake.ui.Modal):
    def __init__(self, app_id: int, thread: disnake.Thread, applicant_id: int, original_message: disnake.Message):
        self.app_id = app_id
        self.thread = thread
        self.applicant_id = applicant_id
        self.original_message = original_message

        title = get_text("modal_reject_title", "Отклонение заявки в Hallez FAMQ")
        label = get_text("modal_reject_label", "Причина отказа")
        placeholder = get_text("modal_reject_ph", "Укажите причину отказа...")

        components = [
            disnake.ui.TextInput(
                label=label[:45],
                custom_id="reject_reason",
                style=disnake.TextInputStyle.paragraph,
                placeholder=placeholder[:100],
                min_length=3,
                max_length=500,
                required=True
            )
        ]
        super().__init__(title=title[:45], components=components)

    async def callback(self, inter: disnake.ModalInteraction):
        await inter.response.defer(ephemeral=True)

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT status, nick, static_id FROM applications WHERE id = ?", (self.app_id,)) as cur:
                row = await cur.fetchone()
                if not row or row["status"] in ("accepted", "rejected"):
                    await inter.edit_original_response(content="❌ Эта заявка уже была закрыта ранее!")
                    return
                app_data = dict(row)

        reason = inter.text_values.get("reject_reason", "Причина не указана")
        await update_application_status(self.app_id, status="rejected", reviewer_id=inter.author.id, reason=reason)

        applicant = inter.guild.get_member(self.applicant_id)
        if not applicant:
            try:
                applicant = await inter.guild.fetch_member(self.applicant_id)
            except Exception:
                applicant = None

        mention_str = applicant.mention if applicant else f"<@{self.applicant_id}>"

        # Деактивация кнопок в исходном сообщении
        try:
            disabled_view = disnake.ui.View.from_message(self.original_message)
            for item in disabled_view.children:
                item.disabled = True
            await self.original_message.edit(view=disabled_view)
        except Exception:
            pass

        # Краткое уведомление в ветку
        short_notify = error_embed(
            get_text("recruit_rejected_title", "Заявка отклонена"),
            f"Заявка кандидата {mention_str} отклонена проверяющим {inter.author.mention}.\n**Причина:** {reason}",
            guild=inter.guild
        )
        await self.thread.send(embed=short_notify)

        # Отправка подробного лога в отдельный канал логов
        config = load_config()
        log_channel_id = config.get("channels", {}).get("logs_channel_id")
        if log_channel_id:
            log_ch = inter.guild.get_channel(log_channel_id)
            if log_ch:
                desc_tpl = get_text(
                    "recruit_rejected_desc",
                    "👤 **Кандидат:** {mention}\n👮 **Проверяющий:** {reviewer}\n📝 **Причина:** {reason}"
                )
                desc = desc_tpl.format(
                    mention=mention_str,
                    nick=app_data.get("nick", "—"),
                    static=app_data.get("static_id", "—"),
                    reviewer=inter.author.mention,
                    reason=reason
                )
                log_emb = error_embed(
                    f"📝 Аудит лог: Заявка #{self.app_id} отклонена",
                    desc + f"\n📂 **Ветка:** {self.thread.mention}",
                    guild=inter.guild
                )
                await log_ch.send(embed=log_emb)

        # Уведомление в ЛС кандидату
        if applicant:
            try:
                dm_tpl = get_text(
                    "recruit_rejected_dm",
                    "Здравствуйте, {nick}!\nК сожалению, ваша заявка на вступление в семью **Hallez FAMQ** была отклонена.\n\n**Причина:** {reason}"
                )
                dm_text = dm_tpl.format(
                    nick=applicant.display_name,
                    mention=mention_str,
                    reviewer=inter.author.display_name,
                    reason=reason
                )
                dm_emb = error_embed(
                    "Вердикт по заявке в Hallez FAMQ",
                    dm_text,
                    guild=inter.guild
                )
                await applicant.send(embed=dm_emb)
            except Exception:
                pass

        await inter.edit_original_response(content="❌ Заявка отклонена, кнопки заблокированы, ветка закрывается.")
        try:
            await self.thread.edit(locked=True, archived=True)
        except Exception:
            pass


class VoiceSelect(disnake.ui.StringSelect):
    def __init__(self, app_id: int, applicant_id: int, thread: disnake.Thread):
        self.app_id = app_id
        self.applicant_id = applicant_id
        self.thread = thread
        config = load_config()
        voice_channels = config.get("voice_channels", [])

        options = []
        for vc in voice_channels:
            options.append(
                disnake.SelectOption(
                    label=vc.get("name", "Комната обзвона")[:100],
                    value=str(vc.get("id", "0")),
                    description="Перенаправить кандидата в этот канал"
                )
            )
        if not options:
            guild_voices = [c for c in thread.guild.channels if isinstance(c, disnake.VoiceChannel)][:5]
            for gv in guild_voices:
                options.append(disnake.SelectOption(label=f"🎙️ {gv.name[:90]}", value=str(gv.id)))
            if not options:
                options.append(disnake.SelectOption(label="Основной канал ожидания", value="0"))

        ph = get_text("select_voice_ph", "Выберите голосовой канал для обзвона...")
        super().__init__(
            placeholder=ph[:100],
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"voice_select:{app_id}"
        )

    async def callback(self, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        selected_id = self.values[0]
        config = load_config()

        voice_link = f"<#{selected_id}>" if selected_id != "0" else "**Комната обзвона**"

        await update_application_status(self.app_id, status="interview_scheduled", reviewer_id=inter.author.id)

        applicant = inter.guild.get_member(self.applicant_id)
        if not applicant:
            try:
                applicant = await inter.guild.fetch_member(self.applicant_id)
            except Exception:
                applicant = None

        mention_str = applicant.mention if applicant else f"<@{self.applicant_id}>"

        title = get_text("recruit_interview_title", "🎙️ Вызов на собеседование (обзвон)")
        desc_tpl = get_text(
            "recruit_interview_desc",
            "{mention}, рекрутер {reviewer} приглашает вас на обзвон в семью **Hallez FAMQ**!\n\n📍 **Голосовой канал:** {voice}\n⏳ Пожалуйста, зайдите в указанный канал и ожидайте рекрутера."
        )
        desc = desc_tpl.format(mention=mention_str, reviewer=inter.author.mention, voice=voice_link)

        notify_emb = base_embed(title, desc, "warning_color", guild=inter.guild)
        await self.thread.send(content=f"{mention_str}", embed=notify_emb)
        await inter.edit_original_response(content=f"✅ Кандидат {mention_str} оповещен о переходе в {voice_link}!")

        log_channel_id = config.get("channels", {}).get("logs_channel_id")
        if log_channel_id:
            log_ch = inter.guild.get_channel(log_channel_id)
            if log_ch:
                log_emb = base_embed(
                    f"🎙️ Аудит лог: Назначен обзвон #{self.app_id}",
                    f"👤 **Кандидат:** {mention_str}\n"
                    f"👮 **Рекрутер:** {inter.author.mention}\n"
                    f"📍 **Канал:** {voice_link}\n"
                    f"📂 **Ветка:** {self.thread.mention}",
                    "warning_color",
                    guild=inter.guild
                )
                await log_ch.send(embed=log_emb)


class VoiceSelectView(disnake.ui.View):
    def __init__(self, app_id: int, applicant_id: int, thread: disnake.Thread):
        super().__init__(timeout=120)
        self.add_item(VoiceSelect(app_id, applicant_id, thread))


class RecruitmentManageView(disnake.ui.View):
    """Панель управления заявкой внутри ветки кандидата с настраиваемыми текстами кнопок"""
    def __init__(self, app_id: int, applicant_id: int):
        super().__init__(timeout=None)
        self.app_id = app_id
        self.applicant_id = applicant_id

        lbl_review = get_text("btn_recruit_review", "Взять на рассмотрение")
        lbl_interview = get_text("btn_recruit_interview", "Вызвать на обзвон")
        lbl_accept = get_text("btn_recruit_accept", "Принять в семью")
        lbl_reject = get_text("btn_recruit_reject", "Отказать")

        self.review_btn = disnake.ui.Button(
            style=disnake.ButtonStyle.primary,
            label=lbl_review[:80],
            emoji="🔎",
            custom_id=f"codex:app_review:{app_id}"
        )
        self.interview_btn = disnake.ui.Button(
            style=disnake.ButtonStyle.secondary,
            label=lbl_interview[:80],
            emoji="🎙️",
            custom_id=f"codex:app_interview:{app_id}"
        )
        self.accept_btn = disnake.ui.Button(
            style=disnake.ButtonStyle.success,
            label=lbl_accept[:80],
            emoji="✅",
            custom_id=f"codex:app_accept:{app_id}"
        )
        self.reject_btn = disnake.ui.Button(
            style=disnake.ButtonStyle.danger,
            label=lbl_reject[:80],
            emoji="❌",
            custom_id=f"codex:app_reject:{app_id}"
        )

        self.add_item(self.review_btn)
        self.add_item(self.interview_btn)
        self.add_item(self.accept_btn)
        self.add_item(self.reject_btn)


class RecruitModal(disnake.ui.Modal):
    def __init__(self):
        title = get_text("modal_recruit_title", "Анкета на вступление в Hallez FAMQ")
        lbl_nick = get_text("modal_recruit_nick_label", "Игровой никнейм (Имя Фамилия)")
        ph_nick = get_text("modal_recruit_nick_ph", "Пример: Travis Hallez FAMQ")
        lbl_static = get_text("modal_recruit_static_label", "Ваш статик (Static ID)")
        ph_static = get_text("modal_recruit_static_ph", "Пример: 12345")
        lbl_age = get_text("modal_recruit_age_label", "Реальный возраст")
        ph_age = get_text("modal_recruit_age_ph", "Пример: 18")
        lbl_prev = get_text("modal_recruit_prev_label", "В каких семьях состояли ранее?")
        ph_prev = get_text("modal_recruit_prev_ph", "Укажите названия семей и причину ухода...")
        lbl_why = get_text("modal_recruit_why_label", "Почему именно Hallez FAMQ и как узнали о нас?")
        ph_why = get_text("modal_recruit_why_ph", "Ваши цели, планы в семье, откуда узнали...")

        components = [
            disnake.ui.TextInput(
                label=lbl_nick[:45],
                custom_id="nick",
                placeholder=ph_nick[:100],
                min_length=3,
                max_length=32,
                required=True
            ),
            disnake.ui.TextInput(
                label=lbl_static[:45],
                custom_id="static_id",
                placeholder=ph_static[:100],
                min_length=1,
                max_length=10,
                required=True
            ),
            disnake.ui.TextInput(
                label=lbl_age[:45],
                custom_id="age",
                placeholder=ph_age[:100],
                min_length=1,
                max_length=3,
                required=True
            ),
            disnake.ui.TextInput(
                label=lbl_prev[:45],
                custom_id="prev_families",
                style=disnake.TextInputStyle.paragraph,
                placeholder=ph_prev[:100],
                min_length=2,
                max_length=500,
                required=True
            ),
            disnake.ui.TextInput(
                label=lbl_why[:45],
                custom_id="why_codex",
                style=disnake.TextInputStyle.paragraph,
                placeholder=ph_why[:100],
                min_length=5,
                max_length=500,
                required=True
            )
        ]
        super().__init__(title=title[:45], components=components)

    async def callback(self, inter: disnake.ModalInteraction):
        config = load_config()
        if not config.get("recruitment_open", True):
            closed_msg = get_text("recruit_closed_msg", "❌ Набор в семью **Hallez FAMQ** на данный момент закрыт.")
            await inter.response.send_message(closed_msg, ephemeral=True)
            return

        nick = inter.text_values.get("nick", "").strip()
        static_id = inter.text_values.get("static_id", "").strip()
        raw_age = inter.text_values.get("age", "").strip()
        prev_families = inter.text_values.get("prev_families", "").strip()
        why_codex = inter.text_values.get("why_codex", "").strip()

        try:
            age = int(raw_age)
        except ValueError:
            await inter.response.send_message("❌ Возраст должен быть числом!", ephemeral=True)
            return

        await inter.response.defer(ephemeral=True)

        channel = inter.channel
        thread_name = f"заявка-{nick} [{static_id}]"
        
        try:
            thread = await channel.create_thread(
                name=thread_name,
                type=disnake.ChannelType.private_thread,
                auto_archive_duration=1440
            )
        except Exception:
            thread = await channel.create_thread(
                name=thread_name,
                auto_archive_duration=1440
            )

        try:
            await thread.add_user(inter.author)
        except Exception:
            pass

        app_id = await create_application(
            user_id=inter.author.id,
            nick=nick,
            static_id=static_id,
            age=age,
            prev_families=prev_families,
            why_codex=why_codex,
            thread_id=thread.id
        )

        title_tpl = get_text("recruit_card_title", "⚜️ Новая анкета кандидата: {nick}")
        desc_tpl = get_text(
            "recruit_card_desc",
            "Кандидат {mention} подал заявку на вступление в семью **Hallez FAMQ**.\nСтатус: 🟡 **Новая заявка**"
        )
        app_card = base_embed(
            title_tpl.format(nick=nick),
            desc_tpl.format(mention=inter.author.mention),
            guild=inter.guild
        )
        app_card.add_field(name="👤 Никнейм в игре", value=f"`{nick}`", inline=True)
        app_card.add_field(name="🆔 Статический ID", value=f"`{static_id}`", inline=True)
        app_card.add_field(name="🎂 Возраст", value=f"`{age}` лет", inline=True)
        app_card.add_field(name="🏛️ Прошлые семьи", value=prev_families, inline=False)
        app_card.add_field(name="🎯 Почему Hallez FAMQ?", value=why_codex, inline=False)

        recruiter_roles = config.get("recruiter_role_ids", [])
        ping_content = " ".join([f"<@&{r_id}>" for r_id in recruiter_roles if r_id > 0])
        if not ping_content:
            ping_content = "📢 Рекрутеры, поступила новая анкета!"

        view = RecruitmentManageView(app_id=app_id, applicant_id=inter.author.id)
        await thread.send(content=f"{inter.author.mention} {ping_content}", embed=app_card, view=view)

        await inter.edit_original_response(
            content=f"✅ Ваша заявка успешно отправлена! Перейдите в созданную ветку: {thread.mention}"
        )

        log_channel_id = config.get("channels", {}).get("logs_channel_id")
        if log_channel_id:
            log_ch = inter.guild.get_channel(log_channel_id)
            if log_ch:
                log_emb = base_embed(
                    f"📝 Аудит лог: Новая заявка #{app_id}",
                    f"👤 **Пользователь:** {inter.author.mention} (`{nick}` | `{static_id}`)\n"
                    f"📂 **Ветка:** {thread.mention}\n"
                    f"🎂 **Возраст:** `{age}`\n"
                    f"🔢 **Номер заявки:** `#{app_id}`",
                    guild=inter.guild
                )
                await log_ch.send(embed=log_emb)


class RecruitLaunchView(disnake.ui.View):
    """Постоянная кнопка в канале набора"""
    def __init__(self):
        super().__init__(timeout=None)
        label = get_text("btn_recruit_apply", "Подать заявку в Hallez FAMQ")
        self.apply_btn = disnake.ui.Button(
            label=label[:80],
            style=disnake.ButtonStyle.danger,
            emoji="📜",
            custom_id="codex:recruit_apply"
        )
        self.add_item(self.apply_btn)


class Recruitment(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_button_click(self, inter: disnake.MessageInteraction):
        custom_id = inter.component.custom_id

        # Обработка кнопки запуска подачи заявки
        if custom_id == "codex:recruit_apply":
            config = load_config()
            if not config.get("recruitment_open", True):
                closed_msg = get_text("recruit_closed_msg", "❌ Набор в семью **Hallez FAMQ** на данный момент закрыт.")
                await inter.response.send_message(closed_msg, ephemeral=True)
                return
            modal = RecruitModal()
            await inter.response.send_modal(modal)
            return

        if not custom_id.startswith("codex:app_"):
            return

        parts = custom_id.split(":")
        if len(parts) < 3:
            return

        action = parts[1]
        try:
            app_id = int(parts[2])
        except ValueError:
            return

        if not is_recruiter(inter.author):
            await inter.response.send_message("❌ У вас нет прав для проверки заявок!", ephemeral=True)
            return

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)) as cur:
                row = await cur.fetchone()
                if not row:
                    await inter.response.send_message("❌ Заявка не найдена в базе данных.", ephemeral=True)
                    return
                app = dict(row)

        if app["status"] in ("accepted", "rejected"):
            try:
                view = disnake.ui.View.from_message(inter.message)
                for item in view.children:
                    item.disabled = True
                await inter.message.edit(view=view)
            except Exception:
                pass

            status_ru = "принята" if app["status"] == "accepted" else "отклонена"
            await inter.response.send_message(
                f"⚠️ **Действие заблокировано:** Эта заявка уже была **{status_ru}**! Повторные действия невозможны.",
                ephemeral=True
            )
            return

        applicant_id = app["user_id"]
        applicant = inter.guild.get_member(applicant_id)
        if not applicant:
            try:
                applicant = await inter.guild.fetch_member(applicant_id)
            except Exception:
                applicant = None

        thread = inter.channel if isinstance(inter.channel, disnake.Thread) else None
        config = load_config()

        if action == "app_review":
            await update_application_status(app_id, status="in_review", reviewer_id=inter.author.id)
            review_title = get_text("recruit_review_title", "🔎 Заявка взята на рассмотрение")
            review_desc = get_text(
                "recruit_review_desc",
                "Рекрутер {reviewer} взял заявку кандидата {mention} на рассмотрение."
            )
            review_emb = base_embed(
                review_title,
                review_desc.format(reviewer=inter.author.mention, mention=f"<@{applicant_id}>"),
                "warning_color",
                guild=inter.guild
            )
            await inter.response.send_message(embed=review_emb)

        elif action == "app_interview":
            if not thread:
                await inter.response.send_message("❌ Действие доступно только внутри ветки заявки.", ephemeral=True)
                return
            view = VoiceSelectView(app_id=app_id, applicant_id=applicant_id, thread=thread)
            await inter.response.send_message("Выберите голосовой канал для обзвона:", view=view, ephemeral=True)

        elif action == "app_accept":
            await inter.response.defer()

            # повторная проверка статуса (защита от двойного клика)
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT status FROM applications WHERE id = ?", (app_id,)) as cur:
                    r = await cur.fetchone()
            if not r or r[0] in ("accepted", "rejected"):
                await inter.followup.send("⚠️ Заявка уже обработана.", ephemeral=True)
                return

            try:
                disabled_view = disnake.ui.View.from_message(inter.message)
                for item in disabled_view.children:
                    item.disabled = True
                await inter.message.edit(view=disabled_view)
            except Exception:
                pass

            problems = []

            # 1) СНАЧАЛА ник (из анкеты), чтобы famq_sync при выдаче роли увидел уже правильный ник
            nick_format = get_text("nickname_format", "[Hallez FAMQ] {nick} | {static}")
            new_nick = build_nick(nick_format, app["nick"], app["static_id"])
            if not applicant:
                problems.append("кандидата нет на сервере — ник и роли не выданы")
            elif config.get("auto_nicknames", True):
                try:
                    await applicant.edit(nick=new_nick, reason=f"Принят в семью, рекрутер {inter.author}")
                except Exception as e:
                    problems.append(f"ник `{new_nick}` не установлен: {e}")
                    print(f"Не удалось сменить никнейм: {e!r}")

            # 2) роли
            roles_to_add = []
            for key in ("family_role_id", "rank_1_role_id"):
                rid = config.get("roles", {}).get(key)
                if rid:
                    role = inter.guild.get_role(int(rid))
                    if role:
                        roles_to_add.append(role)
            if applicant and roles_to_add:
                try:
                    await applicant.add_roles(*roles_to_add, reason=f"Принят в семью Hallez FAMQ рекрутером {inter.author}")
                except Exception as e:
                    problems.append(f"роли не выданы: {e}")
                    print(f"Ошибка выдачи ролей: {e!r}")

            # 3) база
            await update_application_status(app_id, status="accepted", reviewer_id=inter.author.id, reason="Принят по результатам обзвона")
            await upsert_member(user_id=applicant_id, nick=app["nick"], static_id=app["static_id"], rank=1)

            # 4) сообщение в ветке
            acc_title = get_text("recruit_accepted_title", "Добро пожаловать в Hallez FAMQ!")
            acc_thread_msg = get_text(
                "recruit_accepted_thread_msg",
                "🎉 Кандидат {mention} успешно принят в семью рекрутером {reviewer}!\nРоли выданы. Ветка архивирована."
            )
            acc_emb = success_embed(
                acc_title,
                acc_thread_msg.format(mention=f"<@{applicant_id}>", reviewer=inter.author.mention),
                guild=inter.guild
            )
            try:
                await inter.channel.send(content=f"<@{applicant_id}>", embed=acc_emb)
            except Exception:
                pass
            if problems:
                try:
                    await inter.followup.send("⚠️ Заявка принята, но: " + "; ".join(problems), ephemeral=True)
                except Exception:
                    pass

            # 5) лог (до закрытия ветки, чтобы ссылка в логе была живой)
            log_channel_id = config.get("channels", {}).get("logs_channel_id")
            if log_channel_id:
                log_ch = inter.guild.get_channel(log_channel_id)
                if log_ch:
                    desc_tpl = get_text(
                        "recruit_accepted_desc",
                        "🎉 Кандидат {mention} успешно принят в семью!\n\n👤 **Никнейм:** `{nick}`\n🆔 **Статик:** `{static}`\n👑 **Принял:** {reviewer}\n🎖️ **Выдан ранг:** 1 (Новичок)"
                    )
                    desc = desc_tpl.format(
                        mention=f"<@{applicant_id}>",
                        nick=app["nick"],
                        static=app["static_id"],
                        reviewer=inter.author.mention
                    )
                    desc += f"\n🏷️ **Ник на сервере:** `{new_nick}`"
                    if thread:
                        desc += f"\n📂 **Ветка:** `{thread.name}` (#{app_id})"
                    if problems:
                        desc += "\n⚠️ " + "; ".join(problems)
                    log_emb = success_embed(f"📝 Аудит лог: Кандидат принят #{app_id}", desc, guild=inter.guild)
                    try:
                        await log_ch.send(embed=log_emb)
                    except Exception as e:
                        print(f"Не удалось отправить лог: {e!r}")

            # 6) ЛС
            if applicant:
                try:
                    dm_tpl = get_text(
                        "recruit_accepted_dm",
                        "Поздравляем с вступлением в Hallez FAMQ!\nВы успешно приняты в семью **Hallez FAMQ**!"
                    )
                    dm_emb = success_embed(
                        "Поздравляем с вступлением в Hallez FAMQ!",
                        dm_tpl.format(mention=applicant.mention, nick=app["nick"]),
                        guild=inter.guild
                    )
                    await applicant.send(embed=dm_emb)
                except Exception:
                    pass

            # 7) закрыть ветку: config["accept_thread_action"] = "archive" (по умолчанию) или "delete"
            if thread:
                action_t = str(config.get("accept_thread_action", "archive")).lower()
                try:
                    if action_t == "delete":
                        await thread.delete(reason=f"Заявка #{app_id} принята")
                    else:
                        await thread.edit(locked=True, archived=True, reason=f"Заявка #{app_id} принята")
                except Exception as e:
                    print(f"Не удалось закрыть ветку {thread.id}: {e!r} (нужно право 'Управление ветками')")
                    try:
                        await inter.followup.send(
                            "⚠️ Не удалось закрыть ветку: выдайте боту право **Управление ветками** (Manage Threads).",
                            ephemeral=True)
                    except Exception:
                        pass

        elif action == "app_reject":
            if not thread:
                await inter.response.send_message("❌ Действие доступно только внутри ветки заявки.", ephemeral=True)
                return
            modal = RejectReasonModal(app_id=app_id, thread=thread, applicant_id=applicant_id, original_message=inter.message)
            await inter.response.send_modal(modal)


def setup(bot: commands.Bot):
    bot.add_cog(Recruitment(bot))
