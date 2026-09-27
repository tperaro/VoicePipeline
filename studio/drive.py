"""Envio dos videos finais para uma pasta do Google Drive via rclone (so stdlib)."""

import json
import os
import re
import shutil
from urllib.parse import parse_qs, urlparse

from studio.config import RCLONE_REMOTE, get_modelo, metadata_tags


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
