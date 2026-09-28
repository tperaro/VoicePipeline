import errno
import os
import subprocess
import sys
import tempfile
import time
import unittest

from studio import procs
from studio.config import BASE_DIR
from tests import helpers


def gone(pid: int, timeout: float = 3.0) -> bool:
    # processo sumiu ou virou zumbi
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(f"/proc/{pid}/stat") as f:
                state = f.read().rsplit(")", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):   # ESRCH: morreu entre o open e o read
            return True
        if state in ("Z", "X"):
            return True
        time.sleep(0.05)
    return False


def exec_done(pid: int, name: str, timeout: float = 5.0) -> bool:
    # o setpriv arma o pdeathsig antes do exec: comm == name prova que o prctl ja rodou
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(f"/proc/{pid}/comm") as f:
                if f.read().strip() == name:
                    return True
        except (FileNotFoundError, ProcessLookupError):
            return False
        time.sleep(0.01)
    return False


class SpawnRunTest(unittest.TestCase):
    def test_guarded(self):
        self.assertEqual(procs.SETPRIV, ["setpriv", "--pdeathsig", "TERM", "--"])
        self.assertEqual(procs.guarded(["ffmpeg", "-i", "x"]),
                         ["setpriv", "--pdeathsig", "TERM", "--", "ffmpeg", "-i", "x"])

    def test_spawn_prefixes_and_closes_stdin(self):
        p = procs.spawn(["cat"], stdout=subprocess.PIPE)
        out, _ = p.communicate(timeout=5)
        self.assertEqual(p.args[:4], procs.SETPRIV)
        self.assertEqual((p.returncode, out), (0, b""))

    def test_run_captures_text(self):
        r = procs.run(["sh", "-c", "echo olá; echo erro >&2; exit 3"], timeout=5)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout, "olá\n")
        self.assertEqual(r.stderr, "erro\n")
        self.assertEqual(r.args[:4], procs.SETPRIV)

    def test_run_stdin_is_devnull(self):
        r = procs.run(["cat"], timeout=5)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_run_timeout_kills_process_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = os.path.join(tmp, "pid")
            t0 = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                procs.run(["sh", "-c", f"sleep 30 & echo $! > {pidfile}; wait"], timeout=0.5)
            self.assertLess(time.monotonic() - t0, 5)
            with open(pidfile) as f:
                grandchild = int(f.read())
        self.assertTrue(gone(grandchild), "o neto (sleep) sobreviveu ao timeout")

    def test_child_dies_with_parent(self):
        code = ("import sys, time; sys.path.insert(0, sys.argv[1]); from studio import procs; "
                "p = procs.spawn(['sleep', '30']); print(p.pid, flush=True); time.sleep(30)")
        parent = subprocess.Popen([sys.executable, "-c", code, BASE_DIR], stdout=subprocess.PIPE, text=True)
        try:
            child = int(parent.stdout.readline())
            # matar o pai antes do exec do sleep deixaria o filho orfao sem pdeathsig (corrida do teste)
            self.assertTrue(exec_done(child, "sleep"), "o setpriv não chegou a executar o sleep")
        finally:
            parent.kill()
            parent.wait()
            parent.stdout.close()
        self.assertTrue(gone(child), "o filho sobreviveu a morte do pai (pdeathsig)")


class ProcErrorTest(unittest.TestCase):
    def test_fields(self):
        e = procs.ProcError("Falhou", rc=2, detail="linha")
        self.assertEqual((e.message, e.rc, e.detail, str(e)), ("Falhou", 2, "linha", "Falhou"))
        e = procs.ProcError("Só mensagem")
        self.assertEqual((e.rc, e.detail), (None, ""))


class FfprobeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        cls.mkv = helpers.make_synthetic_take(os.path.join(d, "take.mkv"), duration_s=2.0, flash_frame=30,
                                              size="320x240")
        cls.wav = os.path.join(d, "a.wav")
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", cls.mkv, "-map", "0:a", "-c:a",
                        "pcm_s16le", cls.wav], check=True)
        cls.junk = os.path.join(d, "junk.mkv")
        with open(cls.junk, "wb") as f:
            f.write(b"isto nao e um video" * 100)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_ffprobe_json(self):
        data = procs.ffprobe_json(self.mkv, "-show_entries", "stream=codec_type")
        self.assertEqual(sorted(s["codec_type"] for s in data["streams"]), ["audio", "video"])

    def test_ffprobe_json_error(self):
        with self.assertRaises(procs.ProcError) as cm:
            procs.ffprobe_json(self.junk, "-show_format")
        self.assertIn("junk.mkv", cm.exception.message)
        self.assertEqual(cm.exception.rc, 1)
        self.assertIn("Invalid data", cm.exception.detail)

    def test_media_info_av(self):
        info = procs.media_info(self.mkv)
        self.assertEqual(info["video"]["codec_name"], "mjpeg")
        self.assertEqual(info["audio"]["codec_name"], "pcm_s16le")
        self.assertGreater(float(info["format"]["duration"]), 1.9)

    def test_media_info_audio_only(self):
        info = procs.media_info(self.wav)
        self.assertIsNone(info["video"])
        self.assertEqual(info["audio"]["sample_rate"], "48000")


class TailAndMessagesTest(unittest.TestCase):
    def test_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "log")
            with open(path, "wb") as f:
                f.write(b"x" * 200000 + b"\n")
                f.write("".join(f"linha {i}\n" for i in range(30)).encode())
                f.write(b"quebrado \xff\n")
            text = procs.tail(path, 3)
            self.assertEqual(text.splitlines(), ["linha 28", "linha 29", "quebrado �"])
            self.assertEqual(len(procs.tail(path).splitlines()), 15)
            self.assertEqual(procs.tail(os.path.join(tmp, "nao_existe")), "")

    def test_exit_codes(self):
        self.assertEqual(procs.ffmpeg_exit_message(240), "Câmera em uso por outro programa (Meet/Zoom/OBS?)")
        self.assertEqual(procs.ffmpeg_exit_message(254), "Câmera não encontrada")
        self.assertEqual(procs.ffmpeg_exit_message(231), "Dispositivo de vídeo errado")

    def test_exit_message_from_log(self):
        log = "[video4linux2] ioctl(VIDIOC_G_INPUT): Device or resource busy\n"
        self.assertEqual(procs.ffmpeg_exit_message(1, log), procs.FFMPEG_EXIT_MSGS[240])
        msg = procs.ffmpeg_exit_message(1, "algo\nError opening output file x.mkv\n\n")
        self.assertEqual(msg, "ffmpeg falhou (código 1): Error opening output file x.mkv")
        self.assertEqual(procs.ffmpeg_exit_message(-9), "ffmpeg foi interrompido (sinal 9)")
        self.assertEqual(procs.ffmpeg_exit_message(1), "ffmpeg falhou (código 1)")


class OsErrorMessageTest(unittest.TestCase):
    def test_disk_full(self):
        self.assertEqual(procs.MSG_DISK_FULL, "Disco cheio — libere espaço")
        for code in (errno.ENOSPC, errno.EDQUOT):
            e = OSError(code, os.strerror(code), "/x/recordings/2026-09-26_101500/render.part.mp4")
            self.assertEqual(procs.os_error_message(e), "Disco cheio — libere espaço")

    def test_other_errors(self):
        e = PermissionError(errno.EACCES, "Permission denied", "/x/videos_finais/")
        self.assertEqual(procs.os_error_message(e), "Erro ao acessar o disco (videos_finais): Permission denied")
        self.assertEqual(procs.os_error_message(OSError("sem errno")), "Erro ao acessar o disco: sem errno")


if __name__ == "__main__":
    unittest.main()
