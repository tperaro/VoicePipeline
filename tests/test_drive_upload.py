import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from unittest import mock

from studio import drive
from studio.config import BASE_DIR
from studio.takes import Take

FAKEBIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fakebin")
FID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
LINK = f"https://drive.google.com/drive/folders/{FID}?usp=sharing"
COMMENT = "Voz sintética gerada por IA (conversão RVC). Não é a voz real de Silvio Santos."
OROCHI_COMMENT = "Voz sintética gerada por IA (conversão RVC). Não é a voz real do Orochi."
# os dois do Silvio: o MP4 modelo leva o aviso do Silvio, e check_uploadable exige o aviso exato do modelo do nome
NAMES = ("2026-09-26_101500_silvio_IA.mp4", "2026-09-26_101700_silvio_IA.mp4")


def make_mp4(path: str, comment: str | None = COMMENT, duration: float = 0.3) -> str:
    # MP4 pequeno; o comment e o que check_uploadable confere
    args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
            "-i", f"testsrc2=s=64x64:r=10:d={duration}", "-c:v", "mpeg4"]
    if comment is not None:
        args += ["-metadata", f"comment={comment}"]
    subprocess.run(args + [path], check=True, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    return path


def fake_env(tmp: str) -> dict:
    # rclone falso na frente do PATH; o "Drive" e uma pasta local com a pasta compartilhada FID
    drive_dir = os.path.join(tmp, "drive")
    os.makedirs(os.path.join(drive_dir, FID), exist_ok=True)
    return {"PATH": FAKEBIN + os.pathsep + os.environ.get("PATH", ""),
            "FAKE_RCLONE_DRIVE": drive_dir, "FAKE_RCLONE_CALLS": os.path.join(tmp, "calls.jsonl"),
            "FAKE_RCLONE_MODE": "ok", "FAKE_RCLONE_LSJSON": "ok",
            "RCLONE_CONFIG": os.path.join(tmp, "rclone.conf")}


def read_calls(env: dict) -> list[dict]:
    try:
        with open(env["FAKE_RCLONE_CALLS"], encoding="utf-8") as f:
            return [json.loads(line) for line in f]
    except FileNotFoundError:
        return []


def md5(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] not in ("Z", "X")
    except FileNotFoundError:
        return False


class CheckUploadableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.videos = cls.tmp.name              # a pasta dos testes faz o papel de videos_finais/
        cls.good = make_mp4(os.path.join(d, NAMES[0]))
        cls.no_comment = make_mp4(os.path.join(d, "2026-09-26_101600_silvio_IA.mp4"), comment=None)
        cls.other_comment = make_mp4(os.path.join(d, "2026-09-26_101601_silvio_IA.mp4"), comment="feito em casa")
        cls.junk = os.path.join(d, "2026-09-26_101602_silvio_IA.mp4")
        with open(cls.junk, "wb") as f:
            f.write(b"isto nao e um video" * 100)
        cls.media = make_mp4(os.path.join(d, "2026-09-26_101603_silvio_IA.mp4"), comment="MEDIA")
        cls.orochi = make_mp4(os.path.join(d, "2026-09-26_101604_orochi_IA.mp4"), comment=OROCHI_COMMENT)
        cls.orochi_with_silvio = make_mp4(os.path.join(d, "2026-09-26_101605_orochi_IA.mp4"))
        cls.outside_dir = tempfile.TemporaryDirectory()
        cls.outside = make_mp4(os.path.join(cls.outside_dir.name, NAMES[0]))
        cls.link = os.path.join(d, "2026-09-26_101606_silvio_IA.mp4")
        os.symlink(cls.outside, cls.link)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        cls.outside_dir.cleanup()

    def check(self, path: str) -> str | None:
        return drive.check_uploadable(path, self.videos)

    def copy_as(self, name: str) -> str:
        path = os.path.join(self.tmp.name, name)
        shutil.copyfile(self.good, path)
        return path

    def test_ok(self):
        self.assertIsNone(self.check(self.good))
        self.assertIsNone(self.check(self.orochi))

    def test_part_and_name(self):
        for name in (NAMES[0] + ".part", "render.part.mp4", "x.part.y_IA.mp4"):
            with self.subTest(name=name):
                self.assertIn("arquivo incompleto (.part)", self.check(self.copy_as(name)))
        self.assertIn("só vídeos *_IA.mp4", self.check(self.copy_as("video.mp4")))
        for name in ("x_IA.mp4", "2026-09-26_101500_xyz_IA.mp4"):
            with self.subTest(name=name):
                self.assertEqual(self.check(self.copy_as(name)),
                                 f"{name}: nome sem um modelo conhecido (<tomada>_<modelo>_IA.mp4) — não é enviado")

    def test_missing(self):
        path = os.path.join(self.tmp.name, "2026-01-01_000000_silvio_IA.mp4")
        self.assertEqual(self.check(path), "2026-01-01_000000_silvio_IA.mp4: arquivo não encontrado")

    def test_fail_closed_on_metadata(self):
        # so o aviso exato do modelo do nome: "MEDIA" tem "IA", e o aviso do Silvio num video do Orochi nao serve
        for path in (self.no_comment, self.other_comment, self.media, self.orochi_with_silvio):
            with self.subTest(path=os.path.basename(path)):
                self.assertIn("sem o aviso de IA nos metadados", self.check(path))
        self.assertIn("vídeo ilegível", self.check(self.junk))

    def test_only_from_videos_dir(self):
        # o enviar_drive.py --arquivo aceita qualquer caminho: fora de videos_finais/ (ou symlink) nao sai
        want = f"{NAMES[0]}: fora de videos_finais/ — só vídeos gerados pelo app são enviados"
        self.assertEqual(self.check(self.outside), want)
        self.assertEqual(drive.check_uploadable(self.good), want)     # padrao: o videos_finais/ do projeto
        self.assertIn("fora de videos_finais/", self.check(self.link))


class Md5AndListTest(unittest.TestCase):
    def test_md5_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "big.bin")
            data = os.urandom(2_500_000)   # mais de um bloco de leitura
            with open(path, "wb") as f:
                f.write(data)
            self.assertEqual(drive.md5_file(path), hashlib.md5(data).hexdigest())

    def test_list_videos(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("b_orochi_IA.mp4", "a_silvio_IA.mp4", "c.mp4", "y.part.z_IA.mp4", "a_silvio_IA.mp4.part",
                         ".envio.lock"):
                open(os.path.join(tmp, name), "w").close()
            os.mkdir(os.path.join(tmp, "d_IA.mp4"))
            self.assertEqual(drive.list_videos(tmp),
                             [os.path.join(tmp, "a_silvio_IA.mp4"), os.path.join(tmp, "b_orochi_IA.mp4")])
            self.assertEqual(drive.list_videos(os.path.join(tmp, "nao_existe")), [])


class UploadLockTest(unittest.TestCase):
    def test_same_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            videos = os.path.join(tmp, "videos_finais")
            with drive.UploadLock(videos):
                self.assertTrue(os.path.isfile(os.path.join(videos, ".envio.lock")))
                with self.assertRaises(drive.DriveError) as cm:
                    with drive.UploadLock(videos):
                        pass
                self.assertEqual(str(cm.exception), "Outro envio já está em andamento")
            with drive.UploadLock(videos):
                pass

    def test_other_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = ("import sys; sys.path.insert(0, sys.argv[1]); from studio.drive import UploadLock\n"
                    "with UploadLock(sys.argv[2]):\n    print('travado', flush=True); sys.stdin.read()")
            child = subprocess.Popen([sys.executable, "-c", code, BASE_DIR, tmp], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "travado")
                with self.assertRaises(drive.DriveError):
                    with drive.UploadLock(tmp):
                        pass
            finally:
                child.stdin.close()
                child.wait(timeout=10)
                child.stdout.close()
            with drive.UploadLock(tmp):
                pass


class UploadFilesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = tempfile.TemporaryDirectory()
        cls.template = make_mp4(os.path.join(cls.src.name, "t.mp4"))

    @classmethod
    def tearDownClass(cls):
        cls.src.cleanup()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.env = fake_env(self.tmp)
        patcher = mock.patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.videos = os.path.join(self.tmp, "videos_finais")
        os.makedirs(self.videos)
        self.paths = []
        for name in NAMES:
            path = os.path.join(self.videos, name)
            shutil.copy2(self.template, path)
            self.paths.append(path)
        self.remote = os.path.join(self.env["FAKE_RCLONE_DRIVE"], FID)

    def upload(self, paths=None, **kw):
        kw.setdefault("videos_dir", self.videos)
        return drive.upload_files(self.paths if paths is None else paths, LINK, **kw)

    def reset_calls(self):
        try:
            os.remove(self.env["FAKE_RCLONE_CALLS"])
        except FileNotFoundError:
            pass

    def copy_calls(self):
        return [c["argv"] for c in read_calls(self.env) if c["argv"][0] == "copyto"]

    def test_upload_verify_and_progress(self):
        progress = []
        results = self.upload(on_progress=lambda name, frac: progress.append((name, frac)))
        self.assertEqual([(r["ok"], r["pulado"], r["erro"], r["aviso"]) for r in results],
                         [(True, False, "", "")] * 2)
        for path, res in zip(self.paths, results):
            name = os.path.basename(path)
            self.assertEqual(res["arquivo"], path)
            self.assertEqual(res["md5"], md5(path))
            self.assertEqual(md5(os.path.join(self.remote, name)), md5(path))
            with open(os.path.join(self.remote, ".meta", name + ".json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"description": drive.drive_description(name)})
            fracs = [f for n, f in progress if n == name]
            self.assertEqual((fracs[0], fracs[-1]), (0.0, 1.0))
            self.assertEqual(fracs, sorted(fracs))
            self.assertTrue(any(0 < f < 1 for f in fracs))
        kinds = [c["argv"][0] for c in read_calls(self.env)]
        self.assertEqual(kinds, ["copyto", "lsjson", "copyto", "lsjson"])
        for call in read_calls(self.env):
            self.assertIn(FID, call["argv"])

    def test_second_run_skips(self):
        self.upload()
        results = self.upload()
        self.assertEqual([(r["ok"], r["pulado"]) for r in results], [(True, True)] * 2)
        self.assertEqual([r["md5"] for r in results], [md5(p) for p in self.paths])
        self.assertEqual(sorted(n for n in os.listdir(self.remote) if n != ".meta"), sorted(NAMES))

    def test_changed_file_is_sent_again(self):
        self.upload()
        make_mp4(self.paths[0], duration=0.5)
        results = self.upload()
        self.assertEqual([(r["ok"], r["pulado"]) for r in results], [(True, False), (True, True)])
        self.assertEqual(md5(os.path.join(self.remote, NAMES[0])), md5(self.paths[0]))

    def test_dry_run(self):
        results = self.upload(dry_run=True)
        self.assertEqual([(r["ok"], r["pulado"], r["md5"]) for r in results], [(True, False, "")] * 2)
        self.assertEqual(os.listdir(self.remote), [])
        self.assertTrue(all("--dry-run" in argv for argv in self.copy_calls()))
        self.assertNotIn("lsjson", [c["argv"][0] for c in read_calls(self.env)])
        self.upload()
        self.assertEqual([r["pulado"] for r in self.upload(dry_run=True)], [True, True])

    def test_rejected_files_never_reach_rclone(self):
        part = os.path.join(self.videos, "render.part.mp4")
        shutil.copy2(self.template, part)
        results = self.upload([part, self.paths[0]])
        self.assertIn(".part", results[0]["erro"])
        self.assertFalse(results[0]["ok"])
        self.assertTrue(results[1]["ok"])
        self.assertEqual([argv[1] for argv in self.copy_calls()], [self.paths[0]])

    def test_video_outside_videos_dir_never_reaches_rclone(self):
        outside = os.path.join(self.tmp, NAMES[0])          # mesmo nome, fora de videos_finais/
        shutil.copy2(self.template, outside)
        results = self.upload([outside, self.paths[1]])
        self.assertEqual(results[0]["erro"],
                         f"{NAMES[0]}: fora de videos_finais/ — só vídeos gerados pelo app são enviados")
        self.assertTrue(results[1]["ok"])
        self.assertEqual([argv[1] for argv in self.copy_calls()], [self.paths[1]])

    def test_fatal_errors_stop_the_batch(self):
        cases = {"noconfig": drive.MSG_NO_CONFIG, "invalid_grant": drive.MSG_RELOGIN, "quota": drive.MSG_QUOTA}
        for mode, msg in cases.items():
            with self.subTest(mode=mode):
                os.environ["FAKE_RCLONE_MODE"] = mode
                self.reset_calls()
                results = self.upload()
                self.assertEqual([(r["ok"], r["erro"]) for r in results], [(False, msg)] * 2)
                self.assertEqual(len(self.copy_calls()), 1)

    def test_folder_without_access(self):
        other = "1ZzZzZzZzZzZzZzZzZzZzZzZzZ"
        results = drive.upload_files(self.paths, other, videos_dir=self.videos)
        self.assertEqual([r["erro"] for r in results], [drive.MSG_NO_ACCESS] * 2)

    def test_other_error_keeps_going(self):
        os.environ["FAKE_RCLONE_MODE"] = "fail"
        results = self.upload()
        self.assertEqual(len(self.copy_calls()), 2)
        for r in results:
            self.assertFalse(r["ok"])
            self.assertTrue(r["erro"].startswith("Falha no envio (código 5):\n"), r["erro"])
            self.assertIn("Error 500", r["erro"])

    def test_md5_mismatch_is_failure(self):
        os.environ["FAKE_RCLONE_LSJSON"] = "badmd5"
        results = self.upload(self.paths[:1])
        self.assertEqual((results[0]["ok"], results[0]["md5"]), (False, ""))
        self.assertEqual(results[0]["erro"], "O arquivo no Drive não confere com o local (tamanho ou MD5 diferente)")

    def test_description_rejected_retries_without_it(self):
        os.environ["FAKE_RCLONE_MODE"] = "metadata_reject"
        results = self.upload(self.paths[:1])
        self.assertEqual((results[0]["ok"], results[0]["aviso"]),
                         (True, "O Drive recusou a descrição do arquivo; enviado sem ela"))
        calls = self.copy_calls()
        self.assertEqual(len(calls), 2)
        self.assertIn("--metadata-set", calls[0])
        self.assertNotIn("--metadata-set", calls[1])
        self.assertNotIn("-M", calls[1])

    def test_cancel_sends_sigterm(self):
        os.environ["FAKE_RCLONE_MODE"] = "hang"
        cancel = threading.Event()

        def on_progress(name, frac):
            if frac > 0:
                cancel.set()

        t0 = time.monotonic()
        results = self.upload(on_progress=on_progress, cancel=cancel)
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual([(r["ok"], r["erro"]) for r in results], [(False, "Envio cancelado")])
        pids = [c["pid"] for c in read_calls(self.env)]
        self.assertEqual(len(pids), 1)
        self.assertFalse(alive(pids[0]))

    def test_preflight_errors(self):
        with self.assertRaises(drive.DriveError) as cm:
            drive.upload_files(self.paths, "https://example.com/x", videos_dir=self.videos)
        self.assertEqual(str(cm.exception), "Isso não é um link do Google Drive")
        with mock.patch.dict(os.environ, {"PATH": os.path.join(self.tmp, "vazio"), "HOME": self.tmp}):
            with self.assertRaises(drive.DriveError) as cm:
                self.upload()
        self.assertEqual(str(cm.exception), "rclone não instalado — veja o README")
        with drive.UploadLock(self.videos):
            with self.assertRaises(drive.DriveError) as cm:
                self.upload()
        self.assertEqual(str(cm.exception), "Outro envio já está em andamento")
        self.assertEqual(read_calls(self.env), [])


class RecordSentTest(unittest.TestCase):
    def test_record_sent(self):
        with tempfile.TemporaryDirectory() as rec:
            take_dir = os.path.join(rec, "2026-09-26_101500")
            os.makedirs(take_dir)
            Take(id="2026-09-26_101500", dir=take_dir, modo="av", status="renderizado", mic="m",
                 saidas={"silvio": {"wav": "silvio.wav"}}).save()
            res = {"arquivo": "/v/2026-09-26_101500_silvio_IA.mp4", "ok": True, "pulado": False,
                   "md5": "abc", "erro": "", "aviso": ""}
            t1 = datetime(2026, 9, 26, 11, 0, 0)
            self.assertEqual(drive.record_sent(res, rec, now=t1), "2026-09-26_101500")
            saida = Take.load(take_dir).saidas["silvio"]
            self.assertEqual(saida, {"wav": "silvio.wav",
                                     "enviado": {"md5": "abc", "quando": "2026-09-26T11:00:00"}})
            # mesmo md5 (pulado numa nova rodada): nao reescreve
            self.assertEqual(drive.record_sent(res, rec, now=datetime(2026, 9, 27)), "2026-09-26_101500")
            self.assertEqual(Take.load(take_dir).saidas["silvio"]["enviado"]["quando"], "2026-09-26T11:00:00")
            self.assertIsNone(drive.record_sent({**res, "ok": False}, rec))
            self.assertIsNone(drive.record_sent({**res, "md5": ""}, rec))
            self.assertIsNone(drive.record_sent({**res, "arquivo": "/v/2026-01-01_000000_silvio_IA.mp4"}, rec))
            self.assertIsNone(drive.record_sent({**res, "arquivo": "/v/video.mp4"}, rec))


if __name__ == "__main__":
    unittest.main()
