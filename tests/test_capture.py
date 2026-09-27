import itertools
import os
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from studio import capture, procs
from studio.devices import Source
from tests import helpers

MIC = "alsa_input.usb-Generalplus_Usb_Audio_Device-00.mono-fallback"
CAM = "/dev/v4l/by-id/usb-Sonix_Technology_Co.__Ltd._A4tech_HD_720P_PC_Camera_SN0001-video-index0"
VF = "scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2,fps=15"
# com -copyts, qualquer limite de duracao gera arquivo vazio com rc 0 (spec 5.1); -nostdin mata o q
FORBIDDEN = {"-t", "-to", "-frames", "-vframes", "-aframes", "-fs", "-nostdin"}


def forbidden_flags(argv: list[str]) -> list[str]:
    return [a for a in argv if a in FORBIDDEN or a.startswith("-frames:")]


class BuildCmdTest(unittest.TestCase):
    def test_av_cmd_is_spec_5_1(self):
        self.assertEqual(capture.build_av_cmd(MIC, CAM, "/rec/raw.mkv"), [
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-y", "-copyts",
            "-f", "pulse", "-thread_queue_size", "1024", "-sample_rate", "48000", "-channels", "1", "-i", MIC,
            "-f", "v4l2", "-input_format", "mjpeg", "-video_size", "1280x720", "-framerate", "30",
            "-ts", "mono2abs", "-thread_queue_size", "512", "-i", CAM,
            "-map", "1:v", "-map", "0:a", "-c:v", "copy", "-c:a", "pcm_s16le",
            "-avoid_negative_ts", "make_zero", "-f", "matroska", "/rec/raw.mkv",
            "-map", "1:v", "-vf", VF, "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"])

    def test_av_cmd_order_and_flags(self):
        a = capture.build_av_cmd(MIC, CAM, "/rec/raw.mkv")
        mic_i, cam_i = a.index(MIC), a.index(CAM)
        self.assertLess(mic_i, cam_i, "o microfone tem que ser a 1a entrada")
        self.assertLess(a.index("-copyts"), mic_i)
        self.assertEqual(a[a.index("-ts") + 1], "mono2abs")
        self.assertTrue(mic_i < a.index("-ts") < cam_i, "-ts mono2abs vale para a entrada da camera")
        self.assertEqual(a[a.index("-avoid_negative_ts") + 1], "make_zero")
        self.assertLess(a.index("-avoid_negative_ts"), a.index("/rec/raw.mkv"))
        self.assertEqual(a[-1], "pipe:1")
        self.assertEqual(forbidden_flags(a), [])

    def test_preview_cmd(self):
        a = capture.build_preview_cmd(CAM)
        self.assertEqual(a, [
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-y", "-copyts",
            "-f", "v4l2", "-input_format", "mjpeg", "-video_size", "1280x720", "-framerate", "30",
            "-ts", "mono2abs", "-thread_queue_size", "512", "-i", CAM,
            "-map", "0:v", "-vf", VF, "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"])
        self.assertNotIn("pulse", a)
        self.assertEqual(forbidden_flags(a), [])

    def test_audio_cmd(self):
        a = capture.build_audio_cmd(MIC, "/rec/raw.wav")
        self.assertEqual(a, [
            "ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info", "-y",
            "-f", "pulse", "-thread_queue_size", "1024", "-sample_rate", "48000", "-channels", "1", "-i", MIC,
            "-c:a", "pcm_s16le", "-f", "wav", "/rec/raw.wav"])
        self.assertEqual(forbidden_flags(a), [])

    def test_frame_size(self):
        self.assertEqual((capture.PREVIEW_W, capture.PREVIEW_H), (480, 270))
        self.assertEqual(capture.FRAME_BYTES, 388800)
        self.assertEqual(capture.PREVIEW_VF, VF)
        self.assertEqual(capture.ACCEPTED_RC, (0, 255, 224))


class ChunkStream:
    # stdout falso: devolve pedacos irregulares; opcionalmente falha depois de n leituras
    def __init__(self, data: bytes, sizes, fail_after: int | None = None):
        self.data = memoryview(data)
        self.pos = 0
        self.sizes = itertools.cycle(sizes)
        self.fail_after = fail_after
        self.calls = 0
        self.max_request = 0
        self.closed = False

    def readinto(self, b) -> int:
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise OSError(5, "Input/output error")
        self.max_request = max(self.max_request, len(b))
        n = min(len(b), next(self.sizes), len(self.data) - self.pos)
        b[:n] = self.data[self.pos:self.pos + n]
        self.pos += n
        return n

    def close(self):
        self.closed = True


def run_reader(stream, frame_bytes, clock=None) -> capture.FrameReader:
    kw = {"clock": clock} if clock else {}
    r = capture.FrameReader(stream, frame_bytes, **kw)
    r.start()
    r.join(timeout=5)
    assert not r.is_alive()
    return r


class FrameReaderTest(unittest.TestCase):
    def test_partial_reads_assemble_frames(self):
        fb = 1000
        frames = [bytes([i]) * fb for i in range(1, 8)]
        stream = ChunkStream(b"".join(frames) + b"\x09" * 123, sizes=[1, 7, 999, 64, 1500, 3])
        r = run_reader(stream, fb)
        seq, frame = r.latest()
        self.assertEqual(seq, 7)
        self.assertEqual(r.frames, 7)
        self.assertEqual(frame, frames[-1])      # o resto parcial (123 bytes) e descartado
        self.assertTrue(stream.closed)
        self.assertLessEqual(stream.max_request, fb)
        self.assertIsNone(r.error)
        self.assertIsNotNone(r.last_frame_monotonic)

    def test_real_frame_size_with_64k_chunks(self):
        frames = [bytes([i]) * capture.FRAME_BYTES for i in range(3)]
        stream = ChunkStream(b"".join(frames), sizes=[65536, 65535, 1])
        r = run_reader(stream, capture.FRAME_BYTES)
        self.assertEqual(r.latest(), (3, frames[2]))

    def test_nothing_read(self):
        r = run_reader(ChunkStream(b"", sizes=[10]), 100)
        self.assertEqual(r.latest(), (0, None))
        self.assertIsNone(r.last_frame_monotonic)
        self.assertIsNone(r.fps())

    def test_error_closes_stream(self):
        # leitor que morre sem fechar o pipe trava o ffmpeg e perde a tomada (spec 5.3)
        stream = ChunkStream(b"\x01" * 10 * 50, sizes=[10], fail_after=3)
        r = run_reader(stream, 10)
        self.assertEqual(r.frames, 3)
        self.assertIsInstance(r.error, OSError)
        self.assertTrue(stream.closed)

    def test_closed_stream_valueerror_is_handled(self):
        class Closed(ChunkStream):
            def readinto(self, b):
                raise ValueError("I/O operation on closed file")
        stream = Closed(b"", sizes=[1])
        r = run_reader(stream, 10)
        self.assertIsInstance(r.error, ValueError)
        self.assertTrue(stream.closed)

    def test_fps_counts_only_distinct_frames(self):
        # o filtro fps=15 duplica frames quando a camera entrega menos: duplicata nao conta
        a, b, c, d, e = (bytes([i]) * 4 for i in range(5))
        seq = [a, a, b, c, c, d, e, e]
        times = iter([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7])
        r = run_reader(ChunkStream(b"".join(seq), sizes=[3]), 4, clock=lambda: next(times))
        self.assertEqual(r.frames, 8)
        self.assertEqual(r.last_frame_monotonic, 0.7)
        # distintos em 0.0, 0.2, 0.3, 0.5, 0.6 -> 4 intervalos em 0.6 s
        self.assertAlmostEqual(r.fps(), 4 / 0.6)

    def test_fps_needs_enough_frames(self):
        seq = [bytes([i]) * 4 for i in range(capture.FPS_MIN_FRAMES - 1)]
        times = itertools.count(0.0, 0.1)
        r = run_reader(ChunkStream(b"".join(seq), sizes=[4]), 4, clock=lambda: next(times))
        self.assertIsNone(r.fps())

    def test_daemon_thread_starts_empty(self):
        r = capture.FrameReader(ChunkStream(b"", sizes=[1]), 4)
        self.assertIsInstance(r, threading.Thread)
        self.assertTrue(r.daemon)
        self.assertEqual(r.latest(), (0, None))


class WatchdogTest(unittest.TestCase):
    def test_frames(self):
        w = capture.Watchdog(frame_timeout=2.0)
        self.assertIsNone(w.check_frames(now=11.9, last_frame=10.0, started=5.0))
        self.assertEqual(w.check_frames(now=12.1, last_frame=10.0, started=5.0),
                         "A câmera parou de enviar imagem")

    def test_first_frame_gets_more_time(self):
        # o 1o frame leva 0,44-1,2 s (spec 5.4)
        w = capture.Watchdog(frame_timeout=2.0, first_frame_timeout=5.0)
        self.assertIsNone(w.check_frames(now=104.9, last_frame=None, started=100.0))
        self.assertEqual(w.check_frames(now=105.1, last_frame=None, started=100.0),
                         "A câmera não enviou nenhuma imagem")

    def test_duration_limit(self):
        w = capture.Watchdog()
        self.assertEqual(w.max_s, 300)
        self.assertIsNone(w.check_duration(now=399.9, started=100.0))
        self.assertEqual(w.check_duration(now=400.0, started=100.0),
                         "Limite de 5 min atingido — gravação encerrada")

    def test_limit_message(self):
        # fonte unica com check_duration e gui_capture.MSG_LIMIT (Task 10, fix de revisao)
        self.assertEqual("Limite de 5 min atingido — gravação encerrada", capture.limit_message())
        self.assertEqual("Limite de 2 min atingido — gravação encerrada", capture.limit_message(120))

    def test_low_fps(self):
        w = capture.Watchdog()
        self.assertIsNone(w.check_fps(None, now=110.0, started=100.0))
        self.assertIsNone(w.check_fps(14.6, now=110.0, started=100.0))
        self.assertEqual(w.check_fps(9.84, now=110.0, started=100.0), "Câmera a 9,8 fps (abaixo de 12) — pouca luz?")

    def test_low_fps_grace_at_start(self):
        # no real, um preview de 2 s mediu 12,3 fps e um A/V de 4 s mediu 14,95 (atraso do 1o frame)
        w = capture.Watchdog()
        self.assertEqual(w.fps_grace_s, 3.0)
        self.assertIsNone(w.check_fps(9.84, now=102.9, started=100.0))
        self.assertIsNotNone(w.check_fps(9.84, now=103.0, started=100.0))


class CheckMicTest(unittest.TestCase):
    def test_same_index(self):
        with mock.patch("studio.capture.devices.mic_source_of_pid", return_value=54) as m:
            self.assertIsNone(capture.check_mic(4321, 54))
        m.assert_called_once_with(4321)

    def test_other_source_or_missing(self):
        # nome invalido/desplugado: o PipeWire grava do mic padrao sem erro (spec 5.4.5); None = o pactl
        # respondeu e o gravador nao aparece nele (sumiu)
        for found in (56, None):
            with mock.patch("studio.capture.devices.mic_source_of_pid", return_value=found):
                self.assertEqual(capture.check_mic(4321, 54), "Microfone desconectado ou trocado")
                self.assertEqual(capture.mic_status(4321, 54), capture.MIC_SWAPPED)

    def test_pactl_failure_is_unknown(self):
        # pactl que falhou ou estourou o timeout: "nao sei"; a gravacao nao para por isso
        with mock.patch("studio.devices._pactl", return_value=None):
            self.assertIsNone(capture.check_mic(4321, 54))
            self.assertEqual(capture.mic_status(4321, 54), capture.MIC_UNKNOWN)
        self.assertEqual(capture.MSG_MIC_UNKNOWN, "Não deu para conferir o microfone (o pactl não respondeu)")

    def test_mic_status_values(self):
        self.assertEqual((capture.MIC_OK, capture.MIC_SWAPPED, capture.MIC_UNKNOWN), ("ok", "trocado", "desconhecido"))
        with mock.patch("studio.capture.devices.mic_source_of_pid", return_value=54):
            self.assertEqual(capture.mic_status(4321, 54), capture.MIC_OK)


class PreflightTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.rec = os.path.join(tmp.name, "recordings")
        self.cam = os.path.join(tmp.name, "usb-Cam-video-index0")
        open(self.cam, "w").close()
        p1 = mock.patch("studio.capture.devices.list_mics",
                        return_value=[Source(54, MIC), Source(56, "alsa_input.usb-ME6S")])
        p2 = mock.patch("studio.capture.devices.free_bytes", return_value=50 * 1024**3)
        self.list_mics = p1.start()
        self.free = p2.start()
        self.addCleanup(mock.patch.stopall)

    def test_ok_returns_mic_index(self):
        self.assertEqual(capture.preflight(MIC, self.cam, self.rec), (54, None))
        self.free.assert_called_once_with(self.rec)

    def test_audio_only_skips_camera(self):
        self.assertEqual(capture.preflight("alsa_input.usb-ME6S", "", self.rec), (56, None))

    def test_unknown_mic(self):
        self.assertEqual(capture.preflight("alsa_input.sumiu", self.cam, self.rec),
                         (None, "Microfone não encontrado — escolha outro na lista"))

    def test_missing_camera(self):
        self.assertEqual(capture.preflight(MIC, self.cam + "-x", self.rec), (None, "Câmera não encontrada"))

    def test_low_disk(self):
        self.free.return_value = int(1.5 * 1024**3)
        self.assertEqual(capture.preflight(MIC, self.cam, self.rec),
                         (None, "Pouco espaço em disco: 1,5 GB livres (mínimo 2 GB)"))

    def test_injected_list_mics_and_free_bytes_take_over_devices(self):
        # gui_capture.py injeta o Hardware falso dos testes (Task 10, fix de revisao); devices nao e chamado
        injected_mics = mock.Mock(return_value=[Source(54, MIC)])
        injected_free = mock.Mock(return_value=50 * 1024**3)
        self.assertEqual(capture.preflight(MIC, self.cam, self.rec, list_mics=injected_mics,
                                           free_bytes=injected_free), (54, None))
        injected_mics.assert_called_once_with()
        injected_free.assert_called_once_with(self.rec)
        self.list_mics.assert_not_called()
        self.free.assert_not_called()

    def test_injected_list_mics_reports_unknown_mic(self):
        injected_mics = mock.Mock(return_value=[Source(56, "alsa_input.usb-ME6S")])
        self.assertEqual(capture.preflight("alsa_input.sumiu", self.cam, self.rec, list_mics=injected_mics),
                         (None, "Microfone não encontrado — escolha outro na lista"))


class VerifyCaptureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        cls.mkv = helpers.make_synthetic_take(os.path.join(d, "raw.mkv"), duration_s=2.0, flash_frame=30,
                                              size="320x240")
        cls.wav = os.path.join(d, "raw.wav")
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", cls.mkv, "-map", "0:a",
                        "-c:a", "pcm_s16le", cls.wav], check=True)
        with open(cls.mkv, "rb") as f:
            head = f.read(1000)
        # so o cabecalho: o ffprobe ainda diz 2 s de duracao, mas nao ha nenhum pacote
        cls.trunc = os.path.join(d, "trunc.mkv")
        with open(cls.trunc, "wb") as f:
            f.write(head)
        cls.empty = os.path.join(d, "empty.mkv")
        open(cls.empty, "wb").close()
        cls.junk = os.path.join(d, "junk.mkv")
        with open(cls.junk, "wb") as f:
            f.write(b"isto nao e um video" * 100)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_good_take(self):
        self.assertIsNone(capture.verify_capture(self.mkv, need_video=True))
        self.assertIsNone(capture.verify_capture(self.mkv, need_video=False))

    def test_audio_only(self):
        self.assertIsNone(capture.verify_capture(self.wav, need_video=False))
        self.assertEqual(capture.verify_capture(self.wav, need_video=True), "A gravação não tem vídeo")

    def test_truncated_header_only(self):
        self.assertEqual(float(procs.media_info(self.trunc)["format"]["duration"]), 2.0)   # engana o media_info
        self.assertEqual(capture.verify_capture(self.trunc, need_video=True), "A gravação está vazia ou corrompida")

    def test_empty_missing_junk(self):
        self.assertEqual(capture.verify_capture(self.empty, need_video=False), "A gravação está vazia ou corrompida")
        missing = os.path.join(self.tmp.name, "nao_existe.mkv")
        self.assertEqual(capture.verify_capture(missing, need_video=False), "Gravação não encontrada: nao_existe.mkv")
        self.assertEqual(capture.verify_capture(self.junk, need_video=False), "Não foi possível ler junk.mkv")

    def test_zero_duration(self):
        data = {"streams": [{"codec_type": "audio", "nb_read_packets": "3"}], "format": {"duration": "0.000000"}}
        with mock.patch("studio.capture.procs.ffprobe_json", return_value=data) as probe:
            self.assertEqual(capture.verify_capture(self.wav, need_video=False), "A gravação ficou com duração zero")
        self.assertIn("-count_packets", probe.call_args.args)
        data["format"] = {}
        with mock.patch("studio.capture.procs.ffprobe_json", return_value=data):
            self.assertEqual(capture.verify_capture(self.wav, need_video=False), "A gravação ficou com duração zero")

    def test_too_short_video(self):
        # duplo clique em Gravar: 1 ou 2 pacotes de video nao dao video (o render comeca no 2o frame)
        short = os.path.join(self.tmp.name, "curto.mkv")
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "testsrc2=s=320x240:r=30:d=0.066667", "-f", "lavfi",
                        "-i", "aevalsrc=0:s=48000:c=mono:d=0.066667", "-c:v", "mjpeg", "-c:a", "pcm_s16le", short],
                       check=True)
        self.assertEqual(capture.verify_capture(short, need_video=True), "Gravação curta demais")
        self.assertIsNone(capture.verify_capture(short, need_video=False))
        for n, want in ((1, "Gravação curta demais"), (3, None)):
            data = {"streams": [{"codec_type": "video", "nb_read_packets": str(n)},
                                {"codec_type": "audio", "nb_read_packets": "5"}], "format": {"duration": "0.1"}}
            with mock.patch("studio.capture.procs.ffprobe_json", return_value=data):
                self.assertEqual(capture.verify_capture(self.mkv, need_video=True), want)
        self.assertEqual((capture.MSG_SHORT, capture.MIN_VIDEO_PACKETS), ("Gravação curta demais", 3))


if __name__ == "__main__":
    unittest.main()
