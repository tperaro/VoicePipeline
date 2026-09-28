import sys, soundfile as sf, numpy as np
for p in sys.argv[1:]:
    x, sr = sf.read(p, dtype="float32")
    if x.ndim > 1: x = x[:, 0]
    on = int(np.argmax(np.abs(x) > 0.3))
    print(f"{p}: sr={sr} len={len(x)} ({len(x)/sr:.5f}s) beep_onset={on} ({on/sr:.5f}s)")
