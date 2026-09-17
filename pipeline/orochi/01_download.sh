#!/bin/bash
# Baixa 5 faixas oficiais do canal do Orochi no YouTube em WAV.
set -euo pipefail
source "$(dirname "$0")/../env.sh"
activate_applio

OUT="$DATA_DIR/orochi/raw_audio"
mkdir -p "$OUT" && cd "$OUT"

# Só Você, Lua Cheia, Airbnb, Sanguessuga, Fashion
IDS="yEYJexMWX1Q G9xHsguf6iA GkkV-ZMR1fg EFEU2YHbCac EsvqUCWKSJM"
for id in $IDS; do
  yt-dlp -x --audio-format wav --audio-quality 0 -o "%(title)s.%(ext)s" "https://www.youtube.com/watch?v=$id"
done
ls -la
