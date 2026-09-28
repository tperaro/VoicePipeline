import contextlib
import errno
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
import soundfile as sf

from studio import rvc_worker as w
from studio.config import BASE_DIR

SR = 48000


def speech_like(duration_s: float, sr: int = SR, seed: int = 1) -> np.ndarray:
    # tom de 220 Hz com pausas irregulares (da pontos quietos para os cortes) + ruido baixo
    rng = np.random.default_rng(seed)
    n = int(round(duration_s * sr))
    env = np.zeros(n)
    pos = 0
    while pos < n:
        on, off = int(rng.uniform(0.4, 2.5) * sr), int(rng.uniform(0.05, 0.4) * sr)
        env[pos:pos + on] = 1.0
        pos += on + off
    t = np.arange(n) / sr
    return 0.3 * np.sin(2 * np.pi * 220 * t) * env + 0.002 * rng.standard_normal(n)


def write_wav(path: str, x: np.ndarray, sr: int = SR) -> str:
    sf.write(path, x, sr, subtype="PCM_16")
    return path


def outside_crossfades(n: int, cuts: list[float], sr: int, xf_s: float = w.XF_S) -> np.ndarray:
    mask = np.ones(n, bool)
    xf = int(round(xf_s * sr))
    for c in cuts[1:-1]:
        s = int(round(c * sr))
        mask[s:s + xf] = False
    return mask


def check_identity(tc: unittest.TestCase, inp: str, out: str, res: dict) -> None:
    # saida do conversor identidade == entrada reamostrada a 40 kHz, fora das janelas de crossfade
    x, sr = sf.read(inp)
    y, osr = sf.read(out)
    total = len(x) / sr
    tc.assertEqual((res["sr"], osr), (40000, 40000))
    tc.assertLessEqual(abs(len(y) - total * osr), 1)
    tc.assertAlmostEqual(res["duracao"], total, delta=1 / osr)
    cuts = w.plan_cuts(w.frame_energy(x, sr), total) if total > w.MAX_SINGLE_S else [0.0, total]
    tc.assertEqual(res["pedacos"], len(cuts) - 1)
    ref = np.interp(np.arange(len(y)) * (sr / osr), np.arange(len(x)), x)
    mask = outside_crossfades(len(y), cuts, osr)
    tc.assertLessEqual(float(np.abs(y[mask] - ref[mask]).max()), 2 / 32768)
    tc.assertGreater(float(np.abs(y).max()), 0.1)


@contextlib.contextmanager
def fd1_silenced():
    # o conversor falso suja o fd 1 de proposito; no teste em processo o lixo vai para /dev/null
    sys.stdout.flush()
    saved = os.dup(1)
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 1)
    os.close(null)
    try:
        yield
    finally:
        sys.stdout.flush()
        os.dup2(saved, 1)
        os.close(saved)


def quiet_fake(src: str, dst: str) -> None:
    with fd1_silenced():
        w.fake_infer(src, dst)


class ConstantsTest(unittest.TestCase):
    def test_values(self):
        self.assertEqual((w.MAX_SINGLE_S, w.MAX_PIECE_S, w.SEARCH_S, w.CTX_S, w.XF_S),
                         (40.0, 30.0, 3.0, 0.5, 0.005))
        self.assertEqual(w.APPLIO_PARAMS["split_audio"], False)
        self.assertEqual(w.APPLIO_PARAMS["f0_method"], "rmvpe")
        self.assertEqual(w.APPLIO_PARAMS["index_rate"], 0.75)
        self.assertEqual(w.APPLIO_PARAMS["protect"], 0.33)


class PlanCutsTest(unittest.TestCase):
    def test_frame_energy(self):
        x = np.concatenate([np.full(480, 0.5), np.zeros(480), np.full(480, -0.25), np.zeros(100)])
        np.testing.assert_allclose(w.frame_energy(x, SR), [0.5, 0.0, 0.25])

    def test_short_take_is_one_piece(self):
        self.assertEqual(w.plan_cuts(np.ones(2500), 25.0), [0.0, 25.0])
        self.assertEqual(w.plan_cuts(np.ones(3000), 30.0), [0.0, 30.0])

    def test_cuts_at_quietest_frame_within_window(self):
        e = np.ones(9000)          # 90 s em quadros de 10 ms
        e[2300] = 0.0              # 23,00 s: mais quieto, mas fora da janela 24-30 s
        e[2550] = 0.1              # 25,50 s
        e[5200] = 0.1              # 52,00 s (janela 49,5-55,5 s)
        e[7900] = 0.1              # 79,00 s (janela 76-82 s)
        self.assertEqual(w.plan_cuts(e, 90.0), [0.0, 25.5, 52.0, 79.0, 90.0])

    def test_random_energy_respects_limits(self):
        e = np.random.default_rng(7).random(30000)      # 300 s
        cuts = w.plan_cuts(e, 300.0)
        self.assertEqual((cuts[0], cuts[-1]), (0.0, 300.0))
        self.assertGreater(len(cuts), 10)
        for prev, cut in zip(cuts, cuts[1:-1]):
            p, i = int(round(prev * 100)), int(round(cut * 100))
            self.assertTrue(p + 2400 <= i < p + 3000, (prev, cut))
            self.assertEqual(e[i], e[p + 2400:p + 3000].min())
        self.assertTrue(all(0 < b - a <= w.MAX_PIECE_S for a, b in zip(cuts, cuts[1:])))


class AssembleTest(unittest.TestCase):
    def test_identity_pieces_rebuild_signal(self):
        sr = 8000
        x = np.random.default_rng(3).uniform(-0.5, 0.5, 95 * sr)
        total = len(x) / sr
        cuts = w.plan_cuts(w.frame_energy(x, sr), total)
        pieces = []
        for a, b in zip(cuts, cuts[1:]):
            ca, cb = max(0.0, a - w.CTX_S), min(total, b + w.CTX_S)
            pieces.append((a, b, ca, x[int(round(ca * sr)):int(round(cb * sr))]))
        y = w.assemble(pieces, total, sr)
        self.assertGreater(len(pieces), 1)
        self.assertEqual(len(y), len(x))
        mask = outside_crossfades(len(y), cuts, sr)
        np.testing.assert_array_equal(y[mask], x[mask])
        np.testing.assert_allclose(y, x, atol=1e-12)

    def test_crossfade_is_linear_at_the_join(self):
        pieces = [(0.0, 1.0, 0.0, np.ones(1500)), (1.0, 2.0, 0.5, np.zeros(1500))]
        y = w.assemble(pieces, 2.0, 1000, xf_s=0.01)
        self.assertEqual(len(y), 2000)
        np.testing.assert_array_equal(y[:1000], 1.0)
        np.testing.assert_allclose(y[1000:1010], 1 - np.linspace(0, 1, 10))
        np.testing.assert_array_equal(y[1010:], 0.0)

    def test_short_piece_is_zero_padded(self):
        # o RVC perde ate 10 ms por pedaco; a duracao final continua exata
        y = w.assemble([(0.0, 2.0, 0.0, np.ones(1990))], 2.0, 1000)
        self.assertEqual(len(y), 2000)
        np.testing.assert_array_equal(y[:1990], 1.0)
        np.testing.assert_array_equal(y[1990:], 0.0)


class ConvertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.inp = os.path.join(self.dir, "audio.wav")
        self.out = os.path.join(self.dir, "silvio.wav")

    def tearDown(self):
        self.tmp.cleanup()

    def test_short_take_single_piece(self):
        write_wav(self.inp, speech_like(12.0))
        res = w.convert(self.inp, self.out, "silvio", run_infer=quiet_fake)
        self.assertEqual(res["pedacos"], 1)
        check_identity(self, self.inp, self.out, res)
        self.assertEqual(sorted(os.listdir(self.dir)), ["audio.wav", "silvio.wav"])

    def test_long_take_in_pieces(self):
        write_wav(self.inp, speech_like(90.0))
        res = w.convert(self.inp, self.out, "silvio", run_infer=quiet_fake)
        self.assertGreater(res["pedacos"], 1)
        check_identity(self, self.inp, self.out, res)
        self.assertEqual(sorted(os.listdir(self.dir)), ["audio.wav", "silvio.wav"])

    def test_missing_input(self):
        with self.assertRaises(w.ConvertError) as cm:
            w.convert(os.path.join(self.dir, "nao_existe.wav"), self.out, "silvio", run_infer=quiet_fake)
        self.assertIn("não encontrado", str(cm.exception))
        self.assertIn("nao_existe.wav", str(cm.exception))

    def test_unknown_model(self):
        write_wav(self.inp, speech_like(1.0))
        with self.assertRaises(w.ConvertError) as cm:
            w.convert(self.inp, self.out, "xyz", run_infer=quiet_fake)
        self.assertEqual(str(cm.exception), "Modelo desconhecido: xyz")

    def test_relative_path_refused(self):
        with self.assertRaises(w.ConvertError) as cm:
            w.convert("audio.wav", self.out, "silvio", run_infer=quiet_fake)
        self.assertIn("absolutos", str(cm.exception))

    def test_failed_infer_keeps_previous_output(self):
        write_wav(self.inp, speech_like(3.0))
        write_wav(self.out, np.zeros(100))

        def boom(src, dst):
            raise RuntimeError("CUDA out of memory")

        with self.assertRaises(RuntimeError):
            w.convert(self.inp, self.out, "silvio", run_infer=boom)
        self.assertEqual(sorted(os.listdir(self.dir)), ["audio.wav", "silvio.wav"])
        self.assertEqual(sf.info(self.out).frames, 100)

    def test_infer_without_output(self):
        write_wav(self.inp, speech_like(3.0))
        with self.assertRaises(w.ConvertError) as cm:
            w.convert(self.inp, self.out, "silvio", run_infer=lambda src, dst: None)
        self.assertIn("não gerou", str(cm.exception))
        self.assertEqual(os.listdir(self.dir), ["audio.wav"])

    def test_applio_infer_without_checkpoint(self):
        with self.assertRaises(w.ConvertError) as cm:
            w.applio_infer("silvio", logs_dir=self.dir)
        self.assertIn("Nenhum checkpoint", str(cm.exception))
        self.assertNotIn("torch", sys.modules)


class WorkerProtocolTest(unittest.TestCase):
    def test_raw_protocol_survives_stdout_garbage(self):
        with tempfile.TemporaryDirectory() as d:
            inp = write_wav(os.path.join(d, "a.wav"), speech_like(3.0))
            reqs = [{"id": "1", "op": "ping"}, "isto nao e json", {"id": "2", "op": "voar"},
                    {"id": "3", "op": "load"},
                    {"id": "4", "op": "convert", "input": inp, "output": os.path.join(d, "o.wav"),
                     "model": "silvio"},
                    {"id": "5", "op": "convert", "input": os.path.join(d, "x.wav"),
                     "output": os.path.join(d, "p.wav"), "model": "silvio"},
                    {"id": "6", "op": "convert", "input": inp}]
            stdin = "".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in reqs)
            env = {**os.environ, "STUDIO_RVC_FAKE": "1"}
            p = subprocess.run([sys.executable, "-m", "studio.rvc_worker"], cwd=BASE_DIR, env=env,
                               input=stdin, capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr)
            resps = [json.loads(line) for line in p.stdout.splitlines()]   # so JSON no stdout
            self.assertEqual([r["id"] for r in resps], ["1", None, "2", "3", "4", "5", "6"])
            self.assertEqual([r["ok"] for r in resps], [True, False, False, True, True, False, False])
            self.assertEqual((resps[0]["fake"], resps[0]["torch"]), (True, False))   # sem torch no modo falso
            self.assertIsInstance(resps[0]["pid"], int)
            self.assertEqual(resps[2]["erro"], "Operação desconhecida: voar")
            self.assertEqual((resps[4]["sr"], resps[4]["pedacos"]), (40000, 1))
            self.assertIn("x.wav", resps[5]["erro"])
            self.assertEqual(resps[6]["erro"], "Requisição de conversão incompleta")
            self.assertTrue(os.path.isfile(os.path.join(d, "o.wav")))
        self.assertIn("[fake applio]", p.stderr)          # os prints foram para o stderr
        self.assertIn("lixo de biblioteca nativa", p.stderr)


class DiskFullTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.inp = write_wav(os.path.join(self.dir, "audio.wav"), speech_like(3.0))
        self.out = os.path.join(self.dir, "silvio.wav")

    def test_enospc_in_infer_answers_pt_and_cleans_up(self):
        def full_disk(src, dst):
            with open(dst, "wb") as f:
                f.write(b"RIFF metade")
            raise OSError(errno.ENOSPC, "No space left on device")

        req = {"id": "9", "op": "convert", "input": self.inp, "output": self.out, "model": "silvio"}
        with mock.patch.dict(os.environ, {"STUDIO_RVC_FAKE": "1"}), mock.patch.object(w, "fake_infer", full_disk), \
                contextlib.redirect_stderr(io.StringIO()):
            resp = w.handle(req)
        self.assertEqual(resp, {"id": "9", "ok": False, "erro": "Disco cheio — libere espaço"})
        self.assertEqual(os.listdir(self.dir), ["audio.wav"])       # nem .rvc_* nem .part.wav

    def test_part_write_stops_midway(self):
        # com o disco cheio o soundfile nao levanta OSError: o write para no meio (AssertionError)
        real_write = sf.write

        def short_write(path, data, samplerate, **kwargs):
            if path.endswith(".part.wav"):
                real_write(path, data[:10], samplerate, **kwargs)
                raise AssertionError
            return real_write(path, data, samplerate, **kwargs)

        cases = ((0, "Disco cheio — libere espaço"), (50 * 1024**3, "Não foi possível gravar silvio.part.wav"))
        for free, want in cases:
            with self.subTest(free=free), mock.patch.object(sf, "write", short_write), \
                    mock.patch("shutil.disk_usage", return_value=mock.Mock(free=free)):
                with self.assertRaises(w.ConvertError) as cm:
                    w.convert(self.inp, self.out, "silvio", run_infer=quiet_fake)
                self.assertEqual(str(cm.exception), want)
                self.assertEqual(os.listdir(self.dir), ["audio.wav"])

    def test_rvc_without_output_on_full_disk(self):
        # o Applio so imprime o erro e volta sem gravar a saida: com o disco cheio a mensagem diz isso
        with mock.patch("shutil.disk_usage", return_value=mock.Mock(free=1024)):
            with self.assertRaises(w.ConvertError) as cm:
                w.convert(self.inp, self.out, "silvio", run_infer=lambda src, dst: None)
        self.assertEqual(str(cm.exception), "Disco cheio — libere espaço")
        self.assertEqual(os.listdir(self.dir), ["audio.wav"])


class RunInferRaisesOnFullDiskTest(unittest.TestCase):
    # Applio nao tem try/except em convert_audio (infer.py:203-356): o sf.write cru de cada pedaco
    # (infer.py:343) levanta AssertionError (escrita curta) ou SoundFileError/LibsndfileError (open
    # falho) direto de dentro do run_infer, sem deixar o .wav do pedaco no lugar.
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.inp = write_wav(os.path.join(self.dir, "audio.wav"), speech_like(3.0))
        self.out = os.path.join(self.dir, "silvio.wav")

    def test_assertionerror_from_run_infer_on_full_disk_is_disco_cheio(self):
        def boom(src, dst):
            raise AssertionError

        req = {"id": "9", "op": "convert", "input": self.inp, "output": self.out, "model": "silvio"}
        with mock.patch.dict(os.environ, {"STUDIO_RVC_FAKE": "1"}), mock.patch.object(w, "fake_infer", boom), \
                mock.patch("shutil.disk_usage", return_value=mock.Mock(free=0)), \
                contextlib.redirect_stderr(io.StringIO()):
            resp = w.handle(req)
        self.assertEqual(resp, {"id": "9", "ok": False, "erro": "Disco cheio — libere espaço"})
        self.assertEqual(os.listdir(self.dir), ["audio.wav"])       # nem .rvc_* nem .part.wav

    def test_soundfileerror_from_run_infer_on_full_disk_is_disco_cheio(self):
        def boom(src, dst):
            raise sf.LibsndfileError(2, f"Error opening '{dst}': ")

        req = {"id": "9", "op": "convert", "input": self.inp, "output": self.out, "model": "silvio"}
        with mock.patch.dict(os.environ, {"STUDIO_RVC_FAKE": "1"}), mock.patch.object(w, "fake_infer", boom), \
                mock.patch("shutil.disk_usage", return_value=mock.Mock(free=0)), \
                contextlib.redirect_stderr(io.StringIO()):
            resp = w.handle(req)
        self.assertEqual(resp, {"id": "9", "ok": False, "erro": "Disco cheio — libere espaço"})
        self.assertEqual(os.listdir(self.dir), ["audio.wav"])       # nem .rvc_* nem .part.wav

    def test_same_exceptions_with_free_disk_are_not_masked_as_disco_cheio(self):
        def boom(src, dst):
            raise AssertionError

        req = {"id": "9", "op": "convert", "input": self.inp, "output": self.out, "model": "silvio"}
        with mock.patch.dict(os.environ, {"STUDIO_RVC_FAKE": "1"}), mock.patch.object(w, "fake_infer", boom), \
                mock.patch("shutil.disk_usage", return_value=mock.Mock(free=50 * 1024**3)), \
                contextlib.redirect_stderr(io.StringIO()):
            resp = w.handle(req)
        self.assertEqual(resp, {"id": "9", "ok": False, "erro": "Falha no conversor: AssertionError: "})
        self.assertEqual(os.listdir(self.dir), ["audio.wav"])       # nem .rvc_* nem .part.wav


if __name__ == "__main__":
    unittest.main()
