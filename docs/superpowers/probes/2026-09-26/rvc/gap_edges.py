# Direct drift measurement: locate the exact-zero gaps inserted in the input (0.3 s) and find the same gap edges in the output.
import numpy as np, soundfile as sf, json, sys
def zero_runs(x, sr, min_s=0.25):
    z = np.concatenate([[0], (x == 0).astype(np.int8), [0]]); d = np.diff(z)
    st, en = np.where(d == 1)[0], np.where(d == -1)[0]
    return [(s/sr, e/sr) for s, e in zip(st, en) if (e - s) >= min_s*sr and s > 0 and e < len(x)]
def env(x, sr):  # 2 ms window, 1 ms hop, dBFS
    w, h = int(sr*0.002), int(sr*0.001); n = (len(x)-w)//h + 1
    idx = np.arange(w)[None, :] + h*np.arange(n)[:, None]
    return 20*np.log10(np.sqrt(np.mean(x[idx]**2, axis=1)) + 1e-9)
def edges_out(eo, g0, g1, thr=-50):
    # gap in output: frames near expected gap below thr; find last loud frame before, first loud after
    lo, hi = int(g0*1000) - 150, int(g1*1000) + 150
    seg = eo[lo:hi]; quiet = seg < thr
    # longest quiet run
    best = (0, 0); s = None
    for i, q in enumerate(np.append(quiet, False)):
        if q and s is None: s = i
        if not q and s is not None:
            if i - s > best[1] - best[0]: best = (s, i)
            s = None
    return (lo + best[0])/1000, (lo + best[1])/1000
res = {}
for tag, src in [("b_90s", "b_90s"), ("b_split", "b_90s"), ("c_300s", "c_300s"), ("b_anchored", "b_90s"), ("c_anchored", "c_300s")]:
    try:
        xi, si = sf.read(f"{sys.argv[1]}/in/{src}.wav"); xo, so = sf.read(f"{sys.argv[1]}/out/{tag}.wav")
    except Exception: continue
    eo = env(xo, so); rows = []
    for g0, g1 in zero_runs(xi, si):
        o0, o1 = edges_out(eo, g0, g1)
        rows.append({"gap_in_s": [round(g0, 3), round(g1, 3)], "gap_out_s": [o0, o1],
                     "start_edge_err_ms": round((o0 - g0)*1000, 1), "end_edge_err_ms": round((o1 - g1)*1000, 1)})
    res[tag] = rows
    print(tag, [(r["gap_in_s"][1], r["start_edge_err_ms"], r["end_edge_err_ms"]) for r in rows])
json.dump(res, open(f"{sys.argv[1]}/gap_edges.json", "w"), indent=1)
