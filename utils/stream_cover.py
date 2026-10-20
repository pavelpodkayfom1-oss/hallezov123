"""
Рисует обложку стрима для анонса: превью Twitch + название, канал, категория, зрители.
Чистая функция: данные + байты превью -> JPEG. Стиль — как у utils/card_image.py.

Все полупрозрачные слои пишутся на отдельный RGBA-холст и склеиваются через
alpha_composite: PIL не смешивает RGBA-заливку с RGBA-изображением, а заменяет её.
"""
import io
from typing import Dict, List, Optional

from PIL import Image, ImageDraw, ImageFilter

from utils.card_image import _fit, _font, _mix, _rounded_mask

W, H = 1280, 720
PURPLE = (145, 70, 255)
RED = (232, 62, 84)
DARK = (12, 10, 20)
LINE_H = 66
BOTTOM = H - 96


def _cover(source: Image.Image) -> Image.Image:
    sw, sh = source.size
    scale = max(W / sw, H / sh)
    sized = source.resize((round(sw * scale), round(sh * scale)), Image.LANCZOS)
    left, top = (sized.width - W) // 2, (sized.height - H) // 2
    return sized.crop((left, top, left + W, top + H))


def _placeholder(accent) -> Image.Image:
    img = Image.new("RGB", (W, H), DARK)
    d = ImageDraw.Draw(img)
    for y in range(H):
        d.line([(0, y), (W, y)], fill=_mix(DARK, accent, 0.08 + 0.30 * y / H))
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    ImageDraw.Draw(glow).ellipse((-260, H - 560, 720, H + 320), fill=_mix((0, 0, 0), accent, 0.55))
    return Image.blend(img, glow.filter(ImageFilter.GaussianBlur(90)), 0.5)


def _wrap(d: ImageDraw.ImageDraw, text: str, font, max_w: int, max_lines: int = 2) -> List[str]:
    words, lines, cur = text.split(), [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if d.textlength(trial, font=font) <= max_w:
            cur = trial
        elif cur:
            lines.append(cur)
            cur = word
        else:
            cur = word
            break
        if len(lines) == max_lines:
            break
    if len(lines) < max_lines and cur:
        lines.append(cur)
    if len(lines) == max_lines and " ".join(lines) != text:
        lines[-1] = _fit(d, " ".join(lines[1:]) if len(lines) > 1 else lines[-1], font, max_w)
    return lines[-max_lines:]


def render_cover(data: Dict, preview_bytes: Optional[bytes] = None,
                 avatar_bytes: Optional[bytes] = None) -> bytes:
    """
    data: login, title, game, viewers, status ('live'|'offline'|'clip'), author, footer
    """
    status = data.get("status", "offline")
    accent = (70, 130, 255) if status == "clip" else PURPLE

    base = None
    if preview_bytes:
        try:
            base = _cover(Image.open(io.BytesIO(preview_bytes)).convert("RGB"))
        except Exception:
            base = None
    canvas = (base or _placeholder(accent)).convert("RGBA")

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    band = int(H * 0.66)
    for i in range(band):
        y = H - band + i
        d.line([(0, y), (W, y)], fill=(6, 4, 12, int(250 * (i / band) ** 1.35)))
    for i in range(150):
        d.line([(0, i), (W, i)], fill=(6, 4, 12, int(130 * (1 - i / 150))))

    f_live, f_chan, f_title, f_meta = _font(26, True), _font(32, True), _font(52, True), _font(23)

    label = {"live": "LIVE", "clip": "КЛИП", "offline": "ОФЛАЙН"}.get(status, "LIVE")
    pill_w = d.textlength(label, font=f_live) + (76 if status == "live" else 56)
    d.rounded_rectangle((44, 40, 44 + pill_w, 96), radius=28,
                        fill=RED + (240,) if status == "live" else (18, 16, 28, 210),
                        outline=(255, 255, 255, 70), width=1)
    dot_x = 44
    if status == "live":
        d.ellipse((66, 58, 84, 76), fill=(255, 255, 255, 240))
        dot_x = 92
    d.text((dot_x, 52), label, font=f_live, fill=(255, 255, 255))

    if data.get("viewers"):
        v = f"{int(data['viewers']):,} зрителей".replace(",", " ")
        vw = d.textlength(v, font=f_meta) + 44
        d.rounded_rectangle((44 + pill_w + 14, 48, 44 + pill_w + 14 + vw, 92), radius=22,
                            fill=(18, 16, 28, 200), outline=(255, 255, 255, 55), width=1)
        d.text((44 + pill_w + 36, 55), v, font=f_meta, fill=(228, 224, 246))

    login = data.get("login", "")
    if login:
        tag = f"twitch.tv/{login}"
        d.text((W - d.textlength(tag, font=f_chan) - 48, 54), tag, font=f_chan, fill=(255, 255, 255, 240))

    tx = 56
    if avatar_bytes:
        try:
            av = Image.open(io.BytesIO(avatar_bytes)).convert("RGB").resize((128, 128), Image.LANCZOS)
            mask = Image.new("L", (128, 128), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, 127, 127), fill=255)
            layer.paste(av, (56, BOTTOM - 138), mask)
            ring = Image.new("RGBA", (144, 144), (0, 0, 0, 0))
            ImageDraw.Draw(ring).ellipse((0, 0, 143, 143), outline=accent + (230,), width=3)
            layer.paste(ring, (48, BOTTOM - 146), ring)
            tx = 224
        except Exception:
            pass

    right = W - 48
    lines = _wrap(d, (data.get("title") or f"@{login}" or "Стрим").strip(), f_title, right - tx)
    y = BOTTOM - LINE_H * len(lines) + 8
    for line in lines:
        d.text((tx + 2, y + 3), line, font=f_title, fill=(0, 0, 0, 160))
        d.text((tx, y), line, font=f_title, fill=(255, 255, 255))
        y += LINE_H

    parts = [p for p in (data.get("game"), data.get("author"), data.get("footer")) if p]
    if parts:
        meta = "   ·   ".join(str(p) for p in parts)
        d.line([(tx, H - 64), (right, H - 64)], fill=(255, 255, 255, 45), width=1)
        d.text((tx, H - 52), _fit(d, meta, f_meta, right - tx), font=f_meta, fill=(214, 209, 234))

    d.rounded_rectangle((10, 10, W - 11, H - 11), radius=34, outline=accent + (190,), width=3)

    out = Image.new("RGB", (W, H), DARK)
    out.paste(Image.alpha_composite(canvas, layer).convert("RGB"), (0, 0), _rounded_mask((W, H), 36))
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=86, optimize=True)
    return buf.getvalue()
