import asyncio
import os
import re
import time
from collections import deque

import disnake
import yt_dlp
from disnake.ext import commands

from utils.checks import load_config

YTDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}
# Необязательно: если рядом с main.py лежит cookies.txt, он используется для YouTube
COOKIES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cookies.txt")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTS = "-vn"


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
                "❌ Сначала зайдите в голосовой канал, сэр.", ephemeral=True)
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
        await inter.response.send_message("👋 Отключился от голосового канала.", ephemeral=True)

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
        await inter.response.send_message(msg, ephemeral=True)

    @disnake.ui.button(label="Скип", emoji="⏭", style=disnake.ButtonStyle.secondary,
                       custom_id="music:skip", row=1)
    async def skip(self, button, inter: disnake.MessageInteraction):
        cog: "Music" = inter.bot.get_cog("Music")
        vc = await cog.check_control(inter)
        if not vc:
            return
        if vc.is_playing() or vc.is_paused():
            vc.stop()
            await inter.response.send_message("⏭ Трек пропущен.", ephemeral=True)
        else:
            await inter.response.send_message("Сейчас ничего не играет.", ephemeral=True)

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
        await inter.response.send_message("⏹ Остановлено, очередь очищена.", ephemeral=True)

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
        await inter.response.send_message("\n".join(lines) or "Очередь пуста.", ephemeral=True)

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

    @commands.Cog.listener()
    async def on_ready(self):
        # persistent view: кнопки панели работают и после перезапуска бота
        if not getattr(self.bot, "_music_view_added", False):
            self.bot.add_view(MusicPanelView())
            self.bot._music_view_added = True

    # ---------- helpers ----------
    def get_player(self, guild: disnake.Guild) -> GuildPlayer:
        if guild.id not in self.players:
            self.players[guild.id] = GuildPlayer()
        return self.players[guild.id]

    async def cleanup(self, guild: disnake.Guild):
        p = self.players.pop(guild.id, None)
        if p:
            p.queue.clear()
        vc = guild.voice_client
        if vc:
            await vc.disconnect(force=True)

    async def _reply(self, inter, text: str):
        if inter.response.is_done():
            await inter.followup.send(text, ephemeral=True)
        else:
            await inter.response.send_message(text, ephemeral=True)

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
        await inter.response.send_message(f"🔊 Громкость: **{int(p.volume * 100)}%**", ephemeral=True)

    async def _extract(self, query: str) -> dict:
        is_url = query.startswith(("http://", "https://"))

        def run():
            opts = dict(YTDL_OPTS)
            if os.path.exists(COOKIES_FILE):
                opts["cookiefile"] = COOKIES_FILE

            if is_url:
                candidates = [query]
            else:
                with yt_dlp.YoutubeDL(dict(opts, extract_flat=True)) as ydl:
                    res = ydl.extract_info(f"ytsearch6:{query}", download=False)
                candidates = []
                for e in (res.get("entries") or []):
                    if not e:
                        continue
                    url = e.get("url") or (f"https://www.youtube.com/watch?v={e['id']}" if e.get("id") else None)
                    if url:
                        candidates.append(url)
                if not candidates:
                    raise RuntimeError("Ничего не найдено")

            last = None
            for url in candidates:
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        data = ydl.extract_info(url, download=False)
                    if "entries" in data:
                        data = [x for x in data["entries"] if x][0]
                    return data
                except Exception as e:  # возрастное ограничение, недоступно и т.п. — пробуем следующий
                    last = e
            # Запасной вариант: SoundCloud (не требует входа в аккаунт)
            if not is_url:
                try:
                    with yt_dlp.YoutubeDL(dict(opts, extract_flat=True)) as ydl:
                        res = ydl.extract_info(f"scsearch6:{query}", download=False)
                    for e in (res.get("entries") or []):
                        url = e.get("url") if e else None
                        if not url:
                            continue
                        try:
                            with yt_dlp.YoutubeDL(opts) as ydl:
                                data = ydl.extract_info(url, download=False)
                            if "entries" in data:
                                data = [x for x in data["entries"] if x][0]
                            return data
                        except Exception as ex:
                            last = ex
                except Exception as ex:
                    last = ex
            msg = _ANSI.sub("", str(last)) if last else "Ничего не найдено"
            raise RuntimeError(msg[:300])

        data = await asyncio.get_running_loop().run_in_executor(None, run)
        return {
            "title": data.get("title", "Без названия"),
            "stream_url": data["url"],
            "webpage_url": data.get("webpage_url") or data.get("original_url") or query,
            "duration": data.get("duration"),
            "thumbnail": data.get("thumbnail"),
            "ts": time.time(),
        }

    # ---------- core ----------
    async def handle_play(self, inter, query: str):
        """inter уже должен быть defer'нут."""
        if not query:
            return await inter.followup.send("❌ Пустой запрос.", ephemeral=True)
        vc = await self.ensure_voice(inter)
        if not vc:
            return
        p = self.get_player(inter.guild)
        p.text_channel = inter.channel
        try:
            track = await self._extract(query)
        except Exception as e:
            return await inter.followup.send(f"❌ Не удалось найти/загрузить: `{e}`", ephemeral=True)
        track["requester"] = inter.author.mention
        p.queue.append(track)

        if vc.is_playing() or vc.is_paused():
            await inter.followup.send(f"➕ Добавлено в очередь: **{track['title']}**", ephemeral=True)
        else:
            await inter.followup.send(f"🎶 Включаю: **{track['title']}**", ephemeral=True)
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
                disnake.FFmpegPCMAudio(track["stream_url"], before_options=FFMPEG_BEFORE, options=FFMPEG_OPTS),
                volume=p.volume,
            )
        except Exception as e:
            if p.text_channel:
                await p.text_channel.send(f"❌ Не смог проиграть **{track['title']}**: `{e}`\n"
                                          "Проверьте, что FFmpeg установлен.")
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
        await inter.response.send_message("✅ Панель отправлена.", ephemeral=True)

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
