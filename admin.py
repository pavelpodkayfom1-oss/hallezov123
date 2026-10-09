import disnake
from disnake.ext import commands
from typing import Optional, Dict
from utils.checks import load_config, save_config, is_admin, get_text
from utils.embeds import base_embed, success_embed, error_embed, warning_embed
from database import get_stats
from cogs.recruitment import RecruitLaunchView
from cogs.promotions import PromotionLaunchView


# -------------------------------------------------------------
# 1. СЛОВАРЬ ВСЕХ НАСТРАИВАЕМЫХ ТЕКСТОВ С ОПИСАНИЯМИ
# -------------------------------------------------------------
TEXT_CATEGORIES = {
    "buttons": {
        "title": "🔘 Тексты кнопок",
        "items": {
            "btn_recruit_apply": "Кнопка: Подать заявку в семью",
            "btn_recruit_review": "Кнопка: Взять на рассмотрение",
            "btn_recruit_interview": "Кнопка: Вызвать на обзвон",
            "btn_recruit_accept": "Кнопка: Принять в семью",
            "btn_recruit_reject": "Кнопка: Отказать кандидату",
            "btn_promo_accept": "Кнопка: Одобрить повышение",
            "btn_promo_reject": "Кнопка: Отклонить отчет",
            "btn_event_yes": "Кнопка МП: Буду (+)",
            "btn_event_late": "Кнопка МП: Опоздаю (+-)",
            "btn_event_no": "Кнопка МП: Не смогу (-)"
        }
    },
    "modals": {
        "title": "📝 Поля модальных окон (анкета и отчеты)",
        "items": {
            "modal_recruit_title": "Заголовок анкеты кандидата",
            "modal_recruit_nick_label": "Анкета: Название поля никнейма",
            "modal_recruit_nick_ph": "Анкета: Подсказка (placeholder) никнейма",
            "modal_recruit_static_label": "Анкета: Название поля статика",
            "modal_recruit_static_ph": "Анкета: Подсказка статика",
            "modal_recruit_age_label": "Анкета: Название поля возраста",
            "modal_recruit_age_ph": "Анкета: Подсказка возраста",
            "modal_recruit_prev_label": "Анкета: Название поля прошлых семей",
            "modal_recruit_prev_ph": "Анкета: Подсказка прошлых семей",
            "modal_recruit_why_label": "Анкета: Название поля 'Почему Hallez FAMQ'",
            "modal_recruit_why_ph": "Анкета: Подсказка 'Почему Hallez FAMQ'",
            "modal_reject_title": "Заголовок окна отказа заявки",
            "modal_reject_label": "Отказ: Название поля причины",
            "modal_reject_ph": "Отказ: Подсказка поля причины",
            "modal_promo_nick_label": "Отчет: Поле никнейма",
            "modal_promo_static_label": "Отчет: Поле статика",
            "modal_promo_proof_1_2_label": "Отчет 1->2: Поле ссылки на фамилию",
            "modal_promo_proof_2_3_label": "Отчет 2->3: Поле ссылки на 14 дней",
            "modal_promo_proof_ph": "Отчет: Подсказка ссылки",
            "modal_promo_reject_title": "Заголовок окна отклонения отчета",
            "modal_promo_reject_label": "Отчет: Поле причины отклонения",
            "modal_promo_reject_ph": "Отчет: Подсказка причины отклонения"
        }
    },
    "selects": {
        "title": "📋 Меню выбора (Select Menus)",
        "items": {
            "select_voice_ph": "Обзвон: Подсказка меню выбора комнат",
            "select_promo_ph": "Повышение: Подсказка выбора ранга",
            "select_promo_opt_1_2_label": "Пункт 1->2: Название в списке",
            "select_promo_opt_1_2_desc": "Пункт 1->2: Описание в списке",
            "select_promo_opt_2_3_label": "Пункт 2->3: Название в списке",
            "select_promo_opt_2_3_desc": "Пункт 2->3: Описание в списке"
        }
    },
    "embeds_recruit": {
        "title": "📜 Эмбеды и сообщения набора",
        "items": {
            "recruit_panel_title": "Баннер набора: Заголовок",
            "recruit_panel_desc": "Баннер набора: Текст и критерии",
            "recruit_closed_msg": "Сообщение о закрытом наборе",
            "recruit_card_title": "Карточка в ветке: Заголовок",
            "recruit_card_desc": "Карточка в ветке: Описание",
            "recruit_review_title": "Заявка на рассмотрении: Заголовок",
            "recruit_review_desc": "Заявка на рассмотрении: Текст",
            "recruit_interview_title": "Вызов на обзвон: Заголовок",
            "recruit_interview_desc": "Вызов на обзвон: Текст ({mention}, {voice})",
            "recruit_accepted_title": "Принятие в семью: Заголовок",
            "recruit_accepted_desc": "Лог принятия: Описание ({mention}, {nick}, {static})",
            "recruit_accepted_thread_msg": "Сообщение в ветку при принятии",
            "recruit_accepted_dm": "Сообщение в ЛС при принятии",
            "recruit_rejected_title": "Отказ заявки: Заголовок",
            "recruit_rejected_desc": "Лог отказа: Описание ({mention}, {reason})",
            "recruit_rejected_dm": "Сообщение в ЛС при отказе"
        }
    },
    "embeds_promo": {
        "title": "📈 Эмбеды и сообщения повышений",
        "items": {
            "promo_panel_title": "Баннер отчетов: Заголовок",
            "promo_panel_desc": "Баннер отчетов: Критерии и описание",
            "promo_card_title": "Карточка отчета: Заголовок",
            "promo_card_desc": "Карточка отчета: Описание",
            "promo_approved_title": "Повышение одобрено: Заголовок",
            "promo_approved_desc": "Лог одобрения: Описание",
            "promo_approved_dm": "Сообщение в ЛС при повышении",
            "promo_rejected_title": "Отчет отклонен: Заголовок",
            "promo_rejected_desc": "Лог отклонения отчета: Описание",
            "promo_rejected_dm": "Сообщение в ЛС при отклонении отчета"
        }
    },
    "other": {
        "title": "⚔️ Формат ника, МП и футер",
        "items": {
            "nickname_format": "Шаблон ника на сервере ([Hallez FAMQ] {nick} | {static})",
            "event_card_title": "Сбор на МП: Заголовок ({title})",
            "event_card_desc": "Сбор на МП: Описание ({time}, {info})",
            "footer_text": "Текст в нижней части эмбедов (Footer)"
        }
    }
}


# -------------------------------------------------------------
# 2. МОДАЛКА ДЛЯ РЕДАКТИРОВАНИЯ ТЕКСТОВ
# -------------------------------------------------------------
class EditTextModal(disnake.ui.Modal):
    def __init__(self, key: str, label: str, current_value: str):
        self.key = key
        components = [
            disnake.ui.TextInput(
                label=label[:45],
                custom_id="text_val",
                style=disnake.TextInputStyle.paragraph,
                value=current_value[:4000],
                max_length=4000,
                required=True
            )
        ]
        super().__init__(title=f"Настройка: {label[:30]}", components=components)

    async def callback(self, inter: disnake.ModalInteraction):
        new_val = inter.text_values.get("text_val", "").strip()
        config = load_config()
        if "texts" not in config:
            config["texts"] = {}
        config["texts"][self.key] = new_val
        save_config(config)

        await inter.response.send_message(
            f"✅ Текст для **{self.key}** успешно обновлен и сохранен в `config.json`!",
            ephemeral=True
        )


# -------------------------------------------------------------
# 3. МЕНЮ НАСТРОЙКИ ТЕКСТОВ
# -------------------------------------------------------------
class TextCategorySelect(disnake.ui.StringSelect):
    def __init__(self):
        options = []
        for cat_id, cat_info in TEXT_CATEGORIES.items():
            options.append(
                disnake.SelectOption(
                    label=cat_info["title"][:100],
                    value=cat_id,
                    description=f"Настройка ({len(cat_info['items'])} текстов)"
                )
            )
        super().__init__(
            placeholder="1. Выберите категорию текстов для настройки...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="admin:text_category_select"
        )

    async def callback(self, inter: disnake.MessageInteraction):
        cat_id = self.values[0]
        cat_info = TEXT_CATEGORIES.get(cat_id, {})
        view = TextItemsView(cat_id, cat_info)
        await inter.response.edit_message(
            content=f"👉 Выберите конкретный текст из категории **{cat_info.get('title')}**:",
            view=view
        )


class TextItemSelect(disnake.ui.StringSelect):
    def __init__(self, cat_id: str, cat_info: Dict):
        self.cat_id = cat_id
        options = []
        for key, name in list(cat_info["items"].items())[:25]:
            options.append(
                disnake.SelectOption(
                    label=name[:100],
                    value=key,
                    description=f"Ключ: {key}"[:100]
                )
            )
        super().__init__(
            placeholder="2. Выберите текст для редактирования...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"admin:text_item_select:{cat_id}"
        )

    async def callback(self, inter: disnake.MessageInteraction):
        key = self.values[0]
        config = load_config()
        texts = config.get("texts", {})
        current_val = texts.get(key, "")

        cat_info = TEXT_CATEGORIES.get(self.cat_id, {})
        label = cat_info.get("items", {}).get(key, key)

        modal = EditTextModal(key=key, label=label, current_value=current_val)
        await inter.response.send_modal(modal)


class TextItemsView(disnake.ui.View):
    def __init__(self, cat_id: str, cat_info: Dict):
        super().__init__(timeout=300)
        self.add_item(TextItemSelect(cat_id, cat_info))

    @disnake.ui.button(label="🔙 Назад к категориям", style=disnake.ButtonStyle.secondary, row=1)
    async def back_btn(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        view = TextsView()
        await inter.response.edit_message(content=None, view=view)


class TextsView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(TextCategorySelect())

    @disnake.ui.button(label="🔙 Главное меню", style=disnake.ButtonStyle.secondary, row=1)
    async def back_btn(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        embed = await MainDashboardView.generate_dashboard_embed(inter.bot, inter.guild)
        await inter.response.edit_message(content=None, embed=embed, view=MainDashboardView())


# -------------------------------------------------------------
# 4. ПОДМЕНЮ: НАСТРОЙКА КАНАЛОВ
# -------------------------------------------------------------
class ChannelTypeSelect(disnake.ui.StringSelect):
    def __init__(self):
        options = [
            disnake.SelectOption(label="Канал подачи заявок", value="recruitment_channel_id", emoji="📥"),
            disnake.SelectOption(label="Канал отчетов на повышение", value="promotion_channel_id", emoji="📈"),
            disnake.SelectOption(label="Канал модерации отчетов", value="promotion_review_channel_id", emoji="🛡️"),
            disnake.SelectOption(label="Канал для ЛОГОВ семьи", value="logs_channel_id", emoji="📜"),
            disnake.SelectOption(label="Канал сборов на МП (капты/дропы)", value="events_channel_id", emoji="⚔️")
        ]
        super().__init__(
            placeholder="1. Выберите, какой канал настроить...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="admin:select_channel_target"
        )

    async def callback(self, inter: disnake.MessageInteraction):
        target = self.values[0]
        view = ChannelPickerView(target)
        await inter.response.edit_message(
            content=f"👉 Теперь выберите текстовый канал для параметра **{target}**:",
            view=view
        )


class ChannelPickerSelect(disnake.ui.ChannelSelect):
    def __init__(self, target_key: str):
        self.target_key = target_key
        super().__init__(
            channel_types=[disnake.ChannelType.text],
            placeholder="2. Выберите текстовый канал из списка...",
            min_values=1,
            max_values=1,
            custom_id="admin:pick_text_channel"
        )

    async def callback(self, inter: disnake.MessageInteraction):
        selected_ch = self.values[0]
        config = load_config()
        if "channels" not in config:
            config["channels"] = {}
        config["channels"][self.target_key] = selected_ch.id
        save_config(config)

        await inter.response.send_message(
            f"✅ Параметр **{self.target_key}** успешно привязан к каналу {selected_ch.mention}!",
            ephemeral=True
        )


class ChannelPickerView(disnake.ui.View):
    def __init__(self, target_key: str):
        super().__init__(timeout=300)
        self.add_item(ChannelPickerSelect(target_key))

    @disnake.ui.button(label="🔙 Назад к каналам", style=disnake.ButtonStyle.secondary, row=1)
    async def back_btn(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        view = ChannelsView()
        await inter.response.edit_message(content=None, view=view)


class ChannelsView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(ChannelTypeSelect())

    @disnake.ui.button(label="🔙 Главное меню", style=disnake.ButtonStyle.secondary, row=1)
    async def back_btn(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        embed = await MainDashboardView.generate_dashboard_embed(inter.bot, inter.guild)
        await inter.response.edit_message(content=None, embed=embed, view=MainDashboardView())


# -------------------------------------------------------------
# 5. ПОДМЕНЮ: НАСТРОЙКА ГОЛОСОВЫХ ДЛЯ ОБЗВОНА
# -------------------------------------------------------------
class VoiceChannelsSelect(disnake.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            channel_types=[disnake.ChannelType.voice],
            placeholder="Выберите голосовые каналы для обзвона (до 5)...",
            min_values=1,
            max_values=5,
            custom_id="admin:pick_voice_channels"
        )

    async def callback(self, inter: disnake.MessageInteraction):
        selected_channels = self.values
        config = load_config()
        config["voice_channels"] = [
            {"name": ch.name, "id": ch.id} for ch in selected_channels
        ]
        save_config(config)

        names = ", ".join([ch.name for ch in selected_channels])
        await inter.response.send_message(
            f"✅ Голосовые каналы для обзвона успешно сохранены: **{names}**!",
            ephemeral=True
        )


class VoiceChannelsView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(VoiceChannelsSelect())

    @disnake.ui.button(label="🔙 Главное меню", style=disnake.ButtonStyle.secondary, row=1)
    async def back_btn(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        embed = await MainDashboardView.generate_dashboard_embed(inter.bot, inter.guild)
        await inter.response.edit_message(content=None, embed=embed, view=MainDashboardView())


# -------------------------------------------------------------
# 6. ПОДМЕНЮ: НАСТРОЙКА РОЛЕЙ
# -------------------------------------------------------------
class RoleTypeSelect(disnake.ui.StringSelect):
    def __init__(self):
        options = [
            disnake.SelectOption(label="Основная роль семьи", value="family_role_id", emoji="👑"),
            disnake.SelectOption(label="Роль 1 ранга (Новичок)", value="rank_1_role_id", emoji="🥉"),
            disnake.SelectOption(label="Роль 2 ранга (Участник)", value="rank_2_role_id", emoji="🥈"),
            disnake.SelectOption(label="Роль 3 ранга (Опытный)", value="rank_3_role_id", emoji="🥇"),
            disnake.SelectOption(label="Роль рекрутера (проверяющего)", value="recruiter_role_id", emoji="👮"),
            disnake.SelectOption(label="Роль администратора бота", value="admin_role_id", emoji="🛡️")
        ]
        super().__init__(
            placeholder="1. Выберите, какую роль настроить...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="admin:select_role_target"
        )

    async def callback(self, inter: disnake.MessageInteraction):
        target = self.values[0]
        view = RolePickerView(target)
        await inter.response.edit_message(
            content=f"👉 Теперь выберите роль из списка для параметра **{target}**:",
            view=view
        )


class RolePickerSelect(disnake.ui.RoleSelect):
    def __init__(self, target_key: str):
        self.target_key = target_key
        super().__init__(
            placeholder="2. Выберите роль на сервере...",
            min_values=1,
            max_values=1,
            custom_id="admin:pick_role_component"
        )

    async def callback(self, inter: disnake.MessageInteraction):
        selected_role = self.values[0]
        config = load_config()

        if self.target_key == "recruiter_role_id":
            config["recruiter_role_ids"] = [selected_role.id]
        elif self.target_key == "admin_role_id":
            if selected_role.id not in config.get("admin_role_ids", []):
                config["admin_role_ids"] = [selected_role.id]
        else:
            if "roles" not in config:
                config["roles"] = {}
            config["roles"][self.target_key] = selected_role.id

            if self.target_key == "rank_1_role_id":
                config.setdefault("ranks", {}).setdefault("1", {})["role_id"] = selected_role.id
            elif self.target_key == "rank_2_role_id":
                config.setdefault("ranks", {}).setdefault("2", {})["role_id"] = selected_role.id
            elif self.target_key == "rank_3_role_id":
                config.setdefault("ranks", {}).setdefault("3", {})["role_id"] = selected_role.id

        save_config(config)
        await inter.response.send_message(
            f"✅ Роль {selected_role.mention} успешно сохранена для **{self.target_key}**!",
            ephemeral=True
        )


class RolePickerView(disnake.ui.View):
    def __init__(self, target_key: str):
        super().__init__(timeout=300)
        self.add_item(RolePickerSelect(target_key))

    @disnake.ui.button(label="🔙 Назад к ролям", style=disnake.ButtonStyle.secondary, row=1)
    async def back_btn(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        view = RolesView()
        await inter.response.edit_message(content=None, view=view)


class RolesView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(RoleTypeSelect())

    @disnake.ui.button(label="🔙 Главное меню", style=disnake.ButtonStyle.secondary, row=1)
    async def back_btn(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        embed = await MainDashboardView.generate_dashboard_embed(inter.bot, inter.guild)
        await inter.response.edit_message(content=None, embed=embed, view=MainDashboardView())


# -------------------------------------------------------------
# 7. ПОДМЕНЮ: ОТПРАВКА ПАНЕЛЕЙ В ОДИН КЛИК
# -------------------------------------------------------------
class SendPanelsView(disnake.ui.View):
    def __init__(self, recruitment_ch: Optional[disnake.TextChannel], promotion_ch: Optional[disnake.TextChannel]):
        super().__init__(timeout=300)
        self.recruitment_ch = recruitment_ch
        self.promotion_ch = promotion_ch

        rec_label = f"📥 Отправить панель набора в #{recruitment_ch.name}" if recruitment_ch else "📥 Панель набора (канал не привязан)"
        promo_label = f"📈 Отправить панель повышений в #{promotion_ch.name}" if promotion_ch else "📈 Панель повышений (канал не привязан)"

        self.send_rec_btn = disnake.ui.Button(
            label=rec_label[:80],
            style=disnake.ButtonStyle.success if recruitment_ch else disnake.ButtonStyle.secondary,
            disabled=recruitment_ch is None,
            row=0
        )
        self.send_rec_btn.callback = self.send_recruit_panel

        self.send_promo_btn = disnake.ui.Button(
            label=promo_label[:80],
            style=disnake.ButtonStyle.primary if promotion_ch else disnake.ButtonStyle.secondary,
            disabled=promotion_ch is None,
            row=1
        )
        self.send_promo_btn.callback = self.send_promo_panel

        self.add_item(self.send_rec_btn)
        self.add_item(self.send_promo_btn)

    async def send_recruit_panel(self, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        if not self.recruitment_ch:
            await inter.edit_original_response(content="❌ Сначала привяжите канал подачи заявок в меню 'Настройка каналов'!")
            return

        title = get_text("recruit_panel_title", "⚜️ НАБОР В СЕМЬЮ ZAKONOV FAMQ ⚜️")
        desc = get_text("recruit_panel_desc", "Подайте заявку на вступление через кнопку ниже:")
        emb = base_embed(title, desc, guild=inter.guild)
        view = RecruitLaunchView()
        await self.recruitment_ch.send(embed=emb, view=view)
        await inter.edit_original_response(content=f"✅ Панель набора успешно отправлена в {self.recruitment_ch.mention}!")

    async def send_promo_panel(self, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        if not self.promotion_ch:
            await inter.edit_original_response(content="❌ Сначала привяжите канал подачи отчетов в меню 'Настройка каналов'!")
            return

        title = get_text("promo_panel_title", "📈 ОТЧЕТЫ НА ПОВЫШЕНИЕ В СЕМЬЕ ZAKONOV FAMQ")
        desc = get_text("promo_panel_desc", "Выберите ранг из списка ниже для сдачи отчета:")
        emb = base_embed(title, desc, guild=inter.guild)
        view = PromotionLaunchView()
        await self.promotion_ch.send(embed=emb, view=view)
        await inter.edit_original_response(content=f"✅ Панель повышений успешно отправлена в {self.promotion_ch.mention}!")

    @disnake.ui.button(label="🔙 Главное меню", style=disnake.ButtonStyle.secondary, row=2)
    async def back_btn(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        embed = await MainDashboardView.generate_dashboard_embed(inter.bot, inter.guild)
# -------------------------------------------------------------
# 7.5. ПОДМЕНЮ: УПРАВЛЕНИЕ АДМИНИСТРАТОРАМИ (ВЫДАЧА ЧЕЛОВЕКУ)
# -------------------------------------------------------------
class AddAdminUserSelect(disnake.ui.UserSelect):
    def __init__(self):
        super().__init__(
            placeholder="➕ Выберите человека, чтобы ВЫДАТЬ ему админку...",
            min_values=1,
            max_values=1,
            custom_id="admin:add_admin_user"
        )

    async def callback(self, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        selected_user = self.values[0]
        config = load_config()
        admin_users = config.get("admin_user_ids", [])
        if selected_user.id not in admin_users:
            admin_users.append(selected_user.id)
            config["admin_user_ids"] = admin_users
            save_config(config)

        view = AdminUsersView(inter.guild)
        emb = AdminUsersView.generate_embed(inter.guild)
        await inter.edit_original_response(
            content=f"✅ Пользователю {selected_user.mention} (ID: `{selected_user.id}`) успешно выданы права администратора бота!",
            view=view
        )
        try:
            await inter.message.edit(embed=emb, view=view)
        except Exception:
            pass


class RemoveAdminUserSelect(disnake.ui.UserSelect):
    def __init__(self):
        super().__init__(
            placeholder="➖ Выберите человека, чтобы ЗАБРАТЬ админку...",
            min_values=1,
            max_values=1,
            custom_id="admin:remove_admin_user"
        )

    async def callback(self, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        selected_user = self.values[0]
        config = load_config()
        admin_users = config.get("admin_user_ids", [])
        if selected_user.id in admin_users:
            admin_users.remove(selected_user.id)
            config["admin_user_ids"] = admin_users
            save_config(config)

        view = AdminUsersView(inter.guild)
        emb = AdminUsersView.generate_embed(inter.guild)
        await inter.edit_original_response(
            content=f"❌ Права администратора бота у {selected_user.mention} (ID: `{selected_user.id}`) успешно сняты!",
            view=view
        )
        try:
            await inter.message.edit(embed=emb, view=view)
        except Exception:
            pass


class AdminUsersView(disnake.ui.View):
    def __init__(self, guild: Optional[disnake.Guild] = None):
        super().__init__(timeout=300)
        self.add_item(AddAdminUserSelect())
        self.add_item(RemoveAdminUserSelect())

    @staticmethod
    def generate_embed(guild: Optional[disnake.Guild]) -> disnake.Embed:
        config = load_config()
        admin_users = config.get("admin_user_ids", [])

        emb = base_embed(
            "👤 Управление администраторами (по пользователям)",
            "Здесь вы можете **выдать или забрать доступ к боту конкретному человеку** (без необходимости создавать роль).\n\n"
            "👇 Используйте списки выбора ниже:\n"
            "• **1-й список (➕):** выбрать человека, чтобы ВЫДАТЬ админку\n"
            "• **2-й список (➖):** выбрать человека, чтобы ЗАБРАТЬ админку",
            guild=guild
        )

        owner_str = f"<@{guild.owner_id}> (Владелец сервера)" if guild else "Владелец"
        emb.add_field(name="👑 Главный владелец", value=owner_str, inline=False)

        if admin_users:
            users_list = "\n".join([f"• <@{uid}> (ID: `{uid}`)" for uid in admin_users])
        else:
            users_list = "*Пока нет добавленных пользователей. Выберите человека в списке ниже, чтобы выдать доступ.*"

        emb.add_field(name=f"👥 Авторизованные администраторы ({len(admin_users)})", value=users_list, inline=False)
        return emb

    @disnake.ui.button(label="🔙 Главное меню", style=disnake.ButtonStyle.secondary, row=2)
    async def back_btn(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        embed = await MainDashboardView.generate_dashboard_embed(inter.bot, inter.guild)
        await inter.response.edit_message(content=None, embed=embed, view=MainDashboardView())


# -------------------------------------------------------------
# 8. ГЛАВНОЕ МЕНЮ / ДАШБОРД /ADMIN
# -------------------------------------------------------------
class MainCategorySelect(disnake.ui.StringSelect):
    def __init__(self):
        options = [
            disnake.SelectOption(
                label="📢 Отправить панели в каналы (в 1 клик)",
                value="cat_panels",
                emoji="🚀",
                description="Опубликовать кнопки набора или меню повышений в привязанные чаты"
            ),
            disnake.SelectOption(
                label="👤 Администраторы (выдать доступ человеку)",
                value="cat_admin_users",
                emoji="👑",
                description="Выбрать конкретного пользователя Discord и выдать/забрать админку"
            ),
            disnake.SelectOption(
                label="📍 Настройка каналов",
                value="cat_channels",
                emoji="⚙️",
                description="Привязать каналы для заявок, отчетов, модерации, логов и МП"
            ),
            disnake.SelectOption(
                label="🎙️ Настройка обзвонов",
                value="cat_voice",
                emoji="🔊",
                description="Выбрать голосовые каналы, куда отправлять кандидатов"
            ),
            disnake.SelectOption(
                label="🎭 Настройка ролей",
                value="cat_roles",
                emoji="🛡️",
                description="Назначить роли семьи, рангов (1, 2, 3), рекрутеров и админов"
            ),
            disnake.SelectOption(
                label="📝 Настройка текстов (Абсолютно любой текст)",
                value="cat_texts",
                emoji="✍️",
                description="Изменить любые кнопки, модалки, селекторы, описания или шаблоны"
            )
        ]
        super().__init__(
            placeholder="Выберите раздел настроек Hallez FAMQ...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="admin:select_main_category"
        )

    async def callback(self, inter: disnake.MessageInteraction):
        if not is_admin(inter):
            await inter.response.send_message("❌ Доступ запрещен!", ephemeral=True)
            return

        choice = self.values[0]
        config = load_config()

        if choice == "cat_panels":
            rec_id = config.get("channels", {}).get("recruitment_channel_id")
            promo_id = config.get("channels", {}).get("promotion_channel_id")
            rec_ch = inter.guild.get_channel(rec_id) if rec_id else None
            promo_ch = inter.guild.get_channel(promo_id) if promo_id else None

            emb = base_embed(
                "📢 Публикация интерактивных панелей",
                f"Нажмите кнопку ниже, чтобы мгновенно отправить панель в привязанный канал:\n\n"
                f"• Канал набора: {rec_ch.mention if rec_ch else '❌ *Не привязан*'}\n"
                f"• Канал отчетов: {promo_ch.mention if promo_ch else '❌ *Не привязан*'}",
                guild=inter.guild
            )
            await inter.response.edit_message(embed=emb, view=SendPanelsView(rec_ch, promo_ch))

        elif choice == "cat_admin_users":
            emb = AdminUsersView.generate_embed(inter.guild)
            view = AdminUsersView(inter.guild)
            await inter.response.edit_message(embed=emb, view=view)

        elif choice == "cat_channels":
            emb = base_embed(
                "📍 Конфигурация текстовых каналов",
                "Выберите тип канала, который хотите настроить, а затем укажите сам канал:",
                guild=inter.guild
            )
            await inter.response.edit_message(embed=emb, view=ChannelsView())

        elif choice == "cat_voice":
            emb = base_embed(
                "🎙️ Настройка голосовых комнат для обзвона",
                "Выберите один или несколько голосовых каналов (до 5), которые будут доступны в меню рекрутера при вызове кандидата:",
                guild=inter.guild
            )
            await inter.response.edit_message(embed=emb, view=VoiceChannelsView())

        elif choice == "cat_roles":
            emb = base_embed(
                "🎭 Конфигурация ролей семьи и рангов",
                "Выберите, какую роль вы хотите привязать:",
                guild=inter.guild
            )
            await inter.response.edit_message(embed=emb, view=RolesView())

        elif choice == "cat_texts":
            emb = base_embed(
                "📝 Полный редактор всех текстов бота",
                "Выберите категорию текстов для настройки. Можно изменить **абсолютно любой символ, слово, кнопку или фразу**:",
                guild=inter.guild
            )
            await inter.response.edit_message(embed=emb, view=TextsView())


class MainDashboardView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(MainCategorySelect())

    @disnake.ui.button(label="🔒 Тумблер набора", style=disnake.ButtonStyle.primary, emoji="🔄", row=1)
    async def toggle_recruit(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        config = load_config()
        current = config.get("recruitment_open", True)
        config["recruitment_open"] = not current
        save_config(config)
        state_str = "🟢 ОТКРЫТ" if config["recruitment_open"] else "🔴 ЗАКРЫТ"
        await inter.edit_original_response(content=f"Прием заявок в семью теперь: **{state_str}**")

    @disnake.ui.button(label="🏷️ Тумблер авто-ников", style=disnake.ButtonStyle.secondary, emoji="✏️", row=1)
    async def toggle_nicknames(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        config = load_config()
        current = config.get("auto_nicknames", True)
        config["auto_nicknames"] = not current
        save_config(config)
        state_str = "🟢 ВКЛЮЧЕНО" if config["auto_nicknames"] else "🔴 ВЫКЛЮЧЕНО"
        await inter.edit_original_response(content=f"Авто-смена ников теперь: **{state_str}**")

    @disnake.ui.button(label="🔄 Обновить дашборд", style=disnake.ButtonStyle.secondary, emoji="🔃", row=1)
    async def refresh_btn(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        embed = await self.generate_dashboard_embed(inter.bot, inter.guild)
        await inter.response.edit_message(embed=embed, view=self)

    @staticmethod
    async def generate_dashboard_embed(bot: commands.Bot, guild: Optional[disnake.Guild]) -> disnake.Embed:
        config = load_config()
        stats = await get_stats()

        recruit_status = "🟢 ОТКРЫТ" if config.get("recruitment_open", True) else "🔴 ЗАКРЫТ"
        nick_status = "🟢 ВКЛЮЧЕНО" if config.get("auto_nicknames", True) else "🔴 ВЫКЛЮЧЕНО"
        ping_ms = round(bot.latency * 1000)

        emb = base_embed(
            "👑 ПАНЕЛЬ УПРАВЛЕНИЯ ZAKONOV FAMQ • АДМИН-ЦЕНТР",
            "Главная консоль управления рекрутингом, составом и функционалом бота для руководства семьи.",
            guild=guild
        )

        emb.add_field(
            name="⚙️ Текущий статус системы",
            value=(
                f"• **Пинг бота:** `{ping_ms} мс`\n"
                f"• **Прием заявок:** **{recruit_status}**\n"
                f"• **Авто-смена ников:** **{nick_status}**\n"
                f"• **База данных:** `🟢 SQLite Active`"
            ),
            inline=False
        )

        channels = config.get("channels", {})
        rec_ch = f"<#{channels.get('recruitment_channel_id')}>" if channels.get('recruitment_channel_id') else "❌ Не указан"
        promo_ch = f"<#{channels.get('promotion_channel_id')}>" if channels.get('promotion_channel_id') else "❌ Не указан"
        rev_ch = f"<#{channels.get('promotion_review_channel_id')}>" if channels.get('promotion_review_channel_id') else "❌ Не указан"
        log_ch = f"<#{channels.get('logs_channel_id')}>" if channels.get('logs_channel_id') else "❌ Не указан"

        emb.add_field(
            name="📍 Привязка каналов",
            value=(
                f"• **Заявки:** {rec_ch}\n"
                f"• **Отчеты:** {promo_ch}\n"
                f"• **Модерация:** {rev_ch}\n"
                f"• **Логи в чат:** {log_ch}"
            ),
            inline=False
        )

        voices = config.get("voice_channels", [])
        voice_str = ", ".join([f"<#{v['id']}>" for v in voices]) if voices else "❌ Не выбраны"
        emb.add_field(name="🎙️ Голосовые каналы обзвона", value=voice_str, inline=False)

        admin_users = config.get("admin_user_ids", [])
        admin_users_str = ", ".join([f"<@{uid}>" for uid in admin_users]) if admin_users else "❌ Нет (только владелец)"
        emb.add_field(name="👤 Администраторы (пользователи)", value=admin_users_str, inline=False)

        emb.add_field(
            name="📊 Статистика семьи Hallez FAMQ",
            value=(
                f"• 📝 Заявок на рассмотрении: **{stats['pending_apps']}**\n"
                f"• 👥 Принято через бота: **{stats['accepted_apps']}**\n"
                f"• 📈 Ожидает проверки повышений: **{stats['pending_promos']}**\n"
                f"• ⚠️ Активных выговоров: **{stats['active_warns']}**"
            ),
            inline=False
        )

        emb.set_footer(text="Hallez FAMQ Admin System • Все изменения сохраняются в config.json")
        return emb


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command(
        name="admin",
        description="Главная панель управления семьей Hallez FAMQ (Только для руководства)"
    )
    async def admin_command(self, inter: disnake.ApplicationCommandInteraction):
        if not is_admin(inter):
            await inter.response.send_message(
                "❌ **Доступ запрещен.** Команда `/admin` доступна только доверенным лицам по ID или роли администратора.",
                ephemeral=True
            )
            return

        embed = await MainDashboardView.generate_dashboard_embed(self.bot, inter.guild)
        view = MainDashboardView()
        await inter.response.send_message(embed=embed, view=view, ephemeral=True)

    @commands.slash_command(
        name="codex",
        description="Быстрый вызов панели Hallez FAMQ (Алиас для /admin)"
    )
    async def codex_command(self, inter: disnake.ApplicationCommandInteraction):
        await self.admin_command(inter)

    @commands.slash_command(
        name="addadmin",
        description="Выдать права администратора бота конкретному человеку"
    )
    async def add_admin_command(
        self,
        inter: disnake.ApplicationCommandInteraction,
        user: disnake.User = commands.Param(description="Пользователь, которому выдать админку")
    ):
        if not is_admin(inter):
            await inter.response.send_message("❌ Доступ запрещен!", ephemeral=True)
            return

        config = load_config()
        admin_users = config.get("admin_user_ids", [])
        if user.id not in admin_users:
            admin_users.append(user.id)
            config["admin_user_ids"] = admin_users
            save_config(config)

        await inter.response.send_message(
            f"✅ Пользователю {user.mention} (ID: `{user.id}`) успешно выданы права администратора бота!",
            ephemeral=True
        )

    @commands.slash_command(
        name="removeadmin",
        description="Забрать права администратора бота у конкретного человека"
    )
    async def remove_admin_command(
        self,
        inter: disnake.ApplicationCommandInteraction,
        user: disnake.User = commands.Param(description="Пользователь, у которого забрать админку")
    ):
        if not is_admin(inter):
            await inter.response.send_message("❌ Доступ запрещен!", ephemeral=True)
            return

        config = load_config()
        admin_users = config.get("admin_user_ids", [])
        if user.id in admin_users:
            admin_users.remove(user.id)
            config["admin_user_ids"] = admin_users
            save_config(config)

        await inter.response.send_message(
            f"❌ Права администратора бота у {user.mention} (ID: `{user.id}`) успешно сняты!",
            ephemeral=True
        )


def setup(bot: commands.Bot):
    bot.add_cog(AdminCog(bot))
