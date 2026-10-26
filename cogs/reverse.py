"""/reverse — откатить настройки и базу на шаг назад.

Бот снимает состояние в backups/ каждую минуту и сразу после важной записи, поэтому
«не понравилось оформление» или «вернулись старые баги» откатывается одной командой:
снимок поднимается поверх живых файлов, а коги, которые держат эти файлы в памяти,
перезагружаются. Откат обратим — то, что было живым до отката, тоже сохраняется снимком,
так что /reverse ещё раз возвращает как было.
"""
import asyncio
import sys
from datetime import datetime

import disnake
from disnake.ext import commands

from utils import state_backup
from utils.checks import is_admin
from utils.embeds import note_embed

# какой ког держит какой файл: после подъёма снимка его надо перезагрузить, иначе он
# вернёт старое содержимое обратно на диск
FILE_COGS = {
    "recruit_plus.json": ("cogs.hall_recruit",),
    "hall_settings.json": ("cogs.hall_tools", "cogs.hall_panel"),
    "golos_settings.json": ("cogs.govorit", "cogs.govorit_stay"),
    "golos_guild.json": ("cogs.govorit", "cogs.govorit_stay"),
    "staff_settings.json": ("cogs.staff_tools",),
    "teg_settings.json": ("cogs.teg_panel",),
    "logs_settings.json": ("cogs.famq_logs",),
    "panel_settings.json": ("cogs.famq_logs", "cogs.web_panel"),
    "panel_state.json": ("cogs.web_panel",),
    "emoji_map.json": ("cogs.hall_recruit", "cogs.promotions"),
}
MAX_SHOTS = 5


def _exts(files) -> list:
    out = []
    for name in files:
        for ext in FILE_COGS.get(name, ()):
            if ext not in out:
                out.append(ext)
    return out


def _stamp(path) -> str:
    try:
        # хвост `_2` у имени — снимок, сделанный в ту же секунду, что и предыдущий
        return datetime.strptime("_".join(path.name.split("_")[:2]),
                                 "%Y-%m-%d_%H%M%S").strftime("%d.%m %H:%M:%S")
    except ValueError:
        return path.name


def _files(path) -> list:
    return sorted(f.name for f in path.iterdir() if f.is_file())


def _kb(path) -> int:
    return sum(f.stat().st_size for f in path.iterdir() if f.is_file()) // 1024


def intro_embed(cands) -> disnake.Embed:
    lines = "\n".join(f"**{i}.** {_stamp(p)} — {len(_files(p))} файлов, {_kb(p)} КБ"
                      for i, p in enumerate(cands, 1))
    return note_embed("↩️ **Откат настроек, текстов и базы**\n"
                      f"Есть чем откатывать:\n{lines}\n\n"
                      "Выбери шаг ниже — покажу, что вернётся, и спрошу подтверждение.")


def confirm_embed(path) -> disnake.Embed:
    return note_embed(f"↩️ **Откатить к {_stamp(path)}?**\n"
                      "Вернёмся так: " + ", ".join(f"`{n}`" for n in _files(path)) + "\n\n"
                      "Сейчас живое уйдёт в новый снимок, так что передумав — жми /reverse "
                      "ещё раз и выбирай самый свежий шаг.")


async def _republish(bot) -> str:
    """Панель заявки висит в канале сообщением — после отката её надо перерисовать."""
    fn = getattr(sys.modules.get("cogs.hall_recruit"), "publish_panel", None)
    if not fn:
        return "Панель заявки не перерисована: /hr → «Опубликовать»."
    try:
        ok, text = await fn("apply")
    except Exception as e:
        return f"Панель заявки не перерисована ({e}): /hr → «Опубликовать»."
    return ("Панель заявки перерисована: " if ok else "Панель заявки не перерисовалась: ") + text


class _Owner(disnake.ui.View):
    """Общая часть: команда ephemeral, и пользоваться ею может только тот, кто вызвал."""

    def __init__(self, author_id: int, path=None):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.path = path

    async def interaction_check(self, inter) -> bool:
        return inter.author.id == self.author_id

    async def on_timeout(self):
        for c in self.children:
            c.disabled = True
        try:
            await self.message.edit(view=self)
        except Exception:
            pass


class _Pick(disnake.ui.Select):
    def __init__(self, owner: _Owner, cands: list):
        self.owner = owner
        self.cands = cands
        super().__init__(
            custom_id="reverse:pick", placeholder="Куда откатывать",
            options=[disnake.SelectOption(label=f"шаг {i} — {_stamp(p)}", value=str(i),
                                          description=f"{len(_files(p))} файлов, {_kb(p)} КБ")
                     for i, p in enumerate(cands, 1)])

    async def callback(self, inter):
        path = self.cands[int(self.values[0]) - 1]
        self.owner.path = path
        await inter.response.edit_message(embed=confirm_embed(path),
                                          view=_Confirm(inter.author.id, path, self.cands))


class _Go(disnake.ui.Button):
    def __init__(self, owner: _Owner):
        self.owner = owner
        super().__init__(label="Да, откатить", style=disnake.ButtonStyle.danger,
                         emoji="↩️", custom_id="reverse:go")

    async def callback(self, inter):
        await inter.response.defer(ephemeral=True)
        try:
            res = await asyncio.to_thread(state_backup.restore, self.owner.path.name)
        except Exception as e:
            await inter.followup.send(embed=note_embed(f"❌ Откат не получился: {e}"), ephemeral=True)
            return
        bad = []
        for ext in _exts(res["restored"]):
            try:
                inter.bot.reload_extension(ext)
            except Exception as e:
                bad.append(f"• `{ext}` — {e}")
        lines = [f"↩️ **Откатил к {_stamp(self.owner.path)}** — вернул "
                 f"{len(res['restored'])} файлов: " + ", ".join(f"`{n}`" for n in res["restored"])]
        if res["saved_to"]:
            lines.append(f"Живое до отката сохранено в снимке `{res['saved_to']}` — "
                         "/reverse ещё раз вернёт его.")
        if "recruit_plus.json" in res["restored"]:
            lines.append(await _republish(inter.bot))
        if bad:
            lines.append("⚠️ Эти панели в памяти не обновились, нужен рестарт бота:\n"
                         + "\n".join(bad))
        await inter.followup.send(embed=note_embed("\n".join(lines)), ephemeral=True)


class _Cancel(disnake.ui.Button):
    def __init__(self, owner: _Owner, cands: list):
        self.cands = cands
        super().__init__(label="Отмена", style=disnake.ButtonStyle.secondary,
                         emoji="✖️", custom_id="reverse:cancel")

    async def callback(self, inter):
        await inter.response.edit_message(embed=intro_embed(self.cands),
                                          view=_Choice(inter.author.id, self.cands))


class _Choice(_Owner):
    def __init__(self, author_id: int, cands: list):
        super().__init__(author_id)
        self.add_item(_Pick(self, cands))


class _Confirm(_Owner):
    def __init__(self, author_id: int, path, cands: list):
        super().__init__(author_id, path)
        self.add_item(_Go(self))
        self.add_item(_Cancel(self, cands))


class Reverse(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command(name="reverse", dm_permission=False,
                            description="Откатить настройки, тексты и базу на шаг назад")
    async def reverse(self, inter: disnake.ApplicationCommandInteraction):
        if not is_admin(inter):
            return await inter.response.send_message(embed=note_embed("⛔ Недостаточно прав."),
                                                     ephemeral=True)
        await inter.response.defer(ephemeral=True)
        cands = await asyncio.to_thread(state_backup.candidates, MAX_SHOTS)
        if not cands:
            return await inter.followup.send(
                embed=note_embed("Откатывать нечего: живые файлы совпадают со всеми снимками "
                                 "в `backups/`."), ephemeral=True)
        await inter.followup.send(embed=intro_embed(cands), view=_Choice(inter.author.id, cands))


def setup(bot: commands.Bot):
    bot.add_cog(Reverse(bot))
