"""Бот-«ухо»: слушает голосовой канал и передаёт услышанное основному боту.

Основной бот на disnake принимать звук из войса не умеет, а Discord с 2026 года шифрует
голос сквозным шифрованием (DAVE). Поэтому слушает отдельный маленький бот на discord.py
с расширением discord-ext-voice-recv: он заходит в тот же канал, где сидит основной бот,
расшифровывает речь через Gemini (utils/ai_hear) и пишет услышанное в чат канала карточкой
«🎙 Имя» с пометкой «hzear:<id>». Основной бот (cogs/voice_ai.py) читает такие карточки
только от этого бота, отвечает, выполняет просьбы и говорит вслух.

Запуск: основной бот стартует это сам, если в окружении задан EAR_BOT_TOKEN. Вручную:
python ear_bot.py (тогда рядом нужен .env с EAR_BOT_TOKEN и BOT_TOKEN либо MAIN_BOT_ID).

Переменные окружения:
  EAR_BOT_TOKEN   токен второго бота (обязательно)
  MAIN_BOT_ID     id основного бота; если не задан, берётся из BOT_TOKEN
  EAR_WAKE        как звать бота голосом, через запятую (по умолчанию: бот,bot,абу,abu)
  EAR_ALWAYS      1 — отвечать на любую речь, а не только на обращение по имени
  EAR_FOLLOW      секунд после вопроса, когда можно уточнять без имени (по умолчанию 20; 0 — нельзя)
  EAR_TEXT_CHANNEL  id текстового канала для карточек; по умолчанию чат самого войса

Коды выхода: 3 — не хватает настроек или библиотек, перезапускать бессмысленно.
"""
import asyncio
import base64
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))


def log(*a):
    print("👂", *a, flush=True)


def _id_from_token(token: str) -> int:
    """Первый кусок токена бота — это его id в base64."""
    try:
        part = (token or "").strip().split(".")[0]
        return int(base64.b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return 0


def _deps_ok() -> bool:
    try:
        import discord  # noqa: F401
        from discord.ext import voice_recv  # noqa: F401
        return True
    except ImportError:
        return False


def ensure_deps() -> bool:
    """Отдельный requirements, чтобы сбой установки не ломал основного бота."""
    if _deps_ok():
        return True
    req = BASE / "ear_requirements.txt"
    log("ставлю библиотеки для прослушивания:", req.name)
    try:
        p = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(req)],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
        if p.returncode != 0:
            log("pip не справился:", p.stdout.decode(errors="ignore")[-400:])
    except Exception as e:
        log("pip не запустился:", e)
    return _deps_ok()


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE / ".env")
    except Exception:
        pass

    token = os.getenv("EAR_BOT_TOKEN", "").strip()
    if not token:
        log("EAR_BOT_TOKEN не задан — слушать нечем.")
        return 3
    main_id = int(os.getenv("MAIN_BOT_ID") or 0) or _id_from_token(os.getenv("BOT_TOKEN", ""))
    if not main_id:
        log("не знаю id основного бота: задай MAIN_BOT_ID (или BOT_TOKEN в том же .env).")
        return 3
    if not os.getenv("GEMINI_API_KEY", "").strip():
        log("нет GEMINI_API_KEY — расшифровывать речь нечем.")
        return 3
    if not ensure_deps():
        log("нет discord.py и discord-ext-voice-recv. Поставь: pip install -r ear_requirements.txt")
        return 3

    import discord
    from discord.ext import voice_recv

    from utils import ai_hear, ear_core as core

    wakes = core.parse_wake(os.getenv("EAR_WAKE", ""))
    follow = float(os.getenv("EAR_FOLLOW") or 20)
    gate = core.Gate(wakes, follow=follow,
                     always=os.getenv("EAR_ALWAYS", "").strip().lower() in ("1", "true", "yes", "on"))
    text_channel_id = int(os.getenv("EAR_TEXT_CHANNEL") or 0)
    min_level = float(os.getenv("EAR_MIN_LEVEL") or core.MIN_LEVEL)

    class Collector(voice_recv.AudioSink):
        """Собирает чужую речь по людям. write() зовут из потока библиотеки, не из цикла."""

        def __init__(self, buffers, guild_id):
            super().__init__()
            self.buffers, self.guild_id = buffers, guild_id

        def wants_opus(self) -> bool:
            return False

        def write(self, user, data) -> None:
            if user is None or getattr(user, "bot", False):
                return
            self.buffers.add(self.guild_id, user, data.pcm)

        def cleanup(self) -> None:
            pass

    class Ear(discord.Client):
        def __init__(self):
            intents = discord.Intents.none()
            intents.guilds = True
            intents.voice_states = True
            super().__init__(intents=intents)
            self.buffers = core.Buffers()
            self.locks = {}
            self.sem = None
            self.last_sent = {}
            self.warned = set()

        async def setup_hook(self):
            self.sem = asyncio.Semaphore(2)
            self.loop.create_task(self.pump())
            self.loop.create_task(self.watchdog())

        async def on_ready(self):
            log(f"готов как {self.user}; слежу за ботом {main_id}")
            await self.sync_all()

        # ---- где слушать: там же, где основной бот ----
        def _main_channel(self, guild):
            for ch in list(guild.voice_channels) + list(getattr(guild, "stage_channels", [])):
                if main_id in ch.voice_states:
                    return ch
            return None

        async def sync_all(self):
            for g in self.guilds:
                await self.sync_guild(g)

        async def sync_guild(self, guild):
            lock = self.locks.setdefault(guild.id, asyncio.Lock())
            async with lock:
                want = self._main_channel(guild)
                vc = guild.voice_client
                try:
                    if want is None:
                        if vc is not None:
                            await vc.disconnect(force=True)
                            self.buffers.drop_guild(guild.id)
                            log(f"{guild.name}: основной бот вышел, я тоже")
                        return
                    if vc is None or not vc.is_connected() or vc.channel.id != want.id:
                        if vc is not None:
                            await vc.disconnect(force=True)
                        vc = await want.connect(cls=voice_recv.VoiceRecvClient, timeout=25)
                        log(f"{guild.name}: слушаю канал «{want.name}»")
                    if not vc.is_listening():
                        vc.listen(Collector(self.buffers, guild.id))
                except Exception as e:
                    log(f"{guild.name}: не смог встать в канал: {type(e).__name__}: {e}")

        async def on_voice_state_update(self, member, before, after):
            if member.id == main_id and before.channel != after.channel:
                await self.sync_guild(member.guild)

        async def watchdog(self):
            """Раз в полминуты: вдруг соединение упало или запись остановилась."""
            await self.wait_until_ready()
            while not self.is_closed():
                await asyncio.sleep(30)
                try:
                    await self.sync_all()
                except Exception as e:
                    log("сторож:", e)

        # ---- реплики ----
        async def pump(self):
            await self.wait_until_ready()
            while not self.is_closed():
                await asyncio.sleep(0.25)
                for u in self.buffers.take_ready():
                    self.loop.create_task(self.handle(u))

        async def handle(self, u):
            try:
                if u.seconds < core.MIN_SEC:
                    return
                pcm = u.pcm()
                if core.loudness(pcm) < min_level:
                    return
                wav = core.pcm_to_wav(pcm)
                async with self.sem:
                    try:
                        text = await ai_hear.transcribe(wav, timeout=25)
                    except ai_hear.HearError as e:
                        if "не слышно слов" not in str(e):
                            log(f"{u.user.display_name}: не расшифровал: {e}")
                        return
                question, only_called = gate.decide(u.user.id, text)
                guild = self.get_guild(u.guild_id)
                chan = self.target(guild)
                if chan is None:
                    return
                if only_called:
                    await chan.send(f"👂 {u.user.display_name}, слушаю.", delete_after=6)
                    return
                if question is None:
                    return
                now = time.monotonic()
                if now - self.last_sent.get(u.user.id, 0) < 1.5:
                    return
                self.last_sent[u.user.id] = now
                emb = discord.Embed(title=f"🎙 {u.user.display_name}", description=question[:1500],
                                    color=0x990000)
                emb.set_footer(text=f"hzear:{u.user.id}")
                await chan.send(embed=emb)
                log(f"{u.user.display_name}: передал вопрос ({len(question)} зн.)")
            except discord.Forbidden:
                if u.guild_id not in self.warned:
                    self.warned.add(u.guild_id)
                    log("нет права писать в чат канала: дай мне «Отправлять сообщения» и «Встраивать ссылки»")
            except Exception as e:
                log(f"реплика не обработана: {type(e).__name__}: {e}")

        def target(self, guild):
            if guild is None:
                return None
            if text_channel_id:
                return guild.get_channel(text_channel_id)
            vc = guild.voice_client
            return getattr(vc, "channel", None)

    Ear().run(token, log_handler=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
