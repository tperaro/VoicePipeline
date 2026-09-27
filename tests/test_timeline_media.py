import dataclasses
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import soundfile as sf

from studio import timeline
from studio.procs import ProcError
from tests import helpers

FPS = 30


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
                   stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=120)


def mjpeg_video(path: str, duration_s: float, flash_frame: int) -> None:
    ffmpeg("-f", "lavfi", "-i", f"testsrc2=s=640x360:r={FPS}:d={duration_s}",
           "-vf", f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='eq(n,{flash_frame})'",
           "-fps_mode", "passthrough", "-c:v", "mjpeg", "-q:v", "3", "-pix_fmt", "yuvj422p", path)


def beep_expr(beep_t: float) -> str:
    return f"aevalsrc='0.01*(2*random(0)-1)+0.8*sin(2*PI*1000*t)*between(t,{beep_t:.6f},{beep_t + 0.05:.6f})'"


def make_gap_take(path: str) -> None:
    # 8 s; flash no frame 150 (5,0 s) e bip em 5,0 s; o aselect descarta ~0,5 s de audio em 3,0 s
    # mantendo os pts (buraco de timestamp igual a um xrun do mic)
    v = path + ".v.mkv"
    mjpeg_video(v, 8.0, 150)
    ffmpeg("-i", v, "-f", "lavfi", "-i", f"{beep_expr(5.0)}:s=48000:c=mono:d=8",
           "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-af", "aselect='not(between(t,3,3.5))'",
           "-c:a", "pcm_s16le", "-f", "matroska", path)
    os.remove(v)


def make_drift_take(path: str, ppm: float) -> None:
    # mic com taxa real 48000*(1+ppm): o bip do instante 11,0 s cai na amostra 11,0*taxa_real e os pts
    # dos pacotes andam no relogio do sistema (-itsscale). Audio comeca 0,4 s depois do video.
    e = ppm * 1e-6
    v, a = path + ".v.mkv", path + ".a.wav"
    mjpeg_video(v, 12.0, 330)
    ffmpeg("-f", "lavfi", "-i", f"{beep_expr(11.0 * (1 + e) - 0.4)}:s=48000:c=mono:d=11.7",
           "-c:a", "pcm_s16le", a)
    ffmpeg("-i", v, "-itsscale", f"{1 / (1 + e):.12f}", "-itsoffset", "0.4", "-i", a,
           "-map", "0:v", "-map", "1:a", "-c", "copy", "-f", "matroska", path)
    os.remove(v)
    os.remove(a)


def flash_pts(path: str) -> float:
    return timeline.read_packets(path, "v")[helpers.flash_frame_index(path)][0]


class VideoInfoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        cls.cfr = helpers.make_synthetic_take(os.path.join(d, "cfr.mkv"), audio_offset_s=0.40, size="640x360")
        cls.vfr = helpers.make_synthetic_take(os.path.join(d, "vfr.mkv"), audio_offset_s=0.40, shift_s=2.0,
                                              vfr=True, size="640x360")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_read_packets(self):
        v = timeline.read_packets(self.cfr, "v")
        self.assertEqual(len(v), 300)
        self.assertEqual(v[0][0], 0.0)
        self.assertAlmostEqual(v[1][0], 1 / FPS, delta=0.001)
        self.assertAlmostEqual(v[-1][1], 1 / FPS, delta=0.001)
        self.assertTrue(all(size > 0 for _, _, size in v))
        a = timeline.read_packets(self.cfr, "a")
        self.assertAlmostEqual(a[0][0], 0.40, places=3)
        self.assertEqual(sum(size for _, _, size in a) // 2, round(9.6 * 48000))

    def test_read_packets_rejects_unknown_stream(self):
        with self.assertRaises(ValueError):
            timeline.read_packets(self.cfr, "s")

    def test_cfr_anchor_is_second_packet(self):
        vi = timeline.video_info(self.cfr)
        pk = timeline.read_packets(self.cfr, "v")
        self.assertEqual((vi.w, vi.h), (640, 360))
        self.assertEqual(vi.ancora_pts, pk[1][0])
        self.assertEqual(vi.n_frames, round((pk[-1][0] + pk[-1][1] - pk[1][0]) * FPS))
        self.assertEqual(vi.n_frames, 299)           # frames 1..299 (o 1o fica de fora)
        self.assertAlmostEqual(vi.fps_medido, 30.0, delta=0.5)

    def test_vfr(self):
        vi = timeline.video_info(self.vfr)
        pk = timeline.read_packets(self.vfr, "v")
        self.assertEqual(vi.ancora_pts, pk[1][0])
        self.assertAlmostEqual(vi.ancora_pts, 2.0 + 2 / FPS, delta=0.001)   # frame 1 caiu no select
        self.assertEqual(vi.n_frames, round((pk[-1][0] + pk[-1][1] - pk[1][0]) * FPS))
        self.assertTrue(14.0 <= vi.fps_medido <= 17.0, vi.fps_medido)

    def test_fps_out(self):
        pk = timeline.read_packets(self.cfr, "v")
        vi = timeline.video_info(self.cfr, fps_out=15)
        self.assertEqual(vi.n_frames, round((pk[-1][0] + pk[-1][1] - pk[1][0]) * 15))

    def test_audio_only_file_has_no_video(self):
        wav = os.path.join(self.tmp.name, "only.wav")
        ffmpeg("-f", "lavfi", "-i", "sine=f=440:d=1", "-c:a", "pcm_s16le", wav)
        with self.assertRaises(ProcError) as cm:
            timeline.video_info(wav)
        self.assertIn("não tem vídeo", cm.exception.message)


class ExtractAlignedAudioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name

        def take(name, **kw):
            return helpers.make_synthetic_take(os.path.join(d, name), size="640x360", **kw)

        cls.late = take("late.mkv", audio_offset_s=0.40)
        cls.early = take("early.mkv", audio_offset_s=-0.25)
        cls.shifted = take("shift.mkv", audio_offset_s=0.40, shift_s=5.0)
        cls.vfr = take("vfr.mkv", audio_offset_s=0.40, shift_s=2.0, vfr=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def extract(self, raw):
        out = os.path.join(self.tmp.name, os.path.basename(raw) + ".audio.wav")
        vi, fit = timeline.extract_aligned_audio(raw, out)
        return out, vi, fit

    def assert_beep_on_flash(self, raw):
        # t=0 do WAV = ancora (2o pacote de video), igual ao render (trim=start_frame=1)
        out, vi, fit = self.extract(raw)
        expected = flash_pts(raw) - vi.ancora_pts
        self.assertAlmostEqual(helpers.beep_onset_s(out), expected, delta=0.001)
        return out, vi, fit, expected

    def test_audio_late(self):
        out, vi, fit, expected = self.assert_beep_on_flash(self.late)
        self.assertAlmostEqual(expected, (90 - 1) / FPS, delta=0.001)
        self.assertAlmostEqual(fit.inicio, 0.40, delta=0.001)
        self.assertEqual(fit.gaps, 0)
        self.assertNotIn("asetrate", timeline.aligned_audio_filter(fit, vi.ancora_pts))

    def test_audio_early(self):
        _, _, fit, expected = self.assert_beep_on_flash(self.early)
        self.assertAlmostEqual(expected, (90 - 1) / FPS, delta=0.001)
        self.assertAlmostEqual(fit.inicio, 0.0, delta=0.001)

    def test_shifted_5s(self):
        _, vi, _, expected = self.assert_beep_on_flash(self.shifted)
        self.assertAlmostEqual(vi.ancora_pts, 5.0 + 1 / FPS, delta=0.001)
        self.assertAlmostEqual(expected, (90 - 1) / FPS, delta=0.001)

    def test_vfr(self):
        _, _, _, expected = self.assert_beep_on_flash(self.vfr)
        self.assertAlmostEqual(expected, (90 - 2) / FPS, delta=0.001)   # 2o pacote = frame 2

    def test_output_is_mono_48k_pcm16_without_part(self):
        out, _, _ = self.extract(self.late)
        info = sf.info(out)
        self.assertEqual((info.samplerate, info.channels, info.subtype), (48000, 1, "PCM_16"))
        self.assertEqual([f for f in os.listdir(self.tmp.name) if ".part" in f], [])

    def test_gap_uses_async_and_keeps_audio_after_the_hole(self):
        raw = os.path.join(self.tmp.name, "gap.mkv")
        make_gap_take(raw)
        out, vi, fit, expected = self.assert_beep_on_flash(raw)
        self.assertEqual(fit.gaps, 1)
        self.assertTrue(timeline.aligned_audio_filter(fit, vi.ancora_pts).startswith(timeline.ASYNC))
        self.assertAlmostEqual(expected, 5.0 - 1 / FPS, delta=0.001)
        # controle: sem o async o bip cairia ~0,5 s antes
        plain = os.path.join(self.tmp.name, "gap_plain.wav")
        n = round(vi.ancora_pts * 48000)
        ffmpeg("-i", raw, "-map", "0:a:0", "-af", f"asetpts=N/SR/TB,atrim=start_sample={n},asetpts=N/SR/TB",
               "-c:a", "pcm_s16le", plain)
        self.assertLess(helpers.beep_onset_s(plain), expected - 0.4)

    def test_drift_is_corrected(self):
        raw = os.path.join(self.tmp.name, "drift.mkv")
        make_drift_take(raw, 700.0)
        out, vi, fit, expected = self.assert_beep_on_flash(raw)
        self.assertAlmostEqual(fit.taxa_real, 48000 * (1 + 700e-6), delta=2)
        self.assertIn("asetrate=48034,aresample=48000:resampler=soxr",
                      timeline.aligned_audio_filter(fit, vi.ancora_pts))
        # controle: sem corrigir a taxa o bip erra mais de 5 ms
        nominal = dataclasses.replace(fit, taxa_real=48000.0)
        plain = os.path.join(self.tmp.name, "drift_plain.wav")
        ffmpeg("-i", raw, "-map", "0:a:0", "-af", timeline.aligned_audio_filter(nominal, vi.ancora_pts),
               "-c:a", "pcm_s16le", plain)
        self.assertGreater(abs(helpers.beep_onset_s(plain) - expected), 0.005)

    def test_ffmpeg_failure_keeps_old_output_and_leaves_no_part(self):
        out = os.path.join(self.tmp.name, "keep.wav")
        with open(out, "wb") as f:
            f.write(b"old")
        with mock.patch.object(timeline, "aligned_audio_filter", return_value="filtro_que_nao_existe"):
            with self.assertRaises(ProcError) as cm:
                timeline.extract_aligned_audio(self.late, out)
        self.assertIn("áudio", cm.exception.message)
        with open(out, "rb") as f:
            self.assertEqual(f.read(), b"old")
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "keep.part.wav")))

    def test_video_without_audio(self):
        raw = os.path.join(self.tmp.name, "mute.mkv")
        ffmpeg("-i", self.late, "-map", "0:v", "-c", "copy", raw)
        with self.assertRaises(ProcError) as cm:
            timeline.extract_aligned_audio(raw, os.path.join(self.tmp.name, "mute.wav"))
        self.assertIn("não tem áudio", cm.exception.message)


if __name__ == "__main__":
    unittest.main()
