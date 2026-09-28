"""Volume (volumedetect, aumentar com limitador) e MP3 com aviso de IA nas tags ID3."""

import math
import os
import re
import subprocess

from studio.config import Modelo, metadata_tags
from studio.procs import ProcError, run

DETECT_TIMEOUT_S = 60.0
ENCODE_TIMEOUT_S = 300.0
# level=0: o auto level (padrao) multiplica por 1/limit e o pico volta a 0 dBFS;
# latency=1: compensa o lookahead de 5 ms do limitador (o audio nao anda)
LIMITER = "alimiter=limit=0.97:level=0:latency=1"
FFMPEG = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]

_MEAN_RE = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB")
_MAX_RE = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?) dB")


def volumedetect(path: str) -> tuple[float | None, float | None]:
    # (mean_db, max_db) do 1o stream de audio; (None, None) se falhar ou nao houver amostras
    argv = ["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", path,
            "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"]
    try:
        r = run(argv, timeout=DETECT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    if r.returncode != 0:
        return None, None
    # o ffmpeg 6.1 recria o filtro no 1o frame: vale a ultima ocorrencia
    means = _MEAN_RE.findall(r.stderr)
    peaks = _MAX_RE.findall(r.stderr)
    return (float(means[-1]) if means else None, float(peaks[-1]) if peaks else None)


def _part_path(out: str) -> str:
    base, ext = os.path.splitext(out)
    return f"{base}.part{ext}"


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _encode_to(argv: list[str], part: str, out: str, what: str) -> None:
    # argv grava em part; so vira out (os.replace) se o ffmpeg terminar bem
    done = False
    try:
        try:
            r = run(argv, timeout=ENCODE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise ProcError(f"{what}: o ffmpeg demorou demais") from None
        if r.returncode != 0 or not os.path.isfile(part):
            raise ProcError(f"{what}: ffmpeg falhou (código {r.returncode})", r.returncode,
                            r.stderr.strip()[-2000:])
        os.replace(part, out)
        done = True
    finally:
        if not done:
            _unlink(part)


def boost_volume(inp: str, out: str, gain_db: float) -> None:
    gain_db = float(gain_db)
    if not math.isfinite(gain_db):
        raise ValueError(f"Ganho inválido: {gain_db}")
    part = _part_path(out)
    argv = [*FFMPEG, "-i", inp, "-map", "0:a:0", "-af", f"volume={gain_db:.2f}dB,{LIMITER}",
            "-c:a", "pcm_s16le", "-f", "wav", part]
    _encode_to(argv, part, out, "Não foi possível aumentar o volume")


def export_mp3(wav: str, mp3: str, modelo: Modelo) -> None:
    tags = metadata_tags(modelo)       # ValueError se o modelo nao tiver aviso (fail-closed)
    part = _part_path(mp3)
    argv = [*FFMPEG, "-i", wav, "-map", "0:a:0", "-map_metadata", "-1",
            "-c:a", "libmp3lame", "-q:a", "2", "-id3v2_version", "3",
            "-metadata", f"title={tags['title']}", "-metadata", f"comment={tags['comment']}",
            "-f", "mp3", part]
    _encode_to(argv, part, mp3, "Não foi possível gerar o MP3")
