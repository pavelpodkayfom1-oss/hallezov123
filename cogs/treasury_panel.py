"""Казна семьи: банк, отчёты о тратах, приёмка и логи.

Одна кнопка `/казна` → «🏗 Создать ветку» поднимает категорию и четыре канала разом:
🏦 банк семьи (баннер + объяснение + кнопки, баланс видят только свои), 🧾 отчёты (форма),
✅ приёмка (роль решает «принять/отклонить») и 📜 логи (куда падает принятое).

Остаток живёт в treasury.json и меняется только из принятого отчёта — поэтому «молча
потратить» нельзя: каждая сумма оставляет запись в книге казны.
"""
import io
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

# ─────────────────────────── тексты казны ───────────────────────────
# Каждый текст вынесен константой, чтобы редактор «Тексты» в веб-панели видел настоящий
# дефолт: «Как в коде» возвращает именно эту строку, а не пустоту. `:имя:` внутри — токены
# эмодзи пака, brand.E подставляет живые эмодзи сервера. {скобки} — подстановки: их бот
# заполняет цифрами и упоминаниями, править их можно, удалять не стоит — строка тогда
# теряет число.


def _T(bot, key: str, default: str, **subs) -> str:
    """Текст казны: правка из панели → эмодзи пака → подстановки {имя} без риска падения."""
    text = brand.E(bot, get_text(key, default))
    for name, value in subs.items():
        text = text.replace("{" + name + "}", str(value))
    return text


T_PANEL_TITLE = ":red_crown:  КАЗНА СЕМЬИ"
T_PANEL_INTRO = (
    "*:red_crown:  Казна не пустеет молча  :red_crown:*\n"
    ":hz_house:  Одно нажатие поднимает ветку: банк, отчёты, приёмка и логи.\n"
    ":hz_pencil:  Дальше — выбрать роли и задать остаток, кнопки ниже."
)

T_BANK_TEXT = (
    "*:red_crown:  Казна не пустеет молча  :red_crown:*\n\n"
    "### :hz_bitsicon:  ЧТО ЗДЕСЬ\n"
    ":number1:  Общие деньги семьи: сборы, награбленное с мероприятий, подарки от друзей\n"
    ":number2:  Каждая трата проходит через **отчёт** и **приёмку**\n"
    ":number3:  Остаток видят только **свои** — управление казной и приёмка\n\n"
    "### :hz_pcicon:  КНОПКИ\n"
    ":hz_bitsicon:  **💰 Баланс казны** — сколько лежит сейчас\n"
    ":hz_book:  **📜 Книга казны** — кто, сколько и за что\n"
    ":hz_gifticon:  **➕ Пополнить** — записать взнос, он тоже остаётся в книге\n\n"
    "> :hz_house:  Отчёт подаётся в канале ниже. Деньги списываются **только после приёмки**."
)

T_REPORTS_TEXT = (
    "*:hz_pencil:  Потратил — отчитался  :hz_pencil:*\n\n"
    "### :hz_shoppingcart:  КАК СДАТЬ ОТЧЁТ\n"
    ":number1:  Нажми **«🧾 Подать отчёт»** — откроется форма\n"
    ":number2:  **Сумма** и **На что** — сколько ушло и на какую статью: мероприятие,\n"
    "_форму на сбор, транспорт, откат после рейда_\n"
    ":number3:  **Кому** и **Детали** — на кого потратили и всё, что стоит знать проверяющему\n"
    ":number4:  Перетащи чеки в поле **«Скрины»** и жми **Отправить**\n\n"
    "### 📎  СКРИНЫ\n"
    "Перетаскиваются прямо в форму отчёта, до 3 файлов: чек, скрин перевода,\n"
    "фото купленного. Они уходят в приёмку вместе с суммой — проверяющий видит их сразу.\n"
    "Если чека под рукой нет — прикрепи скрины ответом в этот канал в течение 2 минут\n"
    "после отправки, бот подтянет их к отчёту сам.\n\n"
    "### :hz_megaphone:  ЧТО ДАЛЬШЕ\n"
    "✅  Приняли → сумма снялась с баланса, запись ушла в **📜 логи казны**\n"
    "❌  Отклонили → баланс **не тронут**, тебе вернётся причина\n\n"
    "> :hz_warn:  Пиши как есть: :hz_book: казна сходится до последней монеты, и это\n"
    "> спокойнее, чем объясняться потом. :red_crown:"
)

T_REVIEW_TEXT = (
    "*:red_crown:  Приёмка отчётов  :red_crown:*\n\n"
    "### :hz_moderator:  КТО РЕШАЕТ\n"
    ":hz_moderatorpurple:  Роль, которой выдана приёмка, жмёт **«Принять»** или **«Отклонить»**\n"
    ":hz_book:  Без нужной роли кнопки молчат — это не баг, это казна\n\n"
    "### :hz_bitsicon:  ИСХОД ОТЧЁТА\n"
    "✅  **Принято** → деньги снимаются с баланса, отчёт уходит в **📜 логи казны**\n"
    "❌  **Отклонено** → баланс не трогается, автор получает причину\n\n"
    "> :hz_warn:  Отчёт принимается один раз: повторная кнопка уже ничего не снимет."
)

T_LOGS_TEXT = (
    "*:hz_book:  История денег семьи  :hz_book:*\n\n"
    "### :hz_trainicon:  ЧТО СЮДА ПАДАЕТ\n"
    ":hz_gifticon:  Каждое **пополнение** казны\n"
    ":hz_shoppingcart:  Каждая **принятая трата** — сумма, статья и кому\n"
    ":hz_bitsicon:  Остаток после записи\n\n"
    "### :hz_megaphone:  ПОРЯДОК В КАНАЛЕ\n"
    ":red_crown:  Тут только записи бота: обсуждение — в отчётах, вопросы — руководству\n\n"
    "> :hz_book:  Логи не чистят и не правят руками — по ним семью и проверяют."
)

T_BANK_TOPIC = ":red_crown:  Банк семьи — остаток казны и книга трат"
T_REPORTS_TOPIC = ":hz_shoppingcart:  Отчёты о тратах — сколько, на что и кому"
T_REVIEW_TOPIC = ":hz_moderator:  Приёмка отчётов — решает роль приёмки"
T_LOGS_TOPIC = ":hz_book:  Логи казны — принятые траты и пополнения"
T_DEFAULT_TOPIC = ":red_crown:  Казна семьи"

T_BALANCE_TITLE = ":red_crown:  БАЛАНС КАЗНЫ"
T_BALANCE_TEXT = (
    "### :hz_bitsicon:  {balance}\n\n"
    ":hz_gifticon:  Пополнено **{raised}**\n"
    ":hz_shoppingcart:  Потрачено **{spent}**\n"
    ":hz_book:  Записей в книге **{moves}**\n"
    ":hz_botv2:  Отчётов подано **{reports}**\n\n"
    "> :red_crown:  Деньги списываются только из принятого отчёта."
)
T_BOOK_TITLE = ":hz_book:  КНИГА КАЗНЫ"
T_BOOK_EMPTY = ":hz_book:  Книга чистая — трат ещё не было."
T_BOOK_NOTE = ":hz_bitsicon:  Остаток **{balance}** · последние 12 записей"
T_MINE_TITLE = ":hz_pencil:  МОИ ОТЧЁТЫ"
T_MINE_EMPTY = (
    ":hz_pencil:  Ты ещё не подавал отчётов.\n"
    ":hz_bitsicon:  Форма — кнопка «🧾 Подать отчёт» в канале отчётов."
)
T_MINE_NOTE = "⏳ ждёт приёмку · ✅ принято — деньги списаны · ❌ отклонён"
T_REPORT_TITLE = ":hz_shoppingcart:  ОТЧЁТ #{id}"
T_STATUS_OK = "Принято — сумма списана"
T_STATUS_NO = "Отклонено — баланс не тронут"
T_STATUS_WAIT = "Ждёт приёмки"
T_DONE_TEXT = (
    "### :red_crown:  Ветка казны готова\n\n"
    ":hz_house:  {made}\n\n"
    ":hz_pencil:  Осталось два шага — прямо в этом сообщении:\n"
    ":number1:  выбрать **роль управления** и **роль приёмки**\n"
    ":number2:  :hz_bitsicon:  задать остаток кнопкой **«⚖️ Задать остаток»**\n\n"
    "> :hz_house:  Баннеры, объяснения и кнопки уже лежат в каналах."
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
    member = getattr(inter, "author", None) or inter
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
    return _T(bot, "kazna_bank_text", T_BANK_TEXT)


def _reports_text(bot) -> str:
    return _T(bot, "kazna_reports_text", T_REPORTS_TEXT)


def _review_text(bot) -> str:
    return _T(bot, "kazna_review_text", T_REVIEW_TEXT)


def _logs_text(bot) -> str:
    return _T(bot, "kazna_logs_text", T_LOGS_TEXT)


# ─────────────────────────── карточки ───────────────────────────
def _balance_embed(bot, guild, st: dict) -> disnake.Embed:
    emb = card(_T(bot, "kazna_balance_title", T_BALANCE_TITLE), guild=guild)
    emb.description = _T(bot, "kazna_balance_text", T_BALANCE_TEXT,
                         balance=_n(st["balance"]), raised=_n(st["raised"]),
                         spent=_n(st["spent"]), moves=len(st["moves"]), reports=st["seq"])
    return emb


def _book_embed(bot, guild, st: dict) -> disnake.Embed:
    gift = _e(bot, "hz_gifticon", "➕")
    cart = _e(bot, "hz_shoppingcart", "🧾")
    bits = _e(bot, "hz_bitsicon", "⚖️")
    rows = []
    for m in list(reversed(st["moves"]))[:12]:
        icon = gift if m["kind"] == "topup" else (cart if m["kind"] == "spend" else bits)
        sign = "+" if m["kind"] == "topup" else ("−" if m["kind"] == "spend" else "=")
        rows.append([f"{icon}  {sign}{_n(m['amount'])}",
                     f"{clip(m['note'], 90)}\n<@{m['by']}> · {_when(m['ts'])} · "
                     f"остаток **{_n(m['bal'])}**"])
    return list_embed(_T(bot, "kazna_book_title", T_BOOK_TITLE), rows, guild=guild, inline=False,
                      empty=_T(bot, "kazna_book_empty", T_BOOK_EMPTY),
                      note=_T(bot, "kazna_book_note", T_BOOK_NOTE, balance=_n(st["balance"])))


def _report_embed(bot, guild, rep: dict) -> disnake.Embed:
    status = rep.get("status") or "wait"
    icon = {"ok": "✅", "no": "❌", "wait": "⏳"}.get(status, "⏳")
    words = {"ok": _T(bot, "kazna_status_ok", T_STATUS_OK),
             "no": _T(bot, "kazna_status_no", T_STATUS_NO),
             "wait": _T(bot, "kazna_status_wait", T_STATUS_WAIT)}
    word = words.get(status, words["wait"])
    bits = _e(bot, "hz_bitsicon", "💎")
    pencil = _e(bot, "hz_pencil", "✍️")
    like = _e(bot, "hz_membericon", "👤")
    meg = _e(bot, "hz_megaphone", "📢")
    book = _e(bot, "hz_book", "📖")
    emb = card(_T(bot, "kazna_report_title", T_REPORT_TITLE, id=rep["id"]), guild=guild,
               color_key={"ok": "success_color", "no": "error_color"}.get(status, "embed_color"))
    emb.description = (
        f"### {bits}  {_n(rep['amount'])}\n\n"
        f"{pencil}  **{_T(bot, 'kazna_report_purpose', 'На что')}** — {clip(rep['purpose'], 200)}\n"
        f"{like}  **{_T(bot, 'kazna_report_people', 'Кому')}** — {clip(rep['people'], 200)}\n\n"
        f"{icon}  **{word}**"
    )
    if rep.get("photos"):
        emb.description += (f"\n📎  **{_T(bot, 'kazna_report_photos', 'Скринов')}** — "
                            f"{int(rep['photos'])}")
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
    mine = [r for r in (st["reports"] or {}).values() if int(r["by"]) == int(uid)]
    rows = []
    for rep in sorted(mine, key=lambda r: -int(r["id"]))[:12]:
        icon = {"wait": "⏳", "ok": "✅", "no": "❌"}.get(rep.get("status") or "wait", "⏳")
        rows.append([f"{icon}  #{rep['id']} · {_n(rep['amount'])}",
                     f"{clip(rep['purpose'], 60)}\n{_when(rep['ts'])}"])
    return list_embed(_T(bot, "kazna_mine_title", T_MINE_TITLE), rows, guild=guild, inline=False,
                      empty=_T(bot, "kazna_mine_empty", T_MINE_EMPTY),
                      note=_T(bot, "kazna_mine_note", T_MINE_NOTE))


# ─────────────────────────── модалки ───────────────────────────
class ReportModal(disnake.ui.Modal):
    """Форма отчёта: Discord даёт поле с файлом и в модалке (ui.FileUpload, тип 19),
    поэтому скрины перетаскиваются туда же, где сумма, а не отдельной командой."""

    def __init__(self):
        super().__init__(
            title=get_text("kazna_modal_report_title", "🧾 Отчёт о трате"),
            custom_id="hzkazm:report",
            components=[
                disnake.ui.TextInput(
                    label=get_text("kazna_modal_amount_label", "Сумма"), custom_id="amount",
                    placeholder=get_text("kazna_modal_amount_ph", "15 000"), max_length=16),
                disnake.ui.TextInput(
                    label=get_text("kazna_modal_purpose_label", "На что ушли"), custom_id="purpose",
                    placeholder=get_text("kazna_modal_purpose_ph", "Форма на сбор семьи"),
                    max_length=120),
                disnake.ui.TextInput(
                    label=get_text("kazna_modal_people_label", "Кому и на сколько человек"),
                    custom_id="people",
                    placeholder=get_text("kazna_modal_people_ph", "Всему отряду — 12 человек"),
                    max_length=160),
                disnake.ui.TextInput(
                    label=get_text("kazna_modal_detail_label", "Детали"), custom_id="detail",
                    required=False, style=disnake.TextInputStyle.paragraph,
                    placeholder=get_text("kazna_modal_detail_ph",
                                         "Где брали, кто платил, почему столько"),
                    max_length=800),
                disnake.ui.Label(
                    # цифра в надписи = MAX_PHOTOS: если менять лимит, правь и здесь
                    get_text("kazna_modal_screens_label", "Скрины (до 3)"),
                    disnake.ui.FileUpload(custom_id="photos", min_values=0,
                                          max_values=MAX_PHOTOS, required=False),
                    description=get_text("kazna_modal_screens_hint",
                                         "Перетащи чек или скрин перевода — уйдут в приёмку "
                                         "вместе с отчётом")),
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
        await cog.submit(inter, str(inter.text_values["amount"]),
                         str(inter.text_values["purpose"]), str(inter.text_values["people"]),
                         str(inter.text_values.get("detail") or ""), files)


class TopUpModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(
            title=get_text("kazna_modal_topup_title", "🎁 Пополнить казну"),
            custom_id="hzkazm:topup",
            components=[
                disnake.ui.TextInput(
                    label=get_text("kazna_modal_amount_label", "Сумма"), custom_id="amount",
                    placeholder=get_text("kazna_modal_topup_amount_ph", "50 000"), max_length=16),
                disnake.ui.TextInput(
                    label=get_text("kazna_modal_topup_note_label", "Откуда"), custom_id="note",
                    placeholder=get_text("kazna_modal_topup_note_ph", "Взнос семьи за неделю"),
                    max_length=200),
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
            title=get_text("kazna_modal_setbal_title", "💰 Остаток казны"),
            custom_id="hzkazm:setbal",
            components=[
                disnake.ui.TextInput(
                    label=get_text("kazna_modal_setbal_label", "Новый остаток"), custom_id="amount",
                    placeholder=_n(current), max_length=16),
                disnake.ui.TextInput(
                    label=get_text("kazna_modal_setbal_note_label", "Почему меняем"),
                    custom_id="note",
                    placeholder=get_text("kazna_modal_setbal_note_ph",
                                         "Перенесли остаток из старого учёта"), max_length=200),
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
            title=get_text("kazna_modal_reject_title", "🛡 Почему отклоняем"),
            custom_id=f"hzkazm:reject:{rid}",
            components=[
                disnake.ui.TextInput(
                    label=get_text("kazna_modal_reject_label", "Причина отказа"),
                    custom_id="reason", style=disnake.TextInputStyle.paragraph,
                    placeholder=get_text("kazna_modal_reject_ph",
                                         "Нет чека / сумма не сходится с деталями"),
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
    """Постоянная: висит под баннером банка и после рестарта работает по custom_id.

    Надписи кнопок берутся из текстов панели, когда ког загружается: правка из веб-панели
    видна новым постам и сообщениям после рестарта — Discord сам держит старые кнопки.
    """

    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label=get_text("kazna_btn_balance", "💰 Баланс казны"),
                       style=disnake.ButtonStyle.success, custom_id="hzkaz:balance", row=0)
    async def balance(self, button, inter: disnake.MessageInteraction):
        pass

    @disnake.ui.button(label=get_text("kazna_btn_book", "📜 Книга казны"),
                       style=disnake.ButtonStyle.secondary, custom_id="hzkaz:book", row=0)
    async def book(self, button, inter: disnake.MessageInteraction):
        pass

    @disnake.ui.button(label=get_text("kazna_btn_topup", "➕ Пополнить"),
                       style=disnake.ButtonStyle.primary, custom_id="hzkaz:topup", row=0)
    async def topup(self, button, inter: disnake.MessageInteraction):
        pass


class ReportsView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label=get_text("kazna_btn_new", "🧾 Подать отчёт"), emoji="✍️",
                       style=disnake.ButtonStyle.primary, custom_id="hzkaz:new", row=0)
    async def new(self, button, inter: disnake.MessageInteraction):
        pass

    @disnake.ui.button(label=get_text("kazna_btn_mine", "📋 Мои отчёты"),
                       style=disnake.ButtonStyle.secondary, custom_id="hzkaz:mine", row=0)
    async def mine(self, button, inter: disnake.MessageInteraction):
        pass


class ReviewView(disnake.ui.View):
    def __init__(self, rid: str):
        super().__init__(timeout=None)
        self.add_item(disnake.ui.Button(
            label=get_text("kazna_btn_accept", "Принять"), emoji="✅",
            style=disnake.ButtonStyle.success, custom_id=f"hzkaz:ok:{rid}", row=0))
        self.add_item(disnake.ui.Button(
            label=get_text("kazna_btn_reject", "Отклонить"), emoji="❌",
            style=disnake.ButtonStyle.danger, custom_id=f"hzkaz:no:{rid}", row=0))


class PanelView(disnake.ui.View):
    """Экран `/казна`: собрать ветку, перезалить баннеры, выбрать роли, задать остаток.

    custom_id с префиксом hzkazp: — кнопки живого экрана, их ведёт этот вью. Кнопки каналов
    (hzkaz:) ловит on_button_click, потому что они висят в чате после перезапуска бота.
    """

    def __init__(self, st: dict):
        super().__init__(timeout=900)
        self.st = st
        for key, ph_key, ph_default in (
                ("manage", "kazna_select_manage_ph", "👑 Выбери роль управления казной (до 3)"),
                ("approve", "kazna_select_approve_ph", "🛡 Выбери роль приёмки отчётов (до 3)")):
            select = disnake.ui.RoleSelect(
                custom_id=f"hzkazp:role:{key}", placeholder=get_text(ph_key, ph_default),
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

    @disnake.ui.button(label=get_text("kazna_btn_setup", "🏗 Создать ветку казны"),
                       style=disnake.ButtonStyle.primary, custom_id="hzkazp:setup", row=0)
    async def setup(self, button, inter: disnake.MessageInteraction):
        cog = _cog(inter)
        await inter.response.defer(ephemeral=True, with_message=True)
        if cog:
            await cog.build_branch(inter)

    @disnake.ui.button(label=get_text("kazna_btn_banners", "🖼 Перезалить баннеры"),
                       style=disnake.ButtonStyle.secondary, custom_id="hzkazp:banners", row=0)
    async def banners(self, button, inter: disnake.MessageInteraction):
        cog = _cog(inter)
        await inter.response.defer(ephemeral=True, with_message=True)
        if cog:
            await cog.post_banners(inter)

    @disnake.ui.button(label=get_text("kazna_btn_setbal", "⚖️ Задать остаток"), emoji="💰",
                       style=disnake.ButtonStyle.success, custom_id="hzkazp:set", row=1)
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
        sec_bank = _T(self.bot, "kazna_sec_bank_title", ":hz_bitsicon:  БАНК СЕМЬИ")
        sec_reports = _T(self.bot, "kazna_sec_reports_title", ":hz_pencil:  ОТЧЁТЫ")
        sec_review = _T(self.bot, "kazna_sec_review_title", ":hz_moderator:  ПРИЁМКА")
        sec_logs = _T(self.bot, "kazna_sec_logs_title", ":hz_book:  ЛОГИ КАЗНЫ")
        sections = []
        for key, title in (("bank", sec_bank), ("reports", sec_reports),
                           ("review", sec_review), ("logs", sec_logs)):
            cid = (st["channels"] or {}).get(key)
            ch = inter.guild.get_channel(int(cid)) if cid else None
            sections.append((title, ch.mention if ch else _T(
                self.bot, "kazna_channel_missing", "_нет — нажми «🏗 Создать ветку казны» ниже_")))
        manage = " ".join(f"<@&{r}>" for r in _roles(st, "manage")) or \
            _T(self.bot, "kazna_manage_none", "_не выбрана — казной правят только админы семьи_")
        approve = " ".join(f"<@&{r}>" for r in _roles(st, "approve")) or \
            _T(self.bot, "kazna_approve_none", "_не выбрана — приёмка берёт роль управления_")
        sections.append((_T(self.bot, "kazna_sec_balance_title", ":hz_bitsicon:  ОСТАТОК КАЗНЫ"),
                         _T(self.bot, "kazna_sec_balance_text",
                            "**{balance}**\n:hz_gifticon:  собрано {raised} · "
                            ":hz_pencil:  отчётов {reports} · :hz_book:  записей {moves}",
                            balance=_n(st["balance"]), raised=_n(st["raised"]),
                            reports=st["seq"], moves=len(st["moves"]))))
        sections.append((_T(self.bot, "kazna_sec_manage_title",
                            ":hz_moderatorpurple:  УПРАВЛЕНИЕ КАЗНОЙ"), manage))
        sections.append((_T(self.bot, "kazna_sec_approve_title",
                            ":hz_moderator:  ПРИЁМКА ОТЧЁТОВ"), approve))
        emb = panel_embed(
            _T(self.bot, "kazna_panel_title", T_PANEL_TITLE), sections, guild=inter.guild,
            intro=_T(self.bot, "kazna_panel_intro", T_PANEL_INTRO))
        await inter.response.send_message(embed=emb, view=PanelView(st), ephemeral=True,
                                          file=_put_banner(emb, BANK_BANNER))

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
        await inter.followup.send(embed=note_embed(
            _T(self.bot, "kazna_done_text", T_DONE_TEXT, made=clip(note, 300)),
            "success", title=_T(self.bot, "kazna_panel_title", T_PANEL_TITLE), guild=guild),
            ephemeral=True)

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

    async def file_report(self, guild, author_id: int, raw_amount: str, purpose: str,
                          people: str, detail: str,
                          files: Optional[List[disnake.File]] = None) -> dict:
        """Записать отчёт и положить карточку в приёмку.

        Общий вход для Discord (кнопка и модалка) и для веб-панели: права проверяются
        до вызова, здесь остаются только сумма, хроника и карточка.
        """
        st = _load()
        value = _amount(raw_amount)
        if value is None:
            return {"error": "sum"}
        review = guild.get_channel(int((st["channels"] or {}).get("review") or 0)) if guild else None
        if review is None:
            return {"error": "review"}
        st["seq"] = int(st.get("seq") or 0) + 1
        rid = str(st["seq"])
        rep = {"id": rid, "by": int(author_id), "amount": value,
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
        review_msg = await review.send(embed=_report_embed(self.bot, guild, rep),
                                       view=ReviewView(rid), files=files or [])
        return {"rid": rid, "value": value, "review": review, "card": review_msg,
                "photos": len(files or [])}

    def reporters(self, guild) -> dict:
        """Кто имеет право подать отчёт: управление казной плюс админы семьи.

        Панель спрашивает это не для красоты — список «кто подаёт» должен совпадать с теми,
        кого пропустит проверка прав, иначе форма обещает человеку отчёт, которого он не сдаст.
        """
        st = _load()
        want = set(_roles(st, "manage"))
        rows = [{"id": str(m.id), "name": m.display_name} for m in guild.members
                if not m.bot and (is_admin(m) or any(r.id in want for r in m.roles))]
        rows.sort(key=lambda r: r["name"].lower())
        return {"rows": rows[:60]}

    async def panel_report(self, member, raw_amount: str, purpose: str, people: str,
                           detail: str, photos: List[tuple]) -> dict:
        """Отчёт из веб-панели: те же права и та же карточка, только без взаимодействия.

        `photos` — список (имя, mime, байты): на 256 МБ хостинга файл в Discord уходит прямо
        из памяти, писать скрины на диск незачем.
        """
        st = _load()
        if not _allowed(member, st, "manage"):
            return {"error": "Отчёт подаёт управление казной — роль выдаёт экран `/казна`."}
        files = []
        for name, mime, data in photos[:MAX_PHOTOS]:
            if not (mime or "").startswith("image/"):
                return {"error": f"«{name}» — не картинка, прикладывай png или jpg."}
            if len(data) > MAX_PHOTO_MB * 1024 * 1024:
                return {"error": f"«{name}» больше {MAX_PHOTO_MB} МБ — сожми его."}
            files.append(disnake.File(io.BytesIO(data), filename=str(name)[:80] or "screen.png"))
        res = await self.file_report(member.guild, int(member.id), raw_amount, purpose,
                                     people, detail, files)
        if res.get("error") == "sum":
            return {"error": "Сумму пишут цифрами: 15000 или 15к."}
        if res.get("error") == "review":
            return {"error": "Канала приёмки ещё нет — собери ветку в `/казна`."}
        return {"ok": True, "rid": res["rid"], "amount": _n(res["value"]),
                "review": res["review"].name, "photos": res["photos"]}

    async def submit(self, inter, raw_amount: str, purpose: str, people: str, detail: str,
                     files: Optional[List[disnake.File]] = None) -> None:
        st = _load()
        if not _allowed(inter, st, "manage"):
            return await self._answer(inter, _need_manage(self.bot, inter.guild))
        res = await self.file_report(inter.guild, int(inter.author.id), raw_amount, purpose,
                                     people, detail, files)
        if res.get("error") == "sum":
            return await self._answer(
                inter, _warn(self.bot, inter.guild, "НЕ ПОНЯЛ СУММУ",
                             f"{_e(self.bot, 'hz_shoppingcart', '🛒')}  Сумму траты пишут цифрами: "
                             "`15000` или `15к`."))
        if res.get("error") == "review":
            return await self._answer(
                inter, _warn(self.bot, inter.guild, "НЕТ КАНАЛА ПРИЁМКИ",
                             f"{_e(self.bot, 'hz_moderator', '🛡')}  Приёмки ещё нет — на экране "
                             "`/казна` нажми «🏗 Создать ветку казны», потом подавай отчёт."))
        rid, value, review = res["rid"], res["value"], res["review"]
        meg = _e(self.bot, "hz_megaphone", "📢")
        pencil = _e(self.bot, "hz_pencil", "✍️")
        bits = _e(self.bot, "hz_bitsicon", "💎")
        mod = _e(self.bot, "hz_moderator", "🛡")
        photo = "📎"
        if res["photos"]:
            photos = f"{photo}  **Скринов приложено:** {res['photos']}\n"
            tail = f"> {pencil}  Следующая трата — той же кнопкой."
        else:
            PHOTO_SECONDS = 120
            self._pending_photos[int(inter.author.id)] = (
                rid, res["card"].id, int(inter.channel.id), time.time() + PHOTO_SECONDS)
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
            # edit() возвращает сообщение, собранное из ответа Discord: только в нём
            # лежат и старые, и новые вложения (у прежнего объекта attachments не меняются)
            review_msg = await review_msg.edit(files=files)
        except disnake.DiscordException:
            return
        st = _load()
        rep = (st["reports"] or {}).get(rid)
        if rep:
            rep["photos"] = len(review_msg.attachments)
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
    topics = {
        "bank": _T(bot, "kazna_bank_topic", T_BANK_TOPIC),
        "reports": _T(bot, "kazna_reports_topic", T_REPORTS_TOPIC),
        "review": _T(bot, "kazna_review_topic", T_REVIEW_TOPIC),
        "logs": _T(bot, "kazna_logs_topic", T_LOGS_TOPIC),
    }
    return topics.get(key, _T(bot, "kazna_default_topic", T_DEFAULT_TOPIC))


def setup(bot: commands.Bot):
    bot.add_cog(Treasury(bot))
