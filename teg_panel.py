import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

import aiosqlite
import disnake
from disnake.ext import commands

from database import DB_PATH

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SETTINGS_PATH = os.path.join(BASE_DIR, "teg_settings.json")

DM_REPEATS = 2          # сколько раз отправлять сбор в личку каждому
DM_DELAY = 1.0          # пауза между личными сообщениями (защита от лимитов Discord)

STATUS = {
    "yes": ("✅", "Буду"),
    "late": ("⏰", "Опоздаю"),
    "no": ("❌", "Не смогу"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS teg_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER, author_id INTEGER,
    title TEXT, call_time TEXT, info TEXT,
    voice_id INTEGER DEFAULT 0,
    channel_id INTEGER DEFAULT 0, channel_msg_id INTEGER DEFAULT 0,
    recipients INTEGER DEFAULT 0,
    created_at INTEGER
);
CREATE TABLE IF NOT EXISTS teg_dms (
    message_id INTEGER PRIMARY KEY, call_id INTEGER, user_id INTEGER
);
CREATE TABLE IF NOT EXISTS teg_responses (
    call_id INTEGER, user_id INTEGER, status TEXT, updated_at INTEGER,
    PRIMARY KEY (call_id, user_id)
);
"""
_schema_ready = False
_reminding: set = set()


# ───────────────────────── хранилище ─────────────────────────

def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_settings() -> dict:
    data = {"access_user_ids": [], "access_role_ids": []}
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data.update(json.load(f))
    except Exception:
        pass
    return data


def save_settings(data: dict):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def embed_color(cfg: dict, key: str = "embed_color") -> int:
    try:
        return int(str(cfg.get(key, "0x990000")), 16)
    except Exception:
        return 0x990000


@asynccontextmanager
async def tdb():
    global _schema_ready
    if not _schema_ready:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.executescript(SCHEMA)
            await db.commit()
        _schema_ready = True
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db


# ───────────────────────── права доступа ─────────────────────────

def is_admin(member: disnake.Member) -> bool:
    cfg = load_config()
    if member.id in cfg.get("admin_user_ids", []):
        return True
    if member.guild and member.guild.owner_id == member.id:
        return True
    admin_roles = {r for r in cfg.get("admin_role_ids", []) if r}
    return any(r.id in admin_roles for r in member.roles)


def has_access(member: disnake.Member) -> bool:
    if is_admin(member):
        return True
    s = load_settings()
    if member.id in s["access_user_ids"]:
        return True
    roles = set(s["access_role_ids"])
    return any(r.id in roles for r in member.roles)


async def deny(inter: disnake.Interaction):
    await inter.response.send_message("❌ У вас нет доступа к этой панели.", ephemeral=True)


def ids_of(values) -> List[int]:
    return [int(getattr(v, "id", v)) for v in values]


# ───────────────────────── карточка сбора ─────────────────────────

def voice_url(guild_id: int, voice_id: int) -> Optional[str]:
    return f"https://discord.com/channels/{guild_id}/{voice_id}" if voice_id else None


def counts_text(c: dict) -> str:
    total = c["yes"] + c["late"] + c["no"] + c["wait"]
    filled = round(10 * (c["yes"] + c["late"]) / total) if total else 0
    bar = "▰" * filled + "▱" * (10 - filled)
    return (f"`{bar}`\n✅ **{c['yes']}** • ⏰ **{c['late']}** • ❌ **{c['no']}** • ⏳ ждём **{c['wait']}**")


def call_embed(call: dict, cfg: dict, guild: Optional[disnake.Guild],
               status: Optional[str] = None, counts: Optional[dict] = None) -> disnake.Embed:
    lines = []
    if call["call_time"]:
        lines.append(f"⏰ **Время сбора:** `{call['call_time']}`")
    if call["info"]:
        lines.append(f"📍 **Требования:** {call['info']}")
    if call["voice_id"]:
        lines.append(f"🔊 **Голосовой канал:** <#{call['voice_id']}>")
    lines.append(f"\n👑 **Объявил:** <@{call['author_id']}>")
    embed = disnake.Embed(title=f"⚔️ СБОР: {call['title']}", description="\n".join(lines),
                          color=embed_color(cfg))
    if guild:
        embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    if status in STATUS:
        emoji, label = STATUS[status]
        embed.add_field(name="Ваш ответ", value=f"{emoji} **{label}** — можно изменить кнопками ниже", inline=False)
    elif status is None and counts is None:
        embed.add_field(name="Ваш ответ", value="⏳ Ответьте кнопками ниже", inline=False)
    if counts is not None:
        embed.add_field(name="📊 Явка (обновляется)", value=counts_text(counts), inline=False)
    footer = cfg.get("texts", {}).get("footer_text", "").format(bot_name=cfg.get("bot_name", "Hallez FAMQ"))
    if footer:
        embed.set_footer(text=footer)
    return embed


async def get_call(db, call_id: int) -> Optional[dict]:
    async with db.execute("SELECT * FROM teg_calls WHERE id = ?", (call_id,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def get_counts(db, call_id: int, recipients: int) -> dict:
    d = {"yes": 0, "late": 0, "no": 0}
    async with db.execute("SELECT status, COUNT(*) AS c FROM teg_responses WHERE call_id = ? GROUP BY status",
                          (call_id,)) as cur:
        for r in await cur.fetchall():
            if r["status"] in d:
                d[r["status"]] = r["c"]
    d["wait"] = max(recipients - sum(d.values()), 0)
    return d


async def update_scoreboard(bot: commands.Bot, call_id: int):
    """Обновляет сообщение в канале (если сбор дублировали в канал) — живая явка."""
    async with tdb() as db:
        call = await get_call(db, call_id)
        if not call or not call["channel_msg_id"]:
            return
        counts = await get_counts(db, call_id, call["recipients"])
    ch = bot.get_channel(call["channel_id"])
    if ch is None:
        return
    try:
        await ch.get_partial_message(call["channel_msg_id"]).edit(
            embed=call_embed(call, load_config(), bot.get_guild(call["guild_id"]), counts=counts))
    except Exception:
        pass


async def send_call_dm(member: disnake.Member, call: dict, cfg: dict, guild: disnake.Guild, content: str) -> bool:
    try:
        msg = await member.send(content=content, embed=call_embed(call, cfg, guild),
                                view=CallResponseView(voice_url(guild.id, call["voice_id"])))
    except (disnake.Forbidden, disnake.HTTPException):
        return False
    async with tdb() as db:
        await db.execute("INSERT OR REPLACE INTO teg_dms (message_id, call_id, user_id) VALUES (?, ?, ?)",
                         (msg.id, call["id"], member.id))
        await db.commit()
    return True


# ───────────────────────── кнопки в личке ─────────────────────────

async def handle_response(inter: disnake.MessageInteraction, status: str):
    async with tdb() as db:
        async with db.execute("SELECT call_id FROM teg_dms WHERE message_id = ?", (inter.message.id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return await inter.response.send_message("⚠️ Этот сбор уже недоступен.", ephemeral=True)
        call_id = row["call_id"]
        await db.execute(
            "INSERT INTO teg_responses (call_id, user_id, status, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(call_id, user_id) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
            (call_id, inter.author.id, status, int(time.time())))
        await db.commit()
        call = await get_call(db, call_id)
        async with db.execute("SELECT message_id FROM teg_dms WHERE call_id = ? AND user_id = ?",
                              (call_id, inter.author.id)) as cur:
            mids = [r["message_id"] for r in await cur.fetchall()]

    await inter.response.defer()
    embed = call_embed(call, load_config(), inter.bot.get_guild(call["guild_id"]), status=status)
    for mid in mids:                       # обновляем ВСЕ личные сообщения этого сбора у человека
        try:
            await inter.channel.get_partial_message(mid).edit(embed=embed)
        except Exception:
            pass
    await update_scoreboard(inter.bot, call_id)


class CallResponseView(disnake.ui.View):
    """Кнопки под сообщением в личке. Постоянные: работают и после перезапуска бота."""

    def __init__(self, voice: Optional[str] = None):
        super().__init__(timeout=None)
        if voice:
            self.add_item(disnake.ui.Button(label="Зайти в голосовой", emoji="🔊", url=voice, row=1))

    @disnake.ui.button(label="Буду", emoji="✅", style=disnake.ButtonStyle.success,
                       custom_id="teg_call:yes", row=0)
    async def yes(self, button, inter: disnake.MessageInteraction):
        await handle_response(inter, "yes")

    @disnake.ui.button(label="Опоздаю", emoji="⏰", style=disnake.ButtonStyle.primary,
                       custom_id="teg_call:late", row=0)
    async def late(self, button, inter: disnake.MessageInteraction):
        await handle_response(inter, "late")

    @disnake.ui.button(label="Не смогу", emoji="❌", style=disnake.ButtonStyle.danger,
                       custom_id="teg_call:no", row=0)
    async def no(self, button, inter: disnake.MessageInteraction):
        await handle_response(inter, "no")


# ───────────────────────── отчёт о явке ─────────────────────────

def _mentions(ids: List[int]) -> str:
    if not ids:
        return "—"
    out, used = [], 0
    for i, uid in enumerate(ids):
        piece = f"<@{uid}>"
        if used + len(piece) + 1 > 950:
            out.append(f"… и ещё {len(ids) - i}")
            break
        out.append(piece)
        used += len(piece) + 1
    return " ".join(out)


async def build_report(call_id: int) -> Optional[disnake.Embed]:
    async with tdb() as db:
        call = await get_call(db, call_id)
        if not call:
            return None
        async with db.execute("SELECT user_id, status FROM teg_responses WHERE call_id = ?", (call_id,)) as cur:
            resp = await cur.fetchall()
        async with db.execute("SELECT DISTINCT user_id FROM teg_dms WHERE call_id = ?", (call_id,)) as cur:
            dms = [r["user_id"] for r in await cur.fetchall()]
    groups: Dict[str, List[int]] = {k: [] for k in STATUS}
    answered = set()
    for r in resp:
        if r["status"] in groups:
            groups[r["status"]].append(r["user_id"])
            answered.add(r["user_id"])
    silent = [u for u in dms if u not in answered]

    e = disnake.Embed(title=f"📊 Явка • {call['title']} (#{call['id']})",
                      description=f"Получили в личку: **{len(dms)}** • ответили: **{len(answered)}**",
                      color=embed_color(load_config()))
    for key, (emoji, label) in STATUS.items():
        e.add_field(name=f"{emoji} {label} ({len(groups[key])})", value=_mentions(groups[key]), inline=False)
    e.add_field(name=f"⏳ Не ответили ({len(silent)})", value=_mentions(silent), inline=False)
    e.set_footer(text=f"Сбор создан: {time.strftime('%d.%m %H:%M', time.localtime(call['created_at']))}")
    return e


class ReportView(disnake.ui.View):
    def __init__(self, call_id: int):
        super().__init__(timeout=900)
        self.call_id = call_id

    @disnake.ui.button(label="Обновить", emoji="🔄", style=disnake.ButtonStyle.secondary)
    async def refresh_btn(self, button, inter: disnake.MessageInteraction):
        if not has_access(inter.author):
            return await deny(inter)
        embed = await build_report(self.call_id)
        await inter.response.edit_message(embed=embed, view=self)

    @disnake.ui.button(label="Напомнить не ответившим", emoji="🔔", style=disnake.ButtonStyle.primary)
    async def remind_btn(self, button, inter: disnake.MessageInteraction):
        if not has_access(inter.author):
            return await deny(inter)
        if self.call_id in _reminding:
            return await inter.response.send_message("⏳ Напоминание уже рассылается.", ephemeral=True)
        async with tdb() as db:
            call = await get_call(db, self.call_id)
            async with db.execute(
                "SELECT DISTINCT user_id FROM teg_dms WHERE call_id = ? AND user_id NOT IN "
                "(SELECT user_id FROM teg_responses WHERE call_id = ?)", (self.call_id, self.call_id)) as cur:
                silent = [r["user_id"] for r in await cur.fetchall()]
        if not silent:
            return await inter.response.send_message("✅ Все уже ответили.", ephemeral=True)

        _reminding.add(self.call_id)
        await inter.response.defer(ephemeral=True)
        try:
            cfg, sent, failed = load_config(), 0, 0
            for uid in silent:
                m = inter.guild.get_member(uid)
                if m and await send_call_dm(m, call, cfg, inter.guild,
                                            f"🔔 {m.mention}, ты ещё не ответил на сбор! Нажми кнопку ниже."):
                    sent += 1
                else:
                    failed += 1
                await asyncio.sleep(DM_DELAY)
            await inter.followup.send(f"🔔 Напоминание отправлено: **{sent}**" +
                                      (f" • не доставлено: **{failed}**" if failed else ""), ephemeral=True)
        finally:
            _reminding.discard(self.call_id)


# ───────────────────────── управление доступом ─────────────────────────

def access_embed() -> disnake.Embed:
    cfg, s = load_config(), load_settings()
    e = disnake.Embed(
        title="⚙️ Доступ к Teg panel",
        description="Выберите человека или роль: доступ **выдастся**, а если он уже есть — **заберётся**.\n"
                    "Админы бота (из config.json) имеют доступ всегда.",
        color=embed_color(cfg),
    )
    e.add_field(name="👤 Пользователи", value="\n".join(f"<@{u}>" for u in s["access_user_ids"]) or "—")
    e.add_field(name="🎖️ Роли", value="\n".join(f"<@&{r}>" for r in s["access_role_ids"]) or "—")
    return e


class AccessView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @disnake.ui.user_select(placeholder="👤 Выдать / забрать доступ у людей", min_values=1, max_values=10, row=0)
    async def users(self, select: disnake.ui.UserSelect, inter: disnake.MessageInteraction):
        if not is_admin(inter.author):
            return await deny(inter)
        s = load_settings()
        for uid in ids_of(select.values):
            if uid in s["access_user_ids"]:
                s["access_user_ids"].remove(uid)
            else:
                s["access_user_ids"].append(uid)
        save_settings(s)
        await inter.response.edit_message(embed=access_embed(), view=AccessView())

    @disnake.ui.role_select(placeholder="🎖️ Выдать / забрать доступ у ролей", min_values=1, max_values=10, row=1)
    async def roles(self, select: disnake.ui.RoleSelect, inter: disnake.MessageInteraction):
        if not is_admin(inter.author):
            return await deny(inter)
        s = load_settings()
        for rid in ids_of(select.values):
            if rid in s["access_role_ids"]:
                s["access_role_ids"].remove(rid)
            else:
                s["access_role_ids"].append(rid)
        save_settings(s)
        await inter.response.edit_message(embed=access_embed(), view=AccessView())


# ───────────────────────── форма текста ─────────────────────────

class TextModal(disnake.ui.Modal):
    def __init__(self, panel: "TegPanelView"):
        super().__init__(
            title="Текст сбора",
            custom_id="teg_panel:text",
            components=[
                disnake.ui.TextInput(label="Название / тип сбора", custom_id="title",
                                     value=panel.title or None, max_length=80,
                                     placeholder="Например: Capt, Семейный МП, Дом"),
                disnake.ui.TextInput(label="Время сбора", custom_id="time", required=False,
                                     value=panel.time or None, max_length=60,
                                     placeholder="Например: 20:00 МСК"),
                disnake.ui.TextInput(label="Описание / требования", custom_id="info", required=False,
                                     style=disnake.TextInputStyle.paragraph,
                                     value=panel.info or None, max_length=800),
            ],
        )
        self.panel = panel

    async def callback(self, inter: disnake.ModalInteraction):
        if not has_access(inter.author):
            return await deny(inter)
        self.panel.title = inter.text_values["title"].strip()
        self.panel.time = inter.text_values["time"].strip()
        self.panel.info = inter.text_values["info"].strip()
        self.panel.status = ""
        await inter.response.edit_message(embed=self.panel.render(), view=self.panel)


# ───────────────────────── главная панель ─────────────────────────

class TegPanelView(disnake.ui.View):
    """Личная панель: кого тегать → текст → голосовой/канал → «Отправить».
    Каждому уходит личное сообщение DM_REPEATS раза с упоминанием, кнопками ответа и входом в войс."""

    def __init__(self, bot: commands.Bot, author: disnake.Member, default_channel_id: int):
        super().__init__(timeout=900)
        self.bot = bot
        self.author = author
        self.guild = author.guild
        self.everyone = False
        self.role_ids: List[int] = []
        self.user_ids: List[int] = []
        self.channel_on = False
        self.channel_id = default_channel_id
        self.voice_id = 0
        for v in load_config().get("voice_channels", []):      # по умолчанию — первый голосовой из config.json
            if v.get("id") and self.guild.get_channel(int(v["id"])):
                self.voice_id = int(v["id"])
                break
        self.title = ""
        self.time = ""
        self.info = ""
        self.status = ""
        self.sending = False
        if not is_admin(author):
            self.remove_item(self.access_btn)

    # ---- получатели ----
    def recipients(self) -> List[disnake.Member]:
        found: Dict[int, disnake.Member] = {}
        if self.everyone:
            for m in self.guild.members:
                if not m.bot:
                    found[m.id] = m
        for rid in self.role_ids:
            role = self.guild.get_role(rid)
            if role:
                for m in role.members:
                    if not m.bot:
                        found[m.id] = m
        for uid in self.user_ids:
            m = self.guild.get_member(uid)
            if m and not m.bot:
                found[m.id] = m
        return list(found.values())

    # ---- превью ----
    def targets_text(self) -> str:
        parts = []
        if self.everyone:
            parts.append("📢 Все участники сервера")
        parts += [f"<@&{r}>" for r in self.role_ids]
        parts += [f"<@{u}>" for u in self.user_ids]
        return "\n".join(parts) if parts else "*не выбрано*"

    def message_preview(self) -> str:
        if not self.title:
            return "*текст не настроен — нажмите «Настроить текст»*"
        lines = [f"**⚔️ СБОР: {self.title}**"]
        if self.time:
            lines.append(f"⏰ **Время сбора:** `{self.time}`")
        if self.info:
            lines.append(f"📍 **Требования:** {self.info}")
        if self.voice_id:
            lines.append(f"🔊 **Голосовой канал:** <#{self.voice_id}>")
        lines.append("`[✅ Буду] [⏰ Опоздаю] [❌ Не смогу]`" + (" `[🔊 Зайти в голосовой]`" if self.voice_id else ""))
        return "\n".join(lines)

    def render(self) -> disnake.Embed:
        cfg = load_config()
        e = disnake.Embed(
            title="🏷️ Teg panel — сбор семьи",
            description="1️⃣ Выберите, **кого тегнуть**\n"
                        "2️⃣ Нажмите **«Настроить текст»**\n"
                        "3️⃣ Выберите **голосовой канал** (кнопка входа появится в сообщении)\n"
                        f"4️⃣ Жмите **«Отправить»** — каждому уйдёт сообщение в **личку {DM_REPEATS} раза** с упоминанием "
                        "и кнопками ответа\n"
                        "➕ По желанию включите **«В канал»** — сбор появится и в канале с живой явкой",
            color=embed_color(cfg),
        )
        count = len(self.recipients())
        recipients = f"\n\n📩 **Получателей в ЛС:** {count}" if count else ""
        if count > 40:
            recipients += f"\n⏳ Отправка займёт ~{int(count * DM_DELAY * DM_REPEATS)} сек."
        e.add_field(name="🎯 Кого тегаем", value=self.targets_text() + recipients, inline=True)
        channel_text = f"<#{self.channel_id}>" if self.channel_on and self.channel_id else "*выкл — только личка*"
        e.add_field(name="📍 В канал", value=channel_text, inline=True)
        e.add_field(name="🔊 Голосовой", value=f"<#{self.voice_id}>" if self.voice_id else "*не выбран*", inline=True)
        e.add_field(name="📝 Превью сообщения", value=self.message_preview(), inline=False)
        if self.status:
            e.set_footer(text=self.status)
        return e

    async def refresh_panel(self, inter: disnake.MessageInteraction):
        await inter.response.edit_message(embed=self.render(), view=self)

    def _sync_buttons(self):
        self.everyone_btn.style = disnake.ButtonStyle.success if self.everyone else disnake.ButtonStyle.danger
        self.everyone_btn.label = "Всех ✔" if self.everyone else "Всех"
        self.channel_btn.style = disnake.ButtonStyle.success if self.channel_on else disnake.ButtonStyle.secondary
        self.channel_btn.label = "В канал ✔" if self.channel_on else "В канал"

    # ---- кнопки (ряд 0) ----
    @disnake.ui.button(label="Всех", emoji="📢", style=disnake.ButtonStyle.danger, row=0)
    async def everyone_btn(self, button, inter: disnake.MessageInteraction):
        if not has_access(inter.author):
            return await deny(inter)
        self.everyone = not self.everyone
        self.status = ""
        self._sync_buttons()
        await self.refresh_panel(inter)

    @disnake.ui.button(label="Настроить текст", emoji="✏️", style=disnake.ButtonStyle.primary, row=0)
    async def text_btn(self, button, inter: disnake.MessageInteraction):
        if not has_access(inter.author):
            return await deny(inter)
        await inter.response.send_modal(TextModal(self))

    @disnake.ui.button(label="В канал", emoji="📍", style=disnake.ButtonStyle.secondary, row=0)
    async def channel_btn(self, button, inter: disnake.MessageInteraction):
        if not has_access(inter.author):
            return await deny(inter)
        self.channel_on = not self.channel_on
        self.status = ""
        self._sync_buttons()
        await self.refresh_panel(inter)

    @disnake.ui.button(label="Отправить", emoji="🚀", style=disnake.ButtonStyle.success, row=0)
    async def send_btn(self, button, inter: disnake.MessageInteraction):
        if not has_access(inter.author):
            return await deny(inter)
        if self.sending:
            return await inter.response.send_message("⏳ Отправка уже идёт, подождите.", ephemeral=True)
        if not (self.everyone or self.role_ids or self.user_ids):
            return await inter.response.send_message("❌ Сначала выберите, кого тегнуть.", ephemeral=True)
        if not self.title:
            return await inter.response.send_message("❌ Сначала настройте текст сбора.", ephemeral=True)

        channel = None
        if self.channel_on:
            channel = self.guild.get_channel(self.channel_id)
            if channel is None:
                return await inter.response.send_message("❌ Канал не найден.", ephemeral=True)
            perms = channel.permissions_for(self.guild.me)
            if not perms.send_messages:
                return await inter.response.send_message(
                    f"❌ У бота нет прав писать в {channel.mention}.", ephemeral=True)
            if self.everyone and not perms.mention_everyone:
                return await inter.response.send_message(
                    f"❌ У бота нет права **Упоминать @everyone** в {channel.mention}.", ephemeral=True)

        self.sending = True
        await inter.response.defer()
        self.status = "⏳ Отправляю сбор..."
        await inter.edit_original_response(embed=self.render(), view=self)

        try:
            if self.everyone and not self.guild.chunked:
                try:
                    await self.guild.chunk()
                except Exception:
                    pass
            members = self.recipients()
            cfg = load_config()

            # создаём запись сбора (нужна для кнопок ответа и отчёта)
            async with tdb() as db:
                cur = await db.execute(
                    "INSERT INTO teg_calls (guild_id, author_id, title, call_time, info, voice_id, channel_id, "
                    "recipients, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (self.guild.id, self.author.id, self.title, self.time, self.info, self.voice_id,
                     channel.id if channel else 0, len(members), int(time.time())))
                await db.commit()
                call_id = cur.lastrowid
                call = await get_call(db, call_id)

            # 1) по желанию — в канал (с живой явкой)
            channel_ok = None
            if channel is not None:
                mention_parts = (["@everyone"] if self.everyone else []) \
                    + [f"<@&{r}>" for r in self.role_ids] + [f"<@{u}>" for u in self.user_ids]
                allowed = disnake.AllowedMentions(
                    everyone=self.everyone,
                    roles=[disnake.Object(r) for r in self.role_ids] or False,
                    users=[disnake.Object(u) for u in self.user_ids] or False,
                )
                ch_view = None
                if self.voice_id:
                    ch_view = disnake.ui.View(timeout=None)
                    ch_view.add_item(disnake.ui.Button(label="Зайти в голосовой", emoji="🔊",
                                                       url=voice_url(self.guild.id, self.voice_id)))
                try:
                    counts0 = {"yes": 0, "late": 0, "no": 0, "wait": len(members)}
                    msg = await channel.send(content=" ".join(mention_parts),
                                             embed=call_embed(call, cfg, self.guild, counts=counts0),
                                             view=ch_view, allowed_mentions=allowed)
                    async with tdb() as db:
                        await db.execute("UPDATE teg_calls SET channel_msg_id = ? WHERE id = ?", (msg.id, call_id))
                        await db.commit()
                    channel_ok = True
                except Exception:
                    channel_ok = False

            # 2) личные сообщения — DM_REPEATS раза каждому
            delivered, failed = 0, []
            for m in members:
                if await self.dm_member(m, call, cfg):
                    delivered += 1
                else:
                    failed.append(m)
                await asyncio.sleep(DM_DELAY)

            summary = f"✅ ЛС доставлено: {delivered}/{len(members)}"
            if failed:
                summary += f" • закрыта личка: {len(failed)}"
            if channel_ok is True:
                summary += f" • канал: #{channel.name}"
            elif channel_ok is False:
                summary += " • ⚠️ в канал отправить не удалось"
            self.status = summary
            await inter.edit_original_response(embed=self.render(), view=self)

            # отчёт о явке (можно обновлять и напоминать не ответившим)
            report = await build_report(call_id)
            if report:
                await inter.followup.send(embed=report, view=ReportView(call_id), ephemeral=True)

            if failed:
                names = "\n".join(f"{m.mention} (`{m.display_name}`)" for m in failed[:25])
                more = f"\n… и ещё {len(failed) - 25}" if len(failed) > 25 else ""
                await inter.followup.send(
                    f"⚠️ Не удалось написать в личку ({len(failed)}):\n{names}{more}\n"
                    "У них закрыты личные сообщения от участников сервера.",
                    ephemeral=True, allowed_mentions=disnake.AllowedMentions.none())

            await self.log(cfg, call_id, members, delivered, len(failed), channel)
        finally:
            self.sending = False

    @disnake.ui.button(label="Сбросить", emoji="🧹", style=disnake.ButtonStyle.secondary, row=0)
    async def reset_btn(self, button, inter: disnake.MessageInteraction):
        if not has_access(inter.author):
            return await deny(inter)
        if self.sending:
            return await inter.response.send_message("⏳ Отправка ещё идёт.", ephemeral=True)
        self.everyone, self.channel_on = False, False
        self.role_ids, self.user_ids = [], []
        self.title = self.time = self.info = self.status = ""
        self._sync_buttons()
        await self.refresh_panel(inter)

    # ---- личные сообщения ----
    async def dm_member(self, member: disnake.Member, call: dict, cfg: dict) -> bool:
        for n in range(1, DM_REPEATS + 1):
            if n == 1:
                content = f"🔔 {member.mention}, вас вызывают на сбор!"
            else:
                content = f"🔔 {member.mention}, напоминание: сбор ({n}/{DM_REPEATS})!"
            if not await send_call_dm(member, call, cfg, self.guild, content):
                return False
            if n < DM_REPEATS:
                await asyncio.sleep(DM_DELAY)
        return True

    async def log(self, cfg: dict, call_id: int, members, delivered: int, failed: int, channel):
        log_id = cfg.get("channels", {}).get("logs_channel_id", 0)
        log_ch = self.guild.get_channel(log_id) if log_id else None
        if not log_ch:
            return
        text = (f"{self.author.mention} объявил сбор «{self.title}» (#{call_id})\n"
                f"🎯 Кого: {self.targets_text()}\n"
                f"📩 ЛС: {delivered}/{len(members)} (закрыта личка: {failed})")
        if channel is not None:
            text += f"\n📍 Также в канал: {channel.mention}"
        try:
            await log_ch.send(embed=disnake.Embed(title="🏷️ Teg panel", description=text,
                                                  color=embed_color(cfg)),
                              allowed_mentions=disnake.AllowedMentions.none())
        except Exception:
            pass

    # ---- выпадающие списки ----
    @disnake.ui.role_select(placeholder="🎖️ Выбрать роли для тега (до 10)", min_values=0, max_values=10, row=1)
    async def roles_select(self, select: disnake.ui.RoleSelect, inter: disnake.MessageInteraction):
        if not has_access(inter.author):
            return await deny(inter)
        self.role_ids = ids_of(select.values)
        self.status = ""
        await self.refresh_panel(inter)

    @disnake.ui.user_select(placeholder="👤 Выбрать людей для тега (до 10)", min_values=0, max_values=10, row=2)
    async def users_select(self, select: disnake.ui.UserSelect, inter: disnake.MessageInteraction):
        if not has_access(inter.author):
            return await deny(inter)
        self.user_ids = ids_of(select.values)
        self.status = ""
        await self.refresh_panel(inter)

    @disnake.ui.channel_select(
        placeholder="🔊 Голосовой канал и/или 📍 текстовый для дубля (можно оба)",
        channel_types=[disnake.ChannelType.text, disnake.ChannelType.news,
                       disnake.ChannelType.voice, disnake.ChannelType.stage_voice],
        min_values=1, max_values=2, row=3)
    async def channel_select(self, select: disnake.ui.ChannelSelect, inter: disnake.MessageInteraction):
        if not has_access(inter.author):
            return await deny(inter)
        for cid in ids_of(select.values):
            ch = self.guild.get_channel(cid)
            if ch is None:
                continue
            if ch.type in (disnake.ChannelType.voice, disnake.ChannelType.stage_voice):
                self.voice_id = cid
            else:
                self.channel_id = cid
                self.channel_on = True          # выбрали текстовый канал — значит хотим дублировать
        self.status = ""
        self._sync_buttons()
        await self.refresh_panel(inter)

    # ---- доступ (ряд 4, только админам) ----
    @disnake.ui.button(label="Доступ", emoji="⚙️", style=disnake.ButtonStyle.secondary, row=4)
    async def access_btn(self, button, inter: disnake.MessageInteraction):
        if not is_admin(inter.author):
            return await deny(inter)
        await inter.response.send_message(embed=access_embed(), view=AccessView(), ephemeral=True)

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:
        if inter.author.id != self.author.id:
            await inter.response.send_message("❌ Это чужая панель. Откройте свою: `/teg`", ephemeral=True)
            return False
        return True


async def open_panel(bot: commands.Bot, inter: disnake.Interaction):
    if not has_access(inter.author):
        return await deny(inter)
    view = TegPanelView(bot, inter.author, inter.channel.id)
    await inter.response.send_message(embed=view.render(), view=view, ephemeral=True)


class TegLaunchView(disnake.ui.View):
    """Постоянная кнопка в канале: открывает панель тем, у кого есть доступ."""

    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @disnake.ui.button(label="Открыть Teg panel", emoji="🏷️", style=disnake.ButtonStyle.primary,
                       custom_id="teg_panel:open")
    async def open_btn(self, button, inter: disnake.MessageInteraction):
        await open_panel(self.bot, inter)


def launch_embed() -> disnake.Embed:
    return disnake.Embed(
        title="🏷️ Teg panel — сбор семьи",
        description="Нажмите кнопку ниже, чтобы объявить сбор: выберите **всех**, **роль** или **людей**, "
                    "настройте текст и отправьте — каждому придёт личное сообщение с упоминанием, "
                    "кнопками ответа и входом в голосовой.\n\n🔒 Доступно только тем, кому выдан доступ.",
        color=embed_color(load_config()),
    )


# ───────────────────────── cog ─────────────────────────

class TegPanel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(TegLaunchView(bot))
        bot.add_view(CallResponseView())      # кнопки в личке работают и после перезапуска

    @commands.slash_command(name="teg", description="Открыть Teg panel (тег на сборы)")
    async def teg(self, inter: disnake.ApplicationCommandInteraction):
        await open_panel(self.bot, inter)

    @commands.slash_command(name="teg_report", description="Кто ответил на сбор: буду / опоздаю / не смогу / молчат")
    async def teg_report(
        self,
        inter: disnake.ApplicationCommandInteraction,
        number: int = commands.Param(default=None, name="номер", description="Номер сбора (по умолчанию — последний)"),
    ):
        if not has_access(inter.author):
            return await deny(inter)
        async with tdb() as db:
            if number is not None:
                call = await get_call(db, number)
            elif is_admin(inter.author):
                async with db.execute("SELECT * FROM teg_calls ORDER BY id DESC LIMIT 1") as cur:
                    row = await cur.fetchone()
                call = dict(row) if row else None
            else:
                async with db.execute("SELECT * FROM teg_calls WHERE author_id = ? ORDER BY id DESC LIMIT 1",
                                      (inter.author.id,)) as cur:
                    row = await cur.fetchone()
                call = dict(row) if row else None
        if not call:
            return await inter.response.send_message("❌ Сбор не найден.", ephemeral=True)
        if call["author_id"] != inter.author.id and not is_admin(inter.author):
            return await inter.response.send_message("❌ Это сбор другого человека.", ephemeral=True)
        await inter.response.send_message(embed=await build_report(call["id"]),
                                          view=ReportView(call["id"]), ephemeral=True)

    @commands.slash_command(name="teg_post", description="Опубликовать кнопку Teg panel в этом канале (админы)")
    async def teg_post(self, inter: disnake.ApplicationCommandInteraction):
        if not is_admin(inter.author):
            return await deny(inter)
        await inter.channel.send(embed=launch_embed(), view=TegLaunchView(self.bot))
        await inter.response.send_message("✅ Кнопка опубликована.", ephemeral=True)

    @commands.slash_command(name="teg_access", description="Кто может пользоваться Teg panel (админы)")
    async def teg_access(self, inter: disnake.ApplicationCommandInteraction):
        if not is_admin(inter.author):
            return await deny(inter)
        await inter.response.send_message(embed=access_embed(), view=AccessView(), ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(TegPanel(bot))
