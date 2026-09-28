import fcntl, os, struct
def IOWR(nr, size): return (3 << 30) | (size << 16) | (ord('V') << 8) | nr
fd = os.open("/dev/video0", os.O_RDWR | os.O_NONBLOCK)
MJPG = struct.unpack("<I", b"MJPG")[0]; YUYV = struct.unpack("<I", b"YUYV")[0]
for fmt, name in ((MJPG, "MJPG"), (YUYV, "YUYV")):
    for w, h in ((1280, 720), (800, 600), (640, 480)):
        ivs = []
        for i in range(20):
            buf = bytearray(struct.pack("<5I8I", i, fmt, w, h, 0, *([0] * 8)))
            try: fcntl.ioctl(fd, IOWR(75, 52), buf)
            except OSError: break
            v = struct.unpack("<5I8I", buf)
            if v[4] == 1: ivs.append(f"{v[6]}/{v[5]}fps" if v[5] == 1 else f"{v[6]/v[5]:.1f}fps")
            else: ivs.append(f"type{v[4]}")
        print(name, f"{w}x{h}", ivs)
# controls
cid = 0x80000000
while True:
    buf = bytearray(struct.pack("<II32siiiiII", cid, 0, b"", 0, 0, 0, 0, 0, 0) + b"\0" * 0)
    buf = bytearray(68); struct.pack_into("<I", buf, 0, cid)
    try: fcntl.ioctl(fd, IOWR(36, 68), buf)
    except OSError: break
    id_, typ = struct.unpack_from("<II", buf, 0); name = bytes(buf[8:40]).split(b"\0")[0].decode()
    mn, mx, st, df, flags = struct.unpack_from("<iiiiI", buf, 40)
    cid = id_ | 0x80000000
    if typ == 6: continue  # ctrl class
    c = bytearray(struct.pack("<Ii", id_, 0))
    try: fcntl.ioctl(fd, IOWR(27, 8), c); val = struct.unpack("<Ii", c)[1]
    except OSError as e: val = f"err{e.errno}"
    print(f"  {hex(id_)} {name!r} type={typ} min={mn} max={mx} def={df} cur={val} flags={hex(flags)}")
os.close(fd)
