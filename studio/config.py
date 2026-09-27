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
    pattern = os.path.join(logs_dir, model_key, f"{model_key}_*e_*s.pth")
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


def load_estado(path: str = ESTADO_PATH) -> tuple[dict, str | None]:
    estado = dict(DEFAULT_ESTADO)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return estado, None
    except (OSError, ValueError):
        return estado, "estado.json corrompido — usando padrões"
    if not isinstance(data, dict):
        return estado, "estado.json corrompido — usando padrões"
    estado.update(data)
    return estado, None


def save_estado(estado: dict, path: str = ESTADO_PATH) -> None:
    atomic_write_json(path, estado)
