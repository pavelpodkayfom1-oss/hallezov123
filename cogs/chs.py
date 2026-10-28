"""ЧС семьи: бот сам выгоняет тех, кого сюда внесли.

Пишешь ID (или упоминание) — запись ложится в `chs.json`. Как только человек заходит на
сервер, бот его кикает и публикует текст из редактора панели. Право «Управлять
участниками» для этого обязательно, без него бот только напишет, что не смог.

Команда `/чс` появляется в Discord не сразу: список команд регистрируется глобально и
лежит в 429-очереди до получаса, а рестарт её сбрасывает. Поэтому тот же ввод живёт на
постоянной панели (кнопка → модалка) и в хабе `/панель` — они работают сразу после
рестарта, без всякой регистрации.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import disnake
from disnake.ext import commands

from utils import famq_core as core
from utils.checks import get_text, is_admin
from utils.embeds import card, list_embed, note_embed

MSK = timezone(timedelta(hours=3))
STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "chs.json")
MAX_ROWS = 200
# текст правится в редакторе панели, поэтому токены только латинские: `{name}`, `{id}`,
# `{mention}`, `{reason}` — их редактор знает и показывает в предпросмотре живьём.
# Подставляем сами: format() на реальном имени с фигурной скобкой урон бы и выдал ошибку.
TOKENS = ("{mention}", "{name}", "{id}", "{reason}")


def _load() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("list"), dict):
            data.setdefault("channel", 0)
            return data
    except Exception:
        pass
    return {"list": {}, "channel": 0}


def _save(data: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def parse_ids(raw: str) -> list:
    """'1234567890', '<@!1234567890>' или '1234567890 987654321' → список ID.

    Упоминание — это те же цифры в угловых скобках, поэтому вытаскиваем числа: так
    работает и вставленный из Discord профиль, и просто ID из любого места.
    """
    out = []
    for chunk in re.findall(r"\d{5,}", str(raw or "")):
        value = int(chunk)
        if value not in out:
            out.append(value)
    return out


def _human(who: dict) -> str:
    return who.get("name") or "не на сервере"


def _kick_text(member, note: str) -> str:
    text = get_text(
        "chs_kick_text",
        "⛔ **{name}** — ты в чёрном списке семьи. Накуролесил и зашёл снова: "
        "такое не пройдёт. {mention} Разбирайся с руководством.")
    for token, value in (("{mention}", member.mention), ("{name}", member.display_name),
                         ("{id}", str(member.id)), ("{reason}", note or "без причины")):
        text = text.replace(token, value)
    return text


async def _announce(guild, embed) -> bool:
    """Куда писать: привязанный канал → канал логов киков → общий канал сервера."""
    cid = int(_load().get("channel") or 0)
    if cid:
        channel = guild.get_channel(cid)
        if channel is not None:
            try:
                await channel.send(embed=embed, allowed_mentions=disnake.AllowedMentions.none())
                return True
            except Exception:
                pass
    if await core.send_log(guild, "kick", embed):
        return True
    channel = guild.system_channel
    if channel is None:
        return False
    try:
        await channel.send(embed=embed, allowed_mentions=disnake.AllowedMentions.none())
        return True
    except Exception:
        return False


class ChsAddModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(
            title=get_text("chs_modal_add_title", "ЧС: кого внести"),
            custom_id="chs:add_modal",
            components=[
                disnake.ui.TextInput(
                    label=get_text("chs_modal_id_label", "ID или упоминание"),
                    custom_id="ids",
                    style=disnake.TextInputStyle.short,
                    placeholder=get_text("chs_modal_id_hint", "123456789012345678 или @ник"),
                    max_length=400,
                ),
                disnake.ui.TextInput(
                    label=get_text("chs_modal_note_label", "За что (видно в списке)"),
                    custom_id="note",
                    style=disnake.TextInputStyle.paragraph,
                    placeholder=get_text("chs_modal_note_hint", "Что натворил"),
                    required=False,
                    max_length=200,
                ),
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        cog: Optional["Chs"] = inter.bot.get_cog("Chs")
        if cog is None:
            return await inter.response.send_message(embed=note_embed("❌ Ког ЧС выключен."),
                                                     ephemeral=True)
        await cog.do_add(inter, inter.text_values["ids"].strip(),
                         inter.text_values["note"].strip())


class ChsRemoveModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(
            title=get_text("chs_modal_del_title", "ЧС: кого убрать"),
            custom_id="chs:remove",
            components=[
                disnake.ui.TextInput(
                    label=get_text("chs_modal_id_label", "ID или упоминание"),
                    custom_id="ids",
                    style=disnake.TextInputStyle.short,
                    placeholder=get_text("chs_modal_id_hint", "123456789012345678 или @ник"),
                    max_length=400,
                ),
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        cog: Optional["Chs"] = inter.bot.get_cog("Chs")
        if cog is None:
            return await inter.response.send_message(embed=note_embed("❌ Ког ЧС выключен."),
                                                     ephemeral=True)
        await cog.do_remove(inter, inter.text_values["ids"].strip())


class ChsView(disnake.ui.View):
    """Постоянная панель: кнопки живут и после рестарта — custom_id не менять."""

    def __init__(self):
        super().__init__(timeout=None)

    def _deny(self, inter) -> bool:
        return not is_admin(inter.author)

    @disnake.ui.button(label="Внести в ЧС", emoji="⛔", style=disnake.ButtonStyle.danger,
                       custom_id="chs:add", row=0)
    async def add(self, button, inter: disnake.MessageInteraction):
        if self._deny(inter):
            return await inter.response.send_message(
                embed=note_embed(get_text("chs_denied", "⛔ Только для руководства семьи.")),
                ephemeral=True)
        await inter.response.send_modal(ChsAddModal())

    @disnake.ui.button(label="Убрать из ЧС", emoji="🧹", style=disnake.ButtonStyle.secondary,
                       custom_id="chs:remove", row=0)
    async def remove(self, button, inter: disnake.MessageInteraction):
        if self._deny(inter):
            return await inter.response.send_message(
                embed=note_embed(get_text("chs_denied", "⛔ Только для руководства семьи.")),
                ephemeral=True)
        await inter.response.send_modal(ChsRemoveModal())

    @disnake.ui.button(label="Список ЧС", emoji="📋", style=disnake.ButtonStyle.secondary,
                       custom_id="chs:list", row=0)
    async def show_list(self, button, inter: disnake.MessageInteraction):
        if self._deny(inter):
            return await inter.response.send_message(
                embed=note_embed(get_text("chs_denied", "⛔ Только для руководства семьи.")),
                ephemeral=True)
        await inter.response.send_message(embed=await _list_embed(inter.guild), ephemeral=True)

    @disnake.ui.button(label="Писать сюда", emoji="📌", style=disnake.ButtonStyle.primary,
                       custom_id="chs:bind", row=1)
    async def bind(self, button, inter: disnake.MessageInteraction):
        if self._deny(inter):
            return await inter.response.send_message(
                embed=note_embed(get_text("chs_denied", "⛔ Только для руководства семьи.")),
                ephemeral=True)
        data = _load()
        data["channel"] = inter.channel_id
        _save(data)
        await inter.response.send_message(
            embed=note_embed(f"📌 Теперь про ЧС пишу в {inter.channel.mention}."),
            ephemeral=True)


async def _list_embed(guild) -> disnake.Embed:
    data = _load()["list"]
    rows = sorted(data.items(), key=lambda kv: -int((kv[1] or {}).get("at_ts") or 0))[:MAX_ROWS]
    cells = []
    for user_id, info in rows:
        info = info or {}
        note = (info.get("note") or "без причины")[:80]
        tries = int(info.get("tries") or 0)
        again = f" · заходил {tries}" if tries else ""
        cells.append([f"`{user_id}`", f"{_human(info)}{again}\n{note}"])
    return list_embed(get_text("chs_list_title", "📋 Чёрный список семьи"), cells,
                      guild=guild, inline=False,
                      empty=get_text("chs_list_empty", "ЧС пуст — заходит кто угодно."),
                      note=f"Всего {len(data)} · " + get_text(
                          "chs_list_note", "«зашёл» — сколько раз его выгнали после внесения."))


class Chs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        if not getattr(self.bot, "_chs_view_added", False):
            self.bot.add_view(ChsView())
            self.bot._chs_view_added = True

    # ---------- команды ----------
    @commands.slash_command(name="чс", description="Чёрный список: выгнать при заходе",
                            default_member_permissions=disnake.Permissions(kick_members=True),
                            contexts=disnake.InteractionContextTypes(guild=True))
    async def chs(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.send_message(embed=note_embed(
            get_text("chs_usage", "Используй кнопки ниже: `Добавить` / `Убрать` / `Список`.")),
            ephemeral=True)

    @chs.sub_command(name="панель", description="Отправить панель ЧС в этот канал")
    async def chs_panel(self, inter: disnake.ApplicationCommandInteraction):
        emb = card(get_text("chs_panel_title", "⛔ Чёрный список семьи"), guild=inter.guild)
        emb.description = get_text(
            "chs_panel_desc",
            "Внеси ID — и при каждом заходе на сервер бот будет выгонять этого человека "
            "и писать сюда.") + "\n\n" + get_text(
            "chs_panel_extra",
            "Текст при выдворении правится в редакторе панели (ключ `chs_kick_text`), "
            "он живёт без рестарта.")
        await inter.channel.send(embed=emb, view=ChsView())
        await inter.response.send_message(embed=note_embed("✅ Панель ЧС отправлена."),
                                          ephemeral=True)

    @chs.sub_command(name="добавить", description="Внести ID в чёрный список")
    async def chs_add(self, inter: disnake.ApplicationCommandInteraction,
                      target: str = commands.Param(name="id",
                                                   description="ID или упоминание"),
                      note: str = commands.Param(name="причина", default="")):
        await self.do_add(inter, target, note)

    @chs.sub_command(name="убрать", description="Вычеркнуть ID из чёрного списка")
    async def chs_del(self, inter: disnake.ApplicationCommandInteraction,
                      target: str = commands.Param(name="id",
                                                   description="ID или упоминание")):
        await self.do_remove(inter, target)

    @chs.sub_command(name="список", description="Показать чёрный список")
    async def chs_list(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.send_message(embed=await _list_embed(inter.guild), ephemeral=True)

    @chs.sub_command(name="сюда", description="Привязать канал, где писать про ЧС")
    async def chs_here(self, inter: disnake.ApplicationCommandInteraction):
        data = _load()
        data["channel"] = inter.channel_id
        _save(data)
        await inter.response.send_message(
            embed=note_embed(f"📌 Теперь про ЧС пишу в {inter.channel.mention}."),
            ephemeral=True)

    # ---------- ввод ----------
    async def do_add(self, inter, raw: str, note: str = "") -> None:
        ids = parse_ids(raw)
        if not ids:
            return await inter.response.send_message(
                embed=note_embed(get_text("chs_no_id",
                                          "❌ Не увидел ни одного ID. Вставь упоминание "
                                          "или цифры профиля.")),
                ephemeral=True)
        data = _load()
        added, skipped = [], []
        for user_id in ids:
            key = str(user_id)
            if key in data["list"]:
                skipped.append(key)
                continue
            member = inter.guild.get_member(user_id) if inter.guild else None
            data["list"][key] = {
                "name": member.display_name if member else "",
                "note": (note or "")[:200],
                "by": inter.author.id,
                "at": datetime.now(MSK).strftime("%d.%m.%Y %H:%M"),
                "at_ts": int(datetime.now(timezone.utc).timestamp()),
                "tries": 0,
            }
            added.append(key)
        if len(data["list"]) > MAX_ROWS:
            for key in sorted(data["list"], key=lambda k: int(data["list"][k].get("at_ts") or 0))[:
                            len(data["list"]) - MAX_ROWS]:
                data["list"].pop(key, None)
        _save(data)
        lines = [f"⛔ В ЧС: {', '.join('`' + k + '`' for k in added)}" if added else "⛔ Никого не добавил."]
        if skipped:
            lines.append(f"Уже был(и) в списке: {', '.join('`' + k + '`' for k in skipped)}")
        if not data.get("channel"):
            lines.append(get_text("chs_no_channel",
                                  "Канал для таких записей не привязан — напишу в канал логов "
                                  "киков, а если его нет — в общий. Кнопка «Писать сюда» решает."))
        await inter.response.send_message(embed=note_embed("\n".join(lines)), ephemeral=True)

    async def do_remove(self, inter, raw: str) -> None:
        ids = parse_ids(raw)
        if not ids:
            return await inter.response.send_message(
                embed=note_embed(get_text("chs_no_id",
                                          "❌ Не увидел ни одного ID. Вставь упоминание "
                                          "или цифры профиля.")),
                ephemeral=True)
        data = _load()
        gone = [str(i) for i in ids if str(i) in data["list"]]
        for key in gone:
            data["list"].pop(key, None)
        if gone:
            _save(data)
        text = (f"🧹 Убрал из ЧС: {', '.join('`' + k + '`' for k in gone)}" if gone
                else "🧹 Таких ID в списке нет.")
        await inter.response.send_message(embed=note_embed(text), ephemeral=True)

    # ---------- реакция на заход ----------
    @commands.Cog.listener()
    async def on_member_join(self, member: disnake.Member):
        guild = getattr(member, "guild", None)
        if guild is None or getattr(member, "bot", False):
            return
        data = _load()
        entry = data["list"].get(str(member.id))
        if not entry:
            return
        entry["tries"] = int(entry.get("tries") or 0) + 1
        entry["name"] = member.display_name
        _save(data)

        tries = entry["tries"]
        note = entry.get("note") or ""
        kicked = False
        if member.id == guild.owner_id:
            outcome = "это владелец сервера — его не трогаем"
        elif not guild.me.guild_permissions.kick_members:
            outcome = get_text("chs_no_perm", "у меня нет права «Управлять участниками»")
        else:
            try:
                await member.kick(reason=f"ЧС семьи: {note or 'внесён руководством'} "
                                         f"(заход №{tries})")
                kicked = True
                outcome = get_text("chs_kicked", "выдворен")
            except disnake.Forbidden:
                outcome = "Discord не дал: у него роль старлее моей"
            except Exception as exc:
                outcome = f"не вышло ({exc.__class__.__name__})"

        text = _kick_text(member, note)
        if kicked:
            embed = card(get_text("chs_kick_title", "⛔ Чёрный список"), text, guild=guild)
            embed.set_footer(text=f"ID {member.id} · заход №{tries} · "
                                  f"{datetime.now(MSK).strftime('%d.%m.%Y %H:%M')}")
        else:
            embed = note_embed(f"{text}\n\n⚠️ Кик не прошёл: {outcome}.")
        sent = await _announce(guild, embed)
        print(f"⛔ ЧС: {member} зашёл ({tries}) → {outcome}"
              + ("" if sent else " · писать некуда"), flush=True)


def setup(bot: commands.Bot):
    bot.add_cog(Chs(bot))
