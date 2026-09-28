"""Measure A/V sync in a media file: flash frame time vs beep onset time (absolute, per-stream start_time aware)."""
import json, subprocess, sys
import numpy as np

def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)

def flash_time(path):
    # ffprobe via lavfi movie= keeps raw stream pts; crop top-centre (avoids watermark band + badge)
    src = f"movie={path},crop=iw/2:ih/2:iw/4:ih/8,signalstats"
    out = subprocess.run(["ffprobe", "-v", "error", "-f", "lavfi", "-i", src, "-show_entries",
                          "frame=pts_time:frame_tags=lavfi.signalstats.YAVG", "-of", "json"],
                         capture_output=True, text=True, check=True).stdout
    frames = json.loads(out)["frames"]
    ys = [(float(f["pts_time"]), float(f["tags"]["lavfi.signalstats.YAVG"])) for f in frames]
    idx = max(range(len(ys)), key=lambda i: ys[i][1])
    return idx, ys[idx][0], ys[idx][1], len(ys), ys[0][0]

def beep_time(path, sr=48000, thr=0.3):
    info = probe(path)
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    a_start = float(a.get("start_time", 0))
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-map", "0:a:0", "-ac", "1", "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    on = int(np.argmax(np.abs(x) > thr))
    return on, a_start + on / sr, a_start, len(x) / sr

if __name__ == "__main__":
    for p in sys.argv[1:]:
        fi, ft, fy, nfr, v0 = flash_time(p)
        bi, bt, a0, adur = beep_time(p)
        info = probe(p)
        v = next(s for s in info["streams"] if s["codec_type"] == "video")
        a = next(s for s in info["streams"] if s["codec_type"] == "audio")
        print(json.dumps({
            "file": p, "v_start": v0, "a_start": a0,
            "flash_frame_idx": fi, "flash_t": round(ft, 4), "flash_yavg": round(fy, 1),
            "beep_sample": bi, "beep_t": round(bt, 5),
            "offset_ms(beep-flash)": round((bt - ft) * 1000, 2),
            "n_video_frames": nfr, "video_dur_frames": round(nfr / 30, 4),
            "audio_dur_decoded": round(adur, 5),
            "v_stream_dur": v.get("duration"), "a_stream_dur": a.get("duration"),
            "a_sr": a.get("sample_rate"),
        }))
