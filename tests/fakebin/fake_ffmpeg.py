"""ffmpeg falso para os testes de captura. Rodar com python; o argv e ignorado.

Manda frames rgb24 480x270 (frame i = byte i % 251 repetido) no stdout, em pedacos irregulares, a FAKE_FPS.
Le "q" do stdin. FAKE_MODE:
  normal      sai 0 ao ler q (224 se o stdout ja quebrou); SIGTERM -> 255
  ignore_q    ignora q; so sai com SIGTERM (255)
  hang        ignora q e SIGTERM; so SIGKILL
  fail240     escreve "Device or resource busy" no stderr e sai 240 em 50 ms
  stall       para de mandar frames depois de STALL_AFTER; q -> 0
  need_close  como o ffmpeg preso escrevendo no pipe: so atende o q depois que o stdout quebra (sai 224)
"""

import os
import random
import signal
import threading
import time

W, H = 480, 270
FRAME = W * H * 3
MODE = os.environ.get("FAKE_MODE", "normal")
FPS = float(os.environ.get("FAKE_FPS", "15"))
MAX_S = float(os.environ.get("FAKE_MAX_S", "30"))   # trava de seguranca: nunca vira orfao eterno
STALL_AFTER = 5

q_seen = threading.Event()


def log(msg: str) -> None:
    os.write(2, (msg + "\n").encode())


def read_stdin() -> None:
    while True:
        try:
            data = os.read(0, 64)
        except OSError:
            return
        if not data:
            return
        for _ in range(data.count(b"q")):
            log("q recebido")
            q_seen.set()


def on_term(signum, frame) -> None:
    log(f"Exiting normally, received signal {signum}.")
    os._exit(255)


def write_frame(i: int, rng: random.Random) -> None:
    view = memoryview(bytes([i % 251]) * FRAME)
    while view:
        n = os.write(1, view[:rng.randint(1, 100_000)])
        view = view[n:]


def main() -> None:
    log(f"ffmpeg falso: modo {MODE}")
    if MODE == "fail240":
        log("[video4linux2,v4l2 @ 0x5f0] ioctl(VIDIOC_STREAMON): Device or resource busy")
        log("[in#1 @ 0x5f1] Error opening input: Device or resource busy")
        time.sleep(0.05)
        os._exit(240)
    signal.signal(signal.SIGTERM, signal.SIG_IGN if MODE == "hang" else on_term)
    threading.Thread(target=read_stdin, daemon=True).start()
    rng = random.Random(6)
    parent = os.getppid()
    deadline = time.monotonic() + MAX_S
    broken = False
    i = 0
    while time.monotonic() < deadline and os.getppid() == parent:
        if q_seen.is_set() and (MODE in ("normal", "stall") or (MODE == "need_close" and broken)):
            log("ffmpeg falso: saindo pelo q")
            os._exit(224 if broken else 0)
        if not broken and not (MODE == "stall" and i >= STALL_AFTER):
            try:
                write_frame(i, rng)
                i += 1
            except BrokenPipeError:
                broken = True
                log("Error submitting a packet to the muxer: Broken pipe")
        time.sleep(1 / FPS)
    log("ffmpeg falso: tempo esgotado ou pai morreu")
    os._exit(3)


if __name__ == "__main__":
    main()
