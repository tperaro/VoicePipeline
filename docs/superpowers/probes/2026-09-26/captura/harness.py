#!/usr/bin/env python3
"""Probe harness: single ffmpeg process = webcam(v4l2 mjpeg copy) + pulse mic -> MKV
plus a rawvideo preview tap on stdout. Frames are only counted, never saved/viewed."""
import json, os, signal, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
CAM = "/dev/video0"
MIC = "alsa_input.usb-Generalplus_Usb_Audio_Device-00.mono-fallback"
PW, PH = 480, 270
FRAME = PW * PH * 3


def build_argv(out_file, mono2abs=True, copyts=True, avoid_neg=None, preview=True,
               record=True, start_at_zero=False, mic=MIC, cam=CAM):
    a = ["ffmpeg", "-hide_banner", "-loglevel", "info", "-y"]
    if copyts:
        a += ["-copyts"]
        if start_at_zero:
            a += ["-start_at_zero"]
    # input 0: webcam
    a += ["-f", "v4l2", "-input_format", "mjpeg", "-video_size", "1280x720",
          "-framerate", "30"]
    if mono2abs:
        a += ["-ts", "mono2abs"]
    a += ["-thread_queue_size", "512", "-i", cam]
    # input 1: mic
    if record:
        a += ["-f", "pulse", "-thread_queue_size", "1024", "-sample_rate", "48000",
              "-channels", "1", "-i", mic]
    # output A: file
    if record:
        a += ["-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "pcm_s16le"]
        if avoid_neg:
            a += ["-avoid_negative_ts", avoid_neg]
        a += ["-f", "matroska", out_file]
    # output B: preview
    if preview:
        a += ["-map", "0:v", "-vf", f"scale={PW}:-2,fps=15", "-pix_fmt", "rgb24",
              "-f", "rawvideo", "pipe:1"]
    return a


class Reader(threading.Thread):
    def __init__(self, stream, pause_after=None, pause_for=0.0):
        super().__init__(daemon=True)
        self.stream = stream
        self.latest = None
        self.count = 0
        self.first_t = None
        self.partial_bytes = 0
        self.pause_after = pause_after
        self.pause_for = pause_for
        self.pauses_done = False
        self.lock = threading.Lock()

    def run(self):
        f = self.stream
        while True:
            if (self.pause_after is not None and not self.pauses_done
                    and self.count >= self.pause_after):
                self.pauses_done = True
                time.sleep(self.pause_for)  # simulate a stalled consumer
            buf = bytearray()
            while len(buf) < FRAME:
                chunk = f.read(FRAME - len(buf))
                if not chunk:
                    self.partial_bytes = len(buf)
                    return
                buf += chunk
            with self.lock:
                self.latest = bytes(buf)  # only latest kept (never written anywhere)
                self.count += 1
                if self.first_t is None:
                    self.first_t = time.monotonic()


def run(tag, dur=6.0, stop="q", pause_after=None, pause_for=0.0, **kw):
    out = os.path.join(HERE, f"{tag}.mkv")
    argv = build_argv(out, **kw)
    log = open(os.path.join(HERE, f"{tag}.stderr.log"), "wb")
    t0 = time.monotonic()
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE if kw.get("preview", True) else subprocess.DEVNULL,
                         stderr=log, bufsize=0)
    rd = None
    if kw.get("preview", True):
        rd = Reader(p.stdout, pause_after, pause_for)
        rd.start()
    # wait dur or early exit
    while time.monotonic() - t0 < dur and p.poll() is None:
        time.sleep(0.05)
    early = p.poll()
    t_stop = time.monotonic()
    if early is None:
        if stop == "q":
            try:
                p.stdin.write(b"q\n"); p.stdin.flush()
            except BrokenPipeError:
                pass
        elif stop == "term":
            p.send_signal(signal.SIGTERM)
        elif stop == "int":
            p.send_signal(signal.SIGINT)
        try:
            rc = p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill(); rc = p.wait(); rc = f"KILLED({rc})"
    else:
        rc = early
    t_exit = time.monotonic()
    if rd:
        rd.join(timeout=2)
    log.close()
    res = {
        "tag": tag, "argv": argv, "rc": rc, "early_exit": early is not None,
        "run_s": round(t_stop - t0, 3), "stop_to_exit_s": round(t_exit - t_stop, 3),
        "preview_frames": rd.count if rd else None,
        "first_preview_frame_after_s": round(rd.first_t - t0, 3) if rd and rd.first_t else None,
        "preview_fps_measured": round(rd.count / (t_stop - rd.first_t), 2) if rd and rd.first_t else None,
        "trailing_partial_bytes": rd.partial_bytes if rd else None,
        "frame_bytes": FRAME,
    }
    return res


if __name__ == "__main__":
    spec = json.loads(sys.argv[1])
    print(json.dumps(run(**spec)))
