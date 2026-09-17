#!/bin/bash
# Instala o Applio (RVC) no commit usado nos treinos + ferramentas do pipeline.
# Requisitos do sistema: git, git-lfs, uv, ffmpeg, pulseaudio-utils (parecord/pactl), GPU NVIDIA (CUDA 12.8).
set -euo pipefail
source "$(dirname "$0")/env.sh"

if [ ! -d "$APPLIO_DIR/.git" ]; then
  git clone "$APPLIO_REPO" "$APPLIO_DIR"
fi
git -C "$APPLIO_DIR" checkout "$APPLIO_COMMIT"

cd "$APPLIO_DIR"
[ -d .venv ] || uv venv .venv --python 3.12
activate_applio
export UV_HTTP_TIMEOUT=300
uv pip install python-ffmpeg
uv pip install -r requirements.txt \
  --extra-index-url https://download.pytorch.org/whl/cu128 \
  --index-strategy unsafe-best-match
uv pip install yt-dlp

# Baixa pretreinados (HiFi-GAN), contentvec, rmvpe etc.
python core.py prerequisites

# Config padrao do Applio (arquivo local, ignorado pelo git do Applio)
[ -f assets/config.json ] || cp assets/config_template.json assets/config.json

# Liga os modelos versionados (models/, via Git LFS) em Applio/logs/<modelo>/,
# que e onde o app e o infer.sh procuram checkpoint e index.
for dir in "$ROOT"/models/*/; do
  model="$(basename "$dir")"
  mkdir -p "logs/$model"
  for f in "$dir"*.pth "$dir"*.index; do
    target="logs/$model/$(basename "$f")"
    # nao sobrescreve arquivo real ja existente (ex: treino local)
    if [ -e "$f" ] && { [ ! -e "$target" ] || [ -L "$target" ]; }; then
      ln -sf "$f" "$target"
    fi
  done
done

python -c "import torch; print('CUDA:', torch.cuda.is_available())"

# Demucs (separacao de voz) rodou num env separado com Python 3.10:
#   conda create -n applio python=3.10 -y && conda activate applio && pip install demucs numpy
