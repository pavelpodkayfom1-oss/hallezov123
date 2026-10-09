"""
Hallez FAMQ — «Рекрут-центр».

Что внутри:
  • красивая панель заявки с баннером (баннер прикрепляется файлом — работает всегда);
  • анкета, которую вы редактируете сами (вопросы, подсказки, порядок, вкл/выкл);
  • премиум-эмодзи: пишите :имя: в любом тексте — бот сам подставит эмодзи сервера;
  • баллы рекрутов за принятые заявки;
  • панель «Я пригласил человека» (ник/статик/доказательства) → баллы при принятии + сообщение в ЛС;
  • магазин за баллы (призы 100к / 150к, повышение, подарок в Telegram) — всё редактируется;
  • единая админ-панель /hr — все настройки без правки файлов:
    оформление (в т.ч. блок «Как проходит набор»), анкета, ВСЕ кнопки (текст/эмодзи/цвет),
    ВСЕ тексты, эмодзи, баллы, магазин, каналы, предпросмотр, открыть/закрыть набор, публикация.

Все настройки хранятся в recruit_plus.json (создаётся сам).
"""
import os
import re
import json
import sys
import time
import copy
import io
from typing import Optional, Dict, Any, List

import disnake
from disnake.ext import commands

import database as db

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_PATH = os.path.join(BASE_DIR, "recruit_plus.json")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
BANNER_PATH = os.path.join(ASSETS_DIR, "banner.png")

# ───────────────────────────── ЭМОДЗИ ─────────────────────────────

EMOJI_SLOTS = {
    "crown": ("👑", "Корона / заголовки"),
    "apply": ("📝", "Кнопка «Подать заявку»"),
    "review": ("🔎", "Кнопка «Взять на рассмотрение»"),
    "interview": ("🎙️", "Кнопка «Вызвать на обзвон»"),
    "accept": ("✅", "Кнопка «Принять»"),
    "reject": ("❌", "Кнопка «Отказать»"),
    "points": ("💎", "Баллы"),
    "referral": ("🤝", "Приглашение"),
    "shop": ("🛒", "Магазин"),
    "top": ("🏆", "Топ рекрутов"),
    "gift": ("🎁", "Призы"),
    "star": ("✨", "Украшения текста"),
}

_BOT: Optional[commands.Bot] = None
_EMOJI_RE = re.compile(r"(?<![<\w]):([A-Za-z0-9_]{2,32}):(?!\d+>)")
_CUSTOM_RE = re.compile(r"^<a?:\w{2,32}:\d+>$")


def E(text: Optional[str]) -> str:
    """Заменяет :имя: на эмодзи сервера (если такое есть). Готовые <:имя:id> не трогает."""
    if not text:
        return text or ""
    if _BOT is None:
        return text
    table = {e.name.lower(): str(e) for e in _BOT.emojis}
    if not table:
        return text
    return _EMOJI_RE.sub(lambda m: table.get(m.group(1).lower(), m.group(0)), text)


def pe(s: Optional[str]):
    """Строка → эмодзи для кнопок/селектов."""
    if not s:
        return None
    s = s.strip()
    if s.startswith("<"):
        try:
            return disnake.PartialEmoji.from_str(s)
        except Exception:
            return None
    return s


def em(slot: str) -> str:
    return S["emoji"].get(slot) or EMOJI_SLOTS[slot][0]


def split_leading_emoji(text: str):
    """'<:x:1> Название' → ('<:x:1>', 'Название'); '🎁 Приз' → ('🎁', 'Приз')."""
    text = E(text or "").strip()
    m = re.match(r"^(<a?:\w{2,32}:\d+>)\s*(.*)$", text, re.S)
    if m:
        return m.group(1), m.group(2).strip()
    if text and ord(text[0]) >= 0x2190 and not text[0].isalnum():
        first = text[0]
        rest = text[1:]
        if rest[:1] == "\ufe0f":
            first += "\ufe0f"
            rest = rest[1:]
        return first, rest.strip()
    return None, text


# ───────────────────────────── ЗАГРУЗКА ЭМОДЗИ ИЗ ПАПКИ ─────────────────────────────

EMOJI_DIR = os.path.join(BASE_DIR, "emojis")
EMOJI_PREFIX = "hz_"
# какое эмодзи из папки куда ставится автоматически (по имени файла без номера)
AUTO_SLOTS = {
    "crown": "membericon", "apply": "pencil", "review": "book", "interview": "discord",
    "accept": "likeicon", "reject": "warn", "points": "bitsicon", "referral": "followicon",
    "shop": "shoppingcart", "top": "winterstar", "gift": "giveaway", "star": "supersubscribericon",
}


def _emoji_name_for(filename: str, used: set) -> str:
    base = os.path.splitext(filename)[0]
    m = re.match(r"^(\d+)[-_ ]*(.*)$", base)
    num, rest = (m.group(1), m.group(2)) if m else ("", base)
    clean = re.sub(r"[^A-Za-z0-9_]", "", rest) or "emoji"
    name = (EMOJI_PREFIX + clean)[:32]
    if name.lower() in used:
        name = (EMOJI_PREFIX + clean)[:32 - len(num) - 1] + "_" + num
    used.add(name.lower())
    return name


def _prepare_emoji(path: str):
    """→ (bytes | None, animated, причина). Лимит Discord: 256 КБ."""
    with open(path, "rb") as f:
        data = f.read()
    animated = path.lower().endswith(".gif")
    if len(data) <= 256 * 1024:
        return data, animated, ""
    if animated:
        return None, True, "GIF больше 256 КБ"
    try:
        import io
        from PIL import Image
        img = Image.open(path).convert("RGBA")
        img.thumbnail((128, 128))
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        if buf.tell() <= 256 * 1024:
            return buf.getvalue(), False, ""
    except Exception:
        pass
    return None, False, "больше 256 КБ"


async def upload_emojis(guild: disnake.Guild, progress=None) -> str:
    import asyncio
    if not os.path.isdir(EMOJI_DIR):
        return f"❌ Папка не найдена: `{EMOJI_DIR}`. Положите файлы в папку `emojis` рядом с `main.py`."
    files = sorted(f for f in os.listdir(EMOJI_DIR) if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")))
    if not files:
        return "❌ В папке `emojis` нет картинок."
    used: set = set()
    names = {fn: _emoji_name_for(fn, used) for fn in files}   # стабильные имена
    wanted = set(AUTO_SLOTS.values())
    order = sorted(files, key=lambda fn: (0 if names[fn][len(EMOJI_PREFIX):].lower() in wanted else 1, fn))

    existing = {e.name.lower(): e for e in guild.emojis}
    free_static = guild.emoji_limit - sum(1 for e in guild.emojis if not e.animated)
    free_anim = guild.emoji_limit - sum(1 for e in guild.emojis if e.animated)
    created, skipped, failed = 0, 0, []
    for fn in order:
        name = names[fn]
        if name.lower() in existing:
            skipped += 1
            continue
        data, animated, why = _prepare_emoji(os.path.join(EMOJI_DIR, fn))
        if data is None:
            failed.append(f"{fn}: {why}")
            continue
        if (free_anim if animated else free_static) <= 0:
            failed.append(f"{fn}: нет свободных слотов ({'анимированных' if animated else 'обычных'})")
            continue
        try:
            e = await guild.create_custom_emoji(name=name, image=data, reason="Hallez: загрузка эмодзи из папки")
        except disnake.Forbidden:
            failed.append(f"{fn}: у бота нет права «Управлять эмодзи и стикерами»")
            break
        except disnake.HTTPException as ex:
            failed.append(f"{fn}: {str(ex)[:60]}")
            continue
        existing[name.lower()] = e
        created += 1
        if progress:
            await progress(created, len(files) - skipped)
        if animated:
            free_anim -= 1
        else:
            free_static -= 1
        await asyncio.sleep(0.4)   # короткая пауза; при лимите Discord disnake сам подождёт и повторит

    assigned = []
    for slot, key in AUTO_SLOTS.items():
        e = existing.get((EMOJI_PREFIX + key).lower())
        if e and not S["emoji"].get(slot):
            S["emoji"][slot] = str(e)
            assigned.append(f"{e} {slot}")
    save_settings()
    text = f"✅ Загружено: **{created}**, уже были: **{skipped}**, не загружено: **{len(failed)}**."
    if assigned:
        text += "\nАвто-подстановка в бота: " + " ".join(assigned)
        text += "\nОбновите панели: `/hr` → «Опубликовать»."
    if failed:
        text += "\n⚠️ " + "; ".join(failed[:6]) + (" …" if len(failed) > 6 else "")
    text += f"\nВ текстах пишите `:{EMOJI_PREFIX}имя:` — например `:{EMOJI_PREFIX}giveaway:`."
    return text


# ───────────────────────────── ЗАГРУЗКА СТИКЕРОВ ИЗ ПАПКИ ─────────────────────────────

STICKER_DIR = os.path.join(BASE_DIR, "stickers")
STICKER_EXTS = (".png", ".apng", ".gif", ".json", ".jpg", ".jpeg", ".webp")
STICKER_LIMIT_BYTES = 512 * 1024   # лимит Discord


def _sticker_name(filename: str, used: set) -> str:
    base = re.sub(r"^\d+[-_ ]*", "", os.path.splitext(filename)[0])
    clean = re.sub(r"[^\w\- ]", "", base).strip() or "sticker"
    name = clean[:30]
    if len(name) < 2:
        name = (name + "_st")[:30]
    n = 2
    while name.lower() in used:
        name = f"{clean[:26]}_{n}"
        n += 1
    used.add(name.lower())
    return name


def _prepare_sticker(path: str):
    """→ (bytes | None, имя файла для загрузки, причина). Требования Discord: 320×320, ≤512 КБ."""
    import io
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as f:
        raw = f.read()
    if ext in (".json", ".gif", ".apng"):
        if len(raw) <= STICKER_LIMIT_BYTES:
            return raw, os.path.basename(path), ""
        return None, "", "больше 512 КБ"
    try:
        from PIL import Image
        img = Image.open(path)
        if getattr(img, "is_animated", False):
            if ext == ".png" and len(raw) <= STICKER_LIMIT_BYTES:
                return raw, os.path.basename(path), ""
            return None, "", "анимация: нужен APNG/GIF до 512 КБ"
        img = img.convert("RGBA")
        if img.size != (320, 320):
            img.thumbnail((320, 320), Image.LANCZOS)
            canvas = Image.new("RGBA", (320, 320), (0, 0, 0, 0))
            canvas.paste(img, ((320 - img.width) // 2, (320 - img.height) // 2))
            img = canvas
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        if buf.tell() > STICKER_LIMIT_BYTES:
            buf = io.BytesIO()
            img.quantize(colors=128, method=Image.Quantize.FASTOCTREE).save(buf, "PNG", optimize=True)
        if buf.tell() > STICKER_LIMIT_BYTES:
            return None, "", "больше 512 КБ даже после сжатия"
        return buf.getvalue(), "sticker.png", ""
    except Exception as e:
        return None, "", f"не удалось обработать ({e.__class__.__name__})"


async def upload_stickers(guild: disnake.Guild, progress=None, tag: str = "⭐") -> str:
    import asyncio
    import io
    if not os.path.isdir(STICKER_DIR):
        return f"❌ Папка не найдена: `{STICKER_DIR}`. Создайте папку `stickers` рядом с `main.py` и положите туда файлы."
    files = sorted(f for f in os.listdir(STICKER_DIR) if f.lower().endswith(STICKER_EXTS))
    if not files:
        return "❌ В папке `stickers` нет подходящих файлов (png, apng, gif, json, jpg, webp)."
    used: set = set()
    names = {fn: _sticker_name(fn, used) for fn in files}
    existing = {s.name.lower() for s in guild.stickers}
    free = guild.sticker_limit - len(guild.stickers)
    loop = asyncio.get_running_loop()
    created, skipped, failed = 0, 0, []
    for fn in files:
        name = names[fn]
        if name.lower() in existing:
            skipped += 1
            continue
        if free <= 0:
            failed.append(f"{fn}: нет свободных слотов (лимит сервера {guild.sticker_limit})")
            continue
        data, upname, why = await loop.run_in_executor(None, _prepare_sticker, os.path.join(STICKER_DIR, fn))
        if data is None:
            failed.append(f"{fn}: {why}")
            continue
        try:
            await guild.create_sticker(name=name, description=name[:100], emoji=tag,
                                       file=disnake.File(io.BytesIO(data), filename=upname),
                                       reason="Hallez: загрузка стикеров из папки")
        except disnake.Forbidden:
            failed.append(f"{fn}: у бота нет права «Управлять эмодзи и стикерами»")
            break
        except disnake.HTTPException as ex:
            failed.append(f"{fn}: {str(ex)[:70]}")
            continue
        existing.add(name.lower())
        created += 1
        free -= 1
        if progress:
            await progress(created, len(files) - skipped)
        await asyncio.sleep(0.4)
    text = f"✅ Стикеров загружено: **{created}**, уже были: **{skipped}**, не загружено: **{len(failed)}**."
    text += f"\nСвободных слотов осталось: **{max(free, 0)}** из {guild.sticker_limit}."
    if failed:
        text += "\n⚠️ " + "; ".join(failed[:6]) + (" …" if len(failed) > 6 else "")
    return text


_UPLOADING = False
_TASKS: set = set()


async def start_upload(inter, worker=None, what: str = "эмодзи") -> None:
    """Загрузка эмодзи в фоне: панель не зависает, а итог придёт, даже если токен ответа Discord уже истёк."""
    import asyncio
    global _UPLOADING
    if _UPLOADING:
        return await inter.followup.send("⏳ Загрузка уже идёт — дождитесь итога.", ephemeral=True)
    _UPLOADING = True
    await inter.followup.send(f"📥 Загрузка ({what}) запущена в фоне. Итог пришлю сюда — панель можно закрыть.", ephemeral=True)

    async def runner():
        global _UPLOADING
        last = [0.0]

        async def progress(done, total):
            if time.time() - last[0] < 8:
                return
            last[0] = time.time()
            try:
                await inter.edit_original_response(content=f"📥 Загружено {done} из {total}…")
            except Exception:
                pass

        try:
            text = await (worker or upload_emojis)(inter.guild, progress)
        except Exception as e:
            text = f"❌ Ошибка загрузки: {e}"
        finally:
            _UPLOADING = False
        text = text[:1900]
        try:
            await inter.edit_original_response(content=text)
        except Exception:
            try:
                await inter.channel.send(f"{inter.author.mention} {text}")
            except Exception:
                try:
                    await inter.author.send(text)
                except Exception:
                    pass

    t = asyncio.create_task(runner())
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)


# ───────────────────────────── НАСТРОЙКИ ─────────────────────────────

DEFAULT_FORM = [
    {"key": "nick", "label": "Игровой никнейм (Имя Фамилия)", "placeholder": "Пример: Travis Hallez",
     "style": "short", "required": True, "enabled": True},
    {"key": "static", "label": "Ваш статик (Static ID)", "placeholder": "Только цифры, например 12345",
     "style": "short", "required": True, "enabled": True},
    {"key": "age", "label": "Реальный возраст", "placeholder": "Например: 18",
     "style": "short", "required": True, "enabled": True},
    {"key": "prev", "label": "В каких семьях состояли ранее?", "placeholder": "Названия семей и причина ухода (или «нет»)",
     "style": "long", "required": True, "enabled": True},
    {"key": "why", "label": "Почему Hallez и как узнали о нас?", "placeholder": "Ваши цели, планы в семье, откуда узнали...",
     "style": "long", "required": True, "enabled": True},
]

DEFAULT_SHOP = [
    {"key": "cash100", "name": "Приз 100 000$", "emoji": "💵", "desc": "Выплата 100 000$ в игре от руководства",
     "cost": 120, "stock": -1, "enabled": True},
    {"key": "cash150", "name": "Приз 150 000$", "emoji": "💰", "desc": "Выплата 150 000$ в игре от руководства",
     "cost": 180, "stock": -1, "enabled": True},
    {"key": "promo", "name": "Повышение в семье", "emoji": "⬆️", "desc": "Повышение на 1 ранг (по решению руководства)",
     "cost": 350, "stock": -1, "enabled": True},
    {"key": "nft", "name": "Подарок в Telegram (NFT)", "emoji": "🎁", "desc": "NFT-подарок в Telegram на ваш аккаунт",
     "cost": 700, "stock": -1, "enabled": True},
]

DEFAULTS: Dict[str, Any] = {
    "channels": {"panel": 0, "review": 0, "orders": 0, "points_panel": 0},
    "staff_roles": [],
    "published": {"apply": [0, 0], "points": [0, 0]},
    "design": {
        "title": "⚜️ НАБОР В HALLEZ FAMQ",
        "description": (
            "Мы открываем двери для амбициозных, адекватных и стрессоустойчивых игроков семьи **Hallez FAMQ** — "
            "**Loyalty • Power • Respect**.\n\n"
            ":star: **Что мы ждём от тебя**\n"
            "> • Адекватность, дисциплина, уважение к субординации\n"
            "> • Микрофон и желание бывать на семейных МП\n"
            "> • Смена фамилии на **Hallez** после испытательного срока\n\n"
            ":gift: **Что даём взамен**\n"
            "> • Дружный коллектив и поддержку старших\n"
            "> • Рост по рангам, награды и призы за активность\n\n"
            "👇 Нажми кнопку ниже и заполни анкету — это займёт пару минут."
        ),
        "color": "0x990000",
        "footer": "Majestic RP • Hallez FAMQ",
        "steps": None,   # None = стандартный блок «Как проходит набор»; «-» = скрыть
    },
    "form": DEFAULT_FORM,
    # Экономика баллов (средний уровень): рекрут, обрабатывающий ~3-5 заявок в неделю,
    # набирает 100к примерно за 3-4 недели, повышение — за ~2 месяца, NFT — за ~4 месяца.
    "points": {"accept": 10, "referral": 15},
    "shop": DEFAULT_SHOP,
    "emoji": {},
    "buttons": {},
    "texts": {},
}


def _load_settings() -> Dict[str, Any]:
    data = copy.deepcopy(DEFAULTS)
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for k, v in saved.items():
            if isinstance(v, dict) and isinstance(data.get(k), dict):
                data[k].update(v)
            else:
                data[k] = v
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[hall_recruit] не удалось прочитать recruit_plus.json: {e}", file=sys.stderr, flush=True)
    return data


def save_settings():
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(S, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[hall_recruit] не удалось сохранить настройки: {e}", file=sys.stderr, flush=True)


S: Dict[str, Any] = _load_settings()


def get_cfg() -> Dict[str, Any]:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def fmt(text: Optional[str], **kw) -> str:
    text = (text or "").replace("\\n", "\n")
    try:
        return E(text.format_map(_Safe(**kw)))
    except Exception:
        return E(text)


def T(key: str, default: str = "", **kw) -> str:
    return fmt(get_cfg().get("texts", {}).get(key, default), **kw)


def _parse_color(v, default=0x990000) -> int:
    try:
        return int(str(v).replace("#", "").replace("0x", "").replace("0X", ""), 16)
    except Exception:
        return default


def main_color() -> int:
    return _parse_color(S["design"].get("color"), _parse_color(get_cfg().get("embed_color"), 0x990000))


def cfg_color(name: str, default: int) -> int:
    return _parse_color(get_cfg().get(name), default)


# ───────────────────────────── ЗАПИСЬ config.json (безопасно) ─────────────────────────────

def cfg_update(mutator) -> None:
    """Читает config.json, применяет изменение и записывает атомарно. Бросает ошибку, если файл нечитаем."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    mutator(cfg)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


# ───────────────────────────── КНОПКИ: всё редактируется в /hr → Кнопки ─────────────────────────────

BTN_STYLES = {
    "blue": disnake.ButtonStyle.primary, "grey": disnake.ButtonStyle.secondary,
    "green": disnake.ButtonStyle.success, "red": disnake.ButtonStyle.danger,
}
STYLE_ALIASES = {
    "синяя": "blue", "синий": "blue", "голубая": "blue", "серая": "grey", "серый": "grey",
    "зелёная": "green", "зеленая": "green", "зелёный": "green", "зеленый": "green",
    "красная": "red", "красный": "red",
}
STYLE_NAME = {"blue": "синяя", "grey": "серая", "green": "зелёная", "red": "красная"}

# ключ: (название, текст по умолчанию, слот эмодзи, цвет, ключ подписи в config.json → texts)
BUTTONS = {
    "apply": ("Панель: Подать заявку", "Подать заявку", "apply", "red", "btn_recruit_apply"),
    "review": ("Карточка: Взять на рассмотрение", "Взять на рассмотрение", "review", "blue", "btn_recruit_review"),
    "interview": ("Карточка: Вызвать на обзвон", "Вызвать на обзвон", "interview", "grey", "btn_recruit_interview"),
    "accept": ("Карточка: Принять в семью", "Принять в семью", "accept", "green", "btn_recruit_accept"),
    "reject": ("Карточка: Отказать", "Отказать", "reject", "red", "btn_recruit_reject"),
    "me": ("Рекрут-центр: Мои баллы", "Мои баллы", "points", "blue", None),
    "ref": ("Рекрут-центр: Я пригласил", "Я пригласил человека", "referral", "green", None),
    "shop": ("Рекрут-центр: Магазин", "Магазин", "shop", "grey", None),
    "top": ("Рекрут-центр: Топ рекрутов", "Топ рекрутов", "top", "grey", None),
    "ref_ok": ("Приглашение: Подтвердить", "Подтвердить", "accept", "green", None),
    "ref_no": ("Приглашение: Отклонить", "Отклонить", "reject", "red", None),
    "ord_ok": ("Заказ: Выдано", "Выдано", "accept", "green", None),
    "ord_no": ("Заказ: Отказать и вернуть", "Отказать и вернуть баллы", "reject", "red", None),
}


def parse_style(raw: Optional[str], default: str) -> str:
    s = (raw or "").strip().lower()
    s = STYLE_ALIASES.get(s, s)
    return s if s in BTN_STYLES else default


def _base_label(key: str) -> str:
    name, default_label, slot, style, cfg_key = BUTTONS[key]
    return (get_cfg().get("texts", {}).get(cfg_key) if cfg_key else None) or default_label


def btn(key: str) -> Dict[str, Any]:
    """Параметры кнопки (label/emoji/style) с учётом правок из /hr → Кнопки."""
    name, default_label, slot, style, cfg_key = BUTTONS[key]
    o = S["buttons"].get(key) or {}
    label = (o.get("label") or _base_label(key))[:80]
    raw = (o.get("emoji") or "").strip()
    if raw == "-":
        emoji = None
    else:
        emoji = pe(E(raw)) if raw else pe(em(slot))
    return {"label": label, "emoji": emoji, "style": BTN_STYLES[parse_style(o.get("style"), style)]}


# ───────────────────────────── ТЕКСТЫ: всё редактируется в /hr → Тексты ─────────────────────────────

# ключ: (название, текст по умолчанию, подсказка по переменным)
XT = {
    "closed": ("Ответ: набор закрыт", "🔒 Набор в семью сейчас закрыт. Следите за новостями!", ""),
    "already_member": ("Ответ: уже в семье", "ℹ️ Вы уже состоите в семье.", ""),
    "apply_ok_title": ("Заявка отправлена: заголовок", "{e_accept} Заявка отправлена!", "{app_id}"),
    "apply_ok_desc": ("Заявка отправлена: текст",
                      "Ваша анкета **#{app_id}** передана рекрутерам.\nМы напишем вам в ЛС, когда будет решение {e_star}", "{app_id}"),
    "thread_hello": ("Ветка заявки: приветствие", "{mention}, здесь рекрутеры свяжутся с вами. Ожидайте решения {e_star}", "{mention}"),
    "interview_dm_title": ("ЛС «обзвон»: заголовок", "{e_interview} Вас вызывают на обзвон", ""),
    "interview_dm_desc": ("ЛС «обзвон»: текст",
                          "Рекрутер {reviewer} приглашает вас на обзвон в **Hallez FAMQ**!\n📍 Голосовой канал: {voice}\n⏳ Зайдите и ожидайте рекрутера.",
                          "{reviewer} {voice}"),
    "accept_dm_title": ("ЛС «принят»: заголовок", "{e_crown} Добро пожаловать в Hallez FAMQ!", ""),
    "points_title": ("Рекрут-центр: заголовок", "{e_points} РЕКРУТ-ЦЕНТР HALLEZ FAMQ", ""),
    "points_desc": ("Рекрут-центр: текст",
                    "{e_star} Получайте **баллы** за работу в наборе и обменивайте их на призы!\n\n"
                    "**{e_points} Как заработать**\n"
                    "> {e_accept} Приняли заявку кандидата — **+{accept}** б.\n"
                    "> {e_referral} Привели человека, и его приняли — **+{referral}** б.\n\n"
                    "**{e_shop} Магазин**\n{shop_lines}\n\n"
                    "👇 Кнопки ниже: баланс, подача приглашённого, магазин и топ.",
                    "{accept} {referral} {shop_lines}"),
    "ref_sent": ("Приглашение отправлено",
                 "{e_accept} Приглашение **#{ref_id}** отправлено на проверку.\n"
                 "Когда руководство подтвердит доказательства и **{inv_nick}** примут в семью — "
                 "вы получите **{pts}** баллов и сообщение в ЛС.", "{ref_id} {inv_nick} {pts}"),
    "ref_paid_title": ("ЛС «приглашённый принят»: заголовок", "{e_referral} Ваш приглашённый принят!", ""),
    "ref_paid_desc": ("ЛС «приглашённый принят»: текст",
                      "{e_star} Игрок **{nick}** (`{static}`) принят в **Hallez FAMQ** — спасибо, что привели хороших людей!\n\n"
                      "{e_points} Начислено: **+{pts}** баллов\n{e_shop} Баланс: **{bal}** б.\n"
                      "Обменять баллы на призы можно в Рекрут-центре.", "{nick} {static} {pts} {bal}"),
    "accept_points_title": ("ЛС рекруту за принятого: заголовок", "{e_points} Баллы за принятого кандидата", ""),
    "accept_points_desc": ("ЛС рекруту за принятого: текст",
                           "{e_star} Вы приняли в семью **{nick}** (`{static}`) — отличная работа!\n\n"
                           "{e_points} Начислено: **+{pts}** баллов\n{e_shop} Баланс: **{bal}** б.\n"
                           "Потратить баллы можно в Рекрут-центре, в разделе «Магазин».", "{nick} {static} {pts} {bal}"),
    "order_done_title": ("ЛС «приз выдан»: заголовок", "{e_gift} Ваш приз выдан!", ""),
    "order_done_desc": ("ЛС «приз выдан»: текст",
                        "Заказ **#{order_id}** — **{item}** — выдан руководителем {admin}.\n"
                        "{e_star} Спасибо за вклад в семью, продолжайте в том же духе!", "{order_id} {item} {admin}"),
}


def X(key: str, **kw) -> str:
    """Текст рекрут-центра: правка из /hr → Тексты или стандартный. {e_crown}, {e_star}… — эмодзи слотов."""
    base = {f"e_{k}": em(k) for k in EMOJI_SLOTS}
    base.update(kw)
    return fmt(S["texts"].get(key) or XT[key][1], **base)


def default_steps() -> str:
    return (f"{em('crown')} Как проходит набор\n"
            "**1.** Нажмите кнопку и заполните анкету\n"
            "**2.** Рекрутер возьмёт заявку и вызовет на обзвон\n"
            "**3.** После обзвона — решение, роли и приветствие в ЛС")


def steps_block():
    """(название поля, текст) для блока «Как проходит набор» или None, если скрыт."""
    raw = S["design"].get("steps")
    if raw is None:
        raw = default_steps()
    raw = (raw or "").strip()
    if not raw or raw == "-":
        return None
    head, _, body = raw.partition("\n")
    body = body.strip()
    if not body:
        return "\u200b", E(head)[:1024]
    return E(head)[:256], E(body)[:1024]


# ───────────────────────────── ПРАВА / КАНАЛЫ ─────────────────────────────

def is_admin(member) -> bool:
    if not isinstance(member, disnake.Member):
        return False
    p = member.guild_permissions
    if p.administrator or p.manage_guild:
        return True
    cfg = get_cfg()
    if member.id in set(cfg.get("admin_user_ids", [])):
        return True
    ids = set(cfg.get("admin_role_ids", [])) - {0}
    return any(r.id in ids for r in member.roles)


def is_staff(member) -> bool:
    if is_admin(member):
        return True
    if not isinstance(member, disnake.Member):
        return False
    cfg = get_cfg()
    ids = (set(cfg.get("recruiter_role_ids", [])) | set(S["staff_roles"])) - {0}
    return any(r.id in ids for r in member.roles)


def chan_id(kind: str) -> int:
    v = S["channels"].get(kind, 0)
    if v:
        return v
    base = get_cfg().get("channels", {}).get("recruitment_channel_id", 0)
    if kind == "orders":
        return S["channels"].get("review") or base
    if kind == "points_panel":
        return S["channels"].get("panel") or base
    return base


async def get_channel(cid: int):
    if not cid or _BOT is None:
        return None
    ch = _BOT.get_channel(cid)
    if ch is not None:
        return ch
    try:
        return await _BOT.fetch_channel(cid)
    except Exception:
        return None


async def get_thread(guild: disnake.Guild, tid: int):
    t = guild.get_thread(tid)
    if t is not None:
        return t
    try:
        return await guild.fetch_channel(tid)
    except Exception:
        return None


# ───────────────────────────── БАННЕР / ЭМБЕДЫ ─────────────────────────────

def banner_file() -> Optional[disnake.File]:
    if os.path.exists(BANNER_PATH):
        return disnake.File(BANNER_PATH, filename="banner.png")
    return None


def put_banner(embed: disnake.Embed) -> Optional[disnake.File]:
    f = banner_file()
    if f:
        embed.set_image(url="attachment://banner.png")
    return f


VIDEO_EXTS = (".gif", ".mp4", ".mov", ".webm")


def video_path() -> Optional[str]:
    name = S["design"].get("video_file")
    if name:
        p = os.path.join(ASSETS_DIR, name)
        if os.path.exists(p):
            return p
    # положили файл руками: assets/video.mp4 (или .mov / .webm) — подхватится сам
    for ext in VIDEO_EXTS:
        p = os.path.join(ASSETS_DIR, "video" + ext)
        if os.path.exists(p):
            return p
    return None


def video_file() -> Optional[disnake.File]:
    p = video_path()
    return disnake.File(p, filename=os.path.basename(p)) if p else None


def _ffmpeg_exe() -> Optional[str]:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    import shutil
    return shutil.which("ffmpeg")


def _convert_sync(src: str, dst: str, limit: int):
    """Видео → зацикленный GIF (автоплей в эмбеде). Если не влезает в лимит — снижает качество."""
    import subprocess
    exe = _ffmpeg_exe()
    if not exe:
        raise RuntimeError("нет ffmpeg: добавьте `imageio-ffmpeg` в requirements.txt и перезапустите start.bat")
    for width, fps in ((640, 15), (480, 12), (360, 10), (280, 8)):
        vf = (f"fps={fps},scale={width}:-1:flags=lanczos,split[s0][s1];"
              f"[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer:bayer_scale=4")
        r = subprocess.run([exe, "-y", "-i", src, "-vf", vf, "-loop", "0", dst], capture_output=True, timeout=180)
        if r.returncode != 0 or not os.path.exists(dst):
            raise RuntimeError("ffmpeg не смог преобразовать видео в GIF")
        if os.path.getsize(dst) <= limit * 0.95:
            return
    raise RuntimeError("GIF не влезает в лимит сервера даже в минимальном качестве — сократите видео")


async def media_gif(limit: int) -> Optional[str]:
    """Путь к GIF для панели: готовый video.gif либо автоконвертация из mp4/mov/webm."""
    import asyncio
    vp = video_path()
    if not vp:
        return None
    if vp.lower().endswith(".gif"):
        if os.path.getsize(vp) > limit:
            raise RuntimeError(f"GIF ({os.path.getsize(vp) // 1048576} МБ) больше лимита сервера ({limit // 1048576} МБ)")
        return vp
    dst = os.path.join(ASSETS_DIR, "video_auto.gif")
    if os.path.exists(dst) and os.path.getmtime(dst) >= os.path.getmtime(vp) and os.path.getsize(dst) <= limit:
        return dst
    await asyncio.get_running_loop().run_in_executor(None, _convert_sync, vp, dst, limit)
    return dst


def _clear_video_files():
    try:
        os.remove(os.path.join(ASSETS_DIR, "video_auto.gif"))
    except Exception:
        pass
    for ext in VIDEO_EXTS:
        try:
            os.remove(os.path.join(ASSETS_DIR, "video" + ext))
        except FileNotFoundError:
            pass
        except Exception:
            pass


def footer_text() -> str:
    return E(S["design"].get("footer") or "Hallez FAMQ")


def apply_embed() -> disnake.Embed:
    d = S["design"]
    emb = disnake.Embed(title=E(d["title"]), description=E(d["description"]), color=main_color())
    _steps = steps_block()
    if _steps:
        emb.add_field(name=_steps[0], value=_steps[1], inline=False)
    if S["design"].get("video_url"):
        emb.add_field(name="🎬 Видео", value=f"[Смотреть видео]({S['design']['video_url']})", inline=False)
    emb.set_footer(text=footer_text())
    return emb


def points_embed() -> disnake.Embed:
    p = S["points"]
    items = [i for i in S["shop"] if i.get("enabled")]
    shop_lines = "\n".join(f"{(i.get('emoji') or em('gift'))} **{E(i['name'])}** — `{i['cost']}` б." for i in items) or "Пока пусто"
    desc = X("points_desc", accept=p['accept'], referral=p['referral'], shop_lines=shop_lines)
    emb = disnake.Embed(title=X("points_title"), description=desc, color=main_color())
    emb.set_footer(text=footer_text())
    return emb


def set_field(emb: disnake.Embed, name: str, value: str, inline: bool = True):
    for i, f in enumerate(emb.fields):
        if f.name == name:
            emb.set_field_at(i, name=name, value=value, inline=inline)
            return
    emb.add_field(name=name, value=value, inline=inline)


async def dm(user_id: int, title: str, text: str, color: Optional[int] = None, banner: bool = True) -> bool:
    if _BOT is None:
        return False
    try:
        user = _BOT.get_user(user_id) or await _BOT.fetch_user(user_id)
        emb = disnake.Embed(title=E(title), description=E(text), color=color or main_color())
        emb.set_footer(text=footer_text())
        f = put_banner(emb) if banner else None
        if f:
            await user.send(embed=emb, file=f)
        else:
            await user.send(embed=emb)
        return True
    except Exception:
        return False


# ───────────────────────────── БАЗОВЫЕ UI-КЛАССЫ ─────────────────────────────

class HrButton(disnake.ui.Button):
    def __init__(self, handler, **kw):
        super().__init__(**kw)
        self._handler = handler

    async def callback(self, inter: disnake.MessageInteraction):
        await self._handler(inter)


class HrSelect(disnake.ui.StringSelect):
    def __init__(self, handler, **kw):
        super().__init__(**kw)
        self._handler = handler

    async def callback(self, inter: disnake.MessageInteraction):
        await self._handler(inter, list(self.values))


class HrChannelSelect(disnake.ui.ChannelSelect):
    def __init__(self, handler, **kw):
        super().__init__(**kw)
        self._handler = handler

    async def callback(self, inter: disnake.MessageInteraction):
        await self._handler(inter, [int(getattr(v, "id", v)) for v in self.values])


class HrRoleSelect(disnake.ui.RoleSelect):
    def __init__(self, handler, **kw):
        super().__init__(**kw)
        self._handler = handler

    async def callback(self, inter: disnake.MessageInteraction):
        await self._handler(inter, [int(getattr(v, "id", v)) for v in self.values])


def _yes(v: str) -> bool:
    return (v or "").strip().lower().startswith(("д", "y", "1", "t", "+", "в"))


# ═════════════════════════ 1. ЗАЯВКА: КНОПКА → МОДАЛКА → КАРТОЧКА ═════════════════════════

def enabled_fields() -> List[Dict[str, Any]]:
    return [f for f in S["form"] if f.get("enabled")][:5]


class ApplyView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(HrButton(h_apply, custom_id="hr:apply", **btn("apply")))


async def h_apply(inter: disnake.MessageInteraction):
    cfg = get_cfg()
    if not cfg.get("recruitment_open", True):
        return await inter.response.send_message(X("closed"), ephemeral=True)
    await inter.response.send_modal(ApplyModal())


class ApplyModal(disnake.ui.Modal):
    def __init__(self):
        comps = []
        for f in enabled_fields():
            long = f.get("style") == "long"
            comps.append(disnake.ui.TextInput(
                label=f["label"][:45],
                custom_id=f["key"],
                placeholder=(f.get("placeholder") or "")[:100] or None,
                style=disnake.TextInputStyle.paragraph if long else disnake.TextInputStyle.short,
                required=bool(f.get("required", True)),
                max_length=1000 if long else 100,
            ))
        title = (get_cfg().get("texts", {}).get("modal_recruit_title") or "Анкета на вступление")[:45]
        super().__init__(title=title, custom_id="hr:apply_modal", components=comps)

    async def callback(self, inter: disnake.ModalInteraction):
        await submit_application(inter)


def card_embed(app_nick: str, user: disnake.abc.User, answers: List[Dict[str, str]], status: str, color: int) -> disnake.Embed:
    emb = disnake.Embed(
        title=f"{em('crown')} Анкета кандидата: {app_nick}"[:256],
        description=f"Кандидат {user.mention}\nСтатус: {status}",
        color=color,
    )
    try:
        emb.set_thumbnail(url=user.display_avatar.url)
    except Exception:
        pass
    for a in answers:
        long = len(a["value"]) > 40
        emb.add_field(name=a["label"][:256], value=(a["value"] or "—")[:1024], inline=not long)
    emb.add_field(name="Рекрутер", value="—", inline=True)
    emb.set_footer(text=footer_text())
    return emb


async def submit_application(inter: disnake.ModalInteraction):
    vals = inter.text_values
    fields = enabled_fields()
    answers = [{"key": f["key"], "label": f["label"], "value": (vals.get(f["key"]) or "").strip()} for f in fields]
    d = {a["key"]: a["value"] for a in answers}

    nick = d.get("nick") or inter.author.display_name
    static = d.get("static", "")
    if not re.fullmatch(r"\d{1,9}", static):
        return await inter.response.send_message("❌ Статик должен состоять только из цифр (например, `12345`).", ephemeral=True)
    if await db.find_member_by_static(static):
        return await inter.response.send_message("ℹ️ Игрок с таким статиком уже состоит в семье.", ephemeral=True)

    age = 0
    if d.get("age"):
        if not d["age"].isdigit() or not (10 <= int(d["age"]) <= 80):
            return await inter.response.send_message("❌ Возраст укажите числом, например `18`.", ephemeral=True)
        age = int(d["age"])

    ch = await get_channel(chan_id("review"))
    if not isinstance(ch, disnake.TextChannel):
        return await inter.response.send_message(
            "❌ Канал для заявок не настроен. Администратору: `/hr` → «Каналы и роли».", ephemeral=True)

    await inter.response.defer(ephemeral=True)
    emb = card_embed(nick, inter.author, answers, f"🟡 **Новая заявка**", cfg_color("warning_color", 0xF39C12))
    roles = (set(get_cfg().get("recruiter_role_ids", [])) | set(S["staff_roles"])) - {0}
    ping = " ".join(f"<@&{r}>" for r in roles)

    # Только приватная ветка: в самом канале не появляется НИ ОДНОГО сообщения
    thread = None
    err = None
    for duration in (10080, 1440):
        try:
            thread = await ch.create_thread(
                name=f"Заявка • {nick}"[:100],
                type=disnake.ChannelType.private_thread,
                invitable=False,
                auto_archive_duration=duration,
            )
            break
        except disnake.Forbidden as e:
            err = e
            break
        except Exception as e:
            err = e
    if thread is None:
        print(f"[hall_recruit] не удалось создать приватную ветку: {err!r}", file=sys.stderr, flush=True)
        return await inter.followup.send(
            f"❌ Не удалось создать приватную ветку: `{str(err)[:150]}`\n"
            f"Администратору: дайте боту в {ch.mention} права «Создавать приватные ветки», "
            f"«Отправлять сообщения в ветках» и «Управлять ветками».", ephemeral=True)

    try:
        await thread.add_user(inter.author)
    except Exception:
        pass

    try:
        msg = await thread.send(content=f"{inter.author.mention} {ping}".strip(), embed=emb, view=CardView(),
                                allowed_mentions=disnake.AllowedMentions(roles=True, users=True))
    except Exception as e:
        print(f"[hall_recruit] не удалось отправить анкету в ветку: {e!r}", file=sys.stderr, flush=True)
        try:
            await thread.delete()
        except Exception:
            pass
        return await inter.followup.send("❌ Не удалось отправить анкету в ветку. Проверьте права бота.", ephemeral=True)

    # thread_id = id приватной ветки (кнопки находят заявку по ветке)
    app_id = await db.create_application(
        inter.author.id, nick, static, age,
        d.get("prev") or "—", d.get("why") or "—", thread.id,
    )
    await db.save_answers(app_id, json.dumps(answers, ensure_ascii=False))
    emb.set_footer(text=f"Заявка #{app_id} • {footer_text()}")
    try:
        await msg.edit(embed=emb)
    except Exception:
        pass
    try:
        await thread.send(X("thread_hello", mention=inter.author.mention))
    except Exception:
        pass

    ok = disnake.Embed(
        title=X("apply_ok_title", app_id=app_id),
        description=X("apply_ok_desc", app_id=app_id),
        color=cfg_color("success_color", 0x2ECC71),
    )
    await inter.followup.send(embed=ok, ephemeral=True)


# ───────────── Карточка заявки: кнопки рекрутера ─────────────

class CardView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(HrButton(h_review, custom_id="hr:rev", row=0, **btn("review")))
        self.add_item(HrButton(h_interview, custom_id="hr:int", row=0, **btn("interview")))
        self.add_item(HrButton(h_accept, custom_id="hr:acc", row=1, **btn("accept")))
        self.add_item(HrButton(h_reject, custom_id="hr:rej", row=1, **btn("reject")))


async def _load_card(inter: disnake.MessageInteraction):
    if not is_staff(inter.author):
        await inter.response.send_message("❌ Эти кнопки доступны только рекрутерам.", ephemeral=True)
        return None
    app = await db.get_application_by_thread(inter.message.id)   # старые заявки: id сообщения в канале
    if not app and isinstance(inter.channel, disnake.Thread):
        app = await db.get_application_by_thread(inter.channel.id)   # новые заявки: id приватной ветки
    if not app:
        await inter.response.send_message("❌ Заявка не найдена в базе.", ephemeral=True)
        return None
    if app["status"] in ("accepted", "rejected"):
        await inter.response.send_message("ℹ️ Эта заявка уже обработана.", ephemeral=True)
        return None
    return app


def _card_update(message: disnake.Message, user_id: int, status: str, color: int, reviewer: Optional[str]) -> disnake.Embed:
    emb = disnake.Embed.from_dict(message.embeds[0].to_dict())
    emb.description = f"Кандидат <@{user_id}>\nСтатус: {status}"
    emb.color = color
    if reviewer:
        set_field(emb, "Рекрутер", reviewer, True)
    return emb


def voice_channels_text() -> str:
    """Каналы обзвона из config.json: элементы могут быть числами или словарями {"name":..,"id":..}."""
    ids = []
    for item in get_cfg().get("voice_channels", []) or []:
        try:
            cid = int(item.get("id", 0)) if isinstance(item, dict) else int(item)
        except (TypeError, ValueError):
            continue
        if cid and cid not in ids:
            ids.append(cid)
    return ", ".join(f"<#{i}>" for i in ids) or "голосовой канал рекрутинга"


def build_nick(fmt: str, nick: str, static: str) -> str:
    """Ник по формату в лимит Discord (32): обрезается имя, а не статик."""
    nick = (nick or "").strip()
    static = (static or "").strip()
    kw = dict(rank="1", nick=nick, static=static)
    full = fmt.format(**kw)
    if len(full) <= 32:
        return full
    kw["nick"] = nick[:max(1, len(nick) - (len(full) - 32))].rstrip()
    return fmt.format(**kw)[:32]


async def close_thread(th, app_id, delete: bool = False, reason: str = "Заявка обработана"):
    """Закрывает ветку заявки: блокирует и архивирует (или удаляет). Возвращает текст ошибки или None."""
    if th is None:
        return None
    try:
        if delete:
            await th.delete(reason=reason)
        else:
            await th.edit(locked=True, archived=True, reason=reason)
        return None
    except Exception as e:
        print(f"Не удалось закрыть ветку заявки #{app_id}: {e!r}")
        return f"ветку не удалось закрыть ({e.__class__.__name__}) — выдайте боту право «Управление ветками»"


def _thread_dump(messages: List[disnake.Message]) -> str:
    out = []
    for m in messages:
        lines = []
        if m.clean_content:
            lines.append("    " + m.clean_content.replace("\n", "\n    "))
        for e in m.embeds:
            if e.title:
                lines.append(f"    [{e.title}]")
            if e.description:
                lines.append("    " + e.description.replace("\n", "\n    "))
            for f in e.fields:
                lines.append(f"    {f.name}: {f.value}")
        for a in m.attachments:
            lines.append(f"    📎 {a.filename}: {a.url}")
        if not lines:
            continue
        ts = m.created_at.strftime("%d.%m.%Y %H:%M:%S")
        out.append(f"[{ts} UTC] {m.author.display_name}:\n" + "\n".join(lines))
    return "\n\n".join(out)


async def archive_thread_to_logs(th, app: Dict[str, Any], reviewer, result: str, color: int):
    """Сохраняет переписку ветки заявки файлом .txt в канал логов. Возвращает текст ошибки или None."""
    cid = int(get_cfg().get("channels", {}).get("logs_channel_id", 0) or 0)
    log_ch = await get_channel(cid)
    if log_ch is None:
        return "архив ветки не отправлен: канал логов не найден (channels.logs_channel_id в config.json)"
    try:
        messages = [m async for m in th.history(limit=None, oldest_first=True)]
        data = _thread_dump(messages) or "(в ветке нет сообщений)"
        emb = disnake.Embed(
            title=f"📁 Архив заявки #{app['id']} — {result}",
            description=(f"👤 **Кандидат:** <@{app['user_id']}>\n"
                         f"🎮 **Ник:** `{app['nick']}` • **Статик:** `{app['static_id']}`\n"
                         f"👮 **Решение принял:** {reviewer.mention}\n"
                         f"📂 **Ветка:** {th.name}\n"
                         f"💬 **Сообщений:** {len(messages)}"),
            color=color,
        )
        emb.set_footer(text=footer_text())
        file = disnake.File(io.BytesIO(data.encode("utf-8")), filename=f"application_{app['id']}.txt")
        await log_ch.send(embed=emb, file=file)
        return None
    except Exception as e:
        print(f"[hall_recruit] архив ветки заявки #{app['id']}: {e!r}", file=sys.stderr, flush=True)
        return f"архив ветки не отправлен ({e.__class__.__name__}) — проверьте права бота в канале логов и «Читать историю сообщений»"


async def h_review(inter: disnake.MessageInteraction):
    app = await _load_card(inter)
    if not app:
        return
    await db.update_application_status(app["id"], "in_review", inter.author.id)
    emb = _card_update(inter.message, app["user_id"], f"{em('review')} **На рассмотрении**",
                       cfg_color("embed_color", 0x990000), inter.author.mention)
    await inter.response.edit_message(embed=emb)
    th = await get_thread(inter.guild, app["thread_id"])
    if th:
        text = T("recruit_review_desc", "Рекрутер {reviewer} взял заявку {mention} на рассмотрение.",
                 reviewer=inter.author.mention, mention=f"<@{app['user_id']}>")
        try:
            await th.send(text)
        except Exception:
            pass


def voice_options(guild: disnake.Guild) -> List[disnake.SelectOption]:
    """Голосовые каналы обзвона из config.json (числа или {"name":..,"id":..}) → пункты выпадающего списка."""
    opts, seen = [], set()
    for item in get_cfg().get("voice_channels", []) or []:
        try:
            cid = int(item.get("id", 0)) if isinstance(item, dict) else int(item)
        except (TypeError, ValueError):
            continue
        if not cid or cid in seen:
            continue
        seen.add(cid)
        ch = guild.get_channel(cid)
        name = (item.get("name") if isinstance(item, dict) else None) or (ch.name if ch else f"Канал {cid}")
        opts.append(disnake.SelectOption(label=str(name)[:100], value=str(cid),
                                         description="Позвать кандидата в этот канал", emoji="🎙️"))
    return opts[:25]


class VoicePickView(disnake.ui.View):
    """Мини-панель (видна только рекрутёру): выбор голосового канала для обзвона."""

    def __init__(self, card_message: disnake.Message, app: Dict[str, Any], recruiter_id: int,
                 options: List[disnake.SelectOption]):
        super().__init__(timeout=120)
        self.card_message = card_message
        self.app = app
        self.recruiter_id = recruiter_id
        self.add_item(HrSelect(self.on_pick, placeholder="Выберите голосовой канал для обзвона...",
                               min_values=1, max_values=1, options=options))

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:
        if inter.author.id != self.recruiter_id:
            await inter.response.send_message("❌ Это меню открыл другой рекрутер.", ephemeral=True)
            return False
        return True

    async def on_pick(self, inter: disnake.MessageInteraction, values: List[str]):
        fresh = await db.get_application_by_thread(self.app["thread_id"])
        if not fresh or fresh["status"] in ("accepted", "rejected"):
            return await inter.response.edit_message(content="ℹ️ Эта заявка уже обработана.", view=None)
        await do_interview(inter, self.card_message, fresh, f"<#{int(values[0])}>")


async def do_interview(inter: disnake.Interaction, card_message: disnake.Message, app: Dict[str, Any], voice: str):
    """Вызов на обзвон: статус, карточка, сообщение в ветку и ЛС кандидату. voice — упоминание выбранного канала."""
    done = f"✅ Кандидат приглашён на обзвон: {voice}"
    if inter.response.is_done():
        await inter.followup.send(done, ephemeral=True)
    else:
        await inter.response.edit_message(content=done, view=None)
    await db.update_application_status(app["id"], "in_review", inter.author.id)
    try:
        emb = _card_update(card_message, app["user_id"], f"{em('interview')} **Вызван на обзвон**",
                           cfg_color("embed_color", 0x990000), inter.author.mention)
        await card_message.edit(embed=emb)
    except Exception as e:
        print(f"[hall_recruit] не удалось обновить карточку заявки #{app['id']}: {e!r}", file=sys.stderr, flush=True)
    text = T("recruit_interview_desc",
             "{mention}, рекрутер {reviewer} приглашает вас на обзвон!\n📍 Канал: {voice}",
             mention=f"<@{app['user_id']}>", reviewer=inter.author.mention, voice=voice)
    th = await get_thread(inter.guild, app["thread_id"])
    if th:
        try:
            await th.send(text)
        except Exception:
            pass
    await dm(app["user_id"], X("interview_dm_title"),
             X("interview_dm_desc", reviewer=inter.author.mention, voice=voice), banner=False)


async def h_interview(inter: disnake.MessageInteraction):
    app = await _load_card(inter)
    if not app:
        return
    opts = voice_options(inter.guild)
    if len(opts) <= 1:
        # выбирать не из чего: каналов 0 или 1 — приглашаем сразу
        voice = f"<#{opts[0].value}>" if opts else voice_channels_text()
        await inter.response.defer(ephemeral=True)
        return await do_interview(inter, inter.message, app, voice)
    await inter.response.send_message("Выберите голосовой канал для обзвона:",
                                      view=VoicePickView(inter.message, app, inter.author.id, opts),
                                      ephemeral=True)


async def h_accept(inter: disnake.MessageInteraction):
    app = await _load_card(inter)
    if not app:
        return
    await inter.response.defer()
    guild = inter.guild
    member = guild.get_member(app["user_id"])
    if member is None:
        try:
            member = await guild.fetch_member(app["user_id"])
        except Exception:
            member = None
    if member is None:
        return await inter.followup.send("❌ Кандидат уже не на сервере — принять нельзя.", ephemeral=True)

    cfg = get_cfg()
    warnings = []

    # 1) СНАЧАЛА ник из анкеты (чтобы синхронизация рангов при выдаче роли увидела уже верный ник)
    new_nick = None
    if cfg.get("auto_nicknames", True):
        pattern = cfg.get("texts", {}).get("nickname_format", "[Hallez FAMQ] {nick} | {static}")
        try:
            new_nick = build_nick(pattern, app["nick"], app["static_id"])
            await member.edit(nick=new_nick, reason=f"Принят в семью ({inter.author})")
        except Exception as e:
            warnings.append(f"ник не изменён ({e.__class__.__name__}): роль бота должна быть выше роли кандидата; владельцу сервера ник сменить нельзя")

    # 2) роли
    role_ids = []
    fam = cfg.get("roles", {}).get("family_role_id", 0)
    rk1 = (cfg.get("ranks", {}).get("1", {}) or {}).get("role_id") or cfg.get("roles", {}).get("rank_1_role_id", 0)
    for rid in (fam, rk1):
        if rid:
            role = guild.get_role(rid)
            if role:
                role_ids.append(role)
    if role_ids:
        try:
            await member.add_roles(*role_ids, reason=f"Принят в семью ({inter.author})")
        except Exception as e:
            warnings.append(f"роли не выданы ({e.__class__.__name__}) — проверьте иерархию ролей бота")
    else:
        warnings.append("роли семьи не настроены в config.json")

    await db.upsert_member(app["user_id"], app["nick"], app["static_id"], 1)
    # внутри вызывается хук, который начисляет баллы рекруту и приглашающему
    await db.update_application_status(app["id"], "accepted", inter.author.id)

    emb = _card_update(inter.message, app["user_id"], f"{em('accept')} **Принят в семью**",
                       cfg_color("success_color", 0x2ECC71), inter.author.mention)
    await inter.message.edit(embed=emb, view=None)

    th = await get_thread(inter.guild, app["thread_id"])
    if th:
        try:
            await th.send(T("recruit_accepted_thread_msg", "🎉 {mention} принят в семью рекрутером {reviewer}!",
                            mention=f"<@{app['user_id']}>", reviewer=inter.author.mention))
        except Exception:
            pass
        # архив переписки уходит в логи ДО закрытия (иначе при режиме "delete" он был бы потерян)
        log_err = await archive_thread_to_logs(th, app, inter.author, "принят", cfg_color("success_color", 0x2ECC71))
        if log_err:
            warnings.append(log_err)
        # "archive" (по умолчанию) — заблокировать и архивировать, "delete" — удалить ветку
        err = await close_thread(th, app["id"], delete=str(cfg.get("accept_thread_action", "archive")).lower() == "delete",
                                 reason=f"Заявка #{app['id']} принята")
        if err:
            warnings.append(err)

    dm_text = T("recruit_accepted_dm", "🎉 Добро пожаловать в **Hallez FAMQ**!",
                nick=app["nick"], static=app["static_id"], reviewer=inter.author.mention)
    await dm(app["user_id"], X("accept_dm_title"), dm_text,
             color=cfg_color("success_color", 0x2ECC71))

    note = "✅ Кандидат принят."
    if warnings:
        note += "\n⚠️ " + "; ".join(warnings)
    await inter.followup.send(note, ephemeral=True)


class RejectModal(disnake.ui.Modal):
    def __init__(self, message: disnake.Message, app: Dict[str, Any]):
        self.message = message
        self.app = app
        super().__init__(
            title=(get_cfg().get("texts", {}).get("modal_reject_title") or "Отказ по заявке")[:45],
            custom_id=f"hr:rej_modal:{message.id}",
            components=[disnake.ui.TextInput(
                label="Причина отказа", custom_id="reason", style=disnake.TextInputStyle.paragraph,
                placeholder="Например: не прошёл обзвон, возраст, нет микрофона...", max_length=500)],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        reason = inter.text_values["reason"].strip()
        await inter.response.defer(ephemeral=True)
        app = self.app
        await db.update_application_status(app["id"], "rejected", inter.author.id, reason)
        emb = _card_update(self.message, app["user_id"], f"{em('reject')} **Отказано**",
                           cfg_color("error_color", 0xE74C3C), inter.author.mention)
        set_field(emb, "Причина", reason[:1024], False)
        await self.message.edit(embed=emb, view=None)
        th = await get_thread(inter.guild, app["thread_id"])
        if th:
            try:
                await th.send(T("recruit_rejected_desc", "👤 {mention}\n📝 Причина: {reason}",
                                mention=f"<@{app['user_id']}>", reviewer=inter.author.mention, reason=reason))
            except Exception:
                pass
            await close_thread(th, app["id"], reason=f"Заявка #{app['id']} отклонена")
        text = T("recruit_rejected_dm", "К сожалению, заявка отклонена.\n📝 Причина: {reason}",
                 nick=app["nick"], reason=reason)
        await dm(app["user_id"], "Решение по вашей заявке", text, color=cfg_color("error_color", 0xE74C3C))
        await inter.followup.send("✅ Отказ оформлен.", ephemeral=True)


async def h_reject(inter: disnake.MessageInteraction):
    app = await _load_card(inter)
    if not app:
        return
    await inter.response.send_modal(RejectModal(inter.message, app))


# ═════════════════════════ 2. БАЛЛЫ / ПРИГЛАШЕНИЯ / МАГАЗИН ═════════════════════════

class PointsView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(HrButton(h_me, custom_id="hr:me", row=0, **btn("me")))
        self.add_item(HrButton(h_ref, custom_id="hr:ref", row=0, **btn("ref")))
        self.add_item(HrButton(h_shop, custom_id="hr:shop", row=1, **btn("shop")))
        self.add_item(HrButton(h_top, custom_id="hr:top", row=1, **btn("top")))


async def h_me(inter: disnake.MessageInteraction):
    uid = inter.author.id
    pts = await db.get_points(uid)
    log = await db.points_log(uid, 5)
    lines = "\n".join(f"`{('+' if r['delta'] > 0 else '')}{r['delta']}` • {r['reason']} *({str(r['created_at'])[:10]})*" for r in log) or "Пока операций нет"
    emb = disnake.Embed(title=f"{em('points')} Ваши баллы", color=main_color(),
                        description=E(f"**Баланс:** `{pts['points']}` б.\n"
                                      f"Всего заработано: `{pts['earned']}` • потрачено: `{pts['spent']}`"))
    emb.add_field(name="Последние операции", value=lines, inline=False)
    emb.set_footer(text=footer_text())
    await inter.response.send_message(embed=emb, ephemeral=True)


async def h_top(inter: disnake.MessageInteraction):
    rows = await db.points_top(10)
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"{medals[i] if i < 3 else f'`{i + 1}.`'} <@{r['user_id']}> — **{r['earned']}** б. (баланс {r['points']})"
             for i, r in enumerate(rows)]
    emb = disnake.Embed(title=f"{em('top')} Топ рекрутов Hallez FAMQ", color=main_color(),
                        description="\n".join(lines) or "Пока никто не заработал баллов.")
    emb.set_footer(text=footer_text())
    await inter.response.send_message(embed=emb, ephemeral=True)


# ───── Приглашённый ─────

async def h_ref(inter: disnake.MessageInteraction):
    m = await db.get_member(inter.author.id)
    if not m and not is_staff(inter.author):
        return await inter.response.send_message("ℹ️ Подавать приглашённых могут только участники семьи.", ephemeral=True)
    await inter.response.send_modal(ReferralModal(m))


class ReferralModal(disnake.ui.Modal):
    def __init__(self, member: Optional[Dict[str, Any]]):
        T_ = disnake.ui.TextInput
        super().__init__(title="Я пригласил человека", custom_id="hr:ref_modal", components=[
            T_(label="Ваш ник (Имя Фамилия)", custom_id="my_nick", value=(member or {}).get("nick"), max_length=60),
            T_(label="Ваш статик", custom_id="my_static", value=str((member or {}).get("static_id") or "") or None, max_length=9),
            T_(label="Ник приглашённого", custom_id="inv_nick", max_length=60),
            T_(label="Статик приглашённого", custom_id="inv_static", max_length=9),
            T_(label="Доказательства (ссылка на скрин)", custom_id="proof", style=disnake.TextInputStyle.paragraph,
               placeholder="Скрин чата/войса/приглашения, что человек пришёл от вас", max_length=500),
        ])

    async def callback(self, inter: disnake.ModalInteraction):
        v = {k: x.strip() for k, x in inter.text_values.items()}
        if not (v["my_static"].isdigit() and v["inv_static"].isdigit()):
            return await inter.response.send_message("❌ Статики — только цифры.", ephemeral=True)
        if v["my_static"] == v["inv_static"]:
            return await inter.response.send_message("❌ Нельзя пригласить самого себя.", ephemeral=True)
        if await db.find_member_by_static(v["inv_static"]):
            return await inter.response.send_message("ℹ️ Этот игрок уже в семье — приглашение не засчитывается.", ephemeral=True)
        if await db.find_active_referral_by_static(v["inv_static"]):
            return await inter.response.send_message("ℹ️ На этого игрока уже подана заявка о приглашении.", ephemeral=True)
        ch = await get_channel(chan_id("orders"))
        if not isinstance(ch, disnake.TextChannel):
            return await inter.response.send_message("❌ Канал для приглашений не настроен (`/hr` → «Каналы и роли»).", ephemeral=True)

        await inter.response.defer(ephemeral=True)
        ref_id = await db.create_referral(inter.author.id, v["my_nick"], v["my_static"], v["inv_nick"], v["inv_static"], v["proof"])
        emb = disnake.Embed(title=f"{em('referral')} Приглашение #{ref_id}", color=cfg_color("warning_color", 0xF39C12),
                            description=f"От {inter.author.mention}\nСтатус: 🟡 **Ждёт проверки**")
        emb.add_field(name="Пригласил", value=f"`{v['my_nick']}` • `{v['my_static']}`", inline=True)
        emb.add_field(name="Приглашённый", value=f"`{v['inv_nick']}` • `{v['inv_static']}`", inline=True)
        emb.add_field(name="Доказательства", value=v["proof"][:1024], inline=False)
        emb.set_footer(text=f"Баллы: +{S['points']['referral']} после подтверждения и принятия кандидата")
        msg = await ch.send(embed=emb, view=RefView())
        await db.set_referral_message(ref_id, ch.id, msg.id)
        await inter.followup.send(X("ref_sent", ref_id=ref_id, inv_nick=v['inv_nick'], pts=S['points']['referral']), ephemeral=True)


class RefView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(HrButton(h_ref_ok, custom_id="hr:refok", **btn("ref_ok")))
        self.add_item(HrButton(h_ref_no, custom_id="hr:refno", **btn("ref_no")))


async def _load_ref(inter):
    if not is_staff(inter.author):
        await inter.response.send_message("❌ Только для рекрутеров.", ephemeral=True)
        return None
    ref = await db.get_referral_by_message(inter.message.id)
    if not ref:
        await inter.response.send_message("❌ Приглашение не найдено.", ephemeral=True)
        return None
    if ref["status"] not in ("pending",):
        await inter.response.send_message("ℹ️ Уже обработано.", ephemeral=True)
        return None
    return ref


async def _edit_ref_card(ref: Dict[str, Any], status: str, color: int, remove_buttons: bool):
    ch = await get_channel(ref.get("channel_id"))
    if not ch:
        return
    try:
        msg = await ch.fetch_message(ref["message_id"])
        emb = disnake.Embed.from_dict(msg.embeds[0].to_dict())
        emb.description = f"От <@{ref['inviter_id']}>\nСтатус: {status}"
        emb.color = color
        if remove_buttons:
            await msg.edit(embed=emb, view=None)
        else:
            await msg.edit(embed=emb)
    except Exception:
        pass


async def pay_referral(ref: Dict[str, Any], app: Dict[str, Any]):
    pts = S["points"]["referral"]
    if ref["inviter_id"] == app["user_id"]:
        return
    bal = await db.add_points(ref["inviter_id"], pts, f"Приглашён {ref['invitee_nick']}", ref=f"ref:{ref['id']}")
    await db.update_referral_status(ref["id"], "paid", app["id"])
    await _edit_ref_card(ref, f"{em('accept')} **Оплачено (+{pts} б.)**", cfg_color("success_color", 0x2ECC71), True)
    if bal is not None:
        await dm(ref["inviter_id"], X("ref_paid_title"), X("ref_paid_desc", nick=ref['invitee_nick'], static=ref['invitee_static'], pts=pts, bal=bal), color=cfg_color("success_color", 0x2ECC71))


async def h_ref_ok(inter: disnake.MessageInteraction):
    ref = await _load_ref(inter)
    if not ref:
        return
    await inter.response.defer()
    await db.update_referral_status(ref["id"], "verified")
    app = await db.get_accepted_application_by_static(ref["invitee_static"])
    if app:
        await pay_referral(ref, app)
        return await inter.followup.send("✅ Подтверждено — приглашённый уже принят, баллы начислены.", ephemeral=True)
    await _edit_ref_card(ref, f"{em('review')} **Подтверждено, ждём принятия кандидата**", main_color(), False)
    await dm(ref["inviter_id"], f"{em('referral')} Приглашение подтверждено",
             f"Доказательства по **{ref['invitee_nick']}** приняты. Баллы придут автоматически, "
             f"как только кандидата примут в семью.", banner=False)
    await inter.followup.send("✅ Подтверждено. Баллы начислятся при принятии кандидата.", ephemeral=True)


async def h_ref_no(inter: disnake.MessageInteraction):
    ref = await _load_ref(inter)
    if not ref:
        return
    await inter.response.defer()
    await db.update_referral_status(ref["id"], "rejected")
    await _edit_ref_card(ref, f"{em('reject')} **Отклонено**", cfg_color("error_color", 0xE74C3C), True)
    await dm(ref["inviter_id"], "Приглашение отклонено",
             f"Приглашение игрока **{ref['invitee_nick']}** не засчитано (недостаточно доказательств). "
             f"Вы можете подать его заново с понятными скринами.", color=cfg_color("error_color", 0xE74C3C), banner=False)
    await inter.followup.send("✅ Отклонено.", ephemeral=True)


# ───── Магазин ─────

def find_item(key: str) -> Optional[Dict[str, Any]]:
    return next((i for i in S["shop"] if i["key"] == key), None)


async def h_shop(inter: disnake.MessageInteraction):
    items = [i for i in S["shop"] if i.get("enabled")]
    if not items:
        return await inter.response.send_message("🛒 Магазин пока пуст.", ephemeral=True)
    pts = await db.get_points(inter.author.id)
    lines = []
    for i in items:
        stock = "" if i.get("stock", -1) < 0 else f" • осталось: {i['stock']}"
        lines.append(f"{i.get('emoji') or em('gift')} **{E(i['name'])}** — `{i['cost']}` б.{stock}\n> {E(i.get('desc', ''))}")
    emb = disnake.Embed(title=f"{em('shop')} Магазин рекрутов", color=main_color(),
                        description=f"Ваш баланс: **{pts['points']}** б.\n\n" + "\n\n".join(lines))
    emb.set_footer(text=footer_text())
    view = disnake.ui.View(timeout=180)
    opts = []
    for i in items[:25]:
        kw = {}
        e = pe(i.get("emoji")) if i.get("emoji") else None
        if e:
            kw["emoji"] = e
        opts.append(disnake.SelectOption(label=f"{i['name']} — {i['cost']} б."[:100], value=i["key"],
                                         description=(i.get("desc") or "")[:100] or None, **kw))
    view.add_item(HrSelect(_shop_pick, placeholder="Выберите приз...", options=opts))
    await inter.response.send_message(embed=emb, view=view, ephemeral=True)


async def _shop_pick(inter: disnake.MessageInteraction, values: List[str]):
    item = find_item(values[0])
    if not item or not item.get("enabled"):
        return await inter.response.send_message("❌ Этот приз недоступен.", ephemeral=True)
    pts = await db.get_points(inter.author.id)
    emb = disnake.Embed(title=f"{em('shop')} Подтверждение покупки", color=main_color(),
                        description=E(f"{item.get('emoji') or em('gift')} **{item['name']}**\n"
                                      f"Цена: `{item['cost']}` б.\nВаш баланс: `{pts['points']}` б. → останется `{pts['points'] - item['cost']}` б."))
    view = disnake.ui.View(timeout=120)
    view.add_item(HrButton(lambda i, k=item["key"]: _shop_buy(i, k), label="Купить", style=disnake.ButtonStyle.success, emoji="🛒"))
    view.add_item(HrButton(_shop_cancel, label="Отмена", style=disnake.ButtonStyle.secondary))
    await inter.response.edit_message(embed=emb, view=view)


async def _shop_cancel(inter: disnake.MessageInteraction):
    await inter.response.edit_message(content="Покупка отменена.", embed=None, view=None)


async def _shop_buy(inter: disnake.MessageInteraction, key: str):
    item = find_item(key)
    if not item or not item.get("enabled"):
        return await inter.response.edit_message(content="❌ Приз недоступен.", embed=None, view=None)
    if item.get("stock", -1) == 0:
        return await inter.response.edit_message(content="❌ Приз закончился.", embed=None, view=None)
    ch = await get_channel(chan_id("orders"))
    if not isinstance(ch, disnake.TextChannel):
        return await inter.response.edit_message(content="❌ Канал заказов не настроен — обратитесь к администрации.", embed=None, view=None)

    bal = await db.spend_points(inter.author.id, item["cost"], f"Покупка: {item['name']}")
    if bal is None:
        pts = await db.get_points(inter.author.id)
        return await inter.response.edit_message(
            content=f"❌ Не хватает баллов: нужно {item['cost']}, у вас {pts['points']}.", embed=None, view=None)
    if item.get("stock", -1) > 0:
        item["stock"] -= 1
        save_settings()

    order_id = await db.create_order(inter.author.id, item["key"], item["name"], item["cost"])
    emb = disnake.Embed(title=f"{em('shop')} Заказ #{order_id}", color=cfg_color("warning_color", 0xF39C12),
                        description=E(f"{inter.author.mention} купил **{item['name']}**\nСтатус: 🟡 **Ждёт выдачи**"))
    member = await db.get_member(inter.author.id)
    if member:
        emb.add_field(name="Игрок", value=f"`{member['nick']}` • `{member['static_id']}`", inline=True)
    emb.add_field(name="Цена", value=f"`{item['cost']}` б.", inline=True)
    msg = await ch.send(embed=emb, view=OrderView())
    await db.set_order_message(order_id, ch.id, msg.id)
    await inter.response.edit_message(
        content=f"{em('accept')} Заказ **#{order_id}** оформлен! Остаток: **{bal}** б. Руководство выдаст приз, вам придёт сообщение в ЛС.",
        embed=None, view=None)


class OrderView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(HrButton(h_order_ok, custom_id="hr:ordok", **btn("ord_ok")))
        self.add_item(HrButton(h_order_no, custom_id="hr:ordno", **btn("ord_no")))


async def _load_order(inter):
    if not is_admin(inter.author) and not is_staff(inter.author):
        await inter.response.send_message("❌ Только для руководства.", ephemeral=True)
        return None
    o = await db.get_order_by_message(inter.message.id)
    if not o:
        await inter.response.send_message("❌ Заказ не найден.", ephemeral=True)
        return None
    if o["status"] != "pending":
        await inter.response.send_message("ℹ️ Заказ уже обработан.", ephemeral=True)
        return None
    return o


async def h_order_ok(inter: disnake.MessageInteraction):
    o = await _load_order(inter)
    if not o:
        return
    await db.update_order_status(o["id"], "done")
    emb = disnake.Embed.from_dict(inter.message.embeds[0].to_dict())
    emb.description = f"<@{o['user_id']}> • **{o['item_name']}**\nСтатус: {em('accept')} **Выдано** ({inter.author.mention})"
    emb.color = cfg_color("success_color", 0x2ECC71)
    await inter.response.edit_message(embed=emb, view=None)
    await dm(o["user_id"], X("order_done_title"), X("order_done_desc", order_id=o['id'], item=o['item_name'], admin=inter.author.mention), color=cfg_color("success_color", 0x2ECC71))


async def h_order_no(inter: disnake.MessageInteraction):
    o = await _load_order(inter)
    if not o:
        return
    await db.update_order_status(o["id"], "refunded")
    await db.refund_points(o["user_id"], o["cost"], f"Возврат: {o['item_name']}", ref=f"refund:{o['id']}")
    item = find_item(o["item_key"])
    if item and item.get("stock", -1) >= 0:
        item["stock"] += 1
        save_settings()
    emb = disnake.Embed.from_dict(inter.message.embeds[0].to_dict())
    emb.description = f"<@{o['user_id']}> • **{o['item_name']}**\nСтатус: {em('reject')} **Отказано, баллы возвращены** ({inter.author.mention})"
    emb.color = cfg_color("error_color", 0xE74C3C)
    await inter.response.edit_message(embed=emb, view=None)
    await dm(o["user_id"], "Заказ отклонён",
             f"Заказ **#{o['id']}** ({o['item_name']}) отклонён. Потраченные **{o['cost']}** баллов возвращены на ваш баланс.",
             color=cfg_color("error_color", 0xE74C3C), banner=False)


# ═════════════════════════ 3. АДМИН-ПАНЕЛЬ /hr ═════════════════════════

def dash_embed() -> disnake.Embed:
    ch = lambda k: (f"<#{chan_id(k)}>" if chan_id(k) else "не задан")
    pub = S["published"]
    p = S["points"]
    is_open = get_cfg().get("recruitment_open", True)
    emb = disnake.Embed(
        title=f"{em('crown')} Рекрут-центр • Панель управления", color=main_color(),
        description=("Всё настраивается кнопками ниже — править файлы не нужно.\n"
                     f"**Набор сейчас:** {'🟢 открыт' if is_open else '🔴 закрыт'}"))
    try:
        emb.set_thumbnail(url=_BOT.user.display_avatar.url)
    except Exception:
        pass
    emb.add_field(name="📝 Анкета", value=f"Полей включено: **{len(enabled_fields())}** из {len(S['form'])} (макс. 5 в окне)", inline=True)
    emb.add_field(name="💎 Баллы", value=f"Принятие заявки: **+{p['accept']}**\nПриглашённый: **+{p['referral']}**", inline=True)
    emb.add_field(name="🛒 Магазин", value=f"Призов: **{len([i for i in S['shop'] if i.get('enabled')])}**", inline=True)
    emb.add_field(name="🔘 Кнопки и ✍️ тексты",
                  value=f"Кнопок изменено: **{len(S['buttons'])}** из {len(BUTTONS)}\nСвоих текстов: **{len(S['texts'])}** из {len(XT)}", inline=True)
    emb.add_field(name="🖼️ Баннер", value="✅ загружен" if os.path.exists(BANNER_PATH) else "❌ нет файла — `/hr_banner`", inline=True)
    vid = "✅ файл" if video_path() else ("✅ ссылка" if S["design"].get("video_url") else "— (`/hr_video`)")
    emb.add_field(name="🎬 Видео", value=vid, inline=True)
    emb.add_field(name="📍 Каналы",
                  value=f"Панель заявки: {ch('panel')}\nКарточки заявок: {ch('review')}\nЗаказы/приглашения: {ch('orders')}\nРекрут-центр: {ch('points_panel')}",
                  inline=False)
    emb.add_field(name="📌 Опубликовано",
                  value=f"Заявка: {'✅' if pub['apply'][1] else '—'} • Рекрут-центр: {'✅' if pub['points'][1] else '—'}", inline=False)
    emb.set_footer(text="Изменили оформление, кнопки или тексты? Нажмите «Опубликовать», чтобы обновить панели в каналах.")
    return emb


class AdminView(disnake.ui.View):
    """Базовый класс: только вызвавший админ может нажимать."""
    def __init__(self, author_id: int, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.author_id = author_id

    async def interaction_check(self, inter: disnake.Interaction) -> bool:
        if inter.author.id != self.author_id:
            await inter.response.send_message("Это не ваша панель.", ephemeral=True)
            return False
        return True


def _back_button(uid: int):
    async def back(inter):
        await inter.response.edit_message(embed=dash_embed(), view=DashView(inter.author.id))
    return HrButton(back, label="Назад", style=disnake.ButtonStyle.secondary, emoji="⬅️")


async def _refresh(inter, embed: disnake.Embed, view: disnake.ui.View):
    try:
        await inter.response.edit_message(embed=embed, view=view)
    except Exception:
        await inter.response.send_message(embed=embed, view=view, ephemeral=True)


def _dis(view: disnake.ui.View) -> disnake.ui.View:
    for c in view.children:
        c.disabled = True
    return view


class DashView(AdminView):
    def __init__(self, author_id: int):
        super().__init__(author_id)
        B = disnake.ButtonStyle
        is_open = get_cfg().get("recruitment_open", True)
        add = self.add_item
        add(HrButton(self.d_design, label="Оформление", style=B.primary, emoji="🎨", row=0))
        add(HrButton(self.d_form, label="Анкета", style=B.primary, emoji="📝", row=0))
        add(HrButton(self.d_buttons, label="Кнопки", style=B.primary, emoji="🔘", row=0))
        add(HrButton(self.d_texts, label="Тексты", style=B.primary, emoji="✍️", row=0))
        add(HrButton(self.d_emoji, label="Эмодзи", style=B.primary, emoji="😀", row=0))
        add(HrButton(self.d_points, label="Баллы", style=B.secondary, emoji="💎", row=1))
        add(HrButton(self.d_shop, label="Магазин", style=B.secondary, emoji="🛒", row=1))
        add(HrButton(self.d_channels, label="Каналы и роли", style=B.secondary, emoji="📍", row=1))
        add(HrButton(self.d_preview, label="Предпросмотр", style=B.secondary, emoji="👁️", row=2))
        add(HrButton(self.d_toggle, label="Закрыть набор" if is_open else "Открыть набор",
                     style=B.danger if is_open else B.success, emoji="🔒" if is_open else "🔓", row=2))
        add(HrButton(self.d_publish, label="Опубликовать", style=B.success, emoji="🚀", row=2))

    async def d_form(self, inter):
        await inter.response.edit_message(embed=form_embed(), view=FormView(inter.author.id))

    async def d_design(self, inter):
        await inter.response.send_modal(DesignModal())

    async def d_buttons(self, inter):
        await inter.response.edit_message(embed=buttons_embed(), view=ButtonsView(inter.author.id))

    async def d_texts(self, inter):
        v = TextsView(inter.author.id)
        await inter.response.edit_message(embed=texts_embed(None), view=v)

    async def d_points(self, inter):
        await inter.response.send_modal(PointsModal())

    async def d_shop(self, inter):
        await inter.response.edit_message(embed=shop_embed(), view=ShopAdminView(inter.author.id))

    async def d_channels(self, inter):
        v = ChannelsView(inter.author.id)
        await inter.response.edit_message(embed=v.embed(), view=v)

    async def d_emoji(self, inter):
        await inter.response.edit_message(embed=emoji_embed(), view=EmojiView(inter.author.id))

    async def d_preview(self, inter):
        await inter.response.edit_message(
            embed=disnake.Embed(title="👁️ Предпросмотр", color=main_color(),
                                description="Выберите, что показать. Предпросмотр видите только вы, кнопки в нём неактивны."),
            view=PreviewView(inter.author.id))

    async def d_toggle(self, inter):
        try:
            cfg_update(lambda c: c.__setitem__("recruitment_open", not c.get("recruitment_open", True)))
        except Exception as e:
            return await inter.response.send_message(f"❌ Не удалось изменить config.json: {e}", ephemeral=True)
        await inter.response.edit_message(embed=dash_embed(), view=DashView(inter.author.id))

    async def d_publish(self, inter):
        await inter.response.edit_message(embed=publish_embed(), view=PublishView(inter.author.id))


# ───── Предпросмотр ─────

class PreviewView(AdminView):
    def __init__(self, author_id: int):
        super().__init__(author_id)
        B = disnake.ButtonStyle
        self.add_item(HrButton(self.p_apply, label="Панель заявки", style=B.primary, emoji="📝", row=0))
        self.add_item(HrButton(self.p_points, label="Рекрут-центр", style=B.primary, emoji="💎", row=0))
        self.add_item(HrButton(self.p_card, label="Карточка заявки", style=B.primary, emoji="🗂️", row=0))
        self.add_item(_back_button(author_id))

    async def p_apply(self, inter):
        emb = apply_embed()
        f = put_banner(emb)
        kw = {"file": f} if f else {}
        await inter.response.send_message("👁️ Так выглядит панель заявки:", embed=emb, view=_dis(ApplyView()), ephemeral=True, **kw)

    async def p_points(self, inter):
        emb = points_embed()
        f = put_banner(emb)
        kw = {"file": f} if f else {}
        await inter.response.send_message("👁️ Так выглядит Рекрут-центр:", embed=emb, view=_dis(PointsView()), ephemeral=True, **kw)

    async def p_card(self, inter):
        sample = [{"key": f["key"], "label": f["label"], "value": f.get("placeholder") or "—"} for f in enabled_fields()]
        emb = card_embed("Travis Hallez", inter.author, sample, "🟡 **Новая заявка**", cfg_color("warning_color", 0xF39C12))
        await inter.response.send_message("👁️ Так выглядит карточка для рекрутеров:", embed=emb, view=_dis(CardView()), ephemeral=True)


# ───── Кнопки ─────

def buttons_embed() -> disnake.Embed:
    lines = []
    for k, (name, default_label, slot, style, cfg_key) in BUTTONS.items():
        o = S["buttons"].get(k) or {}
        b = btn(k)
        emj = "" if (o.get("emoji") or "").strip() == "-" else E(o.get("emoji") or em(slot))
        mark = "✏️" if o else "▫️"
        lines.append(f"{mark} {emj} **{name}** — «{b['label']}» · `{STYLE_NAME[parse_style(o.get('style'), style)]}`")
    return disnake.Embed(
        title="🔘 Кнопки", color=main_color(),
        description="\n".join(lines) + "\n\nВыберите кнопку в списке: можно поменять **текст, эмодзи и цвет**.\n"
                    "✏️ — изменена вами. Новые карточки/заказы сразу получат новый вид, а панели в каналах — после «Опубликовать».")


class ButtonsView(AdminView):
    def __init__(self, author_id: int):
        super().__init__(author_id)
        opts = []
        for k, (name, *_rest) in BUTTONS.items():
            b = btn(k)
            kw = {"emoji": b["emoji"]} if b["emoji"] else {}
            opts.append(disnake.SelectOption(label=name[:100], value=k, description=f"«{b['label']}»"[:100], **kw))
        self.add_item(HrSelect(self.pick, placeholder="Выберите кнопку для редактирования...", options=opts, row=0))
        self.add_item(HrButton(self.reset, label="Сбросить все кнопки", style=disnake.ButtonStyle.danger, emoji="♻️", row=1))
        self.add_item(_back_button(author_id))

    async def pick(self, inter, values):
        await inter.response.send_modal(ButtonModal(values[0]))

    async def reset(self, inter):
        S["buttons"] = {}
        save_settings()
        await inter.response.edit_message(embed=buttons_embed(), view=ButtonsView(inter.author.id))


class ButtonModal(disnake.ui.Modal):
    def __init__(self, key: str):
        self.key = key
        name, default_label, slot, style, cfg_key = BUTTONS[key]
        o = S["buttons"].get(key) or {}
        T_ = disnake.ui.TextInput
        super().__init__(title=f"Кнопка: {name}"[:45], custom_id=f"hr:btn:{key}", components=[
            T_(label="Текст кнопки", custom_id="label", value=btn(key)["label"], max_length=80),
            T_(label="Эмодзи (:имя: / <:имя:id> / 😀, «-» = без)", custom_id="emoji",
               value=(o.get("emoji") or em(slot))[:100], required=False, max_length=100),
            T_(label="Цвет: синяя / серая / зелёная / красная", custom_id="style",
               value=STYLE_NAME[parse_style(o.get("style"), style)], max_length=12),
            T_(label="Действие: сохранить / сбросить", custom_id="action", value="сохранить", max_length=10),
        ])

    async def callback(self, inter: disnake.ModalInteraction):
        v = {k: x.strip() for k, x in inter.text_values.items()}
        name, default_label, slot, style, cfg_key = BUTTONS[self.key]
        if v["action"].lower().startswith("сброс"):
            S["buttons"].pop(self.key, None)
        else:
            raw = v["emoji"]
            val = E(raw)
            if raw not in ("", "-") and not (_CUSTOM_RE.match(val) or (not val.startswith(":") and any(ord(c) > 127 for c in val))):
                return await inter.response.send_message(f"❌ `{raw}` не похоже на эмодзи (или такого нет на сервере).", ephemeral=True)
            S["buttons"][self.key] = {
                "label": "" if v["label"] == _base_label(self.key) else v["label"],
                "emoji": "" if raw in ("", em(slot)) else raw,
                "style": "" if parse_style(v["style"], style) == style else parse_style(v["style"], style),
            }
            if not any(S["buttons"][self.key].values()):
                S["buttons"].pop(self.key)
        save_settings()
        await _refresh(inter, buttons_embed(), ButtonsView(inter.author.id))
        await inter.followup.send("✅ Сохранено. Чтобы обновить панели в каналах — «Назад» → «Опубликовать».", ephemeral=True)


# ───── Тексты ─────

TEXT_GROUPS = [
    ("💎 Рекрут-центр, ответы и ЛС", lambda k, src: src == "cog"),
    ("📥 Набор: карточки и ЛС", lambda k, src: k.startswith(("recruit_", "modal_recruit", "modal_reject", "btn_recruit"))),
    ("📈 Повышения", lambda k, src: k.startswith(("promo_", "modal_promo", "select_promo", "btn_promo"))),
    ("⚔️ События и прочее", lambda k, src: True),
]


def _text_value(key: str, src: str) -> str:
    if src == "cog":
        return str(S["texts"].get(key) or XT[key][1])
    return str(get_cfg().get("texts", {}).get(key, ""))


def _text_items():
    items = [(k, "cog") for k in XT]
    items += [(k, "cfg") for k in get_cfg().get("texts", {})]
    return items


def _group_of(key: str, src: str) -> str:
    for name, pred in TEXT_GROUPS:
        if pred(key, src):
            return name
    return TEXT_GROUPS[-1][0]


def texts_embed(group: Optional[str]) -> disnake.Embed:
    desc = ("Здесь меняются **все тексты** бота: ответы, карточки, личные сообщения, анкеты и повышения.\n"
            "Выберите раздел, затем нужный текст — откроется окно редактирования.\n\n"
            "• Переменные пишутся в скобках, например `{mention}`; список показан в заголовке окна.\n"
            "• Эмодзи сервера: `:имя:`. Перенос строки — обычный Enter.\n"
            "• Чтобы вернуть стандартный текст рекрут-центра, напишите в окне `сброс`.")
    if group:
        desc += f"\n\n**Раздел:** {group}"
    return disnake.Embed(title="✍️ Тексты", color=main_color(), description=desc)


class TextsView(AdminView):
    PER = 25

    def __init__(self, author_id: int, group: Optional[str] = None):
        super().__init__(author_id)
        self.group, self.page = group, 0
        self._build()

    def _build(self):
        self.clear_items()
        self.add_item(HrSelect(self.pick_group, placeholder="📂 Выберите раздел", row=0,
                               options=[disnake.SelectOption(label=n, value=n, default=(n == self.group)) for n, _ in TEXT_GROUPS]))
        if self.group:
            keys = [(k, s) for k, s in _text_items() if _group_of(k, s) == self.group]
            chunk = keys[self.page * self.PER:(self.page + 1) * self.PER]
            if chunk:
                opts = []
                for k, s in chunk:
                    title = XT[k][0] if s == "cog" else k
                    prev = _text_value(k, s).replace("\n", " ")[:95] or "—"
                    opts.append(disnake.SelectOption(label=title[:100], value=f"{s}|{k}", description=prev))
                self.add_item(HrSelect(self.pick_key, placeholder="✏️ Что изменить?", options=opts, row=1))
            if len(keys) > self.PER:
                self.add_item(HrButton(self.prev, label="◀", row=2, disabled=self.page == 0))
                self.add_item(HrButton(self.next, label="▶", row=2, disabled=(self.page + 1) * self.PER >= len(keys)))
        self.add_item(_back_button(self.author_id))

    async def pick_group(self, inter, values):
        self.group, self.page = values[0], 0
        self._build()
        await inter.response.edit_message(embed=texts_embed(self.group), view=self)

    async def pick_key(self, inter, values):
        src, key = values[0].split("|", 1)
        await inter.response.send_modal(EditTextModal(key, src, self))

    async def prev(self, inter):
        self.page -= 1
        self._build()
        await inter.response.edit_message(view=self)

    async def next(self, inter):
        self.page += 1
        self._build()
        await inter.response.edit_message(view=self)


class EditTextModal(disnake.ui.Modal):
    def __init__(self, key: str, src: str, view: TextsView):
        self.key, self.src, self.tview = key, src, view
        if src == "cog":
            name, _default, hint = XT[key]
            title = name
            label = f"Переменные: {hint}" if hint else "Текст (можно :эмодзи:)"
        else:
            title, label = "Текст", key
        super().__init__(title=title[:45], custom_id=f"hr:txt:{src}:{key}"[:100], components=[
            disnake.ui.TextInput(label=label[:45], custom_id="value", value=_text_value(key, src)[:4000] or None,
                                 style=disnake.TextInputStyle.paragraph, max_length=4000, required=(src == "cfg"))])

    async def callback(self, inter: disnake.ModalInteraction):
        val = inter.text_values["value"]
        if self.src == "cog":
            if val.strip().lower() in ("", "сброс"):
                S["texts"].pop(self.key, None)
            else:
                S["texts"][self.key] = val
            save_settings()
        else:
            try:
                cfg_update(lambda c: c.setdefault("texts", {}).__setitem__(self.key, val))
            except Exception as e:
                return await inter.response.send_message(f"❌ Не удалось записать config.json: {e}", ephemeral=True)
        self.tview._build()
        await inter.response.edit_message(embed=texts_embed(self.tview.group), view=self.tview)
        await inter.followup.send("✅ Сохранено. Новые сообщения будут с этим текстом (панели в каналах — после «Опубликовать»).", ephemeral=True)


# ───── Оформление ─────

class DesignModal(disnake.ui.Modal):
    def __init__(self):
        d = S["design"]
        T_ = disnake.ui.TextInput
        super().__init__(title="Оформление панели заявки", custom_id="hr:design", components=[
            T_(label="Заголовок", custom_id="title", value=d["title"], max_length=200),
            T_(label="Текст (можно :эмодзи:, **жирный**)", custom_id="description", value=d["description"],
               style=disnake.TextInputStyle.paragraph, max_length=3500),
            T_(label="Подпись внизу", custom_id="footer", value=d["footer"], max_length=100),
            T_(label="Цвет (HEX, например 990000)", custom_id="color", value=str(d["color"]).replace("0x", ""), max_length=8),
            T_(label="Шаги набора (1-я строка — заголовок)", custom_id="steps",
               value=(d.get("steps") if d.get("steps") is not None else default_steps())[:1000] or "-",
               style=disnake.TextInputStyle.paragraph, required=False, max_length=1000,
               placeholder="«-» чтобы убрать блок"),
        ])

    async def callback(self, inter: disnake.ModalInteraction):
        v = inter.text_values
        S["design"].update({"title": v["title"].strip(), "description": v["description"].strip(),
                            "footer": v["footer"].strip(), "steps": (v["steps"].strip() or "-"), "color": "0x" + v["color"].strip().replace("#", "").replace("0x", "")})
        save_settings()
        await _refresh(inter, dash_embed(), DashView(inter.author.id))
        await inter.followup.send("✅ Оформление сохранено. Нажмите «Опубликовать», чтобы обновить панель в канале.", ephemeral=True)


class PointsModal(disnake.ui.Modal):
    def __init__(self):
        p = S["points"]
        T_ = disnake.ui.TextInput
        super().__init__(title="Баллы за действия", custom_id="hr:pts", components=[
            T_(label="Баллов за принятую заявку (рекруту)", custom_id="accept", value=str(p["accept"]), max_length=5),
            T_(label="Баллов за приглашённого (пригласившему)", custom_id="referral", value=str(p["referral"]), max_length=5),
        ])

    async def callback(self, inter: disnake.ModalInteraction):
        v = inter.text_values
        try:
            a, r = int(v["accept"]), int(v["referral"])
            assert 0 <= a <= 10000 and 0 <= r <= 10000
        except Exception:
            return await inter.response.send_message("❌ Нужны целые числа от 0 до 10000.", ephemeral=True)
        S["points"].update({"accept": a, "referral": r})
        save_settings()
        await _refresh(inter, dash_embed(), DashView(inter.author.id))


# ───── Анкета ─────

def form_embed() -> disnake.Embed:
    lines = []
    for n, f in enumerate(S["form"], 1):
        mark = "🟢" if f.get("enabled") else "⚫"
        t = "длинный" if f.get("style") == "long" else "короткий"
        req = "обяз." if f.get("required", True) else "необяз."
        lines.append(f"{mark} **{n}.** {f['label']} — *{t}, {req}*")
    emb = disnake.Embed(title="📝 Анкета на вступление", color=main_color(),
                        description="\n".join(lines) +
                        "\n\nВыберите поле в списке, чтобы изменить вопрос, подсказку, тип, порядок или выключить его.\n"
                        "ℹ️ Discord показывает в окне **не больше 5 полей**; **ник** и **статик** отключать нельзя.")
    return emb


class FormView(AdminView):
    def __init__(self, author_id: int):
        super().__init__(author_id)
        opts = [disnake.SelectOption(label=f"{n}. {f['label']}"[:100], value=f["key"],
                                     description=("включено" if f.get("enabled") else "выключено"))
                for n, f in enumerate(S["form"][:25], 1)]
        self.add_item(HrSelect(self.pick, placeholder="Выберите поле для редактирования...", options=opts, row=0))
        self.add_item(HrButton(self.add, label="Добавить вопрос", style=disnake.ButtonStyle.success, emoji="➕", row=1))
        self.add_item(HrButton(self.reset, label="Сбросить на стандартную", style=disnake.ButtonStyle.danger, emoji="♻️", row=1))
        self.add_item(_back_button(author_id))

    async def pick(self, inter, values):
        f = next((x for x in S["form"] if x["key"] == values[0]), None)
        if f:
            await inter.response.send_modal(FieldModal(f))

    async def add(self, inter):
        if len(S["form"]) >= 12:
            return await inter.response.send_message("❌ Максимум 12 вопросов в списке — удалите ненужный.", ephemeral=True)
        await inter.response.send_modal(AddFieldModal())

    async def reset(self, inter):
        S["form"] = copy.deepcopy(DEFAULT_FORM)
        save_settings()
        await inter.response.edit_message(embed=form_embed(), view=FormView(inter.author.id))


class FieldModal(disnake.ui.Modal):
    def __init__(self, field: Dict[str, Any]):
        self.key = field["key"]
        T_ = disnake.ui.TextInput
        super().__init__(title=f"Поле: {field['label']}"[:45], custom_id=f"hr:field:{field['key']}", components=[
            T_(label="Вопрос", custom_id="label", value=field["label"], max_length=45),
            T_(label="Подсказка внутри поля", custom_id="placeholder", value=field.get("placeholder") or None,
               required=False, max_length=100),
            T_(label="Тип: короткий / длинный", custom_id="style", value="длинный" if field.get("style") == "long" else "короткий", max_length=10),
            T_(label="Обязательный: да / нет", custom_id="required", value="да" if field.get("required", True) else "нет", max_length=5),
            T_(label="Действие: вкл / выкл / вверх / вниз / удалить", custom_id="action",
               value="вкл" if field.get("enabled") else "выкл", max_length=10),
        ])

    async def callback(self, inter: disnake.ModalInteraction):
        v = {k: x.strip() for k, x in inter.text_values.items()}
        idx = next((i for i, x in enumerate(S["form"]) if x["key"] == self.key), None)
        if idx is None:
            return await inter.response.send_message("❌ Поле не найдено.", ephemeral=True)
        f = S["form"][idx]
        protected = f["key"] in ("nick", "static")
        act = v["action"].lower()

        if act.startswith("удал"):
            if protected:
                return await inter.response.send_message("❌ Ник и статик удалять нельзя.", ephemeral=True)
            S["form"].pop(idx)
            save_settings()
            return await _refresh(inter, form_embed(), FormView(inter.author.id))

        f["label"] = v["label"] or f["label"]
        f["placeholder"] = v["placeholder"]
        f["style"] = "long" if v["style"].lower().startswith(("д", "l")) else "short"
        f["required"] = True if protected else _yes(v["required"])

        if act.startswith("вверх") and idx > 0:
            S["form"][idx - 1], S["form"][idx] = S["form"][idx], S["form"][idx - 1]
        elif act.startswith("вниз") and idx < len(S["form"]) - 1:
            S["form"][idx + 1], S["form"][idx] = S["form"][idx], S["form"][idx + 1]
        elif act.startswith(("выкл", "off", "нет")):
            if protected:
                return await inter.response.send_message("❌ Ник и статик отключать нельзя.", ephemeral=True)
            f["enabled"] = False
        elif act.startswith(("вкл", "on", "да")):
            if not f.get("enabled") and len(enabled_fields()) >= 5:
                return await inter.response.send_message("❌ Уже включено 5 полей — выключите другое.", ephemeral=True)
            f["enabled"] = True
        save_settings()
        await _refresh(inter, form_embed(), FormView(inter.author.id))


class AddFieldModal(disnake.ui.Modal):
    def __init__(self):
        T_ = disnake.ui.TextInput
        super().__init__(title="Новый вопрос анкеты", custom_id="hr:addfield", components=[
            T_(label="Вопрос", custom_id="label", max_length=45),
            T_(label="Подсказка внутри поля", custom_id="placeholder", required=False, max_length=100),
            T_(label="Тип: короткий / длинный", custom_id="style", value="короткий", max_length=10),
        ])

    async def callback(self, inter: disnake.ModalInteraction):
        v = {k: x.strip() for k, x in inter.text_values.items()}
        enable = len(enabled_fields()) < 5
        S["form"].append({
            "key": f"q{int(time.time()) % 1000000}", "label": v["label"], "placeholder": v["placeholder"],
            "style": "long" if v["style"].lower().startswith(("д", "l")) else "short",
            "required": True, "enabled": enable,
        })
        save_settings()
        await _refresh(inter, form_embed(), FormView(inter.author.id))
        if not enable:
            await inter.followup.send("ℹ️ Вопрос добавлен **выключенным**: в окне уже 5 полей. Выключите другое и включите этот.", ephemeral=True)


# ───── Магазин (админ) ─────

def shop_embed() -> disnake.Embed:
    lines = []
    for i in S["shop"]:
        mark = "🟢" if i.get("enabled") else "⚫"
        stock = "∞" if i.get("stock", -1) < 0 else i["stock"]
        lines.append(f"{mark} {i.get('emoji') or ''} **{i['name']}** — `{i['cost']}` б. • остаток: {stock}\n> {i.get('desc', '')}")
    return disnake.Embed(title="🛒 Магазин призов", color=main_color(),
                         description="\n".join(lines) + "\n\nВыберите приз, чтобы изменить цену, описание, остаток или выключить/удалить.")


class ShopAdminView(AdminView):
    def __init__(self, author_id: int):
        super().__init__(author_id)
        if S["shop"]:
            opts = [disnake.SelectOption(label=f"{i['name']} — {i['cost']} б."[:100], value=i["key"]) for i in S["shop"][:25]]
            self.add_item(HrSelect(self.pick, placeholder="Выберите приз для редактирования...", options=opts, row=0))
        self.add_item(HrButton(self.add, label="Добавить приз", style=disnake.ButtonStyle.success, emoji="➕", row=1))
        self.add_item(_back_button(author_id))

    async def pick(self, inter, values):
        i = find_item(values[0])
        if i:
            await inter.response.send_modal(ItemModal(i))

    async def add(self, inter):
        if len(S["shop"]) >= 20:
            return await inter.response.send_message("❌ Максимум 20 призов.", ephemeral=True)
        await inter.response.send_modal(ItemModal(None))


class ItemModal(disnake.ui.Modal):
    def __init__(self, item: Optional[Dict[str, Any]]):
        self.key = item["key"] if item else None
        T_ = disnake.ui.TextInput
        name = ""
        if item:
            name = (f"{item['emoji']} " if item.get("emoji") else "") + item["name"]
        super().__init__(title="Приз магазина", custom_id=f"hr:item:{self.key or 'new'}", components=[
            T_(label="Название (можно начать с эмодзи)", custom_id="name", value=name or None, max_length=80),
            T_(label="Цена в баллах", custom_id="cost", value=str(item["cost"]) if item else "100", max_length=6),
            T_(label="Описание", custom_id="desc", value=(item or {}).get("desc") or None, required=False,
               style=disnake.TextInputStyle.paragraph, max_length=200),
            T_(label="Остаток (-1 = без ограничений)", custom_id="stock", value=str(item.get("stock", -1)) if item else "-1", max_length=5),
            T_(label="Действие: вкл / выкл / удалить", custom_id="action", value=("вкл" if (not item or item.get("enabled")) else "выкл"), max_length=10),
        ])

    async def callback(self, inter: disnake.ModalInteraction):
        v = {k: x.strip() for k, x in inter.text_values.items()}
        try:
            cost = int(v["cost"])
            stock = int(v["stock"])
            assert 1 <= cost <= 1000000 and stock >= -1
        except Exception:
            return await inter.response.send_message("❌ Цена — целое число ≥ 1, остаток — целое ≥ -1.", ephemeral=True)
        item = find_item(self.key) if self.key else None
        act = v["action"].lower()
        if item and act.startswith("удал"):
            S["shop"].remove(item)
            save_settings()
            return await _refresh(inter, shop_embed(), ShopAdminView(inter.author.id))
        emoji, name = split_leading_emoji(v["name"])
        if not item:
            item = {"key": f"item{int(time.time()) % 1000000}", "emoji": "🎁"}
            S["shop"].append(item)
        if emoji:
            item["emoji"] = emoji
        item.update({"name": name or "Приз", "cost": cost, "desc": v["desc"], "stock": stock,
                     "enabled": not act.startswith(("выкл", "off", "нет"))})
        save_settings()
        await _refresh(inter, shop_embed(), ShopAdminView(inter.author.id))


# ───── Каналы и роли ─────

CHANNEL_TARGETS = {
    "panel": "Панель заявки (где кнопка «Подать заявку»)",
    "review": "Карточки заявок для рекрутеров",
    "orders": "Заказы магазина и приглашения",
    "points_panel": "Панель «Рекрут-центр» (баллы/магазин)",
}


class ChannelsView(AdminView):
    def __init__(self, author_id: int):
        super().__init__(author_id)
        self.target = "panel"
        self.add_item(HrSelect(self.pick_target, placeholder="1) Что настраиваем?", row=0,
                               options=[disnake.SelectOption(label=v[:100], value=k) for k, v in CHANNEL_TARGETS.items()]))
        self.add_item(HrChannelSelect(self.pick_channel, placeholder="2) Выберите канал для выбранного пункта",
                                      channel_types=[disnake.ChannelType.text], row=1))
        self.add_item(HrRoleSelect(self.pick_roles, placeholder="Роли рекрутеров (доступ к кнопкам заявок и пинг)",
                                   min_values=0, max_values=10, row=2))
        self.add_item(_back_button(author_id))

    def embed(self) -> disnake.Embed:
        lines = [f"**{v}:** " + (f"<#{S['channels'][k]}>" if S["channels"].get(k) else "по умолчанию")
                 for k, v in CHANNEL_TARGETS.items()]
        roles = " ".join(f"<@&{r}>" for r in S["staff_roles"]) or "только роли из config.json"
        return disnake.Embed(title="📍 Каналы и роли", color=main_color(),
                             description="\n".join(lines) + f"\n\n**Роли рекрутеров:** {roles}\n\n"
                                         f"Сейчас выбран пункт: **{CHANNEL_TARGETS[self.target]}**")

    async def pick_target(self, inter, values):
        self.target = values[0]
        await inter.response.edit_message(embed=self.embed(), view=self)

    async def pick_channel(self, inter, ids):
        if ids:
            S["channels"][self.target] = ids[0]
            save_settings()
        await inter.response.edit_message(embed=self.embed(), view=self)

    async def pick_roles(self, inter, ids):
        S["staff_roles"] = ids
        save_settings()
        await inter.response.edit_message(embed=self.embed(), view=self)


# ───── Эмодзи ─────

def emoji_embed() -> disnake.Embed:
    lines = [f"{em(k)} `{k}` — {desc}" for k, (_, desc) in EMOJI_SLOTS.items()]
    n = len(_BOT.emojis) if _BOT else 0
    return disnake.Embed(
        title="😀 Премиум-эмодзи", color=main_color(),
        description="\n".join(lines) +
        f"\n\nВыберите место и вставьте эмодзи: `:имя:`, готовый `<:имя:id>` или обычный.\n"
        f"Эмодзи сервера, которые видит бот: **{n}**.\n\n"
        "💡 В любых текстах (оформление, описания призов) можно писать `:имя:` — бот сам подставит эмодзи сервера.\n"
        "⚠️ В названиях полей анкеты (окно Discord) и на кнопках-подписях кастомные эмодзи как текст не показываются — "
        "они работают в сообщениях, эмбедах и на значках кнопок.")


class EmojiView(AdminView):
    def __init__(self, author_id: int):
        super().__init__(author_id)
        opts = [disnake.SelectOption(label=f"{k} — {desc}"[:100], value=k) for k, (_, desc) in EMOJI_SLOTS.items()]
        self.add_item(HrSelect(self.pick, placeholder="Выберите, где заменить эмодзи...", options=opts, row=0))
        self.add_item(HrButton(self.upload, label="Загрузить из папки emojis", style=disnake.ButtonStyle.success, emoji="📥", row=1))
        self.add_item(HrButton(self.d_stickers, label="Загрузить стикеры", style=disnake.ButtonStyle.success, emoji="🖼️", row=1))
        self.add_item(HrButton(self.show, label="Эмодзи сервера", style=disnake.ButtonStyle.primary, emoji="📋", row=1))
        self.add_item(HrButton(self.reset, label="Сбросить все", style=disnake.ButtonStyle.danger, emoji="♻️", row=1))
        self.add_item(_back_button(author_id))

    async def pick(self, inter, values):
        await inter.response.send_modal(EmojiModal(values[0]))

    async def upload(self, inter):
        await inter.response.defer(ephemeral=True)
        await start_upload(inter)

    async def d_stickers(self, inter):
        await inter.response.defer(ephemeral=True)
        await start_upload(inter, upload_stickers, "стикеры")

    async def show(self, inter):
        items = [f"{e} `:{e.name}:`" for e in (_BOT.emojis if _BOT else [])]
        if not items:
            return await inter.response.send_message("У бота нет доступных эмодзи. Загрузите их на сервер.", ephemeral=True)
        chunks, cur = [], ""
        for it in items:          # лимит Discord — 2000 символов на сообщение
            if len(cur) + len(it) + 2 > 1900:
                chunks.append(cur)
                cur = ""
            cur += it + "  "
        if cur:
            chunks.append(cur)
        await inter.response.send_message(chunks[0], ephemeral=True)
        for c in chunks[1:6]:
            await inter.followup.send(c, ephemeral=True)

    async def reset(self, inter):
        S["emoji"] = {}
        save_settings()
        await inter.response.edit_message(embed=emoji_embed(), view=EmojiView(inter.author.id))


class EmojiModal(disnake.ui.Modal):
    def __init__(self, slot: str):
        self.slot = slot
        super().__init__(title=f"Эмодзи: {slot}"[:45], custom_id=f"hr:emoji:{slot}", components=[
            disnake.ui.TextInput(label="Эмодзи (:имя: / <:имя:id> / обычное)", custom_id="emoji",
                                 value=S["emoji"].get(slot) or EMOJI_SLOTS[slot][0], max_length=100)])

    async def callback(self, inter: disnake.ModalInteraction):
        raw = inter.text_values["emoji"].strip()
        val = E(raw)
        if _EMOJI_RE.fullmatch(val):
            return await inter.response.send_message(f"❌ Эмодзи `{raw}` не найдено среди эмодзи сервера.", ephemeral=True)
        if not (_CUSTOM_RE.match(val) or (val and not val.startswith(":"))):
            return await inter.response.send_message("❌ Не похоже на эмодзи.", ephemeral=True)
        S["emoji"][self.slot] = val
        save_settings()
        await _refresh(inter, emoji_embed(), EmojiView(inter.author.id))


# ───── Публикация ─────

def publish_embed() -> disnake.Embed:
    return disnake.Embed(
        title="🚀 Публикация панелей", color=main_color(),
        description=("Кнопки отправят (или обновят) панели в выбранных каналах. Старая панель удаляется автоматически.\n\n"
                     "• **Панель заявки** — баннер, текст и кнопка «Подать заявку»\n"
                     "• **Рекрут-центр** — баллы, приглашения, магазин, топ\n\n"
                     f"Баннер: {'✅ загружен' if os.path.exists(BANNER_PATH) else '❌ нет файла (команда `/hr_banner`)'}\n"
                     f"Видео под заявкой: {'✅ загружено' if video_path() else '—'} (команда `/hr_video`)"))


async def publish_panel(kind: str):
    """Возвращает (ok, текст)."""
    ch = await get_channel(chan_id("panel" if kind == "apply" else "points_panel"))
    if not isinstance(ch, disnake.TextChannel):
        return False, "Канал не настроен: «Каналы и роли»."
    old = S["published"].get(kind, [0, 0])
    if old and old[0]:
        oc = await get_channel(old[0])
        if oc:
            for mid in old[1:]:
                if mid:
                    try:
                        await (await oc.fetch_message(mid)).delete()
                    except Exception:
                        pass
    emb = apply_embed() if kind == "apply" else points_embed()
    warn = ""
    gif = None
    if kind == "apply" and video_path():
        try:
            gif = await media_gif(ch.guild.filesize_limit)
        except Exception as e:
            warn = f" ⚠️ Видео не добавлено: {e}."
    if gif:
        # GIF в эмбеде автоматически играет и стоит под текстом; кнопка — сразу под карточкой
        emb.set_image(url="attachment://media.gif")
        file = disnake.File(gif, filename="media.gif")
    else:
        file = put_banner(emb)
    view = ApplyView() if kind == "apply" else PointsView()
    try:
        msg = await ch.send(embed=emb, file=file, view=view) if file else await ch.send(embed=emb, view=view)
    except disnake.Forbidden:
        return False, f"Нет прав писать в {ch.mention} (нужны: отправка сообщений, встраивание ссылок, прикрепление файлов)."
    S["published"][kind] = [ch.id, msg.id]
    save_settings()
    return True, f"Опубликовано в {ch.mention}: {msg.jump_url}{warn}"


class PublishView(AdminView):
    def __init__(self, author_id: int):
        super().__init__(author_id)
        self.add_item(HrButton(self.apply, label="Панель заявки", style=disnake.ButtonStyle.success, emoji="📝", row=0))
        self.add_item(HrButton(self.points, label="Рекрут-центр", style=disnake.ButtonStyle.success, emoji="💎", row=0))
        self.add_item(HrButton(self.both, label="Обе панели", style=disnake.ButtonStyle.primary, emoji="🚀", row=0))
        self.add_item(_back_button(author_id))

    async def both(self, inter):
        await inter.response.defer(ephemeral=True)
        out = []
        for kind in ("apply", "points"):
            ok, text = await publish_panel(kind)
            out.append(("✅ " if ok else "❌ ") + text)
        await inter.followup.send("\n".join(out), ephemeral=True)

    async def apply(self, inter):
        await inter.response.defer(ephemeral=True)
        ok, text = await publish_panel("apply")
        await inter.followup.send(("✅ " if ok else "❌ ") + text, ephemeral=True)

    async def points(self, inter):
        await inter.response.defer(ephemeral=True)
        ok, text = await publish_panel("points")
        await inter.followup.send(("✅ " if ok else "❌ ") + text, ephemeral=True)


# ═════════════════════════ 4. COG ═════════════════════════

class HallRecruit(commands.Cog):
    def __init__(self, bot: commands.Bot):
        global _BOT
        self.bot = bot
        _BOT = bot
        db.register_accept_hook("hall_recruit", self.on_accept)
        if bot.is_ready():
            self._add_views()
        self._views_added = bot.is_ready()

    def cog_unload(self):
        db.unregister_accept_hook("hall_recruit")

    def _add_views(self):
        for v in (ApplyView(), CardView(), PointsView(), RefView(), OrderView()):
            self.bot.add_view(v)

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._views_added:
            self._add_views()
            self._views_added = True

    # хук: вызывается из database.update_application_status(..., 'accepted', ...)
    async def on_accept(self, app_id: int, reviewer_id: Optional[int]):
        app = await db.get_application_by_id(app_id)
        if not app:
            return
        pts = S["points"]["accept"]
        reviewer = app.get("reviewer_id") or reviewer_id
        if reviewer and pts > 0 and reviewer != app["user_id"]:
            bal = await db.add_points(reviewer, pts, f"Принята заявка #{app_id} ({app['nick']})", ref=f"accept:{app_id}")
            if bal is not None:
                await dm(reviewer, X("accept_points_title"), X("accept_points_desc", nick=app['nick'], static=app['static_id'], pts=pts, bal=bal), color=cfg_color("success_color", 0x2ECC71))
        ref = await db.find_active_referral_by_static(app["static_id"], ("verified",))
        if ref:
            await pay_referral(ref, app)

    # ───── команды ─────

    @commands.slash_command(name="hr", description="Рекрут-центр: настройки анкеты, баллов, магазина и эмодзи")
    async def hr(self, inter: disnake.ApplicationCommandInteraction):
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Только для администрации.", ephemeral=True)
        await inter.response.send_message(embed=dash_embed(), view=DashView(inter.author.id), ephemeral=True)

    @commands.slash_command(name="hr_banner", description="Загрузить баннер для панелей (картинка)")
    async def hr_banner(self, inter: disnake.ApplicationCommandInteraction,
                        image: disnake.Attachment = commands.Param(description="Картинка баннера (PNG/JPG)")):
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Только для администрации.", ephemeral=True)
        if not (image.content_type or "").startswith("image/"):
            return await inter.response.send_message("❌ Нужна картинка (PNG/JPG).", ephemeral=True)
        os.makedirs(ASSETS_DIR, exist_ok=True)
        await image.save(BANNER_PATH)
        await inter.response.send_message("✅ Баннер сохранён. В `/hr` → «Опубликовать» обновите панели.", ephemeral=True)

    @commands.slash_command(name="hr_video", description="Видео/GIF под текстом панели заявки")
    async def hr_video(self, inter: disnake.ApplicationCommandInteraction,
                       video: Optional[disnake.Attachment] = commands.Param(default=None, description="Видео или GIF (mp4/mov/webm/gif)"),
                       link: Optional[str] = commands.Param(default=None, description="Или ссылка на видео (YouTube и т.п.)"),
                       remove: bool = commands.Param(default=False, description="Убрать видео")):
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Только для администрации.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        d = S["design"]
        if remove:
            _clear_video_files()
            d["video_file"] = ""
            d["video_url"] = ""
            save_settings()
            return await inter.followup.send("✅ Видео убрано. Обновите панель: `/hr` → «Опубликовать».", ephemeral=True)
        if video:
            ext = os.path.splitext(video.filename or "")[1].lower()
            ctype = video.content_type or ""
            if ext not in VIDEO_EXTS or not (ctype.startswith("video/") or ctype == "image/gif"):
                return await inter.followup.send("❌ Нужен файл: gif, mp4, mov или webm.", ephemeral=True)
            limit = inter.guild.filesize_limit if inter.guild else 25 * 1024 * 1024
            if video.size > limit:
                return await inter.followup.send(
                    f"❌ Файл {video.size // 1048576} МБ, а лимит сервера {limit // 1048576} МБ. Сожмите видео или дайте ссылку (`link`).",
                    ephemeral=True)
            os.makedirs(ASSETS_DIR, exist_ok=True)
            _clear_video_files()
            await video.save(os.path.join(ASSETS_DIR, "video" + ext))
            d["video_file"] = "video" + ext
            d["video_url"] = ""
            save_settings()
            return await inter.followup.send("✅ Видео сохранено — оно появится под заявкой. `/hr` → «Опубликовать» → «Панель заявки».", ephemeral=True)
        if link:
            if not link.startswith(("http://", "https://")):
                return await inter.followup.send("❌ Ссылка должна начинаться с https://", ephemeral=True)
            _clear_video_files()
            d["video_file"] = ""
            d["video_url"] = link.strip()
            save_settings()
            return await inter.followup.send("✅ Ссылка на видео сохранена. Обновите панель: `/hr` → «Опубликовать».", ephemeral=True)
        await inter.followup.send("ℹ️ Прикрепите видеофайл (`video`), укажите `link` или поставьте `remove`.", ephemeral=True)

    @commands.slash_command(name="hr_emoji_upload", description="Загрузить эмодзи из папки emojis на сервер")
    async def hr_emoji_upload(self, inter: disnake.ApplicationCommandInteraction):
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Только для администрации.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        await start_upload(inter)

    @commands.slash_command(name="hr_sticker_upload", description="Загрузить стикеры из папки stickers на сервер")
    async def hr_sticker_upload(self, inter: disnake.ApplicationCommandInteraction,
                                emoji: str = commands.Param(default="⭐", description="Эмодзи-ярлык стикеров (для поиска)")):
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Только для администрации.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        await start_upload(inter, lambda g, p: upload_stickers(g, p, emoji), "стикеры")

    @commands.slash_command(name="points", description="Баланс баллов рекрута")
    async def points(self, inter: disnake.ApplicationCommandInteraction,
                     user: Optional[disnake.Member] = commands.Param(default=None, description="Чей баланс показать")):
        target = user or inter.author
        pts = await db.get_points(target.id)
        emb = disnake.Embed(title=f"{em('points')} Баллы: {target.display_name}", color=main_color(),
                            description=f"Баланс: **{pts['points']}** б.\nЗаработано: `{pts['earned']}` • потрачено: `{pts['spent']}`")
        await inter.response.send_message(embed=emb, ephemeral=True)

    @commands.slash_command(name="points_give", description="Выдать или снять баллы (руководство)")
    async def points_give(self, inter: disnake.ApplicationCommandInteraction,
                          user: disnake.Member = commands.Param(description="Кому"),
                          amount: int = commands.Param(description="Сколько (минус — снять)", ge=-10000, le=10000),
                          reason: str = commands.Param(default="Корректировка руководства", description="Причина")):
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Только для администрации.", ephemeral=True)
        if amount == 0:
            return await inter.response.send_message("❌ Укажите ненулевое число.", ephemeral=True)
        if amount > 0:
            bal = await db.add_points(user.id, amount, f"{reason} ({inter.author.display_name})")
        else:
            bal = await db.spend_points(user.id, -amount, f"{reason} ({inter.author.display_name})")
            if bal is None:
                return await inter.response.send_message("❌ У игрока недостаточно баллов.", ephemeral=True)
        await inter.response.send_message(f"✅ {user.mention}: {amount:+d} б. Баланс: **{bal}**", ephemeral=True)
        if amount > 0:
            await dm(user.id, f"{em('points')} Вам начислены баллы", f"**+{amount}** б. — {reason}\nБаланс: **{bal}** б.", banner=False)


def setup(bot: commands.Bot):
    print("🛠 hall_recruit: версия с исправлением обзвона/ника/ветки (v2)", flush=True)
    bot.add_cog(HallRecruit(bot))
