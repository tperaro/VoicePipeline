import json, os, signal, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import build_argv, Reader, HERE
def go(tag, pause_after, pause_for, total, close_stdout_at=None):
    out = os.path.join(HERE, f"{tag}.mkv")
    log = open(os.path.join(HERE, f"{tag}.stderr.log"), "wb")
    t0 = time.monotonic()
    p = subprocess.Popen(build_argv(out, avoid_neg="make_zero"), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, bufsize=0)
    r = Reader(p.stdout, pause_after=pause_after, pause_for=pause_for); r.start()
    closed = False; mid = {}
    while time.monotonic() - t0 < total and p.poll() is None:
        if close_stdout_at and not closed and time.monotonic() - t0 > close_stdout_at:
            p.stdout.close(); closed = True   # parent abandons preview pipe -> ffmpeg gets EPIPE
            mid["closed_at_s"] = round(time.monotonic() - t0, 2)
        time.sleep(0.05)
    mid["alive_at_stop"] = p.poll() is None
    mid["rc_before_stop"] = p.poll()
    steps = []
    ts = time.monotonic()
    if p.poll() is None:
        p.stdin.write(b"q\n"); p.stdin.flush(); steps.append("q")
        try: p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.send_signal(signal.SIGTERM); steps.append("SIGTERM")
            try: p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill(); steps.append("SIGKILL"); p.wait()
    te = time.monotonic(); log.close()
    return dict(tag=tag, rc=p.returncode, steps=steps, stop_to_exit_s=round(te - ts, 3), preview_frames=r.count, **mid)
res = [go("t6a_pause3s", pause_after=20, pause_for=3.0, total=9.0),
       go("t6b_reader_dead", pause_after=20, pause_for=1e6, total=7.0),
       go("t6c_reader_dead_then_close", pause_after=20, pause_for=1e6, total=8.0, close_stdout_at=4.0)]
print(json.dumps(res, indent=1))
