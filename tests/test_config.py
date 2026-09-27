import json
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from unittest import mock

from studio import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PathsTest(unittest.TestCase):
    def test_paths_are_absolute_and_based_on_repo_root(self):
        self.assertEqual(config.BASE_DIR, ROOT)
        self.assertEqual(config.APPLIO_DIR, os.path.join(ROOT, "Applio"))
        self.assertEqual(config.LOGS_DIR, os.path.join(ROOT, "Applio", "logs"))
        self.assertEqual(config.VENV_PYTHON, os.path.join(ROOT, "Applio", ".venv", "bin", "python"))
        self.assertEqual(config.REC_DIR, os.path.join(ROOT, "recordings"))
        self.assertEqual(config.VIDEOS_DIR, os.path.join(ROOT, "videos_finais"))
        self.assertEqual(config.ESTADO_PATH, os.path.join(ROOT, "estado.json"))
        self.assertEqual(config.STUDIO_LOG, os.path.join(ROOT, "studio.log"))
        self.assertEqual(config.RVC_LOG, os.path.join(ROOT, "studio_rvc.log"))

    def test_constants(self):
        self.assertEqual(config.RCLONE_REMOTE, "iavoz")
        self.assertEqual(config.AVISO_LINHA1, "VOZ GERADA POR IA")
        self.assertEqual(config.AVISO_LINHA3, "paródia · homenagem")
        self.assertEqual(config.MAX_TAKE_S, 300)
        self.assertEqual(config.MIN_FREE_BYTES, 2 * 1024**3)
        self.assertTrue(os.path.isfile(config.FONT_BOLD))
        self.assertTrue(os.path.isfile(config.FONT_REG))


class ModelosTest(unittest.TestCase):
    def test_modelos(self):
        self.assertEqual([m.key for m in config.MODELOS], ["orochi", "silvio"])
        silvio = config.get_modelo("silvio")
        self.assertEqual(silvio.label, "Silvio Santos")
        self.assertEqual(silvio.nome, "Silvio Santos")
        self.assertEqual(silvio.aviso, "Não é a voz real de Silvio Santos")
        self.assertEqual(config.get_modelo("orochi").aviso, "Não é a voz real do Orochi")

    def test_get_modelo_unknown(self):
        with self.assertRaises(ValueError):
            config.get_modelo("nada")

    def test_modelo_is_frozen(self):
        with self.assertRaises(FrozenInstanceError):
            config.MODELOS[0].aviso = ""

    def test_metadata_tags(self):
        tags = config.metadata_tags(config.get_modelo("silvio"))
        self.assertEqual(tags, {
            "title": "Paródia/homenagem - voz gerada por IA",
            "comment": "Voz sintética gerada por IA (conversão RVC). Não é a voz real de Silvio Santos.",
            "description": "AI-generated/synthetic voice (RVC voice conversion). Parody/tribute. "
                           "Not the real voice of Silvio Santos.",
        })

    def test_metadata_tags_refuses_model_without_warning(self):
        with self.assertRaises(ValueError):
            config.metadata_tags(config.Modelo("x", "X", "X", "  "))


class CheckpointTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = self.tmp.name
        os.makedirs(os.path.join(self.logs, "orochi"))

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, name):
        path = os.path.join(self.logs, "orochi", name)
        open(path, "wb").close()
        return path

    def test_latest_checkpoint_uses_numeric_epoch(self):
        self.touch("orochi_50e_2650s.pth")
        self.touch("orochi_75e_3975s.pth")
        best = self.touch("orochi_350e_18550s.pth")
        self.touch("G_2333333.pth")
        self.touch("D_2333333.pth")
        self.assertEqual(config.find_latest_checkpoint("orochi", self.logs), best)

    def test_latest_checkpoint_none(self):
        self.assertIsNone(config.find_latest_checkpoint("orochi", self.logs))
        self.assertIsNone(config.find_latest_checkpoint("silvio", self.logs))

    def test_model_index_path(self):
        self.assertEqual(config.model_index_path("orochi", self.logs),
                         os.path.join(self.logs, "orochi", "orochi.index"))
        self.assertEqual(config.model_index_path("silvio"),
                         os.path.join(config.LOGS_DIR, "silvio", "silvio.index"))


class EstadoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "estado.json")

    def tearDown(self):
        self.tmp.cleanup()

    def write_raw(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_defaults(self):
        self.assertEqual(config.DEFAULT_ESTADO, {"mic": "", "camera": "", "gravar_video": True,
                                                 "drive_pasta": "", "av_offset_ms": 0, "modelo": "orochi"})

    def test_missing_file(self):
        estado, aviso = config.load_estado(self.path)
        self.assertEqual(estado, config.DEFAULT_ESTADO)
        self.assertIsNone(aviso)
        estado["mic"] = "mudou"
        self.assertEqual(config.DEFAULT_ESTADO["mic"], "")

    def test_corrupt_json(self):
        self.write_raw("{nao e json")
        estado, aviso = config.load_estado(self.path)
        self.assertEqual(estado, config.DEFAULT_ESTADO)
        self.assertEqual(aviso, "estado.json corrompido — usando padrões")

    def test_not_a_dict(self):
        self.write_raw("[1, 2, 3]")
        estado, aviso = config.load_estado(self.path)
        self.assertEqual(estado, config.DEFAULT_ESTADO)
        self.assertEqual(aviso, "estado.json corrompido — usando padrões")

    def test_invalid_utf8(self):
        with open(self.path, "wb") as f:
            f.write(b'{"mic": "\xff\xfe"}')
        estado, aviso = config.load_estado(self.path)
        self.assertEqual(estado, config.DEFAULT_ESTADO)
        self.assertIsNotNone(aviso)

    def test_merge_and_unknown_keys(self):
        self.write_raw(json.dumps({"mic": "alsa_input.usb", "av_offset_ms": 40, "futuro": [1]}))
        estado, aviso = config.load_estado(self.path)
        self.assertIsNone(aviso)
        self.assertEqual(estado["mic"], "alsa_input.usb")
        self.assertEqual(estado["av_offset_ms"], 40)
        self.assertEqual(estado["futuro"], [1])
        self.assertEqual(estado["gravar_video"], True)
        self.assertEqual(estado["modelo"], "orochi")

    def test_save_roundtrip_utf8(self):
        estado = dict(config.DEFAULT_ESTADO, drive_pasta="https://drive.google.com/drive/folders/abc",
                      camera="/dev/v4l/by-id/câmera")
        config.save_estado(estado, self.path)
        with open(self.path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("câmera", text)            # ensure_ascii=False
        self.assertIn('\n  "mic"', text)         # indent=2
        self.assertEqual(config.load_estado(self.path), (estado, None))


class AtomicWriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "x.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_overwrites_and_leaves_no_temp(self):
        config.atomic_write_json(self.path, {"a": 1})
        config.atomic_write_json(self.path, {"a": 2, "texto": "paródia"})
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"a": 2, "texto": "paródia"})
        self.assertEqual(os.listdir(self.tmp.name), ["x.json"])

    def test_failure_keeps_old_file(self):
        config.atomic_write_json(self.path, {"a": 1})
        with mock.patch("studio.config.os.replace", side_effect=OSError("disco cheio")):
            with self.assertRaises(OSError):
                config.atomic_write_json(self.path, {"a": 2})
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"a": 1})
        self.assertEqual(os.listdir(self.tmp.name), ["x.json"])

    def test_unserializable_keeps_old_file(self):
        config.atomic_write_json(self.path, {"a": 1})
        with self.assertRaises(TypeError):
            config.atomic_write_json(self.path, {"a": object()})
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"a": 1})
        self.assertEqual(os.listdir(self.tmp.name), ["x.json"])


class CheckpointEscapeTest(unittest.TestCase):
    def test_glob_chars_in_path_and_key(self):
        # colchete no caminho do projeto e padrao do glob: sem glob.escape o checkpoint some
        with tempfile.TemporaryDirectory() as tmp:
            logs = os.path.join(tmp, "orochi [v2]", "logs")
            for key in ("orochi", "voz[1]"):
                os.makedirs(os.path.join(logs, key))
                open(os.path.join(logs, key, f"{key}_50e_2650s.pth"), "wb").close()
                best = os.path.join(logs, key, f"{key}_350e_18550s.pth")
                open(best, "wb").close()
                self.assertEqual(config.find_latest_checkpoint(key, logs), best)


class EstadoRobustoTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.path = os.path.join(self.dir, "estado.json")

    def write(self, obj):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def test_wrong_types_become_defaults(self):
        self.write({"mic": "alsa_input.usb", "camera": None, "gravar_video": 1, "drive_pasta": 123,
                    "av_offset_ms": True, "modelo": "silvio", "futuro": [1]})
        estado, aviso = config.load_estado(self.path)
        self.assertEqual(estado, dict(config.DEFAULT_ESTADO, mic="alsa_input.usb", modelo="silvio", futuro=[1]))
        self.assertEqual(aviso, "estado.json: valor inválido em camera, gravar_video, drive_pasta, av_offset_ms "
                                "— usando o padrão")

    def test_merge_keeps_values_written_by_others(self):
        # o app abriu com av_offset_ms 0; o calibrar_av.py gravou 40 por fora; o app muda so o mic
        config.save_estado(dict(config.DEFAULT_ESTADO, av_offset_ms=40, drive_pasta="LINK"), self.path)
        estado, aviso = config.merge_estado({"mic": "novo"}, self.path)
        self.assertIsNone(aviso)
        self.assertEqual(estado, dict(config.DEFAULT_ESTADO, av_offset_ms=40, drive_pasta="LINK", mic="novo"))
        self.assertEqual(config.load_estado(self.path), (estado, None))

    def test_merge_without_file(self):
        estado, aviso = config.merge_estado({"modelo": "silvio"}, self.path)
        self.assertEqual((estado, aviso), (dict(config.DEFAULT_ESTADO, modelo="silvio"), None))
        self.assertEqual(os.listdir(self.dir), ["estado.json"])

    def test_merge_keeps_copy_of_corrupt_file(self):
        broken = '{"drive_pasta": "LINK", "av_offset_ms": 40,}'     # virgula sobrando (edicao a mao)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(broken)
        estado, aviso = config.merge_estado({"mic": "novo"}, self.path)
        self.assertEqual(aviso, "estado.json corrompido — cópia guardada em estado.json.corrompido")
        self.assertEqual(estado, dict(config.DEFAULT_ESTADO, mic="novo"))
        with open(self.path + ".corrompido", encoding="utf-8") as f:
            self.assertEqual(f.read(), broken)
        self.assertEqual(config.load_estado(self.path), (estado, None))

    def test_merge_fixes_wrong_types(self):
        self.write({"drive_pasta": 123, "av_offset_ms": 40})
        estado, aviso = config.merge_estado({"mic": "novo"}, self.path)
        self.assertEqual(aviso, "estado.json: valor inválido em drive_pasta — usando o padrão")
        self.assertEqual(estado, dict(config.DEFAULT_ESTADO, av_offset_ms=40, mic="novo"))
        self.assertEqual(config.load_estado(self.path), (estado, None))

    def test_merge_failure_keeps_file(self):
        config.save_estado(dict(config.DEFAULT_ESTADO, av_offset_ms=40), self.path)
        with mock.patch("studio.config.os.replace", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(OSError):
                config.merge_estado({"mic": "novo"}, self.path)
        self.assertEqual(config.load_estado(self.path), (dict(config.DEFAULT_ESTADO, av_offset_ms=40), None))
        self.assertEqual(os.listdir(self.dir), ["estado.json"])


if __name__ == "__main__":
    unittest.main()
