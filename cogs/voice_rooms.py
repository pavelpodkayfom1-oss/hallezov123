"""Временные войс-комнаты: свой канал на отряд, который сам исчезает, когда все вышли.

Кнопка «🎤 Создать комнату» — бот делает голосовой канал с именем автора и ставит его в
категорию «Комнаты» (или в самую людную голосовую категорию). Лимит на 4 человека, автору
даёт право двигать и кикать внутри — никто посторонний комнату не разгромит. Как только
в канале не остаётся людей, он живёт ещё несколько секунд и удаляется, чтобы список
каналов не превращался в свалку.

Панель с кнопкой отправляет экран «Комнаты: отправить панель» из веб-панели семьи
(self-command /rooms_panel нужен только если панель хотят из Discord).
"""
import asyncio
import json
import os
from typing import Dict

import disnake
from disnake.ext import commands

from utils.embeds import base_embed, note_embed

ROOM_LIMIT = 4
ROOM_PREFIX = "🎤"
ROOM_CATEGORY = "комнат"      # ищем категорию с таким словом в названии
EMPTY_GRACE = 45              # секунд, которые пустая комната ещё живёт
MAX_ROOMS = 8                 # не забиваем список каналов

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, "voice_rooms.json")


def _load() -> Dict[str, object]:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    rooms = data.get("rooms") if isinstance(data, dict) else None
    if not isinstance(rooms, dict):
        return {}
    out = {}
    for cid, owner in rooms.items():
        try:
            out[int(cid)] = int(owner)
        except (TypeError, ValueError):
            continue
    return out


def _save(rooms: Dict[int, int]) -> str:
    """Комната должна пережить рестарт: иначе пустой канал висит в списке навсегда."""
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"rooms": {str(k): v for k, v in rooms.items()}}, f,
                      ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
        return ""
    except Exception as e:
        return f"Файл комнат не записался: `{e}`"


def _room_name(nick: str) -> str:
    clean = " ".join(str(nick or "").split())
    if not clean:
        return f"{ROOM_PREFIX} комната"
    return f"{ROOM_PREFIX} {clean}"[:64]


def _humans(channel) -> list:
    try:
        return [m for m in channel.members if not m.bot]
    except Exception:
        return []


def _category(guild: disnake.Guild):
    """Куда ставить комнату: своя категория, а иначе — самая людная голосовая."""
    for c in guild.channels:
        if c.type == disnake.ChannelType.category and ROOM_CATEGORY in str(c.name).lower():
            return c
    voice = [c for c in guild.channels if c.type == disnake.ChannelType.voice]
    for c in voice:
        cat = getattr(c, "category", None)
        if cat and ROOM_CATEGORY in cat.name.lower():
            return cat
    counts: Dict[int, int] = {}
    for c in voice:
        cat = getattr(c, "category", None)
        if cat is not None:
            counts[cat.id] = counts.get(cat.id, 0) + 1
    if not counts:
        return None
    best = max(counts.items(), key=lambda kv: kv[1])[0]
    return guild.get_channel(best)


class RoomPanelView(disnake.ui.View):
    """Постоянное представление: кнопки работают и после перезапуска бота."""

    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="Создать комнату", emoji="🎤", style=disnake.ButtonStyle.success,
                       custom_id="room:create", row=0)
    async def create(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("VoiceRooms").create_room(inter)

    @disnake.ui.button(label="Убрать пустые", emoji="🧹", style=disnake.ButtonStyle.secondary,
                       custom_id="room:clean", row=0)
    async def clean(self, button, inter: disnake.MessageInteraction):
        await inter.bot.get_cog("VoiceRooms").clean_rooms(inter)


class VoiceRooms(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.rooms: Dict[int, int] = _load()
        self._timers: Dict[int, asyncio.TimerHandle] = {}

    def _viewed(self, guild: disnake.Guild):
        """Бот как участник сервера: у него спрашиваем права."""
        return guild.me if guild.me is not None else guild.get_member(self.bot.user.id)

    async def _move(self, member, channel) -> bool:
        try:
            await member.move_to(channel)
            return True
        except Exception:
            return False

    @commands.Cog.listener()
    async def on_ready(self):
        if not getattr(self.bot, "_rooms_view_added", False):
            self.bot.add_view(RoomPanelView())
            self.bot._rooms_view_added = True
            print(f"🎤 Комнаты: следим за {len(self.rooms)} каналами", flush=True)
        # что успело опустеть, пока бота не было
        for guild in self.bot.guilds:
            await self._sweep(guild)

    async def _drop(self, channel) -> bool:
        try:
            await channel.delete(reason="Временная комната опустела")
            return True
        except Exception:
            return False
        finally:
            self.rooms.pop(channel.id, None)
            _save(self.rooms)

    def _forget(self, cid: int):
        self.rooms.pop(cid, None)
        _save(self.rooms)

    def _schedule(self, channel):
        """Отложенное удаление: зайти в комнату успели, таймер отменяем."""
        handle = self._timers.pop(channel.id, None)
        if handle is not None:
            handle.cancel()
        loop = asyncio.get_running_loop()

        def fire():
            self._timers.pop(channel.id, None)
            if _humans(channel):
                return
            asyncio.run_coroutine_threadsafe(self._drop(channel), loop)

        self._timers[channel.id] = loop.call_later(EMPTY_GRACE, fire)

    def _cancel(self, cid: int):
        handle = self._timers.pop(cid, None)
        if handle is not None:
            handle.cancel()

    async def _sweep(self, guild: disnake.Guild):
        """Убрать мёртвые id (канал удалили руками) и пустые комнаты."""
        gone = []
        for cid in list(self.rooms):
            channel = guild.get_channel(cid)
            if channel is None or channel.type != disnake.ChannelType.voice:
                gone.append(cid)
                continue
            if not _humans(channel):
                if await self._drop(channel):
                    gone.append(cid)
        for cid in gone:
            self._forget(cid)

    # ---------- кнопки ----------
    async def create_room(self, inter: disnake.MessageInteraction):
        guild = inter.guild
        if guild is None:
            return await inter.response.send_message(embed=note_embed("Только на сервере."), ephemeral=True)
        me = self._viewed(guild)
        perms = getattr(me, "permissions", None)
        if me is None or perms is None or not perms.manage_channels:
            return await inter.response.send_message(
                embed=note_embed("❌ У меня нет права **Управлять каналами** — не могу создать комнату."),
                ephemeral=True)
        if len(self.rooms) >= MAX_ROOMS:
            return await inter.response.send_message(
                embed=note_embed(f"Уже {MAX_ROOMS} комнат висит. Кнопка «🧹 Убрать пустые» — сюда."),
                ephemeral=True)

        # своя комната уже есть — просто переезжаем в неё
        mine = [cid for cid, owner in self.rooms.items() if owner == inter.author.id]
        for cid in mine:
            channel = guild.get_channel(cid)
            if channel is not None and channel.type == disnake.ChannelType.voice:
                self._cancel(cid)
                where = inter.author.voice.channel if inter.author.voice else None
                if where is not None and where.id == cid:
                    return await inter.response.send_message(
                        embed=note_embed(f"Ты уже в своей **{channel.name}**."), ephemeral=True)
                if await self._move(inter.author, channel):
                    return await inter.response.send_message(
                        embed=note_embed(f"Возвращаю в твою комнату: **{channel.name}**"), ephemeral=True)
                return await inter.response.send_message(
                    embed=note_embed(f"Комната твоя: **{channel.name}** — заходи сам, "
                                     "пересадить не вышло."), ephemeral=True)

        await inter.response.defer(ephemeral=True)
        overwrites = {
            guild.default_role: disnake.PermissionOverwrite(connect=True, speak=True),
            inter.author: disnake.PermissionOverwrite(connect=True, speak=True, stream=True,
                                                     manage_channels=True, move_members=True,
                                                     mute_members=True),
        }
        try:
            channel = await guild.create_voice_channel(
                name=_room_name(inter.author.display_name),
                category=_category(guild),
                user_limit=ROOM_LIMIT,
                overwrites=overwrites,
                reason=f"Временная комната: {inter.author}")
        except Exception as e:
            return await inter.followup.send(
                embed=note_embed(f"❌ Канал не создан: `{e}`\n"
                                 "Проверь, что у меня есть «Управлять каналами» в нужной категории."),
                ephemeral=True)
        self.rooms[channel.id] = inter.author.id
        err = _save(self.rooms)
        moved = ""
        current = inter.author.voice.channel if inter.author.voice else None
        if current is not None and current.id != channel.id:
            moved = ("\n✅ Пересадил тебя туда." if await self._move(inter.author, channel)
                     else "\nЗаходи сам — пересадить не вышло.")
        answer = (f"Комната **{channel.name}** — на {ROOM_LIMIT} человека, зови своих."
                  f"{moved}\nКак только все выйдут, канал исчезнет сам (ждёт {EMPTY_GRACE} сек)."
                  + (f"\n⚠️ {err}" if err else ""))
        await inter.followup.send(embed=note_embed(answer), ephemeral=True)

    async def clean_rooms(self, inter: disnake.MessageInteraction):
        guild = inter.guild
        if guild is None:
            return await inter.response.send_message(embed=note_embed("Только на сервере."), ephemeral=True)
        removed, busy = 0, 0
        for cid in list(self.rooms):
            channel = guild.get_channel(cid)
            if channel is None:
                self._forget(cid)
                continue
            if _humans(channel):
                busy += 1
                continue
            if await self._drop(channel):
                removed += 1
            self._forget(cid)
        await inter.response.send_message(
            embed=note_embed(f"🧹 Убрал комнат: {removed}" + (f", занято ещё {busy}" if busy else "")),
            ephemeral=True)

    # ---------- экран для веб-панели ----------
    @commands.slash_command(name="rooms_panel", description="Отправить панель временных комнат в этот канал",
                            default_member_permissions=disnake.Permissions(manage_guild=True))
    async def rooms_panel(self, inter: disnake.ApplicationCommandInteraction):
        embed = base_embed(
            "🎤 Временные комнаты",
            (f"«🎤 Создать комнату» — бот делает голосовой канал с твоим именем на "
             f"{ROOM_LIMIT} человек. Внутри ты можешь двигать и мутить своих.\n\n"
             f"Никто не остался в канале — через {EMPTY_GRACE} секунд он удалится сам, "
             "список каналов не засоряется. «🧹 Убрать пустые» чистит всё сразу."),
            guild=inter.guild,
        )
        await inter.channel.send(embed=embed, view=RoomPanelView())
        await inter.response.send_message(embed=note_embed("✅ Панель отправлена."), ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        cid = before.channel.id if before.channel else None
        if after.channel is not None:
            self._cancel(after.channel.id)
        if cid is None or cid not in self.rooms:
            return
        channel = before.channel
        if _humans(channel):
            return
        self._schedule(channel)


def setup(bot: commands.Bot):
    bot.add_cog(VoiceRooms(bot))
