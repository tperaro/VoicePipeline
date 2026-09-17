#!/bin/bash
# Grava do microfone (PulseAudio) em WAV 48k mono.
# Uso: pipeline/tools/record.sh <segundos> <saida.wav> [device]
# Liste devices com: pactl list short sources
set -euo pipefail
SECS="$1"; OUT="$2"; DEVICE="${3:-}"
ARGS=(--file-format=wav --rate=48000 --channels=1)
[ -n "$DEVICE" ] && ARGS+=("--device=$DEVICE")
echo "GRAVANDO ${SECS}s - FALE!"
timeout "$SECS" parecord "${ARGS[@]}" "$OUT" || true
ffmpeg -i "$OUT" -af volumedetect -f null - 2>&1 | grep -E "mean_volume|max_volume"
