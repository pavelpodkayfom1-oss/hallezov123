"""
Диагностика памяти бота.

Хостинг режет лимитом RAM, поэтому раз в 10 минут пишем в консоль, сколько едим
и сколько участников держим в кэсе, а /mem показывает развёрнутый отчёт.
"""
import asyncio
import gc
import os
import sys
from collections import Counter

import disnake
from disnake.ext import commands, tasks

from utils.embeds import get_color, note_embed

REPORT_INTERVAL_MIN = 10


def _proc_status() -> dict:
    """VmRSS/VmHWM/VmSize из /proc — работает на Linux-хостах."""
    out = {}
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith(("VmRSS:", "VmHWM:", "VmSize:", "VmData:")):
                    out[line.split(":")[0]] = int(line.split()[1]) // 1024
    except Exception:
        pass
    return out


def _child_processes() -> int:
    """Сколько дочерних процессов у бота (ffmpeg, резолвер)."""
    try:
        with open(f"/proc/{os.getpid()}/task/{os.getpid()}/children", encoding="utf-8") as f:
            return len(f.read().split())
    except Exception:
        return -1


def _cached_members(bot) -> int:
    total = 0
    for g in getattr(bot, "guilds", []):
        total += len(getattr(g, "_members", {}) or {})
    return total


def _cached_messages(bot) -> int:
    try:
        return len(bot._connection._messages)
    except Exception:
        return -1


def _rss_mb() -> int:
    return _proc_status().get("VmRSS", -1)


class Memory(commands.Cog):
    """Показывает, куда уходит RAM на слабом хосте."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def cog_unload(self):
        self.report.cancel()

    @tasks.loop(minutes=REPORT_INTERVAL_MIN)
    async def report(self):
        await self._trim()
        st = _proc_status()
        print(
            "[mem] "
            f"RSS={st.get('VmRSS', -1)}МБ пик={st.get('VmHWM', -1)}МБ "
            f"виртуально={st.get('VmSize', -1)}МБ | "
            f"участников в кэсе={_cached_members(self.bot)} | "
            f"сообщений={_cached_messages(self.bot)} | "
            f"когов={len(self.bot.extensions)} | дочерних процессов={_child_processes()}",
            flush=True,
        )

    @report.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    @staticmethod
    async def _trim():
        """Собирает мусор и просит glibc вернуть свободную память системе."""
        def do():
            gc.collect()
            try:
                import ctypes

                ctypes.CDLL("libc.so.6").malloc_trim(0)
            except Exception:
                pass

        await asyncio.to_thread(do)

    @commands.slash_command(
        name="mem",
        description="Сколько памяти ест бот и кто главный потребитель",
        default_member_permissions=disnake.Permissions(manage_guild=True),
    )
    async def mem(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        st = await asyncio.to_thread(_proc_status)
        members = _cached_members(self.bot)
        per_guild = ", ".join(
            f"{g.name}: {len(getattr(g, '_members', {}) or {})} из {g.member_count}"
            for g in self.bot.guilds
        )
        await self._trim()
        after = await asyncio.to_thread(_proc_status)

        def top_types():
            gc.collect()
            sizes = Counter()
            for obj in gc.get_objects():
                try:
                    sizes[type(obj).__name__] += sys.getsizeof(obj)
                except (TypeError, ReferenceError):
                    continue
            return sizes.most_common(8)

        tops = await asyncio.to_thread(top_types)

        e = disnake.Embed(title="🧠 Память бота", color=get_color("embed_color"))
        e.add_field(
            name="Процесс",
            value=(
                f"RSS: **{st.get('VmRSS', -1)} МБ** (пик {st.get('VmHWM', -1)} МБ)\n"
                f"После сборки мусора: {after.get('VmRSS', -1)} МБ\n"
                f"Виртуально: {st.get('VmSize', -1)} МБ\n"
                f"Дочерних процессов: {_child_processes()}"
            ),
            inline=False,
        )
        e.add_field(
            name="Кэш Discord.py",
            value=(
                f"Участников в кэсе: **{members}**\n"
                f"По серверам: {per_guild or '—'}\n"
                f"Сообщений в кэсе: {_cached_messages(self.bot)}\n"
                f"Модулей загружено: {len(self.bot.extensions)}"
            ),
            inline=False,
        )
        e.add_field(
            name="Топ объектов Python",
            value="\n".join(f"`{n}` — {s / 1048576:.1f} МБ" for n, s in tops) or "—",
            inline=False,
        )
        await inter.edit_original_response(embed=e)


def setup(bot: commands.Bot):
    bot.add_cog(Memory(bot))
