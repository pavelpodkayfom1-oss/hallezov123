import disnake
from disnake.ext import commands
import random
from utils.checks import is_admin

class RandomPartner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.slash_command(
        name="random_partner", 
        description="Найти случайного напарника для общения"
    )
    async def random_partner(self, inter: disnake.ApplicationCommandInteraction):
        # Собираем всех участников сервера, кроме бота и того, кто вызвал команду
        members = [m for m in inter.guild.members if not m.bot and m.id != inter.author.id]
        
        if not members:
            return await inter.response.send_message("Слышь, тут кроме тебя никого нет, с кем бы напарниться! 💀", ephemeral=True)

        partner = random.choice(members)
        
        await inter.response.defer()

        try:
            # Создаем ветку (thread) в текущем канале, где была вызвана команда
            # Если канал не поддерживает треды, создаст обычный канал (зависит от прав)
            thread = await inter.channel.create_thread(
                name=f"🤝 {inter.author.display_name} x {partner.display_name}",
                type=disnake.ChannelType.public_thread # Публичный тред, но доступ ограничен
            )
            
            # Добавляем напарника в тред
            await thread.add_user(partner)
            
            instruction = (
                f"Слышьте, **{inter.author.mention}** и **{partner.mention}**!\n"
                f"Вы теперь напарники на сегодня. Общайтесь, решайте дела или просто задвигайте хуйню.\n"
                f"Лишним быть запрещено! Развлекайтесь, ебать! 💎🔥"
            )
            
            await thread.send(instruction)
            await inter.edit_original_response(content=f"✅ Нашел тебе напарника! Тебя и {partner.mention} закинули в ветку: {thread.mention}")
            
        except Exception as e:
            await inter.edit_original_response(content=f"❌ Бля, что-то пошло не так: {e}")

def setup(bot):
    bot.add_cog(RandomPartner(bot))
