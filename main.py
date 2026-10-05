import os
import sys
import asyncio
import logging
import disnake
from disnake.ext import commands
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from database import init_db
from utils.checks import load_config
from cogs.recruitment import RecruitLaunchView
from cogs.promotions import PromotionLaunchView
from cogs.events import EventAttendanceView

load_dotenv(override=True)  # .env важнее переменных Windows
logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(name)s: %(message)s")

# Защита от двойного запуска: два процесса с одним токеном отвечают на одни и те же
# нажатия кнопок, и второй получает ошибку "Interaction has already been acknowledged"
import socket
_single_instance = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    _single_instance.bind(("127.0.0.1", 47831))
except OSError:
    print("❌ Бот уже запущен в другом окне или процессе! Закрой все окна бота "
          "(или в Диспетчере задач заверши python.exe) и запусти заново.", flush=True)
    input("Нажми Enter, чтобы закрыть...")
    sys.exit(1)

intents = disnake.Intents.default()
intents.members = True

guild_id_str = os.getenv("GUILD_ID", "0")
# GUILD_ID можно указать несколько через запятую: GUILD_ID=111,222
_ids = [int(x) for x in guild_id_str.replace(" ", "").split(",") if x.isdigit() and int(x) > 0]
test_guilds = _ids or None

# Один общий цикл событий для бота и asyncio (иначе "attached to a different loop")
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

bot = commands.Bot(
    command_prefix=commands.when_mentioned,
    intents=intents,
    test_guilds=test_guilds,
    reload=False,
    loop=loop
)

LOADED_COGS = []
FAILED_COGS = []

COGS = [
    "cogs.recruitment",
    "cogs.promotions",
    "cogs.admin",
    "cogs.events",
    "cogs.profile",
    "cogs.games",
    "cogs.checkers",
    "cogs.hall_tools",
    "cogs.famq_control",
    "cogs.hall_panel",
    "cogs.teg_panel",
    "cogs.roulette",
    "cogs.music",
    "cogs.govorit",
    "cogs.govorit_stay"
]


@bot.event
async def on_ready():
    if not getattr(bot, "_views_added", False):
        bot.add_view(RecruitLaunchView())
        bot.add_view(PromotionLaunchView())
        bot.add_view(EventAttendanceView())
        bot._views_added = True

    config = load_config()
    bot_name = config.get("bot_name", "Hallez FAMQ")

    activity = disnake.Activity(
        type=disnake.ActivityType.watching,
        name=f"за порядком в {bot_name} | /admin"
    )
    await bot.change_presence(status=disnake.Status.online, activity=activity)

    print("=" * 60, flush=True)
    print(f"⚜️  Бот {bot_name} успешно запущен!", flush=True)
    print(f"👤  Авторизован как: {bot.user} (ID: {bot.user.id})", flush=True)
    print(f"🌐  Серверов: {len(bot.guilds)} | Задержка: {round(bot.latency * 1000)}ms", flush=True)
    print(f"📋  Слеш-команд в коде: {len(bot.all_slash_commands)}", flush=True)
    for g in bot.guilds:
        print(f"    • Сервер: {g.name} (ID: {g.id}) | Участников: {g.member_count}", flush=True)
    print(f"📁  Загружено модулей: {len(LOADED_COGS)} из {len(COGS)}" + (f" | не загрузились: {', '.join(FAILED_COGS)}" if FAILED_COGS else ""), flush=True)
    print("=" * 60, flush=True)


@bot.event
async def on_slash_command_error(inter: disnake.ApplicationCommandInteraction, error: Exception):
    if isinstance(error, commands.CommandNotFound):
        return
    print(f"Ошибка при выполнении команды /{inter.data.name}: {error}", file=sys.stderr, flush=True)
    try:
        if not inter.response.is_done():
            await inter.response.send_message(f"❌ Произошла внутренняя ошибка: `{str(error)}`", ephemeral=True)
        else:
            await inter.followup.send(f"❌ Произошла внутренняя ошибка: `{str(error)}`", ephemeral=True)
    except Exception:
        pass


async def main():
    print("📦 Инициализация базы данных...", flush=True)
    await init_db()
    print("📦 База готова, загружаю модули...", flush=True)

    for cog in COGS:
        try:
            bot.load_extension(cog)
            LOADED_COGS.append(cog)
            print(f"✅ Модуль {cog} успешно загружен", flush=True)
        except Exception as e:
            FAILED_COGS.append(cog)
            print(f"❌ Ошибка загрузки модуля {cog}: {e!r}", file=sys.stderr, flush=True)
            if e.__cause__:
                print(f"   ↳ причина: {e.__cause__!r}", file=sys.stderr, flush=True)

    token = os.getenv("BOT_TOKEN")
    if not token or token.strip() == "your_bot_token_here":
        print("⚠️  ВНИМАНИЕ: BOT_TOKEN не установлен в файле .env!", flush=True)
        print("👉  Откройте .env и укажите токен вашего бота перед запуском.", flush=True)
        return

    print("🔌 Подключение к Discord Gateway...", flush=True)
    print("ℹ️  Если пишет 'rate limited' — просто жди, НЕ закрывай окно. Команды зарегистрируются сами.", flush=True)
    await bot.start(token)


if __name__ == "__main__":
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен администратором.", flush=True)
