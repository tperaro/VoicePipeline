"""Janela do Voice Studio (Tk): duas colunas + log, fila de eventos, filas de jobs e o conversor RVC.

Unico modulo que importa tkinter. So a thread principal mexe no estado do App e nos widgets.
"""

import os
import threading
import tkinter as tk
import traceback
from datetime import datetime
from tkinter import messagebox, ttk

from studio import procs
from studio.config import (ESTADO_PATH, LOGS_DIR, MODELOS, REC_DIR, STUDIO_LOG, Modelo, find_latest_checkpoint,
                           get_modelo, load_estado, merge_estado)
from studio.events import Event, EventBus, JobRunner, error_message
from studio.gui_capture import CAPTURE_JOB_LABELS, CapturePanel
from studio.rvc_client import RvcClient
from studio.takes import Take, latest_take, recover_takes

TITLE = "Voice Studio (Orochi / Silvio)"
GEOMETRY = "1100x720"
MIN_SIZE = (980, 640)
LEFT_MIN_W = 500          # preview 480x270 + margens
PUMP_MS = 50
JOB_LOAD_MODEL = "load_model"
# texto PT de cada job na confirmacao ao fechar (as outras tasks acrescentam os seus)
JOB_LABELS = {JOB_LOAD_MODEL: "Carregando o modelo", **CAPTURE_JOB_LABELS}
RUNNER_LABELS = {"gpu": "modelo, conversão ou vídeo", "io": "arquivos", "upload": "envio para o Drive"}
COLOR_BAD, COLOR_BUSY, COLOR_OK = "#a33", "#a80", "#2a2"


class App:
    def __init__(self, root: tk.Tk, rvc_factory=RvcClient, rec_dir: str = REC_DIR,
                 estado_path: str = ESTADO_PATH, log_path: str = STUDIO_LOG, logs_dir: str = LOGS_DIR,
                 ask_confirm=None, capture_factory=None, hardware=None):
        self.root = root
        self.rec_dir = rec_dir
        self.estado_path = estado_path
        self.log_path = log_path
        self.logs_dir = logs_dir
        self._rvc_factory = rvc_factory
        self._ask_confirm = ask_confirm or self._ask_yes_no
        # fabrica do CaptureProcess e acesso ao hardware (None = os de verdade); os testes injetam falsos
        self.capture_factory = capture_factory
        self.hardware = hardware
        self.rvc = None
        self._rvc_started = False
        self.model_loaded = False
        self.take: Take | None = None
        self.closing = False
        self.closed = False
        self._subs: dict[str, list] = {}
        self._closers: list = []
        self._shutdown_hooks: list = []
        self._file_lock = threading.Lock()
        self._models_by_label = {m.label: m for m in MODELOS}

        self.bus = EventBus()
        self.gpu_jobs = JobRunner(self.bus, "gpu")
        self.io_jobs = JobRunner(self.bus, "io")
        self.upload_jobs = JobRunner(self.bus, "upload")
        self.estado, aviso_estado = load_estado(estado_path)

        root.title(TITLE)
        root.geometry(GEOMETRY)
        root.minsize(*MIN_SIZE)
        root.resizable(True, True)
        self._build_layout()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.report_callback_exception = self.report_callback_exception
        self._prev_excepthook = threading.excepthook
        threading.excepthook = self._thread_excepthook
        self.on("log", lambda ev: self.log(ev.data["msg"]))
        self.on("error", lambda ev: self.log(ev.data["message"]))
        self.on("job_ok", self._on_job_ok)
        self.on("job_fail", self._on_job_fail)

        self._build_model_section()
        # os paineis das outras tasks entram aqui, antes da abertura (assim recebem o take_changed inicial)
        self.capture_panel = CapturePanel(self, self.left)
        self._startup(aviso_estado)
        self._pump_id = root.after(PUMP_MS, self._pump)

    # ---------- layout ----------

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=0, minsize=LEFT_MIN_W)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)
        self.left = ttk.Frame(outer)
        self.left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.right = ttk.Frame(outer)
        self.right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        box = ttk.LabelFrame(outer, text="Log")
        box.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        self.log_box = tk.Text(box, height=8, state="disabled", wrap="word")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y", padx=(0, 6), pady=6)
        self.log_box.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)

    def add_section(self, title: str, parent=None) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent if parent is not None else self.right, text=title)
        frame.pack(fill="x", pady=(0, 8))
        return frame

    def _build_model_section(self) -> None:
        f = self.model_frame = self.add_section("1. Modelo")
        row = ttk.Frame(f)
        row.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(row, text="Voz:").pack(side="left")
        self.model_var = tk.StringVar(master=self.root, value=self._initial_model().label)
        self.model_box = ttk.Combobox(row, textvariable=self.model_var, values=[m.label for m in MODELOS],
                                      width=20, state="readonly")
        self.model_box.pack(side="left", padx=6)
        self.model_box.bind("<<ComboboxSelected>>", self.on_model_change)
        self.checkpoint_label = ttk.Label(f, text=self._checkpoint_text())
        self.checkpoint_label.pack(anchor="w", padx=8, pady=(6, 0))
        self.model_status = ttk.Label(f)
        self.model_status.pack(anchor="w", padx=8)
        self._set_model_status("não carregado", COLOR_BAD)
        self.btn_load = ttk.Button(f, text="Carregar modelo", command=self.on_load_model)
        self.btn_load.pack(anchor="w", padx=8, pady=(4, 8))

    # ---------- log e erros ----------

    def log(self, msg: str) -> None:
        # de outra thread vira evento: so a thread principal toca no Tk
        if threading.current_thread() is not threading.main_thread():
            self.bus.post("log", msg=msg)
            return
        now = datetime.now()
        self._append_file(f"[{now:%Y-%m-%d %H:%M:%S}] {msg}\n")
        if self.closed:
            return
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{now:%H:%M:%S}] {msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _append_file(self, text: str) -> None:
        try:
            with self._file_lock, open(self.log_path, "a", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass

    def _write_traceback(self, where: str, tb_text: str) -> None:
        self._append_file(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] ERRO ({where}):\n{tb_text.rstrip()}\n")

    def _error_line(self, message: str) -> str:
        return f"Erro inesperado: {message} (detalhes em {os.path.basename(self.log_path)})"

    def report_error(self, exc: BaseException, where: str = "interface") -> None:
        # linha curta no painel + traceback no studio.log
        self._write_traceback(where, "".join(traceback.format_exception(exc)))
        self.log(self._error_line(error_message(exc)))

    def report_callback_exception(self, exc_type, exc, tb) -> None:
        self._write_traceback("interface", "".join(traceback.format_exception(exc_type, exc, tb)))
        self.log(self._error_line(error_message(exc)))

    def _thread_excepthook(self, args) -> None:
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread is not None else "?"
        self._write_traceback(f"thread {name}", "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
        msg = error_message(args.exc_value) if args.exc_value is not None else args.exc_type.__name__
        self.bus.post("error", message=self._error_line(msg))

    # ---------- eventos ----------

    def on(self, kind: str, fn) -> None:
        # fn(ev: Event) roda na thread principal
        self._subs.setdefault(kind, []).append(fn)

    def dispatch_events(self) -> None:
        for ev in self.bus.drain():
            if self.closed:
                return
            for fn in list(self._subs.get(ev.kind, ())):
                try:
                    fn(ev)
                except Exception as e:
                    self.report_error(e, f"evento {ev.kind}")

    def _pump(self) -> None:
        try:
            self.dispatch_events()
        finally:
            if not self.closed:
                self._pump_id = self.root.after(PUMP_MS, self._pump)

    def _on_job_ok(self, ev: Event) -> None:
        if ev.data["job"] == JOB_LOAD_MODEL:
            self.model_loaded = True
            self._set_model_status("carregado ✓", COLOR_OK)
            self.log("Modelo carregado. Pronto para converter rápido.")

    def _on_job_fail(self, ev: Event) -> None:
        d = ev.data
        if d.get("traceback"):
            self._write_traceback(f"job {d['job']} ({d.get('runner', '?')})", d["traceback"])
        if d["job"] == JOB_LOAD_MODEL:
            self._model_load_failed(d["message"])

    # ---------- estado, modelo e tomada ----------

    def update_estado(self, **changes) -> None:
        # rele o arquivo e grava so estas chaves: o que outro programa gravou com o app aberto (calibrar_av.py
        # --salvar, enviar_drive.py --pasta) nao volta atras; o corrompido vira estado.json.corrompido
        try:
            self.estado, aviso = merge_estado(changes, self.estado_path)
        except OSError as e:
            self.estado.update(changes)
            self.log(f"Não foi possível salvar estado.json: {procs.os_error_message(e)}")
            return
        if aviso:
            self.log(f"AVISO: {aviso}")

    def _initial_model(self) -> Modelo:
        try:
            return get_modelo(self.estado.get("modelo"))
        except ValueError:
            return MODELOS[0]

    def modelo(self) -> Modelo:
        return self._models_by_label.get(self.model_var.get(), MODELOS[0])

    def _checkpoint_text(self) -> str:
        ck = find_latest_checkpoint(self.modelo().key, self.logs_dir)
        return f"Checkpoint: {os.path.basename(ck) if ck else 'NENHUM CHECKPOINT ENCONTRADO'}"

    def _warn_missing_checkpoint(self, m: Modelo) -> None:
        if not find_latest_checkpoint(m.key, self.logs_dir):
            self.log(f"AVISO: nenhum checkpoint encontrado em {os.path.join(self.logs_dir, m.key)}")

    def on_model_change(self, event=None) -> None:
        m = self.modelo()
        self.checkpoint_label.configure(text=self._checkpoint_text())
        self.log(f"Modelo selecionado: {m.label}")
        self._warn_missing_checkpoint(m)
        self.update_estado(modelo=m.key)
        self.bus.post("model_changed", modelo=m)

    def set_take(self, take: Take | None) -> None:
        self.take = take
        self.bus.post("take_changed", take=take)

    def _startup(self, aviso_estado: str | None) -> None:
        self.log(f"Pasta de gravações: {self.rec_dir}")
        if aviso_estado:
            self.log(f"AVISO: {aviso_estado}")
        self._warn_missing_checkpoint(self.modelo())
        try:
            for msg in recover_takes(self.rec_dir):
                self.log(msg)
            # a que falhou ao comecar (camera em uso, duplo clique) nao esconde a ultima boa
            take = latest_take(self.rec_dir, usable_only=True)
        except Exception as e:
            self.report_error(e, "abertura")
            return
        if take is not None:
            self.log(f"Última tomada: {take.id} ({take.status})")
            self.set_take(take)

    # ---------- conversor RVC ----------

    def ensure_rvc(self):
        # thread principal: o PDEATHSIG do worker vale enquanto a thread que fez o spawn viver
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("ensure_rvc() só pode ser chamado na thread principal")
        if self.rvc is None:
            self.rvc = self._rvc_factory()
        if not self.rvc.alive():
            restarting = self._rvc_started
            self.rvc.start()
            self._rvc_started = True
            if restarting:
                self.log("Conversor reiniciado")
                self.model_loaded = False
                self._set_model_status("não carregado", COLOR_BAD)
                self.btn_load.configure(state="normal")
        return self.rvc

    def _set_model_status(self, text: str, color: str) -> None:
        self.model_status.configure(text=f"Status: {text}", foreground=color)

    def on_load_model(self) -> None:
        try:
            rvc = self.ensure_rvc()
        except Exception as e:
            self._write_traceback("iniciar conversor", "".join(traceback.format_exception(e)))
            self._model_load_failed(f"não foi possível iniciar o conversor: {error_message(e)}")
            return
        self.btn_load.configure(state="disabled")
        self._set_model_status("carregando… (pode levar alguns segundos)", COLOR_BUSY)
        self.log("Carregando o modelo (o conversor importa torch/RVC)…")
        self.gpu_jobs.submit(JOB_LOAD_MODEL, rvc.load)

    def _model_load_failed(self, message: str) -> None:
        self.model_loaded = False
        self._set_model_status("falhou ao carregar", COLOR_BAD)
        self.log(f"ERRO ao carregar o modelo: {message}")
        self.btn_load.configure(state="normal")

    # ---------- fechar ----------

    def register_closer(self, fn) -> None:
        # fn() -> None (pode fechar) | str (motivo em PT para pedir confirmacao)
        self._closers.append(fn)

    def add_shutdown_hook(self, fn) -> None:
        # fn() roda ao fechar de verdade, com a janela ainda visivel (ex.: parar a gravacao)
        self._shutdown_hooks.append(fn)

    def close_reasons(self) -> list[str]:
        reasons = []
        for runner in (self.gpu_jobs, self.io_jobs, self.upload_jobs):
            if runner.busy:
                job = runner.current
                reasons.append(JOB_LABELS.get(job) or
                               f"Trabalho em andamento ({RUNNER_LABELS.get(runner.name, runner.name)})")
        for fn in self._closers:
            try:
                reason = fn()
            except Exception as e:
                self._write_traceback("fechar", "".join(traceback.format_exception(e)))
                reason = "Não foi possível conferir se há trabalho em andamento"
            if reason:
                reasons.append(reason)
        return list(dict.fromkeys(reasons))

    def _ask_yes_no(self, title: str, message: str) -> bool:
        return messagebox.askyesno(title, message, icon="warning", parent=self.root)

    def on_close(self) -> None:
        if self.closing:
            return
        self.closing = True
        ok = False
        try:
            reasons = self.close_reasons()
            ok = not reasons or self._ask_confirm(
                "Fechar o Voice Studio?",
                "\n".join(f"• {r}" for r in reasons) + "\n\nFechar mesmo assim? O que está em andamento será interrompido.")
        finally:
            if not ok:
                self.closing = False
        if ok:
            self.shutdown()

    def shutdown(self) -> None:
        if self.closed:
            return
        self.closing = True
        self.log("Fechando…")
        for fn in self._shutdown_hooks:
            try:
                fn()
            except Exception as e:
                self._write_traceback("fechar", "".join(traceback.format_exception(e)))
        try:
            self.root.withdraw()
        except tk.TclError:
            pass
        for runner in (self.gpu_jobs, self.io_jobs, self.upload_jobs):
            runner.close()
        if self.rvc is not None:
            try:
                self.rvc.close()
            except Exception as e:
                self._write_traceback("fechar", "".join(traceback.format_exception(e)))
        self.closed = True
        if threading.excepthook == self._thread_excepthook:
            threading.excepthook = self._prev_excepthook
        try:
            self.root.after_cancel(self._pump_id)
        except (AttributeError, tk.TclError):
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()
