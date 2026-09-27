import os
import statistics
import subprocess
import tempfile
import unittest

from tests import helpers


def stream(info, kind):
    return next(s for s in info["streams"] if s["codec_type"] == kind)


def video_pts(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "packet=pts_time", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True).stdout.split()
    return [float(x.strip(",")) for x in out if x.strip(",")]


class SyntheticTakeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        cls.late = helpers.make_synthetic_take(os.path.join(d, "late.mkv"), audio_offset_s=0.40)
        cls.early = helpers.make_synthetic_take(os.path.join(d, "early.mkv"), audio_offset_s=-0.25,
                                                size="640x360")
        cls.shifted = helpers.make_synthetic_take(os.path.join(d, "shift.mkv"), audio_offset_s=0.40,
                                                  shift_s=5.0, size="640x360")
        cls.vfr = helpers.make_synthetic_take(os.path.join(d, "vfr.mkv"), audio_offset_s=0.40,
                                              shift_s=2.0, vfr=True, size="640x360")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def assert_same_instant(self, path, flash_t):
        info = helpers.ffprobe_streams(path)
        a_start = float(stream(info, "audio")["start_time"])
        beep_abs = a_start + helpers.beep_onset_s(path)
        self.assertAlmostEqual(beep_abs, flash_t, delta=0.001)

    def test_format_matches_recorder(self):
        info = helpers.ffprobe_streams(self.late)
        v, a = stream(info, "video"), stream(info, "audio")
        self.assertEqual(info["format"]["format_name"], "matroska,webm")
        self.assertEqual((v["codec_name"], v["width"], v["height"]), ("mjpeg", 1280, 720))
        self.assertEqual(v["pix_fmt"], "yuvj422p")
        self.assertEqual(v["r_frame_rate"], "30/1")
        self.assertEqual((a["codec_name"], a["sample_rate"], a["channels"]), ("pcm_s16le", "48000", 1))
        self.assertEqual(len(video_pts(self.late)), 300)

    def test_size(self):
        v = stream(helpers.ffprobe_streams(self.early), "video")
        self.assertEqual((v["width"], v["height"]), (640, 360))

    def test_start_times_show_offset(self):
        late = helpers.ffprobe_streams(self.late)
        self.assertAlmostEqual(float(stream(late, "video")["start_time"]), 0.0, places=3)
        self.assertAlmostEqual(float(stream(late, "audio")["start_time"]), 0.40, places=3)
        early = helpers.ffprobe_streams(self.early)
        self.assertAlmostEqual(float(stream(early, "video")["start_time"]), 0.25, places=3)
        self.assertAlmostEqual(float(stream(early, "audio")["start_time"]), 0.0, places=3)
        shifted = helpers.ffprobe_streams(self.shifted)
        self.assertAlmostEqual(float(stream(shifted, "video")["start_time"]), 5.0, places=3)
        self.assertAlmostEqual(float(stream(shifted, "audio")["start_time"]), 5.40, places=3)

    def test_flash_frame_index(self):
        self.assertEqual(helpers.flash_frame_index(self.late), 90)
        self.assertEqual(helpers.flash_frame_index(self.early), 90)

    def test_beep_onset_is_audio_local(self):
        self.assertAlmostEqual(helpers.beep_onset_s(self.late), 3.0 - 0.40, delta=0.001)
        self.assertAlmostEqual(helpers.beep_onset_s(self.early), 3.0 + 0.25, delta=0.001)

    def test_beep_and_flash_same_absolute_instant(self):
        self.assert_same_instant(self.late, 3.0)
        self.assert_same_instant(self.early, 0.25 + 3.0)
        self.assert_same_instant(self.shifted, 5.0 + 3.0)

    def test_vfr_is_about_15fps(self):
        pts = video_pts(self.vfr)
        span = pts[-1] - pts[0]
        self.assertTrue(14.0 <= len(pts) / span <= 17.0, f"{len(pts)} pacotes em {span:.3f} s")
        gaps = [b - a for a, b in zip(pts, pts[1:])]
        self.assertAlmostEqual(statistics.median(gaps), 1 / 15, delta=0.002)

    def test_vfr_flash_keeps_absolute_instant(self):
        pts = video_pts(self.vfr)
        flash_t = pts[helpers.flash_frame_index(self.vfr)]
        self.assertAlmostEqual(flash_t, 2.0 + 3.0, delta=0.001)
        self.assert_same_instant(self.vfr, flash_t)

    def test_beep_onset_on_wav(self):
        wav = os.path.join(self.tmp.name, "late.wav")
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", self.late, "-map", "0:a",
                        "-c:a", "pcm_s16le", wav], check=True)
        self.assertAlmostEqual(helpers.beep_onset_s(wav), 2.60, delta=0.001)

    def test_rejects_beep_outside_audio(self):
        with self.assertRaises(ValueError):
            helpers.make_synthetic_take(os.path.join(self.tmp.name, "x.mkv"), audio_offset_s=4.0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "x.mkv")))


if __name__ == "__main__":
    unittest.main()
