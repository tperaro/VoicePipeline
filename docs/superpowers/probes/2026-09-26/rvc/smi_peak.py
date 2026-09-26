import sys, json, datetime
smi, rep = sys.argv[1], sys.argv[2]
rows=[]
for l in open(smi):
    try:
        ts, mem = [x.strip() for x in l.split(",")]
        t = datetime.datetime.strptime(ts, "%Y/%m/%d %H:%M:%S.%f").timestamp(); rows.append((t, int(mem)))
    except Exception: pass
r = json.load(open(rep))
base = rows[0][1]
print("baseline_MiB", base, "global_peak_MiB", max(m for _, m in rows), "samples", len(rows))
for j in r["jobs"]:
    w = [m for t, m in rows if j["t_start"] - 0.2 <= t <= j["t_end"] + 0.2]
    print(j["tag"], "smi_peak_total_MiB", max(w) if w else None, "delta_vs_baseline", (max(w) - base) if w else None)
