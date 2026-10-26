"""Музыка в голосовом: очередь треков, панель заказов и поиск через yt-dlp.

Раньше этот файл назывался cogs/_music.py — с подчёркиванием, и main.py его не грузил.
Поиск специально живёт в дочернем процессе (utils/ytdlp_resolve.py): yt-dlp на пике ест
60-100 МБ, а у хостинга 256 МБ на всё. Поэтому сам ког yt-dlp не импортирует и порядок
очереди держит в памяти, а не на диске. Нужен ffmpeg: на хостинге его нет в PATH, поэтому
берётся статический бинарник из imageio-ffmpeg (см. _ffmpeg_exe). YouTube требует
cookies.txt рядом с main.py, поэтому по умолчанию ищем на SoundCloud (PREFER_SOUNDCLOUD).
"""
import asyncio
import json
import os
import sys
import time
from collections import deque

import disnake
from disnake.ext import commands

from utils.checks import load_config
from utils.embeds import note_embed

# Кулдаун на заказ музыки (секунд на одного пользователя) и число вариантов поиска
PLAY_COOLDOWN = 20
SEARCH_RESULTS = 2
# На слабом хосте поиск на YouTube (с куками) может убить бота по памяти.
# SoundCloud лёгкий и не требует входа, поэтому ищем сначала там. Поставьте False, чтобы вернуть YouTube первым.
PREFER_SOUNDCLOUD = True
# Сколько треков максимум может стоять в очереди
MAX_QUEUE = 25
# Сколько секунд ждём резолвер, пока он не завис
EXTRACT_TIMEOUT = 120

# yt-dlp импортируется только в дочернем процессе: сам по себе он занимает ~26 МБ
# кучи и ещё столько же на разборе, а в обратную ОС память не отдаётся.
RESOLVER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "utils", "ytdlp_resolve.py")


def _trim():
    """Возвращает освобождённую память операционной системе (на Linux — через malloc)."""
    import gc

    gc.collect()
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def _rss_mb() -> int:
    """Сколько памяти (МБ) занимает бот — для диагностики на слабом хосте."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return -1


def _log(msg: str):
    print(f"[music] {msg} | RSS={_rss_mb()} МБ", flush=True)


# Необязательно: если рядом с main.py лежит cookies.txt, он используется для YouTube.
# Этот файл лежит в cogs/, поэтому поднимаемся на папку выше — к main.py.
COOKIES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cookies.txt")
FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
# -vn -sn -dn: ffmpeg не декодирует видео и субтитры — меньше буферов на слабом хосте
FFMPEG_OPTS = "-vn -sn -dn"

_EXE = None


def _ffmpeg_exe():
    """Какой ffmpeg запускать: системный, а на хостинге обычно только из imageio-ffmpeg.

    На бесплатном контейнере нет ни apt, ни бинарника ffmpeg, и без этого трек находится,
    голос подключается, а играть нечем. Колесо imageio-ffmpeg приносит статический
    бинарник с собой — его и берём, если в PATH пусто.
    """
    global _EXE
    if _EXE is None:
        import shutil

        _EXE = shutil.which("ffmpeg") or ""
        if not _EXE:
            try:
                from imageio_ffmpeg import get_ffmpeg_exe

                _EXE = get_ffmpeg_exe()
            except Exception:
                _EXE = ""
        _log(f"ffmpeg: {_EXE or 'не найден'}")
    return _EXE or None


def _color(key: str = "embed_color") -> int:
    try:
        return int(str(load_config().get(key, "0x990000")), 16)
    except Exception:
        return 0x990000


def _fmt_duration(sec) -> str:
    if not sec:
        return "live"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class GuildPlayer:
    def __init__(self):
        self.queue: deque = deque()
        self.current = None
        self.source = None
        self.volume = 0.5
        self.text_channel = None


class MusicModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(
            title="Слушаю, сэр! Какую музыку включить?",
            custom_id="music_modal",
            components=[
                disnake.ui.TextInput(
                    label="Название трека / исполнителя / ссылка",
                    custom_id="query",
                    style=disnake.TextInputStyle.short,
                    placeholder="Например: Эксонад музыка",
                    max_length=200,
                )
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        await inter.response.defer(ephemeral=True)
        await cog.handle_play(inter, inter.text_values["query"].strip())


class TalkView(disnake.ui.View):
    """Показывается после 'Позвать в войс' — кнопка для выбора музыки."""

    def __init__(self):
        super().__init__(timeout=300)

    @disnake.ui.button(label="Назвать музыку", emoji="🎵", style=disnake.ButtonStyle.success)
    async def pick(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.send_modal(MusicModal())


class MusicPanelView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # ---------- ряд 1 ----------
    @disnake.ui.button(label="Включить музыку", emoji="🎵", style=disnake.ButtonStyle.success,
                       custom_id="music:play", row=0)
    async def play(self, button, inter: disnake.MessageInteraction):
        if not inter.author.voice or not inter.author.voice.channel:
            return await inter.response.send_message(
                embed=note_embed("❌ Сначала зайдите в голосовой канал, сэр."), ephemeral=True)
        await inter.response.send_modal(MusicModal())

    @disnake.ui.button(label="Позвать в войс", emoji="🎙", style=disnake.ButtonStyle.primary,
                       custom_id="music:join", row=0)
    async def join(self, button, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        await inter.response.defer(ephemeral=True)
        vc = await cog.ensure_voice(inter)
        if not vc:
            return
        cog.get_player(inter.guild).text_channel = inter.channel
        await inter.edit_original_response(
            content="🎙 **Слушаю, сэр! Какую музыку желаете?**", view=TalkView())

    @disnake.ui.button(label="Выйти", emoji="🚪", style=disnake.ButtonStyle.danger,
                       custom_id="music:leave", row=0)
    async def leave(self, button, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        vc = await cog.check_control(inter)
        if not vc:
            return
        await cog.cleanup(inter.guild)
        await inter.response.send_message(embed=note_embed("👋 Отключился от голосового канала."), ephemeral=True)

    # ---------- ряд 2 ----------
    @disnake.ui.button(label="Пауза / Продолжить", emoji="⏯", style=disnake.ButtonStyle.secondary,
                       custom_id="music:pause", row=1)
    async def pause(self, button, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        vc = await cog.check_control(inter)
        if not vc:
            return
        if vc.is_paused():
            vc.resume()
            msg = "▶️ Продолжаю."
        elif vc.is_playing():
            vc.pause()
            msg = "⏸ Пауза."
        else:
            msg = "Сейчас ничего не играет."
        await inter.response.send_message(embed=note_embed(msg), ephemeral=True)

    @disnake.ui.button(label="Скип", emoji="⏭", style=disnake.ButtonStyle.secondary,
                       custom_id="music:skip", row=1)
    async def skip(self, button, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        vc = await cog.check_control(inter)
        if not vc:
            return
        if vc.is_playing() or vc.is_paused():
            vc.stop()
            await inter.response.send_message(embed=note_embed("⏭ Трек пропущен."), ephemeral=True)
        else:
            await inter.response.send_message(embed=note_embed("Сейчас ничего не играет."), ephemeral=True)

    @disnake.ui.button(label="Стоп", emoji="⏹", style=disnake.ButtonStyle.secondary,
                       custom_id="music:stop", row=1)
    async def stop(self, button, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        vc = await cog.check_control(inter)
        if not vc:
            return
        cog.get_player(inter.guild).queue.clear()
        if vc.is_playing() or vc.is_paused():
            vc.stop()
        await inter.response.send_message(embed=note_embed("⏹ Остановлено, очередь очищена."), ephemeral=True)

    @disnake.ui.button(label="Очередь", emoji="📜", style=disnake.ButtonStyle.secondary,
                       custom_id="music:queue", row=1)
    async def queue(self, button, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        p = cog.get_player(inter.guild)
        lines = []
        if p.current:
            lines.append(f"▶️ **Сейчас:** {p.current['title']} `{_fmt_duration(p.current['duration'])}`")
        for i, t in enumerate(list(p.queue)[:10], 1):
            lines.append(f"`{i}.` {t['title']} `{_fmt_duration(t['duration'])}`")
        if len(p.queue) > 10:
            lines.append(f"…и ещё {len(p.queue) - 10}")
        await inter.response.send_message(embed=note_embed("\n".join(lines) or "Очередь пуста."), ephemeral=True)

    # ---------- ряд 3 ----------
    @disnake.ui.button(label="Тише", emoji="🔉", style=disnake.ButtonStyle.secondary,
                       custom_id="music:vol_down", row=2)
    async def vol_down(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("Music").change_volume(inter, -0.1)

    @disnake.ui.button(label="Громче", emoji="🔊", style=disnake.ButtonStyle.secondary,
                       custom_id="music:vol_up", row=2)
    async def vol_up(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("Music").change_volume(inter, +0.1)


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.players: dict[int, GuildPlayer] = {}
        # Только один поиск yt-dlp за раз: иначе на слабом хосте памяти не хватает
        self._extract_lock = asyncio.Semaphore(1)
        self._last_play: dict[int, float] = {}

    @commands.Cog.listener()
    async def on_ready(self):
        # persistent view: кнопки панели работают и после перезапуска бота
        if not getattr(self.bot, "_music_view_added", False):
            self.bot.add_view(MusicPanelView())
            self.bot._music_view_added = True
            # путь в лог: «требует вход» означает, что файла просто нет по этому пути
            _log(f"cookies: {'есть' if os.path.exists(COOKIES_FILE) else 'нет'} — {COOKIES_FILE}")

    # ---------- helpers ----------
    def get_player(self, guild: disnake.Guild) -> GuildPlayer:
        if guild.id not in self.players:
            self.players[guild.id] = GuildPlayer()
        return self.players[guild.id]

    async def cleanup(self, guild: disnake.Guild):
        p = self.players.pop(guild.id, None)
        if p:
            p.queue.clear()
            p.current = None
            p.source = None
        vc = guild.voice_client
        if vc:
            await vc.disconnect(force=True)
        _trim()

    async def _reply(self, inter, text: str):
        if inter.response.is_done():
            await inter.followup.send(embed=note_embed(text), ephemeral=True)
        else:
            await inter.response.send_message(embed=note_embed(text), ephemeral=True)

    async def ensure_voice(self, inter):
        """Заходит в канал пользователя (или переезжает). Возвращает VoiceClient."""
        voice = inter.author.voice
        if not voice or not voice.channel:
            await self._reply(inter, "❌ Сначала зайдите в голосовой канал, сэр.")
            return None
        vc = inter.guild.voice_client
        try:
            if vc is None:
                vc = await voice.channel.connect(timeout=20)
            elif vc.channel.id != voice.channel.id:
                if vc.is_playing() or vc.is_paused():
                    await self._reply(
                        inter, f"❌ Я уже играю в канале **{vc.channel.name}**. Зайдите туда.")
                    return None
                await vc.move_to(voice.channel)
        except Exception as e:
            await self._reply(inter, f"❌ Не удалось зайти в войс: `{e}`\n"
                                     "Проверьте права бота (Connect / Speak) и что установлен PyNaCl.")
            return None
        return vc

    async def check_control(self, inter):
        vc = inter.guild.voice_client
        if not vc or not vc.is_connected():
            await self._reply(inter, "❌ Бот сейчас не в голосовом канале.")
            return None
        voice = inter.author.voice
        if not voice or voice.channel.id != vc.channel.id:
            await self._reply(inter, "❌ Вы должны быть в одном голосовом канале с ботом.")
            return None
        return vc

    async def change_volume(self, inter, delta: float):
        vc = await self.check_control(inter)
        if not vc:
            return
        p = self.get_player(inter.guild)
        p.volume = max(0.05, min(1.0, round(p.volume + delta, 2)))
        if p.source:
            p.source.volume = p.volume
        await inter.response.send_message(embed=note_embed(f"🔊 Громкость: **{int(p.volume * 100)}%**"), ephemeral=True)

    async def _extract(self, query: str) -> dict:
        """Ищет трек. Разбор — в дочернем процессе, чтобы yt-dlp не съедал память бота."""
        async with self._extract_lock:
            _log(f"старт поиска: {query[:50]!r}")
            t0 = time.time()
            try:
                track = await self._resolve_in_child(query)
                if track is None:
                    # Резолвер не запустился (хост запрещает дочерние процессы) — работаем здесь
                    from utils.ytdlp_resolve import resolve

                    loop = asyncio.get_running_loop()
                    track = await loop.run_in_executor(None, lambda: resolve(
                        query,
                        COOKIES_FILE,
                        PREFER_SOUNDCLOUD,
                        SEARCH_RESULTS,
                    ))
                _log(f"поиск завершён за {time.time() - t0:.1f} с")
            except Exception as e:
                # Без этой строки в логах хостинга тишина: причина только в ephemeral-ответе
                _log(f"поиск не удался за {time.time() - t0:.1f} с: {str(e)[:180]}")
                raise
        _trim()
        track = dict(track)
        track["ts"] = time.time()
        return track

    async def _resolve_in_child(self, query: str):
        """Возвращает трек, либо None, если дочерний процесс не смог стартовать."""
        request = json.dumps(
            {
                "query": query,
                # путь передаём всегда: есть файл или нет — решает резолвер, иначе в ошибке
                # останется бессмысленное «путь не передан» вместо адреса
                "cookies": COOKIES_FILE,
                "prefer_soundcloud": PREFER_SOUNDCLOUD,
                "search_results": SEARCH_RESULTS,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                RESOLVER,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                # PYTHONMALLOC=malloc: без pymalloc арены не раздуваются лишними
                # страницами, и освобождённое сразу возвращается ОС
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONMALLOC": "malloc"},
            )
        except Exception as e:
            _log(f"дочерний резолвер не запустился: {e}")
            return None
        try:
            out = (await asyncio.wait_for(proc.communicate(request), timeout=EXTRACT_TIMEOUT))[0]
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"Поиск завис ({EXTRACT_TIMEOUT} сек), трек не взят.")
        try:
            payload = json.loads(out.decode("utf-8", "replace"))
        except Exception:
            _log("резолвер вернул непонятный ответ")
            return None
        # Пик дочернего процесса — единственная цифра, по которой видно, влезает ли хост
        if payload.get("peak_mb"):
            _log(f"пик резолвера: {payload['peak_mb']} МБ")
        if payload.get("ok"):
            return payload.get("track")
        raise RuntimeError(str(payload.get("error") or "Ничего не найдено")[:300])

    # ---------- core ----------
    async def handle_play(self, inter, query: str):
        """inter уже должен быть defer'нут."""
        if not query:
            return await inter.followup.send(embed=note_embed("❌ Пустой запрос."), ephemeral=True)
        # кулдаун: не чаще одного заказа в PLAY_COOLDOWN секунд на человека
        now = time.time()
        wait = PLAY_COOLDOWN - (now - self._last_play.get(inter.author.id, 0))
        if wait > 0:
            return await inter.followup.send(
                embed=note_embed(f"⏳ Не так быстро, сэр. Подождите ещё {int(wait) + 1} сек."), ephemeral=True)
        self._last_play[inter.author.id] = now
        p = self.get_player(inter.guild)
        if len(p.queue) >= MAX_QUEUE:
            return await inter.followup.send(
                embed=note_embed(f"️ Очередь забита ({MAX_QUEUE} треков). Почистите её кнопкой ⏹."),
                ephemeral=True)
        vc = await self.ensure_voice(inter)
        if not vc:
            return
        p.text_channel = inter.channel
        try:
            track = await self._extract(query)
        except Exception as e:
            return await inter.followup.send(embed=note_embed(f"❌ Не удалось найти/загрузить: `{e}`"), ephemeral=True)
        track["requester"] = inter.author.mention
        p.queue.append(track)

        if vc.is_playing() or vc.is_paused():
            await inter.followup.send(embed=note_embed(f"➕ Добавлено в очередь: **{track['title']}**"), ephemeral=True)
        else:
            await inter.followup.send(embed=note_embed(f"🎶 Включаю: **{track['title']}**"), ephemeral=True)
            await self._play_next(inter.guild)

    async def _play_next(self, guild: disnake.Guild):
        p = self.players.get(guild.id)
        vc = guild.voice_client
        if not p or not vc or not vc.is_connected():
            return
        if vc.is_playing() or vc.is_paused():
            return
        if not p.queue:
            p.current = None
            p.source = None
            return

        track = p.queue.popleft()
        try:
            if time.time() - track["ts"] > 600:  # ссылка на стрим могла протухнуть
                fresh = await self._extract(track["webpage_url"])
                track.update(fresh)
            source = disnake.PCMVolumeTransformer(
                disnake.FFmpegPCMAudio(track["stream_url"], executable=_ffmpeg_exe(),
                                       before_options=FFMPEG_BEFORE, options=FFMPEG_OPTS),
                volume=p.volume,
            )
        except Exception as e:
            if p.text_channel:
                await p.text_channel.send(
                    f"❌ Не смог проиграть **{track['title']}**: `{e}`\n"
                    + ("На хосте нет ffmpeg — добавь `imageio-ffmpeg` в requirements.txt и "
                       "перезапусти бота." if _ffmpeg_exe() is None else
                       "Дальше поможет рестарт: возможно, ffmpeg не успевает стартовать."))
            return await self._play_next(guild)

        p.current = track
        p.source = source

        loop = asyncio.get_running_loop()

        def after(err):
            if err:
                print(f"[music] ошибка воспроизведения: {err}", flush=True)
            asyncio.run_coroutine_threadsafe(self._play_next(guild), loop)

        vc.play(source, after=after)

        if p.text_channel:
            embed = disnake.Embed(
                title="🎶 Сейчас играет",
                description=f"[{track['title']}]({track['webpage_url']})",
                color=_color(),
            )
            embed.add_field(name="Длительность", value=_fmt_duration(track["duration"]))
            if track.get("requester"):
                embed.add_field(name="Заказал", value=track["requester"])
            if track.get("thumbnail"):
                embed.set_thumbnail(url=track["thumbnail"])
            try:
                await p.text_channel.send(embed=embed)
            except Exception:
                pass

    # ---------- commands ----------
    @commands.slash_command(name="music_panel", description="Отправить панель музыки в этот канал",
                            default_member_permissions=disnake.Permissions(manage_guild=True))
    async def music_panel(self, inter: disnake.ApplicationCommandInteraction):
        embed = disnake.Embed(
            title="🎧 Музыкальная панель",
            description=(
                "Зайдите в голосовой канал и нажмите **«Включить музыку»**.\n"
                "Бот спросит, какую музыку включить, зайдёт к вам в войс и поставит трек.\n\n"
                "Управление: ⏯ пауза • ⏭ скип • ⏹ стоп • 📜 очередь • 🔉🔊 громкость"
            ),
            color=_color(),
        )
        await inter.channel.send(embed=embed, view=MusicPanelView())
        await inter.response.send_message(embed=note_embed("✅ Панель отправлена."), ephemeral=True)

    @commands.slash_command(name="play", description="Включить музыку (название или ссылка)")
    async def play_cmd(self, inter: disnake.ApplicationCommandInteraction, запрос: str):
        await inter.response.defer(ephemeral=True)
        await self.handle_play(inter, запрос.strip())

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # если бот остался один в канале — выходим
        vc = member.guild.voice_client
        if vc and vc.channel and len([m for m in vc.channel.members if not m.bot]) == 0:
            await asyncio.sleep(60)
            vc = member.guild.voice_client
            if vc and vc.channel and len([m for m in vc.channel.members if not m.bot]) == 0:
                await self.cleanup(member.guild)


def setup(bot: commands.Bot):
    bot.add_cog(Music(bot))
