"""/панель — все админ-экраны семьи в одном меню.

Экраны живут в десятке когов (/hall, /hallpanel, /staff, /admin, /центр, /golos, /logs
panel, /teg, /media_send ...), и искать нужный по названиям команд утомительно. Хаб ничего
не переписывает: он вызывает уже готовый экран того же кога, поэтому права, кнопки и
поведение каждого раздела остаются ровно те, что были у его команды.

В каталог попадают только экраны, которые открываются без аргументов. Экраны, которым
нужны участник, причина или картинка (уволить, выговор, баллы, баннер-файл), остаются
своими командами: подставить им аргументы из меню нельзя, а вызвать без них — значит
молча сломать половину кнопок.

Вторая панель — /hr (рекрут-центр): там оформление заявки и набора, он вызывается и
отсюда, и своей командой.
"""
import inspect

import disnake
from disnake.ext import commands

from utils.checks import is_admin
from utils.embeds import base_embed, note_embed

TIMEOUT = 900

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
    ("🗂", "Логи и статистика", [
        ("📁", "Панель логов семьи", "FamLogs.logs_panel", "/logs panel"),
        ("👀", "Куда льются логи: смотреть", "FamLogs.logs_view", "/logs view"),
        ("🏗", "Логи: создать приватную категорию", "FamLogs.logs_auto", "/logs auto"),
        ("🧠", "Сколько памяти ест бот", "Memory.mem", "/mem"),
    ]),
    ("⚙️", "Настройки бота", [
        ("🎛", "Главная админ-панель: тексты, каналы, роли, рассылки", "AdminCog.admin_command", "/admin"),
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


def _options(callback):
    """Опции, которые disnake подставил бы сам, или None — если экран без аргументов не открыть.

    Команды пишут `arg = commands.Param(default=...)`, и в __defaults__ функции лежит сам
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


def hub_embed(cat: int = 0, guild=None) -> disnake.Embed:
    icon, title, items = SECTIONS[cat]
    e = base_embed(
        "⚜️ Панель семьи",
        "Категория — экран. Открывается тот же экран, что и своей командой: права, кнопки "
        "и тексты не меняются.\n"
        "Экран встанет рядом или заменит меню — тогда вернись кнопкой «Меню» или снова "
        "`/панель`. Набор и оформление заявки — в `/hr`.",
        guild=guild,
    )
    e.add_field(name="📂 Категории",
                value="  ".join(f"{i}{t}" for i, t, _ in SECTIONS), inline=False)
    e.add_field(name=f"{icon} {title}",
                value="\n".join(f"{i} {label}" for i, label, _, _ in items), inline=False)
    e.set_footer(text=f"Hallez FAMQ • экранов в меню: {TOTAL}")
    return e


class HubView(disnake.ui.View):
    def __init__(self, owner_id: int, cat: int = 0):
        super().__init__(timeout=TIMEOUT)
        self.owner_id = owner_id
        self.cat = cat
        self.cat_select.options = [
            disnake.SelectOption(label=title, emoji=icon, value=str(i),
                                 description=f"{len(items)} экранов",
                                 default=(i == cat))
            for i, (icon, title, items) in enumerate(SECTIONS)
        ]
        self.act_select.options = [
            disnake.SelectOption(label=label, emoji=icon, value=f"{cat}:{i}",
                                 description=f"команда {cmd}"[:100])
            for i, (icon, label, ref, cmd) in enumerate(SECTIONS[cat][2])
        ]

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

    @disnake.ui.string_select(custom_id="hub:cat", placeholder="📂 Категория панели", row=0,
                              options=[disnake.SelectOption(label="Состав", value="0")])
    async def cat_select(self, select: disnake.ui.StringSelect, inter: disnake.MessageInteraction):
        cat = max(min(int(select.values[0]), len(SECTIONS) - 1), 0)
        await inter.response.edit_message(
            embed=hub_embed(cat, inter.guild), view=HubView(self.owner_id, cat))

    @disnake.ui.string_select(custom_id="hub:act", placeholder="🧭 Экран внутри категории", row=1,
                              options=[disnake.SelectOption(label="—", value="0:0")])
    async def act_select(self, select: disnake.ui.StringSelect, inter: disnake.MessageInteraction):
        cat, idx = (int(x) for x in select.values[0].split(":"))
        _, label, ref, cmd = SECTIONS[cat][2][idx]
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
                                                   view=HubView(self.owner_id, self.cat))
            else:
                await inter.edit_original_response(
                    embed=note_embed(f"✅ «{label}» открыт отдельным сообщением — меню выше рабочее."))

    async def _note(self, inter: disnake.MessageInteraction, text: str, cat: int) -> None:
        embed = note_embed(text)
        if inter.response.is_done():
            try:
                await inter.followup.send(embed=embed, ephemeral=True)
            except Exception:
                pass
        else:
            await inter.response.send_message(embed=embed, view=HubView(self.owner_id, cat),
                                              ephemeral=True)

    @disnake.ui.button(label="Меню", emoji="🔄", style=disnake.ButtonStyle.secondary, row=2)
    async def refresh(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.edit_message(
            embed=hub_embed(self.cat, inter.guild), view=HubView(self.owner_id, self.cat))


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
    bot.add_cog(AdminHub(bot))
