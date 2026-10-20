"""
Постер состава семьи картинкой (Pillow): роли-секции и участники с аватарами.
Стиль тот же, что у utils/card_image.py и utils/stream_cover.py.
Чистая функция: данные + байты аватаров -> JPEG.
"""
import io
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter

from utils.card_image import _fit, _font, _mix, _rounded_mask

W = 960
DARK = (12, 10, 20)
PANEL = (24, 20, 38)
ACCENT = (153, 0, 0)
PURPLE = (145, 70, 255)
TEXT = (240, 237, 250)
MUTED = (176, 169, 196)
ROW_H = 74
AV = 52
PAD = 44
HEADER_H = 250


def _background(h: int) -> Image.Image:
    """Ночная подложка: тёмный низ, цветное свечение сверху."""
    img = Image.new("RGB", (W, h), DARK)
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = max(0.0, 1 - y / min(h, 900))
        d.line([(0, y), (W, y)], fill=_mix(DARK, ACCENT, 0.26 * t))
    glow = Image.new("RGB", (W, h), (0, 0, 0))
    ImageDraw.Draw(glow).ellipse((-320, -360, 620, 300), fill=_mix((0, 0, 0), PURPLE, 0.42))
    ImageDraw.Draw(glow).ellipse((420, -420, 1320, 220), fill=_mix((0, 0, 0), ACCENT, 0.5))
    return Image.blend(img, glow.filter(ImageFilter.GaussianBlur(110)), 0.55)


def _avatar(avatar: Optional[bytes], name: str) -> Image.Image:
    """Круглый аватар; если байтов нет — плитка с первой буквой ника."""
    tile = Image.new("RGB", (AV, AV), PANEL)
    d = ImageDraw.Draw(tile)
    if avatar:
        try:
            pic = Image.open(io.BytesIO(avatar)).convert("RGB")
            side = min(pic.size)
            left, top = (pic.width - side) // 2, (pic.height - side) // 2
            tile.paste(pic.crop((left, top, left + side, top + side)).resize((AV, AV), Image.LANCZOS), (0, 0))
        except Exception:
            pass
    else:
        letter = (name.strip() or "?")[:1].upper()
        d.rectangle((0, 0, AV, AV), fill=_mix(PANEL, PURPLE, 0.22))
        f = _font(26, True)
        d.text(((AV - d.textlength(letter, font=f)) / 2, AV / 2 - 17), letter, font=f, fill=TEXT)
    mask = Image.new("L", (AV, AV), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, AV - 1, AV - 1), fill=255)
    out = Image.new("RGBA", (AV, AV), (0, 0, 0, 0))
    out.paste(tile, (0, 0), mask)
    return out


def render_roster(title: str, subtitle: str, sections: List[Tuple[str, List[Tuple[str, Optional[bytes]]]]],
                  note: str = "") -> bytes:
    """
    sections: [(название роли, [(ник, байты аватара или None), ...]), ...]
    """
    shown = sum(len(m) for _, m in sections)
    h = HEADER_H + sum(70 + max(len(m), 1) * ROW_H + 26 for _, m in sections) + (60 if note else 0) + PAD
    canvas = _background(h)
    layer = Image.new("RGBA", (W, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    f_kicker, f_title, f_sub = _font(22, True), _font(56, True), _font(23)
    f_role, f_name, f_count = _font(27, True), _font(25), _font(21, True)

    kicker = "MAJESTIC RP"
    d.text((PAD, 58), kicker, font=f_kicker, fill=(255, 255, 255, 165))
    d.text((PAD, 88), _fit(d, title, f_title, W - PAD * 2), font=f_title, fill=(255, 255, 255, 255))
    if subtitle:
        d.text((PAD, 168), _fit(d, subtitle, f_sub, W - PAD * 2), font=f_sub, fill=(222, 216, 238, 235))
    badge = f"{shown}"
    bw = max(76, d.textlength(badge, font=f_count) + 44)
    top, bottom = 62, 116
    d.rounded_rectangle((W - PAD - bw, top, W - PAD, bottom), radius=27,
                        fill=(18, 16, 28, 215), outline=(255, 255, 255, 60), width=1)
    box = d.textbbox((0, 0), badge, font=f_count)
    d.text((W - PAD - bw / 2 - (box[2] - box[0]) / 2 - box[0],
            (top + bottom) / 2 - (box[1] + box[3]) / 2), badge, font=f_count, fill=TEXT)
    d.line([(PAD, HEADER_H - 40), (W - PAD, HEADER_H - 40)], fill=(255, 255, 255, 40), width=1)

    y = HEADER_H - 14
    for role_title, members in sections:
        d.rounded_rectangle((PAD, y, PAD + 10, y + 40), radius=5, fill=ACCENT + (255,))
        d.text((PAD + 24, y + 2), _fit(d, role_title, f_role, W - PAD * 2 - 120), font=f_role, fill=TEXT)
        counter = f"{len(members)}"
        d.text((W - PAD - d.textlength(counter, font=f_sub), y + 6), counter, font=f_sub, fill=MUTED)
        y += 62

        for name, avatar in members:
            d.rounded_rectangle((PAD, y, W - PAD, y + ROW_H - 8), radius=18, fill=(255, 255, 255, 14))
            layer.alpha_composite(_avatar(avatar, name), (PAD + 12, y + 3))
            d.text((PAD + 12 + AV + 20, y + 15), _fit(d, name, f_name, W - PAD * 2 - AV - 70),
                   font=f_name, fill=(238, 234, 248, 255))
            y += ROW_H
        y += 26

    if note:
        d.text((PAD, h - 54), _fit(d, note, f_sub, W - PAD * 2), font=f_sub, fill=(200, 193, 220, 220))

    out = Image.new("RGB", (W, h), DARK)
    out.paste(Image.alpha_composite(canvas.convert("RGBA"), layer).convert("RGB"), (0, 0), _rounded_mask((W, h), 32))
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=87, optimize=True)
    return buf.getvalue()
