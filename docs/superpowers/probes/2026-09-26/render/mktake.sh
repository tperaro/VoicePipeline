#!/usr/bin/env bash
# Build synthetic "recorder output": MJPEG 720p30 + mono 48k PCM in MKV.
# $1 = out.mkv, $2 = audio offset relative to video in seconds (+0.40 = audio starts later,
#      -0.25 = audio starts earlier), $3 = extra global ts offset (simulate wallclock-ish start), $4 = duration
set -euo pipefail
OUT=$1; OFF=$2; GOFF=${3:-0}; DUR=${4:-10}
tmp=$(mktemp -d -p .)
# video: white full-frame flash exactly on frame 90 (t=3.000 s of video time)
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "testsrc2=s=1280x720:r=30:d=$DUR" \
  -vf "drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='eq(n,90)'" \
  -c:v mjpeg -q:v 3 -pix_fmt yuvj422p "$tmp/v.mkv"
# audio: beep at absolute instant (video_start + 3.000) => audio-local t = 3.000 - OFF
python3 - "$OFF" "$DUR" > "$tmp/params" <<'PY'
import sys
off=float(sys.argv[1]); dur=float(sys.argv[2])
bt = 3.0 - off           # beep time in audio-local timeline
ad = dur - off           # audio duration so both streams end together
print(f"{bt:.6f} {ad:.6f}")
PY
read BT AD < "$tmp/params"
ffmpeg -hide_banner -loglevel error -y -f lavfi \
  -i "aevalsrc='0.01*(2*random(0)-1)+0.8*sin(2*PI*1000*t)*between(t,$BT,$BT+0.05)':s=48000:c=mono:d=$AD" \
  -c:a pcm_s16le "$tmp/a.wav"
if python3 -c "import sys; sys.exit(0 if float('$OFF')>=0 else 1)"; then
  VOFF=0; AOFF=$OFF
else
  VOFF=$(python3 -c "print(-float('$OFF'))"); AOFF=0
fi
ffmpeg -hide_banner -loglevel error -y -itsoffset "$VOFF" -i "$tmp/v.mkv" -itsoffset "$AOFF" -i "$tmp/a.wav" \
  -map 0:v -map 1:a -c copy -output_ts_offset "$GOFF" "$OUT"
rm -rf "$tmp"
