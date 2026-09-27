import gc
import json
import os
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest import mock

import numpy as np
import soundfile as sf

from studio import gui
from studio.config import DEFAULT_ESTADO
from studio.rvc_client import RvcClient, RvcError
from studio.takes import Take, new_take

HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


class FakeRvc:
    def __init__(self, load_error: Exception | None = None):
        self.load_error = load_error
        self.start_threads: list[str] = []
        self.loads = 0
        self.closed = 0
        self._alive = False

    def start(self):
        self.start_threads.append(threading.current_thread().name)
        self._alive = True

    def alive(self):
        return self._alive

    def load(self):
        self.loads += 1
        if self.load_error:
            raise self.load_error
        return {"id": "1", "ok": True}

    def close(self):
        self.closed += 1
        self._alive = False


def pump_until(app, cond, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.root.update()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def write_audio_take(rec_dir: str, take_id: str, status: str, seconds: float = 0.5) -> Take:
    take = Take(id=take_id, dir=os.path.join(rec_dir, take_id), modo="audio", status=status, mic="mic")
    os.makedirs(take.dir)
    sf.write(take.raw_path, np.zeros(int(48000 * seconds), dtype=np.int16), 48000, subtype="PCM_16")
    take.save()
    return take


@unittest.skipUnless(HAS_DISPLAY, "sem display")
class GuiShellTest(unittest.TestCase):
    def setUp(self):
        # roda por ultimo: o lixo do Tk (StringVar, Tk) tem que morrer na thread principal; se o GC
        # automatico o coletar numa thread de trabalho, o Tcl aborta o processo (Tcl_AsyncDelete)
        self.addCleanup(gc.collect)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rec_dir = os.path.join(self.tmp.name, "recordings")
        self.estado_path = os.path.join(self.tmp.name, "estado.json")
        self.log_path = os.path.join(self.tmp.name, "studio.log")
        self.logs_dir = os.path.join(self.tmp.name, "logs")
        self.confirm_calls: list[tuple[str, str]] = []
        self.confirm_answer = False
        self.hook_before = threading.excepthook

    def confirm(self, title: str, message: str) -> bool:
        self.confirm_calls.append((title, message))
        return self.confirm_answer

    def make_app(self, **kw) -> gui.App:
        root = tk.Tk()
        root.withdraw()
        kw.setdefault("rvc_factory", FakeRvc)
        app = gui.App(root, rec_dir=self.rec_dir, estado_path=self.estado_path, log_path=self.log_path,
                      logs_dir=self.logs_dir, ask_confirm=self.confirm, **kw)
        self.addCleanup(self.close_app, app)
        return app

    def close_app(self, app: gui.App) -> None:
        if not app.closed:
            app.shutdown()
        self.assertIs(self.hook_before, threading.excepthook)

    def log_text(self, app: gui.App) -> str:
        return app.log_box.get("1.0", "end")

    def file_text(self) -> str:
        with open(self.log_path, encoding="utf-8") as f:
            return f.read()

    # ---------- janela ----------

    def test_window_shell(self):
        app = self.make_app()
        self.assertEqual("Voice Studio (Orochi / Silvio)", app.root.title())
        self.assertEqual((True, True), app.root.resizable())
        w, h = app.root.minsize()
        self.assertGreaterEqual(w, 900)
        self.assertGreaterEqual(h, 600)
        self.assertIs(app.root, app.left.winfo_toplevel())
        self.assertIs(app.root, app.right.winfo_toplevel())
        self.assertEqual("1. Modelo", app.model_frame.cget("text"))
        self.assertIs(app.right, app.model_frame.master)
        self.assertEqual("gpu", app.gpu_jobs.name)
        self.assertEqual("io", app.io_jobs.name)
        self.assertEqual("upload", app.upload_jobs.name)
        self.assertIsNone(app.rvc)       # o worker so nasce quando alguem pede
        self.assertIsNone(app.take)

    def test_add_section_goes_to_right_column(self):
        app = self.make_app()
        frame = app.add_section("2. Volume")
        self.assertIs(app.right, frame.master)
        self.assertEqual("2. Volume", frame.cget("text"))

    # ---------- log ----------

    def test_log_writes_panel_and_file(self):
        app = self.make_app()
        app.log("Olá, gravação ✓")
        self.assertRegex(self.log_text(app), r"\[\d\d:\d\d:\d\d\] Olá, gravação ✓")
        self.assertRegex(self.file_text(), r"\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] Olá, gravação ✓")

    def test_log_from_other_thread_goes_through_bus(self):
        app = self.make_app()
        t = threading.Thread(target=app.log, args=("vindo da thread",))
        t.start()
        t.join()
        self.assertNotIn("vindo da thread", self.log_text(app))
        app.dispatch_events()
        self.assertIn("vindo da thread", self.log_text(app))

    def test_log_event_from_worker(self):
        app = self.make_app()
        app.bus.post("log", msg="mensagem do job")
        self.assertTrue(pump_until(app, lambda: "mensagem do job" in self.log_text(app)))

    # ---------- estado ----------

    def test_estado_corrompido_gera_aviso(self):
        with open(self.estado_path, "w") as f:
            f.write("{nao e json")
        app = self.make_app()
        self.assertEqual(DEFAULT_ESTADO, app.estado)
        self.assertIn("AVISO: estado.json corrompido", self.log_text(app))

    def test_estado_saved_on_change(self):
        app = self.make_app()
        self.assertFalse(os.path.exists(self.estado_path))
        app.update_estado(mic="alsa_input.usb-teste", av_offset_ms=40)
        with open(self.estado_path, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual("alsa_input.usb-teste", saved["mic"])
        self.assertEqual(40, saved["av_offset_ms"])
        self.assertEqual("alsa_input.usb-teste", app.estado["mic"])

    def test_estado_save_error_is_logged(self):
        app = self.make_app()
        app.estado_path = os.path.join(self.tmp.name, "nao", "existe", "estado.json")
        app.update_estado(mic="x")
        self.assertIn("Não foi possível salvar estado.json", self.log_text(app))

    # ---------- modelo ----------

    def test_model_from_estado_and_change(self):
        os.makedirs(os.path.join(self.logs_dir, "silvio"))
        for name in ("silvio_100e_1000s.pth", "silvio_350e_3500s.pth"):
            open(os.path.join(self.logs_dir, "silvio", name), "w").close()
        with open(self.estado_path, "w") as f:
            json.dump({"modelo": "orochi"}, f)
        app = self.make_app()
        got = []
        app.on("model_changed", got.append)
        self.assertEqual("orochi", app.modelo().key)
        self.assertIn("NENHUM CHECKPOINT", app.checkpoint_label.cget("text"))
        self.assertIn("AVISO: nenhum checkpoint", self.log_text(app))

        app.model_var.set("Silvio Santos")
        app.model_box.event_generate("<<ComboboxSelected>>")
        app.dispatch_events()
        self.assertEqual("silvio", app.modelo().key)
        self.assertEqual("Checkpoint: silvio_350e_3500s.pth", app.checkpoint_label.cget("text"))
        self.assertEqual("silvio", got[0].data["modelo"].key)
        with open(self.estado_path, encoding="utf-8") as f:
            self.assertEqual("silvio", json.load(f)["modelo"])
        self.assertIn("Modelo selecionado: Silvio Santos", self.log_text(app))

    def test_unknown_model_in_estado_falls_back(self):
        with open(self.estado_path, "w") as f:
            json.dump({"modelo": "xyz"}, f)
        app = self.make_app()
        self.assertEqual("orochi", app.modelo().key)
        self.assertEqual("Orochi", app.model_var.get())

    # ---------- erros ----------

    def test_callback_exception_vira_linha_no_log(self):
        app = self.make_app()

        def bad():
            return 1 / 0

        app.root.after(0, bad)
        self.assertTrue(pump_until(app, lambda: "ZeroDivisionError" in self.log_text(app)))
        line = [x for x in self.log_text(app).splitlines() if "ZeroDivisionError" in x][0]
        self.assertIn("Erro inesperado", line)
        self.assertIn("studio.log", line)
        self.assertNotIn("Traceback", self.log_text(app))
        self.assertIn("Traceback", self.file_text())
        self.assertIn("in bad", self.file_text())

    def test_thread_exception_vira_linha_no_log(self):
        app = self.make_app()

        def boom():
            raise RuntimeError("falhou na thread")

        t = threading.Thread(target=boom, name="leitor-teste")
        t.start()
        t.join()
        self.assertIn("in boom", self.file_text())
        self.assertIn("leitor-teste", self.file_text())
        app.dispatch_events()
        self.assertIn("RuntimeError: falhou na thread", self.log_text(app))

    def test_subscriber_exception_does_not_stop_others(self):
        app = self.make_app()
        got = []

        def bad(ev):
            raise KeyError("x")

        app.on("ping", bad)
        app.on("ping", got.append)
        app.bus.post("ping")
        app.dispatch_events()
        self.assertEqual(1, len(got))
        self.assertIn("KeyError", self.log_text(app))

    def test_job_fail_traceback_goes_to_file(self):
        app = self.make_app()

        def job():
            raise OSError("disco cheio")

        app.io_jobs.submit("teste", job)
        self.assertTrue(pump_until(app, lambda: "in job" in (self.file_text() if os.path.exists(self.log_path)
                                                            else "")))
        self.assertIn("teste", self.file_text())

    # ---------- tomada ----------

    def test_take_changed_chega_aos_assinantes(self):
        app = self.make_app()
        got = []
        app.on("take_changed", got.append)
        take = new_take("audio", "mic", rec_dir=self.rec_dir)
        app.set_take(take)
        self.assertIs(take, app.take)
        self.assertTrue(pump_until(app, lambda: got))
        self.assertIs(take, got[0].data["take"])

    def test_startup_recovers_and_loads_latest_take(self):
        write_audio_take(self.rec_dir, "2026-09-26_100000", "convertido")
        write_audio_take(self.rec_dir, "2026-09-26_110000", "gravando")
        app = self.make_app()
        self.assertEqual("2026-09-26_110000", app.take.id)
        self.assertEqual("gravado", app.take.status)
        self.assertEqual("gravado", Take.load(app.take.dir).status)
        self.assertIn("Tomada 2026-09-26_110000: gravação interrompida recuperada", self.log_text(app))
        self.assertIn("Última tomada: 2026-09-26_110000", self.log_text(app))

    def test_startup_without_takes(self):
        app = self.make_app()
        self.assertIsNone(app.take)
        self.assertIn("Pasta de gravações", self.log_text(app))

    # ---------- conversor ----------

    def test_load_model_button(self):
        app = self.make_app()
        app.btn_load.invoke()
        self.assertEqual([threading.current_thread().name], app.rvc.start_threads)
        self.assertEqual("disabled", str(app.btn_load.cget("state")))
        self.assertTrue(pump_until(app, lambda: "carregado" in app.model_status.cget("text")))
        self.assertEqual(1, app.rvc.loads)
        self.assertTrue(app.model_loaded)
        self.assertIn("Modelo carregado", self.log_text(app))

    def test_load_model_failure_reenables_button(self):
        app = self.make_app(rvc_factory=lambda: FakeRvc(load_error=RvcError("GPU sem memória")))
        app.btn_load.invoke()
        self.assertTrue(pump_until(app, lambda: "falhou" in app.model_status.cget("text")))
        self.assertIn("GPU sem memória", self.log_text(app))
        self.assertEqual("normal", str(app.btn_load.cget("state")))
        self.assertFalse(app.model_loaded)

    def test_ensure_rvc_restarts_dead_worker(self):
        app = self.make_app()
        rvc = app.ensure_rvc()
        self.assertIs(rvc, app.ensure_rvc())
        self.assertEqual(1, len(rvc.start_threads))
        app.model_loaded = True
        rvc._alive = False                  # worker morreu
        self.assertIs(rvc, app.ensure_rvc())
        self.assertEqual(2, len(rvc.start_threads))
        self.assertIn("Conversor reiniciado", self.log_text(app))
        self.assertFalse(app.model_loaded)
        self.assertIn("não carregado", app.model_status.cget("text"))

    def test_ensure_rvc_refuses_worker_thread(self):
        app = self.make_app()
        errors = []

        def call():
            try:
                app.ensure_rvc()
            except RuntimeError as e:
                errors.append(e)

        t = threading.Thread(target=call)
        t.start()
        t.join()
        self.assertEqual(1, len(errors))
        self.assertIsNone(app.rvc)

    def test_load_model_with_fake_worker_process(self):
        rvc_log = os.path.join(self.tmp.name, "rvc.log")
        app = self.make_app(rvc_factory=lambda: RvcClient(log_path=rvc_log, env={"STUDIO_RVC_FAKE": "1"}))
        app.btn_load.invoke()
        self.assertTrue(pump_until(app, lambda: app.model_loaded, timeout=30))
        self.assertTrue(app.rvc.alive())
        app.on_close()
        self.assertTrue(app.closed)
        self.assertIsNone(app.rvc._proc)     # worker encerrado junto

    # ---------- fechar ----------

    def test_on_close_sem_nada_rodando_fecha(self):
        app = self.make_app()
        rvc = app.ensure_rvc()
        root = app.root
        app.on_close()
        self.assertEqual([], self.confirm_calls)
        self.assertTrue(app.closed)
        self.assertEqual(1, rvc.closed)
        self.assertRaises(tk.TclError, root.winfo_exists)
        for runner in (app.gpu_jobs, app.io_jobs, app.upload_jobs):
            with self.assertRaises(RuntimeError):
                runner.submit("depois", print)
        self.assertIs(self.hook_before, threading.excepthook)

    def test_close_button_is_wired(self):
        app = self.make_app()
        self.assertIn("on_close", app.root.protocol("WM_DELETE_WINDOW"))

    def test_on_close_asks_panels_and_respects_no(self):
        app = self.make_app()
        app.register_closer(lambda: None)
        app.register_closer(lambda: "Envio para o Drive em andamento")
        app.on_close()
        self.assertFalse(app.closed)
        self.assertEqual(1, len(self.confirm_calls))
        self.assertIn("Envio para o Drive em andamento", self.confirm_calls[0][1])
        self.confirm_answer = True
        app.on_close()
        self.assertTrue(app.closed)

    def test_on_close_asks_when_job_running(self):
        app = self.make_app()
        gate, started = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        app.gpu_jobs.submit(gui.JOB_LOAD_MODEL, lambda: (started.set(), gate.wait(5)))
        app.upload_jobs.submit("fila", gate.wait, 5)
        self.assertTrue(started.wait(5))
        app.on_close()
        self.assertFalse(app.closed)
        self.assertIn("Carregando o modelo", self.confirm_calls[0][1])
        # job ainda sem nome conhecido: cai no rotulo da fila
        self.assertIn("envio para o Drive", self.confirm_calls[0][1])

    def test_closer_error_counts_as_reason(self):
        app = self.make_app()

        def broken():
            raise ValueError("x")

        app.register_closer(broken)
        app.on_close()
        self.assertFalse(app.closed)
        self.assertEqual(1, len(self.confirm_calls))

    def test_shutdown_hooks_run_before_destroy(self):
        app = self.make_app()
        seen = []
        app.add_shutdown_hook(lambda: seen.append(app.root.winfo_exists()))

        def broken():
            raise RuntimeError("hook quebrado")

        app.add_shutdown_hook(broken)
        app.on_close()
        self.assertEqual([1], seen)
        self.assertTrue(app.closed)
        self.assertIn("hook quebrado", self.file_text())


class MainTest(unittest.TestCase):
    def test_main_creates_tk_and_app(self):
        with mock.patch.object(gui.tk, "Tk") as tk_cls, mock.patch.object(gui, "App") as app_cls:
            gui.main()
        app_cls.assert_called_once_with(tk_cls.return_value)
        tk_cls.return_value.mainloop.assert_called_once_with()

    def test_entrypoint_is_thin(self):
        import orochi_studio
        self.assertIs(gui.main, orochi_studio.main)
        with open(orochi_studio.__file__, encoding="utf-8") as f:
            self.assertLess(len(f.read().splitlines()), 15)


if __name__ == "__main__":
    unittest.main()
