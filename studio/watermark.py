"""Marca d'agua RGBA (PIL): selo IA no canto superior direito + faixa de 3 linhas na coluna 9:16."""

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from studio.config import AVISO_LINHA1, AVISO_LINHA3, FONT_BOLD, FONT_REG, Modelo

BAND_RGBA = (0, 0, 0, 140)                  # preto a 55 %
LINE_RGBA = ((255, 255, 255, 255), (240, 240, 240, 255), (240, 240, 240, 255))
BADGE_FILL = (0, 0, 0, 165)
BADGE_OUTLINE = (255, 255, 255, 200)
BADGE_DOT = (230, 30, 40, 255)
BADGE_TEXT = "IA"
MIN_FONT_PX = 6
SIG_KEY = "studio_wm"
LAYOUT_VERSION = "1"                        # mudar quando o desenho mudar (invalida o cache)


def _margin(h: int) -> int:
    return max(8, round(0.03 * h))


def _pad(h: int) -> int:
    return max(3, round(0.012 * h))


def _nominal_sizes(h: int) -> tuple[int, int, int]:
    # 30/24/20 px em 720p; o texto so encolhe a partir daqui
    return max(9, round(0.042 * h)), max(8, round(0.034 * h)), max(7, round(0.028 * h))


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    # sem cache: ~1 ms por marca e nenhum FT_Face compartilhado entre threads
    return ImageFont.truetype(path, size)


def _line_h(font: ImageFont.FreeTypeFont) -> int:
    ascent, descent = font.getmetrics()
    return ascent + descent


def _aviso(modelo: Modelo) -> str:
    aviso = modelo.aviso.strip()
    if not aviso:
        raise ValueError(f"Modelo {modelo.key} sem texto de aviso: a marca d'água não pode ser gerada")
    return aviso


def safe_column(w: int, h: int) -> tuple[int, int]:
    # coluna central 9:16 menos a margem dos dois lados; x1 exclusivo
    max_w = min(round(h * 9 / 16), w) - 2 * _margin(h)
    x0 = (w - max_w) // 2
    return x0, x0 + max_w


def band_rect(w: int, h: int) -> tuple[int, int, int, int]:
    # altura so depende de h (tamanhos nominais), nunca do texto do modelo
    s1, s2, s3 = _nominal_sizes(h)
    band_h = (2 * _pad(h) + _line_h(_font(FONT_BOLD, s1)) + _line_h(_font(FONT_REG, s2))
              + _line_h(_font(FONT_REG, s3)))
    return 0, h - band_h, w, h


def _fit_font(path: str, size: int, text: str, max_w: int) -> ImageFont.FreeTypeFont:
    for s in range(size, MIN_FONT_PX - 1, -1):
        f = _font(path, s)
        left, _, right, _ = f.getbbox(text, anchor="ls")
        if right - left <= max_w:
            return f
    raise ValueError(f"O texto da marca d'água não cabe num vídeo desse tamanho: {text!r}")


def _draw_band(d: ImageDraw.ImageDraw, w: int, h: int, lines: tuple[str, str, str]) -> None:
    x0, x1 = safe_column(w, h)
    _, by0, _, _ = band_rect(w, h)
    d.rectangle((0, by0, w - 1, h - 1), fill=BAND_RGBA)
    slot_top = by0 + _pad(h)
    for path, size, text, fill in zip((FONT_BOLD, FONT_REG, FONT_REG), _nominal_sizes(h), lines, LINE_RGBA):
        slot_h = _line_h(_font(path, size))
        f = _fit_font(path, size, text, x1 - x0)
        left, _, right, _ = f.getbbox(text, anchor="ls")
        ascent, descent = f.getmetrics()
        baseline = slot_top + (slot_h - ascent - descent) // 2 + ascent
        x = x0 + (x1 - x0 - (right - left)) // 2 - left
        d.text((x, baseline), text, font=f, fill=fill, anchor="ls")
        slot_top += slot_h


def _draw_badge(d: ImageDraw.ImageDraw, w: int, h: int) -> None:
    # capsula escura com ponto vermelho desenhado (nao depende de glifo) + "IA"
    f = _font(FONT_BOLD, max(12, round(0.05 * h)))
    left, top, right, bottom = f.getbbox(BADGE_TEXT)
    tw, th = right - left, bottom - top
    dot, pad_x, pad_y, gap = round(th * 0.85), round(th * 0.55), round(th * 0.45), round(th * 0.40)
    x1, y0 = w - _margin(h), _margin(h)
    x0, y1 = x1 - (pad_x + dot + gap + tw + pad_x), y0 + pad_y + th + pad_y
    d.rounded_rectangle((x0, y0, x1, y1), radius=(y1 - y0) // 2, fill=BADGE_FILL,
                        outline=BADGE_OUTLINE, width=max(1, round(h / 360)))
    cy, dx = (y0 + y1) / 2, x0 + pad_x
    d.ellipse((dx, cy - dot / 2, dx + dot, cy + dot / 2), fill=BADGE_DOT)
    d.text((dx + dot + gap - left, y0 + pad_y - top), BADGE_TEXT, font=f, fill=(255, 255, 255, 255))


def make_watermark(w: int, h: int, modelo: Modelo) -> Image.Image:
    lines = (AVISO_LINHA1, _aviso(modelo), AVISO_LINHA3)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    _draw_band(d, w, h, lines)
    _draw_badge(d, w, h)
    return img


def _signature(modelo: Modelo) -> str:
    return "|".join((LAYOUT_VERSION, AVISO_LINHA1, _aviso(modelo), AVISO_LINHA3))


def _cache_ok(path: str, w: int, h: int, sig: str) -> bool:
    try:
        with Image.open(path) as im:
            return im.size == (w, h) and im.mode == "RGBA" and getattr(im, "text", {}).get(SIG_KEY) == sig
    except (OSError, ValueError):
        return False


def watermark_path(take_dir: str, w: int, h: int, modelo: Modelo) -> str:
    sig = _signature(modelo)
    path = os.path.join(take_dir, f"wm_{w}x{h}_{modelo.key}.png")
    if _cache_ok(path, w, h, sig):
        return path
    img = make_watermark(w, h, modelo)
    info = PngImagePlugin.PngInfo()
    info.add_itxt(SIG_KEY, sig)
    part = path + ".part"
    try:
        img.save(part, format="PNG", pnginfo=info, optimize=True)
        os.replace(part, path)
    except BaseException:
        try:
            os.unlink(part)
        except OSError:
            pass
        raise
    return path


def text_pixel_mask(img) -> np.ndarray:
    # aceita Image ou caminho do PNG; True onde o texto e branco opaco
    if isinstance(img, (str, os.PathLike)):
        with Image.open(img) as im:
            a = np.asarray(im.convert("RGBA"))
    else:
        a = np.asarray(img.convert("RGBA"))
    return (a[..., 3] == 255) & (a[..., :3] >= 230).all(axis=2)
