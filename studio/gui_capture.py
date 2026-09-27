"""Painel de captura (coluna esquerda): camera com preview e marca d'agua, microfone e Gravar/Parar.

So a thread principal mexe nos widgets, na Take e nos processos (spawn na thread principal); o que bloqueia
(parar o ffmpeg, conferir o arquivo, alinhar o audio, pactl) roda na fila io do App.
"""

import math
import os
import time
import tkinter as tk
from dataclasses import asdict, dataclass
from tkinter import ttk
from typing import Callable

from PIL import Image, ImageDraw, ImageTk

from studio import audio, capture, devices, timeline
from studio.capture import (ACCEPTED_RC, MIN_FPS, PREVIEW_H, PREVIEW_W, CaptureProcess, Watchdog, build_audio_cmd,
                            build_av_cmd, build_preview_cmd, verify_capture)
from studio.config import MAX_TAKE_S, Modelo
from studio.events import Event, error_message
from studio.takes import Take, new_take
from studio.watermark import make_watermark

JOB_PREVIEW_STOP = "capture_preview_stop"
JOB_STOP = "capture_stop"
JOB_FINISH = "capture_finish"
JOB_MIC = "capture_mic_check"
# texto de cada job na confirmacao ao fechar (o gui.py junta com os seus)
CAPTURE_JOB_LABELS = {JOB_PREVIEW_STOP: "Desligando a câmera", JOB_STOP: "Finalizando a gravação",
                      JOB_FINISH: "Conferindo a gravação", JOB_MIC: "Conferindo o microfone"}

IDLE, STARTING, RECORDING, STOPPING, FINISHING = "idle", "starting", "recording", "stopping", "finishing"
TICK_MS = 66              # consulta do ultimo frame (spec 10)
WATCH_MS = 500            # falha rapida, watchdog de frames e agenda da checagem do mic
MIC_CHECK_S = 2.0         # a 1a checagem sai 2 s depois do inicio (o pactl leva ~1,2 s para ver o gravador)
MIC_SWAPS_TO_STOP = 2     # "trocado" em 2 checagens seguidas para a gravacao ("desconhecido" nao conta nem zera)
PREVIEW_IDLE_S = 300      # preview sem uso por 5 min: desliga (Meet/Zoom nao ficam bloqueados)
REC_AUDIO_S = 0.5         # so-audio nao tem frame: o REC acende quando passa da falha rapida
MIN_REC_S = 1.0           # o Parar so libera 1 s depois do inicio (e, no A/V, depois do 1o frame): duplo clique
SHUTDOWN_WAIT_S = 5.0
MUTE_DB = -45.0
PREVIEW_LOG = "preview.log"
TXT_RECORD, TXT_STOP = "● Gravar", "■ Parar"
COLOR_REC_ON, COLOR_REC_OFF, COLOR_WARN, COLOR_TEXT = "#d11", "#bbb", "#a80", "#333"
GUIDE_RGBA = (255, 255, 255, 70)
IDLE_RGBA = (17, 17, 17, 255)
MSG_NO_MIC = capture.MSG_NO_MIC
MSG_NO_CAMERA = "Nenhuma câmera encontrada — desmarque \"Gravar vídeo\" para gravar só o áudio"
MSG_BAD_DURATION = "Duração inválida: use segundos (ex.: 30) ou deixe vazio"
MSG_LIMIT = capture.limit_message()


@dataclass(frozen=True)
class Hardware:
    # o que o painel consulta no sistema; os testes injetam falsos
    list_cameras: Callable[[], list[str]] = devices.list_cameras
    list_mics: Callable[[], list[devices.Source]] = devices.list_mics
    free_bytes: Callable[[str], int] = devices.free_bytes
    check_mic: Callable[[int, int], str | None] = capture.check_mic   # so o texto; o painel usa o mic_status
    mic_status: Callable[[int, int], str] = capture.mic_status        # MIC_OK | MIC_SWAPPED | MIC_UNKNOWN


def camera_label(path: str) -> str:
    # ".../usb-A4tech_FHD_720P_PC_Camera-video-index0" -> "A4tech_FHD_720P_PC_Camera"
    return os.path.basename(path).removesuffix("-video-index0").removeprefix("usb-")


def parse_duration(text: str) -> float | None:
    # vazio = ate clicar Parar; aceita virgula
    text = text.strip().replace(",", ".")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise ValueError(MSG_BAD_DURATION) from None
    if not (math.isfinite(value) and value > 0):
        raise ValueError(MSG_BAD_DURATION)
    return value


def _dec(x: float, fmt: str = ".1f") -> str:
    return format(x, fmt).replace(".", ",")


def make_overlay(modelo: Modelo) -> Image.Image:
    # marca d'agua do modelo em 480x270 por cima das guias tenues da coluna 9:16
    img = Image.new("RGBA", (PREVIEW_W, PREVIEW_H), (0, 0, 0, 0))
    col = round(PREVIEW_H * 9 / 16)
    x0 = (PREVIEW_W - col) // 2
    d = ImageDraw.Draw(img)
    for x in (x0, x0 + col - 1):
        d.line((x, 0, x, PREVIEW_H - 1), fill=GUIDE_RGBA)
    return Image.alpha_composite(img, make_watermark(PREVIEW_W, PREVIEW_H, modelo))


def compose(frame: bytes | None, overlay: Image.Image) -> Image.Image:
    if frame is None:
        base = Image.new("RGBA", (PREVIEW_W, PREVIEW_H), IDLE_RGBA)
    else:
        base = Image.frombuffer("RGB", (PREVIEW_W, PREVIEW_H), frame, "raw", "RGB", 0, 1).convert("RGBA")
    return Image.alpha_composite(base, overlay)


def mic_check_job(check, pid: int, expected_index: int) -> tuple[int, str | None]:
    # thread de trabalho: so consulta o pactl
    return pid, check(pid, expected_index)


def finish_capture(raw: str, need_video: bool, audio_out: str) -> dict:
    # thread de trabalho: confere o arquivo, alinha o audio (A/V) e mede o volume; nao toca na Take
    erro = verify_capture(raw, need_video)
    if erro:
        return {"erro": erro}
    out = {"erro": None, "video": {}, "audio_fit": {}}
    if need_video:
        vi, fit = timeline.extract_aligned_audio(raw, audio_out)
        out["video"], out["audio_fit"] = asdict(vi), asdict(fit)
    out["volume"] = audio.volumedetect(audio_out)
    return out


def gaps_warning(take: Take) -> str | None:
    # spec 6.3.3: com buraco no audio o alinhamento usa o modo assincrono (aresample=async)
    gaps = (take.audio_fit or {}).get("gaps") or 0
    if gaps <= 0:
        return None
    return (f"AVISO: o áudio da tomada {take.id} teve {gaps} buraco(s) acima de {timeline.GAP_S * 1000:g} ms; "
            "o alinhamento usou o modo assíncrono — confira a sincronia")


class CapturePanel:
    def __init__(self, app, parent):
        self.app = app
        self.root = app.root
        self.hw: Hardware = app.hardware or Hardware()
        self._factory = app.capture_factory or CaptureProcess
        self.watchdog = Watchdog()
        self.state = IDLE
        self.preview: CaptureProcess | None = None
        self.rec: CaptureProcess | None = None
        self.take: Take | None = None           # tomada sendo gravada ou conferida
        self._plan: dict = {}
        self._stops_pending = 0                 # previews parando na fila io (a camera ainda esta presa)
        self._hidden = False
        self._last_activity = time.monotonic()
        self._rec_started = 0.0
        self._rec_elapsed = 0.0
        self._stop_ready = False                # Parar liberado (MIN_REC_S e, no A/V, o 1o frame)
        self._next_mic_check = 0.0
        self._mic_pending = False
        self._mic_swaps = 0                     # "trocado" seguidos
        self._mic_unknown = False               # o aviso do "desconhecido" ja saiu nesta sequencia
        self._failure: str | None = None
        self._fps_warned = False
        self._painted: tuple = (None, None)
        self._fps_shown: tuple = ()
        self._timers: dict[str, str] = {}
        self._cams: dict[str, str] = {}
        self._overlay = make_overlay(app.modelo())
        self._build(parent)
        self._init_devices()

        app.on("model_changed", self._on_model_changed)
        app.on("job_ok", self._on_job)
        app.on("job_fail", self._on_job)
        app.register_closer(self._close_reason)
        app.add_shutdown_hook(self._on_shutdown)
        self.root.bind("<Unmap>", self._on_unmap, add="+")
        self.root.bind("<Map>", self._on_map, add="+")
        self.root.bind_all("<ButtonPress>", self._touch, add="+")
        self.root.bind_all("<KeyPress>", self._touch, add="+")
        self._schedule("tick", TICK_MS, self._tick)
        self._schedule("watch", WATCH_MS, self._watch)

    # ---------- layout ----------

    def _build(self, parent) -> None:
        root = self.root
        f = self.frame = self.app.add_section("Gravação", parent)
        row = ttk.Frame(f)
        row.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(row, text="Câmera:").pack(side="left")
        self.camera_var = tk.StringVar(master=root)
        self.camera_box = ttk.Combobox(row, textvariable=self.camera_var, width=30, state="readonly",
                                       postcommand=self.refresh_devices)
        self.camera_box.pack(side="left", padx=6)
        self.camera_box.bind("<<ComboboxSelected>>", self._on_camera_selected)
        self.camera_on = tk.BooleanVar(master=root, value=False)
        self.cam_check = ttk.Checkbutton(row, text="Câmera ligada", variable=self.camera_on,
                                         command=self._on_camera_toggle)
        self.cam_check.pack(side="left")

        self.canvas = tk.Canvas(f, width=PREVIEW_W, height=PREVIEW_H, bg="#111", highlightthickness=0)
        self.canvas.pack(padx=8, pady=6)
        # um PhotoImage so, reaproveitado com paste() a cada frame novo
        self.photo = ImageTk.PhotoImage("RGB", (PREVIEW_W, PREVIEW_H), master=root)
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self._hint = self.canvas.create_text(PREVIEW_W // 2, PREVIEW_H // 2 - 30, fill="#ddd", text="")

        row = ttk.Frame(f)
        row.pack(fill="x", padx=8)
        self.rec_label = ttk.Label(row, text="● REC", foreground=COLOR_REC_OFF)
        self.rec_label.pack(side="left")
        self.fps_label = ttk.Label(row, text="fps: —", foreground=COLOR_TEXT)
        self.fps_label.pack(side="left", padx=12)
        self.time_label = ttk.Label(row, text="")
        self.time_label.pack(side="right")

        row = ttk.Frame(f)
        row.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(row, text="Microfone:").pack(side="left")
        self.mic_var = tk.StringVar(master=root)
        self.mic_box = ttk.Combobox(row, textvariable=self.mic_var, width=40, state="readonly",
                                    postcommand=self.refresh_devices)
        self.mic_box.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.mic_box.bind("<<ComboboxSelected>>", self._on_mic_selected)

        row = ttk.Frame(f)
        row.pack(fill="x", padx=8, pady=6)
        self.video_on = tk.BooleanVar(master=root, value=bool(self.app.estado.get("gravar_video", True)))
        self.video_check = ttk.Checkbutton(row, text="Gravar vídeo", variable=self.video_on,
                                           command=self._on_video_toggle)
        self.video_check.pack(side="left")
        ttk.Label(row, text="Duração (s):").pack(side="left", padx=(16, 0))
        self.duration_var = tk.StringVar(master=root)
        self.duration_entry = ttk.Entry(row, textvariable=self.duration_var, width=6)
        self.duration_entry.pack(side="left", padx=6)
        ttk.Label(row, text="vazio = até clicar Parar").pack(side="left")

        row = ttk.Frame(f)
        row.pack(fill="x", padx=8, pady=(0, 8))
        self.btn_record = ttk.Button(row, text=TXT_RECORD, command=self.on_record)
        self.btn_record.pack(side="left")
        self.status_label = ttk.Label(row, text="Parado.")
        self.status_label.pack(side="left", padx=10)

    # ---------- dispositivos ----------

    def _init_devices(self) -> None:
        saved_mic, saved_cam = self.app.estado.get("mic") or "", self.app.estado.get("camera") or ""
        self.mic_var.set(saved_mic)
        if saved_cam:
            self.camera_var.set(camera_label(saved_cam))
            self._cams = {camera_label(saved_cam): saved_cam}
        self.refresh_devices()
        mics = list(self.mic_box.cget("values"))
        if not mics:
            self.app.log("AVISO: nenhum microfone encontrado (pactl)")
        elif saved_mic and saved_mic not in mics:
            self.app.log(f"AVISO: o microfone salvo não está conectado ({saved_mic}); usando {mics[0]}")
            self.mic_var.set(mics[0])
        if saved_cam and self.camera_path() != saved_cam:
            self.app.log(f"AVISO: a câmera salva não está conectada ({saved_cam})")

    def refresh_devices(self) -> None:
        # a escolha atual fica mesmo se sumiu: gravar avisa em vez de trocar de microfone sem avisar
        names = [s.name for s in self.hw.list_mics()]
        self.mic_box.configure(values=names)
        if not self.mic_var.get() and names:
            self.mic_var.set(names[0])
        current = self.camera_path()
        paths = list(self.hw.list_cameras())
        self._cams = {camera_label(p): p for p in paths}
        self.camera_box.configure(values=list(self._cams))
        if current not in paths:
            self.camera_var.set(camera_label(paths[0]) if paths else "")

    def camera_path(self) -> str:
        return self._cams.get(self.camera_var.get(), "")

    def _on_mic_selected(self, event=None) -> None:
        self.app.update_estado(mic=self.mic_var.get())

    def _on_camera_selected(self, event=None) -> None:
        self.app.update_estado(camera=self.camera_path())
        if self.preview is not None:
            self._stop_preview()          # a nova liga quando a antiga soltar o dispositivo
            self._maybe_start_preview()

    def _on_camera_toggle(self) -> None:
        self._touch()
        if self.camera_on.get():
            self._maybe_start_preview()
        else:
            self._stop_preview()

    def _on_video_toggle(self) -> None:
        self.app.update_estado(gravar_video=bool(self.video_on.get()))
        if self.video_on.get():
            self._maybe_start_preview()
        else:
            self._stop_preview()

    # ---------- preview (PreviewOnly) ----------

    def _maybe_start_preview(self) -> None:
        if (self.preview is not None or self._stops_pending or self._hidden or self.app.closing
                or self.state not in (IDLE, FINISHING) or not (self.camera_on.get() and self.video_on.get())):
            return
        cam = self.camera_path()
        if not cam or not os.path.exists(cam):
            self.app.log("Câmera não encontrada — confira o cabo e escolha a câmera na lista")
            self.camera_on.set(False)
            return
        try:
            os.makedirs(self.app.rec_dir, exist_ok=True)
            cap = self._factory(build_preview_cmd(cam), os.path.join(self.app.rec_dir, PREVIEW_LOG), True)
            cap.start()           # thread principal: o pdeathsig vale enquanto ela viver
        except Exception as e:
            self.app.report_error(e, "ligar a câmera")
            self.camera_on.set(False)
            return
        self.preview = cap
        self._last_activity = time.monotonic()

    def _stop_preview(self) -> None:
        cap, self.preview = self.preview, None
        if cap is None:
            return
        cap.request_stop()
        self._stops_pending += 1
        if not self._submit(JOB_PREVIEW_STOP, cap.wait_stopped):
            self._stops_pending -= 1

    def _preview_failed(self, msg: str) -> None:
        self.app.log(f"Câmera: {msg}")
        self.camera_on.set(False)
        self._stop_preview()

    def _on_unmap(self, event) -> None:
        # o bind do toplevel tambem recebe o Unmap dos filhos
        if event.widget is not self.root:
            return
        self._hidden = True
        self._stop_preview()

    def _on_map(self, event) -> None:
        if event.widget is not self.root:
            return
        self._hidden = False
        self._touch()
        self._maybe_start_preview()

    def _touch(self, event=None) -> None:
        self._last_activity = time.monotonic()

    # ---------- gravar ----------

    def on_record(self) -> None:
        if self.state == RECORDING:
            if self._stop_ready:              # o botao fica desabilitado ate la; isto e so a garantia
                self.stop_recording()
            return
        if self.state != IDLE or self.app.closing:
            return
        self._touch()
        plan, err = self._prepare()
        if err:
            self.app.log(f"Não foi possível gravar: {err}")
            self._set_status(err)
            return
        self.app.update_estado(mic=plan["mic"], camera=self.camera_path(), gravar_video=plan["av"])
        try:
            take = new_take("av" if plan["av"] else "audio", plan["mic"], plan["cam"], rec_dir=self.app.rec_dir)
        except OSError as e:
            self.app.report_error(e, "criar a tomada")
            return
        self.take, self._plan, self.state = take, plan, STARTING
        self._set_status("Preparando…")
        self._refresh_controls()
        self._stop_preview()
        if not self._stops_pending:
            self._launch()        # senao, o fim do JOB_PREVIEW_STOP chama o _launch

    def _prepare(self) -> tuple[dict | None, str | None]:
        # checagens antes de criar a tomada (spec 5.4.1)
        try:
            duration = parse_duration(self.duration_var.get())
        except ValueError as e:
            return None, str(e)
        if duration is not None and duration < MIN_REC_S:
            return None, f"{capture.MSG_SHORT} (mínimo {_dec(MIN_REC_S, 'g')} s)"
        av = bool(self.video_on.get())
        mic = self.mic_var.get()
        if not mic:
            return None, MSG_NO_MIC
        cam = self.camera_path() if av else ""
        if av and not cam:
            return None, MSG_NO_CAMERA
        # microfone conhecido, camera existe e espaco livre: fonte unica com o preflight de capture.py
        index, err = capture.preflight(mic, cam, self.app.rec_dir, list_mics=self.hw.list_mics,
                                       free_bytes=self.hw.free_bytes)
        if err:
            return None, err
        return {"av": av, "mic": mic, "mic_index": index, "cam": cam, "duration": duration}, None

    def _launch(self) -> None:
        take, plan = self.take, self._plan
        if plan["av"]:
            argv = build_av_cmd(plan["mic"], plan["cam"], take.raw_path)
        else:
            argv = build_audio_cmd(plan["mic"], take.raw_path)
        try:
            cap = self._factory(argv, take.path("ffmpeg.log"), plan["av"])
            cap.start()           # thread principal (spec 5.4.2)
        except Exception as e:
            self.app.report_error(e, "iniciar a gravação")
            self._fail_take(take, f"Não foi possível iniciar a gravação: {error_message(e)}")
            self._back_to_idle()
            return
        self.rec = cap
        self.state = RECORDING
        self._stop_ready = False              # o tick libera o Parar (MIN_REC_S e, no A/V, o 1o frame)
        self._rec_started = time.monotonic()
        self._next_mic_check = self._rec_started + MIC_CHECK_S
        self._mic_pending = False
        self._mic_swaps, self._mic_unknown = 0, False
        self._fps_warned = False
        self._schedule_duration(plan["duration"])
        self._set_status("Gravando…")
        self._refresh_controls()
        what = "vídeo + áudio" if plan["av"] else "só o áudio"
        self.app.log(f"Gravando {what} → {take.id} (fale agora!)")
        self.app.bus.post("recording", active=True, take=take)

    def _schedule_duration(self, duration: float | None) -> None:
        # nunca -t no gravador: a duracao e um after que dispara a parada normal
        if duration is not None and duration <= MAX_TAKE_S:
            ms, msg = duration * 1000, f"Duração de {_dec(duration, 'g')} s atingida — parando a gravação"
        else:
            if duration is not None:
                self.app.log(f"AVISO: a duração passa do limite; a gravação para em {MAX_TAKE_S // 60} min")
            ms, msg = MAX_TAKE_S * 1000, f"AVISO: {MSG_LIMIT}"
        self._schedule("duration", ms, lambda: self.stop_recording(msg))

    def stop_recording(self, reason: str | None = None, failure: str | None = None) -> None:
        # thread principal: pede o q e espera na fila io
        if self.state != RECORDING:
            return
        self.state = STOPPING
        self._cancel("duration")
        self._rec_elapsed = time.monotonic() - self._rec_started
        self._failure = failure
        if reason:
            self.app.log(reason)
        cap = self.rec
        cap.request_stop()
        self._set_rec_lit(False)
        self._set_status("Finalizando a gravação…")
        self._refresh_controls()
        self._submit(JOB_STOP, cap.wait_stopped)

    # ---------- checagens periodicas ----------

    def _watch(self) -> None:
        try:
            if self.preview is not None:
                msg = self.preview.early_failure()
                if msg:
                    self._preview_failed(msg)
            if self.state == RECORDING:
                self._watch_recording(time.monotonic())
        finally:
            if not self.app.closed:
                self._schedule("watch", WATCH_MS, self._watch)

    def _watch_recording(self, now: float) -> None:
        cap = self.rec
        msg = cap.early_failure()
        if msg:
            self.stop_recording(f"ERRO: {msg}", failure=msg)
            return
        if self._plan["av"]:
            msg = self.watchdog.check_frames(now, cap.last_frame_monotonic, cap.started_monotonic)
            if msg:
                self.stop_recording(f"AVISO: {msg} — parando a gravação")
                return
        if not self._mic_pending and now >= self._next_mic_check:
            self._next_mic_check = now + MIC_CHECK_S
            self._mic_pending = self._submit(JOB_MIC, mic_check_job, self.hw.mic_status, cap.pid,
                                             self._plan["mic_index"])

    def _tick(self) -> None:
        try:
            self._refresh_view(time.monotonic())
        finally:
            if not self.app.closed:
                self._schedule("tick", TICK_MS, self._tick)

    def _refresh_view(self, now: float) -> None:
        cap = self.rec if (self.rec is not None and self._plan.get("av")) else self.preview
        seq, frame = cap.latest_frame() if cap is not None else (0, None)
        if frame is None:
            self._show_hint(self._hint_text())
        elif self._painted != (cap, seq):
            self.photo.paste(compose(frame, self._overlay))
            self.canvas.itemconfigure(self._hint, state="hidden")
            self._painted = (cap, seq)
        self._refresh_fps(cap, now)
        if self.state == RECORDING:
            self.time_label.configure(text=f"{_dec(now - self._rec_started)} s")
            lit = seq > 0 if self._plan["av"] else now - self._rec_started >= REC_AUDIO_S
            if lit and str(self.rec_label.cget("foreground")) != COLOR_REC_ON:
                self._set_rec_lit(True)
            if not self._stop_ready and now - self._rec_started >= MIN_REC_S and (seq > 0 or not self._plan["av"]):
                self._stop_ready = True           # duplo clique em Gravar nao para a gravacao
                self._refresh_controls()
        if self.preview is not None and now - self._last_activity > PREVIEW_IDLE_S:
            self.app.log(f"Câmera desligada após {PREVIEW_IDLE_S / 60:g} min sem uso "
                         "(marque \"Câmera ligada\" para voltar)")
            self.camera_on.set(False)
            self._stop_preview()

    def _refresh_fps(self, cap, now: float) -> None:
        fps = cap.fps_measured() if cap is not None else None
        warn = self.watchdog.check_fps(fps, now, cap.started_monotonic) if fps is not None else None
        shown = (warn or ("fps: —" if fps is None else f"Câmera: {_dec(fps)} fps"), COLOR_WARN if warn else COLOR_TEXT)
        if shown != self._fps_shown:
            self.fps_label.configure(text=shown[0], foreground=shown[1])
            self._fps_shown = shown
        if warn and self.state == RECORDING and not self._fps_warned:
            self._fps_warned = True
            self.app.log(f"AVISO: {warn}")

    def _hint_text(self) -> str:
        if self.state in (RECORDING, STOPPING) and not self._plan.get("av"):
            return "Gravando só o áudio"
        if not self.video_on.get():
            return "Só áudio (\"Gravar vídeo\" desmarcado)"
        if not self.camera_on.get() and self.rec is None:
            return "Câmera desligada"
        return "Aguardando a imagem da câmera…"

    def _show_hint(self, text: str) -> None:
        if self._painted == ("hint", text):
            return
        self.photo.paste(compose(None, self._overlay))
        self.canvas.itemconfigure(self._hint, text=text, state="normal")
        self._painted = ("hint", text)

    def _on_model_changed(self, ev: Event) -> None:
        self._overlay = make_overlay(ev.data["modelo"])
        self._painted = (None, None)          # o proximo tick repinta com a marca nova

    # ---------- fim da gravacao (eventos da fila io) ----------

    def _on_job(self, ev: Event) -> None:
        job = ev.data["job"]
        if job == JOB_PREVIEW_STOP:
            self._preview_stopped(ev)
        elif job == JOB_STOP:
            self._recorder_stopped(ev)
        elif job == JOB_FINISH:
            self._finished(ev)
        elif job == JOB_MIC:
            self._mic_checked(ev)

    def _preview_stopped(self, ev: Event) -> None:
        self._stops_pending -= 1
        if ev.kind == "job_fail":
            self.app.log(f"AVISO: a câmera não desligou direito: {ev.data['message']}")
        if self._stops_pending:
            return
        if self.state == STARTING:
            self._launch()
        else:
            self._maybe_start_preview()

    def _recorder_stopped(self, ev: Event) -> None:
        self.rec = None
        rc = ev.data.get("result")
        if ev.kind == "job_fail":
            self.app.log(f"AVISO: o gravador não parou direito: {ev.data['message']}")
        elif rc not in ACCEPTED_RC and not self._failure:
            self.app.log(f"AVISO: o gravador saiu com código {rc}")
        self.state = FINISHING
        self._set_status("Conferindo a gravação…")
        take = self.take
        if not self._submit(JOB_FINISH, finish_capture, take.raw_path, take.modo == "av", take.audio_path):
            return
        self._maybe_start_preview()           # a camera ja esta livre

    def _finished(self, ev: Event) -> None:
        take = self.take
        try:
            res = ev.data["result"] if ev.kind == "job_ok" else {"erro": ev.data["message"]}
            if res["erro"]:
                self._fail_take(take, self._failure or res["erro"])
            else:
                self._take_recorded(take, res)
        finally:
            self._back_to_idle()

    def _take_recorded(self, take: Take, res: dict) -> None:
        # so a thread principal altera o take.json (spec 3.2)
        take.video, take.audio_fit = res["video"], res["audio_fit"]
        take.status, take.erro = "gravado", ""
        take.save()
        fps = take.video.get("fps_medido")
        extra = f", vídeo a {_dec(fps)} fps" if fps else ""
        self.app.log(f"Gravação concluída: {take.id} ({_dec(self._rec_elapsed)} s{extra})")
        if self._failure:
            self.app.log("AVISO: a gravação parou antes da hora; o que foi gravado até ali foi mantido")
        if fps and fps < MIN_FPS:
            self.app.log(f"AVISO: a câmera gravou a {_dec(fps)} fps (abaixo de {MIN_FPS:g}) — pouca luz?")
        warn = gaps_warning(take)
        if warn:
            self.app.log(warn)
        mean, peak = res["volume"]
        if peak is not None:
            self.app.log(f"Volume da gravação: média {_dec(mean or 0)} dB, pico {_dec(peak)} dB")
            if peak < MUTE_DB:
                self.app.log(f"AVISO: Microfone mudo? O pico ficou em {_dec(peak)} dB — confira o microfone escolhido")
        self._set_status(f"Gravado: {take.id}")
        self.app.set_take(take)

    def _fail_take(self, take: Take, erro: str) -> None:
        take.status, take.erro = "falhou", erro
        try:
            take.save()
        except OSError as e:
            self.app.report_error(e, "salvar take.json")
        self.app.log(f"ERRO na gravação {take.id}: {erro}")
        self._set_status(f"Falhou: {erro}")

    def _back_to_idle(self) -> None:
        take = self.take
        self.state, self.take, self._plan, self._failure = IDLE, None, {}, None
        self.rec = None
        self._set_rec_lit(False)
        self.time_label.configure(text="")
        self._refresh_controls()
        self._last_activity = time.monotonic()
        self.app.bus.post("recording", active=False, take=take)
        self._maybe_start_preview()

    def _mic_checked(self, ev: Event) -> None:
        # "desconhecido" (pactl falhou/estourou o timeout) so avisa; "trocado" para no 2o seguido; "ok" zera
        self._mic_pending = False
        if ev.kind != "job_ok":
            return
        pid, status = ev.data["result"]
        if self.state != RECORDING or self.rec is None or self.rec.pid != pid:
            return
        if status == capture.MIC_UNKNOWN:
            if not self._mic_unknown:
                self._mic_unknown = True
                self.app.log(f"AVISO: {capture.MSG_MIC_UNKNOWN}")
            return
        self._mic_unknown = False
        if status != capture.MIC_SWAPPED:
            self._mic_swaps = 0
            return
        self._mic_swaps += 1
        if self._mic_swaps >= MIC_SWAPS_TO_STOP:
            self.stop_recording(f"AVISO: {capture.MSG_MIC} — parando a gravação")

    # ---------- fechar ----------

    def _close_reason(self) -> str | None:
        if self.state in (STARTING, RECORDING):
            return "Gravação em andamento (ela será finalizada antes de fechar)"
        return None

    def _on_shutdown(self) -> None:
        # roda com a janela ainda visivel; a tomada fica "gravando" e a abertura seguinte a recupera
        for name in list(self._timers):
            self._cancel(name)
        if self.state == RECORDING and self.rec is not None:
            self._set_status("Finalizando gravação…")
            self.app.log("Finalizando gravação…")
            try:
                self.root.update_idletasks()
            except tk.TclError:
                pass
            self.rec.request_stop()
            rc = self.rec.wait_stopped(t_q=SHUTDOWN_WAIT_S, t_close=1, t_term=1)
            self.app.log(f"Gravação {self.take.id} parada ao fechar (código {rc}); "
                         "ela será conferida na próxima abertura")
        elif self.state == STARTING and self.take is not None:
            self._fail_take(self.take, "Gravação cancelada: o app foi fechado antes de começar")
        cap, self.preview = self.preview, None
        if cap is not None:
            cap.request_stop()
            cap.wait_stopped(t_q=1, t_close=0.5, t_term=0.5)

    # ---------- utilitarios ----------

    def _submit(self, job: str, fn, *args) -> bool:
        try:
            self.app.io_jobs.submit(job, fn, *args)
            return True
        except RuntimeError:          # fila fechada: o app esta fechando
            return False

    def _schedule(self, name: str, ms: float, fn) -> None:
        self._cancel(name)
        self._timers[name] = self.root.after(max(1, round(ms)), fn)

    def _cancel(self, name: str) -> None:
        timer = self._timers.pop(name, None)
        if timer is not None:
            try:
                self.root.after_cancel(timer)
            except tk.TclError:
                pass

    def _set_status(self, text: str) -> None:
        self.status_label.configure(text=text)

    def _set_rec_lit(self, on: bool) -> None:
        self.rec_label.configure(foreground=COLOR_REC_ON if on else COLOR_REC_OFF)

    def _refresh_controls(self) -> None:
        idle = self.state == IDLE
        for box in (self.mic_box, self.camera_box):
            box.configure(state="readonly" if idle else "disabled")
        for w in (self.video_check, self.duration_entry):
            w.configure(state="normal" if idle else "disabled")
        if self.state == RECORDING:
            self.btn_record.configure(text=TXT_STOP, state="normal" if self._stop_ready else "disabled")
        else:
            self.btn_record.configure(text=TXT_RECORD, state="normal" if idle else "disabled")
