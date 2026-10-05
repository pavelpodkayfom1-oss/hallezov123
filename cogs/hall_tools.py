"""
Hall FAMQ: дополнительные функции бота.

 • Приветствие и правила новичку при входе на сервер
 • Автонапоминание о повышении (срок в днях/часах задаётся в панели /hall)
 • /уволить + автоудаление из базы при выходе с сервера (архив в таблице fired_members)
 • Заявки на отпуск / неактив (/отпуск), решение руководства кнопками
 • Автообновляемый список состава руководства по ролям (канал и роли задаются в /hall)

Настройки хранятся в файле hall_settings.json рядом с main.py.
"""
import os
import re
import sys
import json
import time
from datetime import datetime, timezone

import aiosqlite
import disnake
from disnake.ext import commands, tasks

from database import DB_PATH
from utils.checks import load_config

SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hall_settings.json"
)

DEFAULT_RULES = (
    "Привет, {mention}! 🌟\n\n"
    "Рады видеть тебя на сервере **{server}** — это дом семьи **Hall FAMQ** на Majestic RP.\n\n"
    "📜 **Правила сервера:**\n"
    "• Уважай всех участников, без оскорблений и токсичности\n"
    "• Никакой рекламы и спама\n"
    "• Слушай руководство семьи и соблюдай субординацию\n"
    "• Читай закреплённые сообщения в каналах\n\n"
    "📝 **Как вступить в семью:**\n"
    "1️⃣ Перейди в канал {recruit_channel}\n"
    "2️⃣ Нажми кнопку **«Подать заявку в Hall FAMQ»** и заполни анкету\n"
    "3️⃣ Дождись рекрутера: он вызовет тебя на обзвон в голосовой канал\n\n"
    "✨ Желаем отличного настроения и удачи! Мы рады, что ты с нами 💎"
)

DEFAULTS = {
    "welcome_enabled": True,
    "welcome_channel_id": 0,
    "rules_text": DEFAULT_RULES,
    "remind_enabled": True,
    "remind_hours": 336,          # 14 дней
    "remind_mode": "both",        # dm / channel / both
    "remind_channel_id": 0,
    "leave_channel_id": 0,
    "roster_channel_id": 0,
    "roster_message_id": 0,
    "roster_roles": [],           # [{"role_id": int, "title": str}]
    "afk_enabled": True,
    "afk_default_hours": 3,
    "afk_reminder_hours": 2,
    "afk_panel_channel_id": 0,
    "afk_panel_message_id": 0,
    "afk_panel_text": (
        "Если вам нужно ненадолго отойти — нажмите **«Взять АФК»**.\n"
        "Статус снимется автоматически через заданное время, либо вы можете "
        "нажать **«Прекратить»**, чтобы вернуться раньше."
    ),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS fired_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    nick TEXT,
    static_id TEXT,
    rank INTEGER,
    joined_at TIMESTAMP,
    kind TEXT NOT NULL,
    reason TEXT,
    admin_id INTEGER,
    fired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS leaves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    until_ts INTEGER NOT NULL,
    reason TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    message_id INTEGER,
    reviewer_id INTEGER,
    verdict_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS promo_reminders (
    user_id INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, rank)
);
CREATE TABLE IF NOT EXISTS afk_sessions (
    user_id INTEGER PRIMARY KEY,
    started_at INTEGER NOT NULL,
    duration_hours INTEGER NOT NULL,
    reminder_sent INTEGER DEFAULT 0
);
"""

_schema_ready = False


# ───────────────────────── Вспомогательное ─────────────────────────

async def run(sql, params=(), fetch="none"):
    """Выполняет SQL. fetch: 'all' | 'one' | 'none' (вернёт lastrowid)."""
    global _schema_ready
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if not _schema_ready:
            await db.executescript(SCHEMA)
            _schema_ready = True
        cur = await db.execute(sql, params)
        if fetch == "all":
            result = [dict(r) for r in await cur.fetchall()]
        elif fetch == "one":
            r = await cur.fetchone()
            result = dict(r) if r else None
        else:
            result = cur.lastrowid
        await db.commit()
        return result


def get_settings():
    data = {}
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    merged = json.loads(json.dumps(DEFAULTS))
    merged.update(data)
    return merged


def save_settings(s):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def set_setting(key, value):
    s = get_settings()
    s[key] = value
    save_settings(s)


def _color(key="embed_color"):
    raw = load_config().get(key, "0x990000")
    try:
        return int(str(raw), 16)
    except ValueError:
        return 0x990000


def make_embed(title, desc="", color_key="embed_color"):
    cfg = load_config()
    e = disnake.Embed(title=title, description=desc, color=_color(color_key))
    footer = cfg.get("texts", {}).get("footer_text", "{bot_name}")
    e.set_footer(text=footer.replace("{bot_name}", cfg.get("bot_name", "Hall FAMQ")))
    return e


def is_admin(member) -> bool:
    cfg = load_config()
    if member.id in cfg.get("admin_user_ids", []):
        return True
    role_ids = {r.id for r in getattr(member, "roles", [])}
    if role_ids & set(cfg.get("admin_role_ids", [])):
        return True
    perms = getattr(member, "guild_permissions", None)
    return bool(perms and perms.administrator)


def can_manage(member) -> bool:
    if is_admin(member):
        return True
    cfg = load_config()
    role_ids = {r.id for r in getattr(member, "roles", [])}
    return bool(role_ids & set(cfg.get("recruiter_role_ids", [])))


def guild_ok(guild) -> bool:
    gid = os.getenv("GUILD_ID", "0")
    if gid.isdigit() and int(gid) > 0:
        return guild.id == int(gid)
    return True


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def human_hours(h: int) -> str:
    if h >= 24 and h % 24 == 0:
        return f"{h // 24} дн."
    return f"{h} ч."


def parse_duration(text: str):
    """'14 д', '14d', '36 ч', '36h' -> часы. None если не удалось."""
    m = re.fullmatch(r"\s*(\d+)\s*([a-zа-яё]*)\s*", text.lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    if n <= 0:
        return None
    if unit.startswith(("д", "d")):
        return n * 24
    if unit.startswith(("ч", "h")):
        return n
    return None


def rank_name(rank: int) -> str:
    return load_config().get("ranks", {}).get(str(rank), {}).get("name", str(rank))


async def dm_user(bot, user_id: int, embed: disnake.Embed) -> bool:
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        await user.send(embed=embed)
        return True
    except Exception:
        return False


async def send_log(bot, embed: disnake.Embed):
    cid = load_config().get("channels", {}).get("logs_channel_id", 0)
    ch = bot.get_channel(cid) if cid else None
    if ch:
        try:
            await ch.send(embed=embed)
        except disnake.HTTPException:
            pass


async def archive_member(user_id: int, kind: str, reason: str, admin_id=None):
    """Переносит запись из family_members в fired_members. Возвращает старую запись или None."""
    row = await run("SELECT * FROM family_members WHERE user_id = ?", (user_id,), "one")
    if not row:
        return None
    await run(
        "INSERT INTO fired_members (user_id, nick, static_id, rank, joined_at, kind, reason, admin_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, row["nick"], row["static_id"], row["rank"], row["joined_at"], kind, reason, admin_id),
    )
    await run("DELETE FROM family_members WHERE user_id = ?", (user_id,))
    await run("DELETE FROM promo_reminders WHERE user_id = ?", (user_id,))
    await run(
        "UPDATE leaves SET status = 'cancelled' WHERE user_id = ? AND status IN ('pending', 'approved')",
        (user_id,),
    )
    return row


# ───────────────────────── Отпуска ─────────────────────────

LEAVE_STATUS = {
    "pending": ("🟡 Ожидает решения", "warning_color"),
    "approved": ("🟢 Одобрено", "success_color"),
    "rejected": ("🔴 Отклонено", "error_color"),
    "ended": ("⚪ Завершён", "embed_color"),
    "cancelled": ("⚪ Отменён", "embed_color"),
}


async def leave_embed(row) -> disnake.Embed:
    member = await run("SELECT nick FROM family_members WHERE user_id = ?", (row["user_id"],), "one")
    if not member:
        member = await run(
            "SELECT nick FROM fired_members WHERE user_id = ? ORDER BY id DESC LIMIT 1", (row["user_id"],), "one"
        )
    nick = member["nick"] if member else "—"
    text, color_key = LEAVE_STATUS.get(row["status"], LEAVE_STATUS["pending"])
    e = make_embed(
        f"🌴 Заявка на отпуск #{row['id']}",
        f"Участник <@{row['user_id']}> (`{nick}`) просит освободить его от активности.",
        color_key,
    )
    e.add_field(name="📅 Вернётся", value=f"<t:{row['until_ts']}:D> (<t:{row['until_ts']}:R>)", inline=True)
    e.add_field(name="📌 Статус", value=text, inline=True)
    e.add_field(name="📝 Причина", value=row["reason"], inline=False)
    if row.get("reviewer_id"):
        e.add_field(name="👮 Решил", value=f"<@{row['reviewer_id']}>", inline=True)
    if row["status"] == "rejected" and row.get("verdict_reason"):
        e.add_field(name="💬 Причина отказа", value=row["verdict_reason"], inline=False)
    return e


class LeaveRejectModal(disnake.ui.Modal):
    def __init__(self, leave_id: int, message: disnake.Message):
        self.leave_id = leave_id
        self.message = message
        super().__init__(
            title="Отклонение заявки на отпуск",
            custom_id=f"hall:leave:reject:{leave_id}",
            components=[
                disnake.ui.TextInput(
                    label="Причина отказа",
                    custom_id="reason",
                    style=disnake.TextInputStyle.paragraph,
                    max_length=300,
                    placeholder="Например: сейчас сбор, отпуск невозможен...",
                )
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        reason = inter.text_values["reason"]
        await run(
            "UPDATE leaves SET status = 'rejected', reviewer_id = ?, verdict_reason = ? WHERE id = ? AND status = 'pending'",
            (inter.author.id, reason, self.leave_id),
        )
        row = await run("SELECT * FROM leaves WHERE id = ?", (self.leave_id,), "one")
        try:
            await self.message.edit(embed=await leave_embed(row), view=None)
        except disnake.HTTPException:
            pass
        dm = make_embed(
            "🌸 Заявка на отпуск отклонена",
            f"Здравствуйте! Ваша заявка на отпуск до <t:{row['until_ts']}:D> была отклонена руководителем {inter.author.mention}.\n\n"
            f"📝 **Причина:** {reason}\n\n"
            "Если остались вопросы, напишите руководству семьи, мы всегда на связи 🤍",
            "error_color",
        )
        await dm_user(inter.bot, row["user_id"], dm)
        await inter.response.send_message("✅ Заявка отклонена.", ephemeral=True)


class LeaveReviewView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _get_pending(self, inter):
        if not can_manage(inter.author):
            await inter.response.send_message("❌ У вас нет прав рассматривать заявки.", ephemeral=True)
            return None
        row = await run("SELECT * FROM leaves WHERE message_id = ?", (inter.message.id,), "one")
        if not row or row["status"] != "pending":
            await inter.response.send_message("⚠️ Эта заявка уже обработана.", ephemeral=True)
            return None
        return row

    @disnake.ui.button(label="Одобрить", emoji="✅", style=disnake.ButtonStyle.success, custom_id="hall:leave:ok")
    async def approve(self, button, inter: disnake.MessageInteraction):
        row = await self._get_pending(inter)
        if not row:
            return
        await run("UPDATE leaves SET status = 'approved', reviewer_id = ? WHERE id = ?", (inter.author.id, row["id"]))
        row = await run("SELECT * FROM leaves WHERE id = ?", (row["id"],), "one")
        await inter.response.edit_message(embed=await leave_embed(row), view=None)
        dm = make_embed(
            "🌴 Отпуск одобрен!",
            f"Здравствуйте! Руководитель {inter.author.mention} одобрил вашу заявку.\n\n"
            f"📅 **Вы свободны от активности до:** <t:{row['until_ts']}:D>\n\n"
            "Хорошего отдыха, набирайтесь сил и возвращайтесь с отличным настроением! ☀️\n"
            "Мы будем ждать вас в строю 💎",
            "success_color",
        )
        await dm_user(inter.bot, row["user_id"], dm)

    @disnake.ui.button(label="Отклонить", emoji="❌", style=disnake.ButtonStyle.danger, custom_id="hall:leave:no")
    async def reject(self, button, inter: disnake.MessageInteraction):
        row = await self._get_pending(inter)
        if not row:
            return
        await inter.response.send_modal(LeaveRejectModal(row["id"], inter.message))


# ───────────────────────── AFK-панель ─────────────────────────

def afk_panel_embed() -> disnake.Embed:
    s = get_settings()
    e = make_embed("🌴 АФК-статус", s["afk_panel_text"])
    e.add_field(name="⏱️ Время по умолчанию", value=human_hours(s["afk_default_hours"]), inline=True)
    e.add_field(name="🔔 Напоминание через", value=human_hours(s["afk_reminder_hours"]), inline=True)
    return e


class AfkPanelView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="Взять АФК", emoji="🌴", style=disnake.ButtonStyle.success, custom_id="hall:afk:start")
    async def start(self, button, inter: disnake.MessageInteraction):
        s = get_settings()
        if not s["afk_enabled"]:
            await inter.response.send_message("❌ Функция АФК сейчас выключена администрацией.", ephemeral=True)
            return

        existing = await run("SELECT * FROM afk_sessions WHERE user_id = ?", (inter.author.id,), "one")
        if existing:
            until_ts = existing["started_at"] + existing["duration_hours"] * 3600
            await inter.response.send_message(
                f"⚠️ Вы уже в статусе АФК. Истекает <t:{until_ts}:R>.\nНажмите «Прекратить», если уже вернулись.",
                ephemeral=True,
            )
            return

        now = int(time.time())
        hours = s["afk_default_hours"]
        await run(
            "INSERT INTO afk_sessions (user_id, started_at, duration_hours, reminder_sent) VALUES (?, ?, ?, 0)",
            (inter.author.id, now, hours),
        )
        until_ts = now + hours * 3600
        await inter.response.send_message(
            f"✅ Вы взяли АФК на **{human_hours(hours)}**.\n"
            f"Истекает <t:{until_ts}:R> (<t:{until_ts}:f>).\n"
            "Когда вернётесь — нажмите «Прекратить» на этой же панели.",
            ephemeral=True,
        )
        log = make_embed(
            "🌴 АФК начат",
            f"👤 **Участник:** {inter.author.mention}\n⏱️ **Длительность:** {human_hours(hours)}\n"
            f"📅 **Истекает:** <t:{until_ts}:f>",
            "warning_color",
        )
        await send_log(inter.bot, log)

    @disnake.ui.button(label="Прекратить", emoji="🔴", style=disnake.ButtonStyle.danger, custom_id="hall:afk:stop")
    async def stop(self, button, inter: disnake.MessageInteraction):
        existing = await run("SELECT * FROM afk_sessions WHERE user_id = ?", (inter.author.id,), "one")
        if not existing:
            await inter.response.send_message("ℹ️ У вас сейчас нет активного статуса АФК.", ephemeral=True)
            return
        await run("DELETE FROM afk_sessions WHERE user_id = ?", (inter.author.id,))
        spent = int(time.time()) - existing["started_at"]
        spent_h, spent_m = spent // 3600, (spent % 3600) // 60
        await inter.response.send_message(
            f"✅ АФК завершён. Вы отсутствовали **{spent_h}ч {spent_m}м**. С возвращением! ☀️", ephemeral=True
        )


class AfkHoursModal(disnake.ui.Modal):
    def __init__(self):
        s = get_settings()
        super().__init__(
            title="Настройка времени АФК",
            custom_id="hall:modal:afk_hours",
            components=[
                disnake.ui.TextInput(
                    label="Длительность по умолчанию (часы)",
                    custom_id="default_hours",
                    value=str(s["afk_default_hours"]),
                    max_length=4,
                ),
                disnake.ui.TextInput(
                    label="Напоминание через (часы)",
                    custom_id="reminder_hours",
                    value=str(s["afk_reminder_hours"]),
                    max_length=4,
                ),
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        try:
            default_h = int(inter.text_values["default_hours"].strip())
            reminder_h = int(inter.text_values["reminder_hours"].strip())
        except ValueError:
            await inter.response.send_message("❌ Оба поля должны быть целыми числами (часы).", ephemeral=True)
            return
        if default_h <= 0 or reminder_h <= 0:
            await inter.response.send_message("❌ Значения должны быть больше нуля.", ephemeral=True)
            return
        if reminder_h >= default_h:
            await inter.response.send_message(
                "❌ Напоминание должно приходить раньше, чем истекает АФК (меньше, чем длительность).", ephemeral=True
            )
            return
        s = get_settings()
        s["afk_default_hours"] = default_h
        s["afk_reminder_hours"] = reminder_h
        save_settings(s)
        await inter.response.send_message(
            f"✅ Сохранено: по умолчанию **{human_hours(default_h)}**, напоминание через **{human_hours(reminder_h)}**.",
            ephemeral=True,
        )


class AfkTextModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(
            title="Текст АФК-панели",
            custom_id="hall:modal:afk_text",
            components=[
                disnake.ui.TextInput(
                    label="Текст над кнопками",
                    custom_id="text",
                    style=disnake.TextInputStyle.paragraph,
                    value=get_settings()["afk_panel_text"][:4000],
                    max_length=1000,
                )
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        set_setting("afk_panel_text", inter.text_values["text"])
        await inter.response.send_message("✅ Текст АФК-панели сохранён.", ephemeral=True)


# ───────────────────────── Панель /hall ─────────────────────────

def panel_embed(guild) -> disnake.Embed:
    s = get_settings()

    def ch(i):
        return f"<#{i}>" if i else "не задан"

    mode = {"dm": "в личные сообщения", "channel": "в канал", "both": "в личку и в канал"}[s["remind_mode"]]
    roles = " → ".join(f"<@&{r['role_id']}>" for r in s["roster_roles"]) or "не добавлены"
    e = make_embed("⚙️ Панель настроек Hall FAMQ", "Настройки приветствия, напоминаний, отпусков и списка состава.")
    e.add_field(
        name="👋 Приветствие",
        value=f"Статус: {'🟢 включено' if s['welcome_enabled'] else '🔴 выключено'}\n"
              f"Резервный канал (если ЛС закрыты): {ch(s['welcome_channel_id'])}",
        inline=False,
    )
    e.add_field(
        name="⏰ Напоминание о повышении (2 → 3 ранг)",
        value=f"Статус: {'🟢 включено' if s['remind_enabled'] else '🔴 выключено'}\n"
              f"Срок после вступления: **{human_hours(s['remind_hours'])}**\n"
              f"Куда слать: **{mode}**\n"
              f"Канал для руководства: {ch(s['remind_channel_id'])}",
        inline=False,
    )
    e.add_field(name="🌴 Заявки на отпуск", value=f"Канал заявок: {ch(s['leave_channel_id'])}", inline=False)
    e.add_field(
        name="👥 Состав руководства",
        value=f"Канал: {ch(s['roster_channel_id'])}\nРоли по порядку (сверху вниз): {roles}",
        inline=False,
    )
    e.add_field(
        name="🌴 АФК-панель",
        value=f"Статус: {'🟢 включено' if s['afk_enabled'] else '🔴 выключено'}\n"
              f"По умолчанию: **{human_hours(s['afk_default_hours'])}** • "
              f"Напоминание через: **{human_hours(s['afk_reminder_hours'])}**\n"
              f"Канал панели: {ch(s['afk_panel_channel_id'])}",
        inline=False,
    )
    return e


class ChannelPicker(disnake.ui.View):
    def __init__(self, key: str):
        super().__init__(timeout=120)
        self.key = key

    @disnake.ui.channel_select(
        placeholder="Выберите текстовый канал...",
        channel_types=[disnake.ChannelType.text],
        min_values=1,
        max_values=1,
    )
    async def pick(self, select, inter: disnake.MessageInteraction):
        channel = select.values[0]
        set_setting(self.key, channel.id)
        if self.key == "roster_channel_id":
            set_setting("roster_message_id", 0)
        await inter.response.edit_message(content=f"✅ Сохранено: <#{channel.id}>. Нажмите «Обновить» в панели.", view=None)
        if self.key == "roster_channel_id":
            cog = inter.bot.get_cog("HallTools")
            if cog:
                await cog.update_roster(inter.guild, force=True)


class RoleAddPicker(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @disnake.ui.role_select(placeholder="Выберите роль для списка...", min_values=1, max_values=1)
    async def pick(self, select, inter: disnake.MessageInteraction):
        role = select.values[0]
        s = get_settings()
        if any(r["role_id"] == role.id for r in s["roster_roles"]):
            await inter.response.edit_message(content="⚠️ Эта роль уже в списке.", view=None)
            return
        if len(s["roster_roles"]) >= 20:
            await inter.response.edit_message(content="⚠️ Максимум 20 ролей в списке.", view=None)
            return
        s["roster_roles"].append({"role_id": role.id, "title": role.name})
        save_settings(s)
        await inter.response.edit_message(
            content=f"✅ Роль <@&{role.id}> добавлена в конец списка. Порядок = порядок добавления.", view=None
        )
        cog = inter.bot.get_cog("HallTools")
        if cog:
            await cog.update_roster(inter.guild, force=True)


class RoleRemoveSelect(disnake.ui.StringSelect):
    def __init__(self, options):
        super().__init__(placeholder="Какую роль убрать из списка?", options=options, min_values=1, max_values=1)

    async def callback(self, inter: disnake.MessageInteraction):
        rid = int(self.values[0])
        s = get_settings()
        s["roster_roles"] = [r for r in s["roster_roles"] if r["role_id"] != rid]
        save_settings(s)
        await inter.response.edit_message(content="✅ Роль убрана из списка.", view=None)
        cog = inter.bot.get_cog("HallTools")
        if cog:
            await cog.update_roster(inter.guild, force=True)


class RulesModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(
            title="Текст приветствия и правил",
            custom_id="hall:modal:rules",
            components=[
                disnake.ui.TextInput(
                    label="Текст (можно {mention} {server} {recruit_channel})",
                    custom_id="text",
                    style=disnake.TextInputStyle.paragraph,
                    value=get_settings()["rules_text"][:4000],
                    max_length=4000,
                )
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        set_setting("rules_text", inter.text_values["text"])
        await inter.response.send_message("✅ Текст приветствия сохранён.", ephemeral=True)


class TimeModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(
            title="Срок напоминания о повышении",
            custom_id="hall:modal:time",
            components=[
                disnake.ui.TextInput(
                    label="Через сколько после вступления напомнить",
                    custom_id="time",
                    placeholder="Например: 14 д  или  36 ч",
                    value=human_hours(get_settings()["remind_hours"]).replace("дн.", "д").replace("ч.", "ч"),
                    max_length=10,
                )
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        hours = parse_duration(inter.text_values["time"])
        if hours is None:
            await inter.response.send_message("❌ Не понял формат. Пишите так: `14 д` или `36 ч`.", ephemeral=True)
            return
        set_setting("remind_hours", hours)
        await inter.response.send_message(f"✅ Напоминание через **{human_hours(hours)}** после вступления.", ephemeral=True)


class HallPanel(disnake.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=600)
        self.author_id = author_id

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:
        if inter.author.id != self.author_id or not is_admin(inter.author):
            await inter.response.send_message("❌ Это не ваша панель.", ephemeral=True)
            return False
        return True

    async def _refresh(self, inter):
        await inter.response.edit_message(embed=panel_embed(inter.guild), view=self)

    # Приветствие
    @disnake.ui.button(label="Приветствие вкл/выкл", emoji="👋", style=disnake.ButtonStyle.primary, row=0)
    async def welcome_toggle(self, button, inter):
        set_setting("welcome_enabled", not get_settings()["welcome_enabled"])
        await self._refresh(inter)

    @disnake.ui.button(label="Текст правил", emoji="📜", style=disnake.ButtonStyle.secondary, row=0)
    async def rules_text(self, button, inter):
        await inter.response.send_modal(RulesModal())

    @disnake.ui.button(label="Резервный канал", emoji="📢", style=disnake.ButtonStyle.secondary, row=0)
    async def welcome_channel(self, button, inter):
        await inter.response.send_message("Выберите канал для приветствий:", view=ChannelPicker("welcome_channel_id"), ephemeral=True)

    # Напоминания
    @disnake.ui.button(label="Напоминания вкл/выкл", emoji="⏰", style=disnake.ButtonStyle.primary, row=1)
    async def remind_toggle(self, button, inter):
        set_setting("remind_enabled", not get_settings()["remind_enabled"])
        await self._refresh(inter)

    @disnake.ui.button(label="Срок (дни/часы)", emoji="🕒", style=disnake.ButtonStyle.secondary, row=1)
    async def remind_time(self, button, inter):
        await inter.response.send_modal(TimeModal())

    @disnake.ui.button(label="Куда слать", emoji="📨", style=disnake.ButtonStyle.secondary, row=1)
    async def remind_mode(self, button, inter):
        order = ["dm", "channel", "both"]
        cur = get_settings()["remind_mode"]
        set_setting("remind_mode", order[(order.index(cur) + 1) % 3])
        await self._refresh(inter)

    @disnake.ui.button(label="Канал напоминаний", emoji="📌", style=disnake.ButtonStyle.secondary, row=2)
    async def remind_channel(self, button, inter):
        await inter.response.send_message("Выберите канал для руководства:", view=ChannelPicker("remind_channel_id"), ephemeral=True)

    # Отпуска
    @disnake.ui.button(label="Канал заявок на отпуск", emoji="🌴", style=disnake.ButtonStyle.secondary, row=2)
    async def leave_channel(self, button, inter):
        await inter.response.send_message("Выберите канал для заявок на отпуск:", view=ChannelPicker("leave_channel_id"), ephemeral=True)

    # Состав
    @disnake.ui.button(label="Канал состава", emoji="👥", style=disnake.ButtonStyle.secondary, row=3)
    async def roster_channel(self, button, inter):
        await inter.response.send_message("Выберите канал для списка состава:", view=ChannelPicker("roster_channel_id"), ephemeral=True)

    @disnake.ui.button(label="Добавить роль", emoji="➕", style=disnake.ButtonStyle.success, row=3)
    async def roster_add(self, button, inter):
        await inter.response.send_message(
            "Выберите роль. Добавляйте сверху вниз: Owner, Dep Owner, Chief Recruit и т.д.",
            view=RoleAddPicker(), ephemeral=True,
        )

    # АФК-панель
    @disnake.ui.button(label="АФК вкл/выкл", emoji="🌴", style=disnake.ButtonStyle.primary, row=2)
    async def afk_toggle(self, button, inter):
        set_setting("afk_enabled", not get_settings()["afk_enabled"])
        await self._refresh(inter)

    @disnake.ui.button(label="Часы АФК", emoji="⏱️", style=disnake.ButtonStyle.secondary, row=2)
    async def afk_hours(self, button, inter):
        await inter.response.send_modal(AfkHoursModal())

    @disnake.ui.button(label="Канал АФК-панели", emoji="📌", style=disnake.ButtonStyle.secondary, row=2)
    async def afk_channel(self, button, inter):
        await inter.response.send_message(
            "Выберите канал, куда опубликовать АФК-панель:", view=ChannelPicker("afk_panel_channel_id"), ephemeral=True
        )

    @disnake.ui.button(label="Текст АФК", emoji="📝", style=disnake.ButtonStyle.secondary, row=4)
    async def afk_text(self, button, inter):
        await inter.response.send_modal(AfkTextModal())

    @disnake.ui.button(label="Опубликовать АФК-панель", emoji="📤", style=disnake.ButtonStyle.success, row=4)
    async def afk_publish(self, button, inter):
        s = get_settings()
        if not s["afk_panel_channel_id"]:
            await inter.response.send_message("❌ Сначала выберите канал («Канал АФК-панели»).", ephemeral=True)
            return
        channel = inter.guild.get_channel(s["afk_panel_channel_id"])
        if channel is None:
            await inter.response.send_message("❌ Канал не найден (возможно, был удалён).", ephemeral=True)
            return
        msg = await channel.send(embed=afk_panel_embed(), view=AfkPanelView())
        set_setting("afk_panel_message_id", msg.id)
        await inter.response.send_message(f"✅ АФК-панель опубликована в {channel.mention}.", ephemeral=True)

    @disnake.ui.button(label="Убрать роль", emoji="➖", style=disnake.ButtonStyle.danger, row=3)
    async def roster_remove(self, button, inter):
        roles = get_settings()["roster_roles"]
        if not roles:
            await inter.response.send_message("Список ролей пуст.", ephemeral=True)
            return
        options = [disnake.SelectOption(label=r["title"][:100], value=str(r["role_id"])) for r in roles]
        view = disnake.ui.View(timeout=120)
        view.add_item(RoleRemoveSelect(options))
        await inter.response.send_message("Какую роль убрать?", view=view, ephemeral=True)

    @disnake.ui.button(label="Обновить список сейчас", emoji="🔁", style=disnake.ButtonStyle.secondary, row=4)
    async def roster_refresh(self, button, inter):
        cog = inter.bot.get_cog("HallTools")
        if cog:
            await cog.update_roster(inter.guild, force=True)
        await self._refresh(inter)

    @disnake.ui.button(label="Обновить панель", emoji="🔄", style=disnake.ButtonStyle.secondary, row=4)
    async def refresh(self, button, inter):
        await self._refresh(inter)


# ───────────────────────── Ког ─────────────────────────

class HallTools(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._views_added = False
        self._roster_hash = None
        self.reminder_loop.start()
        self.leave_loop.start()
        self.roster_loop.start()
        self.afk_loop.start()

    def cog_unload(self):
        self.reminder_loop.cancel()
        self.leave_loop.cancel()
        self.roster_loop.cancel()
        self.afk_loop.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._views_added:
            self.bot.add_view(LeaveReviewView())
            self.bot.add_view(AfkPanelView())
            self._views_added = True

    # ── Приветствие ──
    @commands.Cog.listener()
    async def on_member_join(self, member: disnake.Member):
        if member.bot or not guild_ok(member.guild):
            return
        s = get_settings()
        if not s["welcome_enabled"]:
            return
        recruit = load_config().get("channels", {}).get("recruitment_channel_id", 0)
        text = (
            s["rules_text"]
            .replace("{mention}", member.mention)
            .replace("{server}", member.guild.name)
            .replace("{recruit_channel}", f"<#{recruit}>" if recruit else "канал набора")
        )
        embed = make_embed(f"👋 Добро пожаловать на {member.guild.name}!", text)
        try:
            await member.send(embed=embed)
            return
        except (disnake.Forbidden, disnake.HTTPException):
            pass
        ch = member.guild.get_channel(s["welcome_channel_id"]) if s["welcome_channel_id"] else None
        if ch:
            try:
                await ch.send(content=member.mention, embed=embed)
            except disnake.HTTPException:
                pass

    # ── Выход с сервера ──
    @commands.Cog.listener()
    async def on_member_remove(self, member: disnake.Member):
        if member.bot or not guild_ok(member.guild):
            return
        row = await archive_member(member.id, "left", "Покинул сервер")
        if not row:
            return
        e = make_embed(
            "🚪 Участник покинул сервер",
            f"👤 **Участник:** {member.mention} (`{row['nick']}`)\n🆔 **Статик:** `{row['static_id']}`\n"
            f"🎖️ **Ранг:** {row['rank']} ({rank_name(row['rank'])})\n"
            "📁 Запись перенесена в архив и удалена из состава.",
            "warning_color",
        )
        await send_log(self.bot, e)

    # ── /уволить ──
    @commands.slash_command(name="уволить", description="Уволить участника из семьи", dm_permission=False)
    async def fire(
        self,
        inter: disnake.ApplicationCommandInteraction,
        member: disnake.Member = commands.Param(name="участник", description="Кого уволить"),
        reason: str = commands.Param(name="причина", description="Причина увольнения", max_length=300),
    ):
        if not is_admin(inter.author):
            await inter.response.send_message("❌ Эта команда только для администрации.", ephemeral=True)
            return
        if member.bot or member.id == inter.author.id:
            await inter.response.send_message("❌ Нельзя уволить этого пользователя.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)

        cfg = load_config()
        ids = set()
        for k in ("family_role_id", "rank_1_role_id", "rank_2_role_id", "rank_3_role_id", "rank_4_role_id"):
            if cfg.get("roles", {}).get(k):
                ids.add(cfg["roles"][k])
        for r in cfg.get("ranks", {}).values():
            if r.get("role_id"):
                ids.add(r["role_id"])
        to_remove = [r for r in member.roles if r.id in ids]

        notes = []
        if to_remove:
            try:
                await member.remove_roles(*to_remove, reason=f"Увольнение: {reason}")
            except disnake.Forbidden:
                notes.append("⚠️ Не удалось снять роли: роль бота должна стоять выше ролей семьи.")
            except disnake.HTTPException as ex:
                notes.append(f"⚠️ Ошибка при снятии ролей: {ex}")
        try:
            await member.edit(nick=None, reason=f"Увольнение: {reason}")
        except disnake.Forbidden:
            notes.append("⚠️ Не удалось сбросить ник (нет прав или это владелец сервера).")
        except disnake.HTTPException as ex:
            notes.append(f"⚠️ Ошибка при сбросе ника: {ex}")

        row = await archive_member(member.id, "fired", reason, inter.author.id)
        if not row:
            notes.append("ℹ️ В базе состава этого человека не было, архив не создан.")

        dm = make_embed(
            "🤍 Вы покинули семью Hall FAMQ",
            f"Здравствуйте! Вы были уволены из семьи **Hall FAMQ**.\n\n📝 **Причина:** {reason}\n\n"
            "Спасибо за время, проведённое вместе. Желаем вам удачи, ярких побед и отличного настроения! ✨\n"
            "Двери всегда открыты: при желании вы сможете подать заявку снова 🚪",
            "error_color",
        )
        dm_ok = await dm_user(self.bot, member.id, dm)

        log = make_embed(
            "🚪 Увольнение из семьи",
            f"👤 **Участник:** {member.mention}"
            + (f" (`{row['nick']}`)" if row else "")
            + f"\n👮 **Уволил:** {inter.author.mention}\n📝 **Причина:** {reason}"
            + (f"\n🆔 **Статик:** `{row['static_id']}`\n🎖️ **Был ранг:** {row['rank']} ({rank_name(row['rank'])})" if row else ""),
            "error_color",
        )
        await send_log(self.bot, log)

        summary = f"✅ {member.mention} уволен. Роли сняты, ник сброшен, запись в архиве."
        if not dm_ok:
            notes.append("ℹ️ Личное сообщение не доставлено (закрыты ЛС).")
        await inter.edit_original_response(content="\n".join([summary] + notes))

    # ── /отпуск ──
    @commands.slash_command(name="отпуск", description="Подать заявку на отпуск / неактив", dm_permission=False)
    async def leave(
        self,
        inter: disnake.ApplicationCommandInteraction,
        until: str = commands.Param(name="до", description="Дата возвращения в формате ДД.ММ.ГГГГ"),
        reason: str = commands.Param(name="причина", description="Причина отсутствия", max_length=300),
    ):
        member_row = await run("SELECT 1 AS x FROM family_members WHERE user_id = ?", (inter.author.id,), "one")
        if not member_row:
            await inter.response.send_message("❌ Заявка на отпуск доступна только участникам семьи.", ephemeral=True)
            return
        try:
            d = datetime.strptime(until.strip().replace("/", ".").replace("-", "."), "%d.%m.%Y")
        except ValueError:
            await inter.response.send_message("❌ Неверная дата. Пишите так: `25.10.2026`.", ephemeral=True)
            return
        until_ts = int(d.replace(hour=23, minute=59, second=59, tzinfo=timezone.utc).timestamp())
        now = int(time.time())
        if until_ts <= now:
            await inter.response.send_message("❌ Дата должна быть в будущем.", ephemeral=True)
            return
        if until_ts - now > 365 * 86400:
            await inter.response.send_message("❌ Слишком далёкая дата (максимум год).", ephemeral=True)
            return
        existing = await run(
            "SELECT id FROM leaves WHERE user_id = ? AND (status = 'pending' OR (status = 'approved' AND until_ts > ?))",
            (inter.author.id, now), "one",
        )
        if existing:
            await inter.response.send_message("⚠️ У вас уже есть активная или ожидающая заявка на отпуск.", ephemeral=True)
            return

        s = get_settings()
        cfg = load_config()
        channels = cfg.get("channels", {})
        cid = s["leave_channel_id"] or channels.get("promotion_review_channel_id", 0) or channels.get("logs_channel_id", 0)
        channel = self.bot.get_channel(cid) if cid else None
        if channel is None:
            await inter.response.send_message("❌ Канал для заявок не настроен. Сообщите администрации (/hall).", ephemeral=True)
            return

        leave_id = await run(
            "INSERT INTO leaves (user_id, until_ts, reason) VALUES (?, ?, ?)", (inter.author.id, until_ts, reason)
        )
        row = await run("SELECT * FROM leaves WHERE id = ?", (leave_id,), "one")
        try:
            msg = await channel.send(embed=await leave_embed(row), view=LeaveReviewView())
        except disnake.HTTPException:
            await run("DELETE FROM leaves WHERE id = ?", (leave_id,))
            await inter.response.send_message("❌ Не удалось отправить заявку в канал руководства.", ephemeral=True)
            return
        await run("UPDATE leaves SET message_id = ? WHERE id = ?", (msg.id, leave_id))
        await inter.response.send_message(
            f"✅ Заявка #{leave_id} отправлена руководству. Ответ придёт в личные сообщения 🌴", ephemeral=True
        )

    @commands.slash_command(name="отпуск-список", description="Кто сейчас в отпуске", dm_permission=False)
    async def leave_list(self, inter: disnake.ApplicationCommandInteraction):
        if not can_manage(inter.author):
            await inter.response.send_message("❌ Нет прав.", ephemeral=True)
            return
        rows = await run(
            "SELECT * FROM leaves WHERE status = 'approved' AND until_ts > ? ORDER BY until_ts", (int(time.time()),), "all"
        )
        if not rows:
            await inter.response.send_message("🌴 Сейчас никто не в отпуске.", ephemeral=True)
            return
        lines = [f"• <@{r['user_id']}>: до <t:{r['until_ts']}:D> — {r['reason']}" for r in rows]
        e = make_embed("🌴 Сейчас в отпуске", "\n".join(lines)[:4000])
        await inter.response.send_message(embed=e, ephemeral=True)

    @commands.slash_command(name="афк-список", description="Кто сейчас в статусе АФК", dm_permission=False)
    async def afk_list(self, inter: disnake.ApplicationCommandInteraction):
        if not can_manage(inter.author):
            await inter.response.send_message("❌ Нет прав.", ephemeral=True)
            return
        rows = await run("SELECT * FROM afk_sessions ORDER BY started_at", fetch="all")
        if not rows:
            await inter.response.send_message("🌴 Сейчас никто не в АФК.", ephemeral=True)
            return
        now = int(time.time())
        lines = []
        for r in rows:
            until_ts = r["started_at"] + r["duration_hours"] * 3600
            lines.append(f"• <@{r['user_id']}>: истекает <t:{until_ts}:R>")
        e = make_embed("🌴 Сейчас в АФК", "\n".join(lines)[:4000])
        await inter.response.send_message(embed=e, ephemeral=True)

    async def check_on_leave(self, user_id: int) -> bool:
        row = await run(
            "SELECT 1 AS x FROM leaves WHERE user_id = ? AND status = 'approved' AND until_ts > ?",
            (user_id, int(time.time())), "one",
        )
        return bool(row)

    # ── /hall ──
    @commands.slash_command(name="hall", description="Панель настроек Hall FAMQ", dm_permission=False)
    async def hall(self, inter: disnake.ApplicationCommandInteraction):
        if not is_admin(inter.author):
            await inter.response.send_message("❌ Только для администрации.", ephemeral=True)
            return
        await inter.response.send_message(
            embed=panel_embed(inter.guild), view=HallPanel(inter.author.id), ephemeral=True
        )

    # ── Состав ──
    def build_roster_embed(self, guild: disnake.Guild, s) -> disnake.Embed:
        name = load_config().get("bot_name", "Hall FAMQ")
        e = make_embed(f"👥 Состав руководства {name}", "Актуальный список по должностям. Обновляется автоматически.")
        seen = set()
        for item in s["roster_roles"]:
            role = guild.get_role(item["role_id"])
            if role is None:
                continue
            members = sorted(
                (m for m in role.members if not m.bot and m.id not in seen),
                key=lambda m: m.display_name.lower(),
            )
            seen.update(m.id for m in members)
            lines = [f"• {m.mention}" for m in members] or ["— пока никого —"]
            chunks, cur = [], ""
            for line in lines:
                if len(cur) + len(line) + 1 > 1000:
                    chunks.append(cur)
                    cur = ""
                cur += line + "\n"
            if cur:
                chunks.append(cur)
            for i, chunk in enumerate(chunks):
                if len(e.fields) >= 24:
                    break
                title = f"⚜️ {item['title']} • {len(members)}" if i == 0 else "\u200b"
                e.add_field(name=title[:256], value=chunk.strip(), inline=False)
        return e

    async def update_roster(self, guild: disnake.Guild, force: bool = False) -> bool:
        s = get_settings()
        if not s["roster_channel_id"] or not s["roster_roles"]:
            return False
        channel = guild.get_channel(s["roster_channel_id"])
        if channel is None:
            return False
        embed = self.build_roster_embed(guild, s)
        digest = json.dumps(embed.to_dict(), sort_keys=True, ensure_ascii=False)
        msg = None
        if s["roster_message_id"]:
            try:
                msg = await channel.fetch_message(s["roster_message_id"])
            except disnake.NotFound:
                msg = None
            except disnake.HTTPException:
                return False
        try:
            if msg is None:
                msg = await channel.send(embed=embed)
                set_setting("roster_message_id", msg.id)
                self._roster_hash = digest
            elif force or digest != self._roster_hash:
                await msg.edit(embed=embed)
                self._roster_hash = digest
        except disnake.HTTPException:
            return False
        return True

    # ── Фоновые задачи ──
    @tasks.loop(minutes=3)
    async def roster_loop(self):
        for guild in self.bot.guilds:
            try:
                await self.update_roster(guild)
            except Exception as ex:
                print(f"[hall_tools] ошибка обновления состава: {ex}", file=sys.stderr, flush=True)

    @roster_loop.before_loop
    async def _before_roster(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=10)
    async def leave_loop(self):
        try:
            rows = await run(
                "SELECT * FROM leaves WHERE status = 'approved' AND until_ts < ?", (int(time.time()),), "all"
            )
            for r in rows:
                await run("UPDATE leaves SET status = 'ended' WHERE id = ?", (r["id"],))
                dm = make_embed(
                    "☀️ С возвращением!",
                    "Ваш отпуск закончился. Мы рады, что вы снова с нами!\n\n"
                    "Заходите в игру и на семейные мероприятия, вас очень ждут 💎",
                    "success_color",
                )
                await dm_user(self.bot, r["user_id"], dm)
        except Exception as ex:
            print(f"[hall_tools] ошибка проверки отпусков: {ex}", file=sys.stderr, flush=True)

    @leave_loop.before_loop
    async def _before_leave(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=5)
    async def afk_loop(self):
        try:
            s = get_settings()
            now = int(time.time())
            rows = await run("SELECT * FROM afk_sessions", fetch="all")
            for r in rows:
                elapsed = now - r["started_at"]
                duration_s = r["duration_hours"] * 3600
                reminder_s = s["afk_reminder_hours"] * 3600

                if elapsed >= duration_s:
                    await run("DELETE FROM afk_sessions WHERE user_id = ?", (r["user_id"],))
                    dm = make_embed(
                        "☀️ Время АФК истекло",
                        f"Здравствуйте! Ваш статус АФК ({human_hours(r['duration_hours'])}) закончился "
                        "автоматически. С возвращением! Если нужно больше времени — возьмите АФК снова на панели.",
                        "success_color",
                    )
                    await dm_user(self.bot, r["user_id"], dm)
                elif not r["reminder_sent"] and elapsed >= reminder_s:
                    await run("UPDATE afk_sessions SET reminder_sent = 1 WHERE user_id = ?", (r["user_id"],))
                    remaining_h = max(0, (duration_s - elapsed) // 3600)
                    dm = make_embed(
                        "⏰ Напоминание об АФК",
                        f"Здравствуйте! Прошло уже больше **{human_hours(s['afk_reminder_hours'])}** с момента, "
                        f"как вы взяли АФК.\n\nЕсли вы уже вернулись — нажмите «Прекратить» на АФК-панели.\n"
                        f"Если нет — статус снимется автоматически примерно через **{human_hours(int(remaining_h))}**.",
                        "warning_color",
                    )
                    await dm_user(self.bot, r["user_id"], dm)
        except Exception as ex:
            print(f"[hall_tools] ошибка проверки АФК: {ex}", file=sys.stderr, flush=True)

    @afk_loop.before_loop
    async def _before_afk(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=10)
    async def reminder_loop(self):
        try:
            await self.check_promo_reminders()
        except Exception as ex:
            print(f"[hall_tools] ошибка напоминаний о повышении: {ex}", file=sys.stderr, flush=True)

    @reminder_loop.before_loop
    async def _before_reminder(self):
        await self.bot.wait_until_ready()

    async def check_promo_reminders(self):
        s = get_settings()
        if not s["remind_enabled"]:
            return
        need = s["remind_hours"] * 3600
        now = datetime.now(timezone.utc)
        cfg = load_config()
        rows = await run("SELECT * FROM family_members WHERE rank = 2", fetch="all")
        for m in rows:
            joined = parse_ts(m["joined_at"])
            if not joined or (now - joined).total_seconds() < need:
                continue
            sent = await run(
                "SELECT 1 AS x FROM promo_reminders WHERE user_id = ? AND rank = ?", (m["user_id"], 2), "one"
            )
            if sent:
                continue
            spent = human_hours(int((now - joined).total_seconds() // 3600))
            target_name = rank_name(3)
            promo_ch = cfg.get("channels", {}).get("promotion_channel_id", 0)
            mode = s["remind_mode"]

            if mode in ("dm", "both"):
                dm = make_embed(
                    "🌟 Пора подавать на повышение!",
                    f"Здравствуйте, {m['nick']}! 👋\n\n"
                    f"Вы уже **{spent}** в семье **Hall FAMQ**, а значит можете подать отчёт на повышение "
                    f"до ранга **{target_name} (3 ранг)**! 🎖️\n\n"
                    + (f"📝 Подать отчёт можно в канале <#{promo_ch}>.\n\n" if promo_ch else "")
                    + "Желаем успехов и новых высот, вы заслужили это! 🚀✨",
                    "success_color",
                )
                await dm_user(self.bot, m["user_id"], dm)

            if mode in ("channel", "both"):
                channels = cfg.get("channels", {})
                cid = s["remind_channel_id"] or channels.get("promotion_review_channel_id", 0) or channels.get("logs_channel_id", 0)
                ch = self.bot.get_channel(cid) if cid else None
                if ch:
                    e = make_embed(
                        "⏰ Участник может претендовать на повышение",
                        f"👤 <@{m['user_id']}> (`{m['nick']}`, статик `{m['static_id']}`)\n"
                        f"🕒 В семье: **{spent}**\n"
                        f"🎖️ Может подать отчёт: **2 ➔ 3 ранг ({target_name})**",
                        "warning_color",
                    )
                    try:
                        await ch.send(embed=e)
                    except disnake.HTTPException:
                        pass

            await run("INSERT OR IGNORE INTO promo_reminders (user_id, rank) VALUES (?, ?)", (m["user_id"], 2))


def setup(bot: commands.Bot):
    bot.add_cog(HallTools(bot))
