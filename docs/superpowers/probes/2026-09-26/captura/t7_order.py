import json, os, signal, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import Reader, HERE, MIC, CAM
def argv(out, audio_first=True, fastprobe=False):
    vin = ["-f", "v4l2", "-input_format", "mjpeg", "-video_size", "1280x720", "-framerate", "30",
           "-ts", "mono2abs", "-thread_queue_size", "512"]
    if fastprobe: vin += ["-probesize", "32", "-analyzeduration", "0"]
    vin += ["-i", CAM]
    ain = ["-f", "pulse", "-thread_queue_size", "1024", "-sample_rate", "48000", "-channels", "1"]
    if fastprobe: ain += ["-probesize", "32", "-analyzeduration", "0"]
    ain += ["-i", MIC]
    a = ["ffmpeg", "-hide_banner", "-loglevel", "info", "-y", "-copyts"]
    a += (ain + vin) if audio_first else (vin + ain)
    vi, ai = ("1", "0") if audio_first else ("0", "1")
    a += ["-map", f"{vi}:v", "-map", f"{ai}:a", "-c:v", "copy", "-c:a", "pcm_s16le",
          "-avoid_negative_ts", "make_zero", "-f", "matroska", out,
          "-map", f"{vi}:v", "-vf", "scale=480:-2,fps=15", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
    return a
def go(tag, idle_before, **kw):
    time.sleep(idle_before)
    out = os.path.join(HERE, f"{tag}.mkv"); log = open(os.path.join(HERE, f"{tag}.stderr.log"), "wb")
    t0 = time.monotonic()
    p = subprocess.Popen(argv(out, **kw), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, bufsize=0)
    r = Reader(p.stdout); r.start()
    time.sleep(4.0)
    p.stdin.write(b"q\n"); p.stdin.flush(); rc = p.wait(timeout=10); log.close(); r.join(2)
    starts = []
    for l in open(os.path.join(HERE, f"{tag}.stderr.log"), errors="replace"):
        if "start:" in l: starts.append(float(l.split("start:")[1].split(",")[0]))
    src_first = "audio" if kw.get("audio_first", True) else "video"
    a_s, v_s = (starts[0], starts[1]) if src_first == "audio" else (starts[1], starts[0])
    return dict(tag=tag, idle_before_s=idle_before, rc=rc, first_preview_s=round(r.first_t - t0, 3) if r.first_t else None,
                input_start_audio_minus_video_s=round(a_s - v_s, 3))
res = []
res.append(go("t7_cold_videofirst", 8, audio_first=False))
res.append(go("t7_cold_audiofirst", 8, audio_first=True))
res.append(go("t7_warm_audiofirst", 0.5, audio_first=True))
res.append(go("t7_cold_audiofirst_fastprobe", 8, audio_first=True, fastprobe=True))
res.append(go("t7_warm_videofirst_fastprobe", 0.5, audio_first=False, fastprobe=True))
res.append(go("t7_cold_videofirst_fastprobe", 8, audio_first=False, fastprobe=True))
print(json.dumps(res, indent=1))
