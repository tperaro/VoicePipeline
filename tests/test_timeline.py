import os
import random
import unittest

from studio import timeline
from studio.timeline import AudioFit

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
GENERALPLUS = os.path.join(FIXTURES, "drift_usb-Generalplus_Usb_Audio_Device.framemd5")
ME6S = os.path.join(FIXTURES, "drift_usb-ME6S_c1_USB_AUDIO.framemd5")
SR = 48000


def drift_ppm(fit: AudioFit, sr: int = SR) -> float:
    # >0: o relogio do sistema anda mais rapido que as amostras (mic lento)
    return (sr / fit.taxa_real - 1) * 1e6


def packets(duration_s: float, start: float = 100.0, pkt_samples: int = 2400, rate: float = SR,
            jitter_s: float = 0.0005, seed: int = 1) -> tuple[list[float], list[int]]:
    # pts ideais de um mic a `rate` amostras por segundo real, com ruido de +-jitter_s
    rnd = random.Random(seed)
    n = int(duration_s * SR / pkt_samples)
    pts = [start + i * pkt_samples / rate + rnd.uniform(-jitter_s, jitter_s) for i in range(n)]
    return pts, [pkt_samples * 2] * n


class ParseFramemd5Test(unittest.TestCase):
    def test_reads_real_table(self):
        pts, sizes = timeline.parse_framemd5(GENERALPLUS)
        self.assertEqual(len(pts), 4798)
        self.assertEqual(len(sizes), 4798)
        self.assertEqual(set(sizes), {4800})
        self.assertAlmostEqual(pts[0], 1790391080.296582, places=5)
        self.assertAlmostEqual(pts[1] - pts[0], 0.05, places=6)


class RealDriftTest(unittest.TestCase):
    def test_generalplus(self):
        fit = timeline.fit_audio_clock(*timeline.parse_framemd5(GENERALPLUS))
        self.assertAlmostEqual(drift_ppm(fit), 48.6, delta=3)
        self.assertEqual(fit.gaps, 0)
        self.assertLess(fit.residuo_ms, 2.0)
        self.assertAlmostEqual(fit.duracao, 239.9, delta=0.1)

    def test_me6s(self):
        fit = timeline.fit_audio_clock(*timeline.parse_framemd5(ME6S))
        self.assertAlmostEqual(drift_ppm(fit), -56.1, delta=3)
        self.assertEqual(fit.gaps, 0)
        self.assertLess(fit.residuo_ms, 2.0)
        self.assertAlmostEqual(fit.duracao, 240.0, delta=0.1)

    def test_start_comes_from_steady_line_not_first_packet(self):
        # sonda deriva-mic: 1o pacote -10,6 ms (Generalplus) e +3,2 ms (ME6S) fora da reta estavel
        pts, sizes = timeline.parse_framemd5(GENERALPLUS)
        fit = timeline.fit_audio_clock(pts, sizes)
        self.assertAlmostEqual((fit.inicio - pts[0]) * 1000, 10.6, delta=3)
        pts, sizes = timeline.parse_framemd5(ME6S)
        fit = timeline.fit_audio_clock(pts, sizes)
        self.assertAlmostEqual((fit.inicio - pts[0]) * 1000, -3.2, delta=3)


class FitSyntheticPacketsTest(unittest.TestCase):
    def test_clean_clock(self):
        pts, sizes = packets(10.0)
        fit = timeline.fit_audio_clock(pts, sizes)
        self.assertAlmostEqual(fit.inicio, 100.0, delta=0.001)
        self.assertAlmostEqual(fit.taxa_real, SR, delta=2)
        self.assertEqual(fit.gaps, 0)
        self.assertLess(fit.residuo_ms, 0.5)
        self.assertAlmostEqual(fit.duracao, 10.0, places=6)

    def test_first_packet_late_80ms(self):
        pts, sizes = packets(10.0)
        pts[0] += 0.080
        fit = timeline.fit_audio_clock(pts, sizes)
        self.assertAlmostEqual(fit.inicio, 100.0, delta=0.002)
        self.assertEqual(fit.gaps, 0)

    def test_settling_start_is_ignored(self):
        # comeco "assentando": os 20 primeiros pacotes chegam 4 ms mais juntos (vies de 80 ms no 1o)
        pts, sizes = packets(10.0)
        pts = [p + max(0, 20 - i) * 0.004 for i, p in enumerate(pts)]
        fit = timeline.fit_audio_clock(pts, sizes)
        self.assertAlmostEqual(fit.inicio, 100.0, delta=0.002)
        self.assertEqual(fit.gaps, 0)

    def test_drift_recovered(self):
        pts, sizes = packets(60.0, rate=SR * (1 + 700e-6))
        fit = timeline.fit_audio_clock(pts, sizes)
        self.assertAlmostEqual(fit.taxa_real, SR * (1 + 700e-6), delta=1)
        self.assertAlmostEqual(fit.inicio, 100.0, delta=0.001)

    def test_gap_is_counted_and_does_not_bend_the_line(self):
        pts, sizes = packets(10.0)
        pts = [p + (0.5 if i >= 100 else 0.0) for i, p in enumerate(pts)]   # buraco de 0,5 s em t=5 s
        fit = timeline.fit_audio_clock(pts, sizes)
        self.assertEqual(fit.gaps, 1)
        self.assertAlmostEqual(fit.inicio, 100.0, delta=0.002)
        self.assertAlmostEqual(fit.taxa_real, SR, delta=10)
        self.assertLess(fit.residuo_ms, 1.0)

    def test_short_take_uses_packets_after_half_second(self):
        # 3 s: a janela de 2 s sobraria so 1 s; usa > 0,5 s e ignora o vies do comeco
        pts, sizes = packets(3.0)
        pts[0] += 0.060
        pts[1] += 0.030
        fit = timeline.fit_audio_clock(pts, sizes)
        self.assertAlmostEqual(fit.inicio, 100.0, delta=0.002)
        self.assertEqual(fit.gaps, 0)

    def test_very_short_take_uses_all_packets(self):
        pts, sizes = packets(0.5)
        self.assertEqual(len(pts), 10)
        fit = timeline.fit_audio_clock(pts, sizes)
        self.assertAlmostEqual(fit.inicio, 100.0, delta=0.002)
        self.assertAlmostEqual(fit.duracao, 0.5, places=6)

    def test_implausible_slope_keeps_nominal_rate(self):
        # tomada curtissima ainda assentando: inclinacao absurda nao vira asetrate
        pts = [100.0 + i * 0.047 for i in range(12)]
        fit = timeline.fit_audio_clock(pts, [4800] * 12)
        self.assertEqual(fit.taxa_real, SR)

    def test_single_packet(self):
        fit = timeline.fit_audio_clock([7.25], [4800])
        self.assertEqual(fit.inicio, 7.25)
        self.assertEqual(fit.taxa_real, SR)
        self.assertEqual(fit.gaps, 0)
        self.assertAlmostEqual(fit.duracao, 0.05)

    def test_bytes_per_sample(self):
        pts, sizes = packets(10.0)
        fit = timeline.fit_audio_clock(pts, [s * 2 for s in sizes], bytes_per_sample=4)
        self.assertAlmostEqual(fit.taxa_real, SR, delta=2)
        self.assertAlmostEqual(fit.duracao, 10.0, places=6)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            timeline.fit_audio_clock([], [])


def make_fit(inicio=0.0, taxa_real=48000.0, gaps=0, residuo_ms=0.3, duracao=10.0) -> AudioFit:
    return AudioFit(inicio=inicio, taxa_real=taxa_real, gaps=gaps, residuo_ms=residuo_ms, duracao=duracao)


class AlignedAudioFilterTest(unittest.TestCase):
    def test_audio_late_gets_delay_in_samples(self):
        af = timeline.aligned_audio_filter(make_fit(inicio=0.4), 0.033)
        self.assertEqual(af, "asetpts=N/SR/TB,adelay=17616S:all=1,asetpts=N/SR/TB")

    def test_audio_early_gets_trim_in_samples(self):
        af = timeline.aligned_audio_filter(make_fit(inicio=0.0), 0.283)
        self.assertEqual(af, "asetpts=N/SR/TB,atrim=start_sample=13584,asetpts=N/SR/TB")

    def test_no_shift(self):
        self.assertEqual(timeline.aligned_audio_filter(make_fit(inicio=1.5), 1.5), "asetpts=N/SR/TB")

    def test_small_drift_is_ignored(self):
        # 100 ppm em 10 s = 1 ms < 5 ms
        af = timeline.aligned_audio_filter(make_fit(inicio=0.1, taxa_real=48004.8), 0.0)
        self.assertNotIn("asetrate", af)

    def test_drift_over_5ms_resamples_before_the_shift(self):
        # 56 ppm em 300 s = 16,8 ms
        af = timeline.aligned_audio_filter(make_fit(inicio=0.1, taxa_real=47997.3, duracao=300.0), 0.0)
        self.assertEqual(af, "asetpts=N/SR/TB,asetrate=47997,aresample=48000:resampler=soxr,"
                             "adelay=4800S:all=1,asetpts=N/SR/TB")

    def test_drift_threshold_edge(self):
        below = make_fit(taxa_real=48000 + 48000 * 0.0049 / 10, duracao=10.0)
        above = make_fit(taxa_real=48000 + 48000 * 0.0051 / 10, duracao=10.0)
        self.assertNotIn("asetrate", timeline.aligned_audio_filter(below, 0.0))
        self.assertIn("asetrate=48024,", timeline.aligned_audio_filter(above, 0.0))

    def test_gaps_switch_to_async_from_file_time_zero(self):
        # async posiciona pelos pts (amostra 0 = tempo 0 do arquivo): so falta cortar ate a ancora
        af = timeline.aligned_audio_filter(make_fit(inicio=0.4, taxa_real=47990.0, gaps=1, duracao=300.0), 0.033)
        self.assertEqual(af, "aresample=async=1:min_hard_comp=0.03:first_pts=0,"
                             "atrim=start_sample=1584,asetpts=N/SR/TB")

    def test_other_sample_rate(self):
        af = timeline.aligned_audio_filter(make_fit(inicio=0.5, taxa_real=44100.0), 0.0, sr=44100)
        self.assertEqual(af, "asetpts=N/SR/TB,adelay=22050S:all=1,asetpts=N/SR/TB")


if __name__ == "__main__":
    unittest.main()
