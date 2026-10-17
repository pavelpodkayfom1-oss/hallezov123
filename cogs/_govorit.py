import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Optional

import disnake
import edge_tts
from disnake.ext import commands

IDLE_DISCONNECT_SECONDS = 120
SETTINGS_FILE = Path("golos_settings.json")  # создастся рядом с ботом

DEFAULT = {"voice": "ru-RU-DmitryNeural", "rate": -12, "pitch": -18}

# ключ: (название, скорость %, высота Hz)
PRESETS = {
    "calm": ("Спокойный", -15, -12),
    "strict": ("Строгий", -10, -22),
    "hard": ("Жёсткий", -5, -32),
    "slow": ("Медленный", -28, -15),
    "normal": ("Обычный", 0, 0),
}

VOICES = {
    "ru-RU-DmitryNeural": "Dmitry (русский)",
    "en-US-AndrewMultilingualNeural": "Andrew (мультиязычный)",
    "en-US-BrianMultilingualNeural": "Brian (мультиязычный)",
}

RATE_MIN, RATE_MAX = -50, 30
PITCH_MIN, PITCH_MAX = -50, 30


def _load() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict):
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_settings(user_id: int) -> dict:
    s = dict(DEFAULT)
    s.update(_load().get(str(user_id), {}))
    return s


def set_settings(user_id: int, s: dict):
    data = _load()
    data[str(user_id)] = s
    _save(data)


GUILD_FILE = Path("golos_guild.json")  # общий файл с govorit_stay


def _stay_on(guild_id: int) -> bool:
    """Включён ли режим «всегда в войсе» (его настраивает ког govorit_stay)."""
    try:
        data = json.loads(GUILD_FILE.read_text(encoding="utf-8"))
        return bool(data.get(str(guild_id), {}).get("stay"))
    except Exception:
        return False


# ───────────── доступ по роли ─────────────
# Хранится в том же golos_guild.json под ключом "speak_role_id".
# Остальные ключи (stay, greet и т.д.) не трогаем.

def _read_guild_file() -> dict:
    try:
        return json.loads(GUILD_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_speak_roles(guild_id: int) -> list:
    """Список ID ролей с доступом (поддерживает старый одиночный ключ speak_role_id)."""
    g = _read_guild_file().get(str(guild_id), {})
    ids = [int(x) for x in g.get("speak_role_ids", []) if x]
    old = g.get("speak_role_id")
    if old and int(old) not in ids:
        ids.append(int(old))
    return ids


def set_speak_roles(guild_id: int, role_ids: list):
    data = _read_guild_file()
    g = data.get(str(guild_id), {})
    g["speak_role_ids"] = [int(x) for x in role_ids]
    g.pop("speak_role_id", None)  # старый ключ больше не нужен
    data[str(guild_id)] = g
    GUILD_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def roles_text(guild_id: int, empty: str) -> str:
    ids = get_speak_roles(guild_id)
    return ", ".join(f"<@&{i}>" for i in ids) if ids else empty


def is_server_admin(member: disnake.Member) -> bool:
    return bool(member.guild_permissions.manage_guild)


def can_speak(member) -> bool:
    """Можно ли человеку управлять голосом бота.
    • «Управление сервером» — всегда можно.
    • Если роли не выбраны — можно всем (как раньше).
    • Если выбраны — только владельцам хотя бы одной из этих ролей."""
    if not isinstance(member, disnake.Member):
        return False
    if is_server_admin(member):
        return True
    ids = set(get_speak_roles(member.guild.id))
    if not ids:
        return True
    return any(r.id in ids for r in member.roles)


NO_ACCESS = "❌ У тебя нет доступа к голосу бота. Нужна одна из специальных ролей."


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


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ───────────── панель настройки голоса (/golos) ─────────────

class PanelView(disnake.ui.View):
    def __init__(self, cog: "Govorit", user_id: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.user_id = user_id
        self.s = get_settings(user_id)

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:
        if inter.author.id != self.user_id:
            await inter.response.send_message("Это не твоя панель. Открой свою: /golos", ephemeral=True)
            return False
        if not can_speak(inter.author):
            await inter.response.send_message(NO_ACCESS, ephemeral=True)
            return False
        return True

    def make_embed(self) -> disnake.Embed:
        e = disnake.Embed(title="🎙️ Настройка голоса", color=0x2B2D31)
        e.add_field(name="Голос", value=VOICES.get(self.s["voice"], self.s["voice"]), inline=False)
        e.add_field(name="Скорость", value=f"{self.s['rate']:+d}%", inline=True)
        e.add_field(name="Высота", value=f"{self.s['pitch']:+d} Hz", inline=True)
        e.set_footer(text="Ниже высота = строже и глубже. Меньше скорость = медленнее и спокойнее.")
        return e

    async def refresh(self, inter: disnake.MessageInteraction):
        set_settings(self.user_id, self.s)
        await inter.response.edit_message(embed=self.make_embed(), view=self)

    @disnake.ui.string_select(
        placeholder="Готовый стиль",
        options=[disnake.SelectOption(label=v[0], value=k) for k, v in PRESETS.items()],
        row=0,
    )
    async def preset(self, select: disnake.ui.StringSelect, inter: disnake.MessageInteraction):
        _, rate, pitch = PRESETS[select.values[0]]
        self.s["rate"], self.s["pitch"] = rate, pitch
        await self.refresh(inter)

    @disnake.ui.string_select(
        placeholder="Голос",
        options=[disnake.SelectOption(label=name, value=key) for key, name in VOICES.items()],
        row=1,
    )
    async def voice(self, select: disnake.ui.StringSelect, inter: disnake.MessageInteraction):
        self.s["voice"] = select.values[0]
        await self.refresh(inter)

    @disnake.ui.button(label="Ниже", emoji="⬇️", style=disnake.ButtonStyle.secondary, row=2)
    async def pitch_down(self, button, inter):
        self.s["pitch"] = clamp(self.s["pitch"] - 4, PITCH_MIN, PITCH_MAX)
        await self.refresh(inter)

    @disnake.ui.button(label="Выше", emoji="⬆️", style=disnake.ButtonStyle.secondary, row=2)
    async def pitch_up(self, button, inter):
        self.s["pitch"] = clamp(self.s["pitch"] + 4, PITCH_MIN, PITCH_MAX)
        await self.refresh(inter)

    @disnake.ui.button(label="Медленнее", emoji="🐢", style=disnake.ButtonStyle.secondary, row=2)
    async def rate_down(self, button, inter):
        self.s["rate"] = clamp(self.s["rate"] - 4, RATE_MIN, RATE_MAX)
        await self.refresh(inter)

    @disnake.ui.button(label="Быстрее", emoji="🐇", style=disnake.ButtonStyle.secondary, row=2)
    async def rate_up(self, button, inter):
        self.s["rate"] = clamp(self.s["rate"] + 4, RATE_MIN, RATE_MAX)
        await self.refresh(inter)

    @disnake.ui.button(label="Проверить", emoji="🔊", style=disnake.ButtonStyle.success, row=3)
    async def test(self, button, inter: disnake.MessageInteraction):
        voice_state = inter.author.voice
        if not voice_state or not voice_state.channel:
            await inter.response.send_message("Сначала зайди в голосовой канал.", ephemeral=True)
            return
        await inter.response.send_message("Говорю...", ephemeral=True)
        err = await self.cog.speak(
            inter.guild, voice_state.channel,
            "Говорю спокойно и чётко. Так звучит мой голос.", self.s,
        )
        await inter.edit_original_response(err or "Готово.")

    @disnake.ui.button(label="Сброс", emoji="♻️", style=disnake.ButtonStyle.danger, row=3)
    async def reset(self, button, inter):
        self.s = dict(DEFAULT)
        await self.refresh(inter)

    @disnake.ui.button(label="Позвать в войс", emoji="🎙️", style=disnake.ButtonStyle.primary, row=4)
    async def join_voice(self, button, inter: disnake.MessageInteraction):
        await self.cog.do_join(inter)

    @disnake.ui.button(label="Выйти", emoji="🚪", style=disnake.ButtonStyle.danger, row=4)
    async def leave_voice(self, button, inter: disnake.MessageInteraction):
        await self.cog.do_leave(inter)


# ───────────── панель управления говорением (/govorit-panel) ─────────────

class SayModal(disnake.ui.Modal):
    def __init__(self, cog: "Govorit"):
        self.cog = cog
        super().__init__(
            title="Что сказать боту",
            custom_id="govorit_say_modal",
            components=[
                disnake.ui.TextInput(
                    label="Текст",
                    custom_id="text",
                    style=disnake.TextInputStyle.paragraph,
                    max_length=300,
                    placeholder="Бот произнесёт это в твоём голосовом канале",
                )
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        if not can_speak(inter.author):
            await inter.response.send_message(NO_ACCESS, ephemeral=True)
            return
        voice_state = inter.author.voice
        if not voice_state or not voice_state.channel:
            await inter.response.send_message("Сначала зайди в голосовой канал.", ephemeral=True)
            return
        text = inter.text_values["text"].strip()
        if not text:
            await inter.response.send_message("Пустой текст.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        err = await self.cog.speak(inter.guild, voice_state.channel, text, get_settings(inter.author.id))
        await inter.edit_original_response(err or "✅ Сказал.")


class AccessView(disnake.ui.View):
    """Выбор ролей, которым разрешено управлять голосом. Только для админов."""

    def __init__(self, guild: disnake.Guild, user_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild.id
        self.user_id = user_id

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:
        if inter.author.id != self.user_id or not is_server_admin(inter.author):
            await inter.response.send_message("Нужно право «Управление сервером».", ephemeral=True)
            return False
        return True

    def make_embed(self) -> disnake.Embed:
        e = disnake.Embed(title="🔐 Доступ к голосу бота", color=0x2B2D31)
        e.add_field(
            name="Роли с доступом",
            value=roles_text(self.guild_id, "не выбраны (доступно всем)"),
            inline=False,
        )
        e.set_footer(text="Админы с правом «Управление сервером» имеют доступ всегда.")
        return e

    @disnake.ui.role_select(placeholder="Выбери роли с доступом (до 10)", min_values=1, max_values=10, row=0)
    async def pick_role(self, select: disnake.ui.RoleSelect, inter: disnake.MessageInteraction):
        set_speak_roles(self.guild_id, [int(getattr(r, "id", r)) for r in select.values])
        await inter.response.edit_message(embed=self.make_embed(), view=self)

    @disnake.ui.button(label="Снять ограничение (доступно всем)", emoji="🔓", style=disnake.ButtonStyle.secondary, row=1)
    async def clear_role(self, button, inter: disnake.MessageInteraction):
        set_speak_roles(self.guild_id, [])
        await inter.response.edit_message(embed=self.make_embed(), view=self)


class ControlView(disnake.ui.View):
    def __init__(self, cog: "Govorit", guild: disnake.Guild):
        super().__init__(timeout=900)
        self.cog = cog
        self.guild = guild

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:
        if not can_speak(inter.author):
            await inter.response.send_message(NO_ACCESS, ephemeral=True)
            return False
        return True

    def make_embed(self) -> disnake.Embed:
        vc = self.guild.voice_client
        e = disnake.Embed(title="🎛️ Управление голосом бота", color=0x2B2D31)
        e.add_field(name="Доступ", value=roles_text(self.guild.id, "всем"), inline=True)
        e.add_field(name="Бот в канале", value=vc.channel.mention if vc and vc.channel else "нигде", inline=True)
        e.set_footer(text="Зайди в голосовой канал и нажми «Сказать».")
        return e

    @disnake.ui.button(label="Сказать", emoji="💬", style=disnake.ButtonStyle.success, row=0)
    async def say(self, button, inter: disnake.MessageInteraction):
        await inter.response.send_modal(SayModal(self.cog))

    @disnake.ui.button(label="Замолчать", emoji="🛑", style=disnake.ButtonStyle.danger, row=0)
    async def shut_up(self, button, inter: disnake.MessageInteraction):
        vc = inter.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await inter.response.send_message("Замолчал.", ephemeral=True)
        else:
            await inter.response.send_message("Я сейчас ничего не говорю.", ephemeral=True)

    @disnake.ui.button(label="Позвать в войс", emoji="🎙️", style=disnake.ButtonStyle.primary, row=1)
    async def join(self, button, inter: disnake.MessageInteraction):
        await self.cog.do_join(inter)

    @disnake.ui.button(label="Выйти", emoji="🚪", style=disnake.ButtonStyle.secondary, row=1)
    async def leave(self, button, inter: disnake.MessageInteraction):
        await self.cog.do_leave(inter)

    @disnake.ui.button(label="Настройки голоса", emoji="🎚️", style=disnake.ButtonStyle.secondary, row=2)
    async def settings(self, button, inter: disnake.MessageInteraction):
        view = PanelView(self.cog, inter.author.id)
        await inter.response.send_message(embed=view.make_embed(), view=view, ephemeral=True)

    @disnake.ui.button(label="Доступ (роли)", emoji="🔐", style=disnake.ButtonStyle.secondary, row=2)
    async def access(self, button, inter: disnake.MessageInteraction):
        if not is_server_admin(inter.author):
            await inter.response.send_message("Менять доступ могут только админы («Управление сервером»).", ephemeral=True)
            return
        view = AccessView(inter.guild, inter.author.id)
        await inter.response.send_message(embed=view.make_embed(), view=view, ephemeral=True)


class Govorit(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.locks: dict[int, asyncio.Lock] = {}
        self.idle_tasks: dict[int, asyncio.Task] = {}

    def _lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self.locks:
            self.locks[guild_id] = asyncio.Lock()
        return self.locks[guild_id]

    async def _idle_disconnect(self, guild: disnake.Guild):
        await asyncio.sleep(IDLE_DISCONNECT_SECONDS)
        if _stay_on(guild.id):
            return
        vc = guild.voice_client
        if vc and not vc.is_playing():
            await vc.disconnect()

    def _schedule_idle(self, guild: disnake.Guild):
        old = self.idle_tasks.get(guild.id)
        if old:
            old.cancel()
        self.idle_tasks[guild.id] = asyncio.create_task(self._idle_disconnect(guild))

    async def do_join(self, inter: disnake.MessageInteraction):
        voice_state = inter.author.voice
        if not voice_state or not voice_state.channel:
            await inter.response.send_message("Сначала зайди в голосовой канал.", ephemeral=True)
            return
        channel = voice_state.channel
        async with self._lock(inter.guild.id):
            vc = inter.guild.voice_client
            try:
                if vc is None:
                    await channel.connect()
                elif vc.channel.id != channel.id:
                    await vc.move_to(channel)
            except Exception as e:
                await inter.response.send_message(f"Не смог зайти: {e}", ephemeral=True)
                return
        self._schedule_idle(inter.guild)
        await inter.response.send_message(f"Зашёл в **{channel.name}**.", ephemeral=True)

    async def do_leave(self, inter: disnake.MessageInteraction):
        vc = inter.guild.voice_client
        if not vc:
            await inter.response.send_message("Я не в голосовом канале.", ephemeral=True)
            return
        if _stay_on(inter.guild.id):
            await inter.response.send_message(
                "Включён режим «всегда в войсе», я вернусь обратно. Выключи его в /golos-server.",
                ephemeral=True,
            )
            return
        await vc.disconnect()
        await inter.response.send_message("Вышел.", ephemeral=True)

    async def speak(self, guild: disnake.Guild, channel: disnake.VoiceChannel, text: str, s: dict):
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

            old = self.idle_tasks.get(guild.id)
            if old:
                old.cancel()

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
                self._schedule_idle(guild)

    @commands.slash_command(name="govorit", description="Бот произнесёт твой текст в голосовом канале")
    async def govorit(
        self,
        inter: disnake.ApplicationCommandInteraction,
        text: str = commands.Param(description="Что сказать", max_length=300),
    ):
        if not inter.guild:
            await inter.response.send_message("Только на сервере.", ephemeral=True)
            return
        if not can_speak(inter.author):
            await inter.response.send_message(NO_ACCESS, ephemeral=True)
            return
        voice_state = inter.author.voice
        if not voice_state or not voice_state.channel:
            await inter.response.send_message("Сначала зайди в голосовой канал.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        err = await self.speak(inter.guild, voice_state.channel, text, get_settings(inter.author.id))
        await inter.edit_original_response(err or "Готово.")

    @commands.slash_command(name="govorit-panel", description="Панель управления голосом бота (для роли с доступом)")
    async def govorit_panel(self, inter: disnake.ApplicationCommandInteraction):
        if not inter.guild:
            await inter.response.send_message("Только на сервере.", ephemeral=True)
            return
        if not can_speak(inter.author):
            await inter.response.send_message(NO_ACCESS, ephemeral=True)
            return
        view = ControlView(self, inter.guild)
        await inter.response.send_message(embed=view.make_embed(), view=view, ephemeral=True)

    @commands.slash_command(name="golos", description="Панель настройки голоса бота")
    async def golos(self, inter: disnake.ApplicationCommandInteraction):
        if not inter.guild:
            await inter.response.send_message("Только на сервере.", ephemeral=True)
            return
        if not can_speak(inter.author):
            await inter.response.send_message(NO_ACCESS, ephemeral=True)
            return
        view = PanelView(self, inter.author.id)
        await inter.response.send_message(embed=view.make_embed(), view=view, ephemeral=True)

    @commands.slash_command(name="zamolchi", description="Остановить речь и выйти из голосового канала")
    async def zamolchi(self, inter: disnake.ApplicationCommandInteraction):
        if not inter.guild:
            await inter.response.send_message("Только на сервере.", ephemeral=True)
            return
        if not can_speak(inter.author):
            await inter.response.send_message(NO_ACCESS, ephemeral=True)
            return
        vc = inter.guild.voice_client
        if not vc:
            await inter.response.send_message("Я не в голосовом канале.", ephemeral=True)
            return
        if _stay_on(inter.guild.id):
            vc.stop()
            await inter.response.send_message("Замолчал. Остаюсь в канале.", ephemeral=True)
            return
        await vc.disconnect()
        await inter.response.send_message("Вышел.", ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(Govorit(bot))
