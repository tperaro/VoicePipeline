"""Linha do tempo da tomada: relogio do mic ajustado aos pts e filtro que alinha o audio a ancora do video."""

from dataclasses import dataclass

import numpy as np

GAP_S = 0.030            # salto de residuo acima disso = buraco no audio
FIT_FROM_S = 2.0         # ajuste usa pacotes depois de 2 s (o comeco ainda esta assentando)
SHORT_TAKE_S = 4.0
SHORT_FIT_FROM_S = 0.5   # tomadas < 4 s: pacotes depois de 0,5 s
MIN_FIT_PKTS = 5         # menos que isso: usa todos os pacotes
MAX_DRIFT = 1e-3         # inclinacao acima de 1000 ppm nao e deriva de mic: mantem a taxa nominal
DRIFT_FIX_S = 0.005      # deriva acumulada acima de 5 ms -> asetrate
ASYNC = "aresample=async=1:min_hard_comp=0.03:first_pts=0"


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
