"""
Карточка анонса стрима Hallez x Twitch (Pillow). Чистая функция: данные -> PNG-байты.

Композиция 1280x540:
  слева   - огромное имя канала (курсив Black, закатный градиент), ссылка, заметка, кто анонсировал
  справа  - «экран» с превью стрима в фиолетовой рамке Twitch
  фон     - то же превью (или баннер семьи), размытое и затемнённое

Шрифты лежат в assets/fonts, поэтому карточка выглядит одинаково на любом хостинге.
"""
import io
import math
import os
from typing import Optional, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(BASE_DIR, "assets", "fonts")

W, H = 1280, 540

INK = (10, 7, 9)
BLOOD = (196, 22, 28)
EMBER = (255, 138, 61)
GOLD = (226, 190, 120)
GOLD_LIGHT = (250, 226, 170)
TWITCH = (145, 70, 255)
TEXT = (244, 238, 232)
MUTED = (178, 168, 162)


# ───────────────────────── шрифты ─────────────────────────

def _font(file: str, size: int, fallback_bold: bool = False) -> ImageFont.FreeTypeFont:
    path = os.path.join(FONT_DIR, file)
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        from utils.card_image import _font as fallback
        return fallback(size, fallback_bold)


def _display(size: int):
    return _font("InterDisplay-BlackItalic.otf", size, True)


def _semibold(size: int):
    return _font("Inter-SemiBold.otf", size, True)


def _medium(size: int):
    return _font("Inter-Medium.otf", size)


def _serif(size: int):
    font = _font("Lora-Variable.ttf", size)
    try:
        font.set_variation_by_axes([600])
    except Exception:
        pass
    return font


# ───────────────────────── утилиты ─────────────────────────

def _cover(img: Image.Image, size: Tuple[int, int]) -> Image.Image:
    """Заполнить прямоугольник картинкой с обрезкой по центру."""
    tw, th = size
    scale = max(tw / img.width, th / img.height)
    resized = img.resize((math.ceil(img.width * scale), math.ceil(img.height * scale)), Image.LANCZOS)
    left, top = (resized.width - tw) // 2, (resized.height - th) // 2
    return resized.crop((left, top, left + tw, top + th))


def _rounded_mask(size: Tuple[int, int], radius: int) -> Image.Image:
    """Маска со скруглением, нарисованная в 4x для гладких краёв."""
    k = 4
    big = Image.new("L", (size[0] * k, size[1] * k), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, size[0] * k - 1, size[1] * k - 1), radius=radius * k, fill=255)
    return big.resize(size, Image.LANCZOS)


def _circle_mask(d: int) -> Image.Image:
    k = 4
    big = Image.new("L", (d * k, d * k), 0)
    ImageDraw.Draw(big).ellipse((0, 0, d * k - 1, d * k - 1), fill=255)
    return big.resize((d, d), Image.LANCZOS)


def _glow(base: Image.Image, mask: Image.Image, color, radius: int, strength: float = 1.0, offset=(0, 0)):
    """Цветное свечение по форме маски (маска в размер base)."""
    blurred = mask.filter(ImageFilter.GaussianBlur(radius))
    if strength != 1.0:
        blurred = blurred.point(lambda v: min(255, int(v * strength)))
    if offset != (0, 0):
        blurred = ImageChops.offset(blurred, offset[0], offset[1])
    base.paste(Image.new("RGB", base.size, color), (0, 0), blurred)


def _vertical_gradient(size: Tuple[int, int], top, bottom) -> Image.Image:
    w, h = size
    col = Image.new("RGB", (1, h))
    px = col.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return col.resize((w, h))


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int, max_lines: int):
    words, lines, cur = text.split(), [], ""
    for i, word in enumerate(words):
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        cur = word
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    consumed = " ".join(lines).split()
    if len(consumed) < len(words) and lines:
        lines[-1] = _fit_text(draw, lines[-1].rstrip("…") + "…", font, max_w)
    return [_fit_text(draw, ln, font, max_w) for ln in lines[:max_lines]]


def _tracked(draw: ImageDraw.ImageDraw, xy, text: str, font, fill, spacing: float):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + spacing
    return x


def _load(data: Optional[bytes]) -> Optional[Image.Image]:
    if not data:
        return None
    try:
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None


def _load_path(path: Optional[str]) -> Optional[Image.Image]:
    if not path or not os.path.exists(path):
        return None
    try:
        return Image.open(path).convert("RGB")
    except Exception:
        return None


# ───────────────────────── части карточки ─────────────────────────

def _backdrop(source: Image.Image) -> Image.Image:
    bg = _cover(source, (W, H)).filter(ImageFilter.GaussianBlur(16))
    bg = Image.eval(bg, lambda v: int(v * 0.55))

    # читаемость слева: плотный чёрный -> прозрачный
    shade = Image.new("L", (W, H))
    spx = shade.load()
    for x in range(W):
        t = x / W
        a = int(255 * max(0.0, min(1.0, 0.94 - 0.62 * t ** 0.8)))
        for y in range(H):
            spx[x, y] = a
    bg.paste(Image.new("RGB", (W, H), INK), (0, 0), shade)

    # закатное свечение снизу слева и красный отсвет сверху справа
    glow_mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow_mask).ellipse((-260, 330, 640, 760), fill=120)
    _glow(bg, glow_mask, EMBER, 90, 0.55)
    red_mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(red_mask).ellipse((760, -300, 1400, 200), fill=110)
    _glow(bg, red_mask, BLOOD, 100, 0.6)

    # нижняя виньетка
    vig = Image.new("L", (W, H), 0)
    vpx = vig.load()
    for y in range(H):
        a = int(max(0.0, (y - H * 0.55) / (H * 0.45)) ** 1.5 * 190)
        for x in range(0, W):
            vpx[x, y] = a
    bg.paste(Image.new("RGB", (W, H), INK), (0, 0), vig)
    return bg


def _screen(canvas: Image.Image, preview: Optional[Image.Image], fallback: Image.Image, live: bool):
    """Справа: «монитор» с превью стрима в рамке Twitch."""
    sw, sh = 520, 292
    sx, sy = W - sw - 76, 118
    mask = _rounded_mask((sw, sh), 20)

    # тень и фиолетовое свечение под экраном
    full = Image.new("L", (W, H), 0)
    full.paste(mask, (sx, sy))
    _glow(canvas, full, (0, 0, 0), 26, 1.0, offset=(0, 18))
    _glow(canvas, full, TWITCH, 34, 0.85)

    content = _cover(preview or fallback, (sw, sh))
    if not preview:
        content = Image.eval(content.filter(ImageFilter.GaussianBlur(3)), lambda v: int(v * 0.6))
    canvas.paste(content, (sx, sy), mask)

    d = ImageDraw.Draw(canvas, "RGBA")

    # рамка Twitch
    k = 4
    ring = Image.new("RGBA", (sw * k, sh * k), (0, 0, 0, 0))
    ImageDraw.Draw(ring).rounded_rectangle((0, 0, sw * k - 1, sh * k - 1), radius=20 * k, outline=TWITCH + (255,), width=4 * k)
    ring = ring.resize((sw, sh), Image.LANCZOS)
    canvas.paste(ring, (sx, sy), ring)

    if not preview:
        # превью нет (канал оффлайн) - кнопка воспроизведения
        cx, cy = sx + sw // 2, sy + sh // 2
        d.ellipse((cx - 44, cy - 44, cx + 44, cy + 44), fill=(10, 7, 9, 170), outline=TWITCH + (255,), width=3)
        d.polygon([(cx - 12, cy - 20), (cx - 12, cy + 20), (cx + 22, cy)], fill=TEXT + (255,))

    # плашка LIVE на экране
    if live:
        f = _semibold(19)
        label = "LIVE"
        tw = int(d.textlength(label, font=f))
        bx, by = sx + 18, sy + 18
        d.rounded_rectangle((bx, by, bx + tw + 46, by + 36), radius=9, fill=(214, 28, 34, 255))
        d.ellipse((bx + 14, by + 12, bx + 26, by + 24), fill=(255, 255, 255, 255))
        d.text((bx + 34, by + 7), label, font=f, fill=(255, 255, 255, 255))

    return sx, sy, sw, sh


def _twitch_glyph(canvas: Image.Image, x: int, y: int, size: int = 40):
    """Фирменный глитч-пузырь Twitch, нарисованный в 6x."""
    k = 6
    s = size * k
    g = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(g)
    gd.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.24), fill=TWITCH + (255,))
    u = s / 40.0
    bubble = [(9 * u, 8 * u), (32 * u, 8 * u), (32 * u, 22 * u), (26 * u, 28 * u), (21 * u, 28 * u),
              (17 * u, 32 * u), (17 * u, 28 * u), (9 * u, 28 * u)]
    gd.polygon(bubble, fill=(255, 255, 255, 255))
    gd.rectangle((15 * u, 13 * u, 17.2 * u, 20.5 * u), fill=TWITCH + (255,))
    gd.rectangle((22 * u, 13 * u, 24.2 * u, 20.5 * u), fill=TWITCH + (255,))
    g = g.resize((size, size), Image.LANCZOS)
    canvas.paste(g, (x, y), g)


def _status_pill(canvas: Image.Image, x: int, y: int, text: str, live: bool) -> int:
    d = ImageDraw.Draw(canvas, "RGBA")
    f = _semibold(22)
    tw = int(d.textlength(text, font=f))
    pw, ph = tw + 64, 46

    pill = Image.new("L", (W, H), 0)
    ImageDraw.Draw(pill).rounded_rectangle((x, y, x + pw, y + ph), radius=23, fill=255)
    if live:
        _glow(canvas, pill, BLOOD, 16, 0.7)

    d = ImageDraw.Draw(canvas, "RGBA")
    d.rounded_rectangle((x, y, x + pw, y + ph), radius=23, fill=(14, 9, 11, 235),
                        outline=(BLOOD if live else GOLD) + (255,), width=2)
    cx, cy = x + 26, y + ph // 2
    dot = BLOOD if live else GOLD
    if live:
        halo = Image.new("L", (W, H), 0)
        ImageDraw.Draw(halo).ellipse((cx - 9, cy - 9, cx + 9, cy + 9), fill=255)
        _glow(canvas, halo, (255, 60, 66), 7, 1.0)
        d = ImageDraw.Draw(canvas, "RGBA")
    d.ellipse((cx - 7, cy - 7, cx + 7, cy + 7), fill=dot + (255,))
    d.text((x + 46, y + 10), text, font=f, fill=TEXT + (255,))
    return pw


def _channel_name(canvas: Image.Image, text: str, x: int, y: int, max_w: int) -> int:
    """Имя канала: огромный курсив с закатным градиентом, красным свечением и жёсткой тенью."""
    probe = ImageDraw.Draw(canvas)
    size = 210
    font = _display(size)
    # меряем по реальному bbox: у курсива края выступают дальше textlength
    while size > 56:
        l0, _, r0, _ = probe.textbbox((0, 0), text, font=font)
        if r0 - l0 <= max_w:
            break
        size -= 4
        font = _display(size)

    l, t, r, b = probe.textbbox((0, 0), text, font=font)
    tw, th = r - l, b - t
    pad = 40

    mask = Image.new("L", (tw + pad * 2, th + pad * 2), 0)
    ImageDraw.Draw(mask).text((pad - l, pad - t), text, font=font, fill=255)

    layer_w, layer_h = mask.size
    full = Image.new("L", (W, H), 0)
    full.paste(mask, (x - pad, y - pad))

    _glow(canvas, full, BLOOD, 26, 0.9)                       # красное свечение
    _glow(canvas, full, (0, 0, 0), 3, 1.0, offset=(6, 7))     # жёсткая тень

    grad = _vertical_gradient((layer_w, layer_h), (255, 252, 244), GOLD)
    # нижняя треть уходит в закатный оранжевый
    lower = _vertical_gradient((layer_w, layer_h), GOLD, EMBER)
    blend = Image.new("L", (layer_w, layer_h))
    bpx = blend.load()
    for yy in range(layer_h):
        v = int(255 * max(0.0, min(1.0, (yy - pad - th * 0.45) / (th * 0.55))))
        for xx in range(layer_w):
            bpx[xx, yy] = v
    grad = Image.composite(lower, grad, blend)
    canvas.paste(grad, (x - pad, y - pad), mask)

    # тонкая золотая обводка сверху для «чеканки»
    edge = mask.filter(ImageFilter.MinFilter(3))
    rim = ImageChops.subtract(mask, edge)
    canvas.paste(Image.new("RGB", mask.size, GOLD_LIGHT), (x - pad, y - pad), rim.point(lambda v: int(v * 0.55)))

    return y + th


def _announcer(canvas: Image.Image, x: int, y: int, name: str, avatar: Optional[Image.Image], max_w: int):
    d = ImageDraw.Draw(canvas, "RGBA")
    AV = 60
    ring = Image.new("RGBA", (AV + 10, AV + 10), (0, 0, 0, 0))
    rm = _circle_mask(AV + 10)
    ring.paste(Image.new("RGBA", ring.size, GOLD + (255,)), (0, 0), rm)
    canvas.paste(ring, (x - 5, y - 5), ring)

    if avatar is None:
        # нет аватара - золотая буква на тёмном круге
        avatar = Image.new("RGB", (AV, AV), (34, 24, 28))
        letter = (name.strip()[:1] or "H").upper()
        lf = _semibold(30)
        ld = ImageDraw.Draw(avatar)
        lb = ld.textbbox((0, 0), letter, font=lf)
        ld.text(((AV - (lb[2] - lb[0])) / 2 - lb[0], (AV - (lb[3] - lb[1])) / 2 - lb[1]), letter, font=lf, fill=GOLD)
    av = _cover(avatar, (AV, AV))
    canvas.paste(av, (x, y), _circle_mask(AV))

    d = ImageDraw.Draw(canvas, "RGBA")
    d.text((x + AV + 18, y + 2), "Анонсировал", font=_medium(18), fill=MUTED + (255,))
    f = _semibold(26)
    d.text((x + AV + 18, y + 26), _fit_text(d, name, f, max_w - AV - 18), font=f, fill=TEXT + (255,))


def _frame(canvas: Image.Image):
    """Золотая рамка с уголками: структура, а не украшение - держит карточку в семейном стиле."""
    d = ImageDraw.Draw(canvas, "RGBA")
    m = 22
    d.rounded_rectangle((m, m, W - m, H - m), radius=26, outline=GOLD + (60,), width=1)
    L = 46
    c = GOLD + (235,)
    for (cx, cy, sx, sy) in ((m, m, 1, 1), (W - m, m, -1, 1), (m, H - m, 1, -1), (W - m, H - m, -1, -1)):
        d.line([(cx, cy + sy * L), (cx, cy), (cx + sx * L, cy)], fill=c, width=3)


# ───────────────────────── точка входа ─────────────────────────

def render_media_card(
    login: str,
    note: str = "",
    announcer: str = "",
    avatar_bytes: Optional[bytes] = None,
    preview_bytes: Optional[bytes] = None,
    banner_path: Optional[str] = None,
    live: bool = True,
) -> bytes:
    """
    login          - ник на Twitch (без ссылки)
    note           - о чём стрим (необязательно)
    announcer      - отображаемое имя того, кто анонсирует
    avatar_bytes   - аватар анонсирующего
    preview_bytes  - живое превью стрима (если канал в эфире)
    banner_path    - баннер семьи, запасной фон, если превью нет
    live           - канал сейчас в эфире
    """
    preview = _load(preview_bytes)
    banner = _load_path(banner_path) or Image.new("RGB", (W, H), (40, 14, 18))
    avatar = _load(avatar_bytes)

    canvas = _backdrop(preview or banner).convert("RGB")
    sx, sy, sw, sh = _screen(canvas, preview, banner, live)

    left = 78
    text_w = sx - left - 56

    _status_pill(canvas, left, 52, "Сейчас в эфире" if live else "Анонс стрима", live)

    name = login.upper()
    name_bottom = _channel_name(canvas, name, left, 112, text_w - 24)

    d = ImageDraw.Draw(canvas, "RGBA")
    link_y = name_bottom + 28
    _twitch_glyph(canvas, left, link_y, 40)
    d = ImageDraw.Draw(canvas, "RGBA")
    f_link = _semibold(30)
    d.text((left + 54, link_y + 2), f"twitch.tv/{login}", font=f_link, fill=TEXT + (255,))

    note = " ".join(note.split())
    if note:
        f_note = _medium(26)
        lines = _wrap(d, note, f_note, text_w, 2)
        ny = link_y + 62
        d.rectangle((left, ny + 2, left + 4, ny + 2 + 36 * len(lines) - 6), fill=GOLD + (255,))
        for i, line in enumerate(lines):
            d.text((left + 20, ny + i * 36), line, font=f_note, fill=(226, 218, 212, 255))

    if announcer:
        _announcer(canvas, left, H - 54 - 60, announcer, avatar, text_w)

    # подпись семьи под экраном
    d = ImageDraw.Draw(canvas, "RGBA")
    tag_f = _serif(24)
    tag = "Hallez Crime Family"
    spacing = 3.0
    total = sum(d.textlength(ch, font=tag_f) + spacing for ch in tag) - spacing
    tx = sx + (sw - total) / 2
    ty = sy + sh + 38
    _tracked(d, (tx, ty), tag, tag_f, GOLD + (255,), spacing)
    line_y = ty + 40
    d.line([(sx + sw / 2 - 90, line_y), (sx + sw / 2 + 90, line_y)], fill=GOLD + (140,), width=1)
    d.polygon([(sx + sw / 2, line_y - 5), (sx + sw / 2 + 5, line_y), (sx + sw / 2, line_y + 5), (sx + sw / 2 - 5, line_y)],
              fill=GOLD + (255,))

    _frame(canvas)

    out = io.BytesIO()
    canvas.save(out, "PNG", optimize=True)
    return out.getvalue()
