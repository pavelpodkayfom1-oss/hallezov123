import os
import sys
import json
import asyncio
import logging
import traceback
from pathlib import Path

import disnake
from disnake.ext import commands
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Лог-файл (ошибки и предупреждения библиотеки), чтобы ничего не терялось
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),  # теперь предупреждения видны и в консоли хостинга
        logging.FileHandler(BASE_DIR / "bot.log", encoding="utf-8"),
    ],
)
# Показываем ход подключения к Discord
logging.getLogger("disnake.gateway").setLevel(logging.INFO)
logging.getLogger("disnake.client").setLevel(logging.INFO)

from database import init_db
from utils.checks import load_config
from cogs.recruitment import RecruitLaunchView
from cogs.promotions import PromotionLaunchView
from cogs.events import EventAttendanceView

# ───────────────────────── Intents ─────────────────────────
intents = disnake.Intents.default()
intents.members = True          # вход/выход, роли, ники        (Server Members Intent)
intents.message_content = True  # текст удалённых/изменённых сообщений (Message Content Intent)
intents.voice_states = True     # логи голосовых каналов

guild_id_str = os.getenv("GUILD_ID", "0")
test_guilds = [int(guild_id_str)] if guild_id_str.isdigit() and int(guild_id_str) > 0 else None

# Один и тот же цикл событий и для бота, и для запуска.
# Исправляет "future belongs to a different loop" и "heartbeat blocked".
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

_bot_kwargs = dict(
    command_prefix=commands.when_mentioned,
    intents=intents,
    test_guilds=test_guilds,
    chunk_guilds_at_startup=False,
)
try:
    bot = commands.Bot(loop=loop, **_bot_kwargs)
except TypeError:
    bot = commands.Bot(**_bot_kwargs)

# Автозагрузка: подхватывает ВСЕ файлы из папки cogs (логи, emoji, голос, теги и т.д.)
COGS_DIR = BASE_DIR / "cogs"


def _panel_disabled_cogs() -> set:
    """Модули, которые отключены в веб-панели (хранится в panel_state.json)."""
    try:
        with open(BASE_DIR / "panel_state.json", "r", encoding="utf-8") as f:
            return {c for c in json.load(f).get("disabled_cogs", []) if isinstance(c, str)}
    except Exception:
        return set()


_DISABLED_COGS = _panel_disabled_cogs()
COGS = sorted(
    f"cogs.{p.stem}"
    for p in COGS_DIR.glob("*.py")
    if not p.name.startswith("_") and f"cogs.{p.stem}" not in _DISABLED_COGS
)
if _DISABLED_COGS:
    print(f"⏸️ Отключены в панели: {', '.join(sorted(_DISABLED_COGS))}", flush=True)

_ready_once = False


# ───────────────────────── Events ─────────────────────────
@bot.event
async def on_ready():
    global _ready_once
    # on_ready может вызываться повторно при переподключении — выполняем один раз
    if _ready_once:
        return
    _ready_once = True

    bot.add_view(RecruitLaunchView())
    bot.add_view(PromotionLaunchView())
    bot.add_view(EventAttendanceView())

    try:
        await bot._sync_application_commands()
        print("⚡ Слеш-команды успешно синхронизированы с Discord API!", flush=True)
    except Exception as e:
        print(f"⚠️ Ошибка синхронизации команд: {e}", file=sys.stderr, flush=True)

    config = load_config()
    bot_name = config.get("bot_name", "Hallez FAMQ")

    activity = disnake.Activity(
        type=disnake.ActivityType.watching,
        name=f"за порядком в {bot_name} | /logs panel",
    )
    await bot.change_presence(status=disnake.Status.online, activity=activity)

    # Если статус бота меняли в веб-панели, возвращаем выбранный там
    panel = bot.get_cog("WebPanel")
    if panel is not None:
        await panel.apply_saved_presence()

    print("=" * 60, flush=True)
    print(f"⚜️  Бот {bot_name} успешно запущен!", flush=True)
    print(f"👤  Авторизован как: {bot.user} (ID: {bot.user.id})", flush=True)
    print(f"🌐  Серверов: {len(bot.guilds)} | Задержка: {round(bot.latency * 1000)}ms", flush=True)
    for g in bot.guilds:
        print(f"    • Сервер: {g.name} (ID: {g.id}) | Участников: {g.member_count}", flush=True)
    print(f"📁  Загружено модулей: {len(bot.extensions)}/{len(COGS)}", flush=True)
    print("=" * 60, flush=True)

    # Список участников грузим отдельно, чтобы возможная ошибка не мешала запуску бота
    for g in bot.guilds:
        try:
            await asyncio.wait_for(g.chunk(), timeout=60)
            print(f"👥  Участники загружены: {g.name} ({len(g.members)})", flush=True)
        except Exception as e:
            print(f"⚠️ Не удалось загрузить список участников {g.name}: {e}", file=sys.stderr, flush=True)


async def _handle_error(inter: disnake.Interaction, error: Exception, kind: str):
    if isinstance(error, commands.CommandNotFound):
        return

    # достаём исходную ошибку из обёртки
    original = getattr(error, "original", error)

    if isinstance(error, commands.MissingPermissions):
        text = "⛔ У вас недостаточно прав для этой команды."
    elif isinstance(error, commands.BotMissingPermissions):
        text = "⚠️ У бота не хватает прав: " + ", ".join(error.missing_permissions)
    elif isinstance(error, commands.CheckFailure):
        text = "⛔ Эта команда вам недоступна."
    elif isinstance(error, commands.CommandOnCooldown):
        text = f"⏳ Подождите {error.retry_after:.0f} сек. перед повтором."
    else:
        name = getattr(getattr(inter, "data", None), "name", "?")
        print(f"Ошибка в {kind} «{name}»:", file=sys.stderr, flush=True)
        traceback.print_exception(type(original), original, original.__traceback__)
        text = f"❌ Произошла внутренняя ошибка: `{str(original)[:300]}`"

    try:
        if not inter.response.is_done():
            await inter.response.send_message(text, ephemeral=True)
        else:
            await inter.followup.send(text, ephemeral=True)
    except Exception:
        pass


@bot.event
async def on_slash_command_error(inter: disnake.ApplicationCommandInteraction, error: Exception):
    await _handle_error(inter, error, "команде")


@bot.event
async def on_user_command_error(inter: disnake.UserCommandInteraction, error: Exception):
    await _handle_error(inter, error, "контекстной команде")


@bot.event
async def on_message_command_error(inter: disnake.MessageCommandInteraction, error: Exception):
    await _handle_error(inter, error, "контекстной команде")


# ───────────────────────── Startup ─────────────────────────
async def _watchdog():
    """Пишет в консоль, пока бот не готов, чтобы было видно, что он жив."""
    waited = 0
    await asyncio.sleep(30)
    while True:
        try:
            if bot.is_ready():
                break
        except Exception:
            pass
        waited += 30
        print(f"⏳ Ждём ответа Discord… {waited} сек", flush=True)
        if waited == 120:
            print("👉 Если так долго: проверьте Intents в Developer Portal (Server Members + Message Content) "
                  "и что бот не запущен в другом месте.", flush=True)
        await asyncio.sleep(30)


async def main():
    await init_db()

    for cog in COGS:
        try:
            bot.load_extension(cog)
            print(f"✅ Модуль {cog} успешно загружен", flush=True)
        except Exception as e:
            print(f"❌ Ошибка загрузки модуля {cog}: {e}", file=sys.stderr, flush=True)
            traceback.print_exc()

    token = os.getenv("BOT_TOKEN")
    if not token or token.strip() == "your_bot_token_here":
        print("⚠️  ВНИМАНИЕ: BOT_TOKEN не установлен в файле .env!", flush=True)
        print("👉  Откройте .env и укажите токен вашего бота перед запуском.", flush=True)
        return

    print("🔌 Подключение к Discord Gateway...", flush=True)
    try:
        asyncio.create_task(_watchdog())
        await bot.start(token)
    except disnake.HTTPException as e:
        if e.status == 429 or "1015" in str(e) or "Cloudflare" in str(e):
            print("❌ Discord ограничил запросы с IP хостинга (429/1015). Подождите 15-30 минут и запустите снова.", file=sys.stderr, flush=True)
        else:
            print(f"❌ Ошибка HTTP при входе: {e}", file=sys.stderr, flush=True)
    except disnake.PrivilegedIntentsRequired:
        print("\n❌ Не включены привилегированные Intents!", file=sys.stderr, flush=True)
        print("👉 Discord Developer Portal → ваше приложение → Bot → Privileged Gateway Intents:", flush=True)
        print("   включите SERVER MEMBERS INTENT и MESSAGE CONTENT INTENT, сохраните и перезапустите.", flush=True)
    except disnake.LoginFailure:
        print("❌ Неверный BOT_TOKEN. Проверьте файл .env.", file=sys.stderr, flush=True)
    finally:
        if not bot.is_closed():
            await bot.close()


if __name__ == "__main__":
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен администратором.", flush=True)
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()
