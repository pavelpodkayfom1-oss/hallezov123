"""
=====================================================================
  ИГРОВОЙ МОДУЛЬ Hallez FAMQ — ВСЁ В ОДНОМ ФАЙЛЕ
=====================================================================

Просто положи этот файл в папку cogs/ и добавь "cogs.games" в список
COGS в main.py. Больше НИЧЕГО менять не нужно — ни database.py,
ни utils/checks.py. Файл полностью самодостаточен: сам читает
config.json (для цветов embed'ов) и сам создаёт свою таблицу
экономики в той же базе codex_famq.db.

Команды:
  /balance                      — показать баланс
  /daily                        — забрать ежедневный бонус (500 монет / 24ч)
  /leaderboard                  — топ игроков по балансу
  /givecoins <участник> <кол-во> — [Админ] выдать/списать монеты вручную
  /slots <ставка>                        — игровые автоматы
  /roulette <ставка> <red/black/green/число> — рулетка
  /blackjack <ставка>                    — блэкджек против дилера
  /dice <соперник> <ставка>              — дуэль в кости (PvP)
  /mafia_start [ставка]                  — лобби игры "Мафия"
  /prank_panel                           — [Админ] панель розыгрышей (пугающие ЛС)

=====================================================================
"""

import os
import json
import random
import asyncio

import aiosqlite
import disnake
from disnake.ext import commands
from disnake import ButtonStyle

# ---------------------------------------------------------------
# Пути — файл лежит в cogs/, поднимаемся на уровень выше к корню проекта
# ---------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
CONFIG_PATH = os.path.join(_PROJECT_ROOT, "config.json")
DB_PATH = os.path.join(_PROJECT_ROOT, "codex_famq.db")

CURRENCY = "🪙"
DEFAULT_BALANCE = 1000

JOIN_TIME = 60
NIGHT_TIME = 30
DAY_DISCUSS_TIME = 30
VOTE_TIME = 30
MIN_PLAYERS = 4
MAX_PLAYERS = 12


def fmt(amount: int) -> str:
    return f"{amount:,}".replace(",", " ")


def load_local_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def is_admin(inter: disnake.ApplicationCommandInteraction) -> bool:
    """Проверяет, является ли автор команды администратором по config.json
    (admin_user_ids) или по одной из ролей admin_role_ids, либо имеет
    право администратора сервера."""
    cfg = load_local_config()
    admin_user_ids = set(cfg.get("admin_user_ids", []))
    admin_role_ids = set(cfg.get("admin_role_ids", []))

    if inter.author.id in admin_user_ids:
        return True

    member = inter.author
    if isinstance(member, disnake.Member):
        if member.guild_permissions.administrator:
            return True
        member_role_ids = {r.id for r in member.roles}
        if member_role_ids & admin_role_ids:
            return True

    return False


def get_colors():
    cfg = load_local_config()

    def to_int(val, default):
        try:
            return int(val, 16) if isinstance(val, str) else int(val)
        except Exception:
            return default

    return {
        "main": to_int(cfg.get("embed_color"), 0x990000),
        "success": to_int(cfg.get("success_color"), 0x2ecc71),
        "error": to_int(cfg.get("error_color"), 0xe74c3c),
        "warning": to_int(cfg.get("warning_color"), 0xf39c12),
    }


# =========================================================
#                 ЭКОНОМИКА (своя, встроенная)
# =========================================================

async def _ensure_table(db: aiosqlite.Connection):
    await db.execute("""
    CREATE TABLE IF NOT EXISTS economy (
        user_id INTEGER PRIMARY KEY,
        balance INTEGER NOT NULL DEFAULT 1000,
        last_daily TIMESTAMP,
        total_won INTEGER NOT NULL DEFAULT 0,
        total_lost INTEGER NOT NULL DEFAULT 0
    )
    """)
    await db.commit()


async def get_balance(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await _ensure_table(db)
        async with db.execute("SELECT balance FROM economy WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            await db.execute("INSERT INTO economy (user_id, balance) VALUES (?, ?)", (user_id, DEFAULT_BALANCE))
            await db.commit()
            return DEFAULT_BALANCE
        return row[0]


async def change_balance(user_id: int, delta: int) -> int:
    await get_balance(user_id)  # гарантируем наличие записи
    async with aiosqlite.connect(DB_PATH) as db:
        await _ensure_table(db)
        if delta >= 0:
            await db.execute(
                "UPDATE economy SET balance = balance + ?, total_won = total_won + ? WHERE user_id = ?",
                (delta, delta, user_id)
            )
        else:
            await db.execute(
                "UPDATE economy SET balance = balance + ?, total_lost = total_lost + ? WHERE user_id = ?",
                (delta, -delta, user_id)
            )
        await db.commit()
        async with db.execute("SELECT balance FROM economy WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        return row[0] if row else 0


async def try_claim_daily(user_id: int, amount: int = 500, cooldown_hours: int = 24):
    import time
    now = int(time.time())
    await get_balance(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await _ensure_table(db)
        async with db.execute("SELECT last_daily FROM economy WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        last_daily = row[0] if row else None
        if last_daily:
            try:
                last_ts = int(last_daily)
            except (TypeError, ValueError):
                last_ts = 0
            elapsed = now - last_ts
            cooldown = cooldown_hours * 3600
            if elapsed < cooldown:
                return {"success": False, "seconds_left": cooldown - elapsed}
        await db.execute(
            "UPDATE economy SET balance = balance + ?, last_daily = ? WHERE user_id = ?",
            (amount, now, user_id)
        )
        await db.commit()
        async with db.execute("SELECT balance FROM economy WHERE user_id = ?", (user_id,)) as cur:
            new_row = await cur.fetchone()
        return {"success": True, "balance": new_row[0] if new_row else amount}


async def get_leaderboard(limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        await _ensure_table(db)
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, balance FROM economy ORDER BY balance DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


# =========================================================
#                     БЛЭКДЖЕК
# =========================================================

SUITS = ["♠️", "♥️", "♦️", "♣️"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]


def new_deck():
    deck = [(r, s) for r in RANKS for s in SUITS] * 4
    random.shuffle(deck)
    return deck


def card_value(card):
    rank = card[0]
    if rank in ("J", "Q", "K"):
        return 10
    if rank == "A":
        return 11
    return int(rank)


def hand_value(hand):
    total = sum(card_value(c) for c in hand)
    aces = sum(1 for c in hand if c[0] == "A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def hand_str(hand):
    return " ".join(f"{r}{s}" for r, s in hand)


class BlackjackView(disnake.ui.View):
    def __init__(self, author: disnake.User, bet: int):
        super().__init__(timeout=60)
        self.author = author
        self.bet = bet
        self.deck = new_deck()
        self.player = [self.deck.pop(), self.deck.pop()]
        self.dealer = [self.deck.pop(), self.deck.pop()]
        self.finished = False

    def build_embed(self, reveal_dealer=False, result_text=None):
        colors = get_colors()
        color = colors["main"]
        p_val = hand_value(self.player)
        if reveal_dealer:
            d_display = f"{hand_str(self.dealer)} (**{hand_value(self.dealer)}**)"
        else:
            d_display = f"{self.dealer[0][0]}{self.dealer[0][1]} 🂠"

        embed = disnake.Embed(title="🃏 Блэкджек", color=color)
        embed.add_field(name=f"Ваша рука ({p_val})", value=hand_str(self.player), inline=False)
        embed.add_field(name="Рука дилера", value=d_display, inline=False)
        embed.add_field(name="Ставка", value=f"{CURRENCY} {fmt(self.bet)}", inline=False)
        if result_text:
            embed.description = result_text
            if "выигр" in result_text.lower():
                embed.color = colors["success"]
            elif "проигр" in result_text.lower():
                embed.color = colors["error"]
            else:
                embed.color = colors["warning"]
        embed.set_footer(text=f"Игрок: {self.author.display_name}")
        return embed

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:
        if inter.author.id != self.author.id:
            await inter.response.send_message("❌ Это не ваша игра!", ephemeral=True)
            return False
        return True

    async def end_game(self, inter: disnake.MessageInteraction, result_text: str, payout: int):
        self.finished = True
        for child in self.children:
            child.disabled = True
        if payout != 0:
            await change_balance(self.author.id, payout)
        embed = self.build_embed(reveal_dealer=True, result_text=result_text)
        await inter.response.edit_message(embed=embed, view=self)
        self.stop()

    @disnake.ui.button(label="Взять карту (Hit)", style=ButtonStyle.primary, emoji="🃏")
    async def hit(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if self.finished:
            return
        self.player.append(self.deck.pop())
        val = hand_value(self.player)
        if val > 21:
            await self.end_game(inter, f"💥 Перебор! Вы проиграли {CURRENCY} {fmt(self.bet)}.", -self.bet)
            return
        if val == 21:
            await self.stand.callback(inter)
            return
        embed = self.build_embed()
        await inter.response.edit_message(embed=embed, view=self)

    @disnake.ui.button(label="Хватит (Stand)", style=ButtonStyle.secondary, emoji="✋")
    async def stand(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if self.finished:
            return
        while hand_value(self.dealer) < 17:
            self.dealer.append(self.deck.pop())
        p_val = hand_value(self.player)
        d_val = hand_value(self.dealer)

        if d_val > 21 or p_val > d_val:
            payout = self.bet
            text = f"🎉 Вы выиграли {CURRENCY} {fmt(payout)}!"
        elif p_val == d_val:
            payout = 0
            text = "🤝 Ничья! Ставка возвращена."
        else:
            payout = -self.bet
            text = f"😢 Вы проиграли {CURRENCY} {fmt(self.bet)}."

        await self.end_game(inter, text, payout)


# =========================================================
#                    ДУЭЛЬ В КОСТИ (PvP)
# =========================================================

class DiceDuelView(disnake.ui.View):
    def __init__(self, challenger: disnake.User, opponent: disnake.User, bet: int):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.opponent = opponent
        self.bet = bet

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:
        if inter.author.id != self.opponent.id:
            await inter.response.send_message("❌ Только вызванный игрок может ответить на дуэль!", ephemeral=True)
            return False
        return True

    @disnake.ui.button(label="Принять дуэль", style=ButtonStyle.success, emoji="🎲")
    async def accept(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        colors = get_colors()
        opp_balance = await get_balance(self.opponent.id)
        if opp_balance < self.bet:
            await inter.response.send_message(
                f"❌ У вас недостаточно средств для этой ставки ({CURRENCY} {fmt(self.bet)}).",
                ephemeral=True
            )
            return

        roll1 = random.randint(1, 6)
        roll2 = random.randint(1, 6)
        while roll1 == roll2:
            roll1 = random.randint(1, 6)
            roll2 = random.randint(1, 6)

        for child in self.children:
            child.disabled = True

        winner, loser = (self.challenger, self.opponent) if roll1 > roll2 else (self.opponent, self.challenger)

        await change_balance(winner.id, self.bet)
        await change_balance(loser.id, -self.bet)

        embed = disnake.Embed(title="🎲 Дуэль в кости", color=colors["success"])
        embed.add_field(name=self.challenger.display_name, value=f"🎲 {roll1}", inline=True)
        embed.add_field(name=self.opponent.display_name, value=f"🎲 {roll2}", inline=True)
        embed.add_field(
            name="Результат",
            value=f"🏆 **{winner.display_name}** побеждает и забирает {CURRENCY} {fmt(self.bet)}!",
            inline=False
        )
        await inter.response.edit_message(embed=embed, view=self)
        self.stop()

    @disnake.ui.button(label="Отклонить", style=ButtonStyle.danger, emoji="✖️")
    async def decline(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        colors = get_colors()
        for child in self.children:
            child.disabled = True
        embed = disnake.Embed(
            title="🎲 Дуэль отклонена",
            description=f"{self.opponent.mention} отказался от дуэли.",
            color=colors["error"]
        )
        await inter.response.edit_message(embed=embed, view=self)
        self.stop()


# =========================================================
#              КАЗИНО COG (слоты/рулетка/бж/кости)
# =========================================================

class CasinoCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command(name="balance", description="Показать баланс виртуальной валюты")
    async def balance(self, inter: disnake.ApplicationCommandInteraction, участник: disnake.User = None):
        target = участник or inter.author
        colors = get_colors()
        bal = await get_balance(target.id)
        embed = disnake.Embed(
            title="💰 Баланс",
            description=f"{target.mention} — {CURRENCY} **{fmt(bal)}**",
            color=colors["main"]
        )
        await inter.response.send_message(embed=embed)

    @commands.slash_command(name="daily", description="Забрать ежедневный бонус")
    async def daily(self, inter: disnake.ApplicationCommandInteraction):
        colors = get_colors()
        result = await try_claim_daily(inter.author.id, amount=500)
        if result["success"]:
            embed = disnake.Embed(
                title="🎁 Ежедневный бонус получен!",
                description=f"Вы получили {CURRENCY} **500**.\nБаланс: {CURRENCY} **{fmt(result['balance'])}**",
                color=colors["success"]
            )
        else:
            hours = result["seconds_left"] // 3600
            minutes = (result["seconds_left"] % 3600) // 60
            embed = disnake.Embed(
                title="⏳ Бонус уже получен",
                description=f"Следующий бонус можно будет забрать через **{hours}ч {minutes}м**.",
                color=colors["warning"]
            )
        await inter.response.send_message(embed=embed, ephemeral=not result["success"])

    @commands.slash_command(name="leaderboard", description="Таблица лидеров казино")
    async def leaderboard(self, inter: disnake.ApplicationCommandInteraction):
        colors = get_colors()
        top = await get_leaderboard(limit=10)
        if not top:
            await inter.response.send_message("Пока никто не играл в казино.", ephemeral=True)
            return
        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for i, row in enumerate(top):
            medal = medals[i] if i < 3 else f"`{i + 1}.`"
            member = inter.guild.get_member(row["user_id"]) if inter.guild else None
            name = member.display_name if member else f"<@{row['user_id']}>"
            lines.append(f"{medal} {name} — {CURRENCY} {fmt(row['balance'])}")
        embed = disnake.Embed(title="🏆 Таблица лидеров", description="\n".join(lines), color=colors["main"])
        await inter.response.send_message(embed=embed)

    @commands.slash_command(name="givecoins", description="[Админ] Выдать или списать монеты у участника")
    async def givecoins(
        self,
        inter: disnake.ApplicationCommandInteraction,
        участник: disnake.User,
        количество: int = commands.Param(description="Положительное число — выдать, отрицательное — списать"),
    ):
        colors = get_colors()
        if not is_admin(inter):
            await inter.response.send_message("❌ У вас нет прав для использования этой команды.", ephemeral=True)
            return
        if количество == 0:
            await inter.response.send_message("❌ Количество не может быть равно нулю.", ephemeral=True)
            return

        new_balance = await change_balance(участник.id, количество)

        if количество > 0:
            desc = f"👑 {inter.author.mention} выдал {участник.mention} {CURRENCY} **{fmt(количество)}**."
        else:
            desc = f"👑 {inter.author.mention} списал у {участник.mention} {CURRENCY} **{fmt(-количество)}**."

        embed = disnake.Embed(
            title="💰 Баланс изменён",
            description=f"{desc}\n\nНовый баланс {участник.mention}: {CURRENCY} **{fmt(new_balance)}**",
            color=colors["success"] if количество > 0 else colors["warning"]
        )
        await inter.response.send_message(embed=embed)

    @commands.slash_command(name="slots", description="Крутить игровой автомат")
    async def slots(self, inter: disnake.ApplicationCommandInteraction, ставка: int):
        colors = get_colors()
        if ставка <= 0:
            await inter.response.send_message("❌ Ставка должна быть положительным числом.", ephemeral=True)
            return
        balance = await get_balance(inter.author.id)
        if balance < ставка:
            await inter.response.send_message(f"❌ Недостаточно средств. Ваш баланс: {CURRENCY} {fmt(balance)}", ephemeral=True)
            return

        symbols = ["🍒", "🍋", "🍇", "🔔", "⭐", "7️⃣"]
        weights = [30, 25, 20, 12, 8, 5]
        reels = [random.choices(symbols, weights=weights, k=1)[0] for _ in range(3)]

        if reels[0] == reels[1] == reels[2]:
            multiplier = {"🍒": 3, "🍋": 4, "🍇": 5, "🔔": 8, "⭐": 12, "7️⃣": 25}[reels[0]]
            payout = ставка * multiplier
            result_text = f"🎉 ДЖЕКПОТ! Три {reels[0]} подряд! Выигрыш: {CURRENCY} **{fmt(payout)}** (x{multiplier})"
            color = colors["success"]
        elif reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
            payout = int(ставка * 1.5)
            result_text = f"✨ Две одинаковые! Выигрыш: {CURRENCY} **{fmt(payout)}** (x1.5)"
            color = colors["warning"]
        else:
            payout = -ставка
            result_text = f"😢 Пусто. Вы проиграли {CURRENCY} **{fmt(ставка)}**"
            color = colors["error"]

        await change_balance(inter.author.id, payout)
        new_balance = await get_balance(inter.author.id)

        embed = disnake.Embed(title="🎰 Игровой автомат", color=color)
        embed.add_field(name="Барабаны", value=f"# {reels[0]} | {reels[1]} | {reels[2]}", inline=False)
        embed.add_field(name="Результат", value=result_text, inline=False)
        embed.set_footer(text=f"Баланс: {fmt(new_balance)} {CURRENCY}")
        await inter.response.send_message(embed=embed)

    @commands.slash_command(name="roulette", description="Сыграть в рулетку")
    async def roulette(
        self,
        inter: disnake.ApplicationCommandInteraction,
        ставка: int,
        выбор: str = commands.Param(description="red/black/green или число 0-36"),
    ):
        colors = get_colors()
        if ставка <= 0:
            await inter.response.send_message("❌ Ставка должна быть положительным числом.", ephemeral=True)
            return
        balance = await get_balance(inter.author.id)
        if balance < ставка:
            await inter.response.send_message(f"❌ Недостаточно средств. Ваш баланс: {CURRENCY} {fmt(balance)}", ephemeral=True)
            return

        red_numbers = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
        choice_raw = выбор.strip().lower()

        winning_number = random.randint(0, 36)
        if winning_number == 0:
            winning_color = "green"
        elif winning_number in red_numbers:
            winning_color = "red"
        else:
            winning_color = "black"

        color_map = {"red": "🔴 Красное", "black": "⚫ Чёрное", "green": "🟢 Зелёное (0)"}

        payout = -ставка
        bet_desc = choice_raw
        if choice_raw in ("red", "красное", "к"):
            bet_desc = "red"
            if winning_color == "red":
                payout = ставка
        elif choice_raw in ("black", "чёрное", "черное", "ч"):
            bet_desc = "black"
            if winning_color == "black":
                payout = ставка
        elif choice_raw in ("green", "зелёное", "зеленое", "z"):
            bet_desc = "green"
            if winning_color == "green":
                payout = ставка * 14
        elif choice_raw.isdigit() and 0 <= int(choice_raw) <= 36:
            bet_desc = f"число {choice_raw}"
            if int(choice_raw) == winning_number:
                payout = ставка * 36
        else:
            await inter.response.send_message(
                "❌ Некорректная ставка. Укажите `red`, `black`, `green` или число от 0 до 36.", ephemeral=True
            )
            return

        await change_balance(inter.author.id, payout)
        new_balance = await get_balance(inter.author.id)

        won = payout > 0
        embed = disnake.Embed(title="🎡 Рулетка", color=colors["success"] if won else colors["error"])
        embed.add_field(name="Выпало", value=f"**{winning_number}** — {color_map[winning_color]}", inline=False)
        embed.add_field(name="Ваша ставка", value=f"{bet_desc} • {CURRENCY} {fmt(ставка)}", inline=False)
        if won:
            embed.add_field(name="Результат", value=f"🎉 Выигрыш: {CURRENCY} **{fmt(payout)}**", inline=False)
        else:
            embed.add_field(name="Результат", value=f"😢 Проигрыш: {CURRENCY} **{fmt(ставка)}**", inline=False)
        embed.set_footer(text=f"Баланс: {fmt(new_balance)} {CURRENCY}")
        await inter.response.send_message(embed=embed)

    @commands.slash_command(name="blackjack", description="Сыграть в блэкджек против дилера")
    async def blackjack(self, inter: disnake.ApplicationCommandInteraction, ставка: int):
        if ставка <= 0:
            await inter.response.send_message("❌ Ставка должна быть положительным числом.", ephemeral=True)
            return
        balance = await get_balance(inter.author.id)
        if balance < ставка:
            await inter.response.send_message(f"❌ Недостаточно средств. Ваш баланс: {CURRENCY} {fmt(balance)}", ephemeral=True)
            return

        view = BlackjackView(inter.author, ставка)
        p_val = hand_value(view.player)

        if p_val == 21:
            while hand_value(view.dealer) < 17:
                view.dealer.append(view.deck.pop())
            d_val = hand_value(view.dealer)
            if d_val == 21:
                payout = 0
                text = "🤝 У обоих блэкджек — ничья!"
            else:
                payout = int(ставка * 1.5)
                text = f"🃏 БЛЭКДЖЕК! Выигрыш: {CURRENCY} **{fmt(payout)}** (x1.5)"
            view.finished = True
            for child in view.children:
                child.disabled = True
            if payout != 0:
                await change_balance(inter.author.id, payout)
            embed = view.build_embed(reveal_dealer=True, result_text=text)
            await inter.response.send_message(embed=embed, view=view)
            return

        embed = view.build_embed()
        await inter.response.send_message(embed=embed, view=view)

    @commands.slash_command(name="dice", description="Вызвать другого игрока на дуэль в кости")
    async def dice(self, inter: disnake.ApplicationCommandInteraction, соперник: disnake.User, ставка: int):
        colors = get_colors()
        if ставка <= 0:
            await inter.response.send_message("❌ Ставка должна быть положительным числом.", ephemeral=True)
            return
        if соперник.id == inter.author.id:
            await inter.response.send_message("❌ Нельзя вызвать самого себя!", ephemeral=True)
            return
        if соперник.bot:
            await inter.response.send_message("❌ Нельзя вызвать бота!", ephemeral=True)
            return

        challenger_balance = await get_balance(inter.author.id)
        if challenger_balance < ставка:
            await inter.response.send_message(f"❌ Недостаточно средств. Ваш баланс: {CURRENCY} {fmt(challenger_balance)}", ephemeral=True)
            return

        view = DiceDuelView(inter.author, соперник, ставка)
        embed = disnake.Embed(
            title="🎲 Вызов на дуэль!",
            description=(
                f"{inter.author.mention} вызывает {соперник.mention} на дуэль в кости!\n\n"
                f"💰 **Ставка:** {CURRENCY} {fmt(ставка)}\n"
                f"🎲 Побеждает тот, у кого выпадет больше очков."
            ),
            color=colors["warning"]
        )
        await inter.response.send_message(content=соперник.mention, embed=embed, view=view)


# =========================================================
#                          МАФИЯ
# =========================================================

ACTIVE_GAMES = {}

ROLE_NAMES = {
    "mafia": "🔪 Мафия",
    "doctor": "🩺 Доктор",
    "sheriff": "🕵️ Шериф",
    "civilian": "👤 Мирный житель",
}

ROLE_DESCRIPTIONS = {
    "mafia": "Ночью выбирайте жертву вместе с другими мафиози. Ваша цель — сравняться числом с мирными.",
    "doctor": "Ночью выбирайте, кого спасти от возможного убийства мафии.",
    "sheriff": "Ночью выбирайте, кого проверить — бот скажет, мафия это или нет.",
    "civilian": "У вас нет особых способностей. Ваша цель — вычислить и повесить мафию днём.",
}


class Player:
    def __init__(self, member: disnake.Member):
        self.member = member
        self.role = None
        self.alive = True

    @property
    def mention(self):
        return self.member.mention

    @property
    def name(self):
        return self.member.display_name


def assign_roles(players):
    n = len(players)
    mafia_count = max(1, n // 4)
    shuffled = players[:]
    random.shuffle(shuffled)

    for p in shuffled[:mafia_count]:
        p.role = "mafia"
    rest = shuffled[mafia_count:]

    if rest:
        rest[0].role = "doctor"
    if len(rest) > 1:
        rest[1].role = "sheriff"
    for p in rest[2:]:
        p.role = "civilian"


class JoinView(disnake.ui.View):
    def __init__(self, game: "MafiaGame"):
        super().__init__(timeout=JOIN_TIME + 5)
        self.game = game

    @disnake.ui.button(label="Присоединиться", style=ButtonStyle.success, emoji="🙋")
    async def join(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if self.game.started:
            await inter.response.send_message("❌ Игра уже началась.", ephemeral=True)
            return
        if any(p.member.id == inter.author.id for p in self.game.players):
            await inter.response.send_message("Вы уже в игре!", ephemeral=True)
            return
        if len(self.game.players) >= MAX_PLAYERS:
            await inter.response.send_message("❌ Лобби заполнено.", ephemeral=True)
            return

        if self.game.bet > 0:
            balance = await get_balance(inter.author.id)
            if balance < self.game.bet:
                await inter.response.send_message(
                    f"❌ Для участия нужно {CURRENCY} {fmt(self.game.bet)}, у вас {CURRENCY} {fmt(balance)}.",
                    ephemeral=True
                )
                return
            await change_balance(inter.author.id, -self.game.bet)

        self.game.players.append(Player(inter.author))
        embed = self.game.build_lobby_embed()
        await inter.response.edit_message(embed=embed, view=self)

    @disnake.ui.button(label="Начать игру", style=ButtonStyle.primary, emoji="▶️")
    async def start(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if inter.author.id != self.game.host_id:
            await inter.response.send_message("❌ Только создатель лобби может начать игру.", ephemeral=True)
            return
        if len(self.game.players) < MIN_PLAYERS:
            await inter.response.send_message(f"❌ Нужно минимум {MIN_PLAYERS} игроков (сейчас {len(self.game.players)}).", ephemeral=True)
            return
        self.game.started = True
        for child in self.children:
            child.disabled = True
        await inter.response.edit_message(view=self)
        self.stop()
        await self.game.run()


class MafiaGame:
    def __init__(self, bot: commands.Bot, channel: disnake.TextChannel, host: disnake.Member, bet: int = 0):
        self.bot = bot
        self.channel = channel
        self.host_id = host.id
        self.bet = bet
        self.players: list[Player] = []
        self.started = False
        self.round = 0

    def build_lobby_embed(self):
        colors = get_colors()
        names = "\n".join(f"• {p.mention}" for p in self.players) or "_пока никого_"
        desc = (
            f"Нажмите **Присоединиться**, чтобы вступить в игру.\n"
            f"Минимум игроков: **{MIN_PLAYERS}**, максимум: **{MAX_PLAYERS}**.\n\n"
            f"**Игроки ({len(self.players)}):**\n{names}"
        )
        if self.bet > 0:
            desc += f"\n\n💰 Взнос за участие: {CURRENCY} {fmt(self.bet)} (победители делят банк)"
        embed = disnake.Embed(title="🎭 Мафия — сбор игроков", description=desc, color=colors["main"])
        embed.set_footer(text=f"Создатель: {self.players[0].name if self.players else '???'}")
        return embed

    def alive_players(self):
        return [p for p in self.players if p.alive]

    def alive_mafia(self):
        return [p for p in self.alive_players() if p.role == "mafia"]

    def alive_town(self):
        return [p for p in self.alive_players() if p.role != "mafia"]

    def get_player(self, user_id):
        for p in self.players:
            if p.member.id == user_id:
                return p
        return None

    def check_winner(self):
        mafia = len(self.alive_mafia())
        town = len(self.alive_town())
        if mafia == 0:
            return "town"
        if mafia >= town:
            return "mafia"
        return None

    async def send(self, **kwargs):
        return await self.channel.send(**kwargs)

    async def run(self):
        colors = get_colors()
        try:
            assign_roles(self.players)

            role_dm_failed = []
            for p in self.players:
                try:
                    embed = disnake.Embed(
                        title=f"Ваша роль: {ROLE_NAMES[p.role]}",
                        description=ROLE_DESCRIPTIONS[p.role],
                        color=colors["main"]
                    )
                    if p.role == "mafia":
                        teammates = [t.mention for t in self.players if t.role == "mafia" and t != p]
                        if teammates:
                            embed.add_field(name="Ваши сообщники", value=", ".join(teammates))
                    await p.member.send(embed=embed)
                except disnake.Forbidden:
                    role_dm_failed.append(p.mention)

            start_embed = disnake.Embed(
                title="🎭 Игра началась!",
                description=(
                    f"Игроков: **{len(self.players)}**\n"
                    f"Мафия: **{len(self.alive_mafia())}**\n\n"
                    "Роли разосланы в личные сообщения. Проверьте ЛС от бота!"
                ),
                color=colors["success"]
            )
            if role_dm_failed:
                start_embed.add_field(
                    name="⚠️ Не удалось отправить роль в ЛС",
                    value=", ".join(role_dm_failed) + "\n(включите личные сообщения от участников сервера)"
                )
            await self.send(embed=start_embed)
            await asyncio.sleep(3)

            while True:
                self.round += 1
                winner = await self.night_phase()
                if winner:
                    await self.announce_winner(winner)
                    return
                winner = await self.day_phase()
                if winner:
                    await self.announce_winner(winner)
                    return
        finally:
            ACTIVE_GAMES.pop(self.channel.id, None)

    async def night_phase(self):
        colors = get_colors()
        await self.send(embed=disnake.Embed(
            title=f"🌙 Ночь {self.round}",
            description="Город засыпает... Мафия, доктор и шериф совершают свои действия.",
            color=colors["main"]
        ))

        mafia_players = self.alive_mafia()
        doctor = next((p for p in self.alive_players() if p.role == "doctor"), None)
        sheriff = next((p for p in self.alive_players() if p.role == "sheriff"), None)

        mafia_target = await self.collect_night_action(mafia_players, "Выберите жертву", exclude_self=False, action_key="kill")
        doctor_target = None
        if doctor:
            doctor_target = await self.collect_night_action([doctor], "Кого вы хотите спасти этой ночью?", exclude_self=False, action_key="save")
        sheriff_target = None
        if sheriff:
            sheriff_target = await self.collect_night_action([sheriff], "Кого вы хотите проверить этой ночью?", exclude_self=True, action_key="check")

        if sheriff and sheriff_target:
            is_mafia = sheriff_target.role == "mafia"
            try:
                await sheriff.member.send(
                    f"🕵️ Результат проверки: **{sheriff_target.name}** — "
                    + ("🔴 состоит в мафии!" if is_mafia else "🟢 не состоит в мафии.")
                )
            except disnake.Forbidden:
                pass

        colors = get_colors()
        if mafia_target and mafia_target == doctor_target:
            await self.send(embed=disnake.Embed(
                title="☀️ Рассвет",
                description=f"Этой ночью мафия напала на **{mafia_target.name}**, но доктор успел его спасти! Никто не погиб.",
                color=colors["success"]
            ))
        elif mafia_target:
            mafia_target.alive = False
            await self.send(embed=disnake.Embed(
                title="☀️ Рассвет",
                description=f"Этой ночью был убит **{mafia_target.name}** ({ROLE_NAMES[mafia_target.role]}).",
                color=colors["error"]
            ))
        else:
            await self.send(embed=disnake.Embed(
                title="☀️ Рассвет",
                description="Мафия не смогла определиться с жертвой. Этой ночью никто не пострадал.",
                color=colors["warning"]
            ))

        return self.check_winner()

    async def collect_night_action(self, actors, prompt, exclude_self, action_key):
        if not actors:
            return None

        candidates = self.alive_players()
        votes = {}

        async def ask(actor: Player):
            options_pool = [c for c in candidates if not (exclude_self and c.member.id == actor.member.id)]
            if action_key == "kill":
                options_pool = [c for c in options_pool if c.role != "mafia"]
            if not options_pool:
                return
            view = disnake.ui.View(timeout=NIGHT_TIME)
            select = disnake.ui.StringSelect(
                placeholder=prompt,
                options=[disnake.SelectOption(label=c.name, value=str(c.member.id)) for c in options_pool[:25]]
            )
            done = asyncio.Event()

            async def callback(select_inter: disnake.MessageInteraction):
                if select_inter.author.id != actor.member.id:
                    await select_inter.response.send_message("Это не ваш выбор.", ephemeral=True)
                    return
                chosen_id = int(select.values[0])
                votes[actor.member.id] = chosen_id
                await select_inter.response.edit_message(content="✅ Выбор принят.", view=None)
                done.set()

            select.callback = callback
            view.add_item(select)
            try:
                await actor.member.send(content=f"**{prompt}**", view=view)
            except disnake.Forbidden:
                return
            try:
                await asyncio.wait_for(done.wait(), timeout=NIGHT_TIME)
            except asyncio.TimeoutError:
                pass

        await asyncio.gather(*(ask(a) for a in actors))

        if not votes:
            return None

        tally = {}
        for target_id in votes.values():
            tally[target_id] = tally.get(target_id, 0) + 1
        best_id = max(tally, key=tally.get)
        return self.get_player(best_id)

    async def day_phase(self):
        colors = get_colors()
        alive = self.alive_players()
        names = "\n".join(f"• {p.mention} ({p.name})" for p in alive)
        await self.send(embed=disnake.Embed(
            title=f"🗣️ День {self.round} — обсуждение",
            description=f"У вас {DAY_DISCUSS_TIME} секунд, чтобы обсудить, кто может быть мафией.\n\n**Живые игроки:**\n{names}",
            color=colors["warning"]
        ))
        await asyncio.sleep(DAY_DISCUSS_TIME)

        view = VoteView(self, alive)
        vote_msg = await self.send(embed=disnake.Embed(
            title="🗳️ Голосование",
            description="Выберите, кого хотите повесить как подозреваемого в мафии.",
            color=colors["main"]
        ), view=view)
        await asyncio.sleep(VOTE_TIME)
        view.stop_voting = True
        for child in view.children:
            child.disabled = True
        try:
            await vote_msg.edit(view=view)
        except disnake.HTTPException:
            pass

        if not view.votes:
            await self.send(embed=disnake.Embed(
                title="🤷 Голосование не состоялось",
                description="Никто не проголосовал. Город остаётся как есть.",
                color=colors["warning"]
            ))
            return self.check_winner()

        tally = {}
        for target_id in view.votes.values():
            tally[target_id] = tally.get(target_id, 0) + 1
        max_votes = max(tally.values())
        top = [uid for uid, v in tally.items() if v == max_votes]

        if len(top) > 1:
            await self.send(embed=disnake.Embed(
                title="⚖️ Ничья в голосовании",
                description="Голоса разделились поровну. Сегодня никого не повесили.",
                color=colors["warning"]
            ))
            return self.check_winner()

        hanged = self.get_player(top[0])
        hanged.alive = False
        await self.send(embed=disnake.Embed(
            title="⚰️ Приговор приведён в исполнение",
            description=f"Город решил повесить **{hanged.name}**.\nЭто был **{ROLE_NAMES[hanged.role]}**!",
            color=colors["error"] if hanged.role != "mafia" else colors["success"]
        ))
        return self.check_winner()

    async def announce_winner(self, winner):
        colors = get_colors()
        pool = self.bet * len(self.players)
        if winner == "mafia":
            winners = [p for p in self.players if p.role == "mafia"]
            title = "🔪 Победа мафии!"
            desc = "Мафии удалось захватить город."
        else:
            winners = [p for p in self.alive_players() if p.role != "mafia"] or [p for p in self.players if p.role != "mafia"]
            title = "🏙️ Победа мирных жителей!"
            desc = "Город очищен от мафии!"

        payout_line = ""
        if pool > 0 and winners:
            share = pool // len(winners)
            for w in winners:
                await change_balance(w.member.id, share)
            payout_line = f"\n\n💰 Банк {CURRENCY} {fmt(pool)} разделён между победителями: {CURRENCY} {fmt(share)} каждому."

        roles_reveal = "\n".join(f"• {p.name} — {ROLE_NAMES[p.role]}" for p in self.players)
        embed = disnake.Embed(
            title=title,
            description=f"{desc}{payout_line}\n\n**Роли участников:**\n{roles_reveal}",
            color=colors["success"] if winner == "town" else colors["error"]
        )
        await self.send(embed=embed)


class VoteView(disnake.ui.View):
    def __init__(self, game: MafiaGame, alive_players):
        super().__init__(timeout=VOTE_TIME + 5)
        self.game = game
        self.votes = {}
        self.stop_voting = False
        options = [disnake.SelectOption(label=p.name, value=str(p.member.id)) for p in alive_players[:25]]
        select = disnake.ui.StringSelect(placeholder="Выберите подозреваемого...", options=options)
        select.callback = self.vote_callback
        self.add_item(select)

    async def vote_callback(self, inter: disnake.MessageInteraction):
        if self.stop_voting:
            await inter.response.send_message("⏰ Голосование уже закончилось.", ephemeral=True)
            return
        voter = self.game.get_player(inter.author.id)
        if not voter or not voter.alive:
            await inter.response.send_message("❌ Только живые участники игры могут голосовать.", ephemeral=True)
            return
        target_id = int(self.children[0].values[0])
        self.votes[inter.author.id] = target_id
        target = self.game.get_player(target_id)
        await inter.response.send_message(f"✅ Ваш голос против **{target.name}** учтён.", ephemeral=True)


class MafiaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command(name="mafia_start", description="Создать лобби игры в Мафию")
    async def mafia_start(
        self,
        inter: disnake.ApplicationCommandInteraction,
        ставка: int = commands.Param(default=0, description="Взнос за участие (0 — играть без ставок)"),
    ):
        colors = get_colors()
        if inter.channel.id in ACTIVE_GAMES:
            await inter.response.send_message("❌ В этом канале уже идёт игра в Мафию.", ephemeral=True)
            return
        if ставка < 0:
            await inter.response.send_message("❌ Ставка не может быть отрицательной.", ephemeral=True)
            return

        game = MafiaGame(self.bot, inter.channel, inter.author, bet=ставка)

        if ставка > 0:
            balance = await get_balance(inter.author.id)
            if balance < ставка:
                await inter.response.send_message(f"❌ Недостаточно средств для взноса {CURRENCY} {fmt(ставка)}.", ephemeral=True)
                return
            await change_balance(inter.author.id, -ставка)
        game.players.append(Player(inter.author))

        ACTIVE_GAMES[inter.channel.id] = game
        view = JoinView(game)
        embed = game.build_lobby_embed()
        await inter.response.send_message(embed=embed, view=view)

        await asyncio.sleep(JOIN_TIME)
        if not game.started:
            if len(game.players) < MIN_PLAYERS:
                for child in view.children:
                    child.disabled = True
                try:
                    await inter.edit_original_response(view=view)
                except disnake.HTTPException:
                    pass
                await inter.channel.send(embed=disnake.Embed(
                    title="❌ Игра отменена",
                    description=f"Недостаточно игроков (собрано {len(game.players)} из {MIN_PLAYERS}).",
                    color=colors["error"]
                ))
                if ставка > 0:
                    for p in game.players:
                        await change_balance(p.member.id, ставка)
                ACTIVE_GAMES.pop(inter.channel.id, None)
            else:
                game.started = True
                for child in view.children:
                    child.disabled = True
                try:
                    await inter.edit_original_response(view=view)
                except disnake.HTTPException:
                    pass
                await game.run()


# =========================================================
#                  ПАНЕЛЬ РОЗЫГРЫШЕЙ (ПРАНК)
# =========================================================

PRANK_TEMPLATES = {
    "ghost": {
        "label": "Призрак за спиной",
        "emoji": "👻",
        "title": "👻 Ты не один...",
        "desc": "Тссс... кто-то стоит прямо у тебя за спиной уже 3 минуты и молча наблюдает.",
    },
    "curse": {
        "label": "Древнее проклятие",
        "emoji": "🔮",
        "title": "🔮 Древнее проклятие активировано",
        "desc": "Согласно легенде, тот, кто откроет это сообщение — обречён...",
    },
    "watcher": {
        "label": "За тобой следят",
        "emoji": "👁️",
        "title": "👁️ За тобой следят",
        "desc": "Кто-то в семье прямо сейчас думает о тебе... и наблюдает за каждым твоим шагом.",
    },
    "doom": {
        "label": "Обратный отсчёт",
        "emoji": "⏰",
        "title": "⏰ Обратный отсчёт начался",
        "desc": "До конца отсчёта осталось: 3... 2... 1...",
    },
    "secret": {
        "label": "У нас есть секрет",
        "emoji": "🕵️",
        "title": "🕵️ У нас есть секрет о тебе",
        "desc": "Мы знаем кое-что о тебе. Кое-что, о чём ты никому не рассказывал...",
    },
    "mirror": {
        "label": "Не смотри в зеркало",
        "emoji": "🪞",
        "title": "🪞 Не смотри в зеркало сегодня в полночь",
        "desc": "Говорят, что-то может посмотреть в ответ...",
    },
    "timer": {
        "label": "Таймер 5 минут",
        "emoji": "⏳",
        "title": "⏳ У тебя есть 5:00",
        "desc": "Отсчёт уже пошёл: **05:00**\n\nЧто произойдёт, когда таймер закончится? Узнаешь сам...",
    },
}

REVEAL_DELAY_SECONDS = 120
REVEAL_MESSAGE = "😄🎭"


class PrankTemplateView(disnake.ui.View):
    def __init__(self, host_id: int, target: disnake.User):
        super().__init__(timeout=120)
        self.host_id = host_id
        self.target = target

        select = disnake.ui.StringSelect(
            placeholder="Выберите шаблон сообщения...",
            options=[
                disnake.SelectOption(label=t["label"], value=key, emoji=t["emoji"])
                for key, t in PRANK_TEMPLATES.items()
            ]
        )
        select.callback = self.on_select
        self.add_item(select)

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:
        if inter.author.id != self.host_id:
            await inter.response.send_message("❌ Это не ваша панель.", ephemeral=True)
            return False
        return True

    async def on_select(self, inter: disnake.MessageInteraction):
        colors = get_colors()
        cfg = load_local_config()
        bot_name = cfg.get("bot_name", "Hallez FAMQ")

        template_key = self.children[0].values[0]
        template = PRANK_TEMPLATES[template_key]

        dm_embed = disnake.Embed(
            title=template["title"],
            description=template["desc"],
            color=colors["error"]
        )
        dm_embed.set_footer(text=bot_name)

        for child in self.children:
            child.disabled = True

        try:
            await self.target.send(embed=dm_embed)
            sent = True
        except disnake.Forbidden:
            sent = False

        if sent:
            asyncio.create_task(self._send_delayed_reveal(self.target))
            minutes = REVEAL_DELAY_SECONDS // 60
            seconds = REVEAL_DELAY_SECONDS % 60
            delay_str = f"{minutes} мин" + (f" {seconds} сек" if seconds else "")
            result_embed = disnake.Embed(
                title="✅ Розыгрыш отправлен!",
                description=(
                    f"Сообщение «{template['title']}» отправлено {self.target.mention} в личные сообщения.\n"
                    f"Разгадка (эмодзи) придёт автоматически через {delay_str}."
                ),
                color=colors["success"]
            )
        else:
            result_embed = disnake.Embed(
                title="⚠️ Не удалось отправить",
                description=f"{self.target.mention} закрыл(а) личные сообщения от участников сервера.",
                color=colors["warning"]
            )

        await inter.response.edit_message(embed=result_embed, view=self)
        self.stop()

    @staticmethod
    async def _send_delayed_reveal(target: disnake.User):
        await asyncio.sleep(REVEAL_DELAY_SECONDS)
        try:
            await target.send(REVEAL_MESSAGE)
        except disnake.Forbidden:
            pass


class PrankUserSelectView(disnake.ui.View):
    def __init__(self, host_id: int):
        super().__init__(timeout=120)
        self.host_id = host_id

        select = disnake.ui.UserSelect(placeholder="Выберите участника для розыгрыша...")
        select.callback = self.on_select
        self.add_item(select)

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:
        if inter.author.id != self.host_id:
            await inter.response.send_message("❌ Это не ваша панель.", ephemeral=True)
            return False
        return True

    async def on_select(self, inter: disnake.MessageInteraction):
        colors = get_colors()
        target = self.children[0].values[0]

        if target.bot:
            await inter.response.send_message("❌ Нельзя разыграть бота!", ephemeral=True)
            return
        if target.id == inter.author.id:
            await inter.response.send_message("❌ Нельзя разыграть самого себя (хотя было бы забавно).", ephemeral=True)
            return

        view = PrankTemplateView(self.host_id, target)
        embed = disnake.Embed(
            title="🎭 Панель розыгрышей",
            description=f"Цель выбрана: {target.mention}\n\nТеперь выберите шаблон пугающего сообщения:",
            color=colors["main"]
        )
        await inter.response.edit_message(embed=embed, view=view)


class PrankCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command(name="prank_panel", description="[Админ] Панель розыгрышей — отправить участнику пугающее сообщение в ЛС")
    async def prank_panel(self, inter: disnake.ApplicationCommandInteraction):
        if not is_admin(inter):
            await inter.response.send_message("❌ У вас нет прав для использования этой команды.", ephemeral=True)
            return

        colors = get_colors()
        embed = disnake.Embed(
            title="🎭 Панель розыгрышей",
            description="Выберите участника, которого хотите разыграть — бот отправит ему шуточное пугающее сообщение в личные сообщения.",
            color=colors["main"]
        )
        view = PrankUserSelectView(inter.author.id)
        await inter.response.send_message(embed=embed, view=view, ephemeral=True)


# =========================================================
#                        SETUP
# =========================================================

def setup(bot: commands.Bot):
    bot.add_cog(CasinoCog(bot))
    bot.add_cog(MafiaCog(bot))
    bot.add_cog(PrankCog(bot))

