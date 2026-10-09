import disnake
from disnake.ext import commands
from typing import Optional
from utils.checks import load_config, is_recruiter, is_admin
from utils.embeds import base_embed, success_embed, error_embed, warning_embed
from database import (
    get_member,
    find_member_by_static,
    add_warn,
    get_active_warns,
    remove_warn
)

class Profile(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command(
        name="profile",
        description="Просмотреть карточку бойца семьи Hallez FAMQ"
    )
    async def profile_command(
        self,
        inter: disnake.ApplicationCommandInteraction,
        member: Optional[disnake.Member] = commands.Param(description="Пользователь для просмотра", default=None)
    ):
        target = member or inter.author
        db_member = await get_member(target.id)
        warns = await get_active_warns(target.id)
        config = load_config()
        ranks_cfg = config.get("ranks", {})

        if not db_member:
            await inter.response.send_message(
                f"ℹ️ Участник {target.mention} еще не зарегистрирован в базе данных семьи Hallez FAMQ.",
                ephemeral=True
            )
            return

        rank_num = db_member.get("rank", 1)
        rank_name = ranks_cfg.get(str(rank_num), {}).get("name", f"{rank_num} ранг")

        emb = base_embed(
            f"👤 Личное дело бойца: {db_member['nick']}",
            f"Карточка участника семьи **Hallez FAMQ** на сервере Majestic RP."
        )
        if target.avatar:
            emb.set_thumbnail(url=target.avatar.url)

        emb.add_field(name="Discord", value=target.mention, inline=True)
        emb.add_field(name="Игровой ник", value=f"`{db_member['nick']}`", inline=True)
        emb.add_field(name="Статический ID", value=f"`{db_member['static_id']}`", inline=True)
        emb.add_field(name="Ранг в семье", value=f"**{rank_name}** (`{rank_num}` ранг)", inline=True)
        emb.add_field(name="Дата вступления", value=f"`{db_member['joined_at'][:10]}`", inline=True)
        
        warn_count = len(warns)
        warn_status = f"`{warn_count}/3` ⚠️" if warn_count > 0 else "`0/3` ✅"
        emb.add_field(name="Выговоры", value=warn_status, inline=True)

        if warns:
            warn_list_str = "\n".join([f"• ID `{w['id']}`: {w['reason']} (<@{w['admin_id']}>)" for w in warns[:3]])
            emb.add_field(name="Последние выговоры", value=warn_list_str, inline=False)

        await inter.response.send_message(embed=emb)

    @commands.slash_command(
        name="find",
        description="Поиск участника семьи по статическому ID (Static ID)"
    )
    async def find_command(
        self,
        inter: disnake.ApplicationCommandInteraction,
        static_id: str = commands.Param(description="Статический ID игрока на Majestic")
    ):
        db_member = await find_member_by_static(static_id.strip())
        if not db_member:
            await inter.response.send_message(f"❌ Боец со статиком `{static_id}` не найден в семье Hallez FAMQ.", ephemeral=True)
            return

        user_id = db_member["user_id"]
        member = inter.guild.get_member(user_id)
        config = load_config()
        ranks_cfg = config.get("ranks", {})
        rank_num = db_member.get("rank", 1)
        rank_name = ranks_cfg.get(str(rank_num), {}).get("name", f"{rank_num} ранг")

        emb = base_embed(
            f"🔎 Результат поиска по статику: {static_id}",
            f"Найден участник семьи Hallez FAMQ."
        )
        emb.add_field(name="Игровой ник", value=f"`{db_member['nick']}`", inline=True)
        emb.add_field(name="Статик", value=f"`{db_member['static_id']}`", inline=True)
        emb.add_field(name="Ранг", value=f"**{rank_name}** (`{rank_num}`)", inline=True)
        emb.add_field(name="Discord", value=f"<@{user_id}>" if not member else member.mention, inline=True)
        await inter.response.send_message(embed=emb)

    @commands.slash_command(
        name="warn",
        description="Выдать семейный выговор участнику (Для старшего состава)"
    )
    async def warn_command(
        self,
        inter: disnake.ApplicationCommandInteraction,
        member: disnake.Member = commands.Param(description="Участник семьи"),
        reason: str = commands.Param(description="Причина выдачи выговора")
    ):
        if not is_recruiter(inter.author):
            await inter.response.send_message("❌ У вас нет прав для выдачи выговоров!", ephemeral=True)
            return

        warn_id = await add_warn(member.id, inter.author.id, reason)
        active_warns = await get_active_warns(member.id)
        total_warns = len(active_warns)

        emb = warning_embed(
            "Выдан семейный выговор!",
            f"👤 **Нарушитель:** {member.mention}\n"
            f"👮 **Выдал:** {inter.author.mention}\n"
            f"📝 **Причина:** {reason}\n"
            f"🔢 **ID выговора:** `#{warn_id}`\n"
            f"⚠️ **Текущий счетчик:** `{total_warns}/3`"
        )

        if total_warns >= 3:
            emb.add_field(
                name="🚨 ВНИМАНИЕ РУКОВОДСТВУ",
                value=f"Участник {member.mention} набрал критическое количество выговоров (**{total_warns}/3**)! Рекомендуется исключение из семьи Hallez FAMQ.",
                inline=False
            )

        await inter.response.send_message(embed=emb)

        # ЛС нарушителю
        try:
            dm_emb = warning_embed(
                "Вам выдан семейный выговор в Hallez FAMQ",
                f"Вам был назначен выговор в семье.\n"
                f"**Причина:** {reason}\n"
                f"**Выдал:** {inter.author.display_name}\n"
                f"**Всего активных выговоров:** `{total_warns}/3`"
            )
            await member.send(embed=dm_emb)
        except Exception:
            pass

        # Лог
        config = load_config()
        log_channel_id = config.get("channels", {}).get("logs_channel_id")
        if log_channel_id:
            log_ch = inter.guild.get_channel(log_channel_id)
            if log_ch:
                await log_ch.send(embed=emb)

    @commands.slash_command(
        name="unwarn",
        description="Снять выговор с участника семьи"
    )
    async def unwarn_command(
        self,
        inter: disnake.ApplicationCommandInteraction,
        warn_id: int = commands.Param(description="ID выговора (см. /profile или /warns)")
    ):
        if not is_admin(inter):
            await inter.response.send_message("❌ Снимать выговоры может только администрация семьи!", ephemeral=True)
            return

        success = await remove_warn(warn_id)
        if success:
            await inter.response.send_message(f"✅ Выговор `#{warn_id}` успешно аннулирован.", ephemeral=True)
        else:
            await inter.response.send_message(f"❌ Выговор с ID `#{warn_id}` не найден или уже снят.", ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(Profile(bot))
