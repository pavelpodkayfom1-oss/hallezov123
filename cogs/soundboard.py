"""Саундборд: панель кнопок со звуками, бот играет их в голосовом канале.

 • /sounds_panel — выложить панель в канал (для руководства)
 • /sound_add <имя> <файл> — добавить звук (для руководства)
 • /sound_url <имя> <ссылка> — добавить звук по ссылке (для руководства)
 • /sound_edit <имя> — поменять подпись, знак или цвет (для руководства)
 • /sound_remove <имя> — убрать звук (для руководства)
Жать кнопки может любой, кто сидит в голосовом канале. Звуки и их подписи лежат в
utils/sound_store.py — тот же код использует веб-панель, поэтому список правится в
одном месте, а панель в Discord обновляется сама после изменений с сайта.
"""
import asyncio
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import disnake
from disnake.ext import commands

from utils import sound_store as store
from utils.checks import is_admin
from utils.embeds import clip, note_embed, panel_embed
from utils.ffmpeg_path import ffmpeg_exe

PREFIX = "sb:"
STOP_ID = PREFIX + "__stop"
SYNC_ID = PREFIX + "__sync"
PANEL_FILE = Path("soundboard_panels.json")

USER_COOLDOWN = 3          # секунд между нажатиями одного человека
IDLE_LEAVE = 60            # секунд тишины, после которых бот выходит (если зашёл сам)
REPAINT_GAP = 5.0          # как часто можно перекраивать панель в канале
LABEL = 18                 # знаков подписи в кнопке: дальше Discord режет слово
ROWS, PER_ROW = 5, 5       # жёсткий потолок Discord: 5 рядов по 5 кнопок на сообщение
TOTAL = ROWS * PER_ROW     # всего кнопок в одном сообщении
MAX_SOUNDS = store.MAX_SOUNDS      # хранилище и панель должны ждать один лимит
ACCENT = "sound_color"


def buttons(items: List[dict], playing: str = "") -> List[disnake.ui.Button]:
    out = []
    for it in items[:MAX_SOUNDS]:
        title = it["title"][:LABEL].rstrip(" -_")
        label = f"{it['emoji']} {title}".strip()
        style = disnake.ButtonStyle.primary if it["name"] == playing else disnake.ButtonStyle.secondary
        out.append(disnake.ui.Button(label=label[:80], custom_id=PREFIX + it["name"], style=style))
    return out


def controls(playing: str = "") -> List[disnake.ui.Button]:
    stop = disnake.ButtonStyle.danger if playing else disnake.ButtonStyle.secondary
    return [disnake.ui.Button(label="⏹ Стоп", custom_id=STOP_ID, style=stop),
            disnake.ui.Button(label="🔄 Обновить", custom_id=SYNC_ID, style=disnake.ButtonStyle.secondary)]


def components(items: List[dict], playing: str = "") -> List[disnake.ui.ActionRow]:
    """Кнопки рядами по пять — столбцами, а не кашей.

    Лишние звуки отрезаются, а не уходят шестым рядом: Discord отвечает 400 на
    сообщение больше чем с 25 кнопками, и тогда панель не выкладывается вовсе.
    """
    bl = buttons(items, playing)[:TOTAL - 2] + controls(playing)
    rows = [bl[i:i + PER_ROW] for i in range(0, len(bl), PER_ROW)]
    return [disnake.ui.ActionRow(*r) for r in rows[:ROWS] if r]


def size_of(item: dict) -> str:
    kb = item.get("bytes") or 0
    return f"{max(1, round(kb / 1024))} КБ"


def panel_embed_of(items: List[dict], now: str = "",
                   guild: Optional[disnake.Guild] = None) -> disnake.Embed:
    """Карточка панели: что звучит сейчас, и чем жать."""
    lines = []
    for it in items[:MAX_SOUNDS]:
        mark = " ◂" if now and it["title"] in now else ""
        lines.append(f"{it['emoji']}  **{clip(it['title'], 24)}**  `.{it['ext']}` · {size_of(it)}{mark}")
    free = max(0, MAX_SOUNDS - len(items))
    sounds = "\n".join(lines) if lines else "пока пусто — добавь через `/sound_add` или на сайте"
    sounds += f"\n`{len(items)} из {MAX_SOUNDS}, свободно {free}`"
    return panel_embed(
        "🔊  САУНДБОРД",
        [("ИГРАЕТ", now or "тишина — зайди в голосовой канал и нажми кнопку"),
         ("ЗВУКИ", sounds)],
        color_key=ACCENT, guild=guild,
        intro="Кнопка играет звук в том канале, где сидишь ты. «Стоп» глушит только мои звуки, "
              "музыку не трогает. Подписи и порядок правятся на сайте в разделе «Саундборд».")


def audio_source(path: Path) -> disnake.FFmpegPCMAudio:
    return disnake.FFmpegPCMAudio(str(path), executable=ffmpeg_exe() or "ffmpeg")


class Soundboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_click: Dict[int, float] = {}
        self.locks: Dict[int, asyncio.Lock] = {}
        self.now_playing: Dict[int, object] = {}   # guild_id -> токен нашего звука
        self.now: Dict[int, str] = {}              # guild_id -> строка «ИГРАЕТ»
        self.now_name: Dict[int, str] = {}         # guild_id -> имя играющего звука
        self.joined: set = set()                   # серверы, где в войс зашли мы сами
        self.idle: Dict[int, asyncio.Task] = {}
        self.paint: Dict[int, float] = {}          # guild_id -> когда перекраивали панель
        self.panels: Dict[str, dict] = self._load()

    # ---- где лежат панели ----
    def _load(self) -> dict:
        try:
            data = json.loads(PANEL_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save(self) -> None:
        try:
            tmp = PANEL_FILE.with_name(PANEL_FILE.name + ".tmp")
            tmp.write_text(json.dumps(self.panels, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(PANEL_FILE)
        except OSError as e:
            print(f"⚠️ Саундборд: адреса панелей не сохранились ({e.__class__.__name__})", flush=True)

    def remember(self, guild_id: int, channel_id: int, message_id: int) -> None:
        self.panels[str(guild_id)] = {"channel": channel_id, "message": message_id}
        self._save()

    def forget(self, guild_id: int) -> Optional[int]:
        row = self.panels.pop(str(guild_id), None)
        self._save()
        return int(row["message"]) if row and row.get("message") else None

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

    # ---- живая панель ----
    def mine_of(self, gid: int) -> Tuple[str, str]:
        """Строка «ИГРАЕТ» и имя кнопки, которую надо подсветить."""
        return self.now.get(gid, ""), self.now_name.get(gid, "")

    def panel_payload(self, gid: Optional[int] = None) -> tuple:
        items = store.items()
        now, playing = self.mine_of(gid or 0)
        return panel_embed_of(items, now), components(items, playing)

    async def sync_panels(self, guild: Optional[int] = None) -> int:
        """Подтягивает выложенные панели к текущему списку звуков.

        Вызывается после любой правки — из команд бота и из веб-панели. Иначе
        человек добавит звук на сайте, а кнопка в Discord появится только после
        /sounds_panel, и это выглядит как «сайт ничего не сохранил».
        """
        keys = [str(guild)] if guild else list(self.panels)
        done = 0
        for key in list(keys):
            row = self.panels.get(key) or {}
            try:
                chan_id, msg_id = int(row.get("channel") or 0), int(row.get("message") or 0)
            except (TypeError, ValueError):
                chan_id = msg_id = 0
            if not chan_id or not msg_id:
                self.panels.pop(key, None)
                continue
            channel = self.bot.get_channel(chan_id)
            if channel is None:
                continue                      # канал не в кэше: не забываем адрес, дойдёт позже
            gid = int(key) if key.isdigit() else 0
            emb, comps = self.panel_payload(gid)
            try:
                msg = await channel.fetch_message(msg_id)
                await msg.edit(embed=emb, components=comps)
                done += 1
            except disnake.NotFound:
                self.forget(int(key) if key.isdigit() else 0)
            except Exception as e:
                print(f"⚠️ Саундборд: панель не обновилась: {str(e)[:90]}", flush=True)
        self._save()
        return done

    def schedule_repaint(self, gid: int) -> None:
        """Перекрашивает панель, но не чаще раза в REPAINT_GAP: иначе упрёмся в лимит правок."""
        if str(gid) not in self.panels:
            return
        now = time.time()
        if now - self.paint.get(gid, 0) < REPAINT_GAP:
            return
        self.paint[gid] = now
        try:
            asyncio.get_running_loop().create_task(self.sync_panels(gid))
        except RuntimeError:
            pass

    # ---- нажатие кнопки ----
    @commands.Cog.listener()
    async def on_button_click(self, inter: disnake.MessageInteraction):
        cid = getattr(inter.component, "custom_id", "") or ""
        if not cid.startswith(PREFIX):
            return
        if not inter.guild or not isinstance(inter.author, disnake.Member):
            await inter.response.send_message(embed=note_embed("Жми кнопку на сервере, а не в личке."),
                                              ephemeral=True)
            return
        if cid == SYNC_ID:
            emb, comps = self.panel_payload(inter.guild.id)
            count = len(store.items())
            await inter.response.edit_message(embed=emb, components=comps)
            await inter.followup.send(embed=note_embed(f"Свежий список: {count} звуков."), ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        text, kind = await self.press(inter, cid)
        await inter.edit_original_response(embed=note_embed(text, kind))

    async def press(self, inter, cid: str) -> Tuple[str, Optional[str]]:
        """Играет звук из голосового канала нажавшего. Возвращает (текст, вид карточки)."""
        guild, user = inter.guild, inter.author
        now = time.time()
        if now - self.last_click.get(user.id, 0) < USER_COOLDOWN:
            return "Не так быстро — одна кнопка в 3 секунды.", "warning"

        if cid == STOP_ID:
            vc = guild.voice_client
            if vc and vc.is_playing() and self.now_playing.get(guild.id) is not None:
                vc.stop()
                # снимаем токен сразу: after идёт из голосового потока с задержкой, и следующий
                # «Стоп» успел бы посчитать чужую музыку нашей и глушить её.
                self.now_playing.pop(guild.id, None)
                self.now.pop(guild.id, None)
                self.now_name.pop(guild.id, None)
                self.schedule_repaint(guild.id)
                return "Остановил.", "success"
            return "Сейчас мой звук не играет — музыку я не глушу.", None

        self.last_click[user.id] = now
        if not user.voice or not user.voice.channel:
            return "Сначала зайди в голосовой канал.", "warning"
        channel = user.voice.channel
        if not ffmpeg_exe():
            return "На хостинге нет ffmpeg, поэтому звук воспроизвести нельзя.", "warning"
        name = cid[len(PREFIX):]
        card = next((i for i in store.items() if i["name"] == name), None)
        if card is None:
            return "Такого звука уже нет — руководство могло его убрать.", "warning"
        path = store.path_of(name)
        if path is None:
            return "Файл звука пропал с диска. Добавь его заново.", "warning"

        async with self._lock(guild.id):
            vc = guild.voice_client
            ours = self.now_playing.get(guild.id) is not None
            try:
                if vc is None:
                    vc = await channel.connect()
                    self.joined.add(guild.id)
                elif vc.channel.id != channel.id:
                    if vc.is_playing():
                        return (f"Я играю в канале «{vc.channel.name}» — не переезжаю на полуслове. "
                                "Подожди или останови меня кнопкой «Стоп»."), "warning"
                    await vc.move_to(channel)
                if vc.is_playing():
                    if not ours:
                        return ("Сейчас играет музыку — звук поверх неё не положить. "
                                "Скинь трек или подожди."), "warning"
                    vc.stop()
            except Exception as e:
                return f"Не смог зайти в канал: {str(e)[:120]}", "error"

            old = self.idle.pop(guild.id, None)
            if old:
                old.cancel()
            token = object()
            self.now_playing[guild.id] = token
            loop = asyncio.get_running_loop()
            title, emoji = clip(card["title"], 24), card["emoji"]
            self.now[guild.id] = f"{emoji}  **{title}**  ·  по заказу {user.display_name}"
            self.now_name[guild.id] = name

            def _after(_err):
                def _done():
                    if self.now_playing.get(guild.id) is token:
                        self.now_playing.pop(guild.id, None)
                        self.now.pop(guild.id, None)
                        self.now_name.pop(guild.id, None)
                    self._schedule_leave(guild)
                    loop.call_soon_threadsafe(lambda: self.schedule_repaint(guild.id))
                loop.call_soon_threadsafe(_done)

            try:
                vc.play(audio_source(path), after=_after)
            except Exception as e:
                self.now_playing.pop(guild.id, None)
                self.now.pop(guild.id, None)
                self.now_name.pop(guild.id, None)
                return f"Не получилось играть: {str(e)[:120]}", "error"
        self.schedule_repaint(guild.id)
        return f"{emoji}  **{title}**  ·  сыграл {user.display_name}", "success"

    # ---- управление звуками ----
    async def _deny(self, inter) -> bool:
        if not is_admin(inter):
            await inter.response.send_message(embed=note_embed("Нет доступа: это команды руководства.",
                                                                "warning"), ephemeral=True)
            return True
        return False

    @commands.slash_command(name="sounds_panel",
                            description="Выложить в канал панель со звуками (для руководства)")
    async def sounds_panel(self, inter: disnake.ApplicationCommandInteraction):
        if await self._deny(inter):
            return
        items = store.items()
        if not items:
            await inter.response.send_message(
                embed=note_embed("Звуков пока нет. Добавь через /sound_add или на сайте, "
                                 "а потом выложи панель.", "warning"), ephemeral=True)
            return
        old = self.forget(inter.guild.id)
        if old:
            try:
                msg = await inter.channel.fetch_message(old)
                await msg.edit(embed=note_embed("Эта панель переехала: свежая лежит ниже."),
                              components=[])
            except Exception:
                pass
        emb, comps = panel_embed_of(items), components(items)
        msg = await inter.channel.send(embed=emb, components=comps)
        self.remember(inter.guild.id, inter.channel.id, msg.id)
        await inter.response.send_message(
            embed=note_embed(f"Панель выложена: {len(items)} звуков. Она обновляется сама, "
                             "когда звуки правят на сайте.", "success"), ephemeral=True)

    @commands.slash_command(name="sound_add", description="Добавить звук в саундборд (для руководства)")
    async def sound_add(self, inter: disnake.ApplicationCommandInteraction,
                        name: str = commands.Param(description="Имя звука: буквы, цифры, - и _ (до 20)"),
                        file: disnake.Attachment = commands.Param(description="mp3, wav, ogg или m4a"),
                        title: Optional[str] = commands.Param(default=None, max_length=24,
                                                              description="Как подписать кнопку") or None,
                        emoji: Optional[str] = commands.Param(default=None,
                                                              description="Знак на кнопке") or None):
        if await self._deny(inter):
            return
        await inter.response.defer(ephemeral=True)
        ext = Path(file.filename or "").suffix.lower()
        ok, err, card = store.save_bytes(name, await file.read(), ext)
        if ok:
            store.set_meta(card.get("name", ""), title=title or "", emoji=emoji or "")
            await self.sync_panels(inter.guild.id)
        await inter.edit_original_response(embed=note_embed(
            f"Звук **{card.get('title') or name}** добавлен. Кнопка появилась на панели." if ok
            else err, "success" if ok else "warning"))

    @commands.slash_command(name="sound_url", description="Добавить звук по ссылке (для руководства)")
    async def sound_url(self, inter: disnake.ApplicationCommandInteraction,
                        name: str = commands.Param(description="Имя звука: буквы, цифры, - и _ (до 20)"),
                        url: str = commands.Param(description="Ссылка на mp3 или на страницу звука"),
                        title: Optional[str] = commands.Param(default=None, max_length=24,
                                                              description="Как подписать кнопку") or None):
        if await self._deny(inter):
            return
        await inter.response.defer(ephemeral=True)
        ok, err, card = await store.add_from_url(name, url)
        if ok:
            store.set_meta(card.get("name", ""), title=title or "")
            await self.sync_panels(inter.guild.id)
        await inter.edit_original_response(embed=note_embed(
            f"Звук **{card.get('title') or name}** скачан и добавлен." if ok else err,
            "success" if ok else "warning"))

    @commands.slash_command(name="sound_edit",
                            description="Поменять подпись, знак или цвет звука (для руководства)")
    async def sound_edit(self, inter: disnake.ApplicationCommandInteraction,
                         name: str = commands.Param(description="Какой звук поменять"),
                         title: Optional[str] = commands.Param(default=None, max_length=24,
                                                               description="Текст на кнопке") or None,
                         emoji: Optional[str] = commands.Param(default=None,
                                                               description="Знак на кнопке") or None,
                         color: Optional[str] = commands.Param(default=None,
                                                               description="Цвет на сайте, например #00b2ff") or None):
        if await self._deny(inter):
            return
        if store.path_of(name) is None:
            await inter.response.send_message(embed=note_embed("Такого звука нет.", "warning"),
                                              ephemeral=True)
            return
        ok, err = store.set_meta(name, title=title or "", emoji=emoji or "", color=color or "")
        if not ok:
            await inter.response.send_message(embed=note_embed(err, "warning"), ephemeral=True)
            return
        await self.sync_panels(inter.guild.id)
        await inter.response.send_message(embed=note_embed("Готово: панель перерисовалась.",
                                                           "success"), ephemeral=True)

    @commands.slash_command(name="sound_remove", description="Убрать звук из саундборда (для руководства)")
    async def sound_remove(self, inter: disnake.ApplicationCommandInteraction,
                           name: str = commands.Param(description="Какой звук убрать")):
        if await self._deny(inter):
            return
        ok, err = store.remove(name)
        if not ok:
            await inter.response.send_message(embed=note_embed(err, "warning"), ephemeral=True)
            return
        await self.sync_panels(inter.guild.id)
        await inter.response.send_message(embed=note_embed(
            f"Звук **{name}** убран с панели. Звуки из готового пака можно вернуть на сайте.",
            "success"), ephemeral=True)

    @sound_edit.autocomplete("name")
    @sound_remove.autocomplete("name")
    async def _names(self, inter: disnake.ApplicationCommandInteraction, current: str):
        cur = (current or "").lower()
        return [n for n in store.names() if cur in n][:25]

    @sound_add.autocomplete("emoji")
    @sound_edit.autocomplete("emoji")
    async def _emoji_ac(self, inter: disnake.ApplicationCommandInteraction, current: str):
        cur = (current or "").strip()
        return [e for e in store.EMOJIS if not cur or cur in e][:25]


def setup(bot: commands.Bot):
    bot.add_cog(Soundboard(bot))
