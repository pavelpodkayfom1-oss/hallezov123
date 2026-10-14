"""
Картинка для сбора: нейросеть рисует фон, Pillow накладывает чёткий текст
(название, время, место). Нейросети плохо пишут русские буквы, поэтому текст
всегда рисуется самим ботом, а нейросеть отвечает только за сцену.

Настройка через переменные окружения (.env):
  EVENT_ART_PROVIDER   gemini       (нужен GEMINI_API_KEY; включается сам, если ключ задан)
                       pollinations (бесплатно, без ключа; по умолчанию без Gemini-ключа)
                       openai       (нужен OPENAI_API_KEY)
                       none         (без нейросети: ваш баннер размытым фоном)
                       Если выбранный сервис не ответил, бот пробует Pollinations,
                       а затем собирает запасную картинку на вашем баннере.
  GEMINI_API_KEY       ключ Google Gemini (хранить ТОЛЬКО в .env, не в репозитории)
  GEMINI_IMAGE_MODEL   по умолчанию пробует gemini-2.5-flash-image, затем gemini-3.1-flash-image
  OPENAI_API_KEY       ключ OpenAI (только для provider=openai)
  OPENAI_IMAGE_MODEL   по умолчанию gpt-image-1-mini
  OPENAI_IMAGE_QUALITY по умолчанию medium (low / medium / high)
  EVENT_ART_TIMEOUT    сколько секунд ждать нейросеть (по умолчанию 45)

Функция make_event_art никогда не падает: при любой ошибке нейросети собирается
запасная картинка, так что сбор в любом случае уходит с оформлением.
"""
import asyncio
import base64
import hashlib
import io
import os
import sys
import time
import urllib.parse
from typing import Optional

import aiohttp
import disnake
from PIL import Image, ImageDraw, ImageFilter

from utils.card_image import _font, _fit

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART_DIR = os.path.join(BASE_DIR, "event_art")
W, H = 1280, 512
KEEP_DAYS = 30

# (ключевые слова в названии/описании сбора) -> сцена для нейросети
_SCENES = [
    (("капт", "capt", "война", "войн", "war", "зона", "стрелк", "бой"),
     "two rival crime families facing off at dusk in an industrial district of a sunny "
     "California-style city, armored SUVs, tense standoff, dramatic orange sky"),
    (("дроп", "drop", "груз", "поставк", "контейнер", "ящик"),
     "cargo airdrop with crates and a helicopter above a coastal warehouse dock at golden hour"),
    (("взп",),
     "gang clash in front of a big warehouse at night, headlights and smoke"),
    (("гонк", "race", "заезд", "дрифт", "тачк"),
     "street racing supercars on a neon-lit city highway at night, motion blur"),
    (("бизнес", "завод", "склад", "ферм", "порт"),
     "industrial port with containers and trucks at sunset, hazy atmosphere"),
    (("праздник", "день рожд", "турнир", "ивент", "вечеринк"),
     "luxury rooftop party over a glittering city at night, warm lights, celebration"),
]
_DEFAULT_SCENE = ("crime family gathering at night in front of a luxury mansion, black SUVs "
                  "with headlights on, palm trees, cinematic")
_STYLE = ("wide cinematic banner, dramatic lighting, video game key art, highly detailed, "
          "faceless silhouettes only, no text, no letters, no logos, no watermark")


def _scene_for(text: str) -> str:
    low = text.lower()
    for words, scene in _SCENES:
        if any(w in low for w in words):
            return scene
    return _DEFAULT_SCENE


# ───────────────────────── хранение ─────────────────────────

def art_filename(call_id: int) -> str:
    return f"sbor_{call_id}.jpg"


def _art_path(call_id: int) -> str:
    return os.path.join(ART_DIR, art_filename(call_id))


def has_art(call_id) -> bool:
    try:
        return os.path.exists(_art_path(int(call_id)))
    except Exception:
        return False


def save_art(call_id: int, data: bytes):
    os.makedirs(ART_DIR, exist_ok=True)
    with open(_art_path(call_id), "wb") as f:
        f.write(data)
    _cleanup()


def art_file(call_id: int) -> Optional[disnake.File]:
    """Свежий объект File для отправки (на каждую отправку нужен новый)."""
    try:
        with open(_art_path(int(call_id)), "rb") as f:
            data = f.read()
    except Exception:
        return None
    return disnake.File(io.BytesIO(data), filename=art_filename(int(call_id)))


def _cleanup():
    cutoff = time.time() - KEEP_DAYS * 86400
    try:
        for name in os.listdir(ART_DIR):
            p = os.path.join(ART_DIR, name)
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.remove(p)
    except Exception:
        pass


# ───────────────────────── нейросеть ─────────────────────────

def _log(msg: str):
    print(f"🎨 event_art: {msg}", file=sys.stderr, flush=True)


async def _fetch_pollinations(session: aiohttp.ClientSession, prompt: str, seed: int) -> Optional[bytes]:
    url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt, safe="")
           + f"?width={W}&height={H}&model=flux&nologo=true&seed={seed}")
    async with session.get(url) as r:
        if r.status != 200:
            _log(f"pollinations ответил {r.status}")
            return None
        data = await r.read()
    return data if len(data) > 5000 else None


async def _fetch_openai(session: aiohttp.ClientSession, prompt: str) -> Optional[bytes]:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        _log("provider=openai, но OPENAI_API_KEY не задан")
        return None
    payload = {
        "model": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1-mini"),
        "prompt": prompt,
        "size": "1536x1024",
        "quality": os.getenv("OPENAI_IMAGE_QUALITY", "medium"),
        "n": 1,
    }
    async with session.post("https://api.openai.com/v1/images/generations",
                            headers={"Authorization": f"Bearer {key}"}, json=payload) as r:
        if r.status != 200:
            body = (await r.text())[:200]
            _log(f"openai ответил {r.status}: {body}")
            return None
        j = await r.json()
    b64 = (j.get("data") or [{}])[0].get("b64_json")
    return base64.b64decode(b64) if b64 else None


async def _fetch_gemini(session: aiohttp.ClientSession, prompt: str) -> Optional[bytes]:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        _log("provider=gemini, но GEMINI_API_KEY не задан")
        return None
    env_model = os.getenv("GEMINI_IMAGE_MODEL", "").strip()
    models = [env_model] if env_model else ["gemini-2.5-flash-image", "gemini-3.1-flash-image"]
    base = {"contents": [{"parts": [{"text": prompt}]}]}
    variants = [
        {**base, "generationConfig": {"responseModalities": ["IMAGE"],
                                      "imageConfig": {"aspectRatio": "21:9"}}},
        {**base, "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]}},
    ]
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for payload in variants:
            async with session.post(url, headers={"x-goog-api-key": key}, json=payload) as r:
                if r.status == 200:
                    j = await r.json()
                    for cand in j.get("candidates") or []:
                        for part in (cand.get("content") or {}).get("parts") or []:
                            d = part.get("inlineData") or part.get("inline_data")
                            if d and d.get("data"):
                                return base64.b64decode(d["data"])
                    _log(f"gemini ({model}) вернул ответ без картинки")
                    continue
                body = (await r.text())[:200]
                _log(f"gemini ({model}) ответил {r.status}: {body}")
                if r.status in (401, 403, 429):   # ключ/доступ/лимит: другая модель не поможет
                    return None
                if r.status == 404:                # нет такой модели — пробуем следующую
                    break
    return None


async def _generate_background(title: str, info: str, seed: int) -> Optional[bytes]:
    default = "gemini" if os.getenv("GEMINI_API_KEY", "").strip() else "pollinations"
    provider = os.getenv("EVENT_ART_PROVIDER", default).strip().lower() or default
    if provider in ("none", "off", "0"):
        return None
    chain = {"gemini": ["gemini", "pollinations"], "openai": ["openai", "pollinations"]}.get(
        provider, ["pollinations"])
    prompt = f"{_scene_for(title + ' ' + info)}, {_STYLE}"
    try:
        timeout = aiohttp.ClientTimeout(total=float(os.getenv("EVENT_ART_TIMEOUT", "45")))
    except ValueError:
        timeout = aiohttp.ClientTimeout(total=45)
    started = time.time()
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for name in chain:
            if time.time() - started > 60:      # общий бюджет ожидания
                break
            try:
                if name == "gemini":
                    data = await _fetch_gemini(session, prompt)
                elif name == "openai":
                    data = await _fetch_openai(session, prompt)
                else:
                    data = await _fetch_pollinations(session, prompt, seed)
            except Exception as e:
                _log(f"{name} недоступен: {type(e).__name__}: {e}")
                data = None
            if data:
                return data
    return None


# ───────────────────────── сборка картинки ─────────────────────────

def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    sw, sh = img.size
    scale = max(w / sw, h / sh)
    img = img.resize((max(w, int(sw * scale)), max(h, int(sh * scale))), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def _fallback_background() -> Image.Image:
    """Ваш баннер, сильно размытый и затемнённый (чтобы логотип не спорил с текстом)."""
    for name in ("banner_image.png", "banner_image.jpg", "banner_image.jpeg", "banner_image.webp"):
        p = os.path.join(BASE_DIR, name)
        if os.path.exists(p):
            try:
                img = _cover(Image.open(p).convert("RGB"), W, H)
                img = img.filter(ImageFilter.GaussianBlur(7))
                return Image.blend(img, Image.new("RGB", (W, H), (0, 0, 0)), 0.45)
            except Exception:
                break
    img = Image.new("RGB", (W, H), (20, 8, 10))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(70 * (1 - t) + 10), int(14 * (1 - t) + 6), int(20 * (1 - t) + 8)))
    return img


def _wrap(draw, text: str, font, max_w: int, max_lines: int):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if draw.textlength(test, font=font) <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    truncated = len(lines) > max_lines
    if truncated:
        lines = lines[:max_lines]
        lines[-1] = _fit(draw, lines[-1] + "…", font, max_w)
    return lines, truncated


def _compose(bg: Optional[bytes], title: str, time_str: str, place: str,
             guild_name: str, accent=(214, 32, 48)) -> bytes:
    img = None
    if bg:
        try:
            img = _cover(Image.open(io.BytesIO(bg)).convert("RGB"), W, H)
        except Exception as e:
            _log(f"картинка нейросети не открылась: {e}")
    if img is None:
        img = _fallback_background()

    # затемнение слева (под текст) и снизу
    side = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(side)
    for x in range(W):
        a = int(215 * max(0.0, 1 - x / (W * 0.78)) ** 1.2)
        sd.line([(x, 0), (x, H)], fill=(0, 0, 0, a))
    bottom = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bottom)
    for y in range(int(H * 0.55), H):
        a = int(140 * (y - H * 0.55) / (H * 0.45))
        bd.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    img = Image.alpha_composite(img.convert("RGBA"), side)
    img = Image.alpha_composite(img, bottom).convert("RGB")

    d = ImageDraw.Draw(img)
    x0, max_w = 64, int(W * 0.66)

    f_label = _font(26, bold=True)
    label = f"{guild_name.upper()}  •  СБОР"
    d.text((x0, 52), _fit(d, label, f_label, max_w), font=f_label, fill=(255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0))
    d.rectangle([x0, 96, x0 + 96, 102], fill=accent)

    title = (title or "СБОР").upper()
    size = 96
    while True:
        f_title = _font(size, bold=True)
        lines, truncated = _wrap(d, title, f_title, max_w, 2)
        fits = all(d.textlength(l, font=f_title) <= max_w for l in lines)
        if (fits and not truncated) or size <= 44:
            break
        size -= 6
    y = 124
    for line in lines:
        d.text((x0, y), line, font=f_title, fill=(255, 255, 255), stroke_width=4, stroke_fill=(0, 0, 0))
        y += int(size * 1.08)

    base_y = H - 150
    if time_str:
        f_small = _font(22, bold=True)
        f_time = _font(52, bold=True)
        t = _fit(d, time_str, f_time, int(W * 0.5))
        tw = int(d.textlength(t, font=f_time))
        d.rounded_rectangle([x0, base_y, x0 + tw + 56, base_y + 86], radius=14, fill=accent)
        d.text((x0 + 28, base_y + 8), "ВРЕМЯ СБОРА", font=f_small, fill=(255, 220, 224))
        d.text((x0 + 28, base_y + 30), t, font=f_time, fill=(255, 255, 255))
        base_y += 104
    if place:
        f_place = _font(30, bold=True)
        d.text((x0, base_y), _fit(d, f"МЕСТО: {place}", f_place, int(W * 0.7)), font=f_place,
               fill=(255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0))

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88, optimize=True)
    return out.getvalue()


async def make_event_art(title: str, time_str: str = "", info: str = "",
                         guild_name: str = "Hallez FAMQ", accent=None) -> Optional[bytes]:
    """JPEG-байты картинки сбора. Не бросает исключений: в худшем случае вернёт None."""
    try:
        seed = int(hashlib.md5(f"{title}{time_str}{time.time()}".encode()).hexdigest()[:8], 16)
        bg = await _generate_background(title or "", info or "", seed)
        place = (info or "").strip().replace("\n", " ")
        return await asyncio.to_thread(
            _compose, bg, title or "", time_str or "", place, guild_name or "Hallez FAMQ",
            accent or (214, 32, 48))
    except Exception as e:
        _log(f"не удалось собрать картинку: {type(e).__name__}: {e}")
        return None
