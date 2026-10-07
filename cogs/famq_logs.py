"""
cogs/famq_logs.py — полная система логов Hallez FAMQ (disnake 2.12).

У каждой категории свой канал. Если канал категории не задан — используется
запасной канал "default". Если нет и его — категория молча пропускается.

Команды (нужно право "Управление сервером"):
  /logs auto                      — создать приватную категорию с каналами под все логи
  /logs set <категория> <канал>   — привязать канал к категории
  /logs clear <категория>         — отвязать канал
  /logs toggle <категория>        — включить / выключить категорию
  /logs view                      — показать текущие настройки
  /logs test <категория>          — отправить тестовую запись

Из другого кода (например из recruitment.py) можно слать логи напрямую:
  bot.dispatch("famq_log", guild, "applications", embed)
"""
import asyncio
import json
import sys
from pathlib import Path

import disnake
from disnake.ext import commands

BASE = Path(__file__).resolve().parent.parent
CFG_PATH = BASE / "logs_settings.json"

# key: (название, эмодзи, цвет)
CATS = {
    "applications": ("Заявки в семью", "📝", 0x3498DB),
    "promotions": ("Повышения", "📈", 0xF1C40F),
    "shop": ("Магазин / баллы", "🛒", 0xE67E22),
    "join": ("Вход на сервер", "📥", 0x2ECC71),
    "leave": ("Выход с сервера", "📤", 0xE74C3C),
    "voice": ("Голосовые каналы", "🔊", 0x9B59B6),
    "messages": ("Сообщения", "💬", 0x95A5A6),
    "roles": ("Выдача / снятие ролей", "🎭", 0x1ABC9C),
    "nicknames": ("Смена ников", "🏷️", 0x34495E),
    "moderation": ("Модерация (бан/кик/мут)", "🔨", 0xC0392B),
    "server": ("Каналы и роли сервера", "⚙️", 0x7F8C8D),
    "commands": ("Использование команд", "⌨️", 0x2980B9),
    "bot": ("Бот / ошибки", "🤖", 0x990000),
}
CHOICES = {f"{v[1]} {v[0]}": k for k, v in CATS.items()}
CHOICES_NO_DEFAULT = dict(CHOICES)
CHOICES["📦 Общий (запасной)"] = "default"

APP_KEYWORDS = ("заявк", "анкет", "обзвон", "принят", "добро пожаловать", "отклон", "рассмотр")
PROMO_KEYWORDS = ("повышен", "отчет", "отчёт")


def clip(text, n=1000):
    text = str(text) if text is not None else ""
    return text if len(text) <= n else text[: n - 1] + "…"


def who(u):
    return f"{u.mention} (`{u.id}`)" if u else "неизвестно"


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


class FamLogs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cfg = self._load()
        self.voice_since = {}
        self._started = False
        self.last = {}
        self._dirty = 0
        self.up_since = disnake.utils.utcnow()

    def cog_unload(self):
        self._write()

    @staticmethod
    def meta(key):
        return CATS.get(key, ("Общий (запасной)", "📦", 0x990000))

    # ───────────── конфиг ─────────────
    def _load(self):
        if CFG_PATH.exists():
            cfg = read_json(CFG_PATH)
        else:
            cfg = {"channels": {}, "disabled": [], "ignore_bots": True}
            # разовый импорт из старой панели
            old = read_json(BASE / "panel_settings.json").get("logs", {})
            for k, v in old.items():
                if (k in CATS or k == "default") and isinstance(v, int) and v:
                    cfg["channels"][k] = v
            self._write(cfg)
        cfg.setdefault("channels", {})
        cfg.setdefault("disabled", [])
        cfg.setdefault("ignore_bots", True)
        return cfg

    def _write(self, cfg=None):
        CFG_PATH.write_text(json.dumps(cfg or self.cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    def log_channel_ids(self):
        return {int(v) for v in self.cfg["channels"].values() if v}

    # ───────────── отправка ─────────────
    def make(self, cat, title, desc=None, fields=None, user=None):
        name, icon, color = CATS[cat]
        e = disnake.Embed(title=f"{icon} {title}", description=clip(desc, 4000) if desc else None,
                          color=color, timestamp=disnake.utils.utcnow())
        for n, v, inline in fields or []:
            e.add_field(name=n, value=clip(v, 1024) or "—", inline=inline)
        if user is not None:
            e.set_author(name=f"{user} ({user.id})", icon_url=user.display_avatar.url)
        e.set_footer(text=f"Hallez FAMQ • {name}")
        return e

    async def send(self, guild, cat, embed):
        if guild is None or cat in self.cfg["disabled"]:
            return
        ch_id = self.cfg["channels"].get(cat) or self.cfg["channels"].get("default")
        if not ch_id:
            return
        ch = guild.get_channel_or_thread(int(ch_id))
        if ch is None:
            return
        try:
            await ch.send(embed=embed, allowed_mentions=disnake.AllowedMentions.none())
            st = self.cfg.setdefault("stats", {})
            st[cat] = st.get(cat, 0) + 1
            self.last[cat] = disnake.utils.utcnow()
            self._dirty += 1
            if self._dirty >= 20:
                self._dirty = 0
                self._write()
        except disnake.HTTPException as ex:
            print(f"[logs] не удалось отправить в {ch_id}: {ex}", file=sys.stderr, flush=True)

    async def actor(self, guild, action, target_id):
        """Ищет в audit log, кто совершил действие (best effort)."""
        await asyncio.sleep(1.2)
        try:
            async for e in guild.audit_logs(limit=8, action=action):
                if (disnake.utils.utcnow() - e.created_at).total_seconds() > 20:
                    break
                if e.target is not None and getattr(e.target, "id", None) == target_id:
                    return e
        except (disnake.Forbidden, disnake.HTTPException):
            pass
        return None

    @commands.Cog.listener("on_famq_log")
    async def on_famq_log(self, guild, category, embed):
        await self.send(guild, category, embed)

    # ───────────── вход / выход ─────────────
    @commands.Cog.listener()
    async def on_member_join(self, m: disnake.Member):
        age = disnake.utils.format_dt(m.created_at, "R")
        e = self.make("join", "Участник зашёл на сервер", f"{m.mention} присоединился.",
                      [("Аккаунт создан", age, True), ("Всего участников", str(m.guild.member_count), True)], m)
        await self.send(m.guild, "join", e)

    @commands.Cog.listener()
    async def on_member_remove(self, m: disnake.Member):
        roles = [r.mention for r in m.roles if r != m.guild.default_role]
        joined = disnake.utils.format_dt(m.joined_at, "R") if m.joined_at else "неизвестно"
        e = self.make("leave", "Участник вышел с сервера", f"{m.mention} (`{m}`)",
                      [("Был на сервере с", joined, True), ("Роли", ", ".join(roles) or "нет", False)], m)
        await self.send(m.guild, "leave", e)
        # кик?
        ent = await self.actor(m.guild, disnake.AuditLogAction.kick, m.id)
        if ent:
            k = self.make("moderation", "Кик", None,
                          [("Кого", who(m), True), ("Кто", who(ent.user), True),
                           ("Причина", ent.reason or "не указана", False)])
            await self.send(m.guild, "moderation", k)

    # ───────────── голос ─────────────
    @commands.Cog.listener()
    async def on_voice_state_update(self, m, before, after):
        if self.cfg["ignore_bots"] and m.bot:
            return
        g = m.guild
        now = disnake.utils.utcnow()
        if before.channel != after.channel:
            if before.channel is None:
                self.voice_since[m.id] = now
                e = self.make("voice", "Зашёл в голосовой канал", None,
                              [("Канал", after.channel.mention, True)], m)
            elif after.channel is None:
                start = self.voice_since.pop(m.id, None)
                dur = ""
                if start:
                    s = int((now - start).total_seconds())
                    dur = f"{s // 3600}ч {s % 3600 // 60}м {s % 60}с"
                e = self.make("voice", "Вышел из голосового канала", None,
                              [("Канал", before.channel.mention, True), ("Провёл", dur or "неизвестно", True)], m)
            else:
                e = self.make("voice", "Перешёл между каналами", None,
                              [("Из", before.channel.mention, True), ("В", after.channel.mention, True)], m)
            await self.send(g, "voice", e)
            return
        if after.channel is None:
            return
        notes = []
        if before.mute != after.mute:
            notes.append("🔇 Серверный мут включён" if after.mute else "🔊 Серверный мут снят")
        if before.deaf != after.deaf:
            notes.append("🙉 Серверный deafen включён" if after.deaf else "👂 Серверный deafen снят")
        if before.self_stream != after.self_stream:
            notes.append("📺 Начал стрим" if after.self_stream else "📺 Закончил стрим")
        if before.self_video != after.self_video:
            notes.append("📷 Включил камеру" if after.self_video else "📷 Выключил камеру")
        if notes:
            e = self.make("voice", "Изменение состояния в голосе", "\n".join(notes),
                          [("Канал", after.channel.mention, True)], m)
            await self.send(g, "voice", e)

    # ───────────── сообщения ─────────────
    @commands.Cog.listener()
    async def on_raw_message_delete(self, p: disnake.RawMessageDeleteEvent):
        guild = self.bot.get_guild(p.guild_id) if p.guild_id else None
        if guild is None or p.channel_id in self.log_channel_ids():
            return
        msg = p.cached_message
        ch = guild.get_channel_or_thread(p.channel_id)
        ch_text = ch.mention if ch else f"`{p.channel_id}`"
        if msg is None:
            e = self.make("messages", "Сообщение удалено", "Автор и текст неизвестны (сообщение не в кеше).",
                          [("Канал", ch_text, True), ("ID сообщения", f"`{p.message_id}`", True)])
            await self.send(guild, "messages", e)
            return
        if self.cfg["ignore_bots"] and msg.author.bot:
            return
        files = "\n".join(a.filename for a in msg.attachments)
        e = self.make("messages", "Сообщение удалено", clip(msg.content, 3500) or "*без текста*",
                      [("Автор", who(msg.author), True), ("Канал", ch_text, True)], msg.author)
        if files:
            e.add_field(name="Вложения", value=clip(files), inline=False)
        ent = await self.actor(guild, disnake.AuditLogAction.message_delete, msg.author.id)
        if ent and ent.user.id != msg.author.id:
            e.add_field(name="Удалил", value=who(ent.user), inline=True)
        await self.send(guild, "messages", e)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, p: disnake.RawBulkMessageDeleteEvent):
        guild = self.bot.get_guild(p.guild_id) if p.guild_id else None
        if guild is None or p.channel_id in self.log_channel_ids():
            return
        ch = guild.get_channel_or_thread(p.channel_id)
        e = self.make("messages", "Массовое удаление сообщений", None,
                      [("Канал", ch.mention if ch else f"`{p.channel_id}`", True),
                       ("Количество", str(len(p.message_ids)), True)])
        await self.send(guild, "messages", e)

    @commands.Cog.listener()
    async def on_message_edit(self, before: disnake.Message, after: disnake.Message):
        if not after.guild or before.content == after.content:
            return
        if (self.cfg["ignore_bots"] and after.author.bot) or after.channel.id in self.log_channel_ids():
            return
        e = self.make("messages", "Сообщение изменено", f"[Перейти к сообщению]({after.jump_url})",
                      [("Было", clip(before.content) or "—", False), ("Стало", clip(after.content) or "—", False),
                       ("Канал", after.channel.mention, True)], after.author)
        await self.send(after.guild, "messages", e)

    # ───────────── роли / ники / таймаут ─────────────
    @commands.Cog.listener()
    async def on_member_update(self, before: disnake.Member, after: disnake.Member):
        g = after.guild
        added = [r for r in after.roles if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]
        if added or removed:
            ent = await self.actor(g, disnake.AuditLogAction.member_role_update, after.id)
            f = []
            if added:
                f.append(("➕ Выданы", ", ".join(r.mention for r in added), False))
            if removed:
                f.append(("➖ Сняты", ", ".join(r.mention for r in removed), False))
            f.append(("Кто изменил", who(ent.user) if ent else "не удалось определить", True))
            await self.send(g, "roles", self.make("roles", "Изменение ролей", f"Участник: {after.mention}", f, after))
        if before.nick != after.nick:
            ent = await self.actor(g, disnake.AuditLogAction.member_update, after.id)
            f = [("Было", before.nick or "—", True), ("Стало", after.nick or "—", True),
                 ("Кто изменил", who(ent.user) if ent else "не удалось определить", True)]
            await self.send(g, "nicknames", self.make("nicknames", "Смена ника", f"Участник: {after.mention}", f, after))
        if before.current_timeout != after.current_timeout:
            ent = await self.actor(g, disnake.AuditLogAction.member_update, after.id)
            if after.current_timeout:
                t = f"до {disnake.utils.format_dt(after.current_timeout, 'f')}"
                title = "Выдан тайм-аут"
            else:
                t, title = "—", "Тайм-аут снят"
            f = [("Срок", t, True), ("Кто", who(ent.user) if ent else "не удалось определить", True)]
            if ent and ent.reason:
                f.append(("Причина", ent.reason, False))
            await self.send(g, "moderation", self.make("moderation", title, f"Участник: {after.mention}", f, after))

    # ───────────── бан / разбан ─────────────
    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        ent = await self.actor(guild, disnake.AuditLogAction.ban, user.id)
        f = [("Кого", who(user), True), ("Кто", who(ent.user) if ent else "неизвестно", True),
             ("Причина", (ent.reason if ent else None) or "не указана", False)]
        await self.send(guild, "moderation", self.make("moderation", "Бан", None, f))

    @commands.Cog.listener()
    async def on_member_unban(self, guild, user):
        ent = await self.actor(guild, disnake.AuditLogAction.unban, user.id)
        f = [("Кого", who(user), True), ("Кто", who(ent.user) if ent else "неизвестно", True)]
        await self.send(guild, "moderation", self.make("moderation", "Разбан", None, f))

    # ───────────── сервер: каналы и роли ─────────────
    @commands.Cog.listener()
    async def on_guild_channel_create(self, ch):
        ent = await self.actor(ch.guild, disnake.AuditLogAction.channel_create, ch.id)
        f = [("Канал", f"{ch.mention} (`{ch.name}`)", True), ("Кто", who(ent.user) if ent else "неизвестно", True)]
        await self.send(ch.guild, "server", self.make("server", "Канал создан", None, f))

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, ch):
        ent = await self.actor(ch.guild, disnake.AuditLogAction.channel_delete, ch.id)
        f = [("Канал", f"`{ch.name}`", True), ("Кто", who(ent.user) if ent else "неизвестно", True)]
        await self.send(ch.guild, "server", self.make("server", "Канал удалён", None, f))

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        if before.name != after.name:
            ent = await self.actor(after.guild, disnake.AuditLogAction.channel_update, after.id)
            f = [("Было", before.name, True), ("Стало", after.name, True),
                 ("Кто", who(ent.user) if ent else "неизвестно", True)]
            await self.send(after.guild, "server", self.make("server", "Канал переименован", after.mention, f))

    @commands.Cog.listener()
    async def on_guild_role_create(self, role):
        ent = await self.actor(role.guild, disnake.AuditLogAction.role_create, role.id)
        f = [("Роль", f"{role.mention} (`{role.name}`)", True), ("Кто", who(ent.user) if ent else "неизвестно", True)]
        await self.send(role.guild, "server", self.make("server", "Роль создана", None, f))

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        ent = await self.actor(role.guild, disnake.AuditLogAction.role_delete, role.id)
        f = [("Роль", f"`{role.name}`", True), ("Кто", who(ent.user) if ent else "неизвестно", True)]
        await self.send(role.guild, "server", self.make("server", "Роль удалена", None, f))

    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        changes = []
        if before.name != after.name:
            changes.append(f"Название: `{before.name}` → `{after.name}`")
        if before.color != after.color:
            changes.append(f"Цвет: `{before.color}` → `{after.color}`")
        if before.permissions != after.permissions:
            on = [p for p, v in after.permissions if v and not getattr(before.permissions, p)]
            off = [p for p, v in before.permissions if v and not getattr(after.permissions, p)]
            if on:
                changes.append("Права добавлены: " + ", ".join(f"`{p}`" for p in on))
            if off:
                changes.append("Права убраны: " + ", ".join(f"`{p}`" for p in off))
        if not changes:
            return
        ent = await self.actor(after.guild, disnake.AuditLogAction.role_update, after.id)
        f = [("Кто", who(ent.user) if ent else "неизвестно", True)]
        await self.send(after.guild, "server", self.make("server", "Роль изменена", f"{after.mention}\n" + "\n".join(changes), f))

    # ───────────── команды ─────────────
    @commands.Cog.listener()
    async def on_slash_command_completion(self, inter: disnake.ApplicationCommandInteraction):
        opts = ", ".join(f"{k}=`{clip(v, 60)}`" for k, v in (inter.filled_options or {}).items()) or "—"
        ch = inter.channel.mention if getattr(inter.channel, "mention", None) else "—"
        e = self.make("commands", f"Использована команда /{inter.application_command.qualified_name}", None,
                      [("Кто", who(inter.author), True), ("Канал", ch, True), ("Параметры", opts, False)], inter.author)
        await self.send(inter.guild, "commands", e)

    @commands.Cog.listener()
    async def on_slash_command_error(self, inter: disnake.ApplicationCommandInteraction, error):
        if isinstance(error, commands.CommandNotFound):
            return
        e = self.make("bot", f"Ошибка команды /{inter.data.name}", f"```{clip(error, 1500)}```",
                      [("Кто", who(inter.author), True)])
        await self.send(inter.guild, "bot", e)

    @commands.Cog.listener()
    async def on_user_command_completion(self, inter):
        e = self.make("commands", f"Контекстная команда «{inter.data.name}»", None,
                      [("Кто", who(inter.author), True), ("На ком", who(inter.target), True)], inter.author)
        await self.send(inter.guild, "commands", e)

    @commands.Cog.listener()
    async def on_message_command_completion(self, inter):
        e = self.make("commands", f"Контекстная команда «{inter.data.name}»", None,
                      [("Кто", who(inter.author), True), ("Сообщение", inter.target.jump_url, False)], inter.author)
        await self.send(inter.guild, "commands", e)

    # ───────────── заявки / повышения / магазин ─────────────
    def _watch(self):
        """Возвращает (каналы с карточками для зеркалирования, каналы с панелями/кнопками)."""
        cfg = read_json(BASE / "config.json")
        rp = read_json(BASE / "recruit_plus.json")
        ch = cfg.get("channels", {})
        review = {}
        panels = {}
        if rp.get("channels", {}).get("review"):
            review[int(rp["channels"]["review"])] = "applications"
        if rp.get("channels", {}).get("orders"):
            review[int(rp["channels"]["orders"])] = "shop"
        if ch.get("promotion_review_channel_id"):
            review[int(ch["promotion_review_channel_id"])] = "promotions"
        if ch.get("recruitment_channel_id"):
            panels[int(ch["recruitment_channel_id"])] = "applications"
        if ch.get("promotion_channel_id"):
            panels[int(ch["promotion_channel_id"])] = "promotions"
        if rp.get("channels", {}).get("points_panel"):
            panels[int(rp["channels"]["points_panel"])] = "shop"
        panels.update(review)
        return review, panels

    @staticmethod
    def _cid(channel):
        return getattr(channel, "parent_id", None) or channel.id

    @commands.Cog.listener()
    async def on_message(self, msg: disnake.Message):
        """Зеркалит карточки заявок/повышений/заказов, которые бот публикует в рабочие каналы."""
        if not msg.guild or not msg.embeds or msg.author.id != self.bot.user.id:
            return
        review, _ = self._watch()
        cat = review.get(self._cid(msg.channel))
        if not cat or msg.channel.id in self.log_channel_ids():
            return
        title = (msg.embeds[0].title or "").lower()
        if cat == "applications" and not any(k in title for k in APP_KEYWORDS):
            return
        if cat == "promotions" and not any(k in title for k in PROMO_KEYWORDS):
            return
        e = msg.embeds[0].copy()
        e.add_field(name="Источник", value=f"[Перейти к сообщению]({msg.jump_url})", inline=False)
        await self.send(msg.guild, cat, e)

    @commands.Cog.listener()
    async def on_button_click(self, inter: disnake.MessageInteraction):
        if not inter.guild:
            return
        _, panels = self._watch()
        cat = panels.get(self._cid(inter.channel))
        if not cat:
            return
        c = inter.component
        name = c.label or (str(c.emoji) if c.emoji else "") or "кнопка"
        e = self.make(cat, "Нажата кнопка", None,
                      [("Кто", who(inter.author), True), ("Кнопка", f"{name}\n`{c.custom_id}`", True),
                       ("Сообщение", f"[Открыть]({inter.message.jump_url})", False)], inter.author)
        await self.send(inter.guild, cat, e)

    @commands.Cog.listener()
    async def on_dropdown(self, inter: disnake.MessageInteraction):
        if not inter.guild:
            return
        _, panels = self._watch()
        cat = panels.get(self._cid(inter.channel))
        if not cat:
            return
        e = self.make(cat, "Выбран пункт в меню", None,
                      [("Кто", who(inter.author), True), ("Выбор", ", ".join(inter.values) or "—", True)], inter.author)
        await self.send(inter.guild, cat, e)

    @commands.Cog.listener()
    async def on_modal_submit(self, inter: disnake.ModalInteraction):
        if not inter.guild or inter.channel is None:
            return
        _, panels = self._watch()
        cat = panels.get(self._cid(inter.channel))
        if not cat:
            return
        fields = [(clip(k, 250), clip(v, 1000) or "—", False) for k, v in list(inter.text_values.items())[:8]]
        fields.insert(0, ("Кто", who(inter.author), True))
        await self.send(inter.guild, cat, self.make(cat, f"Отправлена форма: {clip(inter.data.custom_id, 80)}", None, fields, inter.author))

    # ───────────── бот ─────────────
    @commands.Cog.listener()
    async def on_ready(self):
        if self._started:
            return
        self._started = True
        for g in self.bot.guilds:
            e = self.make("bot", "Бот запущен", None,
                          [("Задержка", f"{round(self.bot.latency * 1000)} мс", True),
                           ("Участников", str(g.member_count), True)])
            await self.send(g, "bot", e)

    # ───────────── управление ─────────────
    def _allowed(self, inter):
        if inter.author.guild_permissions.manage_guild:
            return True
        return inter.author.id in read_json(BASE / "config.json").get("admin_user_ids", [])

    @commands.slash_command(name="logs", description="Настройка логов семьи",
                            default_member_permissions=disnake.Permissions(manage_guild=True), dm_permission=False)
    async def logs(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @logs.sub_command(name="set", description="Привязать канал к категории логов")
    async def logs_set(self, inter, category: str = commands.Param(choices=CHOICES, description="Категория"),
                       channel: disnake.TextChannel = commands.Param(description="Канал для логов")):
        if not self._allowed(inter):
            return await inter.response.send_message("⛔ Недостаточно прав.", ephemeral=True)
        self.cfg["channels"][category] = channel.id
        self._write()
        await inter.response.send_message(f"✅ Категория **{category}** → {channel.mention}", ephemeral=True)

    @logs.sub_command(name="clear", description="Отвязать канал от категории")
    async def logs_clear(self, inter, category: str = commands.Param(choices=CHOICES, description="Категория")):
        if not self._allowed(inter):
            return await inter.response.send_message("⛔ Недостаточно прав.", ephemeral=True)
        self.cfg["channels"].pop(category, None)
        self._write()
        await inter.response.send_message(f"🗑️ Канал для **{category}** отвязан.", ephemeral=True)

    @logs.sub_command(name="toggle", description="Включить / выключить категорию")
    async def logs_toggle(self, inter, category: str = commands.Param(choices=CHOICES_NO_DEFAULT, description="Категория")):
        if not self._allowed(inter):
            return await inter.response.send_message("⛔ Недостаточно прав.", ephemeral=True)
        if category in self.cfg["disabled"]:
            self.cfg["disabled"].remove(category)
            state = "включена ✅"
        else:
            self.cfg["disabled"].append(category)
            state = "выключена ⛔"
        self._write()
        await inter.response.send_message(f"Категория **{category}** {state}", ephemeral=True)

    @logs.sub_command(name="view", description="Показать настройки логов")
    async def logs_view(self, inter):
        if not self._allowed(inter):
            return await inter.response.send_message("⛔ Недостаточно прав.", ephemeral=True)
        lines = []
        for key, (name, icon, _) in CATS.items():
            cid = self.cfg["channels"].get(key)
            off = " ⛔(выкл)" if key in self.cfg["disabled"] else ""
            lines.append(f"{icon} **{name}** — {f'<#{cid}>' if cid else '*не задан (идёт в запасной)*'}{off}")
        d = self.cfg["channels"].get("default")
        lines.append(f"📦 **Запасной** — {f'<#{d}>' if d else '*не задан*'}")
        await inter.response.send_message(embed=disnake.Embed(title="Настройки логов", description="\n".join(lines), color=0x990000), ephemeral=True)

    @logs.sub_command(name="test", description="Отправить тестовую запись в категорию")
    async def logs_test(self, inter, category: str = commands.Param(choices=CHOICES_NO_DEFAULT, description="Категория")):
        if not self._allowed(inter):
            return await inter.response.send_message("⛔ Недостаточно прав.", ephemeral=True)
        await self.send(inter.guild, category, self.make(category, "Тестовая запись", f"Проверка от {inter.author.mention}"))
        await inter.response.send_message("📨 Отправлено (если канал задан и бот имеет доступ).", ephemeral=True)

    async def auto_create(self, guild):
        ow = {guild.default_role: disnake.PermissionOverwrite(view_channel=False),
              guild.me: disnake.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True)}
        cat = await guild.create_category("📋 ЛОГИ", overwrites=ow)
        made = []
        for key, (name, icon, _) in CATS.items():
            cid = self.cfg["channels"].get(key)
            if cid and guild.get_channel(int(cid)):
                continue
            ch = await guild.create_text_channel(f"log-{key}", category=cat, topic=f"{icon} {name}")
            self.cfg["channels"][key] = ch.id
            made.append(ch.mention)
        self._write()
        return made

    @logs.sub_command(name="auto", description="Создать приватную категорию с каналами под все логи")
    async def logs_auto(self, inter):
        if not self._allowed(inter):
            return await inter.response.send_message("⛔ Недостаточно прав.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        try:
            made = await self.auto_create(inter.guild)
        except disnake.Forbidden:
            return await inter.edit_original_response("❌ У бота нет права «Управление каналами».")
        await inter.edit_original_response(f"✅ Создано каналов: {len(made)}\n" + " ".join(made))

    # ───────────── ПАНЕЛЬ ─────────────
    def panel_embed(self, guild, sel):
        st = self.cfg.get("stats", {})
        dflt = self.cfg["channels"].get("default")
        total = sum(st.values())
        lines = []
        for key, (name, icon, _) in CATS.items():
            cid = self.cfg["channels"].get(key)
            if key in self.cfg["disabled"]:
                dot, dest = "🔴", "*выключено*"
            elif cid:
                dot, dest = "🟢", f"<#{cid}>"
            elif dflt:
                dot, dest = "🟡", f"<#{dflt}> *(запасной)*"
            else:
                dot, dest = "⚪", "*не настроено*"
            arrow = "▸ " if key == sel else ""
            lines.append(f"{dot} {icon} {arrow}**{name}**\n┕ {dest} · `{st.get(key, 0)}`")
        e = disnake.Embed(
            title="📋  ЦЕНТР ЛОГОВ  ·  HALLEZ FAMQ",
            description=(
                "Управляй всеми логами семьи в одном месте.\n"
                "🟢 свой канал  ·  🟡 запасной  ·  ⚪ нет канала  ·  🔴 выключено\n"
                "✦ ━━━━━━━━━━━━━━━━━━ ✦\n\n" + "\n".join(lines)
            ),
            color=0x990000, timestamp=disnake.utils.utcnow())
        name, icon, _ = self.meta(sel)
        cid = self.cfg["channels"].get(sel)
        last = self.last.get(sel)
        e.add_field(
            name=f"{icon} Выбрано: {name}",
            value=(f"**Канал:** {f'<#{cid}>' if cid else '—'}\n"
                   f"**Статус:** {'⛔ выключено' if sel in self.cfg['disabled'] else '✅ включено'}\n"
                   f"**Записей всего:** `{st.get(sel, 0)}`\n"
                   f"**Последняя:** {disnake.utils.format_dt(last, 'R') if last else '—'}"),
            inline=False)
        e.add_field(name="📊 Всего записей", value=f"`{total}`", inline=True)
        e.add_field(name="🤖 Боты", value="игнорируются" if self.cfg["ignore_bots"] else "логируются", inline=True)
        e.add_field(name="⏱ Модуль работает", value=disnake.utils.format_dt(self.up_since, "R"), inline=True)
        if (BASE / "banner_image.png").exists():
            e.set_image(url="attachment://banner_image.png")
        e.set_footer(text="Majestic RP • Hallez FAMQ • Панель логов")
        return e

    @logs.sub_command(name="panel", description="Открыть панель управления логами")
    async def logs_panel(self, inter):
        if not self._allowed(inter):
            return await inter.response.send_message("⛔ Недостаточно прав.", ephemeral=True)
        view = LogPanel(self, inter.author.id, inter.guild, "applications", inter)
        kw = {}
        bp = BASE / "banner_image.png"
        if bp.exists():
            kw["file"] = disnake.File(bp, filename="banner_image.png")
        await inter.response.send_message(embed=self.panel_embed(inter.guild, "applications"),
                                          view=view, ephemeral=True, **kw)


class ChannelPick(disnake.ui.View):
    def __init__(self, parent):
        super().__init__(timeout=300)
        self.parent = parent

    async def interaction_check(self, inter):
        return await self.parent.interaction_check(inter)

    @disnake.ui.channel_select(placeholder="Выбери текстовый канал для логов…", row=0,
                               channel_types=[disnake.ChannelType.text, disnake.ChannelType.news])
    async def pick(self, select, inter):
        p = self.parent
        picked = select.values[0]
        p.cog.cfg["channels"][p.sel] = picked.id
        p.cog._write()
        self.stop()
        p.stop()
        view = LogPanel(p.cog, p.author_id, p.guild, p.sel, p.origin)
        await inter.response.edit_message(content=None, embed=p.cog.panel_embed(p.guild, p.sel), view=view)
        ch = p.guild.get_channel(picked.id)
        if ch and not ch.permissions_for(p.guild.me).send_messages:
            await inter.followup.send(f"⚠️ У бота нет права писать в {ch.mention}. Выдай «Отправлять сообщения».", ephemeral=True)

    @disnake.ui.button(label="Назад", emoji="↩️", style=disnake.ButtonStyle.secondary, row=1)
    async def back(self, button, inter):
        await self.parent.redraw(inter)


class LogPanel(disnake.ui.View):
    def __init__(self, cog, author_id, guild, sel="applications", origin=None):
        super().__init__(timeout=840)
        self.cog, self.author_id, self.guild, self.sel, self.origin = cog, author_id, guild, sel, origin
        opts = []
        for key in [*CATS, "default"]:
            name, icon, _ = cog.meta(key)
            cid = cog.cfg["channels"].get(key)
            ch = guild.get_channel(int(cid)) if cid else None
            desc = f"#{ch.name}" if ch else "канал не задан"
            if key in cog.cfg["disabled"]:
                desc = "⛔ выключено · " + desc
            opts.append(disnake.SelectOption(label=name, value=key, emoji=icon, description=desc[:100], default=(key == sel)))
        self.cat_select.options = opts
        ignore = cog.cfg["ignore_bots"]
        self.bots_btn.label = "Боты: игнор" if ignore else "Боты: логируем"
        self.bots_btn.style = disnake.ButtonStyle.success if ignore else disnake.ButtonStyle.secondary

    async def interaction_check(self, inter):
        if inter.author.id != self.author_id:
            await inter.response.send_message("⛔ Это не твоя панель — открой свою: `/logs panel`.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for c in self.children:
            c.disabled = True
        if self.origin:
            try:
                await self.origin.edit_original_response(view=self)
            except Exception:
                pass

    async def redraw(self, inter, sel=None, deferred=False):
        self.stop()
        view = LogPanel(self.cog, self.author_id, self.guild, sel or self.sel, self.origin)
        embed = self.cog.panel_embed(self.guild, view.sel)
        if deferred:
            await inter.edit_original_response(content=None, embed=embed, view=view)
        else:
            await inter.response.edit_message(content=None, embed=embed, view=view)

    @disnake.ui.string_select(placeholder="📂 Выбери категорию логов…", row=0,
                              options=[disnake.SelectOption(label="…", value="applications")])
    async def cat_select(self, select, inter):
        await self.redraw(inter, sel=select.values[0])

    @disnake.ui.button(label="Привязать канал", emoji="📌", style=disnake.ButtonStyle.primary, row=1)
    async def bind_btn(self, button, inter):
        name, icon, _ = self.cog.meta(self.sel)
        await inter.response.edit_message(content=f"📌 Выбери канал для категории **{icon} {name}**:", view=ChannelPick(self))

    @disnake.ui.button(label="Отвязать", emoji="🗑️", style=disnake.ButtonStyle.secondary, row=1)
    async def unbind_btn(self, button, inter):
        self.cog.cfg["channels"].pop(self.sel, None)
        self.cog._write()
        await self.redraw(inter)

    @disnake.ui.button(label="Вкл / Выкл", emoji="🔄", style=disnake.ButtonStyle.secondary, row=1)
    async def toggle_btn(self, button, inter):
        if self.sel == "default":
            return await inter.response.send_message("ℹ️ Запасной канал нельзя выключить — только отвязать.", ephemeral=True)
        d = self.cog.cfg["disabled"]
        d.remove(self.sel) if self.sel in d else d.append(self.sel)
        self.cog._write()
        await self.redraw(inter)

    @disnake.ui.button(label="Тест", emoji="📨", style=disnake.ButtonStyle.secondary, row=1)
    async def test_btn(self, button, inter):
        ch = self.cog.cfg["channels"]
        if not (ch.get(self.sel) or ch.get("default")):
            return await inter.response.send_message("⚠️ Для этой категории не задан канал (и нет запасного).", ephemeral=True)
        if self.sel in self.cog.cfg["disabled"]:
            return await inter.response.send_message("⚠️ Категория выключена.", ephemeral=True)
        cat = self.sel if self.sel in CATS else "bot"
        e = self.cog.make(cat, "Тестовая запись", f"Проверка из панели от {inter.author.mention}")
        await self.cog.send(inter.guild, self.sel, e)
        await inter.response.send_message("📨 Тестовая запись отправлена.", ephemeral=True)

    @disnake.ui.button(label="Авто-создание", emoji="⚡", style=disnake.ButtonStyle.success, row=2)
    async def auto_btn(self, button, inter):
        await inter.response.defer()
        try:
            made = await self.cog.auto_create(self.guild)
        except disnake.Forbidden:
            return await inter.followup.send("❌ У бота нет права «Управление каналами».", ephemeral=True)
        await self.redraw(inter, deferred=True)
        await inter.followup.send(f"✅ Создано каналов: {len(made)}" if made else "ℹ️ Все категории уже имеют каналы.", ephemeral=True)

    @disnake.ui.button(label="Боты", emoji="🤖", style=disnake.ButtonStyle.secondary, row=2)
    async def bots_btn(self, button, inter):
        self.cog.cfg["ignore_bots"] = not self.cog.cfg["ignore_bots"]
        self.cog._write()
        await self.redraw(inter)

    @disnake.ui.button(label="Статистика", emoji="📊", style=disnake.ButtonStyle.secondary, row=2)
    async def stats_btn(self, button, inter):
        st = self.cog.cfg.get("stats", {})
        top = max(st.values(), default=0) or 1
        rows = []
        for key, cnt in sorted(((k, st.get(k, 0)) for k in CATS), key=lambda x: -x[1]):
            n, icon, _ = self.cog.meta(key)
            filled = round(cnt / top * 10)
            rows.append(f"{icon} **{n}**\n`{'█' * filled}{'░' * (10 - filled)}` {cnt}")
        e = disnake.Embed(title="📊 Статистика логов", description="\n".join(rows) + f"\n\n**Всего:** `{sum(st.values())}`", color=0x990000)
        await inter.response.send_message(embed=e, ephemeral=True)

    @disnake.ui.button(label="Обновить", emoji="♻️", style=disnake.ButtonStyle.secondary, row=2)
    async def refresh_btn(self, button, inter):
        await self.redraw(inter)

    @disnake.ui.button(label="Закрыть", emoji="✖️", style=disnake.ButtonStyle.danger, row=2)
    async def close_btn(self, button, inter):
        self.stop()
        await inter.response.edit_message(content="Панель закрыта. Открыть снова: `/logs panel`", embed=None, view=None, attachments=[])


def setup(bot: commands.Bot):
    bot.add_cog(FamLogs(bot))
