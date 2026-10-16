import io
import random
import sqlite3
from typing import List, Optional, Tuple

import aiohttp
import disnake
from disnake.ext import commands, tasks

from utils.checks import load_config, is_admin
from utils.embeds import base_embed
try:
    from cogs.emoji_manager import render_emojis
except ImportError:
    def render_emojis(text): return text

# Content-Type -> расширение файла
IMAGE_TYPES = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # лимит Discord на файл без буста
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; HallezBot/1.0)"}

CAPTIONS = [
    "POV: Когда ты набил 100 часов актива, а админ говорит «Ну, неплохо для начала» 💀",
    "POV: Твоё лицо, когда в войсе начался разбор за ники 🗿",
    "POV: Зашел в семью Hallez FAMQ и понял, что теперь твоя жизнь — это войс 24/7 😂",
    "POV: Пытаешься объяснить, что ты не АФК, а просто в глубоких раздумьях 🤡",
    "POV: Тот самый момент, когда наконец-то получил повышение до 3 ранга 👑",
    "POV: Когда зашел в войс и там тишина, потому что все на сборе 😱",
    "POV: Смотришь на актив самого топового чела в семье и понимаешь, что он не спит 💀",
    "POV: Когда админ сказал «Собрание через 5 минут», а ты только лег спать 😭",
]


class MemeResult:
    def __init__(self, data: bytes, ext: str, source: str, title: Optional[str] = None):
        self.data = data
        self.ext = ext
        self.source = source
        self.title = title


class Memes(commands.Cog):
    # Вынесено в атрибуты класса, чтобы адреса можно было подменить в тестах
    MEME_API_URL = "https://meme-api.com/gimme"
    IMGFLIP_URL = "https://api.imgflip.com/get_memes"

    def __init__(self, bot):
        self.bot = bot
        self.db_path = 'database.db'
        self.init_db()
        self.daily_meme_loop.start()

    def cog_unload(self):
        self.daily_meme_loop.cancel()

    # ───────────────────────── база ─────────────────────────

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

    def _db_urls(self, limit: int = 3) -> List[str]:
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT url FROM family_memes ORDER BY RANDOM() LIMIT ?", (limit,))
            rows = cursor.fetchall()
            conn.close()
            return [r[0] for r in rows]
        except Exception as e:
            print(f"[memes] ошибка чтения базы мемов: {e!r}")
            return []

    # ───────────────────────── получение мема ─────────────────────────

    async def _download(self, session: aiohttp.ClientSession, url: str) -> Optional[Tuple[bytes, str]]:
        """Скачивает картинку и проверяет, что это действительно картинка."""
        try:
            async with session.get(url, headers=HTTP_HEADERS) as resp:
                if resp.status != 200:
                    print(f"[memes] {url} -> HTTP {resp.status}")
                    return None
                ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
                ext = IMAGE_TYPES.get(ctype)
                if not ext:
                    print(f"[memes] {url} -> это не картинка ({ctype or 'нет Content-Type'})")
                    return None
                data = await resp.content.read(MAX_IMAGE_BYTES + 1)
                if len(data) > MAX_IMAGE_BYTES:
                    print(f"[memes] {url} -> файл больше 8 МБ")
                    return None
                return data, ext
        except Exception as e:
            print(f"[memes] не удалось скачать {url}: {e!r}")
            return None

    async def _from_db(self, session) -> Optional[MemeResult]:
        for url in self._db_urls():
            got = await self._download(session, url)
            if got:
                return MemeResult(got[0], got[1], "база семьи")
        return None

    async def _from_meme_api(self, session) -> Optional[MemeResult]:
        for _ in range(3):
            try:
                async with session.get(self.MEME_API_URL, headers=HTTP_HEADERS) as resp:
                    if resp.status != 200:
                        print(f"[memes] meme-api -> HTTP {resp.status}")
                        continue
                    payload = await resp.json(content_type=None)
            except Exception as e:
                print(f"[memes] meme-api недоступен: {e!r}")
                return None
            if payload.get("nsfw") or payload.get("spoiler"):
                continue
            url = payload.get("url")
            if not url:
                continue
            got = await self._download(session, url)
            if got:
                return MemeResult(got[0], got[1], "meme-api", payload.get("title"))
        return None

    async def _from_imgflip(self, session) -> Optional[MemeResult]:
        """Популярные мем-шаблоны Imgflip (ключ не нужен)."""
        try:
            async with session.get(self.IMGFLIP_URL, headers=HTTP_HEADERS) as resp:
                if resp.status != 200:
                    print(f"[memes] imgflip -> HTTP {resp.status}")
                    return None
                payload = await resp.json(content_type=None)
            memes = payload.get("data", {}).get("memes", [])
        except Exception as e:
            print(f"[memes] imgflip недоступен: {e!r}")
            return None
        random.shuffle(memes)
        for item in memes[:3]:
            got = await self._download(session, item.get("url", ""))
            if got:
                return MemeResult(got[0], got[1], "imgflip", item.get("name"))
        return None

    async def fetch_meme(self) -> Optional[MemeResult]:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for source in (self._from_db, self._from_meme_api, self._from_imgflip):
                result = await source(session)
                if result:
                    return result
        print("[memes] ни один источник не дал картинку")
        return None

    def _build_message(self, meme: MemeResult, title_text: str, footer_text: str, guild):
        filename = f"meme.{meme.ext}"
        caption = random.choice(CAPTIONS)
        desc = f"**{caption}**\n\n{footer_text}"
        if meme.title:
            desc += f"\n\n*{meme.title[:200]}*"
        emb = base_embed(render_emojis(title_text), desc, guild=guild)
        emb.set_image(url=f"attachment://{filename}")
        file = disnake.File(io.BytesIO(meme.data), filename=filename)
        return emb, file

    # ───────────────────────── мем дня ─────────────────────────

    @tasks.loop(hours=24)
    async def daily_meme_loop(self):
        cfg = load_config()
        channel_id = cfg.get("channels", {}).get("memes_channel_id")
        if not channel_id: return
        channel = self.bot.get_channel(channel_id)
        if not channel: return

        meme = await self.fetch_meme()
        if not meme:
            return
        emb, file = self._build_message(
            meme, "{e:laugh} МЕМ ДНЯ: Hallez FAMQ", "Ловите заряд настроения! 👇", channel.guild
        )
        try:
            await channel.send(embed=emb, file=file)
        except Exception as e:
            print(f"[memes] не удалось отправить мем дня: {e!r}")

    # ───────────────────────── команды ─────────────────────────

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

        meme = await self.fetch_meme()
        if not meme:
            return await inter.edit_original_response(
                content="😕 Не получилось достать мем: источники не отвечают. Попробуйте чуть позже."
            )

        emb, file = self._build_message(
            meme, "{e:laugh} РАНДОМНЫЙ МЕМ", "Свежий прикол для семьи 👇", inter.guild
        )
        await inter.edit_original_response(embed=emb, file=file)

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
