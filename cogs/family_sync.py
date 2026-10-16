import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import disnake
from disnake.ext import commands, tasks
import database
from utils import famq_core as core

class FamilySync(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voice_since: Dict[Tuple[int, int], float] = {}

    def cog_unload(self):
        self.flush_loop.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        now = time.time()
        for g in self.bot.guilds:
            for vc in g.voice_channels:
                for m in vc.members:
                    if not m.bot:
                        self.voice_since.setdefault((g.id, m.id), now)
        if not self.flush_loop.is_running():
            self.flush_loop.start()

    @commands.Cog.listener()
    async def on_member_join(self, member: disnake.Member):
        cfg = core.get("autorole", {}) or {}
        if not cfg.get("enabled"): return
        ids = [int(cfg.get("bots", 0) or 0)] if member.bot else core.ids_list("autorole.roles")
        roles = [member.guild.get_role(i) for i in ids if i]
        roles = [r for r in roles if r is not None]
        if not roles: return
        try:
            await member.add_roles(*roles, reason="Авто-роль при входе (/центр)")
        except Exception as e:
            await core.send_log(member.guild, "bot", core.make_embed("⚠️ Не удалось выдать авто-роль", f"{member.mention}: {e}", kind="warn"))

    @staticmethod
    def _name_static(member: disnake.Member, row: Optional[dict]) -> Tuple[str, str]:
        n, s = core.parse_nick(member.display_name)
        if row: return (row["nick"] or n), (row["static_id"] if row["static_id"] not in (None, "") else s)
        return n, s

    async def sync_member(self, member: disnake.Member, apply: bool = True) -> Optional[Tuple[int, int]]:
        role_rank = core.member_rank(member)
        if role_rank < 1: return None
        row = await database.get_member(member.id)
        old = row["rank"] if row else 0
        if row and old == role_rank: return None
        if apply:
            nick, static = self._name_static(member, row)
            await database.upsert_member(member.id, nick, static, role_rank)
        return (old, role_rank)

    async def sync_all(self, guild: disnake.Guild, apply: bool = True) -> dict:
        changed, added, checked = [], 0, 0
        for m in guild.members:
            if m.bot: continue
            r = await self.sync_member(m, apply=apply)
            if core.member_rank(m) >= 1: checked += 1
            if r:
                changed.append((m, r[0], r[1]))
                if r[0] == 0: added += 1
        return {"checked": checked, "changed": changed, "added": added}

    @commands.Cog.listener()
    async def on_member_update(self, before: disnake.Member, after: disnake.Member):
        if before.roles == after.roles or not core.get("rank_sync.auto", True): return
        try:
            res = await self.sync_member(after, apply=True)
        except Exception: return
        if res:
            await core.send_log(after.guild, "roles", core.make_embed("🎖️ Ранг в карточке обновлён", f"{after.mention}: {res[0]} -> {res[1]}", kind="ok"))

    async def _commit(self, guild_id: int, user_id: int, restart: bool = False):
        key = (guild_id, user_id)
        since = self.voice_since.pop(key, None)
        if since: await core.add_voice_seconds(user_id, int(time.time() - since))
        if restart: self.voice_since[key] = time.time()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: disnake.Member, before: disnake.VoiceState, after: disnake.VoiceState):
        if member.bot: return
        key = (member.guild.id, member.id)
        afk = member.guild.afk_channel
        def counts(ch): return ch is not None and (afk is None or ch.id != afk.id)
        if counts(after.channel): self.voice_since.setdefault(key, time.time())
        else: await self._commit(member.guild.id, member.id)

    @tasks.loop(minutes=5)
    async def flush_loop(self):
        for (gid, uid) in list(self.voice_since.keys()):
            await self._commit(gid, uid, restart=True)

    @flush_loop.before_loop
    async def _b1(self):
        await self.bot.wait_until_ready()

    async def build_candidates(self, guild: disnake.Guild) -> List[dict]:
        cfg = core.get("candidates", {}) or {}
        min_days, min_hours = int(cfg.get("min_days", 14)), float(cfg.get("min_voice_hours", 0))
        max_warns, max_from = int(cfg.get("max_warns", 0)), int(cfg.get("max_from_rank", 2))
        warn_rows = await core.q_all("SELECT user_id, COUNT(*) AS c FROM warns WHERE active=1 GROUP BY user_id")
        warns = {r["user_id"]: r["c"] for r in warn_rows}
        members = {r["user_id"]: r for r in await core.q_all("SELECT * FROM family_members")}
        out, now = [], datetime.now(timezone.utc)
        for m in guild.members:
            if m.bot: continue
            rank = core.member_rank(m)
            if rank < 1 or rank > max_from or rank >= core.max_rank(): continue
            if core.pause_kind(m): continue
            joined = None
            row = members.get(m.id)
            if row and row.get("joined_at"):
                try: joined = datetime.strptime(str(row["joined_at"])[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except Exception: joined = None
            joined = joined or m.joined_at
            days = (now - joined).days if joined else 0
            if days < min_days: continue
            w = warns.get(m.id, 0)
            if w > max_warns: continue
            hours = (await core.voice_seconds(m.id, days=14)) / 3600
            if hours < min_hours: continue
            out.append({"member": m, "rank": rank, "days": days, "hours": hours, "warns": w})
        out.sort(key=lambda x: (x["hours"], x["days"]), reverse=True)
        return out

    def candidates_embed(self, items: List[dict]) -> disnake.Embed:
        cfg = core.get("candidates", {}) or {}
        e = core.make_embed("📈 Кандидаты на повышение")
        e.description = f"Критерии: {cfg.get('min_days', 14)} дн., {cfg.get('min_voice_hours', 0)} ч. войса, макс {cfg.get('max_warns', 0)} варнов\n"
        if not items:
            e.description += "\n😴 Никто не подходит."
            return e
        lines = [f"• {it['member'].mention} — {it['rank']}->{it['rank']+1} · {it['days']} дн. · {it['hours']:.1f} ч." for it in items[:25]]
        e.add_field("Подходят", "\n".join(lines), inline=False)
        return e

    async def post_candidates(self, guild: disnake.Guild) -> str:
        cid = int(core.get("candidates.channel_id", 0) or 0)
        channel = guild.get_channel(cid) if cid else None
        if not channel: return "❌ Канал не выбран."
        items = await self.build_candidates(guild)
        try: await channel.send(embed=self.candidates_embed(items))
        except Exception as e: return f"❌ Ошибка: {e}"
        return f"✅ Отправлено в {channel.mention}"

def setup(bot: commands.Bot):
    bot.add_cog(FamilySync(bot))
