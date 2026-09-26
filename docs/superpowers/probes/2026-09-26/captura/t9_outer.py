import json, os, signal, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); PY = sys.executable
res = []
for use, tag in (("0", "t9_orphan_nopdeath"), ("1", "t9_orphan_pdeath")):
    par = subprocess.Popen([PY, os.path.join(HERE, "t9_parent.py"), use, tag], stdout=subprocess.PIPE, text=True)
    pid = int(par.stdout.readline()); par.wait(); t_dead = time.monotonic()
    alive = lambda: os.path.exists(f"/proc/{pid}") and open(f"/proc/{pid}/stat").read().split()[2] != "Z"
    while alive() and time.monotonic() - t_dead < 5: time.sleep(0.05)
    still = alive(); t_exit = round(time.monotonic() - t_dead, 3)
    if still:
        os.kill(pid, signal.SIGTERM)
        for _ in range(100):
            if not alive(): break
            time.sleep(0.05)
        if alive(): os.kill(pid, signal.SIGKILL)
    res.append(dict(tag=tag, pdeathsig=use == "1", ffmpeg_alive_5s_after_parent_death=still,
                    exit_after_parent_death_s=None if still else t_exit))
    time.sleep(1)
print(json.dumps(res, indent=1))
