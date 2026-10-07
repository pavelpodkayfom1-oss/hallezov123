"""
Логи сервера и журнал действий (аудит). Каналы под каждый тип настраиваются в /центр → Логи.
Для журнала аудита боту нужно право «Просмотр журнала аудита».
Для текста удалённых/изменённых сообщений нужен Message Content Intent (см. README).
"""
import disnake
from disnake.ext import commands

import database
from utils import famq_core as core
from utils.famq_core import WarnGuardBlocked

AUDIT_RU = {
    "kick": "Кик", "ban": "Бан", "unban": "Разбан", "member_update": "Изменение участника",
    "member_role_update": "Смена ролей участника", "member_move": "Перемещение в войсе",
    "member_disconnect": "Отключение из войса", "bot_add": "Добавление бота",
    "channel_create": "Создание канала", "channel_update": "Изменение канала", "channel_delete": "Удаление канала",
    "overwrite_create": "Права канала: создание", "overwrite_update": "Права канала: изменение",
    "overwrite_delete": "Права канала: удаление",
    "role_create": "Создание роли", "role_update": "Изменение роли", "role_delete": "Удаление роли",
    "invite_create": "Создание приглашения", "invite_update": "Изменение приглашения",
    "invite_delete": "Удаление приглашения", "webhook_create": "Создание вебхука",
    "webhook_update": "Изменение вебхука", "webhook_delete": "Удаление вебхука",
    "emoji_create": "Создание эмодзи", "emoji_update": "Изменение эмодзи", "emoji_delete": "Удаление эмодзи",
    "message_delete": "Удаление сообщения", "message_bulk_delete": "Массовое удаление сообщений",
    "message_pin": "Закрепление сообщения", "message_unpin": "Открепление сообщения",
    "guild_update": "Изменение сервера", "thread_create": "Создание ветки", "thread_update": "Изменение ветки",
    "thread_delete": "Удаление ветки", "stage_instance_create": "Создание сцены",
    "guild_scheduled_event_create": "Создание события", "guild_scheduled_event_update": "Изменение события",
    "guild_scheduled_event_delete": "Удаление события",
}
SKIP_AUDIT = {"message_delete", "message_pin", "message_unpin", "invite_create", "invite_delete"}


def _ago(dt) -> str:
    return f"<t:{int(dt.timestamp())}:R>" if dt else "—"


class ServerLogs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._started = False

    def _skip(self, user) -> bool:
        return bool(core.get("general.ignore_bots", True) and getattr(user, "bot", False))

    async def _log(self, guild, cat, embed):
        await core.send_log(guild, cat, embed)

    # ───────── заходы / выходы ─────────

    @commands.Cog.listener()
    async def on_member_join(self, member: disnake.Member):
        e = core.make_embed("📥 Участник зашёл", col=0x2ECC71)
        e.description = f"{member.mention} (`{member}`)"
        e.add_field("Аккаунт создан", _ago(member.created_at), inline=True)
        e.add_field("Участников теперь", str(member.guild.member_count), inline=True)
        age = (disnake.utils.utcnow() - member.created_at).days
        if age < 3:
            e.add_field("⚠️ Подозрительно", f"Аккаунту всего {age} дн.", inline=False)
        e.set_thumbnail(url=member.display_avatar.url)
        e.set_footer(text=f"ID: {member.id}")
        await self._log(member.guild, "join", e)

    @commands.Cog.listener()
    async def on_member_remove(self, member: disnake.Member):
        e = core.make_embed("📤 Участник вышел", col=0xE74C3C)
        e.description = f"{member.mention} (`{member}`)"
        e.add_field("Был на сервере", _ago(member.joined_at), inline=True)
        roles = [r.mention for r in member.roles if r.name != "@everyone"]
        if roles:
            e.add_field("Роли", core.clip(" ".join(roles[::-1]), 1000), inline=False)
        try:
            row = await database.get_member(member.id)
        except Exception:
            row = None
        if row:
            e.add_field("В карточке семьи",
                        f"{row['nick']} | `{row['static_id']}` · ранг **{row['rank']}** ({core.rank_name(row['rank'])})",
                        inline=False)
        e.set_thumbnail(url=member.display_avatar.url)
        e.set_footer(text=f"ID: {member.id}")
        await self._log(member.guild, "leave", e)

    # ───────── сообщения ─────────

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: disnake.RawMessageDeleteEvent):
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if guild is None:
            return
        msg = payload.cached_message
        if msg is not None and self._skip(msg.author):
            return
        e = core.make_embed("🗑️ Сообщение удалено", col=0xE67E22)
        if msg is not None:
            e.add_field("Автор", f"{msg.author.mention} (`{msg.author}`)", inline=True)
            e.add_field("Канал", f"<#{payload.channel_id}>", inline=True)
            e.add_field("Текст", core.clip(msg.content or "*(пусто или нет доступа к тексту)*", 1000), inline=False)
            if msg.attachments:
                e.add_field("Вложения", core.clip("\n".join(a.filename for a in msg.attachments), 500), inline=False)
        else:
            e.add_field("Канал", f"<#{payload.channel_id}>", inline=True)
            e.add_field("ID сообщения", str(payload.message_id), inline=True)
            e.description = "*Сообщение было отправлено до запуска бота, содержимое недоступно.*"
        await self._log(guild, "messages", e)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: disnake.RawBulkMessageDeleteEvent):
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if guild:
            e = core.make_embed("🧹 Массовое удаление сообщений", col=0xE67E22)
            e.description = f"Удалено **{len(payload.message_ids)}** сообщений в <#{payload.channel_id}>"
            await self._log(guild, "messages", e)

    @commands.Cog.listener()
    async def on_message_edit(self, before: disnake.Message, after: disnake.Message):
        if after.guild is None or self._skip(after.author) or before.content == after.content:
            return
        e = core.make_embed("✏️ Сообщение изменено", col=0x3498DB)
        e.add_field("Автор", f"{after.author.mention} (`{after.author}`)", inline=True)
        e.add_field("Канал", after.channel.mention, inline=True)
        e.add_field("Было", core.clip(before.content or "—", 900), inline=False)
        e.add_field("Стало", core.clip(after.content or "—", 900), inline=False)
        e.add_field("Перейти", f"[К сообщению]({after.jump_url})", inline=False)
        await self._log(after.guild, "messages", e)

    # ───────── голос ─────────

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: disnake.Member, before: disnake.VoiceState,
                                    after: disnake.VoiceState):
        if self._skip(member):
            return
        b, a = before.channel, after.channel
        if b is None and a is not None:
            e = core.make_embed("🔊 Зашёл в голосовой канал", f"{member.mention} → {a.mention}", col=0x2ECC71)
        elif b is not None and a is None:
            e = core.make_embed("🔇 Вышел из голосового канала", f"{member.mention} ← {b.mention}", col=0xE74C3C)
        elif b is not None and a is not None and b.id != a.id:
            e = core.make_embed("🔀 Перешёл между каналами", f"{member.mention}: {b.mention} → {a.mention}", col=0x3498DB)
        else:
            if before.self_stream != after.self_stream:
                txt = "начал трансляцию" if after.self_stream else "закончил трансляцию"
                e = core.make_embed("📺 Трансляция", f"{member.mention} {txt} в {a.mention}", col=0x9B59B6)
            elif before.mute != after.mute or before.deaf != after.deaf:
                parts = []
                if before.mute != after.mute:
                    parts.append("серверный мут " + ("включён" if after.mute else "снят"))
                if before.deaf != after.deaf:
                    parts.append("серверное заглушение " + ("включено" if after.deaf else "снято"))
                e = core.make_embed("🎚️ Модерация голоса", f"{member.mention}: " + ", ".join(parts), col=0xF39C12)
            else:
                return
        e.set_footer(text=f"ID: {member.id}")
        await self._log(member.guild, "voice", e)

    # ───────── роли / ники / таймауты ─────────

    @commands.Cog.listener()
    async def on_member_update(self, before: disnake.Member, after: disnake.Member):
        if self._skip(after):
            return
        if before.roles != after.roles:
            added = [r for r in after.roles if r not in before.roles]
            removed = [r for r in before.roles if r not in after.roles]
            e = core.make_embed("🎭 Изменение ролей", f"{after.mention} (`{after}`)", col=0x9B59B6)
            if added:
                e.add_field("➕ Выданы", " ".join(r.mention for r in added), inline=False)
            if removed:
                e.add_field("➖ Сняты", " ".join(r.mention for r in removed), inline=False)
            await self._log(after.guild, "roles", e)
        if before.nick != after.nick:
            e = core.make_embed("📝 Смена ника", f"{after.mention}", col=0x3498DB)
            e.add_field("Было", before.nick or f"*{before.name}*", inline=True)
            e.add_field("Стало", after.nick or f"*{after.name}*", inline=True)
            await self._log(after.guild, "nicknames", e)
        bt = getattr(before, "current_timeout", None)
        at = getattr(after, "current_timeout", None)
        if bt != at:
            if at:
                e = core.make_embed("⏳ Таймаут выдан", f"{after.mention} до <t:{int(at.timestamp())}:F>", col=0xE67E22)
            else:
                e = core.make_embed("✅ Таймаут снят", f"{after.mention}", col=0x2ECC71)
            await self._log(after.guild, "moderation", e)

    # ───────── баны ─────────

    @commands.Cog.listener()
    async def on_member_ban(self, guild: disnake.Guild, user: disnake.User):
        await self._log(guild, "moderation", core.make_embed("🔨 Бан", f"{user.mention} (`{user}`)", col=0xC0392B))

    @commands.Cog.listener()
    async def on_member_unban(self, guild: disnake.Guild, user: disnake.User):
        await self._log(guild, "moderation", core.make_embed("♻️ Разбан", f"{user.mention} (`{user}`)", col=0x2ECC71))

    # ───────── структура сервера ─────────

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        await self._log(channel.guild, "server", core.make_embed(
            "📁 Канал создан", f"{channel.mention} · тип `{channel.type}`", col=0x2ECC71))

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        await self._log(channel.guild, "server", core.make_embed(
            "📁 Канал удалён", f"`#{channel.name}` · тип `{channel.type}`", col=0xE74C3C))

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        if before.name != after.name:
            await self._log(after.guild, "server", core.make_embed(
                "📁 Канал переименован", f"{after.mention}: `{before.name}` → `{after.name}`", col=0x3498DB))

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: disnake.Role):
        await self._log(role.guild, "server", core.make_embed("🎭 Роль создана", f"{role.mention}", col=0x2ECC71))

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: disnake.Role):
        await self._log(role.guild, "server", core.make_embed("🎭 Роль удалена", f"`{role.name}`", col=0xE74C3C))

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: disnake.Role, after: disnake.Role):
        changes = []
        if before.name != after.name:
            changes.append(f"Название: `{before.name}` → `{after.name}`")
        if before.color != after.color:
            changes.append(f"Цвет: `{before.color}` → `{after.color}`")
        if before.permissions != after.permissions:
            changes.append("Изменены права роли")
        if changes:
            await self._log(after.guild, "server", core.make_embed(
                "🎭 Роль изменена", f"{after.mention}\n" + "\n".join(changes), col=0x3498DB))

    @commands.Cog.listener()
    async def on_guild_update(self, before: disnake.Guild, after: disnake.Guild):
        if before.name != after.name:
            await self._log(after, "server", core.make_embed(
                "🏠 Сервер переименован", f"`{before.name}` → `{after.name}`", col=0x3498DB))

    # ───────── журнал действий (аудит) ─────────

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: disnake.AuditLogEntry):
        try:
            action = str(entry.action).split(".")[-1]
            if action in SKIP_AUDIT:
                return
            executor = entry.user
            if executor is not None and self.bot.user and executor.id == self.bot.user.id:
                return
            guild = entry.guild
            e = core.make_embed(f"🧾 {AUDIT_RU.get(action, action)}", col=0x7F8C8D)
            e.add_field("Кто", executor.mention if executor else "—", inline=True)
            target = entry.target
            if target is not None:
                tm = getattr(target, "mention", None)
                e.add_field("Над кем / чем", tm or core.clip(str(target), 200), inline=True)
            if entry.reason:
                e.add_field("Причина", core.clip(entry.reason, 500), inline=False)
            try:
                before = dict(iter(entry.before))
                after = dict(iter(entry.after))
                lines = []
                for k in list(after.keys())[:8]:
                    bv, av = before.get(k), after.get(k)
                    if k == "roles":
                        bv = ", ".join(getattr(r, "name", str(r)) for r in (bv or [])) or "—"
                        av = ", ".join(getattr(r, "name", str(r)) for r in (av or [])) or "—"
                    lines.append(f"`{k}`: {core.clip(bv, 80)} → {core.clip(av, 80)}")
                if lines:
                    e.add_field("Изменения", core.clip("\n".join(lines), 1000), inline=False)
            except Exception:
                pass
            await self._log(guild, "audit", e)
        except Exception:
            pass

    # ───────── команды и события бота ─────────

    @commands.Cog.listener()
    async def on_slash_command_completion(self, inter: disnake.ApplicationCommandInteraction):
        if inter.guild is None:
            return
        name = getattr(inter.application_command, "qualified_name", inter.data.name)
        e = core.make_embed("⌨️ Команда использована", col=0x95A5A6)
        e.add_field("Кто", f"{inter.author.mention}", inline=True)
        e.add_field("Команда", f"`/{name}`", inline=True)
        e.add_field("Канал", f"<#{inter.channel_id}>", inline=True)
        try:
            opts = getattr(inter, "filled_options", None)
            if opts:
                e.add_field("Параметры", core.clip(str(opts), 400), inline=False)
        except Exception:
            pass
        await self._log(inter.guild, "commands", e)

    @commands.Cog.listener()
    async def on_slash_command_error(self, inter: disnake.ApplicationCommandInteraction, error: Exception):
        if isinstance(error, (commands.CommandNotFound, WarnGuardBlocked)) or inter.guild is None:
            return
        name = getattr(inter.application_command, "qualified_name", inter.data.name)
        e = core.make_embed("❌ Ошибка команды", kind="err")
        e.add_field("Кто", inter.author.mention, inline=True)
        e.add_field("Команда", f"`/{name}`", inline=True)
        e.add_field("Ошибка", core.clip(f"{type(error).__name__}: {error}", 900), inline=False)
        await self._log(inter.guild, "bot", e)

    @commands.Cog.listener()
    async def on_ready(self):
        if self._started:
            return
        self._started = True
        for g in self.bot.guilds:
            e = core.make_embed("✅ Бот запущен", kind="ok")
            e.description = f"Серверов: **{len(self.bot.guilds)}** · Задержка: **{round(self.bot.latency * 1000)} мс**"
            await self._log(g, "bot", e)


def setup(bot: commands.Bot):
    bot.add_cog(ServerLogs(bot))
