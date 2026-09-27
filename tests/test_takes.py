import json
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from studio import procs, takes
from tests import helpers

NOW = datetime(2026, 9, 26, 10, 15, 0)


def wav_header_only(path):
    # o que o ffmpeg deixa num WAV morto antes do 1o pacote: tamanhos 0xFFFFFFFF e nenhum dado
    fmt = struct.pack("<HHIIHH", 1, 1, 48000, 96000, 2, 16)
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE")
        f.write(b"fmt " + struct.pack("<I", len(fmt)) + fmt)
        f.write(b"data" + struct.pack("<I", 0xFFFFFFFF))


class TakeModelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rec = os.path.join(self.tmp.name, "recordings")

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_take(self):
        t = takes.new_take("av", "mic0", "/dev/cam", rec_dir=self.rec, now=NOW)
        self.assertEqual(t.id, "2026-09-26_101500")
        self.assertEqual(t.dir, os.path.join(self.rec, "2026-09-26_101500"))
        self.assertEqual((t.modo, t.status, t.mic, t.camera), ("av", "gravando", "mic0", "/dev/cam"))
        self.assertEqual(t.criado, "2026-09-26T10:15:00")
        with open(t.path("take.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn("dir", data)
        self.assertEqual(data["id"], t.id)
        self.assertEqual(data["status"], "gravando")

    def test_new_take_unique_ids(self):
        ids = [takes.new_take("audio", "m", rec_dir=self.rec, now=NOW).id for _ in range(3)]
        self.assertEqual(ids, ["2026-09-26_101500", "2026-09-26_101500_2", "2026-09-26_101500_3"])

    def test_new_take_rejects_bad_modo(self):
        with self.assertRaises(ValueError):
            takes.new_take("video", "m", rec_dir=self.rec, now=NOW)

    def test_paths_by_modo(self):
        av = takes.new_take("av", "m", "c", rec_dir=self.rec, now=NOW)
        au = takes.new_take("audio", "m", rec_dir=self.rec, now=NOW)
        self.assertEqual(av.raw_path, os.path.join(av.dir, "raw.mkv"))
        self.assertEqual(av.audio_path, os.path.join(av.dir, "audio.wav"))
        self.assertEqual(au.raw_path, os.path.join(au.dir, "raw.wav"))
        self.assertEqual(au.audio_path, os.path.join(au.dir, "raw.wav"))
        self.assertEqual(av.path("silvio.wav"), os.path.join(av.dir, "silvio.wav"))

    def test_save_load_roundtrip(self):
        t = takes.new_take("av", "m", "c", rec_dir=self.rec, now=NOW)
        t.status = "convertido"
        t.video = {"w": 1280, "h": 720, "ancora_pts": 0.132, "n_frames": 300, "fps_medido": 14.6}
        t.audio_fit = {"inicio": 0.061, "taxa_real": 48002.0, "gaps": 0, "residuo_ms": 1.1, "duracao": 10.0}
        t.saidas = {"silvio": {"wav": "silvio.wav", "mp3": "silvio_IA.mp3"}}
        t.erro = "nenhum — ok"
        t.save()
        self.assertEqual(takes.Take.load(t.dir), t)
        self.assertEqual(sorted(os.listdir(t.dir)), ["take.json"])

    def test_load_ignores_unknown_keys(self):
        t = takes.new_take("audio", "m", rec_dir=self.rec, now=NOW)
        with open(t.path("take.json"), encoding="utf-8") as f:
            data = json.load(f)
        data["campo_futuro"] = 1
        with open(t.path("take.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
        self.assertEqual(takes.Take.load(t.dir), t)

    def test_final_video_name(self):
        self.assertEqual(takes.final_video_name("2026-09-26_101500", "silvio"), "2026-09-26_101500_silvio_IA.mp4")


class ListTakesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rec = os.path.join(self.tmp.name, "recordings")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_dir(self):
        self.assertEqual(takes.list_takes(self.rec), [])
        self.assertIsNone(takes.latest_take(self.rec))
        self.assertEqual(takes.recover_takes(self.rec), [])

    def test_ignores_old_recordings_and_broken_json(self):
        os.makedirs(os.path.join(self.rec, "pasta_sem_json"))
        with open(os.path.join(self.rec, "gravacao_antiga.wav"), "wb") as f:
            f.write(b"RIFF")
        for name, text in (("2026-01-01_000000", "{quebrado"), ("2026-01-01_000001", "[1, 2]"),
                           ("2026-01-01_000002", '{"id": "x"}')):
            os.makedirs(os.path.join(self.rec, name))
            with open(os.path.join(self.rec, name, "take.json"), "w") as f:
                f.write(text)
        t = takes.new_take("audio", "m", rec_dir=self.rec, now=NOW)
        self.assertEqual([x.id for x in takes.list_takes(self.rec)], [t.id])
        self.assertEqual(takes.latest_take(self.rec), t)

    def test_order_with_suffixes(self):
        ids = [takes.new_take("audio", "m", rec_dir=self.rec, now=NOW).id for _ in range(10)]
        older = takes.new_take("audio", "m", rec_dir=self.rec, now=datetime(2026, 9, 25, 23, 59, 59))
        got = [t.id for t in takes.list_takes(self.rec)]
        self.assertEqual(got, [older.id] + ids)
        self.assertEqual(got[-1], "2026-09-26_101500_10")
        self.assertEqual(takes.latest_take(self.rec).id, "2026-09-26_101500_10")


class RecoverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = tempfile.TemporaryDirectory()
        d = cls.media.name
        cls.good_mkv = helpers.make_synthetic_take(os.path.join(d, "good.mkv"), duration_s=2.0, flash_frame=30,
                                                   size="320x240")
        cls.good_wav = os.path.join(d, "good.wav")
        cls.audio_only_mkv = os.path.join(d, "audio_only.mkv")
        for out, extra in ((cls.good_wav, []), (cls.audio_only_mkv, ["-f", "matroska"])):
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", cls.good_mkv, "-map", "0:a",
                            "-c:a", "pcm_s16le", *extra, out], check=True)
        # MKV nunca finalizado (escrito em pipe, como num SIGKILL) e cortado no meio de um cluster
        cls.live_mkv = os.path.join(d, "live.mkv")
        with open(cls.live_mkv, "wb") as f:
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", cls.good_mkv, "-map", "0", "-c", "copy",
                            "-f", "matroska", "pipe:1"], stdout=f, check=True)
        os.truncate(cls.live_mkv, os.path.getsize(cls.live_mkv) * 6 // 10)

    @classmethod
    def tearDownClass(cls):
        cls.media.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rec = os.path.join(self.tmp.name, "recordings")

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, modo, src=None):
        t = takes.new_take(modo, "m", "c" if modo == "av" else "", rec_dir=self.rec, now=NOW)
        if src:
            shutil.copyfile(src, t.raw_path)
        return t

    def recover_one(self, t):
        msgs = takes.recover_takes(self.rec)
        self.assertEqual(len(msgs), 1)
        self.assertIn(t.id, msgs[0])
        return msgs[0], takes.Take.load(t.dir)

    def assert_no_recovered_leftover(self, t):
        self.assertEqual([n for n in os.listdir(t.dir) if "recovered" in n], [])

    def test_good_av_take(self):
        t = self.make("av", self.good_mkv)
        msg, after = self.recover_one(t)
        self.assertEqual((after.status, after.erro), ("gravado", ""))
        self.assertIn("recuperada", msg)

    def test_unfinalized_mkv(self):
        t = self.make("av", self.live_mkv)
        _, after = self.recover_one(t)
        self.assertEqual(after.status, "gravado")

    def test_good_audio_take(self):
        t = self.make("audio", self.good_wav)
        _, after = self.recover_one(t)
        self.assertEqual(after.status, "gravado")

    def test_av_take_without_video_fails(self):
        t = self.make("av", self.audio_only_mkv)
        _, after = self.recover_one(t)
        self.assertEqual(after.status, "falhou")
        self.assertTrue(after.erro)
        self.assert_no_recovered_leftover(t)

    def test_header_only_wav_fails_and_keeps_raw(self):
        t = self.make("audio")
        wav_header_only(t.raw_path)
        with open(t.raw_path, "rb") as f:
            before = f.read()
        msg, after = self.recover_one(t)
        self.assertEqual(after.status, "falhou")
        self.assertIn(after.erro, msg)
        with open(t.raw_path, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assert_no_recovered_leftover(t)

    def test_junk_fails(self):
        t = self.make("av")
        with open(t.raw_path, "wb") as f:
            f.write(b"lixo" * 1000)
        _, after = self.recover_one(t)
        self.assertEqual(after.status, "falhou")
        self.assert_no_recovered_leftover(t)

    def test_missing_raw_fails(self):
        t = self.make("av")
        msg, after = self.recover_one(t)
        self.assertEqual(after.status, "falhou")
        self.assertEqual(after.erro, "Arquivo da gravação não encontrado")

    def test_remux_replaces_raw_when_it_becomes_readable(self):
        t = self.make("av", self.good_mkv)
        real = procs.media_info
        seen = []

        def fake(path):
            seen.append(os.path.basename(path))
            if len(seen) == 1:
                return {"format": {}, "video": None, "audio": None}   # ffprobe "nao le" o raw
            return real(path)

        with mock.patch("studio.takes.media_info", side_effect=fake):
            msg, after = self.recover_one(t)
        self.assertEqual(seen, ["raw.mkv", "raw.recovered.mkv"])
        self.assertEqual(after.status, "gravado")
        self.assertIn("reconstruído", msg)
        info = real(t.raw_path)
        self.assertIsNotNone(info["video"])
        self.assertIsNotNone(info["audio"])
        self.assert_no_recovered_leftover(t)

    def test_only_gravando_is_touched(self):
        t = self.make("av")
        t.status = "convertido"
        t.save()
        mtime = os.stat(t.path("take.json")).st_mtime_ns
        self.assertEqual(takes.recover_takes(self.rec), [])
        self.assertEqual(os.stat(t.path("take.json")).st_mtime_ns, mtime)


if __name__ == "__main__":
    unittest.main()
