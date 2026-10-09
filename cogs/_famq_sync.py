"""Синхронизация участников семьи с базой.

Проблема: карточка (famq card) ищет человека в таблице family_members, а те, кто попал в семью
через другого бота, туда не записаны. Этот модуль заносит в базу ВСЕХ, у кого есть хоть какая-то
роль (кроме @everyone), поэтому карточка начинает их видеть. Ранг берётся из ранговой роли, если
её нет — ставится 1.

• /famq_sync — ручная синхронизация (админы)
• автоматически при старте бота
• автоматически, когда человек получает любую роль
"""
import datetime
import json
import os
import re
from typing import Dict, List, Optional

import aiosqlite
import disnake
from disnake.ext import commands

from database import DB_PATH

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_json(name: str) -> dict:
    try:
        with open(os.path.join(BASE_DIR, name), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _is_admin(member: disnake.Member) -> bool:
    cfg = _load_json("config.json")
    if member.id in cfg.get("admin_user_ids", []):
        return True
    if member.guild and member.guild.owner_id == member.id:
        return True
    admin_roles = {r for r in cfg.get("admin_role_ids", []) if r}
    return any(r.id in admin_roles for r in member.roles)


def _rank_role_map() -> Dict[int, int]:
    """role_id -> номер ранга (из config.json → ranks)."""
    cfg = _load_json("config.json")
    out = {}
    for rank, data in cfg.get("ranks", {}).items():
        rid = int(data.get("role_id", 0) or 0)
        if rid:
            out[rid] = int(rank)
    return out


def calc_rank(member: disnake.Member, rmap: Dict[int, int]):
    """Возвращает (ранг, есть_ли_ранговая_роль).
    Карточка нужна любому, у кого есть хоть одна роль (кроме @everyone) — иначе None.
    Ранг = наивысшая ранговая роль, если её нет — 1."""
    if not any(r.id != member.guild.id for r in member.roles):
        return None, False
    ranks = [rmap[r.id] for r in member.roles if r.id in rmap]
    if ranks:
        return max(ranks), True
    return 1, False


def parse_nick(display_name: str):
    """'[Hallez FAMQ] Hugo | 166941' -> ('Hugo', '166941')."""
    n = re.sub(r"^\[[^\]]*\]\s*", "", display_name.strip())
    static = "—"
    m = re.search(r"\|\s*(\d+)\s*$", n)
    if m:
        static = m.group(1)
        n = n[:m.start()].strip()
    return (n or display_name), static


async def sync_members(members: List[disnake.Member], full: bool = False) -> dict:
    rmap = _rank_role_map()
    added = updated = 0
    no_static = 0
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM family_members") as cur:
            existing = {r["user_id"]: dict(r) for r in await cur.fetchall()}

        for m in members:
            if m.bot:
                continue
            rank, has_rank_role = calc_rank(m, rmap)
            if rank is None:
                continue
            nick, static = parse_nick(m.display_name)

            if m.id not in existing:
                joined = (m.joined_at or datetime.datetime.now(datetime.timezone.utc))
                joined = joined.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                await db.execute(
                    "INSERT OR IGNORE INTO family_members (user_id, nick, static_id, rank, joined_at) "
                    "VALUES (?, ?, ?, ?, ?)", (m.id, nick, static, rank, joined))
                added += 1
                if static == "—":
                    no_static += 1
            elif full:
                row = existing[m.id]
                changed = False
                if has_rank_role and row["rank"] != rank:   # ранг трогаем только если есть ранговая роль
                    await db.execute("UPDATE family_members SET rank = ? WHERE user_id = ?", (rank, m.id))
                    changed = True
                if row["static_id"] in ("—", "") and static != "—":
                    await db.execute("UPDATE family_members SET static_id = ? WHERE user_id = ?", (static, m.id))
                    changed = True
                if changed:
                    updated += 1
        await db.commit()
        async with db.execute("SELECT COUNT(*) FROM family_members") as cur:
            total = (await cur.fetchone())[0]
    return {"added": added, "updated": updated, "no_static": no_static, "total": total}


class FamqSync(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._startup_done = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self._startup_done:
            return
        self._startup_done = True
        for guild in self.bot.guilds:
            try:
                if not guild.chunked:
                    await guild.chunk()
                res = await sync_members(guild.members)
                if res["added"]:
                    print(f"👥 famq_sync: {guild.name} — добавлено в базу {res['added']} участников", flush=True)
            except Exception as e:
                print(f"⚠️ famq_sync: ошибка синхронизации {guild.name}: {e}", flush=True)

    @commands.Cog.listener()
    async def on_member_update(self, before: disnake.Member, after: disnake.Member):
        if before.roles == after.roles:
            return
        try:
            await sync_members([after])
        except Exception as e:
            print(f"⚠️ famq_sync: ошибка для {after.id}: {e}", flush=True)

    @commands.slash_command(name="famq_sync",
                            description="Занести всех участников с ролью в базу для famq card (админы)")
    async def famq_sync(
        self,
        inter: disnake.ApplicationCommandInteraction,
        full: bool = commands.Param(default=False, name="обновить_существующих",
                                    description="Также обновить ранг и статик у тех, кто уже есть в базе"),
    ):
        if not _is_admin(inter.author):
            return await inter.response.send_message("❌ Только для админов.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        if not inter.guild.chunked:
            try:
                await inter.guild.chunk()
            except Exception:
                pass
        res = await sync_members(inter.guild.members, full=full)
        text = (f"✅ **Синхронизация завершена**\n"
                f"➕ Добавлено в базу: **{res['added']}**\n")
        if full:
            text += f"🔄 Обновлено: **{res['updated']}**\n"
        text += f"👥 Всего в базе теперь: **{res['total']}**"
        if res["no_static"]:
            text += (f"\n\n⚠️ У **{res['no_static']}** участников не удалось определить статик из ника "
                     f"(ник не в формате `Имя | 12345`) — в карточке у них будет «—».")
        await inter.edit_original_response(content=text)


def setup(bot: commands.Bot):
    bot.add_cog(FamqSync(bot))
