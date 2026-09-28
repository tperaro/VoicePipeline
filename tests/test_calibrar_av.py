import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np

import calibrar_av
from studio.config import DEFAULT_ESTADO, load_estado, save_estado

SR = 48000
W, H = 640, 360
GAP_PX = 200            # distancia entre os retangulos em repouso
APPROACH_S = 0.3        # aproximacao ate o contato
HOLD_S = 0.1            # maos paradas depois do contato
SEPARATE_S = 0.4
BEEP_S = 0.04
# contatos com fases diferentes em relacao aos frames (15 e 30 fps)
CONTACTS = [1.013, 2.047, 3.071, 4.109, 5.138, 6.162]
CONTACTS_2S = [1.013, 3.047, 5.071, 7.109, 9.138, 11.162]     # uns 2 s entre as palmas, como pede o README
# (pausa no contato, separacao) da revisao: separacao tao ou mais rapida que a aproximacao (0,3 s)
FAST_SEPARATIONS = [(0.03, 0.12), (0.1, 0.2), (0.1, 0.25), (0.1, 0.3)]


def gap_at(t: float, contacts: list[float], hold_s: float = HOLD_S, separate_s: float = SEPARATE_S) -> float:
    g = float(GAP_PX)
    for c in contacts:
        if c - APPROACH_S <= t < c:
            g = min(g, GAP_PX * (c - t) / APPROACH_S)
        elif c <= t < c + hold_s:
            g = 0.0
        elif c + hold_s <= t < c + hold_s + separate_s:
            g = min(g, GAP_PX * (t - c - hold_s) / separate_s)
    return g


def draw_frame(t: float, contacts: list[float], rng, nudges=(), hold_s: float = HOLD_S,
               separate_s: float = SEPARATE_S) -> np.ndarray:
    img = np.full((H, W), 40.0)
    half = round(gap_at(t, contacts, hold_s, separate_s) / 2)
    cx = W // 2
    # "clique" sem palma: a mao direita escorrega 4 px em 0,1 s (movimento fraco que para)
    nudge = round(sum(4 * min(max((t - s) / 0.1, 0.0), 1.0) for s in nudges))
    img[200:320, cx - half - 80:cx - half] = 220          # "mao" esquerda
    img[200:320, cx + half + nudge:cx + half + nudge + 80] = 220   # "mao" direita
    # distracao: quadrado que anda sem parar no alto (nao pode virar a regiao escolhida)
    x = 20 + round(abs((150 * t) % 480 - 240))
    img[30:90, x:x + 60] = 160
    img += rng.normal(0, 2, img.shape)                     # ruido de sensor
    return np.clip(img, 0, 255).astype(np.uint8)


def make_clap_take(take_dir: str, contacts: list[float], audio_delay_s: float, fps: int,
                   duration_s: float = 7.5, nudges=(), hold_s: float = HOLD_S, separate_s: float = SEPARATE_S,
                   beep_shifts=()) -> str:
    # raw.mkv como o do gravador (MJPEG + PCM mono 48k); bip no contato (e nos nudges) atrasado audio_delay_s;
    # beep_shifts: desvio extra do bip de cada contato (palmas que discordam entre si)
    rng = np.random.default_rng(7)
    frames = [draw_frame(i / fps, contacts, rng, nudges, hold_s, separate_s) for i in range(round(duration_s * fps))]
    x = rng.normal(0, 0.003, round(duration_s * SR))
    shifts = [*beep_shifts, *[0.0] * (len(contacts) - len(beep_shifts))]
    for beep in [*(c + s for c, s in zip(contacts, shifts)), *nudges]:
        i0 = round((beep + audio_delay_s) * SR)
        n = round(BEEP_S * SR)
        x[i0:i0 + n] += 0.5 * np.sin(2 * np.pi * 1000 * np.arange(n) / SR)
    os.makedirs(take_dir, exist_ok=True)
    wav = os.path.join(take_dir, "beeps.part.wav")
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "s16le",
                    "-ar", str(SR), "-ac", "1", "-i", "-", wav], input=pcm, check=True)
    raw = os.path.join(take_dir, "raw.mkv")
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}", "-framerate", str(fps), "-i", "-",
                    "-i", wav, "-map", "0:v", "-map", "1:a", "-c:v", "mjpeg", "-q:v", "3",
                    "-c:a", "pcm_s16le", "-f", "matroska", raw],
                   input=b"".join(f.tobytes() for f in frames), check=True)
    os.remove(wav)
    return raw


def run_main(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = calibrar_av.main(list(argv))
    return rc, out.getvalue()


class OnsetTest(unittest.TestCase):
    def test_beeps_and_claps_found_to_the_sample(self):
        rng = np.random.default_rng(1)
        x = rng.normal(0, 0.002, 6 * SR)
        beeps, claps = [0.5123, 1.9001, 3.3337], [2.7004, 4.4444]
        for t in beeps:
            i = round(t * SR)
            x[i:i + 1920] += 0.4 * np.sin(2 * np.pi * 1000 * np.arange(1920) / SR)
        for t in claps:          # palma: ruido que cai rapido
            i = round(t * SR)
            x[i:i + 2400] += 0.5 * rng.normal(0, 1, 2400) * np.exp(-np.arange(2400) / 300)
        got = calibrar_av.clap_onsets(x, SR)
        self.assertEqual(len(got), 5)
        for g, t in zip(got, sorted(beeps + claps)):
            self.assertAlmostEqual(g, t, delta=0.0005)

    def test_silence_has_no_onsets(self):
        self.assertEqual(calibrar_av.clap_onsets(np.zeros(SR), SR), [])
        noise = np.random.default_rng(2).normal(0, 0.002, 2 * SR)
        self.assertEqual(calibrar_av.clap_onsets(noise, SR), [])

    def test_ringing_clap_counts_once(self):
        x = np.zeros(2 * SR)
        i = SR // 2
        x[i:i + 9600] = np.sin(2 * np.pi * 800 * np.arange(9600) / SR) * np.exp(-np.arange(9600) / 3000)
        self.assertEqual(len(calibrar_av.clap_onsets(x, SR)), 1)


class ContactTest(unittest.TestCase):
    def test_peak_then_drop_interpolates_last_interval(self):
        t = np.arange(12) / 15
        d = np.array([0, 0, 0, 2, 6, 10, 10, 4, 0, 0, 0, 0], dtype=float)
        # ultimo intervalo cheio termina no frame 6; 40 % do seguinte ate o contato
        self.assertAlmostEqual(calibrar_av.contact_time(d, t, 0.0, 0.8), t[6] + 0.4 / 15, places=9)

    def test_full_last_interval_lands_on_next_frame(self):
        t = np.arange(10) / 30
        d = np.array([0, 3, 8, 8, 0, 0, 0, 0, 0, 0], dtype=float)
        self.assertAlmostEqual(calibrar_av.contact_time(d, t, 0.0, 0.3), t[3], places=9)

    def test_motion_without_stop_is_rejected(self):
        t = np.arange(10) / 15
        d = np.array([0, 5, 9, 10, 9, 10, 9, 10, 9, 0], dtype=float)
        self.assertIsNone(calibrar_av.contact_time(d, t, 0.1, 0.5))

    def test_window_without_motion_is_rejected(self):
        t = np.arange(10) / 15
        self.assertIsNone(calibrar_av.contact_time(np.zeros(10), t, 0.0, 0.6))

    def test_first_stop_wins_over_faster_separation(self):
        # aproximacao (5), contato, separacao mais rapida (12): o contato e a 1a parada, nao a do maior pico
        t = np.arange(14) / 30
        d = np.array([0, 0, 5, 5, 5, 2, 0, 0, 12, 12, 12, 0, 0, 0], dtype=float)
        self.assertAlmostEqual(calibrar_av.contact_time(d, t, 0.0, 0.45), t[4] + 0.4 / 30, places=9)

    def test_run_from_previous_clap_is_rejected(self):
        # a corrida ja vinha de antes do inicio do ciclo desta palma: e a separacao da palma anterior
        t = np.arange(14) / 30
        d = np.array([0, 6, 6, 6, 6, 6, 6, 0, 0, 0, 0, 0, 0, 0], dtype=float)
        self.assertIsNone(calibrar_av.contact_time(d, t, 0.1, 0.45, cycle_start=0.1))
        self.assertAlmostEqual(calibrar_av.contact_time(d, t, 0.1, 0.45), t[6], places=9)


class SyntheticTakeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir15 = os.path.join(cls.tmp.name, "palmas15")
        make_clap_take(cls.dir15, CONTACTS, audio_delay_s=0.080, fps=15)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check(self, cal, expected_ms: float, tol_ms: float):
        errs = [c.offset_ms - expected_ms for c in cal.claps]
        print(f"\n  [calib] {cal.fps} fps: sugerido {cal.suggested_ms} ms (esperado {expected_ms:+.0f}), "
              f"erro por palma {[round(e, 1) for e in errs]} ms, dispersão {cal.spread_ms} ms", file=sys.stderr)
        self.assertEqual(len(cal.claps), len(CONTACTS))
        self.assertLessEqual(abs(cal.suggested_ms - expected_ms), tol_ms)
        self.assertLessEqual(max(abs(e) for e in errs), 2 * tol_ms)

    def test_15fps_audio_late_suggests_negative_offset(self):
        cal = calibrar_av.calibrate(self.dir15)
        self.check(cal, -80, 5)
        x0, y0, x1, y1 = cal.region
        self.assertGreaterEqual(y0, 150, "a região não pode pegar o quadrado que anda no alto")
        self.assertTrue(x0 < W // 2 < x1 and y0 < 260 < y1)
        self.assertEqual(len(cal.warnings), 1)
        self.assertIn("15,0 fps", cal.warnings[0])                  # aviso de fps baixo
        self.assertTrue(cal.confidence.startswith("média"))
        self.assertFalse(os.path.exists(os.path.join(self.dir15, "audio.wav")))   # nao mexe na tomada

    def test_30fps_audio_early_suggests_positive_offset(self):
        d = os.path.join(self.tmp.name, "palmas30")
        make_clap_take(d, CONTACTS, audio_delay_s=-0.045, fps=30)
        cal = calibrar_av.calibrate(d)
        self.check(cal, 45, 3)
        self.assertEqual((cal.warnings, cal.confidence), ([], "alta"))

    def test_click_without_clap_is_ignored(self):
        d = os.path.join(self.tmp.name, "clique")
        make_clap_take(d, CONTACTS, audio_delay_s=0.080, fps=30, duration_s=7.8, nudges=[7.2])
        cal = calibrar_av.calibrate(d)
        self.assertEqual(cal.audio_onsets, len(CONTACTS) + 1)
        self.check(cal, -80, 3)

    def test_main_prints_and_saves_median(self):
        estado = os.path.join(self.tmp.name, "estado.json")
        save_estado({**DEFAULT_ESTADO, "mic": "meu-mic", "av_offset_ms": 12}, estado)
        rc, out = run_main(self.dir15, "--salvar", "--estado", estado)
        self.assertEqual(rc, 0, out)
        cal = calibrar_av.calibrate(self.dir15)
        self.assertIn(f"av_offset_ms sugerido: {cal.suggested_ms:+d} ms", out)
        self.assertIn("Confiança", out)
        saved, warn = load_estado(estado)
        self.assertIsNone(warn)
        self.assertEqual((saved["av_offset_ms"], saved["mic"]), (cal.suggested_ms, "meu-mic"))

    def test_salvar_merges_and_keeps_corrupted_estado(self):
        # --salvar grava so o av_offset_ms (merge_estado): um estado.json quebrado fica guardado em .corrompido
        estado = os.path.join(self.tmp.name, "quebrado.json")
        broken = '{"drive_pasta": "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUv",}'
        with open(estado, "w", encoding="utf-8") as f:
            f.write(broken)
        rc, out = run_main(self.dir15, "--salvar", "--estado", estado)
        self.assertEqual(rc, 0, out)
        with open(estado + ".corrompido", encoding="utf-8") as f:
            self.assertEqual(broken, f.read())
        self.assertIn("cópia guardada em quebrado.json.corrompido", out)
        saved, warn = load_estado(estado)
        self.assertIsNone(warn)
        self.assertEqual(calibrar_av.calibrate(self.dir15).suggested_ms, saved["av_offset_ms"])

    def test_without_salvar_estado_is_untouched(self):
        estado = os.path.join(self.tmp.name, "intocado.json")
        rc, out = run_main(self.dir15, "--estado", estado)
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(estado))


class ContactAfterApproachTest(unittest.TestCase):
    # o contato e a parada depois da APROXIMACAO, mesmo quando as maos se afastam rapido
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def calibrate(self, name: str, contacts: list[float], expected_ms: float, **kw):
        d = os.path.join(self.tmp.name, name)
        make_clap_take(d, contacts, fps=30, **kw)
        cal = calibrar_av.calibrate(d)
        print(f"\n  [calib] {name}: sugerido {cal.suggested_ms} ms (esperado {expected_ms:+.0f}), offsets "
              f"{[c.offset_ms for c in cal.claps]} ms, dispersão {cal.spread_ms} ms, confiança {cal.confidence}",
              file=sys.stderr)
        return d, cal

    def test_fast_separation_still_finds_contact(self):
        for hold, sep in FAST_SEPARATIONS:
            with self.subTest(pausa=hold, separacao=sep):
                _, cal = self.calibrate(f"pausa{hold}_separacao{sep}", CONTACTS, -80, audio_delay_s=0.080,
                                        hold_s=hold, separate_s=sep)
                self.assertGreaterEqual(len(cal.claps), calibrar_av.MIN_CLAPS)
                self.assertLessEqual(abs(cal.suggested_ms + 80), 1000 / 30)            # +-1 frame
                self.assertLessEqual(max(abs(c.offset_ms + 80) for c in cal.claps), 1000 / 30)
                self.assertEqual(cal.confidence, "alta")

    def test_previous_clap_separation_is_not_taken_as_contact(self):
        # palmas a ~1 s, separacao lenta (0,5 s) e audio 100 ms adiantado: a separacao da palma anterior termina
        # dentro da janela desta. Essa parada nao pode virar contato (daria uns -330 ms, todos iguais)
        _, cal = self.calibrate("separacao_lenta", CONTACTS, 100, audio_delay_s=-0.1, separate_s=0.5)
        self.assertTrue(cal.claps)
        for c in cal.claps:
            self.assertLessEqual(abs(c.offset_ms - 100), 1000 / 30, c)

    def test_disagreeing_claps_are_low_confidence_and_not_saved(self):
        # 4 de 6 bips 100 ms depois: o MAD ignora ate 49 % de discordancia, mas so 4 de 6 ficam perto da mediana
        d, cal = self.calibrate("discordantes", CONTACTS, -180, audio_delay_s=0.080,
                                beep_shifts=[0.0, 0.1, 0.1, 0.1, 0.1, 0.0])
        self.assertEqual(len(cal.claps), len(CONTACTS))
        self.assertLessEqual(cal.spread_ms, calibrar_av.MAX_SPREAD_MS)    # so a dispersao passaria
        self.assertTrue(cal.confidence.startswith("baixa"), cal.confidence)
        self.assertTrue(any("4 de 6" in w for w in cal.warnings), cal.warnings)
        estado = os.path.join(self.tmp.name, "estado_discordantes.json")
        save_estado({**DEFAULT_ESTADO, "mic": "meu-mic", "av_offset_ms": 12}, estado)
        with open(estado, "rb") as f:
            before = f.read()
        rc, out = run_main(d, "--salvar", "--estado", estado)
        self.assertEqual(rc, 1, out)
        self.assertIn("Não salvei: a confiança é baixa", out)
        with open(estado, "rb") as f:
            self.assertEqual(before, f.read())
        self.assertFalse(os.path.exists(estado + ".corrompido"))

    def test_big_offset_warns_but_keeps_confidence(self):
        # audio 250 ms adiantado, palmas a ~2 s com separacao lenta (como pede o README)
        _, cal = self.calibrate("offset_grande", CONTACTS_2S, 250, audio_delay_s=-0.25, duration_s=12.5,
                                separate_s=0.5)
        self.assertEqual(len(cal.claps), len(CONTACTS_2S))
        self.assertLessEqual(abs(cal.suggested_ms - 250), 1000 / 30)
        self.assertEqual(len(cal.warnings), 1, cal.warnings)
        self.assertIn("200 ms", cal.warnings[0])
        # so avisa: um atraso real acima de 200 ms (microfone Bluetooth, por exemplo) ainda pode ser salvo
        self.assertEqual(cal.confidence, "alta")


class FewClapsTest(unittest.TestCase):
    def test_refuses_to_save_with_less_than_5_claps(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "tres")
            make_clap_take(d, CONTACTS[:3], audio_delay_s=0.0, fps=30, duration_s=4.0)
            estado = os.path.join(tmp, "estado.json")
            rc, out = run_main(d, "--salvar", "--estado", estado)
            self.assertEqual(rc, 1, out)
            self.assertIn("Poucas palmas", out)
            self.assertIn("Não salvei", out)
            self.assertIn("Confiança: baixa", out)
            self.assertFalse(os.path.exists(estado))

    def test_missing_take_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = run_main(os.path.join(tmp, "nao-existe"))
        self.assertEqual(rc, 1)
        self.assertIn("raw.mkv", out)


if __name__ == "__main__":
    unittest.main()
