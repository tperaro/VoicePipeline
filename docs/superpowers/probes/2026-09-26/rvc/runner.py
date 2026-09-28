# Mimics orochi_studio.py conversion path exactly; writes only to scratch.
import os, sys, json, time, glob, re
APPLIO_DIR = "/home/peras/orochi-ia-homenagem/Applio"
jobs = json.loads(sys.argv[1]); report = sys.argv[2]
def find_latest_checkpoint(k):
    files = glob.glob(os.path.join(APPLIO_DIR, "logs", k, f"{k}_*e_*s.pth"))
    ep = lambda p: int(re.search(rf"{k}_(\d+)e_", os.path.basename(p)).group(1))
    return sorted(files, key=ep)[-1]
pth = find_latest_checkpoint("silvio"); idx = os.path.join(APPLIO_DIR, "logs", "silvio", "silvio.index")
t0 = time.time()
os.chdir(APPLIO_DIR); sys.path.insert(0, APPLIO_DIR)
import core
vc = core.import_voice_converter()
import torch
t_import = time.time() - t0
res = {"pth": pth, "import_and_init_s": round(t_import, 3), "jobs": []}
for j in jobs:
    torch.cuda.synchronize(); torch.cuda.reset_peak_memory_stats()
    loaded_before = vc.loaded_model; net_before = id(vc.net_g)
    ts = time.time()
    os.chdir(APPLIO_DIR)
    core.run_infer_script(pitch=0, index_rate=0.75, volume_envelope=1.0, protect=0.33, f0_method="rmvpe",
        input_path=j["in"], output_path=j["out"], pth_path=pth, index_path=idx, split_audio=j["split"],
        f0_autotune=False, f0_autotune_strength=1.0, proposed_pitch=False, proposed_pitch_threshold=155.0,
        clean_audio=False, clean_strength=0.5, export_format="WAV", embedder_model="contentvec")
    torch.cuda.synchronize(); te = time.time()
    res["jobs"].append({**j, "t_start": ts, "t_end": te, "wall_s": round(te - ts, 3),
        "torch_max_alloc_MiB": round(torch.cuda.max_memory_allocated() / 2**20),
        "torch_max_reserved_MiB": round(torch.cuda.max_memory_reserved() / 2**20),
        "model_was_loaded_before": loaded_before == pth, "net_g_same_object": net_before == id(vc.net_g)})
    print("JOBDONE", j["tag"], res["jobs"][-1]["wall_s"], flush=True)
json.dump(res, open(report, "w"), indent=1)
