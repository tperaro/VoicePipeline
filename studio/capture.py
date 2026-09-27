"""Captura: comandos do ffmpeg (A/V + preview, so preview, so audio), leitor de frames, checagens e watchdog."""

import collections
import os
import threading
import time

from studio import devices, procs
from studio.config import MAX_TAKE_S, MIN_FREE_BYTES, REC_DIR

PREVIEW_W, PREVIEW_H = 480, 270
PREVIEW_FPS = 15
FRAME_BYTES = PREVIEW_W * PREVIEW_H * 3
# o pad garante o tamanho exato do frame para qualquer proporcao da camera
PREVIEW_VF = (f"scale={PREVIEW_W}:{PREVIEW_H}:force_original_aspect_ratio=decrease,"
              f"pad={PREVIEW_W}:{PREVIEW_H}:(ow-iw)/2:(oh-ih)/2,fps={PREVIEW_FPS}")
ACCEPTED_RC = (0, 255, 224)      # q | SIGTERM/SIGINT | preview com pipe quebrado
FPS_WINDOW = 30                  # intervalos usados na media do fps
FPS_MIN_FRAMES = 5
MIN_FPS = 12.0
FPS_GRACE_S = 3.0                # o fps dos primeiros segundos sai baixo (atraso do 1o frame)
MSG_MIC = "Microfone desconectado ou trocado"
MSG_STALL = "A câmera parou de enviar imagem"
MSG_NO_FRAME = "A câmera não enviou nenhuma imagem"
MSG_EMPTY = "A gravação está vazia ou corrompida"


def _mic_input(mic: str) -> list[str]:
    return ["-f", "pulse", "-thread_queue_size", "1024", "-sample_rate", "48000", "-channels", "1", "-i", mic]


def _cam_input(cam: str) -> list[str]:
    # mono2abs poe a camera no mesmo relogio do audio (sem ele o audio some sem erro)
    return ["-f", "v4l2", "-input_format", "mjpeg", "-video_size", "1280x720", "-framerate", "30",
            "-ts", "mono2abs", "-thread_queue_size", "512", "-i", cam]


def _preview_output(stream: str) -> list[str]:
    return ["-map", stream, "-vf", PREVIEW_VF, "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]


def build_av_cmd(mic: str, cam: str, out_mkv: str) -> list[str]:
    # spec 5.1: mic e a 1a entrada; nunca -t/-to/-frames (com -copyts gera arquivo vazio com rc 0)
    return ["ffmpeg", "-hide_banner", "-loglevel", "info", "-y", "-copyts",
            *_mic_input(mic), *_cam_input(cam),
            "-map", "1:v", "-map", "0:a", "-c:v", "copy", "-c:a", "pcm_s16le",
            "-avoid_negative_ts", "make_zero", "-f", "matroska", out_mkv,
            *_preview_output("1:v")]


def build_preview_cmd(cam: str) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "info", "-y", "-copyts", *_cam_input(cam),
            *_preview_output("0:v")]


def build_audio_cmd(mic: str, out_wav: str) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info", "-y", *_mic_input(mic),
            "-c:a", "pcm_s16le", "-f", "wav", out_wav]


class FrameReader(threading.Thread):
    # le frames rgb24 do stdout do ffmpeg ate o EOF; guarda so o ultimo; nunca toca no Tk
    def __init__(self, stream, frame_bytes: int = FRAME_BYTES, clock=time.monotonic):
        super().__init__(name="FrameReader", daemon=True)
        self.stream = stream
        self.frame_bytes = frame_bytes
        self._clock = clock
        self._lock = threading.Lock()
        self._frame: bytes | None = None
        self._distinct = collections.deque(maxlen=FPS_WINDOW + 1)
        self.frames = 0
        self.last_frame_monotonic: float | None = None
        self.error: Exception | None = None

    def run(self) -> None:
        buf = bytearray(self.frame_bytes)
        view = memoryview(buf)
        try:
            while self._fill(view):
                self._publish(buf)
        except (OSError, ValueError) as e:     # ValueError: stdout fechado pelo wait_stopped
            self.error = e
        finally:
            # leitor morto com o pipe aberto trava o ffmpeg e perde a tomada
            try:
                self.stream.close()
            except OSError:
                pass

    def _fill(self, view: memoryview) -> bool:
        got = 0
        while got < self.frame_bytes:
            n = self.stream.readinto(view[got:])
            if not n:
                return False        # EOF; frame parcial descartado
            got += n
        return True

    def _publish(self, buf: bytearray) -> None:
        now = self._clock()
        new = self._frame is None or buf != self._frame
        frame = bytes(buf)
        with self._lock:
            self._frame = frame
            self.frames += 1
            self.last_frame_monotonic = now
            if new:
                self._distinct.append(now)

    def latest(self) -> tuple[int, bytes | None]:
        with self._lock:
            return self.frames, self._frame

    def fps(self) -> float | None:
        # so frames diferentes: o filtro fps=15 duplica frames quando a camera entrega menos
        with self._lock:
            times = list(self._distinct)
        if len(times) < FPS_MIN_FRAMES or times[-1] <= times[0]:
            return None
        return (len(times) - 1) / (times[-1] - times[0])


def _count(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _seconds(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _decimal(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def verify_capture(path: str, need_video: bool) -> str | None:
    # conta pacotes: um MKV cortado mantem a duracao do cabecalho sem ter nenhum pacote
    name = os.path.basename(path)
    try:
        if os.path.getsize(path) == 0:
            return MSG_EMPTY
    except OSError:
        return f"Gravação não encontrada: {name}"
    try:
        data = procs.ffprobe_json(path, "-count_packets", "-show_entries",
                                  "stream=codec_type,nb_read_packets:format=duration")
    except procs.ProcError as e:
        return e.message
    packets = {"audio": 0, "video": 0}
    for s in data.get("streams", []):
        if s.get("codec_type") in packets:
            packets[s["codec_type"]] += _count(s.get("nb_read_packets"))
    if not any(packets.values()):
        return MSG_EMPTY
    if not packets["audio"]:
        return "A gravação não tem áudio"
    if need_video and not packets["video"]:
        return "A gravação não tem vídeo"
    if not _seconds(data.get("format", {}).get("duration")) > 0:
        return "A gravação ficou com duração zero"
    return None


def check_mic(pid: int, expected_index: int) -> str | None:
    # o setpriv executa o ffmpeg no mesmo processo: o pid do Popen e o do ffmpeg
    return None if devices.mic_source_of_pid(pid) == expected_index else MSG_MIC


def preflight(mic: str, cam: str, rec_dir: str = REC_DIR) -> tuple[int | None, str | None]:
    # (indice do mic, None) ou (None, motivo PT); cam vazio = so audio
    index = next((s.index for s in devices.list_mics() if s.name == mic), None)
    if index is None:
        return None, "Microfone não encontrado — escolha outro na lista"
    if cam and not os.path.exists(cam):
        return None, procs.FFMPEG_EXIT_MSGS[254]
    free = devices.free_bytes(rec_dir)
    if free < MIN_FREE_BYTES:
        return None, (f"Pouco espaço em disco: {_decimal(free / 1024**3)} GB livres "
                      f"(mínimo {MIN_FREE_BYTES // 1024**3} GB)")
    return index, None


class Watchdog:
    # checagens periodicas da GUI durante a gravacao; tempos em time.monotonic()
    def __init__(self, frame_timeout: float = 2.0, first_frame_timeout: float = 5.0,
                 max_s: float = MAX_TAKE_S, min_fps: float = MIN_FPS, fps_grace_s: float = FPS_GRACE_S):
        self.frame_timeout = frame_timeout
        self.first_frame_timeout = first_frame_timeout
        self.max_s = max_s
        self.min_fps = min_fps
        self.fps_grace_s = fps_grace_s

    def check_frames(self, now: float, last_frame: float | None, started: float) -> str | None:
        if last_frame is None:
            return MSG_NO_FRAME if now - started > self.first_frame_timeout else None
        return MSG_STALL if now - last_frame > self.frame_timeout else None

    def check_duration(self, now: float, started: float) -> str | None:
        if now - started >= self.max_s:
            return f"Limite de {self.max_s / 60:g} min atingido — gravação encerrada"
        return None

    def check_fps(self, fps: float | None, now: float, started: float) -> str | None:
        if fps is None or fps >= self.min_fps or now - started < self.fps_grace_s:
            return None
        return f"Câmera a {_decimal(fps)} fps (abaixo de {self.min_fps:g}) — pouca luz?"
