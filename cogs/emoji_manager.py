"""cogs/emoji_manager.py — премиум-эмодзи для бота семьи.

Команды (только для админов из config.json / администраторов сервера):
  /emoji_add        — загрузить ОДИН файл с компьютера как эмодзи сервера
  /emoji_folder     — загрузить ВСЕ файлы из папки  emojis  рядом с main.py
  /emoji_sync       — обновить список эмодзи сервера (emoji_map.json)
  /emoji_list       — показать все доступные эмодзи и их коды
  /emoji_bind       — вручную привязать:  🎉  ->  твоё эмодзи  (приоритет над авто)
  /ai_textraspredeli— ИИ (Claude) смотрит на картинки эмодзи и сам расставляет их по текстам
                      (нужен ANTHROPIC_API_KEY в .env; без него — подбор по именам)
  /emoji_restore    — откатить последнее изменение текстов

Из других когов можно писать в тексте {e:имя_эмодзи} и вызывать
    from cogs.emoji_manager import render_emojis
    text = render_emojis(text)
"""
import asyncio
import base64
import io
import json
import os
import re
import shutil
import time

import aiohttp
import disnake
from disnake.ext import commands

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except ImportError:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMOJI_DIR = os.path.join(BASE, "emojis")
MAP_FILE = os.path.join(BASE, "emoji_map.json")      # имя -> код <a:имя:id>
BIND_FILE = os.path.join(BASE, "emoji_bind.json")    # 🎉 -> имя эмодзи
CONFIG_FILE = os.path.join(BASE, "config.json")
HALL_FILE = os.path.join(BASE, "hall_settings.json")
BACKUP_DIR = os.path.join(BASE, "emoji_backup")

MIME = {".gif", ".png", ".webp", ".jpg", ".jpeg"}
MAX_SIZE = 256 * 1024

# обычные эмодзи: символы + вариации + склейки + цифры в рамке
_C = r"[\u2300-\u23ff\u25a0-\u25ff\u2600-\u27bf\u2b00-\u2bff\U0001F000-\U0001FAFF]"
EMOJI_RE = re.compile(
    rf"(?:[0-9#*]\ufe0f?\u20e3|{_C}\ufe0f?(?:\u200d{_C}\ufe0f?)*)"
)

# какие ключевые слова в ИМЕНИ твоего эмодзи подходят к обычному эмодзи
ALIASES = {
    "🎉": ["party", "tada", "confetti", "celebrate", "congrat", "hype"],
    "🎊": ["party", "tada", "confetti", "celebrate"],
    "✨": ["sparkle", "sparkles", "shine", "glitter", "magic"],
    "🌟": ["star", "glow", "shine"],
    "⭐": ["star"],
    "💎": ["diamond", "gem", "crystal", "jewel"],
    "👑": ["crown", "king", "owner", "leader"],
    "📜": ["scroll", "rules", "paper", "document"],
    "📝": ["note", "form", "write", "pen", "anketa"],
    "📌": ["pin", "pushpin"],
    "📍": ["pin", "location", "place", "marker"],
    "🎖": ["medal", "rank", "award"],
    "🏆": ["trophy", "win", "cup"],
    "🎙": ["mic", "microphone", "voice"],
    "🔎": ["search", "magnify", "find"],
    "🟡": ["yellow"],
    "🟢": ["green", "online"],
    "🔴": ["red", "offline"],
    "⚜": ["fleur", "lily", "emblem", "family"],
    "🤝": ["handshake", "deal", "friend"],
    "👤": ["user", "person", "profile", "member"],
    "🆔": ["static", "idcard"],
    "👇": ["down", "point"],
    "⏳": ["hourglass", "timer", "wait"],
    "✅": ["check", "yes", "done", "success", "tick"],
    "❌": ["cross", "reject", "deny", "fail"],
    "⚠": ["warning", "alert", "warn"],
    "🔥": ["fire", "flame", "hot"],
    "❤": ["heart", "love"],
    "💬": ["chat", "message", "talk"],
    "🔔": ["bell", "notify", "remind"],
    "🏠": ["home", "house"],
    "🔒": ["lock", "closed"],
    "🔓": ["unlock", "open"],
    "📢": ["announce", "megaphone", "news"],
    "🎮": ["game", "gaming", "play"],
    "⚔": ["sword", "battle", "war"],
    "🛡": ["shield", "protect"],
}


# ---------------------------------------------------------------- helpers
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def render_emojis(text: str) -> str:
    """Заменяет {e:имя} на код премиум-эмодзи."""
    emap = load_json(MAP_FILE, {})
    return re.sub(r"\{e:([A-Za-z0-9_]+)\}", lambda m: emap.get(m.group(1), ""), text)


def make_name(filename: str) -> str:
    base = os.path.splitext(filename)[0]
    base = re.sub(r"^\d+-", "", base)
    base = re.sub(r"[^A-Za-z0-9_]", "_", base)
    return (base if len(base) >= 2 else "e_" + base)[:32]


def fit_image(data: bytes, ext: str):
    """Сжимает картинку до лимита Discord (256 КБ). None — не получилось."""
    if len(data) <= MAX_SIZE:
        return data
    try:
        from PIL import Image, ImageSequence
    except ImportError:
        return None
    try:
        im = Image.open(io.BytesIO(data))
        for size in (128, 96, 64):
            out = io.BytesIO()
            if ext == ".gif" and getattr(im, "is_animated", False):
                frames, durs = [], []
                for fr in ImageSequence.Iterator(im):
                    durs.append(fr.info.get("duration", 50))
                    frames.append(fr.convert("RGBA").resize((size, size)))
                frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:],
                               duration=durs, loop=0, disposal=2, optimize=True)
            else:
                im.seek(0)
                im.convert("RGBA").resize((size, size)).save(out, format="PNG", optimize=True)
            result = out.getvalue()
            if len(result) <= MAX_SIZE:
                return result
    except Exception:
        return None
    return None


def sync_map(guild: disnake.Guild) -> dict:
    emap = {e.name: f"<{'a' if e.animated else ''}:{e.name}:{e.id}>" for e in guild.emojis}
    save_json(MAP_FILE, emap)
    return emap


def _match_alias(key: str, names: list):
    m = re.fullmatch(r"([0-9#*])\u20e3", key)
    if m:
        d = m.group(1)
        aliases = [f"number{d}", f"num{d}", f"digit{d}", f"n{d}", d]
    else:
        aliases = ALIASES.get(key, [])
    for name in names:
        low = name.lower()
        tokens = [t for t in re.split(r"[_\W]+", low) if t]
        for a in aliases:
            if low == a or a in tokens:
                return name
            if len(a) >= 4 and not a[-1].isdigit() and a in low:
                return name
    return None


class Replacer:
    """Заменяет обычные эмодзи на премиум по привязкам / именам / (опц.) по очереди."""

    def __init__(self, emap, binds, fill):
        self.emap, self.binds, self.fill = emap, binds, fill
        self.names = list(emap)
        self.used = {n for n in binds.values() if n in emap}
        self.missing, self.count = set(), 0

    def _pick(self, key):
        n = self.binds.get(key)
        if n in self.emap:
            return n
        n = _match_alias(key, self.names)
        if n is None and self.fill:
            free = [x for x in self.names if x not in self.used]
            n = free[0] if free else None
        if n:
            self.binds[key] = n
            self.used.add(n)
        return n

    def __call__(self, m):
        key = m.group(0).replace("\ufe0f", "")
        n = self._pick(key)
        if not n:
            self.missing.add(m.group(0))
            return m.group(0)
        self.count += 1
        return self.emap[n]

    def text(self, s: str) -> str:
        return EMOJI_RE.sub(self, s)


SKIP_PREFIX = ("btn_", "modal_")
SKIP_SUFFIX = ("_label", "_ph", "_placeholder")


def _targets(scope, cfg, hall):
    """Список (имя_файла, словарь, ключ) — какие тексты можно менять."""
    out = []
    for k, v in (cfg.get("texts") or {}).items():
        if not isinstance(v, str) or k.startswith(SKIP_PREFIX) or k.endswith(SKIP_SUFFIX):
            continue
        if scope == "recruit" and not k.startswith("recruit_"):
            continue
        out.append(("config.json", cfg["texts"], k))
    if scope == "all":
        for k, v in hall.items():
            if isinstance(v, str) and k.endswith("_text"):
                out.append(("hall_settings.json", hall, k))
    return out



# ------------------------------------------------------------------ ИИ
CUSTOM_RE = re.compile(r"<a?:\w+:\d+>")
AI_SYSTEM = (
    "Ты оформляешь тексты Discord-бота игровой семьи (Majestic RP). "
    "Тебе дают кастомные премиум-эмодзи (имя, код, картинка) и JSON с текстами. "
    "Верни ТОЛЬКО JSON того же вида {id: новый_текст}, без пояснений и без ```.\n"
    "Правила:\n"
    "1) Меняй только эмодзи: заменяй обычные юникод-эмодзи на подходящие кастомные и можешь "
    "добавить кастомные в начало заголовков и пунктов списка там, где это уместно. Не больше одного эмодзи на строку.\n"
    "2) Весь остальной текст, markdown (**, `, •), переносы строк, плейсхолдеры вида {mention}, {nick}, {server} "
    "и любые упоминания оставь символ в символ.\n"
    "3) Используй только коды из списка и копируй их точно.\n"
    "4) Подбирай по смыслу картинки. Один смысл (принят, отказ, внимание, ранг) всегда одно и то же эмодзи во всех текстах, "
    "но в целом используй разные эмодзи, а не одно на всё.\n"
    "5) Если подходящего эмодзи нет, оставь обычное как есть."
)


def _skeleton(s: str) -> str:
    s = CUSTOM_RE.sub("", s)
    s = EMOJI_RE.sub("", s)
    return re.sub(r"\s+", "", s)


def _valid_ai_text(old: str, new, emap: dict) -> bool:
    if not isinstance(new, str) or _skeleton(new) != _skeleton(old):
        return False
    codes = set(emap.values())
    return all(c in codes for c in CUSTOM_RE.findall(new))


def _thumb_b64(data: bytes):
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.seek(0)
        im = im.convert("RGBA")
        im.thumbnail((64, 64))
        out = io.BytesIO()
        im.save(out, format="PNG", optimize=True)
        return base64.b64encode(out.getvalue()).decode()
    except Exception:
        return None


async def emoji_images(guild, limit=40):
    """[(имя, animated, base64png)] — картинки эмодзи, чтобы ИИ видел, что на них."""
    res = []
    for e in list(guild.emojis)[:limit]:
        try:
            b64 = _thumb_b64(await e.read())
        except Exception:
            b64 = None
        if b64:
            res.append((e.name, e.animated, b64))
    return res


async def ai_distribute(emap: dict, items: dict, images: list, binds: dict):
    """Отдаёт тексты ИИ (Claude). Возвращает (dict | None, ошибка | None)."""
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None, "в .env нет ANTHROPIC_API_KEY"
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5").strip()

    content = [{"type": "text", "text": "Мои премиум-эмодзи (имя = код). Дальше идут их картинки:\n" +
                "\n".join(f"{n} = {c}" for n, c in emap.items())}]
    for name, animated, b64 in images:
        content.append({"type": "text", "text": f"Картинка эмодзи `{name}`" + (" (анимированное):" if animated else ":")})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}})
    hint = ""
    if binds:
        hint = "\nЖёсткие привязки пользователя (соблюдай): " + ", ".join(f"{u} -> {emap[n]}" for u, n in binds.items() if n in emap)
    content.append({"type": "text", "text": "Тексты для оформления (JSON):\n" +
                    json.dumps(items, ensure_ascii=False) + hint})

    body = {"model": model, "max_tokens": 8000, "system": AI_SYSTEM,
            "messages": [{"role": "user", "content": content}]}
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as sess:
            async with sess.post("https://api.anthropic.com/v1/messages", headers=headers, json=body) as r:
                data = await r.json()
                if r.status != 200:
                    return None, str(data.get("error", {}).get("message", data))
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        a, b = text.find("{"), text.rfind("}")
        return json.loads(text[a:b + 1]), None
    except Exception as ex:
        return None, str(ex)


# -------------------------------------------------------------------- cog
class EmojiManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _is_admin(self, inter: disnake.ApplicationCommandInteraction) -> bool:
        cfg = load_json(CONFIG_FILE, {})
        if inter.author.id in cfg.get("admin_user_ids", []):
            return True
        role_ids = set(cfg.get("admin_role_ids", []))
        if any(r.id in role_ids for r in getattr(inter.author, "roles", [])):
            return True
        return inter.author.guild_permissions.administrator

    async def _guard(self, inter) -> bool:
        if inter.guild is None:
            await inter.response.send_message("Только на сервере.", ephemeral=True)
            return False
        if not self._is_admin(inter):
            await inter.response.send_message("⛔ Нет доступа.", ephemeral=True)
            return False
        return True

    async def _upload(self, guild, name, data):
        animated = data[:6] in (b"GIF87a", b"GIF89a")
        same = [e for e in guild.emojis if e.animated == animated]
        if len(same) >= guild.emoji_limit:
            return "нет свободных слотов (" + ("анимированные" if animated else "обычные") + ")"
        try:
            await guild.create_custom_emoji(name=name, image=data, reason="emoji_manager")
        except disnake.HTTPException as ex:
            return str(getattr(ex, "text", ex))
        return None

    # --------------------------------------------------------- загрузка
    @commands.slash_command(name="emoji_add", description="Загрузить эмодзи на сервер из файла с компьютера")
    async def emoji_add(self, inter: disnake.ApplicationCommandInteraction,
                        file: disnake.Attachment, name: str = None):
        if not await self._guard(inter):
            return
        await inter.response.defer(ephemeral=True)
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in MIME:
            return await inter.edit_original_response(content="❌ Нужен файл .gif / .png / .webp / .jpg")
        data = fit_image(await file.read(), ext)
        if data is None:
            return await inter.edit_original_response(content="❌ Файл больше 256 КБ и сжать не удалось.")
        name = re.sub(r"[^A-Za-z0-9_]", "_", name)[:32] if name else make_name(file.filename)
        err = await self._upload(inter.guild, name, data)
        sync_map(inter.guild)
        if err:
            return await inter.edit_original_response(content=f"❌ {err}")
        code = load_json(MAP_FILE, {}).get(name, "")
        await inter.edit_original_response(content=f"✅ Добавлено: {code}  `{name}`")

    @commands.slash_command(name="emoji_folder", description="Загрузить все файлы из папки emojis (рядом с ботом)")
    async def emoji_folder(self, inter: disnake.ApplicationCommandInteraction):
        if not await self._guard(inter):
            return
        await inter.response.defer(ephemeral=True)
        if not os.path.isdir(EMOJI_DIR):
            return await inter.edit_original_response(
                content=f"❌ Нет папки `{EMOJI_DIR}`. Создай `emojis` рядом с main.py и положи туда файлы.")
        files = sorted(f for f in os.listdir(EMOJI_DIR) if os.path.splitext(f)[1].lower() in MIME)
        if not files:
            return await inter.edit_original_response(content="❌ В папке emojis нет картинок.")
        existing = {e.name for e in inter.guild.emojis}
        ok = skipped = 0
        errors = []
        for fn in files:
            name = make_name(fn)
            if name in existing:
                skipped += 1
                continue
            with open(os.path.join(EMOJI_DIR, fn), "rb") as f:
                raw = f.read()
            data = fit_image(raw, os.path.splitext(fn)[1].lower())
            if data is None:
                errors.append(f"{fn}: больше 256 КБ")
                continue
            err = await self._upload(inter.guild, name, data)
            if err:
                errors.append(f"{fn}: {err}")
                if "слотов" in err:
                    break
            else:
                ok += 1
                existing.add(name)
            await asyncio.sleep(1.2)
        sync_map(inter.guild)
        msg = f"✅ Загружено: **{ok}**, уже было: **{skipped}**, ошибок: **{len(errors)}**"
        if errors:
            msg += "\n" + "\n".join(errors[:10])
        msg += "\nДальше: `/ai_textraspredeli`"
        await inter.edit_original_response(content=msg[:1900])

    @commands.slash_command(name="emoji_sync", description="Обновить список эмодзи сервера")
    async def emoji_sync(self, inter: disnake.ApplicationCommandInteraction):
        if not await self._guard(inter):
            return
        emap = sync_map(inter.guild)
        await inter.response.send_message(f"🔄 Эмодзи в списке: **{len(emap)}**", ephemeral=True)

    @commands.slash_command(name="emoji_list", description="Показать эмодзи и их имена")
    async def emoji_list(self, inter: disnake.ApplicationCommandInteraction):
        if not await self._guard(inter):
            return
        emap = sync_map(inter.guild)
        lines, total = [], 0
        for n, c in emap.items():
            line = f"{c} `{n}`"
            if total + len(line) > 1800:
                lines.append(f"…и ещё {len(emap) - len(lines)}")
                break
            lines.append(line)
            total += len(line) + 1
        await inter.response.send_message("\n".join(lines) or "Пусто.", ephemeral=True)

    # ------------------------------------------------------- привязки
    @commands.slash_command(name="emoji_bind", description="Привязать обычное эмодзи к твоему премиум-эмодзи")
    async def emoji_bind(self, inter: disnake.ApplicationCommandInteraction,
                         unicode: str, name: str):
        if not await self._guard(inter):
            return
        m = EMOJI_RE.search(unicode)
        emap = load_json(MAP_FILE, {})
        if not m:
            return await inter.response.send_message("❌ Вставь обычное эмодзи, например 🎉", ephemeral=True)
        if name not in emap:
            return await inter.response.send_message("❌ Нет такого эмодзи. Смотри /emoji_list", ephemeral=True)
        binds = load_json(BIND_FILE, {})
        binds[m.group(0).replace("\ufe0f", "")] = name
        save_json(BIND_FILE, binds)
        await inter.response.send_message(f"✅ {m.group(0)} → {emap[name]}", ephemeral=True)

    @emoji_bind.autocomplete("name")
    async def _bind_ac(self, inter, string: str):
        return [n for n in load_json(MAP_FILE, {}) if string.lower() in n.lower()][:25]

    # ------------------------------------------------ авто-расстановка
    @commands.slash_command(name="ai_textraspredeli",
                            description="Автоматически расставить премиум-эмодзи по текстам заявки и бота")
    async def ai_textraspredeli(
        self, inter: disnake.ApplicationCommandInteraction,
        scope: str = commands.Param(
            default="all", description="Где менять",
            choices={"Все тексты бота": "all", "Только заявка в семью": "recruit"}),
        apply: bool = commands.Param(default=False, description="True — сохранить; False — только показать"),
        ai: bool = commands.Param(default=True, description="Расставить через ИИ (смотрит на картинки эмодзи)"),
        fill_rest: bool = commands.Param(
            default=False, description="Без ИИ: для эмодзи без пары назначить свободные из твоих"),
    ):
        if not await self._guard(inter):
            return
        await inter.response.defer(ephemeral=True)
        emap = sync_map(inter.guild)
        if not emap:
            return await inter.edit_original_response(
                content="❌ На сервере нет своих эмодзи. Сначала `/emoji_folder` или `/emoji_add`.")

        cfg = load_json(CONFIG_FILE, {})
        hall = load_json(HALL_FILE, {})
        binds = load_json(BIND_FILE, {})
        rep = Replacer(emap, binds, fill_rest)

        targets = _targets(scope, cfg, hall)
        ai_out, ai_err, ai_used = {}, None, 0
        if ai:
            await inter.edit_original_response(content="🤖 ИИ смотрит на картинки эмодзи и расставляет их по текстам…")
            items = {f"{f}|{k}": st[k] for f, st, k in targets}
            images = await emoji_images(inter.guild)
            ai_out, ai_err = await ai_distribute(emap, items, images, binds)
            ai_out = ai_out or {}

        changed = []
        for fname, store, key in targets:
            old = store[key]
            new = ai_out.get(f"{fname}|{key}")
            if _valid_ai_text(old, new, emap):
                ai_used += 1
            else:
                new = rep.text(old)  # запасной вариант: подбор по именам
            if new != old:
                store[key] = new
                changed.append((fname, key, new))
        if ai:
            rep.count = sum(len(CUSTOM_RE.findall(c[2])) for c in changed)

        if not changed:
            msg = "Нечего менять: в текстах не нашлось обычных эмодзи, для которых есть пара."
        else:
            msg = f"{'✅ Сохранено' if apply else '👀 Предпросмотр'}: заменено эмодзи **{rep.count}** в **{len(changed)}** текстах.\n"
            for fname, key, new in changed[:3]:
                msg += f"\n**{key}**\n{new[:300]}\n"
        if ai and ai_err:
            msg += f"\n⚠️ ИИ не сработал ({ai_err[:200]}), использовал подбор по именам."
        elif ai and ai_used < len(targets):
            msg += f"\nℹ️ ИИ оформил {ai_used} из {len(targets)} текстов, остальные подобраны по именам."
        if rep.missing:
            msg += ("\n⚠️ Нет пары для: " + " ".join(sorted(rep.missing)) +
                    "\nПривяжи через `/emoji_bind` или включи `fill_rest`.")

        if apply and changed:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            for path in (CONFIG_FILE, HALL_FILE):
                if os.path.exists(path):
                    shutil.copy(path, os.path.join(BACKUP_DIR, f"{ts}__{os.path.basename(path)}"))
            if any(c[0] == "config.json" for c in changed):
                save_json(CONFIG_FILE, cfg)
            if any(c[0] == "hall_settings.json" for c in changed):
                save_json(HALL_FILE, hall)
            save_json(BIND_FILE, binds)
            msg += "\n♻️ Перезапусти бота (start.bat), чтобы тексты подхватились. Старые панели перепубликуй."
        elif not apply and changed:
            msg += "\nЧтобы сохранить: `/ai_textraspredeli apply:True`"
        await inter.edit_original_response(content=msg[:1950])

    @commands.slash_command(name="emoji_restore", description="Откатить последнюю замену эмодзи в текстах")
    async def emoji_restore(self, inter: disnake.ApplicationCommandInteraction):
        if not await self._guard(inter):
            return
        if not os.path.isdir(BACKUP_DIR) or not os.listdir(BACKUP_DIR):
            return await inter.response.send_message("Бэкапов нет.", ephemeral=True)
        last = max(f.split("__")[0] for f in os.listdir(BACKUP_DIR))
        for f in os.listdir(BACKUP_DIR):
            if f.startswith(last + "__"):
                shutil.copy(os.path.join(BACKUP_DIR, f), os.path.join(BASE, f.split("__", 1)[1]))
        await inter.response.send_message(f"♻️ Восстановлено из бэкапа {last}. Перезапусти бота.", ephemeral=True)


def setup(bot):
    bot.add_cog(EmojiManager(bot))
