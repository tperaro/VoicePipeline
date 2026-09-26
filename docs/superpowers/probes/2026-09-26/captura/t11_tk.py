# synthetic frames only (no camera data)
import time, os, tkinter as tk
from PIL import Image, ImageTk
W, H = 480, 270
frames = [bytes([(i * 7) % 256]) * (W * H * 3) for i in range(10)]
root = tk.Tk(); root.withdraw()
N = 150
t = time.perf_counter()
for i in range(N):
    img = Image.frombuffer("RGB", (W, H), frames[i % 10], "raw", "RGB", 0, 1)
    ph = ImageTk.PhotoImage(img)
a = (time.perf_counter() - t) / N * 1000
ph2 = ImageTk.PhotoImage(Image.new("RGB", (W, H)))
t = time.perf_counter()
for i in range(N):
    ph2.paste(Image.frombuffer("RGB", (W, H), frames[i % 10], "raw", "RGB", 0, 1))
b = (time.perf_counter() - t) / N * 1000
hdr = f"P6 {W} {H} 255\n".encode()
t = time.perf_counter()
for i in range(N):
    p3 = tk.PhotoImage(data=hdr + frames[i % 10], format="PPM")
c = (time.perf_counter() - t) / N * 1000
p4 = tk.PhotoImage(width=W, height=H)
t = time.perf_counter()
for i in range(N):
    p4.configure(data=hdr + frames[i % 10], format="PPM")
d = (time.perf_counter() - t) / N * 1000
print(f"new ImageTk.PhotoImage per frame: {a:.2f} ms | ImageTk paste into one PhotoImage: {b:.2f} ms | tk.PhotoImage(PPM data) new: {c:.2f} ms | PhotoImage.configure(PPM data) reuse: {d:.2f} ms")
root.destroy()
