"""Generate a full-frame transparent RGBA watermark PNG (approach A).

Usage: python watermark.py W H out.png
All sizes are proportional to the video height H; the band text auto-shrinks
if it would not fit the width (e.g. 4:3 or portrait).
"""
import sys
from PIL import Image, ImageDraw, ImageFont

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

BADGE_TEXT = "IA"
LINE1 = "VOZ GERADA POR IA · PARÓDIA/HOMENAGEM"
LINE2 = "Não é a voz real de Silvio Santos"


def _fit_font(path, size, text, max_w):
    """Largest font <= size whose rendered text width fits max_w."""
    while size > 8:
        f = ImageFont.truetype(path, size)
        l, t, r, b = f.getbbox(text)
        if r - l <= max_w:
            return f
        size -= 1
    return ImageFont.truetype(path, size)


def make_watermark(w, h, out_path, line1=LINE1, line2=LINE2, badge=BADGE_TEXT):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    margin = max(8, round(0.03 * h))          # safe margin (~22 px @720p)

    # ---------- top-right badge: [ (red dot) IA ] ----------
    bfont = ImageFont.truetype(FONT_BOLD, max(12, round(0.05 * h)))   # 36 px @720p
    l, t, r, b = bfont.getbbox(badge)         # bbox relative to draw origin
    text_w, text_h = r - l, b - t
    dot_d = round(text_h * 0.85)
    pad_x = round(text_h * 0.55)
    pad_y = round(text_h * 0.45)
    gap = round(text_h * 0.40)
    box_w = pad_x + dot_d + gap + text_w + pad_x
    box_h = pad_y + text_h + pad_y
    x1 = w - margin
    x0 = x1 - box_w
    y0 = margin
    y1 = y0 + box_h
    d.rounded_rectangle((x0, y0, x1, y1), radius=box_h // 2,
                        fill=(0, 0, 0, 165),                        # ~65% black
                        outline=(255, 255, 255, 200), width=max(1, round(h / 360)))
    cy = (y0 + y1) / 2
    dx0 = x0 + pad_x
    d.ellipse((dx0, cy - dot_d / 2, dx0 + dot_d, cy + dot_d / 2), fill=(230, 30, 40, 255))
    tx = dx0 + dot_d + gap - l
    ty = y0 + pad_y - t
    d.text((tx, ty), badge, font=bfont, fill=(255, 255, 255, 255))

    # ---------- bottom full-width band ----------
    max_text_w = w - 2 * margin
    f1 = _fit_font(FONT_BOLD, max(10, round(0.042 * h)), line1, max_text_w)   # ~30 px
    f2 = _fit_font(FONT_REG, max(9, round(0.034 * h)), line2, max_text_w)     # ~24 px
    b1 = f1.getbbox(line1)
    b2 = f2.getbbox(line2)
    h1, h2 = b1[3] - b1[1], b2[3] - b2[1]
    line_gap = round(0.35 * h1)
    vpad = round(0.022 * h)
    band_h = vpad + h1 + line_gap + h2 + vpad
    band_y0 = h - band_h
    d.rectangle((0, band_y0, w, h), fill=(0, 0, 0, 140))           # 55% black
    y = band_y0 + vpad
    for text, f, bb, hh, col in ((line1, f1, b1, h1, (255, 255, 255, 255)),
                                 (line2, f2, b2, h2, (235, 235, 235, 255))):
        tw = bb[2] - bb[0]
        d.text(((w - tw) / 2 - bb[0], y - bb[1]), text, font=f, fill=col)
        y += hh + line_gap
    img.save(out_path, optimize=True)
    return {"band_h": band_h, "badge_box": (x0, y0, x1, y1),
            "f1": f1.size, "f2": f2.size}


if __name__ == "__main__":
    W, H, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    print(make_watermark(W, H, out))
