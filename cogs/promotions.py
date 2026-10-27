import json
from pathlib import Path

import disnake
from disnake.ext import commands
from typing import Optional
from utils.checks import load_config, is_recruiter, get_text, rank_title
from utils.embeds import base_embed, brand_name, success_embed, error_embed, warning_embed
from database import (
    create_promotion_report,
    get_promotion_by_id,
    update_promotion_status,
    upsert_member,
    get_member
)


# ───────────────────── премиум-эмодзи из emoji_map.json ─────────────────────
_EMOJI_FILE = Path(__file__).resolve().parent.parent / "emoji_map.json"


def em(name: str) -> str:
    """Код премиум-эмодзи <:имя:id> из emoji_map.json (пустая строка, если такого нет)."""
    try:
        with open(_EMOJI_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get(name, "")
    except Exception:
        return ""


def pe(name: str) -> Optional[disnake.PartialEmoji]:
    """То же самое для выпадающего списка и кнопок."""
    code = em(name)
    if not code:
        return None
    try:
        return disnake.PartialEmoji.from_str(code)
    except Exception:
        return None


def build_promo_panel_embed(guild: Optional[disnake.Guild] = None) -> disnake.Embed:
    """Панель «Отчёты на повышение» — оформлена премиум-эмодзи бота."""
    cfg = load_config()
    bot_name = (cfg.get("bot_name") or "Hallez FAMQ").upper()
    crown = em("red_crown")
    arrow = "➔"
    line = "▬" * 24
    num = {i: em(f"number{i}") for i in range(1, 5)}
    sword_2, sword_3, sword_4 = em("hz_moderatorroleicon"), em("hz_bluemodroleicon"), em("hz_headmoderatorroleicon")

    desc = (
        f"### {em('hz_megaphone')}  Растём вместе с семьёй\n"
        "Выполнил условия своего ранга? Подай отчёт, руководство проверит его и выдаст повышение.\n"
        f"\n{line}\n\n"
        f"### {em('hz_book')}  Путь по рангам\n"
        f"> {num[1]} {arrow} {num[2]}   {sword_2}  **{rank_title(cfg, 2)}**\n"
        f"> {em('hz_pencil')}  Смена фамилии в игре на `Hallez FAMQ`\n"
        f"> {em('hz_pcicon')}  Скриншот паспорта / смены\n"
        ">\n"
        f"> {num[2]} {arrow} {num[3]}   {sword_3}  **{rank_title(cfg, 3)}**\n"
        f"> {em('hz_house')}  Более 14 дней в семье\n"
        f"> {em('hz_pcicon')}  Скриншот планшета / профиля\n"
        ">\n"
        f"> {num[3]} {arrow} {num[4]}   {sword_4}  **{rank_title(cfg, 4)}**\n"
        f"> {em('hz_moderator')}  По индивидуальным нормативам и решению руководства\n"
        f"\n{line}\n\n"
        f"### {em('hz_pencil')}  Как подать отчёт\n"
        f"{em('hz_moderator')}  Выбери свой ранг в списке ниже\n"
        f"{em('hz_pencil')}  Заполни форму: ник, статик и ссылка на скриншот\n"
        f"{em('hz_gifticon')}  Дождись решения, ответ придёт в личные сообщения\n"
        "\n"
        f"{em('hz_warn')}  **Отчёт без доказательств будет отклонён.**"
    )
    title = f"{crown}  ОТЧЁТЫ НА ПОВЫШЕНИЕ  ·  {bot_name}  {crown}".strip()
    return base_embed(title, desc, guild=guild)


class PromoRejectModal(disnake.ui.Modal):
    def __init__(self, promo_id: int, applicant_id: int):
        self.promo_id = promo_id
        self.applicant_id = applicant_id

        title = get_text("modal_promo_reject_title", "Отклонение отчета на повышение")
        label = get_text("modal_promo_reject_label", "Причина отклонения отчета")
        placeholder = get_text("modal_promo_reject_ph", "Недостаточно доказательств / не прошло 14 дней...")

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
        reason = inter.text_values.get("reject_reason", "Причина не указана")

        await update_promotion_status(self.promo_id, status="rejected", reviewer_id=inter.author.id, reject_reason=reason)

        applicant = inter.guild.get_member(self.applicant_id)
        if not applicant:
            try:
                applicant = await inter.guild.fetch_member(self.applicant_id)
            except Exception:
                applicant = None

        mention_str = applicant.mention if applicant else f"<@{self.applicant_id}>"

        title = get_text("promo_rejected_title", "Отчет на повышение отклонен")
        desc_tpl = get_text(
            "promo_rejected_desc",
            "👤 **Сотрудник:** {mention}\n👮 **Проверяющий:** {reviewer}\n📝 **Причина:** {reason}"
        )
        desc = desc_tpl.format(mention=mention_str, reviewer=inter.author.mention, reason=reason)
        emb = error_embed(title, desc, guild=inter.guild)

        try:
            disabled_view = disnake.ui.View.from_message(inter.message)
            for item in disabled_view.children:
                item.disabled = True
            await inter.message.edit(embed=emb, view=disabled_view)
        except Exception:
            pass

        if applicant:
            try:
                dm_tpl = get_text(
                    "promo_rejected_dm",
                    "Здравствуйте, {nick}!\nВаш отчет на повышение был проверен и **отклонен**.\n\n**Причина:** {reason}\n\nИсправьте недочеты и отправьте отчет заново."
                )
                dm_emb = error_embed(
                    f"Отчет на повышение в {brand_name()} отклонен",
                    dm_tpl.format(nick=applicant.display_name, reason=reason, reviewer=inter.author.display_name),
                    guild=inter.guild
                )
                await applicant.send(embed=dm_emb)
            except Exception:
                pass

        config = load_config()
        log_channel_id = config.get("channels", {}).get("logs_channel_id")
        if log_channel_id:
            log_ch = inter.guild.get_channel(log_channel_id)
            if log_ch:
                await log_ch.send(embed=emb)

        await inter.edit_original_response(content="❌ Отчет отклонен, автор уведомлен.")


class PromotionModal(disnake.ui.Modal):
    def __init__(self, target_rank: int):
        self.target_rank = target_rank
        rank_titles = {
            2: "1 ➔ 2 ранг (Смена фамилии на Zakonov)",
            3: "2 ➔ 3 ранг (>2 недель в семье)",
            4: "3 ➔ 4 ранг (Актив и спец. задания)"
        }
        title = rank_titles.get(target_rank, f"Отчет на {target_rank} ранг")
        
        lbl_nick = get_text("modal_promo_nick_label", "Игровой никнейм (Имя Фамилия)")
        ph_nick = get_text("modal_promo_nick_ph", "Пример: Travis Zakonov")
        lbl_static = get_text("modal_promo_static_label", "Статический ID")
        ph_static = get_text("modal_promo_static_ph", "Пример: 12345")
        
        if target_rank == 2:
            lbl_proof = get_text("modal_promo_proof_1_2_label", "Ссылка на док-ва смены фамилии")
        else:
            lbl_proof = get_text("modal_promo_proof_2_3_label", "Ссылка на док-ва нахождения >2 недель")
        ph_proof = get_text("modal_promo_proof_ph", "Вставьте ссылку на скриншот (Imgur / Yapx / Discord)...")

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
                label=lbl_proof[:45],
                custom_id="proof_url",
                style=disnake.TextInputStyle.paragraph,
                placeholder=ph_proof[:100],
                min_length=5,
                max_length=500,
                required=True
            )
        ]
        super().__init__(title=title[:45], components=components)

    async def callback(self, inter: disnake.ModalInteraction):
        nick = inter.text_values.get("nick", "").strip()
        static_id = inter.text_values.get("static_id", "").strip()
        proof_url = inter.text_values.get("proof_url", "").strip()

        await inter.response.defer(ephemeral=True)

        config = load_config()
        review_ch_id = config.get("channels", {}).get("promotion_review_channel_id")
        if not review_ch_id:
            review_ch = inter.channel
        else:
            review_ch = inter.guild.get_channel(review_ch_id) or inter.channel

        promo_id = await create_promotion_report(
            user_id=inter.author.id,
            nick=nick,
            static_id=static_id,
            target_rank=self.target_rank,
            proof_url=proof_url
        )

        target_name = rank_title(config, self.target_rank)
        prev_rank = self.target_rank - 1
        prev_name = rank_title(config, prev_rank)

        title_tpl = get_text("promo_card_title", "📈 Отчет на повышение #{id}")
        desc_tpl = get_text(
            "promo_card_desc",
            "Участник {mention} подал отчет на повышение в должности семьи **{brand}**.\nКвалификация: **{prev_name} ({prev_rank}) ➔ {target_name} ({target_rank})**"
        )
        report_emb = base_embed(
            title_tpl.format(id=promo_id),
            desc_tpl.format(
                mention=inter.author.mention,
                prev_name=prev_name,
                prev_rank=prev_rank,
                target_name=target_name,
                target_rank=self.target_rank,
                brand=brand_name()
            ),
            guild=inter.guild
        )
        report_emb.add_field(name="👤 Никнейм", value=f"`{nick}`", inline=True)
        report_emb.add_field(name="🆔 Статик", value=f"`{static_id}`", inline=True)
        report_emb.add_field(name="🎯 Целевой ранг", value=f"`{self.target_rank}` ({target_name})", inline=True)
        report_emb.add_field(name="📎 Доказательства выполнения условий", value=proof_url, inline=False)

        btn_accept_lbl = get_text("btn_promo_accept", "Одобрить повышение")
        btn_reject_lbl = get_text("btn_promo_reject", "Отклонить отчет")

        view = disnake.ui.View(timeout=None)
        view.add_item(
            disnake.ui.Button(
                style=disnake.ButtonStyle.success,
                label=btn_accept_lbl[:80],
                emoji="✅",
                custom_id=f"codex:promo_accept:{promo_id}"
            )
        )
        view.add_item(
            disnake.ui.Button(
                style=disnake.ButtonStyle.danger,
                label=btn_reject_lbl[:80],
                emoji="❌",
                custom_id=f"codex:promo_reject:{promo_id}"
            )
        )

        recruiter_roles = config.get("recruiter_role_ids", [])
        ping_content = " ".join([f"<@&{r_id}>" for r_id in recruiter_roles if r_id > 0])

        await review_ch.send(content=ping_content if ping_content else None, embed=report_emb, view=view)
        await inter.edit_original_response(content=f"✅ Ваш отчет на повышение (ранг {self.target_rank}) успешно отправлен на рассмотрение руководству!")


class PromotionRankSelect(disnake.ui.StringSelect):
    def __init__(self):
        ph = get_text("select_promo_ph", "Выберите ранг, на который повышаетесь...")
        cfg = load_config()

        options = [
            disnake.SelectOption(
                label=f"1 ➔ 2 ранг  ·  {rank_title(cfg, 2)}"[:100],
                value="2",
                emoji=pe("hz_moderatorroleicon"),
                description="Смена фамилии на Hallez FAMQ + скриншот"[:100]
            ),
            disnake.SelectOption(
                label=f"2 ➔ 3 ранг  ·  {rank_title(cfg, 3)}"[:100],
                value="3",
                emoji=pe("hz_bluemodroleicon"),
                description="Более 14 дней в семье + скриншот профиля"[:100]
            ),
            disnake.SelectOption(
                label=f"3 ➔ 4 ранг  ·  {rank_title(cfg, 4)}"[:100],
                value="4",
                emoji=pe("hz_headmoderatorroleicon"),
                description="По нормативам и решению руководства"[:100]
            )
        ]
        super().__init__(
            placeholder=ph[:100],
            min_values=1,
            max_values=1,
            options=options,
            custom_id="codex:promo_rank_select"
        )

    async def callback(self, inter: disnake.MessageInteraction):
        target_rank = int(self.values[0])
        modal = PromotionModal(target_rank=target_rank)
        await inter.response.send_modal(modal)


class PromotionLaunchView(disnake.ui.View):
    """Постоянная панель в канале отчетов"""
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(PromotionRankSelect())


class Promotions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_button_click(self, inter: disnake.MessageInteraction):
        custom_id = inter.component.custom_id
        if not custom_id.startswith("codex:promo_"):
            return

        parts = custom_id.split(":")
        if len(parts) < 3:
            return

        action = parts[1]
        try:
            promo_id = int(parts[2])
        except ValueError:
            return

        if not is_recruiter(inter.author):
            await inter.response.send_message("❌ У вас нет прав для проверки отчетов на повышение!", ephemeral=True)
            return

        promo = await get_promotion_by_id(promo_id)
        if not promo:
            await inter.response.send_message("❌ Отчет не найден в базе данных.", ephemeral=True)
            return

        if promo["status"] != "pending":
            try:
                disabled_view = disnake.ui.View.from_message(inter.message)
                for item in disabled_view.children:
                    item.disabled = True
                await inter.message.edit(view=disabled_view)
            except Exception:
                pass
            status_ru = "одобрен" if promo["status"] == "approved" else "отклонен"
            await inter.response.send_message(f"⚠️ Этот отчет уже был **{status_ru}**!", ephemeral=True)
            return

        applicant_id = promo["user_id"]
        applicant = inter.guild.get_member(applicant_id)
        if not applicant:
            try:
                applicant = await inter.guild.fetch_member(applicant_id)
            except Exception:
                applicant = None

        if action == "promo_reject":
            modal = PromoRejectModal(promo_id=promo_id, applicant_id=applicant_id)
            await inter.response.send_modal(modal)

        elif action == "promo_accept":
            await inter.response.defer(ephemeral=True)

            try:
                disabled_view = disnake.ui.View.from_message(inter.message)
                for item in disabled_view.children:
                    item.disabled = True
                await inter.message.edit(view=disabled_view)
            except Exception:
                pass

            config = load_config()
            target_rank = promo["target_rank"]
            prev_rank = target_rank - 1

            ranks_cfg = config.get("ranks", {})
            target_role_id = ranks_cfg.get(str(target_rank), {}).get("role_id")
            prev_role_id = ranks_cfg.get(str(prev_rank), {}).get("role_id")

            # Обновление ролей
            if applicant:
                if prev_role_id:
                    old_role = inter.guild.get_role(prev_role_id)
                    if old_role and old_role in applicant.roles:
                        try:
                            await applicant.remove_roles(old_role, reason=f"Повышение до {target_rank} ранга")
                        except Exception:
                            pass

                if target_role_id:
                    new_role = inter.guild.get_role(target_role_id)
                    if new_role:
                        try:
                            await applicant.add_roles(new_role, reason=f"Повышен до {target_rank} ранга проверяющим {inter.author}")
                        except Exception as e:
                            print(f"Ошибка добавления новой роли ранга: {e}")

                nick_format = get_text("nickname_format", "[Zakonov | {rank}] {nick} | {static}")
                if config.get("auto_nicknames", True):
                    try:
                        new_nick = nick_format.format(rank=str(target_rank), nick=promo["nick"], static=promo["static_id"])
                        await applicant.edit(nick=new_nick[:32])
                    except Exception:
                        pass

            await update_promotion_status(promo_id, status="approved", reviewer_id=inter.author.id)
            await upsert_member(user_id=applicant_id, nick=promo["nick"], static_id=promo["static_id"], rank=target_rank)

            target_name = rank_title(config, target_rank)

            title = get_text("promo_approved_title", "Отчет одобрен • Повышение выдано!")
            desc_tpl = get_text(
                "promo_approved_desc",
                "🎉 Участник {mention} повышен до **{target_name} ({target_rank} ранг)**!\n\n👤 **Никнейм:** `{nick}`\n🆔 **Статик:** `{static}`\n👮 **Проверил и одобрил:** {reviewer}\n📎 **Доказательства:** {proof_url}"
            )
            desc = desc_tpl.format(
                mention=f"<@{applicant_id}>",
                target_name=target_name,
                target_rank=target_rank,
                nick=promo["nick"],
                static=promo["static_id"],
                reviewer=inter.author.mention,
                proof_url=promo["proof_url"]
            )
            approved_emb = success_embed(title, desc, guild=inter.guild)

            try:
                await inter.message.edit(embed=approved_emb)
            except Exception:
                pass

            if applicant:
                try:
                    dm_tpl = get_text(
                        "promo_approved_dm",
                        "Поздравляем с повышением в семье {brand}!\nВаш отчет на повышение был **одобрен** проверяющим {reviewer}!\nВам присвоен ранг: **{target_name} ({target_rank} ранг)**.\nПродолжайте показывать отличный актив на благо семьи!"
                    )
                    dm_text = dm_tpl.format(
                        reviewer=inter.author.display_name,
                        target_name=target_name,
                        target_rank=target_rank,
                        nick=promo["nick"],
                        brand=brand_name()
                    )
                    dm_emb = success_embed(f"Поздравляем с повышением в семье {brand_name()}!", dm_text, guild=inter.guild)
                    await applicant.send(embed=dm_emb)
                except Exception:
                    pass

            log_channel_id = config.get("channels", {}).get("logs_channel_id")
            if log_channel_id:
                log_ch = inter.guild.get_channel(log_channel_id)
                if log_ch:
                    await log_ch.send(embed=approved_emb)

            await inter.edit_original_response(content=f"✅ Повышение для <@{applicant_id}> успешно выдано!")


def setup(bot: commands.Bot):
    bot.add_cog(Promotions(bot))
