import dataclasses
import errno
import os
import signal
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

import soundfile as sf
from PIL import Image

from studio import procs, render, timeline, watermark
from studio.config import Modelo, get_modelo, metadata_tags
from studio.render import RenderError
from studio.takes import final_video_name, new_take
from tests import helpers

FPS = 30
SILVIO = get_modelo("silvio")
FLASH = 60          # frame 60 do MKV = frame 59 do MP4 (o render comeca no 2o frame, a ancora)
OVERLAY = "[2:v]format=yuva420p[wm];[v0][wm]overlay=0:0:format=yuv420,"


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
                   stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=120)


def fake_converted(audio_wav: str, out_wav: str, delta_ms: int) -> str:
    # "saida do RVC": audio.wav reamostrado para 40 kHz e delta_ms mais curto (<0) ou mais longo (>0)
    n = round(sf.info(audio_wav).frames * 40000 / 48000)
    extra = round(abs(delta_ms) * 40)
    tail = f"atrim=end_sample={n - extra}" if delta_ms < 0 else f"apad=pad_len={extra}"
    ffmpeg("-i", audio_wav, "-af", f"aresample=40000,{tail}", "-ar", "40000", "-c:a", "pcm_s16le", out_wav)
    return out_wav


def without_overlay(fc: str) -> str:
    if OVERLAY not in fc:
        raise AssertionError(f"overlay não encontrado no filtro: {fc}")
    return fc.replace(OVERLAY, "[v0]")


def probe(mp4: str) -> dict:
    return procs.ffprobe_json(mp4, "-count_packets", "-show_streams", "-show_format")


def listdir(path: str) -> list[str]:
    return sorted(os.listdir(path)) if os.path.isdir(path) else []


class RenderFinalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        take = new_take("av", mic="fake", rec_dir=os.path.join(cls.tmp.name, "recordings"))
        helpers.make_synthetic_take(take.raw_path, audio_offset_s=0.40, duration_s=4.0, flash_frame=FLASH,
                                    size="640x360")
        vi, fit = timeline.extract_aligned_audio(take.raw_path, take.audio_path)
        take.video, take.audio_fit = dataclasses.asdict(vi), dataclasses.asdict(fit)
        cls.take, cls.n = take, vi.n_frames
        cls.short = fake_converted(take.audio_path, take.path("silvio.wav"), -60)
        cls.long = fake_converted(take.audio_path, take.path("silvio_longo.wav"), +40)
        cls.mp4 = render.render_final(take, SILVIO, cls.short, videos_dir=cls.videos("base"))
        cls.wm = take.path("wm_640x360_silvio.png")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def videos(cls, name: str) -> str:
        return os.path.join(cls.tmp.name, f"videos_{name}")

    def render(self, name: str, conv: str | None = None, **kw) -> str:
        return render.render_final(self.take, SILVIO, conv or self.short, videos_dir=self.videos(name), **kw)

    def assert_nothing_published(self, name: str) -> None:
        self.assertEqual(listdir(self.videos(name)), [])
        self.assertFalse(os.path.exists(self.take.path("render.part.mp4")))

    def assert_beep_on_flash(self, mp4: str) -> None:
        flash = helpers.flash_frame_index(mp4)
        self.assertEqual(flash, FLASH - 1)
        # spec: <= 1 frame; medido ~0,4 ms
        self.assertAlmostEqual(helpers.beep_onset_s(mp4), flash / FPS, delta=0.002)

    def assert_lengths(self, mp4: str) -> None:
        info = probe(mp4)
        (v,) = [s for s in info["streams"] if s["codec_type"] == "video"]
        (a,) = [s for s in info["streams"] if s["codec_type"] == "audio"]
        self.assertEqual(int(v["nb_read_packets"]), self.n)
        self.assertAlmostEqual(float(v["duration"]), self.n / FPS, delta=0.001)
        self.assertAlmostEqual(float(a["duration"]), float(v["duration"]), delta=0.002)
        self.assertEqual((a["sample_rate"], a["channels"]), ("48000", 1))

    # --- saida boa ---

    def test_final_name_and_no_part_left(self):
        self.assertEqual(self.mp4, os.path.join(self.videos("base"), final_video_name(self.take.id, "silvio")))
        self.assertTrue(os.path.isfile(self.mp4))
        self.assertFalse(os.path.exists(self.take.path("render.part.mp4")))

    def test_short_40k_wav_beep_on_flash(self):
        self.assertEqual(self.n, 119)
        self.assert_beep_on_flash(self.mp4)

    def test_long_40k_wav_beep_on_flash(self):
        mp4 = self.render("longo", conv=self.long)
        self.assert_beep_on_flash(mp4)
        self.assert_lengths(mp4)

    def test_lengths_and_tags(self):
        self.assert_lengths(self.mp4)
        tags = probe(self.mp4)["format"]["tags"]
        for key, value in metadata_tags(SILVIO).items():
            self.assertEqual(tags[key], value)

    def test_av_offset_moves_audio(self):
        base = helpers.beep_onset_s(self.mp4)
        # sem take.video o render le a timeline do raw.mkv
        later = render.render_final(dataclasses.replace(self.take, video={}), SILVIO, self.short,
                                    av_offset_ms=100, videos_dir=self.videos("mais100"))
        earlier = self.render("menos100", av_offset_ms=-100)
        self.assertAlmostEqual(helpers.beep_onset_s(later) - base, 0.100, delta=0.002)
        self.assertAlmostEqual(helpers.beep_onset_s(earlier) - base, -0.100, delta=0.002)
        self.assertEqual(helpers.flash_frame_index(later), FLASH - 1)
        self.assert_lengths(later)
        self.assert_lengths(earlier)

    # --- verify_render ---

    def test_verify_ok(self):
        self.assertEqual(render.verify_render(self.mp4, self.n, self.wm), [])

    def test_verify_flags_video_without_watermark(self):
        out = os.path.join(self.tmp.name, "sem_marca.mp4")
        cmd = render.build_render_cmd(self.take.raw_path, self.short, self.wm, out, self.n,
                                      self.take.video["ancora_pts"], SILVIO, encoder="x264")
        i = cmd.index("-filter_complex") + 1
        cmd[i] = without_overlay(cmd[i])
        subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=120)
        problems = render.verify_render(out, self.n, self.wm)
        self.assertTrue(any(p.startswith("Texto da marca d'água") for p in problems), problems)
        self.assertTrue(any(p.startswith("Faixa escura") for p in problems), problems)

    def test_verify_flags_wrong_frame_count(self):
        problems = render.verify_render(self.mp4, self.n + 1, self.wm)
        self.assertEqual(problems, [f"O vídeo gerado tem {self.n} frames (esperado {self.n + 1})"])

    def test_verify_flags_missing_tags(self):
        bare = os.path.join(self.tmp.name, "sem_tags.mp4")
        ffmpeg("-i", self.mp4, "-map", "0", "-c", "copy", "-map_metadata", "-1", bare)
        problems = render.verify_render(bare, self.n, self.wm)
        self.assertEqual(problems, ["Metadado comment sem o aviso de IA", "Metadado title ausente",
                                    "Metadado description ausente"])

    def test_verify_flags_short_audio(self):
        short = os.path.join(self.tmp.name, "audio_curto.mp4")
        ffmpeg("-i", self.mp4, "-map", "0", "-c:v", "copy", "-af", "atrim=end=1", "-c:a", "aac", short)
        problems = render.verify_render(short, self.n, self.wm)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("duração do áudio", problems[0])

    def test_verify_flags_png_of_other_size(self):
        small = os.path.join(self.tmp.name, "wm_320x180.png")
        Image.new("RGBA", (320, 180)).save(small)
        self.assertEqual(render.verify_render(self.mp4, self.n, small),
                         ["O vídeo tem 640x360, mas a marca d'água tem 320x180"])

    def test_verify_unreadable_file(self):
        junk = os.path.join(self.tmp.name, "lixo.mp4")
        with open(junk, "wb") as f:
            f.write(b"nao e video")
        problems = render.verify_render(junk, self.n, self.wm)
        self.assertEqual(len(problems), 1)
        self.assertIn("lixo.mp4", problems[0])

    # --- fail-closed ---

    def test_verification_failure_keeps_part_in_take(self):
        self.addCleanup(lambda: os.path.exists(self.take.path("render.part.mp4"))
                        and os.remove(self.take.path("render.part.mp4")))
        real = render.build_filter
        with mock.patch.object(render, "build_filter", lambda n, anc, off=0: without_overlay(real(n, anc, off))):
            with self.assertRaises(RenderError) as cm:
                self.render("falha_verif")
        self.assertIn("Texto da marca d'água", str(cm.exception))
        self.assertTrue(os.path.isfile(self.take.path("render.part.mp4")))
        self.assertEqual(listdir(self.videos("falha_verif")), [])

    def test_png_of_wrong_size_is_refused_before_ffmpeg(self):
        small = os.path.join(self.tmp.name, "wm_errado.png")
        Image.new("RGBA", (320, 180)).save(small)
        with mock.patch.object(watermark, "watermark_path", return_value=small), \
                mock.patch.object(procs, "spawn", wraps=procs.spawn) as spawn:
            with self.assertRaises(RenderError) as cm:
                self.render("png_errado")
        self.assertEqual(str(cm.exception), "Marca d'água 320x180 não confere com o vídeo 640x360")
        self.assertFalse([c for c in spawn.call_args_list if "-filter_complex" in c.args[0]])
        self.assert_nothing_published("png_errado")

    def test_watermark_that_does_not_fit_is_render_error(self):
        # a marca real e fail-closed: em 320x180 o aviso nao cabe na coluna 9:16 e ela levanta ValueError
        with mock.patch.object(render, "_frame_size", return_value=(320, 180)), \
                mock.patch.object(procs, "spawn", wraps=procs.spawn) as spawn:
            with self.assertRaises(RenderError) as cm:
                self.render("wm_nao_cabe")
        self.assertTrue(str(cm.exception).startswith("O texto da marca d'água não cabe"), str(cm.exception))
        self.assertFalse([c for c in spawn.call_args_list if "-filter_complex" in c.args[0]])
        self.assertFalse(os.path.exists(self.take.path("wm_320x180_silvio.png")))
        self.assert_nothing_published("wm_nao_cabe")

    def test_refuses_model_without_warning(self):
        with self.assertRaises(RenderError) as cm:
            render.render_final(self.take, Modelo("mudo", "Mudo", "Mudo", ""), self.short,
                                videos_dir=self.videos("mudo"))
        self.assertEqual(str(cm.exception), "Modelo Mudo sem texto de aviso: o vídeo não pode ser gerado")
        self.assert_nothing_published("mudo")

    def test_refuses_audio_only_take_and_missing_wav(self):
        with self.assertRaises(RenderError) as cm:
            render.render_final(dataclasses.replace(self.take, modo="audio"), SILVIO, self.short,
                                videos_dir=self.videos("so_audio"))
        self.assertEqual(str(cm.exception), "Esta tomada não tem vídeo")
        with self.assertRaises(RenderError) as cm:
            self.render("sem_wav", conv=self.take.path("orochi.wav"))
        self.assertEqual(str(cm.exception), "Áudio convertido não encontrado — converta a voz de novo")

    # --- encoder, cancelamento e timeout ---

    def test_x264_fallback_when_nvenc_fails(self):
        # sem GPU visivel o h264_nvenc falha no cuInit (probe_render-watermark.md)
        with mock.patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": ""}), \
                mock.patch.object(render, "build_render_cmd", wraps=render.build_render_cmd) as build:
            mp4 = self.render("x264")
        self.assertEqual([c.kwargs["encoder"] for c in build.call_args_list], ["nvenc", "x264"])
        with open(mp4, "rb") as f:
            self.assertIn(b"x264 - core", f.read())
        self.assertEqual(render.verify_render(mp4, self.n, self.wm), [])
        with open(self.take.path("render.log"), encoding="utf-8", errors="replace") as f:
            self.assertIn("h264_nvenc", f.read())

    def test_both_encoders_failing(self):
        bad = {enc: ["-c:v", "encoder_que_nao_existe"] for enc in render.ENCODERS}
        with mock.patch.dict(render.ENCODER_FLAGS, bad):
            with self.assertRaises(RenderError) as cm:
                self.render("ambos_falham")
        self.assertTrue(str(cm.exception).startswith("Falha ao gerar o vídeo (código "), str(cm.exception))
        self.assert_nothing_published("ambos_falham")

    def test_cancel_sends_sigterm(self):
        cancel = threading.Event()
        started = []
        real_spawn = procs.spawn

        def spawn_then_cancel(argv, **kw):
            p = real_spawn(argv, **kw)
            if "-filter_complex" in argv:
                started.append(p)
                cancel.set()            # botao Cancelar logo depois do ffmpeg do render nascer
            return p

        with mock.patch.object(procs, "spawn", spawn_then_cancel):
            with self.assertRaises(RenderError) as cm:
                self.render("cancelado", cancel=cancel)
        self.assertEqual(str(cm.exception), "Render cancelado")
        self.assertEqual(len(started), 1)
        self.assertIn(started[0].returncode, (-signal.SIGTERM, 255))
        self.assert_nothing_published("cancelado")

    def test_timeout_stops_ffmpeg(self):
        with mock.patch.object(render, "TIMEOUT_FACTOR", 0), mock.patch.object(render, "TIMEOUT_BASE_S", 0.0):
            with self.assertRaises(RenderError) as cm:
                self.render("timeout")
        self.assertEqual(str(cm.exception), "O render demorou demais e foi interrompido")
        self.assert_nothing_published("timeout")

    # --- disco cheio e outros erros de arquivo: RenderError em PT e nenhum .part sobrando ---

    def assert_no_part_files(self) -> None:
        self.assertEqual([n for n in os.listdir(self.take.dir) if n.endswith(".part") or ".part." in n], [])

    def test_disk_full_writing_watermark(self):
        # a marca do Orochi ainda nao existe nesta tomada: o PNG e gravado agora e o disco enche no meio
        def full_disk_save(img, fp, *args, **kwargs):
            with open(fp, "wb") as f:
                f.write(b"\x89PNG metade")
            raise OSError(errno.ENOSPC, "No space left on device")

        orochi = get_modelo("orochi")
        with mock.patch.object(watermark.Image.Image, "save", full_disk_save):
            with self.assertRaises(RenderError) as cm:
                render.render_final(self.take, orochi, self.short, videos_dir=self.videos("cheio_png"))
        self.assertEqual(str(cm.exception), "Disco cheio — libere espaço")
        self.assertFalse(os.path.exists(self.take.path("wm_640x360_orochi.png")))
        self.assert_no_part_files()
        self.assert_nothing_published("cheio_png")

    def test_disk_full_in_ffmpeg_does_not_retry(self):
        cmd = ["sh", "-c", "echo 'Error writing trailer of render.part.mp4: No space left on device' >&2; exit 1"]
        with mock.patch.object(render, "build_render_cmd", return_value=cmd) as build:
            with self.assertRaises(RenderError) as cm:
                self.render("cheio_ffmpeg")
        self.assertEqual(str(cm.exception), "Disco cheio — libere espaço")
        self.assertEqual(build.call_count, 1)          # com o disco cheio o x264 falharia igual
        self.assert_no_part_files()
        self.assert_nothing_published("cheio_ffmpeg")

    def test_disk_full_publishing(self):
        real_replace = os.replace
        final_dir = self.videos("cheio_final")

        def replace(src, dst, *args, **kwargs):
            if os.path.dirname(dst) == final_dir:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_replace(src, dst, *args, **kwargs)

        with mock.patch.object(render.os, "replace", replace):
            with self.assertRaises(RenderError) as cm:
                self.render("cheio_final")
        self.assertEqual(str(cm.exception), "Disco cheio — libere espaço")
        self.assert_no_part_files()
        self.assert_nothing_published("cheio_final")

    def test_other_os_error_is_render_error(self):
        denied = PermissionError(errno.EACCES, "Permission denied", self.videos("sem_permissao"))
        with mock.patch.object(render.os, "makedirs", side_effect=denied):
            with self.assertRaises(RenderError) as cm:
                self.render("sem_permissao")
        self.assertEqual(str(cm.exception), "Erro ao acessar o disco (videos_sem_permissao): Permission denied")
        self.assert_no_part_files()


class AnchorPtsTest(unittest.TestCase):
    # camera a 15 fps (cada pacote vira 2 frames no MP4) e o 1o pacote as vezes nao decodifica: o video tem de
    # comecar no pacote da ancora (2o pacote, pelo pts), o mesmo relogio do audio.wav e da calibracao
    FLASH_PACKET = 30

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def render_case(self, name: str, stub: bool, video_start_s: float):
        take = new_take("av", mic="fake", rec_dir=os.path.join(self.tmp.name, name))
        helpers.make_mjpeg_take(take.raw_path, fps=15, flash_packet=self.FLASH_PACKET, video_start_s=video_start_s,
                                stub_first_packet=stub)
        vi, fit = timeline.extract_aligned_audio(take.raw_path, take.audio_path)
        take.video, take.audio_fit = dataclasses.asdict(vi), dataclasses.asdict(fit)
        conv = fake_converted(take.audio_path, take.path("silvio.wav"), -60)
        mp4 = render.render_final(take, SILVIO, conv, videos_dir=os.path.join(self.tmp.name, f"videos_{name}"))
        return take, vi, mp4

    def test_video_starts_at_anchor_packet(self):
        for stub in (False, True):
            for start in (0.0, 1.0):
                with self.subTest(pacote0_ruim=stub, video_start_s=start):
                    take, vi, mp4 = self.render_case(f"stub{int(stub)}_start{start:g}", stub, start)
                    pk = timeline.read_packets(take.raw_path, "v")
                    self.assertEqual(vi.ancora_pts, pk[1][0])
                    flash = helpers.flash_frame_index(mp4)
                    # pacote 1 (ancora) = frames 0 e 1 do MP4; o flash do pacote 30 cai nos frames 58 e 59
                    self.assertEqual(flash // 2, self.FLASH_PACKET - 1)
                    self.assertAlmostEqual(helpers.beep_onset_s(mp4), (flash // 2) * 2 / FPS, delta=0.002)
                    self.assertEqual(render.verify_render(mp4, vi.n_frames, take.path("wm_640x360_silvio.png")), [])


class ShortTakeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.rec = os.path.join(cls.tmp.name, "recordings")
        cls.videos = os.path.join(cls.tmp.name, "videos_finais")
        cls.conv = os.path.join(cls.tmp.name, "silvio.wav")
        ffmpeg("-f", "lavfi", "-i", "aevalsrc=0:s=40000:c=mono:d=0.2", "-c:a", "pcm_s16le", cls.conv)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def take_with_frames(self, frames: int):
        # MKV como o do gravador (MJPEG + PCM mono 48k), so com `frames` pacotes de video
        take = new_take("av", mic="fake", rec_dir=self.rec)
        d = f"{frames / FPS:.6f}"
        ffmpeg("-f", "lavfi", "-i", f"testsrc2=s=320x240:r={FPS}:d={d}", "-f", "lavfi",
               "-i", f"aevalsrc=0:s=48000:c=mono:d={d}", "-c:v", "mjpeg", "-pix_fmt", "yuvj422p",
               "-c:a", "pcm_s16le", "-f", "matroska", take.raw_path)
        self.assertEqual(len(timeline.read_packets(take.raw_path, "v")), frames)
        return take

    def test_one_or_two_packets_is_too_short(self):
        # duplo clique em Gravar: o q chega antes do 3o frame; com 2 pacotes sairia um MP4 de 1 frame (33 ms)
        for frames in (1, 2):
            take = self.take_with_frames(frames)
            with_video = dataclasses.replace(take, video=dataclasses.asdict(timeline.video_info(take.raw_path)))
            for t in (take, with_video):
                with self.subTest(frames=frames, take_video=bool(t.video)), \
                        mock.patch.object(procs, "spawn", wraps=procs.spawn) as spawn:
                    with self.assertRaises(RenderError) as cm:
                        render.render_final(t, SILVIO, self.conv, videos_dir=self.videos)
                    self.assertEqual(str(cm.exception), "Gravação curta demais para gerar o vídeo")
                    self.assertFalse([c for c in spawn.call_args_list if "-filter_complex" in c.args[0]])
        self.assertEqual(listdir(self.videos), [])

    def test_three_packets_give_two_frames(self):
        take = self.take_with_frames(3)
        mp4 = render.render_final(take, SILVIO, self.conv, videos_dir=os.path.join(self.tmp.name, "videos_3"))
        self.assertEqual(os.path.basename(mp4), final_video_name(take.id, "silvio"))
        video = [s for s in probe(mp4)["streams"] if s["codec_type"] == "video"][0]
        self.assertEqual(int(video["nb_read_packets"]), 2)


if __name__ == "__main__":
    unittest.main()
