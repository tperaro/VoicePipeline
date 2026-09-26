import json, os, signal, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import build_argv, Reader, FRAME, HERE

def start(argv, logname):
    log = open(os.path.join(HERE, logname), "wb")
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, bufsize=0)
    r = Reader(p.stdout); r.start()
    return p, r, log, time.monotonic()

def first_frame(r, t0, timeout=8):
    while r.first_t is None and time.monotonic() - t0 < timeout:
        time.sleep(0.005)
    return r.first_t

res = []
for how in ("q", "term", "int"):
    pv_argv = build_argv(None, record=False)
    p, r, log, t0 = start(pv_argv, f"t3_preview_{how}.stderr.log")
    ff = first_frame(r, t0)
    time.sleep(3)
    ts = time.monotonic()
    if how == "q":
        p.stdin.write(b"q\n"); p.stdin.flush()
    elif how == "term":
        p.send_signal(signal.SIGTERM)
    else:
        p.send_signal(signal.SIGINT)
    rc = p.wait(timeout=10); te = time.monotonic(); log.close()
    # immediately open a full recording process
    rec_argv = build_argv(os.path.join(HERE, f"t3_rec_after_{how}.mkv"), avoid_neg="make_zero")
    p2, r2, log2, t2 = start(rec_argv, f"t3_rec_after_{how}.stderr.log")
    ff2 = first_frame(r2, t2)
    time.sleep(2)
    p2.stdin.write(b"q\n"); p2.stdin.flush(); rc2 = p2.wait(timeout=10); log2.close()
    res.append(dict(stop=how, preview_only_first_frame_s=round(ff - t0, 3) if ff else None,
                    preview_frames=r.count, stop_to_exit_s=round(te - ts, 3), rc=rc,
                    new_rec_first_frame_after_prev_exit_s=round(ff2 - te, 3) if ff2 else None,
                    new_rec_first_frame_after_stop_cmd_s=round(ff2 - ts, 3) if ff2 else None,
                    new_rec_rc=rc2, new_rec_frames=r2.count))
    time.sleep(0.5)
print(json.dumps(res, indent=1))
