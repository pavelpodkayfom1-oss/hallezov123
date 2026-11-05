"""Жалобы на игроков: публичная форма, приватная приёмка и итог в ЛС.

Схема такая: на сервере поднимается ветка из двух каналов. В первом — пост с объяснением и
баннером под текстом, кнопка «Подать жалобу» есть у всех. Форма спрашивает ник и статик
нарушителя, что он сделал и когда это было, а скрины перетаскиваются прямо в неё. Жалоба
уходит во второй канал — его видят только приёмка и админы, там жмут «Одобрить» или
«Отклонить» с причиной. Человек же узнаёт итог в ЛС: без ЛС он бы искал решение в канале,
куда ему нельзя.

Всё живёт в complaints.json ПО СЕРВЕРАМ: у каждого сервера своя ветка, своя роль приёмки и
свой журнал, поэтому жалобы одного сервера не видны другому. Файл лежит в корне бота, а
значит попадает в снимки состояния (state_backup берёт все *.json) и откатывается через
`/reverse` вместе с остальными настройками.
"""
import json
import os
import re
import time
from typing import List, Optional

import disnake
from disnake.ext import commands

from utils import brand, cdn_assets, guild_cfg
from utils.checks import get_text, is_admin, load_config
from utils.embeds import card, clip, list_embed, note_embed, panel_embed

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, "complaints.json")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

HALL_BANNER = "complaints_hall.jpg"
REVIEW_BANNER = "complaints_review.jpg"
# Свой графитовый акцент: с красными карточками набора и серыми выплаты жалобы читались бы
# как ещё одна финансовая ветка, а это суд, а не касса.
ACCENT = "cmp_color"

CATEGORY = "ЖАЛОБЫ СЕМЬИ"
CHANNELS = {
    "hall": "подать-жалобу",
    "review": "приёмка-жалоб",
}
MAX_CASES = 200
# Скринов — до трёх и только картинками: приёмка читает их прямо с карточки.
MAX_PHOTOS = 3
MAX_PHOTO_MB = 8
# Сколько секунд ждём скрины ответом в канале, если в форму ничего не перетащили.
PHOTO_SECONDS = 120

# ─────────────────────────── тексты жалоб ───────────────────────────
# Каждый текст — константа, чтобы редактор «Тексты» в веб-панели видел настоящий дефолт
# и «Вернуть как в коде» возвращал живую надпись, а не пустоту. `:имя:` — токены эмодзи
# пака, brand.E подставляет их живым эмодзи; {скобки} заполняет бот.


def _T(bot, key: str, default: str, **subs) -> str:
    text = brand.E(bot, get_text(key, default))
    for name, value in subs.items():
        text = text.replace("{" + name + "}", str(value))
    return text


def _e(bot, name: str, fallback: str = "") -> str:
    return brand.emoji(bot, name, fallback)


T_PANEL_TITLE = ":hz_shield:  ЖАЛОБЫ НА ИГРОКОВ"
T_PANEL_INTRO = (
    "*:hz_shield:  Жалоба — не сплетня: её читают только свои, а итог приходит автору в ЛС  :hz_shield:*\n"
    "Подниму два канала: форма для всех и приёмка для своих."
)

T_HALL_HEAD = ":hz_warn:  ЖАЛОБА НА ИГРОКА"
T_REVIEW_HEAD = ":hz_shield:  ПРИЁМКА ЖАЛОБ"

T_HALL_TEXT = (
    "### Жалоба доходит до тех, кто решает\n\n"
    ":hz_pencil: **Раз.** Пишешь ник и статик того, на кого жалуешься.\n"
    "**Два.** Описываешь, что человек сделал, и когда это было.\n"
    "**Три.** Скрины перетаскиваешь прямо в форму :hz_merchicon: — до трёх.\n\n"
    "> **Скрины дают куда больше шансов.** Без них остаётся только твоё слово,\n"
    "> и приёмка не сможет ничего проверить.\n\n"
    "*Жалобу видит только приёмка. Итог — в ЛС.*"
)

T_REVIEW_TEXT = (
    "### Решает только своя роль\n\n"
    "> Без роли приёмки этот канал даже не появляется в списке.\n\n"
    "**Одобрить** :hz_bluetick: — нарушение подтверждено, автору уходит ЛС с итогом\n"
    "**Отклонить** :hz_moderatorroleicon: — автору уходит ЛС с причиной\n\n"
    "Кнопки живые, но ровно один раз: решённую жалобу не переиграть."
)

T_HALL_TOPIC = ":hz_pencil:  Жалоба: ник, статик, что сделал, когда было"
T_REVIEW_TOPIC = ":hz_shield:  Приёмка: решает только своя роль"
T_DEFAULT_TOPIC = ":hz_shield:  Жалобы на игроков"

T_CASE_TITLE = ":hz_warn:  ЖАЛОБА #{id}"
T_FIELD_NICK = "Ник нарушителя"
T_FIELD_STATIC = "Статик"
T_FIELD_WHEN = "Когда было"
T_FIELD_WHAT = "Что сделал"
T_FIELD_PHOTOS = "Скринов"
T_STATUS_OK = "Нарушение подтверждено"
T_STATUS_NO = "Ход жалобе не дан"
T_STATUS_WAIT = "Ждёт решения приёмки"

T_BTN_OPEN = "Подать жалобу"
T_BTN_ACCEPT = "Одобрить"
T_BTN_REJECT = "Отклонить"
T_BTN_SETUP = "Создать ветку жалоб"
T_BTN_BANNERS = "Перезалить баннеры"
T_BTN_BOOK = "Журнал жалоб"

T_MODAL_TITLE = "Жалоба на игрока"
T_MODAL_NICK = "Ник нарушителя"
T_MODAL_NICK_PH = "Как его зовут в игре"
T_MODAL_STATIC = "Статик нарушителя"
T_MODAL_STATIC_PH = "Цифры, например 12345 · можно пропустить"
T_MODAL_WHAT = "Что он сделал"
T_MODAL_WHAT_PH = ("Опиши по делу: читерил, оскорблял, сливал позицию, портил технику — "
                   "когда, где и кто ещё это видел")
T_MODAL_WHEN = "Когда было"
T_MODAL_WHEN_PH = "Вчера около 21:30 · или 04.11"
T_MODAL_SHOTS = "Скрины (до 3)"
T_MODAL_SHOTS_HINT = ("Приложи скрины — с ними жалобе куда больше шансов: "
                      "их видно прямо на карточке в приёмке")
T_MODAL_REJECT_TITLE = "Почему жалобе не дан ход"
T_MODAL_REJECT_PH = "Что не так с жалобой: нет доказательств, не тот игрок, разбор уже был"

T_OK_TITLE = ":hz_bluetick:  ЖАЛОБА ПРИНЯТА"
T_OK_TEXT = (
    "### :hz_shield:  Приёмка подтвердила нарушение\n\n"
    "Твоя жалоба **#{id}** на **{nick}** разобрана и **принята**.\n\n"
    "> Решение по игроку принимает руководство семьи — его тебе не сообщаем,\n"
    "> но жалоба не осталась без хода.\n\n"
    "Решил <@{decided}> · {when}"
)
T_NO_TITLE = ":hz_moderatorroleicon:  ЖАЛОБЕ НЕ ДАН ХОД"
T_NO_TEXT = (
    "### :hz_warn:  Приёмка не увидела нарушения\n\n"
    "Жалоба **#{id}** на **{nick}** закрыта.\n\n"
    "> Причина: {why}\n\n"
    "Если появятся скрины или новые подробности — подай жалобу той же кнопкой,\n"
    "разбор не считается отказом навсегда.\n\n"
    "Решил <@{decided}> · {when}"
)
T_SENT_TITLE = ":hz_shield:  ЖАЛОБА ОТПРАВЛЕНА"
T_SENT_TEXT = (
    "### :hz_pencil:  Жалоба #{id} ушла в {review}\n\n"
    "Её видят только приёмка и админы {lock}\n"
    "Ник и статик из жалобы сверят с игроком — напиши их верно {warn}\n"
    "Итог придёт **в ЛС**, в канал тебе заглядывать некуда {bluetick}\n"
)
T_SENT_WITH_SHOTS = "Скринов приложено: **{n}** — это уже серьёзный аргумент {merch}\n"
T_SENT_NO_SHOTS = ("**Прикрепи скрины** ответом в этот канал — до 3 штук, жду {sec} сек {merch}\n"
                   "> Без скринов остаётся только твоё слово.\n")
T_DONE_TEXT = "Ветка жалоб собрана: {made}"
T_BOOK_TITLE = ":hz_book:  ЖУРНАЛ ЖАЛОБ"
T_BOOK_EMPTY = ":hz_book:  Жалоб ещё не было — на сервере тихо."
T_BOOK_NOTE = "Последние 12 · всего **{total}**, ждут решения **{wait}**"
T_DENY = "Это приёмка жалоб: тут решают судьбу жалоб только свои."
T_NOT_HERE = "Жалобу решает роль приёмки — её выдает руководство семьи."

# ─────────────────────────── состояние по серверам ───────────────────────────


def _all() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def _key(gid) -> str:
    try:
        return str(int(gid or 0))
    except (TypeError, ValueError):
        return "0"


def _srv(gid) -> dict:
    """Настройки одного сервера: чужие каналы и чужой журнал сюда не попадают."""
    one = _all().get(_key(gid)) or {}
    if not isinstance(one, dict):
        one = {}
    out = {"channels": {}, "roles": {"review": []}, "seq": 0, "cases": {}}
    for k in ("channels", "cases"):
        if isinstance(one.get(k), dict):
            out[k] = dict(one[k])
    if isinstance(one.get("roles"), dict) and isinstance(one["roles"].get("review"), list):
        out["roles"]["review"] = [int(r) for r in one["roles"]["review"] if str(r or "").strip()]
    try:
        out["seq"] = int(one.get("seq") or 0)
    except (TypeError, ValueError):
        out["seq"] = 0
    return out


def _put(gid, st: dict) -> None:
    data = _all()
    data[_key(gid)] = st
    _save(data)


def _roles(st: dict) -> List[int]:
    return [int(r) for r in ((st.get("roles") or {}).get("review") or [])]


def _chan(st: dict, key: str) -> int:
    try:
        return int((st.get("channels") or {}).get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _allowed(inter, st: dict) -> bool:
    """Приёмка — своя роль на этом сервере; админ семьи проходит всегда."""
    if is_admin(inter):
        return True
    want = set(_roles(st))
    member = getattr(inter, "author", None) or inter
    return bool(want) and any(r.id in want for r in getattr(member, "roles", []))


def _deny(bot=None, guild=None) -> disnake.Embed:
    lock = _e(bot, "hz_lock", "🔒")
    shield = _e(bot, "hz_shield", "🛡")
    return note_embed(
        f"{lock}  {T_DENY}\n\n"
        f"Роль приёмки выбирается на экране `/жалобы` {shield}",
        "error", guild=guild, title=f"{lock}  НЕТ ДОСТУПА")


def _not_here(bot=None, guild=None) -> disnake.Embed:
    no = _e(bot, "hz_moderatorroleicon", "⛔")
    shield = _e(bot, "hz_shield", "🛡")
    return note_embed(
        f"{no}  {T_NOT_HERE}\n\n"
        f"Роль выбирает руководство семьи — экран `/жалобы` {shield}",
        "error", guild=guild, title=f"{no}  СЮДА НЕЛЬЗЯ")


def _warn(bot=None, guild=None, head: str = "ТАК НЕ ВЫЙДЕТ", text: str = "") -> disnake.Embed:
    no = _e(bot, "hz_moderatorroleicon", "⛔")
    return note_embed(brand.E(bot, text), "error", title=f"{no}  {head}", guild=guild)


def _cog(inter) -> Optional["Complaints"]:
    """Ког берём из взаимодействия: у ephemeral-вью нет .bot, пока его никто не зарегистрировал."""
    bot = getattr(inter, "bot", None)
    return bot.get_cog("Complaints") if bot else None


def _when(ts) -> str:
    try:
        return f"<t:{int(ts)}:R>"
    except (TypeError, ValueError):
        return ""


# ─────────────────────────── баннер под текстом ───────────────────────────


def _put_banner(embed: disnake.Embed, asset: str) -> Optional[disnake.File]:
    """Картинка под текстом — как в заявках семьи: ссылка на CDN, иначе вложение."""
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


def _status_icon(bot, status: str) -> str:
    wait = _e(bot, "hz_warn", "⏳")
    return {"ok": _e(bot, "hz_bluetick", "✅"), "no": _e(bot, "hz_moderatorroleicon", "❌")}.get(
        status, wait)


def _case_embed(bot, guild, case: dict) -> disnake.Embed:
    status = case.get("status") or "wait"
    icon = _status_icon(bot, status)
    words = {"ok": _T(bot, "cmp_status_ok", T_STATUS_OK),
             "no": _T(bot, "cmp_status_no", T_STATUS_NO),
             "wait": _T(bot, "cmp_status_wait", T_STATUS_WAIT)}
    warn = _e(bot, "hz_warn")
    nick = _e(bot, "hz_followicon")
    pc = _e(bot, "hz_pcicon")
    merch = _e(bot, "hz_merchicon")
    emb = card(_T(bot, "cmp_case_title", T_CASE_TITLE, id=case["id"]), guild=guild,
               color_key={"ok": "success_color", "no": "error_color"}.get(status, ACCENT))
    emb.description = (
        f"### {warn}  {clip(case['nick'], 40)}\n\n"
        f"**{_T(bot, 'cmp_field_static', T_FIELD_STATIC)}** · "
        f"`{case['static'] or '—'}` {pc}\n"
        f"**{_T(bot, 'cmp_field_when', T_FIELD_WHEN)}** · {clip(case['when'], 60) or '—'} {pc}\n"
        f"**{_T(bot, 'cmp_field_nick', T_FIELD_NICK)}** · {clip(case['nick'], 60)} {nick}\n\n"
        f"> {icon}  {words.get(status, words['wait'])}"
    )
    if case.get("photos"):
        emb.description += (f"\n**{_T(bot, 'cmp_field_photos', T_FIELD_PHOTOS)}** · "
                            f"{int(case['photos'])} {merch}")
    emb.add_field(name=f"{merch}  ЧТО СДЕЛАЛ", value=clip(case["what"], 900), inline=False)
    foot = f"Подал <@{case['by']}> · {_when(case['ts'])}"
    if status != "wait":
        foot += (f"\nРешил <@{int(case.get('decided_by') or 0)}> · "
                 f"{_when(case.get('decided_ts') or 0)} {icon}")
        if status == "no":
            foot += f"\nПричина: {clip(case.get('why', ''), 200)}"
    emb.add_field(name=f"{_e(bot, 'hz_book')}  ХРОНИКА", value=foot[:1024], inline=False)
    return emb


def _book_embed(bot, st: dict, uid: int, guild=None) -> disnake.Embed:
    rows = []
    for case in sorted((st["cases"] or {}).values(), key=lambda c: -int(c["id"]))[:12]:
        icon = _status_icon(bot, case.get("status") or "wait")
        rows.append([f"{icon}  #{case['id']} · {clip(case['nick'], 28)}",
                     f"{clip(case['what'], 70)}\n{_when(case['ts'])}"])
    waiting = sum(1 for c in (st["cases"] or {}).values() if (c.get("status") or "wait") == "wait")
    return list_embed(_T(bot, "cmp_book_title", T_BOOK_TITLE), rows, guild=guild, color_key=ACCENT,
                      inline=False,
                      empty=_T(bot, "cmp_book_empty", T_BOOK_EMPTY),
                      note=_T(bot, "cmp_book_note", T_BOOK_NOTE,
                              total=len(st["cases"] or {}), wait=waiting))


# ─────────────────────────── кнопки ───────────────────────────
# Декоратор читает надпись один раз — при импорте кога, поэтому правку из «Текстов»
# перечитывают здесь, при каждом сборе вьюхи.

BTN_TEXTS = {
    "hzcmp:open": "cmp_btn_open",
    "hzcmpp:setup": "cmp_btn_setup",
    "hzcmpp:banners": "cmp_btn_banners",
    "hzcmpp:book": "cmp_btn_book",
}


def relabel(view, bot=None) -> disnake.ui.View:
    for child in view.children:
        key = BTN_TEXTS.get(getattr(child, "custom_id", None) or "")
        if key and hasattr(child, "label"):
            child.label = (get_text(key, "") or child.label or "")[:80]
    brand.paint(bot, view, getattr(view, "BRAND", {}) or {})
    return view


class HallView(disnake.ui.View):
    """Постоянная: висит под баннером формы и после рестарта работает по custom_id."""

    BRAND = {"hzcmp:open": ("hz_megaphone",)}

    def __init__(self, bot=None):
        super().__init__(timeout=None)
        relabel(self, bot)

    @disnake.ui.button(label=get_text("cmp_btn_open", T_BTN_OPEN),
                       style=disnake.ButtonStyle.primary, custom_id="hzcmp:open", row=0)
    async def open_case(self, button, inter: disnake.MessageInteraction):
        pass


class ReviewView(disnake.ui.View):
    """Кнопки одной карточки: их ловит on_button_click, вьюхе не нужно переживать рестарт."""

    def __init__(self, cid: str, bot=None):
        super().__init__(timeout=None)
        self.add_item(disnake.ui.Button(
            label=get_text("cmp_btn_accept", T_BTN_ACCEPT),
            style=disnake.ButtonStyle.success, custom_id=f"hzcmp:ok:{cid}", row=0))
        self.add_item(disnake.ui.Button(
            label=get_text("cmp_btn_reject", T_BTN_REJECT),
            style=disnake.ButtonStyle.danger, custom_id=f"hzcmp:no:{cid}", row=0))
        brand.paint(bot, self, {f"hzcmp:ok:{cid}": ("hz_bluetick",),
                                f"hzcmp:no:{cid}": ("hz_moderatorroleicon",)})


class PanelView(disnake.ui.View):
    """Экран `/жалобы`: собрать ветку, перезалить баннеры, выбрать роль приёмки, открыть журнал."""

    BRAND = {"hzcmpp:setup": ("hz_megaphone",), "hzcmpp:banners": ("hz_pcicon",),
             "hzcmpp:book": ("hz_book",)}

    def __init__(self, st: dict, bot=None):
        super().__init__(timeout=900)
        relabel(self, bot)
        self.st = st
        select = disnake.ui.RoleSelect(
            custom_id="hzcmpp:role:review",
            placeholder=get_text("cmp_select_review_ph",
                                 "Выбери роль приёмки жалоб (до 3)"),
            max_values=3, row=2,
            default_values=[disnake.Object(id=r) for r in _roles(st)])
        select.callback = self._role_handler(select)
        self.add_item(select)

    def _role_handler(self, select):
        """disnake зовёт item.callback(interaction) — поэтому select держим в замыкании."""
        async def handler(inter: disnake.MessageInteraction):
            cog = _cog(inter)
            if cog:
                await cog.save_role(inter, select.values)
        return handler

    @disnake.ui.button(label=get_text("cmp_btn_setup", T_BTN_SETUP),
                       style=disnake.ButtonStyle.primary, custom_id="hzcmpp:setup", row=0)
    async def setup(self, button, inter: disnake.MessageInteraction):
        cog = _cog(inter)
        await inter.response.defer(ephemeral=True, with_message=True)
        if cog:
            await cog.build_branch(inter)

    @disnake.ui.button(label=get_text("cmp_btn_banners", T_BTN_BANNERS),
                       style=disnake.ButtonStyle.secondary, custom_id="hzcmpp:banners", row=0)
    async def banners(self, button, inter: disnake.MessageInteraction):
        cog = _cog(inter)
        await inter.response.defer(ephemeral=True, with_message=True)
        if cog:
            await cog.post_banners(inter)

    @disnake.ui.button(label=get_text("cmp_btn_book", T_BTN_BOOK),
                       style=disnake.ButtonStyle.success, custom_id="hzcmpp:book", row=1)
    async def book(self, button, inter: disnake.MessageInteraction):
        cog = _cog(inter)
        await inter.response.send_message(
            embed=_book_embed(cog.bot if cog else None, self.st, inter.author.id, inter.guild),
            ephemeral=True)


# ─────────────────────────── модалки ───────────────────────────


class ComplaintModal(disnake.ui.Modal):
    """Форма жалобы: ник, статик, что сделал, когда было + скрины прямо в форме.

    Пять компонентов — потолок модальной формы, поэтому «когда было» идёт отдельным коротким
    полем, а подробности умещаются в «Что он сделал».
    """

    def __init__(self):
        super().__init__(
            title=get_text("cmp_modal_title", T_MODAL_TITLE)[:45],
            custom_id="hzcmpm:form",
            components=[
                disnake.ui.TextInput(
                    label=get_text("cmp_modal_nick", T_MODAL_NICK), custom_id="nick",
                    placeholder=get_text("cmp_modal_nick_ph", T_MODAL_NICK_PH), max_length=40),
                disnake.ui.TextInput(
                    label=get_text("cmp_modal_static", T_MODAL_STATIC), custom_id="static",
                    required=False,
                    placeholder=get_text("cmp_modal_static_ph", T_MODAL_STATIC_PH), max_length=12),
                disnake.ui.TextInput(
                    label=get_text("cmp_modal_what", T_MODAL_WHAT), custom_id="what",
                    style=disnake.TextInputStyle.paragraph,
                    placeholder=get_text("cmp_modal_what_ph", T_MODAL_WHAT_PH), max_length=900),
                disnake.ui.TextInput(
                    label=get_text("cmp_modal_when", T_MODAL_WHEN), custom_id="when",
                    placeholder=get_text("cmp_modal_when_ph", T_MODAL_WHEN_PH), max_length=60),
                disnake.ui.Label(
                    # цифра в надписи = MAX_PHOTOS: если менять лимит, правь и здесь
                    get_text("cmp_modal_shots", T_MODAL_SHOTS),
                    disnake.ui.FileUpload(custom_id="photos", min_values=0,
                                          max_values=MAX_PHOTOS, required=False),
                    description=get_text("cmp_modal_shots_hint", T_MODAL_SHOTS_HINT)),
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        cog = _cog(inter)
        if cog is None:
            return await inter.response.send_message(
                embed=_warn(inter.bot, inter.guild, "ЖАЛОБЫ СПЯТ",
                            ":hz_shield:  Модуль жалоб не загружен — нужен рестарт бота."),
                ephemeral=inter.guild is not None)
        shots = [a for a in (inter.resolved_values.get("photos") or [])
                 if a is not None][:MAX_PHOTOS]
        files: List[disnake.File] = []
        for att in shots:
            if not (att.content_type or "").startswith("image/"):
                return await inter.response.send_message(
                    embed=_warn(inter.bot, inter.guild, "СКРИН — НЕ КАРТИНКА",
                                f":hz_merchicon:  `{att.filename}` не изображение. "
                                "Прикрепляй png или jpg."),
                    ephemeral=True)
            if att.size > MAX_PHOTO_MB * 1024 * 1024:
                return await inter.response.send_message(
                    embed=_warn(inter.bot, inter.guild, "СКРИН СЛИШКОМ ТЯЖЁЛЫЙ",
                                f":hz_merchicon:  `{att.filename}` больше {MAX_PHOTO_MB} МБ. "
                                "Приложи полегче."),
                    ephemeral=True)
            try:
                files.append(await att.to_file())
            except disnake.DiscordException:
                continue
        vals = inter.text_values
        await cog.submit(inter, (vals.get("nick") or "").strip(),
                         (vals.get("static") or "").strip(),
                         (vals.get("what") or "").strip(),
                         (vals.get("when") or "").strip(), files)


class RejectModal(disnake.ui.Modal):
    def __init__(self, cid: str):
        super().__init__(
            title=get_text("cmp_modal_reject_title", T_MODAL_REJECT_TITLE)[:45],
            custom_id=f"hzcmpm:no:{cid}",
            components=[disnake.ui.TextInput(
                label=get_text("cmp_modal_reject_ph", T_MODAL_REJECT_PH), custom_id="why",
                style=disnake.TextInputStyle.paragraph, max_length=300)],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        cog = _cog(inter)
        if cog:
            await cog.decide(inter, self.custom_id.split(":")[-1], False,
                             (inter.text_values.get("why") or "").strip())


# ─────────────────────────── ког ───────────────────────────


class Complaints(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # ожидание скринов к жалобе: user_id → (cid, msg_id, channel_id, deadline)
        self._pending_photos: dict = {}

    @commands.Cog.listener()
    async def on_ready(self):
        if not getattr(self.bot, "_complaints_view_added", False):
            self.bot.add_view(HallView(self.bot))
            self.bot._complaints_view_added = True

    # ── экран раздела ──
    @commands.slash_command(name="жалобы", description="Жалобы на игроков: форма, приёмка, итог",
                            contexts=disnake.InteractionContextTypes(guild=True))
    async def complaints_panel(self, inter: disnake.ApplicationCommandInteraction):
        if not is_admin(inter):
            return await inter.response.send_message(embed=_deny(self.bot, inter.guild),
                                                     ephemeral=True)
        if inter.guild is None:
            return await inter.response.send_message(
                embed=_warn(self.bot, None, "ТОЛЬКО НА СЕРВЕРЕ",
                            ":hz_shield:  Жалобы живут на сервере — открывай `/жалобы` там."),
                ephemeral=True)
        st = _srv(inter.guild.id)
        rows = []
        for key, label in (("hall", ":hz_pencil:  форма для всех"),
                           ("review", ":hz_shield:  приёмка для своих")):
            ch = inter.guild.get_channel(_chan(st, key))
            rows.append(f"{label} — " + (ch.mention if ch else _T(
                self.bot, "cmp_channel_missing",
                "_нет — нажми «Создать ветку жалоб» ниже_")))
        who = " ".join(f"<@&{r}>" for r in _roles(st)) or _T(
            self.bot, "cmp_role_none",
            "_не выбрана — жалобами правят только админы семьи_")
        cases = st["cases"] or {}
        waiting = sum(1 for c in cases.values() if (c.get("status") or "wait") == "wait")
        ok = sum(1 for c in cases.values() if c.get("status") == "ok")
        no = sum(1 for c in cases.values() if c.get("status") == "no")
        sections = [
            (_T(self.bot, "cmp_sec_channels", ":hz_pencil:  КАНАЛЫ"),
             brand.E(self.bot, "\n".join(rows))),
            (_T(self.bot, "cmp_sec_roles", ":hz_followicon:  РОЛЬ ПРИЁМКИ"),
             _T(self.bot, "cmp_sec_roles_text", ":hz_shield:  {who}", who=who)),
            (_T(self.bot, "cmp_sec_book", ":hz_book:  ЖУРНАЛ"),
             _T(self.bot, "cmp_sec_book_text",
                ":hz_warn:  всего **{total}**  ·  :hz_bluetick:  принято **{ok}**\n"
                ":hz_moderatorroleicon:  отклонено **{no}**  ·  :hz_pcicon:  ждут **{waiting}**",
                total=len(cases), ok=ok, no=no, waiting=waiting)),
        ]
        emb = panel_embed(_T(self.bot, "cmp_panel_title", T_PANEL_TITLE), sections,
                          guild=inter.guild, color_key=ACCENT,
                          intro=_T(self.bot, "cmp_panel_intro", T_PANEL_INTRO))
        await inter.response.send_message(embed=emb, view=PanelView(st, self.bot),
                                          ephemeral=True,
                                          file=_put_banner(emb, HALL_BANNER))

    # ── ветка каналов ──
    async def build_branch(self, inter) -> None:
        guild = inter.guild
        if guild is None:
            return await inter.followup.send(
                embed=_warn(self.bot, None, "ТОЛЬКО НА СЕРВЕРЕ",
                            ":hz_shield:  Ветку жалоб собирают на сервере, где она нужна."),
                ephemeral=True)
        st = _srv(guild.id)
        me = guild.me
        # В форме писать можно: человек без скринов дотягивает их ответом, а бот забирает
        # сообщение в карточку жалобы и удаляет — витрина остаётся чистой.
        public = {guild.default_role: disnake.PermissionOverwrite(view_channel=True,
                                                                  send_messages=True,
                                                                  add_reactions=False),
                  me: disnake.PermissionOverwrite(view_channel=True, send_messages=True,
                                                  embed_links=True, attach_files=True,
                                                  manage_channels=True, manage_messages=True)}
        private = {guild.default_role: disnake.PermissionOverwrite(view_channel=False),
                   me: disnake.PermissionOverwrite(view_channel=True, send_messages=True,
                                                   embed_links=True, attach_files=True,
                                                   manage_channels=True)}
        made: List[str] = []
        cat_id = _chan(st, "category")
        cat = guild.get_channel(cat_id) if cat_id else None
        if cat is None:
            cat = await guild.create_category(CATEGORY, overwrites=public,
                                              reason="Жалобы на игроков")
            st["channels"]["category"] = cat.id
            made.append(f"{_e(self.bot, 'hz_shield')} {cat.mention}")
        for key, name in CHANNELS.items():
            ch = guild.get_channel(_chan(st, key))
            if ch is None:
                ch = await guild.create_text_channel(
                    name, category=cat, overwrites=private if key == "review" else public,
                    topic=_topic(self.bot, key), reason="Жалобы на игроков")
                st["channels"][key] = ch.id
                made.append(ch.mention)
            else:
                # права переставляем и у готового канала: ветка, собранная прошлой версией,
                # иначе осталась бы с прежними настройками
                try:
                    if ch.category != cat:
                        await ch.edit(category=cat, reason="Жалобы: место в ветке")
                    await ch.edit(overwrites=private if key == "review" else public,
                                  reason="Жалобы: права канала")
                except disnake.DiscordException:
                    pass
        _put(guild.id, st)
        await self._sync_perms(guild, st)
        await self._post_banners(guild, st)
        note = ("Собрано: " + ", ".join(made)) if made else "Ветка уже стояла целиком."
        await inter.followup.send(embed=note_embed(
            _T(self.bot, "cmp_done_text", T_DONE_TEXT, made=clip(note, 300)),
            "success", title=_T(self.bot, "cmp_panel_title", T_PANEL_TITLE), guild=guild),
            ephemeral=True)

    async def _sync_perms(self, guild, st: dict) -> None:
        """Приёмка приватная: пускаем роль приёмки этого сервера, админов семьи и его админов."""
        cfg = load_config()
        allow = set(_roles(st)) | set(cfg.get("admin_role_ids") or []) | \
            set(guild_cfg.role_ids("admin_role_ids", guild.id))
        ch = guild.get_channel(_chan(st, "review"))
        if ch is None:
            return
        ow = {guild.default_role: disnake.PermissionOverwrite(view_channel=False),
              guild.me: disnake.PermissionOverwrite(view_channel=True, send_messages=True,
                                                    embed_links=True, attach_files=True)}
        for rid in allow:
            role = guild.get_role(int(rid))
            if role:
                ow[role] = disnake.PermissionOverwrite(view_channel=True, send_messages=False)
        try:
            await ch.edit(overwrites=ow, reason="Жалобы: роль приёмки")
        except disnake.DiscordException:
            pass

    async def post_banners(self, inter) -> None:
        if inter.guild is None:
            return await inter.followup.send(
                embed=_warn(self.bot, None, "ТОЛЬКО НА СЕРВЕРЕ",
                            ":hz_shield:  Баннеры кладутся в каналы жалоб на сервере."),
                ephemeral=True)
        st = _srv(inter.guild.id)
        done = await self._post_banners(inter.guild, st)
        shield = _e(self.bot, "hz_shield")
        pencil = _e(self.bot, "hz_pencil")
        lock = _e(self.bot, "hz_lock")
        if done:
            emb = note_embed(
                f"### {shield}  Витрина жалоб обновлена\n\n"
                f"**Форма** — объяснение сверху, баннер под текстом, кнопка для всех {pencil}\n"
                f"**Приёмка** — памятка, которую видят только свои {lock}\n\n"
                f"> Старые карточки убраны, кнопки на местах {shield}.",
                "success", title=f"{shield}  БАННЕРЫ ПЕРЕЗАЛИТЫ", guild=inter.guild)
        else:
            emb = _warn(self.bot, inter.guild, "ВЕТКИ ЕЩЁ НЕТ",
                        ":hz_pencil:  Сначала «Создать ветку жалоб» — баннеры кладутся "
                        "в уже готовые каналы.")
        await inter.followup.send(embed=emb, ephemeral=True)

    async def _post_banners(self, guild, st: dict) -> bool:
        hall = guild.get_channel(_chan(st, "hall"))
        review = guild.get_channel(_chan(st, "review"))
        if hall is None or review is None:
            return False
        # Чищаем только витрины: карточки жалоб в приёмке — это журнал, его не трогаем.
        for ch in (hall, review):
            async for m in ch.history(limit=50):
                if m.author.id == guild.me.id:
                    try:
                        await m.delete()
                    except disnake.DiscordException:
                        pass
        hall_emb = card(_T(self.bot, "cmp_hall_head", T_HALL_HEAD),
                        _T(self.bot, "cmp_hall_text", T_HALL_TEXT), guild=guild, color_key=ACCENT)
        await hall.send(embed=hall_emb, view=HallView(self.bot),
                        file=_put_banner(hall_emb, HALL_BANNER))
        memo = card(_T(self.bot, "cmp_review_head", T_REVIEW_HEAD),
                    _T(self.bot, "cmp_review_text", T_REVIEW_TEXT), guild=guild, color_key=ACCENT)
        if not await _has_bot_message(review, guild):
            await review.send(embed=memo, file=_put_banner(memo, REVIEW_BANNER))
        return True

    # ── роль приёмки ──
    async def save_role(self, inter, values) -> None:
        if inter.guild is None:
            return await inter.response.send_message(embed=_warn(
                self.bot, None, "ТОЛЬКО НА СЕРВЕРЕ", ":hz_shield:  Роль выбирают на сервере."),
                ephemeral=True)
        if not is_admin(inter):
            return await inter.response.send_message(embed=_deny(self.bot, inter.guild),
                                                     ephemeral=True)
        st = _srv(inter.guild.id)
        st["roles"]["review"] = [int(r.id) for r in list(values)[:3]]
        _put(inter.guild.id, st)
        await self._sync_perms(inter.guild, st)
        shield = _e(self.bot, "hz_shield")
        follow = _e(self.bot, "hz_followicon")
        names = " ".join(f"<@&{r}>" for r in st["roles"]["review"]) or \
            "_роль сбросщена — жалобами правят только админы семьи_"
        await inter.response.send_message(
            embed=note_embed(f"### {shield}  Роль приёмки жалоб\n{names}\n\n"
                             f"Доступ к каналу приёмки уже переставлен {shield}\n"
                             f"Одобрение и отказ пишутся в ЛС автору жалобы {follow}",
                             "success", title=f"{shield}  РОЛЬ ВЫБРАНА", guild=inter.guild),
            ephemeral=True)

    # ── подача жалобы ──
    async def submit(self, inter, nick: str, static: str, what: str, when: str,
                     files: Optional[List[disnake.File]] = None) -> None:
        if getattr(getattr(inter, "author", None), "bot", False):
            return await self._answer(
                inter, _warn(self.bot, inter.guild, "ЭТО БОТ", ":hz_followicon:  Жалобу подают люди."))
        if not nick:
            return await self._answer(
                inter, _warn(self.bot, inter.guild, "НЕ УКАЗАН НИК",
                             ":hz_pencil:  Без ника нарушителя жалобу не на кого подавать."))
        if not what:
            return await self._answer(
                inter, _warn(self.bot, inter.guild, "ПУСТОЕ ОПИСАНИЕ",
                             ":hz_warn:  Опиши, что человек сделал, — приёмка решает по этому."))
        if static and not re.fullmatch(r"\d{1,9}", static):
            return await self._answer(
                inter, _warn(self.bot, inter.guild, "СТАТИК — НЕ ЦИФРЫ",
                             ":hz_pcicon:  Статик состоит только из цифр, например `12345`. "
                             "Если не знаешь — оставь поле пустым."))
        res = await self.file_case(inter.guild, int(inter.author.id), nick, static, what,
                                   when, files)
        if res.get("error") == "review":
            return await self._answer(
                inter, _warn(self.bot, inter.guild, "НЕТ КАНАЛА ПРИЁМКИ",
                             ":hz_shield:  Приёмки ещё нет — на экране `/жалобы` нажми "
                             "«Создать ветку жалоб», потом подавай жалобу."))
        cid, review = res["id"], res["review"]
        shield = _e(self.bot, "hz_shield")
        lock = _e(self.bot, "hz_lock")
        merch = _e(self.bot, "hz_merchicon")
        bluetick = _e(self.bot, "hz_bluetick")
        warn = _e(self.bot, "hz_warn")
        lines = [_T(self.bot, "cmp_sent_text", T_SENT_TEXT, id=cid, review=review.mention,
                    lock=lock, warn=warn, bluetick=bluetick)]
        if res["photos"]:
            lines.append(_T(self.bot, "cmp_sent_shots", T_SENT_WITH_SHOTS, n=res["photos"],
                            merch=merch))
        else:
            self._pending_photos[int(inter.author.id)] = (
                cid, res["card"].id, int(inter.channel.id), time.time() + PHOTO_SECONDS)
            lines.append(_T(self.bot, "cmp_sent_noshots", T_SENT_NO_SHOTS,
                            sec=PHOTO_SECONDS, merch=merch))
        await self._answer(inter, note_embed("".join(lines), "success",
                                             title=_T(self.bot, "cmp_sent_title", T_SENT_TITLE),
                                             guild=inter.guild))

    async def file_case(self, guild, author_id: int, nick: str, static: str, what: str,
                        when: str, files: Optional[List[disnake.File]] = None) -> dict:
        """Записать жалобу и положить карточку в приёмку.

        Общий вход для Discord (кнопка и модалка) и для веб-панели: права уже проверены до
        вызова, здесь остаются только поля формы, хроника и карточка.
        """
        if guild is None:
            return {"error": "review"}
        st = _srv(guild.id)
        review = guild.get_channel(_chan(st, "review"))
        if not isinstance(review, disnake.TextChannel):
            return {"error": "review"}
        st["seq"] = int(st.get("seq") or 0) + 1
        cid = str(st["seq"])
        case = {"id": cid, "by": int(author_id), "nick": (nick or "без ника")[:40],
                "static": (static or "")[:12], "what": (what or "")[:900],
                "when": (when or "")[:60], "ts": int(time.time()), "status": "wait",
                "photos": len(files) if files else 0}
        st["cases"][cid] = case
        if len(st["cases"]) > MAX_CASES:
            # Выкидываем только закрытые жалобы: живая жалоба без карточки осталась бы без приёмки.
            closed = sorted((c for c in st["cases"].values()
                             if c.get("status") != "wait"), key=lambda c: int(c["id"]))
            for old in closed[: len(st["cases"]) - MAX_CASES]:
                st["cases"].pop(str(old["id"]), None)
        _put(guild.id, st)
        card_msg = await review.send(embed=_case_embed(self.bot, guild, case),
                                     view=ReviewView(cid, self.bot), files=files or [])
        return {"id": cid, "review": review, "card": card_msg, "photos": len(files or [])}

    # ── решение ──
    async def decide(self, inter, cid: str, accept: bool, why: str = "") -> None:
        """Кнопка приёмки. Канал приватный, но перепроверяем роль здесь же: карточку могут
        переслать, а Discord не запрещает нажать кнопку тому, кто её видит."""
        if inter.guild is None:
            return await inter.response.send_message(
                embed=_warn(self.bot, None, "ТОЛЬКО НА СЕРВЕРЕ",
                            ":hz_shield:  Жалобу решают на сервере, где её подали."),
                ephemeral=True)
        st = _srv(inter.guild.id)
        if not _allowed(inter, st):
            return await inter.response.send_message(embed=_not_here(self.bot, inter.guild),
                                                     ephemeral=True)
        case = (st["cases"] or {}).get(cid)
        if not case:
            return await inter.response.send_message(
                embed=_warn(self.bot, inter.guild, "ЖАЛОБА НЕ НАЙДЕНА",
                            ":hz_book:  Такая жалоба уже стёрта из журнала — карточка в канале "
                            "осталась старой."),
                ephemeral=True)
        if (case.get("status") or "wait") != "wait":
            return await inter.response.send_message(
                embed=_warn(self.bot, inter.guild, "ЖАЛОБА ЗАКРЫТА",
                            f":hz_shield:  Жалобу #{cid} уже решили — второй раз её не открыть."),
                ephemeral=True)
        case["status"] = "ok" if accept else "no"
        case["decided_by"] = int(inter.author.id)
        case["decided_ts"] = int(time.time())
        if not accept:
            case["why"] = (why or "без причины")[:300]
        _put(inter.guild.id, st)
        emb = _case_embed(self.bot, inter.guild, case)
        if isinstance(inter, disnake.ModalInteraction):
            # форма отказа отвечает своим сообщением: править карточку надо руками,
            # а edit_message модальному взаимодействию Discord не разрешает
            await inter.response.send_message(embed=note_embed(
                f"{_e(self.bot, 'hz_shield')}  Жалобе #{cid} дан отказ. Автору написал в ЛС.",
                "success", title=f"{_e(self.bot, 'hz_moderatorroleicon')}  РЕШЕНИЕ ЗАПИСАНО",
                guild=inter.guild), ephemeral=True)
            if inter.message is not None:
                try:
                    await inter.message.edit(embed=emb, view=None)
                except disnake.DiscordException:
                    pass
        else:
            await inter.response.edit_message(embed=emb, view=None)
        await self._tell(inter.guild, case)

    async def _tell(self, guild, case: dict) -> None:
        """ЛС автору жалобы: итог. В приёмку ему заглядывать некуда — канал виден только своим."""
        ok = case["status"] == "ok"
        author = guild.get_member(int(case["by"]))
        if author is None:
            try:
                author = await self.bot.fetch_user(int(case["by"]))
            except disnake.DiscordException:
                author = None
        if author is None:
            print(f"🛡 Жалоба #{case['id']}: автора не найти в ЛС — <@{case['by']}>", flush=True)
            return
        if ok:
            title = _T(self.bot, "cmp_ok_title", T_OK_TITLE)
            text = _T(self.bot, "cmp_ok_text", T_OK_TEXT, id=case["id"], nick=clip(case["nick"], 40),
                      decided=case.get("decided_by", 0), when=_when(case.get("decided_ts", 0)))
        else:
            title = _T(self.bot, "cmp_no_title", T_NO_TITLE)
            text = _T(self.bot, "cmp_no_text", T_NO_TEXT, id=case["id"],
                      nick=clip(case["nick"], 40), why=clip(case.get("why", ""), 200),
                      decided=case.get("decided_by", 0), when=_when(case.get("decided_ts", 0)))
        emb = note_embed(text, "success" if ok else "error", title=title, guild=guild)
        file = _put_banner(emb, REVIEW_BANNER)
        try:
            await author.send(embed=emb, file=file)
        except disnake.DiscordException:
            # закрытые ЛС: решение всё равно осталось в карточке и в журнале
            print(f"🛡 Жалоба #{case['id']}: ЛС автору не открылось", flush=True)

    async def _answer(self, inter, emb: disnake.Embed) -> None:
        if inter.response.is_done():
            await inter.followup.send(embed=emb, ephemeral=True)
        else:
            await inter.response.send_message(embed=emb, ephemeral=True)

    # ── скрины к жалобе: после модальной формы их прикладывают следующим сообщением ──
    @commands.Cog.listener()
    async def on_message(self, message: disnake.Message):
        if message.author.bot or message.guild is None:
            return
        pending = self._pending_photos.get(int(message.author.id))
        if not pending:
            return
        cid, msg_id, channel_id, deadline = pending
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
        st = _srv(message.guild.id)
        review = message.guild.get_channel(_chan(st, "review"))
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
        st = _srv(message.guild.id)
        case = (st["cases"] or {}).get(cid)
        if case:
            case["photos"] = len(card_msg.attachments)
            _put(message.guild.id, st)
        try:
            await message.delete()
        except disnake.DiscordException:
            pass
        try:
            await message.channel.send(
                embed=note_embed(
                    f"{_e(self.bot, 'hz_merchicon')}  Скрины к жалобе **#{cid}** "
                    f"прикреплены ({len(files)} шт.).",
                    "success", title=f"{_e(self.bot, 'hz_merchicon')}  ФОТО ДОБАВЛЕНЫ",
                    guild=message.guild))
        except disnake.DiscordException:
            pass

    # ── роутер кнопок ──
    @commands.Cog.listener()
    async def on_button_click(self, inter: disnake.MessageInteraction):
        cid = str(inter.data.custom_id or "")
        if not cid.startswith("hzcmp:"):
            return
        action = cid.split(":", 2)[1]

        if action == "open":
            # Жалобу подаёт любой: разбирательство начинается со слова человека,
            # а решает его приёмка.
            return await inter.response.send_modal(ComplaintModal())
        if action in ("ok", "no"):
            case_id = cid.split(":", 2)[2]
            if action == "ok":
                return await self.decide(inter, case_id, True)
            st = _srv(inter.guild.id) if inter.guild else _srv(0)
            if not _allowed(inter, st):
                return await inter.response.send_message(embed=_not_here(self.bot, inter.guild),
                                                         ephemeral=True)
            return await inter.response.send_modal(RejectModal(case_id))


def _topic(bot, key: str) -> str:
    topics = {
        "hall": _T(bot, "cmp_hall_topic", T_HALL_TOPIC),
        "review": _T(bot, "cmp_review_topic", T_REVIEW_TOPIC),
    }
    return topics.get(key, _T(bot, "cmp_default_topic", T_DEFAULT_TOPIC))


def setup(bot: commands.Bot):
    bot.add_cog(Complaints(bot))
