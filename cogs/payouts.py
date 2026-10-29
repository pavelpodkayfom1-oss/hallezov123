"""Выплаты семьи: фонд, заявки, приёмка и логи.

Схема такая: человек взял дело, сделал его и сам подаёт заявку — ник, что сделал, сколько
ему платят и (по желанию) сколько денег это дело принесло в баланс. Скрины-доказательства
перетаскиваются прямо в форму, до трёх штук.

Заявка уходит в отдельный канал приёмки, который виден только нужной роли: там жмут
«Выплатить» или «Отклонить» с причиной. О решении человек узнаёт в ЛС, а принятая выплата
ложится в книгу и снимается с фонда — «молча заплатить» нельзя.

Всё живёт в payouts.json; «🏗 Создать ветку» на экране `/выплаты` поднимает категорию и
четыре канала разом: 💸 фонд, 🧾 заявки, ✅ приёмка и 📜 логи.
"""
import json
import os
import time
from typing import List, Optional

import disnake
from disnake.ext import commands

from utils import brand, cdn_assets
from utils.checks import get_text, is_admin, load_config
from utils.embeds import card, clip, list_embed, note_embed, panel_embed

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, "payouts.json")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

BANK_BANNER = "payouts_bank.jpg"
CLAIM_BANNER = "payouts_requests.jpg"
# Своя полоска цвета: с общей красной карточкой выплаты читались как казна.
ACCENT = "pay_color"

CATEGORY = "💸 ВЫПЛАТЫ СЕМЬИ"
CHANNELS = {
    "bank": "💸-фонд-выплат",
    "claims": "🧾-заявки-выплат",
    "review": "✅-приёмка-выплат",
    "logs": "📜-логи-выплат",
}
MAX_ROWS = 300
MAX_CLAIMS = 200
MAX_AMOUNT = 10 ** 12
# Скринов к заявке максимум три, и только картинками: приёмка читает их прямо с карточки.
MAX_PHOTOS = 3
MAX_PHOTO_MB = 8
# Сколько секунд ждём скрины ответом в канале, если в форму ничего не перетащили.
PHOTO_SECONDS = 120

# ─────────────────────────── тексты выплат ───────────────────────────
# Каждый текст вынесен константой, чтобы редактор «Тексты» в веб-панели видел настоящий
# дефолт: «Как в коде» возвращает именно эту строку, а не пустоту. `:имя:` внутри — токены
# эмодзи пака, brand.E подставляет живые эмодзи сервера. {скобки} — подстановки: их бот
# заполняет цифрами и упоминаниями, править их можно, удалять не стоит — строка тогда
# теряет число.


def _T(bot, key: str, default: str, **subs) -> str:
    """Текст выплат: правка из панели → эмодзи пака → подстановки {имя} без риска падения."""
    text = brand.E(bot, get_text(key, default))
    for name, value in subs.items():
        text = text.replace("{" + name + "}", str(value))
    return text


T_PANEL_TITLE = "💸  ВЫПЛАТЫ"
T_PANEL_INTRO = (
    "Взял дело — сделал — подал заявку — получил.\n"
    "Подниму четыре канала: фонд, заявки, приёмка и логи."
)

T_BANK_HEAD = "💸  ФОНД ВЫПЛАТ"
T_CLAIMS_HEAD = "🧾  ЗАЯВКИ НА ВЫПЛАТУ"
T_REVIEW_HEAD = "✅  ПРИЁМКА ВЫПЛАТ"
T_LOGS_HEAD = "📜  ЛОГИ ВЫПЛАТ"

T_BANK_TEXT = (
    "Деньги на дела семьи: зарплата, награды, помощь своим.\n"
    "Уходят только когда приёмка жмёт «Выплатить».\n\n"
    "💸 **Фонд** — сколько есть сейчас\n"
    "📜 **Книга** — кому, сколько и за что\n"
    "➕ **Пополнить** — записать взнос"
)

T_CLAIMS_TEXT = (
    "Сделал дело — жми **«🧾 Подать заявку»** и заполни форму:\n"
    "ник · что сделал · сколько платим.\n"
    "«Поступило в баланс» — по желанию, можно пропустить.\n\n"
    "📎 Скрины перетаскиваются прямо в форму, до трёх.\n"
    "Не перетащил — кинь их ответом в этот канал за 2 минуты.\n\n"
    "Заявку видит только приёмка. Решение придёт тебе в ЛС."
)

T_REVIEW_TEXT = (
    "Канал только для роли приёмки — без роли его не видно.\n\n"
    "✅ **Выплатить** — сумма снимется с фонда, автору придёт ЛС\n"
    "❌ **Отклонить** — фонд не тронут, в ЛС уйдёт причина\n\n"
    "Заявку можно принять только один раз."
)

T_LOGS_TEXT = (
    "Только записи бота: пополнения фонда и принятые выплаты.\n"
    "Обсуждение — в канале заявок. Логи не правят руками."
)

T_BANK_TOPIC = "💸  Фонд выплат: остаток и книга"
T_CLAIMS_TOPIC = "🧾  Заявки на выплату: ник, дело, сумма"
T_REVIEW_TOPIC = "✅  Приёмка: решает только роль приёмки"
T_LOGS_TOPIC = "📜  Логи: пополнения и принятые выплаты"
T_DEFAULT_TOPIC = "💸  Выплаты семьи"

T_FUND_TITLE = "💸  ФОНД ВЫПЛАТ"
T_FUND_TEXT = (
    "### {balance}\n\n"
    "➕ Пополнено **{raised}**  ·  💸 Выплачено **{paid}**\n"
    "🧾 Заявок **{claims}**  ·  📜 Записей **{moves}**\n\n"
    "> Деньги уходят только из принятой заявки."
)
T_BOOK_TITLE = "📜  КНИГА ВЫПЛАТ"
T_BOOK_EMPTY = "📜  Книга чистая — выплат ещё не было."
T_BOOK_NOTE = "💸  Остаток **{balance}** · последние 12 записей"
T_MINE_TITLE = "🧾  МОИ ЗАЯВКИ"
T_MINE_EMPTY = (
    "🧾  Ты ещё не подавал заявок.\n"
    "Кнопка «🧾 Подать заявку» — в канале заявок."
)
T_MINE_NOTE = "⏳ ждёт приёмку · ✅ выплачено · ❌ отклонена"
T_CLAIM_TITLE = "🧾  ЗАЯВКА #{id}"
T_STATUS_OK = "Выплачено — сумма снята с фонда"
T_STATUS_NO = "Отклонено — фонд не тронут"
T_STATUS_WAIT = "Ждёт приёмки"
T_DONE_TEXT = (
    "### 💸  Ветка выплат готова\n\n"
    "{made}\n\n"
    "Осталось два шага — прямо тут:\n"
    "1️⃣  выбрать роль управления и роль приёмки\n"
    "2️⃣  задать остаток кнопкой **«⚖️ Задать фонд»**"
)
T_OK_TITLE = "💸  ВЫПЛАТА ПРИНЯТА"
T_OK_TEXT = (
    "### Тебе платят {amount}\n\n"
    "🧾  Заявка **#{id}** · дело: {what}\n"
    "✅  Принял <@{decided}>\n\n"
    "> Не сходится — напиши руководству."
)
T_NO_TITLE = "🧾  ВЫПЛАТА ОТКЛОНЕНА"
T_NO_TEXT = (
    "### Заявка **#{id}** отклонена\n\n"
    "💸  Просил {amount} · дело: {what}\n"
    "❌  Решил <@{decided}>\n\n"
    "> Пояснение: {why}"
)


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
    data.setdefault("paid", 0)
    data.setdefault("seq", 0)
    data.setdefault("roles", {"manage": [], "approve": []})
    data.setdefault("channels", {})
    data.setdefault("claims", {})
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
    member = getattr(inter, "author", None) or inter
    return bool(want) and any(r.id in want for r in getattr(member, "roles", []))


def _deny(bot=None, guild=None) -> disnake.Embed:
    warn = "⚠️"
    crown = "👑"
    return note_embed(
        f"{warn}  Это канал выплат: фонд и книга видны только своим.\n\n"
        f"{crown}  Роль выдаёт руководство семьи — экран `/выплаты`.",
        "error", guild=guild, title=f"{warn}  НЕТ ДОСТУПА")


def _not_here(bot=None, guild=None) -> disnake.Embed:
    forbidden = "⛔"
    mod = "🛡"
    crown = "👑"
    return note_embed(
        f"{forbidden}  Приёмка выплат — не твой канал.\n\n"
        f"{mod}  Тут решают судьбу каждой заявки\n"
        f"{crown}  Роль выдаёт руководство семьи — экран `/выплаты`.",
        "error", guild=guild, title=f"{forbidden}  СЮДА НЕЛЬЗЯ")


def _warn(bot=None, guild=None, head: str = "ТАК НЕ ВЫЙДЕТ", text: str = "") -> disnake.Embed:
    warn = "⚠️"
    return note_embed(text, "error", title=f"{warn}  {head}", guild=guild)


def _cog(inter) -> Optional["Payouts"]:
    """Ког берём из взаимодействия: у ephemeral-вью нет .bot, пока его никто не зарегистрировал."""
    bot = getattr(inter, "bot", None)
    return bot.get_cog("Payouts") if bot else None


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
    if len(st["moves"]) > MAX_ROWS:
        del st["moves"][: len(st["moves"]) - MAX_ROWS]


# ─────────────────────────── баннеры и эмодзи ───────────────────────────
def _put_banner(embed: disnake.Embed, asset: str) -> Optional[disnake.File]:
    """Картинка под текстом — как в казне: ссылка на CDN, а если её нет — вложение."""
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


# ─────────────────────────── карточки ───────────────────────────
def _fund_embed(bot, guild, st: dict) -> disnake.Embed:
    emb = card(_T(bot, "pay_fund_title", T_FUND_TITLE), guild=guild, color_key=ACCENT)
    emb.description = _T(bot, "pay_fund_text", T_FUND_TEXT,
                         balance=_n(st["balance"]), raised=_n(st["raised"]),
                         paid=_n(st["paid"]), moves=len(st["moves"]), claims=st["seq"])
    return emb


def _book_embed(bot, guild, st: dict) -> disnake.Embed:
    gift, bits, scale = "➕", "💸", "⚖️"
    rows = []
    for m in list(reversed(st["moves"]))[:12]:
        icon = gift if m["kind"] == "topup" else (bits if m["kind"] == "pay" else scale)
        sign = "+" if m["kind"] == "topup" else ("−" if m["kind"] == "pay" else "=")
        rows.append([f"{icon}  {sign}{_n(m['amount'])}",
                     f"{clip(m['note'], 90)}\n<@{m['by']}> · {_when(m['ts'])} · "
                     f"остаток **{_n(m['bal'])}**"])
    return list_embed(_T(bot, "pay_book_title", T_BOOK_TITLE), rows, guild=guild, color_key=ACCENT,
                      inline=False,
                      empty=_T(bot, "pay_book_empty", T_BOOK_EMPTY),
                      note=_T(bot, "pay_book_note", T_BOOK_NOTE, balance=_n(st["balance"])))


def _claim_embed(bot, guild, pay: dict) -> disnake.Embed:
    status = pay.get("status") or "wait"
    icon = {"ok": "✅", "no": "❌", "wait": "⏳"}.get(status, "⏳")
    words = {"ok": _T(bot, "pay_status_ok", T_STATUS_OK),
             "no": _T(bot, "pay_status_no", T_STATUS_NO),
             "wait": _T(bot, "pay_status_wait", T_STATUS_WAIT)}
    word = words.get(status, words["wait"])
    bits = "💸"
    pencil = "🧾"
    like = "👤"
    cart = "➕"
    book = "📜"
    emb = card(_T(bot, "pay_claim_title", T_CLAIM_TITLE, id=pay["id"]), guild=guild,
               color_key={"ok": "success_color", "no": "error_color"}.get(status, ACCENT))
    emb.description = (
        f"### {bits}  {_n(pay['amount'])}\n\n"
        f"{like}  **{_T(bot, 'pay_field_nick', 'Твой ник')}** — {clip(pay['nick'], 60)}\n"
        f"{cart}  **{_T(bot, 'pay_field_in', 'Поступило в баланс')}** — "
        f"{_n(pay['in']) if pay.get('in') else '—'}\n\n"
        f"{icon}  **{word}**"
    )
    if pay.get("photos"):
        emb.description += (f"\n📎  **{_T(bot, 'pay_field_photos', 'Скринов')}** — "
                            f"{int(pay['photos'])}")
    emb.add_field(name=f"{pencil}  ДЕЛО", value=clip(pay["what"], 900), inline=False)
    foot = f"{pencil}  Подал <@{pay['by']}> · {_when(pay['ts'])}"
    if status == "ok":
        foot += (f"\n✅ Выплатил <@{pay.get('decided_by', 0)}> · "
                 f"{_when(pay.get('decided_ts', 0))}")
    elif status == "no":
        foot += (f"\n❌ Отклонил <@{pay.get('decided_by', 0)}> · "
                 f"{_when(pay.get('decided_ts', 0))}\n📝 {clip(pay.get('why', ''), 200)}")
    emb.add_field(name=f"{book}  ХРОНИКА", value=foot[:1024], inline=False)
    return emb


def _mine_embed(bot, st: dict, uid: int, guild=None) -> disnake.Embed:
    mine = [p for p in (st["claims"] or {}).values() if int(p["by"]) == int(uid)]
    rows = []
    for pay in sorted(mine, key=lambda p: -int(p["id"]))[:12]:
        icon = {"wait": "⏳", "ok": "✅", "no": "❌"}.get(pay.get("status") or "wait", "⏳")
        rows.append([f"{icon}  #{pay['id']} · {_n(pay['amount'])}",
                     f"{clip(pay['what'], 60)}\n{_when(pay['ts'])}"])
    return list_embed(_T(bot, "pay_mine_title", T_MINE_TITLE), rows, guild=guild, color_key=ACCENT,
                      inline=False,
                      empty=_T(bot, "pay_mine_empty", T_MINE_EMPTY),
                      note=_T(bot, "pay_mine_note", T_MINE_NOTE))


# ─────────────────────────── модалки ───────────────────────────
class ClaimModal(disnake.ui.Modal):
    """Форма заявки: Discord даёт поле с файлом прямо в модалке (ui.FileUpload, тип 19),
    поэтому скрины перетаскиваются туда же, где ник и сумма, а не отдельной командой.
    Пять компонентов — потолок модальной формы, поэтому «поступило в баланс» идёт
    необязательным полем, а пояснения человек пишет в «Что сделали»."""

    def __init__(self):
        super().__init__(
            title=get_text("pay_modal_claim_title", "🧾 Заявка на выплату"),
            custom_id="hzpaym:claim",
            components=[
                disnake.ui.TextInput(
                    label=get_text("pay_modal_nick_label", "Твой ник"), custom_id="nick",
                    placeholder=get_text("pay_modal_nick_ph", "Ник в игре, по нему и платим"),
                    max_length=40),
                disnake.ui.TextInput(
                    label=get_text("pay_modal_what_label", "Что сделали"), custom_id="what",
                    style=disnake.TextInputStyle.paragraph,
                    placeholder=get_text("pay_modal_what_ph",
                                         "Взял дело, отвёз груз, получил деньги — детали и кто "
                                         "подтверждает"),
                    max_length=900),
                disnake.ui.TextInput(
                    label=get_text("pay_modal_amount_label", "Сколько платим"), custom_id="amount",
                    placeholder=get_text("pay_modal_amount_ph", "15 000"), max_length=16),
                disnake.ui.TextInput(
                    label=get_text("pay_modal_in_label", "Сколько поступило в баланс"),
                    custom_id="income", required=False,
                    placeholder=get_text("pay_modal_in_ph", "Можно пропустить · 40 000"),
                    max_length=16),
                disnake.ui.Label(
                    # цифра в надписи = MAX_PHOTOS: если менять лимит, правь и здесь
                    get_text("pay_modal_screens_label", "Скрины (до 3)"),
                    disnake.ui.FileUpload(custom_id="photos", min_values=0,
                                          max_values=MAX_PHOTOS, required=False),
                    description=get_text("pay_modal_screens_hint",
                                         "Перетащи скрин перевода или итог сделки — уйдут "
                                         "в приёмку вместе с заявкой")),
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        cog: Optional[Payouts] = inter.bot.get_cog("Payouts")
        if cog is None:
            return await inter.response.send_message(
                embed=_warn(inter.bot, inter.guild, "ВЫПЛАТЫ СПЯТ",
                            "🤖  Модуль выплат не загружен — "
                            "нужен рестарт бота."),
                ephemeral=inter.guild is not None)
        shots = [a for a in (inter.resolved_values.get("photos") or [])
                 if a is not None][:MAX_PHOTOS]
        for att in shots:
            if not (att.content_type or "").startswith("image/"):
                return await inter.response.send_message(
                    embed=_warn(inter.bot, inter.guild, "СКРИН — НЕ КАРТИНКА",
                                f"📎  `{att.filename}` не изображение. Прикрепляй png или jpg."),
                    ephemeral=True)
            if att.size > MAX_PHOTO_MB * 1024 * 1024:
                return await inter.response.send_message(
                    embed=_warn(inter.bot, inter.guild, "СКРИН СЛИШКОМ ТЯЖЁЛЫЙ",
                                f"📎  `{att.filename}` больше {MAX_PHOTO_MB} МБ — сожми его."),
                    ephemeral=True)
        # картинку тянут с CDN, и это не успевает случиться за 3 секунды ответа
        await inter.response.defer(with_message=True, ephemeral=True)
        files = []
        for att in shots:
            try:
                files.append(await att.to_file())
            except disnake.DiscordException:
                pass
        await cog.submit(inter, str(inter.text_values["nick"]), str(inter.text_values["what"]),
                         str(inter.text_values["amount"]),
                         str(inter.text_values.get("income") or ""), files)


class TopUpModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(
            title=get_text("pay_modal_topup_title", "🎁 Пополнить фонд выплат"),
            custom_id="hzpaym:topup",
            components=[
                disnake.ui.TextInput(
                    label=get_text("pay_modal_amount_label", "Сколько"), custom_id="amount",
                    placeholder=get_text("pay_modal_topup_amount_ph", "50 000"), max_length=16),
                disnake.ui.TextInput(
                    label=get_text("pay_modal_topup_note_label", "Откуда"), custom_id="note",
                    placeholder=get_text("pay_modal_topup_note_ph", "Переложили из казны"),
                    max_length=200),
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        cog: Optional[Payouts] = inter.bot.get_cog("Payouts")
        if cog is None:
            return
        await cog.top_up(inter, str(inter.text_values["amount"]),
                         str(inter.text_values["note"]))


class SetFundModal(disnake.ui.Modal):
    def __init__(self, current: int):
        self.current = current
        super().__init__(
            title=get_text("pay_modal_setfund_title", "💸 Фонд выплат"),
            custom_id="hzpaym:setfund",
            components=[
                disnake.ui.TextInput(
                    label=get_text("pay_modal_setfund_label", "Новый остаток фонда"),
                    custom_id="amount", placeholder=_n(current), max_length=16),
                disnake.ui.TextInput(
                    label=get_text("pay_modal_setfund_note_label", "Почему меняем"),
                    custom_id="note",
                    placeholder=get_text("pay_modal_setfund_note_ph",
                                         "Перенесли остаток из старого учёта"), max_length=200),
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        cog: Optional[Payouts] = inter.bot.get_cog("Payouts")
        if cog is None:
            return
        await cog.set_fund(inter, str(inter.text_values["amount"]),
                           str(inter.text_values["note"]))


class RejectModal(disnake.ui.Modal):
    def __init__(self, pid: str):
        self.pid = pid
        super().__init__(
            title=get_text("pay_modal_reject_title", "🛡 Почему не платим"),
            custom_id=f"hzpaym:reject:{pid}",
            components=[
                disnake.ui.TextInput(
                    label=get_text("pay_modal_reject_label", "Причина отказа"),
                    custom_id="reason", style=disnake.TextInputStyle.paragraph,
                    placeholder=get_text("pay_modal_reject_ph",
                                         "Нет скринов / сумма не сходится с делом"),
                    max_length=300),
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        cog: Optional[Payouts] = inter.bot.get_cog("Payouts")
        if cog is None:
            return
        await cog.decide(inter, self.pid, False, str(inter.text_values["reason"]))


# ─────────────────────────── панели на кнопках ───────────────────────────
# Декоратор кнопки читает свою надпись один раз — при импорте кога, поэтому правка из
# «Текстов» попадала в кнопки только после рестарта. Этот проход перечитывает надпись
# каждый раз, когда вьюх собирается, — и новые посты, и экран `/выплаты` выходят уже свежими.
BTN_TEXTS = {
    "hzpay:fund": "pay_btn_fund",
    "hzpay:book": "pay_btn_book",
    "hzpay:topup": "pay_btn_topup",
    "hzpay:new": "pay_btn_new",
    "hzpay:mine": "pay_btn_mine",
    "hzpayp:setup": "pay_btn_setup",
    "hzpayp:banners": "pay_btn_banners",
    "hzpayp:set": "pay_btn_setfund",
}


def relabel(view: disnake.ui.View) -> disnake.ui.View:
    """Свежие надписи кнопок выплат: пустая правка не должна оставлять кнопку без слова."""
    for child in view.children:
        key = BTN_TEXTS.get(getattr(child, "custom_id", None) or "")
        if key and hasattr(child, "label"):
            child.label = (get_text(key, "") or child.label or "")[:80]
    return view


class BankView(disnake.ui.View):
    """Постоянная: висит под баннером фонда и после рестарта работает по custom_id.

    Надписи перечитываются при каждом сборе вьюхи (relabel), поэтому правка из веб-панели
    видна новым постам сразу. Старое сообщение в канале не переписывается: Discord сам держит
    те кнопки, что были положены, — обновить их можно «🖼 Перезалить баннеры».
    """

    def __init__(self):
        super().__init__(timeout=None)
        relabel(self)

    @disnake.ui.button(label=get_text("pay_btn_fund", "💸 Фонд выплат"),
                       style=disnake.ButtonStyle.success, custom_id="hzpay:fund", row=0)
    async def fund(self, button, inter: disnake.MessageInteraction):
        pass

    @disnake.ui.button(label=get_text("pay_btn_book", "📜 Книга выплат"),
                       style=disnake.ButtonStyle.secondary, custom_id="hzpay:book", row=0)
    async def book(self, button, inter: disnake.MessageInteraction):
        pass

    @disnake.ui.button(label=get_text("pay_btn_topup", "➕ Пополнить"),
                       style=disnake.ButtonStyle.primary, custom_id="hzpay:topup", row=0)
    async def topup(self, button, inter: disnake.MessageInteraction):
        pass


class ClaimsView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        relabel(self)

    @disnake.ui.button(label=get_text("pay_btn_new", "🧾 Подать заявку"), emoji="✍️",
                       style=disnake.ButtonStyle.primary, custom_id="hzpay:new", row=0)
    async def new(self, button, inter: disnake.MessageInteraction):
        pass

    @disnake.ui.button(label=get_text("pay_btn_mine", "📋 Мои заявки"),
                       style=disnake.ButtonStyle.secondary, custom_id="hzpay:mine", row=0)
    async def mine(self, button, inter: disnake.MessageInteraction):
        pass


class ReviewView(disnake.ui.View):
    def __init__(self, pid: str):
        super().__init__(timeout=None)
        self.add_item(disnake.ui.Button(
            label=get_text("pay_btn_accept", "Выплатить"), emoji="✅",
            style=disnake.ButtonStyle.success, custom_id=f"hzpay:ok:{pid}", row=0))
        self.add_item(disnake.ui.Button(
            label=get_text("pay_btn_reject", "Отклонить"), emoji="❌",
            style=disnake.ButtonStyle.danger, custom_id=f"hzpay:no:{pid}", row=0))


class PanelView(disnake.ui.View):
    """Экран `/выплаты`: собрать ветку, перезалить баннеры, выбрать роли, задать фонд.

    custom_id с префиксом hzpayp: — кнопки живого экрана, их ведёт этот вью. Кнопки каналов
    (hzpay:) ловит on_button_click, потому что они висят в чате после перезапуска бота.
    """

    def __init__(self, st: dict):
        super().__init__(timeout=900)
        relabel(self)
        self.st = st
        for key, ph in (("manage", get_text("pay_select_manage_ph",
                                            "👑 Выбери роль управления выплатами (до 3)")),
                        ("approve", get_text("pay_select_approve_ph",
                                             "🛡 Выбери роль приёмки выплат (до 3)"))):
            select = disnake.ui.RoleSelect(
                custom_id=f"hzpayp:role:{key}", placeholder=ph,
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

    @disnake.ui.button(label=get_text("pay_btn_setup", "🏗 Создать ветку выплат"),
                       style=disnake.ButtonStyle.primary, custom_id="hzpayp:setup", row=0)
    async def setup(self, button, inter: disnake.MessageInteraction):
        cog = _cog(inter)
        await inter.response.defer(ephemeral=True, with_message=True)
        if cog:
            await cog.build_branch(inter)

    @disnake.ui.button(label=get_text("pay_btn_banners", "🖼 Перезалить баннеры"),
                       style=disnake.ButtonStyle.secondary, custom_id="hzpayp:banners", row=0)
    async def banners(self, button, inter: disnake.MessageInteraction):
        cog = _cog(inter)
        await inter.response.defer(ephemeral=True, with_message=True)
        if cog:
            await cog.post_banners(inter)

    @disnake.ui.button(label=get_text("pay_btn_setfund", "⚖️ Задать фонд"), emoji="💸",
                       style=disnake.ButtonStyle.success, custom_id="hzpayp:set", row=1)
    async def set_fund(self, button, inter: disnake.MessageInteraction):
        await inter.response.send_modal(SetFundModal(int(self.st.get("balance") or 0)))


# ─────────────────────────── ког ───────────────────────────
class Payouts(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # ожидание скринов к заявке: user_id → (pid, msg_id, channel_id, deadline)
        self._pending_photos: dict = {}

    @commands.Cog.listener()
    async def on_ready(self):
        if not getattr(self.bot, "_payouts_view_added", False):
            self.bot.add_view(BankView())
            self.bot.add_view(ClaimsView())
            self.bot._payouts_view_added = True

    # ── команда ──
    @commands.slash_command(name="выплаты", description="Выплаты семьи: фонд, заявки, приёмка",
                            default_member_permissions=disnake.Permissions(manage_guild=True),
                            contexts=disnake.InteractionContextTypes(guild=True))
    async def payouts_panel(self, inter: disnake.ApplicationCommandInteraction):
        if not is_admin(inter):
            return await inter.response.send_message(embed=_deny(self.bot, inter.guild),
                                                     ephemeral=True)
        st = _load()
        rows = []
        for key, label in (("bank", "💸  фонд"), ("claims", "🧾  заявки"),
                           ("review", "✅  приёмка"), ("logs", "📜  логи")):
            cid = (st["channels"] or {}).get(key)
            ch = inter.guild.get_channel(int(cid)) if cid else None
            rows.append(f"{label} — " + (ch.mention if ch else _T(
                self.bot, "pay_channel_missing",
                "_нет — нажми «🏗 Создать ветку выплат» ниже_")))
        manage = " ".join(f"<@&{r}>" for r in _roles(st, "manage")) or \
            _T(self.bot, "pay_manage_none", "_не выбрана — выплатами правят только админы семьи_")
        approve = " ".join(f"<@&{r}>" for r in _roles(st, "approve")) or \
            _T(self.bot, "pay_approve_none",
               "_не выбрана — приёмка берёт роль управления_")
        waiting = sum(1 for p in (st["claims"] or {}).values()
                      if (p.get("status") or "wait") == "wait")
        sections = [
            (_T(self.bot, "pay_sec_channels_title", "🏗  КАНАЛЫ"), "\n".join(rows)),
            (_T(self.bot, "pay_sec_fund_title", "💸  ОСТАТОК ФОНДА"),
             _T(self.bot, "pay_sec_fund_text",
                "**{balance}**\n➕  пополнено {raised}  ·  💸  выплачено {paid}\n"
                "🧾  заявок {claims}  ·  📜  записей {moves}  ·  ⏳  ждёт {waiting}",
                balance=_n(st["balance"]), raised=_n(st["raised"]),
                paid=_n(st["paid"]), claims=st["seq"], moves=len(st["moves"]),
                waiting=waiting)),
            (_T(self.bot, "pay_sec_roles_title", "👥  РОЛИ"),
             _T(self.bot, "pay_sec_roles_text",
                "👑  Управление — {manage}\n🛡  Приёмка — {approve}",
                manage=manage, approve=approve)),
        ]
        emb = panel_embed(
            _T(self.bot, "pay_panel_title", T_PANEL_TITLE), sections, guild=inter.guild,
            color_key=ACCENT,
            intro=_T(self.bot, "pay_panel_intro", T_PANEL_INTRO))
        await inter.response.send_message(embed=emb, view=PanelView(st), ephemeral=True,
                                          file=_put_banner(emb, BANK_BANNER))

    # ── создание ветки ──
    async def build_branch(self, inter) -> None:
        guild = inter.guild
        if guild is None:
            return await inter.followup.send(
                embed=_warn(self.bot, None, "ТОЛЬКО НА СЕРВЕРЕ",
                            f"{'🏠'}  Выплаты живут на сервере семьи — "
                            "собирать ветку нужно там, в `/выплаты`."),
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
                                              reason="Выплаты семьи")
            st["channels"]["category"] = cat.id
            made.append(f"🗂 {cat.mention}")
        for key, name in CHANNELS.items():
            cid = int((st["channels"] or {}).get(key) or 0)
            ch = guild.get_channel(cid) if cid else None
            if ch is None:
                ch = await guild.create_text_channel(
                    name, category=cat, overwrites=private if key in ("review", "logs") else public,
                    topic=_topic(self.bot, key), reason="Выплаты семьи")
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
            _T(self.bot, "pay_done_text", T_DONE_TEXT, made=clip(note, 300)),
            "success", title=_T(self.bot, "pay_panel_title", T_PANEL_TITLE), guild=guild),
            ephemeral=True)

    async def _sync_perms(self, guild, st: dict) -> None:
        """Приёмка и логи — приватные: пускаем выбранные роли и админов семьи."""
        cfg = load_config()
        allow = set(_roles(st, "approve")) | set(_roles(st, "manage")) | \
            set(cfg.get("admin_role_ids") or [])
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
                await ch.edit(overwrites=ow, reason="Выплаты семьи: роли приёмки")
            except disnake.DiscordException:
                pass

    async def post_banners(self, inter) -> None:
        if inter.guild is None:
            return await inter.followup.send(
                embed=_warn(self.bot, None, "ТОЛЬКО НА СЕРВЕРЕ",
                            f"{'🏠'}  Баннеры кладутся в каналы выплат "
                            "на сервере семьи."),
                ephemeral=True)
        st = _load()
        done = await self._post_banners(inter.guild, st)
        star = "⭐"
        crown = "👑"
        house = "🏠"
        pencil = "✍️"
        if done:
            emb = note_embed(
                f"### {star}  Витрина выплат обновлена\n\n"
                f"{crown}  **Фонд** и **заявки** — объяснение сверху, баннер под текстом\n"
                f"{'🛡'}  **Приёмка** и **логи** — памятки для своих\n\n"
                f"> {house}  Старые карточки убраны, кнопки на местах.",
                "success", title=f"{star}  БАННЕРЫ ПЕРЕЗАЛИТЫ", guild=inter.guild)
        else:
            emb = _warn(self.bot, inter.guild, "ВЕТКИ ЕЩЁ НЕТ",
                        f"{pencil}  Сначала «🏗 Создать ветку выплат» — баннеры кладутся "
                        "в уже готовые каналы.")
        await inter.followup.send(embed=emb, ephemeral=True)

    async def _post_banners(self, guild, st: dict) -> bool:
        ch = {k: guild.get_channel(int((st["channels"] or {}).get(k) or 0)) for k in CHANNELS}
        if not ch["bank"] or not ch["claims"]:
            return False
        # Чищаем только витрины: карточки заявок в приёмке — это журнал, его не трогаем.
        for key in ("bank", "claims"):
            async for m in ch[key].history(limit=50):
                if m.author.id == guild.me.id:
                    try:
                        await m.delete()
                    except disnake.DiscordException:
                        pass
        bank_emb = card(_T(self.bot, "pay_bank_head", T_BANK_HEAD),
                        _T(self.bot, "pay_bank_text", T_BANK_TEXT), guild=guild, color_key=ACCENT)
        await ch["bank"].send(embed=bank_emb, view=BankView(),
                              file=_put_banner(bank_emb, BANK_BANNER))
        claims_emb = card(_T(self.bot, "pay_claims_head", T_CLAIMS_HEAD),
                          _T(self.bot, "pay_claims_text", T_CLAIMS_TEXT), guild=guild, color_key=ACCENT)
        await ch["claims"].send(embed=claims_emb, view=ClaimsView(),
                                 file=_put_banner(claims_emb, CLAIM_BANNER))
        # Приватные каналы: ключи текстов названы прямо здесь, иначе редактор «Тексты»
        # не найдёт их в исходнике и правка не дойдёт до канала.
        memos = (("review", card(_T(self.bot, "pay_review_head", T_REVIEW_HEAD),
                                 _T(self.bot, "pay_review_text", T_REVIEW_TEXT), guild=guild, color_key=ACCENT)),
                 ("logs", card(_T(self.bot, "pay_logs_head", T_LOGS_HEAD),
                               _T(self.bot, "pay_logs_text", T_LOGS_TEXT), guild=guild, color_key=ACCENT)))
        for key, memo in memos:
            c = ch[key]
            if c is None or await _has_bot_message(c, guild):
                continue
            await c.send(embed=memo)
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
        label = "Роль управления выплатами" if key == "manage" else "Роль приёмки выплат"
        names = " ".join(f"<@&{r}>" for r in st["roles"][key]) or \
            f"_{label.lower()} сброшена — выплатами правят только админы семьи_"
        crown = "👑"
        mod = "🛡"
        bits = "💸"
        pencil = "✍️"
        await inter.response.send_message(
            embed=note_embed(f"### {mod}  {label}\n{names}\n\n"
                             f"{bits}  Доступ к приёмке и логам выплат уже переставлен\n"
                             f"{pencil}  Управление пополняет фонд и правит остаток\n"
                             f"{crown}  Приёмка решает судьбу каждой заявки",
                             "success", title=f"{mod}  РОЛЬ ВЫБРАНА", guild=inter.guild),
            ephemeral=True)

    # ── фонд ──
    async def set_fund(self, inter, raw: str, note: str) -> None:
        if not is_admin(inter):
            return await inter.response.send_message(embed=_deny(self.bot, inter.guild),
                                                     ephemeral=True)
        value = _amount(raw)
        if value is None:
            return await inter.response.send_message(
                embed=_warn(self.bot, inter.guild, "НЕ ПОНЯЛ СУММУ",
                            f"💸  Остаток фонда пишут цифрами: "
                            "`15000` или `15к`."),
                ephemeral=True)
        st = _load()
        old = int(st["balance"])
        st["balance"] = value
        _move(st, "set", value - old, inter.author.id, note or "ручная корректировка фонда")
        _save(st)
        bits = "💸"
        pencil = "✍️"
        crown = "👑"
        await inter.response.send_message(
            embed=note_embed(f"### {bits}  {_n(old)}  →  {_n(value)}\n\n"
                             f"{pencil}  {note or 'без пояснения'}\n\n"
                             f"> {crown}  Фонд правит только руководство семьи.",
                             "success", title=f"{bits}  ФОНД ВЫПЛАТ", guild=inter.guild),
            ephemeral=True)
        await self._log(inter.guild, f"{bits}  Фонд выплат изменили: {_n(old)} → {_n(value)}",
                        inter.author.id, note or "ручная корректировка")

    async def top_up(self, inter, raw: str, note: str) -> None:
        st = _load()
        if not _allowed(inter, st, "manage"):
            return await inter.response.send_message(embed=_deny(self.bot, inter.guild),
                                                     ephemeral=True)
        value = _amount(raw)
        if value is None:
            return await inter.response.send_message(
                embed=_warn(self.bot, inter.guild, "НЕ ПОНЯЛ СУММУ",
                            f"{'🎁'}  Взнос пишут цифрами: "
                            "`50000` или `50к`."), ephemeral=True)
        st["balance"] = int(st["balance"]) + value
        st["raised"] = int(st["raised"]) + value
        _move(st, "topup", value, inter.author.id, note or "пополнение фонда выплат")
        _save(st)
        gift = "🎁"
        bits = "💸"
        book = "📜"
        await inter.response.send_message(
            embed=note_embed(f"### {gift}  +{_n(value)}\n\n"
                             f"{bits}  Остаток фонда: **{_n(st['balance'])}** · "
                             f"{_when(int(time.time()))}\n"
                             f"{book}  {note or 'взнос в фонд выплат'}",
                             "success", title=f"{gift}  ПОПОЛНЕНИЕ ФОНДА", guild=inter.guild),
            ephemeral=True)
        await self._log(inter.guild, f"{gift}  Пополнение фонда выплат: **{_n(value)}**",
                        inter.author.id, note or "взнос в фонд выплат")

    # ── заявки ──
    async def _answer(self, inter, emb: disnake.Embed) -> None:
        """Ответ туда, где его ещё можно отправить: defer'нутый слэш уходит followup'ом."""
        if inter.response.is_done():
            await inter.followup.send(embed=emb, ephemeral=True)
        else:
            await inter.response.send_message(embed=emb, ephemeral=True)

    async def file_claim(self, guild, author_id: int, nick: str, what: str, raw_amount: str,
                         raw_income: str, files: Optional[List[disnake.File]] = None) -> dict:
        """Записать заявку и положить карточку в приёмку.

        Общий вход для Discord (кнопка и модалка) и для веб-панели: права уже проверены
        до вызова, здесь остаются только поля формы, хроника и карточка.
        """
        st = _load()
        value = _amount(raw_amount)
        if value is None:
            return {"error": "sum"}
        review = guild.get_channel(int((st["channels"] or {}).get("review") or 0)) if guild else None
        if review is None:
            return {"error": "review"}
        st["seq"] = int(st.get("seq") or 0) + 1
        pid = str(st["seq"])
        pay = {"id": pid, "by": int(author_id), "nick": (nick or "без ника")[:40],
               "what": (what or "без описания")[:900], "amount": value,
               "in": _amount(raw_income), "ts": int(time.time()), "status": "wait",
               "photos": len(files) if files else 0}
        st["claims"][pid] = pay
        if len(st["claims"]) > MAX_CLAIMS:
            # Выкидываем только закрытые заявки: живая заявка без карточки осталась бы без приёмки.
            closed = sorted((p for p in st["claims"].values()
                             if p.get("status") != "wait"), key=lambda p: int(p["id"]))
            for old in closed[: len(st["claims"]) - MAX_CLAIMS]:
                st["claims"].pop(str(old["id"]), None)
        _save(st)
        card_msg = await review.send(embed=_claim_embed(self.bot, guild, pay),
                                     view=ReviewView(pid), files=files or [])
        return {"pid": pid, "value": value, "review": review, "card": card_msg,
                "photos": len(files or []), "balance": int(st["balance"])}

    async def submit(self, inter, nick: str, what: str, raw_amount: str, raw_income: str,
                     files: Optional[List[disnake.File]] = None) -> None:
        if getattr(getattr(inter, "author", None), "bot", False):
            return await self._answer(
                inter, _warn(self.bot, inter.guild, "ЭТО БОТ",
                             "🤖  Заявку подают люди."))
        res = await self.file_claim(inter.guild, int(inter.author.id), nick, what,
                                    raw_amount, raw_income, files)
        if res.get("error") == "sum":
            return await self._answer(
                inter, _warn(self.bot, inter.guild, "НЕ ПОНЯЛ СУММУ",
                             f"💸  Сумму выплаты пишут цифрами: "
                             "`15000` или `15к`."))
        if res.get("error") == "review":
            return await self._answer(
                inter, _warn(self.bot, inter.guild, "НЕТ КАНАЛА ПРИЁМКИ",
                             f"{'🛡'}  Приёмки ещё нет — на экране "
                             "`/выплаты` нажми «🏗 Создать ветку выплат», потом подавай заявку."))
        pid, review = res["pid"], res["review"]
        bits = "💸"
        pencil = "✍️"
        mod = "🛡"
        like = "👤"
        photo = "📎"
        lines = [f"### {pencil}  Заявка #{pid} ушла в {review.mention}\n",
                 f"{bits}  **{_n(res['value'])}** спишутся с фонда только после приёмки\n"
                 f"{mod}  Решение за ролью приёмки — о нём придёт **ЛС**\n"
                 f"{like}  Платим по нику из заявки — сверь его перед приёмкой\n"]
        if res["photos"]:
            lines.append(f"{photo}  **Скринов приложено:** {res['photos']}\n")
        else:
            self._pending_photos[int(inter.author.id)] = (
                pid, res["card"].id, int(inter.channel.id), time.time() + PHOTO_SECONDS)
            lines.append(f"{photo}  **Прикрепи скрины** ответом в этот канал — до 3 штук, "
                         f"жду {PHOTO_SECONDS} сек.\n")
        if res["balance"] < res["value"]:
            lines.append(f"\n{bits}  В фонде сейчас **{_n(res['balance'])}** — на эту заявку "
                         "не хватает: примут и фонд уйдёт в минус.")
        lines.append(f"\n> {pencil}  Следующее дело — той же кнопкой.")
        await self._answer(inter, note_embed("".join(lines), "success",
                                             title=f"{pencil}  ЗАЯВКА ОТПРАВЛЕНА",
                                             guild=inter.guild))

    async def decide(self, inter, pid: str, accept: bool, why: str = "") -> None:
        """Кнопка приёмки. Канал приватный, но перепроверяем роль здесь же: карточку могут
        переслать, а Discord не запрещает нажать кнопку тому, кто её видит."""
        st = _load()
        if not _allowed(inter, st, "approve"):
            return await inter.response.send_message(embed=_not_here(self.bot, inter.guild),
                                                     ephemeral=True)
        pay = (st["claims"] or {}).get(pid)
        if not pay:
            return await inter.response.send_message(
                embed=_warn(self.bot, inter.guild, "ЗАЯВКА НЕ НАЙДЕНА",
                            f"📜  Такая заявка уже стёрта из книги — "
                            "карточка в канале осталась старой."),
                ephemeral=True)
        if (pay.get("status") or "wait") != "wait":
            return await inter.response.send_message(
                embed=_warn(self.bot, inter.guild, "ЗАЯВКА ЗАКРЫТА",
                            f"💸  Заявку #{pid} уже решили — "
                            "второй раз деньги не снимаются."), ephemeral=True)
        pay["status"] = "ok" if accept else "no"
        pay["decided_by"] = int(inter.author.id)
        pay["decided_ts"] = int(time.time())
        if not accept:
            pay["why"] = (why or "без причины")[:300]
        short = False
        if accept:
            amount = int(pay["amount"])
            st["balance"] = int(st["balance"]) - amount
            st["paid"] = int(st["paid"]) + amount
            short = st["balance"] < 0
            _move(st, "pay", amount, int(pay["by"]),
                  f"#{pid} · {pay['nick']} · {pay['what'][:80]}")
        _save(st)
        if accept:
            await self._log(inter.guild,
                            f"💸  Выплачено по заявке **#{pid}** — "
                            f"**{_n(pay['amount'])}** → {pay['nick']}",
                            int(pay["by"]), clip(pay["what"], 200), balance=int(st["balance"]))
        await inter.response.edit_message(embed=_claim_embed(self.bot, inter.guild, pay),
                                          view=None)
        await self._tell(inter, pay, short)

    async def _tell(self, inter, pay: dict, short: bool = False) -> None:
        """ЛС автору заявки: принято или отклонено. Решение он узнаёт именно тут, в приёмку
        заглядывать ему некуда — канал виден только своей роли."""
        author = inter.guild.get_member(int(pay["by"])) if inter.guild else None
        ok = pay["status"] == "ok"
        warn = "⚠️"
        if author is None:
            print(f"💸 Выплата #{pay['id']}: автора заявки не найти в ЛС — "
                  f"<@{pay['by']}>, {pay['nick']}", flush=True)
            return
        if ok:
            text = _T(self.bot, "pay_ok_text", T_OK_TEXT, amount=_n(pay["amount"]),
                      id=pay["id"], what=clip(pay["what"], 200), nick=clip(pay["nick"], 40),
                      decided=pay.get("decided_by", 0))
            if short:
                text += (f"\n\n{warn}  Фонд ушёл в минус — скажи руководству семьи, "
                         "когда его пополнят.")
            title = _T(self.bot, "pay_ok_title", T_OK_TITLE)
        else:
            text = _T(self.bot, "pay_no_text", T_NO_TEXT, amount=_n(pay["amount"]),
                      id=pay["id"], what=clip(pay["what"], 200),
                      decided=pay.get("decided_by", 0), why=clip(pay.get("why", ""), 200))
            title = _T(self.bot, "pay_no_title", T_NO_TITLE)
        try:
            await author.send(embed=note_embed(text, "success" if ok else "error", title=title))
        except disnake.DiscordException:
            # закрытые ЛС: решение всё равно осталось в карточке и в логах
            print(f"💸 Выплата #{pay['id']}: ЛС автору не открылось — {pay['nick']}", flush=True)

    async def _log(self, guild, title: str, who: int, note: str,
                   balance: Optional[int] = None) -> None:
        st = _load()
        cid = int((st["channels"] or {}).get("logs") or 0)
        ch = guild.get_channel(cid) if (guild and cid) else None
        if ch is None:
            return
        member = "👤"
        when = "🕒"
        bits = "💸"
        emb = card(title, guild=guild, color_key=ACCENT)
        emb.description = (f"{note}\n\n"
                           f"{member}  <@{who}>\n"
                           f"{when}  {_when(int(time.time()))}\n"
                           + (f"{bits}  Остаток фонда: **{_n(balance)}**"
                              if balance is not None else ""))
        try:
            await ch.send(embed=emb)
        except disnake.DiscordException:
            pass

    # ── скрины к заявке: после модальной формы их прикладывают следующим сообщением ──
    @commands.Cog.listener()
    async def on_message(self, message: disnake.Message):
        if message.author.bot:
            return
        pending = self._pending_photos.get(int(message.author.id))
        if not pending:
            return
        pid, msg_id, channel_id, deadline = pending
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
            card_msg = await review.fetch_message(int(msg_id))
        except disnake.DiscordException:
            return
        files = []
        for att in images[:MAX_PHOTOS - len(card_msg.attachments)]:
            try:
                files.append(await att.to_file())
            except disnake.DiscordException:
                pass
        if not files:
            return
        try:
            # edit() возвращает сообщение, собранное из ответа Discord: только в нём
            # лежат и старые, и новые вложения (у прежнего объекта attachments не меняются)
            card_msg = await card_msg.edit(files=files)
        except disnake.DiscordException:
            return
        st = _load()
        pay = (st["claims"] or {}).get(pid)
        if pay:
            pay["photos"] = len(card_msg.attachments)
            _save(st)
        try:
            await message.delete()
        except disnake.DiscordException:
            pass
        try:
            await message.channel.send(
                embed=note_embed(f"📎  Скрины к заявке **#{pid}** прикреплены ({len(files)} шт.).",
                                 "success", title="📎  ФОТО ДОБАВЛЕНЫ", guild=message.guild))
        except disnake.DiscordException:
            pass

    # ── роутер кнопок ──
    @commands.Cog.listener()
    async def on_button_click(self, inter: disnake.MessageInteraction):
        cid = str(inter.data.custom_id or "")
        if not cid.startswith("hzpay:"):
            return
        st = _load()
        action = cid.split(":", 2)[1]

        if action in ("fund", "book"):
            if not _allowed(inter, st, "manage") and not _allowed(inter, st, "approve"):
                return await inter.response.send_message(embed=_deny(self.bot, inter.guild),
                                                         ephemeral=True)
            emb = (_fund_embed(self.bot, inter.guild, st) if action == "fund"
                   else _book_embed(self.bot, inter.guild, st))
            return await inter.response.send_message(embed=emb, ephemeral=True)
        if action == "topup":
            if not _allowed(inter, st, "manage"):
                return await inter.response.send_message(embed=_deny(self.bot, inter.guild),
                                                         ephemeral=True)
            return await inter.response.send_modal(TopUpModal())
        if action == "new":
            # Заявку подаёт любой: деньги за дело человек отчитывает сам, приёмка решает следом.
            return await inter.response.send_modal(ClaimModal())
        if action == "mine":
            return await inter.response.send_message(
                embed=_mine_embed(self.bot, st, inter.author.id, inter.guild), ephemeral=True)
        if action in ("ok", "no"):
            pid = cid.split(":", 2)[2]
            if action == "ok":
                return await self.decide(inter, pid, True)
            if not _allowed(inter, st, "approve"):
                return await inter.response.send_message(embed=_not_here(self.bot, inter.guild),
                                                         ephemeral=True)
            return await inter.response.send_modal(RejectModal(pid))


def _topic(bot, key: str) -> str:
    topics = {
        "bank": _T(bot, "pay_bank_topic", T_BANK_TOPIC),
        "claims": _T(bot, "pay_claims_topic", T_CLAIMS_TOPIC),
        "review": _T(bot, "pay_review_topic", T_REVIEW_TOPIC),
        "logs": _T(bot, "pay_logs_topic", T_LOGS_TOPIC),
    }
    return topics.get(key, _T(bot, "pay_default_topic", T_DEFAULT_TOPIC))


def setup(bot: commands.Bot):
    bot.add_cog(Payouts(bot))
