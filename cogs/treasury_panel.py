"""Казна семьи: банк, отчёты о тратах, приёмка и логи.

Одна кнопка `/казна` → «🏗 Создать ветку» поднимает категорию и четыре канала разом:
🏦 банк семьи (баннер + объяснение + кнопки, баланс видят только свои), 🧾 отчёты (форма),
✅ приёмка (роль решает «принять/отклонить») и 📜 логи (куда падает принятое).

Остаток живёт в treasury.json и меняется только из принятого отчёта — поэтому «молча
потратить» нельзя: каждая сумма оставляет запись в книге казны.
"""
import json
import os
import time
from typing import List, Optional

import disnake
from disnake.ext import commands

from utils import brand, cdn_assets
from utils.checks import is_admin, load_config
from utils.embeds import card, clip, list_embed, note_embed, panel_embed

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, "treasury.json")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

BANK_BANNER = "kazna_bank.jpg"
REPORT_BANNER = "kazna_reports.jpg"

CATEGORY = "💰 КАЗНА СЕМЬИ"
CHANNELS = {
    "bank": "💰-банк-семьи",
    "reports": "🧾-отчёты-казны",
    "review": "✅-приёмка-отчётов",
    "logs": "📜-логи-казны",
}
MAX_MOVES = 400
MAX_REPORTS = 200
MAX_AMOUNT = 10 ** 12


# ─────────────────────────── состояние ───────────────────────────
def _load() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("balance", 0)
    data.setdefault("raised", 0)
    data.setdefault("spent", 0)
    data.setdefault("seq", 0)
    data.setdefault("roles", {"manage": [], "approve": []})
    data.setdefault("channels", {})
    data.setdefault("reports", {})
    data.setdefault("moves", [])
    return data


def _save(data: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def _roles(st: dict, key: str) -> List[int]:
    try:
        return [int(r) for r in ((st.get("roles") or {}).get(key) or [])]
    except (TypeError, ValueError):
        return []


def _allowed(inter, st: dict, key: str) -> bool:
    """Свои по роли; роль приёмки по умолчанию берётся из управления, чтобы хватило одной настройки."""
    if is_admin(inter):
        return True
    want = set(_roles(st, key))
    if key == "approve" and not want:
        want = set(_roles(st, "manage"))
    member = getattr(inter, "author", None)
    return bool(want) and any(r.id in want for r in getattr(member, "roles", []))


def _deny() -> disnake.Embed:
    return note_embed("⛔ Это канал казны: только для управления и приёмки.")


def _cog(inter) -> Optional["Treasury"]:
    """Ког берём из взаимодействия: у ephemeral-вью нет .bot, пока его никто не зарегистрировал."""
    bot = getattr(inter, "bot", None)
    return bot.get_cog("Treasury") if bot else None


# ─────────────────────────── числа ───────────────────────────
def _n(value) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "0"


def _amount(raw) -> Optional[int]:
    """'15 000', '15000', '15к' → сумма. Мусор или ноль → None."""
    text = str(raw or "").lower().strip()
    if not text:
        return None
    mult = 1000 if text.endswith(("к", "k")) else 1
    num = "".join(ch for ch in text if ch.isdigit())
    if not num:
        return None
    try:
        value = int(num) * mult
    except ValueError:
        return None
    return value if 0 < value <= MAX_AMOUNT else None


def _when(ts) -> str:
    try:
        return f"<t:{int(ts)}:R>"
    except (TypeError, ValueError):
        return ""


def _move(st: dict, kind: str, amount: int, who: int, note: str) -> None:
    st["moves"].append({"kind": kind, "amount": int(amount), "by": int(who),
                        "note": note[:200], "ts": int(time.time()), "bal": int(st["balance"])})
    if len(st["moves"]) > MAX_MOVES:
        del st["moves"][: len(st["moves"]) - MAX_MOVES]


# ─────────────────────────── баннеры и тексты ───────────────────────────
def _put_banner(embed: disnake.Embed, asset: str) -> Optional[disnake.File]:
    """Картинка под текстом — как в заявках: ссылка на CDN, а если её нет — вложение."""
    path = os.path.join(ASSETS_DIR, asset)
    if not os.path.isfile(path):
        return None
    url = cdn_assets.cached_url(asset, path)
    if url:
        embed.set_image(url=url)
        return None
    embed.set_image(url=f"attachment://{asset}")
    return disnake.File(path, filename=asset)


async def _has_bot_message(ch, guild) -> bool:
    async for m in ch.history(limit=30):
        if m.author.id == guild.me.id:
            return True
    return False


def _bank_text(bot) -> str:
    crown = brand.emoji(bot, "red_crown", "👑")
    house = brand.emoji(bot, "hz_house", "🏠")
    bits = brand.emoji(bot, "hz_bitsicon", "💰")
    book = brand.emoji(bot, "hz_book", "📖")
    gift = brand.emoji(bot, "hz_gifticon", "🎁")
    star = brand.emoji(bot, "hz_winterstar", "⭐")
    return (
        f"{crown} **Банк семьи** — здесь лежат общие деньги: сборы, награбленное с мероприятий,\n"
        "подарки от друзей семьи. Всё, что уходит, уходит только через отчёт и приёмку.\n\n"
        f"{bits} **💰 Баланс казны** — сколько лежит сейчас. Показывает только своим.\n"
        f"{book} **📜 Книга казны** — последние пополнения и траты: кто, сколько и за что.\n"
        f"{gift} **➕ Пополнить** — записать взнос в казну, он тоже остаётся в книге.\n\n"
        f"{house} Отчёт подаётся в канале ниже — деньги списываются только после приёмки.\n"
        f"{star} Казна семьи видна всем, кто состоит в семье."
    )


def _reports_text(bot) -> str:
    pencil = brand.emoji(bot, "hz_pencil", "✍️")
    cart = brand.emoji(bot, "hz_shoppingcart", "🛒")
    meg = brand.emoji(bot, "hz_megaphone", "📢")
    book = brand.emoji(bot, "hz_book", "📖")
    like = brand.emoji(bot, "hz_likeicon", "👍")
    return (
        f"{pencil} **Как сдавать отчёт**\n\n"
        "Нажми **«🧾 Подать отчёт»** и заполни форму:\n"
        f"{cart} **Сумма** — сколько ушло из казны;\n"
        f"{pencil} **На что** — статья расхода (мероприятие, форма, транспорт, откат после рейда);\n"
        f"{like} **Кому** — на кого потратили и на сколько человек;\n"
        f"{meg} **Детали** — всё, что стоит знать проверяющему.\n\n"
        f"{book} Отчёт уйдёт в **✅ приёмку**. Как его примут — сумма снимется с баланса, а в\n"
        "**📜 логах казны** появится запись. Отклонят — вернётся тебе с причиной."
    )


def _review_text() -> str:
    return (
        "✅ **Приёмка отчётов**\n\n"
        "Сюда падают отчёты о тратах. Роль, которой ты выдал приёмку, жмёт **«Принять»** или\n"
        "**«Отклонить»**.\n\n"
        "✔️ Принято → деньги снимаются с баланса, отчёт уходит в логи казны.\n"
        "✖️ Отклонено → баланс не трогается, автор получает причину.\n\n"
        "_Кнопки без нужной роли молчат — это не баг, это казна._"
    )


def _logs_text(bot) -> str:
    book = brand.emoji(bot, "hz_book", "📖")
    train = brand.emoji(bot, "hz_trainicon", "🚂")
    crown = brand.emoji(bot, "red_crown", "👑")
    return (
        f"{book} **Логи казны** — сюда падает всё принятое и все пополнения.\n"
        f"{train} Канал только для записей бота: обсуждение — в отчётах, вопросы — руководству семьи.\n"
        f"{crown} Тут история денег семьи: её не чистят и не правят руками."
    )


# ─────────────────────────── карточки ───────────────────────────
def _balance_embed(bot, guild, st: dict) -> disnake.Embed:
    crown = brand.emoji(bot, "red_crown", "👑")
    bits = brand.emoji(bot, "hz_bitsicon", "💰")
    gift = brand.emoji(bot, "hz_gifticon", "🎁")
    cart = brand.emoji(bot, "hz_shoppingcart", "🛒")
    book = brand.emoji(bot, "hz_book", "📖")
    botv2 = brand.emoji(bot, "hz_botv2", "🤖")
    emb = card(f"{crown} Баланс казны", guild=guild)
    emb.description = (
        f"{bits} **Остаток:** {_n(st['balance'])}\n\n"
        f"{gift} Пополнено: **{_n(st['raised'])}** · {cart} Потрачено: **{_n(st['spent'])}**\n"
        f"{book} Записей в книге: {len(st['moves'])} · {botv2} Отчётов подано: {st['seq']}"
    )
    return emb


def _book_embed(bot, guild, st: dict) -> disnake.Embed:
    gift = brand.emoji(bot, "hz_gifticon", "➕")
    cart = brand.emoji(bot, "hz_shoppingcart", "🧾")
    book = brand.emoji(bot, "hz_book", "⚖️")
    rows = []
    for m in list(reversed(st["moves"]))[:12]:
        icon = gift if m["kind"] == "topup" else (cart if m["kind"] == "spend" else book)
        sign = "+" if m["kind"] == "topup" else ("−" if m["kind"] == "spend" else "=")
        rows.append([f"{icon} {sign}{_n(m['amount'])}",
                     f"{clip(m['note'], 90)}\n<@{m['by']}> · {_when(m['ts'])} · остаток {_n(m['bal'])}"])
    return list_embed(f"{book} Книга казны", rows, guild=guild, inline=False,
                      empty="Книга чистая — трат ещё не было.",
                      note=f"Остаток: **{_n(st['balance'])}** · последние 12 записей")


def _report_embed(bot, guild, rep: dict) -> disnake.Embed:
    status = rep.get("status") or "wait"
    icon = {"wait": "⏳", "ok": "✅", "no": "❌"}.get(status, "⏳")
    cart = brand.emoji(bot, "hz_shoppingcart", "🧾")
    bits = brand.emoji(bot, "hz_bitsicon", "💰")
    pencil = brand.emoji(bot, "hz_pencil", "✍️")
    like = brand.emoji(bot, "hz_likeicon", "👤")
    meg = brand.emoji(bot, "hz_megaphone", "📢")
    warn = brand.emoji(bot, "hz_warn", "📝")
    emb = card(f"{cart} Отчёт #{rep['id']} · {icon}", guild=guild,
               color_key={"ok": "success_color", "no": "error_color"}.get(status, "embed_color"))
    emb.description = (
        f"{bits} **Сумма:** {_n(rep['amount'])}\n"
        f"{pencil} **На что:** {clip(rep['purpose'], 200)}\n"
        f"{like} **Кому:** {clip(rep['people'], 200)}"
    )
    if rep.get("detail"):
        emb.add_field(name=f"{meg} Детали", value=clip(rep["detail"], 900), inline=False)
    foot = f"Подал <@{rep['by']}> · {_when(rep['ts'])}"
    if status == "ok":
        foot += f"\n✅ Принял <@{rep.get('decided_by', 0)}> · {_when(rep.get('decided_ts', 0))}"
    elif status == "no":
        foot += (f"\n❌ Отклонил <@{rep.get('decided_by', 0)}> · {_when(rep.get('decided_ts', 0))}"
                 f"\n{warn} {clip(rep.get('reason', ''), 200)}")
    emb.add_field(name="Кто и когда", value=foot[:1024], inline=False)
    return emb


def _mine_embed(bot, st: dict, uid: int) -> disnake.Embed:
    pencil = brand.emoji(bot, "hz_pencil", "🧾")
    rows = []
    for rep in sorted(st["reports"].values(), key=lambda r: -int(r["id"]))[:12]:
        if int(rep["by"]) != int(uid):
            continue
        icon = {"wait": "⏳", "ok": "✅", "no": "❌"}.get(rep.get("status") or "wait", "⏳")
        rows.append([f"{icon} #{rep['id']} · {_n(rep['amount'])}",
                     f"{clip(rep['purpose'], 60)}\n{_when(rep['ts'])}"])
    return list_embed(f"{pencil} Мои отчёты", rows, guild=None, inline=False,
                      empty="Ты ещё не подавал отчётов — форма в этом же канале.",
                      note="⏳ ждёт приёмку · ✅ принято · ❌ отклонён")


# ─────────────────────────── модалки ───────────────────────────
class ReportModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(
            title="Отчёт о трате из казны",
            custom_id="hzkazm:report",
            components=[
                disnake.ui.TextInput(label="Сумма", custom_id="amount",
                                     placeholder="15 000", max_length=16),
                disnake.ui.TextInput(label="На что ушли", custom_id="purpose",
                                     placeholder="Форма на сбор семьи", max_length=120),
                disnake.ui.TextInput(label="Кому и на сколько человек", custom_id="people",
                                     placeholder="Всему отряду — 12 человек", max_length=160),
                disnake.ui.TextInput(label="Детали", custom_id="detail", required=False,
                                     style=disnake.TextInputStyle.paragraph,
                                     placeholder="Где брали, кто платил, почему столько",
                                     max_length=800),
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        cog: Optional[Treasury] = inter.bot.get_cog("Treasury")
        if cog is None:
            return await inter.response.send_message(embed=note_embed("⛔ Казна уснула."),
                                                      ephemeral=inter.guild is not None)
        await cog.submit(inter, str(inter.text_values["amount"]),
                         str(inter.text_values["purpose"]), str(inter.text_values["people"]),
                         str(inter.text_values.get("detail") or ""))


class TopUpModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(
            title="Пополнение казны",
            custom_id="hzkazm:topup",
            components=[
                disnake.ui.TextInput(label="Сумма", custom_id="amount",
                                     placeholder="50 000", max_length=16),
                disnake.ui.TextInput(label="Откуда", custom_id="note",
                                     placeholder="Взнос семьи за неделю", max_length=200),
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        cog: Optional[Treasury] = inter.bot.get_cog("Treasury")
        if cog is None:
            return
        await cog.top_up(inter, str(inter.text_values["amount"]),
                         str(inter.text_values["note"]))


class SetBalanceModal(disnake.ui.Modal):
    def __init__(self, current: int):
        self.current = current
        super().__init__(
            title="Задать остаток в казне",
            custom_id="hzkazm:setbal",
            components=[
                disnake.ui.TextInput(label="Новый остаток", custom_id="amount",
                                     placeholder=_n(current), max_length=16),
                disnake.ui.TextInput(label="Почему меняем", custom_id="note",
                                     placeholder="Перенесли остаток из старого учёта",
                                     max_length=200),
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        cog: Optional[Treasury] = inter.bot.get_cog("Treasury")
        if cog is None:
            return
        await cog.set_balance(inter, str(inter.text_values["amount"]),
                              str(inter.text_values["note"]))


class RejectModal(disnake.ui.Modal):
    def __init__(self, rid: str):
        self.rid = rid
        super().__init__(
            title="Отклонить отчёт",
            custom_id=f"hzkazm:reject:{rid}",
            components=[
                disnake.ui.TextInput(label="Причина отказа", custom_id="reason",
                                     style=disnake.TextInputStyle.paragraph,
                                     placeholder="Нет чека / сумма не сходится с деталями",
                                     max_length=300),
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        cog: Optional[Treasury] = inter.bot.get_cog("Treasury")
        if cog is None:
            return
        await cog.decide(inter, self.rid, False, str(inter.text_values["reason"]))


# ─────────────────────────── панели на кнопках ───────────────────────────
class BankView(disnake.ui.View):
    """Постоянная: висит под баннером банка и после рестарта работает по custom_id."""

    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="💰 Баланс казны", style=disnake.ButtonStyle.success,
                       custom_id="hzkaz:balance", row=0)
    async def balance(self, button, inter: disnake.MessageInteraction):
        pass

    @disnake.ui.button(label="📜 Книга казны", style=disnake.ButtonStyle.secondary,
                       custom_id="hzkaz:book", row=0)
    async def book(self, button, inter: disnake.MessageInteraction):
        pass

    @disnake.ui.button(label="➕ Пополнить", style=disnake.ButtonStyle.primary,
                       custom_id="hzkaz:topup", row=0)
    async def topup(self, button, inter: disnake.MessageInteraction):
        pass


class ReportsView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="🧾 Подать отчёт", emoji="✍️", style=disnake.ButtonStyle.primary,
                       custom_id="hzkaz:new", row=0)
    async def new(self, button, inter: disnake.MessageInteraction):
        pass

    @disnake.ui.button(label="📋 Мои отчёты", style=disnake.ButtonStyle.secondary,
                       custom_id="hzkaz:mine", row=0)
    async def mine(self, button, inter: disnake.MessageInteraction):
        pass


class ReviewView(disnake.ui.View):
    def __init__(self, rid: str):
        super().__init__(timeout=None)
        self.add_item(disnake.ui.Button(label="Принять", emoji="✅",
                                        style=disnake.ButtonStyle.success,
                                        custom_id=f"hzkaz:ok:{rid}", row=0))
        self.add_item(disnake.ui.Button(label="Отклонить", emoji="❌",
                                        style=disnake.ButtonStyle.danger,
                                        custom_id=f"hzkaz:no:{rid}", row=0))


class PanelView(disnake.ui.View):
    """Экран `/казна`: собрать ветку, перезалить баннеры, выбрать роли, задать остаток.

    custom_id с префиксом hzkazp: — кнопки живого экрана, их ведёт этот вью. Кнопки каналов
    (hzkaz:) ловит on_button_click, потому что они висят в чате после перезапуска бота.
    """

    def __init__(self, st: dict):
        super().__init__(timeout=900)
        self.st = st
        for key, placeholder in (("manage", "👑 Роль управления казной"),
                                 ("approve", "✅ Роль приёмки отчётов")):
            select = disnake.ui.RoleSelect(
                custom_id=f"hzkazp:role:{key}", placeholder=placeholder,
                max_values=3, row=2 if key == "manage" else 3,
                default_values=[disnake.Object(id=r) for r in _roles(st, key)])
            select.callback = self._role_handler(key, select)
            self.add_item(select)

    def _role_handler(self, key: str, select):
        """disnake зовёт item.callback(interaction) — поэтому select держим в замыкании."""
        async def handler(inter: disnake.MessageInteraction):
            cog = _cog(inter)
            if cog:
                await cog.save_roles(inter, key, select.values)
        return handler

    @disnake.ui.button(label="🏗 Создать ветку казны", style=disnake.ButtonStyle.primary,
                       custom_id="hzkazp:setup", row=0)
    async def setup(self, button, inter: disnake.MessageInteraction):
        cog = _cog(inter)
        await inter.response.defer(ephemeral=True, with_message=True)
        if cog:
            await cog.build_branch(inter)

    @disnake.ui.button(label="🖼 Перезалить баннеры", style=disnake.ButtonStyle.secondary,
                       custom_id="hzkazp:banners", row=0)
    async def banners(self, button, inter: disnake.MessageInteraction):
        cog = _cog(inter)
        await inter.response.defer(ephemeral=True, with_message=True)
        if cog:
            await cog.post_banners(inter)

    @disnake.ui.button(label="⚖️ Задать остаток", emoji="💰", style=disnake.ButtonStyle.success,
                       custom_id="hzkazp:set", row=1)
    async def set_balance(self, button, inter: disnake.MessageInteraction):
        await inter.response.send_modal(SetBalanceModal(int(self.st.get("balance") or 0)))


# ─────────────────────────── ког ───────────────────────────
class Treasury(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        if not getattr(self.bot, "_treasury_view_added", False):
            self.bot.add_view(BankView())
            self.bot.add_view(ReportsView())
            self.bot._treasury_view_added = True

    # ── команда ──
    @commands.slash_command(name="казна", description="Казна семьи: банк, отчёты, приёмка",
                            default_member_permissions=disnake.Permissions(manage_guild=True),
                            contexts=disnake.InteractionContextTypes(guild=True))
    async def kazna_panel(self, inter: disnake.ApplicationCommandInteraction):
        if not is_admin(inter):
            return await inter.response.send_message(embed=_deny(), ephemeral=True)
        st = _load()
        sections = []
        for key, title in (("bank", "🏦 Банк семьи"), ("reports", "🧾 Отчёты"),
                           ("review", "✅ Приёмка"), ("logs", "📜 Логи казны")):
            cid = (st["channels"] or {}).get(key)
            ch = inter.guild.get_channel(int(cid)) if cid else None
            sections.append((title, ch.mention if ch else "*не создан*"))
        manage = " ".join(f"<@&{r}>" for r in _roles(st, "manage")) or "_не выбрана — только админы_"
        approve = " ".join(f"<@&{r}>" for r in _roles(st, "approve")) or "_не выбрана — берёт управление_"
        bits = brand.emoji(self.bot, "hz_bitsicon", "💰")
        crown = brand.emoji(self.bot, "hz_moderatorpurple", "👑")
        shield = brand.emoji(self.bot, "hz_moderator", "✅")
        sections.append((f"{bits} Остаток", _n(st["balance"])))
        sections.append((f"{crown} Управление казной", manage))
        sections.append((f"{shield} Приёмка отчётов", approve))
        emb = panel_embed(f"{brand.emoji(self.bot, 'red_crown', '👑')} Казна семьи",
                          sections, guild=inter.guild,
                          intro="Кнопки ниже: собрать ветку каналов, выбрать роли и задать остаток.")
        await inter.response.send_message(embed=emb, view=PanelView(st), ephemeral=True,
                                          file=_put_banner(emb, BANK_BANNER))

    # ── создание ветки ──
    async def build_branch(self, inter) -> None:
        guild = inter.guild
        if guild is None:
            return await inter.followup.send(embed=note_embed("⛔ Только на сервере."), ephemeral=True)
        st = _load()
        me = guild.me
        public = {guild.default_role: disnake.PermissionOverwrite(view_channel=True,
                                                                  send_messages=False,
                                                                  add_reactions=False),
                  me: disnake.PermissionOverwrite(view_channel=True, send_messages=True,
                                                  embed_links=True, attach_files=True,
                                                  manage_channels=True)}
        private = {guild.default_role: disnake.PermissionOverwrite(view_channel=False),
                   me: disnake.PermissionOverwrite(view_channel=True, send_messages=True,
                                                   embed_links=True, attach_files=True,
                                                   manage_channels=True)}
        made: List[str] = []
        cat_id = int((st["channels"] or {}).get("category") or 0)
        cat = guild.get_channel(cat_id) if cat_id else None
        if cat is None:
            cat = await guild.create_category(CATEGORY, overwrites=public,
                                              reason="Казна семьи")
            st["channels"]["category"] = cat.id
            made.append(f"📁 {cat.mention}")
        for key, name in CHANNELS.items():
            cid = int((st["channels"] or {}).get(key) or 0)
            ch = guild.get_channel(cid) if cid else None
            if ch is None:
                ch = await guild.create_text_channel(name, category=cat,
                                                     overwrites=private if key in ("review", "logs")
                                                     else public,
                                                     topic=_topic(key),
                                                     reason="Казна семьи")
                st["channels"][key] = ch.id
                made.append(ch.mention)
            elif ch.category != cat:
                try:
                    await ch.edit(category=cat)
                except disnake.DiscordException:
                    pass
        _save(st)
        await self._sync_perms(guild, st)
        await self._post_banners(guild, st)
        note = ("Ветка собрана: " + ", ".join(made)) if made else "Ветка уже стояла целиком."
        await inter.followup.send(embed=note_embed(
            f"✅ {note}\n\nБаннеры и объяснения разложены, кнопки работают.\n"
            "Осталось выбрать роли управления и приёмки в этом же сообщении — "
            "и задать остаток казны.", "success", title="👑 Казна семьи"), ephemeral=True)

    async def _sync_perms(self, guild, st: dict) -> None:
        """Приёмка и логи — приватные: пускаем выбранные роли и админов семьи."""
        cfg = load_config()
        allow = set(_roles(st, "approve")) | set(_roles(st, "manage")) | set(cfg.get("admin_role_ids") or [])
        for key in ("review", "logs"):
            cid = int((st["channels"] or {}).get(key) or 0)
            ch = guild.get_channel(cid) if cid else None
            if ch is None:
                continue
            ow = {guild.default_role: disnake.PermissionOverwrite(view_channel=False),
                  guild.me: disnake.PermissionOverwrite(view_channel=True, send_messages=True,
                                                        embed_links=True, attach_files=True)}
            for rid in allow:
                role = guild.get_role(int(rid))
                if role:
                    ow[role] = disnake.PermissionOverwrite(view_channel=True, send_messages=False)
            try:
                await ch.edit(overwrites=ow, reason="Казна семьи: роли приёмки")
            except disnake.DiscordException:
                pass

    async def post_banners(self, inter) -> None:
        if inter.guild is None:
            return await inter.followup.send(embed=note_embed("⛔ Только на сервере."), ephemeral=True)
        st = _load()
        done = await self._post_banners(inter.guild, st)
        await inter.followup.send(
            embed=note_embed("✅ Баннеры перезалиты." if done else
                             "⛔ Сначала нажми «🏗 Создать ветку казны»."),
            ephemeral=True)

    async def _post_banners(self, guild, st: dict) -> bool:
        ch = {k: guild.get_channel(int((st["channels"] or {}).get(k) or 0))
              for k in CHANNELS}
        if not ch["bank"] or not ch["reports"]:
            return False
        # Чищаем только витрины: карточки отчётов в приёмке — это журнал, его не трогаем.
        for key in ("bank", "reports"):
            async for m in ch[key].history(limit=50):
                if m.author.id == guild.me.id:
                    try:
                        await m.delete()
                    except disnake.DiscordException:
                        pass
        bank_emb = card(f"{brand.emoji(self.bot, 'red_crown', '👑')} Банк семьи",
                        _bank_text(self.bot), guild=guild)
        # Картинка не отдельным сообщением, а под текстом — как в заявках.
        put = _put_banner(bank_emb, BANK_BANNER)
        await ch["bank"].send(embed=bank_emb, view=BankView(), file=put)
        rep_emb = card(f"{brand.emoji(self.bot, 'hz_pencil', '🧾')} Отчёты казны",
                       _reports_text(self.bot), guild=guild)
        rput = _put_banner(rep_emb, REPORT_BANNER)
        await ch["reports"].send(embed=rep_emb, view=ReportsView(), file=rput)
        for key, title, text in (("review", "✅ Приёмка отчётов", _review_text()),
                                 ("logs", "📜 Логи казны", _logs_text(self.bot))):
            c = ch[key]
            if c is None or await _has_bot_message(c, guild):
                continue
            await c.send(embed=card(title, text, guild=guild))
        return True

    # ── роли ──
    async def save_roles(self, inter, key: str, values) -> None:
        if not is_admin(inter):
            return await inter.response.send_message(embed=_deny(),
                                                     ephemeral=inter.guild is not None)
        st = _load()
        st["roles"][key] = [int(r.id) for r in list(values)[:3]]
        _save(st)
        if inter.guild:
            await self._sync_perms(inter.guild, st)
        label = "управления казной" if key == "manage" else "приёмки отчётов"
        names = " ".join(f"<@&{r}>" for r in st["roles"][key]) or "_сброшена — теперь только админы_"
        await inter.response.send_message(embed=note_embed(f"✅ Роль {label}: {names}", "success"),
                                          ephemeral=True)

    # ── баланс ──
    async def set_balance(self, inter, raw: str, note: str) -> None:
        if not is_admin(inter):
            return await inter.response.send_message(embed=_deny(), ephemeral=True)
        value = _amount(raw)
        if value is None:
            return await inter.response.send_message(
                embed=note_embed("❌ Не понял сумму. Напиши цифрами: `15000` или `15к`."),
                ephemeral=True)
        st = _load()
        old = int(st["balance"])
        st["balance"] = value
        _move(st, "set", value - old, inter.author.id, note or "ручная корректировка остатка")
        _save(st)
        await inter.response.send_message(
            embed=note_embed(f"⚖️ Остаток казны: **{_n(old)} → {_n(value)}**.\n"
                             f"📝 {note or 'без пояснения'}", "success"), ephemeral=True)
        await self._log(inter.guild, f"⚖️ Остаток казны изменили: {_n(old)} → {_n(value)}",
                        inter.author.id, note or "ручная корректировка")

    async def top_up(self, inter, raw: str, note: str) -> None:
        st = _load()
        if not _allowed(inter, st, "manage"):
            return await inter.response.send_message(embed=_deny(), ephemeral=True)
        value = _amount(raw)
        if value is None:
            return await inter.response.send_message(
                embed=note_embed("❌ Не понял сумму. Напиши `50000` или `50к`."), ephemeral=True)
        st["balance"] = int(st["balance"]) + value
        st["raised"] = int(st["raised"]) + value
        _move(st, "topup", value, inter.author.id, note or "пополнение казны")
        _save(st)
        await inter.response.send_message(
            embed=note_embed(f"➕ Пополнили казну на **{_n(value)}**.\n"
                             f"Остаток: **{_n(st['balance'])}** · {_when(int(time.time()))}\n"
                             f"📝 {note or 'взнос в казну'}", "success"), ephemeral=True)
        await self._log(inter.guild, f"➕ Пополнение казны: **{_n(value)}**",
                        inter.author.id, note or "взнос в казну")

    # ── отчёты ──
    async def submit(self, inter, raw_amount: str, purpose: str, people: str, detail: str) -> None:
        st = _load()
        if not _allowed(inter, st, "manage"):
            return await inter.response.send_message(
                embed=note_embed("⛔ Отчёт подаёт управление казной или руководство семьи."),
                ephemeral=True)
        value = _amount(raw_amount)
        if value is None:
            return await inter.response.send_message(
                embed=note_embed("❌ Не понял сумму. Напиши `15000` или `15к`."), ephemeral=True)
        review = inter.guild.get_channel(int((st["channels"] or {}).get("review") or 0)) \
            if inter.guild else None
        if review is None:
            return await inter.response.send_message(
                embed=note_embed("⛔ Канала приёмки нет — нажми «🏗 Создать ветку казны» в `/казна`."),
                ephemeral=True)
        st["seq"] = int(st.get("seq") or 0) + 1
        rid = str(st["seq"])
        rep = {"id": rid, "by": int(inter.author.id), "amount": value,
               "purpose": (purpose or "без статьи")[:200],
               "people": (people or "не указано")[:200], "detail": detail[:900],
               "ts": int(time.time()), "status": "wait"}
        st["reports"][rid] = rep
        if len(st["reports"]) > MAX_REPORTS:
            # Выкидываем только закрытые отчёты: живой отчёт без карточки остался бы без приёмки.
            closed = sorted((r for r in st["reports"].values()
                             if r.get("status") != "wait"), key=lambda r: int(r["id"]))
            for old in closed[: len(st["reports"]) - MAX_REPORTS]:
                st["reports"].pop(str(old["id"]), None)
        _save(st)
        await review.send(embed=_report_embed(self.bot, inter.guild, rep), view=ReviewView(rid))
        await inter.response.send_message(
            embed=note_embed(f"🧾 Отчёт **#{rid}** отправлен в {review.mention}.\n"
                             f"Сумма **{_n(value)}** спишется, когда его примут.", "success"),
            ephemeral=True)

    async def decide(self, inter, rid: str, accept: bool, reason: str = "") -> None:
        st = _load()
        rep = (st["reports"] or {}).get(rid)
        if not rep:
            return await inter.response.send_message(
                embed=note_embed("⛔ Такой отчёт уже удалён."), ephemeral=True)
        if (rep.get("status") or "wait") != "wait":
            return await inter.response.send_message(
                embed=note_embed(f"ℹ️ Отчёт #{rid} уже закрыт."), ephemeral=True)
        if not _allowed(inter, st, "approve"):
            return await inter.response.send_message(embed=_deny(), ephemeral=True)
        rep["status"] = "ok" if accept else "no"
        rep["decided_by"] = int(inter.author.id)
        rep["decided_ts"] = int(time.time())
        if not accept:
            rep["reason"] = (reason or "без причины")[:300]
        if accept:
            amount = int(rep["amount"])
            st["balance"] = int(st["balance"]) - amount
            st["spent"] = int(st["spent"]) + amount
            _move(st, "spend", amount, int(rep["by"]),
                  f"#{rid} · {rep['purpose']} · {rep['people']}")
        _save(st)
        if accept:
            await self._log(inter.guild, f"🧾 Принят отчёт **#{rid}** — списано **{_n(rep['amount'])}**",
                            int(rep["by"]), f"{rep['purpose']} · {rep['people']}",
                            balance=int(st["balance"]))
        await inter.response.edit_message(embed=_report_embed(self.bot, inter.guild, rep), view=None)
        await self._tell(inter, rep)

    async def _tell(self, inter, rep: dict) -> None:
        author = inter.guild.get_member(int(rep["by"])) if inter.guild else None
        if author is None:
            return
        ok = rep["status"] == "ok"
        text = (f"✅ Отчёт #{rep['id']} принят: **{_n(rep['amount'])}** ушло из казны.\n"
                f"Остаток: {_n(_load()['balance'])}" if ok else
                f"❌ Отчёт #{rep['id']} отклонён.\n📝 {rep.get('reason', '')}")
        try:
            await author.send(embed=note_embed(text, "success" if ok else "error",
                                               title="Казна семьи"))
        except disnake.DiscordException:
            pass

    async def _log(self, guild, title: str, who: int, note: str, balance: Optional[int] = None) -> None:
        st = _load()
        cid = int((st["channels"] or {}).get("logs") or 0)
        ch = guild.get_channel(cid) if (guild and cid) else None
        if ch is None:
            return
        emb = card(title, guild=guild)
        emb.description = (f"{note}\n"
                           f"**Кто:** <@{who}> · {_when(int(time.time()))}\n"
                           + (f"**Остаток казны:** {_n(balance)}" if balance is not None else ""))
        try:
            await ch.send(embed=emb)
        except disnake.DiscordException:
            pass

    # ── роутер кнопок ──
    @commands.Cog.listener()
    async def on_button_click(self, inter: disnake.MessageInteraction):
        cid = str(inter.data.custom_id or "")
        if not cid.startswith("hzkaz:"):
            return
        st = _load()
        action = cid.split(":", 2)[1]

        if action == "balance":
            if not _allowed(inter, st, "manage"):
                return await inter.response.send_message(embed=_deny(), ephemeral=True)
            return await inter.response.send_message(embed=_balance_embed(self.bot, inter.guild, st),
                                                     ephemeral=True)
        if action == "book":
            if not _allowed(inter, st, "manage"):
                return await inter.response.send_message(embed=_deny(), ephemeral=True)
            return await inter.response.send_message(embed=_book_embed(self.bot, inter.guild, st),
                                                     ephemeral=True)
        if action == "topup":
            if not _allowed(inter, st, "manage"):
                return await inter.response.send_message(embed=_deny(), ephemeral=True)
            return await inter.response.send_modal(TopUpModal())
        if action == "new":
            if not _allowed(inter, st, "manage"):
                return await inter.response.send_message(
                    embed=note_embed("⛔ Отчёт подаёт управление казной или руководство семьи."),
                    ephemeral=True)
            return await inter.response.send_modal(ReportModal())
        if action == "mine":
            return await inter.response.send_message(
                embed=_mine_embed(self.bot, st, inter.author.id), ephemeral=True)
        if action in ("ok", "no"):
            rid = cid.split(":", 2)[2]
            if action == "ok":
                return await self.decide(inter, rid, True)
            return await inter.response.send_modal(RejectModal(rid))


def _topic(key: str) -> str:
    return {
        "bank": "👑 Банк семьи — остаток казны и книга трат",
        "reports": "🧾 Отчёты: сколько потратили, на что и кому",
        "review": "✅ Приёмка отчётов казны",
        "logs": "📜 Логи казны: принятые траты и пополнения",
    }.get(key, "Казна семьи")


def setup(bot: commands.Bot):
    bot.add_cog(Treasury(bot))
