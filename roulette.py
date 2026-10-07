"""
cogs/roulette.py

Ежедневная рулетка с призами + красивая админ-панель управления призами.

Команды:
    /ruletka       — крутить рулетку (раз в 24 часа на пользователя)
    /rulet_panel   — панель администратора: добавить/изменить/удалить призы,
                      настроить веса (%), задать приз "Выговор" с выдачей роли

Подключение:
    1. Положи этот файл в cogs/roulette.py
    2. В main.py добавь "cogs.roulette" в список COGS
"""

import asyncio
import random
import time
from typing import Optional, List, Dict, Any

import aiosqlite
import disnake
from disnake.ext import commands

from database import DB_PATH, add_warn, change_balance, get_balance
from utils.checks import load_config

DAILY_COOLDOWN_SECONDS = 24 * 3600
SPIN_FRAMES = 4          # сколько "кадров" показываем при прокрутке
SPIN_FRAME_DELAY = 0.7   # секунды между кадрами

PRIZE_TYPE_LABELS = {
    "currency": "💰 Монеты",
    "role": "🎭 Роль",
    "warn": "⚠️ Выговор",
    "text": "📝 Просто текст (без эффекта)",
}


# ---------------------------------------------------------------------------
# Доступ к БД
# ---------------------------------------------------------------------------

async def init_roulette_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS roulette_prizes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            emoji TEXT NOT NULL DEFAULT '🎁',
            prize_type TEXT NOT NULL,
            value TEXT,
            weight INTEGER NOT NULL DEFAULT 10
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS roulette_claims (
            user_id INTEGER PRIMARY KEY,
            last_claim INTEGER NOT NULL DEFAULT 0,
            total_spins INTEGER NOT NULL DEFAULT 0
        )
        """)
        await db.commit()


async def get_prizes() -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM roulette_prizes ORDER BY weight DESC") as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_prize(prize_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM roulette_prizes WHERE id = ?", (prize_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def add_prize(name: str, emoji: str, prize_type: str, value: str, weight: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO roulette_prizes (name, emoji, prize_type, value, weight) VALUES (?, ?, ?, ?, ?)",
            (name, emoji, prize_type, value, weight),
        )
        await db.commit()
        return cur.lastrowid


async def update_prize(prize_id: int, name: str, emoji: str, value: str, weight: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE roulette_prizes SET name = ?, emoji = ?, value = ?, weight = ? WHERE id = ?",
            (name, emoji, value, weight, prize_id),
        )
        await db.commit()


async def delete_prize(prize_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM roulette_prizes WHERE id = ?", (prize_id,))
        await db.commit()


async def get_claim(user_id: int) -> Dict[str, Any]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM roulette_claims WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            if row:
                return dict(row)
            return {"user_id": user_id, "last_claim": 0, "total_spins": 0}


async def set_claim(user_id: int, last_claim: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO roulette_claims (user_id, last_claim, total_spins) VALUES (?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                last_claim = excluded.last_claim,
                total_spins = total_spins + 1
            """,
            (user_id, last_claim),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------

def is_admin(inter: disnake.ApplicationCommandInteraction) -> bool:
    if inter.author.guild_permissions.administrator:
        return True
    config = load_config()
    if inter.author.id in config.get("admin_user_ids", []):
        return True
    admin_role_ids = set(config.get("admin_role_ids", []))
    member_role_ids = {r.id for r in getattr(inter.author, "roles", [])}
    return bool(admin_role_ids & member_role_ids)


def fmt_time_left(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} ч {m} мин"
    if m:
        return f"{m} мин {s} сек"
    return f"{s} сек"


def pick_weighted(prizes: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = sum(p["weight"] for p in prizes) or 1
    roll = random.uniform(0, total)
    upto = 0.0
    for p in prizes:
        upto += p["weight"]
        if roll <= upto:
            return p
    return prizes[-1]


def progress_bar(percent: float, length: int = 12) -> str:
    filled = round(length * percent / 100)
    return "█" * filled + "░" * (length - filled)


async def build_panel_embed(guild: disnake.Guild) -> disnake.Embed:
    config = load_config()
    color = int(config.get("embed_color", "0x990000"), 16)
    prizes = await get_prizes()

    embed = disnake.Embed(
        title="🎰 Панель управления рулеткой",
        color=color,
    )

    if not prizes:
        embed.description = "Призов пока нет. Нажми **➕ Добавить приз**, чтобы создать первый."
    else:
        total_weight = sum(p["weight"] for p in prizes) or 1
        lines = []
        for p in prizes:
            percent = p["weight"] / total_weight * 100
            type_label = PRIZE_TYPE_LABELS.get(p["prize_type"], p["prize_type"])
            value_desc = ""
            if p["prize_type"] == "currency":
                value_desc = f" • {p['value']} монет"
            elif p["prize_type"] in ("role", "warn") and p["value"]:
                role = guild.get_role(int(p["value"])) if p["value"].isdigit() else None
                value_desc = f" • роль: {role.mention if role else '⚠️ не найдена'}"
            lines.append(
                f"**#{p['id']} {p['emoji']} {p['name']}** — {type_label}{value_desc}\n"
                f"`{progress_bar(percent)}` {percent:.1f}% (вес {p['weight']})"
            )
        embed.description = "\n\n".join(lines)
        embed.set_footer(text=f"Всего призов: {len(prizes)} • Крутить: /ruletka")

    return embed


# ---------------------------------------------------------------------------
# Модалка добавления / редактирования приза
# ---------------------------------------------------------------------------

class PrizeModal(disnake.ui.Modal):
    def __init__(self, prize_type: str, cog: "Roulette", existing: Optional[Dict[str, Any]] = None):
        self.prize_type = prize_type
        self.cog = cog
        self.existing = existing

        components = [
            disnake.ui.TextInput(
                label="Название приза",
                custom_id="name",
                style=disnake.TextInputStyle.short,
                max_length=60,
                value=existing["name"] if existing else "",
            ),
            disnake.ui.TextInput(
                label="Эмодзи (одно, например 🎉)",
                custom_id="emoji",
                style=disnake.TextInputStyle.short,
                max_length=10,
                required=False,
                value=existing["emoji"] if existing else "🎁",
            ),
        ]

        if prize_type == "currency":
            components.append(disnake.ui.TextInput(
                label="Сумма монет (можно отрицательную)",
                custom_id="value",
                style=disnake.TextInputStyle.short,
                max_length=10,
                value=existing["value"] if existing else "100",
            ))
        elif prize_type in ("role", "warn"):
            label = "ID роли для выдачи" if prize_type == "role" else "ID роли за выговор (можно пусто)"
            components.append(disnake.ui.TextInput(
                label=label,
                custom_id="value",
                style=disnake.TextInputStyle.short,
                max_length=25,
                required=(prize_type == "role"),
                value=existing["value"] if existing else "",
            ))
        else:  # text
            components.append(disnake.ui.TextInput(
                label="Текст сообщения при выпадении",
                custom_id="value",
                style=disnake.TextInputStyle.paragraph,
                max_length=200,
                required=False,
                value=existing["value"] if existing else "Пусто! Повезёт в другой раз.",
            ))

        components.append(disnake.ui.TextInput(
            label="Вес / шанс (число, больше = чаще)",
            custom_id="weight",
            style=disnake.TextInputStyle.short,
            max_length=5,
            value=str(existing["weight"]) if existing else "10",
        ))

        title = f"{'Изменить' if existing else 'Добавить'} приз"
        super().__init__(title=title, custom_id="roulette_prize_modal", components=components)

    async def callback(self, inter: disnake.ModalInteraction):
        name = inter.text_values["name"].strip()
        emoji = inter.text_values.get("emoji", "🎁").strip() or "🎁"
        raw_value = inter.text_values.get("value", "").strip()
        raw_weight = inter.text_values["weight"].strip()

        try:
            weight = max(1, int(raw_weight))
        except ValueError:
            await inter.response.send_message("Вес должен быть целым числом.", ephemeral=True)
            return

        if self.prize_type == "currency":
            try:
                int(raw_value)
            except ValueError:
                await inter.response.send_message("Сумма монет должна быть числом.", ephemeral=True)
                return
        elif self.prize_type in ("role", "warn") and raw_value:
            raw_value = raw_value.strip("<@&>")
            if not raw_value.isdigit():
                await inter.response.send_message("ID роли должен быть числом (можно скопировать через правый клик → Копировать ID).", ephemeral=True)
                return

        if self.existing:
            await update_prize(self.existing["id"], name, emoji, raw_value, weight)
        else:
            await add_prize(name, emoji, self.prize_type, raw_value, weight)

        embed = await build_panel_embed(inter.guild)
        await inter.response.edit_message(embed=embed, view=PanelView(self.cog))


# ---------------------------------------------------------------------------
# View: выбор типа приза (для добавления)
# ---------------------------------------------------------------------------

class TypeSelect(disnake.ui.StringSelect):
    def __init__(self, cog: "Roulette"):
        self.cog = cog
        options = [
            disnake.SelectOption(label=label, value=key)
            for key, label in PRIZE_TYPE_LABELS.items()
        ]
        super().__init__(placeholder="Выбери тип приза...", options=options, custom_id="roulette_type_select")

    async def callback(self, inter: disnake.MessageInteraction):
        await inter.response.send_modal(PrizeModal(self.values[0], self.cog))


class EditSelect(disnake.ui.StringSelect):
    def __init__(self, cog: "Roulette", prizes: List[Dict[str, Any]], mode: str):
        self.cog = cog
        self.mode = mode  # "edit" | "delete"
        options = [
            disnake.SelectOption(
                label=f"{p['emoji']} {p['name']}"[:100],
                value=str(p["id"]),
                description=f"вес {p['weight']} • {PRIZE_TYPE_LABELS.get(p['prize_type'], p['prize_type'])}"[:100],
            )
            for p in prizes[:25]
        ]
        placeholder = "Выбери приз для изменения..." if mode == "edit" else "Выбери приз для удаления..."
        super().__init__(placeholder=placeholder, options=options, custom_id=f"roulette_{mode}_select")

    async def callback(self, inter: disnake.MessageInteraction):
        prize_id = int(self.values[0])
        prize = await get_prize(prize_id)
        if not prize:
            await inter.response.send_message("Приз не найден (возможно, уже удалён).", ephemeral=True)
            return

        if self.mode == "edit":
            await inter.response.send_modal(PrizeModal(prize["prize_type"], self.cog, existing=prize))
        else:
            await delete_prize(prize_id)
            embed = await build_panel_embed(inter.guild)
            await inter.response.edit_message(embed=embed, view=PanelView(self.cog))


# ---------------------------------------------------------------------------
# Главная панель
# ---------------------------------------------------------------------------

class PanelView(disnake.ui.View):
    def __init__(self, cog: "Roulette"):
        super().__init__(timeout=300)
        self.cog = cog
        self.add_item(TypeSelect(cog))

    @disnake.ui.button(label="➕ Добавить приз", style=disnake.ButtonStyle.success, row=1)
    async def add_btn(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.send_message(
            "Выбери тип приза в меню выше ⬆️, затем откроется форма добавления.",
            ephemeral=True,
        )

    @disnake.ui.button(label="✏️ Изменить приз", style=disnake.ButtonStyle.primary, row=1)
    async def edit_btn(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        prizes = await get_prizes()
        if not prizes:
            await inter.response.send_message("Пока нет ни одного приза.", ephemeral=True)
            return
        view = disnake.ui.View(timeout=60)
        view.add_item(EditSelect(self.cog, prizes, "edit"))
        await inter.response.send_message("Выбери приз для изменения:", view=view, ephemeral=True)

    @disnake.ui.button(label="🗑️ Удалить приз", style=disnake.ButtonStyle.danger, row=1)
    async def delete_btn(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        prizes = await get_prizes()
        if not prizes:
            await inter.response.send_message("Пока нет ни одного приза.", ephemeral=True)
            return
        view = disnake.ui.View(timeout=60)
        view.add_item(EditSelect(self.cog, prizes, "delete"))
        await inter.response.send_message("Выбери приз для удаления:", view=view, ephemeral=True)

    @disnake.ui.button(label="🔄 Обновить", style=disnake.ButtonStyle.secondary, row=1)
    async def refresh_btn(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        embed = await build_panel_embed(inter.guild)
        await inter.response.edit_message(embed=embed, view=self)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class Roulette(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._initialized = False

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._initialized:
            await init_roulette_tables()
            self._initialized = True

    # ------------------------------------------------------------------
    # /rulet_panel
    # ------------------------------------------------------------------
    @commands.slash_command(name="rulet_panel", description="Панель управления призами рулетки (только админы).")
    async def rulet_panel(self, inter: disnake.ApplicationCommandInteraction):
        if not is_admin(inter):
            await inter.response.send_message("Только администратор может открыть эту панель.", ephemeral=True)
            return
        embed = await build_panel_embed(inter.guild)
        await inter.response.send_message(embed=embed, view=PanelView(self))

    # ------------------------------------------------------------------
    # /ruletka
    # ------------------------------------------------------------------
    @commands.slash_command(name="ruletka", description="Крутить ежедневную рулетку призов!")
    async def ruletka(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer()

        claim = await get_claim(inter.author.id)
        now = int(time.time())
        elapsed = now - claim["last_claim"]

        if elapsed < DAILY_COOLDOWN_SECONDS:
            left = DAILY_COOLDOWN_SECONDS - elapsed
            await inter.edit_original_response(
                content=f"⏳ Рулетка уже прокручена сегодня! Следующая попытка через **{fmt_time_left(left)}**."
            )
            return

        prizes = await get_prizes()
        if not prizes:
            await inter.edit_original_response(
                content="Призы ещё не настроены. Попроси администратора открыть `/rulet_panel`."
            )
            return

        config = load_config()
        color = int(config.get("embed_color", "0x990000"), 16)

        # --- анимация прокрутки ---
        msg_embed = disnake.Embed(title="🎰 Крутим рулетку...", color=color)
        for _ in range(SPIN_FRAMES):
            fake = random.choice(prizes)
            msg_embed.description = f"{fake['emoji']} {fake['name']} ...?"
            await inter.edit_original_response(embed=msg_embed)
            await asyncio.sleep(SPIN_FRAME_DELAY)

        # --- выбор результата ---
        won = pick_weighted(prizes)
        await set_claim(inter.author.id, now)

        result_embed = disnake.Embed(
            title="🎉 Результат рулетки",
            description=f"## {won['emoji']} {won['name']}",
            color=color,
        )
        result_embed.set_author(name=inter.author.display_name, icon_url=inter.author.display_avatar.url)

        if won["prize_type"] == "currency":
            amount = int(won["value"])
            new_balance = await change_balance(inter.author.id, amount)
            verb = "Начислено" if amount >= 0 else "Списано"
            result_embed.add_field(name=verb, value=f"**{amount}** монет", inline=True)
            result_embed.add_field(name="Баланс", value=f"**{new_balance}** монет", inline=True)

        elif won["prize_type"] == "role":
            role = inter.guild.get_role(int(won["value"])) if won["value"] else None
            if role:
                try:
                    await inter.author.add_roles(role, reason="Приз рулетки")
                    result_embed.add_field(name="Выдана роль", value=role.mention, inline=False)
                except disnake.Forbidden:
                    result_embed.add_field(name="⚠️ Ошибка", value="Не удалось выдать роль (нет прав у бота).", inline=False)
            else:
                result_embed.add_field(name="⚠️ Ошибка", value="Роль приза не найдена, обратись к администратору.", inline=False)

        elif won["prize_type"] == "warn":
            await add_warn(inter.author.id, self.bot.user.id, reason="Выговор получен в рулетке")
            result_embed.add_field(name="⚠️ Выговор", value="Зафиксирован в системе предупреждений.", inline=False)
            if won["value"]:
                role = inter.guild.get_role(int(won["value"]))
                if role:
                    try:
                        await inter.author.add_roles(role, reason="Выговор (рулетка)")
                        result_embed.add_field(name="Выдана роль", value=role.mention, inline=False)
                    except disnake.Forbidden:
                        result_embed.add_field(name="⚠️ Ошибка", value="Не удалось выдать роль выговора (нет прав у бота).", inline=False)

        else:  # text
            if won["value"]:
                result_embed.add_field(name="", value=won["value"], inline=False)

        total_weight = sum(p["weight"] for p in prizes) or 1
        chance = won["weight"] / total_weight * 100
        result_embed.set_footer(text=f"Шанс этого приза: {chance:.1f}% • Следующая попытка через 24ч")

        await inter.edit_original_response(embed=result_embed)


def setup(bot: commands.Bot):
    bot.add_cog(Roulette(bot))
