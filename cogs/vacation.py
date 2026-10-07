"""
Отпуска как шаблон заявки (без команды):
  панель с кнопкой -> форма -> заявка в канал руководства -> одобрить/отклонить
  -> роль отпуска на N дней -> автоснятие роли по сроку.
Плюс «защита выговоров»: если у участника роль отпуска/АФК, выдающего предупреждают.
"""
import time
from typing import Dict, Optional, Tuple

import disnake
from disnake.ext import commands, tasks

from utils import famq_core as core
from utils.famq_core import WarnGuardBlocked


# ───────────────────────── ФОРМА ЗАЯВКИ ─────────────────────────

class VacationModal(disnake.ui.Modal):
    def __init__(self):
        lo = int(core.get("vacation.min_days", 1) or 1)
        hi = int(core.get("vacation.max_days", 30) or 30)
        super().__init__(
            title="Заявка на отпуск",
            custom_id="vac:modal",
            components=[
                disnake.ui.TextInput(
                    label="На сколько дней?", custom_id="days",
                    placeholder=f"Число от {lo} до {hi}", min_length=1, max_length=3,
                ),
                disnake.ui.TextInput(
                    label="Причина отпуска", custom_id="reason",
                    style=disnake.TextInputStyle.paragraph, max_length=500,
                    placeholder="Например: уезжаю, сессия, нет доступа к ПК...",
                ),
                disnake.ui.TextInput(
                    label="Как с вами связаться (по желанию)", custom_id="contact",
                    required=False, max_length=150,
                ),
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        cog: "Vacation" = inter.bot.get_cog("Vacation")
        await cog.handle_submit(inter, inter.text_values)


# ───────────────────────── ПАНЕЛЬ (постоянная) ─────────────────────────

class VacationPanelView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="Подать заявку на отпуск", emoji="🌴",
                       style=disnake.ButtonStyle.green, custom_id="vac:apply")
    async def apply(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if core.rank_roles_map() and any(core.rank_roles_map().values()) and core.member_rank(inter.author) < 1:
            return await inter.response.send_message("❌ Отпуск доступен только участникам семьи.", ephemeral=True)
        if not int(core.get("vacation.review_channel_id", 0) or 0):
            return await inter.response.send_message(
                "⚠️ Канал рассмотрения заявок не настроен. Сообщите руководству.", ephemeral=True)
        row = await core.q_one(
            "SELECT id, status FROM vacations WHERE user_id=? AND guild_id=? AND status IN ('pending','active')",
            (inter.author.id, inter.guild.id))
        if row:
            txt = "ожидает рассмотрения" if row["status"] == "pending" else "уже идёт"
            return await inter.response.send_message(f"ℹ️ У вас уже есть заявка/отпуск, он {txt}.", ephemeral=True)
        await inter.response.send_modal(VacationModal())

    @disnake.ui.button(label="Мой отпуск", emoji="📋",
                       style=disnake.ButtonStyle.secondary, custom_id="vac:status")
    async def status(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        row = await core.q_one(
            "SELECT * FROM vacations WHERE user_id=? AND guild_id=? ORDER BY id DESC LIMIT 1",
            (inter.author.id, inter.guild.id))
        if not row:
            return await inter.response.send_message("📭 У вас ещё не было заявок на отпуск.", ephemeral=True)
        names = {"pending": "🟡 ожидает рассмотрения", "active": "🟢 идёт", "rejected": "🔴 отклонена",
                 "ended": "⚪ завершён", "cancelled": "⚪ отменена"}
        text = f"**Статус:** {names.get(row['status'], row['status'])}\n**Срок:** {row['days']} дн."
        if row["status"] == "active" and row["end_ts"]:
            text += f"\n**Закончится:** <t:{row['end_ts']}:F> (<t:{row['end_ts']}:R>)"
        if row["status"] == "rejected" and row["reject_reason"]:
            text += f"\n**Причина отказа:** {row['reject_reason']}"
        await inter.response.send_message(text, ephemeral=True)

    @disnake.ui.button(label="Вернуться из отпуска", emoji="🔙",
                       style=disnake.ButtonStyle.danger, custom_id="vac:return")
    async def back(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        cog: "Vacation" = inter.bot.get_cog("Vacation")
        row = await core.q_one(
            "SELECT * FROM vacations WHERE user_id=? AND guild_id=? AND status IN ('active','pending') "
            "ORDER BY id DESC LIMIT 1", (inter.author.id, inter.guild.id))
        if not row:
            return await inter.response.send_message("ℹ️ У вас нет активного отпуска.", ephemeral=True)
        await cog.finish(row, reason="Досрочное возвращение", by=inter.author)
        await inter.response.send_message("✅ С возвращением! Роль отпуска снята.", ephemeral=True)


# ───────────────────────── РАССМОТРЕНИЕ (постоянная) ─────────────────────────

class RejectModal(disnake.ui.Modal):
    def __init__(self, message_id: int):
        self.message_id = message_id
        super().__init__(
            title="Отклонение заявки на отпуск", custom_id=f"vac:reject_modal:{message_id}",
            components=[disnake.ui.TextInput(
                label="Причина отказа", custom_id="reason",
                style=disnake.TextInputStyle.paragraph, max_length=300)],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        cog: "Vacation" = inter.bot.get_cog("Vacation")
        await cog.reject(inter, self.message_id, inter.text_values.get("reason", "—"))


class VacationReviewView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="Одобрить", emoji="✅", style=disnake.ButtonStyle.green, custom_id="vac:approve")
    async def approve(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not core.is_staff(inter.author):
            return await inter.response.send_message("❌ У вас нет прав рассматривать заявки.", ephemeral=True)
        await inter.bot.get_cog("Vacation").approve(inter)

    @disnake.ui.button(label="Отклонить", emoji="⛔", style=disnake.ButtonStyle.danger, custom_id="vac:reject")
    async def reject(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not core.is_staff(inter.author):
            return await inter.response.send_message("❌ У вас нет прав рассматривать заявки.", ephemeral=True)
        await inter.response.send_modal(RejectModal(inter.message.id))


# ───────────────────────── ПОДТВЕРЖДЕНИЕ ВЫГОВОРА ─────────────────────────

class WarnConfirmView(disnake.ui.View):
    def __init__(self, cog: "Vacation", issuer_id: int, targets: list):
        super().__init__(timeout=60)
        self.cog, self.issuer_id, self.targets = cog, issuer_id, targets

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:
        if inter.author.id != self.issuer_id:
            await inter.response.send_message("Это не ваше подтверждение.", ephemeral=True)
            return False
        return True

    @disnake.ui.button(label="Всё равно выдать", emoji="⚠️", style=disnake.ButtonStyle.danger)
    async def go(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        exp = time.time() + 120
        for t in self.targets:
            self.cog.allowed[(self.issuer_id, t)] = exp
        await inter.response.edit_message(
            content="✅ Подтверждено. **Повторите команду выговора в течение 2 минут** — на этот раз она пройдёт.",
            embed=None, view=None)
        self.stop()

    @disnake.ui.button(label="Отмена", emoji="✖️", style=disnake.ButtonStyle.secondary)
    async def cancel(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.edit_message(content="Выговор не выдан.", embed=None, view=None)
        self.stop()


# ───────────────────────── COG ─────────────────────────

class Vacation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.allowed: Dict[Tuple[int, int], float] = {}
        self._views_added = False
        try:
            bot.add_app_command_check(self._warn_guard, slash_commands=True)
        except Exception as e:  # старая версия disnake — защита выговоров недоступна, остальное работает
            print(f"⚠️ Защита выговоров не активна: {e!r}", flush=True)

    def cog_unload(self):
        try:
            self.bot.remove_app_command_check(self._warn_guard, slash_commands=True)
        except Exception:
            pass
        self.expiry_loop.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._views_added:
            self.bot.add_view(VacationPanelView())
            self.bot.add_view(VacationReviewView())
            self._views_added = True
        if not self.expiry_loop.is_running():
            self.expiry_loop.start()

    # ───── публикация панели ─────

    def panel_embed(self) -> disnake.Embed:
        lo = core.get("vacation.min_days", 1)
        hi = core.get("vacation.max_days", 30)
        e = core.make_embed(
            "🌴 Отпуск в семье",
            "Нужно ненадолго отойти от игры? Подайте заявку — руководство рассмотрит её, "
            "а при одобрении вам автоматически выдастся роль отпуска и снимется по окончании срока.\n\n"
            f"**Как это работает:**\n"
            f"1️⃣ Нажмите **«Подать заявку на отпуск»**\n"
            f"2️⃣ Укажите срок ({lo}–{hi} дн.) и причину\n"
            f"3️⃣ Дождитесь решения руководства — придёт сообщение в личные\n"
            f"4️⃣ Вернуться раньше можно кнопкой **«Вернуться из отпуска»**\n\n"
            "На время отпуска вы освобождены от проверок активности и взносов.")
        return e

    async def publish_panel(self, guild: disnake.Guild) -> str:
        cid = int(core.get("vacation.panel_channel_id", 0) or 0)
        channel = guild.get_channel(cid) if cid else None
        if channel is None:
            return "❌ Сначала выберите **канал панели подачи заявок**."
        mid = int(core.get("vacation.panel_message_id", 0) or 0)
        if mid:
            try:
                msg = await channel.fetch_message(mid)
                await msg.edit(embed=self.panel_embed(), view=VacationPanelView())
                return f"✅ Панель обновлена: {msg.jump_url}"
            except Exception:
                pass
        try:
            msg = await channel.send(embed=self.panel_embed(), view=VacationPanelView())
        except Exception as e:
            return f"❌ Не удалось отправить сообщение: {e}"
        core.set_value("vacation.panel_message_id", msg.id)
        return f"✅ Панель опубликована: {msg.jump_url}"

    # ───── заявка ─────

    async def handle_submit(self, inter: disnake.ModalInteraction, values: dict):
        lo = int(core.get("vacation.min_days", 1) or 1)
        hi = int(core.get("vacation.max_days", 30) or 30)
        raw = (values.get("days") or "").strip()
        if not raw.isdigit() or not (lo <= int(raw) <= hi):
            return await inter.response.send_message(f"❌ Срок должен быть числом от {lo} до {hi}.", ephemeral=True)
        days = int(raw)
        reason = (values.get("reason") or "").strip()
        contact = (values.get("contact") or "").strip()
        review_id = int(core.get("vacation.review_channel_id", 0) or 0)
        channel = inter.guild.get_channel(review_id) if review_id else None
        if channel is None:
            return await inter.response.send_message("⚠️ Канал рассмотрения не настроен.", ephemeral=True)

        vid = await core.q_exec(
            "INSERT INTO vacations (guild_id, user_id, days, reason, contact, status, created_ts) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (inter.guild.id, inter.author.id, days, reason, contact, core.now_ts()))

        rank = core.member_rank(inter.author)
        e = core.make_embed(f"🌴 Заявка на отпуск #{vid}", kind="warn")
        e.description = f"Участник {inter.author.mention} просит отпуск.\nСтатус: 🟡 **Ожидает рассмотрения**"
        e.add_field("Срок", f"**{days}** дн.", inline=True)
        if rank:
            e.add_field("Ранг", f"{core.rank_name(rank)} ({rank})", inline=True)
        e.add_field("Причина", core.clip(reason, 900), inline=False)
        if contact:
            e.add_field("Связь", core.clip(contact, 200), inline=False)
        e.set_thumbnail(url=inter.author.display_avatar.url)

        ping = " ".join(f"<@&{r}>" for r in core.ids_list("vacation.reviewer_roles"))
        try:
            msg = await channel.send(content=ping or None, embed=e, view=VacationReviewView(),
                                     allowed_mentions=disnake.AllowedMentions(roles=True))
        except Exception as ex:
            await core.q_exec("DELETE FROM vacations WHERE id=?", (vid,))
            return await inter.response.send_message(f"❌ Не удалось отправить заявку: {ex}", ephemeral=True)
        await core.q_exec("UPDATE vacations SET message_id=?, channel_id=? WHERE id=?", (msg.id, channel.id, vid))
        await inter.response.send_message("✅ Заявка отправлена руководству. Решение придёт в личные сообщения.",
                                          ephemeral=True)

    async def _row_by_message(self, message_id: int):
        return await core.q_one("SELECT * FROM vacations WHERE message_id=?", (message_id,))

    async def approve(self, inter: disnake.MessageInteraction):
        row = await self._row_by_message(inter.message.id)
        if not row or row["status"] != "pending":
            return await inter.response.send_message("ℹ️ Эта заявка уже обработана.", ephemeral=True)
        member = inter.guild.get_member(row["user_id"])
        role_id = int(core.get("vacation.role_id", 0) or 0)
        role = inter.guild.get_role(role_id) if role_id else None
        if role is None:
            return await inter.response.send_message("⚠️ Не настроена **роль отпуска** (/центр → Отпуск).", ephemeral=True)
        start = core.now_ts()
        end = start + row["days"] * 86400
        await core.q_exec(
            "UPDATE vacations SET status='active', reviewer_id=?, start_ts=?, end_ts=? WHERE id=?",
            (inter.author.id, start, end, row["id"]))
        note = ""
        if member:
            try:
                await member.add_roles(role, reason=f"Отпуск одобрен: {inter.author}")
            except Exception as ex:
                note = f"\n⚠️ Роль выдать не удалось: {ex}"
        else:
            note = "\n⚠️ Участник уже не на сервере."
        emb = inter.message.embeds[0] if inter.message.embeds else core.make_embed()
        emb.color = core.color("ok")
        emb.description = (f"Участник <@{row['user_id']}> получил отпуск.\n"
                           f"Статус: 🟢 **Одобрено** · {inter.author.mention}")
        emb.add_field("До", f"<t:{end}:F> (<t:{end}:R>)", inline=False)
        await inter.response.edit_message(content=None, embed=emb, view=None)
        if note:
            await inter.followup.send(note.strip(), ephemeral=True)
        await self._dm(row["user_id"], core.make_embed(
            "🌴 Отпуск одобрен",
            f"Руководитель {inter.author.mention} одобрил ваш отпуск на **{row['days']} дн.**\n"
            f"Закончится: <t:{end}:F>. Роль снимется автоматически.\n\nОтличного отдыха! 💎", kind="ok"))
        await core.send_log(inter.guild, "vacation", core.make_embed(
            "🌴 Отпуск одобрен", f"<@{row['user_id']}> — {row['days']} дн. Одобрил {inter.author.mention}", kind="ok"))

    async def reject(self, inter: disnake.ModalInteraction, message_id: int, reason: str):
        row = await self._row_by_message(message_id)
        if not row or row["status"] != "pending":
            return await inter.response.send_message("ℹ️ Эта заявка уже обработана.", ephemeral=True)
        await core.q_exec(
            "UPDATE vacations SET status='rejected', reviewer_id=?, reject_reason=? WHERE id=?",
            (inter.author.id, reason, row["id"]))
        try:
            msg = await inter.channel.fetch_message(message_id)
            emb = msg.embeds[0] if msg.embeds else core.make_embed()
            emb.color = core.color("err")
            emb.description = (f"Участник <@{row['user_id']}> просил отпуск.\n"
                               f"Статус: 🔴 **Отклонено** · {inter.author.mention}")
            emb.add_field("Причина отказа", core.clip(reason, 500), inline=False)
            await msg.edit(content=None, embed=emb, view=None)
        except Exception:
            pass
        await inter.response.send_message("✅ Заявка отклонена, участнику отправлено уведомление.", ephemeral=True)
        await self._dm(row["user_id"], core.make_embed(
            "🌴 Заявка на отпуск отклонена",
            f"Руководитель {inter.author.mention} отклонил заявку.\n📝 **Причина:** {reason}\n\n"
            "Вы можете подать новую заявку позже.", kind="err"))
        await core.send_log(inter.guild, "vacation", core.make_embed(
            "🌴 Отпуск отклонён", f"<@{row['user_id']}> · {inter.author.mention}\nПричина: {core.clip(reason, 300)}",
            kind="err"))

    async def finish(self, row: dict, reason: str, by: Optional[disnake.Member] = None):
        """Завершает отпуск: снимает роль, пишет статус и лог."""
        await core.q_exec("UPDATE vacations SET status='ended', end_ts=? WHERE id=?",
                          (min(core.now_ts(), row.get("end_ts") or core.now_ts()), row["id"]))
        guild = self.bot.get_guild(row["guild_id"])
        if guild is None:
            return
        member = guild.get_member(row["user_id"])
        role_id = int(core.get("vacation.role_id", 0) or 0)
        role = guild.get_role(role_id) if role_id else None
        if member and role and role in member.roles:
            try:
                await member.remove_roles(role, reason=f"Отпуск завершён: {reason}")
            except Exception:
                pass
        await core.send_log(guild, "vacation", core.make_embed(
            "🌴 Отпуск завершён", f"<@{row['user_id']}> · {reason}" + (f" · {by.mention}" if by else "")))

    async def _dm(self, user_id: int, embed: disnake.Embed):
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            await user.send(embed=embed)
        except Exception:
            pass

    # ───── автоокончание ─────

    @tasks.loop(seconds=60)
    async def expiry_loop(self):
        now = core.now_ts()
        rows = await core.q_all("SELECT * FROM vacations WHERE status='active'")
        for row in rows:
            end = row["end_ts"] or 0
            if end and end <= now:
                await self.finish(row, reason="Срок истёк")
                await self._dm(row["user_id"], core.make_embed(
                    "🌴 Отпуск закончился", "Роль отпуска снята. С возвращением в строй! 💎", kind="ok"))
            elif end and end - now <= 86400 and not row["reminded"] and row["days"] > 1:
                await core.q_exec("UPDATE vacations SET reminded=1 WHERE id=?", (row["id"],))
                await self._dm(row["user_id"], core.make_embed(
                    "🌴 Отпуск скоро закончится", f"Ваш отпуск завершится <t:{end}:R>.", kind="warn"))

    @expiry_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    # ───── защита выговоров ─────

    @staticmethod
    def _targets(inter: disnake.ApplicationCommandInteraction) -> list:
        found = []
        try:
            stack = [getattr(inter, "filled_options", None) or {}]
            while stack:
                cur = stack.pop()
                for v in cur.values():
                    if isinstance(v, dict):
                        stack.append(v)
                    elif isinstance(v, disnake.Member):
                        found.append(v)
        except Exception:
            pass
        if found:
            return found
        try:
            res = inter.data.resolved
            known = set(getattr(res, "members", {}) or {}) | set(getattr(res, "users", {}) or {})
            ids, stack = set(), list(inter.data.options)
            while stack:
                o = stack.pop()
                if getattr(o, "options", None):
                    stack.extend(o.options)
                elif str(getattr(o, "value", "")).isdigit() and int(o.value) in known:
                    ids.add(int(o.value))
            for i in ids:
                m = inter.guild.get_member(i)
                if m:
                    found.append(m)
        except Exception:
            pass
        return found

    async def _warn_guard(self, inter: disnake.ApplicationCommandInteraction) -> bool:
        try:
            cfg = core.get("warn_guard", {}) or {}
            if not cfg.get("enabled") or inter.guild is None:
                return True
            name = (getattr(inter.application_command, "qualified_name", None) or inter.data.name or "").lower()
            words = [w.strip().lower() for w in str(cfg.get("keywords", "")).split(",") if w.strip()]
            if not any(w in name for w in words):
                return True
            paused = [m for m in self._targets(inter) if core.pause_kind(m)]
            if not paused:
                return True
            now = time.time()
            self.allowed = {k: v for k, v in self.allowed.items() if v > now}
            if all((inter.author.id, m.id) in self.allowed for m in paused):
                for m in paused:
                    self.allowed.pop((inter.author.id, m.id), None)
                return True
            lines = []
            for m in paused:
                kind = "🌴 в отпуске" if core.pause_kind(m) == "vacation" else "💤 в АФК"
                lines.append(f"• {m.mention} — {kind}")
            e = core.make_embed(
                "⚠️ Вы точно хотите выдать выговор?",
                "У этого участника сейчас роль отпуска/АФК:\n" + "\n".join(lines) +
                "\n\nОн может не видеть сообщений и не иметь возможности отреагировать.", kind="warn")
            view = WarnConfirmView(self, inter.author.id, [m.id for m in paused])
            await inter.response.send_message(embed=e, view=view, ephemeral=True)
        except Exception:
            return True
        raise WarnGuardBlocked("target on vacation/afk")


def setup(bot: commands.Bot):
    bot.add_cog(Vacation(bot))
