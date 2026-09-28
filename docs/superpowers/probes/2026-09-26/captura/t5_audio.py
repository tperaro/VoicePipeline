import json, os, signal, subprocess, sys, time, struct
HERE = os.path.dirname(os.path.abspath(__file__))
MIC = "alsa_input.usb-Generalplus_Usb_Audio_Device-00.mono-fallback"
def argv(out, mic=MIC):
    return ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info", "-y",
            "-f", "pulse", "-thread_queue_size", "1024", "-sample_rate", "48000", "-channels", "1",
            "-i", mic, "-c:a", "pcm_s16le", "-f", "wav", out]
def hdr(path):
    b = open(path, "rb").read(64); size = os.path.getsize(path)
    riff = struct.unpack_from("<I", b, 4)[0]
    # find data chunk
    f = open(path, "rb"); f.seek(12); data_sz = None
    while True:
        h = f.read(8)
        if len(h) < 8: break
        cid, sz = h[:4], struct.unpack("<I", h[4:])[0]
        if cid == b"data": data_sz = sz; break
        f.seek(sz + (sz & 1), 1)
    return dict(file_size=size, riff_size=riff, riff_ok=(riff == size - 8), data_size=data_sz,
                data_dur_s=round(data_sz / 96000, 4) if data_sz not in (None, 0xFFFFFFFF) else data_sz)
res = []
for how, dur in (("q", 4.0), ("term", 4.0), ("int", 4.0)):
    out = os.path.join(HERE, f"t5_{how}.wav")
    log = open(os.path.join(HERE, f"t5_{how}.stderr.log"), "wb")
    t0 = time.monotonic()
    p = subprocess.Popen(argv(out), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
    if how == "q":
        extra = None
    time.sleep(dur)
    ts = time.monotonic()
    if how == "q": p.stdin.write(b"q\n"); p.stdin.flush()
    elif how == "term": p.send_signal(signal.SIGTERM)
    else: p.send_signal(signal.SIGINT)
    rc = p.wait(timeout=10); te = time.monotonic(); log.close()
    pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=duration,sample_rate,channels,codec_name",
                                    "-of", "json", out], capture_output=True, text=True).stdout)
    import soundfile as sf
    info = sf.info(out)
    res.append(dict(stop=how, rc=rc, stop_to_exit_s=round(te - ts, 3), wall_s=round(ts - t0, 3), header=hdr(out),
                    ffprobe=pr, soundfile=dict(frames=info.frames, sr=info.samplerate, dur=round(info.duration, 4))))
    time.sleep(0.3)
# bogus source: which physical source is used?
p = subprocess.Popen(argv(os.path.join(HERE, "t5_bogus.wav"), mic="alsa_input.does_not_exist"), stdin=subprocess.PIPE,
                     stdout=subprocess.DEVNULL, stderr=open(os.path.join(HERE, "t5_bogus.stderr.log"), "wb"))
time.sleep(1.5)
env = dict(os.environ, LC_ALL="C")
so = subprocess.run(["pactl", "list", "source-outputs"], capture_output=True, text=True, env=env).stdout
blk = [b for b in so.split("\n\n") if f'application.process.id = "{p.pid}"' in b]
lines = [l.strip() for l in (blk[0] if blk else "").splitlines() if l.strip().startswith(("Source:", "target.object", "node.name", "media.name"))]
sources = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True, env=env).stdout
p.stdin.write(b"q\n"); p.stdin.flush(); p.wait(timeout=10)
res.append(dict(bogus_source_output=lines, sources=[l.split("\t")[:2] for l in sources.splitlines()]))
print(json.dumps(res, indent=1))
