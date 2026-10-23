"""
/update_save — зафиксировать текущие настройки и базу как эталон.

Файлы состояния (*.json, *.db в корне бота) лежат в репозитории, поэтому деплой
из git возвращает их к закоммиченной версии и приветствие, каналы и баллы слетают.
Снимок в backups/ (он в git не входит) переживает деплой, а guard_after_deploy()
в main.py поднимает его обратно до загрузки когов.
"""
import asyncio

import disnake
from disnake.ext import commands

from utils import state_backup
from utils.checks import is_admin
from utils.embeds import note_embed


class StateSave(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command(name="update_save",
                            description="Сохранить настройки и базу, чтобы деплой их не снёс")
    async def update_save(self, inter: disnake.ApplicationCommandInteraction):
        if not is_admin(inter):
            await inter.response.send_message(embed=note_embed("⛔ Недостаточно прав."), ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        dest = await asyncio.to_thread(state_backup.snapshot, True)

        files = state_backup.state_files()
        kb = sum(p.stat().st_size for p in files) // 1024
        lines = [f"💾 **Состояние зафиксировано** — {len(files)} файлов, {kb} КБ."
                 if dest else "💾 Новых изменений нет — эталон уже актуален."]
        if dest:
            lines.append(f"Снимок: `backups/{dest.name}`")
        fresh = state_backup.newest()
        lines.append(f"Снимков в `backups/`: **{state_backup.snapshot_count()}**"
                     + (f", самый свежий `{fresh.name}`" if fresh else ""))
        lines.append("Дальше при старте бот сам вернёт это состояние, если деплой из git "
                     "откатит файлы. Фон также снимает копию раз в минуту и сразу после "
                     "заявки/заказа/покупки, так что теряться должно максимум минута.")
        if state_backup.LAST_RESTORE:
            lines.append("\n♻️ В этот запуск поднято из снимка: "
                         + ", ".join(f"`{n}`" for n in state_backup.LAST_RESTORE))
        else:
            lines.append("\n♻️ В этот запуск ничего не откатывалось — файлы состояния целые.")
        await inter.followup.send(embed=note_embed("\n".join(lines)), ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(StateSave(bot))
