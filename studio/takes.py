"""Tomadas: pasta recordings/<id>/ com take.json atomico e recuperacao na abertura."""

import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime

from studio.config import REC_DIR, atomic_write_json
from studio.procs import ProcError, media_info, run

STATUS = ("gravando", "gravado", "convertido", "renderizado", "falhou")
MODOS = ("av", "audio")
TAKE_JSON = "take.json"
REMUX_TIMEOUT_S = 300


@dataclass
class Take:
    id: str
    dir: str
    modo: str
    status: str
    mic: str
    camera: str = ""
    video: dict = field(default_factory=dict)
    audio_fit: dict = field(default_factory=dict)
    saidas: dict = field(default_factory=dict)
    criado: str = ""
    erro: str = ""

    def path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    @property
    def raw_path(self) -> str:
        return self.path("raw.mkv" if self.modo == "av" else "raw.wav")

    @property
    def audio_path(self) -> str:
        return self.path("audio.wav" if self.modo == "av" else "raw.wav")

    def save(self) -> None:
        data = asdict(self)
        data.pop("dir")
        atomic_write_json(self.path(TAKE_JSON), data)

    @classmethod
    def load(cls, take_dir: str) -> "Take":
        with open(os.path.join(take_dir, TAKE_JSON), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"take.json inválido em {take_dir}")
        known = {f.name for f in fields(cls)} - {"dir"}
        return cls(dir=os.path.abspath(take_dir), **{k: v for k, v in data.items() if k in known})


def new_take(modo: str, mic: str, camera: str = "", rec_dir: str = REC_DIR,
             now: datetime | None = None) -> Take:
    if modo not in MODOS:
        raise ValueError(f"Modo desconhecido: {modo}")
    now = now or datetime.now()
    base = now.strftime("%Y-%m-%d_%H%M%S")
    os.makedirs(rec_dir, exist_ok=True)
    n = 1
    while True:
        take_id = base if n == 1 else f"{base}_{n}"
        take_dir = os.path.abspath(os.path.join(rec_dir, take_id))
        try:
            os.mkdir(take_dir)   # atomico: duas tomadas no mesmo segundo nunca dividem a pasta
            break
        except FileExistsError:
            n += 1
    take = Take(id=take_id, dir=take_dir, modo=modo, status="gravando", mic=mic, camera=camera,
                criado=now.isoformat(timespec="seconds"))
    take.save()
    return take


def _sort_key(take_id: str) -> tuple[str, int]:
    # "_10" depois de "_9"
    m = re.fullmatch(r"(.+)_(\d{1,3})", take_id)
    return (m.group(1), int(m.group(2))) if m else (take_id, 1)


def list_takes(rec_dir: str = REC_DIR) -> list[Take]:
    try:
        names = os.listdir(rec_dir)
    except OSError:
        return []
    found = []
    for name in names:
        take_dir = os.path.join(rec_dir, name)
        if not os.path.isfile(os.path.join(take_dir, TAKE_JSON)):
            continue
        try:
            found.append(Take.load(take_dir))
        except (OSError, ValueError, TypeError):
            continue   # take.json ilegivel: nao derruba a abertura
    return sorted(found, key=lambda t: _sort_key(t.id))


USABLE_STATUS = ("gravado", "convertido", "renderizado")


def is_usable(take: Take) -> bool:
    # da para converter/gerar/enviar: a gravacao terminou bem e o arquivo bruto esta no disco
    return take.status in USABLE_STATUS and os.path.isfile(take.raw_path)


def latest_take(rec_dir: str = REC_DIR, usable_only: bool = False) -> Take | None:
    # usable_only: a abertura do app pula tomada que falhou (ex.: camera em uso) e mostra a boa anterior
    found = [t for t in list_takes(rec_dir) if not usable_only or is_usable(t)]
    return found[-1] if found else None


def _readable(path: str, need_video: bool) -> bool:
    try:
        info = media_info(path)
    except ProcError:
        return False
    try:
        duration = float(info["format"].get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0 or info["audio"] is None:
        return False
    return info["video"] is not None or not need_video


def _remux(raw: str, need_video: bool) -> bool:
    stem, ext = os.path.splitext(raw)
    fixed = f"{stem}.recovered{ext}"
    try:
        r = run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                 "-i", raw, "-map", "0", "-c", "copy", fixed], timeout=REMUX_TIMEOUT_S)
        if r.returncode == 0 and _readable(fixed, need_video):
            os.replace(fixed, raw)
            return True
    except Exception:
        pass
    try:
        os.unlink(fixed)
    except OSError:
        pass
    return False


def _recover(take: Take) -> str:
    need_video = take.modo == "av"
    raw = take.raw_path
    if _readable(raw, need_video):
        take.status, take.erro = "gravado", ""
        take.save()
        return f"Tomada {take.id}: gravação interrompida recuperada"
    if os.path.exists(raw) and _remux(raw, need_video):
        take.status, take.erro = "gravado", ""
        take.save()
        return f"Tomada {take.id}: gravação interrompida recuperada (arquivo reconstruído)"
    take.status = "falhou"
    if os.path.exists(raw):
        take.erro = "Gravação interrompida e ilegível — não foi possível recuperar"
    else:
        take.erro = "Arquivo da gravação não encontrado"
    take.save()
    return f"Tomada {take.id}: {take.erro}"


def recover_takes(rec_dir: str = REC_DIR) -> list[str]:
    return [_recover(t) for t in list_takes(rec_dir) if t.status == "gravando"]


def final_video_name(take_id: str, model_key: str) -> str:
    return f"{take_id}_{model_key}_IA.mp4"
