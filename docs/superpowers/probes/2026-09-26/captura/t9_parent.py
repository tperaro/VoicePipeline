import ctypes, os, signal, subprocess, sys, time, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import build_argv, Reader, HERE
use_pdeath = sys.argv[1] == "1"; tag = sys.argv[2]
libc = ctypes.CDLL("libc.so.6", use_errno=True)
def pdeath():
    libc.prctl(1, signal.SIGTERM, 0, 0, 0)  # PR_SET_PDEATHSIG = 1
p = subprocess.Popen(build_argv(os.path.join(HERE, f"{tag}.mkv"), avoid_neg="make_zero"),
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=open(os.path.join(HERE, f"{tag}.stderr.log"), "wb"), bufsize=0,
                     preexec_fn=pdeath if use_pdeath else None)
print(p.pid, flush=True)
r = Reader(p.stdout); r.start()
time.sleep(3)
os._exit(1)  # simulate crash: no cleanup
