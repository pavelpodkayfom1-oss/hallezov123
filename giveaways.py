"""
Розыгрыши: /розыгрыш создать | завершить | реролл
Условия участия: минимальный ранг (по ролям) и минимум часов в голосовых каналах.
Победители выбираются автоматически по истечении времени.

Что внутри:
  • красивый баннер-картинка (Pillow) для розыгрыша и для итогов с аватарками победителей;
  • живые кнопки: «Участвовать · N» (счётчик на самой кнопке), «Участники», «Мои шансы»;
  • анимация «крутим барабан» перед объявлением победителей;
  • итоговое сообщение с медалями, кнопкой-ссылкой на розыгрыш и личкой победителям.
"""
import asyncio
import random
from typing import List, Optional, Tuple

import disnake
from disnake.ext import commands, tasks

from utils import famq_core as core
from utils import giveaway_art as art

MEDALS = ["🥇", "🥈", "🥉"]
BANNER_NAME = "giveaway.png"
RESULT_NAME = "giveaway_result.png"


# ───────────────────────── вспомогательное ─────────────────────────

def _accent() -> Tuple[int, int, int]:
    c = core.color("main")
    return (c >> 16) & 255, (c >> 8) & 255, c & 255


def _conditions(row: dict) -> List[str]:
    out = []
    if row["min_rank"]:
        out.append(f"Ранг {row['min_rank']}+")
    if row["min_voice_hours"]:
        out.append(f"Войс {row['min_voice_hours']} ч.")
    return out


async def _count(gid: int) -> int:
    return (await core.q_one("SELECT COUNT(*) AS c FROM giveaway_entries WHERE giveaway_id=?", (gid,)))["c"]


async def _banner_file(row: dict, ended: bool = False, name: str = BANNER_NAME) -> disnake.File:
    import io
    data = await asyncio.to_thread(
        art.render_banner, row["id"], row["prize"], row["winners"], _conditions(row), ended, _accent())
    return disnake.File(io.BytesIO(data), filename=name)


async def _avatar(member: disnake.abc.User) -> Optional[bytes]:
    try:
        return await member.display_avatar.with_size(256).read()
    except Exception:
        return None


def _bar(done: int, total: int = 10) -> str:
    return "▰" * done + "▱" * (total - done)


def gw_embed(row: dict, count: int, winners: Optional[List[int]] = None, ended: bool = False,
             image: Optional[str] = BANNER_NAME) -> disnake.Embed:
    e = core.make_embed(f"🎁 {core.clip(row['prize'], 200)}", kind="ok" if ended else "main")
    lines = [f"🏆 **Победителей:** {row['winners']}",
             f"👤 **Организатор:** <@{row['host_id']}>",
             f"👥 **Участников:** {count}"]
    if ended:
        lines.append(f"⏰ **Завершён:** <t:{row['end_ts']}:R>")
    else:
        lines.append(f"⏰ **Итоги:** <t:{row['end_ts']}:R> · <t:{row['end_ts']}:f>")
    e.description = "\n".join(lines)
    cond = []
    if row["min_rank"]:
        cond.append(f"🎖️ Ранг не ниже **{row['min_rank']}** ({core.rank_name(row['min_rank'])})")
    if row["min_voice_hours"]:
        cond.append(f"🎙️ В войсе не менее **{row['min_voice_hours']} ч.**")
    e.add_field("📋 Условия участия", "\n".join(cond) if cond else "Без ограничений — участвуют все", inline=False)
    if ended:
        if winners:
            txt = "\n".join(f"{MEDALS[i] if i < 3 else '🏅'} <@{w}>" for i, w in enumerate(winners))
        else:
            txt = "Никто не участвовал 😔"
        e.add_field("🎉 Победители", txt, inline=False)
    else:
        e.add_field("🚀 Как участвовать", "Нажмите **«Участвовать»** ниже. Нажмите ещё раз, чтобы выйти.",
                    inline=False)
    if image:
        e.set_image(url=f"attachment://{image}")
    return e


async def check_eligibility(row: dict, member: disnake.Member) -> Tuple[bool, List[str]]:
    """Возвращает (можно_ли_участвовать, строки_статуса по каждому условию)."""
    ok, lines = True, []
    if row["min_rank"]:
        rank = core.member_rank(member)
        good = rank >= row["min_rank"]
        ok &= good
        lines.append(f"{'✅' if good else '❌'} Ранг: нужен **{row['min_rank']}**+ "
                     f"({core.rank_name(row['min_rank'])}) — у вас **{rank}**")
    if row["min_voice_hours"]:
        hours = await core.voice_seconds(member.id) / 3600
        good = hours >= row["min_voice_hours"]
        ok &= good
        lines.append(f"{'✅' if good else '❌'} Войс: нужно **{row['min_voice_hours']} ч.** — у вас **{hours:.1f} ч.**")
    if not lines:
        lines.append("✅ Ограничений нет — участвуют все")
    return ok, lines


# ───────────────────────── кнопки ─────────────────────────

class GiveawayView(disnake.ui.View):
    def __init__(self, count: int = 0):
        super().__init__(timeout=None)
        self.join.label = f"Участвовать · {count}"

    async def _active_row(self, inter: disnake.MessageInteraction) -> Optional[dict]:
        row = await core.q_one("SELECT * FROM giveaways WHERE message_id=?", (inter.message.id,))
        if not row:
            await inter.response.send_message("❔ Розыгрыш не найден.", ephemeral=True)
            return None
        return row

    @disnake.ui.button(label="Участвовать · 0", emoji="🎉", style=disnake.ButtonStyle.green, custom_id="gw:join")
    async def join(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        row = await core.q_one("SELECT * FROM giveaways WHERE message_id=?", (inter.message.id,))
        if not row or row["status"] != "active" or row["end_ts"] <= core.now_ts():
            return await inter.response.send_message("⏰ Этот розыгрыш уже завершён.", ephemeral=True)
        exists = await core.q_one(
            "SELECT 1 AS x FROM giveaway_entries WHERE giveaway_id=? AND user_id=?", (row["id"], inter.author.id))
        if exists:
            await core.q_exec("DELETE FROM giveaway_entries WHERE giveaway_id=? AND user_id=?",
                              (row["id"], inter.author.id))
            text = "↩️ Вы вышли из розыгрыша."
        else:
            ok, lines = await check_eligibility(row, inter.author)
            if not ok:
                return await inter.response.send_message(
                    "🚫 **Вы пока не подходите по условиям:**\n" + "\n".join(lines), ephemeral=True)
            await core.q_exec("INSERT OR IGNORE INTO giveaway_entries (giveaway_id, user_id) VALUES (?, ?)",
                              (row["id"], inter.author.id))
            text = "✅ **Вы участвуете!** Удачи 🍀 Нажмите ещё раз, чтобы выйти."
        cnt = await _count(row["id"])
        await inter.response.edit_message(embed=gw_embed(row, cnt), view=GiveawayView(cnt))
        await inter.followup.send(text, ephemeral=True)

    @disnake.ui.button(label="Участники", emoji="👥", style=disnake.ButtonStyle.secondary, custom_id="gw:list")
    async def members(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        row = await self._active_row(inter)
        if not row:
            return
        rows = await core.q_all("SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (row["id"],))
        if not rows:
            return await inter.response.send_message("😶 Пока никто не участвует — будьте первым!", ephemeral=True)
        ids = [r["user_id"] for r in rows]
        shown = ids[:40]
        e = core.make_embed(f"👥 Участники · {len(ids)}", " ".join(f"<@{u}>" for u in shown), kind="main")
        if len(ids) > len(shown):
            e.set_footer(text=f"…и ещё {len(ids) - len(shown)}")
        await inter.response.send_message(embed=e, ephemeral=True,
                                          allowed_mentions=disnake.AllowedMentions.none())

    @disnake.ui.button(label="Мои шансы", emoji="🍀", style=disnake.ButtonStyle.blurple, custom_id="gw:odds")
    async def odds(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        row = await self._active_row(inter)
        if not row:
            return
        ok, lines = await check_eligibility(row, inter.author)
        cnt = await _count(row["id"])
        joined = await core.q_one(
            "SELECT 1 AS x FROM giveaway_entries WHERE giveaway_id=? AND user_id=?", (row["id"], inter.author.id))
        e = core.make_embed("🍀 Ваши шансы", kind="ok" if ok else "warn")
        if joined:
            chance = min(100.0, row["winners"] / max(1, cnt) * 100)
            filled = max(1, round(chance / 10)) if chance > 0 else 0
            status = (f"✅ Вы участвуете\n**Шанс на победу: ≈ {chance:.1f}%**\n"
                      f"`{_bar(filled)}`  ({row['winners']} из {cnt})")
        elif ok:
            chance = min(100.0, row["winners"] / (cnt + 1) * 100)
            status = f"➕ Вы можете участвовать\nЕсли вступите сейчас — шанс ≈ **{chance:.1f}%**"
        else:
            status = "🚫 Вы пока не проходите по условиям"
        e.description = status + "\n\n" + "\n".join(lines)
        e.add_field("⏰ До итогов", f"<t:{row['end_ts']}:R>", inline=False)
        await inter.response.send_message(embed=e, ephemeral=True)


# ───────────────────────── cog ─────────────────────────

class Giveaways(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._view_added = False

    def cog_unload(self):
        self.check_loop.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._view_added:
            self.bot.add_view(GiveawayView())
            self._view_added = True
        if not self.check_loop.is_running():
            self.check_loop.start()

    # ───────── завершение ─────────

    async def _pool(self, row: dict, exclude: Optional[List[int]] = None) -> List[int]:
        guild = self.bot.get_guild(row["guild_id"])
        entries = [r["user_id"] for r in await core.q_all(
            "SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (row["id"],))]
        pool = []
        for uid in entries:
            if exclude and uid in exclude:
                continue
            m = guild.get_member(uid) if guild else None
            if m and not m.bot:
                pool.append(uid)
        return pool

    async def _pick(self, row: dict, exclude: Optional[List[int]] = None) -> List[int]:
        pool = await self._pool(row, exclude)
        return random.sample(pool, min(row["winners"], len(pool))) if pool else []

    async def _suspense(self, msg: disnake.Message, row: dict, cnt: int, guild: disnake.Guild):
        """Короткая анимация перед объявлением: «крутим барабан» с мелькающими именами участников."""
        pool = await self._pool(row)
        names = [guild.get_member(u).display_name for u in pool if guild.get_member(u)] or ["…"]
        frames = [("🎰", "Крутим барабан"), ("🎲", "Тасуем участников"), ("✨", "Вот-вот объявим")]
        for i, (icon, title) in enumerate(frames, start=1):
            e = core.make_embed(f"{icon} {title}…", kind="warn")
            flick = "  ›  ".join(f"**{n[:18]}**" for n in random.sample(names, min(3, len(names))))
            e.description = (f"### {core.clip(row['prize'], 120)}\n"
                             f"`{_bar(round(i / len(frames) * 10))}`\n\n{flick}\n\n👥 Участников: **{cnt}**")
            try:
                await msg.edit(embed=e, view=None)
            except Exception:
                return
            await asyncio.sleep(1.6)

    async def _results_files(self, row: dict, winners: List[int], guild: disnake.Guild):
        import io
        people: List[Tuple[str, Optional[bytes]]] = []
        for w in winners[:5]:
            m = guild.get_member(w)
            people.append((m.display_name if m else str(w), await _avatar(m) if m else None))
        data = await asyncio.to_thread(art.render_winners, row["id"], row["prize"], people, _accent())
        return disnake.File(io.BytesIO(data), filename=RESULT_NAME)

    async def finish(self, row: dict, winners: Optional[List[int]] = None, announce: bool = True):
        if winners is None:
            winners = await self._pick(row)
        await core.q_exec("UPDATE giveaways SET status='ended', winner_ids=? WHERE id=?",
                          (",".join(map(str, winners)), row["id"]))
        guild = self.bot.get_guild(row["guild_id"])
        channel = guild.get_channel(row["channel_id"]) if guild else None
        if channel is None:
            return
        cnt = await _count(row["id"])
        msg = None
        try:
            msg = await channel.fetch_message(row["message_id"])
        except Exception:
            pass
        if msg and announce and winners:
            await self._suspense(msg, row, cnt, guild)
        if msg:
            try:
                f = await self._results_files(row, winners, guild)
                await msg.edit(embed=gw_embed(row, cnt, winners, ended=True, image=RESULT_NAME),
                               file=f, attachments=[], view=None)
            except Exception:
                try:
                    await msg.edit(embed=gw_embed(row, cnt, winners, ended=True, image=None), view=None)
                except Exception:
                    pass
        if announce:
            await self._announce(channel, row, winners, msg)
            await self._dm_winners(guild, row, winners, msg)
        await core.send_log(guild, "giveaway", core.make_embed(
            "🎁 Розыгрыш завершён",
            f"**{row['prize']}**\nУчастников: {cnt}\nПобедители: {' '.join(f'<@{w}>' for w in winners) or '—'}",
            kind="ok"))

    async def _announce(self, channel, row: dict, winners: List[int], msg: Optional[disnake.Message]):
        if winners:
            e = core.make_embed("🎉 У нас есть победители!", kind="ok")
            e.description = (f"### 🎁 {core.clip(row['prize'], 200)}\n\n" +
                             "\n".join(f"{MEDALS[i] if i < 3 else '🏅'} <@{w}>" for i, w in enumerate(winners)) +
                             "\n\nПоздравляем! Организатор свяжется с вами 💌")
            m = channel.guild.get_member(winners[0])
            if m:
                e.set_thumbnail(url=m.display_avatar.with_size(256).url)
            content = " ".join(f"<@{w}>" for w in winners)
        else:
            e = core.make_embed("😔 Розыгрыш без победителей", kind="warn",
                                description=f"В розыгрыше **{core.clip(row['prize'], 200)}** никто не участвовал.")
            content = None
        view = None
        if msg:
            view = disnake.ui.View()
            view.add_item(disnake.ui.Button(label="К розыгрышу", emoji="🎁", style=disnake.ButtonStyle.link,
                                            url=msg.jump_url))
        try:
            await channel.send(content, embed=e, view=view,
                               allowed_mentions=disnake.AllowedMentions(users=True))
        except Exception:
            pass

    async def _dm_winners(self, guild, row: dict, winners: List[int], msg: Optional[disnake.Message]):
        for w in winners:
            m = guild.get_member(w)
            if not m:
                continue
            e = core.make_embed("🎉 Вы победили в розыгрыше!", kind="ok",
                                description=f"Приз: **{core.clip(row['prize'], 200)}**\n"
                                            f"Сервер: **{guild.name}**" +
                                            (f"\n[Перейти к розыгрышу]({msg.jump_url})" if msg else ""))
            try:
                await m.send(embed=e)
            except Exception:
                pass  # закрытые личные сообщения — не страшно, объявление уже в канале

    @tasks.loop(seconds=20)
    async def check_loop(self):
        rows = await core.q_all("SELECT * FROM giveaways WHERE status='active' AND end_ts<=?", (core.now_ts(),))
        for row in rows:
            try:
                await self.finish(row)
            except Exception:
                await core.q_exec("UPDATE giveaways SET status='ended' WHERE id=?", (row["id"],))

    @check_loop.before_loop
    async def _b(self):
        await self.bot.wait_until_ready()

    # ───────── команды ─────────

    @commands.slash_command(name="розыгрыш", description="🎁 Розыгрыши семьи")
    @commands.guild_only()
    async def giveaway(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @giveaway.sub_command(name="создать", description="Запустить розыгрыш (руководство)")
    async def create(
        self, inter: disnake.ApplicationCommandInteraction,
        prize: str = commands.Param(name="приз", description="Что разыгрываем", max_length=200),
        duration: str = commands.Param(name="длительность", description="Например: 30m, 2h, 1d, 1d12h",
                                       default="1d"),
        winners: int = commands.Param(name="победителей", description="Сколько победителей", default=1, ge=1, le=20),
        min_rank: int = commands.Param(name="мин_ранг", description="Минимальный ранг (0 — без ограничений)",
                                       default=0, ge=0, le=10),
        min_hours: int = commands.Param(name="мин_часов_войса", description="Минимум часов в войсе (0 — нет)",
                                        default=0, ge=0, le=10000),
        channel: Optional[disnake.TextChannel] = commands.Param(name="канал", description="Куда отправить",
                                                                default=None),
    ):
        if not core.is_staff(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        secs = core.parse_duration(duration)
        if not secs or secs < 30 or secs > 60 * 86400:
            return await inter.response.send_message(
                "❌ Неверная длительность. Примеры: `30m`, `2h`, `1d`, `1d12h` (от 30 сек до 60 дней).",
                ephemeral=True)
        cid = int(core.get("giveaway.channel_id", 0) or 0)
        target = channel or (inter.guild.get_channel(cid) if cid else None) or inter.channel
        await inter.response.defer(ephemeral=True)
        end_ts = core.now_ts() + secs
        gid = await core.q_exec(
            "INSERT INTO giveaways (guild_id, channel_id, host_id, prize, winners, min_rank, min_voice_hours, end_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (inter.guild.id, target.id, inter.author.id, prize, winners, min_rank, min_hours, end_ts))
        row = await core.q_one("SELECT * FROM giveaways WHERE id=?", (gid,))
        ping_role = int(core.get("giveaway.ping_role_id", 0) or 0)
        try:
            msg = await target.send(
                content=f"<@&{ping_role}> 🎁 **Новый розыгрыш!**" if ping_role else None,
                embed=gw_embed(row, 0), file=await _banner_file(row), view=GiveawayView(0),
                allowed_mentions=disnake.AllowedMentions(roles=True))
        except Exception as ex:
            await core.q_exec("DELETE FROM giveaways WHERE id=?", (gid,))
            return await inter.edit_original_response(f"❌ Не удалось отправить в {target.mention}: {ex}")
        await core.q_exec("UPDATE giveaways SET message_id=? WHERE id=?", (msg.id, gid))
        await inter.edit_original_response(f"✅ Розыгрыш **#{gid}** запущен: {msg.jump_url}")

    @giveaway.sub_command(name="завершить", description="Завершить розыгрыш досрочно")
    async def end_now(
        self, inter: disnake.ApplicationCommandInteraction,
        gid: int = commands.Param(name="номер", description="Номер розыгрыша (#)", ge=1),
    ):
        if not core.is_staff(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        row = await core.q_one("SELECT * FROM giveaways WHERE id=? AND guild_id=?", (gid, inter.guild.id))
        if not row or row["status"] != "active":
            return await inter.response.send_message("ℹ️ Активный розыгрыш с таким номером не найден.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        row["end_ts"] = core.now_ts()
        await core.q_exec("UPDATE giveaways SET end_ts=? WHERE id=?", (row["end_ts"], gid))
        await self.finish(row)
        await inter.edit_original_response(f"✅ Розыгрыш #{gid} завершён.")

    @giveaway.sub_command(name="реролл", description="Выбрать нового победителя")
    async def reroll(
        self, inter: disnake.ApplicationCommandInteraction,
        gid: int = commands.Param(name="номер", description="Номер завершённого розыгрыша", ge=1),
    ):
        if not core.is_staff(inter.author):
            return await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        row = await core.q_one("SELECT * FROM giveaways WHERE id=? AND guild_id=?", (gid, inter.guild.id))
        if not row or row["status"] != "ended":
            return await inter.response.send_message("ℹ️ Завершённый розыгрыш не найден.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        old = [int(x) for x in (row["winner_ids"] or "").split(",") if x.isdigit()]
        new = await self._pick({**row, "winners": 1}, exclude=old)
        if not new:
            return await inter.edit_original_response("😔 Больше некого выбирать.")
        allw = old + new
        await core.q_exec("UPDATE giveaways SET winner_ids=? WHERE id=?", (",".join(map(str, allw)), gid))
        channel = inter.guild.get_channel(row["channel_id"])
        if channel:
            msg = None
            try:
                msg = await channel.fetch_message(row["message_id"])
            except Exception:
                pass
            e = core.make_embed("🔁 Новый победитель!", kind="ok",
                                description=f"### 🎁 {core.clip(row['prize'], 200)}\n\n"
                                            f"🎉 <@{new[0]}> — поздравляем!")
            m = inter.guild.get_member(new[0])
            if m:
                e.set_thumbnail(url=m.display_avatar.with_size(256).url)
            view = None
            if msg:
                view = disnake.ui.View()
                view.add_item(disnake.ui.Button(label="К розыгрышу", emoji="🎁", style=disnake.ButtonStyle.link,
                                                url=msg.jump_url))
            try:
                await channel.send(f"<@{new[0]}>", embed=e, view=view,
                                   allowed_mentions=disnake.AllowedMentions(users=True))
            except Exception:
                pass
            if msg:  # обновляем итоговое сообщение, чтобы в нём были все победители
                try:
                    cnt = await _count(row["id"])
                    f = await self._results_files(row, allw, inter.guild)
                    await msg.edit(embed=gw_embed(row, cnt, allw, ended=True, image=RESULT_NAME),
                                   file=f, attachments=[], view=None)
                except Exception:
                    pass
            await self._dm_winners(inter.guild, row, new, msg)
        await inter.edit_original_response(f"✅ Новый победитель: <@{new[0]}>")


def setup(bot: commands.Bot):
    bot.add_cog(Giveaways(bot))
