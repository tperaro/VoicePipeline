#!/usr/bin/env python3
"""ffprobe analysis of a take (no frame decoding to disk/images)."""
import json, os, subprocess, sys

def probe(path):
    j = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-count_packets", "-show_streams", "-show_format",
         "-of", "json", path], capture_output=True, text=True).stdout)
    out = {"format": {k: j["format"].get(k) for k in ("start_time", "duration", "size", "format_name")}}
    for s in j["streams"]:
        k = s["codec_type"]
        d = {x: s.get(x) for x in ("codec_name", "start_time", "duration", "nb_frames",
                                     "nb_read_packets", "avg_frame_rate", "r_frame_rate",
                                     "sample_rate", "width", "height", "pix_fmt", "time_base")}
        d["tags"] = s.get("tags")
        # packet pts list
        pk = subprocess.run(["ffprobe", "-v", "error", "-select_streams", str(s["index"]),
                             "-show_entries", "packet=pts_time,duration_time,size", "-of", "csv=p=0", path],
                            capture_output=True, text=True).stdout.split()
        pts = [float(l.split(",")[0]) for l in pk if l.split(",")[0] not in ("", "N/A")]
        if pts:
            d["first_pts"] = pts[0]; d["last_pts"] = pts[-1]; d["n_pkts"] = len(pts)
            span = pts[-1] - pts[0]
            d["pts_span_s"] = round(span, 4)
            if k == "video" and span > 0:
                d["real_fps"] = round((len(pts) - 1) / span, 3)
                gaps = [b - a for a, b in zip(pts, pts[1:])]
                d["max_gap_ms"] = round(max(gaps) * 1000, 1)
                d["min_gap_ms"] = round(min(gaps) * 1000, 1)
                d["gaps_over_50ms"] = sum(1 for g in gaps if g > 0.05)
            if k == "audio":
                gaps = [b - a for a, b in zip(pts, pts[1:])]
                d["audio_pkt_gap_ms_max"] = round(max(gaps) * 1000, 2) if gaps else None
        if k == "audio":
            raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-map", f"0:{s['index']}",
                                  "-f", "s16le", "-ac", "1", "pipe:1"], capture_output=True).stdout
            d["samples"] = len(raw) // 2
            d["samples_dur_s"] = round(len(raw) / 2 / 48000, 4)
        out[k] = d
    if "video" in out and "audio" in out:
        try:
            out["audio_minus_video_start_s"] = round(float(out["audio"]["start_time"]) - float(out["video"]["start_time"]), 4)
        except Exception:
            pass
        if "first_pts" in out["audio"] and "first_pts" in out["video"]:
            out["audio_minus_video_first_pts_s"] = round(out["audio"]["first_pts"] - out["video"]["first_pts"], 4)
            out["audio_end_minus_video_end_s"] = round(
                (out["audio"]["first_pts"] + out["audio"]["samples_dur_s"]) - out["video"]["last_pts"], 4)
    return out

if __name__ == "__main__":
    for p in sys.argv[1:]:
        r = probe(p)
        with open(p + ".probe.json", "w") as f:
            json.dump(r, f, indent=1)
        print(json.dumps(r, indent=1))
