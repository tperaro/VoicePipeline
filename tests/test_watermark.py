import os
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

from studio import watermark
from studio.config import Modelo, get_modelo

SIZES = ((1280, 720), (640, 480), (800, 600), (1920, 1080), (720, 1280))
SILVIO = get_modelo("silvio")
OROCHI = get_modelo("orochi")
BAND_ALPHA = 140


def runs(flags) -> int:
    # quantos blocos continuos de True (linhas de texto separadas)
    n, prev = 0, False
    for f in flags:
        if f and not prev:
            n += 1
        prev = bool(f)
    return n


class GeometryTest(unittest.TestCase):
    def test_safe_column_values(self):
        # 720p: round(720*9/16)=405, margem 22 -> 361 px centrados (spec 8.1)
        self.assertEqual(watermark.safe_column(1280, 720), (459, 820))
        self.assertEqual(watermark.safe_column(640, 480), (199, 441))
        self.assertEqual(watermark.safe_column(800, 600), (249, 551))
        self.assertEqual(watermark.safe_column(1920, 1080), (688, 1232))
        # retrato: a coluna 9:16 e o proprio quadro, menos as margens
        self.assertEqual(watermark.safe_column(720, 1280), (38, 682))

    def test_band_rect_is_footer_full_width(self):
        for w, h in SIZES:
            x0, y0, x1, y1 = watermark.band_rect(w, h)
            self.assertEqual((x0, x1, y1), (0, w, h), (w, h))
            self.assertTrue(0.10 * h < h - y0 < 0.20 * h, (w, h, y0))


class ImageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.imgs = {(w, h, m.key): watermark.make_watermark(w, h, m)
                    for w, h in SIZES for m in (SILVIO, OROCHI)}

    def cases(self):
        return [(w, h, key, img) for (w, h, key), img in self.imgs.items()]

    def test_exact_size_rgba(self):
        for w, h, key, img in self.cases():
            with self.subTest(size=f"{w}x{h}", modelo=key):
                self.assertEqual(img.size, (w, h))
                self.assertEqual(img.mode, "RGBA")

    def test_text_mask_is_bool_hw(self):
        mask = watermark.text_pixel_mask(self.imgs[(1280, 720, "silvio")])
        self.assertEqual(mask.dtype, np.bool_)
        self.assertEqual(mask.shape, (720, 1280))

    def test_band_text_inside_safe_column(self):
        for w, h, key, img in self.cases():
            with self.subTest(size=f"{w}x{h}", modelo=key):
                x0, x1 = watermark.safe_column(w, h)
                _, by0, _, _ = watermark.band_rect(w, h)
                ys, xs = np.nonzero(watermark.text_pixel_mask(img)[by0:])
                self.assertGreater(len(xs), 200)
                self.assertGreaterEqual(xs.min(), x0)
                self.assertLess(xs.max(), x1)
                # tambem as bordas suavizadas: nada na faixa difere do preto 55 % fora da coluna
                band = np.asarray(img)[by0:]
                ink = (band != np.array([0, 0, 0, BAND_ALPHA], dtype=np.uint8)).any(axis=2)
                _, ink_x = np.nonzero(ink)
                self.assertGreaterEqual(ink_x.min(), x0)
                self.assertLess(ink_x.max(), x1)

    def test_band_has_three_lines(self):
        for w, h, key, img in self.cases():
            with self.subTest(size=f"{w}x{h}", modelo=key):
                _, by0, _, _ = watermark.band_rect(w, h)
                rows = watermark.text_pixel_mask(img)[by0:].any(axis=1)
                self.assertEqual(runs(rows), 3)

    def test_band_drawn_full_width_at_bottom(self):
        for w, h, key, img in self.cases():
            with self.subTest(size=f"{w}x{h}", modelo=key):
                _, by0, _, _ = watermark.band_rect(w, h)
                alpha = np.asarray(img)[..., 3]
                x0, _ = watermark.safe_column(w, h)
                # colunas fora da coluna segura: so faixa, sem texto
                for x in (0, x0 // 2, w - 1):
                    self.assertTrue((alpha[by0:, x] == BAND_ALPHA).all(), x)
                    self.assertTrue((alpha[h // 2:by0, x] == 0).all(), x)

    def test_only_badge_text_outside_band(self):
        for w, h, key, img in self.cases():
            with self.subTest(size=f"{w}x{h}", modelo=key):
                _, by0, _, _ = watermark.band_rect(w, h)
                ys, xs = np.nonzero(watermark.text_pixel_mask(img)[:by0])
                self.assertGreater(len(xs), 20)
                self.assertLess(ys.max(), h // 4)
                self.assertGreaterEqual(xs.min(), w // 2)

    def test_badge_red_dot_top_right(self):
        for w, h, key, img in self.cases():
            with self.subTest(size=f"{w}x{h}", modelo=key):
                a = np.asarray(img).astype(int)
                red = (a[..., 0] >= 200) & (a[..., 1] <= 60) & (a[..., 2] <= 60) & (a[..., 3] == 255)
                ys, xs = np.nonzero(red)
                self.assertGreater(len(xs), 20)
                self.assertLess(ys.max(), h // 2)
                self.assertGreaterEqual(xs.min(), w // 2)

    def test_text_changes_with_model(self):
        for w, h in SIZES:
            a = watermark.text_pixel_mask(self.imgs[(w, h, "silvio")])
            b = watermark.text_pixel_mask(self.imgs[(w, h, "orochi")])
            self.assertFalse(np.array_equal(a, b), (w, h))


class ModelTextTest(unittest.TestCase):
    def test_empty_aviso_raises(self):
        for aviso in ("", "   "):
            with self.assertRaises(ValueError):
                watermark.make_watermark(1280, 720, Modelo("x", "X", "X", aviso))

    def test_long_aviso_shrinks_to_fit(self):
        m = Modelo("x", "X", "Fulano", "Não é a voz real de Fulano de Tal da Silva Sauro Júnior")
        img = watermark.make_watermark(1280, 720, m)
        x0, x1 = watermark.safe_column(1280, 720)
        _, by0, _, _ = watermark.band_rect(1280, 720)
        _, xs = np.nonzero(watermark.text_pixel_mask(img)[by0:])
        self.assertGreaterEqual(xs.min(), x0)
        self.assertLess(xs.max(), x1)

    def test_text_that_cannot_fit_raises(self):
        m = Modelo("x", "X", "X", "Não é a voz real " * 20)
        with self.assertRaises(ValueError):
            watermark.make_watermark(1280, 720, m)


class WatermarkPathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_creates_png_with_name_and_size(self):
        p = watermark.watermark_path(self.dir, 640, 480, SILVIO)
        self.assertEqual(p, os.path.join(self.dir, "wm_640x480_silvio.png"))
        with Image.open(p) as im:
            self.assertEqual((im.size, im.mode), ((640, 480), "RGBA"))
        self.assertEqual(os.listdir(self.dir), ["wm_640x480_silvio.png"])
        expected = watermark.text_pixel_mask(watermark.make_watermark(640, 480, SILVIO))
        self.assertTrue(np.array_equal(watermark.text_pixel_mask(p), expected))

    def test_cached_file_is_reused(self):
        p = watermark.watermark_path(self.dir, 640, 480, SILVIO)
        st = os.stat(p)
        self.assertEqual(watermark.watermark_path(self.dir, 640, 480, SILVIO), p)
        st2 = os.stat(p)
        self.assertEqual((st.st_ino, st.st_mtime_ns), (st2.st_ino, st2.st_mtime_ns))

    def test_corrupt_cache_is_regenerated(self):
        p = os.path.join(self.dir, "wm_640x480_silvio.png")
        with open(p, "wb") as f:
            f.write(b"lixo")
        watermark.watermark_path(self.dir, 640, 480, SILVIO)
        with Image.open(p) as im:
            self.assertEqual(im.size, (640, 480))

    def test_non_png_cache_is_regenerated(self):
        p = os.path.join(self.dir, "wm_640x480_silvio.png")
        Image.new("RGB", (640, 480)).save(p, format="JPEG")
        watermark.watermark_path(self.dir, 640, 480, SILVIO)
        with Image.open(p) as im:
            self.assertEqual((im.format, im.mode), ("PNG", "RGBA"))

    def test_failed_save_leaves_nothing(self):
        def half_written(img, fp, *args, **kwargs):
            with open(fp, "wb") as f:
                f.write(b"\x89PNG meio arquivo")
            raise OSError("disco cheio")

        with mock.patch.object(Image.Image, "save", half_written):
            with self.assertRaises(OSError):
                watermark.watermark_path(self.dir, 640, 480, SILVIO)
        self.assertEqual(os.listdir(self.dir), [])

    def test_changed_aviso_regenerates(self):
        p = watermark.watermark_path(self.dir, 640, 480, SILVIO)
        before = watermark.text_pixel_mask(p)
        other = Modelo("silvio", "Silvio Santos", "Silvio Santos", "Não é a voz real do Silvio")
        watermark.watermark_path(self.dir, 640, 480, other)
        self.assertFalse(np.array_equal(watermark.text_pixel_mask(p), before))

    def test_empty_aviso_raises_and_writes_nothing(self):
        with self.assertRaises(ValueError):
            watermark.watermark_path(self.dir, 640, 480, Modelo("x", "X", "X", ""))
        self.assertEqual(os.listdir(self.dir), [])


if __name__ == "__main__":
    unittest.main()
