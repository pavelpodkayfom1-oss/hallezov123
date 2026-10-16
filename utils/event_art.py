"""
Картинка для сбора: нейросеть рисует УНИКАЛЬНЫЙ фон под каждый сбор, Pillow
накладывает чёткий текст (название, время, место). Нейросети плохо пишут русские
буквы, поэтому текст всегда рисует сам бот, а нейросеть отвечает только за сцену.

Настройка через переменные окружения (на хостинге рядом с BOT_TOKEN):
  GEMINI_API_KEY       ключ Google Gemini (если задан, Gemini включается сам)
  GEMINI_IMAGE_MODEL   необязательно: принудительно выбрать модель
                       (иначе бот сам спрашивает у Google список доступных моделей)
  EVENT_ART_PROVIDER   gemini | pollinations | openai | none
  OPENAI_API_KEY / OPENAI_IMAGE_MODEL / OPENAI_IMAGE_QUALITY   для provider=openai
  EVENT_ART_TIMEOUT    секунд на один запрос к нейросети (по умолчанию 45)

Порядок: выбранный сервис -> Pollinations -> запасная картинка на вашем баннере.
make_event_art никогда не падает и возвращает Art с причиной, если нейросеть не
сработала (её показывают администратору в Discord).
"""
import asyncio
import base64
import hashlib
import io
import os
import random
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import List, Optional

import aiohttp
import disnake
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageStat

from utils.card_image import _font as _fallback_font

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART_DIR = os.path.join(BASE_DIR, "event_art")
FONT_PATH = os.path.join(BASE_DIR, "assets", "fonts", "Lora-Variable.ttf")
W, H = 1280, 512
KEEP_DAYS = 30

GOLD = (226, 190, 120)

# ───────────────────────── описание сцены ─────────────────────────

# (ключевые слова) -> сцена; подмешивается к тому, что написал руководитель
_SCENES = [
    (("капт", "capt", "война", "войн", "war", "зона", "стрелк", "бой"),
     "two rival crime families facing off in an industrial district of a sunny California-style "
     "city, armored SUVs, a tense standoff"),
    (("дроп", "drop", "груз", "поставк", "контейнер", "ящик"),
     "a cargo airdrop with parachuting crates and a helicopter above a coastal warehouse dock"),
    (("взп",),
     "a gang clash in front of a huge warehouse, headlights cutting through smoke"),
    (("гонк", "race", "заезд", "дрифт", "тачк"),
     "street racing supercars on a city highway, speed and motion blur"),
    (("бизнес", "завод", "склад", "ферм", "порт"),
     "an industrial port with containers, cranes and trucks"),
    (("праздник", "день рожд", "турнир", "ивент", "вечеринк"),
     "a luxury rooftop celebration above a glittering city"),
]
# Освещение ТОЛЬКО светлое и читаемое: тёмные «полночь/гроза» давали чёрную картинку, где ничего не видно.
_TIMES = [
    "golden hour sunset with warm glowing light",
    "bright sunny afternoon with a clear blue sky",
    "vivid sunset with orange and pink sky",
    "blue hour dusk with bright glowing city lights and a still-visible sky",
    "clear early morning with soft warm sun rays",
]
_CAMERAS = [
    "low-angle wide shot", "cinematic shot from slightly above", "aerial drone view",
    "street-level wide shot", "symmetrical hero shot",
]
_PALETTES = [
    "warm orange and teal with crimson accents",
    "vibrant saturated colors with crimson accents",
    "rich gold and deep blue with red accents",
]


def _scene_for(text: str) -> str:
    """Подсказка по обстановке, только если в тексте нашлось знакомое слово. Иначе — пусто:
    раньше подставлялся один и тот же «особняк с чёрными джипами», и картинка не совпадала с темой."""
    low = text.lower()
    for words, scene in _SCENES:
        if any(w in low for w in words):
            return scene
    return ""


def build_prompt(title: str, info: str) -> str:
    """Каждый раз новый промпт: ГЛАВНОЕ — то, что написал руководитель, плюс случайные свет, ракурс, палитра."""
    rnd = random.Random()
    theme = " ".join(x for x in (title.strip(), info.strip()) if x)[:240]
    hint = _scene_for(theme)
    return (
        f"A bright, vivid, well-lit, highly detailed cinematic illustration for a video-game crime-family "
        f"event announcement. "
        f"MAIN SUBJECT - it must be instantly recognizable and clearly visible: the organizer wrote "
        f"(in Russian) \"{theme}\". Translate it and depict exactly what it means, literally and "
        f"prominently, in the center-right of the frame. "
        + (f"Setting suggestion: {hint}. " if hint else "")
        + f"Lighting: {rnd.choice(_TIMES)}. Camera: {rnd.choice(_CAMERAS)}. "
        f"Color grading: {rnd.choice(_PALETTES)}. "
        f"Ultra-wide 21:9 composition: the main subject fills the right two thirds, the left third "
        f"is less busy (room for a title) but still well lit, never black. "
        f"Bright exposure, sharp focus, strong contrast, no dark or muddy shadows, premium poster quality, "
        f"faceless characters only. "
        f"Absolutely no text, letters, numbers, logos, watermarks or interface elements."
    )


# ───────────────────────── результат ─────────────────────────

@dataclass
class Art:
    data: Optional[bytes] = None
    source: str = "fallback"          # gemini | pollinations | openai | fallback
    notes: List[str] = field(default_factory=list)

    @property
    def ai_ok(self) -> bool:
        return self.source in ("gemini", "pollinations", "openai")

    def problem_text(self) -> str:
        """Понятное объяснение для администратора, почему нейросеть не сработала."""
        if self.ai_ok:
            return ""
        lines = ["Картинку нарисовал запасной вариант (нейросеть не сработала)."]
        lines += [f"• {n}" for n in self.notes[:6]] or ["• нейросеть отключена (EVENT_ART_PROVIDER=none)"]
        return "\n".join(lines)[:1800]


def _log(msg: str):
    print(f"🎨 event_art: {msg}", file=sys.stderr, flush=True)


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


# ───────────────────────── нейросети ─────────────────────────

_IMG_MAGIC = (b"\x89PNG", b"\xff\xd8", b"RIFF")


def _find_image(obj) -> Optional[bytes]:
    """Ищет картинку (base64) в ЛЮБОЙ структуре ответа: inlineData, output_image, steps и т.д."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and len(v) > 2000 and k in ("data", "b64_json", "image", "bytesBase64Encoded"):
                try:
                    raw = base64.b64decode(v, validate=False)
                    if raw.startswith(_IMG_MAGIC):
                        return raw
                except Exception:
                    pass
        for v in obj.values():
            found = _find_image(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_image(v)
            if found:
                return found
    return None


def _snip(text: str, n: int = 170) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:n]


def _explain_http(status: int) -> str:
    return {
        400: "неверный запрос",
        401: "ключ не принят (проверьте GEMINI_API_KEY)",
        403: "нет доступа (регион, лимит или ключ без прав)",
        404: "модель не найдена",
        429: "исчерпан лимит запросов",
    }.get(status, "ошибка сервера")


_GEM = "https://generativelanguage.googleapis.com/v1beta"
_GEM_CACHE = {"ts": 0.0, "models": []}
_GEM_PREFER = ("nano-banana", "3.1-flash-image", "3-pro-image", "flash-image", "image")
_GEM_LAST_RESORT = ["gemini-nano-banana-2.1", "gemini-3.1-flash-image", "gemini-2.5-flash-image"]


class _Stop(Exception):
    """Дальше пробовать Gemini бессмысленно (ключ/доступ/лимит)."""


async def _gemini_models(session, hdr, notes: List[str]) -> List[str]:
    forced = os.getenv("GEMINI_IMAGE_MODEL", "").strip()
    if forced:
        return [forced]
    if _GEM_CACHE["models"] and time.time() - _GEM_CACHE["ts"] < 3600:
        return _GEM_CACHE["models"]
    names, token = [], None
    for _ in range(4):
        url = f"{_GEM}/models?pageSize=200" + (f"&pageToken={token}" if token else "")
        async with session.get(url, headers=hdr) as r:
            if r.status != 200:
                notes.append(f"Gemini: список моделей, HTTP {r.status} ({_explain_http(r.status)}): "
                             f"{_snip(await r.text())}")
                if r.status in (401, 403, 429):
                    raise _Stop()
                break
            j = await r.json()
        for m in j.get("models", []):
            n = (m.get("name") or "").split("/")[-1]
            low = n.lower()
            methods = m.get("supportedGenerationMethods") or []
            if ("image" in low or "banana" in low) and "imagen" not in low \
                    and ("generateContent" in methods or not methods):
                names.append(n)
        token = j.get("nextPageToken")
        if not token:
            break

    def rank(n: str):
        low = n.lower()
        pref = next((i for i, k in enumerate(_GEM_PREFER) if k in low), len(_GEM_PREFER))
        return (1 if "lite" in low else 0, pref, "preview" in low, n)

    names = sorted(set(names), key=rank)
    if names:
        _GEM_CACHE.update(ts=time.time(), models=names)
        return names
    notes.append("Gemini: в списке моделей нет ни одной для картинок, пробую стандартные названия")
    return list(_GEM_LAST_RESORT)


async def _fetch_gemini(session, prompt: str, notes: List[str]) -> Optional[bytes]:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        notes.append("Gemini: переменная GEMINI_API_KEY не задана (или бот не перезапущен после её добавления)")
        return None
    hdr = {"x-goog-api-key": key}
    try:
        models = await _gemini_models(session, hdr, notes)
        base = {"contents": [{"parts": [{"text": prompt}]}]}
        variants = [
            {**base, "generationConfig": {"responseModalities": ["IMAGE"],
                                          "imageConfig": {"aspectRatio": "21:9"}}},
            {**base, "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]}},
        ]
        for model in models[:3]:
            for payload in variants:
                async with session.post(f"{_GEM}/models/{model}:generateContent", headers=hdr,
                                        json=payload) as r:
                    status = r.status
                    if status == 200:
                        j = await r.json()
                        raw = _find_image(j)
                        if raw:
                            return raw
                        why = (j.get("promptFeedback") or {}).get("blockReason") \
                            or ((j.get("candidates") or [{}])[0].get("finishReason")) or "пустой ответ"
                        notes.append(f"Gemini {model}: картинки в ответе нет ({why})")
                        continue
                    body = _snip(await r.text())
                if status in (401, 403, 429):
                    notes.append(f"Gemini {model}: HTTP {status} ({_explain_http(status)}): {body}")
                    raise _Stop()
                notes.append(f"Gemini {model}: HTTP {status} ({_explain_http(status)}): {body}")
                if status == 404:
                    break

        # запасной путь: новый Interactions API
        if models:
            for rf in ({"type": "image", "aspect_ratio": "21:9"}, None):
                payload = {"model": models[0], "input": prompt}
                if rf:
                    payload["response_format"] = rf
                async with session.post(f"{_GEM}/interactions", headers=hdr, json=payload) as r:
                    if r.status == 200:
                        raw = _find_image(await r.json())
                        if raw:
                            return raw
                        notes.append(f"Gemini interactions ({models[0]}): картинки в ответе нет")
                        break
                    notes.append(f"Gemini interactions ({models[0]}): HTTP {r.status}: {_snip(await r.text())}")
    except _Stop:
        pass
    return None


async def _fetch_pollinations(session, prompt: str, seed: int, notes: List[str]) -> Optional[bytes]:
    short = prompt[:700]
    url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(short, safe="")
           + f"?width={W}&height={H}&model=flux&nologo=true&seed={seed}")
    async with session.get(url) as r:
        if r.status != 200:
            notes.append(f"Pollinations: HTTP {r.status} ({_explain_http(r.status)})")
            return None
        data = await r.read()
    if len(data) < 5000 or not data.startswith(_IMG_MAGIC):
        notes.append("Pollinations: вернул не картинку")
        return None
    return data


async def _fetch_openai(session, prompt: str, notes: List[str]) -> Optional[bytes]:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        notes.append("OpenAI: переменная OPENAI_API_KEY не задана")
        return None
    payload = {
        "model": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1-mini"),
        "prompt": prompt, "size": "1536x1024",
        "quality": os.getenv("OPENAI_IMAGE_QUALITY", "medium"), "n": 1,
    }
    async with session.post("https://api.openai.com/v1/images/generations",
                            headers={"Authorization": f"Bearer {key}"}, json=payload) as r:
        if r.status != 200:
            notes.append(f"OpenAI: HTTP {r.status}: {_snip(await r.text())}")
            return None
        return _find_image(await r.json())


async def _generate_background(title: str, info: str, notes: List[str]):
    """-> (bytes | None, источник)"""
    default = "gemini" if os.getenv("GEMINI_API_KEY", "").strip() else "pollinations"
    provider = os.getenv("EVENT_ART_PROVIDER", default).strip().lower() or default
    if provider in ("none", "off", "0"):
        return None, "fallback"
    chain = {"gemini": ["gemini", "pollinations"], "openai": ["openai", "pollinations"]}.get(
        provider, ["pollinations"])
    prompt = build_prompt(title, info)
    seed = int(hashlib.md5(f"{prompt}{time.time()}".encode()).hexdigest()[:8], 16)
    try:
        timeout = aiohttp.ClientTimeout(total=float(os.getenv("EVENT_ART_TIMEOUT", "45")))
    except ValueError:
        timeout = aiohttp.ClientTimeout(total=45)

    started = time.time()
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for name in chain:
            if time.time() - started > 75:
                break
            try:
                if name == "gemini":
                    data = await _fetch_gemini(session, prompt, notes)
                elif name == "openai":
                    data = await _fetch_openai(session, prompt, notes)
                else:
                    data = await _fetch_pollinations(session, prompt, seed, notes)
            except Exception as e:
                notes.append(f"{name}: {type(e).__name__}: {_snip(str(e), 120)}")
                data = None
            if data:
                return data, name
    return None, "fallback"


# ───────────────────────── текст и вёрстка ─────────────────────────

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FFFF☀-➿⌀-⏿⬀-⯿️‍⃣]+", flags=re.UNICODE)


def clean_text(text: str) -> str:
    """Убирает эмодзи (шрифт их не рисует, получились бы квадраты) и лишние пробелы."""
    return re.sub(r"\s+", " ", _EMOJI_RE.sub("", text or "")).strip()


def normalize_time(value: str) -> str:
    """'19 00' / '19.00' / '1900' -> '19:00' (хвост вроде 'МСК' сохраняется)."""
    s = clean_text(value)
    m = re.match(r"^(\d{1,2})[\s.:;,\-]?(\d{2})(?!\d)\s*(.*)$", s)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        tail = f" {m.group(3)}" if m.group(3) else ""
        return f"{int(m.group(1)):02d}:{m.group(2)}{tail}"
    return s


def _efont(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    try:
        f = ImageFont.truetype(FONT_PATH, size)
        try:
            f.set_variation_by_axes([700 if bold else 500])
        except Exception:
            pass
        return f
    except Exception:
        return _fallback_font(size, bold=bold)


def _fit(draw, text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


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


def _tracked(draw, xy, text: str, font, fill, tracking: int = 4) -> int:
    """Текст с разрядкой (для мелких подписей). Возвращает ширину."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return int(x - xy[0])


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    sw, sh = img.size
    scale = max(w / sw, h / sh)
    img = img.resize((max(w, int(sw * scale)), max(h, int(sh * scale))), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def _fallback_background() -> Image.Image:
    """Ваш баннер, размытый и затемнённый (логотип не спорит с текстом)."""
    for name in ("banner_image.png", "banner_image.jpg", "banner_image.jpeg", "banner_image.webp"):
        p = os.path.join(BASE_DIR, name)
        if os.path.exists(p):
            try:
                img = _cover(Image.open(p).convert("RGB"), W, H).filter(ImageFilter.GaussianBlur(7))
                return Image.blend(img, Image.new("RGB", (W, H), (0, 0, 0)), 0.30)
            except Exception:
                break
    img = Image.new("RGB", (W, H), (20, 8, 10))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(70 * (1 - t) + 10), int(14 * (1 - t) + 6), int(20 * (1 - t) + 8)))
    return img


def _soft_shadow(base: Image.Image, draw_fn, blur: int = 8, alpha: int = 230) -> Image.Image:
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer), (0, 0, 0, alpha))
    return Image.alpha_composite(base, layer.filter(ImageFilter.GaussianBlur(blur)))


def _compose(bg: Optional[bytes], title: str, time_str: str, place: str,
             guild_name: str, accent=(200, 30, 45)) -> bytes:
    img = None
    if bg:
        try:
            img = _cover(Image.open(io.BytesIO(bg)).convert("RGB"), W, H)
        except Exception as e:
            _log(f"картинка нейросети не открылась: {e}")
    if img is None:
        img = _fallback_background()

    # если нейросеть выдала тёмный кадр — подсвечиваем, иначе после затемнения под текст ничего не видно
    if bg and img is not None:
        try:
            mean = ImageStat.Stat(img.convert("L")).mean[0]
            if mean < 105:
                img = ImageEnhance.Brightness(img).enhance(min(2.2, 120 / max(mean, 12)))
                img = ImageEnhance.Contrast(img).enhance(1.08)
        except Exception:
            pass

    # лёгкое затемнение под текст: слева, снизу и сверху (раньше было вдвое плотнее и «съедало» картинку)
    side = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(side)
    for x in range(W):
        a = int(150 * max(0.0, 1 - x / (W * 0.58)) ** 1.4)
        sd.line([(x, 0), (x, H)], fill=(0, 0, 0, a))
    bottom = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bottom)
    for y in range(int(H * 0.58), H):
        a = int(125 * max(0.0, (y - H * 0.58) / (H * 0.42)) ** 1.4)
        bd.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    top = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    td = ImageDraw.Draw(top)
    for y in range(0, 90):
        td.line([(0, y), (W, y)], fill=(0, 0, 0, int(65 * (1 - y / 90))))
    img = Image.alpha_composite(img.convert("RGBA"), side)
    img = Image.alpha_composite(img, bottom)
    img = Image.alpha_composite(img, top)

    x0 = 72
    max_w = int(W * 0.64)
    probe = ImageDraw.Draw(img)

    # верхняя подпись: бренд, разрядка, золотая линия
    f_label = _efont(21, bold=True)
    label = clean_text(guild_name).upper() or "HALLEZ FAMQ"
    lw = _tracked(probe, (x0, 50), label, f_label, GOLD, tracking=5)
    f_tag = _efont(21, bold=False)
    _tracked(probe, (x0 + lw + 22, 50), "ОБЪЯВЛЕН СБОР", f_tag, (255, 255, 255), tracking=5)
    probe.line([(x0, 90), (x0 + 130, 90)], fill=GOLD, width=2)

    # название: до двух строк, шрифт сам уменьшается
    title = clean_text(title).upper() or "СБОР"
    size = 104
    while True:
        f_title = _efont(size, bold=True)
        lines, truncated = _wrap(probe, title, f_title, max_w, 2)
        fits = all(probe.textlength(l, font=f_title) <= max_w for l in lines)
        if (fits and not truncated) or size <= 46:
            break
        size -= 6
    line_h = int(size * 1.12)
    ty = 112

    def draw_title(d, fill):
        y = ty
        for line in lines:
            d.text((x0, y), line, font=f_title, fill=fill)
            y += line_h

    img = _soft_shadow(img, draw_title, blur=9)
    d = ImageDraw.Draw(img)
    y = ty
    for line in lines:
        d.text((x0, y), line, font=f_title, fill=(255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0))
        y += line_h

    # низ: ВРЕМЯ | МЕСТО
    f_cap = _efont(19, bold=True)
    f_time = _efont(74, bold=True)
    f_place = _efont(34, bold=True)
    by = H - 138
    cx = x0
    if time_str:
        t = _fit(d, normalize_time(time_str), f_time, int(W * 0.40))
        tw = int(d.textlength(t, font=f_time))
        d.rectangle([cx, by + 4, cx + 6, by + 106], fill=accent)
        _tracked(d, (cx + 24, by), "ВРЕМЯ", f_cap, GOLD, tracking=5)
        img = _soft_shadow(img, lambda dd, fill: dd.text((cx + 24, by + 24), t, font=f_time, fill=fill), blur=7)
        d = ImageDraw.Draw(img)
        d.text((cx + 24, by + 24), t, font=f_time, fill=(255, 255, 255))
        cx += 24 + tw + 44
    place = clean_text(place)
    if place:
        if time_str:
            d.line([(cx - 22, by + 8), (cx - 22, by + 104)], fill=(255, 255, 255, 90), width=2)
        pw = max(160, W - 60 - cx)
        _tracked(d, (cx, by), "МЕСТО", f_cap, GOLD, tracking=5)
        plines, _ = _wrap(d, place, f_place, pw, 2)
        for i, pl in enumerate(plines):
            py = by + 30 + i * 42
            img = _soft_shadow(img, lambda dd, fill, pl=pl, py=py: dd.text((cx, py), pl, font=f_place, fill=fill),
                               blur=5, alpha=200)
            d = ImageDraw.Draw(img)
            d.text((cx, py), pl, font=f_place, fill=(255, 255, 255))

    # нижняя полоса в цвет семьи
    d.rectangle([0, H - 6, W, H], fill=accent)

    out = io.BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=90, optimize=True)
    return out.getvalue()


async def make_event_art(title: str, time_str: str = "", info: str = "",
                         guild_name: str = "Hallez FAMQ", accent=None) -> Art:
    """Всегда возвращает Art (при полном провале data=None). Исключений не бросает."""
    art = Art()
    try:
        bg, art.source = await _generate_background(clean_text(title), clean_text(info), art.notes)
        for n in art.notes:
            _log(n)
        place = (info or "").strip().replace("\n", " ")
        art.data = await asyncio.to_thread(
            _compose, bg, title or "", time_str or "", place, guild_name or "Hallez FAMQ",
            accent or (200, 30, 45))
    except Exception as e:
        art.notes.append(f"сборка картинки: {type(e).__name__}: {_snip(str(e), 120)}")
        _log(art.notes[-1])
    return art
