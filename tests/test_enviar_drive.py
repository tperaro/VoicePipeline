import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from studio.config import BASE_DIR
from studio.drive import UploadLock
from studio.takes import Take
from tests.test_drive_upload import FID, LINK, NAMES, fake_env, make_mp4, md5

ENVIAR = os.path.join(BASE_DIR, "enviar_drive.py")
SYSTEM_PY = "/usr/bin/python3"


class EnviarDriveCliTest(unittest.TestCase):
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
        self.env = {**os.environ, **fake_env(self.tmp)}
        self.estado = os.path.join(self.tmp, "estado.json")
        self.videos = os.path.join(self.tmp, "videos_finais")
        self.rec = os.path.join(self.tmp, "recordings")
        os.makedirs(self.videos)
        for name in NAMES:
            shutil.copy2(self.template, os.path.join(self.videos, name))
        self.remote = os.path.join(self.env["FAKE_RCLONE_DRIVE"], FID)

    def cli(self, *args, env=None, python=sys.executable):
        cmd = [python, ENVIAR, "--estado", self.estado, "--videos-dir", self.videos, "--rec-dir", self.rec, *args]
        return subprocess.run(cmd, env=env or self.env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=120)

    def remote_files(self):
        return sorted(n for n in os.listdir(self.remote) if n != ".meta")

    def test_without_folder_exits_2(self):
        r = self.cli()
        self.assertEqual(r.returncode, 2)
        self.assertIn("Nenhuma pasta do Drive configurada — use --pasta <link>", r.stderr)
        self.assertEqual(self.remote_files(), [])

    def test_invalid_folder_link_exits_2(self):
        r = self.cli("--pasta", f"https://drive.google.com/file/d/{FID}/view")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Link inválido: Esse link é de um arquivo, não de uma pasta", r.stderr)
        self.assertFalse(os.path.exists(self.estado))

    def test_send_all_then_skip(self):
        take_dir = os.path.join(self.rec, "2026-09-26_101500")
        os.makedirs(take_dir)
        Take(id="2026-09-26_101500", dir=take_dir, modo="av", status="renderizado", mic="m").save()
        r = self.cli("--pasta", LINK)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(self.estado, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["drive_pasta"], LINK)
        self.assertEqual(self.remote_files(), sorted(NAMES))
        self.assertIn(f"  {NAMES[0]}: 50%\n  {NAMES[0]}: 100%\n", r.stdout)
        self.assertIn(f"{NAMES[0]}: enviado\n", r.stdout)
        self.assertTrue(r.stdout.endswith("Resumo: 2 enviado(s), 0 pulado(s), 0 falha(s)\n"), r.stdout)
        enviado = Take.load(take_dir).saidas["silvio"]["enviado"]
        self.assertEqual(enviado["md5"], md5(os.path.join(self.videos, NAMES[0])))
        r = self.cli()   # usa a pasta salva e nao duplica nada
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"{NAMES[1]}: já estava igual no Drive (pulado)\n", r.stdout)
        self.assertIn("Resumo: 0 enviado(s), 2 pulado(s), 0 falha(s)", r.stdout)
        self.assertEqual(self.remote_files(), sorted(NAMES))

    def test_dry_run_sends_nothing(self):
        r = self.cli("--pasta", LINK, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.remote_files(), [])
        self.assertIn(f"{NAMES[0]}: seria enviado\n", r.stdout)
        self.assertIn("Resumo (simulação): 2 a enviar, 0 pulado(s), 0 falha(s)", r.stdout)

    def test_single_file(self):
        r = self.cli("--pasta", LINK, "--arquivo", os.path.join(self.videos, NAMES[1]))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.remote_files(), [NAMES[1]])
        self.assertIn("Resumo: 1 enviado(s), 0 pulado(s), 0 falha(s)", r.stdout)

    def test_failures_exit_1(self):
        r = self.cli("--pasta", LINK, env={**self.env, "FAKE_RCLONE_MODE": "quota"})
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"{NAMES[0]}: FALHOU — Seu Drive está cheio — os envios contam na sua cota", r.stdout)
        self.assertIn("Resumo: 0 enviado(s), 0 pulado(s), 2 falha(s)", r.stdout)
        r = self.cli(env={**self.env, "FAKE_RCLONE_MODE": "invalid_grant"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("Login do Drive expirou", r.stdout)
        self.assertIn("rclone config reconnect iavoz:", r.stdout)
        other = os.path.join(self.videos, "video.mp4")
        shutil.copy2(self.template, other)
        r = self.cli("--arquivo", other)
        self.assertEqual(r.returncode, 1)
        self.assertIn("só vídeos *_IA.mp4", r.stdout)

    def test_file_outside_videos_dir_is_refused(self):
        outside = os.path.join(self.tmp, NAMES[0])
        shutil.copy2(self.template, outside)
        r = self.cli("--pasta", LINK, "--arquivo", outside)
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"{NAMES[0]}: FALHOU — {NAMES[0]}: fora de videos_finais/", r.stdout)
        self.assertEqual(self.remote_files(), [])

    def test_pasta_keeps_copy_of_corrupt_estado(self):
        broken = '{"mic": "alsa_input.usb", "av_offset_ms": 40,}'     # virgula sobrando (edicao a mao)
        with open(self.estado, "w", encoding="utf-8") as f:
            f.write(broken)
        r = self.cli("--pasta", LINK, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("estado.json corrompido — cópia guardada em estado.json.corrompido", r.stderr)
        with open(self.estado + ".corrompido", encoding="utf-8") as f:
            self.assertEqual(f.read(), broken)
        with open(self.estado, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["drive_pasta"], LINK)

    def test_preflight_errors_exit_1(self):
        self.cli("--pasta", LINK, "--dry-run")
        env = {**self.env, "PATH": "/usr/bin:/bin", "HOME": self.tmp}
        r = self.cli(env=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("rclone não instalado — veja o README", r.stderr)
        with UploadLock(self.videos):
            r = self.cli()
        self.assertEqual(r.returncode, 1)
        self.assertIn("Outro envio já está em andamento", r.stderr)
        self.assertEqual(self.remote_files(), [])

    def test_no_videos(self):
        for name in NAMES:
            os.remove(os.path.join(self.videos, name))
        r = self.cli("--pasta", LINK)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Nenhum vídeo para enviar", r.stdout)

    def test_stdlib_only(self):
        r = subprocess.run([sys.executable, "-S", "-c", "import enviar_drive, studio.drive; print('ok')"],
                           cwd=BASE_DIR, capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stdout), (0, "ok\n"), r.stderr)

    @unittest.skipUnless(os.path.exists(SYSTEM_PY), "sem python3 do sistema")
    def test_runs_with_system_python(self):
        r = self.cli("--pasta", LINK, python=SYSTEM_PY)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.remote_files(), sorted(NAMES))


if __name__ == "__main__":
    unittest.main()
