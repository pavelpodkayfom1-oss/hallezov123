"""Наказания семьи: таймаут (мут) по кнопкам и журнал.

До этого бот умел только *писать* про баны в лог: реального `timeout` никто не выставлял,
поэтому модерация шла руками в Discord. Здесь — панель, где выбирают бойца, срок и причину,
а бот ставит таймаут, шлёт нарушителю ЛС и заводит журнал (punishments.json).

Лимит Discord — 28 дней, поэтому срок проверяется до запроса, а не через ошибку API.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import disnake
from disnake.ext import commands

from utils.checks import get_text, is_admin, load_config
from utils.embeds import card, list_embed, note_embed

MSK = timezone(timedelta(hours=3))
MAX_MINUTES = 28 * 24 * 60
STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "punishments.json")
PRESETS = ("5 минут", "15 минут", "1 час", "6 часов", "24 часа")


def _load() -> Dict[str, dict]:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: Dict[str, dict]) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def _log_add(entry: dict) -> None:
    data = _load()
    data[str(entry["id"])] = entry
    if len(data) > 500:
        for key in sorted(data, key=lambda k: int(k))[: len(data) - 500]:
            data.pop(key, None)
    _save(data)


def _minutes(raw: str) -> Optional[int]:
    """'15', '1:30', '6h', '2d' → минуты. Ничего похожего на срок — None."""
    text = str(raw or "").strip().lower().replace(",", ".")
    if not text:
        return None
    if ":" in text:
        parts = text.split(":")
        try:
            return int(parts[0]) * 60 + int(parts[1])
        except Exception:
            return None
    num = "".join(ch for ch in text if (ch.isdigit() or ch == "."))
    if not num:
        return None
    try:
        value = float(num)
    except ValueError:
        return None
    if any(w in text for w in ("д", "d")):
        value *= 1440
    elif any(w in text for w in ("ч", "h")):
        value *= 60
    return int(value)


def _human(minutes: int) -> str:
    d, rem = divmod(minutes, 1440)
    h, m = divmod(rem, 60)
    out = []
    if d:
        out.append(f"{d} сут")
    if h:
        out.append(f"{h} ч")
    if m or not out:
        out.append(f"{m} мин")
    return " ".join(out)


def _when(minutes: int) -> str:
    """Когда освободят — Москва и живое <t::R>, чтобы не считать самому."""
    end = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    ts = int(end.timestamp())
    return f"<t:{ts}:R> · <t:{ts}:F>"


class TimeoutModal(disnake.ui.Modal):
    def __init__(self, target_id: int):
        self.target_id = target_id
        super().__init__(
            title=get_text("mod_modal_title", "Таймаут: срок и причина"),
            custom_id=f"hzmod:to:{target_id}",
            components=[
                disnake.ui.TextInput(
                    label=get_text("mod_modal_time", "Срок (5m, 1:30, 6h, 2d)"),
                    custom_id="time",
                    style=disnake.TextInputStyle.short,
                    placeholder="15 минут",
                    max_length=16,
                ),
                disnake.ui.TextInput(
                    label=get_text("mod_modal_reason", "Причина"),
                    custom_id="reason",
                    style=disnake.TextInputStyle.paragraph,
                    placeholder=get_text("mod_modal_reason_hint", "Оскорбления в голосовом"),
                    max_length=400,
                ),
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        cog: "ModPanel" = inter.bot.get_cog("ModPanel")
        await cog.apply_timeout(inter, self.target_id,
                               inter.text_values["time"].strip(),
                               inter.text_values["reason"].strip())


class ModPanelView(disnake.ui.View):
    """Постоянная панель: кнопки работают и после перезапуска бота (custom_id не менять)."""

    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.user_select(placeholder="👤 Выбрать бойца", custom_id="mod:pick", row=0)
    async def pick(self, select: disnake.ui.UserSelect, inter: disnake.MessageInteraction):
        if not is_admin(inter.author):
            return await inter.response.send_message(
                embed=note_embed(get_text("mod_denied", "❌ Только для руководства семьи.")),
                ephemeral=True)
        member = inter.guild.get_member(select.values[0].id)
        if member is None:
            return await inter.response.send_message(
                embed=note_embed("❌ Этот пользователь не на сервере."), ephemeral=True)
        if member.bot:
            return await inter.response.send_message(embed=note_embed("❌ Ботов наказывать нельзя."),
                                                     ephemeral=True)
        if member.top_role >= inter.author.top_role:
            return await inter.response.send_message(
                embed=note_embed("❌ Нельзя наказать равного по иерархии или старшего."),
                ephemeral=True)
        await inter.response.send_modal(TimeoutModal(member.id))

    @disnake.ui.user_select(placeholder="🔊 Кого размутить", custom_id="mod:unpick", row=1)
    async def unpick(self, select: disnake.ui.UserSelect, inter: disnake.MessageInteraction):
        if not is_admin(inter.author):
            return await inter.response.send_message(
                embed=note_embed(get_text("mod_denied", "❌ Только для руководства семьи.")),
                ephemeral=True)
        member = inter.guild.get_member(select.values[0].id)
        if member is None:
            return await inter.response.send_message(
                embed=note_embed("❌ Этот пользователь не на сервере."), ephemeral=True)
        if not inter.guild.me.guild_permissions.moderate_members:
            return await inter.response.send_message(
                embed=note_embed("❌ У меня нет права «Управлять участниками»."),
                ephemeral=True)
        try:
            await member.edit(timeout=None, reason=f"Размут: {inter.author.display_name}")
        except disnake.Forbidden:
            return await inter.response.send_message(
                embed=note_embed("❌ Discord не дал: у бойца роль старлее моей."), ephemeral=True)
        except Exception as e:
            return await inter.response.send_message(
                embed=note_embed(f"❌ Не вышло: `{e}`"), ephemeral=True)

        _log_add({"id": int(datetime.now(timezone.utc).timestamp() * 1000),
                  "guild": inter.guild.id, "target": member.id, "moderator": inter.author.id,
                  "minutes": 0, "kind": "clear", "reason": "Таймаут снят досрочно",
                  "at": datetime.now(MSK).strftime("%d.%m.%Y %H:%M")})
        await inter.response.send_message(
            embed=note_embed(f"🔊 <@{member.id}> размучен. {get_text('mod_clear_note', 'Объяснись в чате.')}"),
            ephemeral=True)

    @disnake.ui.button(label="Журнал наказаний", emoji="📜", style=disnake.ButtonStyle.secondary,
                       custom_id="mod:log", row=2)
    async def log(self, button, inter: disnake.MessageInteraction):
        if not is_admin(inter.author):
            return await inter.response.send_message(
                embed=note_embed(get_text("mod_denied", "❌ Только для руководства семьи.")),
                ephemeral=True)
        await inter.response.send_message(embed=await _journal_embed(inter.guild), ephemeral=True)


async def _journal_embed(guild) -> disnake.Embed:
    data = _load()
    rows = sorted((e for k, e in data.items() if k.isdigit()), key=lambda e: -int(e["id"]))[:12]
    cells = []
    for e in rows:
        reason = (e.get("reason") or "без причины")[:60]
        head = "🔊 размут" if e.get("kind") == "clear" else f"🔇 {_human(int(e['minutes']))}"
        cells.append([f"<@{e['target']}>", f"{head} · <@{e['moderator']}>\n{reason}"])
    return list_embed(get_text("mod_journal_title", "📜 Журнал наказаний"), cells,
                      guild=guild, inline=False,
                      empty="Наказаний ещё не было — так и держим.",
                      note=get_text("mod_journal_note", "Последние 12 записей."))


class ModPanel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        if not getattr(self.bot, "_mod_view_added", False):
            self.bot.add_view(ModPanelView())
            self.bot._mod_view_added = True

    @commands.slash_command(name="наказания",
                            description="Панель таймаутов и журнал наказаний",
                            default_member_permissions=disnake.Permissions(manage_guild=True),
                            dm_permission=False)
    async def mod_panel(self, inter: disnake.ApplicationCommandInteraction):
        embed = card(get_text("mod_panel_title", "⚖️ Наказания семьи"),
                     guild=inter.guild)
        embed.description = get_text(
            "mod_panel_desc",
            "Выбери бойца в списке — бот спросит срок и причину, поставит таймаут, "
            "сам напишет нарушителю и запишет в журнал.\n\n"
            f"Быстрые сроки: {', '.join(PRESETS)}. Максимум 28 дней (это лимит Discord).")
        await inter.channel.send(embed=embed, view=ModPanelView())
        await inter.response.send_message(embed=note_embed("✅ Панель наказаний отправлена."),
                                          ephemeral=True)

    async def apply_timeout(self, inter: disnake.Interaction, target_id: int,
                            raw_time: str, reason: str) -> None:
        guild = inter.guild
        member = guild.get_member(target_id) if guild else None
        if member is None:
            return await inter.response.send_message(
                embed=note_embed("❌ Боец не найден на сервере."), ephemeral=True)
        minutes = _minutes(raw_time)
        if not minutes or minutes <= 0:
            return await inter.response.send_message(
                embed=note_embed("❌ Не понял срок. Напиши `15`, `1:30`, `6h` или `2d`."),
                ephemeral=True)
        if minutes > MAX_MINUTES:
            return await inter.response.send_message(
                embed=note_embed(f"❌ Максимум 28 дней, а ты просишь {_human(minutes)}."),
                ephemeral=True)
        if member.id == inter.author.id:
            return await inter.response.send_message(embed=note_embed("❌ Себя наказывать нельзя."),
                                                     ephemeral=True)
        perms = guild.me.guild_permissions
        if not perms.moderate_members:
            return await inter.response.send_message(
                embed=note_embed("❌ У меня нет права **«Управлять участниками»** — "
                                 "таймаут не поставить."), ephemeral=True)

        reason_text = (reason or get_text("mod_default_reason", "Решение руководства семьи"))[:400]
        try:
            await member.edit(timeout=timedelta(minutes=minutes),
                              reason=f"{inter.author.display_name}: {reason_text}")
        except disnake.Forbidden:
            return await inter.response.send_message(
                embed=note_embed("❌ Discord не дал: у бойца роль старлее моей."), ephemeral=True)
        except Exception as e:
            return await inter.response.send_message(
                embed=note_embed(f"❌ Не вышло: `{e}`"), ephemeral=True)

        _log_add({"id": int(datetime.now(timezone.utc).timestamp() * 1000),
                  "guild": guild.id, "target": member.id, "moderator": inter.author.id,
                  "minutes": minutes, "reason": reason_text,
                  "at": datetime.now(MSK).strftime("%d.%m.%Y %H:%M")})

        await inter.response.send_message(
            embed=note_embed(f"🔇 <@{member.id}> — таймаут **{_human(minutes)}**, "
                             f"освободится {_when(minutes)}.\n📝 {reason_text}"),
            ephemeral=True)

        dm = card(get_text("mod_dm_title", "🔇 Вам выдан таймаут"), guild=guild, color_key="error_color")
        dm.description = get_text(
            "mod_dm_text",
            f"**Срок:** {_human(minutes)} · до {_when(minutes)}\n"
            f"**Причина:** {reason_text}\n\n"
            "Если не согласен — напиши руководству семьи в ЛС, а не в общий чат.")
        try:
            await member.send(embed=dm)
        except disnake.HTTPException:
            pass

        await self._to_log(guild, member, inter.author, minutes, reason_text)

    async def _to_log(self, guild, member, moderator, minutes: int, reason: str) -> None:
        """Журнал в канал: куда — берём из настроек логов или из панели наказаний."""
        cfg = load_config()
        ch_id = (cfg.get("channels", {}) or {}).get("mod_log_channel_id") or \
            (cfg.get("channels", {}) or {}).get("logs_channel_id")
        if not ch_id:
            state = _load().get("_channel") or {}
            ch_id = state.get(str(guild.id))
        ch = guild.get_channel(int(ch_id)) if ch_id else None
        if ch is None:
            return
        emb = card(f"🔇 Таймаут: {_human(minutes)}", guild=guild, color_key="warning_color")
        emb.description = (f"**Боец:** {member.mention}\n"
                           f"**Кто выдал:** <@{moderator.id}>\n"
                           f"**Причина:** {reason}\n"
                           f"**Освободится:** {_when(minutes)}")
        try:
            await ch.send(embed=emb)
        except disnake.HTTPException:
            pass


def setup(bot: commands.Bot):
    bot.add_cog(ModPanel(bot))
