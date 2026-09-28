import numpy as np, soundfile as sf, json, sys, os
P = os.path.dirname(os.path.abspath(__file__))
def env_db(x, sr, hop_ms, win_ms=10.0):
    hop = int(round(sr*hop_ms/1000)); win = int(round(sr*win_ms/1000))
    n = (len(x) - win)//hop + 1
    idx = np.arange(win)[None, :] + hop*np.arange(n)[:, None]
    rms = np.sqrt(np.mean(x[idx]**2, axis=1) + 1e-12)
    db = 20*np.log10(rms); db = np.maximum(db, db.max() - 60)
    return db
def xcorr_lag(ei, eo, i0, i1, maxlag):
    # lag>0 means output is LATE vs input
    a = ei[i0:i1]; a = a - a.mean()
    best, bl = -2, 0
    for L in range(-maxlag, maxlag+1):
        j0, j1 = i0+L, i1+L
        if j0 < 0 or j1 > len(eo): continue
        b = eo[j0:j1] - eo[j0:j1].mean()
        c = float(np.dot(a, b)/(np.linalg.norm(a)*np.linalg.norm(b)+1e-9))
        if c > best: best, bl = c, L
    return bl, best
def onset_offset(x, sr, thr_rel_db=-35):
    e = env_db(x, sr, 1.0); on = np.where(e > e.max()+thr_rel_db)[0]
    return on[0]/1000.0, (on[-1]+10)/1000.0
def analyze(inp, out, windows_s):
    xi, si = sf.read(inp); xo, so = sf.read(out)
    r = {"in": os.path.basename(inp), "out": os.path.basename(out), "in_sr": si, "out_sr": so,
         "in_samples": len(xi), "out_samples": len(xo), "in_dur_s": round(len(xi)/si, 6), "out_dur_s": round(len(xo)/so, 6),
         "len_diff_ms": round((len(xo)/so - len(xi)/si)*1000, 3)}
    for hop_ms, key in ((10.0, "lags_10ms_frames"), (1.0, "lags_1ms_hop")):
        ei, eo = env_db(xi, si, hop_ms), env_db(xo, so, hop_ms)
        per = 1000/hop_ms; out_l = []
        for name, (t0, t1) in windows_s.items():
            t1 = min(t1, len(xi)/si - 0.35, len(xo)/so - 0.35)
            L, c = xcorr_lag(ei, eo, int(t0*per), int(t1*per), int(300/hop_ms))
            out_l.append({"win": name, "t0": round(t0,2), "t1": round(t1,2), "lag_ms": L*hop_ms, "corr": round(c, 3)})
        r[key] = out_l
    oi, fi = onset_offset(xi, si); oo, fo = onset_offset(xo, so)
    r["onset_in_s"], r["onset_out_s"], r["offset_in_s"], r["offset_out_s"] = oi, oo, fi, fo
    r["lead_silence_out_rms_dbfs"] = round(20*np.log10(np.sqrt(np.mean(xo[:int(0.9*so)]**2))+1e-12), 1)
    return r
O = f"{P}/out"; I = f"{P}/in"
res = {}
wa = {"start": (0.0, 10.0), "middle": (7.0, 17.0), "end": (13.3, 23.3), "whole": (0.0, 23.3)}
dur_b = 88.964
wb = {"start": (0.0, 10.0), "middle": (39.5, 49.5), "end": (dur_b-10.4, dur_b)}
for k in range(0, 9): wb[f"t{k*10:02d}"] = (k*10.0, k*10.0+10.0)
wc = {"start": (0.0, 10.0), "middle": (145.0, 155.0), "end": (289.6, 300.0)}
for k in range(0, 30): wc[f"t{k*10:03d}"] = (k*10.0, k*10.0+10.0)
jobs = [("a_cold", "a_pad", wa), ("a_warm", "a_pad", wa), ("a_split", "a_pad", wa),
        ("b_90s", "b_90s", wb), ("b_split", "b_90s", wb), ("c_300s", "c_300s", wc)]
for tag, src, w in jobs:
    if os.path.exists(f"{O}/{tag}.wav"): res[tag] = analyze(f"{I}/{src}.wav", f"{O}/{tag}.wav", w)
R = "/home/peras/orochi-ia-homenagem/recordings"
res["app_existing_take_20260923_150423"] = analyze(f"{R}/take_20260923_150423_boosted.wav", f"{R}/take_20260923_150423_silvio.wav",
    {"start": (0.0, 7.0), "middle": (7.0, 14.0), "end": (14.0, 21.4)})
# determinism check a_cold vs a_warm
xa, _ = sf.read(f"{O}/a_cold.wav"); xb, _ = sf.read(f"{O}/a_warm.wav")
res["a_cold_vs_warm_identical"] = bool(len(xa) == len(xb) and np.allclose(xa, xb, atol=1e-4))
res["a_cold_vs_warm_maxabsdiff"] = float(np.max(np.abs(xa - xb))) if len(xa) == len(xb) else None
json.dump(res, open(f"{P}/analysis_raw.json", "w"), indent=1)
for k, v in res.items():
    if not isinstance(v, dict): print(k, v); continue
    print(f"== {k}: in {v['in_dur_s']}s@{v['in_sr']} ({v['in_samples']}) -> out {v['out_dur_s']}s@{v['out_sr']} ({v['out_samples']}) diff {v['len_diff_ms']} ms | onset {v['onset_in_s']}->{v['onset_out_s']} offset {v['offset_in_s']}->{v['offset_out_s']} | lead-sil {v['lead_silence_out_rms_dbfs']} dBFS")
    print("   10ms:", [(l['win'], l['lag_ms'], l['corr']) for l in v['lags_10ms_frames']])
    print("   1ms :", [(l['win'], l['lag_ms'], l['corr']) for l in v['lags_1ms_hop']])
