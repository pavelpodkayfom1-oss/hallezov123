"""/панель — все админ-экраны семьи в одном меню.

Экраны жили по своим командам (/hall, /hallpanel, /admin, /центр, /golos, /logs panel,
/teg, /media_send ...), и искать нужный по названиям утомительно. Здесь два уровня
кнопок: раздел и экран. Хаб ничего не переписывает — он вызывает уже готовый экран того
же кога, поэтому права, кнопки и тексты раздела ровно те, что были у его команды.

В каталог попали только экраны, которые открываются без аргументов. Экранам, которым нужен
участник, причина или картинка, аргументы подставляет форма: раздел «✋ Приказы» (баллы,
выговоры, ранг, ник) для этого и заведён — там ник пишут в поле, а не в слэш-команду. Команды
с картинкой (баннер-файл) и увольнение остаются своими командами: подставить им файл или
решение с подтверждением из меню нельзя, а вызвать без них — значит молча сломать половину кнопок.

Экраны со строковыми аргументами (эмодзи по ссылке, событие, чёрный список, привязка канала
логов) живут в EXTRA_SCREENS: в Discord их по-прежнему открывают своими командами, а веб-панель
спрашивает то же самое формой. В SECTIONS их нет, чтобы меню Discord не предлагало кнопки,
которые просят аргументы.

Вторая панель — /hr (набор и анкета), она кнопкой внизу.

Кнопка «🙈 Убрать дубли» прячет из меню Discord те команды, которые /панель открывает не
хуже. Флаг лежит в panel_state.json, а он входит в снимки backups/, поэтому /reverse
возвращает список команд целиком — без рестарта и без правок кода. Скрытие работает и после
рестарта, а при спрятывании чистится и глобальный реестр команд: Discord показывает его в
меню всегда, даже когда у сервера свой список.
"""
import asyncio
import inspect
import json
from pathlib import Path

import disnake
from disnake.ext import commands

from utils.checks import is_admin
from utils.embeds import base_embed, note_embed

TIMEOUT = 900
STATE_FILE = Path(__file__).resolve().parent.parent / "panel_state.json"
FLAG = "hide_duplicate_commands"

# (эмодзи, категория, [(эмодзи, экран, "КлассКога.метод", "команда")])
# Экран называется коротко и по делу: подпись читается на кнопке и в эмбеде, команда — только
# в подсказке, её показывать некрасиво.
SECTIONS = [
    ("👥", "Состав и кадры", [
        ("🧑‍🤝‍🧑", "Состав", "HallTools.hall", "/hall"),
        ("🏠", "Hall-панель", "HallPanel.hallpanel", "/hallpanel"),
        ("⚜️", "Центр управления", "ControlCenter.center", "/центр"),
        ("🧾", "Карточка бойца", "Profile.profile_command", "/profile"),
        ("🪪", "Карточка семьи", "FamqControl.card", "/famq-card"),
        ("🖼", "Карточка картинкой", "ProfileCard.card", "/карточка-фото"),
        ("🔎", "Активность состава", "ActivityCenter.activity_check", "/activity_check"),
        ("📈", "На повышение", "ActivityCenter.for_improve", "/for_improve"),
    ]),
    ("✋", "Приказы", [
        ("💰", "Баллы", "AdminOps.points", "/баллы"),
        ("⚠️", "Выговоры", "AdminOps.warns", "/выговоры"),
        ("🎖", "Ранг", "AdminOps.rank", "/ранг"),
        ("🪪", "Ник", "AdminOps.nick", "/ник"),
    ]),
    ("🏖", "Отпуска и списки", [
        ("🌴", "Отпуск: кто", "HallTools.leave_list", "/отпуск-список"),
        ("💤", "АФК: кто", "HallTools.afk_list", "/афк-список"),
        ("🗑", "Чёрный список", "FamqControl.bl_list", "/famq-blacklist list"),
        ("🎧", "Онлайн в голосовых", "FamqControl.voice_top_cmd", "/famq-voice top"),
        ("🧮", "Рекрутеры: статистика", "FamqControl.recruiters", "/famq-recruiters"),
        ("📊", "Статистика семьи", "FamqControl.livestats_refresh", "/famq-livestats refresh"),
    ]),
    ("🎙", "Голос и сборы", [
        ("🔊", "Голос бота", "Govorit.golos", "/golos"),
        ("🎛", "Голос: роль доступа", "Govorit.govorit_panel", "/govorit-panel"),
        ("🏢", "Голос: канал и войс", "GovoritStay.golos_server", "/golos-server"),
        ("🔇", "Заглушить бота", "Govorit.zamolchi", "/zamolchi"),
        ("📍", "Teg panel: теги", "TegPanel.teg", "/teg"),
        ("🙋", "Teg: доступ", "TegPanel.teg_access", "/teg_access"),
        ("📋", "Teg: ответы на сбор", "TegPanel.teg_report", "/teg_report"),
        ("📤", "Teg: кнопка в канал", "TegPanel.teg_post", "/teg_post"),
        ("📢", "Явка сбора", "AdminOps.calls", "/сборы"),
    ]),
    ("🗂", "Логи и память", [
        ("📁", "Логи: панель", "FamLogs.logs_panel", "/logs panel"),
        ("👀", "Логи: куда льются", "FamLogs.logs_view", "/logs view"),
        ("🏗", "Логи: категория", "FamLogs.logs_auto", "/logs auto"),
        ("🧠", "Память бота", "Memory.mem", "/mem"),
    ]),
    ("⚙️", "Настройки бота", [
        ("🎛", "Главная админ-панель", "AdminCog.admin_command", "/admin"),
        ("🧪", "Проверка текстов", "AdminCog.test_texts", "/test_texts"),
        ("😀", "Эмодзи пака", "HallTools.hz_emojis", "/hz_emojis"),
        ("🔤", "Эмодзи сервера", "BannerPanel.emoji_list", "/emoji_list"),
    ]),
    ("📣", "Панели и рассылки", [
        ("🎯", "Панель набора", "BannerPanel.recruit_panel", "/recruit_panel"),
        ("🏞", "Баннер под заявками", "BannerPanel.banner", "/banner"),
        ("📺", "Анонс стрима", "Media.media_send", "/media_send"),
        ("📢", "Правила семьи", "HallRecruit.rules_cmd", "/rules"),
        ("🎭", "Розыгрыши-пугалки", "PrankCog.prank_panel", "/prank_panel"),
    ]),
    ("✍️", "Набор и рекруты", [
        ("💎", "Рекрут-центр", "HallRecruit.hr", "/hr"),
        ("🔓", "Доступ к веткам", "HallRecruit.hr_access", "/hr_access"),
        ("🗃", "Эмодзи из папки", "HallRecruit.hr_emoji_upload", "/hr_emoji_upload"),
        ("🎫", "Стикеры из папки", "HallRecruit.hr_sticker_upload", "/hr_sticker_upload"),
        ("💬", "Баллы рекрута", "HallRecruit.points", "/points"),
    ]),
    ("🔧", "Сервис и откат", [
        ("💾", "Снимок состояния", "StateSave.update_save", "/update_save"),
        ("↩️", "Откат настроек", "Reverse.reverse", "/reverse"),
    ]),
]

TOTAL = sum(len(items) for _, _, items in SECTIONS)
HR_REF = "HallRecruit.hr"

# Чистые панели-дубли: /панель открывает их ровно так же, как они сами. Команд, с которыми
# что-то делают аргументами (staff, logs, famq-warn, hr_banner, /banner), здесь нет — из
# меню их не заменить, и прятать их значило бы отобрать у руководства выговоры и баннеры.
# /codex — алиас /admin, тот же экран. Приказы (/баллы, /выговоры, /ранг, /ник, /сборы)
# спрятали вместе с ними: аргументы им подставляет форма, так что команда им больше не нужна.
HIDDEN = (
    "admin", "codex", "test_texts", "hall", "hallpanel", "hz_emojis", "центр",
    "activity_check", "for_improve", "отпуск-список", "афк-список", "famq-recruiters",
    "golos", "golos-server", "govorit-panel", "zamolchi", "teg", "teg_access",
    "emoji_list", "mem", "prank_panel", "media_send", "hr_access",
    "hr_emoji_upload", "hr_sticker_upload",
    "баллы", "выговоры", "ранг", "ник", "сборы",
)

_stash: dict = {}

# Экраны только для веб-панели: у них обязательные аргументы, форма панели их спрашивает.
# (эмодзи, экран, "КлассКога.метод", "команда")
EXTRA_SCREENS = [
    ("😀", "Эмодзи: добавить", "BannerPanel.emoji_add", "/emoji_add"),
    ("🗓", "Событие: собрать семью", "Events.event_command", "/event"),
    ("🔎", "ЧС: проверить статик", "FamqControl.bl_check", "/famq-blacklist check"),
    ("⛔", "ЧС: внести игрока", "FamqControl.bl_add", "/famq-blacklist add"),
    ("🧹", "ЧС: убрать запись", "FamqControl.bl_remove", "/famq-blacklist remove"),
    ("📁", "Логи: привязать канал", "FamLogs.logs_set", "/logs set"),
    ("🔕", "Логи: отвязать категорию", "FamLogs.logs_clear", "/logs clear"),
    ("👑", "Забрать админку", "AdminCog.remove_admin_command", "/removeadmin"),
    ("🎧", "Музыка: отправить панель", "Music.music_panel", "/music_panel"),
]


def _find_command(bot, name: str):
    """Снять команду с её же кога: после /reverse хаб новый и _stash пуст.

    /reverse перезагружает только коги тех файлов, которые поднял, — у admin_hub это
    panel_state.json. Команды-дубли при этом живут в чужих когах, и disnake вешает их
    объекты на сами коги. Достаём оттуда, иначе откат вернул бы половину списка.
    """
    for cog in getattr(bot, "cogs", {}).values():
        for cmd in vars(cog).values():
            if isinstance(cmd, commands.InvokableSlashCommand) and cmd.name == name:
                return cmd
    return None


# ───────────────────────── флаг «спрятать дубли» ─────────────────────────
def flag_on() -> bool:
    try:
        return bool(json.loads(STATE_FILE.read_text(encoding="utf-8")).get(FLAG))
    except Exception:
        return False


def flag_set(value: bool) -> None:
    """Пишем в panel_state.json, сохранив чужие ключи: файл снимает backups/ и трогает веб-панель."""
    state = {}
    try:
        loaded = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            state = loaded
    except Exception:
        pass
    state[FLAG] = bool(value)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_visibility(bot) -> list:
    """Снять или вернуть команды-дубли по флагу. Возвращает, что изменилось."""
    changed = []
    if flag_on():
        for name in HIDDEN:
            cmd = bot.all_slash_commands.get(name)
            if cmd is None:
                continue
            bot.remove_slash_command(name)
            _stash[name] = cmd
            changed.append(name)
    else:
        for name in HIDDEN:
            if name in bot.all_slash_commands:
                continue
            cmd = _stash.pop(name, None) or _find_command(bot, name)
            if cmd is None:
                continue
            bot.add_slash_command(cmd)
            changed.append(name)
    return changed


def _global_bodies(bot):
    """Список команд, который должен остаться в глобальном реестре Discord.

    С GUILD_ID disnake держит команды в этих серверах, а глобальный список — огрыстки
    прежних запусков без GUILD_ID: их и надо вычистить.
    """
    if getattr(bot, "test_guilds", None):
        return []
    return [c.body for c in bot.application_commands if not getattr(c, "guild_ids", None)]


async def _stragglers(bot):
    """Команды из HIDDEN, которые Discord всё ещё показывает, и попытка убрать их по id.

    Перезапись списка — best effort: Discord может отдать RateLimit, а клиент держит меню
    у себя. Проверяем живой список и дожимаем удалением, чтобы панель честно сказала, что
    именно не ушло. Возвращает (не ушло, список удался).
    """
    if not flag_on():
        return [], True
    hide = set(HIDDEN)
    stale = []
    try:
        for c in await bot.fetch_global_commands():
            if c.get("name") in hide:
                stale.append(("global", None, c.get("id"), c["name"]))
        for gid in (list(getattr(bot, "test_guilds", None) or []) or [g.id for g in bot.guilds]):
            for c in await bot.fetch_guild_commands(gid):
                if c.get("name") in hide:
                    stale.append(("guild", gid, c.get("id"), c["name"]))
    except Exception as e:
        print(f"🔌 Панель: живой список команд не проверить ({e.__class__.__name__})", flush=True)
        return [], False
    left = []
    for kind, gid, cid, name in stale:
        try:
            await (bot.delete_global_command(cid) if kind == "global"
                   else bot.delete_guild_command(gid, cid))
            print(f"🙈 Панель: дожал /{name} ({'глобально' if kind == 'global' else gid})", flush=True)
        except Exception as e:
            print(f"🙈 Панель: /{name} не удаляется ({e.__class__.__name__})", flush=True)
            left.append(name)
    return left, True


async def resync(bot) -> dict:
    """Списать реестр бота с Discord и проверить, что скрытое действительно исчезло.

    Одной перезаписи сервера мало: глобально зарегистрированные команды Discord показывает
    в слэш-меню всегда, поэтому скрытая команда обязана уйти и из глобального списка.
    """
    global_bodies = _global_bodies(bot)
    test = list(getattr(bot, "test_guilds", None) or [])
    servers = test or [g.id for g in bot.guilds]
    await bot.bulk_overwrite_global_commands(global_bodies)
    if test:
        await bot._sync_application_commands()      # disnake сам пишет только эти серверы
    else:
        for gid in servers:
            bodies = [c.body for c in bot.application_commands
                      if not getattr(c, "guild_ids", None) or gid in c.guild_ids]
            await bot.bulk_overwrite_guild_commands(gid, bodies)
    left, checked = await _stragglers(bot)
    if not checked:
        note = " · живой список Discord не проверить"
    elif left:
        note = f" · не удалось убрать: {', '.join('/' + n for n in left)}"
    else:
        note = " · скрытых в списке не осталось"
    print(f"⚡ Панель: список команд перезаписан на {len(servers)} серверах{note}", flush=True)
    return {"guilds": len(servers), "left": left, "checked": checked}


# ───────────────────────── делегирование экрана ─────────────────────────
def _kind(annotation) -> str:
    """Как спросить аргумент у панели: число, участник, канал, да/нет или просто текст."""
    text = str(getattr(annotation, "__name__", annotation) or "").lower()
    if annotation is bool or "bool" in text:
        return "bool"
    if "member" in text or "user" in text:
        return "member"
    if "channel" in text or "thread" in text:
        return "channel"
    if annotation in (int, float) or "int" in text or "float" in text:
        return "number"
    return "text"


def _choices(info) -> list:
    """Варианты commands.Param(choices=…) в виде [[показывать, значение]].

    Коги передают то dict, то list[str], а disnake иногда отдаёт свой список Option —
    разбираем все три формы, чтобы селект в панели не оказался пустым.
    """
    raw = getattr(info, "choices", None)
    if not raw:
        return []
    try:
        pairs = list(raw.items()) if isinstance(raw, dict) else \
            [(getattr(o, "name", o), getattr(o, "value", o)) for o in raw]
    except Exception:
        return []
    return [[str(label), str(value)] for label, value in pairs
            if label is not None and value is not None][:25]


def _fields(callback):
    """(дефолты, поля формы) — то, что подставил бы disnake, и что нужно спросить у панели.

    Команды пишут `arg = commands.Param(default=…)`, и в __defaults__ функции лежит сам
    ParamInfo, а не его значение. Вызовешь колбэк напрямую — экран получит ParamInfo вместо
    None или «⭐» и покажет мусор, поэтому дефолты разбираем здесь.
    """
    params = list(inspect.signature(callback).parameters.values())[2:]  # после self и inter
    defaults: dict = {}
    fields: list = []
    for p in params:
        info = p.default if isinstance(p.default, commands.ParamInfo) else None
        if info is not None and info.required:
            fields.append({"name": p.name, "label": getattr(info, "description", "") or p.name,
                           "kind": _kind(p.annotation), "choices": _choices(info)})
            continue
        if info is not None:
            defaults[p.name] = info.default
            continue
        if p.default is inspect.Parameter.empty:
            fields.append({"name": p.name, "label": p.name,
                           "kind": _kind(p.annotation), "choices": []})
        else:
            defaults[p.name] = p.default
    return defaults, fields


def _options(callback):
    """Опции экрана, который открывается без аргументов, или None, если экран их требует."""
    defaults, fields = _fields(callback)
    return None if fields else defaults


def _digits(raw) -> int:
    parts = [p for p in str(raw or "").replace(",", " ").split() if p.isdigit()]
    return int(parts[-1]) if parts else 0


async def _fill(inter, field, raw):
    """Строку из формы превращаем в тот объект, который ждёт команда."""
    kind = field["kind"]
    if kind == "bool":
        return str(raw).strip().lower() in ("1", "true", "on", "yes", "да")
    if kind == "number":
        try:
            return int(str(raw).strip())
        except ValueError:
            raise commands.BadArgument(f"«{field['label']}» нужно числом")
    if kind in ("member", "channel"):
        what = "участника" if kind == "member" else "канал"
        ident = _digits(raw)
        if not ident:
            raise commands.BadArgument(f"«{field['label']}»: выбери {what} из списка")
        guild = getattr(inter, "guild", None)
        if kind == "channel":
            found = inter.bot.get_channel(ident) or await _fetch(inter.bot, "fetch_channel", ident)
        else:
            found = (guild.get_member(ident) if guild else None) or inter.bot.get_user(ident)
            if found is None:
                found = (await _fetch(inter.bot, "fetch_member", guild.id, ident) if guild
                         else await _fetch(inter.bot, "fetch_user", ident))
        if found is None:
            raise commands.BadArgument(f"Я не нашла {what} «{raw}»")
        return found
    return str(raw)


async def _fetch(bot, method: str, *args):
    """fetch_* с ловушкой: без прав или при таймауте возвращаем None, а не роняем экран."""
    try:
        return await getattr(bot, method)(*args)
    except Exception:
        return None


def _opener(cog, callback, defaults: dict, fields: list):
    async def open_screen(inter, given=None):
        kwargs = {}
        for name, value in defaults.items():
            if callable(value):                   # динамический default от disnake
                value = value(inter)
                if inspect.isawaitable(value):
                    value = await value
            kwargs[name] = value
        if fields:
            given = given or {}
            for field in fields:
                raw = given.get(field["name"])
                if raw is None or str(raw).strip() == "":
                    raise commands.BadArgument(f"В форме не хватает: {field['label']}")
                kwargs[field["name"]] = await _fill(inter, field, raw)
        await callback(cog, inter, **kwargs)

    return open_screen


def _callable_of(bot, ref: str):
    """(ког, колбэк) уже загруженного экрана либо None."""
    cog_name, method = ref.split(".")
    cog = bot.get_cog(cog_name)
    if cog is None:
        return None
    command = getattr(cog, method, None)          # disnake вешает объект команды на ког
    callback = getattr(command, "callback", None)
    if callback is None or not callable(callback):
        return None
    return cog, callback


def _target(bot, ref: str):
    """Экран из уже загруженного кога в виде func(inter) либо None.

    Экраны с обязательными аргументами сюда не проходят: у Discord-хаба формы нет, кнопка
    без аргументов вызвала бы команду криво.
    """
    pair = _callable_of(bot, ref)
    if pair is None:
        return None
    cog, callback = pair
    defaults, fields = _fields(callback)
    if fields:
        return None
    return _opener(cog, callback, defaults, [])


def web_target(bot, ref: str):
    """Как _target, но аргументы экрану отдаёт форма панели."""
    pair = _callable_of(bot, ref)
    if pair is None:
        return None
    cog, callback = pair
    defaults, fields = _fields(callback)
    return _opener(cog, callback, defaults, fields)


def args_of(bot, ref: str) -> list:
    """Что экран просит заполнить: [] — если открывается без аргументов."""
    pair = _callable_of(bot, ref)
    return _fields(pair[1])[1] if pair else []


def screen_of(ref: str):
    for _, _, items in SECTIONS:
        for icon, label, r, cmd in items:
            if r == ref:
                return label, cmd
    for icon, label, r, cmd in EXTRA_SCREENS:
        if r == ref:
            return label, cmd
    return ref, ref


# ───────────────────────── эмбед ─────────────────────────
def hub_embed(cat=None, guild=None) -> disnake.Embed:
    e = base_embed(
        "⚜️ Панель семьи",
        "Всё для руководства: сначала раздел, потом экран. Открывается тот же экран, что и по "
        "своей команде — права и кнопки не меняются.\n"
        "Набор и анкета — кнопка «💎 Набор».",
        guild=guild,
    )
    if cat is None:
        e.add_field(name="📂 Разделы",
                    value="\n".join(f"{icon} **{title}** · {len(items)} экранов"
                                    for icon, title, items in SECTIONS),
                    inline=False)
    else:
        icon, title, items = SECTIONS[cat]
        e.add_field(name=f"{icon} {title} · {len(items)} экранов",
                    value="\n".join(f"{i} **{label}**" for i, label, _, _ in items),
                    inline=False)
    e.set_footer(text=f"Hallez FAMQ • экранов: {TOTAL} • "
                      f"дублей команд скрыто: {len(_stash)}")
    return e


class HubButton(disnake.ui.Button):
    """Как HrButton в /hr: кнопка со своим обработчиком — их набирают по каталогу."""

    def __init__(self, handler, **kw):
        super().__init__(**kw)
        self._handler = handler

    async def callback(self, inter: disnake.MessageInteraction):
        await self._handler(inter)


class HubView(disnake.ui.View):
    def __init__(self, owner_id: int, cat=None):
        super().__init__(timeout=TIMEOUT)
        self.owner_id = owner_id
        self.cat = cat
        if cat is None:
            for i, (icon, title, _) in enumerate(SECTIONS):
                self.add_item(HubButton(lambda inter, n=i: self.open_cat(inter, n),
                                        label=title[:80], emoji=icon,
                                        style=disnake.ButtonStyle.secondary, row=min(i // 4, 3)))
        else:
            for i, (icon, label, _, _) in enumerate(SECTIONS[cat][2]):
                self.add_item(HubButton(lambda inter, n=i: self.open_screen(inter, cat, n),
                                        label=label[:80], emoji=icon,
                                        style=disnake.ButtonStyle.primary, row=min(i // 4, 3)))
            self.add_item(HubButton(self.back, label="Разделы", emoji="⬅️",
                                    style=disnake.ButtonStyle.secondary, row=4))
        self.add_item(HubButton(self.hr, label="Набор", emoji="💎",
                                style=disnake.ButtonStyle.success, row=4))
        hide = not flag_on()
        self.add_item(HubButton(
            self.toggle,
            label="Убрать дубли команд" if hide else "Вернуть дубли команд",
            emoji="🙈" if hide else "👁",
            style=disnake.ButtonStyle.danger if hide else disnake.ButtonStyle.secondary,
            row=4))

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:
        if inter.author.id != self.owner_id:
            await inter.response.send_message(
                embed=note_embed("⛔ Эта панель открыта не тобой — набери свою: `/панель`."),
                ephemeral=True)
            return False
        if not is_admin(inter):
            await inter.response.send_message(embed=note_embed("⛔ Недостаточно прав."),
                                              ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

    def menu(self, cat=None):
        return HubView(self.owner_id, cat)

    async def open_cat(self, inter, cat: int):
        await inter.response.edit_message(embed=hub_embed(cat, inter.guild),
                                          view=self.menu(cat))

    async def back(self, inter):
        await inter.response.edit_message(embed=hub_embed(None, inter.guild),
                                          view=self.menu(None))

    async def hr(self, inter):
        await self.open_screen(inter, None, None, ref=HR_REF)

    async def open_screen(self, inter, cat, idx, ref=None):
        if ref is None:
            _, label, ref, cmd = SECTIONS[cat][2][idx]
        else:
            label, cmd = screen_of(ref)
        target = _target(inter.bot, ref)
        if target is None:
            return await self._note(
                inter, f"⚠️ Экран «{label}» отсюда не открывается — модуль выключен или экран "
                       f"требует аргументы. Открой командой {cmd}.", cat)
        try:
            await target(inter)
        except Exception as error:
            return await self._note(
                inter, f"⚠️ «{label}» не открылся: `{type(error).__name__}: {error}`. "
                       f"Можно открыть командой {cmd}.", cat)

        if not inter.response.is_done():
            return await self._note(inter, f"⚠️ «{label}» ничего не ответил — открой командой {cmd}.", cat)

        # экран отложил ответ и не тронул его: сообщение висит «думает…», чиним сами
        if getattr(inter, "_original_response", None) is None and inter.response.type in (
            disnake.InteractionResponseType.deferred_channel_message,
            disnake.InteractionResponseType.deferred_message_update,
        ):
            if inter.response.type is disnake.InteractionResponseType.deferred_message_update:
                await inter.edit_original_response(embed=hub_embed(self.cat, inter.guild),
                                                   view=self.menu(self.cat))
            else:
                await inter.edit_original_response(
                    embed=note_embed(f"✅ «{label}» открыт отдельным сообщением — меню выше рабочее."))

    async def _note(self, inter, text, cat):
        embed = note_embed(text)
        if inter.response.is_done():
            try:
                await inter.followup.send(embed=embed, ephemeral=True)
            except Exception:
                pass
        else:
            await inter.response.send_message(embed=embed, view=self.menu(cat), ephemeral=True)

    async def toggle(self, inter):
        await inter.response.defer(ephemeral=True)
        value = not flag_on()
        flag_set(value)
        changed = apply_visibility(inter.bot)
        try:
            res = await resync(inter.bot)
            tail = (f"Список команд перезаписан на {res['guilds']} серверах — "
                    "меню Discord обновится само, обычно за минуту, иногда дольше."
                    if res["checked"] and not res["left"]
                    else "Часть списка Discord не отдал: "
                         f"{', '.join('/' + n for n in res['left']) or 'проверить не удалось'}. "
                         "Список перезапишется сам при следующем запуске.")
        except Exception as error:
            tail = (f"В Discord список не перезаписался: `{type(error).__name__}: {error}`. "
                    "Поможет рестарт бота.")
        names = ", ".join(f"/{n}" for n in changed)
        head = (f"🙈 Спрятал дубли: {names}.\n" if value
                else f"👁 Вернул команды: {names}.\n") if names else (
            "🙈 Прятать нечего — этих команд в реестре нет.\n" if value
            else "👁 Возвращать нечего: дубли и так на месте.\n")
        await inter.edit_original_response(embed=hub_embed(self.cat, inter.guild),
                                          view=self.menu(self.cat))
        await inter.followup.send(
            embed=note_embed(head + tail + " Передумал — `/reverse` вернёт как было."),
            ephemeral=True)


class AdminHub(commands.Cog):
    """Одна команда вместо двенадцати экранов."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        """Флаг «спрятать дубли» переживает рестарт, а Discord — нет.

        На старте main.py перезаписывает только списки серверов, а команды, которые этот бот
        когда-то зарегистрировал глобально, остаются в меню навсегда: скрытая команда
        перестаёт быть скрытой. После старта сверяем ещё и глобальный реестр.

        Сверку ведём с чтения живого списка и не сразу: своя перезапись у main.py и наша
        падают в один bucket /commands, и за второй Discord отвечает 429 с очередью на
        несколько минут. Если в списке и так нет дублей — не пишем ничего.
        """
        if self.bot is None or not flag_on():
            return
        apply_visibility(self.bot)
        await asyncio.sleep(25)
        try:
            left, checked = await _stragglers(self.bot)
            if checked and not left:
                print("⚡ Панель: дублей в живом списке Discord нет — перезапись не нужна",
                      flush=True)
                return
            await resync(self.bot)
        except Exception as error:
            print(f"⚠️ Панель: дубли не перезаписать в Discord "
                  f"({type(error).__name__}: {error})", flush=True)

    @commands.slash_command(
        name="панель",
        description="Все админ-экраны семьи в одном меню",
        contexts=disnake.InteractionContextTypes(guild=True),
    )
    @commands.guild_only()
    async def hub(self, inter: disnake.ApplicationCommandInteraction):
        if not is_admin(inter):
            return await inter.response.send_message(
                embed=note_embed("⛔ Недостаточно прав."), ephemeral=True)
        await inter.response.send_message(embed=hub_embed(guild=inter.guild),
                                          view=HubView(inter.author.id), ephemeral=True)


def setup(bot: commands.Bot):
    apply_visibility(bot)
    if bot.is_ready():
        # ког перезагружен после /reverse — Discord уже надо пересинхронизировать самим
        bot.loop.create_task(resync(bot))
    bot.add_cog(AdminHub(bot))
