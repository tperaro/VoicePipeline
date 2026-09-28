import json, os, subprocess, sys, time, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import build_argv, Reader, HERE
def tail(b): return [l for l in b.decode(errors="replace").replace("\r", "\n").splitlines() if l.strip() and not l.startswith("frame=")][-4:]
def run_fail(name, argv):
    t = time.monotonic()
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    err = bytearray()
    def drain(f, keep):
        while True:
            c = f.read(65536)
            if not c: return
            if keep: err.extend(c)
    th = [threading.Thread(target=drain, args=(p.stdout, False), daemon=True),
          threading.Thread(target=drain, args=(p.stderr, True), daemon=True)]
    [x.start() for x in th]
    note = None
    try:
        rc = p.wait(timeout=6)
    except subprocess.TimeoutExpired:
        so_ = subprocess.run(["pactl", "list", "source-outputs"], capture_output=True, text=True).stdout
        srcs = [l.strip() for l in so_.splitlines() if l.strip().startswith(("Source:", "Source Output", "application.process.id", "target.object", "node.target", "media.name"))]
        note = dict(still_running_after_6s=True, source_outputs=srcs)
        p.stdin.write(b"q\n"); p.stdin.flush()
        rc = p.wait(timeout=10)
    [x.join(2) for x in th]
    return dict(rc=rc, elapsed_s=round(time.monotonic() - t, 3), stderr_tail=tail(bytes(err)), note=note)
out = {}
hold = subprocess.Popen(build_argv(None, record=False), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=open(os.path.join(HERE, "t4_holder.stderr.log"), "wb"), bufsize=0)
r = Reader(hold.stdout); r.start()
while r.first_t is None: time.sleep(0.01)
try:
    out["busy_camera"] = run_fail("busy", build_argv(os.path.join(HERE, "t4_busy.mkv"), avoid_neg="make_zero"))
finally:
    hold.stdin.write(b"q\n"); hold.stdin.flush(); hold.wait(timeout=10)
time.sleep(0.3)
out["bad_pulse_source"] = run_fail("badmic", build_argv(os.path.join(HERE, "t4_badmic.mkv"), avoid_neg="make_zero", mic="alsa_input.does_not_exist"))
time.sleep(0.3)
out["video1_metadata_node"] = run_fail("v1", build_argv(os.path.join(HERE, "t4_v1.mkv"), avoid_neg="make_zero", cam="/dev/video1"))
out["missing_video9"] = run_fail("v9", build_argv(os.path.join(HERE, "t4_v9.mkv"), avoid_neg="make_zero", cam="/dev/video9"))
print(json.dumps(out, indent=1, ensure_ascii=False))
