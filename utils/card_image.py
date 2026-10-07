"""
Рисует карточку участника картинкой (Pillow). Чистая функция: данные -> PNG-байты.
Шрифт с кириллицей ищется автоматически; можно положить свой в assets/font.ttf (+ assets/font_bold.ttf).
"""
import io
import os
from typing import Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont, ImageFilter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_REGULAR = [
    os.path.join(BASE_DIR, "assets", "font.ttf"),
    "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf", "/Library/Fonts/Arial.ttf",
]
_BOLD = [
    os.path.join(BASE_DIR, "assets", "font_bold.ttf"),
    "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path in (_BOLD if bold else _REGULAR) + (_REGULAR if bold else []):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _fit(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _mix(c1, c2, t: float):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def _rounded_mask(size, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return m


def render_card(data: Dict, avatar_bytes: Optional[bytes] = None) -> bytes:
    """
    data: name, subtitle, rank, max_rank, rank_name, static, status (None|'vacation'|'afk'),
          stats: [(label, value), ...] (до 4), badges: [str], accent: (r,g,b), footer
    """
    W, H = 1000, 440
    accent = tuple(data.get("accent", (153, 0, 0)))
    bg_top, bg_bot = (14, 14, 20), _mix((14, 14, 20), accent, 0.28)

    img = Image.new("RGB", (W, H), bg_top)
    px = ImageDraw.Draw(img)
    for y in range(H):
        px.line([(0, y), (W, y)], fill=_mix(bg_top, bg_bot, y / H))

    # мягкое свечение за аватаром
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    ImageDraw.Draw(glow).ellipse((-60, -40, 380, 400), fill=_mix((0, 0, 0), accent, 0.55))
    glow = glow.filter(ImageFilter.GaussianBlur(70))
    img = Image.blend(img, Image.composite(glow, img, Image.new("L", (W, H), 255)), 0.35)

    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle((16, 16, W - 17, H - 17), radius=28, outline=accent + (150,), width=2)
    d.rectangle((16, 60, 22, H - 60), fill=accent + (255,))

    # аватар
    AV = 220
    ax, ay = 62, 70
    ring = Image.new("RGBA", (AV + 20, AV + 20), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((0, 0, AV + 19, AV + 19), fill=accent + (255,))
    img.paste(ring, (ax - 10, ay - 10), ring)
    if avatar_bytes:
        try:
            av = Image.open(io.BytesIO(avatar_bytes)).convert("RGB").resize((AV, AV), Image.LANCZOS)
        except Exception:
            av = Image.new("RGB", (AV, AV), (40, 40, 50))
    else:
        av = Image.new("RGB", (AV, AV), (40, 40, 50))
    mask = Image.new("L", (AV, AV), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, AV - 1, AV - 1), fill=255)
    img.paste(av, (ax, ay), mask)

    d = ImageDraw.Draw(img, "RGBA")
    x0 = 330
    right = W - 50

    # имя
    f_name, f_sub, f_lbl, f_val, f_badge, f_small = _font(44, True), _font(24), _font(18), _font(26, True), _font(18, True), _font(16)
    status = data.get("status")
    name_w = right - x0
    if status:
        name_w -= int(d.textlength("В ОТПУСКЕ", font=f_badge)) + 56
    d.text((x0, 48), _fit(d, data.get("name", "—"), f_name, name_w), font=f_name, fill=(255, 255, 255))
    d.text((x0, 106), _fit(d, data.get("subtitle", ""), f_sub, right - x0), font=f_sub, fill=(200, 200, 210))

    # статус-пилюля
    if status:
        txt = "В ОТПУСКЕ" if status == "vacation" else "АФК"
        col = (46, 160, 90) if status == "vacation" else (200, 150, 30)
        tw = d.textlength(txt, font=f_badge)
        d.rounded_rectangle((right - tw - 36, 52, right, 88), radius=18, fill=col + (255,))
        d.text((right - tw - 18, 59), txt, font=f_badge, fill=(255, 255, 255))

    # шкала ранга
    rank, mx = int(data.get("rank", 0)), max(1, int(data.get("max_rank", 4)))
    d.text((x0, 150), f"РАНГ {rank} из {mx}  ·  {data.get('rank_name', '')}", font=f_lbl, fill=(190, 190, 200))
    bar_x, bar_y, bar_w, bar_h = x0, 180, right - x0, 16
    d.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), radius=8, fill=(255, 255, 255, 40))
    fill_w = int(bar_w * min(1.0, rank / mx))
    if fill_w > 0:
        d.rounded_rectangle((bar_x, bar_y, bar_x + max(fill_w, bar_h), bar_y + bar_h), radius=8, fill=accent + (255,))

    # плитки статистики
    stats: List = list(data.get("stats", []))[:4]
    n = max(1, len(stats))
    gap = 16
    tile_w = (right - x0 - gap * (n - 1)) // n
    ty, th = 222, 96
    for i, (label, value) in enumerate(stats):
        tx = x0 + i * (tile_w + gap)
        d.rounded_rectangle((tx, ty, tx + tile_w, ty + th), radius=16, fill=(255, 255, 255, 22),
                            outline=(255, 255, 255, 40), width=1)
        d.text((tx + 16, ty + 14), _fit(d, label.upper(), f_small, tile_w - 28), font=f_small, fill=(170, 170, 185))
        d.text((tx + 16, ty + 44), _fit(d, str(value), f_val, tile_w - 28), font=f_val, fill=(255, 255, 255))

    # значки
    bx, by = x0, 340
    for b in data.get("badges", [])[:6]:
        tw = d.textlength(b, font=f_badge)
        if bx + tw + 28 > right:
            break
        d.rounded_rectangle((bx, by, bx + tw + 28, by + 34), radius=17, fill=accent + (210,))
        d.text((bx + 14, by + 6), b, font=f_badge, fill=(255, 255, 255))
        bx += tw + 28 + 10
    if not data.get("badges"):
        d.text((x0, by + 6), "Значков пока нет — всё впереди", font=f_small, fill=(150, 150, 165))

    # низ
    d.text((62, H - 60), _fit(d, data.get("static", ""), f_small, 250), font=f_small, fill=(170, 170, 185))
    foot = data.get("footer", "")
    fw = d.textlength(foot, font=f_small)
    d.text((right - fw, H - 60), foot, font=f_small, fill=(150, 150, 165))

    # скругляем всю картинку
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.paste(img, (0, 0), _rounded_mask((W, H), 30))
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
