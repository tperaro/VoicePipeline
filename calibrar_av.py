#!/usr/bin/env python3
"""Calibra o av_offset_ms com uma tomada de palmas.

Uso (da raiz do projeto, com o python do venv do Applio):
    Applio/.venv/bin/python calibrar_av.py recordings/<id> [--salvar]

Acha o inicio de cada palma no audio alinhado (transiente) e, no video, o instante do contato das maos
(a parada que fecha a aproximacao, numa regiao escolhida sozinha). Imprime a mediana dos offsets como
sugestao para av_offset_ms (positivo = audio mais tarde).
"""

import argparse
import os
import re
import sys
import tempfile
import wave
from dataclasses import dataclass, field

import numpy as np

from studio import procs, timeline
from studio.config import ESTADO_PATH, load_estado, merge_estado

MIN_CLAPS = 5
# audio
ONSET_SNR = 10.0         # limiar >= 10x o ruido de fundo ...
ONSET_PEAK_FRAC = 0.1    # ... e >= 10 % da palma mais forte
ONSET_FRAC = 0.2         # inicio = 1a amostra com 20 % do pico da palma
REFRACTORY_S = 0.25      # eco/ressonancia da mesma palma
# video
ANALYSIS_W = 160         # frames reduzidos para 160 px de largura, em cinza
CELL = 10                # celulas de 10x10 px para escolher a regiao das maos
NOISE_LEVEL = 8          # diferenca de luma abaixo disso e ruido
REGION_FRAC = 0.4        # celulas com >= 40 % da melhor pontuacao entram na regiao
SEARCH_S = 0.4           # procura o contato a +-0,4 s de cada palma do audio
SIG_FRAC = 0.3           # movimento >= 30 % do maior da janela conta (no quique a aproximacao tem ~40 %)
FULL_FRAC = 0.8          # intervalo com >= 80 % do anterior ainda e movimento cheio
STOP_FRAC = 0.5          # depois do contato o movimento cai abaixo de 50 % do pico da aproximacao
WEAK_FRAC = 0.25         # pico < 25 % da mediana das palmas = clique/barulho sem palma
# confianca
MAX_SPREAD_MS = 15.0
NEAR_FRAC = 0.8          # >= 80 % das palmas a ate max(15 ms, 1 frame) da mediana
LOW_FPS = 20.0
BIG_OFFSET_MS = 200      # acima disso so avisa: camera e microfone USB costumam ficar abaixo

_SHOWINFO = re.compile(r"\bn:\s*\d+\s+pts:\s*-?\d+\s+pts_time:\s*(-?[0-9.]+(?:e[-+]?\d+)?)")


class CalibrationError(Exception):
    pass


@dataclass
class Clap:
    audio_s: float       # inicio da palma no audio alinhado (t=0 no 2o frame)
    video_s: float       # contato das maos no mesmo relogio
    offset_ms: float     # video - audio: quanto atrasar o audio


@dataclass
class Calibration:
    claps: list[Clap]
    audio_onsets: int
    suggested_ms: int | None
    spread_ms: float | None
    fps: float
    region: tuple[int, int, int, int]      # (x0, y0, x1, y1) em pixels do video
    warnings: list[str] = field(default_factory=list)
    confidence: str = ""


def _decimal(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


# --- audio ---

def clap_onsets(x: np.ndarray, sr: int) -> list[float]:
    # inicio (s) de cada transiente forte; envelope = maximo por bloco de 1 ms
    a = np.abs(np.asarray(x, dtype=np.float64))
    hop = max(1, sr // 1000)
    n = len(a) // hop
    if n == 0:
        return []
    env = a[:n * hop].reshape(n, hop).max(axis=1)
    peak = float(env.max())
    thr = max(ONSET_SNR * float(np.median(env)), ONSET_PEAK_FRAC * peak)
    if peak <= 0 or thr >= peak:
        return []
    onsets, refractory = [], round(REFRACTORY_S * 1000)
    j = 0
    above = np.flatnonzero(env > thr)
    while True:
        later = above[above >= j]
        if len(later) == 0:
            return onsets
        j = int(later[0])
        p = j + int(np.argmax(env[j:j + 20]))           # pico nos 20 ms seguintes
        s0, s1 = max(0, (j - 10) * hop), (p + 1) * hop
        k = s0 + int(np.argmax(a[s0:s1] >= ONSET_FRAC * env[p]))
        onsets.append(k / sr)
        j += refractory


def _read_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768
    return x.reshape(-1, ch).mean(axis=1), sr


# --- video ---

def decode_gray(raw_mkv: str, w: int, h: int, timeout: float) -> tuple[np.ndarray, np.ndarray]:
    # frames (n, ah, aw) uint8 e pts (s) de cada frame decodificado (showinfo, com -copyts)
    aw, ah = ANALYSIS_W, max(CELL, round(ANALYSIS_W * h / w / CELL) * CELL)
    r = procs.run(["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-loglevel", "info", "-copyts",
                   "-i", raw_mkv, "-map", "0:v:0", "-vf", f"scale={aw}:{ah}:flags=area,format=gray,showinfo",
                   "-fps_mode", "passthrough", "-f", "rawvideo", "-"], timeout=timeout, text=False)
    err = r.stderr.decode("utf-8", "replace")
    if r.returncode != 0:
        raise procs.ProcError("Não foi possível decodificar o vídeo", r.returncode, err[-2000:])
    pts = np.array([float(m) for m in _SHOWINFO.findall(err)])
    n = len(r.stdout) // (aw * ah)
    if n < 3 or n != len(pts):
        raise CalibrationError(f"Leitura do vídeo inconsistente ({n} frames, {len(pts)} tempos)")
    return np.frombuffer(r.stdout, np.uint8, n * aw * ah).reshape(n, ah, aw), pts


def motion_cells(frames: np.ndarray) -> np.ndarray:
    # (n, celulas): soma da diferenca acima do ruido entre o frame i e o i-1, por celula; linha 0 = 0
    n, ah, aw = frames.shape
    out = np.zeros((n, ah // CELL, aw // CELL))
    prev = frames[0].astype(np.int16)
    for i in range(1, n):
        cur = frames[i].astype(np.int16)
        d = np.maximum(np.abs(cur - prev) - NOISE_LEVEL, 0)
        out[i] = d.reshape(ah // CELL, CELL, aw // CELL, CELL).sum(axis=(1, 3))
        prev = cur
    return out.reshape(n, -1)


def search_windows(onsets: list[float]) -> list[tuple[float, float]]:
    out = []
    for i, ta in enumerate(onsets):
        lo, hi = ta - SEARCH_S, ta + SEARCH_S
        if i > 0:
            lo = max(lo, (onsets[i - 1] + ta) / 2)
        if i + 1 < len(onsets):
            hi = min(hi, (ta + onsets[i + 1]) / 2)
        out.append((lo, hi))
    return out


def region_score(m: np.ndarray, t: np.ndarray, windows: list[tuple[float, float]]) -> np.ndarray:
    # por celula: soma, nas janelas das palmas, da maior queda de movimento de um frame para o seguinte
    score = np.zeros(m.shape[1])
    for lo, hi in windows:
        idx = np.flatnonzero((t >= lo) & (t <= hi))
        idx = idx[idx + 1 < len(t)]
        if len(idx):
            score += np.clip(np.max(m[idx] - m[idx + 1], axis=0), 0, None)
    return score


def contact_time(d: np.ndarray, t: np.ndarray, lo: float, hi: float,
                 cycle_start: float = float("-inf")) -> float | None:
    # contato = parada que fecha a APROXIMACAO: a 1a corrida da janela com movimento >= SIG_FRAC do maior.
    # Nao o maior pico: se as maos se afastam tao rapido quanto se juntam (ou quicam), o maior pico e a
    # separacao, e a parada dela sao as maos ja longe. Corrida que ja vinha de antes de cycle_start (meio do
    # caminho desde a palma anterior) e a separacao da anterior: None, nunca a parada dela. Segue enquanto o
    # movimento continua cheio; o contato cai dentro do 1o intervalo que encolhe, na fracao que ele andou:
    # t[q-1] + d[q]/d[q-1] * (t[q] - t[q-1])
    idx = np.flatnonzero((t >= lo) & (t <= hi))
    if len(idx) == 0:
        return None
    big = float(d[idx].max())
    if big <= 0:
        return None
    p = int(idx[np.argmax(d[idx] >= SIG_FRAC * big)])
    b = p
    while b > 0 and d[b - 1] >= SIG_FRAC * big:
        b -= 1
    if t[b] < cycle_start:
        return None
    peak = d[p]
    q = p + 1
    while q < len(d) and d[q] >= FULL_FRAC * d[q - 1]:
        peak = max(peak, d[q])
        q += 1
    if q >= len(d) or t[q - 1] > hi:
        return None
    after = d[q + 1] if q + 1 < len(d) else 0.0
    if min(d[q], after) >= STOP_FRAC * peak:
        return None                     # desacelerou mas nao parou: nao e palma
    return float(t[q - 1] + d[q] / d[q - 1] * (t[q] - t[q - 1]))


def _region_box(mask: np.ndarray, w: int, h: int, shape: tuple[int, int]) -> tuple[int, int, int, int]:
    ah, aw = shape
    rows, cols = np.nonzero(mask.reshape(ah // CELL, aw // CELL))
    sx, sy = w / aw, h / ah
    return (round(cols.min() * CELL * sx), round(rows.min() * CELL * sy),
            round((cols.max() + 1) * CELL * sx), round((rows.max() + 1) * CELL * sy))


# --- calibracao ---

def _pair_claps(onsets: list[float], d: np.ndarray, t: np.ndarray) -> list[Clap]:
    windows = search_windows(onsets)
    peaks = [float(d[(t >= lo) & (t <= hi)].max(initial=0.0)) for lo, hi in windows]
    weak = WEAK_FRAC * float(np.median(peaks))
    # ciclo de cada palma comeca no meio do caminho desde a anterior
    starts = [float("-inf"), *((a + b) / 2 for a, b in zip(onsets, onsets[1:]))]
    claps = []
    for ta, (lo, hi), peak, start in zip(onsets, windows, peaks, starts):
        tv = contact_time(d, t, lo, hi, start) if peak > weak else None
        if tv is not None:
            claps.append(Clap(round(ta, 4), round(tv, 4), round((tv - ta) * 1000, 1)))
    return claps


def _assess(offs: np.ndarray, n_onsets: int, spread: float, fps: float, suggested: int) -> tuple[list[str], str]:
    # (avisos, confianca): poucas palmas, dispersao alta ou palmas longe da mediana = baixa; so o fps baixo = media
    # o MAD ignora ate 49 % de palmas discordando; por isso tambem se conta quantas ficam perto da mediana
    warnings, n_claps = [], len(offs)
    if n_claps < MIN_CLAPS:
        warnings.append(f"Poucas palmas válidas ({n_claps} de {n_onsets} no áudio; o mínimo é {MIN_CLAPS})")
    if spread > MAX_SPREAD_MS:
        warnings.append(f"Dispersão alta (±{_decimal(spread)} ms): use boa luz e deixe as mãos inteiras no quadro")
    tol = max(MAX_SPREAD_MS, 1000 / max(fps, 1))
    near = int(np.sum(np.abs(offs - np.median(offs)) <= tol))
    if near < NEAR_FRAC * n_claps:
        warnings.append(f"Palmas discordando: só {near} de {n_claps} ficam a até {round(tol)} ms da mediana "
                        "(pare as mãos um instante depois de cada palma e afaste-as devagar)")
    confidence = "baixa — refaça a tomada se puder" if warnings else "alta"
    if fps < LOW_FPS:
        warnings.append(f"Câmera a {_decimal(fps)} fps (um frame a cada {round(1000 / max(fps, 1))} ms): "
                        "a 30 fps a medida é mais firme (veja \"fps baixo\" no README)")
        if confidence == "alta":
            confidence = "média — câmera abaixo de 20 fps"
    if abs(suggested) > BIG_OFFSET_MS:
        warnings.append(f"Offset acima de {BIG_OFFSET_MS} ms ({suggested:+d} ms), grande para câmera e microfone "
                        "USB: confira as palmas acima e, na dúvida, grave outra tomada")
    return warnings, confidence


def calibrate(take_dir: str) -> Calibration:
    raw = os.path.join(take_dir, "raw.mkv")
    if not os.path.isfile(raw):
        raise CalibrationError(f"Não achei raw.mkv em {take_dir} (a tomada precisa ter vídeo)")
    with tempfile.TemporaryDirectory(prefix="calibrar_av_") as tmp:     # nao mexe na tomada
        wav = os.path.join(tmp, "audio.wav")
        vi, _ = timeline.extract_aligned_audio(raw, wav)
        x, sr = _read_wav(wav)
    onsets = clap_onsets(x, sr)
    if not onsets:
        raise CalibrationError("Nenhuma palma encontrada no áudio")
    frames, pts = decode_gray(raw, vi.w, vi.h, timeout=60 + vi.n_frames / 30)
    t = pts - vi.ancora_pts                  # mesmo relogio do audio alinhado (e do render)
    m = motion_cells(frames)
    score = region_score(m, t, search_windows(onsets))
    if score.max() <= 0:
        raise CalibrationError("Nenhum movimento de palmas no vídeo")
    mask = score >= REGION_FRAC * score.max()
    claps = _pair_claps(onsets, m[:, mask].sum(axis=1), t)
    if not claps:
        raise CalibrationError("Nenhuma palma foi vista no vídeo (mãos fora do quadro ou pouca luz?)")
    offs = np.array([c.offset_ms for c in claps])
    med = float(np.median(offs))
    spread = round(1.4826 * float(np.median(np.abs(offs - med))), 1)     # desvio robusto (MAD)
    warnings, confidence = _assess(offs, len(onsets), spread, vi.fps_medido, int(round(med)))
    return Calibration(claps=claps, audio_onsets=len(onsets), suggested_ms=int(round(med)), spread_ms=spread,
                       fps=vi.fps_medido, region=_region_box(mask, vi.w, vi.h, frames.shape[1:]),
                       warnings=warnings, confidence=confidence)


def print_report(take_dir: str, cal: Calibration) -> None:
    offs = [c.offset_ms for c in cal.claps]
    x0, y0, x1, y1 = cal.region
    print(f"Tomada: {os.path.basename(os.path.normpath(take_dir))} (vídeo a {_decimal(cal.fps)} fps; "
          f"{cal.audio_onsets} palmas no áudio, {len(cal.claps)} vistas no vídeo)")
    print(f"Região das mãos: x {x0}–{x1}, y {y0}–{y1}")
    print(" palma   áudio (s)   vídeo (s)   offset (ms)")
    for i, c in enumerate(cal.claps, 1):
        print(f" {i:5d}   {c.audio_s:9.3f}   {c.video_s:9.3f}   {c.offset_ms:+11.1f}")
    print(f"av_offset_ms sugerido: {cal.suggested_ms:+d} ms (mediana de {len(offs)} palmas; dispersão "
          f"±{_decimal(cal.spread_ms)} ms; de {min(offs):+.0f} a {max(offs):+.0f})")
    print("Positivo = áudio mais tarde. A medida é feita sem offset: o valor substitui o atual.")
    for w in cal.warnings:
        print(f"Aviso: {w}")
    print(f"Confiança: {cal.confidence}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sugere o av_offset_ms a partir de uma tomada com 5 ou mais palmas.")
    ap.add_argument("tomada", help="pasta da tomada (recordings/<id>) com raw.mkv")
    ap.add_argument("--salvar", action="store_true", help="grava o valor sugerido em estado.json")
    ap.add_argument("--estado", default=ESTADO_PATH, help="caminho do estado.json (padrão: o do projeto)")
    args = ap.parse_args(argv)
    try:
        cal = calibrate(os.path.abspath(args.tomada))
    except (CalibrationError, procs.ProcError) as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 1
    print_report(args.tomada, cal)
    if not args.salvar:
        return 0
    if len(cal.claps) < MIN_CLAPS:
        print(f"Não salvei: são precisas pelo menos {MIN_CLAPS} palmas vistas no vídeo.")
        return 1
    if cal.confidence.startswith("baixa"):
        print("Não salvei: a confiança é baixa (veja os avisos acima). Grave outra tomada de palmas.")
        return 1
    antes = load_estado(args.estado)[0]["av_offset_ms"]
    # grava so o av_offset_ms, relendo o arquivo: o app pode estar aberto (ele tambem so grava o que muda)
    _, aviso = merge_estado({"av_offset_ms": cal.suggested_ms}, args.estado)
    if aviso:
        print(f"Aviso: {aviso}", file=sys.stderr)
    print(f"av_offset_ms = {cal.suggested_ms:+d} ms salvo em {args.estado} (antes: {antes})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
