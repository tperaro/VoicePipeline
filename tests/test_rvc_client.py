import os
import shutil
import signal
import tempfile
import time
import unittest
from unittest import mock

import numpy as np
import soundfile as sf

from studio.rvc_client import RvcClient, RvcError
from tests.test_rvc_worker import check_identity, speech_like, write_wav

FAKE = {"STUDIO_RVC_FAKE": "1"}
REAL_TAKE = os.path.expanduser("~/orochi-ia-homenagem/recordings/take_20260923_150423_boosted.wav")


def wait_dead(client: RvcClient, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while client.alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    return not client.alive()


def fake_python(folder: str, body: str) -> str:
    # "python" falso: ignora "-m studio.rvc_worker" e roda o corpo em sh
    path = os.path.join(folder, "fakepy")
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body + "\n")
    os.chmod(path, 0o755)
    return path


class FakeWorkerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.log = os.path.join(self.dir, "rvc.log")
        self.c = RvcClient(log_path=self.log, env=FAKE)
        self.c.start()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def read_log(self) -> str:
        with open(self.log, encoding="utf-8", errors="replace") as f:
            return f.read()

    def test_ping(self):
        r = self.c.request("ping", timeout=30)
        self.assertTrue(r["ok"])
        self.assertEqual((r["fake"], r["torch"]), (True, False))
        self.assertEqual(r["pid"], self.c._proc.pid)       # o setpriv faz exec: mesmo pid
        self.assertEqual(self.c._proc.args[:4], ["setpriv", "--pdeathsig", "TERM", "--"])
        self.assertTrue(self.c.alive())

    def test_load(self):
        self.assertTrue(self.c.load()["ok"])

    def test_convert_short_single_piece(self):
        inp = write_wav(os.path.join(self.dir, "audio.wav"), speech_like(12.0))
        out = os.path.join(self.dir, "silvio.wav")
        r = self.c.convert(inp, out, "silvio")
        self.assertEqual((r["pedacos"], r["sr"]), (1, 40000))
        check_identity(self, inp, out, r)
        self.assertIn("[fake applio]", self.read_log())    # o lixo do stdout foi para o log

    def test_convert_long_in_pieces(self):
        inp = write_wav(os.path.join(self.dir, "audio.wav"), speech_like(90.0))
        out = os.path.join(self.dir, "silvio.wav")
        r = self.c.convert(inp, out, "silvio")
        self.assertGreater(r["pedacos"], 1)
        check_identity(self, inp, out, r)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "silvio.part.wav")))

    def test_missing_file_is_error_and_worker_survives(self):
        with self.assertRaises(RvcError) as cm:
            self.c.convert(os.path.join(self.dir, "sumiu.wav"), os.path.join(self.dir, "o.wav"), "silvio")
        self.assertIn("não encontrado", str(cm.exception))
        self.assertIn("sumiu.wav", str(cm.exception))
        self.assertTrue(self.c.alive())
        self.assertTrue(self.c.request("ping")["ok"])

    def test_unknown_op(self):
        with self.assertRaises(RvcError) as cm:
            self.c.request("voar")
        self.assertEqual(str(cm.exception), "Operação desconhecida: voar")

    def test_killed_worker(self):
        os.kill(self.c._proc.pid, signal.SIGKILL)
        self.assertTrue(wait_dead(self.c))
        with self.assertRaises(RvcError) as cm:
            self.c.request("ping")
        self.assertIn("não está rodando", str(cm.exception))
        self.c.start()                                       # a GUI recria no proximo job
        self.assertTrue(self.c.request("ping", timeout=30)["ok"])

    def test_close_waits_clean_exit(self):
        p = self.c._proc
        self.c.close()
        self.assertEqual(p.returncode, 0)
        self.assertFalse(self.c.alive())
        with self.assertRaises(RvcError):
            self.c.request("ping")
        self.c.close()                                       # idempotente
        self.assertIn("worker RVC iniciado", self.read_log())


class StubWorkerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def client(self, body: str) -> RvcClient:
        c = RvcClient(python=fake_python(self.dir, body), log_path=os.path.join(self.dir, "rvc.log"))
        c.start()
        self.addCleanup(c.close)
        return c

    def test_not_started(self):
        with self.assertRaises(RvcError) as cm:
            RvcClient(log_path=os.path.join(self.dir, "rvc.log")).request("ping")
        self.assertIn("não está rodando", str(cm.exception))

    def test_timeout_kills_worker(self):
        c = self.client("exec sleep 30")
        t0 = time.monotonic()
        with self.assertRaises(RvcError) as cm:
            c.request("ping", timeout=0.5)
        self.assertLess(time.monotonic() - t0, 3)
        self.assertIn("demorou demais", str(cm.exception))
        self.assertTrue(wait_dead(c))

    def test_worker_dies_mid_request(self):
        c = self.client("read line; echo morrendo >&2; exit 3")
        with self.assertRaises(RvcError) as cm:
            c.request("ping", timeout=10)
        self.assertIn("código 3", str(cm.exception))
        self.assertFalse(c.alive())
        with open(os.path.join(self.dir, "rvc.log")) as f:
            self.assertIn("morrendo", f.read())

    def test_garbage_on_protocol_is_error(self):
        c = self.client('read line; echo "Loading model..."; exec sleep 30')
        with self.assertRaises(RvcError) as cm:
            c.request("ping", timeout=10)
        self.assertIn("Resposta inválida", str(cm.exception))
        self.assertTrue(wait_dead(c))

    def test_stale_id_is_skipped(self):
        c = self.client("read line; echo '{\"id\": \"99\", \"ok\": true}'; "
                        "echo '{\"id\": \"1\", \"ok\": true, \"x\": 7}'; read line")
        self.assertEqual(c.request("ping", timeout=10)["x"], 7)

    def test_close_terminates_worker_that_ignores_eof(self):
        c = self.client("exec sleep 30")
        p = c._proc
        t0 = time.monotonic()
        with mock.patch("studio.rvc_client.CLOSE_WAIT_S", 0.3):
            c.close()
        self.assertLess(time.monotonic() - t0, 3)
        self.assertEqual(p.returncode, -signal.SIGTERM)
        self.assertFalse(c.alive())


@unittest.skipUnless(os.environ.get("RUN_HARDWARE") == "1", "precisa de GPU e do modelo silvio (RUN_HARDWARE=1)")
class RealRvcTest(unittest.TestCase):
    def test_silvio_6s(self):
        if not os.path.isfile(REAL_TAKE):
            self.skipTest(f"gravação de teste ausente: {REAL_TAKE}")
        with tempfile.TemporaryDirectory() as d:
            copy = shutil.copy(REAL_TAKE, os.path.join(d, "take.wav"))
            x, sr = sf.read(copy)
            inp = write_wav(os.path.join(d, "audio.wav"), x[:6 * sr], sr)
            out = os.path.join(d, "silvio.wav")
            c = RvcClient(log_path=os.path.join(d, "rvc.log"), env={"PYTHONDONTWRITEBYTECODE": "1"})
            c.start()
            try:
                self.assertTrue(c.load()["ok"])
                t0 = time.monotonic()
                r = c.convert(inp, out, "silvio")
                wall = time.monotonic() - t0
                ping = c.request("ping")
            finally:
                c.close()
            info = sf.info(out)
            y, _ = sf.read(out)
            print(f"\n[real] {r} conversão {wall:.2f} s, saída {info.duration:.4f} s @ {info.samplerate}")
        self.assertEqual((r["sr"], info.samplerate, r["pedacos"]), (40000, 40000, 1))
        self.assertAlmostEqual(info.duration, 6.0, delta=0.02)
        self.assertEqual((ping["fake"], ping["torch"]), (False, True))
        self.assertGreater(float(np.sqrt(np.mean(y ** 2))), 0.001)        # nao e silencio


if __name__ == "__main__":
    unittest.main()
