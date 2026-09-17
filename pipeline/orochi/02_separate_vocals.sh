#!/bin/bash
# Separa voz/instrumental com Demucs (htdemucs) e monta o dataset só com as vozes.
# Demucs roda no env conda "applio" (Python 3.10), não no venv do Applio.
set -euo pipefail
source "$(dirname "$0")/../env.sh"

WORK="$DATA_DIR/orochi"
cd "$WORK"

if command -v conda >/dev/null; then
  source "$(conda info --base)/etc/profile.d/conda.sh"
  conda activate applio
fi
demucs --two-stems=vocals -n htdemucs -o separated raw_audio/*.wav

mkdir -p dataset/orochi
i=1
for f in separated/htdemucs/*/vocals.wav; do
  cp "$f" "dataset/orochi/orochi_$i.wav"
  i=$((i+1))
done

python3 -c "
import wave, glob
total = sum(w.getnframes() / w.getframerate() for w in map(wave.open, glob.glob('dataset/orochi/*.wav')))
print(f'Dataset: {total/60:.1f} minutos')
"
