import os
import sys
import tempfile
import time
import unittest
from dataclasses import asdict

import soundfile as sf

from studio import audio, capture, procs, render, takes, timeline, watermark
from studio.config import get_modelo
from studio.rvc_client import RvcClient

MIC = "alsa_input.usb-Generalplus_Usb_Audio_Device-00.mono-fallback"
CAM = "/dev/v4l/by-id/usb-Sonix_Technology_Co.__Ltd._A4tech_HD_720P_PC_Camera_SN0001-video-index0"
TAKE_S = 5.0


def wait_until(cond, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


@unittest.skipUnless(os.environ.get("RUN_HARDWARE") == "1",
                     "precisa de câmera, microfone, GPU e do modelo silvio (RUN_HARDWARE=1)")
class EndToEndTest(unittest.TestCase):
    """Gravar -> Parar -> audio alinhado -> RVC silvio -> MP3 -> MP4 verificado. Toda a midia some no fim."""

    def setUp(self):
        self.assertTrue(os.path.exists(CAM), f"câmera não encontrada: {CAM}")
        tmp = tempfile.TemporaryDirectory(prefix="studio_hw_")
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.rec_dir = os.path.join(self.root, "recordings")
        self.videos_dir = os.path.join(self.root, "videos_finais")
        self.times: dict[str, float] = {}

    def lap(self, name: str, t0: float) -> float:
        now = time.monotonic()
        self.times[name] = now - t0
        return now

    def record(self, take: takes.Take) -> tuple[int, float | None]:
        index, err = capture.preflight(MIC, CAM, self.rec_dir)
        self.assertIsNone(err)
        cap = capture.CaptureProcess(capture.build_av_cmd(MIC, CAM, take.raw_path), take.path("ffmpeg.log"), True)
        t0 = time.monotonic()
        cap.start()                                     # thread principal (pdeathsig)
        try:
            self.assertTrue(wait_until(lambda: cap.latest_frame()[0] > 0, 5.0), procs.tail(take.path("ffmpeg.log")))
            t_rec = self.lap("1o frame (REC)", t0)
            time.sleep(1.0)
            self.assertIsNone(cap.early_failure(), procs.tail(take.path("ffmpeg.log")))
            self.assertEqual(capture.mic_status(cap.pid, index), capture.MIC_OK)     # "desconhecido" tambem falha
            time.sleep(max(0.0, TAKE_S - (time.monotonic() - t_rec)))
            seq = cap.latest_frame()[0]                 # so a contagem; o frame nunca e olhado
            fps = cap.fps_measured()
        finally:
            cap.request_stop()
            t_stop = time.monotonic()
            rc = cap.wait_stopped()
            self.lap("parada", t_stop)
        self.assertIn(rc, capture.ACCEPTED_RC)
        self.assertEqual(cap.stop_steps, ["q"])
        self.assertFalse(cap.running)
        return seq, fps

    def test_av_silvio_end_to_end(self):
        modelo = get_modelo("silvio")
        take = takes.new_take("av", MIC, CAM, rec_dir=self.rec_dir)

        seq, fps = self.record(take)
        t = time.monotonic()
        self.assertIsNone(capture.verify_capture(take.raw_path, need_video=True))
        take.status = "gravado"
        take.save()
        t = self.lap("verify_capture", t)

        vi, fit = timeline.extract_aligned_audio(take.raw_path, take.audio_path)
        take.video, take.audio_fit = asdict(vi), asdict(fit)
        take.save()
        t = self.lap("audio alinhado", t)
        self.assertEqual(takes.Take.load(take.dir).video, asdict(vi))      # take.json aceita o que saiu do numpy
        self.assertEqual((vi.w, vi.h), (1280, 720))
        self.assertEqual(fit.gaps, 0)
        dur_audio = sf.info(take.audio_path).duration
        self.assertAlmostEqual(dur_audio, vi.n_frames / render.FPS, delta=0.5)
        mean_db, max_db = audio.volumedetect(take.audio_path)
        self.assertIsNotNone(max_db)

        conv = take.path("silvio.wav")
        client = RvcClient(log_path=take.path("rvc.log"), env={"PYTHONDONTWRITEBYTECODE": "1"})
        client.start()                                  # thread principal (pdeathsig)
        try:
            client.load()
            t = self.lap("RVC carregar", t)
            r = client.convert(take.audio_path, conv, modelo.key)
            t = self.lap("RVC converter", t)
        finally:
            client.close()
        self.assertFalse(client.alive())
        self.assertEqual((r["sr"], r["pedacos"]), (40000, 1))
        self.assertAlmostEqual(r["duracao"], dur_audio, delta=0.02)
        take.status = "convertido"
        take.saidas[modelo.key] = {"wav": "silvio.wav"}
        take.save()

        mp3 = take.path("silvio_IA.mp3")
        audio.export_mp3(conv, mp3, modelo)
        t = self.lap("MP3", t)
        self.assertIn("IA", procs.media_info(mp3)["format"]["tags"]["comment"])

        final = render.render_final(take, modelo, conv, av_offset_ms=0, videos_dir=self.videos_dir)
        t = self.lap("render + verificação", t)
        self.assertEqual(final, os.path.join(self.videos_dir, takes.final_video_name(take.id, modelo.key)))
        self.assertEqual(os.listdir(self.videos_dir), [os.path.basename(final)])
        self.assertFalse(os.path.exists(take.path(render.PART_NAME)))
        wm = watermark.watermark_path(take.dir, vi.w, vi.h, modelo)
        self.assertEqual(render.verify_render(final, vi.n_frames, wm), [])
        info = procs.media_info(final)
        self.assertAlmostEqual(float(info["format"]["duration"]), vi.n_frames / render.FPS, delta=0.05)
        self.assertEqual(int(info["audio"]["sample_rate"]), 48000)
        self.lap("verificação extra", t)
        take.status = "renderizado"
        take.saidas[modelo.key].update(mp3="silvio_IA.mp3", mp4=os.path.relpath(final, take.dir))   # como a GUI
        take.save()
        self.assertTrue(os.path.isfile(os.path.join(take.dir, takes.Take.load(take.dir).saidas[modelo.key]["mp4"])))

        self.assertEqual(procs.run(["pgrep", "-af", self.root]).stdout, "")    # nenhum processo sobrou
        print(f"\n  [hw] preview {seq} frames a {fps and round(fps, 1)} fps; MKV {vi.n_frames} frames "
              f"({vi.fps_medido} fps medidos), âncora {vi.ancora_pts:.3f} s; mic início {fit.inicio:.3f} s, "
              f"taxa {fit.taxa_real} Hz, resíduo {fit.residuo_ms} ms; volume médio {mean_db} dB, pico {max_db} dB",
              file=sys.stderr)
        print("  [hw] tempos: " + ", ".join(f"{k} {v:.2f} s" for k, v in self.times.items()), file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
