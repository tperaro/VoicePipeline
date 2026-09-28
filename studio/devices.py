"""Microfones (pactl), cameras (/dev/v4l/by-id), source-output do gravador e espaco livre (so stdlib)."""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from studio.procs import run

PACTL_TIMEOUT_S = 5.0
BY_ID_DIR = "/dev/v4l/by-id"


@dataclass(frozen=True)
class Source:
    index: int
    name: str


def parse_sources(text: str) -> list[Source]:
    # linhas "indice\tnome\tdriver\tformato\testado"; monitores das saidas nao sao microfones
    found = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or not parts[0].strip().isdigit():
            continue
        name = parts[1].strip()
        if not name or name.endswith(".monitor"):
            continue
        found.append(Source(int(parts[0]), name))
    found.sort(key=lambda s: ("usb" not in s.name.lower(), s.name))
    return found


def _pactl(args: list[str], env: dict | None = None) -> str | None:
    try:
        r = run(["pactl", *args], timeout=PACTL_TIMEOUT_S, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def list_mics() -> list[Source]:
    return parse_sources(_pactl(["list", "short", "sources"]) or "")


def list_cameras(by_id_dir: str = BY_ID_DIR) -> list[str]:
    # so o no de captura (index0); link quebrado = camera desplugada
    folder = os.path.abspath(by_id_dir)
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    paths = [os.path.join(folder, n) for n in names if n.endswith("-video-index0")]
    return sorted(p for p in paths if os.path.exists(p))


_BLOCK_RE = re.compile(r"^Source Output #\d+", re.M)
_SOURCE_RE = re.compile(r"^\s+Source:\s*(\d+)\s*$", re.M)
_PID_RE = re.compile(r'^\s+application\.process\.id\s*=\s*"(\d+)"', re.M)


def parse_source_outputs(text: str) -> dict[int, int]:
    # {pid do processo: indice do Source}; so entende a saida com LC_ALL=C
    result = {}
    for block in _BLOCK_RE.split(text)[1:]:
        src = _SOURCE_RE.search(block)
        pid = _PID_RE.search(block)
        if src and pid:
            result[int(pid.group(1))] = int(src.group(1))
    return result


class PactlError(Exception):
    """O pactl falhou ou estourou o timeout: nao da para saber qual microfone o gravador usa."""


def mic_source_of_pid(pid: int) -> int | None:
    # None = o pactl respondeu e o pid nao tem source-output (gravador sumiu); PactlError = nao sei
    text = _pactl(["list", "source-outputs"], env={**os.environ, "LC_ALL": "C"})
    if text is None:
        raise PactlError("O pactl não respondeu — não deu para conferir o microfone")
    return parse_source_outputs(text).get(pid)


def free_bytes(path: str) -> int:
    # sobe ate a pasta existente mais proxima (recordings/ pode ainda nao existir)
    p = os.path.abspath(path)
    while not os.path.exists(p) and os.path.dirname(p) != p:
        p = os.path.dirname(p)
    return shutil.disk_usage(p).free
