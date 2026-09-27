import os
import stat
import tempfile
import unittest
from unittest import mock

from studio import drive

FID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"

# linhas reais do rclone v1.75.1 (--use-json-log), capturadas numa copia local
REAL_COPIED = ('{"time":"2026-09-26T01:14:20.303208433-03:00","level":"info","msg":"Copied (new)",'
               '"size":3000000,"object":"a_IA.mp4","objectType":"*local.Object",'
               '"source":"operations/copy.go:380"}')
REAL_STATS = ('{"time":"2026-09-26T01:14:20.303286814-03:00","level":"notice","msg":"\\nTransferred:   \\t'
              '    2.861 MiB / 2.861 MiB, 100%, 0 B/s, ETA -\\n","stats":{"bytes":3000000,"checks":0,'
              '"elapsedTime":0.009152492,"errors":0,"eta":null,"fatalError":false,"totalBytes":3000000,'
              '"totalTransfers":1,"transfers":1},"source":"accounting/stats.go:549"}')
REAL_NO_CONFIG = ('{"time":"2026-09-26T01:14:31.88807524-03:00","level":"critical","msg":"Failed to create '
                  'file system for destination \\"iavoz:\\": didn\'t find section in config file (\\"iavoz\\")",'
                  '"source":"cmd/cmd.go:208"}')
REAL_TEXT = ('2026/09/26 01:14:31 CRITICAL: Failed to create file system for "iavoz:a_IA.mp4": '
             'didn\'t find section in config file ("iavoz")')


class ParseFolderLinkTest(unittest.TestCase):
    # os 12 casos do probe (drive_probe.py): 9 aceitos + 3 recusados
    GOOD = {
        f"https://drive.google.com/drive/folders/{FID}?usp=sharing": (FID, None),
        f"https://drive.google.com/drive/u/0/folders/{FID}": (FID, None),
        f"https://drive.google.com/drive/u/1/folders/{FID}?usp=drive_link": (FID, None),
        f"https://drive.google.com/drive/mobile/folders/{FID}?usp=sharing": (FID, None),
        "https://drive.google.com/drive/folders/0B1234abcdEFGHijklMNOPqrstu?resourcekey=0-ABCDEFGHIXJQpIGqBJq3MC"
        "&usp=sharing": ("0B1234abcdEFGHijklMNOPqrstu", "0-ABCDEFGHIXJQpIGqBJq3MC"),
        f"https://drive.google.com/open?id={FID}": (FID, None),
        f"https://drive.google.com/folderview?id={FID}": (FID, None),
        "drive.google.com/drive/folders/0AEeXXXXXXXXUk9PVA": ("0AEeXXXXXXXXUk9PVA", None),
        FID: (FID, None),
    }
    BAD = {
        f"https://drive.google.com/file/d/{FID}/view?usp=sharing": "arquivo, não de uma pasta",
        f"https://evil.example.com/drive/folders/{FID}": "não é um link do Google Drive",
        "https://drive.google.com/drive/shared-with-me": "Não encontrei o ID da pasta",
    }

    def test_probe_cases(self):
        for link, want in self.GOOD.items():
            with self.subTest(link=link):
                self.assertEqual(drive.parse_folder_link(link), want)
        for link, msg in self.BAD.items():
            with self.subTest(link=link):
                with self.assertRaises(drive.DriveError) as cm:
                    drive.parse_folder_link(link)
                self.assertIn(msg, str(cm.exception))
        self.assertEqual(len(self.GOOD) + len(self.BAD), 12)

    def test_extra_cases(self):
        self.assertEqual(drive.parse_folder_link(f"  https://docs.google.com/drive/folders/{FID}\n"), (FID, None))
        for link in ("", "   ", f"https://drive.google.com.evil.com/drive/folders/{FID}", "https://[::1",
                     "https://drive.google.com/drive/my-drive", "curto"):
            with self.subTest(link=link):
                with self.assertRaises(drive.DriveError):
                    drive.parse_folder_link(link)

    def test_not_a_string(self):
        # estado.json editado a mao ("drive_pasta": 123) nao pode derrubar a GUI com AttributeError
        for value in (123, 1.5, True, ["link"], {"id": FID}, FID.encode()):
            with self.subTest(value=value):
                with self.assertRaises(drive.DriveError) as cm:
                    drive.parse_folder_link(value)
                self.assertEqual(str(cm.exception), "O link da pasta do Drive tem que ser um texto")
        with self.assertRaises(drive.DriveError) as cm:
            drive.parse_folder_link(None)
        self.assertEqual(str(cm.exception), "Cole o link da pasta do Drive")


class RcloneBinTest(unittest.TestCase):
    def test_path_first_then_local_bin(self):
        with tempfile.TemporaryDirectory() as tmp:
            on_path = os.path.join(tmp, "bin")
            home = os.path.join(tmp, "home")
            local = os.path.join(home, ".local", "bin")
            os.makedirs(on_path)
            os.makedirs(local)
            with mock.patch.dict(os.environ, {"PATH": on_path, "HOME": home}):
                self.assertIsNone(drive.rclone_bin())
                exe = os.path.join(local, "rclone")
                with open(exe, "w") as f:
                    f.write("#!/bin/sh\n")
                self.assertIsNone(drive.rclone_bin())          # sem permissao de execucao
                os.chmod(exe, 0o755)
                self.assertEqual(drive.rclone_bin(), exe)
                first = os.path.join(on_path, "rclone")
                with open(first, "w") as f:
                    f.write("#!/bin/sh\n")
                os.chmod(first, stat.S_IRWXU)
                self.assertEqual(drive.rclone_bin(), first)


class CommandsTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(drive, "rclone_bin", return_value="/x/rclone")
        p.start()
        self.addCleanup(p.stop)

    def test_copyto(self):
        cmd = drive.build_copyto_cmd("/v/a_IA.mp4", "a_IA.mp4", FID)
        self.assertEqual(cmd, [
            "/x/rclone", "copyto", "/v/a_IA.mp4", "iavoz:a_IA.mp4", "--drive-root-folder-id", FID,
            "--use-json-log", "--stats", "1s", "--stats-log-level", "NOTICE", "-v",
            "--retries", "3", "--retries-sleep", "10s", "--low-level-retries", "10",
            "--drive-chunk-size", "64M", "--transfers", "1", "--drive-stop-on-upload-limit",
            "--error-on-no-transfer"])

    def test_copyto_resource_key_and_description(self):
        cmd = drive.build_copyto_cmd("/v/a_IA.mp4", "a_IA.mp4", FID, "0-RK", description="Voz gerada por IA.")
        self.assertEqual(cmd[4:8], ["--drive-root-folder-id", FID, "--drive-resource-key", "0-RK"])
        self.assertEqual(cmd[-3:], ["-M", "--metadata-set", "description=Voz gerada por IA."])

    def test_never_destructive(self):
        cmd = drive.build_copyto_cmd("/v/a_IA.mp4", "a_IA.mp4", FID, "0-RK", description="x")
        for bad in ("sync", "move", "delete", "dedupe", "link", "--no-check-dest", "-vv", "--config"):
            self.assertNotIn(bad, cmd)

    def test_lsjson(self):
        self.assertEqual(drive.build_lsjson_cmd("a_IA.mp4", FID), [
            "/x/rclone", "lsjson", "iavoz:a_IA.mp4", "--drive-root-folder-id", FID,
            "--stat", "--hash", "--hash-type", "md5"])
        self.assertEqual(drive.build_lsjson_cmd("a_IA.mp4", FID, "0-RK")[5:7], ["--drive-resource-key", "0-RK"])

    def test_reconnect(self):
        self.assertEqual(drive.reconnect_cmd(),
                         ["/x/rclone", "config", "update", "iavoz", "config_refresh_token=true"])

    def test_without_rclone_uses_plain_name(self):
        with mock.patch.object(drive, "rclone_bin", return_value=None):
            self.assertEqual(drive.reconnect_cmd()[0], "rclone")


class LogLineTest(unittest.TestCase):
    def test_real_lines(self):
        d = drive.parse_log_line(REAL_COPIED + "\n")
        self.assertEqual((d["msg"], d["size"], d["object"]), ("Copied (new)", 3000000, "a_IA.mp4"))
        d = drive.parse_log_line(REAL_STATS)
        self.assertEqual((d["stats"]["bytes"], d["stats"]["totalBytes"]), (3000000, 3000000))

    def test_not_json(self):
        for line in (REAL_TEXT, "", "   \n", "123", "[1, 2]", "{quebrado", '"texto"'):
            with self.subTest(line=line):
                self.assertIsNone(drive.parse_log_line(line))


class ClassifyErrorTest(unittest.TestCase):
    def test_table(self):
        cases = [
            (1, REAL_NO_CONFIG, "Drive não configurado — rode a configuração"),
            (1, REAL_TEXT, "Drive não configurado — rode a configuração"),
            (1, 'couldn\'t fetch token: invalid_grant: maybe token expired? - try refreshing with '
                '"rclone config reconnect iavoz:"', "Login do Drive expirou — clique Reconectar"),
            (1, 'empty token found - please run "rclone config reconnect iavoz:"',
             "Login do Drive expirou — clique Reconectar"),
            (3, "Failed to copyto: directory not found",
             "Essa conta Google não tem acesso a essa pasta (ou o link está errado)"),
            (3, "", "Essa conta Google não tem acesso a essa pasta (ou o link está errado)"),
            (7, "Received upload limit error: googleapi: Error 403: The user's Drive storage quota has been "
                "exceeded., storageQuotaExceeded", "Seu Drive está cheio — os envios contam na sua cota"),
        ]
        for rc, text, want in cases:
            with self.subTest(text=text[:40]):
                self.assertEqual(drive.classify_error(rc, text), want)

    def test_other_shows_code_and_last_messages(self):
        text = "\n".join([REAL_STATS, "linha 1", '{"level":"error","msg":"erro A"}',
                          '{"level":"error","msg":"erro B"}', "erro C", ""])
        self.assertEqual(drive.classify_error(5, text), "Falha no envio (código 5):\nerro A\nerro B\nerro C")
        self.assertEqual(drive.classify_error(6, ""), "Falha no envio (código 6)")
        self.assertEqual(drive.classify_error(-15, "x"), "rclone foi interrompido (sinal 15)")


class NamesTest(unittest.TestCase):
    def test_split_video_name(self):
        self.assertEqual(drive.split_video_name("2026-09-26_101500_silvio_IA.mp4"), ("2026-09-26_101500", "silvio"))
        self.assertEqual(drive.split_video_name("2026-09-26_101500_2_orochi_IA.mp4"),
                         ("2026-09-26_101500_2", "orochi"))
        self.assertIsNone(drive.split_video_name("video.mp4"))

    def test_description(self):
        self.assertEqual(drive.drive_description("2026-09-26_101500_silvio_IA.mp4"),
                         "Voz sintética gerada por IA (conversão RVC). Não é a voz real de Silvio Santos.")
        self.assertEqual(drive.drive_description("2026-09-26_101500_outro_IA.mp4"),
                         "Voz sintética gerada por IA (conversão RVC). Paródia/homenagem.")


if __name__ == "__main__":
    unittest.main()
