import unittest

from studio import render
from studio.config import Modelo, get_modelo

SILVIO = get_modelo("silvio")

ANCORA = 0.132          # pts do 2o pacote de video (take.json da spec 3.2)
SPEC_FILTER_300 = (
    "[0:v]trim=start=0.131500,setpts=PTS-STARTPTS,fps=30,tpad=stop_mode=clone:stop=2,"
    "trim=end_frame=300,setpts=PTS-STARTPTS,format=yuv420p[v0];"
    "[2:v]format=yuva420p[wm];[v0][wm]overlay=0:0:format=yuv420,format=yuv420p[v];"
    "[1:a]aresample=48000:resampler=soxr,asetpts=N/SR/TB,"
    "apad=whole_len=480000,atrim=end_sample=480000,asetpts=N/SR/TB[a]"
)


class OffsetFilterTest(unittest.TestCase):
    def test_zero_is_empty(self):
        self.assertEqual(render.offset_filter(0), "")
        self.assertEqual(render.offset_filter(0.004), "")      # arredonda para 0 amostra

    def test_positive_delays_audio(self):
        self.assertEqual(render.offset_filter(100), ",adelay=4800S:all=1")
        self.assertEqual(render.offset_filter(10, sr=40000), ",adelay=400S:all=1")

    def test_negative_trims_audio(self):
        self.assertEqual(render.offset_filter(-250), ",atrim=start_sample=12000")


class BuildFilterTest(unittest.TestCase):
    def test_matches_spec_without_offset(self):
        self.assertEqual(render.build_filter(300, ANCORA), SPEC_FILTER_300)

    def test_offset_goes_before_apad(self):
        fc = render.build_filter(119, ANCORA, -100)
        self.assertIn("asetpts=N/SR/TB,atrim=start_sample=4800,apad=whole_len=190400,"
                      "atrim=end_sample=190400,asetpts=N/SR/TB[a]", fc)
        self.assertIn("trim=end_frame=119,", fc)
        self.assertIn(",adelay=4800S:all=1,apad=", render.build_filter(119, ANCORA, 100))

    def test_trims_by_anchor_pts(self):
        # pelo pts (nao pelo indice do frame decodificado): o pacote 0 que nao decodifica nao empurra o video
        fc = render.build_filter(119, 1.049)
        self.assertTrue(fc.startswith("[0:v]trim=start=1.048500,setpts=PTS-STARTPTS,fps=30,"), fc)
        self.assertNotIn("start_frame", fc)

    def test_rejects_empty_video(self):
        with self.assertRaises(ValueError):
            render.build_filter(0, ANCORA)


class BuildRenderCmdTest(unittest.TestCase):
    def test_nvenc_argv_matches_spec(self):
        cmd = render.build_render_cmd("/t/raw.mkv", "/t/silvio.wav", "/t/wm.png", "/t/render.part.mp4", 300,
                                     ANCORA, SILVIO)
        self.assertEqual(cmd, [
            "nice", "-n", "10",
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-copyts",
            "-i", "/t/raw.mkv", "-i", "/t/silvio.wav", "-i", "/t/wm.png",
            "-filter_complex", SPEC_FILTER_300,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "21", "-b:v", "0",
            "-maxrate", "6M", "-bufsize", "12M", "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", "30",
            "-colorspace", "smpte170m", "-color_primaries", "smpte170m", "-color_trc", "smpte170m",
            "-color_range", "tv",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "1", "-movflags", "+faststart",
            "-metadata", "title=Paródia/homenagem - voz gerada por IA",
            "-metadata", "comment=Voz sintética gerada por IA (conversão RVC). Não é a voz real de Silvio Santos.",
            "-metadata", "description=AI-generated/synthetic voice (RVC voice conversion). Parody/tribute. "
                         "Not the real voice of Silvio Santos.",
            "-f", "mp4", "/t/render.part.mp4"])

    def test_copyts_before_first_input(self):
        # com -copyts o trim ve o mesmo pts do ffprobe (a ancora), sem o ffmpeg zerar o inicio do arquivo
        for enc in render.ENCODERS:
            cmd = render.build_render_cmd("r.mkv", "c.wav", "w.png", "o.mp4", 30, ANCORA, SILVIO, encoder=enc)
            self.assertEqual(cmd.count("-copyts"), 1)
            self.assertLess(cmd.index("-copyts"), cmd.index("-i"))

    def test_x264_fallback_flags(self):
        cmd = render.build_render_cmd("r.mkv", "c.wav", "w.png", "o.mp4", 30, ANCORA, SILVIO, encoder="x264")
        i = cmd.index("-c:v")
        self.assertEqual(cmd[i:i + 14], ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                                         "-maxrate", "6M", "-bufsize", "12M", "-profile:v", "high",
                                         "-pix_fmt", "yuv420p"])
        self.assertNotIn("h264_nvenc", cmd)

    def test_offset_reaches_filter(self):
        cmd = render.build_render_cmd("r.mkv", "c.wav", "w.png", "o.mp4", 30, ANCORA, SILVIO, av_offset_ms=100)
        self.assertIn(",adelay=4800S:all=1,", cmd[cmd.index("-filter_complex") + 1])

    def test_never_shortest_nor_setpriv(self):
        # apad + -shortest trava o ffmpeg 6.1.1; o setpriv entra no procs.spawn
        for enc in render.ENCODERS:
            cmd = render.build_render_cmd("r.mkv", "c.wav", "w.png", "o.mp4", 30, ANCORA, SILVIO, encoder=enc)
            self.assertNotIn("-shortest", cmd)
            self.assertEqual(cmd[:4], ["nice", "-n", "10", "ffmpeg"])

    def test_rejects_unknown_encoder(self):
        with self.assertRaises(ValueError):
            render.build_render_cmd("r.mkv", "c.wav", "w.png", "o.mp4", 30, ANCORA, SILVIO, encoder="vaapi")

    def test_rejects_model_without_warning(self):
        mudo = Modelo("mudo", "Mudo", "Mudo", "")
        with self.assertRaises(ValueError):
            render.build_render_cmd("r.mkv", "c.wav", "w.png", "o.mp4", 30, ANCORA, mudo)


class TimeoutTest(unittest.TestCase):
    def test_five_times_duration_plus_60s(self):
        self.assertEqual(render.render_timeout(300), 110.0)
        self.assertEqual(render.render_timeout(9000), 1560.0)


if __name__ == "__main__":
    unittest.main()
