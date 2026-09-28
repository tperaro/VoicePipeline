"""Linha do tempo da tomada: relogio do mic ajustado aos pts e filtro que alinha o audio a ancora do video."""

import os
import subprocess
from dataclasses import dataclass

import numpy as np

from studio import procs

GAP_S = 0.030            # salto de residuo acima disso = buraco no audio
FIT_FROM_S = 2.0         # ajuste usa pacotes depois de 2 s (o comeco ainda esta assentando)
SHORT_TAKE_S = 4.0
SHORT_FIT_FROM_S = 0.5   # tomadas < 4 s: pacotes depois de 0,5 s
MIN_FIT_PKTS = 5         # menos que isso: usa todos os pacotes
MAX_DRIFT = 1e-3         # inclinacao acima de 1000 ppm nao e deriva de mic: mantem a taxa nominal
DRIFT_FIX_S = 0.005      # deriva acumulada acima de 5 ms -> asetrate
ASYNC = "aresample=async=1:min_hard_comp=0.03:first_pts=0"
OUT_SR = 48000
EXTRACT_TIMEOUT_S = 60.0


@dataclass
class VideoInfo:
    w: int
    h: int
    ancora_pts: float
    n_frames: int
    fps_medido: float


@dataclass
class AudioFit:
    inicio: float        # tempo real (pts) da amostra 0
    taxa_real: float     # amostras por segundo do relogio do sistema
    gaps: int
    residuo_ms: float
    duracao: float       # segundos de amostras


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_packets(path: str, stream: str) -> list[tuple[float, float, int]]:
    # (pts_time, duration_time, size) na ordem do arquivo, sem decodificar
    if stream not in ("v", "a"):
        raise ValueError(f"stream inválido: {stream!r}")
    data = procs.ffprobe_json(path, "-select_streams", f"{stream}:0",
                              "-show_entries", "packet=pts_time,duration_time,size")
    out = []
    for pk in data.get("packets", []):
        pts = _num(pk.get("pts_time"))
        if pts is None:
            continue
        out.append((pts, _num(pk.get("duration_time")) or 0.0, int(pk.get("size") or 0)))
    return out


def _video_info(path: str, info: dict, fps_out: int) -> VideoInfo:
    name = os.path.basename(path)
    v = info["video"]
    if not v:
        raise procs.ProcError(f"{name} não tem vídeo")
    pk = read_packets(path, "v")
    if not pk:
        raise procs.ProcError(f"O vídeo de {name} não tem frames")
    # 1o frame MJPEG costuma vir truncado: a ancora e o 2o pacote
    ancora = pk[1][0] if len(pk) > 1 else pk[0][0]
    last, dur, _ = pk[-1]
    if dur <= 0:
        dur = 1.0 / fps_out
    n_frames = max(1, round((last + dur - ancora) * fps_out))
    fps = (len(pk) - 2) / (last - ancora) if len(pk) > 2 and last > ancora else 0.0
    return VideoInfo(w=int(v["width"]), h=int(v["height"]), ancora_pts=ancora, n_frames=n_frames,
                     fps_medido=round(fps, 2))


def video_info(path: str, fps_out: int = 30) -> VideoInfo:
    return _video_info(path, procs.media_info(path), fps_out)


def parse_framemd5(path: str) -> tuple[list[float], list[int]]:
    # tabela do muxer framemd5: "#tb N: a/b" e linhas "stream, dts, pts, duration, size, hash"
    tbs: dict[int, float] = {}
    kinds: dict[int, str] = {}
    rows: list[tuple[int, int, int]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#tb "):
                idx, frac = line[4:].split(":", 1)
                num, den = frac.strip().split("/")
                tbs[int(idx)] = int(num) / int(den)
            elif line.startswith("#media_type "):
                idx, kind = line[12:].split(":", 1)
                kinds[int(idx)] = kind.strip()
            elif line.strip() and not line.startswith("#"):
                p = [x.strip() for x in line.split(",")]
                rows.append((int(p[0]), int(p[2]), int(p[4])))
    audio = [i for i, k in sorted(kinds.items()) if k == "audio"]
    stream = audio[0] if audio else 0
    tb = tbs.get(stream, 1.0)
    pts = [v * tb for s, v, _ in rows if s == stream]
    sizes = [n for s, _, n in rows if s == stream]
    return pts, sizes


def fit_audio_clock(pts: list[float], sizes: list[int], sr: int = 48000, bytes_per_sample: int = 2) -> AudioFit:
    # minimos quadrados: pts_i = inicio + s * amostras_acum_i / sr ; taxa_real = sr / s
    if not pts or len(pts) != len(sizes):
        raise ValueError("Sem pacotes de áudio para ajustar o relógio do microfone")
    p = np.asarray(pts, dtype=np.float64)
    n = np.asarray(sizes, dtype=np.int64) // bytes_per_sample
    x = np.concatenate(([0], np.cumsum(n)[:-1])) / sr
    duracao = float(n.sum()) / sr
    r = (p - p[0]) - x                     # >0: o relogio do sistema andou mais que as amostras
    m = x > (FIT_FROM_S if duracao >= SHORT_TAKE_S else SHORT_FIT_FROM_S)
    if m.sum() < MIN_FIT_PKTS:
        m = np.ones(len(p), dtype=bool)
    # buracos so dentro da janela do ajuste (antes dela e vies do comeco); tira os degraus antes de ajustar
    steps = np.diff(r)
    jump = (np.abs(steps) > GAP_S) & m[:-1] & m[1:]
    r = r - np.concatenate(([0.0], np.cumsum(np.where(jump, steps, 0.0))))
    b, a = 0.0, float(r[m].mean())
    if m.sum() >= 2 and np.ptp(x[m]) > 0:
        b, a = (float(v) for v in np.polyfit(x[m], r[m], 1))
        if abs(b) > MAX_DRIFT:
            b, a = 0.0, float(r[m].mean())
    res = r[m] - (a + b * x[m])
    return AudioFit(inicio=round(float(p[0]) + a, 6), taxa_real=round(sr / (1.0 + b), 3), gaps=int(jump.sum()),
                    residuo_ms=round(float(res.std()) * 1000, 3), duracao=round(duracao, 6))


def aligned_audio_filter(fit: AudioFit, ancora_pts: float, sr: int = 48000) -> str:
    # saida: amostra 0 = instante da ancora (2o frame do video), relogio do sistema
    if fit.gaps > 0:
        chain = [ASYNC]             # async poe cada pacote no seu pts; amostra 0 = tempo 0 do arquivo
        start = 0.0
    else:
        chain = ["asetpts=N/SR/TB"]
        start = fit.inicio
        rate = round(fit.taxa_real)
        if abs(fit.taxa_real - sr) * fit.duracao / sr > DRIFT_FIX_S and rate != sr:
            chain += [f"asetrate={rate}", f"aresample={sr}:resampler=soxr"]
    delta = start - ancora_pts
    n = round(abs(delta) * sr)
    if n and delta > 0:
        chain.append(f"adelay={n}S:all=1")
    elif n:
        chain.append(f"atrim=start_sample={n}")
    if len(chain) > 1:
        chain.append("asetpts=N/SR/TB")
    return ",".join(chain)


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def extract_aligned_audio(raw_mkv: str, out_wav: str) -> tuple[VideoInfo, AudioFit]:
    # audio.wav mono 48k PCM16 com t=0 no 2o frame do video; grava .part.wav e troca com os.replace
    name = os.path.basename(raw_mkv)
    info = procs.media_info(raw_mkv)
    vi = _video_info(raw_mkv, info, 30)
    a = info["audio"]
    if not a:
        raise procs.ProcError(f"{name} não tem áudio")
    codec = str(a.get("codec_name") or "")
    if not codec.startswith("pcm_s16"):
        raise procs.ProcError(f"Áudio de {name} em formato inesperado ({codec})")
    sr = int(a["sample_rate"])
    channels = int(a.get("channels") or 1)
    pk = read_packets(raw_mkv, "a")
    if not pk:
        raise procs.ProcError(f"{name} não tem áudio")
    fit = fit_audio_clock([p[0] for p in pk], [p[2] for p in pk], sr=sr, bytes_per_sample=2 * channels)
    af = aligned_audio_filter(fit, vi.ancora_pts, sr)
    part = os.path.splitext(out_wav)[0] + ".part.wav"
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", raw_mkv,
           "-map", "0:a:0", "-af", af, "-ac", "1", "-ar", str(OUT_SR), "-c:a", "pcm_s16le", "-f", "wav", part]
    try:
        r = procs.run(cmd, timeout=EXTRACT_TIMEOUT_S + fit.duracao)
    except subprocess.TimeoutExpired:
        _remove(part)
        raise procs.ProcError("A extração do áudio demorou demais") from None
    if r.returncode != 0:
        _remove(part)
        raise procs.ProcError("Não foi possível extrair o áudio alinhado", r.returncode, r.stderr.strip()[-2000:])
    os.replace(part, out_wav)
    return vi, fit
