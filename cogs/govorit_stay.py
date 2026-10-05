import asyncio
import json
import os
import tempfile
from collections import deque
from pathlib import Path

import disnake
import edge_tts
from disnake.ext import commands

# Отдельный ког: режим «всегда в войсе» + приветствие зашедших.
# Не трогает /govorit и /golos. Голос берёт из тех же настроек (golos_settings.json).

VOICE_SETTINGS_FILE = Path("golos_settings.json")
VOICE_DEFAULT = {"voice": "ru-RU-DmitryNeural", "rate": -12, "pitch": -18}
IDLE_DISCONNECT_SECONDS = 120
VISIT_COOLDOWN = 5  # сек: защита от спама входом-выходом (поставь 0, чтобы отключить)

_SILENCE = b"\x00" * 3840  # 20 мс тишины (48kHz, стерео, 16bit)


class _OneShot(disnake.AudioSource):
    """Играет приветствие, потом отдаёт тишину и сообщает, что закончил.
    Нужен, чтобы подменить источник на лету, не завершая плеер музыки."""

    def __init__(self, src: disnake.AudioSource, on_done):
        self.src = src
        self.on_done = on_done
        self.finished = False

    def read(self) -> bytes:
        if self.finished:
            return _SILENCE
        data = self.src.read()
        if data:
            return data
        self.finished = True
        self.on_done()
        return _SILENCE

    def is_opus(self) -> bool:
        return False

    def cleanup(self):
        self.src.cleanup()


async def tts_save(text: str, s: dict, path: str):
    """Озвучка с запасными вариантами: если сервис не вернул звук с заданной
    высотой/скоростью, пробуем мягче, а в конце обычным голосом."""
    last = None
    attempts = [
        {"rate": f"{s['rate']:+d}%", "pitch": f"{s['pitch']:+d}Hz"},
        {"rate": f"{s['rate']:+d}%"},
        {},
    ]
    for kw in attempts:
        try:
            await edge_tts.Communicate(text, s["voice"], **kw).save(path)
            return
        except edge_tts.exceptions.NoAudioReceived as e:
            last = e
    try:
        await edge_tts.Communicate(text, "ru-RU-DmitryNeural").save(path)
        return
    except edge_tts.exceptions.NoAudioReceived:
        raise last


def get_voice_settings(user_id: int) -> dict:
    s = dict(VOICE_DEFAULT)
    try:
        data = json.loads(VOICE_SETTINGS_FILE.read_text(encoding="utf-8"))
        s.update(data.get(str(user_id), {}))
    except Exception:
        pass
    return s


GUILD_FILE = Path("golos_guild.json")  # настройки сервера: канал, приветствие
GUILD_DEFAULT = {
    "channel_id": None,
    "stay": False,
    "greet": False,
    "visit": False,  # режим гостя: заходит на приветствие в любой войс и выходит
    "greet_text": "Привет, {name}! Добро пожаловать.",
    "owner_id": None,  # чьи настройки голоса (/golos) использовать для приветствия
}


def get_guild(guild_id: int) -> dict:
    try:
        data = json.loads(GUILD_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    g = dict(GUILD_DEFAULT)
    g.update(data.get(str(guild_id), {}))
    return g


def set_guild(guild_id: int, g: dict):
    try:
        data = json.loads(GUILD_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data[str(guild_id)] = g
    GUILD_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class GreetModal(disnake.ui.Modal):
    def __init__(self, view: "GuildPanelView"):
        self.view = view
        super().__init__(
            title="Текст приветствия",
            custom_id="golos_greet_modal",
            components=[
                disnake.ui.TextInput(
                    label="Что говорить, когда кто-то заходит",
                    custom_id="text",
                    style=disnake.TextInputStyle.paragraph,
                    value=view.g["greet_text"],
                    max_length=200,
                    placeholder="Можно использовать {name} - имя зашедшего",
                )
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        self.view.g["greet_text"] = inter.text_values["text"].strip() or GUILD_DEFAULT["greet_text"]
        self.view.g["owner_id"] = inter.author.id
        set_guild(self.view.guild_id, self.view.g)
        self.view.sync()
        try:
            await inter.response.edit_message(embed=self.view.make_embed(), view=self.view)
        except Exception:
            await inter.response.send_message("Текст сохранён.", ephemeral=True)


class GuildPanelView(disnake.ui.View):
    def __init__(self, cog: "GovoritStay", guild: disnake.Guild):
        super().__init__(timeout=900)
        self.cog = cog
        self.guild_id = guild.id
        self.g = get_guild(guild.id)
        self.sync()

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:
        if not inter.author.guild_permissions.manage_guild:
            await inter.response.send_message("Нужно право «Управление сервером».", ephemeral=True)
            return False
        return True

    def sync(self):
        self.stay_btn.label = "Всегда в войсе: ВКЛ" if self.g["stay"] else "Всегда в войсе: ВЫКЛ"
        self.stay_btn.style = disnake.ButtonStyle.success if self.g["stay"] else disnake.ButtonStyle.secondary
        self.greet_btn.label = "Приветствие: ВКЛ" if self.g["greet"] else "Приветствие: ВЫКЛ"
        self.greet_btn.style = disnake.ButtonStyle.success if self.g["greet"] else disnake.ButtonStyle.secondary
        self.visit_btn.label = "Режим гостя: ВКЛ" if self.g["visit"] else "Режим гостя: ВЫКЛ"
        self.visit_btn.style = disnake.ButtonStyle.success if self.g["visit"] else disnake.ButtonStyle.secondary

    def make_embed(self) -> disnake.Embed:
        e = disnake.Embed(title="🎛️ Голос бота на сервере", color=0x2B2D31)
        ch = f"<#{self.g['channel_id']}>" if self.g["channel_id"] else "не выбран"
        e.add_field(name="Канал", value=ch, inline=True)
        e.add_field(name="Всегда в войсе", value="включено" if self.g["stay"] else "выключено", inline=True)
        e.add_field(name="Приветствие", value="включено" if self.g["greet"] else "выключено", inline=True)
        e.add_field(name="Режим гостя", value="включён (заходит в любой войс, здоровается и уходит)" if self.g["visit"] else "выключен", inline=False)
        e.add_field(name="Текст приветствия", value=self.g["greet_text"], inline=False)
        e.set_footer(text="{name} в тексте заменится на имя зашедшего. Голос берётся из твоих настроек /golos.")
        return e

    async def refresh(self, inter: disnake.MessageInteraction):
        self.g["owner_id"] = inter.author.id
        set_guild(self.guild_id, self.g)
        self.sync()
        await inter.response.edit_message(embed=self.make_embed(), view=self)

    @disnake.ui.channel_select(
        placeholder="Выбери голосовой канал для бота",
        channel_types=[disnake.ChannelType.voice, disnake.ChannelType.stage_voice],
        row=0,
    )
    async def channel_pick(self, select: disnake.ui.ChannelSelect, inter: disnake.MessageInteraction):
        ch = select.values[0]
        self.g["channel_id"] = int(getattr(ch, "id", ch))
        await self.refresh(inter)
        if self.g["stay"]:
            await self.cog.ensure_stay(inter.guild)

    @disnake.ui.button(label="Всегда в войсе", style=disnake.ButtonStyle.secondary, row=1)
    async def stay_btn(self, button, inter: disnake.MessageInteraction):
        if not self.g["channel_id"]:
            await inter.response.send_message("Сначала выбери канал в списке выше.", ephemeral=True)
            return
        self.g["stay"] = not self.g["stay"]
        if self.g["stay"]:
            self.g["visit"] = False  # режимы взаимоисключающие
        await self.refresh(inter)
        if self.g["stay"]:
            await self.cog.ensure_stay(inter.guild)
        else:
            vc = inter.guild.voice_client
            if vc and not vc.is_playing():
                await vc.disconnect()

    @disnake.ui.button(label="Приветствие", style=disnake.ButtonStyle.secondary, row=1)
    async def greet_btn(self, button, inter: disnake.MessageInteraction):
        self.g["greet"] = not self.g["greet"]
        await self.refresh(inter)

    @disnake.ui.button(label="Режим гостя", style=disnake.ButtonStyle.secondary, row=1)
    async def visit_btn(self, button, inter: disnake.MessageInteraction):
        self.g["visit"] = not self.g["visit"]
        if self.g["visit"]:
            self.g["stay"] = False   # 24/7 выключаем
            self.g["greet"] = True   # приветствие нужно для гостя
        await self.refresh(inter)
        if self.g["visit"]:
            vc = inter.guild.voice_client
            if vc and not vc.is_playing():
                await vc.disconnect()

    @disnake.ui.button(label="Текст приветствия", emoji="✏️", style=disnake.ButtonStyle.primary, row=2)
    async def text_btn(self, button, inter: disnake.MessageInteraction):
        await inter.response.send_modal(GreetModal(self))

    @disnake.ui.button(label="Проверить приветствие", emoji="🔊", style=disnake.ButtonStyle.success, row=2)
    async def test_btn(self, button, inter: disnake.MessageInteraction):
        vc = inter.guild.voice_client
        channel = vc.channel if vc else (inter.author.voice.channel if inter.author.voice else None)
        if not channel:
            await inter.response.send_message("Зайди в голосовой канал или включи «Всегда в войсе».", ephemeral=True)
            return
        await inter.response.send_message("Говорю...", ephemeral=True)
        text = self.g["greet_text"].replace("{name}", inter.author.display_name)
        err = await self.cog.speak(inter.guild, channel, text, get_voice_settings(inter.author.id))
        await inter.edit_original_response(err or "Готово.")



class GovoritStay(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.locks: dict[int, asyncio.Lock] = {}
        self._greet_cd: dict[int, float] = {}
        self._return_tasks: dict[int, asyncio.Task] = {}
        self._idle_tasks: dict[int, asyncio.Task] = {}
        self._visit_queues: dict[int, deque] = {}
        self._visit_workers: dict[int, asyncio.Task] = {}
        self._visit_cd: dict[tuple, float] = {}

    def cog_unload(self):
        for t in self._visit_workers.values():
            t.cancel()

    def _lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self.locks:
            self.locks[guild_id] = asyncio.Lock()
        return self.locks[guild_id]

    async def ensure_stay(self, guild: disnake.Guild):
        """Зайти в выбранный канал, если включён режим «всегда в войсе»."""
        cfg = get_guild(guild.id)
        if not (cfg["stay"] and cfg["channel_id"]):
            return
        ch = guild.get_channel(cfg["channel_id"])
        if not ch:
            return
        async with self._lock(guild.id):
            vc = guild.voice_client
            try:
                if vc is not None and not vc.is_connected():
                    await vc.disconnect(force=True)
                    vc = None
                if vc is None:
                    await ch.connect()
                elif vc.channel.id != ch.id:
                    await vc.move_to(ch)
            except Exception as e:
                print(f"[govorit_stay] не удалось зайти в канал: {e}", flush=True)

    async def _return_later(self, guild: disnake.Guild):
        await asyncio.sleep(15)
        for _ in range(60):
            vc = guild.voice_client
            if vc and vc.is_playing():
                await asyncio.sleep(2)
            else:
                break
        await self.ensure_stay(guild)

    async def _idle_disconnect(self, guild: disnake.Guild):
        await asyncio.sleep(IDLE_DISCONNECT_SECONDS)
        if get_guild(guild.id)["stay"]:
            return
        if guild.id in self._visit_workers:
            return
        vc = guild.voice_client
        if vc and not vc.is_playing():
            await vc.disconnect()

    async def _visit_worker(self, guild: disnake.Guild):
        """Обходит очередь: зашёл → поздоровался → следующий. Очередь пуста → выходит."""
        q = self._visit_queues[guild.id]
        while True:
            while q:
                member_id, channel_id = q.popleft()
                cfg = get_guild(guild.id)
                if not cfg["visit"]:
                    q.clear()
                    break
                member = guild.get_member(member_id)
                ch = guild.get_channel(channel_id)
                if not member or not ch:
                    continue
                # человек уже ушёл из канала — не ходим зря
                if not member.voice or not member.voice.channel or member.voice.channel.id != channel_id:
                    continue
                perms = ch.permissions_for(guild.me)
                if not (perms.connect and perms.speak):
                    continue
                text = cfg["greet_text"].replace("{name}", member.display_name)
                s = get_voice_settings(cfg["owner_id"] or 0)
                vc = guild.voice_client
                try:
                    if vc and vc.is_connected() and (vc.is_playing() or vc.is_paused()):
                        # играет музыка: пауза → к человеку → приветствие → назад → музыка дальше
                        await self._swap_speak(guild, vc, ch, text, s)
                    else:
                        await self.speak(guild, ch, text, s)
                except Exception as e:
                    print(f"[govorit_stay] ошибка визита: {e}", flush=True)

            vc = guild.voice_client
            # выходим сразу; но если играет музыка — остаёмся (это её подключение)
            if vc and not get_guild(guild.id)["stay"] and not (vc.is_playing() or vc.is_paused()):
                try:
                    await vc.disconnect()
                except Exception:
                    pass
            if not q:
                self._visit_workers.pop(guild.id, None)  # без await между проверкой и pop
                return

    async def _swap_speak(self, guild: disnake.Guild, vc, channel, text: str, s: dict):
        """Музыка играет. Ставим на паузу, переезжаем к человеку, говорим приветствие,
        возвращаемся в прежний канал и продолжаем музыку с того же места."""
        fd, path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        try:
            await tts_save(text, s, path)  # озвучка готовится, пока музыка ещё играет

            old = vc.source
            was_paused = vc.is_paused()
            origin = vc.channel
            if old is None:
                return
            loop = asyncio.get_running_loop()
            done = asyncio.Event()
            greet = _OneShot(disnake.FFmpegPCMAudio(path), lambda: loop.call_soon_threadsafe(done.set))

            vc.pause()
            try:
                if vc.channel.id != channel.id:
                    await vc.move_to(channel)
                vc.source = greet  # плеер тот же, музыка не теряется — просто источник на время другой
                try:
                    await asyncio.wait_for(done.wait(), timeout=60)
                except asyncio.TimeoutError:
                    pass
            finally:
                # вернуть всё как было
                try:
                    vc.pause()
                    if vc.is_connected() and vc.channel.id != origin.id:
                        await vc.move_to(origin)
                    vc.source = old
                    if was_paused:
                        vc.pause()
                except Exception as e:
                    print(f"[govorit_stay] не удалось вернуть музыку: {e}", flush=True)
                    try:
                        old.cleanup()
                    except Exception:
                        pass
                try:
                    greet.cleanup()
                except Exception:
                    pass
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    async def speak(self, guild: disnake.Guild, channel, text: str, s: dict):
        """Возвращает None при успехе или текст ошибки."""
        async with self._lock(guild.id):
            vc = guild.voice_client
            try:
                if vc is None:
                    vc = await channel.connect()
                elif vc.channel.id != channel.id:
                    await vc.move_to(channel)
            except Exception as e:
                return f"Не смог зайти в канал: {e}"

            # если сейчас что-то играет (например, /govorit), подождать
            for _ in range(40):
                if vc.is_playing():
                    await asyncio.sleep(0.5)
                else:
                    break

            fd, path = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
            try:
                await tts_save(text, s, path)
                done = asyncio.Event()
                loop = asyncio.get_running_loop()
                vc.play(
                    disnake.FFmpegPCMAudio(path),
                    after=lambda err: loop.call_soon_threadsafe(done.set),
                )
                await done.wait()
                return None
            except Exception as e:
                return f"Ошибка: {e}"
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
                _cfg = get_guild(guild.id)
                if not _cfg["stay"] and not _cfg["visit"]:
                    old = self._idle_tasks.get(guild.id)
                    if old:
                        old.cancel()
                    self._idle_tasks[guild.id] = asyncio.create_task(self._idle_disconnect(guild))

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await self.ensure_stay(guild)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: disnake.Member, before: disnake.VoiceState, after: disnake.VoiceState):
        guild = member.guild
        cfg = get_guild(guild.id)

        if member.id == self.bot.user.id:
            if not (cfg["stay"] and cfg["channel_id"]):
                return
            if before.channel and after.channel is None:
                # бота выкинуло из канала: вернуться
                await asyncio.sleep(5)
                await self.ensure_stay(guild)
            elif after.channel and after.channel.id != cfg["channel_id"]:
                # бот ушёл говорить в другой канал: вернуться потом
                old = self._return_tasks.get(guild.id)
                if old:
                    old.cancel()
                self._return_tasks[guild.id] = asyncio.create_task(self._return_later(guild))
            return

        if member.bot or not cfg["greet"]:
            return
        if after.channel is None or before.channel == after.channel:
            return
        # режим гостя: любой войс, по очереди, бот не сидит постоянно
        if cfg["visit"] and not cfg["stay"]:
            now = asyncio.get_running_loop().time()
            key = (guild.id, member.id)
            if now - self._visit_cd.get(key, 0) < VISIT_COOLDOWN:
                return
            self._visit_cd[key] = now
            q = self._visit_queues.setdefault(guild.id, deque(maxlen=20))
            q.append((member.id, after.channel.id))
            task = self._visit_workers.get(guild.id)
            if task is None or task.done():
                self._visit_workers[guild.id] = asyncio.create_task(self._visit_worker(guild))
            return

        vc = guild.voice_client
        if not vc or vc.channel.id != after.channel.id:
            return
        now = asyncio.get_running_loop().time()
        if now - self._greet_cd.get(member.id, 0) < 10:
            return
        self._greet_cd[member.id] = now
        text = cfg["greet_text"].replace("{name}", member.display_name)
        await self.speak(guild, after.channel, text, get_voice_settings(cfg["owner_id"] or 0))

    @commands.slash_command(
        name="golos-server",
        description="Панель сервера: канал, режим «всегда в войсе», приветствие",
        default_member_permissions=disnake.Permissions(manage_guild=True),
    )
    async def golos_server(self, inter: disnake.ApplicationCommandInteraction):
        if not inter.guild:
            await inter.response.send_message("Только на сервере.", ephemeral=True)
            return
        view = GuildPanelView(self, inter.guild)
        await inter.response.send_message(embed=view.make_embed(), view=view, ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(GovoritStay(bot))
