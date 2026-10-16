"""
/статус — быстрая проверка здоровья бота (только руководство, ответ виден только вам):
аптайм, пинг, база данных, фоновые задачи (кто остановился или упал), память,
подключённые модули, важные настройки и последняя ошибка из bot.log.
"""
import os
import time
from typing import List, Tuple

import disnake
from disnake.ext import commands

import database
from utils import famq_core as core

START = time.time()
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _dur(sec: float) -> str:
    sec = int(sec)
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    parts = ([f"{d} д"] if d else []) + ([f"{h} ч"] if h or d else []) + [f"{m} мин"]
    return " ".join(parts)


def _memory_mb() -> str:
    try:
        with open("/proc/self/status", encoding="utf-8") as f:  # Linux
            for line in f:
                if line.startswith("VmRSS:"):
                    return f"{int(line.split()[1]) / 1024:.0f} МБ"
    except Exception:
        pass
    try:
        import resource  # macOS / Linux
        v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return f"{v / (1024 if os.name != 'darwin' else 1024 * 1024):.0f} МБ"
    except Exception:
        return "н/д"


def _last_error() -> str:
    """Последняя строка с ERROR/Traceback из хвоста bot.log (читаем только последние ~64 КБ)."""
    path = os.path.join(BASE_DIR, "bot.log")
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - 65536))
            tail = f.read().decode("utf-8", errors="replace").splitlines()
    except Exception:
        return "лог не найден"
    for line in reversed(tail):
        if "ERROR" in line or "Traceback" in line or "Exception" in line:
            return core.clip(line.strip(), 180)
    return "ошибок в конце лога нет"


def loops_state(bot: commands.Bot) -> Tuple[int, List[str]]:
    """(сколько фоновых задач всего, список проблемных строк)."""
    total, bad = 0, []
    for name, cog in bot.cogs.items():
        for attr in dir(cog):
            if attr.startswith("__"):
                continue
            try:
                obj = getattr(cog, attr)
            except Exception:
                continue
            if obj.__class__.__name__ != "Loop":
                continue
            total += 1
            try:
                failed = obj.failed() if hasattr(obj, "failed") else False
                running = obj.is_running()
            except Exception:
                continue
            if failed:
                bad.append(f"❌ `{name}.{attr}` — упала с ошибкой")
            elif not running:
                bad.append(f"⚠️ `{name}.{attr}` — не запущена")
    return total, bad


class Status(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command(name="статус", description="🩺 Состояние бота: жив ли он и ничего ли не сломалось")
    @commands.guild_only()
    async def status(self, inter: disnake.ApplicationCommandInteraction):
        if not core.is_staff(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        problems: List[str] = []

        # пинг
        lat_ms = self.bot.latency * 1000 if self.bot.latency == self.bot.latency else -1
        if lat_ms < 0 or lat_ms > 1000:
            problems.append("⚠️ Высокий пинг до Discord" if lat_ms > 0 else "⚠️ Нет ответа от шлюза Discord")

        # база данных
        t0 = time.perf_counter()
        try:
            row = await core.q_one("SELECT COUNT(*) AS c FROM family_members")
            members = row["c"] if row else 0
            db_ms = (time.perf_counter() - t0) * 1000
            try:
                db_size = f"{os.path.getsize(database.DB_PATH) / 1024 / 1024:.1f} МБ"
            except Exception:
                db_size = "н/д"
            db_line = f"✅ отвечает за {db_ms:.0f} мс · {db_size} · в составе {members} чел."
        except Exception as ex:
            db_line = f"❌ ошибка: {core.clip(str(ex), 120)}"
            problems.append("❌ База данных не отвечает")

        # фоновые задачи
        total, bad = loops_state(self.bot)
        problems += bad
        loops_line = f"✅ работают все ({total})" if not bad else f"{len(bad)} из {total} с проблемами"

        # настройки
        checks = []
        if not core.get("logs.default") and not any(core.get(f"logs.{k}") for k in ("bot", "audit")):
            checks.append("⚠️ не выбран канал логов")
        if not core.rank_roles_map():
            checks.append("⚠️ не настроены роли рангов")
        provider = os.getenv("EVENT_ART_PROVIDER", "").strip().lower() or (
            "gemini" if os.getenv("GEMINI_API_KEY", "").strip() else "pollinations")
        art = {"gemini": "Gemini", "pollinations": "Pollinations (запасной)", "openai": "OpenAI",
               "none": "выключена", "off": "выключена"}.get(provider, provider)
        checks.append(f"🎨 Нейросеть для картинок: **{art}**")
        problems += [c for c in checks if c.startswith("⚠️")]

        kind = "ok" if not problems else ("err" if any(p.startswith("❌") for p in problems) else "warn")
        e = core.make_embed("🩺 Состояние бота", "Всё в порядке ✅" if not problems else
                            "Есть что проверить:\n" + "\n".join(problems), kind=kind)
        e.add_field("⏱️ Аптайм", _dur(time.time() - START), inline=True)
        e.add_field("📡 Пинг", f"{lat_ms:.0f} мс" if lat_ms >= 0 else "н/д", inline=True)
        e.add_field("🧠 Память", _memory_mb(), inline=True)
        e.add_field("🗄️ База данных", db_line, inline=False)
        e.add_field("🔁 Фоновые задачи", loops_line, inline=True)
        e.add_field("🧩 Модулей", str(len(self.bot.cogs)), inline=True)
        e.add_field("🌐 Серверов", str(len(self.bot.guilds)), inline=True)
        e.add_field("⚙️ Настройки", "\n".join(checks), inline=False)
        e.add_field("📜 Последняя ошибка в логе", f"`{_last_error()}`", inline=False)
        await inter.edit_original_response(embed=e)


def setup(bot: commands.Bot):
    bot.add_cog(Status(bot))
