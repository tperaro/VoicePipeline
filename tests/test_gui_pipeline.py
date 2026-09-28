import errno
import gc
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import tkinter as tk
import unittest
from dataclasses import asdict
from tkinter import ttk
from types import SimpleNamespace
from unittest import mock

import numpy as np
import soundfile as sf

from studio import drive, gui, gui_capture, gui_pipeline, procs, render, timeline
from studio.devices import Source
from studio.rvc_client import RvcClient, RvcError
from studio.takes import Take, final_video_name
from tests import helpers

HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
MIC = "alsa_input.usb-teste.mono"
TAKE_ID = "2026-09-26_101500"
FOLDER_ID = "1AbCdEfGhIjKlMnOpQrStUv"
LINK = f"https://drive.google.com/drive/folders/{FOLDER_ID}?usp=sharing"
MD5 = "0123456789abcdef0123456789abcdef"
FAIL_MSG = "O vídeo gerado não passou na verificação: Texto da marca d'água não aparece no(s) frame(s) 0"


class FakeRvc:
    """Conversor falso: copia a entrada; die=True simula o worker morrendo no meio do job."""

    def __init__(self):
        self.starts = 0
        self.calls: list[tuple] = []
        self.die = False
        self._alive = False

    def start(self):
        self.starts += 1
        self._alive = True

    def alive(self):
        return self._alive

    def load(self):
        return {"id": "1", "ok": True}

    def close(self):
        self._alive = False

    def convert(self, inp, out, model_key):
        self.calls.append((threading.current_thread().name, inp, out, model_key))
        if self.die:
            self.die = False
            self._alive = False
            raise RvcError("O conversor fechou inesperadamente (código -9) — veja studio_rvc.log")
        data, sr = sf.read(inp, dtype="int16")
        sf.write(out, data, sr, subtype="PCM_16")
        return {"id": "2", "ok": True, "duracao": len(data) / sr, "sr": sr, "pedacos": 1}


class FakeRender:
    """render_final falso (sem GPU): 'ok' cria o MP4 final, 'fail' levanta, 'block' espera o Cancelar."""

    def __init__(self):
        self.mode = "ok"
        self.calls: list[SimpleNamespace] = []
        self.started = threading.Event()

    def __call__(self, take, modelo, conv_wav, av_offset_ms=0, videos_dir="", cancel=None):
        self.calls.append(SimpleNamespace(thread=threading.current_thread().name, take=take, modelo=modelo,
                                          conv_wav=conv_wav, av_offset_ms=av_offset_ms, videos_dir=videos_dir,
                                          cancel=cancel))
        self.started.set()
        if self.mode == "block":
            if not cancel.wait(10):
                raise AssertionError("ninguém cancelou o render")
            raise render.RenderError(render.CANCEL_MSG)
        if self.mode == "fail":
            raise render.RenderError(FAIL_MSG)
        os.makedirs(videos_dir, exist_ok=True)
        final = os.path.join(videos_dir, final_video_name(take.id, modelo.key))
        with open(final, "wb") as f:
            f.write(b"mp4 falso")
        return final


class FakeUpload:
    """upload_files falso (sem Drive): posta progresso 0 e 42 %; block=True espera o gate ou o Cancelar."""

    def __init__(self):
        self.calls: list[SimpleNamespace] = []
        self.block = False
        self.gate = threading.Event()
        self.result: dict | None = None

    def __call__(self, paths, folder_link, on_progress=None, cancel=None, dry_run=False, videos_dir=""):
        self.calls.append(SimpleNamespace(thread=threading.current_thread().name, paths=list(paths),
                                          link=folder_link, videos_dir=videos_dir, cancel=cancel))
        name = os.path.basename(paths[0])
        on_progress(name, 0.0)
        on_progress(name, 0.42)
        end = time.monotonic() + 10
        while self.block and not (self.gate.is_set() or cancel.is_set()) and time.monotonic() < end:
            time.sleep(0.01)
        res = {"arquivo": paths[0], "ok": True, "pulado": False, "md5": MD5, "erro": "", "aviso": ""}
        if cancel.is_set():
            res.update(ok=False, md5="", erro=drive.MSG_CANCELLED)
        elif self.result is not None:
            res.update(self.result)
        return [res]


class FakeProc:
    def __init__(self, argv, kw):
        self.argv, self.kw = argv, kw
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True


def pump_until(app, cond, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.root.update()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def state(widget) -> str:
    return str(widget.cget("state"))


def zombie(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return True


class ReconnectDriveTest(unittest.TestCase):
    def test_runs_reconnect_cmd_with_timeout(self):
        calls = []

        def run(argv, timeout=None):
            calls.append((argv, timeout))
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(drive, "rclone_bin", return_value="/opt/bin/rclone"):
            gui_pipeline.reconnect_drive(run=run)
            self.assertEqual([(drive.reconnect_cmd(), 300)], calls)
            self.assertEqual("/opt/bin/rclone", calls[0][0][0])

    def test_errors_in_portuguese(self):
        def timeout(argv, timeout=None):
            raise subprocess.TimeoutExpired(argv, timeout)

        def no_config(argv, timeout=None):
            return subprocess.CompletedProcess(argv, 1, "", "Failed: didn't find section in config file")

        with mock.patch.object(drive, "rclone_bin", return_value="/opt/bin/rclone"):
            with self.assertRaises(drive.DriveError) as cm:
                gui_pipeline.reconnect_drive(run=timeout)
            self.assertEqual(gui_pipeline.MSG_RECONNECT_TIMEOUT, str(cm.exception))
            with self.assertRaises(drive.DriveError) as cm:
                gui_pipeline.reconnect_drive(run=no_config)
            self.assertEqual(drive.MSG_NO_CONFIG, str(cm.exception))
        with mock.patch.object(drive, "rclone_bin", return_value=None):
            with self.assertRaises(drive.DriveError) as cm:
                gui_pipeline.reconnect_drive(run=no_config)
            self.assertEqual(drive.MSG_NO_RCLONE, str(cm.exception))


@unittest.skipUnless(HAS_DISPLAY, "sem display")
class PipelinePanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = tempfile.TemporaryDirectory()
        cls.av_raw = helpers.make_synthetic_take(os.path.join(cls.media.name, "raw.mkv"), duration_s=2.0,
                                                 flash_frame=30, size="320x240")
        cls.av_audio = os.path.join(cls.media.name, "audio.wav")
        vi, fit = timeline.extract_aligned_audio(cls.av_raw, cls.av_audio)
        cls.vi, cls.fit = asdict(vi), asdict(fit)
        cls.tone = os.path.join(cls.media.name, "tom.wav")
        t = np.arange(48000) / 48000
        sf.write(cls.tone, (0.2 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16), 48000, subtype="PCM_16")

    @classmethod
    def tearDownClass(cls):
        cls.media.cleanup()

    def setUp(self):
        # roda por ultimo: o lixo do Tk (App, StringVar, PhotoImage) morre na thread principal
        self.addCleanup(gc.collect)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rec_dir = os.path.join(self.tmp.name, "recordings")
        self.videos_dir = os.path.join(self.tmp.name, "videos_finais")
        self.estado_path = os.path.join(self.tmp.name, "estado.json")
        self.log_path = os.path.join(self.tmp.name, "studio.log")
        self.rvc = FakeRvc()
        self.render = FakeRender()
        self.upload = FakeUpload()
        self.spawned: list[FakeProc] = []
        self.answers: list[str | None] = []
        self.asked: list[tuple[str, str, str]] = []
        self.reconnects: list[str] = []
        self.reconnect_error: Exception | None = None
        self.extract_threads: list[str] = []
        self.confirm_calls: list[tuple[str, str]] = []
        self.confirm_answer = False
        # so a thread principal grava o take.json (spec 3.2)
        self.save_threads: list[str] = []
        original_save = Take.save

        def spy_save(take):
            self.save_threads.append(threading.current_thread().name)
            original_save(take)

        patcher = mock.patch.object(Take, "save", spy_save)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ---------- falsos ----------

    def spawn(self, argv, **kw):
        self.spawned.append(FakeProc(argv, kw))
        return self.spawned[-1]

    def ask(self, title, prompt, initial):
        self.asked.append((title, prompt, initial))
        return self.answers.pop(0) if self.answers else None

    def reconnect(self):
        self.reconnects.append(threading.current_thread().name)
        if self.reconnect_error:
            raise self.reconnect_error

    def extract(self, raw, out):
        self.extract_threads.append(threading.current_thread().name)
        return timeline.extract_aligned_audio(raw, out)

    def no_capture(self, *args):
        raise AssertionError("o painel de vídeo não pode abrir a câmera")

    def confirm(self, title, message):
        self.confirm_calls.append((title, message))
        return self.confirm_answer

    # ---------- montagem ----------

    def make_take(self, modo: str = "av", with_audio: bool = True, take_id: str = TAKE_ID) -> Take:
        take = Take(id=take_id, dir=os.path.join(self.rec_dir, take_id), modo=modo, status="gravado", mic=MIC)
        os.makedirs(take.dir)
        if modo == "av":
            shutil.copyfile(self.av_raw, take.raw_path)
            if with_audio:
                shutil.copyfile(self.av_audio, take.audio_path)
                take.video, take.audio_fit = dict(self.vi), dict(self.fit)
        else:
            shutil.copyfile(self.tone, take.raw_path)
        take.save()
        return take

    def converted(self, take: Take, key: str = "orochi", mp4: bool = False) -> str:
        # tomada ja convertida (e renderizada, com mp4=True), como o painel deixa
        shutil.copyfile(self.tone, take.path(f"{key}.wav"))
        shutil.copyfile(self.tone, take.path(f"{key}_IA.mp3"))
        take.saidas[key] = {"wav": f"{key}.wav", "mp3": f"{key}_IA.mp3"}
        take.status = "convertido"
        final = os.path.join(self.videos_dir, final_video_name(take.id, key))
        if mp4:
            os.makedirs(self.videos_dir, exist_ok=True)
            with open(final, "wb") as f:
                f.write(b"mp4 falso")
            take.saidas[key]["mp4"] = os.path.relpath(final, take.dir)
            take.status = "renderizado"
        take.save()
        return final

    def make_app(self, estado: dict | None = None, rvc_factory=None, **deps_kw) -> gui.App:
        if estado is not None:
            with open(self.estado_path, "w", encoding="utf-8") as f:
                json.dump(estado, f)
        root = tk.Tk()
        root.withdraw()
        hw = gui_capture.Hardware(list_cameras=lambda: [], list_mics=lambda: [Source(54, MIC)],
                                  free_bytes=lambda path: 50 * 1024**3, check_mic=lambda pid, idx: None)
        kw = dict(extract_aligned_audio=self.extract, render_final=self.render, upload_files=self.upload,
                  reconnect=self.reconnect, spawn=self.spawn, ask_link=self.ask, videos_dir=self.videos_dir)
        deps = gui_pipeline.PipelineDeps(**{**kw, **deps_kw})
        app = gui.App(root, rvc_factory=rvc_factory or (lambda: self.rvc), rec_dir=self.rec_dir,
                      estado_path=self.estado_path, log_path=self.log_path,
                      logs_dir=os.path.join(self.tmp.name, "logs"), ask_confirm=self.confirm,
                      capture_factory=self.no_capture, hardware=hw, pipeline_deps=deps)
        self.addCleanup(self.close_app, app)
        app.dispatch_events()                   # take_changed da abertura
        return app

    def close_app(self, app):
        if not app.closed:
            pump_until(app, lambda: not (app.gpu_jobs.busy or app.io_jobs.busy or app.upload_jobs.busy), 20)
            app.shutdown()
        self.assertLessEqual(set(self.save_threads), {"MainThread"})

    def log_text(self, app) -> str:
        return app.log_box.get("1.0", "end")

    def wait_done(self, app, timeout: float = 20.0) -> None:
        p = app.pipeline_panel
        self.assertTrue(pump_until(app, lambda: p.busy is None and p.uploading is None and not app.gpu_jobs.busy
                                   and not app.io_jobs.busy and not app.upload_jobs.busy, timeout),
                        self.log_text(app))

    # ---------- montagem da coluna ----------

    def test_sections_and_labels(self):
        app = self.make_app()
        p = app.pipeline_panel
        titles = [w.cget("text") for w in app.right.winfo_children() if isinstance(w, ttk.LabelFrame)]
        self.assertEqual(["1. Modelo", "2. Aumentar volume", "3. Converter", "4. Vídeo", "5. Drive"], titles)
        labels = [p.btn_boost, p.btn_convert, p.btn_play_rec, p.btn_play_out, p.btn_render, p.btn_cancel_render,
                  p.btn_watch, p.btn_open_folder, p.btn_upload, p.btn_drive_config, p.btn_reconnect,
                  p.btn_cancel_upload]
        self.assertEqual(["Aumentar volume", "Converter", "▶ Ouvir gravação", "▶ Ouvir resultado", "Gerar vídeo",
                          "Cancelar", "Assistir", "Abrir pasta", "Enviar", "Configurar Drive", "Reconectar",
                          "Cancelar"], [b.cget("text") for b in labels])
        self.assertIn("pipeline_render", gui.JOB_LABELS)
        # sem tomada: so o Drive e a pasta ficam liberados
        for b in (p.btn_boost, p.btn_convert, p.btn_play_rec, p.btn_play_out, p.btn_render, p.btn_cancel_render,
                  p.btn_watch, p.btn_upload, p.btn_cancel_upload):
            self.assertEqual("disabled", state(b), b.cget("text"))
        for b in (p.btn_open_folder, p.btn_drive_config, p.btn_reconnect):
            self.assertEqual("normal", state(b), b.cget("text"))
        self.assertIn("Nenhuma tomada", p.convert_label.cget("text"))
        self.assertIn("não configurada", p.drive_label.cget("text"))

    # ---------- converter e gerar o video ----------

    def test_convert_av_renders_automatically(self):
        take = self.make_take()
        app = self.make_app({"av_offset_ms": 40})
        p = app.pipeline_panel
        self.assertEqual("normal", state(p.btn_convert))
        self.assertEqual("disabled", state(p.btn_render))          # ainda nao convertido
        p.btn_convert.invoke()
        for b in (p.btn_convert, p.btn_boost, p.btn_render):
            self.assertEqual("disabled", state(b))
        self.wait_done(app)
        self.assertEqual([("job-gpu", take.audio_path, take.path("orochi.wav"), "orochi")], self.rvc.calls)
        (r,) = self.render.calls
        self.assertEqual("job-gpu", r.thread)
        self.assertIsNot(app.take, r.take)                         # o job recebe uma copia
        self.assertEqual(take.id, r.take.id)
        self.assertEqual(("orochi", take.path("orochi.wav"), 40, self.videos_dir),
                         (r.modelo.key, r.conv_wav, r.av_offset_ms, r.videos_dir))
        self.assertIsInstance(r.cancel, threading.Event)
        final = os.path.join(self.videos_dir, final_video_name(take.id, "orochi"))
        saved = Take.load(take.dir)
        self.assertEqual("renderizado", saved.status)
        self.assertEqual({"wav": "orochi.wav", "mp3": "orochi_IA.mp3",
                          "mp4": os.path.join("..", "..", "videos_finais", os.path.basename(final))},
                         saved.saidas["orochi"])
        self.assertEqual(saved.saidas, app.take.saidas)
        self.assertIn("IA", procs.media_info(take.path("orochi_IA.mp3"))["format"]["tags"]["comment"])
        self.assertIn(f"Pronto: {os.path.basename(final)}", p.video_label.cget("text"))
        for b in (p.btn_convert, p.btn_render, p.btn_watch, p.btn_upload, p.btn_play_out):
            self.assertEqual("normal", state(b), b.cget("text"))
        self.assertEqual("disabled", state(p.btn_cancel_render))
        self.assertIn(f"Voz convertida para Orochi: {take.id}", self.log_text(app))
        self.assertIn(f"Vídeo pronto: {os.path.basename(final)}", self.log_text(app))
        self.assertTrue(app.model_loaded)                          # a conversao ja carregou o modelo
        self.assertIn("carregado ✓", app.model_status.cget("text"))

    def test_render_reads_av_offset_from_file(self):
        # calibrar_av.py --salvar com o app aberto: o render usa o valor do arquivo, e o app nao o desfaz
        take = self.make_take()
        self.converted(take)
        app = self.make_app({"av_offset_ms": 0})
        p = app.pipeline_panel
        with open(self.estado_path, encoding="utf-8") as f:
            estado = json.load(f)
        estado["av_offset_ms"] = 40
        with open(self.estado_path, "w", encoding="utf-8") as f:
            json.dump(estado, f)
        p.btn_render.invoke()
        self.wait_done(app)
        app.capture_panel.mic_box.event_generate("<<ComboboxSelected>>")     # o app grava o estado.json
        p.btn_render.invoke()
        self.wait_done(app)
        self.assertEqual([40, 40], [c.av_offset_ms for c in self.render.calls])
        with open(self.estado_path, encoding="utf-8") as f:
            self.assertEqual(40, json.load(f)["av_offset_ms"])

    def test_wrong_types_in_estado_open_with_defaults(self):
        # estado.json editado a mao com tipo errado: o app abre com os padroes e avisa (correcao da Task 1)
        take = self.make_take()
        self.converted(take)
        app = self.make_app({"drive_pasta": 123, "camera": 5, "av_offset_ms": "x"})
        p = app.pipeline_panel
        self.assertIn("AVISO: estado.json: valor inválido em camera, drive_pasta, av_offset_ms — usando o padrão",
                      self.log_text(app))
        self.assertIn("não configurada", p.drive_label.cget("text"))
        self.assertEqual("", app.capture_panel.camera_path())
        p.btn_render.invoke()
        self.wait_done(app)
        self.assertEqual(0, self.render.calls[0].av_offset_ms)

    def test_audio_only_take_does_not_render(self):
        take = self.make_take(modo="audio")
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_convert.invoke()
        self.wait_done(app)
        self.assertEqual(take.raw_path, self.rvc.calls[0][1])
        self.assertEqual([], self.render.calls)
        saved = Take.load(take.dir)
        self.assertEqual("convertido", saved.status)
        self.assertEqual({"orochi": {"wav": "orochi.wav", "mp3": "orochi_IA.mp3"}}, saved.saidas)
        self.assertEqual("disabled", state(p.btn_render))
        self.assertEqual("disabled", state(p.btn_upload))
        self.assertEqual("normal", state(p.btn_play_out))
        self.assertIn("só de áudio", p.video_label.cget("text"))

    def test_recovered_take_extracts_audio_before_converting(self):
        take = self.make_take(with_audio=False)                    # como o recover_takes deixa
        self.assertFalse(os.path.exists(take.audio_path))
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_convert.invoke()
        self.wait_done(app)
        self.assertEqual(["job-io"], self.extract_threads)
        self.assertEqual(take.audio_path, self.rvc.calls[0][1])
        saved = Take.load(take.dir)
        self.assertEqual(self.vi["n_frames"], saved.video["n_frames"])
        self.assertIn("taxa_real", saved.audio_fit)
        self.assertEqual("renderizado", saved.status)
        self.assertEqual(saved.video, self.render.calls[0].take.video)
        self.assertIn(f"Áudio alinhado da tomada {take.id} preparado", self.log_text(app))
        self.assertNotIn("buraco", self.log_text(app))

    def test_recovered_take_with_gaps_warns(self):
        # buraco no audio (spec 6.3.3): o alinhamento usa o modo assincrono e o log avisa
        take = self.make_take(with_audio=False)

        def extract_with_gaps(raw, out):
            vi, fit = timeline.extract_aligned_audio(raw, out)
            fit.gaps = 3
            return vi, fit

        app = self.make_app(extract_aligned_audio=extract_with_gaps)
        app.pipeline_panel.btn_convert.invoke()
        self.wait_done(app)
        self.assertEqual(3, Take.load(take.dir).audio_fit["gaps"])
        self.assertIn(f"AVISO: o áudio da tomada {take.id} teve 3 buraco(s) acima de 30 ms; o alinhamento usou o "
                      "modo assíncrono — confira a sincronia", self.log_text(app))

    def test_boost_then_convert_uses_boosted(self):
        take = self.make_take(modo="audio")
        app = self.make_app()
        p = app.pipeline_panel
        self.assertEqual(str(gui_pipeline.DEFAULT_GAIN_DB), p.gain_var.get())
        p.gain_var.set("abc")
        p.btn_boost.invoke()
        self.assertIn("Ganho inválido", self.log_text(app))
        self.assertIsNone(p.busy)
        p.gain_var.set("6")
        p.btn_boost.invoke()
        self.assertEqual("disabled", state(p.btn_convert))
        self.wait_done(app)
        self.assertTrue(os.path.exists(take.path("boosted.wav")))
        self.assertIn("Volume aumentado em 6 dB", self.log_text(app))
        self.assertIn("pico", p.volume_label.cget("text"))
        p.btn_convert.invoke()
        self.wait_done(app)
        self.assertEqual(take.path("boosted.wav"), self.rvc.calls[0][1])

    def test_render_failure_shows_message_and_reenables(self):
        take = self.make_take()
        self.render.mode = "fail"
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_convert.invoke()
        self.wait_done(app)
        saved = Take.load(take.dir)
        # o erro fica no modelo; "falhou" e so da gravacao (a tomada continua utilizavel)
        self.assertEqual(("convertido", ""), (saved.status, saved.erro))
        self.assertEqual(FAIL_MSG, saved.saidas["orochi"]["erro"])
        self.assertNotIn("mp4", saved.saidas["orochi"])
        self.assertIn(f"ERRO ao gerar o vídeo: {FAIL_MSG}", self.log_text(app))
        self.assertIn("Falhou", p.video_label.cget("text"))
        self.assertEqual(gui_pipeline.COLOR_BAD, str(p.video_label.cget("foreground")))
        for b in (p.btn_convert, p.btn_render, p.btn_boost):
            self.assertEqual("normal", state(b), b.cget("text"))
        for b in (p.btn_cancel_render, p.btn_watch, p.btn_upload):
            self.assertEqual("disabled", state(b), b.cget("text"))
        # Gerar video refaz so o video, sem converter de novo
        self.render.mode = "ok"
        p.btn_render.invoke()
        self.wait_done(app)
        self.assertEqual(1, len(self.rvc.calls))
        self.assertEqual(("renderizado", ""), (app.take.status, app.take.erro))
        self.assertNotIn("erro", Take.load(take.dir).saidas["orochi"])      # o sucesso limpa o erro
        self.assertIn("Pronto", p.video_label.cget("text"))

    def test_missing_wav_does_not_render(self):
        take = self.make_take()

        def mp3_and_lose_wav(wav, mp3, modelo):
            shutil.copyfile(wav, mp3)
            os.remove(wav)                                         # o WAV sumiu antes do render

        app = self.make_app(export_mp3=mp3_and_lose_wav)
        p = app.pipeline_panel
        p.btn_convert.invoke()
        self.wait_done(app)
        self.assertEqual([], self.render.calls)
        self.assertIn(f"ERRO ao gerar o vídeo: {gui_pipeline.MSG_NO_WAV}", self.log_text(app))
        self.assertIn(gui_pipeline.MSG_NO_WAV, p.video_label.cget("text"))
        self.assertEqual("normal", state(p.btn_convert))

    def test_cancel_render(self):
        take = self.make_take()
        self.converted(take)
        self.render.mode = "block"
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_render.invoke()
        self.assertTrue(self.render.started.wait(5))
        self.assertEqual("normal", state(p.btn_cancel_render))
        self.assertEqual("disabled", state(p.btn_render))
        p.btn_cancel_render.invoke()
        self.wait_done(app)
        self.assertTrue(self.render.calls[0].cancel.is_set())
        self.assertIn("Vídeo cancelado", self.log_text(app))
        self.assertNotIn("ERRO", self.log_text(app))
        self.assertEqual("convertido", Take.load(take.dir).status)
        self.assertEqual("normal", state(p.btn_render))
        self.assertEqual("disabled", state(p.btn_cancel_render))

    def test_reconvert_drops_old_mp4_but_keeps_enviado(self):
        # reconverter por cima de um video ja enviado: o mp4 antigo some (o novo sai sozinho, encadeado), mas
        # o "enviado" fica (o arquivo antigo continua no Drive; enviar_drive.py usa o md5 pra nao reenviar)
        take = self.make_take()
        self.converted(take, mp4=True)
        take.saidas["orochi"]["enviado"] = {"md5": MD5, "quando": "2026-09-26T10:20:00"}
        take.save()
        self.render.mode = "block"
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_convert.invoke()
        self.assertTrue(pump_until(app, lambda: self.render.started.is_set(), 10))
        saved = Take.load(take.dir)
        self.assertNotIn("mp4", saved.saidas["orochi"])
        self.assertEqual({"md5": MD5, "quando": "2026-09-26T10:20:00"}, saved.saidas["orochi"]["enviado"])
        self.assertEqual("disabled", state(p.btn_watch))
        self.assertEqual("disabled", state(p.btn_upload))
        self.assertNotIn("Pronto", p.video_label.cget("text"))
        self.assertNotIn("Enviado", p.drive_label.cget("text"))
        p.btn_cancel_render.invoke()
        self.wait_done(app)
        self.assertNotIn("mp4", Take.load(take.dir).saidas["orochi"])
        self.assertNotIn("Pronto", p.video_label.cget("text"))
        self.assertNotIn("Enviado", p.drive_label.cget("text"))

    def test_dead_worker_asks_to_click_again_and_restarts(self):
        take = self.make_take(modo="audio")
        rvc_log = os.path.join(self.tmp.name, "rvc.log")
        app = self.make_app(rvc_factory=lambda: RvcClient(log_path=rvc_log, env={"STUDIO_RVC_FAKE": "1"}))
        p = app.pipeline_panel
        p.btn_convert.invoke()
        self.wait_done(app, timeout=30)
        self.assertEqual("convertido", Take.load(take.dir).status)
        pid = app.rvc._proc.pid
        # segura a fila gpu para o worker morrer entre o clique e o job
        gate = threading.Event()
        self.addCleanup(gate.set)
        app.gpu_jobs.submit("segura", gate.wait, 10)
        p.btn_convert.invoke()
        os.kill(pid, signal.SIGKILL)
        end = time.monotonic() + 5
        while not zombie(pid) and time.monotonic() < end:
            time.sleep(0.01)
        gate.set()
        self.wait_done(app)
        self.assertIn(gui_pipeline.MSG_WORKER_DIED, self.log_text(app))
        self.assertIn(gui_pipeline.MSG_WORKER_DIED, p.convert_label.cget("text"))
        self.assertEqual("normal", state(p.btn_convert))
        self.assertFalse(app.rvc.alive())
        p.btn_convert.invoke()                                     # recria o worker
        self.wait_done(app, timeout=30)
        self.assertNotEqual(pid, app.rvc._proc.pid)
        self.assertEqual("convertido", Take.load(take.dir).status)
        self.assertNotIn(gui_pipeline.MSG_WORKER_DIED, p.convert_label.cget("text"))

    def test_fake_worker_death_marks_take(self):
        take = self.make_take(modo="audio")
        self.rvc.die = True
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_convert.invoke()
        self.wait_done(app)
        self.assertEqual(1, self.rvc.starts)
        saved = Take.load(take.dir)
        self.assertEqual(("gravado", ""), (saved.status, saved.erro))         # a gravacao continua boa
        self.assertIn("fechou inesperadamente", saved.saidas["orochi"]["erro"])
        p.btn_convert.invoke()
        self.wait_done(app)
        self.assertEqual(2, self.rvc.starts)
        self.assertEqual(("convertido", ""), (app.take.status, app.take.erro))
        self.assertNotIn("erro", Take.load(take.dir).saidas["orochi"])       # o sucesso limpa o erro

    def test_model_error_follows_the_chosen_model(self):
        # erro de conversao do Orochi: some com o Silvio escolhido, volta com o Orochi e sobrevive a reabertura
        take = self.make_take(modo="audio")

        def no_checkpoint(inp, out, model_key):
            raise RvcError(f"Nenhum checkpoint do modelo {model_key}")

        self.rvc.convert = no_checkpoint
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_convert.invoke()
        self.wait_done(app)
        self.assertEqual("Falhou: Nenhum checkpoint do modelo orochi", p.convert_label.cget("text"))
        for label in ("Silvio Santos", "Orochi"):
            app.model_var.set(label)
            app.model_box.event_generate("<<ComboboxSelected>>")
            app.dispatch_events()
            if label == "Silvio Santos":
                self.assertEqual(f"Tomada {take.id}: pronta para converter para Silvio Santos",
                                 p.convert_label.cget("text"))
        expected = f"Tomada {take.id}: a conversão para Orochi falhou — Nenhum checkpoint do modelo orochi"
        self.assertEqual(expected, p.convert_label.cget("text"))
        self.assertEqual(gui_pipeline.COLOR_BAD, str(p.convert_label.cget("foreground")))
        app.shutdown()
        again = self.make_app()                                    # reabrir: a tomada continua a ultima
        self.assertEqual(take.id, again.take.id)
        self.assertEqual(expected, again.pipeline_panel.convert_label.cget("text"))
        self.assertEqual("normal", state(again.pipeline_panel.btn_convert))

    def test_disk_full_errors_reach_the_labels(self):
        # "Disco cheio" do render (RenderError) e da conversao (RvcError) aparecem no rotulo e reabilitam
        take = self.make_take()
        self.converted(take)

        def render_full(*args, **kwargs):
            raise render.RenderError(procs.MSG_DISK_FULL)

        def convert_full(inp, out, model_key):
            raise RvcError(procs.MSG_DISK_FULL)

        app = self.make_app(render_final=render_full)
        p = app.pipeline_panel
        p.btn_render.invoke()
        self.wait_done(app)
        self.assertEqual("Falhou: Disco cheio — libere espaço", p.video_label.cget("text"))
        self.assertIn("ERRO ao gerar o vídeo: Disco cheio — libere espaço", self.log_text(app))
        self.assertEqual(procs.MSG_DISK_FULL, Take.load(take.dir).saidas["orochi"]["erro"])
        self.rvc.convert = convert_full
        p.btn_convert.invoke()
        self.wait_done(app)
        self.assertEqual("Falhou: Disco cheio — libere espaço", p.convert_label.cget("text"))
        for b in (p.btn_convert, p.btn_render, p.btn_boost):
            self.assertEqual("normal", state(b), b.cget("text"))

    def test_take_save_failure_shows_error_and_does_not_render(self):
        # disco cheio ao gravar o take.json depois de converter: erro no rotulo, nada de "convertida" e sem video
        take = self.make_take()
        app = self.make_app()
        p = app.pipeline_panel
        with mock.patch.object(Take, "save", side_effect=OSError(errno.ENOSPC, "No space left on device")):
            p.btn_convert.invoke()
            self.wait_done(app)
        self.assertEqual([], self.render.calls)
        self.assertEqual("Falhou: Disco cheio — libere espaço", p.convert_label.cget("text"))
        self.assertIn("ERRO ao salvar a tomada: Disco cheio — libere espaço", self.log_text(app))
        self.assertEqual(({}, "gravado"), (app.take.saidas, app.take.status))    # a memoria volta ao take.json
        self.assertEqual({}, Take.load(take.dir).saidas)
        self.assertEqual("normal", state(p.btn_convert))
        self.assertEqual("disabled", state(p.btn_render))

    # ---------- gravacao e troca de tomada ----------

    def test_recording_disables_processing_buttons(self):
        take = self.make_take()
        self.converted(take, mp4=True)
        app = self.make_app()
        p = app.pipeline_panel
        busy_while_recording = (p.btn_boost, p.btn_convert, p.btn_render, p.btn_play_rec, p.btn_play_out,
                                p.btn_watch)
        for b in busy_while_recording:
            self.assertEqual("normal", state(b), b.cget("text"))
        app.bus.post("recording", active=True, take=None)
        app.dispatch_events()
        for b in busy_while_recording:
            self.assertEqual("disabled", state(b), b.cget("text"))
        self.assertEqual("normal", state(p.btn_upload))           # o envio nao usa a camera nem o mic
        p.btn_convert.invoke()
        self.assertEqual([], self.rvc.calls)
        app.bus.post("recording", active=False, take=None)
        app.dispatch_events()
        for b in busy_while_recording:
            self.assertEqual("normal", state(b), b.cget("text"))

    def test_recording_stops_playback(self):
        # um som tocando (ex.: "Ouvir resultado") entraria no microfone da gravacao nova: a gravacao para o som
        take = self.make_take()
        self.converted(take, mp4=True)
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_play_out.invoke()
        player = self.spawned[-1]
        self.assertFalse(player.terminated)
        app.bus.post("recording", active=True, take=None)
        app.dispatch_events()
        self.assertTrue(player.terminated)

    def test_take_changed_updates_status_and_buttons(self):
        av = self.make_take()
        self.converted(av, mp4=True)
        app = self.make_app()
        p = app.pipeline_panel
        self.assertIn("Pronto", p.video_label.cget("text"))
        self.assertIn(av.id, p.convert_label.cget("text"))
        audio_take = self.make_take(modo="audio", take_id="2026-09-26_110000")
        app.set_take(audio_take)
        self.assertTrue(pump_until(app, lambda: "só de áudio" in p.video_label.cget("text")))
        self.assertIn(audio_take.id, p.convert_label.cget("text"))
        for b in (p.btn_render, p.btn_watch, p.btn_upload, p.btn_play_out):
            self.assertEqual("disabled", state(b), b.cget("text"))
        for b in (p.btn_convert, p.btn_boost, p.btn_play_rec):
            self.assertEqual("normal", state(b), b.cget("text"))
        app.set_take(None)
        self.assertTrue(pump_until(app, lambda: "Nenhuma tomada" in p.convert_label.cget("text")))
        for b in (p.btn_convert, p.btn_boost, p.btn_play_rec, p.btn_render):
            self.assertEqual("disabled", state(b), b.cget("text"))

    def test_model_change_updates_buttons(self):
        take = self.make_take()
        self.converted(take, mp4=True)
        app = self.make_app()
        p = app.pipeline_panel
        self.assertEqual("normal", state(p.btn_render))
        app.model_var.set("Silvio Santos")
        app.model_box.event_generate("<<ComboboxSelected>>")
        app.dispatch_events()
        for b in (p.btn_render, p.btn_play_out, p.btn_watch, p.btn_upload):
            self.assertEqual("disabled", state(b), b.cget("text"))
        self.assertIn("Silvio Santos", p.convert_label.cget("text"))

    # ---------- ouvir, assistir e abrir pasta ----------

    def test_players_and_openers(self):
        take = self.make_take()
        final = self.converted(take, mp4=True)
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_play_rec.invoke()
        p.btn_play_out.invoke()
        p.btn_watch.invoke()
        p.btn_open_folder.invoke()
        self.assertEqual([["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", take.audio_path],
                          ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", take.path("orochi.wav")],
                          ["xdg-open", final], ["xdg-open", self.videos_dir]], [s.argv for s in self.spawned])
        self.assertTrue(self.spawned[0].terminated)                # um som por vez
        self.assertFalse(self.spawned[1].terminated)
        for s in self.spawned:
            self.assertEqual(subprocess.DEVNULL, s.kw["stdout"])
            self.assertEqual(subprocess.DEVNULL, s.kw["stderr"])
        app.shutdown()
        self.assertTrue(self.spawned[1].terminated)                # fechar para o som

    # ---------- Drive ----------

    def test_upload_without_folder_opens_config_then_sends(self):
        take = self.make_take()
        final = self.converted(take, mp4=True)
        self.answers = [LINK]
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_upload.invoke()
        self.assertEqual(1, len(self.asked))
        with open(self.estado_path, encoding="utf-8") as f:
            self.assertEqual(LINK, json.load(f)["drive_pasta"])
        self.wait_done(app)
        (call,) = self.upload.calls
        self.assertEqual(("job-upload", [final], LINK, self.videos_dir),
                         (call.thread, call.paths, call.link, call.videos_dir))
        enviado = Take.load(take.dir).saidas["orochi"]["enviado"]
        self.assertEqual(MD5, enviado["md5"])
        self.assertRegex(enviado["quando"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
        self.assertEqual(enviado, app.take.saidas["orochi"]["enviado"])
        self.assertIn(f"Enviado pro Drive: {os.path.basename(final)}", self.log_text(app))
        self.assertEqual(100, float(p.progress.cget("value")))
        self.assertIn("Enviado", p.drive_label.cget("text"))

    def test_upload_reads_folder_from_file_on_click(self):
        # enviar_drive.py --pasta com o app aberto: o Enviar manda para a pasta do arquivo, nao a da memoria
        take = self.make_take()
        final = self.converted(take, mp4=True)
        app = self.make_app({"drive_pasta": LINK})
        p = app.pipeline_panel
        other = f"https://drive.google.com/drive/folders/{FOLDER_ID}Z"
        with open(self.estado_path, encoding="utf-8") as f:
            estado = json.load(f)
        estado["drive_pasta"] = other
        with open(self.estado_path, "w", encoding="utf-8") as f:
            json.dump(estado, f)
        p.btn_upload.invoke()
        self.wait_done(app)
        self.assertEqual([(final, other)], [(c.paths[0], c.link) for c in self.upload.calls])
        self.assertEqual(other, app.estado["drive_pasta"])
        self.assertIn(f"{FOLDER_ID}Z", p.drive_label.cget("text"))
        # arquivo corrompido ou com tipo errado: fica com o link da memoria (e avisa)
        for text, aviso in (("{quebrado", "estado.json corrompido — usando padrões"),
                            ('{"drive_pasta": 123}', "estado.json: valor inválido em drive_pasta")):
            with open(self.estado_path, "w", encoding="utf-8") as f:
                f.write(text)
            p.btn_upload.invoke()
            self.wait_done(app)
            self.assertEqual(other, self.upload.calls[-1].link)
            self.assertIn(f"AVISO: {aviso}", self.log_text(app))
        self.assertEqual(3, len(self.upload.calls))
        self.assertEqual([], self.asked)

    def test_upload_click_during_dialog_sends_once(self):
        # o dialogo do link roda o laco de eventos: um 2o clique em Enviar nao pode enfileirar outro envio
        take = self.make_take()
        self.converted(take, mp4=True)
        asked = []

        def ask(title, prompt, initial):
            asked.append(title)
            if len(asked) == 1:
                app.pipeline_panel.on_upload()                     # o clique que chega com o dialogo aberto
            return LINK

        app = self.make_app(ask_link=ask)
        app.pipeline_panel.btn_upload.invoke()
        self.wait_done(app)
        self.assertEqual(2, len(asked))
        self.assertEqual(1, len(self.upload.calls))

    def test_upload_dialog_cancelled_sends_nothing(self):
        take = self.make_take()
        self.converted(take, mp4=True)
        app = self.make_app()
        app.pipeline_panel.btn_upload.invoke()
        self.assertEqual(1, len(self.asked))
        app.root.update()
        self.assertEqual([], self.upload.calls)
        self.assertEqual("", app.estado["drive_pasta"])

    def test_invalid_link_is_rejected(self):
        app = self.make_app({"drive_pasta": LINK})
        p = app.pipeline_panel
        self.assertIn(FOLDER_ID, p.drive_label.cget("text"))
        self.answers = [f"https://drive.google.com/file/d/{FOLDER_ID}/view", "https://example.com/folders/x", None]
        p.btn_drive_config.invoke()
        self.assertEqual(3, len(self.asked))
        self.assertEqual(LINK, self.asked[0][2])                    # comeca com o link atual
        self.assertIn("Esse link é de um arquivo, não de uma pasta", self.asked[1][1])
        self.assertIn("Isso não é um link do Google Drive", self.asked[2][1])
        self.assertIn("Link do Drive recusado: Esse link é de um arquivo", self.log_text(app))
        self.assertEqual(LINK, app.estado["drive_pasta"])
        with open(self.estado_path, encoding="utf-8") as f:
            self.assertEqual(LINK, json.load(f)["drive_pasta"])
        # link valido: salva
        self.answers = [f"  https://drive.google.com/drive/u/1/folders/{FOLDER_ID}x  "]
        p.btn_drive_config.invoke()
        self.assertEqual(f"https://drive.google.com/drive/u/1/folders/{FOLDER_ID}x", app.estado["drive_pasta"])
        self.assertIn(f"Pasta do Drive configurada (ID {FOLDER_ID}x)", self.log_text(app))

    def test_progress_updates_bar(self):
        take = self.make_take()
        self.converted(take, mp4=True)
        self.upload.block = True
        app = self.make_app({"drive_pasta": LINK})
        p = app.pipeline_panel
        p.btn_upload.invoke()
        self.assertTrue(pump_until(app, lambda: float(p.progress.cget("value")) == 42))
        self.assertIn("42%", p.drive_label.cget("text"))
        for b in (p.btn_upload, p.btn_drive_config, p.btn_reconnect):
            self.assertEqual("disabled", state(b), b.cget("text"))
        self.assertEqual("normal", state(p.btn_cancel_upload))
        self.assertIn("Envio para o Drive em andamento", app.close_reasons())
        self.upload.gate.set()
        self.wait_done(app)
        self.assertEqual(100, float(p.progress.cget("value")))
        self.assertEqual("normal", state(p.btn_upload))
        self.assertEqual("disabled", state(p.btn_cancel_upload))

    def test_cancel_upload(self):
        take = self.make_take()
        self.converted(take, mp4=True)
        self.upload.block = True
        app = self.make_app({"drive_pasta": LINK})
        p = app.pipeline_panel
        p.btn_upload.invoke()
        self.assertTrue(pump_until(app, lambda: float(p.progress.cget("value")) == 42))
        p.btn_cancel_upload.invoke()
        self.wait_done(app)
        self.assertTrue(self.upload.calls[0].cancel.is_set())
        self.assertIn("Envio cancelado", self.log_text(app))
        self.assertNotIn("enviado", Take.load(take.dir).saidas["orochi"])
        self.assertEqual(0, float(p.progress.cget("value")))
        self.assertEqual("normal", state(p.btn_upload))

    def test_upload_error_is_shown(self):
        take = self.make_take()
        self.converted(take, mp4=True)
        self.upload.result = {"ok": False, "md5": "", "erro": drive.MSG_RELOGIN}
        app = self.make_app({"drive_pasta": LINK})
        p = app.pipeline_panel
        p.btn_upload.invoke()
        self.wait_done(app)
        self.assertIn(f"ERRO no envio: {drive.MSG_RELOGIN}", self.log_text(app))
        self.assertIn(drive.MSG_RELOGIN, p.drive_label.cget("text"))
        self.assertEqual(gui_pipeline.COLOR_BAD, str(p.drive_label.cget("foreground")))
        self.assertNotIn("enviado", Take.load(take.dir).saidas["orochi"])

    def test_reconnect(self):
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_reconnect.invoke()
        self.assertEqual("disabled", state(p.btn_reconnect))
        self.wait_done(app)
        self.assertEqual(["job-upload"], self.reconnects)
        self.assertIn("Drive reconectado", self.log_text(app))
        self.reconnect_error = drive.DriveError(drive.MSG_NO_RCLONE)
        p.btn_reconnect.invoke()
        self.wait_done(app)
        self.assertIn(f"ERRO ao reconectar o Drive: {drive.MSG_NO_RCLONE}", self.log_text(app))
        self.assertEqual("normal", state(p.btn_reconnect))

    # ---------- fechar ----------

    def test_close_asks_while_rendering_and_cancels(self):
        take = self.make_take()
        self.converted(take)
        self.render.mode = "block"
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_render.invoke()
        self.assertTrue(self.render.started.wait(5))
        app.on_close()                                            # respondeu "nao"
        self.assertFalse(app.closed)
        self.assertIn("Gerando o vídeo", self.confirm_calls[0][1])
        self.assertFalse(self.render.calls[0].cancel.is_set())
        self.confirm_answer = True
        app.on_close()
        self.assertTrue(app.closed)
        self.assertTrue(self.render.calls[0].cancel.is_set())     # o ffmpeg para e o .part sai


if __name__ == "__main__":
    unittest.main()
