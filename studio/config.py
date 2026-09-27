"""Caminhos, modelos e estado.json do Voice Studio (so stdlib)."""

import glob
import json
import os
import re
import tempfile
from dataclasses import dataclass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPLIO_DIR = os.path.join(BASE_DIR, "Applio")
LOGS_DIR = os.path.join(APPLIO_DIR, "logs")
VENV_PYTHON = os.path.join(APPLIO_DIR, ".venv", "bin", "python")
REC_DIR = os.path.join(BASE_DIR, "recordings")
VIDEOS_DIR = os.path.join(BASE_DIR, "videos_finais")
ESTADO_PATH = os.path.join(BASE_DIR, "estado.json")
STUDIO_LOG = os.path.join(BASE_DIR, "studio.log")
RVC_LOG = os.path.join(BASE_DIR, "studio_rvc.log")
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
RCLONE_REMOTE = "iavoz"
AVISO_LINHA1 = "VOZ GERADA POR IA"
AVISO_LINHA3 = "paródia · homenagem"
MAX_TAKE_S = 300
MIN_FREE_BYTES = 2 * 1024**3


@dataclass(frozen=True)
class Modelo:
    key: str
    label: str
    nome: str
    aviso: str


MODELOS = (
    Modelo("orochi", "Orochi", "Orochi", "Não é a voz real do Orochi"),
    Modelo("silvio", "Silvio Santos", "Silvio Santos", "Não é a voz real de Silvio Santos"),
)


def get_modelo(key: str) -> Modelo:
    for m in MODELOS:
        if m.key == key:
            return m
    raise ValueError(f"Modelo desconhecido: {key}")


def metadata_tags(m: Modelo) -> dict[str, str]:
    # fail-closed: sem aviso nao existe metadado de video
    if not m.aviso.strip() or not m.nome.strip():
        raise ValueError(f"Modelo {m.key} sem texto de aviso")
    return {
        "title": "Paródia/homenagem - voz gerada por IA",
        "comment": f"Voz sintética gerada por IA (conversão RVC). {m.aviso}.",
        "description": "AI-generated/synthetic voice (RVC voice conversion). Parody/tribute. "
                       f"Not the real voice of {m.nome}.",
    }


def find_latest_checkpoint(model_key: str, logs_dir: str = LOGS_DIR) -> str | None:
    # colchete no caminho (ex.: "orochi [v2]") nao pode virar padrao do glob
    pattern = os.path.join(glob.escape(logs_dir), glob.escape(model_key), f"{glob.escape(model_key)}_*e_*s.pth")
    files = glob.glob(pattern)

    def epoch(path):
        m = re.search(rf"{re.escape(model_key)}_(\d+)e_", os.path.basename(path))
        return int(m.group(1)) if m else -1

    files.sort(key=lambda p: (epoch(p), p))
    return files[-1] if files else None


def model_index_path(model_key: str, logs_dir: str = LOGS_DIR) -> str:
    return os.path.join(logs_dir, model_key, f"{model_key}.index")


DEFAULT_ESTADO = {"mic": "", "camera": "", "gravar_video": True, "drive_pasta": "",
                  "av_offset_ms": 0, "modelo": "orochi"}


def atomic_write_json(path: str, obj) -> None:
    # serializa antes de abrir o tmp: erro de tipo nao deixa lixo
    text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{os.path.basename(path)}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


MSG_ESTADO_CORROMPIDO = "estado.json corrompido — usando padrões"
CORRUPT_SUFFIX = ".corrompido"


def _read_estado(path: str) -> tuple[dict, bool]:
    # (dados do arquivo, corrompido?); arquivo ausente = ({}, False)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}, False
    except (OSError, ValueError):
        return {}, True
    return (data, False) if isinstance(data, dict) else ({}, True)


def _with_defaults(data: dict) -> tuple[dict, str | None]:
    # valor de tipo errado (ex.: "drive_pasta": 123) vira o padrao, com aviso; chaves desconhecidas ficam
    estado = dict(DEFAULT_ESTADO)
    estado.update(data)
    bad = [k for k, v in DEFAULT_ESTADO.items() if type(estado[k]) is not type(v)]
    for k in bad:
        estado[k] = DEFAULT_ESTADO[k]
    if not bad:
        return estado, None
    return estado, f"estado.json: valor inválido em {', '.join(bad)} — usando o padrão"


def load_estado(path: str = ESTADO_PATH) -> tuple[dict, str | None]:
    data, corrupt = _read_estado(path)
    if corrupt:
        return dict(DEFAULT_ESTADO), MSG_ESTADO_CORROMPIDO
    return _with_defaults(data)


def save_estado(estado: dict, path: str = ESTADO_PATH) -> None:
    atomic_write_json(path, estado)


def merge_estado(changes: dict, path: str = ESTADO_PATH) -> tuple[dict, str | None]:
    # rele o arquivo e grava so as chaves de changes: o que outro programa gravou (calibrar_av.py,
    # enviar_drive.py) nao volta atras; arquivo corrompido e guardado em <path>.corrompido antes
    data, corrupt = _read_estado(path)
    aviso = None
    if corrupt:
        backup = path + CORRUPT_SUFFIX
        os.replace(path, backup)
        aviso = f"estado.json corrompido — cópia guardada em {os.path.basename(backup)}"
    estado, aviso_tipos = _with_defaults(data)
    estado.update(changes)
    save_estado(estado, path)
    return estado, aviso or aviso_tipos
