#!/bin/bash
# Recorta os trechos em que só o Silvio fala (~6 min no total).
set -euo pipefail
source "$(dirname "$0")/../env.sh"

cd "$DATA_DIR/silvio"
SRC="raw_audio/silvio_entrevista_empreendedorismo.wav"
mkdir -p dataset/silvio

ffmpeg -y -i "$SRC" -ss 6   -to 48  -c copy dataset/silvio/silvio_1.wav
ffmpeg -y -i "$SRC" -ss 59  -to 162 -c copy dataset/silvio/silvio_2.wav
ffmpeg -y -i "$SRC" -ss 182 -to 260 -c copy dataset/silvio/silvio_3.wav
ffmpeg -y -i "$SRC" -ss 285 -to 360 -c copy dataset/silvio/silvio_4.wav
ffmpeg -y -i "$SRC" -ss 363 -to 429 -c copy dataset/silvio/silvio_5.wav

for f in dataset/silvio/*.wav; do
  echo "$f: $(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$f")s"
done
