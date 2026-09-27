import os
import shutil
import subprocess
import tempfile
import time
import unittest
from dataclasses import FrozenInstanceError
from unittest import mock

from studio import devices, procs
from studio.devices import Source

# saida real de `pactl list short sources` (PipeWire, 2026-09-26): 2 mics USB + 3 monitores
SOURCES_SHORT = """\
54\talsa_input.usb-Generalplus_Usb_Audio_Device-00.mono-fallback\tPipeWire\ts16le 1ch 48000Hz\tSUSPENDED
55\talsa_output.usb-ME6S_c1_USB_AUDIO-00.iec958-stereo.monitor\tPipeWire\ts16le 2ch 48000Hz\tSUSPENDED
56\talsa_input.usb-ME6S_c1_USB_AUDIO-00.mono-fallback\tPipeWire\ts24le 1ch 48000Hz\tSUSPENDED
57\talsa_output.pci-0000_07_00.4.analog-stereo.monitor\tPipeWire\ts32le 2ch 48000Hz\tSUSPENDED
3420\talsa_output.pci-0000_05_00.1.hdmi-stereo-extra1.monitor\tPipeWire\ts32le 2ch 48000Hz\tSUSPENDED
"""

# saida real de `LC_ALL=C pactl list source-outputs` com 2 ffmpeg gravando ao mesmo tempo:
# pid 873534 no ME6S (-i alsa_input.usb-ME6S...) e pid 873535 com nome invalido
# (-i nao_existe_este_mic): o PipeWire caiu no mic padrao (54) sem erro
SOURCE_OUTPUTS = """\
Source Output #3403
\tDriver: PipeWire
\tOwner Module: n/a
\tClient: 3401
\tSource: 56
\tSample Specification: s16le 1ch 48000Hz
\tChannel Map: mono
\tFormat: pcm, format.sample_format = "\\"s16le\\""  format.rate = "48000"  format.channels = "1"  format.channel_map = "\\"mono\\""
\tCorked: no
\tMute: no
\tVolume: mono: 65536 / 100% / 0.00 dB
\t        balance 0.00
\tBuffer Latency: 0 usec
\tSource Latency: 0 usec
\tResample method: PipeWire
\tProperties:
\t\tclient.api = "pipewire-pulse"
\t\tpulse.server.type = "unix"
\t\tapplication.name = "Lavf60.16.100"
\t\tapplication.process.id = "873534"
\t\tapplication.process.user = "peras"
\t\tapplication.process.host = "operario"
\t\tapplication.process.binary = "ffmpeg"
\t\tapplication.language = "C"
\t\twindow.x11.display = ":1"
\t\tapplication.process.machine_id = "b32d67aae707420585e64ac45a416607"
\t\tmedia.name = "record"
\t\tnode.rate = "1/48000"
\t\tnode.latency = "2400/48000"
\t\ttarget.object = "alsa_input.usb-ME6S_c1_USB_AUDIO-00.mono-fallback"
\t\tstream.is-live = "true"
\t\tnode.name = "Lavf60.16.100"
\t\tnode.want-driver = "true"
\t\tnode.autoconnect = "true"
\t\tmedia.class = "Stream/Input/Audio"
\t\tadapt.follower.spa-node = ""
\t\tobject.register = "false"
\t\tfactory.id = "6"
\t\tclock.quantum-limit = "8192"
\t\tfactory.mode = "merge"
\t\taudio.adapt.follower = ""
\t\tlibrary.name = "audioconvert/libspa-audioconvert"
\t\tclient.id = "64"
\t\tobject.id = "83"
\t\tobject.serial = "3403"
\t\tpulse.attr.maxlength = "4194304"
\t\tpulse.attr.fragsize = "4800"
\t\tmodule-stream-restore.id = "source-output-by-application-name:Lavf60.16.100"

Source Output #3404
\tDriver: PipeWire
\tOwner Module: n/a
\tClient: 3402
\tSource: 54
\tSample Specification: s16le 1ch 48000Hz
\tChannel Map: mono
\tFormat: pcm, format.sample_format = "\\"s16le\\""  format.rate = "48000"  format.channels = "1"  format.channel_map = "\\"mono\\""
\tCorked: no
\tMute: no
\tVolume: mono: 65536 / 100% / 0.00 dB
\t        balance 0.00
\tBuffer Latency: 0 usec
\tSource Latency: 0 usec
\tResample method: PipeWire
\tProperties:
\t\tclient.api = "pipewire-pulse"
\t\tpulse.server.type = "unix"
\t\tapplication.name = "Lavf60.16.100"
\t\tapplication.process.id = "873535"
\t\tapplication.process.user = "peras"
\t\tapplication.process.host = "operario"
\t\tapplication.process.binary = "ffmpeg"
\t\tapplication.language = "C"
\t\twindow.x11.display = ":1"
\t\tapplication.process.machine_id = "b32d67aae707420585e64ac45a416607"
\t\tmedia.name = "record"
\t\tnode.rate = "1/48000"
\t\tnode.latency = "2400/48000"
\t\ttarget.object = "nao_existe_este_mic"
\t\tstream.is-live = "true"
\t\tnode.name = "Lavf60.16.100"
\t\tnode.want-driver = "true"
\t\tnode.autoconnect = "true"
\t\tmedia.class = "Stream/Input/Audio"
\t\tadapt.follower.spa-node = ""
\t\tobject.register = "false"
\t\tfactory.id = "6"
\t\tclock.quantum-limit = "8192"
\t\tfactory.mode = "merge"
\t\taudio.adapt.follower = ""
\t\tlibrary.name = "audioconvert/libspa-audioconvert"
\t\tclient.id = "63"
\t\tobject.id = "81"
\t\tobject.serial = "3404"
\t\tpulse.attr.maxlength = "4194304"
\t\tpulse.attr.fragsize = "4800"
\t\tmodule-stream-restore.id = "source-output-by-application-name:Lavf60.16.100"
"""


def completed(stdout: str = "", rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["pactl"], rc, stdout, "")


class ParseSourcesTest(unittest.TestCase):
    def test_real_output_without_monitors(self):
        self.assertEqual(devices.parse_sources(SOURCES_SHORT), [
            Source(54, "alsa_input.usb-Generalplus_Usb_Audio_Device-00.mono-fallback"),
            Source(56, "alsa_input.usb-ME6S_c1_USB_AUDIO-00.mono-fallback"),
        ])

    def test_usb_first_then_name(self):
        pci = "60\talsa_input.pci-0000_07_00.6.analog-stereo\tPipeWire\ts32le 2ch 48000Hz\tSUSPENDED\n"
        names = [s.name for s in devices.parse_sources(pci + SOURCES_SHORT)]
        self.assertEqual(names, ["alsa_input.usb-Generalplus_Usb_Audio_Device-00.mono-fallback",
                                 "alsa_input.usb-ME6S_c1_USB_AUDIO-00.mono-fallback",
                                 "alsa_input.pci-0000_07_00.6.analog-stereo"])

    def test_ignores_blank_and_garbage(self):
        self.assertEqual(devices.parse_sources(""), [])
        self.assertEqual(devices.parse_sources("\nlixo\nx\ty\n54\t\n"), [])

    def test_source_is_frozen(self):
        with self.assertRaises(FrozenInstanceError):
            Source(1, "a").name = "b"


class ListMicsTest(unittest.TestCase):
    def test_runs_pactl_short_sources(self):
        with mock.patch("studio.devices.run", return_value=completed(SOURCES_SHORT)) as run:
            mics = devices.list_mics()
        self.assertEqual([m.index for m in mics], [54, 56])
        self.assertEqual(run.call_args.args[0], ["pactl", "list", "short", "sources"])
        self.assertIsNotNone(run.call_args.kwargs.get("timeout"))

    def test_failures_give_empty_list(self):
        with mock.patch("studio.devices.run", return_value=completed(SOURCES_SHORT, rc=1)):
            self.assertEqual(devices.list_mics(), [])
        with mock.patch("studio.devices.run", side_effect=subprocess.TimeoutExpired(["pactl"], 5)):
            self.assertEqual(devices.list_mics(), [])
        with mock.patch("studio.devices.run", side_effect=FileNotFoundError("setpriv")):
            self.assertEqual(devices.list_mics(), [])

    def test_real_pactl_smoke(self):
        # le o servidor de audio de verdade (so leitura); sem pactl a lista vem vazia
        mics = devices.list_mics()
        self.assertIsInstance(mics, list)
        for m in mics:
            self.assertIsInstance(m, Source)
            self.assertFalse(m.name.endswith(".monitor"))


class ListCamerasTest(unittest.TestCase):
    def test_only_existing_index0_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            by_id = os.path.join(tmp, "by-id")
            os.mkdir(by_id)
            for node in ("video0", "video1", "video2"):
                open(os.path.join(tmp, node), "w").close()
            links = {
                "usb-B_Cam_SN1-video-index0": "../video2",
                "usb-A_Cam_SN0-video-index0": "../video0",
                "usb-A_Cam_SN0-video-index1": "../video1",     # no de metadados
                "usb-C_Cam_SN9-video-index0": "../video9",     # camera desplugada: link quebrado
            }
            for name, target in links.items():
                os.symlink(target, os.path.join(by_id, name))
            self.assertEqual(devices.list_cameras(by_id), [
                os.path.join(by_id, "usb-A_Cam_SN0-video-index0"),
                os.path.join(by_id, "usb-B_Cam_SN1-video-index0"),
            ])

    def test_missing_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(devices.list_cameras(os.path.join(tmp, "nada")), [])


class SourceOutputsTest(unittest.TestCase):
    def test_real_output_two_recorders(self):
        # "Source Latency: 0 usec" nao pode ser confundido com "Source: N"
        self.assertEqual(devices.parse_source_outputs(SOURCE_OUTPUTS), {873534: 56, 873535: 54})

    def test_empty_and_block_without_source(self):
        self.assertEqual(devices.parse_source_outputs(""), {})
        cut = SOURCE_OUTPUTS.replace("\tSource: 56\n", "")
        self.assertEqual(devices.parse_source_outputs(cut), {873535: 54})

    def test_mic_source_of_pid_uses_c_locale(self):
        with mock.patch("studio.devices.run", return_value=completed(SOURCE_OUTPUTS)) as run:
            self.assertEqual(devices.mic_source_of_pid(873534), 56)
            self.assertIsNone(devices.mic_source_of_pid(1))
        self.assertEqual(run.call_args.args[0], ["pactl", "list", "source-outputs"])
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")
        self.assertIsNotNone(run.call_args.kwargs.get("timeout"))

    def test_mic_source_of_pid_failures(self):
        with mock.patch("studio.devices.run", return_value=completed(SOURCE_OUTPUTS, rc=1)):
            self.assertIsNone(devices.mic_source_of_pid(873534))
        with mock.patch("studio.devices.run", side_effect=subprocess.TimeoutExpired(["pactl"], 5)):
            self.assertIsNone(devices.mic_source_of_pid(873534))


class FreeBytesTest(unittest.TestCase):
    def test_matches_disk_usage_and_walks_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref = shutil.disk_usage(tmp).free
            here = devices.free_bytes(tmp)
            missing = devices.free_bytes(os.path.join(tmp, "recordings", "2026-09-26_101500"))
        self.assertIsInstance(here, int)
        self.assertLess(abs(here - ref), 256 * 1024**2)
        self.assertLess(abs(missing - ref), 256 * 1024**2)


@unittest.skipUnless(os.environ.get("RUN_HARDWARE") == "1", "precisa de microfone e câmera reais (RUN_HARDWARE=1)")
class RealDevicesTest(unittest.TestCase):
    def test_source_of_real_recorder(self):
        mics = devices.list_mics()
        self.assertTrue(mics, "nenhum microfone no pactl")
        mic = mics[0]
        # grava para o muxer null: nada vai para o disco
        p = procs.spawn(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "pulse",
                         "-sample_rate", "48000", "-channels", "1", "-i", mic.name, "-t", "6", "-f", "null", "-"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            found = None
            deadline = time.monotonic() + 4
            while found is None and time.monotonic() < deadline:
                time.sleep(0.5)
                found = devices.mic_source_of_pid(p.pid)
            self.assertEqual(found, mic.index)
        finally:
            p.terminate()
            p.wait(timeout=5)

    def test_real_cameras(self):
        cams = devices.list_cameras()
        self.assertTrue(cams, "nenhuma camera em /dev/v4l/by-id")
        self.assertTrue(all(c.endswith("-video-index0") for c in cams))


if __name__ == "__main__":
    unittest.main()
