"""Render final: MKV da tomada + WAV convertido + marca d'agua -> MP4 verificado em videos_finais/."""

import os
import signal
import subprocess
import threading
import time

import numpy as np
from PIL import Image

from studio import procs, timeline, watermark
from studio.config import VIDEOS_DIR, Modelo, metadata_tags
from studio.takes import Take, final_video_name

FPS = 30
OUT_SR = 48000
SAMPLES_PER_FRAME = OUT_SR // FPS           # 1600
TIMEOUT_FACTOR = 5                          # 5x a duracao da tomada + 60 s
TIMEOUT_BASE_S = 60.0
ENCODERS = ("nvenc", "x264")                # ordem de tentativa
ENCODER_FLAGS = {
    "nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "21", "-b:v", "0",
              "-maxrate", "6M", "-bufsize", "12M", "-profile:v", "high"],
    "x264": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-maxrate", "6M", "-bufsize", "12M",
             "-profile:v", "high"],
}
PART_NAME = "render.part.mp4"
LOG_NAME = "render.log"
CANCEL_MSG = "Render cancelado"
MIN_FRAMES = 2                              # o video comeca no 2o frame (ancora): 1 frame so nao e video
ANCHOR_MARGIN_S = 0.0005                    # pts do MKV em ms: meio ms antes da ancora pega ela, nunca o anterior
MSG_SHORT = "Gravação curta demais para gerar o vídeo"
DISK_FULL_HINT = "No space left on device"  # o ffmpeg escreve o strerror do ENOSPC no render.log
POLL_S = 0.1
STOP_WAIT_S = 5.0
DECODE_TIMEOUT_S = 60.0
TEXT_LUMA_MIN = 200                         # texto branco da marca: luma > 200 ...
TEXT_MIN_FRACTION = 0.95                    # ... em >= 95 % dos pixels de texto
BAND_P95_MAX = 140                          # faixa escura: percentil 95 da luma < 140


class RenderError(Exception):
    pass


def offset_filter(av_offset_ms: int, sr: int = OUT_SR) -> str:
    # positivo = audio mais tarde; conta amostras (vale depois do aresample)
    n = round(abs(av_offset_ms) * sr / 1000)
    if n == 0:
        return ""
    if av_offset_ms > 0:
        return f",adelay={n}S:all=1"
    return f",atrim=start_sample={n}"


def build_filter(n_frames: int, ancora_pts: float, av_offset_ms: int = 0) -> str:
    # video comeca no frame da ancora pelo pts (com -copyts, o relogio do ffprobe, do audio.wav e da calibracao),
    # mesmo quando o 1o pacote nao decodifica; audio com exatamente n_frames*1600 amostras
    # (apad whole_len + atrim end_sample; NUNCA apad + -shortest: trava no ffmpeg 6.1.1)
    if n_frames < 1:
        raise ValueError("O vídeo precisa de pelo menos 1 frame")
    s = n_frames * SAMPLES_PER_FRAME
    return (f"[0:v]trim=start={ancora_pts - ANCHOR_MARGIN_S:.6f},setpts=PTS-STARTPTS,fps={FPS},"
            "tpad=stop_mode=clone:stop=2,"
            f"trim=end_frame={n_frames},setpts=PTS-STARTPTS,format=yuv420p[v0];"
            "[2:v]format=yuva420p[wm];[v0][wm]overlay=0:0:format=yuv420,format=yuv420p[v];"
            f"[1:a]aresample={OUT_SR}:resampler=soxr,asetpts=N/SR/TB{offset_filter(av_offset_ms)},"
            f"apad=whole_len={s},atrim=end_sample={s},asetpts=N/SR/TB[a]")


def build_render_cmd(raw_mkv: str, conv_wav: str, wm_png: str, out_part: str, n_frames: int, ancora_pts: float,
                     modelo: Modelo, av_offset_ms: int = 0, encoder: str = "nvenc") -> list[str]:
    # sem o prefixo setpriv (o procs.spawn poe); ValueError se o modelo nao tem aviso
    if encoder not in ENCODER_FLAGS:
        raise ValueError(f"Encoder desconhecido: {encoder}")
    meta = []
    for key, value in metadata_tags(modelo).items():
        meta += ["-metadata", f"{key}={value}"]
    return ["nice", "-n", "10",
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-copyts",
            "-i", raw_mkv, "-i", conv_wav, "-i", wm_png,
            "-filter_complex", build_filter(n_frames, ancora_pts, av_offset_ms),
            "-map", "[v]", "-map", "[a]", *ENCODER_FLAGS[encoder], "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-colorspace", "smpte170m", "-color_primaries", "smpte170m", "-color_trc", "smpte170m",
            "-color_range", "tv",
            "-c:a", "aac", "-b:a", "128k", "-ar", str(OUT_SR), "-ac", "1", "-movflags", "+faststart",
            *meta, "-f", "mp4", out_part]


def render_timeout(n_frames: int) -> float:
    return TIMEOUT_FACTOR * n_frames / FPS + TIMEOUT_BASE_S


# --- verificacao (fail-closed) ---

def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stream_problems(info: dict, n_frames: int) -> tuple[list[str], dict | None]:
    streams = info.get("streams", [])
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if len(video) != 1 or len(audio) != 1:
        return [f"O vídeo gerado tem {len(video)} faixa(s) de vídeo e {len(audio)} de áudio (esperado 1 e 1)"], None
    problems = []
    frames = int(video[0].get("nb_read_packets") or 0)
    if frames != n_frames:
        problems.append(f"O vídeo gerado tem {frames} frames (esperado {n_frames})")
    dv, da = _num(video[0].get("duration")), _num(audio[0].get("duration"))
    if dv is None or da is None or abs(dv - da) > 1 / FPS + 1e-6:
        problems.append(f"A duração do áudio ({da} s) difere da do vídeo ({dv} s) em mais de 1 frame")
    return problems, video[0]


def _tag_problems(tags: dict) -> list[str]:
    tags = {str(k).lower(): str(v) for k, v in tags.items()}
    problems = []
    if "IA" not in tags.get("comment", ""):
        problems.append("Metadado comment sem o aviso de IA")
    for key in ("title", "description"):
        if not tags.get(key, "").strip():
            problems.append(f"Metadado {key} ausente")
    return problems


def _gray_frames(mp4: str, idx: list[int], w: int, h: int) -> list[np.ndarray] | None:
    sel = "+".join(f"eq(n,{i})" for i in idx)
    try:
        r = procs.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", mp4, "-map", "0:v:0",
                       "-vf", f"select='{sel}',format=gray", "-fps_mode", "passthrough", "-f", "rawvideo", "-"],
                      timeout=DECODE_TIMEOUT_S + idx[-1] / FPS, text=False)
    except subprocess.TimeoutExpired:
        return None
    size = w * h
    if r.returncode != 0 or len(r.stdout) != size * len(idx):
        return None
    return [np.frombuffer(r.stdout, np.uint8, size, k * size).reshape(h, w) for k in range(len(idx))]


def _pixel_problems(mp4: str, n_frames: int, wm_png: str, size: tuple[int, int]) -> list[str]:
    # decodifica os frames 0, N/2 e N-1 em cinza e confere texto branco + faixa escura
    try:
        with Image.open(wm_png) as im:
            wm = im.convert("RGBA")
    except OSError:
        return ["Marca d'água ilegível: não dá para conferir o vídeo"]
    w, h = wm.size
    if (w, h) != size:
        return [f"O vídeo tem {size[0]}x{size[1]}, mas a marca d'água tem {w}x{h}"]
    text = watermark.text_pixel_mask(wm)
    x0, y0, x1, y1 = watermark.band_rect(w, h)
    band = np.zeros((h, w), dtype=bool)
    band[y0:y1, x0:x1] = True
    band &= np.asarray(wm)[..., :3].max(axis=2) == 0       # so o fundo da faixa, fora do texto
    if not text.any() or not band.any():
        return ["A marca d'água não tem texto para conferir"]
    idx = sorted({0, n_frames // 2, n_frames - 1})
    frames = _gray_frames(mp4, idx, w, h)
    if frames is None:
        return ["Não foi possível decodificar os frames do vídeo gerado"]
    no_text = [i for i, y in zip(idx, frames) if (y[text] > TEXT_LUMA_MIN).mean() < TEXT_MIN_FRACTION]
    no_band = [i for i, y in zip(idx, frames) if np.percentile(y[band], 95) >= BAND_P95_MAX]
    problems = []
    if no_text:
        problems.append(f"Texto da marca d'água não aparece no(s) frame(s) {', '.join(map(str, no_text))}")
    if no_band:
        problems.append(f"Faixa escura da marca d'água não aparece no(s) frame(s) {', '.join(map(str, no_band))}")
    return problems


def verify_render(mp4: str, n_frames: int, wm_png: str) -> list[str]:
    try:
        info = procs.ffprobe_json(mp4, "-count_packets", "-show_streams", "-show_format")
    except procs.ProcError as e:
        return [e.message]
    problems, video = _stream_problems(info, n_frames)
    problems += _tag_problems(info.get("format", {}).get("tags") or {})
    frames = int(video.get("nb_read_packets") or 0) if video else 0
    if frames > 0:
        # amostra os frames que o arquivo tem de fato (contagem errada ja foi acusada acima)
        size = (int(video.get("width") or 0), int(video.get("height") or 0))
        problems += _pixel_problems(mp4, frames, wm_png, size)
    return problems


# --- execucao ---

def _remove(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise RenderError(CANCEL_MSG)


def _stop(p: subprocess.Popen) -> None:
    # SIGTERM no grupo (o ffmpeg fecha o arquivo e sai); SIGKILL se nao sair
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if p.poll() is not None:
            return
        try:
            os.killpg(p.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            p.wait(timeout=STOP_WAIT_S)
        except subprocess.TimeoutExpired:
            pass


def _run(cmd: list[str], log: str, timeout: float, cancel: threading.Event | None) -> int:
    # spawn nesta thread (a que espera): pdeathsig vale enquanto ela viver
    with open(log, "ab") as lf:
        p = procs.spawn(cmd, stdout=subprocess.DEVNULL, stderr=lf, start_new_session=True)
    deadline = time.monotonic() + timeout
    try:
        while True:
            _check_cancel(cancel)
            if time.monotonic() >= deadline:
                raise RenderError("O render demorou demais e foi interrompido")
            try:
                return p.wait(timeout=POLL_S)
            except subprocess.TimeoutExpired:
                pass
    finally:
        _stop(p)


def _encode(take: Take, conv_wav: str, wm: str, part: str, vi: timeline.VideoInfo, modelo: Modelo,
            av_offset_ms: int, cancel: threading.Event | None) -> None:
    # NVENC primeiro; se o ffmpeg falhar, libx264
    log = take.path(LOG_NAME)
    open(log, "wb").close()
    rc = None
    for encoder in ENCODERS:
        _check_cancel(cancel)
        cmd = build_render_cmd(take.raw_path, conv_wav, wm, part, vi.n_frames, vi.ancora_pts, modelo,
                               av_offset_ms=av_offset_ms, encoder=encoder)
        rc = _run(cmd, log, render_timeout(vi.n_frames), cancel)
        if rc == 0:
            return
        _remove(part)
        if DISK_FULL_HINT in procs.tail(log, 20):
            raise RenderError(procs.MSG_DISK_FULL)    # o x264 falharia igual: nao tenta de novo
    last = next((ln.strip() for ln in reversed(procs.tail(log, 5).splitlines()) if ln.strip()), "")
    raise RenderError(f"Falha ao gerar o vídeo (código {rc})" + (f": {last}" if last else ""))


def _video_info(take: Take) -> timeline.VideoInfo:
    if take.video:
        try:
            return timeline.VideoInfo(**take.video)
        except TypeError:
            pass                    # take.json de outra versao: le de novo do raw
    try:
        return timeline.video_info(take.raw_path)
    except procs.ProcError as e:
        raise RenderError(e.message) from None


def _frame_size(raw: str) -> tuple[int, int]:
    try:
        v = procs.media_info(raw)["video"]
    except procs.ProcError as e:
        raise RenderError(e.message) from None
    if not v:
        raise RenderError("Esta tomada não tem vídeo")
    return int(v["width"]), int(v["height"])


def _check_png(wm: str, w: int, h: int) -> None:
    try:
        with Image.open(wm) as im:
            pw, ph = im.size
    except OSError:
        raise RenderError("Marca d'água ilegível") from None
    if (pw, ph) != (w, h):
        raise RenderError(f"Marca d'água {pw}x{ph} não confere com o vídeo {w}x{h}")


def render_final(take: Take, modelo: Modelo, conv_wav: str, av_offset_ms: int = 0,
                 videos_dir: str = VIDEOS_DIR, cancel: threading.Event | None = None) -> str:
    # nao mexe no take.json (so a thread principal da GUI grava); devolve o caminho final
    try:
        return _render_final(take, modelo, conv_wav, av_offset_ms, videos_dir, cancel)
    except OSError as e:
        # disco cheio (ou outro erro de arquivo) no meio do caminho: mensagem PT e nenhum .part sobrando
        try:
            _remove(take.path(PART_NAME))
        except OSError:
            pass
        raise RenderError(procs.os_error_message(e)) from None


def _render_final(take: Take, modelo: Modelo, conv_wav: str, av_offset_ms: int, videos_dir: str,
                  cancel: threading.Event | None) -> str:
    try:
        metadata_tags(modelo)
    except ValueError:
        raise RenderError(f"Modelo {modelo.label} sem texto de aviso: o vídeo não pode ser gerado") from None
    if take.modo != "av":
        raise RenderError("Esta tomada não tem vídeo")
    if not os.path.isfile(conv_wav):
        raise RenderError("Áudio convertido não encontrado — converta a voz de novo")
    vi = _video_info(take)
    if vi.n_frames < MIN_FRAMES:
        raise RenderError(MSG_SHORT)        # antes do ffmpeg: com 1 pacote saia "0 faixa(s) de vídeo"
    w, h = _frame_size(take.raw_path)
    try:
        wm = watermark.watermark_path(take.dir, w, h, modelo)
    except ValueError as e:         # marca fail-closed: aviso que nao cabe no quadro
        raise RenderError(str(e)) from None
    _check_png(wm, w, h)
    part = take.path(PART_NAME)
    _remove(part)
    try:
        _encode(take, conv_wav, wm, part, vi, modelo, av_offset_ms, cancel)
        _check_cancel(cancel)
    except BaseException:
        _remove(part)
        raise
    problems = verify_render(part, vi.n_frames, wm)
    if problems:
        # o .part fica na pasta da tomada para diagnostico; nada vai para videos_finais/
        raise RenderError("O vídeo gerado não passou na verificação: " + "; ".join(problems))
    os.makedirs(videos_dir, exist_ok=True)
    final = os.path.join(videos_dir, final_video_name(take.id, modelo.key))
    os.replace(part, final)
    return final
