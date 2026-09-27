"""Envio dos videos finais para uma pasta do Google Drive via rclone (so stdlib)."""

import collections
import fcntl
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from studio import procs
from studio.config import RCLONE_REMOTE, REC_DIR, VIDEOS_DIR, get_modelo, metadata_tags
from studio.takes import Take


class DriveError(Exception):
    pass


MSG_NO_RCLONE = "rclone não instalado — veja o README"
MSG_NO_CONFIG = "Drive não configurado — rode a configuração"
MSG_RELOGIN = "Login do Drive expirou — clique Reconectar"
MSG_NO_ACCESS = "Essa conta Google não tem acesso a essa pasta (ou o link está errado)"
MSG_QUOTA = "Seu Drive está cheio — os envios contam na sua cota"
MSG_CANCELLED = "Envio cancelado"
# erros que valem para a pasta inteira: nao adianta tentar os proximos arquivos
FATAL_MSGS = (MSG_NO_CONFIG, MSG_RELOGIN, MSG_NO_ACCESS, MSG_QUOTA)

# link -> ID (base: probes/2026-09-26/drive/drive_probe.py, 12 casos verificados)
_ID = r"[A-Za-z0-9_-]{10,}"
_BARE_ID = re.compile(rf"^{_ID}$")
_FOLDER_PATH = re.compile(rf"/folders/({_ID})")
_FILE_PATH = re.compile(rf"/file/d/({_ID})")
_HOSTS = {"drive.google.com", "docs.google.com"}


def parse_folder_link(s: str) -> tuple[str, str | None]:
    s = (s or "").strip()
    if not s:
        raise DriveError("Cole o link da pasta do Drive")
    if _BARE_ID.match(s):
        return s, None
    try:
        u = urlparse(s if "://" in s else "https://" + s)
        host = (u.hostname or "").lower()
    except ValueError:
        raise DriveError("Isso não é um link do Google Drive") from None
    if host not in _HOSTS:
        raise DriveError("Isso não é um link do Google Drive")
    if _FILE_PATH.search(u.path):
        raise DriveError("Esse link é de um arquivo, não de uma pasta")
    q = parse_qs(u.query)
    rk = (q.get("resourcekey") or [None])[0]
    m = _FOLDER_PATH.search(u.path)
    if m:
        return m.group(1), rk
    ids = q.get("id")
    if ids and _BARE_ID.match(ids[0]):
        return ids[0], rk
    raise DriveError("Não encontrei o ID da pasta no link")


def rclone_bin() -> str | None:
    found = shutil.which("rclone")
    if found:
        return found
    # o atalho do desktop pode nao ter ~/.local/bin no PATH
    local = os.path.expanduser("~/.local/bin/rclone")
    return local if os.path.isfile(local) and os.access(local, os.X_OK) else None


def _bin() -> str:
    return rclone_bin() or "rclone"


def _folder_flags(folder_id: str, resource_key: str | None) -> list[str]:
    # a pasta vai em cada execucao (nao fica no remote): trocar de pasta nao exige novo login
    flags = ["--drive-root-folder-id", folder_id]
    if resource_key:
        flags += ["--drive-resource-key", resource_key]
    return flags


# spec 9.2 + --error-on-no-transfer: rc 9 = arquivo ja igual no Drive (verificado no rclone v1.75.1)
COPY_FLAGS = ["--use-json-log", "--stats", "1s", "--stats-log-level", "NOTICE", "-v",
              "--retries", "3", "--retries-sleep", "10s", "--low-level-retries", "10",
              "--drive-chunk-size", "64M", "--transfers", "1", "--drive-stop-on-upload-limit",
              "--error-on-no-transfer"]


def build_copyto_cmd(local: str, dest_name: str, folder_id: str, resource_key: str | None = None,
                     description: str | None = None) -> list[str]:
    cmd = [_bin(), "copyto", local, f"{RCLONE_REMOTE}:{dest_name}", *_folder_flags(folder_id, resource_key),
           *COPY_FLAGS]
    if description:
        cmd += ["-M", "--metadata-set", f"description={description}"]
    return cmd


def build_lsjson_cmd(dest_name: str, folder_id: str, resource_key: str | None = None) -> list[str]:
    return [_bin(), "lsjson", f"{RCLONE_REMOTE}:{dest_name}", *_folder_flags(folder_id, resource_key),
            "--stat", "--hash", "--hash-type", "md5"]


def reconnect_cmd() -> list[str]:
    return [_bin(), "config", "update", RCLONE_REMOTE, "config_refresh_token=true"]


def parse_log_line(line: str) -> dict | None:
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    return entry if isinstance(entry, dict) else None


def _last_messages(text: str, n: int = 3) -> str:
    msgs = []
    for line in text.splitlines():
        entry = parse_log_line(line)
        if entry is not None and "stats" in entry:
            continue
        msg = str(entry.get("msg", "")) if entry is not None else line
        if msg.strip():
            msgs.append(msg.strip())
    return "\n".join(msgs[-n:])


# pasta inexistente/sem permissao: rc 3 (verificado) ou 404/403 da API do Drive [I]
_ACCESS_HINTS = ("directory not found", "File not found", "insufficientFilePermissions")


def classify_error(rc: int, log_text: str) -> str:
    text = log_text or ""
    if "didn't find section in config" in text:
        return MSG_NO_CONFIG
    if "invalid_grant" in text or "config reconnect" in text:
        return MSG_RELOGIN
    if "storageQuotaExceeded" in text:
        return MSG_QUOTA
    if rc == 3 or any(h in text for h in _ACCESS_HINTS):
        return MSG_NO_ACCESS
    if rc < 0:
        return f"rclone foi interrompido (sinal {-rc})"
    detail = _last_messages(text)
    msg = f"Falha no envio (código {rc})"
    return f"{msg}:\n{detail}" if detail else msg


_VIDEO_NAME = re.compile(r"^(?P<take>[\w-]+)_(?P<model>[a-z0-9]+)_IA\.mp4$")
DEFAULT_DESCRIPTION = "Voz sintética gerada por IA (conversão RVC). Paródia/homenagem."


def split_video_name(name: str) -> tuple[str, str] | None:
    m = _VIDEO_NAME.match(name)
    return (m.group("take"), m.group("model")) if m else None


def drive_description(dest_name: str) -> str:
    parts = split_video_name(dest_name)
    if parts is None:
        return DEFAULT_DESCRIPTION
    try:
        return metadata_tags(get_modelo(parts[1]))["comment"]
    except ValueError:
        return DEFAULT_DESCRIPTION


def _is_part(name: str) -> bool:
    return name.endswith(".part") or ".part." in name


def check_uploadable(path: str) -> str | None:
    name = os.path.basename(path)
    if _is_part(name):
        return f"{name}: arquivo incompleto (.part) — não é enviado"
    if not name.endswith("_IA.mp4"):
        return f"{name}: só vídeos *_IA.mp4 de videos_finais/ são enviados"
    if not os.path.isfile(path):
        return f"{name}: arquivo não encontrado"
    try:
        data = procs.ffprobe_json(path, "-show_entries", "format_tags")
    except procs.ProcError:
        return f"{name}: vídeo ilegível — não é enviado"
    tags = (data.get("format") or {}).get("tags") or {}
    comment = next((str(v) for k, v in tags.items() if k.lower() == "comment"), "")
    # fail-closed: sem o aviso de IA nos metadados o video nao sai da maquina
    if "IA" not in comment:
        return f"{name}: vídeo sem o aviso de IA nos metadados — não é enviado"
    return None


def md5_file(path: str) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


LOCK_NAME = ".envio.lock"


class UploadLock:
    # flock nao bloqueante compartilhado entre GUI e CLI; o kernel solta a trava se o processo morrer
    def __init__(self, videos_dir: str = VIDEOS_DIR):
        self.path = os.path.join(os.path.abspath(videos_dir), LOCK_NAME)
        self._fd: int | None = None

    def __enter__(self) -> "UploadLock":
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise DriveError("Outro envio já está em andamento") from None
        self._fd = fd
        return self

    def __exit__(self, *exc) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def list_videos(videos_dir: str = VIDEOS_DIR) -> list[str]:
    pattern = os.path.join(glob.escape(os.path.abspath(videos_dir)), "*_IA.mp4")
    return sorted(p for p in glob.glob(pattern) if os.path.isfile(p) and not _is_part(os.path.basename(p)))


LSJSON_TIMEOUT_S = 120
KILL_AFTER_S = 10
LOG_KEEP = 200
MSG_NO_DESCRIPTION = "O Drive recusou a descrição do arquivo; enviado sem ela"


def _result(path: str, **kw) -> dict:
    res = {"arquivo": path, "ok": False, "pulado": False, "md5": "", "erro": "", "aviso": ""}
    res.update(kw)
    return res


def _cancelled(cancel: threading.Event | None) -> bool:
    return cancel is not None and cancel.is_set()


def _fraction(entry: dict) -> float | None:
    st = entry.get("stats")
    try:
        total = float(st.get("totalBytes") or 0)
        done = float(st.get("bytes") or 0)
    except (AttributeError, TypeError, ValueError):
        return None
    return max(0.0, min(1.0, done / total)) if total > 0 else None


def _watch_cancel(p: subprocess.Popen, cancel: threading.Event, done: threading.Event) -> None:
    while not done.wait(0.1):
        if cancel.is_set():
            p.terminate()   # SIGTERM direto no rclone: o setpriv faz exec, o pid e o dele
            if not done.wait(KILL_AFTER_S):
                p.kill()
            return


def _run_copy(cmd: list[str], name: str, on_progress, cancel: threading.Event | None) -> tuple[int, str]:
    # o rclone escreve tudo no stderr, uma linha JSON por evento; o stdout fica vazio
    kept = collections.deque(maxlen=LOG_KEEP)
    p = procs.spawn(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace")
    done = threading.Event()
    watcher = None
    if cancel is not None:
        watcher = threading.Thread(target=_watch_cancel, args=(p, cancel, done), daemon=True)
        watcher.start()
    try:
        for line in p.stderr:
            entry = parse_log_line(line)
            if entry is not None and "stats" in entry:
                frac = _fraction(entry)
                if frac is not None and on_progress is not None:
                    on_progress(name, frac)
                continue
            if line.strip():
                kept.append(line.rstrip())
        rc = p.wait()
    except BaseException:
        p.terminate()
        try:
            p.wait(timeout=KILL_AFTER_S)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
        raise
    finally:
        done.set()
        p.stderr.close()
        if watcher is not None:
            watcher.join()
    return rc, "\n".join(kept)


def _verify(path: str, name: str, folder_id: str, rk: str | None) -> tuple[str, str]:
    # (md5, erro): compara tamanho e MD5 do arquivo no Drive com o local
    local_md5 = md5_file(path)
    try:
        r = procs.run(build_lsjson_cmd(name, folder_id, rk), timeout=LSJSON_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return "", "O Drive não respondeu a tempo na conferência do envio"
    if r.returncode != 0:
        return "", classify_error(r.returncode, r.stderr)
    try:
        info = json.loads(r.stdout)
        size = info.get("Size")
        remote_md5 = str((info.get("Hashes") or {}).get("md5") or "").lower()
    except (ValueError, AttributeError):
        return "", "Resposta inválida do rclone na conferência do envio"
    if size != os.path.getsize(path) or remote_md5 != local_md5:
        return "", "O arquivo no Drive não confere com o local (tamanho ou MD5 diferente)"
    return local_md5, ""


def _upload_one(path: str, folder_id: str, rk: str | None, on_progress, cancel: threading.Event | None,
                dry_run: bool) -> dict:
    res = _result(path)
    name = os.path.basename(path)
    motivo = check_uploadable(path)
    if motivo:
        res["erro"] = motivo
        return res
    if on_progress is not None:
        on_progress(name, 0.0)
    extra = ["--dry-run"] if dry_run else []
    cmd = build_copyto_cmd(path, name, folder_id, rk, description=drive_description(name)) + extra
    rc, log = _run_copy(cmd, name, on_progress, cancel)
    no_description = False
    if rc not in (0, 9) and not _cancelled(cancel) and "metadata" in log.lower():
        # o Drive recusou a descricao: repete sem ela (spec 9.2)
        no_description = True
        rc, log = _run_copy(build_copyto_cmd(path, name, folder_id, rk) + extra, name, on_progress, cancel)
    if rc not in (0, 9):
        res["erro"] = MSG_CANCELLED if _cancelled(cancel) else classify_error(rc, log)
        return res
    if not dry_run:
        res["md5"], res["erro"] = _verify(path, name, folder_id, rk)
        if res["erro"]:
            return res
    # rc 9 (--error-on-no-transfer) = ja estava igual no Drive
    res.update(ok=True, pulado=rc == 9, aviso=MSG_NO_DESCRIPTION if no_description else "")
    if on_progress is not None:
        on_progress(name, 1.0)
    return res


def upload_files(paths: list[str], folder_link: str, on_progress=None, cancel: threading.Event | None = None,
                 dry_run: bool = False, videos_dir: str = VIDEOS_DIR) -> list[dict]:
    folder_id, rk = parse_folder_link(folder_link)
    if rclone_bin() is None:
        raise DriveError(MSG_NO_RCLONE)
    results = []
    with UploadLock(videos_dir):
        fatal = ""
        for path in paths:
            if _cancelled(cancel):
                break
            path = os.path.abspath(path)
            if fatal:
                results.append(_result(path, erro=fatal))
                continue
            res = _upload_one(path, folder_id, rk, on_progress, cancel, dry_run)
            results.append(res)
            if res["erro"] in FATAL_MSGS:
                fatal = res["erro"]
    return results


def record_sent(result: dict, rec_dir: str = REC_DIR, now: datetime | None = None) -> str | None:
    # grava saidas[modelo]["enviado"] no take.json; so a thread principal da GUI (ou a CLI) chama
    if not result.get("ok") or not result.get("md5"):
        return None
    parts = split_video_name(os.path.basename(result.get("arquivo", "")))
    if parts is None:
        return None
    take_id, model_key = parts
    try:
        take = Take.load(os.path.join(rec_dir, take_id))
    except (OSError, ValueError, TypeError):
        return None
    saida = take.saidas.setdefault(model_key, {})
    if (saida.get("enviado") or {}).get("md5") != result["md5"]:
        saida["enviado"] = {"md5": result["md5"], "quando": (now or datetime.now()).isoformat(timespec="seconds")}
        take.save()
    return take.id
