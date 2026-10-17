"""
Семейная казна: взносы, расходы, остаток, должники, еженедельный отчёт.
Взнос в неделю и канал отчётов настраиваются в /центр → Казна.
Участники в отпуске от взносов освобождены.
"""
import asyncio
from typing import List, Tuple

import disnake
from disnake.ext import commands, tasks

from utils import famq_core as core


def _n(x: int) -> str:
    return f"{int(x):,}".replace(",", " ")


class Treasury(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def cog_unload(self):
        self.weekly.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.weekly.is_running():
            self.weekly.start()

    # ───────── расчёты ─────────

    async def debtors(self, guild: disnake.Guild) -> List[Tuple[disnake.Member, int]]:
        due = int(core.get("treasury.weekly_due", 0) or 0)
        if due <= 0:
            return []
        rows = await core.q_all(
            "SELECT user_id, SUM(amount) AS s FROM treasury "
            "WHERE guild_id=? AND kind='deposit' AND created_ts>=? GROUP BY user_id",
            (guild.id, core.week_start_ts()))
        paid = {r["user_id"]: int(r["s"]) for r in rows}
        out = []
        for m in guild.members:
            if m.bot or core.member_rank(m) < 1 or core.pause_kind(m) == "vacation":
                continue
            p = paid.get(m.id, 0)
            if p < due:
                out.append((m, p))
        out.sort(key=lambda x: x[1])
        return out

    async def week_summary(self, guild: disnake.Guild) -> dict:
        ws = core.week_start_ts()
        inc = await core.q_one(
            "SELECT COALESCE(SUM(amount),0) AS s FROM treasury WHERE guild_id=? AND kind='deposit' AND created_ts>=?",
            (guild.id, ws))
        out = await core.q_one(
            "SELECT COALESCE(SUM(amount),0) AS s FROM treasury WHERE guild_id=? AND kind='spend' AND created_ts>=?",
            (guild.id, ws))
        top = await core.q_all(
            "SELECT user_id, SUM(amount) AS s FROM treasury WHERE guild_id=? AND kind='deposit' AND created_ts>=? "
            "GROUP BY user_id ORDER BY s DESC LIMIT 5", (guild.id, ws))
        return {"balance": await core.treasury_balance(guild.id), "income": int(inc["s"]),
                "spent": int(out["s"]), "top": top}

    async def report_embed(self, guild: disnake.Guild) -> Tuple[disnake.Embed, list]:
        s = await self.week_summary(guild)
        debt = await self.debtors(guild)
        due = int(core.get("treasury.weekly_due", 0) or 0)
        e = core.make_embed("💰 Отчёт семейной казны")
        e.description = f"Неделя с <t:{core.week_start_ts()}:D>"
        e.add_field("🏦 Остаток", f"**{_n(s['balance'])}**", inline=True)
        e.add_field("📥 Приход за неделю", _n(s["income"]), inline=True)
        e.add_field("📤 Расход за неделю", _n(s["spent"]), inline=True)
        if s["top"]:
            e.add_field("🏅 Больше всех сдали",
                        "\n".join(f"{i}. <@{r['user_id']}> — {_n(r['s'])}"
                                  for i, r in enumerate(s["top"], 1)), inline=False)
        if due > 0:
            if debt:
                lines = [f"• {m.mention} — {_n(p)}/{_n(due)}" for m, p in debt[:20]]
                more = f"\n…и ещё {len(debt) - 20}" if len(debt) > 20 else ""
                e.add_field(f"⏳ Не сдали взнос ({len(debt)})", core.clip("\n".join(lines) + more, 1000), inline=False)
            else:
                e.add_field("✅ Взносы", "Все сдали взнос за неделю!", inline=False)
        return e, debt

    async def post_report(self, guild: disnake.Guild, dm: bool = True) -> str:
        cid = int(core.get("treasury.channel_id", 0) or 0)
        channel = guild.get_channel(cid) if cid else None
        if channel is None:
            return "❌ Не выбран канал для отчётов казны."
        e, debt = await self.report_embed(guild)
        try:
            await channel.send(embed=e, allowed_mentions=disnake.AllowedMentions.none())
        except Exception as ex:
            return f"❌ Не удалось отправить отчёт: {ex}"
        sent = 0
        if dm and core.get("treasury.dm_debtors", True) and debt:
            due = int(core.get("treasury.weekly_due", 0) or 0)
            for m, paid in debt:
                try:
                    await m.send(embed=core.make_embed(
                        "💰 Напоминание о взносе",
                        f"Привет! За эту неделю вы сдали **{paid}** из **{due}** в казну семьи.\n"
                        "Пожалуйста, сдайте оставшееся руководству. Если вы в отпуске — просто игнорируйте. 💎",
                        kind="warn"))
                    sent += 1
                except Exception:
                    pass
                await asyncio.sleep(1)
        return f"✅ Отчёт отправлен в {channel.mention}" + (f", напоминаний в ЛС: {sent}" if sent else "")

    @tasks.loop(minutes=1)
    async def weekly(self):
        cfg = core.get("treasury", {}) or {}
        if not cfg.get("report_enabled"):
            return
        now = core.local_now()
        today = now.strftime("%Y-%m-%d")
        if now.weekday() != int(cfg.get("weekday", 6)) or now.hour != int(cfg.get("hour", 19)):
            return
        if cfg.get("last_run") == today:
            return
        core.set_value("treasury.last_run", today)
        for g in self.bot.guilds:
            try:
                await self.post_report(g)
            except Exception:
                pass

    @weekly.before_loop
    async def _b(self):
        await self.bot.wait_until_ready()

    # ───────── команды ─────────

    @commands.slash_command(name="казна", description="💰 Семейная казна")
    @commands.guild_only()
    async def kazna(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @kazna.sub_command(name="баланс", description="Остаток и итоги недели")
    async def balance(self, inter: disnake.ApplicationCommandInteraction):
        e, _ = await self.report_embed(inter.guild)
        await inter.response.send_message(embed=e, ephemeral=True)

    @kazna.sub_command(name="сдал", description="Записать взнос участника (руководство)")
    async def deposit(
        self, inter: disnake.ApplicationCommandInteraction,
        member: disnake.Member = commands.Param(name="участник", description="Кто сдал"),
        amount: int = commands.Param(name="сумма", description="Сколько сдал", ge=1, le=1_000_000_000),
        comment: str = commands.Param(name="комментарий", description="Необязательно", default="", max_length=200),
    ):
        if not core.is_staff(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        await core.q_exec(
            "INSERT INTO treasury (guild_id, kind, user_id, admin_id, amount, comment, created_ts) "
            "VALUES (?, 'deposit', ?, ?, ?, ?, ?)",
            (inter.guild.id, member.id, inter.author.id, amount, comment, core.now_ts()))
        bal = await core.treasury_balance(inter.guild.id)
        await inter.response.send_message(
            f"✅ Записано: {member.mention} сдал **{_n(amount)}**. Остаток казны: **{_n(bal)}**",
            ephemeral=True)
        await core.send_log(inter.guild, "treasury", core.make_embed(
            "📥 Взнос в казну",
            f"{member.mention} — **{_n(amount)}**\nПринял: {inter.author.mention}\n{comment or ''}", kind="ok"))

    @kazna.sub_command(name="потратил", description="Записать расход казны (руководство)")
    async def spend(
        self, inter: disnake.ApplicationCommandInteraction,
        amount: int = commands.Param(name="сумма", description="Сколько потрачено", ge=1, le=1_000_000_000),
        comment: str = commands.Param(name="на_что", description="Причина расхода", max_length=200),
    ):
        if not core.is_staff(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        bal = await core.treasury_balance(inter.guild.id)
        if amount > bal:
            return await inter.response.send_message(f"❌ В казне только {_n(bal)}.", ephemeral=True)
        await core.q_exec(
            "INSERT INTO treasury (guild_id, kind, user_id, admin_id, amount, comment, created_ts) "
            "VALUES (?, 'spend', NULL, ?, ?, ?, ?)",
            (inter.guild.id, inter.author.id, amount, comment, core.now_ts()))
        await inter.response.send_message(
            f"✅ Расход **{_n(amount)}** записан. Остаток: **{_n(bal - amount)}**", ephemeral=True)
        await core.send_log(inter.guild, "treasury", core.make_embed(
            "📤 Расход казны", f"**{_n(amount)}** — {comment}\nЗаписал: {inter.author.mention}", kind="warn"))

    @kazna.sub_command(name="история", description="Последние операции казны")
    async def history(
        self, inter: disnake.ApplicationCommandInteraction,
        limit: int = commands.Param(name="сколько", description="Сколько записей (1-20)", default=10, ge=1, le=20),
    ):
        if not core.is_staff(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        rows = await core.q_all(
            "SELECT * FROM treasury WHERE guild_id=? ORDER BY id DESC LIMIT ?", (inter.guild.id, limit))
        if not rows:
            return await inter.response.send_message("📭 Операций пока нет.", ephemeral=True)
        lines = []
        for r in rows:
            sign = "📥" if r["kind"] == "deposit" else "📤"
            who = f"<@{r['user_id']}>" if r["user_id"] else (r["comment"] or "расход")
            lines.append(f"{sign} <t:{r['created_ts']}:d> **{_n(r['amount'])}** — {who}")
        await inter.response.send_message(
            embed=core.make_embed("📒 История казны", "\n".join(lines)), ephemeral=True)

    @kazna.sub_command(name="долги", description="Кто не сдал взнос за неделю")
    async def debts(self, inter: disnake.ApplicationCommandInteraction):
        due = int(core.get("treasury.weekly_due", 0) or 0)
        if due <= 0:
            return await inter.response.send_message(
                "ℹ️ Недельный взнос не задан (/центр → Казна).", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        debt = await self.debtors(inter.guild)
        if not debt:
            return await inter.edit_original_response("✅ Все сдали взнос за эту неделю.")
        lines = [f"• {m.mention} — {p}/{due}" for m, p in debt[:40]]
        await inter.edit_original_response(embed=core.make_embed(f"⏳ Должники ({len(debt)})", "\n".join(lines)))


def setup(bot: commands.Bot):
    bot.add_cog(Treasury(bot))
