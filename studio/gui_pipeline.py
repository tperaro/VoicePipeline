"""Coluna direita, etapas 2 a 5: aumentar volume, converter (RVC), gerar o video e enviar pro Drive.

So a thread principal mexe nos widgets e na Take (spec 3.2): os jobs recebem copias das entradas e devolvem
resultados, e o handler do job_ok aplica o resultado na Take e grava o take.json.
"""

import copy
import math
import os
import subprocess
import threading
import tkinter as tk
from dataclasses import asdict, dataclass
from datetime import datetime
from tkinter import simpledialog, ttk
from typing import Callable

from studio import audio, drive, procs, render, timeline
from studio.config import VIDEOS_DIR, Modelo, load_estado
from studio.events import Event, error_message
from studio.gui_capture import gaps_warning
from studio.takes import Take

JOB_EXTRACT = "pipeline_extract"      # fila io (curto)
JOB_BOOST = "pipeline_boost"          # fila io (curto)
JOB_CONVERT = "pipeline_convert"      # fila gpu
JOB_RENDER = "pipeline_render"        # fila gpu
JOB_UPLOAD = "pipeline_upload"        # fila upload
JOB_RECONNECT = "pipeline_reconnect"  # fila upload
LABEL_RENDER = "Gerando o vídeo"
LABEL_UPLOAD = "Envio para o Drive em andamento"
# texto de cada job na confirmacao ao fechar (o gui.py junta com os seus)
PIPELINE_JOB_LABELS = {JOB_EXTRACT: "Preparando o áudio da tomada", JOB_BOOST: "Aumentando o volume",
                       JOB_CONVERT: "Convertendo a voz", JOB_RENDER: LABEL_RENDER, JOB_UPLOAD: LABEL_UPLOAD,
                       JOB_RECONNECT: "Reconectando o Drive"}
BUSY_TEXT = {JOB_EXTRACT: "Preparando o áudio da tomada…", JOB_BOOST: "Aumentando o volume…",
             JOB_CONVERT: "Convertendo para {label}… (a 1ª vez carrega o modelo)",
             JOB_RENDER: "Gerando o vídeo…"}
EV_PROGRESS = "drive_progress"        # {nome, fracao}, postado pela thread do envio

DEFAULT_GAIN_DB = 15
MIN_GAIN_DB, MAX_GAIN_DB = 1, 40
RECONNECT_TIMEOUT_S = 300
BOOSTED = "boosted.wav"
FFPLAY = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error"]
OPENER = "xdg-open"
MIN_WRAP = 120
COLOR_BAD, COLOR_BUSY, COLOR_OK, COLOR_TEXT = "#a33", "#a80", "#2a2", "#333"
TITLE_DRIVE = "Configurar Drive"
MSG_ASK_LINK = "Cole o link da pasta do Google Drive compartilhada com você (Compartilhar → Copiar link):"
MSG_WORKER_DIED = "Conversor reiniciado — clique Converter de novo"
MSG_BAD_GAIN = f"Ganho inválido: use de {MIN_GAIN_DB} a {MAX_GAIN_DB} dB (ex.: {DEFAULT_GAIN_DB})"
MSG_RECONNECT_TIMEOUT = "O login do Drive não terminou em 5 min — clique Reconectar de novo"
MSG_NO_WAV = "Áudio convertido não encontrado — clique Converter de novo"


def parse_gain(text: str) -> float:
    try:
        gain = float(text.strip().replace(",", "."))
    except ValueError:
        raise ValueError(MSG_BAD_GAIN) from None
    if not (math.isfinite(gain) and MIN_GAIN_DB <= gain <= MAX_GAIN_DB):
        raise ValueError(MSG_BAD_GAIN)
    return gain


def _num(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def _db(x: float | None) -> str:
    return "—" if x is None else f"{x:.1f} dB".replace(".", ",")


# ---------- jobs (threads de trabalho: nao tocam no Tk nem na Take) ----------

def reconnect_drive(run=procs.run, timeout: float = RECONNECT_TIMEOUT_S) -> None:
    # refaz o login do remote (abre o navegador); spec 9.3
    if drive.rclone_bin() is None:
        raise drive.DriveError(drive.MSG_NO_RCLONE)
    try:
        r = run(drive.reconnect_cmd(), timeout=timeout)
    except subprocess.TimeoutExpired:
        raise drive.DriveError(MSG_RECONNECT_TIMEOUT) from None
    if r.returncode != 0:
        raise drive.DriveError(drive.classify_error(r.returncode, r.stderr or ""))


def boost_job(boost, detect, inp: str, out: str, gain_db: float) -> tuple[float | None, float | None]:
    boost(inp, out, gain_db)
    return detect(out)


def convert_job(rvc, export_mp3, inp: str, wav: str, mp3: str, modelo: Modelo) -> dict:
    info = rvc.convert(inp, wav, modelo.key)
    export_mp3(wav, mp3, modelo)
    return info


def upload_job(upload_files, bus, mp4: str, link: str, videos_dir: str, cancel: threading.Event) -> dict:
    def progress(nome: str, fracao: float) -> None:
        bus.post(EV_PROGRESS, nome=nome, fracao=fracao)

    results = upload_files([mp4], link, on_progress=progress, cancel=cancel, videos_dir=videos_dir)
    if not results:           # cancelado antes de comecar
        raise drive.DriveError(drive.MSG_CANCELLED)
    return results[0]


@dataclass(frozen=True)
class PipelineDeps:
    # o que o painel chama fora do processo da GUI; os testes injetam falsos (sem GPU, Drive nem som)
    boost_volume: Callable = audio.boost_volume
    volumedetect: Callable = audio.volumedetect
    export_mp3: Callable = audio.export_mp3
    extract_aligned_audio: Callable = timeline.extract_aligned_audio
    render_final: Callable = render.render_final
    upload_files: Callable = drive.upload_files
    reconnect: Callable[[], None] = reconnect_drive
    spawn: Callable = procs.spawn
    ask_link: Callable | None = None      # (titulo, texto, inicial) -> str | None; None = simpledialog
    videos_dir: str = VIDEOS_DIR


class PipelinePanel:
    def __init__(self, app, parent):
        self.app = app
        self.root = app.root
        self.deps: PipelineDeps = app.pipeline_deps or PipelineDeps()
        self.busy: str | None = None           # etapa rodando: JOB_EXTRACT, JOB_BOOST, JOB_CONVERT ou JOB_RENDER
        self.uploading: str | None = None      # JOB_UPLOAD ou JOB_RECONNECT
        self.recording = False
        self._ctx: dict = {}                   # tomada, modelo e extras da etapa em andamento
        self._up: dict = {}                    # tomada, modelo e arquivo do envio em andamento
        self._errors: dict[str, str] = {}      # secao -> ultimo erro (texto do rotulo)
        self._volume: dict[str, tuple] = {}    # take.id -> (media, pico) depois do ganho
        self._progress_text = ""
        self._player = None
        self._opened: list = []
        self._build(parent)

        app.on("take_changed", self._on_take_changed)
        app.on("model_changed", self._on_take_changed)
        app.on("recording", self._on_recording)
        app.on("job_ok", self._on_job)
        app.on("job_fail", self._on_job)
        app.on(EV_PROGRESS, self._on_progress)
        app.register_closer(self._render_reason)
        app.register_closer(self._upload_reason)
        app.add_shutdown_hook(self._on_shutdown)
        self.refresh()

    # ---------- layout ----------

    def _build(self, parent) -> None:
        # compacto: a coluna direita inteira cabe acima do log em 1100x720
        f = self.volume_frame = self.app.add_section("2. Aumentar volume", parent)
        row = self._row(f, last=True)
        ttk.Label(row, text="Ganho (dB):").pack(side="left")
        self.gain_var = tk.StringVar(master=self.root, value=str(DEFAULT_GAIN_DB))
        self.gain_entry = ttk.Entry(row, textvariable=self.gain_var, width=5)
        self.gain_entry.pack(side="left", padx=6)
        self.btn_boost = self._button(row, "Aumentar volume", self.on_boost)
        self.volume_label = self._status(row, side="left")

        f = self.convert_frame = self.app.add_section("3. Converter", parent)
        row = self._row(f)
        self.btn_convert = self._button(row, "Converter", self.on_convert)
        self.btn_play_rec = self._button(row, "▶ Ouvir gravação", self.on_play_recording)
        self.btn_play_out = self._button(row, "▶ Ouvir resultado", self.on_play_result)
        self.convert_label = self._status(f)

        f = self.video_frame = self.app.add_section("4. Vídeo", parent)
        row = self._row(f)
        self.btn_render = self._button(row, "Gerar vídeo", self.on_render)
        self.btn_cancel_render = self._button(row, "Cancelar", self.on_cancel_render)
        self.btn_watch = self._button(row, "Assistir", self.on_watch)
        self.btn_open_folder = self._button(row, "Abrir pasta", self.on_open_folder)
        self.video_label = self._status(f)

        f = self.drive_frame = self.app.add_section("5. Drive", parent)
        row = self._row(f)
        self.btn_upload = self._button(row, "Enviar", self.on_upload)
        self.btn_drive_config = self._button(row, "Configurar Drive", self.configure_drive)
        self.btn_reconnect = self._button(row, "Reconectar", self.on_reconnect)
        self.btn_cancel_upload = ttk.Button(row, text="Cancelar", command=self.on_cancel_upload)
        self.btn_cancel_upload.pack(side="right")
        self.progress = ttk.Progressbar(row, maximum=100, mode="determinate", length=80)
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.drive_label = self._status(f)

    @staticmethod
    def _row(parent, last: bool = False) -> ttk.Frame:
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=(4, 6 if last else 0))
        return row

    @staticmethod
    def _button(row, text: str, command) -> ttk.Button:
        btn = ttk.Button(row, text=text, command=command)
        btn.pack(side="left", padx=(0, 6))
        return btn

    @staticmethod
    def _status(parent, side: str = "top") -> ttk.Label:
        # quebra a linha na largura real (a janela pode encolher ate MIN_SIZE)
        label = ttk.Label(parent, text="", justify="left")
        if side == "top":
            label.pack(anchor="w", fill="x", padx=8, pady=(2, 6))
        else:
            label.pack(side=side, fill="x", expand=True)
        label.bind("<Configure>", lambda e: label.configure(wraplength=max(MIN_WRAP, e.width - 4)))
        return label

    # ---------- estado da tomada ----------

    @staticmethod
    def _has_source(take: Take) -> bool:
        # A/V recuperada sem audio.wav ainda serve: o audio e extraido antes
        if take.status == "gravando":
            return False
        return os.path.isfile(take.audio_path) or (take.modo == "av" and os.path.isfile(take.raw_path))

    @staticmethod
    def _recording_audio(take: Take | None) -> str | None:
        if take is None:
            return None
        for path in (take.audio_path, take.raw_path):
            if os.path.isfile(path):
                return path
        return None

    @staticmethod
    def _saida(take: Take | None, m: Modelo) -> dict:
        saida = take.saidas.get(m.key) if take is not None else None
        return saida if isinstance(saida, dict) else {}

    def _output(self, take: Take | None, m: Modelo, key: str) -> str | None:
        # caminho absoluto de saidas[modelo][key] ("mp4" e relativo a pasta da tomada), se existir
        name = self._saida(take, m).get(key)
        if not name:
            return None
        path = os.path.normpath(os.path.join(take.dir, name))
        return path if os.path.isfile(path) else None

    def _av_offset(self) -> int:
        # le o arquivo no clique: o calibrar_av.py --salvar pode ter gravado com o app aberto
        estado, aviso = load_estado(self.app.estado_path)
        if aviso:
            self.app.log(f"AVISO: {aviso}")
        return estado["av_offset_ms"]           # o load_estado ja garante int

    def _drive_link(self) -> str:
        # le o arquivo no clique: o enviar_drive.py --pasta pode ter trocado a pasta com o app aberto;
        # arquivo corrompido/ilegivel (ou sem link) fica com o da memoria
        estado, aviso = load_estado(self.app.estado_path)
        if aviso:
            self.app.log(f"AVISO: {aviso}")
        link = estado["drive_pasta"] or self.app.estado.get("drive_pasta") or ""
        if link:
            self.app.estado["drive_pasta"] = link    # o rotulo do Drive mostra a pasta que vai receber
        return link

    # ---------- botoes e rotulos ----------

    def refresh(self) -> None:
        take, m = self.app.take, self.app.modelo()
        ready = take is not None and not self.recording
        free = ready and self.busy is None
        wav, mp4 = self._output(take, m, "wav"), self._output(take, m, "mp4")
        source = free and self._has_source(take)
        self._enable(self.btn_boost, source)
        self._enable(self.btn_convert, source)
        self._enable(self.btn_play_rec, ready and self._recording_audio(take) is not None)
        self._enable(self.btn_play_out, ready and wav is not None)
        self._enable(self.btn_render, free and take.modo == "av" and wav is not None)
        self._enable(self.btn_cancel_render, self.busy == JOB_RENDER)
        self._enable(self.btn_watch, ready and mp4 is not None)
        self._enable(self.btn_upload, self.uploading is None and mp4 is not None)
        self._enable(self.btn_drive_config, self.uploading is None)
        self._enable(self.btn_reconnect, self.uploading is None)
        self._enable(self.btn_cancel_upload, self.uploading == JOB_UPLOAD)
        self._set(self.volume_label, *self._volume_text(take))
        self._set(self.convert_label, *self._convert_text(take, m, wav))
        self._set(self.video_label, *self._video_text(take, m, wav, mp4))
        self._set(self.drive_label, *self._drive_text(take, m))

    @staticmethod
    def _enable(widget, on: bool) -> None:
        widget.configure(state="normal" if on else "disabled")

    @staticmethod
    def _set(label, text: str, color: str) -> None:
        label.configure(text=text, foreground=color)

    def _busy_in(self, section: str) -> str | None:
        # texto da etapa rodando, so na secao dela (a extracao aparece na secao de quem pediu)
        if self.busy is None:
            return None
        where = {JOB_BOOST: "volume", JOB_CONVERT: "convert", JOB_RENDER: "video"}.get(self.busy)
        if where is None:
            where = "volume" if self._ctx.get("then") == JOB_BOOST else "convert"
        if where != section:
            return None
        text = BUSY_TEXT[self.busy].format(label=self._ctx["modelo"].label)
        take = self._ctx["take"]
        return text if take is self.app.take else f"{text} (tomada {take.id})"

    def _volume_text(self, take: Take | None) -> tuple[str, str]:
        busy = self._busy_in("volume")
        if busy:
            return busy, COLOR_BUSY
        if "volume" in self._errors:
            return self._errors["volume"], COLOR_BAD
        if take is None:
            return "", COLOR_TEXT
        if os.path.isfile(take.path(BOOSTED)):
            if take.id in self._volume:
                mean, peak = self._volume[take.id]
                return f"{BOOSTED}: média {_db(mean)}, pico {_db(peak)}", COLOR_OK
            return f"A conversão usa o {BOOSTED}", COLOR_OK
        return "Opcional: se a voz ficou baixa", COLOR_TEXT

    def _convert_text(self, take: Take | None, m: Modelo, wav: str | None) -> tuple[str, str]:
        busy = self._busy_in("convert")
        if busy:
            return busy, COLOR_BUSY
        if "convert" in self._errors:
            return self._errors["convert"], COLOR_BAD
        if take is None:
            return "Nenhuma tomada — grave primeiro", COLOR_TEXT
        head = f"Tomada {take.id}"
        if not self._has_source(take):
            return f"{head}: {take.erro or 'gravação não encontrada'}", COLOR_BAD
        if take.status == "falhou" and take.erro:
            return f"{head}: falhou — {take.erro}", COLOR_BAD
        if wav:
            return f"{head}: convertida para {m.label} ✓", COLOR_OK
        erro = self._saida(take, m).get("erro")          # ultimo erro deste modelo (o de outro nao aparece)
        if erro:
            return f"{head}: a conversão para {m.label} falhou — {erro}", COLOR_BAD
        return f"{head}: pronta para converter para {m.label}", COLOR_TEXT

    def _video_text(self, take: Take | None, m: Modelo, wav: str | None, mp4: str | None) -> tuple[str, str]:
        busy = self._busy_in("video")
        if busy:
            return busy, COLOR_BUSY
        if "video" in self._errors:
            return self._errors["video"], COLOR_BAD
        if take is None:
            return "", COLOR_TEXT
        if take.modo != "av":
            return "Tomada só de áudio: sem vídeo", COLOR_TEXT
        if mp4:
            return f"Pronto: {os.path.basename(mp4)}", COLOR_OK
        erro = self._saida(take, m).get("erro")          # o ultimo video deste modelo falhou
        if wav and erro:
            return f"Falhou: {erro}", COLOR_BAD
        if wav:
            return "Clique Gerar vídeo para gerar o MP4", COLOR_TEXT
        return "O vídeo sai sozinho depois de converter", COLOR_TEXT

    def _drive_text(self, take: Take | None, m: Modelo) -> tuple[str, str]:
        if self.uploading == JOB_RECONNECT:
            return "Reconectando: conclua o login no navegador…", COLOR_BUSY
        if self.uploading == JOB_UPLOAD:
            return self._progress_text, COLOR_BUSY
        if "drive" in self._errors:
            return self._errors["drive"], COLOR_BAD
        link = self.app.estado.get("drive_pasta") or ""
        if not link:
            return "Pasta do Drive não configurada — clique Configurar Drive", COLOR_TEXT
        try:
            folder_id = drive.parse_folder_link(link)[0]
        except drive.DriveError as e:
            return f"Link da pasta inválido ({e}) — clique Configurar Drive", COLOR_BAD
        sent = self._saida(take, m).get("enviado")
        # "Enviado em ..." so quando o mp4 atual ainda existe: uma reconversao tira o mp4 antigo, mas mantem
        # o "enviado" (o arquivo continua no Drive)
        if isinstance(sent, dict) and sent.get("md5") and self._output(take, m, "mp4") is not None:
            return f"Enviado em {str(sent.get('quando', '')).replace('T', ' ')} (pasta {folder_id})", COLOR_OK
        return f"Pasta: {folder_id}", COLOR_TEXT

    # ---------- eventos ----------

    def _on_take_changed(self, ev: Event) -> None:
        # tomada ou modelo novo: os erros da etapa anterior nao valem mais
        for section in ("volume", "convert", "video"):
            self._errors.pop(section, None)
        self.refresh()

    def _on_recording(self, ev: Event) -> None:
        self.recording = bool(ev.data.get("active"))
        if self.recording:
            self._stop_player()               # um som tocando entraria no microfone da gravacao nova
        self.refresh()

    def _on_progress(self, ev: Event) -> None:
        if self.uploading != JOB_UPLOAD:
            return
        pct = round(100 * max(0.0, min(1.0, float(ev.data["fracao"]))))
        self.progress.configure(value=pct)
        self._progress_text = f"Enviando {ev.data['nome']}… {pct}%"
        self._set(self.drive_label, self._progress_text, COLOR_BUSY)

    def _on_job(self, ev: Event) -> None:
        handlers = {JOB_EXTRACT: self._extracted, JOB_BOOST: self._boosted, JOB_CONVERT: self._converted,
                    JOB_RENDER: self._rendered, JOB_UPLOAD: self._uploaded, JOB_RECONNECT: self._reconnected}
        job = ev.data["job"]
        handler = handlers.get(job)
        if handler is None:
            return
        # reabilita antes de aplicar: um erro no handler nao deixa botao preso
        if job in (JOB_UPLOAD, JOB_RECONNECT):
            self.uploading = None
        else:
            self.busy = None
        ok = ev.kind == "job_ok"
        try:
            handler(ok, ev.data.get("result") if ok else ev.data.get("message", ""))
        finally:
            self.refresh()

    # ---------- 2 e 3: volume e conversao ----------

    def _can_process(self, take: Take | None) -> bool:
        return take is not None and not self.recording and self.busy is None and self._has_source(take)

    def on_boost(self) -> None:
        take = self.app.take
        if not self._can_process(take):
            return
        try:
            gain = parse_gain(self.gain_var.get())
        except ValueError as e:
            self.app.log(str(e))
            self._errors["volume"] = str(e)
            self.refresh()
            return
        self._errors.pop("volume", None)
        self._prepare(take, JOB_BOOST, gain=gain)
        self.refresh()

    def on_convert(self) -> None:
        take = self.app.take
        if not self._can_process(take):
            return
        for section in ("convert", "video"):
            self._errors.pop(section, None)
        self._prepare(take, JOB_CONVERT)
        self.refresh()

    def _prepare(self, take: Take, then: str, **extra) -> None:
        # tomada A/V recuperada (crash ou fechar gravando) nao tem audio.wav: extrai antes, na fila io
        m = self.app.modelo()
        if take.modo == "av" and not os.path.isfile(take.audio_path):
            if self._submit(self.app.io_jobs, JOB_EXTRACT, self.deps.extract_aligned_audio,
                            take.raw_path, take.audio_path):
                self._ctx = {"take": take, "modelo": m, "then": then, **extra}
                self.busy = JOB_EXTRACT
                self.app.log(f"Preparando o áudio alinhado da tomada {take.id}…")
            return
        if then == JOB_BOOST:
            self._start_boost(take, m, extra["gain"])
        else:
            self._start_convert(take, m)

    def _extracted(self, ok: bool, value) -> None:
        ctx = self._ctx
        take = ctx["take"]
        section = "volume" if ctx["then"] == JOB_BOOST else "convert"
        if not ok:
            self._failed(take, section, f"ERRO ao preparar o áudio da tomada {take.id}", value, mark=True)
            return
        vi, fit = value
        if not self._commit(take, section, video=asdict(vi), audio_fit=asdict(fit)):
            return
        self.app.log(f"Áudio alinhado da tomada {take.id} preparado")
        warn = gaps_warning(take)
        if warn:
            self.app.log(warn)
        if ctx["then"] == JOB_BOOST:
            self._start_boost(take, ctx["modelo"], ctx["gain"])
        else:
            self._start_convert(take, ctx["modelo"])

    def _start_boost(self, take: Take, m: Modelo, gain: float) -> None:
        # sempre a partir da gravacao original: aumentar de novo nao acumula ganho
        if self._submit(self.app.io_jobs, JOB_BOOST, boost_job, self.deps.boost_volume, self.deps.volumedetect,
                        take.audio_path, take.path(BOOSTED), gain):
            self._ctx = {"take": take, "modelo": m, "then": None, "gain": gain}
            self.busy = JOB_BOOST
            self.app.log(f"Aumentando o volume em {_num(gain)} dB ({take.id})…")

    def _boosted(self, ok: bool, value) -> None:
        take, gain = self._ctx["take"], self._ctx["gain"]
        if not ok:
            self._failed(take, "volume", "ERRO ao aumentar o volume", value, mark=False)
            return
        mean, peak = value
        self._volume[take.id] = (mean, peak)
        self.app.log(f"Volume aumentado em {_num(gain)} dB → {BOOSTED} (média {_db(mean)}, pico {_db(peak)})")

    def _start_convert(self, take: Take, m: Modelo) -> None:
        try:
            rvc = self.app.ensure_rvc()      # thread principal; recria o worker que morreu
        except Exception as e:
            self.app.report_error(e, "iniciar o conversor")
            self._errors["convert"] = f"Falhou: não foi possível iniciar o conversor ({error_message(e)})"
            return
        inp = take.path(BOOSTED) if os.path.isfile(take.path(BOOSTED)) else take.audio_path
        wav, mp3 = take.path(f"{m.key}.wav"), take.path(f"{m.key}_IA.mp3")
        if self._submit(self.app.gpu_jobs, JOB_CONVERT, convert_job, rvc, self.deps.export_mp3, inp, wav, mp3, m):
            self._ctx = {"take": take, "modelo": m, "then": None, "rvc": rvc}
            self.busy = JOB_CONVERT
            self.app.log(f"Convertendo {os.path.basename(inp)} ({take.id}) para {m.label}…")

    def _converted(self, ok: bool, value) -> None:
        ctx = self._ctx
        take, m = ctx["take"], ctx["modelo"]
        if not ok:
            dead = not ctx["rvc"].alive()
            self._failed(take, "convert", "ERRO na conversão", value, modelo=m)
            if dead:
                # o proximo clique chama ensure_rvc(), que sobe um worker novo
                self.app.log(MSG_WORKER_DIED)
                self._errors["convert"] = MSG_WORKER_DIED
            return
        # o sucesso limpa o erro; o mp4 antigo nao vale mais (o video sai de novo), mas o enviado fica
        # (o arquivo antigo ainda esta no Drive; enviar_drive.py usa o md5 pra nao reenviar)
        saida = {k: v for k, v in self._saida(take, m).items() if k not in ("erro", "mp4")}
        saida.update(wav=f"{m.key}.wav", mp3=f"{m.key}_IA.mp3")
        if not self._commit(take, "convert", saidas={**take.saidas, m.key: saida}, status="convertido", erro=""):
            return                            # o take.json nao foi gravado: sem video
        self._mark_model_loaded()
        self.app.log(f"Voz convertida para {m.label}: {take.id} ({saida['wav']} e {saida['mp3']})")
        if take.modo == "av":
            self._start_render(take, m)       # o video sai sozinho depois de converter

    def _mark_model_loaded(self) -> None:
        # a conversao carrega o Applio no worker: "Carregar modelo" nao faz mais falta
        if not self.app.model_loaded:
            self.app.model_loaded = True
            self.app.model_status.configure(text="Status: carregado ✓", foreground=COLOR_OK)
            self.app.btn_load.configure(state="disabled")

    # ---------- 4: video ----------

    def on_render(self) -> None:
        take, m = self.app.take, self.app.modelo()
        if (take is None or self.recording or self.busy is not None or take.modo != "av"
                or self._output(take, m, "wav") is None):
            return
        self._errors.pop("video", None)
        self._start_render(take, m)
        self.refresh()

    def _start_render(self, take: Take, m: Modelo) -> None:
        wav = self._output(take, m, "wav")
        if wav is None:
            self._failed(take, "video", "ERRO ao gerar o vídeo", MSG_NO_WAV, modelo=m)
            return
        cancel = threading.Event()
        # o job recebe uma copia da Take: a thread principal pode mexer na original enquanto isso
        if self._submit(self.app.gpu_jobs, JOB_RENDER, self.deps.render_final, copy.deepcopy(take), m, wav,
                        av_offset_ms=self._av_offset(), videos_dir=self.deps.videos_dir, cancel=cancel):
            self._ctx = {"take": take, "modelo": m, "then": None, "cancel": cancel}
            self.busy = JOB_RENDER
            self.app.log(f"Gerando o vídeo de {take.id} ({m.label})…")

    def on_cancel_render(self) -> None:
        cancel = self._ctx.get("cancel") if self.busy == JOB_RENDER else None
        if cancel is not None and not cancel.is_set():
            cancel.set()
            self.app.log("Cancelando o vídeo…")
            self._set(self.video_label, "Cancelando…", COLOR_BUSY)

    def _rendered(self, ok: bool, value) -> None:
        ctx = self._ctx
        take, m = ctx["take"], ctx["modelo"]
        if not ok:
            if ctx["cancel"].is_set():
                self.app.log(f"Vídeo cancelado ({take.id})")
                return
            self._failed(take, "video", "ERRO ao gerar o vídeo", value, modelo=m)
            return
        # sem "enviado" (o arquivo mudou: ainda nao foi enviado) e sem "erro" (o sucesso limpa)
        saida = {k: v for k, v in self._saida(take, m).items() if k not in ("enviado", "erro")}
        saida["mp4"] = os.path.relpath(value, take.dir)
        if not self._commit(take, "video", saidas={**take.saidas, m.key: saida}, status="renderizado", erro=""):
            return
        self.app.log(f"Vídeo pronto: {os.path.basename(value)} (em {os.path.dirname(value)})")

    # ---------- 5: Drive ----------

    def on_upload(self) -> None:
        take, m = self.app.take, self.app.modelo()
        mp4 = self._output(take, m, "mp4")
        if self.uploading is not None or mp4 is None:
            return
        link = self._drive_link() or self.configure_drive()
        # o dialogo do link roda o laco de eventos: um 2o clique pode ter comecado um envio enquanto isso
        if not link or self.uploading is not None:
            return
        cancel = threading.Event()
        if not self._submit(self.app.upload_jobs, JOB_UPLOAD, upload_job, self.deps.upload_files, self.app.bus,
                            mp4, link, self.deps.videos_dir, cancel):
            return
        self._up = {"take": take, "modelo": m, "mp4": mp4, "cancel": cancel}
        self.uploading = JOB_UPLOAD
        self._errors.pop("drive", None)
        self._progress_text = f"Enviando {os.path.basename(mp4)}…"
        self.progress.configure(value=0)
        self.app.log(f"Enviando pro Drive: {os.path.basename(mp4)}")
        self.refresh()

    def on_cancel_upload(self) -> None:
        cancel = self._up.get("cancel") if self.uploading == JOB_UPLOAD else None
        if cancel is not None and not cancel.is_set():
            cancel.set()
            self.app.log("Cancelando o envio…")
            self._set(self.drive_label, "Cancelando o envio…", COLOR_BUSY)

    def _uploaded(self, ok: bool, value) -> None:
        take, m, name = self._up["take"], self._up["modelo"], os.path.basename(self._up["mp4"])
        res = value if ok else {"ok": False, "erro": value}
        if res.get("ok"):
            self.progress.configure(value=100)
            what = "Já estava igual no Drive (pulado)" if res.get("pulado") else "Enviado pro Drive"
            self.app.log(f"{what}: {name}")
            if res.get("aviso"):
                self.app.log(f"AVISO: {res['aviso']}")
            if res.get("md5"):
                sent = {"md5": res["md5"], "quando": datetime.now().isoformat(timespec="seconds")}
                self._commit(take, "drive", saidas={**take.saidas, m.key: {**self._saida(take, m), "enviado": sent}})
            return
        self.progress.configure(value=0)
        erro = res.get("erro") or "Falha desconhecida no envio"
        if erro == drive.MSG_CANCELLED:
            self.app.log(f"Envio cancelado: {name}")
            return
        self.app.log(f"ERRO no envio: {erro}")
        self._errors["drive"] = f"Falhou: {erro}"

    def configure_drive(self) -> str | None:
        # pede o link ate ser valido (ou Cancelar); salva no estado.json
        if self.uploading is not None:
            return None
        ask = self.deps.ask_link or self._ask_link
        prompt, initial = MSG_ASK_LINK, self.app.estado.get("drive_pasta") or ""
        while True:
            text = ask(TITLE_DRIVE, prompt, initial)
            if text is None:
                return None
            try:
                folder_id, _rk = drive.parse_folder_link(text)
            except drive.DriveError as e:
                self.app.log(f"Link do Drive recusado: {e}")
                prompt, initial = f"{e}.\n\n{MSG_ASK_LINK}", text
                continue
            link = text.strip()
            self.app.update_estado(drive_pasta=link)
            self._errors.pop("drive", None)
            self.app.log(f"Pasta do Drive configurada (ID {folder_id})")
            self.refresh()
            return link

    def _ask_link(self, title: str, prompt: str, initial: str) -> str | None:
        return simpledialog.askstring(title, prompt, initialvalue=initial, parent=self.root)

    def on_reconnect(self) -> None:
        if self.uploading is not None:
            return
        if not self._submit(self.app.upload_jobs, JOB_RECONNECT, self.deps.reconnect):
            return
        self.uploading = JOB_RECONNECT
        self._errors.pop("drive", None)
        self.app.log("Reconectando o Drive: conclua o login no navegador que vai abrir (até 5 min)…")
        self.refresh()

    def _reconnected(self, ok: bool, value) -> None:
        if ok:
            self.app.log("Drive reconectado")
            return
        self.app.log(f"ERRO ao reconectar o Drive: {value}")
        self._errors["drive"] = f"Falhou: {value}"

    # ---------- ouvir, assistir, abrir pasta ----------

    def on_play_recording(self) -> None:
        if not self.recording:
            self._play(self._recording_audio(self.app.take))

    def on_play_result(self) -> None:
        if not self.recording:
            self._play(self._output(self.app.take, self.app.modelo(), "wav"))

    def _play(self, path: str | None) -> None:
        if not path:
            return
        self._stop_player()                   # um som por vez
        self._player = self._spawn([*FFPLAY, path])

    def _stop_player(self) -> None:
        player, self._player = self._player, None
        if player is not None and player.poll() is None:
            try:
                player.terminate()
            except OSError:
                pass

    def on_watch(self) -> None:
        mp4 = self._output(self.app.take, self.app.modelo(), "mp4")
        if mp4 and not self.recording:
            self._spawn([OPENER, mp4])

    def on_open_folder(self) -> None:
        os.makedirs(self.deps.videos_dir, exist_ok=True)
        self._spawn([OPENER, self.deps.videos_dir])

    def _spawn(self, argv: list[str]):
        self._opened = [p for p in self._opened if p.poll() is None]     # colhe os que ja sairam
        try:
            p = self.deps.spawn(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            self.app.log(f"Não foi possível abrir {argv[0]}: {error_message(e)}")
            return None
        self._opened.append(p)
        return p

    # ---------- falhas, fechar e utilitarios ----------

    def _failed(self, take: Take, section: str, what: str, message: str, mark: bool = False,
                modelo: Modelo | None = None) -> None:
        # log + rotulo da secao; no take.json (thread principal): mark=True -> "falhou" (so a gravacao: o audio
        # da tomada nao se le); modelo -> saidas[modelo]["erro"] (conversao e video), sem mexer no status
        self.app.log(f"{what}: {message}")
        if take is self.app.take:
            self._errors[section] = f"Falhou: {message}"
        if not mark and modelo is None:
            return
        if mark:
            take.status, take.erro = "falhou", message
        if modelo is not None:
            take.saidas[modelo.key] = {**self._saida(take, modelo), "erro": message}
        try:
            take.save()
        except OSError as e:
            self.app.log(f"AVISO: o erro não foi gravado no take.json: {procs.os_error_message(e)}")

    def _commit(self, take: Take, section: str, **changes) -> bool:
        # aplica e grava o take.json; se nao gravar (ex.: disco cheio), desfaz na memoria e mostra o erro
        before = {k: copy.deepcopy(getattr(take, k)) for k in changes}
        for k, v in changes.items():
            setattr(take, k, v)
        try:
            take.save()
        except OSError as e:
            for k, v in before.items():
                setattr(take, k, v)
            self._failed(take, section, "ERRO ao salvar a tomada", procs.os_error_message(e))
            return False
        return True

    def _render_reason(self) -> str | None:
        return LABEL_RENDER if self.busy == JOB_RENDER else None

    def _upload_reason(self) -> str | None:
        return LABEL_UPLOAD if self.uploading == JOB_UPLOAD else None

    def _on_shutdown(self) -> None:
        # fechar mesmo assim: o render e o envio param (SIGTERM) e o .part sai; o som para
        for cancel in (self._ctx.get("cancel"), self._up.get("cancel")):
            if cancel is not None:
                cancel.set()
        self._stop_player()

    @staticmethod
    def _submit(runner, job: str, fn, *args, **kwargs) -> bool:
        try:
            runner.submit(job, fn, *args, **kwargs)
            return True
        except RuntimeError:                  # fila fechada: o app esta fechando
            return False
