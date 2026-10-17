"""
cogs/checkers.py

Игра в русские/английские шашки (8x8, обязательное взятие, дамки, серии взятий)
для Discord-бота на disnake.

Команды:
    /checkers challenge <opponent>   - вызвать другого игрока на партию
    /checkers move <from> <to>       - сделать ход (например: C3 D4)
    /checkers board                  - показать текущую доску
    /checkers resign                 - сдаться

Не зависит от utils/checks.py или cogs/games.py — самостоятельный модуль.
Просто добавьте "cogs.checkers" в список COGS в main.py.
"""

import os
import json
import disnake
from disnake.ext import commands
from typing import Optional, Dict, Tuple, List

# --------------------------------------------------------------------------
# Конфиг (опционально читаем цвета из config.json, если он рядом с ботом)
# --------------------------------------------------------------------------

def _load_colors() -> Dict[str, int]:
    defaults = {
        "embed_color": 0x990000,
        "success_color": 0x2ecc71,
        "error_color": 0xe74c3c,
        "warning_color": 0xf39c12,
    }
    try:
        cfg_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for key in defaults:
            val = cfg.get(key)
            if val is not None:
                defaults[key] = int(str(val), 16) if isinstance(val, str) and val.startswith("0x") else int(val)
    except Exception:
        pass
    return defaults

COLORS = _load_colors()

# --------------------------------------------------------------------------
# Игровой движок
# --------------------------------------------------------------------------

EMPTY = 0
RED_MAN = 1
RED_KING = 2
BLACK_MAN = -1
BLACK_KING = -2

RED_PIECES = (RED_MAN, RED_KING)
BLACK_PIECES = (BLACK_MAN, BLACK_KING)

PIECE_EMOJI = {
    RED_MAN: "🔴",
    RED_KING: "👑",
    BLACK_MAN: "⚫",
    BLACK_KING: "♛",
}
DARK_EMPTY = "▪️"
LIGHT_EMPTY = "▫️"

Square = Tuple[int, int]  # (row_idx 0..7 top=rank8, col_idx 0..7 A..H)


def is_dark(r: int, c: int) -> bool:
    return (r + c) % 2 == 1


def parse_square(label: str) -> Optional[Square]:
    label = label.strip().upper()
    if len(label) < 2:
        return None
    col_letter = label[0]
    rank_str = label[1:]
    if col_letter < "A" or col_letter > "H":
        return None
    if not rank_str.isdigit():
        return None
    rank = int(rank_str)
    if rank < 1 or rank > 8:
        return None
    c = ord(col_letter) - ord("A")
    r = 8 - rank
    if not is_dark(r, c):
        return None
    return (r, c)


def square_label(sq: Square) -> str:
    r, c = sq
    rank = 8 - r
    return f"{chr(ord('A') + c)}{rank}"


def new_board() -> List[List[int]]:
    board = [[EMPTY] * 8 for _ in range(8)]
    for r in range(8):
        for c in range(8):
            if not is_dark(r, c):
                continue
            if r <= 2:
                board[r][c] = BLACK_MAN
            elif r >= 5:
                board[r][c] = RED_MAN
    return board


class CheckersGame:
    """Состояние одной партии в шашки."""

    def __init__(self, red_id: int, black_id: int):
        self.board = new_board()
        self.red_id = red_id
        self.black_id = black_id
        self.turn = RED_PIECES  # чья очередь: RED_PIECES или BLACK_PIECES
        self.must_continue: Optional[Square] = None  # серия взятий одной фигурой
        self.finished = False
        self.winner_id: Optional[int] = None
        self.last_move_text: str = ""

    # -- служебное --------------------------------------------------------

    def player_id_for(self, side) -> int:
        return self.red_id if side is RED_PIECES else self.black_id

    def side_of(self, user_id: int):
        if user_id == self.red_id:
            return RED_PIECES
        if user_id == self.black_id:
            return BLACK_PIECES
        return None

    def opponent_side(self, side):
        return BLACK_PIECES if side is RED_PIECES else RED_PIECES

    def piece_at(self, sq: Square) -> int:
        r, c = sq
        return self.board[r][c]

    def in_bounds(self, r: int, c: int) -> bool:
        return 0 <= r < 8 and 0 <= c < 8

    def is_own_piece(self, piece: int, side) -> bool:
        return piece in side

    def is_enemy_piece(self, piece: int, side) -> bool:
        enemy = self.opponent_side(side)
        return piece in enemy

    def directions_for(self, piece: int) -> List[Tuple[int, int]]:
        if piece == RED_MAN:
            return [(-1, -1), (-1, 1)]
        if piece == BLACK_MAN:
            return [(1, -1), (1, 1)]
        # короли ходят во все стороны
        return [(-1, -1), (-1, 1), (1, -1), (1, 1)]

    # -- генерация ходов ---------------------------------------------------

    def piece_captures(self, sq: Square) -> List[Square]:
        """Возвращает список клеток, на которые фигура из sq может пойти взятием (за один прыжок)."""
        r, c = sq
        piece = self.board[r][c]
        if piece == EMPTY:
            return []
        side = RED_PIECES if piece in RED_PIECES else BLACK_PIECES
        results = []
        for dr, dc in self.directions_for(piece):
            mr, mc = r + dr, c + dc  # клетка со сбиваемой фигурой
            tr, tc = r + 2 * dr, c + 2 * dc  # клетка приземления
            if not self.in_bounds(tr, tc):
                continue
            mid_piece = self.board[mr][mc]
            if self.is_enemy_piece(mid_piece, side) and self.board[tr][tc] == EMPTY:
                results.append((tr, tc))
        return results

    def piece_simple_moves(self, sq: Square) -> List[Square]:
        r, c = sq
        piece = self.board[r][c]
        if piece == EMPTY:
            return []
        results = []
        for dr, dc in self.directions_for(piece):
            nr, nc = r + dr, c + dc
            if self.in_bounds(nr, nc) and self.board[nr][nc] == EMPTY:
                results.append((nr, nc))
        return results

    def any_capture_available(self, side) -> bool:
        for r in range(8):
            for c in range(8):
                piece = self.board[r][c]
                if piece != EMPTY and self.is_own_piece(piece, side):
                    if self.piece_captures((r, c)):
                        return True
        return False

    def side_has_moves(self, side) -> bool:
        for r in range(8):
            for c in range(8):
                piece = self.board[r][c]
                if piece != EMPTY and self.is_own_piece(piece, side):
                    if self.piece_captures((r, c)) or self.piece_simple_moves((r, c)):
                        return True
        return False

    def side_piece_count(self, side) -> int:
        return sum(1 for row in self.board for p in row if p in side)

    # -- выполнение хода ----------------------------------------------------

    def try_move(self, user_id: int, frm_label: str, to_label: str) -> Tuple[bool, str]:
        """Возвращает (успех, сообщение)."""
        if self.finished:
            return False, "Партия уже завершена."

        side = self.side_of(user_id)
        if side is None:
            return False, "Вы не участвуете в этой партии."
        if side is not self.turn:
            return False, "Сейчас не ваш ход."

        frm = parse_square(frm_label)
        to = parse_square(to_label)
        if frm is None or to is None:
            return False, "Некорректные координаты клетки. Пример: C3 (буква A-H и цифра 1-8, тёмная клетка)."

        piece = self.piece_at(frm)
        if piece == EMPTY or not self.is_own_piece(piece, side):
            return False, "На указанной клетке нет вашей фигуры."

        if self.must_continue is not None and frm != self.must_continue:
            return False, f"Нужно продолжить взятие фигурой на {square_label(self.must_continue)}."

        captures = self.piece_captures(frm)
        must_capture_somewhere = self.any_capture_available(side)

        if to in captures:
            self._execute_capture(frm, to)
            further = self.piece_captures(to)
            promoted = self._maybe_promote(to)
            if further and not promoted:
                self.must_continue = to
                self.last_move_text = f"{square_label(frm)}→{square_label(to)} (взятие, продолжайте бить)"
                return True, "Взятие выполнено. Эта же фигура должна продолжить бить."
            else:
                self.must_continue = None
                self._end_turn(side)
                self.last_move_text = f"{square_label(frm)}→{square_label(to)} (взятие)"
                return True, "Взятие выполнено."

        if self.must_continue is not None:
            return False, f"Нужно продолжить взятие фигурой на {square_label(self.must_continue)}."

        if must_capture_somewhere:
            return False, "Взятие обязательно — есть фигура, которая может бить."

        if to in self.piece_simple_moves(frm):
            self._execute_simple(frm, to)
            self._maybe_promote(to)
            self._end_turn(side)
            self.last_move_text = f"{square_label(frm)}→{square_label(to)}"
            return True, "Ход выполнен."

        return False, "Недопустимый ход."

    def _execute_simple(self, frm: Square, to: Square):
        r1, c1 = frm
        r2, c2 = to
        self.board[r2][c2] = self.board[r1][c1]
        self.board[r1][c1] = EMPTY

    def _execute_capture(self, frm: Square, to: Square):
        r1, c1 = frm
        r2, c2 = to
        mr, mc = (r1 + r2) // 2, (c1 + c2) // 2
        self.board[r2][c2] = self.board[r1][c1]
        self.board[r1][c1] = EMPTY
        self.board[mr][mc] = EMPTY

    def _maybe_promote(self, sq: Square) -> bool:
        r, c = sq
        piece = self.board[r][c]
        if piece == RED_MAN and r == 0:
            self.board[r][c] = RED_KING
            return True
        if piece == BLACK_MAN and r == 7:
            self.board[r][c] = BLACK_KING
            return True
        return False

    def _end_turn(self, moved_side):
        opponent = self.opponent_side(moved_side)
        if self.side_piece_count(opponent) == 0 or not self.side_has_moves(opponent):
            self.finished = True
            self.winner_id = self.player_id_for(moved_side)
            return
        self.turn = opponent

    def resign(self, user_id: int) -> bool:
        side = self.side_of(user_id)
        if side is None or self.finished:
            return False
        self.finished = True
        self.winner_id = self.player_id_for(self.opponent_side(side))
        return True

    # -- рендер --------------------------------------------------------------

    def render(self) -> str:
        lines = ["```", "    A  B  C  D  E  F  G  H"]
        for r in range(8):
            rank = 8 - r
            row_cells = []
            for c in range(8):
                piece = self.board[r][c]
                if piece != EMPTY:
                    row_cells.append(PIECE_EMOJI[piece])
                elif is_dark(r, c):
                    row_cells.append(DARK_EMPTY)
                else:
                    row_cells.append(LIGHT_EMPTY)
            lines.append(f" {rank}  " + " ".join(row_cells))
        lines.append("```")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Discord-обвязка
# --------------------------------------------------------------------------

class ChallengeView(disnake.ui.View):
    def __init__(self, cog: "Checkers", channel_id: int, challenger_id: int, opponent_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.channel_id = channel_id
        self.challenger_id = challenger_id
        self.opponent_id = opponent_id

    async def _guard(self, inter: disnake.MessageInteraction) -> bool:
        if inter.author.id != self.opponent_id:
            await inter.response.send_message("Это приглашение не для вас.", ephemeral=True)
            return False
        return True

    @disnake.ui.button(label="Принять", style=disnake.ButtonStyle.success, emoji="✅")
    async def accept(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await self._guard(inter):
            return
        if self.channel_id in self.cog.games and not self.cog.games[self.channel_id].finished:
            await inter.response.send_message("В этом канале уже идёт партия.", ephemeral=True)
            return
        game = CheckersGame(red_id=self.challenger_id, black_id=self.opponent_id)
        self.cog.games[self.channel_id] = game
        for child in self.children:
            child.disabled = True
        embed = self.cog.build_board_embed(game, footer="Партия началась! Ходит 🔴 (красные).")
        await inter.response.edit_message(
            content=f"⚜️ <@{self.challenger_id}> (🔴) против <@{self.opponent_id}> (⚫)",
            embed=embed,
            view=self,
        )
        self.stop()

    @disnake.ui.button(label="Отклонить", style=disnake.ButtonStyle.danger, emoji="❌")
    async def decline(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await self._guard(inter):
            return
        for child in self.children:
            child.disabled = True
        await inter.response.edit_message(content="Вызов на шашки отклонён.", embed=None, view=self)
        self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class Checkers(commands.Cog):
    """Игра в шашки на сервере."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.games: Dict[int, CheckersGame] = {}

    def build_board_embed(self, game: CheckersGame, footer: str = "") -> disnake.Embed:
        if game.finished:
            color = COLORS["success_color"]
            title = "⚜️ Партия завершена"
            desc = f"{game.render()}\n🏆 Победитель: <@{game.winner_id}>"
        else:
            color = COLORS["embed_color"]
            turn_id = game.player_id_for(game.turn)
            turn_emoji = "🔴" if game.turn is RED_PIECES else "⚫"
            title = "⚜️ Шашки"
            desc = f"{game.render()}\nХод: {turn_emoji} <@{turn_id}>"
            if game.must_continue is not None:
                desc += f"\n⚠️ Обязательное продолжение взятия фигурой **{square_label(game.must_continue)}**"
        embed = disnake.Embed(title=title, description=desc, color=color)
        embed.add_field(name="🔴 Красные", value=f"<@{game.red_id}>", inline=True)
        embed.add_field(name="⚫ Чёрные", value=f"<@{game.black_id}>", inline=True)
        if game.last_move_text:
            embed.add_field(name="Последний ход", value=game.last_move_text, inline=False)
        if footer:
            embed.set_footer(text=footer)
        else:
            embed.set_footer(text="Ход записывается командой /checkers move от:C3 до:D4")
        return embed

    def build_error_embed(self, text: str) -> disnake.Embed:
        return disnake.Embed(description=f"❌ {text}", color=COLORS["error_color"])

    # -- команды ------------------------------------------------------------

    @commands.slash_command(name="checkers", description="Игра в шашки")
    async def checkers(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @checkers.sub_command(name="challenge", description="Вызвать другого игрока на партию в шашки")
    async def challenge(
        self,
        inter: disnake.ApplicationCommandInteraction,
        opponent: disnake.Member = commands.Param(description="С кем сыграть"),
    ):
        if opponent.bot:
            await inter.response.send_message(embed=self.build_error_embed("Нельзя вызвать бота."), ephemeral=True)
            return
        if opponent.id == inter.author.id:
            await inter.response.send_message(embed=self.build_error_embed("Нельзя вызвать самого себя."), ephemeral=True)
            return
        existing = self.games.get(inter.channel_id)
        if existing and not existing.finished:
            await inter.response.send_message(
                embed=self.build_error_embed("В этом канале уже идёт партия. Дождитесь её окончания или используйте `/checkers resign`."),
                ephemeral=True,
            )
            return

        embed = disnake.Embed(
            title="⚜️ Вызов на шашки",
            description=f"{inter.author.mention} вызывает {opponent.mention} на партию в шашки!\n\n{opponent.mention}, принимаете вызов?",
            color=COLORS["warning_color"],
        )
        view = ChallengeView(self, inter.channel_id, inter.author.id, opponent.id)
        await inter.response.send_message(content=opponent.mention, embed=embed, view=view)

    @checkers.sub_command(name="move", description="Сделать ход в шашках")
    async def move(
        self,
        inter: disnake.ApplicationCommandInteraction,
        frm: str = commands.Param(name="от", description="Откуда, например C3"),
        to: str = commands.Param(name="до", description="Куда, например D4"),
    ):
        game = self.games.get(inter.channel_id)
        if game is None or game.finished:
            await inter.response.send_message(
                embed=self.build_error_embed("В этом канале нет активной партии. Начните её через `/checkers challenge`."),
                ephemeral=True,
            )
            return

        ok, message = game.try_move(inter.author.id, frm, to)
        if not ok:
            await inter.response.send_message(embed=self.build_error_embed(message), ephemeral=True)
            return

        footer = message
        embed = self.build_board_embed(game, footer=footer)
        await inter.response.send_message(embed=embed)

        if game.finished:
            del self.games[inter.channel_id]

    @checkers.sub_command(name="board", description="Показать текущую доску")
    async def show_board(self, inter: disnake.ApplicationCommandInteraction):
        game = self.games.get(inter.channel_id)
        if game is None:
            await inter.response.send_message(
                embed=self.build_error_embed("В этом канале нет активной партии."), ephemeral=True
            )
            return
        await inter.response.send_message(embed=self.build_board_embed(game))

    @checkers.sub_command(name="resign", description="Сдаться в текущей партии")
    async def resign(self, inter: disnake.ApplicationCommandInteraction):
        game = self.games.get(inter.channel_id)
        if game is None or game.finished:
            await inter.response.send_message(
                embed=self.build_error_embed("В этом канале нет активной партии."), ephemeral=True
            )
            return
        if not game.resign(inter.author.id):
            await inter.response.send_message(
                embed=self.build_error_embed("Вы не участвуете в этой партии."), ephemeral=True
            )
            return
        embed = self.build_board_embed(game, footer=f"{inter.author.display_name} сдался(-ась).")
        await inter.response.send_message(embed=embed)
        del self.games[inter.channel_id]


def setup(bot: commands.Bot):
    bot.add_cog(Checkers(bot))
