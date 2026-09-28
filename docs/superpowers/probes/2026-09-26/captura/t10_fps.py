import fcntl, json, os, struct, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import build_argv, Reader, HERE
def IOWR(nr, size): return (3 << 30) | (size << 16) | (ord('V') << 8) | nr
CID = 0x9a0903  # V4L2_CID_EXPOSURE_AUTO_PRIORITY ("Exposure, Dynamic Framerate")
def gctrl(fd):
    c = bytearray(struct.pack("<Ii", CID, 0)); fcntl.ioctl(fd, IOWR(27, 8), c); return struct.unpack("<Ii", c)[1]
def sctrl(fd, v):
    c = bytearray(struct.pack("<Ii", CID, v)); fcntl.ioctl(fd, IOWR(28, 8), c)
def cpu_ticks(pid):
    s = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split(); return int(s[11]) + int(s[12])
def rec(tag):
    log = open(os.path.join(HERE, f"{tag}.stderr.log"), "wb")
    p = subprocess.Popen(build_argv(os.path.join(HERE, f"{tag}.mkv"), avoid_neg="make_zero"), stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=log, bufsize=0)
    r = Reader(p.stdout); r.start()
    time.sleep(1.5); c0 = cpu_ticks(p.pid); t0 = time.monotonic(); time.sleep(4); c1 = cpu_ticks(p.pid); t1 = time.monotonic()
    rss = int([l for l in open(f"/proc/{p.pid}/status") if l.startswith("VmRSS")][0].split()[1]) // 1024
    p.stdin.write(b"q\n"); p.stdin.flush(); p.wait(timeout=10); log.close()
    return dict(cpu_pct_of_one_core=round((c1 - c0) / os.sysconf("SC_CLK_TCK") / (t1 - t0) * 100, 1), rss_mb=rss)
fd = os.open("/dev/video0", os.O_RDWR | os.O_NONBLOCK)
orig = gctrl(fd); out = {"original_value": orig}
try:
    out["with_orig"] = rec("t10_prio_orig")
    time.sleep(0.5)
    sctrl(fd, 0); out["set_to"] = gctrl(fd)
    out["with_0"] = rec("t10_prio_0")
finally:
    sctrl(fd, orig); out["restored_value"] = gctrl(fd); os.close(fd)
print(json.dumps(out))
