"""/панель — все админ-экраны семьи в одном меню.

Экраны жили по своим командам (/hall, /hallpanel, /admin, /центр, /golos, /logs panel,
/teg, /media_send ...), и искать нужный по названиям утомительно. Здесь два уровня
кнопок: раздел и экран. Хаб ничего не переписывает — он вызывает уже готовый экран того
же кога, поэтому права, кнопки и тексты раздела ровно те, что были у его команды.

В каталог попали только экраны, которые открываются без аргументов. Экраны, которым нужны
участник, причина или картинка (уволить, выговор, баллы, баннер-файл), остаются своими
командами: подставить им аргументы из меню нельзя, а вызвать без них — значит молча
сломать половину кнопок.

Вторая панель — /hr (набор и анкета), она кнопкой внизу.

Кнопка «🙈 Убрать дубли» прячет из меню Discord те команды, которые /панель открывает не
хуже. Флаг лежит в panel_state.json, а он входит в снимки backups/, поэтому /reverse
возвращает список команд целиком — без рестарта и без правок кода.
"""
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
SECTIONS = [
    ("👥", "Состав и кадры", [
        ("🧑‍🤝‍🧑", "Состав, роли и ранги", "HallTools.hall", "/hall"),
        ("🏠", "Hall-панель: приветствие, напоминания, АФК, доступ", "HallPanel.hallpanel", "/hallpanel"),
        ("⚜️", "Центр управления: логи, роли, отпуска", "ControlCenter.center", "/центр"),
        ("🧾", "Карточка бойца", "Profile.profile_command", "/profile"),
        ("🪪", "Карточка семьи", "FamqControl.card", "/famq-card"),
        ("🖼", "Карточка картинкой", "ProfileCard.card", "/карточка-фото"),
        ("🔎", "Проверка активности состава", "ActivityCenter.activity_check", "/activity_check"),
        ("📈", "Список на повышение по активу", "ActivityCenter.for_improve", "/for_improve"),
    ]),
    ("🏖", "Отпуска, АФК, списки", [
        ("🌴", "Кто сейчас в отпуске", "HallTools.leave_list", "/отпуск-список"),
        ("💤", "Кто сейчас в АФК", "HallTools.afk_list", "/афк-список"),
        ("🗑", "Записи чёрного списка", "FamqControl.bl_list", "/famq-blacklist list"),
        ("🎧", "Онлайн в голосовых семьи", "FamqControl.voice_top_cmd", "/famq-voice top"),
        ("🧮", "Статистика рекрутеров", "FamqControl.recruiters", "/famq-recruiters"),
        ("📊", "Статистика семьи: обновить сейчас", "FamqControl.livestats_refresh", "/famq-livestats refresh"),
    ]),
    ("🎙", "Голос, сборы, звонки", [
        ("🔊", "Настройка голоса бота", "Govorit.golos", "/golos"),
        ("🎛", "Управление голосом (роль с доступом)", "Govorit.govorit_panel", "/govorit-panel"),
        ("🏢", "Сервер: канал, режим «всегда в войсе»", "GovoritStay.golos_server", "/golos-server"),
        ("🔇", "Заглушить речь и выйти из войса", "Govorit.zamolchi", "/zamolchi"),
        ("📍", "Teg panel: теги на сборы", "TegPanel.teg", "/teg"),
        ("🙋", "Кто может пользоваться Teg panel", "TegPanel.teg_access", "/teg_access"),
        ("📋", "Кто ответил на последний сбор", "TegPanel.teg_report", "/teg_report"),
        ("📤", "Опубликовать кнопку Teg panel в этом канале", "TegPanel.teg_post", "/teg_post"),
    ]),
    ("🗂", "Логи и память", [
        ("📁", "Панель логов семьи", "FamLogs.logs_panel", "/logs panel"),
        ("👀", "Куда льются логи: смотреть", "FamLogs.logs_view", "/logs view"),
        ("🏗", "Логи: создать приватную категорию", "FamLogs.logs_auto", "/logs auto"),
        ("🧠", "Сколько памяти ест бот", "Memory.mem", "/mem"),
    ]),
    ("⚙️", "Настройки бота", [
        ("🎛", "Главная админ-панель: тексты, каналы, роли", "AdminCog.admin_command", "/admin"),
        ("🧪", "Проверка всех оформленных текстов", "AdminCog.test_texts", "/test_texts"),
        ("😀", "Какие эмодзи пака видит бот", "HallTools.hz_emojis", "/hz_emojis"),
        ("🔤", "Список эмодзи сервера", "BannerPanel.emoji_list", "/emoji_list"),
    ]),
    ("📣", "Панели и рассылки", [
        ("🎯", "Панель набора: опубликовать", "BannerPanel.recruit_panel", "/recruit_panel"),
        ("🏞", "Баннер под заявками", "BannerPanel.banner", "/banner"),
        ("📺", "Анонс стрима в личные", "Media.media_send", "/media_send"),
        ("📢", "Правила семьи", "HallRecruit.rules_cmd", "/rules"),
        ("🎭", "Панель розыгрышей-пугалок", "PrankCog.prank_panel", "/prank_panel"),
    ]),
    ("✍️", "Набор — вторая панель", [
        ("💎", "Рекрут-центр: анкета, баллы, магазин, эмодзи", "HallRecruit.hr", "/hr"),
        ("🔓", "Доступ рекрутеров ко всем веткам", "HallRecruit.hr_access", "/hr_access"),
        ("🗃", "Загрузить эмодзи из папки", "HallRecruit.hr_emoji_upload", "/hr_emoji_upload"),
        ("🎫", "Загрузить стикеры из папки", "HallRecruit.hr_sticker_upload", "/hr_sticker_upload"),
        ("💬", "Баллы рекрута", "HallRecruit.points", "/points"),
    ]),
    ("🔧", "Сервис и откат", [
        ("💾", "Снять состояние в backups/ сейчас", "StateSave.update_save", "/update_save"),
        ("↩️", "Откатить настройки и базу на шаг назад", "Reverse.reverse", "/reverse"),
    ]),
]

TOTAL = sum(len(items) for _, _, items in SECTIONS)
HR_REF = "HallRecruit.hr"

# Чистые панели-дубли: /панель открывает их ровно так же, как они сами. Команд, с которыми
# что-то делают аргументами (staff, logs, famq-warn, hr_banner, /banner), здесь нет — из
# меню их не заменить, и прятать их значило бы отобрать у руководства выговоры и баннеры.
# /codex — алиас /admin, тот же экран.
HIDDEN = (
    "admin", "codex", "test_texts", "hall", "hallpanel", "hz_emojis", "центр",
    "activity_check", "for_improve", "отпуск-список", "афк-список", "famq-recruiters",
    "golos", "golos-server", "govorit-panel", "zamolchi", "teg", "teg_access",
    "emoji_list", "mem", "prank_panel", "media_send", "hr_access",
    "hr_emoji_upload", "hr_sticker_upload",
)

_stash: dict = {}


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


async def resync(bot) -> int:
    """Переписать список команд в Discord, чтобы изменение было видно сразу."""
    if getattr(bot, "test_guilds", None):
        await bot._sync_application_commands()
        return len(bot.guilds)
    for g in bot.guilds:
        cmds = [c.body for c in bot.application_commands
                if not getattr(c, "guild_ids", None) or g.id in c.guild_ids]
        await bot.bulk_overwrite_guild_commands(g.id, cmds)
    return len(bot.guilds)


# ───────────────────────── делегирование экрана ─────────────────────────
def _options(callback):
    """Опции, которые disnake подставил бы сам, или None — если экран без аргументов не открыть.

    Команды пишут `arg = commands.Param(default=…)`, и в __defaults__ функции лежит сам
    ParamInfo, а не его значение. Вызовешь колбэк напрямую — экран получит ParamInfo вместо
    None или «⭐» и покажет мусор, поэтому дефолты разбираем здесь.
    """
    params = list(inspect.signature(callback).parameters.values())[2:]  # после self и inter
    out = {}
    for p in params:
        default = p.default
        if isinstance(default, commands.ParamInfo):
            if default.required:
                return None
            out[p.name] = default.default
        elif default is inspect.Parameter.empty:
            return None
    return out


def _target(bot, ref: str):
    """Экран из уже загруженного кога в виде func(inter) либо None."""
    cog_name, method = ref.split(".")
    cog = bot.get_cog(cog_name)
    if cog is None:
        return None
    command = getattr(cog, method, None)          # disnake вешает объект команды на ког
    callback = getattr(command, "callback", None)
    if callback is None or not callable(callback):
        return None
    options = _options(callback)
    if options is None:
        return None

    async def open_screen(inter):
        kwargs = {}
        for name, value in options.items():
            if callable(value):                   # динамический default от disnake
                value = value(inter)
                if inspect.isawaitable(value):
                    value = await value
            kwargs[name] = value
        await callback(cog, inter, **kwargs)

    return open_screen


def screen_of(ref: str):
    for _, _, items in SECTIONS:
        for icon, label, r, cmd in items:
            if r == ref:
                return label, cmd
    return ref, ref


# ───────────────────────── эмбед ─────────────────────────
def hub_embed(cat=None, guild=None) -> disnake.Embed:
    e = base_embed(
        "⚜️ Панель семьи",
        "Здесь всё для руководства: сначала раздел, потом экран. Открывается тот же экран, "
        "что и своей командой — права и кнопки не меняются.\n"
        "Вторая панель — набор и анкета, кнопка «💎 Набор» внизу.",
        guild=guild,
    )
    if cat is None:
        e.add_field(name="📂 Разделы — жми кнопку",
                    value="\n".join(f"{icon} **{title}** · {len(items)}"
                                    for icon, title, items in SECTIONS),
                    inline=False)
    else:
        icon, title, items = SECTIONS[cat]
        e.add_field(name=f"{icon} {title}",
                    value="\n".join(f"{i} **{label}**\n└ `{cmd}`" for i, label, _, cmd in items),
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
            servers = await resync(inter.bot)
            tail = f"Список команд обновлён на {servers} серверах — видно в Discord сразу."
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
