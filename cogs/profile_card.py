"""
Карточка участника картинкой: /карточка-фото [участник]
Ранг берётся из ролей Discord (если роли настроены в /центр → Ранги), иначе из базы.
Если твоя старая команда профиля называется так же — поменяй name= ниже.
"""
import asyncio
from datetime import datetime, timezone
from typing import Optional

import disnake
from disnake.ext import commands

import database
from utils import famq_core as core
from utils.card_image import render_card


def _accent():
    c = core.color("main")
    return ((c >> 16) & 255, (c >> 8) & 255, c & 255)


class ProfileCard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command(name="карточка-фото", description="🪪 Карточка участника картинкой")
    @commands.guild_only()
    async def card(
        self, inter: disnake.ApplicationCommandInteraction,
        member: Optional[disnake.Member] = commands.Param(name="участник", description="Чья карточка (по умолчанию ваша)",
                                                          default=None),
    ):
        await inter.response.defer()
        m = member or inter.author
        row = await database.get_member(m.id)
        role_rank = core.member_rank(m)
        rank = role_rank or (row["rank"] if row else 0)
        nick, static = core.parse_nick(m.display_name)
        if row:
            nick = row["nick"] or nick
            static = row["static_id"] or static

        balance = await database.get_balance(m.id)
        total = await core.voice_seconds(m.id)
        week = await core.voice_seconds(m.id, days=7)
        warns = len(await database.get_active_warns(m.id))

        joined = None
        if row and row.get("joined_at"):
            try:
                joined = datetime.strptime(str(row["joined_at"])[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except Exception:
                joined = None
        joined = joined or m.joined_at
        days = (datetime.now(timezone.utc) - joined).days if joined else 0

        badges = []
        if rank and rank >= core.max_rank():
            badges.append("Старший состав")
        if days >= 180:
            badges.append("Ветеран")
        elif days >= 30:
            badges.append("Свой человек")
        if total / 3600 >= 100:
            badges.append("Голос семьи")
        elif total / 3600 >= 20:
            badges.append("Активный")
        if warns == 0 and days >= 30:
            badges.append("Чистая история")
        if balance >= 10000:
            badges.append("Богач")
        if m.premium_since:
            badges.append("Бустер")

        cfg = core.load_config()
        data = {
            "name": nick,
            "subtitle": f"{core.rank_name(rank) if rank else 'Не в семье'} · {cfg.get('bot_name', 'Hallez FAMQ')}",
            "rank": rank, "max_rank": core.max_rank(), "rank_name": core.rank_name(rank) if rank else "—",
            "static": f"Static ID: {static}" + (f"  ·  выговоров: {warns}" if warns else ""),
            "status": core.pause_kind(m),
            "stats": [
                ("Баланс", f"{balance:,}".replace(",", " ")),
                ("Войс всего", f"{total / 3600:.1f} ч"),
                ("Войс 7 дн.", f"{week / 3600:.1f} ч"),
                ("В семье", f"{days} дн."),
            ],
            "badges": badges, "accent": _accent(),
            "footer": f"{cfg.get('bot_name', 'Hallez FAMQ')} • Majestic RP",
        }
        try:
            avatar = await m.display_avatar.replace(size=256, format="png").read()
        except Exception:
            avatar = None
        png = await asyncio.get_running_loop().run_in_executor(None, render_card, data, avatar)
        import io
        await inter.edit_original_response(file=disnake.File(io.BytesIO(png), filename=f"card_{m.id}.png"))


def setup(bot: commands.Bot):
    bot.add_cog(ProfileCard(bot))
