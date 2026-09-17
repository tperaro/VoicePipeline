#!/bin/bash
# Aplica efeito na voz convertida (highpass + compressor + reverb) e mixa
# com um instrumental separado pelo Demucs.
# Uso: pipeline/tools/mix_with_instrumental.sh <voz.wav> <no_vocals.wav> <saida.mp3> [duracao_s=34]
set -euo pipefail
source "$(dirname "$0")/../env.sh"
VOCAL="$(realpath "$1")"; INSTR="$(realpath "$2")"; OUT="$3"; DUR="${4:-34}"
FX="${VOCAL%.wav}_fx.wav"
MIX="${OUT%.mp3}.wav"

activate_applio
python3 - "$VOCAL" "$FX" <<'PY'
import sys
from pedalboard import Pedalboard, Reverb, Compressor, HighpassFilter
from pedalboard.io import AudioFile

board = Pedalboard([
    HighpassFilter(cutoff_frequency_hz=90),
    Compressor(threshold_db=-18, ratio=2.5, attack_ms=5, release_ms=120),
    Reverb(room_size=0.35, damping=0.5, wet_level=0.18, dry_level=0.85, width=0.8),
])
with AudioFile(sys.argv[1]) as f:
    audio, sr = f.read(f.frames), f.samplerate
with AudioFile(sys.argv[2], "w", sr, audio.shape[0]) as f:
    f.write(board(audio, sr))
PY

FADE_OUT=$((DUR - 2))
ffmpeg -y -i "$INSTR" -i "$FX" -filter_complex "
[0:a]atrim=0:${DUR},afade=t=in:st=0:d=1,afade=t=out:st=${FADE_OUT}:d=2,volume=0.5[instr];
[1:a]apad=pad_dur=1[voc];
[instr][voc]amix=inputs=2:duration=longest:dropout_transition=0,alimiter=limit=0.95
" -loglevel error "$MIX"
ffmpeg -y -i "$MIX" -codec:a libmp3lame -qscale:a 2 -loglevel error "$OUT"
echo "OK: $OUT"
