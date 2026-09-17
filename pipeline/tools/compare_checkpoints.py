#!/usr/bin/env python3
"""
Converte o mesmo audio com varios checkpoints de um modelo para comparar
qualidade por epoca.

Uso (com o venv do Applio ativo):
  python pipeline/tools/compare_checkpoints.py silvio entrada.wav 225 250 350
"""

import glob
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE_DIR = os.path.expanduser(os.environ.get("VOICE_STUDIO_HOME", REPO_ROOT))
APPLIO_DIR = os.path.expanduser(os.environ.get("APPLIO_DIR", os.path.join(BASE_DIR, "Applio")))

model, input_path, *epochs = sys.argv[1:]
input_path = os.path.abspath(input_path)
out_dir = os.path.join(BASE_DIR, "previews", model)
os.makedirs(out_dir, exist_ok=True)

os.chdir(APPLIO_DIR)
sys.path.insert(0, APPLIO_DIR)
import core  # noqa: E402

core.import_voice_converter()
index_path = os.path.join(APPLIO_DIR, "logs", model, f"{model}.index")

for epoch in epochs:
    matches = glob.glob(os.path.join(APPLIO_DIR, "logs", model, f"{model}_{epoch}e_*s.pth"))
    if not matches:
        print(f"Checkpoint {epoch}e nao encontrado, pulando")
        continue
    output_path = os.path.join(out_dir, f"{model}_{epoch}e.wav")
    print(f"Convertendo com {epoch}e...")
    core.run_infer_script(
        pitch=0,
        index_rate=0.75,
        volume_envelope=1.0,
        protect=0.33,
        f0_method="rmvpe",
        input_path=input_path,
        output_path=output_path,
        pth_path=matches[0],
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
    subprocess.run(["ffmpeg", "-y", "-i", output_path, "-codec:a", "libmp3lame",
                    "-qscale:a", "2", "-loglevel", "error", mp3_path], check=True)
    print(f"OK: {mp3_path}")
