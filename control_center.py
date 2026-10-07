"""
⚜️ Центр управления: /центр
Одна панель для всех новых функций: логи (по каналам), авто-роли при входе, отпуска,
защита выговоров, ранги и карточка, кандидаты, казна, розыгрыши.
Навигация: меню разделов -> меню параметров -> выбор канала/роли -> готово (сохраняется сразу).
Доступ: только администраторы бота (admin_user_ids / admin_role_ids из config.json) и админы Discord.
"""
from collections import namedtuple
from typing import Any, List, Optional

import disnake
from disnake.ext import commands

import database
from utils import famq_core as core

P = namedtuple("P", "key label kind lo hi")


def param(key: str, label: str, kind: str, lo: int = 0, hi: int = 10 ** 9) -> P:
    return P(key, label, kind, lo, hi)


SECTIONS = {
    "logs": {
        "emoji": "📜", "title": "Логи",
        "desc": "Куда бот пишет события. Если для типа канал не задан — используется **общий**. "
                "Нет ни того, ни другого — тип не логируется.",
        "params": [
            param("logs.default", "Общий канал логов", "channel"),
            param("logs.join", "Заходы на сервер", "channel"),
            param("logs.leave", "Выходы с сервера", "channel"),
            param("logs.messages", "Сообщения (удаление/правка)", "channel"),
            param("logs.voice", "Голосовые каналы", "channel"),
            param("logs.roles", "Выдача/снятие ролей", "channel"),
            param("logs.nicknames", "Смена ников", "channel"),
            param("logs.moderation", "Баны, разбаны, таймауты", "channel"),
            param("logs.server", "Каналы и роли сервера", "channel"),
            param("logs.audit", "Журнал действий администраторов", "channel"),
            param("logs.commands", "Использование команд бота", "channel"),
            param("logs.bot", "События и ошибки бота", "channel"),
            param("logs.vacation", "Отпуска", "channel"),
            param("logs.treasury", "Казна", "channel"),
            param("logs.giveaway", "Розыгрыши", "channel"),
        ],
        "actions": [("Тест логов", "🧪", "act_test_logs")],
    },
    "autorole": {
        "emoji": "🚪", "title": "Авто-роли при входе",
        "desc": "Какие роли бот выдаёт сам, когда человек заходит на сервер.",
        "params": [
            param("autorole.enabled", "Авто-роли включены", "bool"),
            param("autorole.roles", "Роли для новых участников", "roles"),
            param("autorole.bots", "Роль для ботов", "role"),
        ],
        "actions": [],
    },
    "vacation": {
        "emoji": "🌴", "title": "Отпуска",
        "desc": "Отпуск оформляется заявкой через панель с кнопкой (команд нет). "
                "Роль АФК тут нужна для защиты выговоров.",
        "params": [
            param("vacation.role_id", "Роль «Отпуск»", "role"),
            param("vacation.afk_role_id", "Роль «АФК»", "role"),
            param("vacation.review_channel_id", "Канал, куда приходят заявки", "channel"),
            param("vacation.panel_channel_id", "Канал с панелью подачи заявок", "channel"),
            param("vacation.reviewer_roles", "Кто рассматривает заявки (роли)", "roles"),
            param("vacation.min_days", "Минимум дней отпуска", "int", 1, 365),
            param("vacation.max_days", "Максимум дней отпуска", "int", 1, 365),
        ],
        "actions": [("Опубликовать панель", "📌", "act_vac_panel"), ("Активные отпуска", "📋", "act_vac_list")],
    },
    "warn_guard": {
        "emoji": "⚠️", "title": "Защита выговоров",
        "desc": "Если выдают выговор человеку с ролью отпуска/АФК, бот спросит подтверждение. "
                "Срабатывает на команды, в названии которых есть одно из слов.",
        "params": [
            param("warn_guard.enabled", "Защита включена", "bool"),
            param("warn_guard.keywords", "Слова в названии команды (через запятую)", "text"),
        ],
        "actions": [],
    },
    "ranks": {
        "emoji": "🎖️", "title": "Ранги и карточка",
        "desc": "Ранг в карточке (профиле) берётся из ролей Discord. Укажите, какие роли какому рангу соответствуют, "
                "и нажмите «Синхронизировать» — карточки подтянутся к ролям.",
        "params": None,  # строится динамически по config.json
        "actions": [("Предпросмотр", "👀", "act_sync_preview"), ("Синхронизировать", "🔄", "act_sync_apply")],
    },
    "candidates": {
        "emoji": "📈", "title": "Кандидаты на повышение",
        "desc": "Раз в неделю бот публикует список тех, кто подходит под критерии. Решение за руководством.",
        "params": [
            param("candidates.enabled", "Еженедельный список включён", "bool"),
            param("candidates.channel_id", "Канал для списка", "channel"),
            param("candidates.min_days", "Минимум дней в семье", "int", 0, 3650),
            param("candidates.min_voice_hours", "Минимум часов в войсе за 14 дней", "int", 0, 1000),
            param("candidates.max_warns", "Максимум активных выговоров", "int", 0, 50),
            param("candidates.max_from_rank", "Рассматривать ранги до (включительно)", "int", 1, 10),
            param("candidates.weekday", "День недели (0=пн … 6=вс)", "int", 0, 6),
            param("candidates.hour", "Час публикации (0–23)", "int", 0, 23),
        ],
        "actions": [("Показать сейчас", "👁️", "act_cand_show"), ("Опубликовать сейчас", "📨", "act_cand_post")],
    },
    "treasury": {
        "emoji": "💰", "title": "Казна",
        "desc": "Команды: /казна баланс · сдал · потратил · история · долги. "
                "Должникам бот тихо напоминает в личные сообщения.",
        "params": [
            param("treasury.channel_id", "Канал для недельного отчёта", "channel"),
            param("treasury.weekly_due", "Взнос в неделю с участника (0 — не учитывать)", "int", 0, 10 ** 9),
            param("treasury.report_enabled", "Недельный отчёт включён", "bool"),
            param("treasury.dm_debtors", "Напоминать должникам в ЛС", "bool"),
            param("treasury.weekday", "День недели отчёта (0=пн … 6=вс)", "int", 0, 6),
            param("treasury.hour", "Час отчёта (0–23)", "int", 0, 23),
        ],
        "actions": [("Отчёт сейчас", "📊", "act_treasury_now")],
    },
    "giveaway": {
        "emoji": "🎁", "title": "Розыгрыши",
        "desc": "Запуск: /розыгрыш создать (приз, длительность, победители, мин. ранг, мин. часы войса).",
        "params": [
            param("giveaway.channel_id", "Канал для розыгрышей по умолчанию", "channel"),
            param("giveaway.ping_role_id", "Роль для пинга при запуске", "role"),
        ],
        "actions": [("Активные розыгрыши", "📋", "act_gw_list")],
    },
    "general": {
        "emoji": "⚙️", "title": "Общие",
        "desc": "Часовой пояс влияет на время недельных отчётов и подсчёт войса по дням.",
        "params": [
            param("general.tz_offset", "Часовой пояс (UTC+N, Узбекистан = 5)", "int", -12, 14),
            param("general.ignore_bots", "Не логировать действия ботов", "bool"),
        ],
        "actions": [],
    },
}
ORDER = ["logs", "autorole", "vacation", "warn_guard", "ranks", "candidates", "treasury", "giveaway", "general"]


def _id(x: Any) -> int:
    return int(getattr(x, "id", x))


def params_for(section: str) -> List[P]:
    if section == "ranks":
        cfg = core.load_config()
        out = []
        for k in sorted((cfg.get("ranks", {}) or {}).keys(), key=lambda z: int(z)):
            name = cfg["ranks"][k].get("name", f"Ранг {k}")
            out.append(param(f"rank_roles.{k}", f"Ранг {k} — {name}: роли", "roles"))
        out.append(param("rank_sync.auto", "Авто-синхронизация при смене ролей", "bool"))
        return out
    return SECTIONS[section]["params"] or []


# ───────────────────────── ЭЛЕМЕНТЫ ИНТЕРФЕЙСА ─────────────────────────

class NavSelect(disnake.ui.StringSelect):
    def __init__(self, view: "PanelView"):
        opts = [disnake.SelectOption(label="Главная · обзор", value="home", emoji="🏠",
                                     default=view.section == "home")]
        for key in ORDER:
            s = SECTIONS[key]
            opts.append(disnake.SelectOption(label=s["title"], value=key, emoji=s["emoji"],
                                             default=view.section == key))
        super().__init__(placeholder="📂 Раздел панели…", options=opts, row=0)
        self.pv = view

    async def callback(self, inter: disnake.MessageInteraction):
        await self.pv.cog.show(inter, self.values[0], None)


class ParamSelect(disnake.ui.StringSelect):
    def __init__(self, view: "PanelView", params: List[P]):
        opts = []
        for p in params[:25]:
            cur = core.short_value(view.guild, p.kind, core.get(p.key))
            opts.append(disnake.SelectOption(
                label=core.clip(p.label, 100), value=p.key,
                description=core.clip(f"Сейчас: {cur}", 100), default=(p.key == view.selected)))
        super().__init__(placeholder="🔧 Что настроить…", options=opts, row=1)
        self.pv = view

    async def callback(self, inter: disnake.MessageInteraction):
        await self.pv.cog.show(inter, self.pv.section, self.values[0])


class ChannelEditor(disnake.ui.ChannelSelect):
    def __init__(self, view: "PanelView", key: str):
        super().__init__(placeholder="📺 Выберите текстовый канал…", row=2, min_values=1, max_values=1,
                         channel_types=[disnake.ChannelType.text, disnake.ChannelType.news])
        self.pv, self.key = view, key

    async def callback(self, inter: disnake.MessageInteraction):
        core.set_value(self.key, _id(self.values[0]))
        await self.pv.cog.show(inter, self.pv.section, self.key)


class RoleEditor(disnake.ui.RoleSelect):
    def __init__(self, view: "PanelView", key: str, multi: bool):
        super().__init__(placeholder="🎭 Выберите роль…" if not multi else "🎭 Выберите роли (можно несколько)…",
                         row=2, min_values=1, max_values=25 if multi else 1)
        self.pv, self.key, self.multi = view, key, multi

    async def callback(self, inter: disnake.MessageInteraction):
        ids = [_id(v) for v in self.values]
        core.set_value(self.key, ids if self.multi else ids[0])
        await self.pv.cog.show(inter, self.pv.section, self.key)


class ToggleButton(disnake.ui.Button):
    def __init__(self, view: "PanelView", key: str):
        on = bool(core.get(key))
        super().__init__(label="Выключить" if on else "Включить", emoji="🔴" if on else "🟢",
                         style=disnake.ButtonStyle.danger if on else disnake.ButtonStyle.success, row=2)
        self.pv, self.key = view, key

    async def callback(self, inter: disnake.MessageInteraction):
        core.set_value(self.key, not bool(core.get(self.key)))
        await self.pv.cog.show(inter, self.pv.section, self.key)


class EditValueModal(disnake.ui.Modal):
    def __init__(self, view: "PanelView", p: P):
        self.pv, self.p = view, p
        super().__init__(
            title=core.clip(p.label, 45), custom_id=f"cc:edit:{p.key}",
            components=[disnake.ui.TextInput(
                label="Новое значение", custom_id="v", value=str(core.get(p.key, "") or ""),
                max_length=200 if p.kind == "text" else 8,
                placeholder=f"Число от {p.lo} до {p.hi}" if p.kind == "int" else "Текст")])

    async def callback(self, inter: disnake.ModalInteraction):
        raw = (inter.text_values.get("v") or "").strip()
        if self.p.kind == "int":
            try:
                val = int(raw)
            except ValueError:
                return await inter.response.send_message("❌ Нужно целое число.", ephemeral=True)
            if not (self.p.lo <= val <= self.p.hi):
                return await inter.response.send_message(
                    f"❌ Допустимо от {self.p.lo} до {self.p.hi}.", ephemeral=True)
            core.set_value(self.p.key, val)
        else:
            core.set_value(self.p.key, raw)
        await self.pv.cog.show(inter, self.pv.section, self.p.key)


class EditButton(disnake.ui.Button):
    def __init__(self, view: "PanelView", p: P):
        super().__init__(label="Изменить значение", emoji="✏️", style=disnake.ButtonStyle.primary, row=2)
        self.pv, self.p = view, p

    async def callback(self, inter: disnake.MessageInteraction):
        await inter.response.send_modal(EditValueModal(self.pv, self.p))


class ResetButton(disnake.ui.Button):
    def __init__(self, view: "PanelView", key: str):
        super().__init__(label="Сбросить", emoji="🗑️", style=disnake.ButtonStyle.secondary, row=3)
        self.pv, self.key = view, key

    async def callback(self, inter: disnake.MessageInteraction):
        core.set_value(self.key, core.default_for(self.key))
        await self.pv.cog.show(inter, self.pv.section, self.key)


class ActionButton(disnake.ui.Button):
    def __init__(self, view: "PanelView", label: str, emoji: str, handler: str):
        super().__init__(label=label, emoji=emoji, style=disnake.ButtonStyle.secondary, row=3)
        self.pv, self.handler = view, handler

    async def callback(self, inter: disnake.MessageInteraction):
        await getattr(self.pv.cog, self.handler)(inter)


class RefreshButton(disnake.ui.Button):
    def __init__(self, view: "PanelView"):
        super().__init__(label="Обновить", emoji="🔃", style=disnake.ButtonStyle.secondary, row=3)
        self.pv = view

    async def callback(self, inter: disnake.MessageInteraction):
        await self.pv.cog.show(inter, self.pv.section, self.pv.selected)


class PanelView(disnake.ui.View):
    def __init__(self, cog: "ControlCenter", guild: disnake.Guild, user_id: int, section: str = "home",
                 selected: Optional[str] = None):
        super().__init__(timeout=900)
        self.cog, self.guild, self.user_id = cog, guild, user_id
        self.section, self.selected = section, selected

        self.add_item(NavSelect(self))
        if section == "home":
            self.add_item(RefreshButton(self))
            return
        params = params_for(section)
        if params:
            self.add_item(ParamSelect(self, params))
        p = next((x for x in params if x.key == selected), None)
        if p:
            if p.kind == "channel":
                self.add_item(ChannelEditor(self, p.key))
            elif p.kind == "role":
                self.add_item(RoleEditor(self, p.key, multi=False))
            elif p.kind == "roles":
                self.add_item(RoleEditor(self, p.key, multi=True))
            elif p.kind == "bool":
                self.add_item(ToggleButton(self, p.key))
            else:
                self.add_item(EditButton(self, p))
            if p.kind in ("channel", "role", "roles"):
                self.add_item(ResetButton(self, p.key))
        for label, emoji, handler in SECTIONS[section]["actions"]:
            self.add_item(ActionButton(self, label, emoji, handler))

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:
        if inter.author.id != self.user_id:
            await inter.response.send_message("Это чужая панель. Откройте свою: `/центр`.", ephemeral=True)
            return False
        return True


# ───────────────────────── COG ─────────────────────────

class ControlCenter(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ───── отрисовка ─────

    async def home_embed(self, guild: disnake.Guild) -> disnake.Embed:
        humans = [m for m in guild.members if not m.bot]
        in_family = [m for m in humans if core.member_rank(m) >= 1]
        vac_active = await core.q_one("SELECT COUNT(*) AS c FROM vacations WHERE guild_id=? AND status='active'", (guild.id,))
        vac_pending = await core.q_one("SELECT COUNT(*) AS c FROM vacations WHERE guild_id=? AND status='pending'", (guild.id,))
        gw = await core.q_one("SELECT COUNT(*) AS c FROM giveaways WHERE guild_id=? AND status='active'", (guild.id,))
        bal = await core.treasury_balance(guild.id)
        stats = await database.get_stats()
        afk = sum(1 for m in humans if core.pause_kind(m) == "afk")

        e = core.make_embed("⚜️ Центр управления", "Выберите раздел в меню ниже. Все изменения применяются сразу.")
        e.add_field("👥 Сервер", f"Людей: **{len(humans)}**\nВ семье: **{len(in_family)}**", inline=True)
        e.add_field("🌴 Отпуска", f"Идут: **{vac_active['c']}**\nЖдут решения: **{vac_pending['c']}**\nВ АФК: **{afk}**",
                    inline=True)
        e.add_field("📋 Заявки", f"На вступление: **{stats['pending_apps']}**\nНа повышение: **{stats['pending_promos']}**\n"
                                 f"Выговоров: **{stats['active_warns']}**", inline=True)
        e.add_field("💰 Казна", f"**{bal:,}**".replace(",", " "), inline=True)
        e.add_field("🎁 Розыгрыши", f"Активных: **{gw['c']}**", inline=True)
        e.add_field("🎙️ В войсе сейчас", f"**{sum(len(v.members) for v in guild.voice_channels)}**", inline=True)

        todo = []
        if not any(int(core.get(f"logs.{k}", 0) or 0) for k in core.DEFAULTS["logs"]):
            todo.append("📜 Не настроены каналы логов")
        if not int(core.get("vacation.role_id", 0) or 0):
            todo.append("🌴 Не выбрана роль отпуска")
        if not int(core.get("vacation.review_channel_id", 0) or 0):
            todo.append("🌴 Не выбран канал заявок на отпуск")
        if core.get("autorole.enabled") and not core.ids_list("autorole.roles"):
            todo.append("🚪 Не выбраны авто-роли при входе")
        if not any(core.rank_roles_map().values()):
            todo.append("🎖️ Не заданы роли рангов")
        e.add_field("🧭 Что ещё настроить",
                    "\n".join(f"• {t}" for t in todo) if todo else "✅ Всё основное настроено", inline=False)
        return e

    def section_embed(self, guild: disnake.Guild, section: str, selected: Optional[str]) -> disnake.Embed:
        s = SECTIONS[section]
        e = core.make_embed(f"{s['emoji']} {s['title']}", s["desc"])
        params = params_for(section)
        lines = []
        for p in params:
            mark = "▶️ " if p.key == selected else ""
            lines.append(f"{mark}**{p.label}**\n└ {core.fmt_value(guild, p.kind, core.get(p.key))}")
        chunk, size, n = [], 0, 1
        for ln in lines:
            if size + len(ln) > 950 and chunk:
                e.add_field("Текущие настройки" if n == 1 else "…продолжение", "\n".join(chunk), inline=False)
                chunk, size, n = [], 0, n + 1
            chunk.append(ln)
            size += len(ln) + 1
        if chunk:
            e.add_field("Текущие настройки" if n == 1 else "…продолжение", "\n".join(chunk), inline=False)
        e.set_footer(text="Выберите параметр в меню «Что настроить», затем канал/роль/значение")
        return e

    async def show(self, inter: disnake.Interaction, section: str, selected: Optional[str]):
        view = PanelView(self, inter.guild, inter.author.id, section, selected)
        embed = await self.home_embed(inter.guild) if section == "home" else self.section_embed(inter.guild, section, selected)
        await inter.response.edit_message(embed=embed, view=view)

    # ───── команда ─────

    @commands.slash_command(name="центр", description="⚜️ Центр управления: логи, роли, отпуска, казна, розыгрыши")
    @commands.guild_only()
    async def center(self, inter: disnake.ApplicationCommandInteraction):
        if not core.is_admin(inter.author):
            return await inter.response.send_message("❌ Панель доступна только администраторам.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        embed = await self.home_embed(inter.guild)
        await inter.edit_original_response(embed=embed, view=PanelView(self, inter.guild, inter.author.id))

    # ───── действия-кнопки ─────

    async def act_test_logs(self, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        done, bad = [], []
        for cat in core.DEFAULTS["logs"]:
            cid = int(core.get(f"logs.{cat}", 0) or 0)
            if not cid:
                continue
            ok = await core.send_log(inter.guild, cat, core.make_embed(
                "🧪 Тест логов", f"Тип **{cat}** настроен верно. Запросил {inter.author.mention}", kind="ok"))
            (done if ok else bad).append(cat)
        if not done and not bad:
            return await inter.followup.send("ℹ️ Ни один канал логов не задан.", ephemeral=True)
        text = f"✅ Отправлено: {', '.join(done) or '—'}"
        if bad:
            text += f"\n❌ Нет доступа к каналу: {', '.join(bad)}"
        await inter.followup.send(text, ephemeral=True)

    async def act_vac_panel(self, inter: disnake.MessageInteraction):
        cog = self.bot.get_cog("Vacation")
        await inter.response.defer(ephemeral=True)
        await inter.followup.send(await cog.publish_panel(inter.guild) if cog else "❌ Модуль отпусков не загружен.",
                                  ephemeral=True)

    async def act_vac_list(self, inter: disnake.MessageInteraction):
        rows = await core.q_all(
            "SELECT * FROM vacations WHERE guild_id=? AND status IN ('active','pending') ORDER BY status, end_ts",
            (inter.guild.id,))
        if not rows:
            return await inter.response.send_message("📭 Активных отпусков и заявок нет.", ephemeral=True)
        lines = []
        for r in rows[:25]:
            if r["status"] == "active":
                lines.append(f"🟢 <@{r['user_id']}> — до <t:{r['end_ts']}:d> (<t:{r['end_ts']}:R>)")
            else:
                lines.append(f"🟡 <@{r['user_id']}> — заявка на {r['days']} дн.")
        await inter.response.send_message(embed=core.make_embed("🌴 Отпуска", "\n".join(lines)), ephemeral=True)

    async def _sync(self, inter: disnake.MessageInteraction, apply: bool):
        cog = self.bot.get_cog("FamilySync")
        if cog is None:
            return await inter.response.send_message("❌ Модуль синхронизации не загружен.", ephemeral=True)
        if not any(core.rank_roles_map().values()):
            return await inter.response.send_message("⚠️ Сначала задайте роли рангов.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        res = await cog.sync_all(inter.guild, apply=apply)
        e = core.make_embed("🔄 Синхронизация рангов" if apply else "👀 Предпросмотр синхронизации",
                            kind="ok" if apply else "warn")
        e.add_field("Проверено (есть роль ранга)", str(res["checked"]), inline=True)
        e.add_field("Изменится/изменено", str(len(res["changed"])), inline=True)
        e.add_field("Новых карточек", str(res["added"]), inline=True)
        if res["changed"]:
            lines = [f"• {m.mention}: {a} → **{b}**" for m, a, b in res["changed"][:25]]
            more = f"\n…и ещё {len(res['changed']) - 25}" if len(res["changed"]) > 25 else ""
            e.add_field("Кто", core.clip("\n".join(lines) + more, 1000), inline=False)
        else:
            e.add_field("Итог", "Все карточки уже совпадают с ролями ✅", inline=False)
        if not apply and res["changed"]:
            e.set_footer(text="Нажмите «Синхронизировать», чтобы применить")
        await inter.followup.send(embed=e, ephemeral=True)

    async def act_sync_preview(self, inter: disnake.MessageInteraction):
        await self._sync(inter, apply=False)

    async def act_sync_apply(self, inter: disnake.MessageInteraction):
        await self._sync(inter, apply=True)

    async def act_cand_show(self, inter: disnake.MessageInteraction):
        cog = self.bot.get_cog("FamilySync")
        await inter.response.defer(ephemeral=True)
        items = await cog.build_candidates(inter.guild)
        await inter.followup.send(embed=cog.candidates_embed(items), ephemeral=True)

    async def act_cand_post(self, inter: disnake.MessageInteraction):
        cog = self.bot.get_cog("FamilySync")
        await inter.response.defer(ephemeral=True)
        await inter.followup.send(await cog.post_candidates(inter.guild), ephemeral=True)

    async def act_treasury_now(self, inter: disnake.MessageInteraction):
        cog = self.bot.get_cog("Treasury")
        await inter.response.defer(ephemeral=True)
        await inter.followup.send(await cog.post_report(inter.guild, dm=False), ephemeral=True)

    async def act_gw_list(self, inter: disnake.MessageInteraction):
        rows = await core.q_all("SELECT * FROM giveaways WHERE guild_id=? AND status='active' ORDER BY end_ts",
                                (inter.guild.id,))
        if not rows:
            return await inter.response.send_message("📭 Активных розыгрышей нет.", ephemeral=True)
        lines = [f"**#{r['id']}** · {core.clip(r['prize'], 60)} — итоги <t:{r['end_ts']}:R>" for r in rows[:20]]
        await inter.response.send_message(
            embed=core.make_embed("🎁 Активные розыгрыши", "\n".join(lines)), ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(ControlCenter(bot))
