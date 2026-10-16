import disnake
from disnake.ext import commands, tasks
import sqlite3
import random
import asyncio
import aiohttp
from datetime import datetime

from utils.checks import load_config, is_admin
from utils.embeds import base_embed, success_embed
try:
    from cogs.emoji_manager import render_emojis
except ImportError:
    def render_emojis(text): return text

class Memes(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = 'database.db'
        self.daily_meme_loop.start()

    def get_db_connection(self):
        return sqlite3.connect(self.db_path)

    @tasks.loop(hours=24)
    async def daily_meme_loop(self):
        """Рассылка случайного мема раз в сутки."""
        cfg = load_config()
        channel_id = cfg.get("channels", {}).get("memes_channel_id")
        if not channel_id:
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        # Пытаемся взять из локальной базы, если нет - берем рандомный из сети
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT url FROM family_memes ORDER BY RANDOM() LIMIT 1")
        result = cursor.fetchone()
        conn.close()

        if result:
            meme_url = result[0]
        else:
            # Запасной вариант: рандомный мем из открытого API
            async with aiohttp.ClientSession() as session:
                async with session.get("https://meme-api.com/meme") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        meme_url = data.get("url")
                    else:
                        meme_url = None

        if meme_url:
            title = render_emojis("{e:laugh} МЕМ ДНЯ: Hallez FAMQ")
            desc = "Ловите свежий заряд настроения на сегодня! 👇"
            emb = base_embed(title, desc, guild=channel.guild)
            emb.set_image(url=meme_url)
            try:
                await channel.send(embed=emb)
            except Exception as e:
                print(f"Ошибка при отправке ежедневного мема: {e}")

    @commands.slash_command(
        name="add_meme", 
        description="Добавить свой мем в базу (Для руководства)"
    )
    async def add_meme(self, inter: disnake.ApplicationCommandInteraction, url: str):
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Нет доступа.", ephemeral=True)

        if not url.startswith(("http://", "https://")):
            return await inter.response.send_message("❌ Ошибка: Нужно ввести прямую ссылку на изображение!", ephemeral=True)

        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO family_memes (url, added_by) VALUES (?, ?)", (url, inter.author.id))
        conn.commit()
        conn.close()

        await inter.response.send_message(render_emojis("{e:check} Мем добавлен в локальную базу!"), ephemeral=True)

    @commands.slash_command(
        name="meme", 
        description="Получить рандомный мем (КД 20 сек)"
    )
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def meme(self, inter: disnake.ApplicationCommandInteraction):
        # Сначала пробуем взять локальный мем (которые загрузили админы)
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT url FROM family_memes ORDER BY RANDOM() LIMIT 1")
        result = cursor.fetchone()
        conn.close()

        # Если локальных нет или с шансом 50% берем из сети (для разнообразия)
        if not result or random.random() > 0.5:
            async with aiohttp.ClientSession() as session:
                try:
                    # Используем API с популярными мемами
                    async with session.get("https://meme-api.com/meme", timeout=5) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            meme_url = data.get("url")
                        else:
                            meme_url = None
                except:
                    meme_url = None
        else:
            meme_url = result[0]

        if not meme_url:
            return await inter.response.send_message("📦 Мемы временно закончились, попробуй чуть позже!", ephemeral=True)

        title = render_emojis("{e:laugh} РАНДОМНЫЙ МЕМ")
        desc = "Свежий прикол для Hallez FAMQ 👇"
        emb = base_embed(title, desc, guild=inter.guild)
        emb.set_image(url=meme_url)

        await inter.response.send_message(embed=emb)

    @meme.error
    async def meme_error(self, inter: disnake.ApplicationCommandInteraction, error):
        if isinstance(error, commands.CommandOnCooldown):
            await inter.response.send_message(
                f"⏳ Слишком быстро! Погоди {error.retry_after:.0f} сек. и запрашивай новый мем!", 
                ephemeral=True
            )

    @commands.slash_command(
        name="meme_channel", 
        description="Установить канал для ежедневных мемов (Для руководства)"
    )
    async def meme_channel(self, inter: disnake.ApplicationCommandInteraction, channel: disnake.TextChannel):
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Нет доступа.", ephemeral=True)

        from utils.checks import save_config
        cfg = load_config()
        if "channels" not in cfg:
            cfg["channels"] = {}
        
        cfg["channels"]["memes_channel_id"] = channel.id
        save_config(cfg)

        await inter.response.send_message(f"✅ Канал для ежедневных мемов установлен: {channel.mention}", ephemeral=True)

def setup(bot):
    bot.add_cog(Memes(bot))
