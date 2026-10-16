"""
Подсказки о повышении: раз в сутки бот находит участников, которые набрали критерии,
и кладёт руководству КАРТОЧКУ с кнопками «Повысить / Позже / Не предлагать».

Правила против спама (участникам бот НИКОГДА не пишет — ни в личку, ни в канале):
  • одна карточка на участника и на ранг; пока карточка висит без ответа — новую не создаём;
  • «Позже» прячет участника на N дней (по умолчанию 7), и так не больше M раз (по умолчанию 3);
  • «Не предлагать» убирает участника из подсказок на этот ранг насовсем;
  • в сутки не больше K новых карточек (по умолчанию 3), остальные ждут следующего дня;
  • без пингов, пока в /центр не выбрана роль для пинга.

Критерии берутся из блока «Кандидаты» в /центр (дни в семье, часы войса, выговоры, ранги)
плюс минимум отметок «Буду/Опоздаю» на сборах за 14 дней.
Команды (только руководство): /повышения проверить · /повышения сбросить
"""
import time
from typing import Dict, List, Optional

import disnake
from disnake.ext import commands, tasks

import database
from utils import attendance
from utils import famq_core as core

DDL = """CREATE TABLE IF NOT EXISTS promo_hints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    rank_from INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    shown_count INTEGER NOT NULL DEFAULT 1,
    snooze_until INTEGER NOT NULL DEFAULT 0,
    message_id INTEGER,
    channel_id INTEGER,
    updated_ts INTEGER NOT NULL DEFAULT 0,
    UNIQUE (guild_id, user_id, rank_from)
)"""


def _cfg() -> dict:
    return core.get("promo_hint", {}) or {}


def event_marks(user_id: int, days: int = 14) -> int:
    """Сколько раз участник отметился «Буду/Опоздаю» на сборах за последние N дней."""
    cutoff = time.time() - days * 86400
    n = 0
    try:
        for rec in attendance._load().values():
            if not isinstance(rec, dict) or rec.get("ts", 0) < cutoff:
                continue
            if user_id in rec.get("yes", []) or user_id in rec.get("late", []):
                n += 1
    except Exception:
        return 0
    return n


def card_view(uid: int, rank: int) -> disnake.ui.View:
    v = disnake.ui.View(timeout=None)
    v.add_item(disnake.ui.Button(label="Повысить", emoji="✅", style=disnake.ButtonStyle.green,
                                 custom_id=f"ph:up:{uid}:{rank}"))
    v.add_item(disnake.ui.Button(label="Позже", emoji="⏳", style=disnake.ButtonStyle.secondary,
                                 custom_id=f"ph:later:{uid}:{rank}"))
    v.add_item(disnake.ui.Button(label="Не предлагать", emoji="🚫", style=disnake.ButtonStyle.danger,
                                 custom_id=f"ph:no:{uid}:{rank}"))
    return v


def card_embed(item: dict, marks: int, min_marks: int) -> disnake.Embed:
    m: disnake.Member = item["member"]
    nxt = item["rank"] + 1
    e = core.make_embed("📈 Пора повысить?", kind="main")
    e.description = (f"{m.mention} — **{core.rank_name(item['rank'])}** ➜ **{core.rank_name(nxt)}**\n"
                     f"Участник набрал нужные показатели. Решение за руководством.")
    e.add_field("📅 В семье", f"{item['days']} дн.", inline=True)
    e.add_field("🎙️ Войс (14 дн.)", f"{item['hours']:.1f} ч.", inline=True)
    e.add_field("📣 Сборы (14 дн.)", f"{marks} отм." + (f" (нужно {min_marks})" if min_marks else ""), inline=True)
    e.add_field("⚠️ Выговоры", str(item["warns"]), inline=True)
    e.set_thumbnail(url=m.display_avatar.with_size(256).url)
    e.set_footer(text="Участнику ничего не отправляется • «Позже» — напомню позже, «Не предлагать» — больше не буду")
    return e


class PromoHints(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ready = False

    def cog_unload(self):
        self.daily_loop.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._ready:
            await core.q_exec(DDL)
            self._ready = True
        if not self.daily_loop.is_running():
            self.daily_loop.start()

    # ───────── подбор и публикация ─────────

    def _channel(self, guild: disnake.Guild):
        cid = int(_cfg().get("channel_id", 0) or 0) or int(core.get("candidates.channel_id", 0) or 0)
        return guild.get_channel(cid) if cid else None

    async def scan(self, guild: disnake.Guild, limit: Optional[int] = None) -> str:
        channel = self._channel(guild)
        if channel is None:
            return "❌ Не выбран канал для подсказок (раздел «Подсказки о повышении» в /центр)."
        fs = self.bot.get_cog("FamilySync")
        if fs is None:
            return "❌ Не загружен модуль FamilySync."
        cfg = _cfg()
        cap = int(limit if limit is not None else cfg.get("max_per_day", 3))
        snooze = int(cfg.get("snooze_days", 7)) * 86400
        max_rem = int(cfg.get("max_reminders", 3))
        min_marks = int(cfg.get("min_events", 2))
        ping_role = int(cfg.get("ping_role_id", 0) or 0)

        items = await fs.build_candidates(guild)
        now = int(time.time())
        posted = skipped = 0
        for it in items:
            if posted >= cap:
                break
            m: disnake.Member = it["member"]
            marks = event_marks(m.id)
            if marks < min_marks:
                skipped += 1
                continue
            row = await core.q_one(
                "SELECT * FROM promo_hints WHERE guild_id=? AND user_id=? AND rank_from=?",
                (guild.id, m.id, it["rank"]))
            if row:
                if row["status"] in ("pending", "dismissed", "done"):
                    continue
                if row["status"] == "snoozed" and (row["snooze_until"] > now or row["shown_count"] >= max_rem):
                    continue
            try:
                msg = await channel.send(
                    content=f"<@&{ping_role}>" if ping_role else None,
                    embed=card_embed(it, marks, min_marks), view=card_view(m.id, it["rank"]),
                    allowed_mentions=disnake.AllowedMentions(roles=True, users=False))
            except Exception as ex:
                return f"❌ Не удалось отправить в {channel.mention}: {ex}"
            if row:
                await core.q_exec(
                    "UPDATE promo_hints SET status='pending', shown_count=shown_count+1, message_id=?, "
                    "channel_id=?, updated_ts=? WHERE id=?", (msg.id, channel.id, now, row["id"]))
            else:
                await core.q_exec(
                    "INSERT INTO promo_hints (guild_id,user_id,rank_from,status,message_id,channel_id,updated_ts) "
                    "VALUES (?,?,?,?,?,?,?)", (guild.id, m.id, it["rank"], "pending", msg.id, channel.id, now))
            posted += 1
        return (f"✅ Новых карточек: **{posted}** (лимит {cap}). "
                f"Подходят по критериям: {len(items)}, не хватило отметок на сборах: {skipped}.")

    @tasks.loop(minutes=5)
    async def daily_loop(self):
        cfg = _cfg()
        if not cfg.get("enabled", True):
            return
        now = core.local_now()
        today = now.strftime("%Y-%m-%d")
        if now.hour != int(cfg.get("hour", 12)) or cfg.get("last_run") == today:
            return
        core.set_value("promo_hint.last_run", today)
        for g in self.bot.guilds:
            try:
                await self.scan(g)
            except Exception:
                pass

    @daily_loop.before_loop
    async def _b(self):
        await self.bot.wait_until_ready()

    # ───────── кнопки ─────────

    @commands.Cog.listener()
    async def on_button_click(self, inter: disnake.MessageInteraction):
        cid = inter.data.custom_id
        if not cid.startswith("ph:"):
            return
        try:
            _, action, uid_s, rank_s = cid.split(":")
            uid, rank = int(uid_s), int(rank_s)
        except ValueError:
            return
        if not core.is_admin(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        row = await core.q_one("SELECT * FROM promo_hints WHERE guild_id=? AND user_id=? AND rank_from=?",
                               (inter.guild.id, uid, rank))
        if not row or row["status"] != "pending":
            return await inter.response.send_message("ℹ️ По этой карточке решение уже принято.", ephemeral=True)
        base = inter.message.embeds[0] if inter.message.embeds else core.make_embed("📈 Повышение")
        now = int(time.time())

        if action == "later":
            until = now + int(_cfg().get("snooze_days", 7)) * 86400
            await core.q_exec("UPDATE promo_hints SET status='snoozed', snooze_until=?, updated_ts=? WHERE id=?",
                              (until, now, row["id"]))
            base.color = core.color("warn")
            base.add_field("⏳ Отложено", f"{inter.author.mention} · напомню после <t:{until}:D>", inline=False)
            return await inter.response.edit_message(embed=base, view=None)

        if action == "no":
            await core.q_exec("UPDATE promo_hints SET status='dismissed', updated_ts=? WHERE id=?", (now, row["id"]))
            base.color = core.color("err")
            base.add_field("🚫 Не предлагать", f"{inter.author.mention} · больше не буду предлагать на этот ранг",
                           inline=False)
            return await inter.response.edit_message(embed=base, view=None)

        if action == "up":
            member = inter.guild.get_member(uid)
            if member is None:
                await core.q_exec("UPDATE promo_hints SET status='dismissed', updated_ts=? WHERE id=?", (now, row["id"]))
                return await inter.response.edit_message(
                    content="ℹ️ Участник уже не на сервере.", embed=base, view=None)
            if core.member_rank(member) != rank:
                await core.q_exec("UPDATE promo_hints SET status='done', updated_ts=? WHERE id=?", (now, row["id"]))
                return await inter.response.edit_message(
                    content="ℹ️ Ранг участника уже изменён вручную.", embed=base, view=None)
            new_rank = rank + 1
            if new_rank > core.max_rank():
                return await inter.response.send_message("❌ Это высший ранг.", ephemeral=True)
            await inter.response.defer()
            note = await self._apply(inter.guild, member, new_rank)
            await core.q_exec("UPDATE promo_hints SET status='done', updated_ts=? WHERE id=?", (now, row["id"]))
            base.color = core.color("ok")
            base.title = "✅ Повышение выполнено"
            base.add_field("🎖️ Итог", f"{member.mention} теперь **{core.rank_name(new_rank)}**\n"
                                      f"Повысил: {inter.author.mention} · {note}", inline=False)
            await inter.edit_original_message(embed=base, view=None)
            await core.send_log(inter.guild, "audit", core.make_embed(
                "🎖️ Смена ранга", f"{member.mention}: **{rank}** ➜ **{new_rank}** "
                                 f"({core.rank_name(new_rank)})\nИзменил: {inter.author.mention}", kind="ok"))

    async def _apply(self, guild: disnake.Guild, member: disnake.Member, new_rank: int) -> str:
        """Меняет ранг так же, как кнопка в карточке участника (если доступна), иначе — только роли."""
        st = self.bot.get_cog("StaffTools")
        row = await database.get_member(member.id)
        if st is not None and row:
            try:
                return await st.apply_rank(guild, row, new_rank)
            except Exception as ex:
                return f"ошибка смены ранга: {ex}"
        roles_map = core.rank_roles_map()
        target = roles_map.get(new_rank) or set()
        try:
            every = set().union(*roles_map.values()) if roles_map else set()
            remove = [r for r in member.roles if r.id in every and r.id not in target]
            if remove:
                await member.remove_roles(*remove, reason="Повышение")
            add = [guild.get_role(rid) for rid in target]
            add = [r for r in add if r and r not in member.roles]
            if add:
                await member.add_roles(*add, reason="Повышение")
        except disnake.Forbidden:
            return "у бота нет прав менять роли (проверьте иерархию ролей)"
        return "роли обновлены"

    # ───────── команды ─────────

    @commands.slash_command(name="повышения", description="📈 Подсказки о повышении (руководство)")
    @commands.guild_only()
    async def promo(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @promo.sub_command(name="проверить", description="Проверить критерии прямо сейчас и выложить новые карточки")
    async def check_now(self, inter: disnake.ApplicationCommandInteraction):
        if not core.is_admin(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        await inter.edit_original_response(await self.scan(inter.guild))

    @promo.sub_command(name="сбросить", description="Снова разрешить подсказки по участнику")
    async def reset(self, inter: disnake.ApplicationCommandInteraction,
                    user: disnake.Member = commands.Param(name="участник", description="Кого вернуть в подсказки")):
        if not core.is_admin(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        await core.q_exec("DELETE FROM promo_hints WHERE guild_id=? AND user_id=? AND status IN "
                          "('pending','snoozed','dismissed')", (inter.guild.id, user.id))
        await inter.response.send_message(f"✅ {user.mention} снова может попасть в подсказки.", ephemeral=True,
                                          allowed_mentions=disnake.AllowedMentions.none())


def setup(bot: commands.Bot):
    bot.add_cog(PromoHints(bot))
