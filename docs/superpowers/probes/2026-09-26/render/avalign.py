"""A/V alignment + final render (stdlib only; needs ffmpeg/ffprobe on PATH)."""
import json, subprocess, time

FPS = 30
OUT_SR = 48000


def ffprobe_streams(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    return v, a, info["format"]


def video_span(path):
    """(first_pts, end_pts) of the video stream in seconds, from packet timestamps (no decode).
    Do NOT use the Matroska DURATION tag: it is the stream END timestamp, not its length."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "packet=pts_time,duration_time", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True).stdout.split()
    pts = []
    last_dur = 1.0 / FPS
    for line in out:
        parts = line.split(",")
        if parts[0] not in ("", "N/A"):
            pts.append(float(parts[0]))
            if len(parts) > 1 and parts[1] not in ("", "N/A"):
                last_dur = float(parts[1])
    return min(pts), max(pts) + last_dur


def extract_aligned_audio(mkv, wav):
    """Write mono WAV (source sample rate) whose sample 0 == first video frame."""
    v, a, _ = ffprobe_streams(mkv)
    v_start = float(v["start_time"])
    a_start = float(a["start_time"])
    sr = int(a["sample_rate"])
    delta = a_start - v_start                  # >0: audio started after video
    n = round(abs(delta) * sr)
    if delta > 0:    # prepend silence
        af = f"asetpts=N/SR/TB,adelay=delays={n}S:all=1,asetpts=N/SR/TB"
    elif delta < 0:  # drop audio captured before the first video frame
        af = f"asetpts=N/SR/TB,atrim=start_sample={n},asetpts=N/SR/TB"
    else:
        af = "asetpts=N/SR/TB"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", mkv,
           "-map", "0:a:0", "-af", af, "-ac", "1", "-c:a", "pcm_s16le", wav]
    subprocess.run(cmd, check=True)
    return {"v_start": v_start, "a_start": a_start, "delta_s": delta, "shift_samples": n, "sr": sr, "cmd": cmd}


META = {
    "title": "Paródia/homenagem - voz gerada por IA",
    "comment": "Voz sintética gerada por IA (conversão RVC). Não é a voz real de Silvio Santos.",
    "description": "AI-generated/synthetic voice (RVC voice conversion). Parody/tribute. "
                   "Not the real voice of Silvio Santos.",
}


def build_render_cmd(mkv, converted_wav, watermark_png, out_mp4, encoder="h264_nvenc", color="601"):
    v_first, v_end = video_span(mkv)
    n_frames = round((v_end - v_first) * FPS)
    n_samples = n_frames * OUT_SR // FPS            # 1600 samples per frame @48k/30fps
    # Webcam MJPEG = yuvj422p (full range, BT.601 matrix). format=yuv420p does the range squeeze
    # (swscale) once, BEFORE overlay, so overlay blends in plain yuv420 (cheapest path).
    # color="601": keep BT.601 matrix and TAG it (free).  color="709": convert matrix (+~2 s/min).
    if color == "709":
        conv = "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"
        wm = "[2:v]scale=out_color_matrix=bt709:out_range=tv,format=yuva420p[wm];"
        ctag = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    else:
        conv = "format=yuv420p"
        wm = "[2:v]format=yuva420p[wm];"
        ctag = ["-colorspace", "smpte170m", "-color_primaries", "smpte170m", "-color_trc", "smpte170m"]
    vf = (f"[0:v]setpts=PTS-STARTPTS,fps={FPS},tpad=stop_mode=clone:stop=2,trim=end_frame={n_frames},"
          f"setpts=PTS-STARTPTS,{conv}[v0];{wm}"
          f"[v0][wm]overlay=0:0:format=yuv420,format=yuv420p[v]")
    # Exact length: pad (apad whole_len) then cut (atrim end_sample) to n_frames*1600 samples.
    # Do NOT use infinite apad + -shortest inside -filter_complex: ffmpeg 6.1 never terminates.
    af = (f"[1:a]aresample={OUT_SR},asetpts=N/SR/TB,apad=whole_len={n_samples},"
          f"atrim=end_sample={n_samples},asetpts=N/SR/TB[a]")
    if encoder == "h264_nvenc":
        venc = ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "21",
                "-b:v", "0", "-profile:v", "high"]
    else:
        venc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-profile:v", "high"]
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-i", mkv, "-i", converted_wav, "-i", watermark_png,
           "-filter_complex", vf + ";" + af, "-map", "[v]", "-map", "[a]",
           *venc, "-pix_fmt", "yuv420p", "-r", str(FPS),
           *ctag, "-color_range", "tv",
           "-c:a", "aac", "-b:a", "192k", "-ar", str(OUT_SR), "-ac", "1",
           "-movflags", "+faststart"]
    for k, val in META.items():
        cmd += ["-metadata", f"{k}={val}"]
    cmd += [out_mp4]
    return cmd, n_frames, n_samples


def render(mkv, converted_wav, watermark_png, out_mp4, encoder=None, color="601"):
    """nvenc first, libx264 fallback. Hard timeout guards against any ffmpeg hang."""
    import os
    encs = [encoder] if encoder else ["h264_nvenc", "libx264"]
    last_err = None
    for enc in encs:
        cmd, nf, ns = build_render_cmd(mkv, converted_wav, watermark_png, out_mp4, enc, color)
        t0 = time.time()
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=max(120, nf / FPS * 3))
        except subprocess.TimeoutExpired as e:
            last_err = f"timeout: {e}"
            continue
        if r.returncode == 0:
            return {"encoder": enc, "seconds": round(time.time() - t0, 2), "n_frames": nf,
                    "n_samples": ns, "cmd": cmd}
        last_err = r.stderr[-2000:]
        try:
            os.remove(out_mp4)          # never leave a half-written mp4 behind
        except OSError:
            pass
    raise RuntimeError(last_err)


if __name__ == "__main__":
    import sys
    op = sys.argv[1]
    if op == "extract":
        print(json.dumps(extract_aligned_audio(sys.argv[2], sys.argv[3]), ensure_ascii=False))
    elif op == "render":
        enc = sys.argv[6] if len(sys.argv) > 6 and sys.argv[6] != "auto" else None
        color = sys.argv[7] if len(sys.argv) > 7 else "601"
        print(json.dumps(render(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], enc, color), ensure_ascii=False))
