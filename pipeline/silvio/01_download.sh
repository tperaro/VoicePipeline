#!/bin/bash
# Baixa a entrevista do Silvio Santos (empreendedorismo) em WAV.
set -euo pipefail
source "$(dirname "$0")/../env.sh"
activate_applio

OUT="$DATA_DIR/silvio/raw_audio"
mkdir -p "$OUT" && cd "$OUT"
yt-dlp -x --audio-format wav --audio-quality 0 \
  -o "silvio_entrevista_empreendedorismo.%(ext)s" \
  "https://www.youtube.com/watch?v=CTOaajZX-qo"
