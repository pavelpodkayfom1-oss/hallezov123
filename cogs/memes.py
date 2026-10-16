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

    async def generate_ai_caption(self):
        """Пытается получить смешную подпись от Gemini через существующие модули бота."""
        # Ищем любой модуль, который может работать с AI (например, по названию в extensions)
        ai_cog = None
        for ext in self.bot.extensions:
            if "ai" in ext.lower() or "gemini" in ext.lower() or "pishi" in ext.lower():
                cog = self.bot.get_cog(ext.split('.')[-1].capitalize()) # Попытка найти Cog
                if cog:
                    ai_cog = cog
                    break
        
        # Если нашли какой-то AI-модуль, пробуем вызвать у него метод генерации
        # (Так как мы не знаем точного названия функции, используем общие варианты)
        if ai_cog:
            try:
                # Пробуем самые частые названия функций для ИИ
                for method_name in ["generate", "ask", "chat", "get_response"]:
                    method = getattr(ai_cog, method_name, None)
                    if method:
                        prompt = "Придумай очень короткий и смешной POV-мем для семьи 'Hallez FAMQ'. Тема: актив, повышения, войсы или приколы в семье. Стиль: ТикТок. Ответ должен быть только одним предложением."
                        return await method(prompt)
            except:
                pass
        
        # Запасные варианты, если AI не найден или ошибка (чтобы команда не падала)
        backups = [
            "POV: Ты зашел в войс в 3 часа ночи, а там админы всё еще обсуждают повышения 💀",
            "POV: Когда набил 100 часов актива, но тебя всё равно называют новичком 😂",
            "POV: Тот самый момент, когда наконец-то получил роль в Hallez FAMQ 👑",
            "POV: Твоё лицо, когда увидел, сколько часов в войсе у самого активного чела 😱",
            "POV: Пытаешься объяснить админу, что ты не АФК, а просто задумался о смысле жизни 🗿"
        ]
        return random.choice(backups)

    @tasks.loop(hours=24)
    async def daily_meme_loop(self):
        cfg = load_config()
        channel_id = cfg.get("channels", {}).get("memes_channel_id")
        if not channel_id: return
        channel = self.bot.get_channel(channel_id)
        if not channel: return

        caption = await self.generate_ai_caption()
        
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT url FROM family_memes ORDER BY RANDOM() LIMIT 1")
        result = cursor.fetchone()
        conn.close()

        meme_url = result[0] if result else None
        if not meme_url:
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get("https://meme-api.com/meme", timeout=5) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            meme_url = data.get("url")
                except: meme_url = None

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

    @commands.slash_command(name="meme", description="Получить мем с ИИ-подписью (КД 20 сек)")
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def meme(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer() # Чтобы бот не выдал "внутреннюю ошибку" пока ИИ думает

        # 1. Генерируем умную подпись через Gemini
        caption = await self.generate_ai_caption()

        # 2. Ищем картинку
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT url FROM family_memes ORDER BY RANDOM() LIMIT 1")
        result = cursor.fetchone()
        conn.close()

        if not result or random.random() > 0.5:
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get("https://meme-api.com/meme", timeout=5) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            meme_url = data.get("url")
                        else: meme_url = None
                except: meme_url = None
        else:
            meme_url = result[0]

        if not meme_url:
            return await inter.edit_original_response("📦 Мемы временно закончились, попробуй чуть позже!")

        title = render_emojis("{e:laugh} AI-МЕМ: Hallez FAMQ")
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
