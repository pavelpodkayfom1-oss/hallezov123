import re

import disnake
from disnake.ext import commands

from utils.checks import is_admin, load_config, save_config
from utils.embeds import base_embed

TWITCH_COLOR = 0x9146FF
TWITCH_URL_RE = re.compile(r"^(?:https?://)?(?:www\.|m\.)?twitch\.tv/(?P<login>[^\s/?#]+)", re.IGNORECASE)


def _can_post(member: disnake.Member) -> bool:
    if is_admin(member):
        return True
    allowed = set(load_config().get("media_role_ids", []))
    return any(role.id in allowed for role in member.roles)


class Media(commands.Cog):
    """Анонсы Twitch-стримов в отдельном канале."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command(name="media", description="Анонсировать стрим на Twitch")
    async def media(
        self,
        inter: disnake.ApplicationCommandInteraction,
        url: str = commands.Param(name="ссылка", description="Ссылка на канал, например https://twitch.tv/kanald"),
        note: str = commands.Param(name="о_чём", description="Что стримишь", default=""),
    ):
        if not _can_post(inter.author):
            return await inter.response.send_message(
                "⛔ Анонсировать стримы могут только участники с ролью из `/media_role`.",
                ephemeral=True,
            )
        match = TWITCH_URL_RE.match(url.strip())
        if not match:
            return await inter.response.send_message(
                "❌ Нужна ссылка вида `https://twitch.tv/kanald`.", ephemeral=True
            )

        channel_id = load_config().get("channels", {}).get("media_channel_id")
        target = self.bot.get_channel(channel_id) if channel_id else None
        if target is None:
            return await inter.response.send_message(
                "❌ Канал для анонсов не настроен. Попроси администрацию: `/media_channel`.",
                ephemeral=True,
            )

        login = match.group("login")
        link = f"https://twitch.tv/{login}"
        emb = base_embed("🔴 Стрим на Twitch", f"[**{link.removeprefix('https://')}**]({link})", guild=inter.guild)
        emb.color = disnake.Color(TWITCH_COLOR)
        if note:
            emb.add_field(name="📝 От автора", value=note[:600], inline=False)
        emb.add_field(name="👤 Анонсировал", value=inter.author.mention, inline=False)

        view = disnake.ui.View(timeout=None)
        view.add_item(disnake.ui.Button(style=disnake.ButtonStyle.url, label="🔴 Открыть на Twitch", url=link))

        try:
            await target.send(embed=emb, view=view)
        except disnake.Forbidden:
            return await inter.response.send_message(
                f"⛔ У бота нет прав писать в {target.mention}.", ephemeral=True
            )

        await inter.response.send_message(f"✅ Анонс опубликован в {target.mention}.", ephemeral=True)

    @commands.slash_command(name="media_channel", description="Канал для анонсов стримов (для руководства)")
    async def media_channel(
        self,
        inter: disnake.ApplicationCommandInteraction,
        channel: disnake.TextChannel = commands.Param(name="канал", description="Сюда бот будет выкладывать анонсы"),
    ):
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Нет доступа.", ephemeral=True)
        config = load_config()
        config.setdefault("channels", {})["media_channel_id"] = channel.id
        save_config(config)
        await inter.response.send_message(f"✅ Канал для анонсов: {channel.mention}", ephemeral=True)

    @commands.slash_command(
        name="media_role",
        description="Выдать или забрать право анонсировать стримы (для руководства)",
    )
    async def media_role(
        self,
        inter: disnake.ApplicationCommandInteraction,
        role: disnake.Role = commands.Param(name="роль", description="Роль, которой переключается право поста"),
    ):
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Нет доступа.", ephemeral=True)
        config = load_config()
        ids = list(config.get("media_role_ids", []))
        if role.id in ids:
            ids.remove(role.id)
            action = "🚫 Забрано"
        else:
            ids.append(role.id)
            action = "✅ Выдано"
        config["media_role_ids"] = ids
        save_config(config)

        if ids:
            current = "\nСейчас могут постить: " + ", ".join(f"<@&{rid}>" for rid in ids)
        else:
            current = "\nСписок пуст — право осталось только у администрации."
        await inter.response.send_message(f"{action} право анонсировать для {role.mention}.{current}", ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(Media(bot))
