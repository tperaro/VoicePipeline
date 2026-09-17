#!/bin/bash
# Converte um audio para a voz de um modelo treinado (sem abrir o app).
# Uso: pipeline/infer.sh <orochi|silvio> <entrada.wav> [ganho_db=15]
# Gera <entrada>_<modelo>.wav e .mp3 em recordings/.
set -euo pipefail
source "$(dirname "$0")/env.sh"

MODEL="$1"; INPUT="$(realpath "$2")"; GAIN="${3:-15}"
LOGS="$APPLIO_DIR/logs/$MODEL"
# checkpoint com mais epocas (nome: <modelo>_<N>e_<steps>s.pth)
PTH="$(for f in "$LOGS"/"${MODEL}"_*e_*s.pth; do b="${f##*/}"; e="${b#"${MODEL}"_}"; echo "${e%%e_*} $f"; done | sort -n | tail -1 | cut -d' ' -f2-)"
mkdir -p "$REC_DIR"
NAME="$(basename "${INPUT%.*}")"
BOOSTED="$REC_DIR/${NAME}_boosted.wav"
OUT="$REC_DIR/${NAME}_${MODEL}.wav"

ffmpeg -y -i "$INPUT" -af "volume=${GAIN}dB" -loglevel error "$BOOSTED"

activate_applio
cd "$APPLIO_DIR"
python core.py infer \
  --input-path "$BOOSTED" --output-path "$OUT" \
  --pth-path "$PTH" --index-path "$LOGS/$MODEL.index" \
  --f0-method rmvpe --index-rate 0.75 --pitch 0

ffmpeg -y -i "$OUT" -codec:a libmp3lame -qscale:a 2 -loglevel error "${OUT%.wav}.mp3"
echo "OK: $OUT"
