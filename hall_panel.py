"""
Hall Panel — админ-панель управления + АФК с автоматической ролью.

• /hallpanel — удобная панель для администрации (разделы, выбор каналов/ролей, кнопки).
• АФК: при нажатии «Взять АФК» выдаётся выбранная роль, через N часов (по умолчанию 3)
  она снимается автоматически. Сессии хранятся в БД, поэтому таймер переживает перезапуск бота.

Настройки лежат в hall_settings.json (те же ключи, что использует hall_tools).
"""
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

import aiosqlite
import disnake
from disnake.ext import commands, tasks

from database import DB_PATH, get_stats
from utils.checks import load_config

# ──────────────────────────── Настройки ────────────────────────────

SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hall_settings.json"
)

DEFAULTS: Dict[str, Any] = {
    "welcome_enabled": True,
    "welcome_channel_id": 0,
    "rules_text": "Привет, {mention}! Добро пожаловать на {server}!",
    "remind_enabled": True,
    "remind_hours": 336,
    "remind_channel_id": 0,
    "leave_channel_id": 0,
    "afk_enabled": True,
    "afk_default_hours": 3,
    "afk_reminder_hours": 2,
    "afk_max_hours": 12,
    "afk_role_id": 0,
    "afk_panel_channel_id": 0,
    "afk_panel_message_id": 0,
    "afk_panel_text": (
        "Если вам нужно ненадолго отойти — нажмите **«Взять АФК»**.\n"
        "Статус снимется автоматически через заданное время, либо вы можете нажать "
        "**«Прекратить»**, чтобы вернуться раньше."
    ),
    "panel_access_user_ids": [],
    "panel_access_role_ids": [],
}


def _read_raw() -> Dict[str, Any]:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_settings() -> Dict[str, Any]:
    return {**DEFAULTS, **_read_raw()}


def update_settings(**changes: Any) -> None:
    data = _read_raw()
    data.update(changes)
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_PATH)


# ──────────────────────────── База АФК ────────────────────────────

_table_ready = False


async def _ensure_table() -> None:
    global _table_ready
    if _table_ready:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS hp_afk_sessions (
                user_id    INTEGER PRIMARY KEY,
                guild_id   INTEGER NOT NULL,
                started_at INTEGER NOT NULL,
                ends_at    INTEGER NOT NULL,
                reason     TEXT,
                reminded   INTEGER NOT NULL DEFAULT 0,
                role_id    INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await db.commit()
    _table_ready = True


async def afk_upsert(user_id: int, guild_id: int, started: int, ends: int, reason: str, role_id: int) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO hp_afk_sessions (user_id, guild_id, started_at, ends_at, reason, reminded, role_id)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                guild_id = excluded.guild_id, started_at = excluded.started_at,
                ends_at = excluded.ends_at, reason = excluded.reason,
                reminded = 0,
                role_id = CASE WHEN excluded.role_id != 0 THEN excluded.role_id ELSE hp_afk_sessions.role_id END
            """,
            (user_id, guild_id, started, ends, reason, role_id),
        )
        await db.commit()


async def afk_get(user_id: int) -> Optional[Dict[str, Any]]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM hp_afk_sessions WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def afk_delete(user_id: int) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM hp_afk_sessions WHERE user_id = ?", (user_id,))
        await db.commit()


async def _afk_query(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def afk_list(guild_id: int) -> List[Dict[str, Any]]:
    return await _afk_query("SELECT * FROM hp_afk_sessions WHERE guild_id = ? ORDER BY ends_at", (guild_id,))


async def afk_due(now: int) -> List[Dict[str, Any]]:
    return await _afk_query("SELECT * FROM hp_afk_sessions WHERE ends_at <= ?", (now,))


async def afk_need_reminder(now: int, reminder_seconds: int) -> List[Dict[str, Any]]:
    return await _afk_query(
        "SELECT * FROM hp_afk_sessions WHERE reminded = 0 AND started_at + ? <= ? AND ends_at > ?",
        (reminder_seconds, now, now),
    )


async def afk_mark_reminded(user_id: int) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE hp_afk_sessions SET reminded = 1 WHERE user_id = ?", (user_id,))
        await db.commit()


# ──────────────────────────── Хелперы ────────────────────────────

def fmt_hours(h: float) -> str:
    return f"{float(h):g} ч"


def ch_mention(cid: int) -> str:
    return f"<#{cid}>" if cid else "`не задан`"


def role_mention(rid: int) -> str:
    return f"<@&{rid}>" if rid else "`не задана`"


def onoff(v: bool) -> str:
    return "🟢 Включено" if v else "🔴 Выключено"


def parse_num(text: str, lo: float, hi: float) -> Optional[float]:
    try:
        v = float(text.strip().replace(",", "."))
    except (ValueError, AttributeError):
        return None
    return v if lo <= v <= hi else None


def fill(text: str, mapping: Dict[str, str]) -> str:
    for k, v in mapping.items():
        text = text.replace("{" + k + "}", v)
    return text


def base_embed(title: str, description: str = "") -> disnake.Embed:
    cfg = load_config()
    try:
        color = int(str(cfg.get("embed_color", "0x990000")), 16)
    except ValueError:
        color = 0x990000
    e = disnake.Embed(title=title, description=description, color=color)
    e.set_footer(text=f"{cfg.get('bot_name', 'Hallez FAMQ')} • Hall Panel")
    return e


# ──────────────────────────── Модалки ────────────────────────────

class EditModal(disnake.ui.Modal):
    """Универсальная модалка: после отправки вызывает handler(inter, values)."""

    def __init__(self, title: str, inputs: List[disnake.ui.TextInput], handler: Callable):
        self._handler = handler
        super().__init__(title=title[:45], components=inputs, timeout=300)

    async def callback(self, inter: disnake.ModalInteraction):
        await self._handler(inter, inter.text_values)


def text_input(label: str, cid: str, value: str = "", paragraph: bool = False,
               max_length: int = 100, required: bool = True, placeholder: str = "") -> disnake.ui.TextInput:
    return disnake.ui.TextInput(
        label=label[:45],
        custom_id=cid,
        value=value[:max_length] if value else None,
        placeholder=placeholder or None,
        style=disnake.TextInputStyle.paragraph if paragraph else disnake.TextInputStyle.short,
        max_length=max_length,
        required=required,
    )


# ──────────────────────────── Панель АФК (публичная, persistent) ────────────────────────────

class AfkPanelView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @staticmethod
    def _cog(inter: disnake.MessageInteraction) -> "HallPanel":
        return inter.bot.get_cog("HallPanel")

    @disnake.ui.button(label="Взять АФК", emoji="💤", style=disnake.ButtonStyle.primary,
                       custom_id="hallpanel:afk:quick")
    async def quick(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        st = load_settings()
        ok, msg = await self._cog(inter).take_afk(inter.author, float(st["afk_default_hours"]), "")
        await inter.response.send_message(msg, ephemeral=True)

    @disnake.ui.button(label="Указать время / причину", emoji="⏱", style=disnake.ButtonStyle.secondary,
                       custom_id="hallpanel:afk:custom")
    async def custom(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        st = load_settings()
        cog = self._cog(inter)

        async def handler(minter: disnake.ModalInteraction, values: Dict[str, str]):
            mx = float(st["afk_max_hours"])
            hours = parse_num(values["hours"], 0.5, mx)
            if hours is None:
                return await minter.response.send_message(
                    f"❌ Укажите число часов от 0.5 до {mx:g}.", ephemeral=True)
            ok, msg = await cog.take_afk(minter.author, hours, values.get("reason", "").strip())
            await minter.response.send_message(msg, ephemeral=True)

        await inter.response.send_modal(EditModal("Взять АФК", [
            text_input("На сколько часов?", "hours", f"{float(st['afk_default_hours']):g}", max_length=5),
            text_input("Причина (необязательно)", "reason", max_length=100, required=False),
        ], handler))

    @disnake.ui.button(label="Прекратить", emoji="✅", style=disnake.ButtonStyle.success,
                       custom_id="hallpanel:afk:stop")
    async def stop(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        ok, msg = await self._cog(inter).stop_afk(inter.author)
        await inter.response.send_message(msg, ephemeral=True)

    @disnake.ui.button(label="Кто в АФК", emoji="👥", style=disnake.ButtonStyle.secondary,
                       custom_id="hallpanel:afk:list")
    async def who(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        embed = await self._cog(inter).afk_list_embed(inter.guild)
        await inter.response.send_message(embed=embed, ephemeral=True)


# ──────────────────────────── Админ-панель ────────────────────────────

SECTIONS = {
    "home": ("🏠", "Главная", "Обзор всех настроек"),
    "welcome": ("👋", "Приветствие", "Текст правил и канал"),
    "remind": ("⏰", "Напоминания", "Напоминания и канал выходов"),
    "afk": ("💤", "АФК", "Роль, время, публичная панель"),
    "stats": ("📊", "Статистика", "Заявки, участники, варны"),
    "access": ("🔐", "Доступ", "Кто может открывать эту панель"),
}


class PanelView(disnake.ui.View):
    """Базовый вид: меню разделов в верхнем ряду + проверка прав."""
    section = "home"

    def __init__(self, cog: "HallPanel", owner_id: int, full: bool = False):
        super().__init__(timeout=900)
        self.cog = cog
        self.owner_id = owner_id
        self.full = full  # полный админ: видит раздел «Доступ»
        self.nav.options = [
            disnake.SelectOption(label=name, value=key, emoji=emoji, description=desc,
                                 default=(key == self.section))
            for key, (emoji, name, desc) in SECTIONS.items()
            if key != "access" or full
        ]

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:
        if inter.author.id != self.owner_id or not self.cog.has_access(inter.author):
            await inter.response.send_message("❌ Эта панель доступна только тому, кто её открыл.",
                                              ephemeral=True)
            return False
        return True

    @disnake.ui.string_select(placeholder="📂 Раздел панели", row=0,
                              options=[disnake.SelectOption(label="Главная", value="home")])
    async def nav(self, select: disnake.ui.StringSelect, inter: disnake.MessageInteraction):
        await self.cog.show(inter, select.values[0], self.owner_id)


class HomeView(PanelView):
    section = "home"


class WelcomeView(PanelView):
    section = "welcome"

    def __init__(self, cog, owner_id, full=False):
        super().__init__(cog, owner_id, full)
        on = load_settings()["welcome_enabled"]
        self.toggle.label = "Выключить" if on else "Включить"
        self.toggle.style = disnake.ButtonStyle.danger if on else disnake.ButtonStyle.success

    @disnake.ui.channel_select(placeholder="📢 Канал приветствий", row=1,
                               channel_types=[disnake.ChannelType.text])
    async def pick_channel(self, select: disnake.ui.ChannelSelect, inter: disnake.MessageInteraction):
        update_settings(welcome_channel_id=select.values[0].id)
        await self.cog.show(inter, "welcome", self.owner_id)

    @disnake.ui.button(label="Включить", emoji="🔌", row=2)
    async def toggle(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        update_settings(welcome_enabled=not load_settings()["welcome_enabled"])
        await self.cog.show(inter, "welcome", self.owner_id)

    @disnake.ui.button(label="Изменить текст", emoji="📝", style=disnake.ButtonStyle.primary, row=2)
    async def edit_text(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        async def handler(minter, values):
            update_settings(rules_text=values["rules_text"])
            await self.cog.show(minter, "welcome", self.owner_id)

        await inter.response.send_modal(EditModal("Текст приветствия", [
            text_input("Текст ({mention} {server} {recruit_channel})", "rules_text",
                       load_settings()["rules_text"], paragraph=True, max_length=4000),
        ], handler))

    @disnake.ui.button(label="Предпросмотр", emoji="👁", row=2)
    async def preview(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        cfg = load_config()
        rid = cfg.get("channels", {}).get("recruitment_channel_id", 0)
        text = fill(load_settings()["rules_text"], {
            "mention": inter.author.mention, "server": inter.guild.name,
            "recruit_channel": f"<#{rid}>" if rid else "#набор",
        })
        await inter.response.send_message(embed=base_embed("👁 Так увидит новичок", text[:4000]), ephemeral=True)

    @disnake.ui.button(label="Сбросить канал", emoji="🧹", row=2)
    async def reset_channel(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        update_settings(welcome_channel_id=0)
        await self.cog.show(inter, "welcome", self.owner_id)


class RemindView(PanelView):
    section = "remind"

    def __init__(self, cog, owner_id, full=False):
        super().__init__(cog, owner_id, full)
        on = load_settings()["remind_enabled"]
        self.toggle.label = "Выключить" if on else "Включить"
        self.toggle.style = disnake.ButtonStyle.danger if on else disnake.ButtonStyle.success

    @disnake.ui.channel_select(placeholder="📢 Канал напоминаний", row=1,
                               channel_types=[disnake.ChannelType.text])
    async def pick_remind(self, select: disnake.ui.ChannelSelect, inter: disnake.MessageInteraction):
        update_settings(remind_channel_id=select.values[0].id)
        await self.cog.show(inter, "remind", self.owner_id)

    @disnake.ui.channel_select(placeholder="🚪 Канал уведомлений о выходе", row=2,
                               channel_types=[disnake.ChannelType.text])
    async def pick_leave(self, select: disnake.ui.ChannelSelect, inter: disnake.MessageInteraction):
        update_settings(leave_channel_id=select.values[0].id)
        await self.cog.show(inter, "remind", self.owner_id)

    @disnake.ui.button(label="Включить", emoji="🔌", row=3)
    async def toggle(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        update_settings(remind_enabled=not load_settings()["remind_enabled"])
        await self.cog.show(inter, "remind", self.owner_id)

    @disnake.ui.button(label="Через сколько часов", emoji="⏱", style=disnake.ButtonStyle.primary, row=3)
    async def hours(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        async def handler(minter, values):
            v = parse_num(values["hours"], 1, 8760)
            if v is None:
                return await minter.response.send_message("❌ Укажите число часов от 1 до 8760.", ephemeral=True)
            update_settings(remind_hours=int(v))
            await self.cog.show(minter, "remind", self.owner_id)

        await inter.response.send_modal(EditModal("Время напоминания", [
            text_input("Часов (336 = 14 дней)", "hours", str(load_settings()["remind_hours"]), max_length=5),
        ], handler))

    @disnake.ui.button(label="Сбросить каналы", emoji="🧹", row=3)
    async def reset(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        update_settings(remind_channel_id=0, leave_channel_id=0)
        await self.cog.show(inter, "remind", self.owner_id)


class AfkAdminView(PanelView):
    section = "afk"

    def __init__(self, cog, owner_id, full=False):
        super().__init__(cog, owner_id, full)
        on = load_settings()["afk_enabled"]
        self.toggle.label = "Выключить" if on else "Включить"
        self.toggle.style = disnake.ButtonStyle.danger if on else disnake.ButtonStyle.success

    @disnake.ui.role_select(placeholder="🎭 Роль, которая выдаётся при АФК", row=1)
    async def pick_role(self, select: disnake.ui.RoleSelect, inter: disnake.MessageInteraction):
        role = select.values[0]
        update_settings(afk_role_id=role.id)
        await self.cog.show(inter, "afk", self.owner_id)
        problem = self.cog.role_problem(inter.guild, role)
        if problem:
            await inter.followup.send(problem, ephemeral=True)

    @disnake.ui.channel_select(placeholder="📢 Канал для публичной АФК-панели", row=2,
                               channel_types=[disnake.ChannelType.text])
    async def pick_channel(self, select: disnake.ui.ChannelSelect, inter: disnake.MessageInteraction):
        update_settings(afk_panel_channel_id=select.values[0].id, afk_panel_message_id=0)
        await self.cog.show(inter, "afk", self.owner_id)

    @disnake.ui.button(label="Включить", emoji="🔌", row=3)
    async def toggle(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        update_settings(afk_enabled=not load_settings()["afk_enabled"])
        await self.cog.show(inter, "afk", self.owner_id)

    @disnake.ui.button(label="Время", emoji="⏱", style=disnake.ButtonStyle.primary, row=3)
    async def times(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        st = load_settings()

        async def handler(minter, values):
            default = parse_num(values["default"], 0.5, 72)
            maxh = parse_num(values["max"], 0.5, 72)
            rem = parse_num(values["remind"], 0, 72)
            if default is None or maxh is None or rem is None:
                return await minter.response.send_message("❌ Нужны числа (максимум 72 часа).", ephemeral=True)
            if maxh < default:
                return await minter.response.send_message("❌ Максимум не может быть меньше значения по умолчанию.",
                                                          ephemeral=True)
            if rem and rem >= default:
                return await minter.response.send_message(
                    "❌ Напоминание должно быть раньше конца АФК по умолчанию (или 0 — выключить).", ephemeral=True)
            update_settings(afk_default_hours=default, afk_max_hours=maxh, afk_reminder_hours=rem)
            await self.cog.show(minter, "afk", self.owner_id)

        await inter.response.send_modal(EditModal("Время АФК", [
            text_input("Снять роль через (часов)", "default", f"{float(st['afk_default_hours']):g}", max_length=5),
            text_input("Максимум, который можно выбрать (ч)", "max", f"{float(st['afk_max_hours']):g}", max_length=5),
            text_input("Напомнить через (ч, 0 — не нужно)", "remind", f"{float(st['afk_reminder_hours']):g}",
                       max_length=5),
        ], handler))

    @disnake.ui.button(label="Текст панели", emoji="📝", row=3)
    async def text(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        async def handler(minter, values):
            update_settings(afk_panel_text=values["text"])
            await self.cog.show(minter, "afk", self.owner_id)

        await inter.response.send_modal(EditModal("Текст АФК-панели", [
            text_input("Описание панели", "text", load_settings()["afk_panel_text"],
                       paragraph=True, max_length=1500),
        ], handler))

    @disnake.ui.button(label="Убрать роль", emoji="🧹", row=3)
    async def clear_role(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        update_settings(afk_role_id=0)
        await self.cog.show(inter, "afk", self.owner_id)

    @disnake.ui.button(label="Отправить / обновить панель", emoji="📤", style=disnake.ButtonStyle.success, row=4)
    async def send_panel(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        ok, msg = await self.cog.publish_afk_panel(inter.guild)
        await inter.response.send_message(msg, ephemeral=True)

    @disnake.ui.button(label="Кто в АФК", emoji="👥", row=4)
    async def who(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.send_message(embed=await self.cog.afk_list_embed(inter.guild), ephemeral=True)

    @disnake.ui.button(label="Снять АФК со всех", emoji="🛑", style=disnake.ButtonStyle.danger, row=4)
    async def clear_all(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        sessions = await afk_list(inter.guild.id)
        for s in sessions:
            await self.cog.finish_afk(s, "АФК снят администратором", notify=False)
        await inter.followup.send(f"🛑 АФК снят у **{len(sessions)}** чел.", ephemeral=True)


class StatsView(PanelView):
    section = "stats"

    @disnake.ui.button(label="Обновить", emoji="🔄", row=1)
    async def refresh(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await self.cog.show(inter, "stats", self.owner_id)


class AccessView(PanelView):
    section = "access"

    def __init__(self, cog, owner_id, full=False):
        super().__init__(cog, owner_id, full)
        st = load_settings()
        entries = ([("u", i) for i in st["panel_access_user_ids"]]
                   + [("r", i) for i in st["panel_access_role_ids"]])[:25]
        if not entries:
            self.remove_item(self.remove_access)
        else:
            self.remove_access.options = [
                disnake.SelectOption(
                    label=(cog.user_name(i) if k == "u" else cog.role_name(i))[:100],
                    value=f"{k}:{i}", emoji="👤" if k == "u" else "🎭")
                for k, i in entries
            ]
            self.remove_access.max_values = len(entries)

    @disnake.ui.user_select(placeholder="👤 Дать доступ человеку", row=1, min_values=1, max_values=5)
    async def add_user(self, select: disnake.ui.UserSelect, inter: disnake.MessageInteraction):
        ids = list(load_settings()["panel_access_user_ids"])
        for u in select.values:
            if not u.bot and u.id not in ids:
                ids.append(u.id)
        update_settings(panel_access_user_ids=ids)
        await self.cog.show(inter, "access", self.owner_id)

    @disnake.ui.role_select(placeholder="🎭 Дать доступ всей роли", row=2, min_values=1, max_values=5)
    async def add_role(self, select: disnake.ui.RoleSelect, inter: disnake.MessageInteraction):
        ids = list(load_settings()["panel_access_role_ids"])
        for r in select.values:
            if not r.is_default() and r.id not in ids:
                ids.append(r.id)
        update_settings(panel_access_role_ids=ids)
        await self.cog.show(inter, "access", self.owner_id)

    @disnake.ui.string_select(placeholder="🗑 Забрать доступ", row=3, min_values=1, max_values=1,
                              options=[disnake.SelectOption(label="—", value="x:0")])
    async def remove_access(self, select: disnake.ui.StringSelect, inter: disnake.MessageInteraction):
        st = load_settings()
        users = {int(v.split(":")[1]) for v in select.values if v.startswith("u:")}
        roles = {int(v.split(":")[1]) for v in select.values if v.startswith("r:")}
        update_settings(
            panel_access_user_ids=[i for i in st["panel_access_user_ids"] if i not in users],
            panel_access_role_ids=[i for i in st["panel_access_role_ids"] if i not in roles],
        )
        await self.cog.show(inter, "access", self.owner_id)


VIEWS = {
    "home": HomeView, "welcome": WelcomeView, "remind": RemindView,
    "afk": AfkAdminView, "stats": StatsView, "access": AccessView,
}


# ──────────────────────────── Cog ────────────────────────────

class HallPanel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._views_ready = False
        self.afk_loop.start()

    def cog_unload(self):
        self.afk_loop.cancel()

    # ── права ──
    @staticmethod
    def is_admin(member: disnake.abc.User) -> bool:
        if not isinstance(member, disnake.Member):
            return False
        if member.guild_permissions.administrator:
            return True
        cfg = load_config()
        if member.id in cfg.get("admin_user_ids", []):
            return True
        admin_roles = {r for r in cfg.get("admin_role_ids", []) if r}
        return any(r.id in admin_roles for r in member.roles)

    def has_access(self, member: disnake.abc.User) -> bool:
        """Полные админы + люди/роли, которым дали доступ именно к /hallpanel."""
        if self.is_admin(member):
            return True
        if not isinstance(member, disnake.Member):
            return False
        st = load_settings()
        if member.id in st["panel_access_user_ids"]:
            return True
        allowed = set(st["panel_access_role_ids"])
        return any(r.id in allowed for r in member.roles)

    def user_name(self, uid: int) -> str:
        u = self.bot.get_user(uid)
        return u.display_name if u else f"ID {uid}"

    def role_name(self, rid: int) -> str:
        for g in self.bot.guilds:
            r = g.get_role(rid)
            if r:
                return f"@{r.name}"
        return f"роль {rid}"

    @staticmethod
    def role_problem(guild: disnake.Guild, role: disnake.Role) -> Optional[str]:
        me = guild.me
        if not me.guild_permissions.manage_roles:
            return "⚠️ У бота нет права **«Управление ролями»**."
        if role.managed:
            return "⚠️ Эту роль нельзя выдавать — она управляется интеграцией."
        if role.position >= me.top_role.position:
            return f"⚠️ Роль {role.mention} выше роли бота. Поднимите роль бота выше неё в настройках сервера."
        return None

    # ── регистрация persistent-вью ──
    @commands.Cog.listener()
    async def on_ready(self):
        if not self._views_ready:
            self.bot.add_view(AfkPanelView())
            self._views_ready = True

    # ── команда ──
    @commands.slash_command(name="hallpanel", description="Панель управления Hall для администрации",
                            contexts=disnake.InteractionContextTypes(guild=True))
    async def hallpanel(self, inter: disnake.ApplicationCommandInteraction):
        if not self.has_access(inter.author):
            return await inter.response.send_message("❌ У вас нет доступа к этой панели.", ephemeral=True)
        full = self.is_admin(inter.author)
        embed = await self.build_embed("home", inter.guild)
        await inter.response.send_message(embed=embed, view=HomeView(self, inter.author.id, full), ephemeral=True)

    async def show(self, inter, section: str, owner_id: int):
        full = self.is_admin(inter.author)
        if section == "access" and not full:
            return await inter.response.send_message("❌ Раздел «Доступ» только для полных администраторов.",
                                                     ephemeral=True)
        embed = await self.build_embed(section, inter.guild)
        await inter.response.edit_message(embed=embed, view=VIEWS[section](self, owner_id, full))

    # ── эмбеды разделов ──
    async def build_embed(self, section: str, guild: disnake.Guild) -> disnake.Embed:
        st = load_settings()
        afk_count = len(await afk_list(guild.id))
        role = role_mention(st["afk_role_id"])

        if section == "home":
            e = base_embed("⚜️ Панель управления Hall",
                           "Выберите раздел в меню ниже. Все изменения применяются сразу.")
            e.add_field(name="👋 Приветствие",
                        value=f"{onoff(st['welcome_enabled'])}\nКанал: {ch_mention(st['welcome_channel_id'])}")
            e.add_field(name="⏰ Напоминания",
                        value=f"{onoff(st['remind_enabled'])}\nЧерез: **{st['remind_hours']} ч** "
                              f"(~{st['remind_hours'] / 24:.0f} дн.)")
            e.add_field(name="💤 АФК",
                        value=f"{onoff(st['afk_enabled'])}\nРоль: {role}\nСейчас в АФК: **{afk_count}**")
            n_acc = len(st["panel_access_user_ids"]) + len(st["panel_access_role_ids"])
            e.add_field(name="🔐 Доступ к панели", value=f"Выдан: **{n_acc}**")
            return e

        if section == "welcome":
            e = base_embed("👋 Приветствие новичков")
            e.add_field(name="Статус", value=onoff(st["welcome_enabled"]))
            e.add_field(name="Канал", value=ch_mention(st["welcome_channel_id"]))
            preview = st["rules_text"]
            e.add_field(name="Текст", value=(preview[:900] + "…") if len(preview) > 900 else preview, inline=False)
            e.add_field(name="Переменные", value="`{mention}` `{server}` `{recruit_channel}`", inline=False)
            return e

        if section == "remind":
            e = base_embed("⏰ Напоминания и выходы")
            e.add_field(name="Статус", value=onoff(st["remind_enabled"]))
            e.add_field(name="Через", value=f"**{st['remind_hours']} ч**")
            e.add_field(name="Канал напоминаний", value=ch_mention(st["remind_channel_id"]))
            e.add_field(name="Канал выходов", value=ch_mention(st["leave_channel_id"]))
            return e

        if section == "afk":
            e = base_embed("💤 АФК-система",
                           "При АФК бот сам выдаёт роль и сам снимает её по таймеру. "
                           "Таймер сохраняется, даже если бот перезапустится.")
            e.add_field(name="Статус", value=onoff(st["afk_enabled"]))
            e.add_field(name="Роль АФК", value=role)
            e.add_field(name="Сейчас в АФК", value=f"**{afk_count}**")
            e.add_field(name="Снять роль через", value=fmt_hours(st["afk_default_hours"]))
            rem = float(st["afk_reminder_hours"])
            e.add_field(name="Напоминание", value=fmt_hours(rem) if rem else "выключено")
            e.add_field(name="Максимум", value=fmt_hours(st["afk_max_hours"]))
            e.add_field(name="Публичная панель",
                        value=f"{ch_mention(st['afk_panel_channel_id'])}"
                              + (" • отправлена ✅" if st["afk_panel_message_id"] else " • ещё не отправлена"),
                        inline=False)
            rid = int(st["afk_role_id"] or 0)
            if rid:
                r = guild.get_role(rid)
                problem = self.role_problem(guild, r) if r else "⚠️ Выбранная роль больше не существует."
                if problem:
                    e.add_field(name="Проблема", value=problem, inline=False)
            return e

        if section == "access":
            e = base_embed("🔐 Доступ к /hallpanel",
                           "Люди и роли из списка могут открывать **только эту панель** и менять в ней настройки. "
                           "Админами бота они не становятся, а раздел «Доступ» видят только полные администраторы.")
            users = st["panel_access_user_ids"]
            roles = st["panel_access_role_ids"]
            e.add_field(name="👤 Люди",
                        value="\n".join(f"<@{i}>" for i in users) or "`никого`", inline=True)
            e.add_field(name="🎭 Роли",
                        value="\n".join(f"<@&{i}>" for i in roles) or "`нет`", inline=True)
            e.add_field(name="Как пользоваться",
                        value="Выберите человека или роль в списках ниже — доступ выдаётся сразу. "
                              "Чтобы забрать, выберите в нижнем списке «Забрать доступ».", inline=False)
            return e

        # stats
        s = await get_stats()
        e = base_embed("📊 Статистика семьи")
        e.add_field(name="👥 Участников", value=str(s["total_members"]))
        e.add_field(name="📝 Заявок в ожидании", value=str(s["pending_apps"]))
        e.add_field(name="✅ Принято всего", value=str(s["accepted_apps"]))
        e.add_field(name="📈 Отчётов на повышение", value=str(s["pending_promos"]))
        e.add_field(name="⚠️ Активных варнов", value=str(s["active_warns"]))
        e.add_field(name="💤 В АФК сейчас", value=str(afk_count))
        return e

    # ── АФК: логика ──
    async def take_afk(self, member: disnake.Member, hours: float, reason: str):
        st = load_settings()
        if not st["afk_enabled"]:
            return False, "❌ АФК-система сейчас отключена."

        guild = member.guild
        now = int(time.time())
        ends = now + int(hours * 3600)
        role_id, warn = 0, ""
        rid = int(st["afk_role_id"] or 0)

        if rid:
            role = guild.get_role(rid)
            if role is None:
                warn = "\n⚠️ Роль АФК не найдена — сообщите администратору."
            else:
                problem = self.role_problem(guild, role)
                if problem:
                    warn = f"\n{problem}"
                else:
                    try:
                        if role not in member.roles:
                            await member.add_roles(role, reason=f"АФК на {fmt_hours(hours)}")
                        role_id = role.id
                    except disnake.HTTPException:
                        warn = "\n⚠️ Не удалось выдать роль АФК — сообщите администратору."

        await afk_upsert(member.id, guild.id, now, ends, reason, role_id)
        text = f"💤 Вы в АФК на **{fmt_hours(hours)}** — до <t:{ends}:t> (<t:{ends}:R>)."
        if role_id:
            text += f"\nРоль <@&{role_id}> выдана и снимется автоматически."
        if reason:
            text += f"\n📝 Причина: {reason}"
        return True, text + warn

    async def stop_afk(self, member: disnake.Member):
        s = await afk_get(member.id)
        if not s:
            return False, "ℹ️ Вы сейчас не в АФК."
        await self.finish_afk(s, "АФК прекращён пользователем", notify=False)
        return True, "✅ С возвращением! АФК снят, роль убрана."

    async def finish_afk(self, s: Dict[str, Any], reason_text: str, notify: bool):
        guild = self.bot.get_guild(s["guild_id"])
        member = None
        if guild:
            member = guild.get_member(s["user_id"])
            if member is None:
                try:
                    member = await guild.fetch_member(s["user_id"])
                except disnake.NotFound:
                    member = None
                except disnake.HTTPException:
                    return  # временная ошибка — попробуем в следующем цикле

        role = guild.get_role(s["role_id"]) if guild and s["role_id"] else None
        if member and role and role in member.roles:
            try:
                await member.remove_roles(role, reason=reason_text)
            except disnake.Forbidden:
                print(f"⚠️ [АФК] Нет прав снять роль {role.id} у {member.id}", flush=True)
            except disnake.HTTPException:
                return  # попробуем ещё раз позже, запись не удаляем

        await afk_delete(s["user_id"])

        if notify and member:
            try:
                await member.send(embed=base_embed("✅ АФК закончился",
                                                   "Время АФК истекло, роль снята. С возвращением!"))
            except disnake.HTTPException:
                pass

    @tasks.loop(seconds=30)
    async def afk_loop(self):
        now = int(time.time())
        for s in await afk_due(now):
            await self.finish_afk(s, "АФК закончился по таймеру", notify=True)

        rem = float(load_settings()["afk_reminder_hours"] or 0)
        if rem > 0:
            for s in await afk_need_reminder(now, int(rem * 3600)):
                await afk_mark_reminded(s["user_id"])
                user = self.bot.get_user(s["user_id"])
                if user:
                    try:
                        await user.send(embed=base_embed(
                            "⏰ Вы всё ещё в АФК",
                            f"АФК закончится <t:{s['ends_at']}:R>. Если вы уже вернулись — "
                            "нажмите **«Прекратить»** на АФК-панели."))
                    except disnake.HTTPException:
                        pass

    @afk_loop.before_loop
    async def _before_loop(self):
        await self.bot.wait_until_ready()
        await _ensure_table()

    @afk_loop.error
    async def _loop_error(self, error: BaseException):
        print(f"⚠️ [АФК] Ошибка цикла: {error}", flush=True)

    @commands.Cog.listener()
    async def on_member_remove(self, member: disnake.Member):
        await afk_delete(member.id)

    # ── АФК: публичная панель ──
    async def afk_list_embed(self, guild: disnake.Guild) -> disnake.Embed:
        sessions = await afk_list(guild.id)
        if not sessions:
            return base_embed("👥 Сейчас в АФК", "Никого нет — все на месте ✨")
        lines = []
        for s in sessions[:25]:
            line = f"<@{s['user_id']}> — до <t:{s['ends_at']}:t> (<t:{s['ends_at']}:R>)"
            if s["reason"]:
                line += f" • {s['reason'][:60]}"
            lines.append(line)
        if len(sessions) > 25:
            lines.append(f"… и ещё {len(sessions) - 25}")
        return base_embed(f"👥 Сейчас в АФК: {len(sessions)}", "\n".join(lines))

    async def publish_afk_panel(self, guild: disnake.Guild):
        st = load_settings()
        channel = guild.get_channel(int(st["afk_panel_channel_id"] or 0))
        if not isinstance(channel, disnake.TextChannel):
            return False, "❌ Сначала выберите канал для АФК-панели."

        desc = st["afk_panel_text"]
        desc += f"\n\n⏱ По умолчанию: **{fmt_hours(st['afk_default_hours'])}**"
        if st["afk_role_id"]:
            desc += f" • Роль <@&{st['afk_role_id']}> выдаётся автоматически и снимается по окончании."
        embed = base_embed("💤 АФК-панель", desc)
        view = AfkPanelView()

        mid = int(st["afk_panel_message_id"] or 0)
        if mid:
            try:
                msg = await channel.fetch_message(mid)
                await msg.edit(embed=embed, view=view)
                return True, f"✅ Панель обновлена: {msg.jump_url}"
            except disnake.HTTPException:
                pass
        try:
            msg = await channel.send(embed=embed, view=view)
        except disnake.Forbidden:
            return False, f"❌ У бота нет прав писать в {channel.mention}."
        update_settings(afk_panel_message_id=msg.id)
        return True, f"✅ Панель отправлена: {msg.jump_url}"


def setup(bot: commands.Bot):
    bot.add_cog(HallPanel(bot))
