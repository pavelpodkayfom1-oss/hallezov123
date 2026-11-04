"""Саундборд: панель кнопок с мемными звуками, бот играет их в голосовом канале.

 • /sounds_panel — выложить в канал панель с кнопками (для руководства)
 • /sound_add <имя> <файл> — добавить звук (mp3, wav, ogg, до 2 МБ; для руководства)
 • /sound_remove <имя> — убрать звук (для руководства)
Нажать кнопку может любой, кто сидит в голосовом канале. Звуки лежат в папке sounds/.
"""
import asyncio
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

import disnake
from disnake.ext import commands

from utils.checks import is_admin
from utils.embeds import note_embed
from utils.ffmpeg_path import ffmpeg_exe

SOUND_DIR = Path("sounds")
EXTS = {".mp3", ".wav", ".ogg", ".m4a"}
MAX_BYTES = 2 * 1024 * 1024
MAX_SOUNDS = 24            # 25 кнопок в сообщении минус «Стоп»
NAME_RE = re.compile(r"^[a-zа-яё0-9_\-]{1,20}$")
USER_COOLDOWN = 3
IDLE_LEAVE = 60            # секунд тишины, после которых бот выходит (если зашёл сам)
PREFIX = "sb:"
STOP_ID = PREFIX + "__stop"


def list_sounds() -> Dict[str, Path]:
    try:
        files = [p for p in SOUND_DIR.iterdir() if p.is_file() and p.suffix.lower() in EXTS]
    except FileNotFoundError:
        return {}
    return {p.stem: p for p in sorted(files, key=lambda p: p.stem)}


def panel_components(names: List[str]) -> List[disnake.ui.ActionRow]:
    buttons = [disnake.ui.Button(label=n, custom_id=PREFIX + n, style=disnake.ButtonStyle.secondary)
               for n in names[:MAX_SOUNDS]]
    buttons.append(disnake.ui.Button(label="⏹ Стоп", custom_id=STOP_ID, style=disnake.ButtonStyle.danger))
    return [disnake.ui.ActionRow(*buttons[i:i + 5]) for i in range(0, len(buttons), 5)]


class Soundboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_click: Dict[int, float] = {}
        self.locks: Dict[int, asyncio.Lock] = {}
        self.now_playing: Dict[int, object] = {}   # guild_id -> токен нашего звука
        self.joined: set = set()                   # серверы, где в войс зашли мы сами
        self.idle: Dict[int, asyncio.Task] = {}

    def _lock(self, gid: int) -> asyncio.Lock:
        return self.locks.setdefault(gid, asyncio.Lock())

    async def _leave_later(self, guild: disnake.Guild):
        await asyncio.sleep(IDLE_LEAVE)
        vc = guild.voice_client
        if vc and guild.id in self.joined and not vc.is_playing():
            self.joined.discard(guild.id)
            await vc.disconnect()

    def _schedule_leave(self, guild: disnake.Guild):
        old = self.idle.pop(guild.id, None)
        if old:
            old.cancel()
        if guild.id in self.joined:
            self.idle[guild.id] = asyncio.create_task(self._leave_later(guild))

    # ---- нажатие кнопки ----
    @commands.Cog.listener()
    async def on_button_click(self, inter: disnake.MessageInteraction):
        cid = inter.component.custom_id or ""
        if not cid.startswith(PREFIX):
            return
        if not inter.guild or not isinstance(inter.author, disnake.Member):
            await inter.response.send_message(embed=note_embed("ℹ️ Только на сервере."), ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        msg = await self._press(inter, cid)
        await inter.edit_original_response(embed=note_embed(msg[0], msg[1]))

    async def _press(self, inter: disnake.MessageInteraction, cid: str):
        """Возвращает (текст, вид карточки)."""
        guild, user = inter.guild, inter.author
        now = time.time()
        if now - self.last_click.get(user.id, 0) < USER_COOLDOWN:
            return "⏳ Не так быстро.", "warning"
        self.last_click[user.id] = now

        if cid == STOP_ID:
            vc = guild.voice_client
            if vc and vc.is_playing() and self.now_playing.get(guild.id) is not None:
                vc.stop()
                return "⏹ Остановил.", "success"
            return "ℹ️ Сейчас мой звук не играет.", None

        if not ffmpeg_exe():
            return "⚠️ На хостинге нет ffmpeg — звук воспроизвести нельзя.", "warning"
        path = list_sounds().get(cid[len(PREFIX):])
        if path is None:
            return "⚠️ Такого звука уже нет. Руководство могло его убрать.", "warning"
        if not user.voice or not user.voice.channel:
            return "⚠️ Сначала зайди в голосовой канал.", "warning"
        channel = user.voice.channel

        async with self._lock(guild.id):
            vc = guild.voice_client
            ours = self.now_playing.get(guild.id) is not None
            try:
                if vc is None:
                    vc = await channel.connect()
                    self.joined.add(guild.id)
                elif vc.channel.id != channel.id:
                    if vc.is_playing():
                        return f"⚠️ Я сейчас занят в канале «{vc.channel.name}».", "warning"
                    await vc.move_to(channel)
                if vc.is_playing():
                    if not ours:
                        return "⚠️ Сейчас играет музыка или озвучка, подожди.", "warning"
                    vc.stop()
            except Exception as e:
                return f"❌ Не смог зайти в канал: {e}", "error"

            old = self.idle.pop(guild.id, None)
            if old:
                old.cancel()
            token = object()
            self.now_playing[guild.id] = token
            loop = asyncio.get_running_loop()

            def _after(_err):
                def _done():
                    if self.now_playing.get(guild.id) is token:
                        self.now_playing.pop(guild.id, None)
                    self._schedule_leave(guild)
                loop.call_soon_threadsafe(_done)

            try:
                vc.play(disnake.FFmpegPCMAudio(str(path), executable=ffmpeg_exe() or "ffmpeg"), after=_after)
            except Exception as e:
                self.now_playing.pop(guild.id, None)
                return f"❌ Не получилось играть: {e}", "error"
        return f"🔊 {path.stem}", "success"

    # ---- управление звуками ----
    async def _deny(self, inter) -> bool:
        if not is_admin(inter):
            await inter.response.send_message(embed=note_embed("❌ Нет доступа."), ephemeral=True)
            return True
        return False

    @commands.slash_command(name="sounds_panel", description="Выложить в канал панель со звуками (для руководства)")
    async def sounds_panel(self, inter: disnake.ApplicationCommandInteraction):
        if await self._deny(inter):
            return
        names = list(list_sounds())
        if not names:
            await inter.response.send_message(embed=note_embed(
                "⚠️ Звуков пока нет. Добавь через /sound_add.", "warning"), ephemeral=True)
            return
        await inter.channel.send(
            embed=note_embed("Зайди в голосовой канал и жми кнопку: бот придёт и сыграет звук.",
                             title="🔊 Саундборд"),
            components=panel_components(names))
        await inter.response.send_message(embed=note_embed("✅ Панель выложена.", "success"), ephemeral=True)

    @commands.slash_command(name="sound_add", description="Добавить звук в саундборд (для руководства)")
    async def sound_add(self, inter: disnake.ApplicationCommandInteraction,
                        name: str = commands.Param(description="Название кнопки: буквы, цифры, - и _ (до 20)"),
                        file: disnake.Attachment = commands.Param(description="mp3, wav или ogg, до 2 МБ")):
        if await self._deny(inter):
            return
        name = name.strip().lower()
        ext = Path(file.filename).suffix.lower()
        if not NAME_RE.match(name):
            err = "Название: только буквы, цифры, - и _, до 20 знаков."
        elif ext not in EXTS:
            err = "Формат должен быть mp3, wav, ogg или m4a."
        elif file.size > MAX_BYTES:
            err = "Файл больше 2 МБ."
        elif name not in list_sounds() and len(list_sounds()) >= MAX_SOUNDS:
            err = f"Уже {MAX_SOUNDS} звуков — убери лишние через /sound_remove."
        else:
            err = ""
        if err:
            await inter.response.send_message(embed=note_embed("⚠️ " + err, "warning"), ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        SOUND_DIR.mkdir(exist_ok=True)
        old = list_sounds().get(name)
        if old:
            old.unlink(missing_ok=True)
        await file.save(SOUND_DIR / f"{name}{ext}")
        await inter.edit_original_response(embed=note_embed(
            f"✅ Звук **{name}** добавлен. Выложи панель заново через /sounds_panel, чтобы кнопка появилась.",
            "success"))

    @commands.slash_command(name="sound_remove", description="Убрать звук из саундборда (для руководства)")
    async def sound_remove(self, inter: disnake.ApplicationCommandInteraction,
                           name: str = commands.Param(description="Какой звук убрать")):
        if await self._deny(inter):
            return
        path = list_sounds().get(name.strip().lower())
        if path is None:
            await inter.response.send_message(embed=note_embed("⚠️ Такого звука нет.", "warning"), ephemeral=True)
            return
        path.unlink(missing_ok=True)
        await inter.response.send_message(embed=note_embed(f"✅ Звук **{path.stem}** убран.", "success"),
                                          ephemeral=True)

    @sound_remove.autocomplete("name")
    async def _names(self, inter: disnake.ApplicationCommandInteraction, current: str):
        cur = (current or "").lower()
        return [n for n in list_sounds() if cur in n][:25]


def setup(bot: commands.Bot):
    bot.add_cog(Soundboard(bot))
