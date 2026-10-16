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
    def render_//_emojis(text): return text
class Memes(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = 'database.db'
        self.init_db()
        self.daily_meme_loop.start()

    def init_db(self):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS family_memes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    added_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ Error init_db: {e}")

    def get_db_connection(self):
        return sqlite3.connect(self.db_path)

    def get_fallback_meme(self):
        fallbacks = [
            ("POV: Когда ты набил 100 часов актива, а админ говорит 'Ну, неплохо для начала' 💀", "https://i.imgflip.com/4t0m_1.jpg"),
            ("POV: Твоё лицо, когда в войсе начался разбор за ники 🗿", "https://i.imgflip.com/1otk91.jpg"),
            ("POV: Зашел в семью Hallez FAMQ и понял, что теперь твоя жизнь — это войс 24/7 😂", "https://i.imgflip.com/26am.jpg"),
            ("POV: Пытаешься объяснить, что ты не АФК, а просто в глубоких раздумьях 🤡", "https://i.imgflip.com/4t0m_1.jpg"),
            ("POV: Тот самый момент, когда наконец-то получил повышение до 3 ранга 👑", "https://i.imgflip.com/2//_2.jpg"),
            ("POV: Когда зашел в войс и там тишина, потому что все на сборе 😱", "https://i.imgflip.com/1otk91.jpg"),
            ("POV: Смотришь на актив самого топового чела в семье и понимаешь, что он не спит 💀", "https://i.imgflip.com/4t0m_1.jpg"),
            ("POV: Когда админ сказал 'Собрание через 5 минут', а ты только лег спать 😭", "https://i.imgflip.com/26am.jpg"),
        ]
        return random.choice(fallbacks)

    @tasks.loop(hours=24)
    async def daily_meme_loop(self):
        cfg = load_config()
        channel_id = cfg.get("channels", {}).get("memes_channel_id")
        if not channel_id: return
        channel = self.bot.get_channel(channel_id)
        if not channel: return
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT url FROM family_memes ORDER BY RANDOM() LIMIT 1")
        result = cursor.fetchone()
        conn.close()
        caption, meme_url = self.get_fallback_meme()
        if result:
            meme_url = result[0]
        else:
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get("https://meme-api.com/meme", timeout=5) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            meme_url = data.get("url")
                except: pass
        if meme_url:
            title = render_emojis("{e:laugh} МЕМ ДНЯ: Hallez FAMQ")
            desc = f"**{caption}**\n\nЛовите заряд настроения! 👇"
            emb = base_embed(title, desc, guild=channel.guild)
            emb.set_image(url=meme_url)
            try: await channel.send(embed=emb)
            except: pass

    @commands.slash_command(name="add_meme", description="Добавить свой мем в базу (Для руководства)")
    async def add_meme(self, inter: disnake.ApplicationCommandInteraction, url: str):
        if not is_admin(inter.author): return await inter.response.send_message("❌ Нет доступа.", ephemeral=True)
        if not url.startswith(("http://", "https://")): return await inter.response.send_message("❌ Ошибка: нужна прямая ссылка!", ephemeral=True)
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO family_memes (url, added_by) VALUES (?, ?)", (url, inter.author.id))
        conn.commit()
        conn.close()
        await inter.response.send_message(render_emojis("{e:check} Мем добавлен!"), ephemeral=True)

    @commands.slash_command(name="meme", description="Получить мем (КД 20 сек)")
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def meme(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer()
        fallback_caption, fallback_url = self.get_fallback_meme()
        conn = self.get_db_//_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT url FROM family_memes ORDER BY RANDOM() LIMIT 1")
        result = cursor.fetchone()
        conn.close()
        caption = fallback_caption
        meme_url = None
        if result:
            meme_url = result[0]
        else:
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get("https://meme-api.com/meme", timeout=5) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            meme_url = data.get("url")
                except: pass
        if not meme_url:
            meme_url = fallback_url
        title = render_emojis("{e:laugh} РАНДОМНЫЙ МЕМ")
        desc = f"**{caption}**\n\nСвежий прикол для семьи 👇"
        emb = base_embed(title, desc, guild=inter.guild)
        emb.set_image(url=meme_url)
        await inter.edit_original_response(embed=emb)

    @meme.error
    async def meme_error(self, inter: disnake.ApplicationCommandInteraction, error):
        if isinstance(error, commands.CommandOnCooldown):
            await inter.response.send_message(f"⏳ Слишком быстро! Погоди {error.retry_after:.0f} сек.!", ephemeral=True)

    @commands.slash_command(name="meme_channel", description="Канал для ежедневных мемов (Для руководства)")
    async def meme_channel(self, inter: disnake.ApplicationCommandInteraction, channel: disnake.TextChannel):
        if not is_admin(inter.author): return await inter.response.send_message("❌ Нет доступа.", ephemeral=True)
        from utils.checks import save_config
        cfg = load_config()
        if "channels" not in cfg: cfg["channels"] = {}
        cfg["channels"]["memes_channel_id"] = channel.id
        save_config(cfg)
        await inter.response.send_message(f"✅ Канал установлен: {channel.mention}", ephemeral=True)

def setup(bot):
    bot.add_cog(Memes(bot))
