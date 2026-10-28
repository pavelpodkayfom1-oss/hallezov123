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
# Скринов к отчёту максимум три, и только картинками: приёмка читает их прямо с карточки.
MAX_PHOTOS = 3
MAX_PHOTO_MB = 8


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


def _deny(bot=None, guild=None) -> disnake.Embed:
    warn = _e(bot, "hz_warn", "⚠️")
    crown = _e(bot, "red_crown", "👑")
    return note_embed(
        f"{warn}  Это канал казны: остаток и приёмка видны только своим.\n\n"
        f"{crown}  Роль выдаёт руководство семьи — экран `/казна`.",
        "error", guild=guild, title=f"{warn}  НЕТ ДОСТУПА")


def _warn(bot=None, guild=None, head: str = "ТАК НЕ ВЫЙДЕТ", text: str = "") -> disnake.Embed:
    warn = _e(bot, "hz_warn", "⚠️")
    return note_embed(text, "error", title=f"{warn}  {head}", guild=guild)


def _need_manage(bot=None, guild=None) -> disnake.Embed:
    pencil = _e(bot, "hz_pencil", "✍️")
    bits = _e(bot, "hz_bitsicon", "💎")
    return note_embed(
        f"{pencil}  Отчёт подаёт **управление казной**\n"
        f"{bits}  Роль выдаёт руководство семьи — экран `/казна`.",
        "error", guild=guild, title=f"{pencil}  НЕ ТЕ ПРАВА")


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


def _e(bot, name: str, fallback: str) -> str:
    return brand.emoji(bot, name, fallback)


def _bank_text(bot) -> str:
    crown = _e(bot, "red_crown", "👑")
    house = _e(bot, "hz_house", "🏠")
    bits = _e(bot, "hz_bitsicon", "💎")
    book = _e(bot, "hz_book", "📖")
    gift = _e(bot, "hz_gifticon", "🎁")
    pc = _e(bot, "hz_pcicon", "🖥")
    n = [_e(bot, f"number{i}", f"{i}⃣") for i in range(1, 4)]
    return (
        f"*{crown}  Казна не пустеет молча  {crown}*\n\n"
        f"### {bits}  ЧТО ЗДЕСЬ\n"
        f"{n[0]}  Общие деньги семьи: сборы, награбленное с мероприятий, подарки от друзей\n"
        f"{n[1]}  Каждая трата проходит через **отчёт** и **приёмку**\n"
        f"{n[2]}  Остаток видят только **свои** — управление казной и приёмка\n\n"
        f"### {pc}  КНОПКИ\n"
        f"{bits}  **💰 Баланс казны** — сколько лежит сейчас\n"
        f"{book}  **📜 Книга казны** — кто, сколько и за что\n"
        f"{gift}  **➕ Пополнить** — записать взнос, он тоже остаётся в книге\n\n"
        f"> {house}  Отчёт подаётся в канале ниже. Деньги списываются **только после приёмки**."
    )


def _reports_text(bot) -> str:
    pencil = _e(bot, "hz_pencil", "✍️")
    cart = _e(bot, "hz_shoppingcart", "🛒")
    meg = _e(bot, "hz_megaphone", "📢")
    book = _e(bot, "hz_book", "📖")
    warn = _e(bot, "hz_warn", "⚠️")
    crown = _e(bot, "red_crown", "👑")
    n = [_e(bot, f"number{i}", f"{i}⃣") for i in range(1, 6)]
    return (
        f"*{pencil}  Потратил — отчитался  {pencil}*\n\n"
        f"### {cart}  КАК СДАТЬ ОТЧЁТ\n"
        f"{n[0]}  Нажми **«🧾 Подать отчёт»** и заполни форму\n"
        f"{n[1]}  **Сумма** — сколько ушло из казны\n"
        f"{n[2]}  **На что** — мероприятие, форма, транспорт, откат после рейда\n"
        f"{n[3]}  **Кому** — на кого потратили и на сколько человек\n"
        f"{n[4]}  **Детали** — всё, что стоит знать проверяющему\n\n"
        f"### 📎  СКРИНЫ\n"
        f"Со скринами отчёт подаётся командой **`/отчёт`** — там поле «Скриншот», "
        f"куда можно перетащить чек или скрин перевода (до {MAX_PHOTOS} штук).\n"
        f"Кнопка **«🧾 Подать отчёт»** — это быстрая форма без файлов: после неё можно "
        f"прикрепить скрины ответом в этом канале в течение 2 минут.\n\n"
        f"### {meg}  ЧТО ДАЛЬШЕ\n"
        f"✅  Приняли → сумма снялась с баланса, запись ушла в **📜 логи казны**\n"
        f"❌  Отклонили → баланс **не тронут**, тебе вернётся причина\n\n"
        f"> {warn}  Пиши как есть: {book} казна сходится до последней монеты, и это\n"
        f"> спокойнее, чем объясняться потом. {crown}"
    )


def _review_text(bot) -> str:
    mod = _e(bot, "hz_moderator", "🛡")
    purp = _e(bot, "hz_moderatorpurple", "👥")
    book = _e(bot, "hz_book", "📖")
    warn = _e(bot, "hz_warn", "⚠️")
    crown = _e(bot, "red_crown", "👑")
    bits = _e(bot, "hz_bitsicon", "💎")
    return (
        f"*{crown}  Приёмка отчётов  {crown}*\n\n"
        f"### {mod}  КТО РЕШАЕТ\n"
        f"{purp}  Роль, которой выдана приёмка, жмёт **«Принять»** или **«Отклонить»**\n"
        f"{book}  Без нужной роли кнопки молчат — это не баг, это казна\n\n"
        f"### {bits}  ИСХОД ОТЧЁТА\n"
        f"✅  **Принято** → деньги снимаются с баланса, отчёт уходит в **📜 логи казны**\n"
        f"❌  **Отклонено** → баланс не трогается, автор получает причину\n\n"
        f"> {warn}  Отчёт принимается один раз: повторная кнопка уже ничего не снимет."
    )


def _logs_text(bot) -> str:
    book = _e(bot, "hz_book", "📖")
    train = _e(bot, "hz_trainicon", "🚂")
    crown = _e(bot, "red_crown", "👑")
    gift = _e(bot, "hz_gifticon", "🎁")
    cart = _e(bot, "hz_shoppingcart", "🛒")
    bits = _e(bot, "hz_bitsicon", "💎")
    meg = _e(bot, "hz_megaphone", "📢")
    return (
        f"*{book}  История денег семьи  {book}*\n\n"
        f"### {train}  ЧТО СЮДА ПАДАЕТ\n"
        f"{gift}  Каждое **пополнение** казны\n"
        f"{cart}  Каждая **принятая трата** — сумма, статья и кому\n"
        f"{bits}  Остаток после записи\n\n"
        f"### {meg}  ПОРЯДОК В КАНАЛЕ\n"
        f"{crown}  Тут только записи бота: обсуждение — в отчётах, вопросы — руководству\n\n"
        f"> {book}  Логи не чистят и не правят руками — по ним семью и проверяют."
    )


# ─────────────────────────── карточки ───────────────────────────
def _balance_embed(bot, guild, st: dict) -> disnake.Embed:
    crown = _e(bot, "red_crown", "👑")
    bits = _e(bot, "hz_bitsicon", "💎")
    gift = _e(bot, "hz_gifticon", "🎁")
    cart = _e(bot, "hz_shoppingcart", "🛒")
    book = _e(bot, "hz_book", "📖")
    botv2 = _e(bot, "hz_botv2", "🤖")
    emb = card(f"{crown}  БАЛАНС КАЗНЫ", guild=guild)
    emb.description = (
        f"### {bits}  {_n(st['balance'])}\n\n"
        f"{gift}  Пополнено **{_n(st['raised'])}**\n"
        f"{cart}  Потрачено **{_n(st['spent'])}**\n"
        f"{book}  Записей в книге **{len(st['moves'])}**\n"
        f"{botv2}  Отчётов подано **{st['seq']}**\n\n"
        f"> {crown}  Деньги списываются только из принятого отчёта."
    )
    return emb


def _book_embed(bot, guild, st: dict) -> disnake.Embed:
    gift = _e(bot, "hz_gifticon", "➕")
    cart = _e(bot, "hz_shoppingcart", "🧾")
    bits = _e(bot, "hz_bitsicon", "⚖️")
    book = _e(bot, "hz_book", "📖")
    rows = []
    for m in list(reversed(st["moves"]))[:12]:
        icon = gift if m["kind"] == "topup" else (cart if m["kind"] == "spend" else bits)
        sign = "+" if m["kind"] == "topup" else ("−" if m["kind"] == "spend" else "=")
        rows.append([f"{icon}  {sign}{_n(m['amount'])}",
                     f"{clip(m['note'], 90)}\n<@{m['by']}> · {_when(m['ts'])} · "
                     f"остаток **{_n(m['bal'])}**"])
    return list_embed(f"{book}  КНИГА КАЗНЫ", rows, guild=guild, inline=False,
                      empty=f"{book}  Книга чистая — трат ещё не было.",
                      note=f"{_e(bot, 'hz_bitsicon', '💎')}  Остаток **{_n(st['balance'])}** · "
                           f"последние 12 записей")


def _report_embed(bot, guild, rep: dict) -> disnake.Embed:
    status = rep.get("status") or "wait"
    icon, word = {"ok": ("✅", "Принято — сумма списана"),
                  "no": ("❌", "Отклонено — баланс не тронут"),
                  "wait": ("⏳", "Ждёт приёмки")}.get(status, ("⏳", "Ждёт приёмки"))
    cart = _e(bot, "hz_shoppingcart", "🧾")
    bits = _e(bot, "hz_bitsicon", "💎")
    pencil = _e(bot, "hz_pencil", "✍️")
    like = _e(bot, "hz_membericon", "👤")
    meg = _e(bot, "hz_megaphone", "📢")
    book = _e(bot, "hz_book", "📖")
    emb = card(f"{cart}  ОТЧЁТ #{rep['id']}", guild=guild,
               color_key={"ok": "success_color", "no": "error_color"}.get(status, "embed_color"))
    emb.description = (
        f"### {bits}  {_n(rep['amount'])}\n\n"
        f"{pencil}  **На что** — {clip(rep['purpose'], 200)}\n"
        f"{like}  **Кому** — {clip(rep['people'], 200)}\n\n"
        f"{icon}  **{word}**"
    )
    if rep.get("photos"):
        emb.description += f"\n📎  **Скринов** — {int(rep['photos'])}"
    if rep.get("detail"):
        emb.add_field(name=f"{meg}  ДЕТАЛИ", value=clip(rep["detail"], 900), inline=False)
    foot = f"{pencil}  Подал <@{rep['by']}> · {_when(rep['ts'])}"
    if status == "ok":
        foot += f"\n✅ Принял <@{rep.get('decided_by', 0)}> · {_when(rep.get('decided_ts', 0))}"
    elif status == "no":
        foot += (f"\n❌ Отклонил <@{rep.get('decided_by', 0)}> · {_when(rep.get('decided_ts', 0))}"
                 f"\n📝 {clip(rep.get('reason', ''), 200)}")
    emb.add_field(name=f"{book}  ХРОНИКА", value=foot[:1024], inline=False)
    return emb


def _mine_embed(bot, st: dict, uid: int, guild=None) -> disnake.Embed:
    pencil = _e(bot, "hz_pencil", "✍️")
    bits = _e(bot, "hz_bitsicon", "💎")
    mine = [r for r in (st["reports"] or {}).values() if int(r["by"]) == int(uid)]
    rows = []
    for rep in sorted(mine, key=lambda r: -int(r["id"]))[:12]:
        icon = {"wait": "⏳", "ok": "✅", "no": "❌"}.get(rep.get("status") or "wait", "⏳")
        rows.append([f"{icon}  #{rep['id']} · {_n(rep['amount'])}",
                     f"{clip(rep['purpose'], 60)}\n{_when(rep['ts'])}"])
    return list_embed(f"{pencil}  МОИ ОТЧЁТЫ", rows, guild=guild, inline=False,
                      empty=f"{pencil}  Ты ещё не подавал отчётов.\n"
                            f"{bits}  Форма — кнопка «🧾 Подать отчёт» в канале отчётов.",
                      note="⏳ ждёт приёмку · ✅ принято — деньги списаны · ❌ отклонён")


# ─────────────────────────── модалки ───────────────────────────
class ReportModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(
            title="🧾 Отчёт о трате",
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
            return await inter.response.send_message(
                embed=_warn(inter.bot, inter.guild, "КАЗНА СПИТ",
                            f"{_e(inter.bot, 'hz_botv2', '🤖')}  Модуль казны не загружен — "
                            "нужен рестарт бота."),
                ephemeral=inter.guild is not None)
        await cog.submit(inter, str(inter.text_values["amount"]),
                         str(inter.text_values["purpose"]), str(inter.text_values["people"]),
                         str(inter.text_values.get("detail") or ""))


class TopUpModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(
            title="🎁 Пополнить казну",
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
            title="💰 Остаток казны",
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
            title="🛡 Почему отклоняем",
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
        for key, placeholder in (("manage", "👑 Выбери роль управления казной (до 3)"),
                                 ("approve", "🛡 Выбери роль приёмки отчётов (до 3)")):
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
        # ожидание скринов к отчёту: user_id → (rid, msg_id, channel_id, deadline)
        self._pending_photos: dict = {}

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
            return await inter.response.send_message(embed=_deny(self.bot, inter.guild), ephemeral=True)
        st = _load()
        bank_i = _e(self.bot, "hz_bitsicon", "💎")
        rep_i = _e(self.bot, "hz_pencil", "✍️")
        rev_i = _e(self.bot, "hz_moderator", "🛡")
        log_i = _e(self.bot, "hz_book", "📖")
        crown_i = _e(self.bot, "red_crown", "👑")
        purp_i = _e(self.bot, "hz_moderatorpurple", "👥")
        house_i = _e(self.bot, "hz_house", "🏠")
        sections = []
        for key, title in (("bank", f"{bank_i}  БАНК СЕМЬИ"), ("reports", f"{rep_i}  ОТЧЁТЫ"),
                           ("review", f"{rev_i}  ПРИЁМКА"), ("logs", f"{log_i}  ЛОГИ КАЗНЫ")):
            cid = (st["channels"] or {}).get(key)
            ch = inter.guild.get_channel(int(cid)) if cid else None
            sections.append((title, ch.mention if ch else
                             f"_нет — нажми «🏗 Создать ветку казны» ниже_"))
        manage = " ".join(f"<@&{r}>" for r in _roles(st, "manage")) or \
            "_не выбрана — казной правят только админы семьи_"
        approve = " ".join(f"<@&{r}>" for r in _roles(st, "approve")) or \
            "_не выбрана — приёмка берёт роль управления_"
        sections.append((f"{bank_i}  ОСТАТОК КАЗНЫ",
                         f"**{_n(st['balance'])}**\n"
                         f"{_e(self.bot, 'hz_gifticon', '🎁')}  собрано {_n(st['raised'])} · "
                         f"{rep_i} отчётов {st['seq']} · {log_i} записей {len(st['moves'])}"))
        sections.append((f"{purp_i}  УПРАВЛЕНИЕ КАЗНОЙ", manage))
        sections.append((f"{rev_i}  ПРИЁМКА ОТЧЁТОВ", approve))
        emb = panel_embed(
            f"{crown_i}  КАЗНА СЕМЬИ", sections, guild=inter.guild,
            intro=(f"*{crown_i}  Казна не пустеет молча  {crown_i}*\n"
                   f"{house_i}  Одно нажатие поднимает ветку: банк, отчёты, приёмка и логи.\n"
                   f"{rep_i}  Дальше — выбрать роли и задать остаток, кнопки ниже."))
        await inter.response.send_message(embed=emb, view=PanelView(st), ephemeral=True,
                                          file=_put_banner(emb, BANK_BANNER))

    # ── отчёт командой: только так Discord даёт поле с вложением ──
    @commands.slash_command(name="отчёт",
                            description="Отчёт о трате казны: сумма, статья, кому и до 3 скринов",
                            contexts=disnake.InteractionContextTypes(guild=True))
    async def report_cmd(
            self, inter: disnake.ApplicationCommandInteraction,
            сумма: str = commands.Param(description="Сколько ушло: 15 000 или 15к"),
            на_что: str = commands.Param(description="Статья: форма, транспорт, откат после рейда"),
            кому: str = commands.Param(description="На кого и на сколько человек"),
            детали: str = commands.Param("", description="Где брали, кто платил, почему столько"),
            скриншот: disnake.Attachment = commands.Param(None, description="Чек или скрин перевода"),
            скриншот_2: disnake.Attachment = commands.Param(None, description="Второй скрин"),
            скриншот_3: disnake.Attachment = commands.Param(None, description="Третий скрин"),
    ) -> None:
        if not _allowed(inter, _load(), "manage"):
            return await inter.response.send_message(embed=_need_manage(self.bot, inter.guild),
                                                     ephemeral=True)
        shots = [a for a in (скриншот, скриншот_2, скриншот_3) if a is not None][:MAX_PHOTOS]
        for att in shots:
            if not (att.content_type or "").startswith("image/"):
                return await inter.response.send_message(
                    embed=_warn(self.bot, inter.guild, "СКРИН — НЕ КАРТИНКА",
                                f"📎  `{att.filename}` не изображение. Прикрепляй png или jpg."),
                    ephemeral=True)
            if att.size > MAX_PHOTO_MB * 1024 * 1024:
                return await inter.response.send_message(
                    embed=_warn(self.bot, inter.guild, "СКРИН СЛИШКОМ ТЯЖЁЛЫЙ",
                                f"📎  `{att.filename}` больше {MAX_PHOTO_MB} МБ — сожми его."),
                    ephemeral=True)
        await inter.response.defer(ephemeral=True)
        files = []
        for att in shots:
            try:
                files.append(await att.to_file())
            except disnake.DiscordException:
                pass
        await self.submit(inter, сумма, на_что, кому, детали, files)

    # ── создание ветки ──
    async def build_branch(self, inter) -> None:
        guild = inter.guild
        if guild is None:
            return await inter.followup.send(
                embed=_warn(self.bot, None, "ТОЛЬКО НА СЕРВЕРЕ",
                            f"{_e(self.bot, 'hz_house', '🏠')}  Казна живёт на сервере семьи — "
                            "собирать ветку нужно там, в `/казна`."),
                ephemeral=True)
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
            made.append(f"🗂 {cat.mention}")
        for key, name in CHANNELS.items():
            cid = int((st["channels"] or {}).get(key) or 0)
            ch = guild.get_channel(cid) if cid else None
            if ch is None:
                ch = await guild.create_text_channel(name, category=cat,
                                                     overwrites=private if key in ("review", "logs")
                                                     else public,
                                                     topic=_topic(self.bot, key),
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
        crown = _e(self.bot, "red_crown", "👑")
        house = _e(self.bot, "hz_house", "🏠")
        pencil = _e(self.bot, "hz_pencil", "✍️")
        bits = _e(self.bot, "hz_bitsicon", "💎")
        n1 = _e(self.bot, "number1", "1⃣")
        n2 = _e(self.bot, "number2", "2⃣")
        await inter.followup.send(embed=note_embed(
            f"### {crown}  Ветка казны готова\n\n"
            f"{house}  {clip(note, 300)}\n\n"
            f"{pencil}  Осталось два шага — прямо в этом сообщении:\n"
            f"{n1}  выбрать **роль управления** и **роль приёмки**\n"
            f"{n2}  {bits}  задать остаток кнопкой **«⚖️ Задать остаток»**\n\n"
            f"> {house}  Баннеры, объяснения и кнопки уже лежат в каналах.",
            "success", title=f"{crown}  КАЗНА СЕМЬИ", guild=guild), ephemeral=True)

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
            return await inter.followup.send(
                embed=_warn(self.bot, None, "ТОЛЬКО НА СЕРВЕРЕ",
                            f"{_e(self.bot, 'hz_house', '🏠')}  Баннеры кладутся в каналы казны "
                            "на сервере семьи."),
                ephemeral=True)
        st = _load()
        done = await self._post_banners(inter.guild, st)
        star = _e(self.bot, "hz_winterstar", "⭐")
        crown = _e(self.bot, "red_crown", "👑")
        house = _e(self.bot, "hz_house", "🏠")
        if done:
            emb = note_embed(
                f"### {star}  Витрина казны обновлена\n\n"
                f"{crown}  **Банк** и **отчёты** — объяснение сверху, баннер под текстом\n"
                f"{_e(self.bot, 'hz_moderator', '🛡')}  **Приёмка** и **логи** — памятки для своих\n\n"
                f"> {house}  Старые карточки убраны, кнопки на местах.",
                "success", title=f"{star}  БАННЕРЫ ПЕРЕЗАЛИТЫ", guild=inter.guild)
        else:
            emb = _warn(self.bot, inter.guild, "ВЕТКИ ЕЩЁ НЕТ",
                        f"{_e(self.bot, 'hz_pencil', '✍️')}  Сначала «🏗 Создать ветку казны» — "
                        "баннеры кладутся в уже готовые каналы.")
        await inter.followup.send(embed=emb, ephemeral=True)

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
        bank_emb = card(f"{_e(self.bot, 'red_crown', '👑')}  БАНК СЕМЬИ",
                        _bank_text(self.bot), guild=guild)
        # Картинка не отдельным сообщением, а под текстом — как в заявках.
        put = _put_banner(bank_emb, BANK_BANNER)
        await ch["bank"].send(embed=bank_emb, view=BankView(), file=put)
        rep_emb = card(f"{_e(self.bot, 'hz_pencil', '✍️')}  ОТЧЁТЫ КАЗНЫ",
                       _reports_text(self.bot), guild=guild)
        rput = _put_banner(rep_emb, REPORT_BANNER)
        await ch["reports"].send(embed=rep_emb, view=ReportsView(), file=rput)
        for key, title, text in (
                ("review", f"{_e(self.bot, 'hz_moderator', '🛡')}  ПРИЁМКА ОТЧЁТОВ",
                 _review_text(self.bot)),
                ("logs", f"{_e(self.bot, 'hz_book', '📖')}  ЛОГИ КАЗНЫ", _logs_text(self.bot))):
            c = ch[key]
            if c is None or await _has_bot_message(c, guild):
                continue
            await c.send(embed=card(title, text, guild=guild))
        return True

    # ── роли ──
    async def save_roles(self, inter, key: str, values) -> None:
        if not is_admin(inter):
            return await inter.response.send_message(embed=_deny(self.bot, inter.guild),
                                                     ephemeral=inter.guild is not None)
        st = _load()
        st["roles"][key] = [int(r.id) for r in list(values)[:3]]
        _save(st)
        if inter.guild:
            await self._sync_perms(inter.guild, st)
        label = "Роль управления казной" if key == "manage" else "Роль приёмки отчётов"
        names = " ".join(f"<@&{r}>" for r in st["roles"][key]) or \
            f"_{label.lower()} сброшена — казной правят только админы семьи_"
        crown = _e(self.bot, "red_crown", "👑")
        mod = _e(self.bot, "hz_moderator", "🛡")
        bits = _e(self.bot, "hz_bitsicon", "💎")
        pencil = _e(self.bot, "hz_pencil", "✍️")
        await inter.response.send_message(
            embed=note_embed(f"### {mod}  {label}\n{names}\n\n"
                             f"{bits}  Доступ к приёмке и логам казны уже переставлен\n"
                             f"{pencil}  Управление подаёт отчёты и пополняет казну\n"
                             f"{crown}  Приёмка решает судьбу каждого отчёта",
                             "success", title=f"{mod}  РОЛЬ ВЫБРАНА", guild=inter.guild),
            ephemeral=True)

    # ── баланс ──
    async def set_balance(self, inter, raw: str, note: str) -> None:
        if not is_admin(inter):
            return await inter.response.send_message(embed=_deny(self.bot, inter.guild), ephemeral=True)
        value = _amount(raw)
        if value is None:
            return await inter.response.send_message(
                embed=_warn(self.bot, inter.guild, "НЕ ПОНЯЛ СУММУ",
                            f"{_e(self.bot, 'hz_bitsicon', '💎')}  Остаток пишут цифрами: "
                            "`15000` или `15к`."),
                ephemeral=True)
        st = _load()
        old = int(st["balance"])
        st["balance"] = value
        _move(st, "set", value - old, inter.author.id, note or "ручная корректировка остатка")
        _save(st)
        bits = _e(self.bot, "hz_bitsicon", "💎")
        pencil = _e(self.bot, "hz_pencil", "✍️")
        crown = _e(self.bot, "red_crown", "👑")
        await inter.response.send_message(
            embed=note_embed(f"### {bits}  {_n(old)}  →  {_n(value)}\n\n"
                             f"{pencil}  {note or 'без пояснения'}\n\n"
                             f"> {crown}  Остаток правит только руководство семьи.",
                             "success", title=f"{bits}  ОСТАТОК КАЗНЫ", guild=inter.guild),
            ephemeral=True)
        await self._log(inter.guild, f"{bits}  Остаток казны изменили: {_n(old)} → {_n(value)}",
                        inter.author.id, note or "ручная корректировка")

    async def top_up(self, inter, raw: str, note: str) -> None:
        st = _load()
        if not _allowed(inter, st, "manage"):
            return await inter.response.send_message(embed=_deny(self.bot, inter.guild), ephemeral=True)
        value = _amount(raw)
        if value is None:
            return await inter.response.send_message(
                embed=_warn(self.bot, inter.guild, "НЕ ПОНЯЛ СУММУ",
                            f"{_e(self.bot, 'hz_gifticon', '🎁')}  Взнос пишут цифрами: "
                            "`50000` или `50к`."), ephemeral=True)
        st["balance"] = int(st["balance"]) + value
        st["raised"] = int(st["raised"]) + value
        _move(st, "topup", value, inter.author.id, note or "пополнение казны")
        _save(st)
        gift = _e(self.bot, "hz_gifticon", "🎁")
        bits = _e(self.bot, "hz_bitsicon", "💎")
        book = _e(self.bot, "hz_book", "📖")
        await inter.response.send_message(
            embed=note_embed(f"### {gift}  +{_n(value)}\n\n"
                             f"{bits}  Остаток: **{_n(st['balance'])}** · {_when(int(time.time()))}\n"
                             f"{book}  {note or 'взнос в казну'}",
                             "success", title=f"{gift}  ПОПОЛНЕНИЕ КАЗНЫ", guild=inter.guild),
            ephemeral=True)
        await self._log(inter.guild, f"{gift}  Пополнение казны: **{_n(value)}**",
                        inter.author.id, note or "взнос в казну")

    # ── отчёты ──
    async def _answer(self, inter, emb: disnake.Embed) -> None:
        """Ответ туда, где его ещё можно отправить: defer'нутый слэш уходит followup'ом."""
        if inter.response.is_done():
            await inter.followup.send(embed=emb, ephemeral=True)
        else:
            await inter.response.send_message(embed=emb, ephemeral=True)

    async def submit(self, inter, raw_amount: str, purpose: str, people: str, detail: str,
                     files: Optional[List[disnake.File]] = None) -> None:
        st = _load()
        if not _allowed(inter, st, "manage"):
            return await self._answer(inter, _need_manage(self.bot, inter.guild))
        value = _amount(raw_amount)
        if value is None:
            return await self._answer(
                inter, _warn(self.bot, inter.guild, "НЕ ПОНЯЛ СУММУ",
                             f"{_e(self.bot, 'hz_shoppingcart', '🛒')}  Сумму траты пишут цифрами: "
                             "`15000` или `15к`."))
        review = inter.guild.get_channel(int((st["channels"] or {}).get("review") or 0)) \
            if inter.guild else None
        if review is None:
            return await self._answer(
                inter, _warn(self.bot, inter.guild, "НЕТ КАНАЛА ПРИЁМКИ",
                             f"{_e(self.bot, 'hz_moderator', '🛡')}  Приёмки ещё нет — на экране "
                             "`/казна` нажми «🏗 Создать ветку казны», потом подавай отчёт."))
        st["seq"] = int(st.get("seq") or 0) + 1
        rid = str(st["seq"])
        rep = {"id": rid, "by": int(inter.author.id), "amount": value,
               "purpose": (purpose or "без статьи")[:200],
               "people": (people or "не указано")[:200], "detail": detail[:900],
               "ts": int(time.time()), "status": "wait",
               "photos": len(files) if files else 0}
        st["reports"][rid] = rep
        if len(st["reports"]) > MAX_REPORTS:
            # Выкидываем только закрытые отчёты: живой отчёт без карточки остался бы без приёмки.
            closed = sorted((r for r in st["reports"].values()
                             if r.get("status") != "wait"), key=lambda r: int(r["id"]))
            for old in closed[: len(st["reports"]) - MAX_REPORTS]:
                st["reports"].pop(str(old["id"]), None)
        _save(st)
        review_msg = await review.send(embed=_report_embed(self.bot, inter.guild, rep),
                                       view=ReviewView(rid), files=files or [])
        meg = _e(self.bot, "hz_megaphone", "📢")
        pencil = _e(self.bot, "hz_pencil", "✍️")
        bits = _e(self.bot, "hz_bitsicon", "💎")
        mod = _e(self.bot, "hz_moderator", "🛡")
        photo = "📎"
        if files:
            photos = f"{photo}  **Скринов приложено:** {len(files)}\n"
            tail = f"> {pencil}  Следующая трата — `/отчёт` или кнопкой в канале отчётов."
        else:
            PHOTO_SECONDS = 120
            self._pending_photos[int(inter.author.id)] = (
                rid, review_msg.id, int(inter.channel.id), time.time() + PHOTO_SECONDS)
            photos = (f"{photo}  **Прикрепи скрины** ответом в этот канал — до 3 штук, "
                      f"жду {PHOTO_SECONDS} сек.\n")
            tail = f"> {pencil}  Следующая трата — той же кнопкой."
        await self._answer(
            inter, note_embed(
                f"### {meg}  Отчёт #{rid} ушёл в {review.mention}\n\n"
                f"{bits}  **{_n(value)}** спишутся только после приёмки\n"
                f"{mod}  Решение за ролью приёмки — о нём придёт ЛС\n\n"
                f"{photos}"
                f"{tail}",
                "success", title=f"{pencil}  ОТЧЁТ ОТПРАВЛЕН",
                guild=inter.guild))

    async def decide(self, inter, rid: str, accept: bool, reason: str = "") -> None:
        st = _load()
        rep = (st["reports"] or {}).get(rid)
        if not rep:
            return await inter.response.send_message(
                embed=_warn(self.bot, inter.guild, "ОТЧЁТ НЕ НАЙДЕН",
                            f"{_e(self.bot, 'hz_book', '📖')}  Такой отчёт уже стёрли из книги — "
                            "карточка в канале осталась старой."),
                ephemeral=True)
        if (rep.get("status") or "wait") != "wait":
            return await inter.response.send_message(
                embed=_warn(self.bot, inter.guild, "ОТЧЁТ ЗАКРЫТ",
                            f"{_e(self.bot, 'hz_bitsicon', '💎')}  Отчёт #{rid} уже решили — "
                            "второй раз деньги не снимаются."), ephemeral=True)
        if not _allowed(inter, st, "approve"):
            return await inter.response.send_message(embed=_deny(self.bot, inter.guild), ephemeral=True)
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
            await self._log(inter.guild,
                            f"{_e(self.bot, 'hz_shoppingcart', '🧾')}  Принят отчёт **#{rid}** — "
                            f"списано **{_n(rep['amount'])}**",
                            int(rep["by"]), f"{rep['purpose']} · {rep['people']}",
                            balance=int(st["balance"]))
        await inter.response.edit_message(embed=_report_embed(self.bot, inter.guild, rep), view=None)
        await self._tell(inter, rep)

    async def _tell(self, inter, rep: dict) -> None:
        author = inter.guild.get_member(int(rep["by"])) if inter.guild else None
        if author is None:
            return
        ok = rep["status"] == "ok"
        crown = _e(self.bot, "red_crown", "👑")
        bits = _e(self.bot, "hz_bitsicon", "💎")
        book = _e(self.bot, "hz_book", "📖")
        pencil = _e(self.bot, "hz_pencil", "✍️")
        warn = _e(self.bot, "hz_warn", "📝")
        if ok:
            text = (f"### {bits}  −{_n(rep['amount'])}\n\n"
                    f"{pencil}  Отчёт **#{rep['id']}** принят: «{clip(rep['purpose'], 90)}»\n"
                    f"{bits}  Остаток казны: **{_n(_load()['balance'])}**\n"
                    f"{book}  Запись уже в книге — её видно руководству\n\n"
                    f"> {crown}  Спасибо, что отчитался.")
        else:
            text = (f"### {warn}  Отчёт **#{rep['id']}** отклонён\n\n"
                    f"{pencil}  «{clip(rep['purpose'], 90)}» на **{_n(rep['amount'])}**\n"
                    f"{bits}  Баланс казны **не тронут**\n\n"
                    f"> {crown}  Пояснение: {clip(rep.get('reason', '') or 'без причины', 200)}")
        try:
            await author.send(embed=note_embed(text, "success" if ok else "error",
                                               title=f"{crown}  КАЗНА СЕМЬИ"))
        except disnake.DiscordException:
            pass

    async def _log(self, guild, title: str, who: int, note: str, balance: Optional[int] = None) -> None:
        st = _load()
        cid = int((st["channels"] or {}).get("logs") or 0)
        ch = guild.get_channel(cid) if (guild and cid) else None
        if ch is None:
            return
        member = _e(self.bot, "hz_membericon", "👤")
        when = "🕒"
        bits = _e(self.bot, "hz_bitsicon", "💎")
        emb = card(title, guild=guild)
        emb.description = (f"{note}\n\n"
                           f"{member}  <@{who}>\n"
                           f"{when}  {_when(int(time.time()))}\n"
                           + (f"{bits}  Остаток: **{_n(balance)}**" if balance is not None else ""))
        try:
            await ch.send(embed=emb)
        except disnake.DiscordException:
            pass

    # ── скрины к отчёту: после модальной формы их прикладывают следующим сообщением ──
    @commands.Cog.listener()
    async def on_message(self, message: disnake.Message):
        if message.author.bot:
            return
        pending = self._pending_photos.get(int(message.author.id))
        if not pending:
            return
        rid, msg_id, channel_id, deadline = pending
        if int(message.channel.id) != channel_id:
            return
        if time.time() > deadline:
            self._pending_photos.pop(int(message.author.id), None)
            return
        images = [a for a in message.attachments
                  if (a.content_type or "").startswith("image/")]
        if not images:
            return
        self._pending_photos.pop(int(message.author.id), None)
        review = message.guild.get_channel(int((_load()["channels"] or {}).get("review") or 0)) \
            if message.guild else None
        if review is None:
            return
        try:
            review_msg = await review.fetch_message(int(msg_id))
        except disnake.DiscordException:
            return
        files = []
        for att in images[:MAX_PHOTOS - len(review_msg.attachments)]:
            try:
                files.append(await att.to_file())
            except disnake.DiscordException:
                pass
        if not files:
            return
        try:
            await review_msg.edit(files=files)
        except disnake.DiscordException:
            return
        st = _load()
        rep = (st["reports"] or {}).get(rid)
        if rep:
            rep["photos"] = len(review_msg.attachments) + len(files)
            _save(st)
        try:
            await message.delete()
        except disnake.DiscordException:
            pass
        try:
            await message.channel.send(
                embed=note_embed(f"📎  Скрины к отчёту **#{rid}** прикреплены ({len(files)} шт.).",
                                 "success", title="📎  ФОТО ДОБАВЛЕНЫ", guild=message.guild))
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
                return await inter.response.send_message(embed=_deny(self.bot, inter.guild), ephemeral=True)
            return await inter.response.send_message(embed=_balance_embed(self.bot, inter.guild, st),
                                                     ephemeral=True)
        if action == "book":
            if not _allowed(inter, st, "manage"):
                return await inter.response.send_message(embed=_deny(self.bot, inter.guild), ephemeral=True)
            return await inter.response.send_message(embed=_book_embed(self.bot, inter.guild, st),
                                                     ephemeral=True)
        if action == "topup":
            if not _allowed(inter, st, "manage"):
                return await inter.response.send_message(embed=_deny(self.bot, inter.guild), ephemeral=True)
            return await inter.response.send_modal(TopUpModal())
        if action == "new":
            if not _allowed(inter, st, "manage"):
                return await inter.response.send_message(
                    embed=_need_manage(self.bot, inter.guild),
                    ephemeral=True)
            return await inter.response.send_modal(ReportModal())
        if action == "mine":
            return await inter.response.send_message(
                embed=_mine_embed(self.bot, st, inter.author.id, inter.guild), ephemeral=True)
        if action in ("ok", "no"):
            rid = cid.split(":", 2)[2]
            if action == "ok":
                return await self.decide(inter, rid, True)
            return await inter.response.send_modal(RejectModal(rid))


def _topic(bot, key: str) -> str:
    crown = _e(bot, "red_crown", "👑")
    cart = _e(bot, "hz_shoppingcart", "🧾")
    mod = _e(bot, "hz_moderator", "🛡")
    book = _e(bot, "hz_book", "📖")
    return {
        "bank": f"{crown}  Банк семьи — остаток казны и книга трат",
        "reports": f"{cart}  Отчёты о тратах — сколько, на что и кому",
        "review": f"{mod}  Приёмка отчётов — решает роль приёмки",
        "logs": f"{book}  Логи казны — принятые траты и пополнения",
    }.get(key, f"{crown}  Казна семьи")


def setup(bot: commands.Bot):
    bot.add_cog(Treasury(bot))
