#!/usr/bin/env python3
"""
Voice Studio — telinha para gravar, dar boost de volume e converter
a voz pro Orochi ou pro Silvio Santos via RVC (Applio), sem precisar
abrir o Claude toda vez.

Fluxo: Escolher modelo -> Carregar -> Gravar (com duracao opcional ou
       clicar p/ parar) -> Aumentar volume -> Converter -> salva tudo
       em recordings/.
"""

import glob
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import ttk

# Por padrao usa a raiz do repo (pai de app/). Sobrescreva com
# VOICE_STUDIO_HOME / APPLIO_DIR se o Applio estiver em outro lugar.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_DIR = os.path.expanduser(os.environ.get("VOICE_STUDIO_HOME", REPO_ROOT))
APPLIO_DIR = os.path.expanduser(os.environ.get("APPLIO_DIR", os.path.join(BASE_DIR, "Applio")))
LOGS_DIR = os.path.join(APPLIO_DIR, "logs")
REC_DIR = os.path.join(BASE_DIR, "recordings")

MODELS = [
    {"key": "orochi", "label": "Orochi"},
    {"key": "silvio", "label": "Silvio Santos"},
]

os.makedirs(REC_DIR, exist_ok=True)


def model_dir(model_key):
    return os.path.join(LOGS_DIR, model_key)


def model_index_path(model_key):
    return os.path.join(model_dir(model_key), f"{model_key}.index")


def find_latest_checkpoint(model_key):
    pattern = os.path.join(model_dir(model_key), f"{model_key}_*e_*s.pth")
    files = glob.glob(pattern)

    def epoch(path):
        m = re.search(rf"{model_key}_(\d+)e_", os.path.basename(path))
        return int(m.group(1)) if m else -1

    files.sort(key=epoch)
    return files[-1] if files else None


def list_input_devices():
    try:
        out = subprocess.run(
            ["pactl", "list", "short", "sources"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return []
    devices = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and ".monitor" not in parts[1]:
            devices.append(parts[1])
    devices.sort(key=lambda d: ("usb" not in d.lower(), d))
    return devices


def run_volumedetect(path):
    """Retorna (mean_db, max_db) ou (None, None) se falhar."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=30,
        ).stderr
    except Exception:
        return None, None
    mean = re.search(r"mean_volume:\s*(-?\d+\.?\d*)", out)
    peak = re.search(r"max_volume:\s*(-?\d+\.?\d*)", out)
    return (
        float(mean.group(1)) if mean else None,
        float(peak.group(1)) if peak else None,
    )


class App:
    def __init__(self, root):
        self.root = root
        root.title("Voice Studio (Orochi / Silvio)")
        root.geometry("560x680")
        root.resizable(False, False)

        self.model_loaded = False  # pipeline RVC (torch/core) ja importado?
        self.rec_proc = None
        self.rec_timer = None
        self.rec_start = None
        self.tick_job = None
        self.current_wav = None       # ultima gravacao crua
        self.boosted_wav = None       # apos aumentar volume
        self.output_wav = None        # resultado convertido

        self.models_by_label = {m["label"]: m["key"] for m in MODELS}
        self.current_model_key = MODELS[0]["key"]
        self.checkpoint_path = find_latest_checkpoint(self.current_model_key)
        self.index_path = model_index_path(self.current_model_key)
        self.devices = list_input_devices()

        pad = {"padx": 12, "pady": 6}

        # ---------- Secao 1: modelo ----------
        f1 = ttk.LabelFrame(root, text="1. Modelo")
        f1.pack(fill="x", **pad)

        row_model = ttk.Frame(f1)
        row_model.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(row_model, text="Voz:").pack(side="left")
        self.model_var = tk.StringVar(value=MODELS[0]["label"])
        model_box = ttk.Combobox(
            row_model, textvariable=self.model_var,
            values=[m["label"] for m in MODELS], width=20, state="readonly",
        )
        model_box.pack(side="left", padx=6)
        model_box.bind("<<ComboboxSelected>>", self.on_model_change)

        self.checkpoint_label = ttk.Label(f1, text=self._checkpoint_label_text())
        self.checkpoint_label.pack(anchor="w", padx=8, pady=(6, 0))
        self.model_status = ttk.Label(f1, text="Status: não carregado", foreground="#a33")
        self.model_status.pack(anchor="w", padx=8, pady=(0, 6))
        self.btn_load = ttk.Button(f1, text="Carregar modelo", command=self.on_load_model)
        self.btn_load.pack(anchor="w", padx=8, pady=(0, 8))

        # ---------- Secao 2: gravacao ----------
        f2 = ttk.LabelFrame(root, text="2. Gravação")
        f2.pack(fill="x", **pad)

        row = ttk.Frame(f2)
        row.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(row, text="Microfone:").pack(side="left")
        self.device_var = tk.StringVar(value=self.devices[0] if self.devices else "")
        device_box = ttk.Combobox(row, textvariable=self.device_var, values=self.devices, width=42, state="readonly")
        device_box.pack(side="left", padx=6)

        row2 = ttk.Frame(f2)
        row2.pack(fill="x", padx=8, pady=6)
        ttk.Label(row2, text="Duração em segundos (deixe vazio p/ parar manualmente):").pack(side="left")
        self.duration_var = tk.StringVar(value="")
        ttk.Entry(row2, textvariable=self.duration_var, width=6).pack(side="left", padx=6)

        self.btn_record = ttk.Button(f2, text="● Gravar", command=self.on_record_toggle)
        self.btn_record.pack(anchor="w", padx=8, pady=(0, 4))

        self.rec_status = ttk.Label(f2, text="Parado.")
        self.rec_status.pack(anchor="w", padx=8, pady=(0, 8))

        # ---------- Secao 3: volume ----------
        f3 = ttk.LabelFrame(root, text="3. Aumentar volume")
        f3.pack(fill="x", **pad)

        row3 = ttk.Frame(f3)
        row3.pack(fill="x", padx=8, pady=6)
        ttk.Label(row3, text="Ganho (dB):").pack(side="left")
        self.gain_var = tk.StringVar(value="15")
        ttk.Entry(row3, textvariable=self.gain_var, width=5).pack(side="left", padx=6)
        self.btn_boost = ttk.Button(row3, text="🔊 Aumentar volume", command=self.on_boost, state="disabled")
        self.btn_boost.pack(side="left", padx=6)

        self.boost_status = ttk.Label(f3, text="Aguardando gravação.")
        self.boost_status.pack(anchor="w", padx=8, pady=(0, 8))

        # ---------- Secao 4: conversao ----------
        self.f4 = ttk.LabelFrame(root, text=self._convert_frame_text())
        self.f4.pack(fill="x", **pad)
        self.btn_convert = ttk.Button(self.f4, text="🎤 Converter", command=self.on_convert, state="disabled")
        self.btn_convert.pack(anchor="w", padx=8, pady=6)
        self.convert_status = ttk.Label(self.f4, text="Aguardando gravação.")
        self.convert_status.pack(anchor="w", padx=8, pady=(0, 4))

        rowp = ttk.Frame(self.f4)
        rowp.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(rowp, text="▶ Ouvir gravação", command=lambda: self.play(self.current_wav)).pack(side="left")
        ttk.Button(rowp, text="▶ Ouvir resultado", command=lambda: self.play(self.output_wav)).pack(side="left", padx=8)

        # ---------- Log ----------
        f5 = ttk.LabelFrame(root, text="Log")
        f5.pack(fill="both", expand=True, **pad)
        self.log_box = tk.Text(f5, height=10, state="disabled", wrap="word")
        self.log_box.pack(fill="both", expand=True, padx=6, pady=6)

        self.log(f"Pasta de gravações: {REC_DIR}")
        if not self.checkpoint_path:
            self.log(f"AVISO: nenhum checkpoint encontrado em {model_dir(self.current_model_key)}")
        if not self.devices:
            self.log("AVISO: nenhum microfone encontrado via pactl.")

    # ---------------- utilidades ----------------

    def current_model_label(self):
        return self.model_var.get()

    def _checkpoint_label_text(self):
        ck_name = os.path.basename(self.checkpoint_path) if self.checkpoint_path else "NENHUM CHECKPOINT ENCONTRADO"
        return f"Checkpoint: {ck_name}"

    def _convert_frame_text(self):
        return f"4. Converter para voz do {self.current_model_label()}"

    def on_model_change(self, event=None):
        label = self.model_var.get()
        self.current_model_key = self.models_by_label[label]
        self.checkpoint_path = find_latest_checkpoint(self.current_model_key)
        self.index_path = model_index_path(self.current_model_key)

        self.checkpoint_label.configure(text=self._checkpoint_label_text())
        self.f4.configure(text=self._convert_frame_text())
        self.convert_status.configure(text="Aguardando gravação.")
        self.output_wav = None

        self.log(f"Modelo selecionado: {label}")
        if not self.checkpoint_path:
            self.log(f"AVISO: nenhum checkpoint encontrado em {model_dir(self.current_model_key)}")

        # habilita converter de novo se ja existe gravacao pronta
        if self.current_wav and os.path.exists(self.current_wav) and self.checkpoint_path:
            self.btn_convert.configure(state="normal")

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{ts}] {msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def run_bg(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def play(self, path):
        if not path or not os.path.exists(path):
            self.log("Nada pra tocar ainda.")
            return
        subprocess.Popen(
            ["ffplay", "-autoexit", "-nodisp", "-loglevel", "error", path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    # ---------------- 1. carregar modelo ----------------

    def on_load_model(self):
        if self.model_loaded:
            return
        self.btn_load.configure(state="disabled")
        self.model_status.configure(text="Status: carregando... (pode levar alguns segundos)", foreground="#a80")
        self.log("Carregando modelo (importando torch/RVC)...")

        def work():
            try:
                os.chdir(APPLIO_DIR)
                sys.path.insert(0, APPLIO_DIR)
                global core
                import core  # noqa: importa e ja inicializa o pipeline lazy
                core.import_voice_converter()  # instancia o VoiceConverter uma vez
                self.root.after(0, self.on_model_loaded, True, None)
            except Exception as e:
                self.root.after(0, self.on_model_loaded, False, str(e))

        self.run_bg(work)

    def on_model_loaded(self, ok, err):
        if ok:
            self.model_loaded = True
            self.model_status.configure(text="Status: carregado ✓", foreground="#2a2")
            self.log("Modelo carregado. Pronto pra converter rapido.")
        else:
            self.model_status.configure(text="Status: falhou ao carregar", foreground="#a33")
            self.log(f"ERRO ao carregar modelo: {err}")
            self.btn_load.configure(state="normal")

    # ---------------- 2. gravacao ----------------

    def on_record_toggle(self):
        if self.rec_proc is None:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self):
        device = self.device_var.get()
        if not device:
            self.log("Selecione um microfone antes de gravar.")
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_wav = os.path.join(REC_DIR, f"take_{ts}.wav")
        self.boosted_wav = None
        self.output_wav = None
        self.btn_boost.configure(state="disabled")
        self.btn_convert.configure(state="disabled")
        self.boost_status.configure(text="Aguardando gravação.")
        self.convert_status.configure(text="Aguardando gravação.")

        cmd = [
            "parecord", "--file-format=wav", "--rate=48000", "--channels=1",
            f"--device={device}", self.current_wav,
        ]
        self.rec_proc = subprocess.Popen(cmd)
        self.rec_start = time.time()
        self.btn_record.configure(text="■ Parar")
        self.log(f"Gravando -> {os.path.basename(self.current_wav)} (fale agora!)")

        dur_text = self.duration_var.get().strip()
        if dur_text:
            try:
                secs = float(dur_text)
                self.rec_timer = threading.Timer(secs, lambda: self.root.after(0, self.stop_recording))
                self.rec_timer.start()
            except ValueError:
                self.log("Duração inválida, ignorando (grave manualmente).")

        self.tick()

    def tick(self):
        if self.rec_proc is None:
            return
        elapsed = time.time() - self.rec_start
        self.rec_status.configure(text=f"Gravando... {elapsed:0.1f}s")
        self.tick_job = self.root.after(100, self.tick)

    def stop_recording(self):
        if self.rec_proc is None:
            return
        if self.rec_timer:
            self.rec_timer.cancel()
            self.rec_timer = None
        if self.tick_job:
            self.root.after_cancel(self.tick_job)
            self.tick_job = None

        proc = self.rec_proc
        self.rec_proc = None
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

        elapsed = time.time() - self.rec_start
        self.btn_record.configure(text="● Gravar")
        self.rec_status.configure(text=f"Gravado: {elapsed:0.1f}s -> {os.path.basename(self.current_wav)}")
        self.log(f"Gravação finalizada ({elapsed:0.1f}s).")
        self.btn_boost.configure(state="normal")
        self.btn_convert.configure(state="normal")  # pode converter direto sem boost

        mean, peak = run_volumedetect(self.current_wav)
        if mean is not None:
            self.log(f"Volume da gravação: mean={mean:.1f}dB max={peak:.1f}dB")

    # ---------------- 3. aumentar volume ----------------

    def on_boost(self):
        if not self.current_wav or not os.path.exists(self.current_wav):
            self.log("Nenhuma gravação disponível.")
            return
        try:
            gain = float(self.gain_var.get())
        except ValueError:
            gain = 15.0

        self.btn_boost.configure(state="disabled")
        self.boost_status.configure(text="Aumentando volume...")

        def work():
            boosted = self.current_wav.replace(".wav", "_boosted.wav")
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", self.current_wav, "-af", f"volume={gain}dB",
                     "-loglevel", "error", boosted],
                    check=True,
                )
                mean, peak = run_volumedetect(boosted)
                self.boosted_wav = boosted
                self.root.after(0, self.on_boost_done, True, mean, peak, None)
            except Exception as e:
                self.root.after(0, self.on_boost_done, False, None, None, str(e))

        self.run_bg(work)

    def on_boost_done(self, ok, mean, peak, err):
        self.btn_boost.configure(state="normal")
        if ok:
            self.boost_status.configure(text=f"OK: mean={mean:.1f}dB max={peak:.1f}dB -> {os.path.basename(self.boosted_wav)}")
            self.log(f"Volume ajustado -> {os.path.basename(self.boosted_wav)}")
        else:
            self.boost_status.configure(text="Falhou.")
            self.log(f"ERRO no boost de volume: {err}")

    # ---------------- 4. converter ----------------

    def on_convert(self):
        if not self.current_wav or not os.path.exists(self.current_wav):
            self.log("Nenhuma gravação disponível.")
            return
        if not self.checkpoint_path:
            self.log("Sem checkpoint do modelo, não dá pra converter.")
            return

        model_key = self.current_model_key
        model_label = self.current_model_label()
        checkpoint_path = self.checkpoint_path
        index_path = self.index_path

        input_path = self.boosted_wav if self.boosted_wav and os.path.exists(self.boosted_wav) else self.current_wav
        output_path = input_path.replace(".wav", "").replace("_boosted", "") + f"_{model_key}.wav"

        self.btn_convert.configure(state="disabled")
        self.convert_status.configure(text="Convertendo...")
        self.log(f"Convertendo {os.path.basename(input_path)} para a voz do {model_label}...")

        def work():
            try:
                if not self.model_loaded:
                    os.chdir(APPLIO_DIR)
                    sys.path.insert(0, APPLIO_DIR)
                    global core
                    import core
                    core.import_voice_converter()
                os.chdir(APPLIO_DIR)
                core.run_infer_script(
                    pitch=0,
                    index_rate=0.75,
                    volume_envelope=1.0,
                    protect=0.33,
                    f0_method="rmvpe",
                    input_path=input_path,
                    output_path=output_path,
                    pth_path=checkpoint_path,
                    index_path=index_path,
                    split_audio=False,
                    f0_autotune=False,
                    f0_autotune_strength=1.0,
                    proposed_pitch=False,
                    proposed_pitch_threshold=155.0,
                    clean_audio=False,
                    clean_strength=0.5,
                    export_format="WAV",
                    embedder_model="contentvec",
                )
                mp3_path = output_path.replace(".wav", ".mp3")
                subprocess.run(
                    ["ffmpeg", "-y", "-i", output_path, "-codec:a", "libmp3lame",
                     "-qscale:a", "2", "-loglevel", "error", mp3_path],
                    check=True,
                )
                self.root.after(0, self.on_convert_done, True, output_path, mp3_path, None)
            except Exception as e:
                self.root.after(0, self.on_convert_done, False, None, None, str(e))

        self.run_bg(work)

    def on_convert_done(self, ok, wav_path, mp3_path, err):
        self.btn_convert.configure(state="normal")
        if not self.model_loaded:
            self.model_loaded = True
            self.model_status.configure(text="Status: carregado ✓", foreground="#2a2")
            self.btn_load.configure(state="disabled")
        if ok:
            self.output_wav = wav_path
            self.convert_status.configure(text=f"Pronto: {os.path.basename(mp3_path)}")
            self.log(f"Convertido! Salvo em {wav_path} e {mp3_path}")
        else:
            self.convert_status.configure(text="Falhou.")
            self.log(f"ERRO na conversão: {err}")


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
