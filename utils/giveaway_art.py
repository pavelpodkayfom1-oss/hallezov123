"""
Баннеры для розыгрышей (Pillow). Чистые функции: данные -> PNG-байты.
Без эмодзи в самой картинке (Pillow не рисует цветные эмодзи) — подарок и конфетти рисуются фигурами.
"""
import io
import math
import random
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter

from utils.card_image import _font, _fit, _mix

W, H = 1000, 380
PALETTES = [
    ((153, 0, 0), (255, 170, 60)),     # огонь
    ((90, 40, 190), (255, 110, 200)),  # неон
    ((0, 110, 160), (90, 240, 200)),   # лёд
    ((190, 110, 0), (255, 225, 90)),   # золото
]


def _palette(seed: int, accent: Optional[Tuple[int, int, int]] = None):
    if accent:
        return accent, _mix(accent, (255, 230, 120), 0.65)
    return PALETTES[seed % len(PALETTES)]


def _background(seed: int, c1, c2) -> Image.Image:
    base = (12, 12, 18)
    img = Image.new("RGB", (W, H), base)
    px = ImageDraw.Draw(img)
    for x in range(W):
        px.line([(x, 0), (x, H)], fill=_mix(_mix(base, c1, 0.40), base, x / W))
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((560, -80, 1100, 460), fill=_mix((0, 0, 0), c2, 0.65))
    gd.ellipse((-120, 180, 300, 520), fill=_mix((0, 0, 0), c1, 0.55))
    glow = glow.filter(ImageFilter.GaussianBlur(80))
    img = Image.blend(img, Image.composite(glow, img, Image.new("L", (W, H), 255)), 0.55)

    # конфетти (детерминированно от номера розыгрыша)
    rnd = random.Random(seed * 7919 + 13)
    d = ImageDraw.Draw(img, "RGBA")
    for _ in range(70):
        x, y = rnd.randint(0, W), rnd.randint(0, H)
        col = rnd.choice([c2, (255, 255, 255), _mix(c1, (255, 255, 255), 0.5), (255, 215, 90)])
        a = rnd.randint(70, 190)
        s = rnd.randint(4, 11)
        kind = rnd.random()
        if kind < 0.4:
            d.ellipse((x, y, x + s, y + s), fill=col + (a,))
        elif kind < 0.75:
            ang = rnd.random() * math.pi
            dx, dy = math.cos(ang) * s * 1.6, math.sin(ang) * s * 1.6
            d.line((x, y, x + dx, y + dy), fill=col + (a,), width=3)
        else:
            d.polygon([(x, y - s), (x + s, y), (x, y + s), (x - s, y)], fill=col + (a,))
    return img


def _gift(img: Image.Image, cx: int, cy: int, size: int, c1, c2):
    """Рисует подарочную коробку с бантом."""
    d = ImageDraw.Draw(img, "RGBA")
    # тень
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse((cx - size * 0.75, cy + size * 0.62, cx + size * 0.75, cy + size * 0.82),
                               fill=(0, 0, 0, 150))
    sh = sh.filter(ImageFilter.GaussianBlur(10))
    img.paste(sh, (0, 0), sh)
    d = ImageDraw.Draw(img, "RGBA")
    bw = size
    box_top = cy - int(size * 0.05)
    box_bot = cy + int(size * 0.68)
    lid_top = cy - int(size * 0.28)
    # корпус
    d.rounded_rectangle((cx - bw * 0.46, box_top, cx + bw * 0.46, box_bot), radius=10,
                        fill=_mix(c1, (255, 255, 255), 0.10) + (255,))
    # крышка
    d.rounded_rectangle((cx - bw * 0.54, lid_top, cx + bw * 0.54, box_top + 8), radius=10,
                        fill=_mix(c1, (255, 255, 255), 0.28) + (255,))
    # лента
    rib = _mix(c2, (255, 255, 255), 0.25) + (255,)
    d.rectangle((cx - bw * 0.09, lid_top, cx + bw * 0.09, box_bot), fill=rib)
    # бант
    d.ellipse((cx - bw * 0.44, lid_top - bw * 0.30, cx - bw * 0.02, lid_top + bw * 0.04), outline=rib, width=9)
    d.ellipse((cx + bw * 0.02, lid_top - bw * 0.30, cx + bw * 0.44, lid_top + bw * 0.04), outline=rib, width=9)
    d.ellipse((cx - bw * 0.07, lid_top - bw * 0.10, cx + bw * 0.07, lid_top + bw * 0.04), fill=rib)


def _wrap(d: ImageDraw.ImageDraw, text: str, font, max_w: int, max_lines: int) -> List[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
            while d.textlength(cur, font=font) > max_w and len(cur) > 1:  # очень длинное слово
                cut = len(cur) - 1
                while cut > 1 and d.textlength(cur[:cut], font=font) > max_w:
                    cut -= 1
                lines.append(cur[:cut])
                cur = cur[cut:]
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _fit(d, lines[-1] + "…", font, max_w)
    return lines or [""]


def _tracked(d, xy, text, font, fill, tracking=6):
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill)
        x += d.textlength(ch, font=font) + tracking
    return x


def _pill(d, x, y, text, font, fill, fg=(255, 255, 255)) -> int:
    tw = d.textlength(text, font=font)
    d.rounded_rectangle((x, y, x + tw + 34, y + 40), radius=20, fill=fill)
    d.text((x + 17, y + 6), text, font=font, fill=fg)
    return int(x + tw + 34 + 12)


def render_banner(gid: int, prize: str, winners: int, conditions: List[str],
                  ended: bool = False, accent: Optional[Tuple[int, int, int]] = None) -> bytes:
    c1, c2 = _palette(gid, accent)
    img = _background(gid, c1, c2)
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle((14, 14, W - 15, H - 15), radius=28, outline=c2 + (170,), width=2)

    f_tag, f_prize, f_pill = _font(22, True), _font(58, True), _font(21, True)

    # заголовок-метка
    label = "ИТОГИ РОЗЫГРЫША" if ended else f"РОЗЫГРЫШ  #{gid}"
    d.rounded_rectangle((52, 48, 52 + int(d.textlength(label, font=f_tag)) + len(label) * 6 + 40, 92),
                        radius=22, fill=c2 + (230,))
    _tracked(d, (72, 56), label, f_tag, (20, 14, 10), 6)

    # приз
    max_w = 580
    lines = _wrap(d, prize, f_prize, max_w, 3)
    if len(lines) >= 3:
        f_prize = _font(40, True)
        lines = _wrap(d, prize, f_prize, max_w, 3)
    y = 118
    for ln in lines:
        d.text((54, y + 3), ln, font=f_prize, fill=(0, 0, 0, 140))  # тень
        d.text((52, y), ln, font=f_prize, fill=(255, 255, 255))
        y += f_prize.size + 8

    # пилюли
    px = 52
    py = H - 96
    word = "победитель" if winners == 1 else ("победителя" if 2 <= winners <= 4 else "победителей")
    px = _pill(d, px, py, f"{winners} {word}", f_pill, (255, 255, 255, 40))
    for c in conditions[:2]:
        px = _pill(d, px, py, c, f_pill, c1 + (200,))

    _gift(img, 810, 168, 190, c1, c2)
    if ended:
        d = ImageDraw.Draw(img, "RGBA")
        d.rounded_rectangle((700, 300, 930, 346), radius=23, fill=(46, 204, 113, 235))
        t = "ЗАВЕРШЁН"
        d.text((815 - d.textlength(t, font=f_tag) / 2, 311), t, font=f_tag, fill=(10, 30, 18))

    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def render_winners(gid: int, prize: str, winners: List[Tuple[str, Optional[bytes]]],
                   accent: Optional[Tuple[int, int, int]] = None) -> bytes:
    """winners: [(имя, байты аватарки|None), ...] — рисуем до 5 крупных аватарок, остальные счётчиком."""
    c1, c2 = _palette(gid, accent)
    img = _background(gid + 99, c1, c2)
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle((14, 14, W - 15, H - 15), radius=28, outline=c2 + (170,), width=2)
    f_tag, f_prize, f_name = _font(22, True), _font(34, True), _font(22, True)

    label = "ПОБЕДИТЕЛИ" if len(winners) != 1 else "ПОБЕДИТЕЛЬ"
    tw = d.textlength(label, font=f_tag) + len(label) * 6
    d.rounded_rectangle((W / 2 - tw / 2 - 24, 36, W / 2 + tw / 2 + 24, 80), radius=22, fill=c2 + (235,))
    _tracked(d, (W / 2 - tw / 2 + 2, 44), label, f_tag, (20, 14, 10), 6)
    p = _fit(d, prize, f_prize, W - 140)
    d.text((W / 2 - d.textlength(p, font=f_prize) / 2, 96), p, font=f_prize, fill=(255, 255, 255))

    shown = winners[:5]
    extra = len(winners) - len(shown)
    if not shown:
        t = "Никто не участвовал"
        f = _font(40, True)
        d.text((W / 2 - d.textlength(t, font=f) / 2, 220), t, font=f, fill=(220, 220, 230))
    else:
        n = len(shown)
        AV = 150 if n <= 3 else 124
        gap = 36
        total = n * AV + (n - 1) * gap
        x = (W - total) // 2
        y = 160
        for name, ab in shown:
            ring = Image.new("RGBA", (AV + 16, AV + 16), (0, 0, 0, 0))
            ImageDraw.Draw(ring).ellipse((0, 0, AV + 15, AV + 15), fill=c2 + (255,))
            img.paste(ring, (x - 8, y - 8), ring)
            try:
                av = Image.open(io.BytesIO(ab)).convert("RGB").resize((AV, AV), Image.LANCZOS) if ab else None
            except Exception:
                av = None
            if av is None:
                av = Image.new("RGB", (AV, AV), _mix((30, 30, 40), c1, 0.5))
            m = Image.new("L", (AV, AV), 0)
            ImageDraw.Draw(m).ellipse((0, 0, AV - 1, AV - 1), fill=255)
            img.paste(av, (x, y), m)
            d = ImageDraw.Draw(img, "RGBA")
            nm = _fit(d, name, f_name, AV + gap - 6)
            d.text((x + AV / 2 - d.textlength(nm, font=f_name) / 2, y + AV + 18), nm, font=f_name,
                   fill=(255, 255, 255))
            x += AV + gap
        if extra > 0:
            t = f"и ещё {extra}"
            d.text((W - 40 - d.textlength(t, font=f_name), H - 54), t, font=f_name, fill=(220, 220, 230))

    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
