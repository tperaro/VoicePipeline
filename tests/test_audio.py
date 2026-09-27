import glob
import os
import subprocess
import tempfile
import unittest
import wave
from unittest import mock

from studio import audio
from studio.config import Modelo, get_modelo, metadata_tags
from studio.procs import ProcError
from tests import helpers


def make_wav(path: str, amp: float, sr: int = 48000, dur: float = 2.0, *extra: str) -> str:
    # senoide de 440 Hz mono PCM16 com amplitude amp (1.0 = 0 dBFS)
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"aevalsrc={amp}*sin(2*PI*440*t):s={sr}:c=mono:d={dur}", *extra,
                    "-c:a", "pcm_s16le", path], check=True, stdin=subprocess.DEVNULL, capture_output=True)
    return path


def audio_stream(path: str) -> dict:
    return next(s for s in helpers.ffprobe_streams(path)["streams"] if s["codec_type"] == "audio")


def leftovers(folder: str) -> list[str]:
    return glob.glob(os.path.join(folder, "*.part*"))


class VolumedetectTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_sine_levels(self):
        mean, peak = audio.volumedetect(make_wav(os.path.join(self.tmp, "s.wav"), 0.5))
        self.assertAlmostEqual(peak, -6.0, delta=0.15)
        self.assertAlmostEqual(mean, -9.0, delta=0.15)

    def test_reads_audio_stream_of_mkv(self):
        mkv = helpers.make_synthetic_take(os.path.join(self.tmp, "raw.mkv"), duration_s=2.0, flash_frame=30)
        mean, peak = audio.volumedetect(mkv)
        self.assertAlmostEqual(peak, -1.8, delta=0.3)     # bip de 0.8 + ruido
        self.assertLess(mean, -15.0)

    def test_failures_give_none(self):
        self.assertEqual(audio.volumedetect(os.path.join(self.tmp, "nada.wav")), (None, None))
        empty = os.path.join(self.tmp, "vazio.wav")
        with wave.open(empty, "wb") as w:                  # so cabecalho, 0 amostras
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
        self.assertEqual(audio.volumedetect(empty), (None, None))
        video_only = os.path.join(self.tmp, "v.mkv")
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=d=1",
                        "-c:v", "mjpeg", video_only], check=True, capture_output=True)
        self.assertEqual(audio.volumedetect(video_only), (None, None))


class BoostVolumeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_loud_sine_is_limited_below_full_scale(self):
        src = make_wav(os.path.join(self.tmp, "s.wav"), 0.5)          # -6 dBFS
        out = os.path.join(self.tmp, "boosted.wav")
        audio.boost_volume(src, out, 15)                              # +9 dBFS sem limitador
        mean, peak = audio.volumedetect(out)
        self.assertLessEqual(peak, -0.2)
        self.assertGreater(mean, -4.0)
        st = audio_stream(out)
        self.assertEqual((st["codec_name"], st["sample_rate"], st["channels"]), ("pcm_s16le", "48000", 1))
        self.assertEqual(st["duration_ts"], audio_stream(src)["duration_ts"])
        self.assertEqual(leftovers(self.tmp), [])

    def test_quiet_sine_gets_exact_gain(self):
        # abaixo do limite o limitador e transparente (sem auto level)
        src = make_wav(os.path.join(self.tmp, "q.wav"), 0.0316)       # -30 dBFS
        out = os.path.join(self.tmp, "boosted.wav")
        audio.boost_volume(src, out, 15)
        self.assertAlmostEqual(audio.volumedetect(out)[1], -15.0, delta=0.15)

    def test_beep_not_shifted(self):
        mkv = helpers.make_synthetic_take(os.path.join(self.tmp, "raw.mkv"), duration_s=3.0, flash_frame=45)
        out = os.path.join(self.tmp, "boosted.wav")
        audio.boost_volume(mkv, out, 15)
        shift = helpers.beep_onset_s(out) - helpers.beep_onset_s(mkv)
        self.assertLessEqual(abs(shift), 0.001)

    def test_replaces_existing_output(self):
        src = make_wav(os.path.join(self.tmp, "s.wav"), 0.1)
        out = os.path.join(self.tmp, "boosted.wav")
        with open(out, "wb") as f:
            f.write(b"lixo antigo")
        audio.boost_volume(src, out, 6)
        with wave.open(out, "rb") as w:
            self.assertEqual(w.getnframes(), 96000)
        self.assertEqual(leftovers(self.tmp), [])

    def test_failure_keeps_old_output(self):
        out = os.path.join(self.tmp, "boosted.wav")
        with open(out, "wb") as f:
            f.write(b"anterior")
        with self.assertRaises(ProcError) as cm:
            audio.boost_volume(os.path.join(self.tmp, "nada.wav"), out, 15)
        self.assertIn("volume", cm.exception.message)
        self.assertNotEqual(cm.exception.rc, 0)
        with open(out, "rb") as f:
            self.assertEqual(f.read(), b"anterior")
        self.assertEqual(leftovers(self.tmp), [])

    def test_invalid_gain(self):
        with mock.patch("studio.audio.run") as run:
            for bad in (float("nan"), float("inf")):
                with self.assertRaises(ValueError):
                    audio.boost_volume("/x/in.wav", "/x/out.wav", bad)
        run.assert_not_called()


class ExportMp3Test(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        # saida do RVC do silvio e 40 kHz, taxa que o MP3 nao tem
        self.wav = make_wav(os.path.join(self.tmp, "silvio.wav"), 0.3, 40000, 3.0, "-metadata", "artist=Fulano")

    def tearDown(self):
        self._tmp.cleanup()

    def test_id3v23_tags_with_ai_notice(self):
        modelo = get_modelo("silvio")
        mp3 = os.path.join(self.tmp, "silvio_IA.mp3")
        audio.export_mp3(self.wav, mp3, modelo)
        with open(mp3, "rb") as f:
            self.assertEqual(f.read(4), b"ID3\x03")
        info = helpers.ffprobe_streams(mp3)
        tags = info["format"]["tags"]
        expected = metadata_tags(modelo)
        self.assertEqual(tags["title"], expected["title"])
        self.assertEqual(tags["comment"], expected["comment"])
        self.assertIn("IA", tags["comment"])
        self.assertIn("Não é a voz real de Silvio Santos", tags["comment"])
        self.assertNotIn("artist", tags)                   # nada herdado do WAV
        st = audio_stream(mp3)
        self.assertEqual(st["codec_name"], "mp3")
        self.assertIn(int(st["sample_rate"]), (32000, 44100, 48000))
        self.assertAlmostEqual(float(info["format"]["duration"]), 3.0, delta=0.1)
        self.assertEqual(leftovers(self.tmp), [])

    def test_tags_follow_model(self):
        mp3 = os.path.join(self.tmp, "orochi_IA.mp3")
        audio.export_mp3(self.wav, mp3, get_modelo("orochi"))
        tags = helpers.ffprobe_streams(mp3)["format"]["tags"]
        self.assertIn("Não é a voz real do Orochi", tags["comment"])

    def test_model_without_notice_is_refused(self):
        mp3 = os.path.join(self.tmp, "x_IA.mp3")
        with self.assertRaises(ValueError):
            audio.export_mp3(self.wav, mp3, Modelo("x", "X", "X", " "))
        self.assertFalse(os.path.exists(mp3))
        self.assertEqual(leftovers(self.tmp), [])

    def test_failure_leaves_nothing(self):
        mp3 = os.path.join(self.tmp, "silvio_IA.mp3")
        with self.assertRaises(ProcError) as cm:
            audio.export_mp3(os.path.join(self.tmp, "nada.wav"), mp3, get_modelo("silvio"))
        self.assertIn("MP3", cm.exception.message)
        self.assertFalse(os.path.exists(mp3))
        self.assertEqual(leftovers(self.tmp), [])


if __name__ == "__main__":
    unittest.main()
