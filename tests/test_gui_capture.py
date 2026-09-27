import dataclasses
import gc
import itertools
import json
import os
import shutil
import tempfile
import threading
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np
import soundfile as sf
from PIL import ImageTk

from studio import capture, gui, gui_capture
from studio.config import get_modelo
from studio.devices import Source
from studio.takes import Take, list_takes
from tests import helpers

HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
MIC = "alsa_input.usb-Generalplus_Usb_Audio_Device-00.mono-fallback"
OTHER_MIC = "alsa_input.pci-0000_00_1f.3.analog-stereo"
PIXELS = capture.PREVIEW_W * capture.PREVIEW_H
RED = bytes((200, 40, 40)) * PIXELS
BLUE = bytes((30, 60, 220)) * PIXELS
_pids = itertools.count(40000)


class FakeCapture:
    """CaptureProcess falso: frames sinteticos sob demanda; no wait_stopped 'grava' o raw copiando uma midia pronta."""

    def __init__(self, argv, log_path, with_preview, raw_source=None, fail=None):
        self.argv = list(argv)
        self.log_path = log_path
        self.with_preview = with_preview
        self.raw_source = raw_source
        self.fail = fail
        self.pid = next(_pids)
        self.start_thread = None
        self.started_monotonic = None
        self.last_frame_monotonic = None
        self.fps = None
        self.stop_requested = False
        self.stopped_at = None
        self.wait_threads: list[str] = []
        self.wait_args: list[tuple] = []
        self._seq = 0
        self._frame = None

    def start(self):
        self.start_thread = threading.current_thread().name
        self.started_monotonic = time.monotonic()

    @property
    def running(self):
        return self.started_monotonic is not None and not self.stop_requested and self.fail is None

    def early_failure(self):
        return None if self.stop_requested else self.fail

    def push(self, frame: bytes = RED):
        self._seq += 1
        self._frame = frame
        self.last_frame_monotonic = time.monotonic()

    def latest_frame(self):
        return self._seq, self._frame

    def fps_measured(self):
        return self.fps

    def request_stop(self):
        self.stop_requested = True

    def wait_stopped(self, t_q=5, t_close=3, t_term=3):
        self.wait_threads.append(threading.current_thread().name)
        self.wait_args.append((t_q, t_close, t_term))
        self.stop_requested = True
        time.sleep(0.05)                        # o ffmpeg de verdade leva ~0,2 s para sair com o q
        out = next((a for a in self.argv if a.endswith(("raw.mkv", "raw.wav"))), None)
        if out and self.raw_source and self.fail is None and not os.path.exists(out):
            shutil.copyfile(self.raw_source, out)
        self.stopped_at = time.monotonic()
        return 0


def pump_until(app, cond, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.root.update()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def pump_for(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.root.update()
        time.sleep(0.01)


def pixel(panel, xy) -> tuple:
    return ImageTk.getimage(panel.photo).getpixel(xy)[:3]


class HelpersTest(unittest.TestCase):
    def test_parse_duration(self):
        self.assertIsNone(gui_capture.parse_duration("  "))
        self.assertEqual(2.5, gui_capture.parse_duration("2,5"))
        self.assertEqual(30.0, gui_capture.parse_duration(" 30 "))
        for bad in ("abc", "0", "-3", "nan", "inf"):
            with self.subTest(bad):
                with self.assertRaises(ValueError) as cm:
                    gui_capture.parse_duration(bad)
                self.assertEqual(gui_capture.MSG_BAD_DURATION, str(cm.exception))

    def test_camera_label(self):
        self.assertEqual("A4tech_FHD_720P_PC_Camera",
                         gui_capture.camera_label("/dev/v4l/by-id/usb-A4tech_FHD_720P_PC_Camera-video-index0"))

    def test_overlay_and_compose(self):
        ov = gui_capture.make_overlay(get_modelo("silvio"))
        self.assertEqual(("RGBA", (480, 270)), (ov.mode, ov.size))
        self.assertEqual((17, 17, 17), gui_capture.compose(None, ov).getpixel((10, 10))[:3])
        img = gui_capture.compose(RED, ov)
        self.assertEqual((480, 270), img.size)
        self.assertEqual((200, 40, 40), img.getpixel((10, 10))[:3])
        other = gui_capture.compose(RED, gui_capture.make_overlay(get_modelo("orochi")))
        band = (0, 200, 480, 270)
        self.assertNotEqual(img.crop(band).tobytes(), other.crop(band).tobytes())   # aviso muda por modelo

    def test_min_rec_s(self):
        self.assertEqual(1.0, gui_capture.MIN_REC_S)

    def test_finish_capture_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            res = gui_capture.finish_capture(os.path.join(d, "raw.mkv"), True, os.path.join(d, "audio.wav"))
        self.assertEqual({"erro": "Gravação não encontrada: raw.mkv"}, res)


@unittest.skipUnless(HAS_DISPLAY, "sem display")
class CapturePanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = tempfile.TemporaryDirectory()
        cls.av_raw = helpers.make_synthetic_take(os.path.join(cls.media.name, "av.mkv"), duration_s=2.0,
                                                 flash_frame=30, size="320x240")
        cls.silent_wav = os.path.join(cls.media.name, "mudo.wav")
        sf.write(cls.silent_wav, np.zeros(48000, dtype=np.int16), 48000, subtype="PCM_16")

    @classmethod
    def tearDownClass(cls):
        cls.media.cleanup()

    def setUp(self):
        # roda por ultimo: o lixo do Tk (App, PhotoImage, StringVar) morre na thread principal
        self.addCleanup(gc.collect)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rec_dir = os.path.join(self.tmp.name, "recordings")
        self.estado_path = os.path.join(self.tmp.name, "estado.json")
        self.log_path = os.path.join(self.tmp.name, "studio.log")
        by_id = os.path.join(self.tmp.name, "by-id")
        os.makedirs(by_id)
        self.cam = os.path.join(by_id, "usb-A4tech_FHD_720P_PC_Camera-video-index0")
        open(self.cam, "w").close()
        self.cams = [self.cam]
        self.mics = [Source(54, MIC), Source(56, OTHER_MIC)]
        self.free = 50 * 1024**3
        self.mic_state = capture.MIC_OK         # resposta do mic_status falso quando a fila mic_states acaba
        self.mic_states: list[str] = []
        self.mic_checks: list[tuple] = []
        self.caps: list[FakeCapture] = []
        self.fail_next = None
        self.confirm_calls: list[tuple[str, str]] = []
        self.confirm_answer = False
        # MIN_REC_S=0: os testes que nao sao do duplo clique podem parar logo depois do 1o frame
        patcher = mock.patch.multiple(gui_capture, TICK_MS=20, WATCH_MS=40, MIC_CHECK_S=0.2, MIN_REC_S=0)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ---------- fabricas falsas ----------

    def factory(self, argv, log_path, with_preview):
        raw = self.silent_wav if "wav" in argv[-1] else self.av_raw
        cap = FakeCapture(argv, log_path, with_preview, raw_source=raw, fail=self.fail_next)
        self.fail_next = None
        self.caps.append(cap)
        return cap

    def mic_status(self, pid, expected_index):
        self.mic_checks.append((threading.current_thread().name, pid, expected_index))
        return self.mic_states.pop(0) if self.mic_states else self.mic_state

    def confirm(self, title, message):
        self.confirm_calls.append((title, message))
        return self.confirm_answer

    def make_app(self, estado: dict | None = None) -> gui.App:
        if estado is not None:
            with open(self.estado_path, "w", encoding="utf-8") as f:
                json.dump(estado, f)
        root = tk.Tk()
        root.withdraw()
        hw = gui_capture.Hardware(list_cameras=lambda: list(self.cams), list_mics=lambda: list(self.mics),
                                  free_bytes=lambda path: self.free, mic_status=self.mic_status)
        app = gui.App(root, rec_dir=self.rec_dir, estado_path=self.estado_path, log_path=self.log_path,
                      logs_dir=os.path.join(self.tmp.name, "logs"), ask_confirm=self.confirm,
                      capture_factory=self.factory, hardware=hw)
        self.addCleanup(self.close_app, app)
        return app

    def close_app(self, app):
        if not app.closed:
            pump_until(app, lambda: not app.io_jobs.busy)
            app.shutdown()
        for cap in self.caps:
            self.assertTrue(cap.stop_requested, f"captura esquecida ligada: {cap.argv[:4]}")

    def log_text(self, app) -> str:
        return app.log_box.get("1.0", "end")

    def file_text(self) -> str:
        with open(self.log_path, encoding="utf-8") as f:
            return f.read()

    def start_recording(self, app) -> tuple[Take, FakeCapture]:
        p = app.capture_panel
        n = len(self.caps)
        p.btn_record.invoke()
        self.assertTrue(pump_until(app, lambda: p.state == "recording"), self.log_text(app))
        self.assertEqual(n + 1, len(self.caps))
        return p.take, self.caps[-1]

    def wait_idle(self, app, timeout: float = 20.0) -> None:
        p = app.capture_panel
        self.assertTrue(pump_until(app, lambda: p.state == "idle" and not app.io_jobs.busy, timeout),
                        self.log_text(app))

    # ---------- montagem ----------

    def test_layout_devices_and_estado(self):
        app = self.make_app({"mic": OTHER_MIC, "gravar_video": True})
        p = app.capture_panel
        self.assertIs(app.left, p.frame.master)
        self.assertEqual("Gravação", p.frame.cget("text"))
        self.assertEqual(("480", "270"), (str(p.canvas.cget("width")), str(p.canvas.cget("height"))))
        self.assertEqual([MIC, OTHER_MIC], list(p.mic_box.cget("values")))
        self.assertEqual(OTHER_MIC, p.mic_var.get())
        self.assertEqual(["A4tech_FHD_720P_PC_Camera"], list(p.camera_box.cget("values")))
        self.assertEqual(self.cam, p.camera_path())
        self.assertTrue(p.video_on.get())
        self.assertFalse(p.camera_on.get())          # a camera nunca liga sozinha
        self.assertEqual("● Gravar", p.btn_record.cget("text"))
        self.assertEqual("idle", p.state)
        self.assertEqual([], self.caps)
        self.assertIn("capture_stop", gui.JOB_LABELS)
        p.mic_var.set(MIC)
        p.mic_box.event_generate("<<ComboboxSelected>>")
        with open(self.estado_path, encoding="utf-8") as f:
            self.assertEqual(MIC, json.load(f)["mic"])

    def test_saved_mic_missing_falls_back_with_warning(self):
        app = self.make_app({"mic": "alsa_input.sumiu"})
        self.assertEqual(MIC, app.capture_panel.mic_var.get())
        self.assertIn("alsa_input.sumiu", self.log_text(app))

    # ---------- preview ----------

    def test_preview_paints_canvas_with_watermark_guides_and_fps(self):
        app = self.make_app()
        p = app.capture_panel
        p.cam_check.invoke()
        self.assertTrue(p.camera_on.get())
        (cap,) = self.caps
        self.assertEqual(capture.build_preview_cmd(self.cam), cap.argv)
        self.assertTrue(cap.with_preview)
        self.assertEqual(threading.main_thread().name, cap.start_thread)
        cap.fps = 14.6
        cap.push(RED)
        self.assertTrue(pump_until(app, lambda: pixel(p, (10, 10)) == (200, 40, 40)))
        r, g, b = pixel(p, (10, 265))                 # faixa escura da marca d'agua
        self.assertLess(r, 120)
        col = round(capture.PREVIEW_H * 9 / 16)
        x0 = (capture.PREVIEW_W - col) // 2
        self.assertNotEqual((200, 40, 40), pixel(p, (x0, 60)))       # guia 9:16
        self.assertEqual((200, 40, 40), pixel(p, (x0 + 5, 60)))
        self.assertIn("14,6", p.fps_label.cget("text"))
        self.assertEqual(gui_capture.COLOR_REC_OFF, str(p.rec_label.cget("foreground")))   # preview nao e REC

        # fps baixo (depois da carencia) vira aviso
        cap.started_monotonic -= 10
        cap.fps = 8.0
        cap.push(BLUE)
        self.assertTrue(pump_until(app, lambda: "abaixo de 12" in p.fps_label.cget("text")))
        self.assertTrue(pump_until(app, lambda: pixel(p, (10, 10)) == (30, 60, 220)))

        # trocar o modelo refaz a marca no preview
        band = (0, 200, 480, 270)
        before = ImageTk.getimage(p.photo).crop(band).tobytes()
        app.model_var.set("Silvio Santos")
        app.model_box.event_generate("<<ComboboxSelected>>")
        self.assertTrue(pump_until(app, lambda: ImageTk.getimage(p.photo).crop(band).tobytes() != before))

    def test_preview_stops_on_uncheck_unmap_and_idle(self):
        app = self.make_app()
        p = app.capture_panel
        p.cam_check.invoke()
        p.cam_check.invoke()                          # desmarcou
        first = self.caps[0]
        self.assertTrue(first.stop_requested)
        self.assertIsNone(p.preview)
        self.assertTrue(pump_until(app, lambda: first.wait_threads == ["job-io"]))

        p.cam_check.invoke()
        self.assertTrue(pump_until(app, lambda: len(self.caps) == 2))
        p._on_unmap(SimpleNamespace(widget=p.canvas))  # Unmap de um filho nao conta
        self.assertFalse(self.caps[1].stop_requested)
        p._on_unmap(SimpleNamespace(widget=app.root))  # janela minimizada
        self.assertTrue(self.caps[1].stop_requested)
        p._on_map(SimpleNamespace(widget=app.root))    # volta: religa so depois de a anterior soltar a camera
        self.assertTrue(pump_until(app, lambda: len(self.caps) == 3))
        self.assertEqual(["job-io"], self.caps[1].wait_threads)

        with mock.patch.object(gui_capture, "PREVIEW_IDLE_S", 0.2):
            self.assertTrue(pump_until(app, lambda: self.caps[2].stop_requested))
        self.assertFalse(p.camera_on.get())
        self.assertIn("sem uso", self.log_text(app))
        self.assertEqual(3, len(self.caps))

    def test_video_unchecked_means_no_preview(self):
        app = self.make_app({"gravar_video": False})
        p = app.capture_panel
        self.assertFalse(p.video_on.get())
        p.cam_check.invoke()
        app.root.update()
        self.assertEqual([], self.caps)
        p.video_check.invoke()                        # marcou "Gravar vídeo": agora liga
        self.assertEqual(1, len(self.caps))
        with open(self.estado_path, encoding="utf-8") as f:
            self.assertTrue(json.load(f)["gravar_video"])

    def test_preview_early_failure_turns_camera_off(self):
        app = self.make_app()
        p = app.capture_panel
        self.fail_next = "Câmera em uso por outro programa (Meet/Zoom/OBS?)"
        p.cam_check.invoke()
        self.assertTrue(pump_until(app, lambda: not p.camera_on.get()))
        self.assertIn("Câmera em uso por outro programa", self.log_text(app))
        self.assertTrue(pump_until(app, lambda: self.caps[0].wait_threads == ["job-io"]))

    # ---------- gravar ----------

    def test_record_av_creates_take_and_changes_button(self):
        app = self.make_app()
        p = app.capture_panel
        got = []
        app.on("recording", got.append)
        p.cam_check.invoke()
        preview = self.caps[0]
        p.btn_record.invoke()
        (take,) = list_takes(self.rec_dir)
        self.assertEqual(("gravando", "av", MIC, self.cam), (take.status, take.modo, take.mic, take.camera))
        self.assertTrue(pump_until(app, lambda: p.state == "recording"))
        rec = self.caps[1]
        self.assertEqual(["job-io"], preview.wait_threads)
        self.assertLess(preview.stopped_at, rec.started_monotonic)  # o preview soltou a camera antes
        self.assertEqual(capture.build_av_cmd(MIC, self.cam, take.raw_path), rec.argv)
        self.assertEqual(take.path("ffmpeg.log"), rec.log_path)
        self.assertTrue(rec.with_preview)
        self.assertEqual(threading.main_thread().name, rec.start_thread)
        self.assertEqual("■ Parar", p.btn_record.cget("text"))
        self.assertEqual("disabled", str(p.mic_box.cget("state")))
        pump_for(app, 0.15)
        self.assertEqual(gui_capture.COLOR_REC_OFF, str(p.rec_label.cget("foreground")))   # sem frame ainda
        rec.push(RED)
        self.assertTrue(pump_until(app, lambda: str(p.rec_label.cget("foreground")) == gui_capture.COLOR_REC_ON))
        self.assertTrue(pump_until(app, lambda: pixel(p, (10, 10)) == (200, 40, 40)))
        with open(self.estado_path, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual((MIC, self.cam, True), (saved["mic"], saved["camera"], saved["gravar_video"]))
        self.assertTrue(any("Gravação em andamento" in r for r in app.close_reasons()))
        self.assertTrue(pump_until(app, lambda: got))
        self.assertTrue(got[0].data["active"])
        self.assertEqual(take.id, got[0].data["take"].id)
        p.btn_record.invoke()
        self.wait_idle(app)

    def test_stop_takes_take_to_gravado(self):
        app = self.make_app()
        p = app.capture_panel
        changed, recording = [], []
        app.on("take_changed", changed.append)
        app.on("recording", recording.append)
        p.cam_check.invoke()
        take, rec = self.start_recording(app)
        rec.push(RED)
        # o Parar so libera quando o tick ve o 1o frame
        self.assertTrue(pump_until(app, lambda: str(p.btn_record.cget("state")) == "normal"))
        p.btn_record.invoke()                         # Parar
        self.assertEqual("stopping", p.state)
        self.assertTrue(rec.stop_requested)
        self.wait_idle(app)
        self.assertEqual(["job-io"], rec.wait_threads)
        self.assertIs(take, app.take)
        self.assertEqual("gravado", app.take.status)
        saved = Take.load(take.dir)
        self.assertEqual("gravado", saved.status)
        self.assertEqual((320, 240), (saved.video["w"], saved.video["h"]))
        self.assertGreater(saved.video["n_frames"], 50)
        self.assertIn("taxa_real", saved.audio_fit)
        self.assertTrue(os.path.exists(take.audio_path))
        self.assertTrue(pump_until(app, lambda: changed))
        self.assertIs(take, changed[-1].data["take"])
        self.assertEqual([True, False], [e.data["active"] for e in recording])
        self.assertIn(f"Gravação concluída: {take.id}", self.log_text(app))
        self.assertIn("Volume da gravação", self.log_text(app))
        self.assertNotIn("Microfone mudo?", self.log_text(app))
        self.assertEqual("● Gravar", p.btn_record.cget("text"))
        self.assertEqual("normal", str(p.btn_record.cget("state")))
        self.assertEqual("readonly", str(p.mic_box.cget("state")))
        # camera continua marcada: o preview volta
        self.assertTrue(pump_until(app, lambda: len(self.caps) == 3 and p.preview is self.caps[2]))

    def test_audio_only(self):
        app = self.make_app({"gravar_video": False})
        p = app.capture_panel
        p.cam_check.invoke()                          # sem "Gravar vídeo" a camera nao liga
        take, rec = self.start_recording(app)
        self.assertEqual(("audio", ""), (take.modo, take.camera))
        self.assertEqual(capture.build_audio_cmd(MIC, take.raw_path), rec.argv)
        self.assertTrue(take.raw_path.endswith("raw.wav"))
        self.assertFalse(rec.with_preview)
        self.assertTrue(pump_until(app, lambda: str(p.rec_label.cget("foreground")) == gui_capture.COLOR_REC_ON))
        p.btn_record.invoke()
        self.wait_idle(app)
        self.assertEqual("gravado", app.take.status)
        self.assertEqual({}, app.take.video)
        self.assertEqual(take.raw_path, app.take.audio_path)
        self.assertIn("Microfone mudo?", self.log_text(app))       # o raw falso e silencio
        self.assertEqual(1, len(self.caps))

    def test_checks_block_recording(self):
        app = self.make_app()
        p = app.capture_panel
        cases = [
            ("free", 1536 * 1024**2, "Pouco espaço em disco: 1,5 GB livres (mínimo 2 GB)"),
            ("mics", [Source(56, OTHER_MIC)], "Microfone não encontrado — escolha outro na lista"),
            ("cam", None, "Câmera não encontrada"),
            ("duration", "abc", "Duração inválida"),
        ]
        for what, value, msg in cases:
            with self.subTest(what):
                self.free, self.mics = 50 * 1024**3, [Source(54, MIC), Source(56, OTHER_MIC)]
                p.duration_var.set("")
                if what == "free":
                    self.free = value
                elif what == "mics":
                    self.mics = value
                elif what == "cam":
                    os.rename(self.cam, self.cam + ".x")
                    self.addCleanup(os.rename, self.cam + ".x", self.cam)
                else:
                    p.duration_var.set(value)
                p.btn_record.invoke()
                self.assertIn(msg, self.log_text(app))
                self.assertEqual("idle", p.state)
                self.assertEqual([], list_takes(self.rec_dir))
                self.assertEqual([], self.caps)

    def test_double_click_does_not_stop_recording(self):
        # duplo clique em Gravar: o 2o clique cai no Parar desabilitado (MIN_REC_S e, no A/V, o 1o frame)
        app = self.make_app()
        p = app.capture_panel
        with mock.patch.object(gui_capture, "MIN_REC_S", 0.3):
            p.btn_record.invoke()
            p.btn_record.invoke()
            self.assertEqual("recording", p.state)
            self.assertEqual(("■ Parar", "disabled"), (p.btn_record.cget("text"), str(p.btn_record.cget("state"))))
            rec = self.caps[-1]
            pump_for(app, 0.45)                       # passou do MIN_REC_S, mas sem frame: continua travado
            p.btn_record.invoke()
            self.assertEqual(("recording", "disabled"), (p.state, str(p.btn_record.cget("state"))))
            rec.push(RED)
            self.assertTrue(pump_until(app, lambda: str(p.btn_record.cget("state")) == "normal"))
            p.btn_record.invoke()
            self.assertEqual("stopping", p.state)
        self.wait_idle(app)
        self.assertEqual("gravado", app.take.status)

    def test_audio_only_stop_waits_min_rec_s(self):
        app = self.make_app({"gravar_video": False})
        p = app.capture_panel
        with mock.patch.object(gui_capture, "MIN_REC_S", 0.3):
            p.btn_record.invoke()
            p.btn_record.invoke()
            self.assertEqual(("recording", "disabled"), (p.state, str(p.btn_record.cget("state"))))
            self.assertTrue(pump_until(app, lambda: str(p.btn_record.cget("state")) == "normal"))
            self.assertGreaterEqual(time.monotonic() - self.caps[-1].started_monotonic, 0.3)
            p.btn_record.invoke()
            self.assertEqual("stopping", p.state)
        self.wait_idle(app)
        self.assertEqual("gravado", app.take.status)

    def test_duration_below_min_rec_s_is_refused(self):
        app = self.make_app({"gravar_video": False})
        p = app.capture_panel
        p.duration_var.set("0,5")
        with mock.patch.object(gui_capture, "MIN_REC_S", 1.0):
            p.btn_record.invoke()
        self.assertIn("Não foi possível gravar: Gravação curta demais (mínimo 1 s)", self.log_text(app))
        self.assertEqual(("idle", [], []), (p.state, list_takes(self.rec_dir), self.caps))

    def test_early_failure_shows_message_and_goes_back_to_idle(self):
        app = self.make_app()
        p = app.capture_panel
        self.fail_next = "Câmera em uso por outro programa (Meet/Zoom/OBS?)"
        p.btn_record.invoke()
        self.assertTrue(pump_until(app, lambda: len(self.caps) == 1))
        self.wait_idle(app)
        rec = self.caps[0]
        self.assertIn("Câmera em uso por outro programa (Meet/Zoom/OBS?)", self.log_text(app))
        self.assertEqual(["job-io"], rec.wait_threads)                # colhido mesmo depois da falha
        (take,) = list_takes(self.rec_dir)
        self.assertEqual("falhou", take.status)
        self.assertEqual("Câmera em uso por outro programa (Meet/Zoom/OBS?)", take.erro)
        self.assertIsNone(app.take)
        self.assertEqual("● Gravar", p.btn_record.cget("text"))
        self.assertEqual("normal", str(p.btn_record.cget("state")))

    def test_audio_gaps_warn_in_log(self):
        # buraco no audio (spec 6.3.3): o alinhamento usa o modo assincrono e o log avisa
        app = self.make_app()
        p = app.capture_panel
        real = gui_capture.timeline.extract_aligned_audio

        def with_gaps(raw, out):
            vi, fit = real(raw, out)
            fit.gaps = 2
            return vi, fit

        def record_and_stop() -> Take:
            take, rec = self.start_recording(app)
            rec.push(RED)
            self.assertTrue(pump_until(app, lambda: str(p.btn_record.cget("state")) == "normal"))
            p.btn_record.invoke()
            self.wait_idle(app)
            return take

        record_and_stop()
        self.assertNotIn("buraco", self.log_text(app))
        with mock.patch.object(gui_capture.timeline, "extract_aligned_audio", with_gaps):
            take = record_and_stop()
        self.assertEqual(2, Take.load(take.dir).audio_fit["gaps"])
        self.assertIn(f"AVISO: o áudio da tomada {take.id} teve 2 buraco(s) acima de 30 ms; o alinhamento usou o "
                      "modo assíncrono — confira a sincronia", self.log_text(app))

    def test_watchdog_stops_when_camera_stalls(self):
        app = self.make_app()
        p = app.capture_panel
        p.watchdog = capture.Watchdog(frame_timeout=0.3)
        take, rec = self.start_recording(app)
        rec.push(RED)
        self.wait_idle(app)
        self.assertIn("A câmera parou de enviar imagem", self.log_text(app))
        self.assertEqual(["job-io"], rec.wait_threads)
        self.assertEqual("gravado", Take.load(take.dir).status)       # parada graciosa: o que gravou fica

    def test_mic_switched_stops_recording(self):
        app = self.make_app()
        p = app.capture_panel
        self.mic_state = capture.MIC_SWAPPED
        take, rec = self.start_recording(app)
        rec.push(RED)
        self.wait_idle(app)
        self.assertEqual(("job-io", rec.pid, 54), self.mic_checks[0])
        self.assertEqual(2, len(self.mic_checks))                  # para na 2a checagem seguida
        self.assertIn("Microfone desconectado ou trocado", self.log_text(app))
        self.assertEqual("gravado", Take.load(take.dir).status)

    def test_mic_swapped_stops_only_on_second_in_a_row(self):
        # "trocado" isolado nao para ("ok" zera a contagem); "desconhecido" no meio nao conta nem zera
        app = self.make_app({"gravar_video": False})
        swapped, ok, unknown = capture.MIC_SWAPPED, capture.MIC_OK, capture.MIC_UNKNOWN
        self.mic_states = [swapped, ok, swapped, unknown]
        self.mic_state = swapped
        self.start_recording(app)
        self.wait_idle(app)
        self.assertEqual(5, len(self.mic_checks))
        self.assertIn("AVISO: Microfone desconectado ou trocado — parando a gravação", self.log_text(app))
        self.assertEqual(1, self.log_text(app).count(capture.MSG_MIC_UNKNOWN))

    def test_pactl_failure_is_unknown_and_does_not_stop(self):
        # pactl que falha ou estoura o timeout (5 s) e "nao sei": um aviso no log e a gravacao segue
        app = self.make_app({"gravar_video": False})
        p = app.capture_panel
        seen = []

        def real_status(pid, expected_index):
            seen.append(capture.mic_status(pid, expected_index))
            return seen[-1]

        p.hw = dataclasses.replace(p.hw, mic_status=real_status)
        with mock.patch("studio.devices._pactl", return_value=None):
            self.start_recording(app)
            self.assertTrue(pump_until(app, lambda: len(seen) >= 3))
            pump_for(app, 0.1)
            self.assertEqual("recording", p.state)
            p.btn_record.invoke()
            self.wait_idle(app)
        self.assertEqual({capture.MIC_UNKNOWN}, set(seen))
        self.assertEqual(1, self.log_text(app).count(f"AVISO: {capture.MSG_MIC_UNKNOWN}"))
        self.assertEqual("gravado", app.take.status)

    def test_duration_and_limit_stop_by_themselves(self):
        app = self.make_app({"gravar_video": False})
        p = app.capture_panel
        p.duration_var.set("0,3")
        self.start_recording(app)
        self.wait_idle(app)
        self.assertIn("Duração de 0,3 s atingida", self.log_text(app))
        p.duration_var.set("")
        with mock.patch.object(gui_capture, "MAX_TAKE_S", 0.3):
            self.start_recording(app)
            self.wait_idle(app)
        self.assertIn("Limite de 5 min atingido — gravação encerrada", self.log_text(app))
        self.assertEqual(["gravado", "gravado"], [t.status for t in list_takes(self.rec_dir)])

    # ---------- fechar ----------

    def test_close_while_recording_uses_closer_and_hook(self):
        app = self.make_app()
        p = app.capture_panel
        take, rec = self.start_recording(app)
        rec.push(RED)
        app.on_close()                                # respondeu "não"
        self.assertFalse(app.closed)
        self.assertIn("Gravação em andamento", self.confirm_calls[0][1])
        self.assertFalse(rec.stop_requested)
        self.confirm_answer = True
        app.on_close()
        self.assertTrue(app.closed)
        self.assertEqual([threading.main_thread().name], rec.wait_threads)
        self.assertEqual(5, rec.wait_args[0][0])
        self.assertIn("Finalizando gravação…", self.file_text())
        # a tomada fica "gravando": a abertura seguinte confere e recupera (recover_takes)
        self.assertEqual("gravando", Take.load(take.dir).status)

    def test_close_with_preview_only_stops_it_without_asking(self):
        app = self.make_app()
        app.capture_panel.cam_check.invoke()
        app.on_close()
        self.assertTrue(app.closed)
        self.assertEqual([], self.confirm_calls)
        self.assertEqual([threading.main_thread().name], self.caps[0].wait_threads)


if __name__ == "__main__":
    unittest.main()
