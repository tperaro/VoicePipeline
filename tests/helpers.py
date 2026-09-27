"""Midia sintetica para os testes (porte de probes/2026-09-26/render/mktake.sh, onset.py e measure.py).

So stdlib + ffmpeg/ffprobe no PATH.
"""

import array
import json
import os
import subprocess
import sys
import tempfile

PY = sys.executable
FPS = 30
SR = 48000
BEEP_S = 0.05
BEEP_THRESHOLD = 0.3


def _run(argv: list[str], timeout: float = 120) -> bytes:
    r = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace")[-2000:]
        raise RuntimeError(f"{argv[0]} falhou (rc={r.returncode}): {err}")
    return r.stdout


def _ffmpeg(*args: str) -> bytes:
    return _run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args])


def make_synthetic_take(path: str, audio_offset_s: float = 0.0, duration_s: float = 10.0, flash_frame: int = 90,
                        shift_s: float = 0.0, vfr: bool = False, size: str = "1280x720") -> str:
    # flash branco no frame flash_frame (t = flash_frame/30 do video) e bip de 1 kHz no mesmo instante absoluto
    flash_t = flash_frame / FPS
    beep_t = flash_t - audio_offset_s          # instante do bip no tempo local do audio
    audio_dur = duration_s - audio_offset_s    # os dois streams terminam juntos
    if not 0 <= flash_frame < round(duration_s * FPS):
        raise ValueError("flash_frame fora do video")
    if beep_t < 0 or beep_t + BEEP_S > audio_dur:
        raise ValueError("bip fora do audio: ajuste audio_offset_s")
    vf = f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='eq(n,{flash_frame})'"
    if vfr:
        # maior parte a 15 fps (frames pares) + rajada de 30 fps em volta do flash
        vf += f",select='not(mod(n,2))+between(n,{flash_frame - 9},{flash_frame + 9})'"
    expr = (f"0.01*(2*random(0)-1)+0.8*sin(2*PI*1000*t)"
            f"*between(t,{beep_t:.6f},{beep_t + BEEP_S:.6f})")
    folder = os.path.dirname(os.path.abspath(path))
    with tempfile.TemporaryDirectory(dir=folder) as tmp:
        v = os.path.join(tmp, "v.mkv")
        a = os.path.join(tmp, "a.wav")
        _ffmpeg("-f", "lavfi", "-i", f"testsrc2=s={size}:r={FPS}:d={duration_s}", "-vf", vf,
                "-fps_mode", "passthrough", "-c:v", "mjpeg", "-q:v", "3", "-pix_fmt", "yuvj422p", v)
        _ffmpeg("-f", "lavfi", "-i", f"aevalsrc='{expr}':s={SR}:c=mono:d={audio_dur:.6f}",
                "-c:a", "pcm_s16le", a)
        v_off = max(0.0, -audio_offset_s)
        a_off = max(0.0, audio_offset_s)
        _ffmpeg("-itsoffset", f"{v_off:.6f}", "-i", v, "-itsoffset", f"{a_off:.6f}", "-i", a,
                "-map", "0:v", "-map", "1:a", "-c", "copy", "-output_ts_offset", f"{shift_s:.6f}",
                "-f", "matroska", path)
    return path


def make_mjpeg_take(path: str, fps: int = 15, duration_s: float = 4.0, flash_packet: int = 30,
                    video_start_s: float = 0.0, stub_first_packet: bool = False, size: str = "640x360",
                    stub_bytes: int = 200) -> str:
    # MKV no layout do gravador: audio em t=0, video MJPEG a `fps` comecando em video_start_s; flash branco no
    # pacote flash_packet e bip no mesmo instante. stub_first_packet troca o pacote 0 pelos primeiros
    # stub_bytes do JPEG (so cabecalho): o decoder descarta ("No JPEG data found"), mas o pts continua no MKV
    n = round(duration_s * fps)
    if not 1 < flash_packet < n:
        raise ValueError("flash_packet fora do video")
    beep_t = video_start_s + flash_packet / fps
    audio_dur = video_start_s + duration_s
    vf = f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='eq(n,{flash_packet})'"
    expr = (f"0.01*(2*random(0)-1)+0.8*sin(2*PI*1000*t)"
            f"*between(t,{beep_t:.6f},{beep_t + BEEP_S:.6f})")
    folder = os.path.dirname(os.path.abspath(path))
    with tempfile.TemporaryDirectory(dir=folder) as tmp:
        frames = os.path.join(tmp, "%05d.jpg")
        a = os.path.join(tmp, "a.wav")
        _ffmpeg("-f", "lavfi", "-i", f"testsrc2=s={size}:r={fps}:d={duration_s}", "-vf", vf,
                "-c:v", "mjpeg", "-q:v", "3", "-pix_fmt", "yuvj422p", "-f", "image2", frames)
        if stub_first_packet:
            first = frames % 1
            with open(first, "rb") as f:
                head = f.read(stub_bytes)
            with open(first, "wb") as f:
                f.write(head)
        _ffmpeg("-f", "lavfi", "-i", f"aevalsrc='{expr}':s={SR}:c=mono:d={audio_dur:.6f}", "-c:a", "pcm_s16le", a)
        _ffmpeg("-framerate", str(fps), "-itsoffset", f"{video_start_s:.6f}", "-i", frames, "-i", a,
                "-map", "0:v", "-map", "1:a", "-c", "copy", "-f", "matroska", path)
    return path


def beep_onset_s(wav_or_media: str) -> float:
    # tempo do 1o sample com |x| > 0.3, contado do inicio do audio decodificado (mono 48k)
    raw = _run(["ffmpeg", "-nostdin", "-v", "error", "-i", wav_or_media, "-map", "0:a:0", "-ac", "1",
                "-ar", str(SR), "-f", "s16le", "-"])
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) // 2 * 2])
    if sys.byteorder != "little":
        samples.byteswap()
    limit = BEEP_THRESHOLD * 32768
    for i, s in enumerate(samples):
        if s > limit or s < -limit:
            return i / SR
    raise ValueError(f"bip não encontrado em {wav_or_media}")


def flash_frame_index(media: str) -> int:
    # indice (decodificado, sem duplicar frames) do frame mais claro; recorte do topo evita a faixa e o selo
    side = 16
    raw = _run(["ffmpeg", "-nostdin", "-v", "error", "-i", media, "-map", "0:v:0",
                "-vf", f"crop=iw/2:ih/2:iw/4:ih/8,scale={side}:{side},format=gray",
                "-fps_mode", "passthrough", "-f", "rawvideo", "-"])
    n = side * side
    sums = [sum(raw[i:i + n]) for i in range(0, len(raw) - n + 1, n)]
    if not sums:
        raise ValueError(f"nenhum frame em {media}")
    return max(range(len(sums)), key=sums.__getitem__)


def ffprobe_streams(path: str) -> dict:
    out = _run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path])
    return json.loads(out)
