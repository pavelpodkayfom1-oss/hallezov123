"""💰 Баллы, ⚠️ Выговоры, 🎖 Ранг, 🪪 Ник и 📢 Явка сбора — приказы без слэш-аргументов.

Всё, что требует «кому, сколько, за что», раньше жило только своими командами: из /панель
и с сайта руководить составом было нельзя, потому что экран просил аргумент, а подставить
его некуда. Здесь то же самое сделано формами: открываешь экран → жмёшь кнопку → пишешь ник,
статик или ID. Экран открывается без аргументов и отвечает своими кнопками, поэтому он
одинаково работает и в Discord, и в веб-панели.

Своих полов не делаем: баллы и выговоры пишет database, ранг и роли — StaffTools, явку сбора
рисует teg_panel. Если модуль-хозяин выключен, экран честно об этом говорит, а не молчит.
"""
import re
import sys
import uuid
from typing import Any, Dict, List, Tuple

import aiosqlite
import disnake
from disnake.ext import commands

from database import (DB_PATH, add_points, add_warn, get_active_warns, list_family_members,
                      points_top, remove_warn, spend_points, upsert_member)
from utils.checks import is_admin, load_config, rank_title
from utils.embeds import base_embed, note_embed

TIMEOUT = 600
MAX_POINTS = 10000

# kind формы → (заголовок, [(custom_id, label, placeholder, max_length, многострочная)])
FORMS: Dict[str, Tuple[str, List[Tuple[str, str, str, int, bool]]]] = {
    "give": ("💰 Начислить баллы", [
        ("who", "Кому (ник, статик или ID)", "Tommy / 123456789012345678", 100, False),
        ("amount", "Сколько", "например, 50", 6, False),
        ("why", "За что", "дежурство на сборе", 200, True)]),
    "spend": ("💸 Списать баллы", [
        ("who", "Кому (ник, статик или ID)", "Tommy / 123456789012345678", 100, False),
        ("amount", "Сколько", "например, 30", 6, False),
        ("why", "За что", "пропуск сбора без предупреждения", 200, True)]),
    "warn": ("⚠️ Выдать выговор", [
        ("who", "Кому (ник, статик или ID)", "Tommy / 123456789012345678", 100, False),
        ("why", "Причина", "опоздал на сбор на 40 минут", 400, True)]),
    "warnoff": ("✂️ Снять выговор", [
        ("wid", "Номер выговора из списка", "например, 17", 8, False)]),
    "rank": ("🎖 Поставить ранг", [
        ("who", "Кому (ник, статик или ID)", "Tommy / 123456789012345678", 100, False),
        ("rank", "Номер ранга", "например, 3", 4, False)]),
    "nick": ("🪪 Сменить ник", [
        ("who", "Кому (ник, статик или ID)", "Tommy / 123456789012345678", 100, False),
        ("nick", "Новый ник в Discord", "Tommy Hallez", 32, False)]),
}


async def active_warns(limit: int = 12) -> Tuple[List[Dict[str, Any]], int]:
    """Все активные выговоры сразу: get_active_warns умеет только по одному участнику."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT COUNT(*) AS c FROM warns WHERE active = 1") as cur:
            total = (await cur.fetchone())["c"]
        async with db.execute(
            "SELECT w.id, w.user_id, w.reason, w.created_at, COALESCE(m.nick, '—') AS nick "
            "FROM warns w LEFT JOIN family_members m ON m.user_id = w.user_id "
            "WHERE w.active = 1 ORDER BY w.id DESC LIMIT ?", (limit,)) as cur:
            return [dict(r) for r in await cur.fetchall()], total


def text_of(inter, key: str) -> str:
    """Значение поля формы: в disnake это строка, в веб-панели тоже, но береём устойчиво."""
    raw = (getattr(inter, "text_values", None) or {}).get(key)
    return str(getattr(raw, "value", raw) or "").strip()


def rank_nums(cfg: Dict[str, Any]) -> List[int]:
    """Ранги из config.json: ключи могут быть и не числами, их пропускаем."""
    out = set()
    for key in (cfg.get("ranks", {}) or {}):
        try:
            out.add(int(key))
        except (TypeError, ValueError):
            pass
    return sorted(out | {1})


async def roster() -> List[Dict[str, Any]]:
    try:
        return await list_family_members()
    except Exception:
        return []


async def find_target(guild, query: str) -> Tuple[List[Dict[str, Any]], str]:
    """Ник, статик, ID или упоминание → строки состава. Второй элемент — ошибка, если не нашлось."""
    rows = await roster()
    q = (query or "").strip()
    m = re.fullmatch(r"<@!?(\d+)>", q)
    if m:
        q = m.group(1)
    if q.isdigit():
        found = [r for r in rows if str(r["user_id"]) == q]
        if found:
            return found, ""
        member = guild.get_member(int(q)) if guild else None
        if member is None or member.bot:
            return [], "на сервере никого с таким ID"
        return [{"user_id": member.id, "nick": member.display_name,
                 "static_id": "—", "rank": 0}], ""
    if not q:
        return [], "поле «Кому» пустое"
    exact = [r for r in rows if (r.get("static_id") or "").lower() == q.lower()]
    if exact:
        return exact, ""
    return [r for r in rows if q.lower() in (r.get("nick") or "").lower()][:8], "под такое имя в составе никого"


def line(row: Dict[str, Any]) -> str:
    return f"**{row.get('nick') or '—'}** · `{row.get('static_id') or '—'}` · <@{row['user_id']}>"


class OpsButton(disnake.ui.Button):
    """Кнопка экрана: свой обработчик, как в хабе — их набирает OpsView."""

    def __init__(self, handler, **kw):
        super().__init__(**kw)
        self._handler = handler

    async def callback(self, inter: disnake.MessageInteraction):
        await self._handler(inter)


class OpsView(disnake.ui.View):
    """Один и тот же набор кнопок для Discord и для сайта: «действие» открывает форму,
    «Обновить» перерисовывает экран, «Отмена» закрывает панель."""

    def __init__(self, cog, owner_id: int, screen: str,
                 forms: List[Tuple[str, str, str]]):
        super().__init__(timeout=TIMEOUT)
        self.cog, self.owner_id, self.screen = cog, owner_id, screen
        items = [(label, emoji, kind, disnake.ButtonStyle.primary) for kind, label, emoji in forms]
        items.append(("Обновить", "🔄", "refresh", disnake.ButtonStyle.secondary))
        items.append(("В меню", "⬅️", "menu", disnake.ButtonStyle.secondary))
        for i, (label, emoji, kind, style) in enumerate(items):
            self.add_item(OpsButton(lambda inter, k=kind: self.press(inter, k),
                                    label=label, emoji=emoji, style=style, row=min(i // 3, 1)))

    async def press(self, inter, kind: str):
        if inter.author.id != self.owner_id:
            return await inter.response.send_message(
                embed=note_embed("⛔ Этот экран открыл не ты — набери свою `/панель`."), ephemeral=True)
        if not is_admin(inter):
            return await inter.response.send_message(embed=note_embed("⛔ Недостаточно прав."),
                                                     ephemeral=True)
        if kind == "menu":
            hub = sys.modules.get("cogs.admin_hub")
            if hub is None:
                return await inter.response.send_message(
                    embed=note_embed("⚠️ Каталог экранов (cogs.admin_hub) выключен."), ephemeral=True)
            return await inter.response.edit_message(embed=hub.hub_embed(guild=inter.guild),
                                                     view=hub.HubView(self.owner_id))
        if kind == "refresh":
            embed, view = await self.cog.render(inter, self.screen)
            return await inter.response.edit_message(embed=embed, view=view)
        title, fields = FORMS[kind]
        modal = OpsModal(kind, self.screen, title, fields)
        return await inter.response.send_modal(modal)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class OpsModal(disnake.ui.Modal):
    """Форма приказа. Поля задаются каталогом FORMS, обрабатывает их ког."""

    def __init__(self, kind: str, screen: str, title: str, fields):
        super().__init__(
            title=title[:45], custom_id=f"ops:{kind}",
            components=[disnake.ui.TextInput(
                label=label[:45], custom_id=cid, placeholder=ph[:100] or None,
                max_length=length, required=True,
                style=disnake.TextInputStyle.paragraph if long else disnake.TextInputStyle.short)
                for cid, label, ph, length, long in fields])
        self.kind, self.screen = kind, screen

    async def callback(self, inter: disnake.ModalInteraction):
        cog = inter.bot.get_cog("AdminOps")
        if cog is None:
            return await inter.response.send_message(
                embed=note_embed("⚠️ Модуль приказов выключен — отмени и открой `/панель`."),
                ephemeral=True)
        await cog.apply(inter, self.kind, self.screen)


class AdminOps(commands.Cog):
    """Экраны, которыми руководят: баллы, выговоры, ранг, ник и явка сбора."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ───────────────────────── отдача экрана ─────────────────────────
    async def render(self, inter, screen: str, note: str = ""):
        build = getattr(self, f"screen_{screen}")
        return await build(inter, note)

    async def _show(self, inter, screen: str):
        if not is_admin(inter):
            return await inter.response.send_message(
                embed=note_embed("⛔ Недостаточно прав."), ephemeral=True)
        embed, view = await self.render(inter, screen)
        await inter.response.send_message(embed=embed, view=view, ephemeral=True)

    # ───────────────────────── 1. баллы ─────────────────────────
    async def screen_points(self, inter, note: str = ""):
        rows = {r["user_id"]: r for r in await roster()}
        top = await points_top(8)
        lines = [f"{i}. **{(rows.get(t['user_id']) or {}).get('nick') or '—'}** — "
                 f"{t['points']} 💰 (заработано {t['earned']})" for i, t in enumerate(top, 1)]
        e = base_embed("💰 Баллы",
                       "Начислить или списать: кнопка → «кому, сколько, за что». Запись "
                       "попадает в историю баллов и в топ состава.")
        e.add_field(name="🏆 Топ по заработку", value="\n".join(lines) or "баллов пока нет",
                    inline=False)
        if note:
            e.add_field(name="Последнее действие", value=note[:900], inline=False)
        return e, OpsView(self, inter.author.id, "points",
                          [("give", "Начислить", "➕"), ("spend", "Списать", "➖")])

    async def do_points(self, inter, kind: str):
        rows, err = await find_target(inter.guild, text_of(inter, "who"))
        if not rows:
            return await self.screen_points(inter, f"❌ Никого не нашлось: {err}.")
        if len(rows) > 1:
            return await self.screen_points(
                inter, "⚠️ Слишком похоже:\n" + "\n".join(line(r) for r in rows[:6]))
        row = rows[0]
        amount = int(re.sub(r"\D", "", text_of(inter, "amount")) or 0)
        if not 1 <= amount <= MAX_POINTS:
            return await self.screen_points(inter, f"⚠️ «{amount}» — нужно от 1 до {MAX_POINTS}.")
        why = text_of(inter, "why")[:200] or "из панели приказов"
        uid = row["user_id"]
        ref = f"ops:{kind}:{uuid.uuid4().hex[:10]}"
        if kind == "give":
            balance = await add_points(uid, amount, why, ref)
            note = f"➕ {row['nick']}: **+{amount}**, баланс **{balance}** · «{why}»"
        else:
            balance = await spend_points(uid, amount, why, ref)
            if balance is None:
                return await self.screen_points(
                    inter, f"❌ Списать не вышло: у {row['nick']} не хватает баллов.")
            note = f"➖ {row['nick']}: **−{amount}**, баланс **{balance}** · «{why}»"
        await self._log("💰 Баллы", f"{line(row)}\n**{note}**\nАвтор: <@{inter.author.id}>")
        return await self.screen_points(inter, note)

    # ───────────────────────── 2. выговоры ─────────────────────────
    async def screen_warns(self, inter, note: str = ""):
        rows, total = await active_warns()
        cfg = load_config()
        limit = int(cfg.get("warn_limit", 3) or 3)
        lines = [f"`#{w['id']}` **{w['nick']}** — {str(w['reason'])[:90]}" for w in rows]
        e = base_embed("⚠️ Выговоры",
                       "Выдать или снять, не выходя из панели. При выдаче участник получает "
                       "уведомление в личку, а случай на пределе — в логи семьи.")
        e.add_field(name=f"🚨 Активных: {total} (предел {limit})",
                    value=("\n".join(lines) or "чисто")[:1000], inline=False)
        if note:
            e.add_field(name="Последнее действие", value=note[:900], inline=False)
        return e, OpsView(self, inter.author.id, "warns",
                          [("warn", "Выдать", "⚠️"), ("warnoff", "Снять", "✂️")])

    async def do_warn(self, inter):
        rows, err = await find_target(inter.guild, text_of(inter, "who"))
        if not rows:
            return await self.screen_warns(inter, f"❌ Никого не нашлось: {err}.")
        if len(rows) > 1:
            return await self.screen_warns(
                inter, "⚠️ Слишком похоже:\n" + "\n".join(line(r) for r in rows[:6]))
        row = rows[0]
        why = text_of(inter, "why")[:400] or "без указания причины"
        staff = self.bot.get_cog("StaffTools")
        if staff is not None and inter.guild is not None:
            note = "⚠️ " + await staff.issue_warn(inter.guild, row["user_id"],
                                                 inter.author.id, why)
        else:
            wid = await add_warn(row["user_id"], inter.author.id, why)
            active = await get_active_warns(row["user_id"])
            note = (f"⚠️ Выговор `#{wid}` выдан: {row['nick']} — «{why}». "
                    f"Активных: {len(active)} (модуль staff_tools выключен, "
                    "личкой и в логи не писали).")
        return await self.screen_warns(inter, note)

    async def do_warnoff(self, inter):
        wid = re.sub(r"\D", "", text_of(inter, "wid"))
        if not wid:
            return await self.screen_warns(inter, "⚠️ Номер выговора — это цифры из списка, например `17`.")
        ok = await remove_warn(int(wid))
        note = (f"✂️ Выговор `#{wid}` снят." if ok
                else f"❌ Выговора `#{wid}` нет среди активных — обнови экран.")
        await self._log("✂️ Выговор снят",
                        f"`#{wid}` снял <@{inter.author.id}>" + ("" if ok else " · не найден"))
        return await self.screen_warns(inter, note)

    # ───────────────────────── 3. ранг ─────────────────────────
    async def screen_rank(self, inter, note: str = ""):
        cfg = load_config()
        ranks = cfg.get("ranks", {}) or {}
        nums = rank_nums(cfg)
        ladder = "\n".join(
            f"**{n}** — {rank_title(cfg, n)}"
            + (f" · роль `{(ranks.get(str(n)) or {}).get('role_id')}`"
               if (ranks.get(str(n)) or {}).get("role_id") else "")
            for n in nums)
        rows = await roster()
        byrank: Dict[int, int] = {}
        for r in rows:
            byrank[r.get("rank") or 0] = byrank.get(r.get("rank") or 0, 0) + 1
        e = base_embed("🎖 Ранги",
                       "Ранг меняем числом: база, роль в Discord и лог семьи обновляются вместе. "
                       "Крайние ранги команда не тронет.")
        e.add_field(name="📋 Шкала", value=ladder[:1000], inline=False)
        e.add_field(name="👥 Сейчас в составе",
                    value="\n".join(f"{rank_title(cfg, n)}: **{byrank[n]}**"
                                    for n in sorted(byrank)) or "пусто", inline=False)
        if note:
            e.add_field(name="Последнее действие", value=note[:900], inline=False)
        return e, OpsView(self, inter.author.id, "rank", [("rank", "Поставить ранг", "🎖")])

    async def do_rank(self, inter):
        rows, err = await find_target(inter.guild, text_of(inter, "who"))
        if not rows:
            return await self.screen_rank(inter, f"❌ Никого не нашлось: {err}.")
        if len(rows) > 1:
            return await self.screen_rank(inter, "⚠️ Слишком похоже:\n"
                                                + "\n".join(line(r) for r in rows[:6]))
        row = rows[0]
        cfg = load_config()
        nums = rank_nums(cfg)
        new = int(re.sub(r"\D", "", text_of(inter, "rank")) or 0)
        if new not in nums:
            return await self.screen_rank(
                inter, f"⚠️ Ранга {new} нет. Есть: {', '.join(str(n) for n in nums)}.")
        if row.get("static_id") in (None, "—"):
            return await self.screen_rank(
                inter, f"❌ {row['nick']} не в составе семьи — сначала примите заявку.")
        staff = self.bot.get_cog("StaffTools")
        if staff is not None and inter.guild is not None:
            how = await staff.apply_rank(inter.guild, row, new)
            note = f"🎖 {row['nick']}: {rank_title(cfg, row['rank'])} → **{rank_title(cfg, new)}** ({how})."
        else:
            await upsert_member(row["user_id"], row["nick"], row["static_id"], new)
            note = (f"🎖 {row['nick']}: ранг записан в базу — **{rank_title(cfg, new)}**, "
                    "но roles не менялись: модуль staff_tools выключен.")
        await self._log("🎖 Смена ранга", f"{line(row)}\n{note}\nАвтор: <@{inter.author.id}>")
        return await self.screen_rank(inter, note)

    # ───────────────────────── 4. ник ─────────────────────────
    async def screen_nick(self, inter, note: str = ""):
        rows = await roster()
        guild = inter.guild
        out_of_sync = []
        for r in rows:
            member = guild.get_member(r["user_id"]) if guild else None
            if member and (member.display_name or "") != (r.get("nick") or ""):
                out_of_sync.append((r, member.display_name))
        lines = [f"**{r['nick'] or '—'}** → Discord: **{dn}**" for r, dn in out_of_sync[:10]]
        e = base_embed("🪪 Ники",
                       "Ник в Discord и запись в составе должны совпадать: меняем по форме, "
                       "«подтянуть» — правим базу под то, что человек уже показал в сервере.")
        e.add_field(name=f"🔎 Расхождений: {len(out_of_sync)}",
                    value=("\n".join(lines) or "все ники совпадают")[:1000], inline=False)
        if note:
            e.add_field(name="Последнее действие", value=note[:900], inline=False)
        return e, OpsView(self, inter.author.id, "nick", [("nick", "Сменить ник", "✏️")])

    async def do_nick(self, inter):
        rows, err = await find_target(inter.guild, text_of(inter, "who"))
        if not rows:
            return await self.screen_nick(inter, f"❌ Никого не нашлось: {err}.")
        if len(rows) > 1:
            return await self.screen_nick(inter, "⚠️ Слишком похоже:\n"
                                                 + "\n".join(line(r) for r in rows[:6]))
        row = rows[0]
        nick = text_of(inter, "nick")[:32]
        if not nick:
            return await self.screen_nick(inter, "⚠️ Новый ник пустой.")
        member = inter.guild.get_member(row["user_id"]) if inter.guild else None
        if member is None:
            return await self.screen_nick(inter, f"❌ {row['nick']} нет на сервере — некому менять ник.")
        try:
            await member.edit(nick=nick, reason=f"Ник из панели приказов: {inter.author}")
        except disnake.Forbidden:
            note = f"❌ У бота нет прав менять ник у {member.mention} (иерархия ролей)."
            return await self.screen_nick(inter, note)
        await upsert_member(row["user_id"], nick, row["static_id"], row["rank"])
        note = f"🪪 {row['nick']} → **{nick}**, запись в составе тоже обновлена."
        await self._log("🪪 Смена ника", f"<@{row['user_id']}>\n{note}\nАвтор: <@{inter.author.id}>")
        return await self.screen_nick(inter, note)

    # ───────────────────────── 5. явка сбора ─────────────────────────
    async def screen_calls(self, inter, note: str = ""):
        teg = sys.modules.get("cogs.teg_panel")
        if teg is None:
            return base_embed("📢 Сборы", "⚠️ Модуль сборов (cogs.teg_panel) выключен — "
                                          "явку не показать."), None
        async with teg.tdb() as db:
            async with db.execute("SELECT id, title, created_at FROM teg_calls "
                                  "ORDER BY id DESC LIMIT 1") as cur:
                last = await cur.fetchone()
        if last is None:
            return base_embed("📢 Сборы", "Сборов через панель ещё не объявляли — "
                                          "объяви его командой `/teg` или кнопкой «📤 Teg: кнопка в канал»."), None
        embed = await teg.build_report(int(last["id"]))
        if embed is None:
            return base_embed("📢 Сборы", "Последний сбор не читается — обнови экран."), None
        embed.description = (embed.description or "") + \
            "\n🔔 «Напомнить не ответившим» разошлёт личками по тем, кто получил сбор и молчит."
        if note:
            embed.add_field(name="Действие", value=note[:900], inline=False)
        return embed, teg.ReportView(int(last["id"]))

    # ───────────────────────── диспетчер форм ─────────────────────────
    async def apply(self, inter, kind: str, screen: str):
        try:
            handler = {"give": lambda: self.do_points(inter, "give"),
                       "spend": lambda: self.do_points(inter, "spend"),
                       "warn": lambda: self.do_warn(inter),
                       "warnoff": lambda: self.do_warnoff(inter),
                       "rank": lambda: self.do_rank(inter),
                       "nick": lambda: self.do_nick(inter)}[kind]()
            embed, view = await handler
        except Exception as error:
            embed, view = note_embed(f"❌ Не вышло: `{type(error).__name__}: {error}`"), None
        await inter.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _log(self, title: str, desc: str):
        """В лог семьи, если хозяин логов на месте; без него приказ всё равно исполняется."""
        staff = self.bot.get_cog("StaffTools")
        if staff is not None:
            try:
                await staff.log(title, desc)
                return
            except Exception:
                pass
        print(f"🧾 Приказы: {title} — {desc[:160]}", flush=True)

    # ───────────────────────── команды ─────────────────────────
    @commands.slash_command(name="баллы", description="Начислить или списать баллы, посмотреть топ",
                            contexts=disnake.InteractionContextTypes(guild=True))
    @commands.guild_only()
    async def points(self, inter: disnake.ApplicationCommandInteraction):
        await self._show(inter, "points")

    @commands.slash_command(name="выговоры", description="Активные выговоры семьи, выдать и снять",
                            contexts=disnake.InteractionContextTypes(guild=True))
    @commands.guild_only()
    async def warns(self, inter: disnake.ApplicationCommandInteraction):
        await self._show(inter, "warns")

    @commands.slash_command(name="ранг", description="Поставить бойцу ранг и роль",
                            contexts=disnake.InteractionContextTypes(guild=True))
    @commands.guild_only()
    async def rank(self, inter: disnake.ApplicationCommandInteraction):
        await self._show(inter, "rank")

    @commands.slash_command(name="ник", description="Сменить ник бойцу и в составе",
                            contexts=disnake.InteractionContextTypes(guild=True))
    @commands.guild_only()
    async def nick(self, inter: disnake.ApplicationCommandInteraction):
        await self._show(inter, "nick")

    @commands.slash_command(name="сборы", description="Явка последнего сбора и напоминание молчунам",
                            contexts=disnake.InteractionContextTypes(guild=True))
    @commands.guild_only()
    async def calls(self, inter: disnake.ApplicationCommandInteraction):
        await self._show(inter, "calls")


def setup(bot: commands.Bot):
    bot.add_cog(AdminOps(bot))
