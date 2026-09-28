# Drift-free long-take conversion WITHOUT modifying Applio: own chunking (<=30 s, below Applio's 41 s internal
# chunk threshold) at quiet points on a 10 ms grid, 0.5 s context each side, each piece re-anchored at its exact
# original offset, 5 ms crossfade at joins. Uses only core.run_infer_script (same params as orochi_studio.py).
import os, sys, json, time, glob, re
import numpy as np, soundfile as sf
APPLIO_DIR = "/home/peras/orochi-ia-homenagem/Applio"
inp, outp, work = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(work, exist_ok=True)
MAX_PIECE_S, SEARCH_S, CTX_S, XF_S = 30.0, 3.0, 0.5, 0.005
def latest(k):
    f = glob.glob(os.path.join(APPLIO_DIR, "logs", k, f"{k}_*e_*s.pth"))
    return sorted(f, key=lambda p: int(re.search(rf"{k}_(\d+)e_", os.path.basename(p)).group(1)))[-1]
pth, idx = latest("silvio"), os.path.join(APPLIO_DIR, "logs", "silvio", "silvio.index")
x, sr = sf.read(inp); x = x if x.ndim == 1 else x.mean(1)
F = sr // 100  # 10 ms frame
nfr = len(x) // F
e = np.sqrt(np.mean(x[:nfr*F].reshape(nfr, F)**2, axis=1))
cuts = [0]
while (len(x)/sr) - cuts[-1]/100 > MAX_PIECE_S:
    nom = cuts[-1] + int((MAX_PIECE_S - SEARCH_S)*100)
    lo, hi = nom - int(SEARCH_S*100), min(nom + int(SEARCH_S*100), nfr - 1)
    cuts.append(lo + int(np.argmin(e[lo:hi])))
cuts_s = [c/100 for c in cuts] + [len(x)/sr]
t0 = time.time()
os.chdir(APPLIO_DIR); sys.path.insert(0, APPLIO_DIR)
import core
core.import_voice_converter()
t_load = time.time() - t0
OSR = None; pieces = []
t1 = time.time()
for i in range(len(cuts_s) - 1):
    a, b = cuts_s[i], cuts_s[i+1]
    ca, cb = max(0.0, a - CTX_S), min(len(x)/sr, b + CTX_S)
    pin, pout = os.path.join(work, f"p{i:03d}.wav"), os.path.join(work, f"p{i:03d}_out.wav")
    sf.write(pin, x[int(round(ca*sr)):int(round(cb*sr))], sr, subtype="PCM_16")
    os.chdir(APPLIO_DIR)
    core.run_infer_script(pitch=0, index_rate=0.75, volume_envelope=1.0, protect=0.33, f0_method="rmvpe",
        input_path=pin, output_path=pout, pth_path=pth, index_path=idx, split_audio=False, f0_autotune=False,
        f0_autotune_strength=1.0, proposed_pitch=False, proposed_pitch_threshold=155.0, clean_audio=False,
        clean_strength=0.5, export_format="WAV", embedder_model="contentvec")
    y, OSR = sf.read(pout); pieces.append((a, b, ca, y)); os.remove(pin); os.remove(pout)
t_conv = time.time() - t1
N = int(round(len(x)/sr*OSR)); out = np.zeros(N); xf = int(XF_S*OSR)
for i, (a, b, ca, y) in enumerate(pieces):
    s0 = int(round(a*OSR)); s1 = int(round(b*OSR)); off = int(round((a - ca)*OSR))
    ext = xf if i < len(pieces) - 1 else 0          # overlap into next slot for crossfade
    seg = y[off: off + (s1 - s0) + ext]
    seg = np.pad(seg, (0, max(0, (s1 - s0) + ext - len(seg))))
    L = len(seg)
    if i > 0 and xf:
        ramp = np.linspace(0.0, 1.0, xf)
        out[s0:s0+xf] = out[s0:s0+xf]*(1 - ramp) + seg[:xf]*ramp
        out[s0+xf:s0+L] = seg[xf:L]
    else:
        out[s0:s0+L] = seg
sf.write(outp, out, OSR, subtype="PCM_16")
print(json.dumps({"pieces": len(pieces), "cuts_s": [round(c, 2) for c in cuts_s], "load_s": round(t_load, 2),
                  "convert_s": round(t_conv, 2), "in_dur": len(x)/sr, "out_dur": N/OSR, "out_sr": OSR}))
