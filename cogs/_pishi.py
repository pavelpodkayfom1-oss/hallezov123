"""
/pishi — отправить сообщение от имени бота в выбранный канал.

Использование:
  /pishi канал:#общий текст:Привет всем      — быстро, одной строкой
  /pishi канал:#общий                         — откроется окно для длинного текста
                                                с переносами строк
  Необязательно:
    оформление — обычное сообщение или рамка (embed)
    ответ_на   — ID сообщения, на которое бот ответит

Доступ: администраторы бота (admin_user_ids / admin_role_ids в config.json)
или право «Администратор» на сервере.
"""
from typing import Optional

import disnake
from disnake.ext import commands

from utils.checks import load_config

MAX_LEN = 2000        # лимит Discord для обычного сообщения
MAX_EMBED_LEN = 4000  # лимит описания embed


def _can_use(member) -> bool:
    cfg = load_config()
    if member.id in cfg.get("admin_user_ids", []):
        return True
    if isinstance(member, disnake.Member):
        if member.guild_permissions.administrator:
            return True
        ids = {r.id for r in member.roles}
        return bool(ids & set(cfg.get("admin_role_ids", [])))
    return False


def _allowed_mentions(member: disnake.Member) -> disnake.AllowedMentions:
    """@everyone/@here работают только если у самого автора есть на это право."""
    return disnake.AllowedMentions(
        everyone=member.guild_permissions.mention_everyone,
        users=True,
        roles=True,
        replied_user=False,
    )


async def _send(
    inter: disnake.Interaction,
    channel: disnake.abc.Messageable,
    text: str,
    as_embed: bool,
    reply_to: Optional[int],
):
    """Отправляет сообщение и отвечает автору (скрытно) результатом."""
    guild = inter.guild
    me = guild.me if guild else None

    if isinstance(channel, disnake.abc.GuildChannel) and me is not None:
        perms = channel.permissions_for(me)
        if not perms.view_channel or not perms.send_messages:
            await inter.edit_original_response(
                f"❌ У бота нет прав писать в {channel.mention}."
            )
            return
        if as_embed and not perms.embed_links:
            await inter.edit_original_response(
                f"❌ В {channel.mention} у бота нет права «Встраивать ссылки» (нужно для рамки)."
            )
            return

    reference = None
    if reply_to:
        try:
            ref_msg = await channel.fetch_message(reply_to)
            reference = ref_msg.to_reference(fail_if_not_exists=False)
        except (disnake.NotFound, disnake.HTTPException):
            await inter.edit_original_response(
                "❌ Сообщение для ответа не найдено в этом канале (проверьте ID)."
            )
            return

    kwargs = {
        "allowed_mentions": _allowed_mentions(inter.author),
        "reference": reference,
    }
    if as_embed:
        kwargs["embed"] = disnake.Embed(description=text, color=disnake.Color.blurple())
    else:
        kwargs["content"] = text

    try:
        sent = await channel.send(**kwargs)
    except disnake.Forbidden:
        await inter.edit_original_response("❌ Discord не разрешил отправку (нет доступа).")
        return
    except disnake.HTTPException as e:
        await inter.edit_original_response(f"❌ Не удалось отправить: {e}")
        return

    print(
        f"📝 /pishi: {inter.author} ({inter.author.id}) -> #{getattr(channel, 'name', channel)} "
        f"[{sent.id}] {len(text)} симв.",
        flush=True,
    )
    await inter.edit_original_response(f"✅ Отправлено: {sent.jump_url}")


class PishiModal(disnake.ui.Modal):
    """Окно для длинного текста с переносами строк."""

    def __init__(self, channel, as_embed: bool, reply_to: Optional[int]):
        self.channel = channel
        self.as_embed = as_embed
        self.reply_to = reply_to
        limit = MAX_EMBED_LEN if as_embed else MAX_LEN
        super().__init__(
            title="Сообщение от имени бота",
            custom_id=f"pishi_modal_{channel.id}",
            components=[
                disnake.ui.TextInput(
                    label="Текст сообщения",
                    custom_id="text",
                    style=disnake.TextInputStyle.paragraph,
                    placeholder="Можно писать в несколько строк",
                    min_length=1,
                    max_length=limit,
                    required=True,
                )
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        await inter.response.defer(ephemeral=True)
        text = inter.text_values["text"].strip()
        if not text:
            await inter.edit_original_response("❌ Пустое сообщение.")
            return
        await _send(inter, self.channel, text, self.as_embed, self.reply_to)


class Pishi(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command(
        name="pishi",
        description="Отправить сообщение от имени бота в выбранный канал",
        dm_permission=False,
    )
    async def pishi(
        self,
        inter: disnake.ApplicationCommandInteraction,
        channel: disnake.TextChannel = commands.Param(
            name="канал", description="Куда отправить"
        ),
        text: Optional[str] = commands.Param(
            name="текст",
            description="Что написать (если пусто — откроется окно для длинного текста)",
            default=None,
            max_length=MAX_LEN,
        ),
        style: str = commands.Param(
            name="оформление",
            description="Обычное сообщение или рамка",
            choices={"Обычное": "plain", "Рамка (embed)": "embed"},
            default="plain",
        ),
        reply_to: Optional[str] = commands.Param(
            name="ответ_на",
            description="ID сообщения, на которое бот ответит (необязательно)",
            default=None,
        ),
    ):
        if not _can_use(inter.author):
            await inter.response.send_message(
                "⛔ Эта команда доступна только администрации.", ephemeral=True
            )
            return

        reply_id: Optional[int] = None
        if reply_to:
            if not reply_to.strip().isdigit():
                await inter.response.send_message(
                    "❌ «ответ_на» должен быть числовым ID сообщения.", ephemeral=True
                )
                return
            reply_id = int(reply_to.strip())

        as_embed = style == "embed"

        # Текст не указан — открываем окно для длинного/многострочного текста.
        if text is None:
            await inter.response.send_modal(PishiModal(channel, as_embed, reply_id))
            return

        # Текст в одну строку; \n в слэш-параметре превращаем в перенос.
        text = text.replace("\\n", "\n").strip()
        if not text:
            await inter.response.send_message("❌ Пустое сообщение.", ephemeral=True)
            return

        await inter.response.defer(ephemeral=True)
        await _send(inter, channel, text, as_embed, reply_id)


def setup(bot: commands.Bot):
    bot.add_cog(Pishi(bot))
