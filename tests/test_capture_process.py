import glob
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from studio import capture, procs
from tests import helpers

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fakebin", "fake_ffmpeg.py")
FAST = {"t_q": 0.4, "t_close": 1.0, "t_term": 0.4}


def fake_argv(mode: str) -> list[str]:
    # o env faz exec: o pid continua sendo o do "ffmpeg" (como o setpriv)
    return ["env", f"FAKE_MODE={mode}", helpers.PY, FAKE, "-hide_banner", "-i", "ignorado"]


def wait_until(cond, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


def uniform(frame: bytes) -> bool:
    return frame.count(frame[:1]) == len(frame)


class FakeCaptureTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = os.path.join(tmp.name, "ffmpeg.log")

    def start(self, mode: str, with_preview: bool = True) -> capture.CaptureProcess:
        cap = capture.CaptureProcess(fake_argv(mode), self.log, with_preview)
        cap.start()
        self.addCleanup(self.reap, cap)
        return cap

    def reap(self, cap):
        # sempre: mesmo depois de uma falha rapida, fecha o stdin e colhe o processo
        if cap.pid is not None:
            cap.wait_stopped(0.1, 0.1, 0.1)

    def read_log(self) -> str:
        with open(self.log, encoding="utf-8") as f:
            return f.read()

    def test_normal_q_stops_with_rc0(self):
        cap = self.start("normal")
        self.assertTrue(cap.running)
        self.assertTrue(wait_until(lambda: cap.latest_frame()[0] >= 6))
        seq, frame = cap.latest_frame()
        self.assertEqual(len(frame), capture.FRAME_BYTES)
        self.assertTrue(uniform(frame), "frame montado com bytes de dois frames")
        self.assertEqual(frame[0], (seq - 1) % 251, "frame perdido ou fora de ordem")
        self.assertIsNotNone(cap.last_frame_monotonic)
        self.assertGreaterEqual(cap.last_frame_monotonic, cap.started_monotonic)
        self.assertIsNone(cap.early_failure())
        fps = cap.fps_measured()
        self.assertIsNotNone(fps)
        self.assertTrue(5 < fps < 40, fps)
        pid = cap.pid
        cap.request_stop()
        cap.request_stop()                       # idempotente
        self.assertEqual(cap.wait_stopped(**FAST), 0)
        self.assertEqual(cap.stop_steps, ["q"])
        self.assertEqual(cap.pid, pid)
        self.assertFalse(cap.running)
        self.assertIsNone(cap.early_failure())   # saida pedida nao e falha
        log = self.read_log()
        self.assertIn("ffmpeg falso: modo normal", log)
        self.assertEqual(log.count("q recebido"), 1)

    def test_start_uses_procs_spawn(self):
        with mock.patch("studio.capture.procs.spawn", wraps=procs.spawn) as spawn:
            cap = self.start("normal")
        self.assertEqual(spawn.call_args.args[0], fake_argv("normal"))
        kw = spawn.call_args.kwargs
        self.assertEqual((kw["stdin"], kw["stdout"], kw["bufsize"]), (subprocess.PIPE, subprocess.PIPE, 0))
        self.assertEqual(kw["stderr"].name, self.log)          # log em arquivo, nunca um 3o pipe
        self.assertNotIn("preexec_fn", kw)
        self.assertTrue(wait_until(lambda: cap.latest_frame()[0] >= 1))
        with open(f"/proc/{cap.pid}/cmdline", "rb") as f:
            # setpriv e env fazem exec: o pid do Popen ja e o do "ffmpeg"
            self.assertIn(FAKE.encode(), f.read().split(b"\0"))
        cap.request_stop()
        self.assertEqual(cap.wait_stopped(**FAST), 0)

    def test_ignore_q_needs_close_then_term(self):
        cap = self.start("ignore_q")
        self.assertTrue(wait_until(lambda: cap.latest_frame()[0] >= 2))
        cap.request_stop()
        self.assertEqual(cap.wait_stopped(**FAST), 255)
        self.assertEqual(cap.stop_steps, ["q", "close", "term"])
        self.assertIn("received signal 15", self.read_log())

    def test_hang_needs_kill(self):
        cap = self.start("hang")
        self.assertTrue(wait_until(lambda: cap.latest_frame()[0] >= 2))
        t0 = time.monotonic()
        self.assertEqual(cap.wait_stopped(0.3, 0.3, 0.3), -9)
        self.assertLess(time.monotonic() - t0, 3)
        self.assertEqual(cap.stop_steps, ["q", "close", "term", "kill"])
        self.assertFalse(cap.running)

    def test_blocked_pipe_released_by_close(self):
        # ffmpeg preso escrevendo no pipe so atende o q depois do EPIPE e sai 224 (probe t6c)
        cap = self.start("need_close")
        self.assertTrue(wait_until(lambda: cap.latest_frame()[0] >= 2))
        rc = cap.wait_stopped(**FAST)
        self.assertEqual(rc, 224)
        self.assertIn(rc, capture.ACCEPTED_RC)
        self.assertEqual(cap.stop_steps, ["q", "close"])
        self.assertIn("Broken pipe", self.read_log())

    def test_early_failure_busy_camera(self):
        cap = self.start("fail240")
        self.assertTrue(wait_until(lambda: not cap.running, timeout=3))
        self.assertEqual(cap.early_failure(), "Câmera em uso por outro programa (Meet/Zoom/OBS?)")
        self.assertEqual(cap.latest_frame(), (0, None))
        self.assertEqual(cap.wait_stopped(**FAST), 240)
        self.assertEqual(cap.stop_steps, ["q"])

    def test_early_failure_uses_log_tail(self):
        cap = capture.CaptureProcess(["sh", "-c", "echo 'Unknown input format: v4l3' >&2; exit 8"], self.log, True)
        cap.start()
        self.addCleanup(self.reap, cap)
        self.assertTrue(wait_until(lambda: not cap.running, timeout=3))
        self.assertEqual(cap.early_failure(), "ffmpeg falhou (código 8): Unknown input format: v4l3")

    def test_stall_trips_watchdog(self):
        cap = self.start("stall")
        self.assertTrue(wait_until(lambda: cap.latest_frame()[0] >= 5))
        dog = capture.Watchdog(frame_timeout=0.3)
        self.assertTrue(wait_until(lambda: dog.check_frames(time.monotonic(), cap.last_frame_monotonic,
                                                            cap.started_monotonic) is not None, timeout=3))
        self.assertEqual(cap.latest_frame()[0], 5)
        self.assertEqual(dog.check_frames(time.monotonic(), cap.last_frame_monotonic, cap.started_monotonic),
                         "A câmera parou de enviar imagem")
        self.assertTrue(cap.running)
        cap.request_stop()
        self.assertEqual(cap.wait_stopped(**FAST), 0)

    def test_audio_only_without_preview(self):
        with mock.patch("studio.capture.procs.spawn", wraps=procs.spawn) as spawn:
            cap = self.start("normal", with_preview=False)
        self.assertEqual(spawn.call_args.kwargs["stdout"], subprocess.DEVNULL)
        time.sleep(0.3)
        self.assertEqual(cap.latest_frame(), (0, None))
        self.assertIsNone(cap.last_frame_monotonic)
        self.assertIsNone(cap.fps_measured())
        self.assertEqual(cap.wait_stopped(**FAST), 0)
        self.assertEqual(cap.stop_steps, ["q"])

    def test_misuse(self):
        cap = capture.CaptureProcess(fake_argv("normal"), self.log, True)
        self.assertIsNone(cap.pid)
        self.assertFalse(cap.running)
        self.assertIsNone(cap.started_monotonic)
        self.assertIsNone(cap.early_failure())
        cap.request_stop()                                  # antes do start: nao faz nada
        with self.assertRaises(RuntimeError):
            cap.wait_stopped()
        cap.start()
        self.addCleanup(self.reap, cap)
        with self.assertRaises(RuntimeError):
            cap.start()


@unittest.skipUnless(os.environ.get("RUN_HARDWARE") == "1", "precisa de câmera e microfone reais (RUN_HARDWARE=1)")
class RealCaptureTest(unittest.TestCase):
    MIC = "alsa_input.usb-Generalplus_Usb_Audio_Device-00.mono-fallback"

    def setUp(self):
        cams = sorted(glob.glob("/dev/v4l/by-id/*A4tech*-video-index0"))
        self.assertTrue(cams, "câmera A4tech não encontrada em /dev/v4l/by-id")
        self.cam = cams[0]
        tmp = tempfile.TemporaryDirectory()        # a midia real some no fim do teste
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.log = os.path.join(self.dir, "ffmpeg.log")

    def run_capture(self, argv, with_preview, seconds, mic_index=None):
        cap = capture.CaptureProcess(argv, self.log, with_preview)
        cap.start()
        try:
            time.sleep(1.2)
            self.assertIsNone(cap.early_failure(), procs.tail(self.log))
            if mic_index is not None:
                self.assertIsNone(capture.check_mic(cap.pid, mic_index))
            time.sleep(seconds - 1.2)
            seq, frame = cap.latest_frame()
            fps = cap.fps_measured()
        finally:
            cap.request_stop()
            t0 = time.monotonic()
            rc = cap.wait_stopped()
            stop_s = time.monotonic() - t0
        self.assertIn(rc, capture.ACCEPTED_RC)
        self.assertEqual(cap.stop_steps, ["q"])
        size = len(frame) if frame is not None else None     # so o tamanho; o conteudo nunca e olhado
        del frame
        print(f"\n  {os.path.basename(argv[-1])}: {seq} frames, fps {fps}, rc {rc}, parada {stop_s:.2f} s",
              file=sys.stderr)
        return seq, size, fps

    def test_av_4s(self):
        index, err = capture.preflight(self.MIC, self.cam, self.dir)
        self.assertIsNone(err)
        out = os.path.join(self.dir, "raw.mkv")
        seq, size, fps = self.run_capture(capture.build_av_cmd(self.MIC, self.cam, out), True, 4.0, index)
        self.assertGreaterEqual(seq, 20)
        self.assertEqual(size, capture.FRAME_BYTES)
        self.assertIsNotNone(fps)
        self.assertIsNone(capture.verify_capture(out, need_video=True))

    def test_preview_only_2s(self):
        seq, size, _ = self.run_capture(capture.build_preview_cmd(self.cam), True, 2.0)
        self.assertGreaterEqual(seq, 10)
        self.assertEqual(size, capture.FRAME_BYTES)

    def test_audio_only_2s(self):
        index, err = capture.preflight(self.MIC, "", self.dir)
        self.assertIsNone(err)
        out = os.path.join(self.dir, "raw.wav")
        seq, size, _ = self.run_capture(capture.build_audio_cmd(self.MIC, out), False, 2.0, index)
        self.assertEqual((seq, size), (0, None))
        self.assertIsNone(capture.verify_capture(out, need_video=False))


if __name__ == "__main__":
    unittest.main()
