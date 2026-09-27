"""Worker do RVC: subprocesso que carrega o Applio uma vez e converte em pedacos sem deriva.

Roda como [VENV_PYTHON, "-m", "studio.rvc_worker"] com cwd=BASE_DIR. Protocolo: uma requisicao JSON por
linha no stdin e uma resposta JSON por linha num fd dedicado (os prints do Applio vao para o stderr).
"""

import contextlib
import json
import os
import sys
import tempfile
import traceback

# garante o pacote studio no path mesmo depois do chdir para o Applio
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from studio.config import (APPLIO_DIR, LOGS_DIR, find_latest_checkpoint, get_modelo,  # noqa: E402
                           model_index_path)

MAX_SINGLE_S = 40.0     # ate ~41 s o RVC preserva o tempo: um pedaco so
MAX_PIECE_S = 30.0
SEARCH_S = 3.0
CTX_S = 0.5
XF_S = 0.005
FAKE_SR = 40000         # taxa do conversor falso (a mesma do silvio)

# mesmos parametros do orochi_studio.py
APPLIO_PARAMS = dict(pitch=0, index_rate=0.75, volume_envelope=1.0, protect=0.33, f0_method="rmvpe",
                     split_audio=False, f0_autotune=False, f0_autotune_strength=1.0, proposed_pitch=False,
                     proposed_pitch_threshold=155.0, clean_audio=False, clean_strength=0.5,
                     export_format="WAV", embedder_model="contentvec")


class ConvertError(Exception):
    """Erro esperado da conversao; str(e) e a mensagem para o usuario."""


def fake_mode() -> bool:
    return os.environ.get("STUDIO_RVC_FAKE") == "1"


def frame_energy(x: np.ndarray, sr: int) -> np.ndarray:
    # RMS por quadro de 10 ms
    frame = sr // 100
    n = len(x) // frame
    return np.sqrt(np.mean(x[:n * frame].reshape(n, frame) ** 2, axis=1))


def plan_cuts(energy_10ms, total_s: float, max_piece_s: float = MAX_PIECE_S,
              search_s: float = SEARCH_S) -> list[float]:
    # corte no quadro mais quieto a +-search_s do nominal (max_piece_s - search_s apos o corte anterior)
    e = np.asarray(energy_10ms)
    step, search = int(round((max_piece_s - search_s) * 100)), int(round(search_s * 100))
    cuts = [0]
    while total_s - cuts[-1] / 100 > max_piece_s:
        nom = cuts[-1] + step
        lo, hi = nom - search, min(nom + search, len(e) - 1)
        cuts.append(lo + int(np.argmin(e[lo:hi])))
    return [c / 100 for c in cuts] + [total_s]


def assemble(pieces: list[tuple[float, float, float, np.ndarray]], total_s: float, out_sr: int,
             xf_s: float = XF_S) -> np.ndarray:
    # pieces = (a, b, inicio_do_contexto, audio convertido); cada trecho volta ao offset exato
    out = np.zeros(int(round(total_s * out_sr)))
    xf = int(round(xf_s * out_sr))
    last = len(pieces) - 1
    for i, (a, b, ca, y) in enumerate(pieces):
        s0, s1 = int(round(a * out_sr)), int(round(b * out_sr))
        off = int(round((a - ca) * out_sr))
        ext = xf if i < last else 0          # invade o trecho seguinte para o crossfade
        seg = np.asarray(y, dtype=float)[off:off + (s1 - s0) + ext]
        seg = np.pad(seg, (0, max(0, (s1 - s0) + ext - len(seg))))
        if i > 0 and xf:
            ramp = np.linspace(0.0, 1.0, xf)
            out[s0:s0 + xf] = out[s0:s0 + xf] * (1 - ramp) + seg[:xf] * ramp
            out[s0 + xf:s0 + len(seg)] = seg[xf:]
        else:
            out[s0:s0 + len(seg)] = seg
    return out


def fake_infer(src: str, dst: str) -> None:
    # conversor identidade (STUDIO_RVC_FAKE=1): reamostra para 40 kHz e suja o stdout de proposito
    print(f"[fake applio] Converting audio '{src}'...", flush=True)
    os.write(1, b"[fake applio] lixo de biblioteca nativa no fd 1\n")
    x, sr = sf.read(src, always_2d=True)
    x = x.mean(axis=1)
    n = int(round(len(x) * FAKE_SR / sr))
    y = np.interp(np.arange(n) * (sr / FAKE_SR), np.arange(len(x)), x)
    sf.write(dst, y, FAKE_SR, subtype="PCM_16")


_core = None


def load_applio():
    # importa o Applio uma vez; ele le os.getcwd() no import (o main ja fez chdir para APPLIO_DIR)
    global _core
    if _core is None:
        if APPLIO_DIR not in sys.path:
            sys.path.insert(0, APPLIO_DIR)
        import core
        core.import_voice_converter()
        _core = core
    return _core


def applio_infer(model_key: str, logs_dir: str = LOGS_DIR):
    pth = find_latest_checkpoint(model_key, logs_dir)
    if not pth:
        raise ConvertError(f"Nenhum checkpoint do modelo {get_modelo(model_key).label} em {logs_dir}")
    index = model_index_path(model_key, logs_dir)
    core = load_applio()

    def run(src: str, dst: str) -> None:
        core.run_infer_script(input_path=src, output_path=dst, pth_path=pth, index_path=index,
                              **APPLIO_PARAMS)
    return run


def warm_up() -> None:
    if fake_mode():
        print("[fake applio] carregando modelo (falso)", flush=True)
        return
    load_applio()


def free_gpu() -> None:
    torch = sys.modules.get("torch")
    if torch is not None and not fake_mode() and torch.cuda.is_available():
        torch.cuda.empty_cache()


def convert_pieces(x: np.ndarray, sr: int, cuts: list[float], work: str, run_infer):
    total_s = len(x) / sr
    pieces, out_sr = [], None
    for i, (a, b) in enumerate(zip(cuts, cuts[1:])):
        ca, cb = max(0.0, a - CTX_S), min(total_s, b + CTX_S)
        src, dst = os.path.join(work, f"p{i:03d}.wav"), os.path.join(work, f"p{i:03d}_out.wav")
        sf.write(src, x[int(round(ca * sr)):int(round(cb * sr))], sr, subtype="PCM_16")
        run_infer(src, dst)
        if not os.path.isfile(dst):
            raise ConvertError("O RVC não gerou o áudio convertido (veja studio_rvc.log)")
        y, psr = sf.read(dst, always_2d=True)
        if out_sr not in (None, psr):
            raise ConvertError("O RVC devolveu taxas de amostragem diferentes entre os pedaços")
        out_sr = psr
        pieces.append((a, b, ca, y.mean(axis=1)))
    return pieces, out_sr


def convert(inp: str, out: str, model_key: str, run_infer=None) -> dict:
    try:
        get_modelo(model_key)
    except ValueError:
        raise ConvertError(f"Modelo desconhecido: {model_key}") from None
    if not (os.path.isabs(inp) and os.path.isabs(out)):
        raise ConvertError("Os caminhos da conversão precisam ser absolutos")
    if not os.path.isfile(inp):
        raise ConvertError(f"Arquivo de entrada não encontrado: {inp}")
    if not os.path.isdir(os.path.dirname(out)):
        raise ConvertError(f"Pasta de saída não existe: {os.path.dirname(out)}")
    try:
        x, sr = sf.read(inp, always_2d=True)
    except RuntimeError as e:
        raise ConvertError(f"Não foi possível ler {os.path.basename(inp)}: {e}") from None
    x = x.mean(axis=1)
    if len(x) == 0:
        raise ConvertError(f"Áudio vazio: {os.path.basename(inp)}")
    total_s = len(x) / sr
    cuts = plan_cuts(frame_energy(x, sr), total_s) if total_s > MAX_SINGLE_S else [0.0, total_s]
    if run_infer is None:
        run_infer = fake_infer if fake_mode() else applio_infer(model_key)
    part = os.path.splitext(out)[0] + ".part.wav"
    try:
        with tempfile.TemporaryDirectory(prefix=".rvc_", dir=os.path.dirname(out)) as work:
            pieces, out_sr = convert_pieces(x, sr, cuts, work, run_infer)
        y = assemble(pieces, total_s, out_sr)
        sf.write(part, y, out_sr, subtype="PCM_16")
        info = sf.info(part)
        if info.samplerate != out_sr or info.frames != len(y) or len(y) == 0:
            raise ConvertError("Saída do conversor inválida")
        os.replace(part, out)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(part)
        raise
    return {"duracao": len(y) / out_sr, "sr": out_sr, "pedacos": len(pieces)}


def handle(req) -> dict:
    rid = req.get("id") if isinstance(req, dict) else None
    op = req.get("op") if isinstance(req, dict) else None
    try:
        if op == "ping":
            return {"id": rid, "ok": True, "pid": os.getpid(), "fake": fake_mode(),
                    "torch": "torch" in sys.modules}
        if op == "load":
            warm_up()
            return {"id": rid, "ok": True}
        if op == "convert":
            args = [req.get(k) for k in ("input", "output", "model")]
            if not all(isinstance(a, str) and a for a in args):
                raise ConvertError("Requisição de conversão incompleta")
            return {"id": rid, "ok": True, **convert(*args)}
        return {"id": rid, "ok": False, "erro": f"Operação desconhecida: {op}"}
    except ConvertError as e:
        return {"id": rid, "ok": False, "erro": str(e)}
    except Exception as e:
        traceback.print_exc()
        return {"id": rid, "ok": False, "erro": f"Falha no conversor: {type(e).__name__}: {e}"}
    finally:
        if op == "convert":
            free_gpu()


def main() -> int:
    # protocolo num fd dedicado; o fd 1 (prints do Applio e de libs nativas) passa a ir para o stderr
    proto = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    os.chdir(APPLIO_DIR)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
        except ValueError:
            resp = {"id": None, "ok": False, "erro": "Requisição inválida (JSON)"}
        else:
            resp = handle(req)
        proto.write(json.dumps(resp) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
