"""Processos filhos: sempre com setpriv --pdeathsig, ffprobe e mensagens de erro (so stdlib)."""

import errno
import json
import os
import signal
import subprocess

SETPRIV = ["setpriv", "--pdeathsig", "TERM", "--"]
FFPROBE_TIMEOUT_S = 60.0


def guarded(argv: list[str]) -> list[str]:
    return [*SETPRIV, *argv]


def spawn(argv: list[str], **popen_kwargs) -> subprocess.Popen:
    popen_kwargs.setdefault("stdin", subprocess.DEVNULL)
    return subprocess.Popen(guarded(argv), **popen_kwargs)


def _kill(p: subprocess.Popen, own_group: bool) -> None:
    try:
        if own_group:
            os.killpg(p.pid, signal.SIGKILL)
        else:
            p.kill()
    except (ProcessLookupError, PermissionError):
        pass


def run(argv: list[str], timeout: float | None = None, **kw) -> subprocess.CompletedProcess:
    kw.setdefault("stdin", subprocess.DEVNULL)
    if kw.pop("capture_output", True):
        kw.setdefault("stdout", subprocess.PIPE)
        kw.setdefault("stderr", subprocess.PIPE)
    kw.setdefault("text", True)
    kw.setdefault("start_new_session", True)   # grupo proprio: o timeout mata netos tambem
    own_group = kw["start_new_session"]
    with spawn(argv, **kw) as p:
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(p, own_group)
            try:
                out, err = p.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                out, err = None, None
            raise subprocess.TimeoutExpired(p.args, timeout, output=out, stderr=err) from None
        except BaseException:
            _kill(p, own_group)
            raise
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)


class ProcError(Exception):
    def __init__(self, message: str, rc: int | None = None, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.rc = rc
        self.detail = detail


def ffprobe_json(path: str, *args: str, timeout: float = FFPROBE_TIMEOUT_S) -> dict:
    name = os.path.basename(path)
    try:
        r = run(["ffprobe", "-v", "error", "-of", "json", *args, path], timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ProcError(f"ffprobe demorou demais para ler {name}") from None
    if r.returncode != 0:
        raise ProcError(f"Não foi possível ler {name}", r.returncode, r.stderr.strip()[-2000:])
    try:
        data = json.loads(r.stdout)
    except ValueError:
        raise ProcError(f"Resposta inválida do ffprobe para {name}", r.returncode, r.stdout[:500]) from None
    if not isinstance(data, dict):
        raise ProcError(f"Resposta inválida do ffprobe para {name}", r.returncode, r.stdout[:500])
    return data


def media_info(path: str) -> dict:
    data = ffprobe_json(path, "-show_format", "-show_streams")
    streams = data.get("streams", [])

    def first(kind):
        return next((s for s in streams if s.get("codec_type") == kind), None)

    return {"format": data.get("format", {}), "video": first("video"), "audio": first("audio")}


def tail(path: str, n: int = 15) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 64 * 1024))
            data = f.read()
    except OSError:
        return ""
    lines = data.decode("utf-8", "replace").splitlines()
    return "\n".join(lines[-n:])


FFMPEG_EXIT_MSGS = {240: "Câmera em uso por outro programa (Meet/Zoom/OBS?)",
                    254: "Câmera não encontrada", 231: "Dispositivo de vídeo errado"}
# mesmo erro com outro codigo de saida: reconhece pelo texto do log
_LOG_HINTS = (("Device or resource busy", 240), ("Inappropriate ioctl for device", 231))


def ffmpeg_exit_message(rc: int, log_tail: str = "") -> str:
    if rc in FFMPEG_EXIT_MSGS:
        return FFMPEG_EXIT_MSGS[rc]
    for hint, code in _LOG_HINTS:
        if hint in log_tail:
            return FFMPEG_EXIT_MSGS[code]
    if rc < 0:
        return f"ffmpeg foi interrompido (sinal {-rc})"
    last = next((ln.strip() for ln in reversed(log_tail.splitlines()) if ln.strip()), "")
    msg = f"ffmpeg falhou (código {rc})"
    return f"{msg}: {last}" if last else msg


MSG_DISK_FULL = "Disco cheio — libere espaço"


def os_error_message(e: OSError) -> str:
    # erro de disco/arquivo em PT para o usuario; ENOSPC/EDQUOT viram "Disco cheio"
    if e.errno in (errno.ENOSPC, errno.EDQUOT):
        return MSG_DISK_FULL
    where = ""
    if isinstance(e.filename, (str, bytes)):
        where = f" ({os.path.basename(os.path.normpath(os.fsdecode(e.filename)))})"
    return f"Erro ao acessar o disco{where}: {e.strerror or e}"
