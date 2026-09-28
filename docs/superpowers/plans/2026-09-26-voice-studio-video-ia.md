# Voice Studio v2 (vídeo com voz de IA, marca d'água e Drive) — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gravar webcam + microfone no Voice Studio, trocar a voz pela convertida (Silvio/Orochi), gerar um MP4 com a boca sincronizada e marca d'água permanente de "voz gerada por IA", e enviar os vídeos prontos para uma pasta do Google Drive compartilhada com o usuário (script + botão).

**Architecture:** O `orochi_studio.py` vira um ponto de entrada fino, e a lógica vai para o pacote `studio/`, com um módulo por responsabilidade (captura, alinhamento, marca d'água, render, RVC, Drive). Um único ffmpeg grava mic (1ª entrada) + câmera no mesmo relógio. O RVC roda num processo *worker* separado. Toda saída passa por `.part` → verificação *fail-closed* → `os.replace`. A GUI Tk só consome eventos de uma fila, e o trabalho pesado roda em `JobRunner`s seriais.

**Tech Stack:** Python 3.12 (venv do Applio: numpy, soundfile, PIL 12.3, torch; sem pytest), Tkinter, ffmpeg/ffprobe 6.1.1 (h264_nvenc com fallback libx264), PipeWire/`pactl`, `setpriv`, rclone v1.75.1, `unittest` da stdlib.

**Spec:** `docs/superpowers/specs/2026-09-26-voice-studio-video-ia-design.md`. As evidências dos testes de hardware estão em `docs/superpowers/probes/2026-09-26/`, e os comandos marcados [V] na spec foram rodados nesta máquina.

## Global Constraints

- Python 3.12. Os testes rodam com `Applio/.venv/bin/python -m unittest discover -s tests -t .` (ou `./run_tests.sh`). **Nada de pip/apt**: não instalar nada no venv do Applio (D11).
- Só `studio/gui*.py` importam `tkinter`. Só `studio/rvc_worker.py` importa torch/Applio. `studio/drive.py` e `enviar_drive.py` usam **só a stdlib**.
- Todo caminho é absoluto. Ninguém chama `os.chdir` no processo da GUI. Só o worker faz `os.chdir(APPLIO_DIR)`, uma vez.
- Todo processo filho nasce via `studio.procs` com o prefixo `setpriv --pdeathsig TERM --`. `preexec_fn` é proibido. O gravador, o PreviewOnly e o worker RVC nascem na **thread principal**.
- Todo ffmpeg que não é o gravador recebe `-nostdin` e `stdin=DEVNULL`. O render roda com `nice -n 10`.
- No gravador, **nunca** usar `-t`, `-to` ou `-frames`, porque com `-copyts` isso gera um arquivo vazio com rc=0. A duração é um `root.after` que dispara a parada normal.
- **Proibido:** `apad` + `-shortest` (trava no ffmpeg 6.1.1). O `aresample=48000` é explícito no render.
- Saídas gravadas em `.part` + `os.replace`. WAV temporário usa `*.part.wav`, porque o soundfile infere o formato pela extensão.
- Nenhum arquivo sem marca d'água ou quebrado chega a `videos_finais/` nem ao Drive. Não existe caminho de código que renderize sem a sobreposição.
- rclone: só `copyto` e `lsjson`. **Proibidos:** `sync`, `move`, `delete`, `dedupe`, `link` e `--no-check-dest`. Nunca logar `config show` nem usar `-vv`. O token fica em `~/.config/rclone/rclone.conf`, nunca no projeto.
- Nomes: `videos_finais/<AAAA-MM-DD_HHMMSS>_<modelo>_IA.mp4`, `recordings/<id>/`, `<modelo>_IA.mp3`, remote rclone `iavoz`.
- Textos fixos: "VOZ GERADA POR IA", "paródia · homenagem", "Não é a voz real de Silvio Santos", "Não é a voz real do Orochi".
- Limites: tomada de no máximo 300 s (`MAX_TAKE_S`); 2 GB livres para gravar (`MIN_FREE_BYTES`); RVC em pedaços de ≤ 30 s acima de 40 s.
- Estilo: identificadores em inglês, exceto os campos de dados definidos no contrato (em PT). Comentários curtos em português sem acento, como no código atual. Mensagens ao usuário em PT-BR com acento.
- Testes que precisam de câmera, mic, GPU ou Drive reais só rodam com `RUN_HARDWARE=1`. Nos testes, nunca salvar nem olhar frames da câmera real, e sempre apagar a mídia real gravada.
- Commits por etapa, sem push. Toda mensagem termina com `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Estrutura de arquivos

| Arquivo | Responsabilidade | Task |
|---|---|---|
| `studio/config.py` | caminhos, `MODELOS`, textos de aviso, `estado.json` (leitura com tipos corrigidos, mescla atômica) | 1 |
| `studio/procs.py` | spawn com `setpriv --pdeathsig`, `run` com timeout, `ffprobe`, mapa de códigos de saída, erros de disco em PT | 1 |
| `studio/takes.py` | `Take` + `take.json` atômico, listagem, última tomada utilizável, recuperação | 1 |
| `tests/helpers.py`, `run_tests.sh` | tomada sintética flash+bip; roda a suíte com o python do venv | 1 |
| `studio/devices.py`, `studio/audio.py` | mics (`pactl`), câmeras by-id, espaço livre; volumedetect, aumentar volume, MP3 com aviso | 2 |
| `studio/watermark.py` | PNG RGBA da marca d'água (selo + faixa de 3 linhas na coluna 9:16) | 3 |
| `studio/timeline.py` | pacotes via ffprobe, âncora no 2º frame, ajuste do relógio do mic, `audio.wav` alinhado | 4 |
| `studio/render.py` | filtro e argv do MP4 (NVENC/x264), render com verificação *fail-closed* | 5 |
| `studio/capture.py`, `tests/fakebin/fake_ffmpeg.py` | comandos do gravador/preview, `FrameReader`, `CaptureProcess`, watchdogs, checagem do mic | 6 |
| `studio/rvc_worker.py`, `studio/rvc_client.py` | worker RVC em subprocesso (conversão em pedaços) e cliente do lado da GUI | 7 |
| `studio/drive.py`, `enviar_drive.py`, `tests/fakebin/rclone`, `README.md` (Drive) | envio via rclone (só stdlib), CLI, rclone falso, guia do Google Cloud | 8 |
| `studio/events.py`, `studio/gui.py`, `orochi_studio.py` | fila de eventos, `JobRunner`s, casca do App, seção "1. Modelo", entrada fina | 9 |
| `studio/gui_capture.py` | painel da esquerda: câmera, preview com marca d'água, mic, Gravar/Parar | 10 |
| `studio/gui_pipeline.py` | painel da direita: volume, conversão, vídeo, Drive | 11 |
| `tests/test_hardware.py`, `calibrar_av.py`, `README.md` (uso) | ponta a ponta real, calibração por palmas, guia de uso e solução de problemas | 12 |

Cada task cria os seus `tests/test_<modulo>.py`. As Tasks 10 e 11 fazem trocas pequenas e exatas no `studio/gui.py` da Task 9.

## Como este plano foi feito (e como conferir a execução)

- Cada task foi **prototipada com TDD** num clone de rascunho. Os blocos de código deste plano são **idênticos** ao que passou nos testes. Um *replay* que aplica as 12 tasks em ordem sobre a `main` reproduz byte a byte o código testado.
- O código final testado está na branch **`prototipo-plano`** deste repositório. Ao terminar a Task 12, `git diff prototipo-plano -- studio tests *.py *.sh README.md` deve sair vazio.
- As contagens `Ran N tests` dos passos "suíte inteira" valem para a ordem 1 → 12 com um display X11 disponível. Sem display, os testes de GUI aparecem como pulados.
- Antes da Task 1, o plano e a spec já estão commitados na `main`. Trabalhe numa branch nova, por exemplo `git switch -c voice-studio-v2`.
- O último passo da Task 12 é **manual e precisa do usuário**: instalar o rclone, fazer o login do Google, enviar de verdade para a pasta e calibrar com palmas. O executor para ali e chama o usuário.

## Review Focus

1. **Tomada curtíssima** (duplo clique em Gravar, parar antes do 1º frame, duração "0,1"): o 2º clique no primeiro segundo é ignorado; uma gravação curta demais vira "Gravação curta demais" (captura) ou `RenderError` com a mesma ideia (render), e nunca vira um MP4 de 1 frame. Testes: Task 5 (render com < 2 frames), Task 6 (`verify_capture` com < 3 pacotes), Task 10 (`MIN_REC_S`).
2. **Falha ao iniciar a gravação e depois reabrir o app** (Meet segurando a câmera → rc 240): a abertura carrega a última tomada *utilizável*, e a tomada boa anterior não some. Testes: Task 1 (`latest_take(usable_only=True)`), Task 9 (`_startup`).
3. **`estado.json` mudado fora do app ou com tipo errado** (`calibrar_av.py --salvar` ou `enviar_drive.py --pasta` com o app aberto; edição à mão com erro de sintaxe; `"drive_pasta": 123`): o app não desfaz o valor gravado por fora, preserva o JSON quebrado como `estado.json.corrompido` e abre com o padrão e um aviso. Testes: Task 1 (`load_estado` tipos, `merge_estado`), Task 8 (`--pasta`), Task 9 (`update_estado`), Task 11 (offset lido no clique), Task 12 (`--salvar`).
4. **`pactl` lento ou falhando durante a gravação**: "não sei" não para a gravação (só loga um aviso). O app só para com o `pactl` respondendo outro microfone duas vezes seguidas. Testes: Task 2 (`PactlError`), Task 6 (`mic_status`/`check_mic`), Task 10 (contador).
5. **Disco cheio no meio do pipeline** (a checagem de 2 GB só acontece ao Gravar): mensagem "Disco cheio — libere espaço", nenhum `.part`/`.rvc_*` sobrando, `take.json` coerente e botões reabilitados. Testes: Task 1 (`os_error_message`), Task 5 (render), Task 7 (worker), Task 11 (GUI).

---

### Task 1: Base do pacote (config, procs, takes, helpers de teste)

**Files:**
- Create: `studio/__init__.py` (vazio)
- Create: `studio/config.py`
- Create: `studio/procs.py`
- Create: `studio/takes.py`
- Create: `tests/__init__.py` (vazio)
- Create: `tests/helpers.py`
- Create: `run_tests.sh` (executável)
- Test: `tests/test_config.py`, `tests/test_helpers.py`, `tests/test_procs.py`, `tests/test_takes.py`

**Interfaces:**
- Consumes: nenhuma (primeira tarefa). Usa só `ffmpeg`/`ffprobe` 6.1.1 e `setpriv` (util-linux) do sistema.
- Produces:
  - `studio/config.py` (só stdlib): `BASE_DIR`, `APPLIO_DIR`, `LOGS_DIR`, `VENV_PYTHON`, `REC_DIR`, `VIDEOS_DIR`,
    `ESTADO_PATH`, `STUDIO_LOG`, `RVC_LOG`, `FONT_BOLD`, `FONT_REG`, `RCLONE_REMOTE = "iavoz"`,
    `AVISO_LINHA1`, `AVISO_LINHA3`, `MAX_TAKE_S = 300`, `MIN_FREE_BYTES = 2 * 1024**3`;
    `@dataclass(frozen=True) class Modelo(key, label, nome, aviso)`; `MODELOS` (orochi, silvio);
    `get_modelo(key: str) -> Modelo` (ValueError se desconhecido);
    `metadata_tags(m: Modelo) -> dict[str, str]` (chaves `title`, `comment`, `description`; ValueError se o modelo
    não tiver aviso/nome);
    `find_latest_checkpoint(model_key: str, logs_dir: str = LOGS_DIR) -> str | None`;
    `model_index_path(model_key: str, logs_dir: str = LOGS_DIR) -> str`; `DEFAULT_ESTADO`;
    `atomic_write_json(path: str, obj) -> None`; `load_estado(path: str = ESTADO_PATH) -> tuple[dict, str | None]`
    (arquivo ausente → `(padrões, None)`; corrompido → `(padrões, MSG_ESTADO_CORROMPIDO)`; valor de tipo diferente
    do de `DEFAULT_ESTADO`, ex. `"drive_pasta": 123` → o padrão daquela chave e o aviso
    `"estado.json: valor inválido em <chaves> — usando o padrão"`; chaves desconhecidas ficam; Step 28);
    `save_estado(estado: dict, path: str = ESTADO_PATH) -> None` (grava o dicionário inteiro);
    `merge_estado(changes: dict, path: str = ESTADO_PATH) -> tuple[dict, str | None]` (Step 28: relê o arquivo,
    aplica só `changes` e grava; devolve `(estado gravado, aviso | None)`; com o arquivo corrompido, antes faz
    `os.replace(path, path + CORRUPT_SUFFIX)` e o aviso é
    `"estado.json corrompido — cópia guardada em estado.json.corrompido"`; `OSError` do disco sobe para quem chamou);
    `MSG_ESTADO_CORROMPIDO = "estado.json corrompido — usando padrões"`; `CORRUPT_SUFFIX = ".corrompido"`.
    **Quem grava o estado com o app aberto usa `merge_estado`** (a GUI da Task 9 e o `enviar_drive.py --pasta` da
    Task 8), para não desfazer o que outro programa gravou (ex.: `calibrar_av.py --salvar`).
  - `studio/procs.py` (só stdlib): `SETPRIV`; `guarded(argv) -> list[str]`;
    `spawn(argv, **popen_kwargs) -> subprocess.Popen` (stdin=DEVNULL por padrão);
    `run(argv, timeout: float | None = None, **kw) -> subprocess.CompletedProcess` (capture_output + text por
    padrão, grupo de processo próprio; no timeout mata o grupo e relança `subprocess.TimeoutExpired`);
    `class ProcError(Exception)` com `.message`, `.rc`, `.detail`;
    `ffprobe_json(path: str, *args: str, timeout: float = 60.0) -> dict` (ProcError se falhar);
    `media_info(path: str) -> dict` (`{"format": {...}, "video": stream|None, "audio": stream|None}`);
    `tail(path: str, n: int = 15) -> str`; `FFMPEG_EXIT_MSGS`; `ffmpeg_exit_message(rc: int, log_tail: str = "") -> str`;
    `MSG_DISK_FULL = "Disco cheio — libere espaço"`; `os_error_message(e: OSError) -> str` (Step 38: ENOSPC/EDQUOT →
    `MSG_DISK_FULL`; outros → `"Erro ao acessar o disco (<nome>): <strerror>"`; as Tasks 5 e 7 e a GUI usam).
  - `studio/takes.py`: `STATUS`, `MODOS`; `@dataclass class Take` (campos `id, dir, modo, status, mic, camera,
    video, audio_fit, saidas, criado, erro`; `path(name)`, `raw_path`, `audio_path`, `save()`,
    `Take.load(take_dir)`);
    `new_take(modo: str, mic: str, camera: str = "", rec_dir: str = REC_DIR, now: datetime | None = None) -> Take`;
    `list_takes(rec_dir: str = REC_DIR) -> list[Take]`;
    `latest_take(rec_dir: str = REC_DIR, usable_only: bool = False) -> Take | None` (Step 33: com `usable_only=True`,
    só a última tomada utilizável; a abertura do app, Task 9, usa assim);
    `USABLE_STATUS = ("gravado", "convertido", "renderizado")`; `is_usable(take: Take) -> bool` (status em
    `USABLE_STATUS` e `raw_path` existe);
    `recover_takes(rec_dir: str = REC_DIR) -> list[str]`; `final_video_name(take_id: str, model_key: str) -> str`.
  - `tests/helpers.py` (Tasks 4, 5 e 6 usam): `PY`, `FPS = 30`, `SR = 48000`;
    `make_synthetic_take(path, audio_offset_s=0.0, duration_s=10.0, flash_frame=90, shift_s=0.0, vfr=False,
    size="1280x720") -> str`; `beep_onset_s(wav_or_media) -> float` (tempo local ao áudio decodificado);
    `flash_frame_index(media) -> int` (índice do frame decodificado, sem duplicar); `ffprobe_streams(path) -> dict`
    (JSON completo do ffprobe com `streams` e `format`).
  - `run_tests.sh`: `./run_tests.sh [-v] [args do unittest discover]`.

Todos os comandos abaixo rodam da raiz do repositório (`~/orochi-ia-homenagem`).

- [ ] **Step 1: Criar o esqueleto do pacote e o teste de config que falha**

```bash
mkdir -p studio tests
: > studio/__init__.py
: > tests/__init__.py
cat > run_tests.sh <<'EOF'
#!/usr/bin/env bash
# roda a suite de testes com o python do venv do Applio
cd "$(dirname "$0")" || exit 1
exec Applio/.venv/bin/python -m unittest discover -s tests -t . "$@"
EOF
chmod +x run_tests.sh
```

`tests/test_config.py`:

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_config -v`
Expected: ERROR com `ImportError: cannot import name 'config' from 'studio' (.../studio/__init__.py)` e
`FAILED (errors=1)`

- [ ] **Step 3: Implementar `studio/config.py`**

`studio/config.py`:

```python
"""Caminhos, modelos e estado.json do Voice Studio (so stdlib)."""

import glob
import json
import os
import re
import tempfile
from dataclasses import dataclass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPLIO_DIR = os.path.join(BASE_DIR, "Applio")
LOGS_DIR = os.path.join(APPLIO_DIR, "logs")
VENV_PYTHON = os.path.join(APPLIO_DIR, ".venv", "bin", "python")
REC_DIR = os.path.join(BASE_DIR, "recordings")
VIDEOS_DIR = os.path.join(BASE_DIR, "videos_finais")
ESTADO_PATH = os.path.join(BASE_DIR, "estado.json")
STUDIO_LOG = os.path.join(BASE_DIR, "studio.log")
RVC_LOG = os.path.join(BASE_DIR, "studio_rvc.log")
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
RCLONE_REMOTE = "iavoz"
AVISO_LINHA1 = "VOZ GERADA POR IA"
AVISO_LINHA3 = "paródia · homenagem"
MAX_TAKE_S = 300
MIN_FREE_BYTES = 2 * 1024**3


@dataclass(frozen=True)
class Modelo:
    key: str
    label: str
    nome: str
    aviso: str


MODELOS = (
    Modelo("orochi", "Orochi", "Orochi", "Não é a voz real do Orochi"),
    Modelo("silvio", "Silvio Santos", "Silvio Santos", "Não é a voz real de Silvio Santos"),
)


def get_modelo(key: str) -> Modelo:
    for m in MODELOS:
        if m.key == key:
            return m
    raise ValueError(f"Modelo desconhecido: {key}")


def metadata_tags(m: Modelo) -> dict[str, str]:
    # fail-closed: sem aviso nao existe metadado de video
    if not m.aviso.strip() or not m.nome.strip():
        raise ValueError(f"Modelo {m.key} sem texto de aviso")
    return {
        "title": "Paródia/homenagem - voz gerada por IA",
        "comment": f"Voz sintética gerada por IA (conversão RVC). {m.aviso}.",
        "description": "AI-generated/synthetic voice (RVC voice conversion). Parody/tribute. "
                       f"Not the real voice of {m.nome}.",
    }


def find_latest_checkpoint(model_key: str, logs_dir: str = LOGS_DIR) -> str | None:
    pattern = os.path.join(logs_dir, model_key, f"{model_key}_*e_*s.pth")
    files = glob.glob(pattern)

    def epoch(path):
        m = re.search(rf"{re.escape(model_key)}_(\d+)e_", os.path.basename(path))
        return int(m.group(1)) if m else -1

    files.sort(key=lambda p: (epoch(p), p))
    return files[-1] if files else None


def model_index_path(model_key: str, logs_dir: str = LOGS_DIR) -> str:
    return os.path.join(logs_dir, model_key, f"{model_key}.index")


DEFAULT_ESTADO = {"mic": "", "camera": "", "gravar_video": True, "drive_pasta": "",
                  "av_offset_ms": 0, "modelo": "orochi"}


def atomic_write_json(path: str, obj) -> None:
    # serializa antes de abrir o tmp: erro de tipo nao deixa lixo
    text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{os.path.basename(path)}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_estado(path: str = ESTADO_PATH) -> tuple[dict, str | None]:
    estado = dict(DEFAULT_ESTADO)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return estado, None
    except (OSError, ValueError):
        return estado, "estado.json corrompido — usando padrões"
    if not isinstance(data, dict):
        return estado, "estado.json corrompido — usando padrões"
    estado.update(data)
    return estado, None


def save_estado(estado: dict, path: str = ESTADO_PATH) -> None:
    atomic_write_json(path, estado)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_config -v`
Expected: `Ran 20 tests` e `OK`

- [ ] **Step 5: Commit**

```bash
git add studio/__init__.py studio/config.py tests/__init__.py tests/test_config.py run_tests.sh
git commit -m "feat(studio): config com caminhos, modelos e estado.json atomico

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: Escrever o teste dos helpers de mídia sintética**

Os helpers geram a "tomada do gravador" sintética: MJPEG 30 fps com um frame branco em `flash_frame` e PCM mono
48 kHz com um bip de 1 kHz / 50 ms no mesmo instante absoluto. É o porte de
`docs/superpowers/probes/2026-09-26/render/mktake.sh`, `onset.py` e `measure.py`.

`tests/test_helpers.py`:

```python
import os
import statistics
import subprocess
import tempfile
import unittest

from tests import helpers


def stream(info, kind):
    return next(s for s in info["streams"] if s["codec_type"] == kind)


def video_pts(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "packet=pts_time", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True).stdout.split()
    return [float(x.strip(",")) for x in out if x.strip(",")]


class SyntheticTakeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        cls.late = helpers.make_synthetic_take(os.path.join(d, "late.mkv"), audio_offset_s=0.40)
        cls.early = helpers.make_synthetic_take(os.path.join(d, "early.mkv"), audio_offset_s=-0.25,
                                                size="640x360")
        cls.shifted = helpers.make_synthetic_take(os.path.join(d, "shift.mkv"), audio_offset_s=0.40,
                                                  shift_s=5.0, size="640x360")
        cls.vfr = helpers.make_synthetic_take(os.path.join(d, "vfr.mkv"), audio_offset_s=0.40,
                                              shift_s=2.0, vfr=True, size="640x360")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def assert_same_instant(self, path, flash_t):
        info = helpers.ffprobe_streams(path)
        a_start = float(stream(info, "audio")["start_time"])
        beep_abs = a_start + helpers.beep_onset_s(path)
        self.assertAlmostEqual(beep_abs, flash_t, delta=0.001)

    def test_format_matches_recorder(self):
        info = helpers.ffprobe_streams(self.late)
        v, a = stream(info, "video"), stream(info, "audio")
        self.assertEqual(info["format"]["format_name"], "matroska,webm")
        self.assertEqual((v["codec_name"], v["width"], v["height"]), ("mjpeg", 1280, 720))
        self.assertEqual(v["pix_fmt"], "yuvj422p")
        self.assertEqual(v["r_frame_rate"], "30/1")
        self.assertEqual((a["codec_name"], a["sample_rate"], a["channels"]), ("pcm_s16le", "48000", 1))
        self.assertEqual(len(video_pts(self.late)), 300)

    def test_size(self):
        v = stream(helpers.ffprobe_streams(self.early), "video")
        self.assertEqual((v["width"], v["height"]), (640, 360))

    def test_start_times_show_offset(self):
        late = helpers.ffprobe_streams(self.late)
        self.assertAlmostEqual(float(stream(late, "video")["start_time"]), 0.0, places=3)
        self.assertAlmostEqual(float(stream(late, "audio")["start_time"]), 0.40, places=3)
        early = helpers.ffprobe_streams(self.early)
        self.assertAlmostEqual(float(stream(early, "video")["start_time"]), 0.25, places=3)
        self.assertAlmostEqual(float(stream(early, "audio")["start_time"]), 0.0, places=3)
        shifted = helpers.ffprobe_streams(self.shifted)
        self.assertAlmostEqual(float(stream(shifted, "video")["start_time"]), 5.0, places=3)
        self.assertAlmostEqual(float(stream(shifted, "audio")["start_time"]), 5.40, places=3)

    def test_flash_frame_index(self):
        self.assertEqual(helpers.flash_frame_index(self.late), 90)
        self.assertEqual(helpers.flash_frame_index(self.early), 90)

    def test_beep_onset_is_audio_local(self):
        self.assertAlmostEqual(helpers.beep_onset_s(self.late), 3.0 - 0.40, delta=0.001)
        self.assertAlmostEqual(helpers.beep_onset_s(self.early), 3.0 + 0.25, delta=0.001)

    def test_beep_and_flash_same_absolute_instant(self):
        self.assert_same_instant(self.late, 3.0)
        self.assert_same_instant(self.early, 0.25 + 3.0)
        self.assert_same_instant(self.shifted, 5.0 + 3.0)

    def test_vfr_is_about_15fps(self):
        pts = video_pts(self.vfr)
        span = pts[-1] - pts[0]
        self.assertTrue(14.0 <= len(pts) / span <= 17.0, f"{len(pts)} pacotes em {span:.3f} s")
        gaps = [b - a for a, b in zip(pts, pts[1:])]
        self.assertAlmostEqual(statistics.median(gaps), 1 / 15, delta=0.002)

    def test_vfr_flash_keeps_absolute_instant(self):
        pts = video_pts(self.vfr)
        flash_t = pts[helpers.flash_frame_index(self.vfr)]
        self.assertAlmostEqual(flash_t, 2.0 + 3.0, delta=0.001)
        self.assert_same_instant(self.vfr, flash_t)

    def test_beep_onset_on_wav(self):
        wav = os.path.join(self.tmp.name, "late.wav")
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", self.late, "-map", "0:a",
                        "-c:a", "pcm_s16le", wav], check=True)
        self.assertAlmostEqual(helpers.beep_onset_s(wav), 2.60, delta=0.001)

    def test_rejects_beep_outside_audio(self):
        with self.assertRaises(ValueError):
            helpers.make_synthetic_take(os.path.join(self.tmp.name, "x.mkv"), audio_offset_s=4.0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "x.mkv")))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 7: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_helpers -v`
Expected: ERROR com `ImportError: cannot import name 'helpers' from 'tests' (.../tests/__init__.py)` e
`FAILED (errors=1)`

- [ ] **Step 8: Implementar `tests/helpers.py`**

`tests/helpers.py`:

```python
"""Midia sintetica para os testes (porte de probes/2026-09-26/render/mktake.sh, onset.py e measure.py).

So stdlib + ffmpeg/ffprobe no PATH.
"""

import array
import json
import os
import subprocess
import sys
import tempfile

PY = sys.executable
FPS = 30
SR = 48000
BEEP_S = 0.05
BEEP_THRESHOLD = 0.3


def _run(argv: list[str], timeout: float = 120) -> bytes:
    r = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace")[-2000:]
        raise RuntimeError(f"{argv[0]} falhou (rc={r.returncode}): {err}")
    return r.stdout


def _ffmpeg(*args: str) -> bytes:
    return _run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args])


def make_synthetic_take(path: str, audio_offset_s: float = 0.0, duration_s: float = 10.0, flash_frame: int = 90,
                        shift_s: float = 0.0, vfr: bool = False, size: str = "1280x720") -> str:
    # flash branco no frame flash_frame (t = flash_frame/30 do video) e bip de 1 kHz no mesmo instante absoluto
    flash_t = flash_frame / FPS
    beep_t = flash_t - audio_offset_s          # instante do bip no tempo local do audio
    audio_dur = duration_s - audio_offset_s    # os dois streams terminam juntos
    if not 0 <= flash_frame < round(duration_s * FPS):
        raise ValueError("flash_frame fora do video")
    if beep_t < 0 or beep_t + BEEP_S > audio_dur:
        raise ValueError("bip fora do audio: ajuste audio_offset_s")
    vf = f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='eq(n,{flash_frame})'"
    if vfr:
        # maior parte a 15 fps (frames pares) + rajada de 30 fps em volta do flash
        vf += f",select='not(mod(n,2))+between(n,{flash_frame - 9},{flash_frame + 9})'"
    expr = (f"0.01*(2*random(0)-1)+0.8*sin(2*PI*1000*t)"
            f"*between(t,{beep_t:.6f},{beep_t + BEEP_S:.6f})")
    folder = os.path.dirname(os.path.abspath(path))
    with tempfile.TemporaryDirectory(dir=folder) as tmp:
        v = os.path.join(tmp, "v.mkv")
        a = os.path.join(tmp, "a.wav")
        _ffmpeg("-f", "lavfi", "-i", f"testsrc2=s={size}:r={FPS}:d={duration_s}", "-vf", vf,
                "-fps_mode", "passthrough", "-c:v", "mjpeg", "-q:v", "3", "-pix_fmt", "yuvj422p", v)
        _ffmpeg("-f", "lavfi", "-i", f"aevalsrc='{expr}':s={SR}:c=mono:d={audio_dur:.6f}",
                "-c:a", "pcm_s16le", a)
        v_off = max(0.0, -audio_offset_s)
        a_off = max(0.0, audio_offset_s)
        _ffmpeg("-itsoffset", f"{v_off:.6f}", "-i", v, "-itsoffset", f"{a_off:.6f}", "-i", a,
                "-map", "0:v", "-map", "1:a", "-c", "copy", "-output_ts_offset", f"{shift_s:.6f}",
                "-f", "matroska", path)
    return path


def beep_onset_s(wav_or_media: str) -> float:
    # tempo do 1o sample com |x| > 0.3, contado do inicio do audio decodificado (mono 48k)
    raw = _run(["ffmpeg", "-nostdin", "-v", "error", "-i", wav_or_media, "-map", "0:a:0", "-ac", "1",
                "-ar", str(SR), "-f", "s16le", "-"])
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) // 2 * 2])
    if sys.byteorder != "little":
        samples.byteswap()
    limit = BEEP_THRESHOLD * 32768
    for i, s in enumerate(samples):
        if s > limit or s < -limit:
            return i / SR
    raise ValueError(f"bip não encontrado em {wav_or_media}")


def flash_frame_index(media: str) -> int:
    # indice (decodificado, sem duplicar frames) do frame mais claro; recorte do topo evita a faixa e o selo
    side = 16
    raw = _run(["ffmpeg", "-nostdin", "-v", "error", "-i", media, "-map", "0:v:0",
                "-vf", f"crop=iw/2:ih/2:iw/4:ih/8,scale={side}:{side},format=gray",
                "-fps_mode", "passthrough", "-f", "rawvideo", "-"])
    n = side * side
    sums = [sum(raw[i:i + n]) for i in range(0, len(raw) - n + 1, n)]
    if not sums:
        raise ValueError(f"nenhum frame em {media}")
    return max(range(len(sums)), key=sums.__getitem__)


def ffprobe_streams(path: str) -> dict:
    out = _run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path])
    return json.loads(out)
```

- [ ] **Step 9: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_helpers -v`
Expected: `Ran 10 tests` (~3 s) e `OK`

Conferência opcional dos números, iguais aos do relatório `probe_render-watermark.md`:
atrasado 0,40 s → `v0 0.000000 a0 0.400000 pkts 300 flash_idx 90 beep_local 2.60008 beep_abs 3.00008`;
adiantado 0,25 s → `v0 0.250000 a0 0.000000 flash_t 3.25 beep_abs 3.25008`;
VFR (+0,40 s, deslocado 2 s) → `pkts 160 flash_idx 50 flash_t 5.0 beep_abs 5.00008`. Diferença bip − flash de
+0,083 ms em todos os casos.

- [ ] **Step 10: Commit**

```bash
git add tests/helpers.py tests/test_helpers.py
git commit -m "test(helpers): tomada sintetica flash+bip (porte de mktake.sh/onset.py/measure.py)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 11: Escrever o teste de `procs` que falha**

`tests/test_procs.py`:

```python
import os
import subprocess
import sys
import tempfile
import time
import unittest

from studio import procs
from studio.config import BASE_DIR
from tests import helpers


def gone(pid: int, timeout: float = 3.0) -> bool:
    # processo sumiu ou virou zumbi
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(f"/proc/{pid}/stat") as f:
                state = f.read().rsplit(")", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):   # ESRCH: morreu entre o open e o read
            return True
        if state in ("Z", "X"):
            return True
        time.sleep(0.05)
    return False


def exec_done(pid: int, name: str, timeout: float = 5.0) -> bool:
    # o setpriv arma o pdeathsig antes do exec: comm == name prova que o prctl ja rodou
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(f"/proc/{pid}/comm") as f:
                if f.read().strip() == name:
                    return True
        except (FileNotFoundError, ProcessLookupError):
            return False
        time.sleep(0.01)
    return False


class SpawnRunTest(unittest.TestCase):
    def test_guarded(self):
        self.assertEqual(procs.SETPRIV, ["setpriv", "--pdeathsig", "TERM", "--"])
        self.assertEqual(procs.guarded(["ffmpeg", "-i", "x"]),
                         ["setpriv", "--pdeathsig", "TERM", "--", "ffmpeg", "-i", "x"])

    def test_spawn_prefixes_and_closes_stdin(self):
        p = procs.spawn(["cat"], stdout=subprocess.PIPE)
        out, _ = p.communicate(timeout=5)
        self.assertEqual(p.args[:4], procs.SETPRIV)
        self.assertEqual((p.returncode, out), (0, b""))

    def test_run_captures_text(self):
        r = procs.run(["sh", "-c", "echo olá; echo erro >&2; exit 3"], timeout=5)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout, "olá\n")
        self.assertEqual(r.stderr, "erro\n")
        self.assertEqual(r.args[:4], procs.SETPRIV)

    def test_run_stdin_is_devnull(self):
        r = procs.run(["cat"], timeout=5)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_run_timeout_kills_process_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = os.path.join(tmp, "pid")
            t0 = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                procs.run(["sh", "-c", f"sleep 30 & echo $! > {pidfile}; wait"], timeout=0.5)
            self.assertLess(time.monotonic() - t0, 5)
            with open(pidfile) as f:
                grandchild = int(f.read())
        self.assertTrue(gone(grandchild), "o neto (sleep) sobreviveu ao timeout")

    def test_child_dies_with_parent(self):
        code = ("import sys, time; sys.path.insert(0, sys.argv[1]); from studio import procs; "
                "p = procs.spawn(['sleep', '30']); print(p.pid, flush=True); time.sleep(30)")
        parent = subprocess.Popen([sys.executable, "-c", code, BASE_DIR], stdout=subprocess.PIPE, text=True)
        try:
            child = int(parent.stdout.readline())
            # matar o pai antes do exec do sleep deixaria o filho orfao sem pdeathsig (corrida do teste)
            self.assertTrue(exec_done(child, "sleep"), "o setpriv não chegou a executar o sleep")
        finally:
            parent.kill()
            parent.wait()
            parent.stdout.close()
        self.assertTrue(gone(child), "o filho sobreviveu a morte do pai (pdeathsig)")


class ProcErrorTest(unittest.TestCase):
    def test_fields(self):
        e = procs.ProcError("Falhou", rc=2, detail="linha")
        self.assertEqual((e.message, e.rc, e.detail, str(e)), ("Falhou", 2, "linha", "Falhou"))
        e = procs.ProcError("Só mensagem")
        self.assertEqual((e.rc, e.detail), (None, ""))


class FfprobeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        cls.mkv = helpers.make_synthetic_take(os.path.join(d, "take.mkv"), duration_s=2.0, flash_frame=30,
                                              size="320x240")
        cls.wav = os.path.join(d, "a.wav")
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", cls.mkv, "-map", "0:a", "-c:a",
                        "pcm_s16le", cls.wav], check=True)
        cls.junk = os.path.join(d, "junk.mkv")
        with open(cls.junk, "wb") as f:
            f.write(b"isto nao e um video" * 100)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_ffprobe_json(self):
        data = procs.ffprobe_json(self.mkv, "-show_entries", "stream=codec_type")
        self.assertEqual(sorted(s["codec_type"] for s in data["streams"]), ["audio", "video"])

    def test_ffprobe_json_error(self):
        with self.assertRaises(procs.ProcError) as cm:
            procs.ffprobe_json(self.junk, "-show_format")
        self.assertIn("junk.mkv", cm.exception.message)
        self.assertEqual(cm.exception.rc, 1)
        self.assertIn("Invalid data", cm.exception.detail)

    def test_media_info_av(self):
        info = procs.media_info(self.mkv)
        self.assertEqual(info["video"]["codec_name"], "mjpeg")
        self.assertEqual(info["audio"]["codec_name"], "pcm_s16le")
        self.assertGreater(float(info["format"]["duration"]), 1.9)

    def test_media_info_audio_only(self):
        info = procs.media_info(self.wav)
        self.assertIsNone(info["video"])
        self.assertEqual(info["audio"]["sample_rate"], "48000")


class TailAndMessagesTest(unittest.TestCase):
    def test_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "log")
            with open(path, "wb") as f:
                f.write(b"x" * 200000 + b"\n")
                f.write("".join(f"linha {i}\n" for i in range(30)).encode())
                f.write(b"quebrado \xff\n")
            text = procs.tail(path, 3)
            self.assertEqual(text.splitlines(), ["linha 28", "linha 29", "quebrado �"])
            self.assertEqual(len(procs.tail(path).splitlines()), 15)
            self.assertEqual(procs.tail(os.path.join(tmp, "nao_existe")), "")

    def test_exit_codes(self):
        self.assertEqual(procs.ffmpeg_exit_message(240), "Câmera em uso por outro programa (Meet/Zoom/OBS?)")
        self.assertEqual(procs.ffmpeg_exit_message(254), "Câmera não encontrada")
        self.assertEqual(procs.ffmpeg_exit_message(231), "Dispositivo de vídeo errado")

    def test_exit_message_from_log(self):
        log = "[video4linux2] ioctl(VIDIOC_G_INPUT): Device or resource busy\n"
        self.assertEqual(procs.ffmpeg_exit_message(1, log), procs.FFMPEG_EXIT_MSGS[240])
        msg = procs.ffmpeg_exit_message(1, "algo\nError opening output file x.mkv\n\n")
        self.assertEqual(msg, "ffmpeg falhou (código 1): Error opening output file x.mkv")
        self.assertEqual(procs.ffmpeg_exit_message(-9), "ffmpeg foi interrompido (sinal 9)")
        self.assertEqual(procs.ffmpeg_exit_message(1), "ffmpeg falhou (código 1)")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 12: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_procs -v`
Expected: ERROR com `ImportError: cannot import name 'procs' from 'studio' (.../studio/__init__.py)` e
`FAILED (errors=1)`

- [ ] **Step 13: Implementar `studio/procs.py`**

`studio/procs.py`:

```python
"""Processos filhos: sempre com setpriv --pdeathsig, ffprobe e mensagens de erro (so stdlib)."""

import json
import os
import signal
import subprocess

SETPRIV = ["setpriv", "--pdeathsig", "TERM", "--"]
FFPROBE_TIMEOUT_S = 60.0


def guarded(argv: list[str]) -> list[str]:
    return [*SETPRIV, *argv]


def spawn(argv: list[str], **popen_kwargs) -> subprocess.Popen:
    popen_kwargs.setdefault("stdin", subprocess.DEVNULL)
    return subprocess.Popen(guarded(argv), **popen_kwargs)


def _kill(p: subprocess.Popen, own_group: bool) -> None:
    try:
        if own_group:
            os.killpg(p.pid, signal.SIGKILL)
        else:
            p.kill()
    except (ProcessLookupError, PermissionError):
        pass


def run(argv: list[str], timeout: float | None = None, **kw) -> subprocess.CompletedProcess:
    kw.setdefault("stdin", subprocess.DEVNULL)
    if kw.pop("capture_output", True):
        kw.setdefault("stdout", subprocess.PIPE)
        kw.setdefault("stderr", subprocess.PIPE)
    kw.setdefault("text", True)
    kw.setdefault("start_new_session", True)   # grupo proprio: o timeout mata netos tambem
    own_group = kw["start_new_session"]
    with spawn(argv, **kw) as p:
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(p, own_group)
            try:
                out, err = p.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                out, err = None, None
            raise subprocess.TimeoutExpired(p.args, timeout, output=out, stderr=err) from None
        except BaseException:
            _kill(p, own_group)
            raise
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)


class ProcError(Exception):
    def __init__(self, message: str, rc: int | None = None, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.rc = rc
        self.detail = detail


def ffprobe_json(path: str, *args: str, timeout: float = FFPROBE_TIMEOUT_S) -> dict:
    name = os.path.basename(path)
    try:
        r = run(["ffprobe", "-v", "error", "-of", "json", *args, path], timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ProcError(f"ffprobe demorou demais para ler {name}") from None
    if r.returncode != 0:
        raise ProcError(f"Não foi possível ler {name}", r.returncode, r.stderr.strip()[-2000:])
    try:
        data = json.loads(r.stdout)
    except ValueError:
        raise ProcError(f"Resposta inválida do ffprobe para {name}", r.returncode, r.stdout[:500]) from None
    if not isinstance(data, dict):
        raise ProcError(f"Resposta inválida do ffprobe para {name}", r.returncode, r.stdout[:500])
    return data


def media_info(path: str) -> dict:
    data = ffprobe_json(path, "-show_format", "-show_streams")
    streams = data.get("streams", [])

    def first(kind):
        return next((s for s in streams if s.get("codec_type") == kind), None)

    return {"format": data.get("format", {}), "video": first("video"), "audio": first("audio")}


def tail(path: str, n: int = 15) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 64 * 1024))
            data = f.read()
    except OSError:
        return ""
    lines = data.decode("utf-8", "replace").splitlines()
    return "\n".join(lines[-n:])


FFMPEG_EXIT_MSGS = {240: "Câmera em uso por outro programa (Meet/Zoom/OBS?)",
                    254: "Câmera não encontrada", 231: "Dispositivo de vídeo errado"}
# mesmo erro com outro codigo de saida: reconhece pelo texto do log
_LOG_HINTS = (("Device or resource busy", 240), ("Inappropriate ioctl for device", 231))


def ffmpeg_exit_message(rc: int, log_tail: str = "") -> str:
    if rc in FFMPEG_EXIT_MSGS:
        return FFMPEG_EXIT_MSGS[rc]
    for hint, code in _LOG_HINTS:
        if hint in log_tail:
            return FFMPEG_EXIT_MSGS[code]
    if rc < 0:
        return f"ffmpeg foi interrompido (sinal {-rc})"
    last = next((ln.strip() for ln in reversed(log_tail.splitlines()) if ln.strip()), "")
    msg = f"ffmpeg falhou (código {rc})"
    return f"{msg}: {last}" if last else msg
```

- [ ] **Step 14: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_procs -v`
Expected: `Ran 14 tests` (~1 s) e `OK`. Depois, `pgrep -af "sleep 30"` não deve listar nenhum `sleep` (só a
própria linha do pgrep).

- [ ] **Step 15: Commit**

```bash
git add studio/procs.py tests/test_procs.py
git commit -m "feat(studio): procs com setpriv --pdeathsig, run com timeout que mata o grupo, ffprobe

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 16: Escrever o teste de `takes` que falha**

`tests/test_takes.py`:

```python
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
```

- [ ] **Step 17: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_takes -v`
Expected: ERROR com `ImportError: cannot import name 'takes' from 'studio' (.../studio/__init__.py)` e
`FAILED (errors=1)`

- [ ] **Step 18: Implementar `studio/takes.py`**

`studio/takes.py`:

```python
"""Tomadas: pasta recordings/<id>/ com take.json atomico e recuperacao na abertura."""

import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime

from studio.config import REC_DIR, atomic_write_json
from studio.procs import ProcError, media_info, run

STATUS = ("gravando", "gravado", "convertido", "renderizado", "falhou")
MODOS = ("av", "audio")
TAKE_JSON = "take.json"
REMUX_TIMEOUT_S = 300


@dataclass
class Take:
    id: str
    dir: str
    modo: str
    status: str
    mic: str
    camera: str = ""
    video: dict = field(default_factory=dict)
    audio_fit: dict = field(default_factory=dict)
    saidas: dict = field(default_factory=dict)
    criado: str = ""
    erro: str = ""

    def path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    @property
    def raw_path(self) -> str:
        return self.path("raw.mkv" if self.modo == "av" else "raw.wav")

    @property
    def audio_path(self) -> str:
        return self.path("audio.wav" if self.modo == "av" else "raw.wav")

    def save(self) -> None:
        data = asdict(self)
        data.pop("dir")
        atomic_write_json(self.path(TAKE_JSON), data)

    @classmethod
    def load(cls, take_dir: str) -> "Take":
        with open(os.path.join(take_dir, TAKE_JSON), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"take.json inválido em {take_dir}")
        known = {f.name for f in fields(cls)} - {"dir"}
        return cls(dir=os.path.abspath(take_dir), **{k: v for k, v in data.items() if k in known})


def new_take(modo: str, mic: str, camera: str = "", rec_dir: str = REC_DIR,
             now: datetime | None = None) -> Take:
    if modo not in MODOS:
        raise ValueError(f"Modo desconhecido: {modo}")
    now = now or datetime.now()
    base = now.strftime("%Y-%m-%d_%H%M%S")
    os.makedirs(rec_dir, exist_ok=True)
    n = 1
    while True:
        take_id = base if n == 1 else f"{base}_{n}"
        take_dir = os.path.abspath(os.path.join(rec_dir, take_id))
        try:
            os.mkdir(take_dir)   # atomico: duas tomadas no mesmo segundo nunca dividem a pasta
            break
        except FileExistsError:
            n += 1
    take = Take(id=take_id, dir=take_dir, modo=modo, status="gravando", mic=mic, camera=camera,
                criado=now.isoformat(timespec="seconds"))
    take.save()
    return take


def _sort_key(take_id: str) -> tuple[str, int]:
    # "_10" depois de "_9"
    m = re.fullmatch(r"(.+)_(\d{1,3})", take_id)
    return (m.group(1), int(m.group(2))) if m else (take_id, 1)


def list_takes(rec_dir: str = REC_DIR) -> list[Take]:
    try:
        names = os.listdir(rec_dir)
    except OSError:
        return []
    found = []
    for name in names:
        take_dir = os.path.join(rec_dir, name)
        if not os.path.isfile(os.path.join(take_dir, TAKE_JSON)):
            continue
        try:
            found.append(Take.load(take_dir))
        except (OSError, ValueError, TypeError):
            continue   # take.json ilegivel: nao derruba a abertura
    return sorted(found, key=lambda t: _sort_key(t.id))


def latest_take(rec_dir: str = REC_DIR) -> Take | None:
    found = list_takes(rec_dir)
    return found[-1] if found else None


def _readable(path: str, need_video: bool) -> bool:
    try:
        info = media_info(path)
    except ProcError:
        return False
    try:
        duration = float(info["format"].get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0 or info["audio"] is None:
        return False
    return info["video"] is not None or not need_video


def _remux(raw: str, need_video: bool) -> bool:
    stem, ext = os.path.splitext(raw)
    fixed = f"{stem}.recovered{ext}"
    try:
        r = run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                 "-i", raw, "-map", "0", "-c", "copy", fixed], timeout=REMUX_TIMEOUT_S)
        if r.returncode == 0 and _readable(fixed, need_video):
            os.replace(fixed, raw)
            return True
    except Exception:
        pass
    try:
        os.unlink(fixed)
    except OSError:
        pass
    return False


def _recover(take: Take) -> str:
    need_video = take.modo == "av"
    raw = take.raw_path
    if _readable(raw, need_video):
        take.status, take.erro = "gravado", ""
        take.save()
        return f"Tomada {take.id}: gravação interrompida recuperada"
    if os.path.exists(raw) and _remux(raw, need_video):
        take.status, take.erro = "gravado", ""
        take.save()
        return f"Tomada {take.id}: gravação interrompida recuperada (arquivo reconstruído)"
    take.status = "falhou"
    if os.path.exists(raw):
        take.erro = "Gravação interrompida e ilegível — não foi possível recuperar"
    else:
        take.erro = "Arquivo da gravação não encontrado"
    take.save()
    return f"Tomada {take.id}: {take.erro}"


def recover_takes(rec_dir: str = REC_DIR) -> list[str]:
    return [_recover(t) for t in list_takes(rec_dir) if t.status == "gravando"]


def final_video_name(take_id: str, model_key: str) -> str:
    return f"{take_id}_{model_key}_IA.mp4"
```

- [ ] **Step 19: Rodar e ver passar (módulo e suíte inteira)**

Run: `Applio/.venv/bin/python -m unittest tests.test_takes -v`
Expected: `Ran 19 tests` (~1,2 s) e `OK`

Run: `./run_tests.sh`
Expected: `Ran 63 tests` (~5,3 s) e `OK`. A suíte também passa com `python3 -m unittest discover -s tests -t .`
(tudo aqui é stdlib). Confira que nada sobrou: `pgrep -a ffmpeg` sem saída, e nenhum `estado.json` nem
`recordings/` novo foi criado pelos testes (`git status --short --ignored`).

- [ ] **Step 20: Commit**

```bash
git add studio/takes.py tests/test_takes.py
git commit -m "feat(studio): takes com take.json atomico e recuperacao de tomada interrompida

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

**Correções da revisão (Steps 21–40).** Os quatro ciclos abaixo corrigem problemas achados na revisão do plano.
Cada um acrescenta testes, vê os testes falharem e só então troca o código, com blocos "substituir isto → por
isto". Os passos anteriores ficam como estão.

- [ ] **Step 21: Teste do colchete no caminho dos checkpoints (falha)**

Se o projeto estiver numa pasta como `~/orochi [v2]/`, os checkpoints somem: o `glob` lê `[v2]` como uma classe
de caracteres. Em `tests/test_config.py`, substituir isto:

```python
if __name__ == "__main__":
    unittest.main()
```

por isto:

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 22: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_config -v`
Expected: FAIL em `test_glob_chars_in_path_and_key` com
`AssertionError: None != '/tmp/tmp…/orochi [v2]/logs/orochi/orochi_350e_18550s.pth'` e `FAILED (failures=1)`
(`Ran 21 tests`)

- [ ] **Step 23: Escapar o caminho e a chave no `glob`**

Em `studio/config.py`, substituir isto:

```python
    pattern = os.path.join(logs_dir, model_key, f"{model_key}_*e_*s.pth")
```

por isto:

```python
    # colchete no caminho (ex.: "orochi [v2]") nao pode virar padrao do glob
    pattern = os.path.join(glob.escape(logs_dir), glob.escape(model_key), f"{glob.escape(model_key)}_*e_*s.pth")
```

- [ ] **Step 24: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_config -v`
Expected: `Ran 21 tests` e `OK`

- [ ] **Step 25: Commit**

```bash
git add studio/config.py tests/test_config.py
git commit -m "fix(config): find_latest_checkpoint escapa o caminho e a chave no glob

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 26: Testes do `estado.json` com tipo errado e gravado por fora (falha)**

Três casos reais: `"drive_pasta": 123` derrubava a abertura do app; o `calibrar_av.py --salvar`, rodando com o
app aberto, era desfeito no próximo clique; e um `estado.json` editado à mão com uma vírgula sobrando era
sobrescrito com os padrões, perdendo o link do Drive. Em `tests/test_config.py`, substituir isto:

```python
if __name__ == "__main__":
    unittest.main()
```

por isto:

```python
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
```

- [ ] **Step 27: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_config -v`
Expected: `FAILED (failures=1, errors=5)` (`Ran 27 tests`): 5 ERROR com
`AttributeError: module 'studio.config' has no attribute 'merge_estado'. Did you mean: 'save_estado'?` e 1 FAIL
em `test_wrong_types_become_defaults` (`AssertionError: {'mic[26 chars]ra': None, 'gravar_video': 1, …`)

- [ ] **Step 28: Tipos pelo `DEFAULT_ESTADO` e `merge_estado`**

Em `studio/config.py`, substituir isto:

```python
def load_estado(path: str = ESTADO_PATH) -> tuple[dict, str | None]:
    estado = dict(DEFAULT_ESTADO)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return estado, None
    except (OSError, ValueError):
        return estado, "estado.json corrompido — usando padrões"
    if not isinstance(data, dict):
        return estado, "estado.json corrompido — usando padrões"
    estado.update(data)
    return estado, None


def save_estado(estado: dict, path: str = ESTADO_PATH) -> None:
    atomic_write_json(path, estado)
```

por isto:

```python
MSG_ESTADO_CORROMPIDO = "estado.json corrompido — usando padrões"
CORRUPT_SUFFIX = ".corrompido"


def _read_estado(path: str) -> tuple[dict, bool]:
    # (dados do arquivo, corrompido?); arquivo ausente = ({}, False)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}, False
    except (OSError, ValueError):
        return {}, True
    return (data, False) if isinstance(data, dict) else ({}, True)


def _with_defaults(data: dict) -> tuple[dict, str | None]:
    # valor de tipo errado (ex.: "drive_pasta": 123) vira o padrao, com aviso; chaves desconhecidas ficam
    estado = dict(DEFAULT_ESTADO)
    estado.update(data)
    bad = [k for k, v in DEFAULT_ESTADO.items() if type(estado[k]) is not type(v)]
    for k in bad:
        estado[k] = DEFAULT_ESTADO[k]
    if not bad:
        return estado, None
    return estado, f"estado.json: valor inválido em {', '.join(bad)} — usando o padrão"


def load_estado(path: str = ESTADO_PATH) -> tuple[dict, str | None]:
    data, corrupt = _read_estado(path)
    if corrupt:
        return dict(DEFAULT_ESTADO), MSG_ESTADO_CORROMPIDO
    return _with_defaults(data)


def save_estado(estado: dict, path: str = ESTADO_PATH) -> None:
    atomic_write_json(path, estado)


def merge_estado(changes: dict, path: str = ESTADO_PATH) -> tuple[dict, str | None]:
    # rele o arquivo e grava so as chaves de changes: o que outro programa gravou (calibrar_av.py,
    # enviar_drive.py) nao volta atras; arquivo corrompido e guardado em <path>.corrompido antes
    data, corrupt = _read_estado(path)
    aviso = None
    if corrupt:
        backup = path + CORRUPT_SUFFIX
        os.replace(path, backup)
        aviso = f"estado.json corrompido — cópia guardada em {os.path.basename(backup)}"
    estado, aviso_tipos = _with_defaults(data)
    estado.update(changes)
    save_estado(estado, path)
    return estado, aviso or aviso_tipos
```

- [ ] **Step 29: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_config -v`
Expected: `Ran 27 tests` e `OK`

- [ ] **Step 30: Commit**

```bash
git add studio/config.py tests/test_config.py
git commit -m "fix(config): estado.json com tipos corrigidos pelo padrao e merge_estado que preserva o corrompido

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 31: Teste da última tomada utilizável (falha)**

Uma tomada que falhou ao começar (câmera em uso, rc 240) virava a "última tomada" na abertura seguinte e escondia
a boa anterior, porque o app não tem seletor de tomadas. Em `tests/test_takes.py`, substituir isto:

```python
if __name__ == "__main__":
    unittest.main()
```

por isto:

```python
class LatestUsableTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.rec = os.path.join(tmp.name, "recordings")

    def make(self, hhmmss: str, status: str, raw: bool = True, modo: str = "audio") -> takes.Take:
        now = datetime.strptime(f"2026-09-26 {hhmmss}", "%Y-%m-%d %H%M%S")
        t = takes.new_take(modo, "m", "c" if modo == "av" else "", rec_dir=self.rec, now=now)
        t.status = status
        t.save()
        if raw:
            open(t.raw_path, "wb").close()
        return t

    def test_usable_only_skips_failed_and_unfinished(self):
        good = self.make("100000", "convertido")
        self.make("110000", "falhou", raw=False)        # camera em uso: a gravacao nem comecou
        self.make("120000", "falhou")                   # gravacao curta demais
        self.make("130000", "gravado", raw=False)       # raw apagado a mao
        self.make("140000", "gravando")                 # a recuperacao nao conseguiu mexer nela
        self.assertEqual(takes.latest_take(self.rec).id, "2026-09-26_140000")
        self.assertEqual(takes.latest_take(self.rec, usable_only=True), good)

    def test_usable_statuses(self):
        self.assertEqual(takes.USABLE_STATUS, ("gravado", "convertido", "renderizado"))
        for i, status in enumerate(takes.STATUS):
            t = self.make(f"10000{i}", status, modo="av")
            self.assertEqual(takes.is_usable(t), status in takes.USABLE_STATUS, status)

    def test_nothing_usable(self):
        self.make("110000", "falhou")
        self.assertIsNone(takes.latest_take(self.rec, usable_only=True))
        self.assertIsNone(takes.latest_take(os.path.join(self.rec, "nao_existe"), usable_only=True))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 32: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_takes -v`
Expected: `FAILED (errors=3)` (`Ran 22 tests`): 2× `TypeError: latest_take() got an unexpected keyword argument
'usable_only'` e 1× `AttributeError: module 'studio.takes' has no attribute 'USABLE_STATUS'`

- [ ] **Step 33: `latest_take(..., usable_only=True)`**

Em `studio/takes.py`, substituir isto:

```python
def latest_take(rec_dir: str = REC_DIR) -> Take | None:
    found = list_takes(rec_dir)
    return found[-1] if found else None
```

por isto:

```python
USABLE_STATUS = ("gravado", "convertido", "renderizado")


def is_usable(take: Take) -> bool:
    # da para converter/gerar/enviar: a gravacao terminou bem e o arquivo bruto esta no disco
    return take.status in USABLE_STATUS and os.path.isfile(take.raw_path)


def latest_take(rec_dir: str = REC_DIR, usable_only: bool = False) -> Take | None:
    # usable_only: a abertura do app pula tomada que falhou (ex.: camera em uso) e mostra a boa anterior
    found = [t for t in list_takes(rec_dir) if not usable_only or is_usable(t)]
    return found[-1] if found else None
```

- [ ] **Step 34: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_takes -v`
Expected: `Ran 22 tests` e `OK`

- [ ] **Step 35: Commit**

```bash
git add studio/takes.py tests/test_takes.py
git commit -m "fix(takes): latest_take com usable_only pula tomada que falhou ou sem arquivo bruto

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 36: Teste da mensagem de disco cheio (falha)**

O render (Task 5) e o worker do RVC (Task 7) precisam dizer "Disco cheio" em PT, e não `OSError: [Errno 28]`.
A mensagem fica aqui para os dois usarem a mesma. Em `tests/test_procs.py`, substituir isto:

```python
import os
import subprocess
import sys
import tempfile
```

por isto:

```python
import errno
import os
import subprocess
import sys
import tempfile
```

E em `tests/test_procs.py`, substituir isto:

```python
if __name__ == "__main__":
    unittest.main()
```

por isto:

```python
class OsErrorMessageTest(unittest.TestCase):
    def test_disk_full(self):
        self.assertEqual(procs.MSG_DISK_FULL, "Disco cheio — libere espaço")
        for code in (errno.ENOSPC, errno.EDQUOT):
            e = OSError(code, os.strerror(code), "/x/recordings/2026-09-26_101500/render.part.mp4")
            self.assertEqual(procs.os_error_message(e), "Disco cheio — libere espaço")

    def test_other_errors(self):
        e = PermissionError(errno.EACCES, "Permission denied", "/x/videos_finais/")
        self.assertEqual(procs.os_error_message(e), "Erro ao acessar o disco (videos_finais): Permission denied")
        self.assertEqual(procs.os_error_message(OSError("sem errno")), "Erro ao acessar o disco: sem errno")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 37: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_procs -v`
Expected: `FAILED (errors=2)` (`Ran 16 tests`) com `AttributeError: module 'studio.procs' has no attribute
'MSG_DISK_FULL'` e `... has no attribute 'os_error_message'`

- [ ] **Step 38: `MSG_DISK_FULL` e `os_error_message`**

Em `studio/procs.py`, substituir isto:

```python
import json
import os
import signal
import subprocess
```

por isto:

```python
import errno
import json
import os
import signal
import subprocess
```

E em `studio/procs.py`, substituir isto (o fim do arquivo):

```python
    last = next((ln.strip() for ln in reversed(log_tail.splitlines()) if ln.strip()), "")
    msg = f"ffmpeg falhou (código {rc})"
    return f"{msg}: {last}" if last else msg
```

por isto:

```python
    last = next((ln.strip() for ln in reversed(log_tail.splitlines()) if ln.strip()), "")
    msg = f"ffmpeg falhou (código {rc})"
    return f"{msg}: {last}" if last else msg


MSG_DISK_FULL = "Disco cheio — libere espaço"


def os_error_message(e: OSError) -> str:
    # erro de disco/arquivo em PT para o usuario; ENOSPC/EDQUOT viram "Disco cheio"
    if e.errno in (errno.ENOSPC, errno.EDQUOT):
        return MSG_DISK_FULL
    where = ""
    if isinstance(e.filename, (str, bytes)):
        where = f" ({os.path.basename(os.path.normpath(os.fsdecode(e.filename)))})"
    return f"Erro ao acessar o disco{where}: {e.strerror or e}"
```

- [ ] **Step 39: Rodar e ver passar (módulo e suíte inteira)**

Run: `Applio/.venv/bin/python -m unittest tests.test_procs -v`
Expected: `Ran 16 tests` e `OK`

Run: `./run_tests.sh`
Expected: `Ran 75 tests` (~5,6 s) e `OK` (as 63 da Step 19 + 12 destas correções)

- [ ] **Step 40: Commit**

```bash
git add studio/procs.py tests/test_procs.py
git commit -m "feat(procs): MSG_DISK_FULL e os_error_message com o erro de disco em PT

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

**Notas para o executor**

- **MKV não finalizado continua "legível":** um MKV morto com SIGKILL, ou escrito em pipe, é lido pelo ffprobe
  com uma duração *estimada pelo bitrate*. Um arquivo de 3 s apareceu com 16,23 s. Por isso a recuperação o marca
  como "gravado" direto, como a spec pede. A Task 4 deve medir por pacotes e nunca por `format.duration`.
- **WAV só com cabeçalho:** o ffmpeg morto antes do 1º pacote deixa tamanhos `0xFFFFFFFF`. O ffprobe devolve
  rc 0, mas sem `duration`. O remux `-c copy` também devolve rc 0 e continua sem duração, então a tomada vira
  "falhou". Arquivo lixo: o ffprobe sai com rc 1 e o remux com rc 183.
- **Teste do remux:** não achei arquivo real que o ffprobe rejeite e o remux conserte. O ramo "reconstruído" é
  testado com `mock.patch("studio.takes.media_info")`. O patch tem que ser no nome importado em `takes`, não em
  `procs`.
- **O neto não recebe o PDEATHSIG:** `setpriv --pdeathsig` vale só para o processo que o setpriv executa.
  Por isso `procs.run` cria um grupo próprio (`start_new_session=True`) e, no timeout, faz `killpg(SIGKILL)`.
  O teste com `sh -c "sleep 30 & …"` falha sem isso: conferi que o neto sobrevive com
  `start_new_session=False`.
- **Corrida no teste do PDEATHSIG:** o `setpriv` só arma o `prctl(PR_SET_PDEATHSIG)` depois que o `Popen` volta.
  Se o pai morrer antes disso, o filho fica órfão e o sinal nunca chega. Matando o pai logo após ler o pid, o
  teste falhou 3 vezes em 40 com os 12 núcleos ocupados (e deixou `sleep 30` órfãos). Por isso
  `test_child_dies_with_parent` espera `/proc/<pid>/comm == "sleep"` (`exec_done`) antes de matar o pai: o exec
  do sleep só acontece depois do `prctl`. Com isso foram 0 falhas em 60 execuções na mesma carga.
- **`/proc` de processo que acabou de morrer:** se o processo sai entre o `open` e o `read` de
  `/proc/<pid>/stat` (ou `comm`), o `read` levanta `ProcessLookupError` (ESRCH), e não `FileNotFoundError`. Por
  isso `gone()` e `exec_done()` tratam os dois.
- **Grupo próprio e Ctrl-C:** como o filho de `procs.run` fica em outra sessão, o Ctrl-C do terminal não chega a
  ele. Chega ao Python, que mata o grupo no `except BaseException`. O PDEATHSIG só vale enquanto viver a
  *thread* que fez o spawn: processos longos (gravador, worker) usam `procs.spawn` na thread principal.
- **`flash_frame_index` devolve o índice decodificado:** na tomada VFR, o flash do frame 90 cai no índice 50.
  Para ter o tempo, use o pts dos pacotes (veja `video_pts` em `tests/test_helpers.py`).
- **`beep_onset_s` é relativo ao início do áudio decodificado:** o instante absoluto é `start_time` do áudio +
  onset. O limiar de 0,3 cruza ~4 amostras depois do início do seno (+0,083 ms), então use tolerância ≥ 0,5 ms.
- **Receita do VFR:** `mktake.sh` não tem modo VFR. A receita vem do relatório ("160 packets became 299 frames"):
  `select='not(mod(n,2))+between(n,F-9,F+9)'` com `-fps_mode passthrough` gera exatamente 160 pacotes.
- **Aviso do `yuvj422p`:** `-pix_fmt yuvj422p` gera um aviso de formato obsoleto, escondido por
  `-loglevel error`. É o mesmo formato da webcam, então não troque.
- **Sem pytest:** o venv do Applio não tem pytest, então use `unittest`. `./run_tests.sh -v` repassa os
  argumentos ao `discover`.
- **Correções da revisão (Steps 21–40):**
  - O `glob` lê `[v2]` como classe de caracteres: com o projeto em `orochi [v2]/`, o `find_latest_checkpoint`
    devolvia `None` mesmo com os checkpoints lá. O `glob.escape` vale para o caminho e para a chave.
  - O `load_estado` compara tipos com `type(v) is type(padrão)`: `True` não vale como `int` (`av_offset_ms`), `1`
    não vale como `bool` (`gravar_video`), e `null` também é tipo errado. Um `float` como `40.0` em `av_offset_ms`
    também vira o padrão, porque o `calibrar_av.py` grava `int`.
  - O `merge_estado` relê o arquivo a cada gravação. Se o arquivo corromper com o app aberto, a cópia `.corrompido`
    é feita na gravação seguinte. Uma cópia `.corrompido` antiga é sobrescrita.
  - O `os_error_message` não traduz o `strerror`, que vem do sistema em inglês. Só o disco cheio ganha uma frase
    própria.

---

### Task 2: Dispositivos e áudio (devices, audio)

**Files:**
- Create: `studio/devices.py`
- Create: `studio/audio.py`
- Test: `tests/test_devices.py`, `tests/test_audio.py`

**Interfaces:**
- Consumes (Task 1):
  - `studio.procs.run(argv: list[str], timeout: float | None = None, **kw) -> subprocess.CompletedProcess`
    (prefixa setpriv, `stdin=DEVNULL`, `capture_output` + `text`; os `**kw` vão para o `Popen`, então `env=` funciona);
    `studio.procs.spawn(argv, **popen_kwargs) -> subprocess.Popen` (só no teste de hardware);
    `studio.procs.ProcError(message: str, rc: int | None = None, detail: str = "")`.
  - `studio.config.Modelo`, `studio.config.get_modelo(key: str) -> Modelo`,
    `studio.config.metadata_tags(m: Modelo) -> dict[str, str]` (ValueError se o modelo não tiver aviso/nome).
  - `tests.helpers.make_synthetic_take(path, duration_s=..., flash_frame=...) -> str`,
    `tests.helpers.beep_onset_s(wav_or_media) -> float`, `tests.helpers.ffprobe_streams(path) -> dict`.
- Produces:
  - `studio/devices.py` (só stdlib): `@dataclass(frozen=True) class Source(index: int, name: str)`;
    `parse_sources(text: str) -> list[Source]` (saída de `pactl list short sources`; sem `*.monitor`; USB primeiro,
    depois nome); `list_mics() -> list[Source]` (`[]` se o pactl falhar);
    `list_cameras(by_id_dir: str = "/dev/v4l/by-id") -> list[str]` (caminhos by-id `*-video-index0` que existem,
    ordenados; `[]` sem a pasta); `parse_source_outputs(text: str) -> dict[int, int]` (`{pid: índice do Source}`,
    só entende a saída com `LC_ALL=C`); `mic_source_of_pid(pid: int) -> int | None` (roda
    `LC_ALL=C pactl list source-outputs`; `None` se o pactl respondeu e o pid não está lá; **levanta `PactlError`**
    se o pactl falhou ou estourou o timeout, Step 13); `class PactlError(Exception)` (`str(e)` = mensagem PT);
    `free_bytes(path: str) -> int` (sobe até a pasta existente mais próxima). Constantes `PACTL_TIMEOUT_S = 5.0`,
    `BY_ID_DIR`.
  - `studio/audio.py` (só stdlib): `volumedetect(path: str) -> tuple[float | None, float | None]` (`(mean_db, max_db)`
    do 1º stream de áudio; `(None, None)` se falhar ou não houver amostras);
    `boost_volume(inp: str, out: str, gain_db: float) -> None` (filtro `volume=<g>dB,` + `LIMITER`, PCM16, grava
    `<out sem .wav>.part.wav` e faz `os.replace`; `ValueError` se o ganho não for finito; `ProcError` se o ffmpeg
    falhar, sem tocar no `out` antigo);
    `export_mp3(wav: str, mp3: str, modelo: Modelo) -> None` (libmp3lame `-q:a 2`, ID3v2.3, `title` e `comment` de
    `metadata_tags`, grava `<mp3 sem .mp3>.part.mp3` e faz `os.replace`; `ValueError` para modelo sem aviso antes de
    rodar o ffmpeg; `ProcError` se o ffmpeg falhar).
    Constante `LIMITER = "alimiter=limit=0.97:level=0:latency=1"` (ver Notas: o `level=0` é obrigatório).

Todos os comandos abaixo rodam da raiz do repositório (`~/orochi-ia-homenagem`).

- [ ] **Step 1: Escrever o teste de `devices` que falha**

As duas constantes `SOURCES_SHORT` e `SOURCE_OUTPUTS` são saídas **reais** desta máquina. Copie-as como estão,
sem recapturar: os índices e pids estão nos asserts. Elas usam escapes `\t` e `\\"`, sem tabulação literal. A
captura foi feita assim, só leitura, gravando para o muxer `null`, sem nada ir para o disco:

```bash
pactl list short sources
setpriv --pdeathsig TERM -- ffmpeg -nostdin -loglevel error -f pulse -sample_rate 48000 -channels 1 \
  -i alsa_input.usb-ME6S_c1_USB_AUDIO-00.mono-fallback -t 4 -f null - &
setpriv --pdeathsig TERM -- ffmpeg -nostdin -loglevel error -f pulse -sample_rate 48000 -channels 1 \
  -i nao_existe_este_mic -t 4 -f null - &
sleep 1.5; LC_ALL=C pactl list source-outputs; wait
```

`tests/test_devices.py`:

```python
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
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_devices -v`
Expected: ERROR com `ImportError: cannot import name 'devices' from 'studio' (.../studio/__init__.py)` e
`FAILED (errors=1)`

- [ ] **Step 3: Implementar `studio/devices.py`**

`studio/devices.py`:

```python
"""Microfones (pactl), cameras (/dev/v4l/by-id), source-output do gravador e espaco livre (so stdlib)."""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from studio.procs import run

PACTL_TIMEOUT_S = 5.0
BY_ID_DIR = "/dev/v4l/by-id"


@dataclass(frozen=True)
class Source:
    index: int
    name: str


def parse_sources(text: str) -> list[Source]:
    # linhas "indice\tnome\tdriver\tformato\testado"; monitores das saidas nao sao microfones
    found = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or not parts[0].strip().isdigit():
            continue
        name = parts[1].strip()
        if not name or name.endswith(".monitor"):
            continue
        found.append(Source(int(parts[0]), name))
    found.sort(key=lambda s: ("usb" not in s.name.lower(), s.name))
    return found


def _pactl(args: list[str], env: dict | None = None) -> str | None:
    try:
        r = run(["pactl", *args], timeout=PACTL_TIMEOUT_S, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def list_mics() -> list[Source]:
    return parse_sources(_pactl(["list", "short", "sources"]) or "")


def list_cameras(by_id_dir: str = BY_ID_DIR) -> list[str]:
    # so o no de captura (index0); link quebrado = camera desplugada
    folder = os.path.abspath(by_id_dir)
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    paths = [os.path.join(folder, n) for n in names if n.endswith("-video-index0")]
    return sorted(p for p in paths if os.path.exists(p))


_BLOCK_RE = re.compile(r"^Source Output #\d+", re.M)
_SOURCE_RE = re.compile(r"^\s+Source:\s*(\d+)\s*$", re.M)
_PID_RE = re.compile(r'^\s+application\.process\.id\s*=\s*"(\d+)"', re.M)


def parse_source_outputs(text: str) -> dict[int, int]:
    # {pid do processo: indice do Source}; so entende a saida com LC_ALL=C
    result = {}
    for block in _BLOCK_RE.split(text)[1:]:
        src = _SOURCE_RE.search(block)
        pid = _PID_RE.search(block)
        if src and pid:
            result[int(pid.group(1))] = int(src.group(1))
    return result


def mic_source_of_pid(pid: int) -> int | None:
    text = _pactl(["list", "source-outputs"], env={**os.environ, "LC_ALL": "C"})
    if text is None:
        return None
    return parse_source_outputs(text).get(pid)


def free_bytes(path: str) -> int:
    # sobe ate a pasta existente mais proxima (recordings/ pode ainda nao existir)
    p = os.path.abspath(path)
    while not os.path.exists(p) and os.path.dirname(p) != p:
        p = os.path.dirname(p)
    return shutil.disk_usage(p).free
```

- [ ] **Step 4: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_devices -v`
Expected: `Ran 16 tests` (~0,01 s) e `OK (skipped=2)`. Os 2 pulados são `RealDevicesTest`.

Opcional, com o microfone e a câmera ligados. O teste grava do 1º mic para o muxer `null`, sem salvar nada, e só
lista a câmera, sem abri-la:
Run: `RUN_HARDWARE=1 Applio/.venv/bin/python -m unittest tests.test_devices.RealDevicesTest -v`
Expected: `Ran 2 tests` (~0,6 s) e `OK`. Depois, `pgrep -a ffmpeg` não mostra nada.

- [ ] **Step 5: Commit**

```bash
git add studio/devices.py tests/test_devices.py
git commit -m "feat(studio): devices com microfones, cameras by-id e source-output do gravador

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: Escrever o teste de `audio` que falha**

O teste usa senoides sintéticas (`aevalsrc`) e a tomada sintética de `tests.helpers`. Ele confere quatro coisas:
- **Pico:** com +15 dB, o pico fica ≤ −0,2 dBFS pelo `volumedetect`.
- **Ganho exato abaixo do limite:** −30 dBFS vira −15,0 dBFS. Isso pega o *auto level* do `alimiter`.
- **Tempo:** o bip do helper não anda mais que 1 ms. Isso pega a falta de `latency=1`.
- **Tags do MP3:** lidas pelo `ffprobe`, a partir de um WAV de 40 kHz, que é a taxa do silvio.

`tests/test_audio.py`:

```python
import glob
import os
import subprocess
import tempfile
import unittest
import wave
from unittest import mock

from studio import audio
from studio.config import Modelo, get_modelo, metadata_tags
from studio.procs import ProcError
from tests import helpers


def make_wav(path: str, amp: float, sr: int = 48000, dur: float = 2.0, *extra: str) -> str:
    # senoide de 440 Hz mono PCM16 com amplitude amp (1.0 = 0 dBFS)
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"aevalsrc={amp}*sin(2*PI*440*t):s={sr}:c=mono:d={dur}", *extra,
                    "-c:a", "pcm_s16le", path], check=True, stdin=subprocess.DEVNULL, capture_output=True)
    return path


def audio_stream(path: str) -> dict:
    return next(s for s in helpers.ffprobe_streams(path)["streams"] if s["codec_type"] == "audio")


def leftovers(folder: str) -> list[str]:
    return glob.glob(os.path.join(folder, "*.part*"))


class VolumedetectTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_sine_levels(self):
        mean, peak = audio.volumedetect(make_wav(os.path.join(self.tmp, "s.wav"), 0.5))
        self.assertAlmostEqual(peak, -6.0, delta=0.15)
        self.assertAlmostEqual(mean, -9.0, delta=0.15)

    def test_reads_audio_stream_of_mkv(self):
        mkv = helpers.make_synthetic_take(os.path.join(self.tmp, "raw.mkv"), duration_s=2.0, flash_frame=30)
        mean, peak = audio.volumedetect(mkv)
        self.assertAlmostEqual(peak, -1.8, delta=0.3)     # bip de 0.8 + ruido
        self.assertLess(mean, -15.0)

    def test_failures_give_none(self):
        self.assertEqual(audio.volumedetect(os.path.join(self.tmp, "nada.wav")), (None, None))
        empty = os.path.join(self.tmp, "vazio.wav")
        with wave.open(empty, "wb") as w:                  # so cabecalho, 0 amostras
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
        self.assertEqual(audio.volumedetect(empty), (None, None))
        video_only = os.path.join(self.tmp, "v.mkv")
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=d=1",
                        "-c:v", "mjpeg", video_only], check=True, capture_output=True)
        self.assertEqual(audio.volumedetect(video_only), (None, None))


class BoostVolumeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_loud_sine_is_limited_below_full_scale(self):
        src = make_wav(os.path.join(self.tmp, "s.wav"), 0.5)          # -6 dBFS
        out = os.path.join(self.tmp, "boosted.wav")
        audio.boost_volume(src, out, 15)                              # +9 dBFS sem limitador
        mean, peak = audio.volumedetect(out)
        self.assertLessEqual(peak, -0.2)
        self.assertGreater(mean, -4.0)
        st = audio_stream(out)
        self.assertEqual((st["codec_name"], st["sample_rate"], st["channels"]), ("pcm_s16le", "48000", 1))
        self.assertEqual(st["duration_ts"], audio_stream(src)["duration_ts"])
        self.assertEqual(leftovers(self.tmp), [])

    def test_quiet_sine_gets_exact_gain(self):
        # abaixo do limite o limitador e transparente (sem auto level)
        src = make_wav(os.path.join(self.tmp, "q.wav"), 0.0316)       # -30 dBFS
        out = os.path.join(self.tmp, "boosted.wav")
        audio.boost_volume(src, out, 15)
        self.assertAlmostEqual(audio.volumedetect(out)[1], -15.0, delta=0.15)

    def test_beep_not_shifted(self):
        mkv = helpers.make_synthetic_take(os.path.join(self.tmp, "raw.mkv"), duration_s=3.0, flash_frame=45)
        out = os.path.join(self.tmp, "boosted.wav")
        audio.boost_volume(mkv, out, 15)
        shift = helpers.beep_onset_s(out) - helpers.beep_onset_s(mkv)
        self.assertLessEqual(abs(shift), 0.001)

    def test_replaces_existing_output(self):
        src = make_wav(os.path.join(self.tmp, "s.wav"), 0.1)
        out = os.path.join(self.tmp, "boosted.wav")
        with open(out, "wb") as f:
            f.write(b"lixo antigo")
        audio.boost_volume(src, out, 6)
        with wave.open(out, "rb") as w:
            self.assertEqual(w.getnframes(), 96000)
        self.assertEqual(leftovers(self.tmp), [])

    def test_failure_keeps_old_output(self):
        out = os.path.join(self.tmp, "boosted.wav")
        with open(out, "wb") as f:
            f.write(b"anterior")
        with self.assertRaises(ProcError) as cm:
            audio.boost_volume(os.path.join(self.tmp, "nada.wav"), out, 15)
        self.assertIn("volume", cm.exception.message)
        self.assertNotEqual(cm.exception.rc, 0)
        with open(out, "rb") as f:
            self.assertEqual(f.read(), b"anterior")
        self.assertEqual(leftovers(self.tmp), [])

    def test_invalid_gain(self):
        with mock.patch("studio.audio.run") as run:
            for bad in (float("nan"), float("inf")):
                with self.assertRaises(ValueError):
                    audio.boost_volume("/x/in.wav", "/x/out.wav", bad)
        run.assert_not_called()


class ExportMp3Test(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        # saida do RVC do silvio e 40 kHz, taxa que o MP3 nao tem
        self.wav = make_wav(os.path.join(self.tmp, "silvio.wav"), 0.3, 40000, 3.0, "-metadata", "artist=Fulano")

    def tearDown(self):
        self._tmp.cleanup()

    def test_id3v23_tags_with_ai_notice(self):
        modelo = get_modelo("silvio")
        mp3 = os.path.join(self.tmp, "silvio_IA.mp3")
        audio.export_mp3(self.wav, mp3, modelo)
        with open(mp3, "rb") as f:
            self.assertEqual(f.read(4), b"ID3\x03")
        info = helpers.ffprobe_streams(mp3)
        tags = info["format"]["tags"]
        expected = metadata_tags(modelo)
        self.assertEqual(tags["title"], expected["title"])
        self.assertEqual(tags["comment"], expected["comment"])
        self.assertIn("IA", tags["comment"])
        self.assertIn("Não é a voz real de Silvio Santos", tags["comment"])
        self.assertNotIn("artist", tags)                   # nada herdado do WAV
        st = audio_stream(mp3)
        self.assertEqual(st["codec_name"], "mp3")
        self.assertIn(int(st["sample_rate"]), (32000, 44100, 48000))
        self.assertAlmostEqual(float(info["format"]["duration"]), 3.0, delta=0.1)
        self.assertEqual(leftovers(self.tmp), [])

    def test_tags_follow_model(self):
        mp3 = os.path.join(self.tmp, "orochi_IA.mp3")
        audio.export_mp3(self.wav, mp3, get_modelo("orochi"))
        tags = helpers.ffprobe_streams(mp3)["format"]["tags"]
        self.assertIn("Não é a voz real do Orochi", tags["comment"])

    def test_model_without_notice_is_refused(self):
        mp3 = os.path.join(self.tmp, "x_IA.mp3")
        with self.assertRaises(ValueError):
            audio.export_mp3(self.wav, mp3, Modelo("x", "X", "X", " "))
        self.assertFalse(os.path.exists(mp3))
        self.assertEqual(leftovers(self.tmp), [])

    def test_failure_leaves_nothing(self):
        mp3 = os.path.join(self.tmp, "silvio_IA.mp3")
        with self.assertRaises(ProcError) as cm:
            audio.export_mp3(os.path.join(self.tmp, "nada.wav"), mp3, get_modelo("silvio"))
        self.assertIn("MP3", cm.exception.message)
        self.assertFalse(os.path.exists(mp3))
        self.assertEqual(leftovers(self.tmp), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 7: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_audio -v`
Expected: ERROR com `ImportError: cannot import name 'audio' from 'studio' (.../studio/__init__.py)` e
`FAILED (errors=1)`

- [ ] **Step 8: Implementar `studio/audio.py`**

`studio/audio.py`, com o `volumedetect` portado do `orochi_studio.py` antigo (`run_volumedetect`):

```python
"""Volume (volumedetect, aumentar com limitador) e MP3 com aviso de IA nas tags ID3."""

import math
import os
import re
import subprocess

from studio.config import Modelo, metadata_tags
from studio.procs import ProcError, run

DETECT_TIMEOUT_S = 60.0
ENCODE_TIMEOUT_S = 300.0
# level=0: o auto level (padrao) multiplica por 1/limit e o pico volta a 0 dBFS;
# latency=1: compensa o lookahead de 5 ms do limitador (o audio nao anda)
LIMITER = "alimiter=limit=0.97:level=0:latency=1"
FFMPEG = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]

_MEAN_RE = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB")
_MAX_RE = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?) dB")


def volumedetect(path: str) -> tuple[float | None, float | None]:
    # (mean_db, max_db) do 1o stream de audio; (None, None) se falhar ou nao houver amostras
    argv = ["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", path,
            "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"]
    try:
        r = run(argv, timeout=DETECT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    if r.returncode != 0:
        return None, None
    # o ffmpeg 6.1 recria o filtro no 1o frame: vale a ultima ocorrencia
    means = _MEAN_RE.findall(r.stderr)
    peaks = _MAX_RE.findall(r.stderr)
    return (float(means[-1]) if means else None, float(peaks[-1]) if peaks else None)


def _part_path(out: str) -> str:
    base, ext = os.path.splitext(out)
    return f"{base}.part{ext}"


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _encode_to(argv: list[str], part: str, out: str, what: str) -> None:
    # argv grava em part; so vira out (os.replace) se o ffmpeg terminar bem
    done = False
    try:
        try:
            r = run(argv, timeout=ENCODE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise ProcError(f"{what}: o ffmpeg demorou demais") from None
        if r.returncode != 0 or not os.path.isfile(part):
            raise ProcError(f"{what}: ffmpeg falhou (código {r.returncode})", r.returncode,
                            r.stderr.strip()[-2000:])
        os.replace(part, out)
        done = True
    finally:
        if not done:
            _unlink(part)


def boost_volume(inp: str, out: str, gain_db: float) -> None:
    gain_db = float(gain_db)
    if not math.isfinite(gain_db):
        raise ValueError(f"Ganho inválido: {gain_db}")
    part = _part_path(out)
    argv = [*FFMPEG, "-i", inp, "-map", "0:a:0", "-af", f"volume={gain_db:.2f}dB,{LIMITER}",
            "-c:a", "pcm_s16le", "-f", "wav", part]
    _encode_to(argv, part, out, "Não foi possível aumentar o volume")


def export_mp3(wav: str, mp3: str, modelo: Modelo) -> None:
    tags = metadata_tags(modelo)       # ValueError se o modelo nao tiver aviso (fail-closed)
    part = _part_path(mp3)
    argv = [*FFMPEG, "-i", wav, "-map", "0:a:0", "-map_metadata", "-1",
            "-c:a", "libmp3lame", "-q:a", "2", "-id3v2_version", "3",
            "-metadata", f"title={tags['title']}", "-metadata", f"comment={tags['comment']}",
            "-f", "mp3", part]
    _encode_to(argv, part, mp3, "Não foi possível gerar o MP3")
```

- [ ] **Step 9: Rodar e ver passar (módulo e suíte inteira)**

Run: `Applio/.venv/bin/python -m unittest tests.test_audio -v`
Expected: `Ran 13 tests` (~1,9 s) e `OK`

Run: `./run_tests.sh`
Expected: `Ran 104 tests` (~7,5 s) e `OK (skipped=2)`, contando a Task 1 com as correções. Depois, `pgrep -a ffmpeg` não mostra nada e
`git status --short` fica limpo, porque os testes só escrevem em diretórios temporários.

- [ ] **Step 10: Commit**

```bash
git add studio/audio.py tests/test_audio.py
git commit -m "feat(studio): audio com volumedetect, boost com limitador sem atraso e MP3 com aviso de IA

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

**Correção da revisão (Steps 11–15).** O `mic_source_of_pid` devolvia `None` tanto quando o pactl falhava
(ou estourava os 5 s) quanto quando o gravador não aparecia no pactl. A Task 6 tratava os dois casos como
"Microfone desconectado ou trocado", e uma falha passageira do pactl parava a gravação. Agora a falha do pactl
levanta `PactlError`, e fica claro que ele "não sabe" qual é o microfone.

- [ ] **Step 11: Teste da falha do pactl distinta de gravador ausente (falha)**

Em `tests/test_devices.py`, substituir isto:

```python
    def test_mic_source_of_pid_failures(self):
        with mock.patch("studio.devices.run", return_value=completed(SOURCE_OUTPUTS, rc=1)):
            self.assertIsNone(devices.mic_source_of_pid(873534))
        with mock.patch("studio.devices.run", side_effect=subprocess.TimeoutExpired(["pactl"], 5)):
            self.assertIsNone(devices.mic_source_of_pid(873534))
```

por isto:

```python
    def test_mic_source_of_pid_failures(self):
        # pactl que falhou ou estourou o timeout = "nao sei" (PactlError), diferente de gravador ausente (None)
        failures = {"rc 1": {"return_value": completed(SOURCE_OUTPUTS, rc=1)},
                    "timeout": {"side_effect": subprocess.TimeoutExpired(["pactl"], 5)},
                    "sem setpriv": {"side_effect": FileNotFoundError("setpriv")}}
        for name, kw in failures.items():
            with self.subTest(name), mock.patch("studio.devices.run", **kw):
                with self.assertRaises(devices.PactlError) as cm:
                    devices.mic_source_of_pid(873534)
                self.assertEqual(str(cm.exception), "O pactl não respondeu — não deu para conferir o microfone")
        with mock.patch("studio.devices.run", return_value=completed(SOURCE_OUTPUTS)):
            self.assertIsNone(devices.mic_source_of_pid(1))     # pactl ok e o pid sem source-output
```

- [ ] **Step 12: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_devices -v`
Expected: `FAILED (errors=3, skipped=2)` (`Ran 16 tests`): os 3 subtestes (`[rc 1]`, `[timeout]`, `[sem setpriv]`) com
`AttributeError: module 'studio.devices' has no attribute 'PactlError'`

- [ ] **Step 13: `PactlError` no `mic_source_of_pid`**

Em `studio/devices.py`, substituir isto:

```python
def mic_source_of_pid(pid: int) -> int | None:
    text = _pactl(["list", "source-outputs"], env={**os.environ, "LC_ALL": "C"})
    if text is None:
        return None
    return parse_source_outputs(text).get(pid)
```

por isto:

```python
class PactlError(Exception):
    """O pactl falhou ou estourou o timeout: nao da para saber qual microfone o gravador usa."""


def mic_source_of_pid(pid: int) -> int | None:
    # None = o pactl respondeu e o pid nao tem source-output (gravador sumiu); PactlError = nao sei
    text = _pactl(["list", "source-outputs"], env={**os.environ, "LC_ALL": "C"})
    if text is None:
        raise PactlError("O pactl não respondeu — não deu para conferir o microfone")
    return parse_source_outputs(text).get(pid)
```

- [ ] **Step 14: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_devices -v`
Expected: `Ran 16 tests` e `OK (skipped=2)`

- [ ] **Step 15: Commit**

```bash
git add studio/devices.py tests/test_devices.py
git commit -m "fix(devices): mic_source_of_pid levanta PactlError quando o pactl falha (nao sei != gravador ausente)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

**Notas para o executor**

- **`alimiter` precisa de `level=0` (desvio do contrato):** o filtro do contrato,
  `volume=15dB,alimiter=limit=0.97:latency=1`, **corta os picos**. O `level` (*auto level*) vem ligado por padrão e
  multiplica a saída por 1/limit. Numa senoide de −6 dBFS com +15 dB, o pico int16 foi 32768 (0,0 dBFS). Com
  `level=0`, o pico foi 31785 (−0,26 dBFS, e o `volumedetect` mostra −0,3). Com o filtro do contrato, o teste
  falha assim: `AssertionError: 0.0 not less than or equal to -0.2` e `-14.7 != -15.0 within 0.15 delta`.
- **`latency=1` funciona:** o bip deslocou 0,000 ms e o número de amostras ficou igual (96000 → 96000). Sem ele,
  o bip atrasa +4,979 ms e o teste falha com `0.004979166666666535 not less than or equal to 0.001`.
- **`volumedetect` no ffmpeg 6.1.1:**
  - O ffmpeg imprime uma linha extra `n_samples: 0`, de uma 1ª instância do filtro que ele recria no 1º frame.
    Por isso o código usa a **última** ocorrência.
  - O `-map 0:a:0` faz o `raw.mkv` ser lido sem decodificar o MJPEG. Arquivo sem áudio sai com rc ≠ 0 e o
    resultado é `(None, None)`.
  - Silêncio digital dá −91,0 dB, e não `-inf`. Conferi com WAV PCM16 e float32.
  - Um WAV só com cabeçalho dá `(None, None)`. A GUI deve tratar `None` como "sem áudio".
- **MP3 a partir de 40 kHz:** o MP3 não tem a taxa de 40 kHz, e o ffmpeg escolhe 44,1 kHz sozinho. A duração
  cresce ~30 ms com o *padding* do encoder, e o render nunca usa o MP3.
  - O arquivo começa com `ID3\x03` (v2.3), e os acentos das tags voltam iguais no `ffprobe`.
  - O `-map_metadata -1` impede que tags do WAV vazem para o MP3 (o teste usa `artist=Fulano`).
  - O ffmpeg acrescenta uma tag `encoder` por conta própria.
- **pactl:**
  - Em pt_BR, `pactl list ...` sai traduzido ("Fonte #54", "Estado:"), por isso o código usa sempre `LC_ALL=C`.
  - Os índices mudam: o monitor HDMI passou de 3377 para 3420 entre duas capturas. Guarde o **nome** do mic no
    `estado.json` e busque o índice com `list_mics()` na hora de gravar.
  - Um nome de mic inválido grava do mic padrão com rc 0. Na fixture, o pid 873535 com `nao_existe_este_mic` caiu
    no Source 54. É por isso que a Task 6 compara o índice.
  - O `setpriv` executa o ffmpeg no mesmo processo, então o `Popen.pid` é o do ffmpeg. No teste real, o índice
    apareceu já na 1ª consulta, em 0,5 s.
  - A linha `Source Latency: 0 usec` também começa com "Source". A regex exige `Source:` seguido só de dígitos.
- **Câmeras:** `list_cameras` devolve o caminho by-id, sem resolver para `/dev/video0`, e descarta links
  quebrados de câmera desplugada. O `*-video-index1` é o nó de metadados e fica de fora.
- **Erros:** o contrato não fixa o tipo de erro de `audio`. Aqui é `ProcError`, com `.message` em PT-BR,
  `.rc` e `.detail` (o fim do stderr). Ganho não finito e modelo sem aviso dão `ValueError` antes de rodar o
  ffmpeg. A GUI (Tasks 9 e 11) deve pegar os dois.
- **Dados das fixtures:** elas contêm usuário, host e machine-id desta máquina, porque a instrução foi não
  anonimizar.
- **Instabilidade não atribuída:** uma rodada da suíte inteira com o `python3` do sistema, sob carga (load ~5,5,
  com outras tasks rodando), deu `failures=1`, e a saída se perdeu. Não se repetiu em 18 rodadas completas, nem em
  20 rodadas só de `test_devices` + `test_audio`. Estes dois módulos não têm asserções de tempo. A causa provável
  era a corrida de `test_child_dies_with_parent` (Task 1), que falhava sob carga; ela já foi corrigida na Task 1
  (`exec_done` espera o exec do sleep, e `gone()` trata `ProcessLookupError`).
- **Falha do pactl ≠ gravador ausente (Steps 11–15):** o `_pactl` continua devolvendo `None` quando falha, e o
  `list_mics` continua dando `[]` nesse caso. Só o `mic_source_of_pid` muda: ele levanta `PactlError` ("não sei")
  em vez de devolver o mesmo `None` de "o pid não tem source-output". É a Task 6 que decide o que fazer com cada
  caso.

---

### Task 3: Marca d'água (`studio/watermark.py`)

**Files:**
- Create: `studio/watermark.py`
- Test: `tests/test_watermark.py`

**Interfaces:**
- Consumes (Task 1, `studio/config.py`): `FONT_BOLD`, `FONT_REG`, `AVISO_LINHA1 = "VOZ GERADA POR IA"`,
  `AVISO_LINHA3 = "paródia · homenagem"`, `@dataclass(frozen=True) class Modelo(key, label, nome, aviso)`,
  `get_modelo(key: str) -> Modelo` (só nos testes).
- Produces (Tasks 5 e 10 usam):
  - `safe_column(w: int, h: int) -> tuple[int, int]`: `(x0, x1)` da coluna central 9:16 menos margens, com `x1`
    exclusivo. `max_w = min(round(h*9/16), w) - 2*margem` e `margem = max(8, round(0.03*h))`. Em 1280×720 dá
    `(459, 820)`, ou seja, 361 px.
  - `make_watermark(w: int, h: int, modelo: Modelo) -> PIL.Image.Image`: RGBA com tamanho exato `(w, h)`. Levanta
    `ValueError` (mensagem em PT) se `modelo.aviso` estiver vazio ou se algum texto não couber nem com 6 px.
  - `watermark_path(take_dir: str, w: int, h: int, modelo: Modelo) -> str`: devolve
    `<take_dir>/wm_<W>x<H>_<key>.png`. Reaproveita o arquivo se tamanho, modo e assinatura iTXt `studio_wm` baterem.
    Senão, regrava via `.part` + `os.replace`. Levanta `ValueError` nos mesmos casos de `make_watermark`, sem
    gravar nada.
  - `text_pixel_mask(img) -> numpy.ndarray`: `bool` com forma `(h, w)`, True onde `alpha == 255` e os três
    canais RGB são `>= 230`. Aceita `PIL.Image` ou caminho do PNG.
  - `band_rect(w: int, h: int) -> tuple[int, int, int, int]`: `(0, y0, w, h)` da faixa (x1/y1 exclusivos). Depende
    só de `h` (tamanhos nominais), nunca do texto do modelo. Em 720p dá `(0, 613, 1280, 720)`, 107 px ≈ 15 %.
  - Constantes: `BAND_RGBA = (0, 0, 0, 140)`, `BADGE_DOT = (230, 30, 40, 255)`, `MIN_FONT_PX = 6`,
    `SIG_KEY = "studio_wm"`, `LAYOUT_VERSION`.

Layout: a faixa ocupa a largura toda embaixo, em preto com alpha 140. Nela vão 3 linhas centralizadas na coluna
segura:
1. `AVISO_LINHA1`, em negrito
2. `modelo.aviso`, regular
3. `AVISO_LINHA3`, regular menor

Os tamanhos nominais são 4,2 %, 3,4 % e 2,8 % de `h` (30/24/20 px em 720p). Cada linha encolhe 1 px por vez até
caber em `max_w` (Silvio em 720p fica com 22 px). Cada linha ocupa um "slot" de altura
`ascent + descent` da fonte nominal, e a linha encolhida fica centralizada no seu slot. O selo fica no canto
superior direito, com margem de 3 % de `h`: é o layout do probe `render/watermark.py`, com cápsula escura,
contorno branco, ponto vermelho desenhado com `ellipse` (sem glifo) e "IA" em negrito branco.

Todos os comandos rodam da raiz do repositório (`~/orochi-ia-homenagem`).

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_watermark.py`:

```python
import os
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

from studio import watermark
from studio.config import Modelo, get_modelo

SIZES = ((1280, 720), (640, 480), (800, 600), (1920, 1080), (720, 1280))
SILVIO = get_modelo("silvio")
OROCHI = get_modelo("orochi")
BAND_ALPHA = 140


def runs(flags) -> int:
    # quantos blocos continuos de True (linhas de texto separadas)
    n, prev = 0, False
    for f in flags:
        if f and not prev:
            n += 1
        prev = bool(f)
    return n


class GeometryTest(unittest.TestCase):
    def test_safe_column_values(self):
        # 720p: round(720*9/16)=405, margem 22 -> 361 px centrados (spec 8.1)
        self.assertEqual(watermark.safe_column(1280, 720), (459, 820))
        self.assertEqual(watermark.safe_column(640, 480), (199, 441))
        self.assertEqual(watermark.safe_column(800, 600), (249, 551))
        self.assertEqual(watermark.safe_column(1920, 1080), (688, 1232))
        # retrato: a coluna 9:16 e o proprio quadro, menos as margens
        self.assertEqual(watermark.safe_column(720, 1280), (38, 682))

    def test_band_rect_is_footer_full_width(self):
        for w, h in SIZES:
            x0, y0, x1, y1 = watermark.band_rect(w, h)
            self.assertEqual((x0, x1, y1), (0, w, h), (w, h))
            self.assertTrue(0.10 * h < h - y0 < 0.20 * h, (w, h, y0))


class ImageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.imgs = {(w, h, m.key): watermark.make_watermark(w, h, m)
                    for w, h in SIZES for m in (SILVIO, OROCHI)}

    def cases(self):
        return [(w, h, key, img) for (w, h, key), img in self.imgs.items()]

    def test_exact_size_rgba(self):
        for w, h, key, img in self.cases():
            with self.subTest(size=f"{w}x{h}", modelo=key):
                self.assertEqual(img.size, (w, h))
                self.assertEqual(img.mode, "RGBA")

    def test_text_mask_is_bool_hw(self):
        mask = watermark.text_pixel_mask(self.imgs[(1280, 720, "silvio")])
        self.assertEqual(mask.dtype, np.bool_)
        self.assertEqual(mask.shape, (720, 1280))

    def test_band_text_inside_safe_column(self):
        for w, h, key, img in self.cases():
            with self.subTest(size=f"{w}x{h}", modelo=key):
                x0, x1 = watermark.safe_column(w, h)
                _, by0, _, _ = watermark.band_rect(w, h)
                ys, xs = np.nonzero(watermark.text_pixel_mask(img)[by0:])
                self.assertGreater(len(xs), 200)
                self.assertGreaterEqual(xs.min(), x0)
                self.assertLess(xs.max(), x1)
                # tambem as bordas suavizadas: nada na faixa difere do preto 55 % fora da coluna
                band = np.asarray(img)[by0:]
                ink = (band != np.array([0, 0, 0, BAND_ALPHA], dtype=np.uint8)).any(axis=2)
                _, ink_x = np.nonzero(ink)
                self.assertGreaterEqual(ink_x.min(), x0)
                self.assertLess(ink_x.max(), x1)

    def test_band_has_three_lines(self):
        for w, h, key, img in self.cases():
            with self.subTest(size=f"{w}x{h}", modelo=key):
                _, by0, _, _ = watermark.band_rect(w, h)
                rows = watermark.text_pixel_mask(img)[by0:].any(axis=1)
                self.assertEqual(runs(rows), 3)

    def test_band_drawn_full_width_at_bottom(self):
        for w, h, key, img in self.cases():
            with self.subTest(size=f"{w}x{h}", modelo=key):
                _, by0, _, _ = watermark.band_rect(w, h)
                alpha = np.asarray(img)[..., 3]
                x0, _ = watermark.safe_column(w, h)
                # colunas fora da coluna segura: so faixa, sem texto
                for x in (0, x0 // 2, w - 1):
                    self.assertTrue((alpha[by0:, x] == BAND_ALPHA).all(), x)
                    self.assertTrue((alpha[h // 2:by0, x] == 0).all(), x)

    def test_only_badge_text_outside_band(self):
        for w, h, key, img in self.cases():
            with self.subTest(size=f"{w}x{h}", modelo=key):
                _, by0, _, _ = watermark.band_rect(w, h)
                ys, xs = np.nonzero(watermark.text_pixel_mask(img)[:by0])
                self.assertGreater(len(xs), 20)
                self.assertLess(ys.max(), h // 4)
                self.assertGreaterEqual(xs.min(), w // 2)

    def test_badge_red_dot_top_right(self):
        for w, h, key, img in self.cases():
            with self.subTest(size=f"{w}x{h}", modelo=key):
                a = np.asarray(img).astype(int)
                red = (a[..., 0] >= 200) & (a[..., 1] <= 60) & (a[..., 2] <= 60) & (a[..., 3] == 255)
                ys, xs = np.nonzero(red)
                self.assertGreater(len(xs), 20)
                self.assertLess(ys.max(), h // 2)
                self.assertGreaterEqual(xs.min(), w // 2)

    def test_text_changes_with_model(self):
        for w, h in SIZES:
            a = watermark.text_pixel_mask(self.imgs[(w, h, "silvio")])
            b = watermark.text_pixel_mask(self.imgs[(w, h, "orochi")])
            self.assertFalse(np.array_equal(a, b), (w, h))


class ModelTextTest(unittest.TestCase):
    def test_empty_aviso_raises(self):
        for aviso in ("", "   "):
            with self.assertRaises(ValueError):
                watermark.make_watermark(1280, 720, Modelo("x", "X", "X", aviso))

    def test_long_aviso_shrinks_to_fit(self):
        m = Modelo("x", "X", "Fulano", "Não é a voz real de Fulano de Tal da Silva Sauro Júnior")
        img = watermark.make_watermark(1280, 720, m)
        x0, x1 = watermark.safe_column(1280, 720)
        _, by0, _, _ = watermark.band_rect(1280, 720)
        _, xs = np.nonzero(watermark.text_pixel_mask(img)[by0:])
        self.assertGreaterEqual(xs.min(), x0)
        self.assertLess(xs.max(), x1)

    def test_text_that_cannot_fit_raises(self):
        m = Modelo("x", "X", "X", "Não é a voz real " * 20)
        with self.assertRaises(ValueError):
            watermark.make_watermark(1280, 720, m)


class WatermarkPathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_creates_png_with_name_and_size(self):
        p = watermark.watermark_path(self.dir, 640, 480, SILVIO)
        self.assertEqual(p, os.path.join(self.dir, "wm_640x480_silvio.png"))
        with Image.open(p) as im:
            self.assertEqual((im.size, im.mode), ((640, 480), "RGBA"))
        self.assertEqual(os.listdir(self.dir), ["wm_640x480_silvio.png"])
        expected = watermark.text_pixel_mask(watermark.make_watermark(640, 480, SILVIO))
        self.assertTrue(np.array_equal(watermark.text_pixel_mask(p), expected))

    def test_cached_file_is_reused(self):
        p = watermark.watermark_path(self.dir, 640, 480, SILVIO)
        st = os.stat(p)
        self.assertEqual(watermark.watermark_path(self.dir, 640, 480, SILVIO), p)
        st2 = os.stat(p)
        self.assertEqual((st.st_ino, st.st_mtime_ns), (st2.st_ino, st2.st_mtime_ns))

    def test_corrupt_cache_is_regenerated(self):
        p = os.path.join(self.dir, "wm_640x480_silvio.png")
        with open(p, "wb") as f:
            f.write(b"lixo")
        watermark.watermark_path(self.dir, 640, 480, SILVIO)
        with Image.open(p) as im:
            self.assertEqual(im.size, (640, 480))

    def test_non_png_cache_is_regenerated(self):
        p = os.path.join(self.dir, "wm_640x480_silvio.png")
        Image.new("RGB", (640, 480)).save(p, format="JPEG")
        watermark.watermark_path(self.dir, 640, 480, SILVIO)
        with Image.open(p) as im:
            self.assertEqual((im.format, im.mode), ("PNG", "RGBA"))

    def test_failed_save_leaves_nothing(self):
        def half_written(img, fp, *args, **kwargs):
            with open(fp, "wb") as f:
                f.write(b"\x89PNG meio arquivo")
            raise OSError("disco cheio")

        with mock.patch.object(Image.Image, "save", half_written):
            with self.assertRaises(OSError):
                watermark.watermark_path(self.dir, 640, 480, SILVIO)
        self.assertEqual(os.listdir(self.dir), [])

    def test_changed_aviso_regenerates(self):
        p = watermark.watermark_path(self.dir, 640, 480, SILVIO)
        before = watermark.text_pixel_mask(p)
        other = Modelo("silvio", "Silvio Santos", "Silvio Santos", "Não é a voz real do Silvio")
        watermark.watermark_path(self.dir, 640, 480, other)
        self.assertFalse(np.array_equal(watermark.text_pixel_mask(p), before))

    def test_empty_aviso_raises_and_writes_nothing(self):
        with self.assertRaises(ValueError):
            watermark.watermark_path(self.dir, 640, 480, Modelo("x", "X", "X", ""))
        self.assertEqual(os.listdir(self.dir), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_watermark -v`
Expected: ERROR com `ImportError: cannot import name 'watermark' from 'studio' (.../studio/__init__.py)`

- [ ] **Step 3: Implementar**

`studio/watermark.py`:

```python
"""Marca d'agua RGBA (PIL): selo IA no canto superior direito + faixa de 3 linhas na coluna 9:16."""

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from studio.config import AVISO_LINHA1, AVISO_LINHA3, FONT_BOLD, FONT_REG, Modelo

BAND_RGBA = (0, 0, 0, 140)                  # preto a 55 %
LINE_RGBA = ((255, 255, 255, 255), (240, 240, 240, 255), (240, 240, 240, 255))
BADGE_FILL = (0, 0, 0, 165)
BADGE_OUTLINE = (255, 255, 255, 200)
BADGE_DOT = (230, 30, 40, 255)
BADGE_TEXT = "IA"
MIN_FONT_PX = 6
SIG_KEY = "studio_wm"
LAYOUT_VERSION = "1"                        # mudar quando o desenho mudar (invalida o cache)


def _margin(h: int) -> int:
    return max(8, round(0.03 * h))


def _pad(h: int) -> int:
    return max(3, round(0.012 * h))


def _nominal_sizes(h: int) -> tuple[int, int, int]:
    # 30/24/20 px em 720p; o texto so encolhe a partir daqui
    return max(9, round(0.042 * h)), max(8, round(0.034 * h)), max(7, round(0.028 * h))


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    # sem cache: ~1 ms por marca e nenhum FT_Face compartilhado entre threads
    return ImageFont.truetype(path, size)


def _line_h(font: ImageFont.FreeTypeFont) -> int:
    ascent, descent = font.getmetrics()
    return ascent + descent


def _aviso(modelo: Modelo) -> str:
    aviso = modelo.aviso.strip()
    if not aviso:
        raise ValueError(f"Modelo {modelo.key} sem texto de aviso: a marca d'água não pode ser gerada")
    return aviso


def safe_column(w: int, h: int) -> tuple[int, int]:
    # coluna central 9:16 menos a margem dos dois lados; x1 exclusivo
    max_w = min(round(h * 9 / 16), w) - 2 * _margin(h)
    x0 = (w - max_w) // 2
    return x0, x0 + max_w


def band_rect(w: int, h: int) -> tuple[int, int, int, int]:
    # altura so depende de h (tamanhos nominais), nunca do texto do modelo
    s1, s2, s3 = _nominal_sizes(h)
    band_h = (2 * _pad(h) + _line_h(_font(FONT_BOLD, s1)) + _line_h(_font(FONT_REG, s2))
              + _line_h(_font(FONT_REG, s3)))
    return 0, h - band_h, w, h


def _fit_font(path: str, size: int, text: str, max_w: int) -> ImageFont.FreeTypeFont:
    for s in range(size, MIN_FONT_PX - 1, -1):
        f = _font(path, s)
        left, _, right, _ = f.getbbox(text, anchor="ls")
        if right - left <= max_w:
            return f
    raise ValueError(f"O texto da marca d'água não cabe num vídeo desse tamanho: {text!r}")


def _draw_band(d: ImageDraw.ImageDraw, w: int, h: int, lines: tuple[str, str, str]) -> None:
    x0, x1 = safe_column(w, h)
    _, by0, _, _ = band_rect(w, h)
    d.rectangle((0, by0, w - 1, h - 1), fill=BAND_RGBA)
    slot_top = by0 + _pad(h)
    for path, size, text, fill in zip((FONT_BOLD, FONT_REG, FONT_REG), _nominal_sizes(h), lines, LINE_RGBA):
        slot_h = _line_h(_font(path, size))
        f = _fit_font(path, size, text, x1 - x0)
        left, _, right, _ = f.getbbox(text, anchor="ls")
        ascent, descent = f.getmetrics()
        baseline = slot_top + (slot_h - ascent - descent) // 2 + ascent
        x = x0 + (x1 - x0 - (right - left)) // 2 - left
        d.text((x, baseline), text, font=f, fill=fill, anchor="ls")
        slot_top += slot_h


def _draw_badge(d: ImageDraw.ImageDraw, w: int, h: int) -> None:
    # capsula escura com ponto vermelho desenhado (nao depende de glifo) + "IA"
    f = _font(FONT_BOLD, max(12, round(0.05 * h)))
    left, top, right, bottom = f.getbbox(BADGE_TEXT)
    tw, th = right - left, bottom - top
    dot, pad_x, pad_y, gap = round(th * 0.85), round(th * 0.55), round(th * 0.45), round(th * 0.40)
    x1, y0 = w - _margin(h), _margin(h)
    x0, y1 = x1 - (pad_x + dot + gap + tw + pad_x), y0 + pad_y + th + pad_y
    d.rounded_rectangle((x0, y0, x1, y1), radius=(y1 - y0) // 2, fill=BADGE_FILL,
                        outline=BADGE_OUTLINE, width=max(1, round(h / 360)))
    cy, dx = (y0 + y1) / 2, x0 + pad_x
    d.ellipse((dx, cy - dot / 2, dx + dot, cy + dot / 2), fill=BADGE_DOT)
    d.text((dx + dot + gap - left, y0 + pad_y - top), BADGE_TEXT, font=f, fill=(255, 255, 255, 255))


def make_watermark(w: int, h: int, modelo: Modelo) -> Image.Image:
    lines = (AVISO_LINHA1, _aviso(modelo), AVISO_LINHA3)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    _draw_band(d, w, h, lines)
    _draw_badge(d, w, h)
    return img


def _signature(modelo: Modelo) -> str:
    return "|".join((LAYOUT_VERSION, AVISO_LINHA1, _aviso(modelo), AVISO_LINHA3))


def _cache_ok(path: str, w: int, h: int, sig: str) -> bool:
    try:
        with Image.open(path) as im:
            return im.size == (w, h) and im.mode == "RGBA" and getattr(im, "text", {}).get(SIG_KEY) == sig
    except (OSError, ValueError):
        return False


def watermark_path(take_dir: str, w: int, h: int, modelo: Modelo) -> str:
    sig = _signature(modelo)
    path = os.path.join(take_dir, f"wm_{w}x{h}_{modelo.key}.png")
    if _cache_ok(path, w, h, sig):
        return path
    img = make_watermark(w, h, modelo)
    info = PngImagePlugin.PngInfo()
    info.add_itxt(SIG_KEY, sig)
    part = path + ".part"
    try:
        img.save(part, format="PNG", pnginfo=info, optimize=True)
        os.replace(part, path)
    except BaseException:
        try:
            os.unlink(part)
        except OSError:
            pass
        raise
    return path


def text_pixel_mask(img) -> np.ndarray:
    # aceita Image ou caminho do PNG; True onde o texto e branco opaco
    if isinstance(img, (str, os.PathLike)):
        with Image.open(img) as im:
            a = np.asarray(im.convert("RGBA"))
    else:
        a = np.asarray(img.convert("RGBA"))
    return (a[..., 3] == 255) & (a[..., :3] >= 230).all(axis=2)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_watermark -v`
Expected: `Ran 20 tests in ~1.2s` e `OK`

Run: `./run_tests.sh`
Expected: `Ran 124 tests` e `OK (skipped=2)`, contando as Tasks 1 e 2 (com as correções) e esta.

Conferência visual (fora do git, não versionar):

```bash
Applio/.venv/bin/python -c "
from PIL import Image
from studio import watermark
from studio.config import get_modelo
for w, h in ((1280, 720), (640, 480)):
    bg = Image.new('RGBA', (w, h), (150, 150, 150, 255))
    bg.paste((250, 240, 120, 255), (w // 3, h * 3 // 4, w * 2 // 3, h))
    Image.alpha_composite(bg, watermark.make_watermark(w, h, get_modelo('silvio'))).save(f'_preview_{w}x{h}.png')
    print(f'_preview_{w}x{h}.png', watermark.safe_column(w, h), watermark.band_rect(w, h))
"
```

Expected:

```
_preview_1280x720.png (459, 820) (0, 613, 1280, 720)
_preview_640x480.png (199, 441) (0, 408, 640, 480)
```

Abra os dois PNGs e confira:
- As 3 linhas estão centralizadas e legíveis sobre o cinza e sobre o amarelo claro.
- Os acentos (ã, é, ó) e o "·" aparecem certos.
- O selo "● IA" está no canto superior direito.

Depois apague com `rm _preview_*.png`.

- [ ] **Step 5: Commit**

```bash
git add studio/watermark.py tests/test_watermark.py
git commit -m "feat(studio): marca d'agua com faixa de 3 linhas na coluna 9:16 e selo IA

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

**Notas para o executor:**

- **Desenho no RGBA:** o `ImageDraw.Draw` num RGBA não compõe, ele substitui os pixels. Então o texto por cima da
  faixa sai `(255,255,255,255)` no miolo, e as bordas suavizadas saem com alpha entre 140 e 255.
  - É por isso que `text_pixel_mask` só pega os pixels 100 % opacos e com RGB `>= 230`.
  - A Task 5 usa essa máscara na checagem de luminância `> 200`. Não mude as cores do texto para menos de 230.
- **`band_rect(w, h)` não recebe o modelo:** a altura da faixa vem dos tamanhos nominais e do
  `ascent + descent` da fonte. Se ela dependesse do bbox do texto, cada modelo teria outra faixa.
- **`rectangle` do PIL inclui as duas pontas:** use `(0, y0, w - 1, h - 1)`.
- **Mesma âncora na medida e no desenho:** meça com `getbbox(text, anchor="ls")` e desenhe com `anchor="ls"` em
  `x` inteiro. Só assim a tinta (inclusive a borda suavizada) cai dentro de `[x0, x1)`. O teste confere as duas
  coisas.
- **`subTest` nos testes:** não coloque `with self.subTest` em volta de um `yield` num gerador. Uma falha dentro
  do loop vira um erro espúrio de `GeneratorExit`. Os testes usam `cases()` com uma lista e o `subTest` dentro de
  cada teste.
- **Vídeo em retrato (720×1280):** a coluna segura ocupa quase a largura toda, `(38, 682)`. Por isso a checagem
  "só faixa" usa colunas fora dela: `0`, `x0 // 2` e `w - 1`.
- **Cache de `watermark_path`:**
  - `JpegImageFile` não tem `.text`, daí o `getattr(im, "text", {})`.
  - A assinatura iTXt inclui `LAYOUT_VERSION` e os três textos. Assim, trocar o aviso em `config.MODELOS` ou o
    desenho invalida o PNG antigo da tomada. Suba `LAYOUT_VERSION` quando mudar o desenho.
- **Sem cache de fontes:** não há `lru_cache` em `_font`. A diferença medida foi de 2,3 contra 3,4 ms por marca
  em 720p, e assim nenhum objeto FreeType fica compartilhado entre a thread da GUI e a do render.
- **Selo e corte 9:16:** o selo fica fora da coluna 9:16 (é o que D1 e a spec 8.1 pedem). Num corte 9:16 o selo
  some, mas as 3 linhas sobrevivem inteiras (conferido recortando o preview 720p).
- **Teste da Task 1 sob carga:** `tests.test_procs.SpawnRunTest.test_child_dies_with_parent` chegou a falhar com a
  CPU carregada (`o filho sobreviveu a morte do pai (pdeathsig)`): o pai morria antes de o `setpriv` chamar
  `prctl(PR_SET_PDEATHSIG)`. Isso já foi corrigido na Task 1 (o teste espera o exec do sleep com `exec_done`), então
  a suíte deve passar sempre. Não tem relação com a marca d'água.

---

### Task 4: Linha do tempo (relógio do mic, âncora do vídeo e `audio.wav` alinhado)

**Files:**
- Create: `studio/timeline.py`
- Create: `tests/fixtures/drift_usb-Generalplus_Usb_Audio_Device.framemd5` e
  `tests/fixtures/drift_usb-ME6S_c1_USB_AUDIO.framemd5` (cópias de `docs/superpowers/probes/2026-09-26/deriva-mic/`)
- Test: `tests/test_timeline.py` (contas puras: framemd5, ajuste do relógio, texto do filtro)
- Test: `tests/test_timeline_media.py` (mídia sintética: pacotes, âncora, `extract_aligned_audio`, buraco, deriva)

**Interfaces:**
- Consumes (Task 1):
  - `studio.procs.ffprobe_json(path: str, *args: str, timeout: float = 60.0) -> dict`
  - `studio.procs.media_info(path: str) -> dict` (`{"format", "video", "audio"}`)
  - `studio.procs.run(argv: list[str], timeout: float | None = None, **kw) -> subprocess.CompletedProcess`
  - `studio.procs.ProcError(message: str, rc: int | None = None, detail: str = "")` (`.message` em PT)
  - testes: `tests.helpers.make_synthetic_take(path, audio_offset_s=0.0, duration_s=10.0, flash_frame=90,
    shift_s=0.0, vfr=False, size="1280x720") -> str`, `tests.helpers.beep_onset_s(wav_or_media) -> float`,
    `tests.helpers.flash_frame_index(media) -> int`
- Produces (`studio/timeline.py`, usa numpy; o render da Task 5 e a GUI usam):
  - `@dataclass class VideoInfo: w: int; h: int; ancora_pts: float; n_frames: int; fps_medido: float`
  - `@dataclass class AudioFit: inicio: float; taxa_real: float; gaps: int; residuo_ms: float; duracao: float`
    (campos já em `float`/`int` do Python: `dataclasses.asdict(...)` vai direto para `take.video` /
    `take.audio_fit`; de volta com `VideoInfo(**take.video)`)
  - `read_packets(path: str, stream: str) -> list[tuple[float, float, int]]` — `"v"` | `"a"` →
    `(pts_time, duration_time, size)` na ordem do arquivo; `duration_time` ausente vira `0.0`; `ValueError` se
    `stream` não for `"v"`/`"a"`.
  - `video_info(path: str, fps_out: int = 30) -> VideoInfo` — `ancora_pts` = pts do 2º pacote de vídeo (1º se
    só houver um); `n_frames = round((pts_ult + dur_ult - ancora) * fps_out)` (`dur_ult` ausente = `1/fps_out`);
    `fps_medido` = pacotes por segundo a partir da âncora (2 casas). `ProcError` "`<arquivo>` não tem vídeo".
  - `fit_audio_clock(pts: list[float], sizes: list[int], sr: int = 48000, bytes_per_sample: int = 2) -> AudioFit`
    — `ValueError` se vazio.
  - `parse_framemd5(path: str) -> tuple[list[float], list[int]]`
  - `aligned_audio_filter(fit: AudioFit, ancora_pts: float, sr: int = 48000) -> str`
  - `extract_aligned_audio(raw_mkv: str, out_wav: str) -> tuple[VideoInfo, AudioFit]` — grava `out_wav` mono
    48 kHz `pcm_s16le` via `<out>.part.wav` + `os.replace`; `ProcError` com mensagem PT se faltar vídeo/áudio,
    se o áudio não for PCM16 ou se o ffmpeg falhar (o `out_wav` antigo fica intacto, o `.part.wav` é apagado).
  - Constantes: `ASYNC = "aresample=async=1:min_hard_comp=0.03:first_pts=0"`, `GAP_S = 0.030`, `OUT_SR = 48000`.
  - **Convenção de tempo (a Task 5 depende dela):** a amostra 0 do `audio.wav` é o instante do **2º pacote de
    vídeo** (`ancora_pts`), que é exatamente o frame 0 do render com `trim=start_frame=1,setpts=PTS-STARTPTS`.
    Um evento no pacote de vídeo de pts `P` cai no WAV em `P - ancora_pts`: na tomada sintética CFR com flash no
    frame 90, o bip fica em `(90-1)/30 = 2,967 s`, não em 3,000 s.
  - `fit.gaps > 0` = houve buraco no áudio: quem chama (GUI, Task 11) deve registrar um aviso no log.

- [ ] **Step 1: Copiar as tabelas reais do mic e escrever o teste das contas puras (falha)**

```bash
mkdir -p tests/fixtures
cp docs/superpowers/probes/2026-09-26/deriva-mic/*.framemd5 tests/fixtures/
```

`tests/test_timeline.py`:

```python
import os
import random
import unittest

from studio import timeline
from studio.timeline import AudioFit

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
GENERALPLUS = os.path.join(FIXTURES, "drift_usb-Generalplus_Usb_Audio_Device.framemd5")
ME6S = os.path.join(FIXTURES, "drift_usb-ME6S_c1_USB_AUDIO.framemd5")
SR = 48000


def drift_ppm(fit: AudioFit, sr: int = SR) -> float:
    # >0: o relogio do sistema anda mais rapido que as amostras (mic lento)
    return (sr / fit.taxa_real - 1) * 1e6


def packets(duration_s: float, start: float = 100.0, pkt_samples: int = 2400, rate: float = SR,
            jitter_s: float = 0.0005, seed: int = 1) -> tuple[list[float], list[int]]:
    # pts ideais de um mic a `rate` amostras por segundo real, com ruido de +-jitter_s
    rnd = random.Random(seed)
    n = int(duration_s * SR / pkt_samples)
    pts = [start + i * pkt_samples / rate + rnd.uniform(-jitter_s, jitter_s) for i in range(n)]
    return pts, [pkt_samples * 2] * n


class ParseFramemd5Test(unittest.TestCase):
    def test_reads_real_table(self):
        pts, sizes = timeline.parse_framemd5(GENERALPLUS)
        self.assertEqual(len(pts), 4798)
        self.assertEqual(len(sizes), 4798)
        self.assertEqual(set(sizes), {4800})
        self.assertAlmostEqual(pts[0], 1790391080.296582, places=5)
        self.assertAlmostEqual(pts[1] - pts[0], 0.05, places=6)


class RealDriftTest(unittest.TestCase):
    def test_generalplus(self):
        fit = timeline.fit_audio_clock(*timeline.parse_framemd5(GENERALPLUS))
        self.assertAlmostEqual(drift_ppm(fit), 48.6, delta=3)
        self.assertEqual(fit.gaps, 0)
        self.assertLess(fit.residuo_ms, 2.0)
        self.assertAlmostEqual(fit.duracao, 239.9, delta=0.1)

    def test_me6s(self):
        fit = timeline.fit_audio_clock(*timeline.parse_framemd5(ME6S))
        self.assertAlmostEqual(drift_ppm(fit), -56.1, delta=3)
        self.assertEqual(fit.gaps, 0)
        self.assertLess(fit.residuo_ms, 2.0)
        self.assertAlmostEqual(fit.duracao, 240.0, delta=0.1)

    def test_start_comes_from_steady_line_not_first_packet(self):
        # sonda deriva-mic: 1o pacote -10,6 ms (Generalplus) e +3,2 ms (ME6S) fora da reta estavel
        pts, sizes = timeline.parse_framemd5(GENERALPLUS)
        fit = timeline.fit_audio_clock(pts, sizes)
        self.assertAlmostEqual((fit.inicio - pts[0]) * 1000, 10.6, delta=3)
        pts, sizes = timeline.parse_framemd5(ME6S)
        fit = timeline.fit_audio_clock(pts, sizes)
        self.assertAlmostEqual((fit.inicio - pts[0]) * 1000, -3.2, delta=3)


class FitSyntheticPacketsTest(unittest.TestCase):
    def test_clean_clock(self):
        pts, sizes = packets(10.0)
        fit = timeline.fit_audio_clock(pts, sizes)
        self.assertAlmostEqual(fit.inicio, 100.0, delta=0.001)
        self.assertAlmostEqual(fit.taxa_real, SR, delta=2)
        self.assertEqual(fit.gaps, 0)
        self.assertLess(fit.residuo_ms, 0.5)
        self.assertAlmostEqual(fit.duracao, 10.0, places=6)

    def test_first_packet_late_80ms(self):
        pts, sizes = packets(10.0)
        pts[0] += 0.080
        fit = timeline.fit_audio_clock(pts, sizes)
        self.assertAlmostEqual(fit.inicio, 100.0, delta=0.002)
        self.assertEqual(fit.gaps, 0)

    def test_settling_start_is_ignored(self):
        # comeco "assentando": os 20 primeiros pacotes chegam 4 ms mais juntos (vies de 80 ms no 1o)
        pts, sizes = packets(10.0)
        pts = [p + max(0, 20 - i) * 0.004 for i, p in enumerate(pts)]
        fit = timeline.fit_audio_clock(pts, sizes)
        self.assertAlmostEqual(fit.inicio, 100.0, delta=0.002)
        self.assertEqual(fit.gaps, 0)

    def test_drift_recovered(self):
        pts, sizes = packets(60.0, rate=SR * (1 + 700e-6))
        fit = timeline.fit_audio_clock(pts, sizes)
        self.assertAlmostEqual(fit.taxa_real, SR * (1 + 700e-6), delta=1)
        self.assertAlmostEqual(fit.inicio, 100.0, delta=0.001)

    def test_gap_is_counted_and_does_not_bend_the_line(self):
        pts, sizes = packets(10.0)
        pts = [p + (0.5 if i >= 100 else 0.0) for i, p in enumerate(pts)]   # buraco de 0,5 s em t=5 s
        fit = timeline.fit_audio_clock(pts, sizes)
        self.assertEqual(fit.gaps, 1)
        self.assertAlmostEqual(fit.inicio, 100.0, delta=0.002)
        self.assertAlmostEqual(fit.taxa_real, SR, delta=10)
        self.assertLess(fit.residuo_ms, 1.0)

    def test_short_take_uses_packets_after_half_second(self):
        # 3 s: a janela de 2 s sobraria so 1 s; usa > 0,5 s e ignora o vies do comeco
        pts, sizes = packets(3.0)
        pts[0] += 0.060
        pts[1] += 0.030
        fit = timeline.fit_audio_clock(pts, sizes)
        self.assertAlmostEqual(fit.inicio, 100.0, delta=0.002)
        self.assertEqual(fit.gaps, 0)

    def test_very_short_take_uses_all_packets(self):
        pts, sizes = packets(0.5)
        self.assertEqual(len(pts), 10)
        fit = timeline.fit_audio_clock(pts, sizes)
        self.assertAlmostEqual(fit.inicio, 100.0, delta=0.002)
        self.assertAlmostEqual(fit.duracao, 0.5, places=6)

    def test_implausible_slope_keeps_nominal_rate(self):
        # tomada curtissima ainda assentando: inclinacao absurda nao vira asetrate
        pts = [100.0 + i * 0.047 for i in range(12)]
        fit = timeline.fit_audio_clock(pts, [4800] * 12)
        self.assertEqual(fit.taxa_real, SR)

    def test_single_packet(self):
        fit = timeline.fit_audio_clock([7.25], [4800])
        self.assertEqual(fit.inicio, 7.25)
        self.assertEqual(fit.taxa_real, SR)
        self.assertEqual(fit.gaps, 0)
        self.assertAlmostEqual(fit.duracao, 0.05)

    def test_bytes_per_sample(self):
        pts, sizes = packets(10.0)
        fit = timeline.fit_audio_clock(pts, [s * 2 for s in sizes], bytes_per_sample=4)
        self.assertAlmostEqual(fit.taxa_real, SR, delta=2)
        self.assertAlmostEqual(fit.duracao, 10.0, places=6)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            timeline.fit_audio_clock([], [])


def make_fit(inicio=0.0, taxa_real=48000.0, gaps=0, residuo_ms=0.3, duracao=10.0) -> AudioFit:
    return AudioFit(inicio=inicio, taxa_real=taxa_real, gaps=gaps, residuo_ms=residuo_ms, duracao=duracao)


class AlignedAudioFilterTest(unittest.TestCase):
    def test_audio_late_gets_delay_in_samples(self):
        af = timeline.aligned_audio_filter(make_fit(inicio=0.4), 0.033)
        self.assertEqual(af, "asetpts=N/SR/TB,adelay=17616S:all=1,asetpts=N/SR/TB")

    def test_audio_early_gets_trim_in_samples(self):
        af = timeline.aligned_audio_filter(make_fit(inicio=0.0), 0.283)
        self.assertEqual(af, "asetpts=N/SR/TB,atrim=start_sample=13584,asetpts=N/SR/TB")

    def test_no_shift(self):
        self.assertEqual(timeline.aligned_audio_filter(make_fit(inicio=1.5), 1.5), "asetpts=N/SR/TB")

    def test_small_drift_is_ignored(self):
        # 100 ppm em 10 s = 1 ms < 5 ms
        af = timeline.aligned_audio_filter(make_fit(inicio=0.1, taxa_real=48004.8), 0.0)
        self.assertNotIn("asetrate", af)

    def test_drift_over_5ms_resamples_before_the_shift(self):
        # 56 ppm em 300 s = 16,8 ms
        af = timeline.aligned_audio_filter(make_fit(inicio=0.1, taxa_real=47997.3, duracao=300.0), 0.0)
        self.assertEqual(af, "asetpts=N/SR/TB,asetrate=47997,aresample=48000:resampler=soxr,"
                             "adelay=4800S:all=1,asetpts=N/SR/TB")

    def test_drift_threshold_edge(self):
        below = make_fit(taxa_real=48000 + 48000 * 0.0049 / 10, duracao=10.0)
        above = make_fit(taxa_real=48000 + 48000 * 0.0051 / 10, duracao=10.0)
        self.assertNotIn("asetrate", timeline.aligned_audio_filter(below, 0.0))
        self.assertIn("asetrate=48024,", timeline.aligned_audio_filter(above, 0.0))

    def test_gaps_switch_to_async_from_file_time_zero(self):
        # async posiciona pelos pts (amostra 0 = tempo 0 do arquivo): so falta cortar ate a ancora
        af = timeline.aligned_audio_filter(make_fit(inicio=0.4, taxa_real=47990.0, gaps=1, duracao=300.0), 0.033)
        self.assertEqual(af, "aresample=async=1:min_hard_comp=0.03:first_pts=0,"
                             "atrim=start_sample=1584,asetpts=N/SR/TB")

    def test_other_sample_rate(self):
        af = timeline.aligned_audio_filter(make_fit(inicio=0.5, taxa_real=44100.0), 0.0, sr=44100)
        self.assertEqual(af, "asetpts=N/SR/TB,adelay=22050S:all=1,asetpts=N/SR/TB")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_timeline -v`
Expected: ERROR com
`ImportError: cannot import name 'timeline' from 'studio' (.../studio/__init__.py)` e `FAILED (errors=1)`.

- [ ] **Step 3: Implementar o ajuste do relógio e o filtro (primeira versão de `studio/timeline.py`)**

`studio/timeline.py`:

```python
"""Linha do tempo da tomada: relogio do mic ajustado aos pts e filtro que alinha o audio a ancora do video."""

from dataclasses import dataclass

import numpy as np

GAP_S = 0.030            # salto de residuo acima disso = buraco no audio
FIT_FROM_S = 2.0         # ajuste usa pacotes depois de 2 s (o comeco ainda esta assentando)
SHORT_TAKE_S = 4.0
SHORT_FIT_FROM_S = 0.5   # tomadas < 4 s: pacotes depois de 0,5 s
MIN_FIT_PKTS = 5         # menos que isso: usa todos os pacotes
MAX_DRIFT = 1e-3         # inclinacao acima de 1000 ppm nao e deriva de mic: mantem a taxa nominal
DRIFT_FIX_S = 0.005      # deriva acumulada acima de 5 ms -> asetrate
ASYNC = "aresample=async=1:min_hard_comp=0.03:first_pts=0"


@dataclass
class VideoInfo:
    w: int
    h: int
    ancora_pts: float
    n_frames: int
    fps_medido: float


@dataclass
class AudioFit:
    inicio: float        # tempo real (pts) da amostra 0
    taxa_real: float     # amostras por segundo do relogio do sistema
    gaps: int
    residuo_ms: float
    duracao: float       # segundos de amostras


def parse_framemd5(path: str) -> tuple[list[float], list[int]]:
    # tabela do muxer framemd5: "#tb N: a/b" e linhas "stream, dts, pts, duration, size, hash"
    tbs: dict[int, float] = {}
    kinds: dict[int, str] = {}
    rows: list[tuple[int, int, int]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#tb "):
                idx, frac = line[4:].split(":", 1)
                num, den = frac.strip().split("/")
                tbs[int(idx)] = int(num) / int(den)
            elif line.startswith("#media_type "):
                idx, kind = line[12:].split(":", 1)
                kinds[int(idx)] = kind.strip()
            elif line.strip() and not line.startswith("#"):
                p = [x.strip() for x in line.split(",")]
                rows.append((int(p[0]), int(p[2]), int(p[4])))
    audio = [i for i, k in sorted(kinds.items()) if k == "audio"]
    stream = audio[0] if audio else 0
    tb = tbs.get(stream, 1.0)
    pts = [v * tb for s, v, _ in rows if s == stream]
    sizes = [n for s, _, n in rows if s == stream]
    return pts, sizes


def fit_audio_clock(pts: list[float], sizes: list[int], sr: int = 48000, bytes_per_sample: int = 2) -> AudioFit:
    # minimos quadrados: pts_i = inicio + s * amostras_acum_i / sr ; taxa_real = sr / s
    if not pts or len(pts) != len(sizes):
        raise ValueError("Sem pacotes de áudio para ajustar o relógio do microfone")
    p = np.asarray(pts, dtype=np.float64)
    n = np.asarray(sizes, dtype=np.int64) // bytes_per_sample
    x = np.concatenate(([0], np.cumsum(n)[:-1])) / sr
    duracao = float(n.sum()) / sr
    r = (p - p[0]) - x                     # >0: o relogio do sistema andou mais que as amostras
    m = x > (FIT_FROM_S if duracao >= SHORT_TAKE_S else SHORT_FIT_FROM_S)
    if m.sum() < MIN_FIT_PKTS:
        m = np.ones(len(p), dtype=bool)
    # buracos so dentro da janela do ajuste (antes dela e vies do comeco); tira os degraus antes de ajustar
    steps = np.diff(r)
    jump = (np.abs(steps) > GAP_S) & m[:-1] & m[1:]
    r = r - np.concatenate(([0.0], np.cumsum(np.where(jump, steps, 0.0))))
    b, a = 0.0, float(r[m].mean())
    if m.sum() >= 2 and np.ptp(x[m]) > 0:
        b, a = (float(v) for v in np.polyfit(x[m], r[m], 1))
        if abs(b) > MAX_DRIFT:
            b, a = 0.0, float(r[m].mean())
    res = r[m] - (a + b * x[m])
    return AudioFit(inicio=round(float(p[0]) + a, 6), taxa_real=round(sr / (1.0 + b), 3), gaps=int(jump.sum()),
                    residuo_ms=round(float(res.std()) * 1000, 3), duracao=round(duracao, 6))


def aligned_audio_filter(fit: AudioFit, ancora_pts: float, sr: int = 48000) -> str:
    # saida: amostra 0 = instante da ancora (2o frame do video), relogio do sistema
    if fit.gaps > 0:
        chain = [ASYNC]             # async poe cada pacote no seu pts; amostra 0 = tempo 0 do arquivo
        start = 0.0
    else:
        chain = ["asetpts=N/SR/TB"]
        start = fit.inicio
        rate = round(fit.taxa_real)
        if abs(fit.taxa_real - sr) * fit.duracao / sr > DRIFT_FIX_S and rate != sr:
            chain += [f"asetrate={rate}", f"aresample={sr}:resampler=soxr"]
    delta = start - ancora_pts
    n = round(abs(delta) * sr)
    if n and delta > 0:
        chain.append(f"adelay={n}S:all=1")
    elif n:
        chain.append(f"atrim=start_sample={n}")
    if len(chain) > 1:
        chain.append("asetpts=N/SR/TB")
    return ",".join(chain)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_timeline -v`
Expected: `Ran 23 tests in 0.057s` / `OK`. Valores reais que os testes conferem: Generalplus
`taxa_real=47997.664` (+48,67 ppm, `inicio - pts[0] = +10,58 ms`, `residuo_ms=1.129`); ME6S `taxa_real=48002.752`
(−57,33 ppm, `inicio - pts[0] = −2,97 ms`, `residuo_ms=0.78`).

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/drift_usb-Generalplus_Usb_Audio_Device.framemd5 tests/fixtures/drift_usb-ME6S_c1_USB_AUDIO.framemd5 tests/test_timeline.py studio/timeline.py
git commit -m "feat(studio): timeline com ajuste do relogio do mic e filtro de alinhamento

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: Escrever o teste com mídia sintética (falha)**

`tests/test_timeline_media.py`:

```python
import dataclasses
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import soundfile as sf

from studio import timeline
from studio.procs import ProcError
from tests import helpers

FPS = 30


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
                   stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=120)


def mjpeg_video(path: str, duration_s: float, flash_frame: int) -> None:
    ffmpeg("-f", "lavfi", "-i", f"testsrc2=s=640x360:r={FPS}:d={duration_s}",
           "-vf", f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='eq(n,{flash_frame})'",
           "-fps_mode", "passthrough", "-c:v", "mjpeg", "-q:v", "3", "-pix_fmt", "yuvj422p", path)


def beep_expr(beep_t: float) -> str:
    return f"aevalsrc='0.01*(2*random(0)-1)+0.8*sin(2*PI*1000*t)*between(t,{beep_t:.6f},{beep_t + 0.05:.6f})'"


def make_gap_take(path: str) -> None:
    # 8 s; flash no frame 150 (5,0 s) e bip em 5,0 s; o aselect descarta ~0,5 s de audio em 3,0 s
    # mantendo os pts (buraco de timestamp igual a um xrun do mic)
    v = path + ".v.mkv"
    mjpeg_video(v, 8.0, 150)
    ffmpeg("-i", v, "-f", "lavfi", "-i", f"{beep_expr(5.0)}:s=48000:c=mono:d=8",
           "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-af", "aselect='not(between(t,3,3.5))'",
           "-c:a", "pcm_s16le", "-f", "matroska", path)
    os.remove(v)


def make_drift_take(path: str, ppm: float) -> None:
    # mic com taxa real 48000*(1+ppm): o bip do instante 11,0 s cai na amostra 11,0*taxa_real e os pts
    # dos pacotes andam no relogio do sistema (-itsscale). Audio comeca 0,4 s depois do video.
    e = ppm * 1e-6
    v, a = path + ".v.mkv", path + ".a.wav"
    mjpeg_video(v, 12.0, 330)
    ffmpeg("-f", "lavfi", "-i", f"{beep_expr(11.0 * (1 + e) - 0.4)}:s=48000:c=mono:d=11.7",
           "-c:a", "pcm_s16le", a)
    ffmpeg("-i", v, "-itsscale", f"{1 / (1 + e):.12f}", "-itsoffset", "0.4", "-i", a,
           "-map", "0:v", "-map", "1:a", "-c", "copy", "-f", "matroska", path)
    os.remove(v)
    os.remove(a)


def flash_pts(path: str) -> float:
    return timeline.read_packets(path, "v")[helpers.flash_frame_index(path)][0]


class VideoInfoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        cls.cfr = helpers.make_synthetic_take(os.path.join(d, "cfr.mkv"), audio_offset_s=0.40, size="640x360")
        cls.vfr = helpers.make_synthetic_take(os.path.join(d, "vfr.mkv"), audio_offset_s=0.40, shift_s=2.0,
                                              vfr=True, size="640x360")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_read_packets(self):
        v = timeline.read_packets(self.cfr, "v")
        self.assertEqual(len(v), 300)
        self.assertEqual(v[0][0], 0.0)
        self.assertAlmostEqual(v[1][0], 1 / FPS, delta=0.001)
        self.assertAlmostEqual(v[-1][1], 1 / FPS, delta=0.001)
        self.assertTrue(all(size > 0 for _, _, size in v))
        a = timeline.read_packets(self.cfr, "a")
        self.assertAlmostEqual(a[0][0], 0.40, places=3)
        self.assertEqual(sum(size for _, _, size in a) // 2, round(9.6 * 48000))

    def test_read_packets_rejects_unknown_stream(self):
        with self.assertRaises(ValueError):
            timeline.read_packets(self.cfr, "s")

    def test_cfr_anchor_is_second_packet(self):
        vi = timeline.video_info(self.cfr)
        pk = timeline.read_packets(self.cfr, "v")
        self.assertEqual((vi.w, vi.h), (640, 360))
        self.assertEqual(vi.ancora_pts, pk[1][0])
        self.assertEqual(vi.n_frames, round((pk[-1][0] + pk[-1][1] - pk[1][0]) * FPS))
        self.assertEqual(vi.n_frames, 299)           # frames 1..299 (o 1o fica de fora)
        self.assertAlmostEqual(vi.fps_medido, 30.0, delta=0.5)

    def test_vfr(self):
        vi = timeline.video_info(self.vfr)
        pk = timeline.read_packets(self.vfr, "v")
        self.assertEqual(vi.ancora_pts, pk[1][0])
        self.assertAlmostEqual(vi.ancora_pts, 2.0 + 2 / FPS, delta=0.001)   # frame 1 caiu no select
        self.assertEqual(vi.n_frames, round((pk[-1][0] + pk[-1][1] - pk[1][0]) * FPS))
        self.assertTrue(14.0 <= vi.fps_medido <= 17.0, vi.fps_medido)

    def test_fps_out(self):
        pk = timeline.read_packets(self.cfr, "v")
        vi = timeline.video_info(self.cfr, fps_out=15)
        self.assertEqual(vi.n_frames, round((pk[-1][0] + pk[-1][1] - pk[1][0]) * 15))

    def test_audio_only_file_has_no_video(self):
        wav = os.path.join(self.tmp.name, "only.wav")
        ffmpeg("-f", "lavfi", "-i", "sine=f=440:d=1", "-c:a", "pcm_s16le", wav)
        with self.assertRaises(ProcError) as cm:
            timeline.video_info(wav)
        self.assertIn("não tem vídeo", cm.exception.message)


class ExtractAlignedAudioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name

        def take(name, **kw):
            return helpers.make_synthetic_take(os.path.join(d, name), size="640x360", **kw)

        cls.late = take("late.mkv", audio_offset_s=0.40)
        cls.early = take("early.mkv", audio_offset_s=-0.25)
        cls.shifted = take("shift.mkv", audio_offset_s=0.40, shift_s=5.0)
        cls.vfr = take("vfr.mkv", audio_offset_s=0.40, shift_s=2.0, vfr=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def extract(self, raw):
        out = os.path.join(self.tmp.name, os.path.basename(raw) + ".audio.wav")
        vi, fit = timeline.extract_aligned_audio(raw, out)
        return out, vi, fit

    def assert_beep_on_flash(self, raw):
        # t=0 do WAV = ancora (2o pacote de video), igual ao render (trim=start_frame=1)
        out, vi, fit = self.extract(raw)
        expected = flash_pts(raw) - vi.ancora_pts
        self.assertAlmostEqual(helpers.beep_onset_s(out), expected, delta=0.001)
        return out, vi, fit, expected

    def test_audio_late(self):
        out, vi, fit, expected = self.assert_beep_on_flash(self.late)
        self.assertAlmostEqual(expected, (90 - 1) / FPS, delta=0.001)
        self.assertAlmostEqual(fit.inicio, 0.40, delta=0.001)
        self.assertEqual(fit.gaps, 0)
        self.assertNotIn("asetrate", timeline.aligned_audio_filter(fit, vi.ancora_pts))

    def test_audio_early(self):
        _, _, fit, expected = self.assert_beep_on_flash(self.early)
        self.assertAlmostEqual(expected, (90 - 1) / FPS, delta=0.001)
        self.assertAlmostEqual(fit.inicio, 0.0, delta=0.001)

    def test_shifted_5s(self):
        _, vi, _, expected = self.assert_beep_on_flash(self.shifted)
        self.assertAlmostEqual(vi.ancora_pts, 5.0 + 1 / FPS, delta=0.001)
        self.assertAlmostEqual(expected, (90 - 1) / FPS, delta=0.001)

    def test_vfr(self):
        _, _, _, expected = self.assert_beep_on_flash(self.vfr)
        self.assertAlmostEqual(expected, (90 - 2) / FPS, delta=0.001)   # 2o pacote = frame 2

    def test_output_is_mono_48k_pcm16_without_part(self):
        out, _, _ = self.extract(self.late)
        info = sf.info(out)
        self.assertEqual((info.samplerate, info.channels, info.subtype), (48000, 1, "PCM_16"))
        self.assertEqual([f for f in os.listdir(self.tmp.name) if ".part" in f], [])

    def test_gap_uses_async_and_keeps_audio_after_the_hole(self):
        raw = os.path.join(self.tmp.name, "gap.mkv")
        make_gap_take(raw)
        out, vi, fit, expected = self.assert_beep_on_flash(raw)
        self.assertEqual(fit.gaps, 1)
        self.assertTrue(timeline.aligned_audio_filter(fit, vi.ancora_pts).startswith(timeline.ASYNC))
        self.assertAlmostEqual(expected, 5.0 - 1 / FPS, delta=0.001)
        # controle: sem o async o bip cairia ~0,5 s antes
        plain = os.path.join(self.tmp.name, "gap_plain.wav")
        n = round(vi.ancora_pts * 48000)
        ffmpeg("-i", raw, "-map", "0:a:0", "-af", f"asetpts=N/SR/TB,atrim=start_sample={n},asetpts=N/SR/TB",
               "-c:a", "pcm_s16le", plain)
        self.assertLess(helpers.beep_onset_s(plain), expected - 0.4)

    def test_drift_is_corrected(self):
        raw = os.path.join(self.tmp.name, "drift.mkv")
        make_drift_take(raw, 700.0)
        out, vi, fit, expected = self.assert_beep_on_flash(raw)
        self.assertAlmostEqual(fit.taxa_real, 48000 * (1 + 700e-6), delta=2)
        self.assertIn("asetrate=48034,aresample=48000:resampler=soxr",
                      timeline.aligned_audio_filter(fit, vi.ancora_pts))
        # controle: sem corrigir a taxa o bip erra mais de 5 ms
        nominal = dataclasses.replace(fit, taxa_real=48000.0)
        plain = os.path.join(self.tmp.name, "drift_plain.wav")
        ffmpeg("-i", raw, "-map", "0:a:0", "-af", timeline.aligned_audio_filter(nominal, vi.ancora_pts),
               "-c:a", "pcm_s16le", plain)
        self.assertGreater(abs(helpers.beep_onset_s(plain) - expected), 0.005)

    def test_ffmpeg_failure_keeps_old_output_and_leaves_no_part(self):
        out = os.path.join(self.tmp.name, "keep.wav")
        with open(out, "wb") as f:
            f.write(b"old")
        with mock.patch.object(timeline, "aligned_audio_filter", return_value="filtro_que_nao_existe"):
            with self.assertRaises(ProcError) as cm:
                timeline.extract_aligned_audio(self.late, out)
        self.assertIn("áudio", cm.exception.message)
        with open(out, "rb") as f:
            self.assertEqual(f.read(), b"old")
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "keep.part.wav")))

    def test_video_without_audio(self):
        raw = os.path.join(self.tmp.name, "mute.mkv")
        ffmpeg("-i", self.late, "-map", "0:v", "-c", "copy", raw)
        with self.assertRaises(ProcError) as cm:
            timeline.extract_aligned_audio(raw, os.path.join(self.tmp.name, "mute.wav"))
        self.assertIn("não tem áudio", cm.exception.message)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 7: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_timeline_media -v`
Expected: 15 ERROR (`AttributeError: module 'studio.timeline' has no attribute 'extract_aligned_audio'` ×9,
`... has no attribute 'read_packets'` ×3, `... has no attribute 'video_info'. Did you mean: 'VideoInfo'?` ×3) e
`FAILED (errors=15)`.

- [ ] **Step 8: Implementar pacotes, âncora e extração (arquivo `studio/timeline.py` COMPLETO final)**

Substitua o arquivo inteiro pela versão abaixo.

`studio/timeline.py` (arquivo completo final):

```python
"""Linha do tempo da tomada: relogio do mic ajustado aos pts e filtro que alinha o audio a ancora do video."""

import os
import subprocess
from dataclasses import dataclass

import numpy as np

from studio import procs

GAP_S = 0.030            # salto de residuo acima disso = buraco no audio
FIT_FROM_S = 2.0         # ajuste usa pacotes depois de 2 s (o comeco ainda esta assentando)
SHORT_TAKE_S = 4.0
SHORT_FIT_FROM_S = 0.5   # tomadas < 4 s: pacotes depois de 0,5 s
MIN_FIT_PKTS = 5         # menos que isso: usa todos os pacotes
MAX_DRIFT = 1e-3         # inclinacao acima de 1000 ppm nao e deriva de mic: mantem a taxa nominal
DRIFT_FIX_S = 0.005      # deriva acumulada acima de 5 ms -> asetrate
ASYNC = "aresample=async=1:min_hard_comp=0.03:first_pts=0"
OUT_SR = 48000
EXTRACT_TIMEOUT_S = 60.0


@dataclass
class VideoInfo:
    w: int
    h: int
    ancora_pts: float
    n_frames: int
    fps_medido: float


@dataclass
class AudioFit:
    inicio: float        # tempo real (pts) da amostra 0
    taxa_real: float     # amostras por segundo do relogio do sistema
    gaps: int
    residuo_ms: float
    duracao: float       # segundos de amostras


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_packets(path: str, stream: str) -> list[tuple[float, float, int]]:
    # (pts_time, duration_time, size) na ordem do arquivo, sem decodificar
    if stream not in ("v", "a"):
        raise ValueError(f"stream inválido: {stream!r}")
    data = procs.ffprobe_json(path, "-select_streams", f"{stream}:0",
                              "-show_entries", "packet=pts_time,duration_time,size")
    out = []
    for pk in data.get("packets", []):
        pts = _num(pk.get("pts_time"))
        if pts is None:
            continue
        out.append((pts, _num(pk.get("duration_time")) or 0.0, int(pk.get("size") or 0)))
    return out


def _video_info(path: str, info: dict, fps_out: int) -> VideoInfo:
    name = os.path.basename(path)
    v = info["video"]
    if not v:
        raise procs.ProcError(f"{name} não tem vídeo")
    pk = read_packets(path, "v")
    if not pk:
        raise procs.ProcError(f"O vídeo de {name} não tem frames")
    # 1o frame MJPEG costuma vir truncado: a ancora e o 2o pacote
    ancora = pk[1][0] if len(pk) > 1 else pk[0][0]
    last, dur, _ = pk[-1]
    if dur <= 0:
        dur = 1.0 / fps_out
    n_frames = max(1, round((last + dur - ancora) * fps_out))
    fps = (len(pk) - 2) / (last - ancora) if len(pk) > 2 and last > ancora else 0.0
    return VideoInfo(w=int(v["width"]), h=int(v["height"]), ancora_pts=ancora, n_frames=n_frames,
                     fps_medido=round(fps, 2))


def video_info(path: str, fps_out: int = 30) -> VideoInfo:
    return _video_info(path, procs.media_info(path), fps_out)


def parse_framemd5(path: str) -> tuple[list[float], list[int]]:
    # tabela do muxer framemd5: "#tb N: a/b" e linhas "stream, dts, pts, duration, size, hash"
    tbs: dict[int, float] = {}
    kinds: dict[int, str] = {}
    rows: list[tuple[int, int, int]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#tb "):
                idx, frac = line[4:].split(":", 1)
                num, den = frac.strip().split("/")
                tbs[int(idx)] = int(num) / int(den)
            elif line.startswith("#media_type "):
                idx, kind = line[12:].split(":", 1)
                kinds[int(idx)] = kind.strip()
            elif line.strip() and not line.startswith("#"):
                p = [x.strip() for x in line.split(",")]
                rows.append((int(p[0]), int(p[2]), int(p[4])))
    audio = [i for i, k in sorted(kinds.items()) if k == "audio"]
    stream = audio[0] if audio else 0
    tb = tbs.get(stream, 1.0)
    pts = [v * tb for s, v, _ in rows if s == stream]
    sizes = [n for s, _, n in rows if s == stream]
    return pts, sizes


def fit_audio_clock(pts: list[float], sizes: list[int], sr: int = 48000, bytes_per_sample: int = 2) -> AudioFit:
    # minimos quadrados: pts_i = inicio + s * amostras_acum_i / sr ; taxa_real = sr / s
    if not pts or len(pts) != len(sizes):
        raise ValueError("Sem pacotes de áudio para ajustar o relógio do microfone")
    p = np.asarray(pts, dtype=np.float64)
    n = np.asarray(sizes, dtype=np.int64) // bytes_per_sample
    x = np.concatenate(([0], np.cumsum(n)[:-1])) / sr
    duracao = float(n.sum()) / sr
    r = (p - p[0]) - x                     # >0: o relogio do sistema andou mais que as amostras
    m = x > (FIT_FROM_S if duracao >= SHORT_TAKE_S else SHORT_FIT_FROM_S)
    if m.sum() < MIN_FIT_PKTS:
        m = np.ones(len(p), dtype=bool)
    # buracos so dentro da janela do ajuste (antes dela e vies do comeco); tira os degraus antes de ajustar
    steps = np.diff(r)
    jump = (np.abs(steps) > GAP_S) & m[:-1] & m[1:]
    r = r - np.concatenate(([0.0], np.cumsum(np.where(jump, steps, 0.0))))
    b, a = 0.0, float(r[m].mean())
    if m.sum() >= 2 and np.ptp(x[m]) > 0:
        b, a = (float(v) for v in np.polyfit(x[m], r[m], 1))
        if abs(b) > MAX_DRIFT:
            b, a = 0.0, float(r[m].mean())
    res = r[m] - (a + b * x[m])
    return AudioFit(inicio=round(float(p[0]) + a, 6), taxa_real=round(sr / (1.0 + b), 3), gaps=int(jump.sum()),
                    residuo_ms=round(float(res.std()) * 1000, 3), duracao=round(duracao, 6))


def aligned_audio_filter(fit: AudioFit, ancora_pts: float, sr: int = 48000) -> str:
    # saida: amostra 0 = instante da ancora (2o frame do video), relogio do sistema
    if fit.gaps > 0:
        chain = [ASYNC]             # async poe cada pacote no seu pts; amostra 0 = tempo 0 do arquivo
        start = 0.0
    else:
        chain = ["asetpts=N/SR/TB"]
        start = fit.inicio
        rate = round(fit.taxa_real)
        if abs(fit.taxa_real - sr) * fit.duracao / sr > DRIFT_FIX_S and rate != sr:
            chain += [f"asetrate={rate}", f"aresample={sr}:resampler=soxr"]
    delta = start - ancora_pts
    n = round(abs(delta) * sr)
    if n and delta > 0:
        chain.append(f"adelay={n}S:all=1")
    elif n:
        chain.append(f"atrim=start_sample={n}")
    if len(chain) > 1:
        chain.append("asetpts=N/SR/TB")
    return ",".join(chain)


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def extract_aligned_audio(raw_mkv: str, out_wav: str) -> tuple[VideoInfo, AudioFit]:
    # audio.wav mono 48k PCM16 com t=0 no 2o frame do video; grava .part.wav e troca com os.replace
    name = os.path.basename(raw_mkv)
    info = procs.media_info(raw_mkv)
    vi = _video_info(raw_mkv, info, 30)
    a = info["audio"]
    if not a:
        raise procs.ProcError(f"{name} não tem áudio")
    codec = str(a.get("codec_name") or "")
    if not codec.startswith("pcm_s16"):
        raise procs.ProcError(f"Áudio de {name} em formato inesperado ({codec})")
    sr = int(a["sample_rate"])
    channels = int(a.get("channels") or 1)
    pk = read_packets(raw_mkv, "a")
    if not pk:
        raise procs.ProcError(f"{name} não tem áudio")
    fit = fit_audio_clock([p[0] for p in pk], [p[2] for p in pk], sr=sr, bytes_per_sample=2 * channels)
    af = aligned_audio_filter(fit, vi.ancora_pts, sr)
    part = os.path.splitext(out_wav)[0] + ".part.wav"
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", raw_mkv,
           "-map", "0:a:0", "-af", af, "-ac", "1", "-ar", str(OUT_SR), "-c:a", "pcm_s16le", "-f", "wav", part]
    try:
        r = procs.run(cmd, timeout=EXTRACT_TIMEOUT_S + fit.duracao)
    except subprocess.TimeoutExpired:
        _remove(part)
        raise procs.ProcError("A extração do áudio demorou demais") from None
    if r.returncode != 0:
        _remove(part)
        raise procs.ProcError("Não foi possível extrair o áudio alinhado", r.returncode, r.stderr.strip()[-2000:])
    os.replace(part, out_wav)
    return vi, fit
```

- [ ] **Step 9: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_timeline_media -v`
Expected: `Ran 15 tests in 6.791s` / `OK`.

Run: `./run_tests.sh`
Expected: `Ran 162 tests` e `OK (skipped=2)`, contando as Tasks 1–4 (com as correções das anteriores). Depois confira que não sobrou ffmpeg: `pgrep -a ffmpeg` sem saída.

Erro medido (bip no WAV − `flash_pts + ancora`) em cada caso: áudio +0,40 s, −0,25 s, deslocado 5 s e VFR:
+0,083 ms; buraco de 0,5 s (caminho async): +0,063 ms; deriva de 700 ppm corrigida: −0,104 ms (sem corrigir:
~7,4 ms).

- [ ] **Step 10: Commit**

```bash
git add studio/timeline.py tests/test_timeline_media.py
git commit -m "feat(studio): timeline le pacotes, ancora no 2o frame e extrai audio.wav alinhado

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

**Notas para o executor**

- **Âncora no 2º pacote:** o t=0 do `audio.wav` é o frame 1 do MKV, não o frame 0. Por isso os testes comparam
  o bip com `flash_pts - ancora_pts` (CFR: `(90-1)/30`; na tomada VFR sintética o 2º pacote é o frame 2, porque o
  `select` descarta os ímpares, então dá `(90-2)/30`). Não "corrija" para 3,000 s: o render da Task 5 corta
  `trim=start_frame=1` e fica coerente com isso.
- **Buracos só dentro da janela do ajuste.** Se os saltos > 30 ms fossem contados desde o 1º pacote, um 1º pacote
  80 ms atrasado viraria "buraco", e a correção de degrau puxaria o `inicio` 80 ms para o lugar errado. O viés real
  do começo em A/V (−13 a +91 ms) vem espalhado nos primeiros pacotes (salto máximo de 55 ms entre pacotes de
  50 ms nas sondas), então nunca passa do limiar dentro da janela.
- **Caminho async (`gaps > 0`):** o `aresample=async=1:min_hard_comp=0.03:first_pts=0` põe cada pacote no seu
  pts e a amostra 0 no tempo 0 do **arquivo**; por isso o corte é `atrim=start_sample=round(ancora*48000)` (início
  = 0.0), e não `inicio - ancora`. Usar o `inicio` aí deslocaria todo o áudio pelo viés do 1º pacote. Nesse caminho
  não há `asetrate` (a deriva de ≤ 17 ms em 5 min fica abaixo do `min_hard_comp` de 30 ms). Com
  `asetpts=N/SR/TB` o bip depois de um buraco de 0,5 s cai ~0,5 s antes (o teste confere isso como controle).
- **`MAX_DRIFT = 1e-3`:** tomada curtíssima ainda assentando dá inclinação absurda (ex.: 12 pacotes a cada 47 ms =
  −6 %); acima de 1000 ppm o ajuste fica com a taxa nominal e só estima o início, em vez de virar um `asetrate`
  que muda o tom da voz.
- A janela "> 2 s" dá −57,3 ppm no ME6S (a sonda usou "> 1 s" e deu −56,1); os dois ficam dentro de ±3 ppm.
- `atrim=start_sample` e `adelay=...S` contam **amostras**, não pts, por isso funcionam depois de
  `asetrate`/`aresample`. O `asetpts=N/SR/TB` final só normaliza os pts.
- No ffmpeg 6.1.1 o `-itsscale` também escala o `-itsoffset` da mesma entrada (o áudio da tomada de deriva começa em
  `0.4/(1+e)`); o bip foi posto em `11.0*(1+e) - 0.4` e o teste compara com `flash_pts - ancora`, então o erro de
  construção é ≤ 0,3 ms em qualquer ordem.
- O `aselect` do teste do buraco descarta quadros inteiros de 1024 amostras: o buraco real é 0,512 s, não 0,500 s.
  Não importa, porque o async segue os pts.
- O MKV tem base de tempo de 1 ms: os pts são quantizados e o `residuo_ms` das tomadas sintéticas fica em ~0,27 ms.
- Tudo o que sai do numpy é convertido para `float`/`int` do Python antes de ir para o dataclass: `np.int64` não
  passa no `json.dumps` do `take.json`.
- O arquivo temporário é `audio.part.wav` (sufixo `.wav` no fim, e `-f wav` explícito); ele casa com `*.wav` e
  `*.part.*` do `.gitignore`.

---

### Task 5: Render final (`render.py`: MP4 com voz convertida, marca d'água e verificação *fail-closed*)

**Files:**
- Create: `studio/render.py`
- Test: `tests/test_render.py` (contas puras: filtro, argv do ffmpeg, timeout)
- Test: `tests/test_render_media.py` (mídia sintética: alinhamento, durações, tags, `av_offset_ms`, `verify_render`,
  fallback x264, cancelamento, timeout, *fail-closed*)

**Interfaces:**
- Consumes:
  - Task 1 (`studio.config`): `VIDEOS_DIR`, `Modelo`, `get_modelo(key: str) -> Modelo`,
    `metadata_tags(m: Modelo) -> dict[str, str]` (`ValueError` se o modelo não tem aviso)
  - Task 1 (`studio.procs`): `spawn(argv: list[str], **popen_kwargs) -> subprocess.Popen` (prefixa
    `setpriv --pdeathsig TERM --`, `stdin=DEVNULL` por padrão), `run(argv: list[str], timeout: float | None = None,
    **kw) -> subprocess.CompletedProcess` (aceita `text=False`), `ffprobe_json(path: str, *args: str,
    timeout: float = 60.0) -> dict`, `media_info(path: str) -> dict`, `tail(path: str, n: int = 15) -> str`,
    `ProcError` (`.message` em PT), `MSG_DISK_FULL`, `os_error_message(e: OSError) -> str` (Task 1, Step 38)
  - Task 1 (`studio.takes`): `Take` (`.id`, `.dir`, `.modo`, `.video`, `.raw_path`, `.audio_path`,
    `.path(name)`), `final_video_name(take_id: str, model_key: str) -> str`; nos testes
    `new_take(modo, mic, camera="", rec_dir=..., now=None) -> Take`
  - Task 1 (`tests.helpers`): `make_synthetic_take(path, audio_offset_s=0.0, duration_s=10.0, flash_frame=90,
    shift_s=0.0, vfr=False, size="1280x720") -> str`, `beep_onset_s(wav_or_media) -> float`,
    `flash_frame_index(media) -> int`
  - Task 3 (`studio.watermark`): `watermark_path(take_dir: str, w: int, h: int, modelo: Modelo) -> str`,
    `text_pixel_mask(img) -> np.ndarray` (aceita `Image` ou caminho), `band_rect(w: int, h: int) ->
    tuple[int, int, int, int]`
  - Task 4 (`studio.timeline`): `VideoInfo(w, h, ancora_pts, n_frames, fps_medido)`,
    `video_info(path: str, fps_out: int = 30) -> VideoInfo`; nos testes
    `extract_aligned_audio(raw_mkv: str, out_wav: str) -> tuple[VideoInfo, AudioFit]` e
    `read_packets(path: str, stream: str) -> list[tuple[float, float, int]]`. **Convenção de tempo:** a
    amostra 0 do `audio.wav` (e do WAV convertido) é o 2º pacote de vídeo; o render corta `trim=start_frame=1`, então
    o frame `k` do MKV vira o frame `k-1` do MP4.
- Produces (`studio/render.py`; a GUI da Task 11 usa):
  - `class RenderError(Exception)` — `str(e)` é a mensagem PT para o usuário. Mensagens fixas:
    `"Render cancelado"`, `"O render demorou demais e foi interrompido"`,
    `"Falha ao gerar o vídeo (código <rc>): <última linha do render.log>"`,
    `"O vídeo gerado não passou na verificação: <problemas separados por '; '>"`,
    `"Marca d'água <W>x<H> não confere com o vídeo <W>x<H>"`, `"Esta tomada não tem vídeo"`,
    `"Áudio convertido não encontrado — converta a voz de novo"`,
    `"Modelo <label> sem texto de aviso: o vídeo não pode ser gerado"`, e a mensagem da `ValueError` de
    `watermark_path` (ex.: `"O texto da marca d'água não cabe num vídeo desse tamanho: '...'"`), repassada como
    `RenderError` antes de o ffmpeg rodar. Steps 13 e 18: `MSG_SHORT` (tomada com `n_frames < MIN_FRAMES`, antes do
    ffmpeg), `"Disco cheio — libere espaço"` (ENOSPC em qualquer ponto, inclusive no ffmpeg, reconhecido pelo
    "No space left on device" no `render.log`; aí o x264 não é tentado) e
    `"Erro ao acessar o disco (<nome>): <strerror>"` (outro `OSError`). O `render_final` nunca deixa escapar `OSError`
  - `offset_filter(av_offset_ms: int, sr: int = 48000) -> str` — `""` | `",adelay=<n>S:all=1"` (positivo = áudio
    mais tarde) | `",atrim=start_sample=<n>"`, com `n = round(|ms|·sr/1000)`
  - `build_filter(n_frames: int, av_offset_ms: int = 0) -> str` — o `-filter_complex` da spec 8.2; `ValueError` se
    `n_frames < 1`
  - `build_render_cmd(raw_mkv: str, conv_wav: str, wm_png: str, out_part: str, n_frames: int, modelo: Modelo,
    av_offset_ms: int = 0, encoder: str = "nvenc") -> list[str]` — `encoder` `"nvenc"` | `"x264"`; começa com
    `["nice", "-n", "10", "ffmpeg", "-nostdin", ...]` (sem `setpriv`: quem põe é o `procs.spawn`); `ValueError`
    para encoder desconhecido ou modelo sem aviso
  - `render_timeout(n_frames: int) -> float` — `5 · n_frames/30 + 60`
  - `verify_render(mp4: str, n_frames: int, wm_png: str) -> list[str]` — problemas em PT; `[]` = ok
  - `render_final(take: Take, modelo: Modelo, conv_wav: str, av_offset_ms: int = 0, videos_dir: str = VIDEOS_DIR,
    cancel: threading.Event | None = None) -> str` — devolve `videos_dir/<id>_<modelo>_IA.mp4`. Bloqueante: rodar
    numa thread de trabalho (o ffmpeg nasce e é esperado nela). **Não altera o `take.json`**: quem grava
    `status`/`saidas[key]` (`"mp4"` ou `"erro"`) é a thread principal da GUI (Task 11), a partir do retorno ou da
    `RenderError`.
  - Constantes: `FPS = 30`, `ENCODERS = ("nvenc", "x264")`, `ENCODER_FLAGS` (dict encoder → flags de vídeo),
    `PART_NAME = "render.part.mp4"`, `LOG_NAME = "render.log"`, `CANCEL_MSG = "Render cancelado"`,
    `TIMEOUT_FACTOR = 5`, `TIMEOUT_BASE_S = 60.0`, `MIN_FRAMES = 2`,
    `MSG_SHORT = "Gravação curta demais para gerar o vídeo"`, `DISK_FULL_HINT = "No space left on device"`.
  - Arquivos na pasta da tomada: `wm_<W>x<H>_<key>.png` (via `watermark_path`), `render.log` (stderr do ffmpeg,
    recriado a cada render, as duas tentativas em sequência) e `render.part.mp4` (só sobra quando a verificação
    falha; em falha do ffmpeg, cancelamento, timeout ou erro de disco ele é apagado).

- [ ] **Step 1: Escrever o teste das contas puras (falha)**

`tests/test_render.py`:

```python
import unittest

from studio import render
from studio.config import Modelo, get_modelo

SILVIO = get_modelo("silvio")

SPEC_FILTER_300 = (
    "[0:v]trim=start_frame=1,setpts=PTS-STARTPTS,fps=30,tpad=stop_mode=clone:stop=2,"
    "trim=end_frame=300,setpts=PTS-STARTPTS,format=yuv420p[v0];"
    "[2:v]format=yuva420p[wm];[v0][wm]overlay=0:0:format=yuv420,format=yuv420p[v];"
    "[1:a]aresample=48000:resampler=soxr,asetpts=N/SR/TB,"
    "apad=whole_len=480000,atrim=end_sample=480000,asetpts=N/SR/TB[a]"
)


class OffsetFilterTest(unittest.TestCase):
    def test_zero_is_empty(self):
        self.assertEqual(render.offset_filter(0), "")
        self.assertEqual(render.offset_filter(0.004), "")      # arredonda para 0 amostra

    def test_positive_delays_audio(self):
        self.assertEqual(render.offset_filter(100), ",adelay=4800S:all=1")
        self.assertEqual(render.offset_filter(10, sr=40000), ",adelay=400S:all=1")

    def test_negative_trims_audio(self):
        self.assertEqual(render.offset_filter(-250), ",atrim=start_sample=12000")


class BuildFilterTest(unittest.TestCase):
    def test_matches_spec_without_offset(self):
        self.assertEqual(render.build_filter(300), SPEC_FILTER_300)

    def test_offset_goes_before_apad(self):
        fc = render.build_filter(119, -100)
        self.assertIn("asetpts=N/SR/TB,atrim=start_sample=4800,apad=whole_len=190400,"
                      "atrim=end_sample=190400,asetpts=N/SR/TB[a]", fc)
        self.assertIn("trim=end_frame=119,", fc)
        self.assertIn(",adelay=4800S:all=1,apad=", render.build_filter(119, 100))

    def test_rejects_empty_video(self):
        with self.assertRaises(ValueError):
            render.build_filter(0)


class BuildRenderCmdTest(unittest.TestCase):
    def test_nvenc_argv_matches_spec(self):
        cmd = render.build_render_cmd("/t/raw.mkv", "/t/silvio.wav", "/t/wm.png", "/t/render.part.mp4", 300, SILVIO)
        self.assertEqual(cmd, [
            "nice", "-n", "10",
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", "/t/raw.mkv", "-i", "/t/silvio.wav", "-i", "/t/wm.png",
            "-filter_complex", SPEC_FILTER_300,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "21", "-b:v", "0",
            "-maxrate", "6M", "-bufsize", "12M", "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", "30",
            "-colorspace", "smpte170m", "-color_primaries", "smpte170m", "-color_trc", "smpte170m",
            "-color_range", "tv",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "1", "-movflags", "+faststart",
            "-metadata", "title=Paródia/homenagem - voz gerada por IA",
            "-metadata", "comment=Voz sintética gerada por IA (conversão RVC). Não é a voz real de Silvio Santos.",
            "-metadata", "description=AI-generated/synthetic voice (RVC voice conversion). Parody/tribute. "
                         "Not the real voice of Silvio Santos.",
            "-f", "mp4", "/t/render.part.mp4"])

    def test_x264_fallback_flags(self):
        cmd = render.build_render_cmd("r.mkv", "c.wav", "w.png", "o.mp4", 30, SILVIO, encoder="x264")
        i = cmd.index("-c:v")
        self.assertEqual(cmd[i:i + 14], ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                                         "-maxrate", "6M", "-bufsize", "12M", "-profile:v", "high",
                                         "-pix_fmt", "yuv420p"])
        self.assertNotIn("h264_nvenc", cmd)

    def test_offset_reaches_filter(self):
        cmd = render.build_render_cmd("r.mkv", "c.wav", "w.png", "o.mp4", 30, SILVIO, av_offset_ms=100)
        self.assertIn(",adelay=4800S:all=1,", cmd[cmd.index("-filter_complex") + 1])

    def test_never_shortest_nor_setpriv(self):
        # apad + -shortest trava o ffmpeg 6.1.1; o setpriv entra no procs.spawn
        for enc in render.ENCODERS:
            cmd = render.build_render_cmd("r.mkv", "c.wav", "w.png", "o.mp4", 30, SILVIO, encoder=enc)
            self.assertNotIn("-shortest", cmd)
            self.assertEqual(cmd[:4], ["nice", "-n", "10", "ffmpeg"])

    def test_rejects_unknown_encoder(self):
        with self.assertRaises(ValueError):
            render.build_render_cmd("r.mkv", "c.wav", "w.png", "o.mp4", 30, SILVIO, encoder="vaapi")

    def test_rejects_model_without_warning(self):
        mudo = Modelo("mudo", "Mudo", "Mudo", "")
        with self.assertRaises(ValueError):
            render.build_render_cmd("r.mkv", "c.wav", "w.png", "o.mp4", 30, mudo)


class TimeoutTest(unittest.TestCase):
    def test_five_times_duration_plus_60s(self):
        self.assertEqual(render.render_timeout(300), 110.0)
        self.assertEqual(render.render_timeout(9000), 1560.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_render -v`
Expected: FAIL/ERROR com `ImportError: cannot import name 'render' from 'studio'`

- [ ] **Step 3: Implementar a montagem do filtro e do comando**

`studio/render.py` (versão inicial; o Step 8 substitui pelo arquivo completo):

```python
"""Render final: MKV da tomada + WAV convertido + marca d'agua -> MP4 verificado em videos_finais/."""

from studio.config import Modelo, metadata_tags

FPS = 30
OUT_SR = 48000
SAMPLES_PER_FRAME = OUT_SR // FPS           # 1600
TIMEOUT_FACTOR = 5                          # 5x a duracao da tomada + 60 s
TIMEOUT_BASE_S = 60.0
ENCODERS = ("nvenc", "x264")                # ordem de tentativa
ENCODER_FLAGS = {
    "nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "21", "-b:v", "0",
              "-maxrate", "6M", "-bufsize", "12M", "-profile:v", "high"],
    "x264": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-maxrate", "6M", "-bufsize", "12M",
             "-profile:v", "high"],
}


class RenderError(Exception):
    pass


def offset_filter(av_offset_ms: int, sr: int = OUT_SR) -> str:
    # positivo = audio mais tarde; conta amostras (vale depois do aresample)
    n = round(abs(av_offset_ms) * sr / 1000)
    if n == 0:
        return ""
    if av_offset_ms > 0:
        return f",adelay={n}S:all=1"
    return f",atrim=start_sample={n}"


def build_filter(n_frames: int, av_offset_ms: int = 0) -> str:
    # video comeca no 2o frame (ancora da timeline); audio com exatamente n_frames*1600 amostras
    # (apad whole_len + atrim end_sample; NUNCA apad + -shortest: trava no ffmpeg 6.1.1)
    if n_frames < 1:
        raise ValueError("O vídeo precisa de pelo menos 1 frame")
    s = n_frames * SAMPLES_PER_FRAME
    return (f"[0:v]trim=start_frame=1,setpts=PTS-STARTPTS,fps={FPS},tpad=stop_mode=clone:stop=2,"
            f"trim=end_frame={n_frames},setpts=PTS-STARTPTS,format=yuv420p[v0];"
            "[2:v]format=yuva420p[wm];[v0][wm]overlay=0:0:format=yuv420,format=yuv420p[v];"
            f"[1:a]aresample={OUT_SR}:resampler=soxr,asetpts=N/SR/TB{offset_filter(av_offset_ms)},"
            f"apad=whole_len={s},atrim=end_sample={s},asetpts=N/SR/TB[a]")


def build_render_cmd(raw_mkv: str, conv_wav: str, wm_png: str, out_part: str, n_frames: int, modelo: Modelo,
                     av_offset_ms: int = 0, encoder: str = "nvenc") -> list[str]:
    # sem o prefixo setpriv (o procs.spawn poe); ValueError se o modelo nao tem aviso
    if encoder not in ENCODER_FLAGS:
        raise ValueError(f"Encoder desconhecido: {encoder}")
    meta = []
    for key, value in metadata_tags(modelo).items():
        meta += ["-metadata", f"{key}={value}"]
    return ["nice", "-n", "10",
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", raw_mkv, "-i", conv_wav, "-i", wm_png,
            "-filter_complex", build_filter(n_frames, av_offset_ms),
            "-map", "[v]", "-map", "[a]", *ENCODER_FLAGS[encoder], "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-colorspace", "smpte170m", "-color_primaries", "smpte170m", "-color_trc", "smpte170m",
            "-color_range", "tv",
            "-c:a", "aac", "-b:a", "128k", "-ar", str(OUT_SR), "-ac", "1", "-movflags", "+faststart",
            *meta, "-f", "mp4", out_part]


def render_timeout(n_frames: int) -> float:
    return TIMEOUT_FACTOR * n_frames / FPS + TIMEOUT_BASE_S
```

- [ ] **Step 4: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_render -v`
Expected: `Ran 13 tests in 0.000s` / `OK`

- [ ] **Step 5: Commit**

```bash
git add studio/render.py tests/test_render.py
git commit -m "feat(studio): render monta filtro e argv do MP4 (nvenc/x264, offset, apad whole_len)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: Escrever o teste com mídia sintética (falha)**

Uma tomada sintética de 4 s em 640×360 (áudio 0,40 s atrasado, flash no frame 60), `audio.wav` alinhado pela
Task 4 e dois WAVs "convertidos" simulados a partir dele: reamostrados para 40 kHz, um 60 ms mais curto e outro
40 ms mais longo. O MP4 sem marca d'água é gerado trocando o trecho do `overlay` no filtro.

`tests/test_render_media.py`:

```python
import dataclasses
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
        cmd = render.build_render_cmd(self.take.raw_path, self.short, self.wm, out, self.n, SILVIO, encoder="x264")
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
        with mock.patch.object(render, "build_filter", lambda n, off=0: without_overlay(real(n, off))):
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 7: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_render_media -v`
Expected: `ERROR: setUpClass (tests.test_render_media.RenderFinalTest)` com
`AttributeError: module 'studio.render' has no attribute 'render_final'`

- [ ] **Step 8: Implementar verificação, execução e `render_final`**

`studio/render.py` (arquivo completo final):

```python
"""Render final: MKV da tomada + WAV convertido + marca d'agua -> MP4 verificado em videos_finais/."""

import os
import signal
import subprocess
import threading
import time

import numpy as np
from PIL import Image

from studio import procs, timeline, watermark
from studio.config import VIDEOS_DIR, Modelo, metadata_tags
from studio.takes import Take, final_video_name

FPS = 30
OUT_SR = 48000
SAMPLES_PER_FRAME = OUT_SR // FPS           # 1600
TIMEOUT_FACTOR = 5                          # 5x a duracao da tomada + 60 s
TIMEOUT_BASE_S = 60.0
ENCODERS = ("nvenc", "x264")                # ordem de tentativa
ENCODER_FLAGS = {
    "nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "21", "-b:v", "0",
              "-maxrate", "6M", "-bufsize", "12M", "-profile:v", "high"],
    "x264": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-maxrate", "6M", "-bufsize", "12M",
             "-profile:v", "high"],
}
PART_NAME = "render.part.mp4"
LOG_NAME = "render.log"
CANCEL_MSG = "Render cancelado"
POLL_S = 0.1
STOP_WAIT_S = 5.0
DECODE_TIMEOUT_S = 60.0
TEXT_LUMA_MIN = 200                         # texto branco da marca: luma > 200 ...
TEXT_MIN_FRACTION = 0.95                    # ... em >= 95 % dos pixels de texto
BAND_P95_MAX = 140                          # faixa escura: percentil 95 da luma < 140


class RenderError(Exception):
    pass


def offset_filter(av_offset_ms: int, sr: int = OUT_SR) -> str:
    # positivo = audio mais tarde; conta amostras (vale depois do aresample)
    n = round(abs(av_offset_ms) * sr / 1000)
    if n == 0:
        return ""
    if av_offset_ms > 0:
        return f",adelay={n}S:all=1"
    return f",atrim=start_sample={n}"


def build_filter(n_frames: int, av_offset_ms: int = 0) -> str:
    # video comeca no 2o frame (ancora da timeline); audio com exatamente n_frames*1600 amostras
    # (apad whole_len + atrim end_sample; NUNCA apad + -shortest: trava no ffmpeg 6.1.1)
    if n_frames < 1:
        raise ValueError("O vídeo precisa de pelo menos 1 frame")
    s = n_frames * SAMPLES_PER_FRAME
    return (f"[0:v]trim=start_frame=1,setpts=PTS-STARTPTS,fps={FPS},tpad=stop_mode=clone:stop=2,"
            f"trim=end_frame={n_frames},setpts=PTS-STARTPTS,format=yuv420p[v0];"
            "[2:v]format=yuva420p[wm];[v0][wm]overlay=0:0:format=yuv420,format=yuv420p[v];"
            f"[1:a]aresample={OUT_SR}:resampler=soxr,asetpts=N/SR/TB{offset_filter(av_offset_ms)},"
            f"apad=whole_len={s},atrim=end_sample={s},asetpts=N/SR/TB[a]")


def build_render_cmd(raw_mkv: str, conv_wav: str, wm_png: str, out_part: str, n_frames: int, modelo: Modelo,
                     av_offset_ms: int = 0, encoder: str = "nvenc") -> list[str]:
    # sem o prefixo setpriv (o procs.spawn poe); ValueError se o modelo nao tem aviso
    if encoder not in ENCODER_FLAGS:
        raise ValueError(f"Encoder desconhecido: {encoder}")
    meta = []
    for key, value in metadata_tags(modelo).items():
        meta += ["-metadata", f"{key}={value}"]
    return ["nice", "-n", "10",
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", raw_mkv, "-i", conv_wav, "-i", wm_png,
            "-filter_complex", build_filter(n_frames, av_offset_ms),
            "-map", "[v]", "-map", "[a]", *ENCODER_FLAGS[encoder], "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-colorspace", "smpte170m", "-color_primaries", "smpte170m", "-color_trc", "smpte170m",
            "-color_range", "tv",
            "-c:a", "aac", "-b:a", "128k", "-ar", str(OUT_SR), "-ac", "1", "-movflags", "+faststart",
            *meta, "-f", "mp4", out_part]


def render_timeout(n_frames: int) -> float:
    return TIMEOUT_FACTOR * n_frames / FPS + TIMEOUT_BASE_S


# --- verificacao (fail-closed) ---

def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stream_problems(info: dict, n_frames: int) -> tuple[list[str], dict | None]:
    streams = info.get("streams", [])
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if len(video) != 1 or len(audio) != 1:
        return [f"O vídeo gerado tem {len(video)} faixa(s) de vídeo e {len(audio)} de áudio (esperado 1 e 1)"], None
    problems = []
    frames = int(video[0].get("nb_read_packets") or 0)
    if frames != n_frames:
        problems.append(f"O vídeo gerado tem {frames} frames (esperado {n_frames})")
    dv, da = _num(video[0].get("duration")), _num(audio[0].get("duration"))
    if dv is None or da is None or abs(dv - da) > 1 / FPS + 1e-6:
        problems.append(f"A duração do áudio ({da} s) difere da do vídeo ({dv} s) em mais de 1 frame")
    return problems, video[0]


def _tag_problems(tags: dict) -> list[str]:
    tags = {str(k).lower(): str(v) for k, v in tags.items()}
    problems = []
    if "IA" not in tags.get("comment", ""):
        problems.append("Metadado comment sem o aviso de IA")
    for key in ("title", "description"):
        if not tags.get(key, "").strip():
            problems.append(f"Metadado {key} ausente")
    return problems


def _gray_frames(mp4: str, idx: list[int], w: int, h: int) -> list[np.ndarray] | None:
    sel = "+".join(f"eq(n,{i})" for i in idx)
    try:
        r = procs.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", mp4, "-map", "0:v:0",
                       "-vf", f"select='{sel}',format=gray", "-fps_mode", "passthrough", "-f", "rawvideo", "-"],
                      timeout=DECODE_TIMEOUT_S + idx[-1] / FPS, text=False)
    except subprocess.TimeoutExpired:
        return None
    size = w * h
    if r.returncode != 0 or len(r.stdout) != size * len(idx):
        return None
    return [np.frombuffer(r.stdout, np.uint8, size, k * size).reshape(h, w) for k in range(len(idx))]


def _pixel_problems(mp4: str, n_frames: int, wm_png: str, size: tuple[int, int]) -> list[str]:
    # decodifica os frames 0, N/2 e N-1 em cinza e confere texto branco + faixa escura
    try:
        with Image.open(wm_png) as im:
            wm = im.convert("RGBA")
    except OSError:
        return ["Marca d'água ilegível: não dá para conferir o vídeo"]
    w, h = wm.size
    if (w, h) != size:
        return [f"O vídeo tem {size[0]}x{size[1]}, mas a marca d'água tem {w}x{h}"]
    text = watermark.text_pixel_mask(wm)
    x0, y0, x1, y1 = watermark.band_rect(w, h)
    band = np.zeros((h, w), dtype=bool)
    band[y0:y1, x0:x1] = True
    band &= np.asarray(wm)[..., :3].max(axis=2) == 0       # so o fundo da faixa, fora do texto
    if not text.any() or not band.any():
        return ["A marca d'água não tem texto para conferir"]
    idx = sorted({0, n_frames // 2, n_frames - 1})
    frames = _gray_frames(mp4, idx, w, h)
    if frames is None:
        return ["Não foi possível decodificar os frames do vídeo gerado"]
    no_text = [i for i, y in zip(idx, frames) if (y[text] > TEXT_LUMA_MIN).mean() < TEXT_MIN_FRACTION]
    no_band = [i for i, y in zip(idx, frames) if np.percentile(y[band], 95) >= BAND_P95_MAX]
    problems = []
    if no_text:
        problems.append(f"Texto da marca d'água não aparece no(s) frame(s) {', '.join(map(str, no_text))}")
    if no_band:
        problems.append(f"Faixa escura da marca d'água não aparece no(s) frame(s) {', '.join(map(str, no_band))}")
    return problems


def verify_render(mp4: str, n_frames: int, wm_png: str) -> list[str]:
    try:
        info = procs.ffprobe_json(mp4, "-count_packets", "-show_streams", "-show_format")
    except procs.ProcError as e:
        return [e.message]
    problems, video = _stream_problems(info, n_frames)
    problems += _tag_problems(info.get("format", {}).get("tags") or {})
    frames = int(video.get("nb_read_packets") or 0) if video else 0
    if frames > 0:
        # amostra os frames que o arquivo tem de fato (contagem errada ja foi acusada acima)
        size = (int(video.get("width") or 0), int(video.get("height") or 0))
        problems += _pixel_problems(mp4, frames, wm_png, size)
    return problems


# --- execucao ---

def _remove(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise RenderError(CANCEL_MSG)


def _stop(p: subprocess.Popen) -> None:
    # SIGTERM no grupo (o ffmpeg fecha o arquivo e sai); SIGKILL se nao sair
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if p.poll() is not None:
            return
        try:
            os.killpg(p.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            p.wait(timeout=STOP_WAIT_S)
        except subprocess.TimeoutExpired:
            pass


def _run(cmd: list[str], log: str, timeout: float, cancel: threading.Event | None) -> int:
    # spawn nesta thread (a que espera): pdeathsig vale enquanto ela viver
    with open(log, "ab") as lf:
        p = procs.spawn(cmd, stdout=subprocess.DEVNULL, stderr=lf, start_new_session=True)
    deadline = time.monotonic() + timeout
    try:
        while True:
            _check_cancel(cancel)
            if time.monotonic() >= deadline:
                raise RenderError("O render demorou demais e foi interrompido")
            try:
                return p.wait(timeout=POLL_S)
            except subprocess.TimeoutExpired:
                pass
    finally:
        _stop(p)


def _encode(take: Take, conv_wav: str, wm: str, part: str, n_frames: int, modelo: Modelo,
            av_offset_ms: int, cancel: threading.Event | None) -> None:
    # NVENC primeiro; se o ffmpeg falhar, libx264
    log = take.path(LOG_NAME)
    open(log, "wb").close()
    rc = None
    for encoder in ENCODERS:
        _check_cancel(cancel)
        cmd = build_render_cmd(take.raw_path, conv_wav, wm, part, n_frames, modelo,
                               av_offset_ms=av_offset_ms, encoder=encoder)
        rc = _run(cmd, log, render_timeout(n_frames), cancel)
        if rc == 0:
            return
        _remove(part)
    last = next((ln.strip() for ln in reversed(procs.tail(log, 5).splitlines()) if ln.strip()), "")
    raise RenderError(f"Falha ao gerar o vídeo (código {rc})" + (f": {last}" if last else ""))


def _video_info(take: Take) -> timeline.VideoInfo:
    if take.video:
        try:
            return timeline.VideoInfo(**take.video)
        except TypeError:
            pass                    # take.json de outra versao: le de novo do raw
    try:
        return timeline.video_info(take.raw_path)
    except procs.ProcError as e:
        raise RenderError(e.message) from None


def _frame_size(raw: str) -> tuple[int, int]:
    try:
        v = procs.media_info(raw)["video"]
    except procs.ProcError as e:
        raise RenderError(e.message) from None
    if not v:
        raise RenderError("Esta tomada não tem vídeo")
    return int(v["width"]), int(v["height"])


def _check_png(wm: str, w: int, h: int) -> None:
    try:
        with Image.open(wm) as im:
            pw, ph = im.size
    except OSError:
        raise RenderError("Marca d'água ilegível") from None
    if (pw, ph) != (w, h):
        raise RenderError(f"Marca d'água {pw}x{ph} não confere com o vídeo {w}x{h}")


def render_final(take: Take, modelo: Modelo, conv_wav: str, av_offset_ms: int = 0,
                 videos_dir: str = VIDEOS_DIR, cancel: threading.Event | None = None) -> str:
    # nao mexe no take.json (so a thread principal da GUI grava); devolve o caminho final
    try:
        metadata_tags(modelo)
    except ValueError:
        raise RenderError(f"Modelo {modelo.label} sem texto de aviso: o vídeo não pode ser gerado") from None
    if take.modo != "av":
        raise RenderError("Esta tomada não tem vídeo")
    if not os.path.isfile(conv_wav):
        raise RenderError("Áudio convertido não encontrado — converta a voz de novo")
    vi = _video_info(take)
    w, h = _frame_size(take.raw_path)
    try:
        wm = watermark.watermark_path(take.dir, w, h, modelo)
    except ValueError as e:         # marca fail-closed: aviso que nao cabe no quadro
        raise RenderError(str(e)) from None
    _check_png(wm, w, h)
    part = take.path(PART_NAME)
    _remove(part)
    try:
        _encode(take, conv_wav, wm, part, vi.n_frames, modelo, av_offset_ms, cancel)
        _check_cancel(cancel)
    except BaseException:
        _remove(part)
        raise
    problems = verify_render(part, vi.n_frames, wm)
    if problems:
        # o .part fica na pasta da tomada para diagnostico; nada vai para videos_finais/
        raise RenderError("O vídeo gerado não passou na verificação: " + "; ".join(problems))
    os.makedirs(videos_dir, exist_ok=True)
    final = os.path.join(videos_dir, final_video_name(take.id, modelo.key))
    os.replace(part, final)
    return final
```

- [ ] **Step 9: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_render tests.test_render_media -v`
Expected: `Ran 34 tests in 7.5s` / `OK`

Run: `./run_tests.sh`
Expected: `Ran 196 tests` (~22 s) e `OK (skipped=2)` (162 das Tasks 1–4 + 34 desta)

Run: `pgrep -a ffmpeg`
Expected: nenhuma linha (nenhum ffmpeg órfão)

- [ ] **Step 10: Commit**

```bash
git add studio/render.py tests/test_render_media.py
git commit -m "feat(studio): render_final com fallback x264, cancelamento, timeout e verificacao fail-closed

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

**Correções da revisão (Steps 11–20).** Dois casos que escapavam da verificação. (1) Uma tomada curtíssima,
como um duplo clique em Gravar: com 1 pacote de vídeo, a verificação recusava o MP4 com "0 faixa(s) de vídeo",
e com 2 pacotes ia para `videos_finais/` um MP4 de 1 frame (33 ms). (2) Disco cheio: um `OSError` cru escapava
do `render_final`, e a GUI mostrava `OSError: [Errno 28] ...`.

- [ ] **Step 11: Teste da tomada curta demais (falha)**

Em `tests/test_render_media.py`, substituir isto:

```python
if __name__ == "__main__":
    unittest.main()
```

por isto:

```python
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
```

- [ ] **Step 12: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_render_media -v`
Expected: `FAILED (failures=5)` (`Ran 23 tests`). Com 1 pacote: `AssertionError: 'O vídeo gerado não passou na
verificação:[65 chars]e 1)' != 'Gravação curta demais para gerar o vídeo'`; com 2 pacotes:
`AssertionError: RenderError not raised`; e no fim `AssertionError: Lists differ:
['2026-09-26_211545_silvio_IA.mp4'] != []` (o MP4 de 1 frame foi publicado). O
`test_three_packets_give_two_frames` já passa: é o limite que tem de continuar funcionando.

- [ ] **Step 13: Recusar a tomada com menos de 2 frames antes do ffmpeg**

Em `studio/render.py`, substituir isto:

```python
LOG_NAME = "render.log"
CANCEL_MSG = "Render cancelado"
```

por isto:

```python
LOG_NAME = "render.log"
CANCEL_MSG = "Render cancelado"
MIN_FRAMES = 2                              # o video comeca no 2o frame (ancora): 1 frame so nao e video
MSG_SHORT = "Gravação curta demais para gerar o vídeo"
```

E em `studio/render.py`, substituir isto:

```python
    vi = _video_info(take)
    w, h = _frame_size(take.raw_path)
```

por isto:

```python
    vi = _video_info(take)
    if vi.n_frames < MIN_FRAMES:
        raise RenderError(MSG_SHORT)        # antes do ffmpeg: com 1 pacote saia "0 faixa(s) de vídeo"
    w, h = _frame_size(take.raw_path)
```

- [ ] **Step 14: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_render_media -v`
Expected: `Ran 23 tests` (~9 s) e `OK`

- [ ] **Step 15: Commit**

```bash
git add studio/render.py tests/test_render_media.py
git commit -m "fix(render): tomada com menos de 2 frames vira RenderError 'Gravacao curta demais'

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 16: Testes de disco cheio no render (falha)**

Os testes simulam o disco cheio em três pontos: ao gravar a marca d'água, no ffmpeg e ao publicar em
`videos_finais/`. Um quarto teste cobre outro erro de arquivo (sem permissão).
Em `tests/test_render_media.py`, substituir isto:

```python
import dataclasses
import os
import signal
```

por isto:

```python
import dataclasses
import errno
import os
import signal
```

E em `tests/test_render_media.py`, substituir isto (o fim de `RenderFinalTest`):

```python
        self.assertEqual(str(cm.exception), "O render demorou demais e foi interrompido")
        self.assert_nothing_published("timeout")
```

por isto:

```python
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
```

- [ ] **Step 17: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_render_media -v`
Expected: `FAILED (failures=4, errors=3)` (`Ran 27 tests`):
`test_disk_full_writing_watermark` e `test_disk_full_publishing` com `OSError: [Errno 28] No space left on device`;
`test_other_os_error_is_render_error` com `PermissionError: [Errno 13] Permission denied: '/tmp/…/videos_sem_permissao'`;
`test_disk_full_in_ffmpeg_does_not_retry` com `AssertionError: 'Falha ao gerar o vídeo (código 1): Error [55 chars]vice'
!= 'Disco cheio — libere espaço'`. Outros 3 testes antigos (`test_final_name_and_no_part_left`,
`test_png_of_wrong_size_is_refused_before_ffmpeg`, `test_refuses_model_without_warning`) falham com
`AssertionError: True is not false`: é o `render.part.mp4` que o `test_disk_full_publishing` deixou na tomada, o
próprio defeito.

- [ ] **Step 18: `OSError` vira `RenderError` e o disco cheio do ffmpeg não tenta o x264**

Em `studio/render.py`, substituir isto:

```python
MIN_FRAMES = 2                              # o video comeca no 2o frame (ancora): 1 frame so nao e video
MSG_SHORT = "Gravação curta demais para gerar o vídeo"
```

por isto:

```python
MIN_FRAMES = 2                              # o video comeca no 2o frame (ancora): 1 frame so nao e video
MSG_SHORT = "Gravação curta demais para gerar o vídeo"
DISK_FULL_HINT = "No space left on device"  # o ffmpeg escreve o strerror do ENOSPC no render.log
```

E em `studio/render.py`, substituir isto:

```python
        rc = _run(cmd, log, render_timeout(n_frames), cancel)
        if rc == 0:
            return
        _remove(part)
```

por isto:

```python
        rc = _run(cmd, log, render_timeout(n_frames), cancel)
        if rc == 0:
            return
        _remove(part)
        if DISK_FULL_HINT in procs.tail(log, 20):
            raise RenderError(procs.MSG_DISK_FULL)    # o x264 falharia igual: nao tenta de novo
```

E em `studio/render.py`, substituir isto:

```python
def render_final(take: Take, modelo: Modelo, conv_wav: str, av_offset_ms: int = 0,
                 videos_dir: str = VIDEOS_DIR, cancel: threading.Event | None = None) -> str:
    # nao mexe no take.json (so a thread principal da GUI grava); devolve o caminho final
    try:
        metadata_tags(modelo)
```

por isto:

```python
def render_final(take: Take, modelo: Modelo, conv_wav: str, av_offset_ms: int = 0,
                 videos_dir: str = VIDEOS_DIR, cancel: threading.Event | None = None) -> str:
    # nao mexe no take.json (so a thread principal da GUI grava); devolve o caminho final
    try:
        return _render_final(take, modelo, conv_wav, av_offset_ms, videos_dir, cancel)
    except OSError as e:
        # disco cheio (ou outro erro de arquivo) no meio do caminho: mensagem PT e nenhum .part sobrando
        try:
            _remove(take.path(PART_NAME))
        except OSError:
            pass
        raise RenderError(procs.os_error_message(e)) from None


def _render_final(take: Take, modelo: Modelo, conv_wav: str, av_offset_ms: int, videos_dir: str,
                  cancel: threading.Event | None) -> str:
    try:
        metadata_tags(modelo)
```

- [ ] **Step 19: Rodar e ver passar (módulos e suíte inteira)**

Run: `Applio/.venv/bin/python -m unittest tests.test_render tests.test_render_media -v`
Expected: `Ran 40 tests` (~11 s) e `OK`

Run: `./run_tests.sh` e depois `pgrep -a ffmpeg`
Expected: `Ran 202 tests` (~26 s) e `OK (skipped=2)`, contando as Tasks 1–5 com as correções; o `pgrep` não
mostra nada

- [ ] **Step 20: Commit**

```bash
git add studio/render.py tests/test_render_media.py
git commit -m "fix(render): disco cheio e outros OSError viram RenderError em PT sem sobrar .part

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

**Notas para o executor**

- **Valores medidos** (tomada sintética de 4 s): o bip fica +0,42 ms depois do flash em 360p (x264) e em 720p
  (NVENC); numa tomada VFR 720p com áudio 0,25 s adiantado e deslocada +2 s, fica −0,35 ms. O vídeo dura 3,966667 s
  e o áudio AAC 3,966000 s (0,67 ms a menos). Por isso o teste usa `delta=0.002` para "durações iguais", e não
  igualdade exata. A spec pede ≤ 1 frame; o teste de alinhamento usa 2 ms de propósito, porque 1 frame (33 ms) não
  pegaria um erro de ±1 no `trim=start_frame`.
- **Limiares da verificação de pixels:** o texto branco fica com luma > 200 em 100 % dos pixels da máscara (mínimo
  206 em 360p/x264 e 225 em 720p/NVENC). O p95 da faixa fica entre 101 e 115. O pior caso é 115, no frame branco do
  flash, porque a faixa a 55 % sobre branco dá 0,45·255. Sem a marca d'água, a mesma fonte dá 43–45 % de texto
  > 200 e p95 226 na faixa. A região "faixa fora do texto" é `band_rect` ∩ (pixels do PNG com RGB == 0), ou seja, só
  o fundo da faixa. As bordas suavizadas das letras têm RGB > 0 e ficam de fora. No teste, o flash no frame 60
  cai exatamente em `N//2 = 59`, um dos frames conferidos. É proposital: é o pior caso do limiar da faixa.
- **`verify_render` amostra os frames que o MP4 tem de fato:** 0, M/2 e M−1, com M = `nb_read_packets`. Quando a
  contagem está certa, M = N. Na primeira versão a amostragem usava o N esperado, e um N errado gerava um segundo
  problema, "Não foi possível decodificar". O teste `test_verify_flags_wrong_frame_count` exige exatamente um
  problema.
- **Forçar a falha do NVENC:** `mock.patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": ""})`. O filho herda o ambiente
  no spawn, e o ffmpeg sai com rc 171 e `cuInit(0) failed -> CUDA_ERROR_NO_DEVICE`. Numa máquina sem GPU o NVENC
  falha de qualquer jeito, e o teste continua valendo. A saída do libx264 contém a string SEI `b"x264 - core"`, que
  a do NVENC não tem; é assim que o teste sabe qual encoder gerou o arquivo.
- **O teste de cancelamento é determinístico:** o wrapper de `procs.spawn` liga o `Event` logo depois de criar o
  ffmpeg **do render**, reconhecido por `"-filter_complex"` no argv. O filtro é necessário porque o `ffprobe` do
  `media_info` também passa por `procs.spawn` (via `procs.run`). Sem ele, o cancelamento dispararia antes do
  render e o caminho do SIGTERM não seria testado. O `_run` confere cancelamento e prazo **antes** do primeiro
  `wait`, e é isso que torna determinísticos o cancelamento e o teste de timeout (com `TIMEOUT_* = 0`). O rc pode
  ser −15 (o SIGTERM chegou antes do `setpriv`/`nice` executarem o ffmpeg) ou 255 (o ffmpeg tratou o sinal).
- **Processo:** `start_new_session=True` + `os.killpg` (SIGTERM, 5 s, SIGKILL), igual ao `procs.run`. O `stderr`
  vai para `render.log`, nunca para um pipe: o decoder MJPEG da webcam pode despejar muitas linhas de erro e
  encher o pipe. O timeout **não** aciona o fallback x264. Só um código de saída ≠ 0 aciona: se o render passou de
  5×+60 s, o problema não é o encoder.
- **A marca d'água também é fail-closed:** `watermark_path` levanta `ValueError` quando o aviso não cabe na
  coluna 9:16 nem com a fonte mínima (em 320x180 o texto do Silvio não cabe; 320x240 já cabe). O `render_final`
  converte isso em `RenderError` com a mesma mensagem, então a GUI só precisa tratar `RenderError`. O teste
  `test_watermark_that_does_not_fit_is_render_error` usa a marca real, trocando só o `_frame_size` por 320x180.
- **O tamanho do frame vem do `ffprobe` do `raw.mkv`** (spec 8.1), não do `take.video`. O `take.video` só fornece
  `n_frames`. Se estiver vazio ou for de outro formato, o render chama `timeline.video_info(raw)`.
- **Decodificação dos frames:** `procs.run(..., text=False)` devolve bytes. O filtro vai como
  `select='eq(n,0)+eq(n,59)+eq(n,118)',format=gray`, com as aspas simples para o parser de filtros do ffmpeg (sem
  shell), mais `-fps_mode passthrough`. Sem ele, a saída `rawvideo` duplica frames. O `format=gray` do ffmpeg 6.1
  sai em faixa cheia (branco ≈ 255).
- **O que fica fora deste módulo:** o `render_final` não mexe no `take.json`. A GUI marca `"renderizado"` e grava
  `saidas[key]["mp4"]` com o caminho retornado, ou grava `saidas[key]["erro"] = str(e)` sem mexer no `status` da
  tomada (o "falhou" fica só para falha da gravação; decisão da revisão, Task 11). O `os.replace` para
  `videos_finais/` supõe o mesmo sistema de arquivos, e `recordings/` e `videos_finais/` ficam ambos sob
  `BASE_DIR`.
- **Tomada curta (Steps 11–15), medido com MKVs MJPEG de 1, 2 e 3 pacotes:** com 1 pacote, a âncora é o 1º pacote
  e `n_frames = 1`; com 2, a âncora é o 2º pacote e `n_frames = 1`; com 3, `n_frames = 2`. Antes da correção, o de
  1 pacote falhava na verificação ("0 faixa(s) de vídeo"), e o de 2 publicava um MP4 de 1 frame. O de 3 pacotes
  gera um MP4 de 2 frames que passa na verificação. O teste confere isso para o limite não quebrar. A Task 6
  recusa a mesma gravação já na captura (`MIN_VIDEO_PACKETS = 3`).
- **Disco cheio (Steps 16–20):**
  - O ffmpeg com o disco cheio sai com rc ≠ 0 e escreve "No space left on device" no `render.log`. O render
    reconhece esse texto e não tenta o x264: com o `+faststart`, o x264 levaria o encode inteiro para falhar de novo.
    O teste simula isso com um `sh -c` no lugar do argv do ffmpeg.
  - Os `mock.patch.object(render.os, "replace"/"makedirs")` trocam a função do módulo `os` inteiro enquanto o
    `with` dura. Por isso o `replace` falso só falha quando o destino é a pasta do teste e repassa o resto.
  - O `watermark_path` já apagava o `wm_*.png.part` sozinho. Faltava converter o `OSError` e apagar o
    `render.part.mp4` quando o erro vem depois do encode (ao publicar).

---

### Task 6: Captura (studio/capture.py) com ffmpeg falso

**Files:**
- Create: `studio/capture.py`
- Create: `tests/fakebin/fake_ffmpeg.py`
- Test: `tests/test_capture.py` (comandos, FrameReader, verificação, mic, pré-checagem, watchdog)
- Test: `tests/test_capture_process.py` (CaptureProcess com o ffmpeg falso + teste real com `RUN_HARDWARE=1`)

**Interfaces:**
- Consumes (Tasks 1 e 2):
  - `studio.procs.spawn(argv: list[str], **popen_kwargs) -> subprocess.Popen` (prefixa `setpriv --pdeathsig TERM --`;
    o `setpriv` faz exec, então `Popen.pid` é o pid do próprio ffmpeg).
  - `studio.procs.ffprobe_json(path: str, *args: str) -> dict` e `studio.procs.ProcError` (`.message` em PT-BR).
  - `studio.procs.tail(path: str, n: int = 15) -> str`, `studio.procs.ffmpeg_exit_message(rc: int, log_tail: str = "") -> str`,
    `studio.procs.FFMPEG_EXIT_MSGS` (240/254/231).
  - `studio.devices.mic_source_of_pid(pid: int) -> int | None` (levanta `studio.devices.PactlError` quando o pactl
    falha; Task 2, Step 13), `studio.devices.list_mics() -> list[Source]`,
    `studio.devices.free_bytes(path: str) -> int`, `studio.devices.Source(index: int, name: str)`.
  - `studio.config.MAX_TAKE_S`, `studio.config.MIN_FREE_BYTES`, `studio.config.REC_DIR`.
  - `tests.helpers.make_synthetic_take(path, duration_s=..., flash_frame=..., size=...) -> str`, `tests.helpers.PY`.
- Produces (`studio/capture.py`):
  - Constantes: `PREVIEW_W, PREVIEW_H = 480, 270`; `PREVIEW_FPS = 15`; `FRAME_BYTES = 388800`; `PREVIEW_VF`
    (scale+pad+fps=15); `ACCEPTED_RC = (0, 255, 224)`; `MIN_FPS = 12.0`; `FPS_GRACE_S = 3.0`; mensagens `MSG_MIC`,
    `MSG_STALL`, `MSG_NO_FRAME`, `MSG_EMPTY`; e, dos Steps 14 e 19,
    `MSG_MIC_UNKNOWN = "Não deu para conferir o microfone (o pactl não respondeu)"`,
    `MIC_OK, MIC_SWAPPED, MIC_UNKNOWN = "ok", "trocado", "desconhecido"`, `MSG_SHORT = "Gravação curta demais"`,
    `MIN_VIDEO_PACKETS = 3`.
  - `build_av_cmd(mic: str, cam: str, out_mkv: str) -> list[str]` (spec 5.1 sem o prefixo setpriv);
    `build_preview_cmd(cam: str) -> list[str]` (só a saída `pipe:1`);
    `build_audio_cmd(mic: str, out_wav: str) -> list[str]` (WAV PCM16, sem pipe). Nenhum tem `-t`/`-to`/`-frames`/`-nostdin`.
  - `class FrameReader(threading.Thread)` (daemon): `__init__(self, stream, frame_bytes: int = FRAME_BYTES, clock=time.monotonic)`;
    `latest() -> tuple[int, bytes | None]` (seq, frame); `fps() -> float | None` (só frames distintos, janela de 30
    intervalos, mínimo 5 frames); atributos `frames: int`, `last_frame_monotonic: float | None`, `error: Exception | None`.
  - `class CaptureProcess`: `__init__(self, argv: list[str], log_path: str, with_preview: bool)`; `start() -> None`
    (CHAMAR NA THREAD PRINCIPAL DO TK); `early_failure() -> str | None` (não bloqueia; mensagem PT se o processo saiu
    sem pedido de parada); `latest_frame() -> tuple[int, bytes | None]`; `fps_measured() -> float | None`;
    `request_stop() -> None` (escreve `b"q\n"`, idempotente); `wait_stopped(t_q: float = 5, t_close: float = 3,
    t_term: float = 3) -> int` (bloqueante, thread de trabalho; q → fecha stdout → SIGTERM → SIGKILL);
    propriedades `pid`, `running`, `started_monotonic`, `last_frame_monotonic`; atributo `stop_steps: list[str]`
    (subconjunto ordenado de `["q", "close", "term", "kill"]`).
  - `verify_capture(path: str, need_video: bool) -> str | None` (conta pacotes com `-count_packets`; com
    `need_video`, menos de `MIN_VIDEO_PACKETS` pacotes de vídeo → `MSG_SHORT`, Step 19).
  - `mic_status(pid: int, expected_index: int) -> str` (Step 14): `MIC_OK` (o pactl mostra o gravador no índice
    esperado), `MIC_SWAPPED` (o pactl respondeu e mostra outro índice, ou não mostra o gravador) ou `MIC_UNKNOWN`
    (o pactl falhou ou estourou os 5 s: "não sei").
  - `check_mic(pid: int, expected_index: int) -> str | None`: `MSG_MIC` só para `MIC_SWAPPED`; **`None` tanto para ok
    quanto para "não sei"**. Para registrar o aviso do "não sei" (`MSG_MIC_UNKNOWN`) e contar trocas seguidas, a GUI
    (Task 10) usa o `mic_status`.
  - `preflight(mic: str, cam: str, rec_dir: str = REC_DIR) -> tuple[int | None, str | None]` (índice do mic ou motivo;
    `cam == ""` = só áudio).
  - `class Watchdog`: `__init__(self, frame_timeout: float = 2.0, first_frame_timeout: float = 5.0,
    max_s: float = MAX_TAKE_S, min_fps: float = MIN_FPS, fps_grace_s: float = FPS_GRACE_S)`;
    `check_frames(now: float, last_frame: float | None, started: float) -> str | None`;
    `check_duration(now: float, started: float) -> str | None`; `check_fps(fps: float | None, now: float, started: float) -> str | None`.
- Produces (`tests/fakebin/fake_ffmpeg.py`): script python que ignora o argv. Env `FAKE_MODE` = `normal` | `ignore_q` |
  `hang` | `fail240` | `stall` | `need_close`; `FAKE_FPS` (15); `FAKE_MAX_S` (30, trava de segurança).

Todos os comandos rodam da raiz do repositório (`~/orochi-ia-homenagem`).

- [ ] **Step 1: Escrever o teste que falha (partes sem processo)**

`tests/test_capture.py`:

```python
import itertools
import os
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from studio import capture, procs
from studio.devices import Source
from tests import helpers

MIC = "alsa_input.usb-Generalplus_Usb_Audio_Device-00.mono-fallback"
CAM = "/dev/v4l/by-id/usb-Sonix_Technology_Co.__Ltd._A4tech_HD_720P_PC_Camera_SN0001-video-index0"
VF = "scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2,fps=15"
# com -copyts, qualquer limite de duracao gera arquivo vazio com rc 0 (spec 5.1); -nostdin mata o q
FORBIDDEN = {"-t", "-to", "-frames", "-vframes", "-aframes", "-fs", "-nostdin"}


def forbidden_flags(argv: list[str]) -> list[str]:
    return [a for a in argv if a in FORBIDDEN or a.startswith("-frames:")]


class BuildCmdTest(unittest.TestCase):
    def test_av_cmd_is_spec_5_1(self):
        self.assertEqual(capture.build_av_cmd(MIC, CAM, "/rec/raw.mkv"), [
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-y", "-copyts",
            "-f", "pulse", "-thread_queue_size", "1024", "-sample_rate", "48000", "-channels", "1", "-i", MIC,
            "-f", "v4l2", "-input_format", "mjpeg", "-video_size", "1280x720", "-framerate", "30",
            "-ts", "mono2abs", "-thread_queue_size", "512", "-i", CAM,
            "-map", "1:v", "-map", "0:a", "-c:v", "copy", "-c:a", "pcm_s16le",
            "-avoid_negative_ts", "make_zero", "-f", "matroska", "/rec/raw.mkv",
            "-map", "1:v", "-vf", VF, "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"])

    def test_av_cmd_order_and_flags(self):
        a = capture.build_av_cmd(MIC, CAM, "/rec/raw.mkv")
        mic_i, cam_i = a.index(MIC), a.index(CAM)
        self.assertLess(mic_i, cam_i, "o microfone tem que ser a 1a entrada")
        self.assertLess(a.index("-copyts"), mic_i)
        self.assertEqual(a[a.index("-ts") + 1], "mono2abs")
        self.assertTrue(mic_i < a.index("-ts") < cam_i, "-ts mono2abs vale para a entrada da camera")
        self.assertEqual(a[a.index("-avoid_negative_ts") + 1], "make_zero")
        self.assertLess(a.index("-avoid_negative_ts"), a.index("/rec/raw.mkv"))
        self.assertEqual(a[-1], "pipe:1")
        self.assertEqual(forbidden_flags(a), [])

    def test_preview_cmd(self):
        a = capture.build_preview_cmd(CAM)
        self.assertEqual(a, [
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-y", "-copyts",
            "-f", "v4l2", "-input_format", "mjpeg", "-video_size", "1280x720", "-framerate", "30",
            "-ts", "mono2abs", "-thread_queue_size", "512", "-i", CAM,
            "-map", "0:v", "-vf", VF, "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"])
        self.assertNotIn("pulse", a)
        self.assertEqual(forbidden_flags(a), [])

    def test_audio_cmd(self):
        a = capture.build_audio_cmd(MIC, "/rec/raw.wav")
        self.assertEqual(a, [
            "ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info", "-y",
            "-f", "pulse", "-thread_queue_size", "1024", "-sample_rate", "48000", "-channels", "1", "-i", MIC,
            "-c:a", "pcm_s16le", "-f", "wav", "/rec/raw.wav"])
        self.assertEqual(forbidden_flags(a), [])

    def test_frame_size(self):
        self.assertEqual((capture.PREVIEW_W, capture.PREVIEW_H), (480, 270))
        self.assertEqual(capture.FRAME_BYTES, 388800)
        self.assertEqual(capture.PREVIEW_VF, VF)
        self.assertEqual(capture.ACCEPTED_RC, (0, 255, 224))


class ChunkStream:
    # stdout falso: devolve pedacos irregulares; opcionalmente falha depois de n leituras
    def __init__(self, data: bytes, sizes, fail_after: int | None = None):
        self.data = memoryview(data)
        self.pos = 0
        self.sizes = itertools.cycle(sizes)
        self.fail_after = fail_after
        self.calls = 0
        self.max_request = 0
        self.closed = False

    def readinto(self, b) -> int:
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise OSError(5, "Input/output error")
        self.max_request = max(self.max_request, len(b))
        n = min(len(b), next(self.sizes), len(self.data) - self.pos)
        b[:n] = self.data[self.pos:self.pos + n]
        self.pos += n
        return n

    def close(self):
        self.closed = True


def run_reader(stream, frame_bytes, clock=None) -> capture.FrameReader:
    kw = {"clock": clock} if clock else {}
    r = capture.FrameReader(stream, frame_bytes, **kw)
    r.start()
    r.join(timeout=5)
    assert not r.is_alive()
    return r


class FrameReaderTest(unittest.TestCase):
    def test_partial_reads_assemble_frames(self):
        fb = 1000
        frames = [bytes([i]) * fb for i in range(1, 8)]
        stream = ChunkStream(b"".join(frames) + b"\x09" * 123, sizes=[1, 7, 999, 64, 1500, 3])
        r = run_reader(stream, fb)
        seq, frame = r.latest()
        self.assertEqual(seq, 7)
        self.assertEqual(r.frames, 7)
        self.assertEqual(frame, frames[-1])      # o resto parcial (123 bytes) e descartado
        self.assertTrue(stream.closed)
        self.assertLessEqual(stream.max_request, fb)
        self.assertIsNone(r.error)
        self.assertIsNotNone(r.last_frame_monotonic)

    def test_real_frame_size_with_64k_chunks(self):
        frames = [bytes([i]) * capture.FRAME_BYTES for i in range(3)]
        stream = ChunkStream(b"".join(frames), sizes=[65536, 65535, 1])
        r = run_reader(stream, capture.FRAME_BYTES)
        self.assertEqual(r.latest(), (3, frames[2]))

    def test_nothing_read(self):
        r = run_reader(ChunkStream(b"", sizes=[10]), 100)
        self.assertEqual(r.latest(), (0, None))
        self.assertIsNone(r.last_frame_monotonic)
        self.assertIsNone(r.fps())

    def test_error_closes_stream(self):
        # leitor que morre sem fechar o pipe trava o ffmpeg e perde a tomada (spec 5.3)
        stream = ChunkStream(b"\x01" * 10 * 50, sizes=[10], fail_after=3)
        r = run_reader(stream, 10)
        self.assertEqual(r.frames, 3)
        self.assertIsInstance(r.error, OSError)
        self.assertTrue(stream.closed)

    def test_closed_stream_valueerror_is_handled(self):
        class Closed(ChunkStream):
            def readinto(self, b):
                raise ValueError("I/O operation on closed file")
        stream = Closed(b"", sizes=[1])
        r = run_reader(stream, 10)
        self.assertIsInstance(r.error, ValueError)
        self.assertTrue(stream.closed)

    def test_fps_counts_only_distinct_frames(self):
        # o filtro fps=15 duplica frames quando a camera entrega menos: duplicata nao conta
        a, b, c, d, e = (bytes([i]) * 4 for i in range(5))
        seq = [a, a, b, c, c, d, e, e]
        times = iter([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7])
        r = run_reader(ChunkStream(b"".join(seq), sizes=[3]), 4, clock=lambda: next(times))
        self.assertEqual(r.frames, 8)
        self.assertEqual(r.last_frame_monotonic, 0.7)
        # distintos em 0.0, 0.2, 0.3, 0.5, 0.6 -> 4 intervalos em 0.6 s
        self.assertAlmostEqual(r.fps(), 4 / 0.6)

    def test_fps_needs_enough_frames(self):
        seq = [bytes([i]) * 4 for i in range(capture.FPS_MIN_FRAMES - 1)]
        times = itertools.count(0.0, 0.1)
        r = run_reader(ChunkStream(b"".join(seq), sizes=[4]), 4, clock=lambda: next(times))
        self.assertIsNone(r.fps())

    def test_daemon_thread_starts_empty(self):
        r = capture.FrameReader(ChunkStream(b"", sizes=[1]), 4)
        self.assertIsInstance(r, threading.Thread)
        self.assertTrue(r.daemon)
        self.assertEqual(r.latest(), (0, None))


class WatchdogTest(unittest.TestCase):
    def test_frames(self):
        w = capture.Watchdog(frame_timeout=2.0)
        self.assertIsNone(w.check_frames(now=11.9, last_frame=10.0, started=5.0))
        self.assertEqual(w.check_frames(now=12.1, last_frame=10.0, started=5.0),
                         "A câmera parou de enviar imagem")

    def test_first_frame_gets_more_time(self):
        # o 1o frame leva 0,44-1,2 s (spec 5.4)
        w = capture.Watchdog(frame_timeout=2.0, first_frame_timeout=5.0)
        self.assertIsNone(w.check_frames(now=104.9, last_frame=None, started=100.0))
        self.assertEqual(w.check_frames(now=105.1, last_frame=None, started=100.0),
                         "A câmera não enviou nenhuma imagem")

    def test_duration_limit(self):
        w = capture.Watchdog()
        self.assertEqual(w.max_s, 300)
        self.assertIsNone(w.check_duration(now=399.9, started=100.0))
        self.assertEqual(w.check_duration(now=400.0, started=100.0),
                         "Limite de 5 min atingido — gravação encerrada")

    def test_low_fps(self):
        w = capture.Watchdog()
        self.assertIsNone(w.check_fps(None, now=110.0, started=100.0))
        self.assertIsNone(w.check_fps(14.6, now=110.0, started=100.0))
        self.assertEqual(w.check_fps(9.84, now=110.0, started=100.0), "Câmera a 9,8 fps (abaixo de 12) — pouca luz?")

    def test_low_fps_grace_at_start(self):
        # no real, um preview de 2 s mediu 12,3 fps e um A/V de 4 s mediu 14,95 (atraso do 1o frame)
        w = capture.Watchdog()
        self.assertEqual(w.fps_grace_s, 3.0)
        self.assertIsNone(w.check_fps(9.84, now=102.9, started=100.0))
        self.assertIsNotNone(w.check_fps(9.84, now=103.0, started=100.0))


class CheckMicTest(unittest.TestCase):
    def test_same_index(self):
        with mock.patch("studio.capture.devices.mic_source_of_pid", return_value=54) as m:
            self.assertIsNone(capture.check_mic(4321, 54))
        m.assert_called_once_with(4321)

    def test_other_source_or_missing(self):
        # nome invalido/desplugado: o PipeWire grava do mic padrao sem erro (spec 5.4.5)
        for found in (56, None):
            with mock.patch("studio.capture.devices.mic_source_of_pid", return_value=found):
                self.assertEqual(capture.check_mic(4321, 54), "Microfone desconectado ou trocado")


class PreflightTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.rec = os.path.join(tmp.name, "recordings")
        self.cam = os.path.join(tmp.name, "usb-Cam-video-index0")
        open(self.cam, "w").close()
        p1 = mock.patch("studio.capture.devices.list_mics",
                        return_value=[Source(54, MIC), Source(56, "alsa_input.usb-ME6S")])
        p2 = mock.patch("studio.capture.devices.free_bytes", return_value=50 * 1024**3)
        self.list_mics = p1.start()
        self.free = p2.start()
        self.addCleanup(mock.patch.stopall)

    def test_ok_returns_mic_index(self):
        self.assertEqual(capture.preflight(MIC, self.cam, self.rec), (54, None))
        self.free.assert_called_once_with(self.rec)

    def test_audio_only_skips_camera(self):
        self.assertEqual(capture.preflight("alsa_input.usb-ME6S", "", self.rec), (56, None))

    def test_unknown_mic(self):
        self.assertEqual(capture.preflight("alsa_input.sumiu", self.cam, self.rec),
                         (None, "Microfone não encontrado — escolha outro na lista"))

    def test_missing_camera(self):
        self.assertEqual(capture.preflight(MIC, self.cam + "-x", self.rec), (None, "Câmera não encontrada"))

    def test_low_disk(self):
        self.free.return_value = int(1.5 * 1024**3)
        self.assertEqual(capture.preflight(MIC, self.cam, self.rec),
                         (None, "Pouco espaço em disco: 1,5 GB livres (mínimo 2 GB)"))


class VerifyCaptureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        cls.mkv = helpers.make_synthetic_take(os.path.join(d, "raw.mkv"), duration_s=2.0, flash_frame=30,
                                              size="320x240")
        cls.wav = os.path.join(d, "raw.wav")
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", cls.mkv, "-map", "0:a",
                        "-c:a", "pcm_s16le", cls.wav], check=True)
        with open(cls.mkv, "rb") as f:
            head = f.read(1000)
        # so o cabecalho: o ffprobe ainda diz 2 s de duracao, mas nao ha nenhum pacote
        cls.trunc = os.path.join(d, "trunc.mkv")
        with open(cls.trunc, "wb") as f:
            f.write(head)
        cls.empty = os.path.join(d, "empty.mkv")
        open(cls.empty, "wb").close()
        cls.junk = os.path.join(d, "junk.mkv")
        with open(cls.junk, "wb") as f:
            f.write(b"isto nao e um video" * 100)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_good_take(self):
        self.assertIsNone(capture.verify_capture(self.mkv, need_video=True))
        self.assertIsNone(capture.verify_capture(self.mkv, need_video=False))

    def test_audio_only(self):
        self.assertIsNone(capture.verify_capture(self.wav, need_video=False))
        self.assertEqual(capture.verify_capture(self.wav, need_video=True), "A gravação não tem vídeo")

    def test_truncated_header_only(self):
        self.assertEqual(float(procs.media_info(self.trunc)["format"]["duration"]), 2.0)   # engana o media_info
        self.assertEqual(capture.verify_capture(self.trunc, need_video=True), "A gravação está vazia ou corrompida")

    def test_empty_missing_junk(self):
        self.assertEqual(capture.verify_capture(self.empty, need_video=False), "A gravação está vazia ou corrompida")
        missing = os.path.join(self.tmp.name, "nao_existe.mkv")
        self.assertEqual(capture.verify_capture(missing, need_video=False), "Gravação não encontrada: nao_existe.mkv")
        self.assertEqual(capture.verify_capture(self.junk, need_video=False), "Não foi possível ler junk.mkv")

    def test_zero_duration(self):
        data = {"streams": [{"codec_type": "audio", "nb_read_packets": "3"}], "format": {"duration": "0.000000"}}
        with mock.patch("studio.capture.procs.ffprobe_json", return_value=data) as probe:
            self.assertEqual(capture.verify_capture(self.wav, need_video=False), "A gravação ficou com duração zero")
        self.assertIn("-count_packets", probe.call_args.args)
        data["format"] = {}
        with mock.patch("studio.capture.procs.ffprobe_json", return_value=data):
            self.assertEqual(capture.verify_capture(self.wav, need_video=False), "A gravação ficou com duração zero")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_capture -v`
Expected: ERROR com `ImportError: cannot import name 'capture' from 'studio' (.../studio/__init__.py)` e
`FAILED (errors=1)`

- [ ] **Step 3: Implementar `studio/capture.py` (sem o CaptureProcess)**

`studio/capture.py`:

```python
"""Captura: comandos do ffmpeg (A/V + preview, so preview, so audio), leitor de frames, checagens e watchdog."""

import collections
import os
import threading
import time

from studio import devices, procs
from studio.config import MAX_TAKE_S, MIN_FREE_BYTES, REC_DIR

PREVIEW_W, PREVIEW_H = 480, 270
PREVIEW_FPS = 15
FRAME_BYTES = PREVIEW_W * PREVIEW_H * 3
# o pad garante o tamanho exato do frame para qualquer proporcao da camera
PREVIEW_VF = (f"scale={PREVIEW_W}:{PREVIEW_H}:force_original_aspect_ratio=decrease,"
              f"pad={PREVIEW_W}:{PREVIEW_H}:(ow-iw)/2:(oh-ih)/2,fps={PREVIEW_FPS}")
ACCEPTED_RC = (0, 255, 224)      # q | SIGTERM/SIGINT | preview com pipe quebrado
FPS_WINDOW = 30                  # intervalos usados na media do fps
FPS_MIN_FRAMES = 5
MIN_FPS = 12.0
FPS_GRACE_S = 3.0                # o fps dos primeiros segundos sai baixo (atraso do 1o frame)
MSG_MIC = "Microfone desconectado ou trocado"
MSG_STALL = "A câmera parou de enviar imagem"
MSG_NO_FRAME = "A câmera não enviou nenhuma imagem"
MSG_EMPTY = "A gravação está vazia ou corrompida"


def _mic_input(mic: str) -> list[str]:
    return ["-f", "pulse", "-thread_queue_size", "1024", "-sample_rate", "48000", "-channels", "1", "-i", mic]


def _cam_input(cam: str) -> list[str]:
    # mono2abs poe a camera no mesmo relogio do audio (sem ele o audio some sem erro)
    return ["-f", "v4l2", "-input_format", "mjpeg", "-video_size", "1280x720", "-framerate", "30",
            "-ts", "mono2abs", "-thread_queue_size", "512", "-i", cam]


def _preview_output(stream: str) -> list[str]:
    return ["-map", stream, "-vf", PREVIEW_VF, "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]


def build_av_cmd(mic: str, cam: str, out_mkv: str) -> list[str]:
    # spec 5.1: mic e a 1a entrada; nunca -t/-to/-frames (com -copyts gera arquivo vazio com rc 0)
    return ["ffmpeg", "-hide_banner", "-loglevel", "info", "-y", "-copyts",
            *_mic_input(mic), *_cam_input(cam),
            "-map", "1:v", "-map", "0:a", "-c:v", "copy", "-c:a", "pcm_s16le",
            "-avoid_negative_ts", "make_zero", "-f", "matroska", out_mkv,
            *_preview_output("1:v")]


def build_preview_cmd(cam: str) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "info", "-y", "-copyts", *_cam_input(cam),
            *_preview_output("0:v")]


def build_audio_cmd(mic: str, out_wav: str) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info", "-y", *_mic_input(mic),
            "-c:a", "pcm_s16le", "-f", "wav", out_wav]


class FrameReader(threading.Thread):
    # le frames rgb24 do stdout do ffmpeg ate o EOF; guarda so o ultimo; nunca toca no Tk
    def __init__(self, stream, frame_bytes: int = FRAME_BYTES, clock=time.monotonic):
        super().__init__(name="FrameReader", daemon=True)
        self.stream = stream
        self.frame_bytes = frame_bytes
        self._clock = clock
        self._lock = threading.Lock()
        self._frame: bytes | None = None
        self._distinct = collections.deque(maxlen=FPS_WINDOW + 1)
        self.frames = 0
        self.last_frame_monotonic: float | None = None
        self.error: Exception | None = None

    def run(self) -> None:
        buf = bytearray(self.frame_bytes)
        view = memoryview(buf)
        try:
            while self._fill(view):
                self._publish(buf)
        except (OSError, ValueError) as e:     # ValueError: stdout fechado pelo wait_stopped
            self.error = e
        finally:
            # leitor morto com o pipe aberto trava o ffmpeg e perde a tomada
            try:
                self.stream.close()
            except OSError:
                pass

    def _fill(self, view: memoryview) -> bool:
        got = 0
        while got < self.frame_bytes:
            n = self.stream.readinto(view[got:])
            if not n:
                return False        # EOF; frame parcial descartado
            got += n
        return True

    def _publish(self, buf: bytearray) -> None:
        now = self._clock()
        new = self._frame is None or buf != self._frame
        frame = bytes(buf)
        with self._lock:
            self._frame = frame
            self.frames += 1
            self.last_frame_monotonic = now
            if new:
                self._distinct.append(now)

    def latest(self) -> tuple[int, bytes | None]:
        with self._lock:
            return self.frames, self._frame

    def fps(self) -> float | None:
        # so frames diferentes: o filtro fps=15 duplica frames quando a camera entrega menos
        with self._lock:
            times = list(self._distinct)
        if len(times) < FPS_MIN_FRAMES or times[-1] <= times[0]:
            return None
        return (len(times) - 1) / (times[-1] - times[0])


def _count(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _seconds(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _decimal(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def verify_capture(path: str, need_video: bool) -> str | None:
    # conta pacotes: um MKV cortado mantem a duracao do cabecalho sem ter nenhum pacote
    name = os.path.basename(path)
    try:
        if os.path.getsize(path) == 0:
            return MSG_EMPTY
    except OSError:
        return f"Gravação não encontrada: {name}"
    try:
        data = procs.ffprobe_json(path, "-count_packets", "-show_entries",
                                  "stream=codec_type,nb_read_packets:format=duration")
    except procs.ProcError as e:
        return e.message
    packets = {"audio": 0, "video": 0}
    for s in data.get("streams", []):
        if s.get("codec_type") in packets:
            packets[s["codec_type"]] += _count(s.get("nb_read_packets"))
    if not any(packets.values()):
        return MSG_EMPTY
    if not packets["audio"]:
        return "A gravação não tem áudio"
    if need_video and not packets["video"]:
        return "A gravação não tem vídeo"
    if not _seconds(data.get("format", {}).get("duration")) > 0:
        return "A gravação ficou com duração zero"
    return None


def check_mic(pid: int, expected_index: int) -> str | None:
    # o setpriv executa o ffmpeg no mesmo processo: o pid do Popen e o do ffmpeg
    return None if devices.mic_source_of_pid(pid) == expected_index else MSG_MIC


def preflight(mic: str, cam: str, rec_dir: str = REC_DIR) -> tuple[int | None, str | None]:
    # (indice do mic, None) ou (None, motivo PT); cam vazio = so audio
    index = next((s.index for s in devices.list_mics() if s.name == mic), None)
    if index is None:
        return None, "Microfone não encontrado — escolha outro na lista"
    if cam and not os.path.exists(cam):
        return None, procs.FFMPEG_EXIT_MSGS[254]
    free = devices.free_bytes(rec_dir)
    if free < MIN_FREE_BYTES:
        return None, (f"Pouco espaço em disco: {_decimal(free / 1024**3)} GB livres "
                      f"(mínimo {MIN_FREE_BYTES // 1024**3} GB)")
    return index, None


class Watchdog:
    # checagens periodicas da GUI durante a gravacao; tempos em time.monotonic()
    def __init__(self, frame_timeout: float = 2.0, first_frame_timeout: float = 5.0,
                 max_s: float = MAX_TAKE_S, min_fps: float = MIN_FPS, fps_grace_s: float = FPS_GRACE_S):
        self.frame_timeout = frame_timeout
        self.first_frame_timeout = first_frame_timeout
        self.max_s = max_s
        self.min_fps = min_fps
        self.fps_grace_s = fps_grace_s

    def check_frames(self, now: float, last_frame: float | None, started: float) -> str | None:
        if last_frame is None:
            return MSG_NO_FRAME if now - started > self.first_frame_timeout else None
        return MSG_STALL if now - last_frame > self.frame_timeout else None

    def check_duration(self, now: float, started: float) -> str | None:
        if now - started >= self.max_s:
            return f"Limite de {self.max_s / 60:g} min atingido — gravação encerrada"
        return None

    def check_fps(self, fps: float | None, now: float, started: float) -> str | None:
        if fps is None or fps >= self.min_fps or now - started < self.fps_grace_s:
            return None
        return f"Câmera a {_decimal(fps)} fps (abaixo de {self.min_fps:g}) — pouca luz?"
```

- [ ] **Step 4: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_capture -v`
Expected: `Ran 30 tests` (~0,5 s) e `OK`

- [ ] **Step 5: Commit**

```bash
git add studio/capture.py tests/test_capture.py
git commit -m "feat(studio): capture com comandos da spec 5.1, FrameReader, verificacao e watchdog

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: Escrever o ffmpeg falso e o teste do processo que falha**

`tests/fakebin/fake_ffmpeg.py`. O arquivo não precisa de permissão de execução, porque os testes o chamam com o
python:

```python
"""ffmpeg falso para os testes de captura. Rodar com python; o argv e ignorado.

Manda frames rgb24 480x270 (frame i = byte i % 251 repetido) no stdout, em pedacos irregulares, a FAKE_FPS.
Le "q" do stdin. FAKE_MODE:
  normal      sai 0 ao ler q (224 se o stdout ja quebrou); SIGTERM -> 255
  ignore_q    ignora q; so sai com SIGTERM (255)
  hang        ignora q e SIGTERM; so SIGKILL
  fail240     escreve "Device or resource busy" no stderr e sai 240 em 50 ms
  stall       para de mandar frames depois de STALL_AFTER; q -> 0
  need_close  como o ffmpeg preso escrevendo no pipe: so atende o q depois que o stdout quebra (sai 224)
"""

import os
import random
import signal
import threading
import time

W, H = 480, 270
FRAME = W * H * 3
MODE = os.environ.get("FAKE_MODE", "normal")
FPS = float(os.environ.get("FAKE_FPS", "15"))
MAX_S = float(os.environ.get("FAKE_MAX_S", "30"))   # trava de seguranca: nunca vira orfao eterno
STALL_AFTER = 5

q_seen = threading.Event()


def log(msg: str) -> None:
    os.write(2, (msg + "\n").encode())


def read_stdin() -> None:
    while True:
        try:
            data = os.read(0, 64)
        except OSError:
            return
        if not data:
            return
        for _ in range(data.count(b"q")):
            log("q recebido")
            q_seen.set()


def on_term(signum, frame) -> None:
    log(f"Exiting normally, received signal {signum}.")
    os._exit(255)


def write_frame(i: int, rng: random.Random) -> None:
    view = memoryview(bytes([i % 251]) * FRAME)
    while view:
        n = os.write(1, view[:rng.randint(1, 100_000)])
        view = view[n:]


def main() -> None:
    log(f"ffmpeg falso: modo {MODE}")
    if MODE == "fail240":
        log("[video4linux2,v4l2 @ 0x5f0] ioctl(VIDIOC_STREAMON): Device or resource busy")
        log("[in#1 @ 0x5f1] Error opening input: Device or resource busy")
        time.sleep(0.05)
        os._exit(240)
    signal.signal(signal.SIGTERM, signal.SIG_IGN if MODE == "hang" else on_term)
    threading.Thread(target=read_stdin, daemon=True).start()
    rng = random.Random(6)
    parent = os.getppid()
    deadline = time.monotonic() + MAX_S
    broken = False
    i = 0
    while time.monotonic() < deadline and os.getppid() == parent:
        if q_seen.is_set() and (MODE in ("normal", "stall") or (MODE == "need_close" and broken)):
            log("ffmpeg falso: saindo pelo q")
            os._exit(224 if broken else 0)
        if not broken and not (MODE == "stall" and i >= STALL_AFTER):
            try:
                write_frame(i, rng)
                i += 1
            except BrokenPipeError:
                broken = True
                log("Error submitting a packet to the muxer: Broken pipe")
        time.sleep(1 / FPS)
    log("ffmpeg falso: tempo esgotado ou pai morreu")
    os._exit(3)


if __name__ == "__main__":
    main()
```

`tests/test_capture_process.py`:

```python
import glob
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from studio import capture, procs
from tests import helpers

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fakebin", "fake_ffmpeg.py")
FAST = {"t_q": 0.4, "t_close": 1.0, "t_term": 0.4}


def fake_argv(mode: str) -> list[str]:
    # o env faz exec: o pid continua sendo o do "ffmpeg" (como o setpriv)
    return ["env", f"FAKE_MODE={mode}", helpers.PY, FAKE, "-hide_banner", "-i", "ignorado"]


def wait_until(cond, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


def uniform(frame: bytes) -> bool:
    return frame.count(frame[:1]) == len(frame)


class FakeCaptureTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = os.path.join(tmp.name, "ffmpeg.log")

    def start(self, mode: str, with_preview: bool = True) -> capture.CaptureProcess:
        cap = capture.CaptureProcess(fake_argv(mode), self.log, with_preview)
        cap.start()
        self.addCleanup(self.reap, cap)
        return cap

    def reap(self, cap):
        # sempre: mesmo depois de uma falha rapida, fecha o stdin e colhe o processo
        if cap.pid is not None:
            cap.wait_stopped(0.1, 0.1, 0.1)

    def read_log(self) -> str:
        with open(self.log, encoding="utf-8") as f:
            return f.read()

    def test_normal_q_stops_with_rc0(self):
        cap = self.start("normal")
        self.assertTrue(cap.running)
        self.assertTrue(wait_until(lambda: cap.latest_frame()[0] >= 6))
        seq, frame = cap.latest_frame()
        self.assertEqual(len(frame), capture.FRAME_BYTES)
        self.assertTrue(uniform(frame), "frame montado com bytes de dois frames")
        self.assertEqual(frame[0], (seq - 1) % 251, "frame perdido ou fora de ordem")
        self.assertIsNotNone(cap.last_frame_monotonic)
        self.assertGreaterEqual(cap.last_frame_monotonic, cap.started_monotonic)
        self.assertIsNone(cap.early_failure())
        fps = cap.fps_measured()
        self.assertIsNotNone(fps)
        self.assertTrue(5 < fps < 40, fps)
        pid = cap.pid
        cap.request_stop()
        cap.request_stop()                       # idempotente
        self.assertEqual(cap.wait_stopped(**FAST), 0)
        self.assertEqual(cap.stop_steps, ["q"])
        self.assertEqual(cap.pid, pid)
        self.assertFalse(cap.running)
        self.assertIsNone(cap.early_failure())   # saida pedida nao e falha
        log = self.read_log()
        self.assertIn("ffmpeg falso: modo normal", log)
        self.assertEqual(log.count("q recebido"), 1)

    def test_start_uses_procs_spawn(self):
        with mock.patch("studio.capture.procs.spawn", wraps=procs.spawn) as spawn:
            cap = self.start("normal")
        self.assertEqual(spawn.call_args.args[0], fake_argv("normal"))
        kw = spawn.call_args.kwargs
        self.assertEqual((kw["stdin"], kw["stdout"], kw["bufsize"]), (subprocess.PIPE, subprocess.PIPE, 0))
        self.assertEqual(kw["stderr"].name, self.log)          # log em arquivo, nunca um 3o pipe
        self.assertNotIn("preexec_fn", kw)
        self.assertTrue(wait_until(lambda: cap.latest_frame()[0] >= 1))
        with open(f"/proc/{cap.pid}/cmdline", "rb") as f:
            # setpriv e env fazem exec: o pid do Popen ja e o do "ffmpeg"
            self.assertIn(FAKE.encode(), f.read().split(b"\0"))
        cap.request_stop()
        self.assertEqual(cap.wait_stopped(**FAST), 0)

    def test_ignore_q_needs_close_then_term(self):
        cap = self.start("ignore_q")
        self.assertTrue(wait_until(lambda: cap.latest_frame()[0] >= 2))
        cap.request_stop()
        self.assertEqual(cap.wait_stopped(**FAST), 255)
        self.assertEqual(cap.stop_steps, ["q", "close", "term"])
        self.assertIn("received signal 15", self.read_log())

    def test_hang_needs_kill(self):
        cap = self.start("hang")
        self.assertTrue(wait_until(lambda: cap.latest_frame()[0] >= 2))
        t0 = time.monotonic()
        self.assertEqual(cap.wait_stopped(0.3, 0.3, 0.3), -9)
        self.assertLess(time.monotonic() - t0, 3)
        self.assertEqual(cap.stop_steps, ["q", "close", "term", "kill"])
        self.assertFalse(cap.running)

    def test_blocked_pipe_released_by_close(self):
        # ffmpeg preso escrevendo no pipe so atende o q depois do EPIPE e sai 224 (probe t6c)
        cap = self.start("need_close")
        self.assertTrue(wait_until(lambda: cap.latest_frame()[0] >= 2))
        rc = cap.wait_stopped(**FAST)
        self.assertEqual(rc, 224)
        self.assertIn(rc, capture.ACCEPTED_RC)
        self.assertEqual(cap.stop_steps, ["q", "close"])
        self.assertIn("Broken pipe", self.read_log())

    def test_early_failure_busy_camera(self):
        cap = self.start("fail240")
        self.assertTrue(wait_until(lambda: not cap.running, timeout=3))
        self.assertEqual(cap.early_failure(), "Câmera em uso por outro programa (Meet/Zoom/OBS?)")
        self.assertEqual(cap.latest_frame(), (0, None))
        self.assertEqual(cap.wait_stopped(**FAST), 240)
        self.assertEqual(cap.stop_steps, ["q"])

    def test_early_failure_uses_log_tail(self):
        cap = capture.CaptureProcess(["sh", "-c", "echo 'Unknown input format: v4l3' >&2; exit 8"], self.log, True)
        cap.start()
        self.addCleanup(self.reap, cap)
        self.assertTrue(wait_until(lambda: not cap.running, timeout=3))
        self.assertEqual(cap.early_failure(), "ffmpeg falhou (código 8): Unknown input format: v4l3")

    def test_stall_trips_watchdog(self):
        cap = self.start("stall")
        self.assertTrue(wait_until(lambda: cap.latest_frame()[0] >= 5))
        dog = capture.Watchdog(frame_timeout=0.3)
        self.assertTrue(wait_until(lambda: dog.check_frames(time.monotonic(), cap.last_frame_monotonic,
                                                            cap.started_monotonic) is not None, timeout=3))
        self.assertEqual(cap.latest_frame()[0], 5)
        self.assertEqual(dog.check_frames(time.monotonic(), cap.last_frame_monotonic, cap.started_monotonic),
                         "A câmera parou de enviar imagem")
        self.assertTrue(cap.running)
        cap.request_stop()
        self.assertEqual(cap.wait_stopped(**FAST), 0)

    def test_audio_only_without_preview(self):
        with mock.patch("studio.capture.procs.spawn", wraps=procs.spawn) as spawn:
            cap = self.start("normal", with_preview=False)
        self.assertEqual(spawn.call_args.kwargs["stdout"], subprocess.DEVNULL)
        time.sleep(0.3)
        self.assertEqual(cap.latest_frame(), (0, None))
        self.assertIsNone(cap.last_frame_monotonic)
        self.assertIsNone(cap.fps_measured())
        self.assertEqual(cap.wait_stopped(**FAST), 0)
        self.assertEqual(cap.stop_steps, ["q"])

    def test_misuse(self):
        cap = capture.CaptureProcess(fake_argv("normal"), self.log, True)
        self.assertIsNone(cap.pid)
        self.assertFalse(cap.running)
        self.assertIsNone(cap.started_monotonic)
        self.assertIsNone(cap.early_failure())
        cap.request_stop()                                  # antes do start: nao faz nada
        with self.assertRaises(RuntimeError):
            cap.wait_stopped()
        cap.start()
        self.addCleanup(self.reap, cap)
        with self.assertRaises(RuntimeError):
            cap.start()


@unittest.skipUnless(os.environ.get("RUN_HARDWARE") == "1", "precisa de câmera e microfone reais (RUN_HARDWARE=1)")
class RealCaptureTest(unittest.TestCase):
    MIC = "alsa_input.usb-Generalplus_Usb_Audio_Device-00.mono-fallback"

    def setUp(self):
        cams = sorted(glob.glob("/dev/v4l/by-id/*A4tech*-video-index0"))
        self.assertTrue(cams, "câmera A4tech não encontrada em /dev/v4l/by-id")
        self.cam = cams[0]
        tmp = tempfile.TemporaryDirectory()        # a midia real some no fim do teste
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.log = os.path.join(self.dir, "ffmpeg.log")

    def run_capture(self, argv, with_preview, seconds, mic_index=None):
        cap = capture.CaptureProcess(argv, self.log, with_preview)
        cap.start()
        try:
            time.sleep(1.2)
            self.assertIsNone(cap.early_failure(), procs.tail(self.log))
            if mic_index is not None:
                self.assertIsNone(capture.check_mic(cap.pid, mic_index))
            time.sleep(seconds - 1.2)
            seq, frame = cap.latest_frame()
            fps = cap.fps_measured()
        finally:
            cap.request_stop()
            t0 = time.monotonic()
            rc = cap.wait_stopped()
            stop_s = time.monotonic() - t0
        self.assertIn(rc, capture.ACCEPTED_RC)
        self.assertEqual(cap.stop_steps, ["q"])
        size = len(frame) if frame is not None else None     # so o tamanho; o conteudo nunca e olhado
        del frame
        print(f"\n  {os.path.basename(argv[-1])}: {seq} frames, fps {fps}, rc {rc}, parada {stop_s:.2f} s",
              file=sys.stderr)
        return seq, size, fps

    def test_av_4s(self):
        index, err = capture.preflight(self.MIC, self.cam, self.dir)
        self.assertIsNone(err)
        out = os.path.join(self.dir, "raw.mkv")
        seq, size, fps = self.run_capture(capture.build_av_cmd(self.MIC, self.cam, out), True, 4.0, index)
        self.assertGreaterEqual(seq, 20)
        self.assertEqual(size, capture.FRAME_BYTES)
        self.assertIsNotNone(fps)
        self.assertIsNone(capture.verify_capture(out, need_video=True))

    def test_preview_only_2s(self):
        seq, size, _ = self.run_capture(capture.build_preview_cmd(self.cam), True, 2.0)
        self.assertGreaterEqual(seq, 10)
        self.assertEqual(size, capture.FRAME_BYTES)

    def test_audio_only_2s(self):
        index, err = capture.preflight(self.MIC, "", self.dir)
        self.assertIsNone(err)
        out = os.path.join(self.dir, "raw.wav")
        seq, size, _ = self.run_capture(capture.build_audio_cmd(self.MIC, out), False, 2.0, index)
        self.assertEqual((seq, size), (0, None))
        self.assertIsNone(capture.verify_capture(out, need_video=False))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 7: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_capture_process -v`
Expected: ERROR ao importar o módulo de teste, com
`AttributeError: module 'studio.capture' has no attribute 'CaptureProcess'`

- [ ] **Step 8: Implementar o `CaptureProcess`**

Em `studio/capture.py`, substituir:

```python
import collections
import os
import threading
import time
```

por:

```python
import collections
import os
import subprocess
import threading
import time
```

E acrescentar ao fim do arquivo, depois do `check_fps` do `Watchdog`, separado por duas linhas em branco:

```python
def _wait(p: subprocess.Popen, timeout: float) -> int | None:
    try:
        return p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def _close(stream) -> None:
    if stream is None:
        return
    try:
        stream.close()
    except OSError:
        pass


class CaptureProcess:
    # um ffmpeg de captura (A/V, preview ou so audio); para com q e escala ate o SIGKILL
    def __init__(self, argv: list[str], log_path: str, with_preview: bool):
        self.argv = list(argv)
        self.log_path = log_path
        self.with_preview = with_preview
        self.stop_steps: list[str] = []
        self._proc: subprocess.Popen | None = None
        self._reader: FrameReader | None = None
        self._started: float | None = None
        self._stop_requested = False
        self._lock = threading.Lock()

    def start(self) -> None:
        # CHAMAR NA THREAD PRINCIPAL DO TK: o pdeathsig vale enquanto a thread que fez o spawn viver
        if self._proc is not None:
            raise RuntimeError("captura já iniciada")
        stdout = subprocess.PIPE if self.with_preview else subprocess.DEVNULL
        with open(self.log_path, "wb") as log:     # stderr em arquivo: nunca um 3o pipe que pode encher
            self._proc = procs.spawn(self.argv, stdin=subprocess.PIPE, stdout=stdout, stderr=log, bufsize=0)
        self._started = time.monotonic()
        if self.with_preview:
            self._reader = FrameReader(self._proc.stdout)
            self._reader.start()

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def started_monotonic(self) -> float | None:
        return self._started

    @property
    def last_frame_monotonic(self) -> float | None:
        return self._reader.last_frame_monotonic if self._reader else None

    def early_failure(self) -> str | None:
        # nao bloqueia; saida sem pedido de parada = falha (camera ocupada, sumiu, no errado...)
        if self._proc is None or self._stop_requested:
            return None
        rc = self._proc.poll()
        if rc is None:
            return None
        return procs.ffmpeg_exit_message(rc, procs.tail(self.log_path))

    def latest_frame(self) -> tuple[int, bytes | None]:
        return self._reader.latest() if self._reader else (0, None)

    def fps_measured(self) -> float | None:
        return self._reader.fps() if self._reader else None

    def request_stop(self) -> None:
        with self._lock:
            if self._proc is None or self._stop_requested:
                return
            self._stop_requested = True
        try:
            self._proc.stdin.write(b"q\n")
        except (OSError, ValueError):
            pass        # ja saiu (pipe quebrado)

    def wait_stopped(self, t_q: float = 5, t_close: float = 3, t_term: float = 3) -> int:
        # bloqueante: rodar numa thread de trabalho; o leitor continua drenando ate o EOF
        p = self._proc
        if p is None:
            raise RuntimeError("captura não iniciada")
        self.request_stop()
        steps = ["q"]
        rc = _wait(p, t_q)
        if rc is None:
            steps.append("close")       # leitor parado: o EPIPE solta o ffmpeg preso no pipe (rc 224)
            _close(p.stdout)
            rc = _wait(p, t_close)
        if rc is None:
            steps.append("term")
            p.terminate()
            rc = _wait(p, t_term)
        if rc is None:
            steps.append("kill")
            p.kill()
            rc = p.wait()
        self.stop_steps = steps
        if self._reader:
            self._reader.join(timeout=2)
        _close(p.stdin)
        return rc
```

- [ ] **Step 9: Rodar e ver passar (módulo e suíte inteira)**

Run: `Applio/.venv/bin/python -m unittest tests.test_capture_process -v`
Expected: `Ran 13 tests` (~4,9 s) e `OK (skipped=3)`. Os 3 pulados são `RealCaptureTest`.

Run: `./run_tests.sh`
Expected: `Ran 245 tests` (~31 s) e `OK (skipped=5)`, contando as Tasks 1–6 (as correções desta task vêm depois). Depois, `pgrep -af fake_ffmpeg.py`
e `pgrep -a ffmpeg` não mostram nenhum processo de teste. Se o comando rodar dentro de um `bash -c` que contém o
texto `fake_ffmpeg`, o `pgrep -f` mostra a linha desse shell, e ela pode ser ignorada.

- [ ] **Step 10: Teste real (câmera e microfone; a luz da câmera acende)**

Confira antes que nada está usando a câmera (`fuser /dev/video0` sai com rc 1). O teste só conta frames e confere
o tamanho deles, sem olhar o conteúdo. A mídia fica num `TemporaryDirectory`, que é apagado no fim.

Run: `RUN_HARDWARE=1 Applio/.venv/bin/python -m unittest tests.test_capture_process.RealCaptureTest -v`
Expected (valores medidos aqui; os números variam um pouco):

```text
test_audio_only_2s (tests.test_capture_process.RealCaptureTest.test_audio_only_2s) ...
  raw.wav: 0 frames, fps None, rc 0, parada 0.21 s
ok
test_av_4s (tests.test_capture_process.RealCaptureTest.test_av_4s) ...
  pipe:1: 52 frames, fps 14.951734090390042, rc 0, parada 0.26 s
ok
test_preview_only_2s (tests.test_capture_process.RealCaptureTest.test_preview_only_2s) ...
  pipe:1: 23 frames, fps 12.34280296148074, rc 0, parada 0.21 s
ok

----------------------------------------------------------------------
Ran 3 tests in 8.809s

OK
```

Depois, `pgrep -a ffmpeg` não mostra nada.

- [ ] **Step 11: Commit**

```bash
git add studio/capture.py tests/fakebin/fake_ffmpeg.py tests/test_capture_process.py
git commit -m "feat(studio): CaptureProcess com parada q/close/TERM/KILL e ffmpeg falso para os testes

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

**Correções da revisão (Steps 12–21).** (1) Com a Task 2 corrigida, o `mic_source_of_pid` levanta `PactlError`
quando o pactl falha ou estoura o timeout. Antes, a falha do pactl virava `None`, e o `check_mic` a tratava como
"Microfone desconectado ou trocado", parando a gravação. Agora a falha do pactl é "não sei": o `mic_status` diz
`MIC_UNKNOWN` e o `check_mic` devolve `None`. (2) Uma gravação parada antes do 3º frame (duplo clique em Gravar)
passava na verificação e virava uma tomada "gravado" sem vídeo que desse para renderizar.

- [ ] **Step 12: Testes do "não sei" do microfone (falha)**

Em `tests/test_capture.py`, substituir isto:

```python
    def test_other_source_or_missing(self):
        # nome invalido/desplugado: o PipeWire grava do mic padrao sem erro (spec 5.4.5)
        for found in (56, None):
            with mock.patch("studio.capture.devices.mic_source_of_pid", return_value=found):
                self.assertEqual(capture.check_mic(4321, 54), "Microfone desconectado ou trocado")
```

por isto:

```python
    def test_other_source_or_missing(self):
        # nome invalido/desplugado: o PipeWire grava do mic padrao sem erro (spec 5.4.5); None = o pactl
        # respondeu e o gravador nao aparece nele (sumiu)
        for found in (56, None):
            with mock.patch("studio.capture.devices.mic_source_of_pid", return_value=found):
                self.assertEqual(capture.check_mic(4321, 54), "Microfone desconectado ou trocado")
                self.assertEqual(capture.mic_status(4321, 54), capture.MIC_SWAPPED)

    def test_pactl_failure_is_unknown(self):
        # pactl que falhou ou estourou o timeout: "nao sei"; a gravacao nao para por isso
        with mock.patch("studio.devices._pactl", return_value=None):
            self.assertIsNone(capture.check_mic(4321, 54))
            self.assertEqual(capture.mic_status(4321, 54), capture.MIC_UNKNOWN)
        self.assertEqual(capture.MSG_MIC_UNKNOWN, "Não deu para conferir o microfone (o pactl não respondeu)")

    def test_mic_status_values(self):
        self.assertEqual((capture.MIC_OK, capture.MIC_SWAPPED, capture.MIC_UNKNOWN), ("ok", "trocado", "desconhecido"))
        with mock.patch("studio.capture.devices.mic_source_of_pid", return_value=54):
            self.assertEqual(capture.mic_status(4321, 54), capture.MIC_OK)
```

- [ ] **Step 13: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_capture -v`
Expected: `FAILED (errors=3)` (`Ran 32 tests`): `AttributeError: module 'studio.capture' has no attribute 'MIC_OK'`,
`... has no attribute 'mic_status'` e, no `test_pactl_failure_is_unknown`, a exceção vazando do `check_mic`:
`studio.devices.PactlError: O pactl não respondeu — não deu para conferir o microfone`

- [ ] **Step 14: `mic_status` e `check_mic` que devolve `None` quando não sabe**

Em `studio/capture.py`, substituir isto:

```python
MSG_MIC = "Microfone desconectado ou trocado"
MSG_STALL = "A câmera parou de enviar imagem"
```

por isto:

```python
MSG_MIC = "Microfone desconectado ou trocado"
MSG_MIC_UNKNOWN = "Não deu para conferir o microfone (o pactl não respondeu)"
MIC_OK, MIC_SWAPPED, MIC_UNKNOWN = "ok", "trocado", "desconhecido"     # resultados de mic_status
MSG_STALL = "A câmera parou de enviar imagem"
```

E em `studio/capture.py`, substituir isto:

```python
def check_mic(pid: int, expected_index: int) -> str | None:
    # o setpriv executa o ffmpeg no mesmo processo: o pid do Popen e o do ffmpeg
    return None if devices.mic_source_of_pid(pid) == expected_index else MSG_MIC
```

por isto:

```python
def mic_status(pid: int, expected_index: int) -> str:
    # o setpriv executa o ffmpeg no mesmo processo: o pid do Popen e o do ffmpeg
    try:
        found = devices.mic_source_of_pid(pid)
    except devices.PactlError:
        return MIC_UNKNOWN                  # pactl falhou/estourou o timeout: nao sei
    return MIC_OK if found == expected_index else MIC_SWAPPED


def check_mic(pid: int, expected_index: int) -> str | None:
    # MSG_MIC so com o pactl respondendo (outro indice ou gravador ausente); None = ok ou nao sei
    return MSG_MIC if mic_status(pid, expected_index) == MIC_SWAPPED else None
```

- [ ] **Step 15: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_capture -v`
Expected: `Ran 32 tests` e `OK`

- [ ] **Step 16: Commit**

```bash
git add studio/capture.py tests/test_capture.py
git commit -m "fix(capture): pactl sem resposta e 'nao sei' (mic_status MIC_UNKNOWN; check_mic devolve None)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 17: Teste da gravação curta demais (falha)**

Um MKV de 2 frames é gerado com o ffmpeg; os casos de 1 e de 3 pacotes são simulados com o `ffprobe` falso.
Em `tests/test_capture.py`, substituir isto:

```python
        data["format"] = {}
        with mock.patch("studio.capture.procs.ffprobe_json", return_value=data):
            self.assertEqual(capture.verify_capture(self.wav, need_video=False), "A gravação ficou com duração zero")
```

por isto:

```python
        data["format"] = {}
        with mock.patch("studio.capture.procs.ffprobe_json", return_value=data):
            self.assertEqual(capture.verify_capture(self.wav, need_video=False), "A gravação ficou com duração zero")

    def test_too_short_video(self):
        # duplo clique em Gravar: 1 ou 2 pacotes de video nao dao video (o render comeca no 2o frame)
        short = os.path.join(self.tmp.name, "curto.mkv")
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "testsrc2=s=320x240:r=30:d=0.066667", "-f", "lavfi",
                        "-i", "aevalsrc=0:s=48000:c=mono:d=0.066667", "-c:v", "mjpeg", "-c:a", "pcm_s16le", short],
                       check=True)
        self.assertEqual(capture.verify_capture(short, need_video=True), "Gravação curta demais")
        self.assertIsNone(capture.verify_capture(short, need_video=False))
        for n, want in ((1, "Gravação curta demais"), (3, None)):
            data = {"streams": [{"codec_type": "video", "nb_read_packets": str(n)},
                                {"codec_type": "audio", "nb_read_packets": "5"}], "format": {"duration": "0.1"}}
            with mock.patch("studio.capture.procs.ffprobe_json", return_value=data):
                self.assertEqual(capture.verify_capture(self.mkv, need_video=True), want)
        self.assertEqual((capture.MSG_SHORT, capture.MIN_VIDEO_PACKETS), ("Gravação curta demais", 3))
```

- [ ] **Step 18: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_capture -v`
Expected: `FAILED (failures=1)` (`Ran 33 tests`) com `AssertionError: None != 'Gravação curta demais'`: o MKV de 2
frames passa na verificação

- [ ] **Step 19: Recusar a gravação com menos de 3 pacotes de vídeo**

Em `studio/capture.py`, substituir isto:

```python
MSG_EMPTY = "A gravação está vazia ou corrompida"
```

por isto:

```python
MSG_EMPTY = "A gravação está vazia ou corrompida"
MSG_SHORT = "Gravação curta demais"
MIN_VIDEO_PACKETS = 3            # o render comeca no 2o frame (ancora) e precisa de 2 frames
```

E em `studio/capture.py`, substituir isto:

```python
    if need_video and not packets["video"]:
        return "A gravação não tem vídeo"
```

por isto:

```python
    if need_video and not packets["video"]:
        return "A gravação não tem vídeo"
    if need_video and packets["video"] < MIN_VIDEO_PACKETS:
        return MSG_SHORT                # parou antes do 3o frame (ex.: duplo clique em Gravar)
```

- [ ] **Step 20: Rodar e ver passar (módulos e suíte inteira)**

Run: `Applio/.venv/bin/python -m unittest tests.test_capture tests.test_capture_process -v`
Expected: `Ran 46 tests` (~5,5 s) e `OK (skipped=3)`

Run: `./run_tests.sh` e depois `pgrep -a ffmpeg`
Expected: `Ran 248 tests` (~33 s) e `OK (skipped=5)`, contando as Tasks 1–6 com as correções; o `pgrep` não
mostra nada

- [ ] **Step 21: Commit**

```bash
git add studio/capture.py tests/test_capture.py
git commit -m "fix(capture): gravacao com menos de 3 pacotes de video vira 'Gravacao curta demais'

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

**Notas para o executor**

- **`verify_capture` conta pacotes em vez de usar só o `media_info`.** Um MKV cortado em 1000 bytes ainda mostra
  `format.duration = 2.0` no `ffprobe`, porque a duração está no cabeçalho, mas não tem nenhum pacote. O teste
  `test_truncated_header_only` documenta isso. Com `-count_packets`, o `nb_read_packets` volta `N/A`, e o `_count` trata
  isso como 0. Isso custa ~60 ms para uma tomada de 100 MB com o arquivo em cache, porque só faz demux, sem
  decodificar.
  - Um MKV morto com SIGKILL no meio da gravação passa na verificação: o `ffprobe` lê os pacotes e estima uma duração.
    Só um arquivo sem nenhum pacote é recusado.
- **O filtro `fps=15` duplica frames** quando a câmera entrega menos de 15 fps, e contar o `seq` sempre daria ~15.
  - Por isso o `FrameReader.fps()` só conta frames diferentes do anterior. As duplicatas são idênticas byte a byte,
    porque o scale vem antes do fps no filtro.
  - O `seq` (`frames`) conta todos os frames e serve para a GUI saber se chegou frame novo para pintar.
  - Com a câmera a 30 fps, o preview fica limitado a 15. O fps real da câmera vem dos pacotes gravados
    (`timeline.video_info().fps_medido`).
- **O fps dos primeiros ~2 s sai baixo.** No real, o preview de 2 s mediu 12,3 fps e o A/V de 4 s mediu 14,95 fps,
  porque a janela ainda inclui o atraso do 1º frame. Por isso o `Watchdog.check_fps` só avisa depois de `fps_grace_s = 3 s`.
  O teste real rodou antes dessa carência entrar, mas não chama o `check_fps`, e o resto do módulo ficou igual.
- **Fechar o stdout não interrompe um `readinto` em andamento no Linux.** O leitor só morre na leitura seguinte, com
  `ValueError`, e só então o ffmpeg recebe EPIPE. Por isso o passo "close" precisa de alguns frames de folga, e o teste
  usa `t_close=1.0`. Os caminhos medidos com o falso:

  | Modo | rc | `stop_steps` |
  |---|---|---|
  | `normal` | 0 | `["q"]` |
  | `need_close` | 224 | `["q", "close"]` |
  | `ignore_q` | 255 | `["q", "close", "term"]` |
  | `hang` | -9 | `["q", "close", "term", "kill"]` |

  O modo `need_close` imita o probe t6c: o ffmpeg preso escrevendo no pipe só atende o `q` depois do EPIPE e sai com 224.
- **Sempre chame `wait_stopped()`, mesmo depois de uma falha rápida.** Com o processo já morto, ele volta na hora e fecha
  o stdin. Sem isso, o `-X dev` acusa `ResourceWarning: unclosed file`.
  - `early_failure()` devolve `None` depois de `request_stop()`, porque saída pedida não é falha.
  - Antes disso, qualquer saída vira mensagem, inclusive rc 0. Com o ffmpeg rodando, esperar 0,5 s não basta para
    afirmar sucesso.
- **O ffmpeg falso recebe o modo por `env FAKE_MODE=... python fake_ffmpeg.py`, dentro do argv.**
  - O `setpriv` e o `env` fazem exec, então o `pid` continua sendo o do "ffmpeg", e o `os.environ` do teste não muda.
  - No modo `hang` o SIGTERM é ignorado, então o pdeathsig não o mata. Por isso ele sai sozinho em 30 s ou quando o pai
    muda.
- **Comandos:**
  - O A/V é exatamente o da spec 5.1, sem `-nostats`. As linhas de progresso vão para o log com `\r`, e o
    `procs.tail` as separa porque `str.splitlines()` quebra em `\r`.
  - O só-áudio é o do probe t5 (com `-nostats`).
  - O só-preview é o "holder" dos probes t3/t4, com `-copyts` e `-ts mono2abs`, trocando o `scale=480:-2` pelo
    scale+pad.
- **`check_mic` só acusa troca com o `pactl` respondendo (Steps 12–16).** Pid ainda não registrado (o pactl responde,
  mas sem o gravador) conta como "Microfone desconectado ou trocado". Já o pactl que falhou ou estourou o timeout é
  "não sei": o `mic_status` devolve `MIC_UNKNOWN` e o `check_mic` devolve `None`. No teste real, o índice já estava
  lá em 1,2 s. A 1ª checagem da GUI deve ser em ~2 s (`MIC_CHECK_S = 2.0` na Task 10), e não antes.
- **Uso esperado pela GUI (Task 10):**
  1. `idx, err = preflight(mic, cam)`.
  2. `cap = CaptureProcess(build_av_cmd(mic, cam, take.raw_path), take.path("ffmpeg.log"), True)` e `cap.start()` na
     thread principal.
  3. Em `after(500)`, chamar `cap.early_failure()`.
  4. A cada 66 ms, `seq, frame = cap.latest_frame()`. O REC acende quando `seq > 0`, e o frame vai para
     `Image.frombuffer("RGB", (480, 270), frame, "raw", "RGB", 0, 1)`.
  5. A cada ~2 s: `mic_status(cap.pid, idx)` (ou `check_mic`), e os três checks do `Watchdog` com `time.monotonic()`,
     `cap.last_frame_monotonic` e `cap.started_monotonic`.
  6. Para parar: `cap.request_stop()` na thread principal. Depois, na thread de trabalho, `rc = cap.wait_stopped()`,
     `rc in ACCEPTED_RC` e `verify_capture(path, need_video)`.
- **Instabilidade da Task 1 (já corrigida lá).** `tests.test_procs.SpawnRunTest.test_child_dies_with_parent`
  falhava sob carga, com dois sintomas: "o filho sobreviveu" (o pai morria antes de o `setpriv` chamar o `prctl`) e
  `ProcessLookupError` dentro de `gone()`. A Task 1 agora espera o exec do sleep (`exec_done`) e `gone()` trata
  `ProcessLookupError`. Se algum teste de processo falhar aqui, a causa é desta task.
- **Gravação curta demais (Steps 17–21):** com o `q` chegando antes do 3º frame (duplo clique em Gravar), o MKV
  fica com 1 ou 2 pacotes de vídeo. O render começa no 2º frame e precisa de 2 frames (Task 5, `MIN_FRAMES`), então
  a captura já recusa com `MSG_SHORT` e a tomada vira "falhou" em vez de "gravado". O teste gera um MKV real de 2
  frames com `testsrc2=...:d=0.066667`, e o ffprobe conta exatamente 2 pacotes.

---

### Task 7: Conversor RVC (worker em subprocesso + cliente)

**Files:**
- Create: `studio/rvc_worker.py`
- Create: `studio/rvc_client.py`
- Test: `tests/test_rvc_worker.py`, `tests/test_rvc_client.py`

**Interfaces:**
- Consumes (Task 1):
  - `studio.config`: `APPLIO_DIR`, `LOGS_DIR`, `BASE_DIR`, `VENV_PYTHON`, `RVC_LOG`,
    `get_modelo(key: str) -> Modelo` (ValueError se desconhecido),
    `find_latest_checkpoint(model_key: str, logs_dir: str = LOGS_DIR) -> str | None`,
    `model_index_path(model_key: str, logs_dir: str = LOGS_DIR) -> str`.
  - `studio.procs.spawn(argv: list[str], **popen_kwargs) -> subprocess.Popen` (prefixa `setpriv --pdeathsig TERM --`).
  - `studio.procs.MSG_DISK_FULL`, `studio.procs.os_error_message(e: OSError) -> str` (Task 1, Step 38).
- Produces:
  - `studio/rvc_worker.py` (numpy + soundfile; só importa torch/Applio no modo real, dentro de `load_applio()`):
    `MAX_SINGLE_S = 40.0`, `MAX_PIECE_S = 30.0`, `SEARCH_S = 3.0`, `CTX_S = 0.5`, `XF_S = 0.005`, `FAKE_SR = 40000`,
    `APPLIO_PARAMS` (mesmos parâmetros do `orochi_studio.py`);
    `class ConvertError(Exception)` (str(e) = mensagem PT);
    `fake_mode() -> bool` (`STUDIO_RVC_FAKE == "1"`);
    `frame_energy(x: np.ndarray, sr: int) -> np.ndarray` (RMS por quadro de 10 ms);
    `plan_cuts(energy_10ms, total_s: float, max_piece_s: float = MAX_PIECE_S, search_s: float = SEARCH_S) -> list[float]`
    (fronteiras em segundos, começa em `0.0` e termina em `total_s`);
    `assemble(pieces: list[tuple[float, float, float, np.ndarray]], total_s: float, out_sr: int, xf_s: float = XF_S) -> np.ndarray`
    (`pieces` = `(a, b, inicio_do_contexto, audio_convertido)`; saída com exatamente `round(total_s*out_sr)` amostras);
    `fake_infer(src: str, dst: str) -> None`; `applio_infer(model_key: str, logs_dir: str = LOGS_DIR)` → `run(src, dst)`;
    `convert(inp: str, out: str, model_key: str, run_infer=None) -> dict` → `{"duracao": float, "sr": int, "pedacos": int}`
    (`run_infer(src: str, dst: str) -> None`; caminhos absolutos; grava `<out sem .wav>.part.wav` → `os.replace`);
    `handle(req) -> dict` (`OSError` vira `{"ok": false, "erro": os_error_message(e)}`, Step 14); `main() -> int`;
    `LOW_DISK_BYTES = 64 * 1024**2`; `disk_full(folder: str) -> bool`; `write_wav(path: str, y: np.ndarray, sr: int)
    -> None` (PCM16; `ConvertError(MSG_DISK_FULL)` se a escrita falhou com o disco quase cheio, senão
    `ConvertError("Não foi possível gravar <nome>")`). Pedaço sem saída do Applio com o disco quase cheio também
    vira `MSG_DISK_FULL`.
  - Protocolo (uma linha JSON por mensagem, ASCII):
    - requisição `{"id": str, "op": "ping"|"load"|"convert", ...}`; `convert` leva `input`, `output`, `model`;
    - resposta `{"id", "ok": true, ...}` ou `{"id", "ok": false, "erro": "<PT>"}`;
      `ping` → `{"id", "ok", "pid", "fake", "torch"}`; `load` → `{"id", "ok"}`;
      `convert` → `{"id", "ok", "duracao", "sr", "pedacos"}`; linha que não é JSON → `{"id": null, "ok": false, ...}`.
  - `studio/rvc_client.py` (só stdlib): `class RvcError(Exception)` (str(e) = mensagem PT); `CLOSE_WAIT_S = 10.0`;
    `class RvcClient(python: str = VENV_PYTHON, log_path: str = RVC_LOG, env: dict | None = None)` com
    `start() -> None` (**thread principal**), `alive() -> bool`,
    `request(op: str, timeout: float = 600, **params) -> dict` (RvcError se `ok:false`, worker morto, timeout ou resposta
    inválida; no timeout e na resposta inválida o worker é morto),
    `load() -> dict`, `convert(inp: str, out: str, model_key: str) -> dict`, `close() -> None`.

Todos os comandos rodam da raiz do repositório (`~/orochi-ia-homenagem`).

- [ ] **Step 1: Escrever o teste do worker que falha**

`tests/test_rvc_worker.py`:

```python
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_rvc_worker -v`
Expected: ERROR com `ImportError: cannot import name 'rvc_worker' from 'studio'`

- [ ] **Step 3: Implementar o worker**

A base é o `docs/superpowers/probes/2026-09-26/rvc/anchored.py`, verificado sem deriva em 90 s e 300 s. O planejamento
dos cortes e a remontagem são os mesmos. A diferença está no crossfade: o número de amostras usa `int(round(...))`
em vez de `int(...)`, e o valor continua 200 a 40 kHz.

`studio/rvc_worker.py`:

```python
"""Worker do RVC: subprocesso que carrega o Applio uma vez e converte em pedacos sem deriva.

Roda como [VENV_PYTHON, "-m", "studio.rvc_worker"] com cwd=BASE_DIR. Protocolo: uma requisicao JSON por
linha no stdin e uma resposta JSON por linha num fd dedicado (os prints do Applio vao para o stderr).
"""

import contextlib
import json
import os
import sys
import tempfile
import traceback

# garante o pacote studio no path mesmo depois do chdir para o Applio
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from studio.config import (APPLIO_DIR, LOGS_DIR, find_latest_checkpoint, get_modelo,  # noqa: E402
                           model_index_path)

MAX_SINGLE_S = 40.0     # ate ~41 s o RVC preserva o tempo: um pedaco so
MAX_PIECE_S = 30.0
SEARCH_S = 3.0
CTX_S = 0.5
XF_S = 0.005
FAKE_SR = 40000         # taxa do conversor falso (a mesma do silvio)

# mesmos parametros do orochi_studio.py
APPLIO_PARAMS = dict(pitch=0, index_rate=0.75, volume_envelope=1.0, protect=0.33, f0_method="rmvpe",
                     split_audio=False, f0_autotune=False, f0_autotune_strength=1.0, proposed_pitch=False,
                     proposed_pitch_threshold=155.0, clean_audio=False, clean_strength=0.5,
                     export_format="WAV", embedder_model="contentvec")


class ConvertError(Exception):
    """Erro esperado da conversao; str(e) e a mensagem para o usuario."""


def fake_mode() -> bool:
    return os.environ.get("STUDIO_RVC_FAKE") == "1"


def frame_energy(x: np.ndarray, sr: int) -> np.ndarray:
    # RMS por quadro de 10 ms
    frame = sr // 100
    n = len(x) // frame
    return np.sqrt(np.mean(x[:n * frame].reshape(n, frame) ** 2, axis=1))


def plan_cuts(energy_10ms, total_s: float, max_piece_s: float = MAX_PIECE_S,
              search_s: float = SEARCH_S) -> list[float]:
    # corte no quadro mais quieto a +-search_s do nominal (max_piece_s - search_s apos o corte anterior)
    e = np.asarray(energy_10ms)
    step, search = int(round((max_piece_s - search_s) * 100)), int(round(search_s * 100))
    cuts = [0]
    while total_s - cuts[-1] / 100 > max_piece_s:
        nom = cuts[-1] + step
        lo, hi = nom - search, min(nom + search, len(e) - 1)
        cuts.append(lo + int(np.argmin(e[lo:hi])))
    return [c / 100 for c in cuts] + [total_s]


def assemble(pieces: list[tuple[float, float, float, np.ndarray]], total_s: float, out_sr: int,
             xf_s: float = XF_S) -> np.ndarray:
    # pieces = (a, b, inicio_do_contexto, audio convertido); cada trecho volta ao offset exato
    out = np.zeros(int(round(total_s * out_sr)))
    xf = int(round(xf_s * out_sr))
    last = len(pieces) - 1
    for i, (a, b, ca, y) in enumerate(pieces):
        s0, s1 = int(round(a * out_sr)), int(round(b * out_sr))
        off = int(round((a - ca) * out_sr))
        ext = xf if i < last else 0          # invade o trecho seguinte para o crossfade
        seg = np.asarray(y, dtype=float)[off:off + (s1 - s0) + ext]
        seg = np.pad(seg, (0, max(0, (s1 - s0) + ext - len(seg))))
        if i > 0 and xf:
            ramp = np.linspace(0.0, 1.0, xf)
            out[s0:s0 + xf] = out[s0:s0 + xf] * (1 - ramp) + seg[:xf] * ramp
            out[s0 + xf:s0 + len(seg)] = seg[xf:]
        else:
            out[s0:s0 + len(seg)] = seg
    return out


def fake_infer(src: str, dst: str) -> None:
    # conversor identidade (STUDIO_RVC_FAKE=1): reamostra para 40 kHz e suja o stdout de proposito
    print(f"[fake applio] Converting audio '{src}'...", flush=True)
    os.write(1, b"[fake applio] lixo de biblioteca nativa no fd 1\n")
    x, sr = sf.read(src, always_2d=True)
    x = x.mean(axis=1)
    n = int(round(len(x) * FAKE_SR / sr))
    y = np.interp(np.arange(n) * (sr / FAKE_SR), np.arange(len(x)), x)
    sf.write(dst, y, FAKE_SR, subtype="PCM_16")


_core = None


def load_applio():
    # importa o Applio uma vez; ele le os.getcwd() no import (o main ja fez chdir para APPLIO_DIR)
    global _core
    if _core is None:
        if APPLIO_DIR not in sys.path:
            sys.path.insert(0, APPLIO_DIR)
        import core
        core.import_voice_converter()
        _core = core
    return _core


def applio_infer(model_key: str, logs_dir: str = LOGS_DIR):
    pth = find_latest_checkpoint(model_key, logs_dir)
    if not pth:
        raise ConvertError(f"Nenhum checkpoint do modelo {get_modelo(model_key).label} em {logs_dir}")
    index = model_index_path(model_key, logs_dir)
    core = load_applio()

    def run(src: str, dst: str) -> None:
        core.run_infer_script(input_path=src, output_path=dst, pth_path=pth, index_path=index,
                              **APPLIO_PARAMS)
    return run


def warm_up() -> None:
    if fake_mode():
        print("[fake applio] carregando modelo (falso)", flush=True)
        return
    load_applio()


def free_gpu() -> None:
    torch = sys.modules.get("torch")
    if torch is not None and not fake_mode() and torch.cuda.is_available():
        torch.cuda.empty_cache()


def convert_pieces(x: np.ndarray, sr: int, cuts: list[float], work: str, run_infer):
    total_s = len(x) / sr
    pieces, out_sr = [], None
    for i, (a, b) in enumerate(zip(cuts, cuts[1:])):
        ca, cb = max(0.0, a - CTX_S), min(total_s, b + CTX_S)
        src, dst = os.path.join(work, f"p{i:03d}.wav"), os.path.join(work, f"p{i:03d}_out.wav")
        sf.write(src, x[int(round(ca * sr)):int(round(cb * sr))], sr, subtype="PCM_16")
        run_infer(src, dst)
        if not os.path.isfile(dst):
            raise ConvertError("O RVC não gerou o áudio convertido (veja studio_rvc.log)")
        y, psr = sf.read(dst, always_2d=True)
        if out_sr not in (None, psr):
            raise ConvertError("O RVC devolveu taxas de amostragem diferentes entre os pedaços")
        out_sr = psr
        pieces.append((a, b, ca, y.mean(axis=1)))
    return pieces, out_sr


def convert(inp: str, out: str, model_key: str, run_infer=None) -> dict:
    try:
        get_modelo(model_key)
    except ValueError:
        raise ConvertError(f"Modelo desconhecido: {model_key}") from None
    if not (os.path.isabs(inp) and os.path.isabs(out)):
        raise ConvertError("Os caminhos da conversão precisam ser absolutos")
    if not os.path.isfile(inp):
        raise ConvertError(f"Arquivo de entrada não encontrado: {inp}")
    if not os.path.isdir(os.path.dirname(out)):
        raise ConvertError(f"Pasta de saída não existe: {os.path.dirname(out)}")
    try:
        x, sr = sf.read(inp, always_2d=True)
    except RuntimeError as e:
        raise ConvertError(f"Não foi possível ler {os.path.basename(inp)}: {e}") from None
    x = x.mean(axis=1)
    if len(x) == 0:
        raise ConvertError(f"Áudio vazio: {os.path.basename(inp)}")
    total_s = len(x) / sr
    cuts = plan_cuts(frame_energy(x, sr), total_s) if total_s > MAX_SINGLE_S else [0.0, total_s]
    if run_infer is None:
        run_infer = fake_infer if fake_mode() else applio_infer(model_key)
    part = os.path.splitext(out)[0] + ".part.wav"
    try:
        with tempfile.TemporaryDirectory(prefix=".rvc_", dir=os.path.dirname(out)) as work:
            pieces, out_sr = convert_pieces(x, sr, cuts, work, run_infer)
        y = assemble(pieces, total_s, out_sr)
        sf.write(part, y, out_sr, subtype="PCM_16")
        info = sf.info(part)
        if info.samplerate != out_sr or info.frames != len(y) or len(y) == 0:
            raise ConvertError("Saída do conversor inválida")
        os.replace(part, out)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(part)
        raise
    return {"duracao": len(y) / out_sr, "sr": out_sr, "pedacos": len(pieces)}


def handle(req) -> dict:
    rid = req.get("id") if isinstance(req, dict) else None
    op = req.get("op") if isinstance(req, dict) else None
    try:
        if op == "ping":
            return {"id": rid, "ok": True, "pid": os.getpid(), "fake": fake_mode(),
                    "torch": "torch" in sys.modules}
        if op == "load":
            warm_up()
            return {"id": rid, "ok": True}
        if op == "convert":
            args = [req.get(k) for k in ("input", "output", "model")]
            if not all(isinstance(a, str) and a for a in args):
                raise ConvertError("Requisição de conversão incompleta")
            return {"id": rid, "ok": True, **convert(*args)}
        return {"id": rid, "ok": False, "erro": f"Operação desconhecida: {op}"}
    except ConvertError as e:
        return {"id": rid, "ok": False, "erro": str(e)}
    except Exception as e:
        traceback.print_exc()
        return {"id": rid, "ok": False, "erro": f"Falha no conversor: {type(e).__name__}: {e}"}
    finally:
        if op == "convert":
            free_gpu()


def main() -> int:
    # protocolo num fd dedicado; o fd 1 (prints do Applio e de libs nativas) passa a ir para o stderr
    proto = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    os.chdir(APPLIO_DIR)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
        except ValueError:
            resp = {"id": None, "ok": False, "erro": "Requisição inválida (JSON)"}
        else:
            resp = handle(req)
        proto.write(json.dumps(resp) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_rvc_worker -v`
Expected: `Ran 17 tests in ~1.2s` e `OK`

- [ ] **Step 5: Commit**

```bash
git add studio/rvc_worker.py tests/test_rvc_worker.py
git commit -m "feat(studio): rvc_worker com conversao em pedacos sem deriva e protocolo JSON em fd dedicado

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: Escrever o teste do cliente que falha**

Os testes `FakeWorkerTest` sobem o worker de verdade em modo falso (`STUDIO_RVC_FAKE=1`), e o `fake_infer` suja o
stdout de propósito. Os testes `StubWorkerTest` trocam o "python" por um script `sh` que simula quatro casos: worker
travado, worker morrendo, lixo no protocolo e id velho. O `RealRvcTest` só roda com `RUN_HARDWARE=1`.

`tests/test_rvc_client.py`:

```python
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
```

- [ ] **Step 7: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_rvc_client -v`
Expected: ERROR com `ModuleNotFoundError: No module named 'studio.rvc_client'`

- [ ] **Step 8: Implementar o cliente**

`studio/rvc_client.py`:

```python
"""Lado do app do conversor RVC: inicia o worker e troca JSON por linha com ele (so stdlib)."""

import json
import os
import queue
import subprocess
import threading
import time

from studio import procs
from studio.config import BASE_DIR, RVC_LOG, VENV_PYTHON

WORKER_MODULE = "studio.rvc_worker"
CLOSE_WAIT_S = 10.0


class RvcError(Exception):
    """str(e) e a mensagem para o usuario."""


def _read_lines(stream, lines: queue.Queue) -> None:
    # thread leitora: repassa as linhas do protocolo; None = EOF (worker morreu ou fechou)
    try:
        for line in stream:
            lines.put(line)
    except (OSError, ValueError):
        pass
    finally:
        lines.put(None)


class RvcClient:
    def __init__(self, python: str = VENV_PYTHON, log_path: str = RVC_LOG, env: dict | None = None):
        self.python = python
        self.log_path = log_path
        self.env = dict(env or {})
        self._proc: subprocess.Popen | None = None
        self._lines: queue.Queue | None = None
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._seq = 0

    def start(self) -> None:
        # chamar na thread principal: o PDEATHSIG vale enquanto a thread que fez o spawn viver
        if self.alive():
            return
        self._reap()
        env = {**os.environ, **self.env}
        with open(self.log_path, "ab") as log:
            log.write(f"\n=== worker RVC iniciado {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n".encode())
            log.flush()
            p = procs.spawn([self.python, "-m", WORKER_MODULE], cwd=BASE_DIR, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
        lines: queue.Queue = queue.Queue()
        reader = threading.Thread(target=_read_lines, args=(p.stdout, lines), name="rvc-reader", daemon=True)
        reader.start()
        self._proc, self._lines, self._reader = p, lines, reader

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def request(self, op: str, timeout: float = 600, **params) -> dict:
        if not self._lock.acquire(timeout=timeout):
            raise RvcError("O conversor está ocupado com outro trabalho")
        try:
            return self._request(op, timeout, params)
        finally:
            self._lock.release()

    def load(self) -> dict:
        return self.request("load")

    def convert(self, inp: str, out: str, model_key: str) -> dict:
        # o worker faz chdir para o Applio: so caminhos absolutos
        return self.request("convert", input=os.path.abspath(inp), output=os.path.abspath(out), model=model_key)

    def close(self) -> None:
        p = self._proc
        if p is None:
            return
        try:
            p.stdin.close()                 # EOF: o worker termina o job atual e sai com 0
        except (OSError, ValueError):
            pass
        try:
            p.wait(timeout=CLOSE_WAIT_S)
        except subprocess.TimeoutExpired:
            p.terminate()
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        self._reap()

    def _request(self, op: str, timeout: float, params: dict) -> dict:
        p, lines = self._proc, self._lines
        if p is None or p.poll() is not None:
            raise RvcError("O conversor não está rodando")
        self._seq += 1
        rid = str(self._seq)
        try:
            p.stdin.write(json.dumps({"id": rid, "op": op, **params}) + "\n")
            p.stdin.flush()
        except (OSError, ValueError):
            raise RvcError(self._died_message(p)) from None
        deadline = time.monotonic() + timeout
        while True:
            try:
                line = lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                self._kill(p)
                raise RvcError(f"O conversor demorou demais ({op}) e foi encerrado") from None
            if line is None:
                raise RvcError(self._died_message(p))
            try:
                resp = json.loads(line)
            except ValueError:
                resp = None
            if not isinstance(resp, dict):
                self._kill(p)
                raise RvcError(f"Resposta inválida do conversor: {line.strip()[:200]}")
            if resp.get("id") != rid:
                continue                    # resposta velha de outro pedido
            if not resp.get("ok"):
                raise RvcError(resp.get("erro") or f"O conversor falhou ({op})")
            return resp

    def _died_message(self, p: subprocess.Popen) -> str:
        try:
            rc = p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._kill(p)
            rc = p.returncode
        return f"O conversor fechou inesperadamente (código {rc}) — veja {os.path.basename(self.log_path)}"

    @staticmethod
    def _kill(p: subprocess.Popen) -> None:
        try:
            p.kill()
            p.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _reap(self) -> None:
        p, reader = self._proc, self._reader
        if reader is not None:
            reader.join(timeout=2)
        if p is not None:
            for stream in (p.stdin, p.stdout):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        self._proc = self._lines = self._reader = None
```

- [ ] **Step 9: Rodar e ver passar (e a suíte inteira)**

Run: `Applio/.venv/bin/python -m unittest tests.test_rvc_client -v`
Expected: `Ran 15 tests in ~2.4s` e `OK (skipped=1)`. O teste pulado é o `test_silvio_6s`, que precisa de
`RUN_HARDWARE=1`.

Run: `./run_tests.sh`
Expected: `Ran 280 tests` (~34 s) e `OK (skipped=6)`, contando as Tasks 1–7 (as correções desta task vêm depois).

Run: `pgrep -af "rvc_worker|sleep 30" | grep -v pgrep || echo "sem worker"`
Expected: `sem worker`. Nenhum worker nem stub pode sobrar.

- [ ] **Step 10: Teste real com a GPU (uma vez)**

Antes, confira que a GPU está livre. Rode `nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader`
e espere algo como `837 MiB, 0 %`. Depois rode:

Run: `RUN_HARDWARE=1 Applio/.venv/bin/python -m unittest tests.test_rvc_client.RealRvcTest -v`
Expected (medido):

```
test_silvio_6s (tests.test_rvc_client.RealRvcTest.test_silvio_6s) ... 
[real] {'id': '2', 'ok': True, 'duracao': 6.0, 'sr': 40000, 'pedacos': 1} conversão 3.31 s, saída 6.0000 s @ 40000
ok
----------------------------------------------------------------------
Ran 1 test in 10.014s

OK
```

- [ ] **Step 11: Commit**

```bash
git add studio/rvc_client.py tests/test_rvc_client.py
git commit -m "feat(studio): rvc_client com spawn via setpriv, timeout por thread leitora e RvcError

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

**Correção da revisão (Steps 12–16): disco cheio na conversão.** A limpeza já existia: o `TemporaryDirectory`
apaga a pasta `.rvc_*`, e o `except BaseException` apaga o `*.part.wav`. Faltava a mensagem. Um `OSError(ENOSPC)`
voltava como `Falha no conversor: OSError: [Errno 28] ...`. E o `soundfile` nem levanta `OSError` quando o disco
enche: o `write` para no meio com `AssertionError` (`assert written == len(data)`), e o Applio só imprime o erro e
volta sem gravar a saída. Estes passos devolvem "Disco cheio — libere espaço" nos três casos e conferem que nada
sobra na pasta da tomada.

- [ ] **Step 12: Testes de disco cheio no worker (falha)**

Em `tests/test_rvc_worker.py`, substituir isto:

```python
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
```

por isto:

```python
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
```

E em `tests/test_rvc_worker.py`, substituir isto:

```python
if __name__ == "__main__":
    unittest.main()
```

por isto:

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 13: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_rvc_worker -v`
Expected: `FAILED (failures=4)` (`Ran 20 tests`):
`AssertionError: ... 'Falha no conversor: OSError: [Errno 28] No space left on device' ... != ... 'Disco cheio — libere
espaço'`; os dois subtestes de `test_part_write_stops_midway` com o `AssertionError` cru do `soundfile` (sem mensagem);
e `AssertionError: 'O RVC não gerou o áudio convertido (veja studio_rvc.log)' != 'Disco cheio — libere espaço'`.
Os asserts de limpeza nem chegam a rodar, mas conferi à parte, com o código anterior, que nos dois primeiros
casos a pasta já ficava só com `audio.wav`: o que faltava era a mensagem.

- [ ] **Step 14: Mensagem de disco cheio no worker**

Em `studio/rvc_worker.py`, substituir isto:

```python
import contextlib
import json
import os
import sys
import tempfile
import traceback
```

por isto:

```python
import contextlib
import json
import os
import shutil
import sys
import tempfile
import traceback
```

E em `studio/rvc_worker.py`, substituir isto:

```python
from studio.config import (APPLIO_DIR, LOGS_DIR, find_latest_checkpoint, get_modelo,  # noqa: E402
                           model_index_path)
```

por isto:

```python
from studio.config import (APPLIO_DIR, LOGS_DIR, find_latest_checkpoint, get_modelo,  # noqa: E402
                           model_index_path)
from studio.procs import MSG_DISK_FULL, os_error_message  # noqa: E402
```

E em `studio/rvc_worker.py`, substituir isto:

```python
FAKE_SR = 40000         # taxa do conversor falso (a mesma do silvio)
```

por isto:

```python
FAKE_SR = 40000         # taxa do conversor falso (a mesma do silvio)
LOW_DISK_BYTES = 64 * 1024**2   # com menos que isso livre, uma escrita que falhou e disco cheio
```

E em `studio/rvc_worker.py`, substituir isto:

```python
def fake_mode() -> bool:
    return os.environ.get("STUDIO_RVC_FAKE") == "1"
```

por isto:

```python
def fake_mode() -> bool:
    return os.environ.get("STUDIO_RVC_FAKE") == "1"


def disk_full(folder: str) -> bool:
    try:
        return shutil.disk_usage(folder).free < LOW_DISK_BYTES
    except OSError:
        return False


def write_wav(path: str, y: np.ndarray, sr: int) -> None:
    # com o disco cheio o soundfile nao levanta OSError: o write para no meio (AssertionError) ou o libsndfile falha
    try:
        sf.write(path, y, sr, subtype="PCM_16")
    except (AssertionError, sf.SoundFileError):
        if disk_full(os.path.dirname(path)):
            raise ConvertError(MSG_DISK_FULL) from None
        raise ConvertError(f"Não foi possível gravar {os.path.basename(path)}") from None
```

E em `studio/rvc_worker.py`, substituir isto:

```python
        sf.write(src, x[int(round(ca * sr)):int(round(cb * sr))], sr, subtype="PCM_16")
        run_infer(src, dst)
        if not os.path.isfile(dst):
            raise ConvertError("O RVC não gerou o áudio convertido (veja studio_rvc.log)")
```

por isto:

```python
        write_wav(src, x[int(round(ca * sr)):int(round(cb * sr))], sr)
        run_infer(src, dst)
        if not os.path.isfile(dst):
            # o Applio so imprime o erro (ex.: disco cheio) e volta sem gravar a saida
            raise ConvertError(MSG_DISK_FULL if disk_full(work) else
                               "O RVC não gerou o áudio convertido (veja studio_rvc.log)")
```

E em `studio/rvc_worker.py`, substituir isto:

```python
        y = assemble(pieces, total_s, out_sr)
        sf.write(part, y, out_sr, subtype="PCM_16")
```

por isto:

```python
        y = assemble(pieces, total_s, out_sr)
        write_wav(part, y, out_sr)
```

E em `studio/rvc_worker.py`, substituir isto:

```python
    except ConvertError as e:
        return {"id": rid, "ok": False, "erro": str(e)}
    except Exception as e:
```

por isto:

```python
    except ConvertError as e:
        return {"id": rid, "ok": False, "erro": str(e)}
    except OSError as e:
        traceback.print_exc()           # vai para o studio_rvc.log
        return {"id": rid, "ok": False, "erro": os_error_message(e)}
    except Exception as e:
```

- [ ] **Step 15: Rodar e ver passar (módulos e suíte inteira)**

Run: `Applio/.venv/bin/python -m unittest tests.test_rvc_worker tests.test_rvc_client -v`
Expected: `Ran 35 tests` (~3,2 s) e `OK (skipped=1)`

Run: `./run_tests.sh` e depois `pgrep -af "rvc_worker|sleep 30" | grep -v pgrep || echo "sem worker"`
Expected: `Ran 283 tests` (~35 s) e `OK (skipped=6)`, contando as Tasks 1–7 com as correções; depois,
`sem worker`

- [ ] **Step 16: Commit**

```bash
git add studio/rvc_worker.py tests/test_rvc_worker.py
git commit -m "fix(rvc_worker): disco cheio responde 'Disco cheio' em PT e nao deixa .rvc_* nem .part.wav

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

**Notas para o executor**

- **Stub com `cat > /dev/null` fecha o pipe do protocolo.** A primeira versão do teste de timeout usava
  `exec cat > /dev/null`. O stdout do stub virava `/dev/null`, então o cliente recebia EOF na hora. Ele esperava 5 s
  pelo processo, que continuava vivo, e só então o matava, com a mensagem "fechou inesperadamente (código -9)". Por
  isso os stubs usam `exec sleep 30`, que mantém o stdout aberto, ou `read line`, que sai no EOF do stdin. O cliente
  mantém esse comportamento de propósito: um worker que fecha o stdout e continua vivo é morto depois de 5 s.
- **`close()` espera até `CLOSE_WAIT_S` (10 s)** o worker terminar o job atual. Depois disso, manda SIGTERM. Um stub
  que ignora o EOF deixava a suíte 10 s mais lenta. O teste do fallback faz patch em
  `studio.rvc_client.CLOSE_WAIT_S`.
- **`request()` segura o lock durante o job inteiro,** que pode levar até 600 s. Ele só deve ser chamado na thread de
  trabalho (JobRunner), nunca na thread do Tk. O `start()` é o contrário: tem que ser chamado na thread principal,
  porque o PDEATHSIG vale enquanto viver a thread que fez o spawn. Depois de um `RvcError` (worker morto ou timeout),
  quem recria o worker é a GUI, na thread principal, antes do próximo job ("Conversor reiniciado").
- **O `fake_infer` suja o fd 1 com `os.write`,** e isso passa por fora do `sys.stdout`. Nos testes em processo, o
  `fd1_silenced()` faz `dup2` de `/dev/null` no fd 1 para não poluir a saída do unittest. No worker, o `os.dup2(2, 1)`
  manda esse lixo para o log. O teste cru do protocolo exige que **todas** as linhas do stdout sejam JSON. Conferi que,
  sem o `dup2`, o teste falha com `JSONDecodeError`.
- **O cliente é estrito:** uma linha que não é JSON no protocolo gera RvcError e mata o worker. Assim, um vazamento de
  print não passa despercebido.
- **O Applio não avisa quando falha.** O `run_infer_script` retorna sem erro quando o `pth` está vazio, e o
  `convert_audio_format` só imprime a exceção. Por isso o worker confere se o arquivo de saída de cada pedaço existe
  ("O RVC não gerou o áudio convertido").
- **Duração exata:** mesmo com um pedaço só, a saída passa pelo `assemble`. Ele completa os ≤ 10 ms que o RVC perde,
  então `duracao` é igual à duração da entrada (6.0000 s no teste real). No sintético de 90 s saíram 4 pedaços, com
  cortes `[0, 29.93, 56.81, 85.77, 90]`. O erro máximo contra a entrada reamostrada foi de 0,8 LSB, que é só
  quantização. Conferi que, trocando `off` por 0 no `assemble`, os testes de identidade falham.
- **Medições do teste real:** com a GPU ociosa em 837 MiB, o pico total foi de 2175 MiB, amostrado a cada 0,5 s. Ao
  fechar o worker, a memória voltou para 837 MiB. O teste levou 10 s: `load`, a 1ª conversão com o carregamento do
  modelo (3,31 s) e o `close`. Com `PYTHONDONTWRITEBYTECODE=1`, o `find ~/orochi-ia-homenagem -newer marca` voltou
  vazio.
- **`test_rvc_client` importa helpers de `tests.test_rvc_worker`.** Importe só funções. Se importar uma classe
  `TestCase`, o discover roda essa classe duas vezes.
- **Disco cheio (Steps 12–16):** o `soundfile` 0.14 não levanta `OSError` quando o disco enche. O `SoundFile.write`
  faz `assert written == len(data)` e sai com um `AssertionError` sem mensagem, e o `open` falho levanta
  `LibsndfileError` (subclasse de `SoundFileError`). Conferi abrindo `/dev/full`: `LibsndfileError(2, "Error opening
  '/dev/full': ")`. Por isso o `write_wav` pega os dois e decide "disco cheio" pelo espaço livre
  (`LOW_DISK_BYTES`). Os testes trocam o `shutil.disk_usage` por `mock.Mock(free=...)`.

---

### Task 8: Envio para o Google Drive (`drive.py`, `enviar_drive.py`, README)

**Files:**
- Create: `studio/drive.py` (só stdlib)
- Create: `enviar_drive.py` (executável, só stdlib; roda com o `python3` do sistema ou o do venv)
- Create: `tests/fakebin/rclone` (executável; rclone FALSO em Python, controlado por variáveis de ambiente)
- Create: `README.md` (título + seção "Google Drive: configuração inicial"; a Task 12 acrescenta uso e calibração)
- Test: `tests/test_drive.py`, `tests/test_drive_upload.py`, `tests/test_enviar_drive.py`

**Interfaces:**
- Consumes (Task 1):
  - `studio.config`: `RCLONE_REMOTE`, `REC_DIR`, `VIDEOS_DIR`, `ESTADO_PATH`, `BASE_DIR`,
    `get_modelo(key: str) -> Modelo`, `metadata_tags(m: Modelo) -> dict[str, str]` (o `["comment"]` vira a descrição
    do arquivo no Drive), `load_estado(path: str = ESTADO_PATH) -> tuple[dict, str | None]`,
    `save_estado(estado: dict, path: str = ESTADO_PATH) -> None`,
    `merge_estado(changes: dict, path: str = ESTADO_PATH) -> tuple[dict, str | None]` (Task 1, Step 28; usado no
    Step 30).
  - `studio.procs`: `spawn(argv, **popen_kwargs) -> subprocess.Popen`, `run(argv, timeout=None, **kw)`,
    `ffprobe_json(path: str, *args: str) -> dict`, `ProcError`.
  - `studio.takes`: `Take` (`Take.load(take_dir)`, `.saidas`, `.save()`).
- Produces (Tasks 9 e 11 usam; nomes do CONTRACT.md + extras marcados com *):
  - `class DriveError(Exception)` — `str(e)` é a mensagem PT.
  - `parse_folder_link(s: str) -> tuple[str, str | None]` — `(folder_id, resource_key)`; `DriveError` se inválido,
    inclusive se `s` não for `str` (Step 20: `"O link da pasta do Drive tem que ser um texto"`; `None` = vazio).
  - `rclone_bin() -> str | None` — `shutil.which("rclone")` ou `~/.local/bin/rclone` executável.
  - `build_copyto_cmd(local: str, dest_name: str, folder_id: str, resource_key: str | None = None,
    description: str | None = None) -> list[str]`; `build_lsjson_cmd(dest_name: str, folder_id: str,
    resource_key: str | None = None) -> list[str]`; `reconnect_cmd() -> list[str]` (sem prefixo setpriv; quem
    executa passa por `procs.spawn`).
  - `parse_log_line(line: str) -> dict | None`; `classify_error(rc: int, log_text: str) -> str`.
  - `check_uploadable(path: str, videos_dir: str = VIDEOS_DIR) -> str | None` (Step 25: `None` = ok; senão o motivo
    em PT. Recusa `.part`, nome fora de `<tomada>_<modelo>_IA.mp4` ou com modelo desconhecido, arquivo cujo
    `realpath` não está direto em `realpath(videos_dir)` (symlink para fora também) e comment diferente de
    `metadata_tags(get_modelo(<modelo do nome>))["comment"]`); `md5_file(path: str) -> str`.
  - `class UploadLock(videos_dir: str = VIDEOS_DIR)` — context manager; `__enter__` levanta
    `DriveError("Outro envio já está em andamento")`.
  - `list_videos(videos_dir: str = VIDEOS_DIR) -> list[str]`.
  - `upload_files(paths: list[str], folder_link: str, on_progress=None, cancel: threading.Event | None = None,
    dry_run: bool = False, videos_dir: str = VIDEOS_DIR*) -> list[dict]` (`videos_dir` vai para o
    `check_uploadable`: arquivo fora dele vira erro do item, sem chamar o rclone) — cada item
    `{"arquivo", "ok", "pulado", "md5", "erro", "aviso"*}`; `on_progress(nome: str, fracao: float)` (0.0 no
    início de cada arquivo, 1.0 no fim); levanta `DriveError` só para link inválido, rclone ausente ou trava ocupada.
    Rodar numa thread de trabalho (o rclone nasce e é esperado na mesma thread, por causa do `--pdeathsig`).
  - `record_sent(result: dict, rec_dir: str = REC_DIR, now: datetime | None = None) -> str | None`* — grava
    `saidas[<modelo>]["enviado"] = {"md5", "quando"}` no `take.json`; chamar na thread principal da GUI (ou na CLI).
  - `split_video_name(name: str) -> tuple[str, str] | None`*, `drive_description(dest_name: str) -> str`*.
  - Constantes*: `MSG_NO_RCLONE`, `MSG_NO_CONFIG`, `MSG_RELOGIN`, `MSG_NO_ACCESS`, `MSG_QUOTA`, `MSG_CANCELLED`,
    `MSG_NO_DESCRIPTION`, `FATAL_MSGS`, `COPY_FLAGS`, `LOCK_NAME = ".envio.lock"`.
  - CLI: `enviar_drive.py [--pasta LINK] [--arquivo CAMINHO]... [--dry-run] [--estado P] [--videos-dir D]
    [--rec-dir D]`; saída 0 = tudo enviado/pulado, 1 = alguma falha (ou rclone ausente/trava ocupada), 2 = sem
    pasta configurada ou link inválido, 130 = Ctrl-C. O `--pasta` grava com `merge_estado` (Step 30).

Todos os comandos rodam da raiz do repositório (`~/orochi-ia-homenagem`). **Não instale o rclone nem crie
`~/.config/rclone` para esta task**: os testes usam só o rclone falso de `tests/fakebin/`, posto na frente do
`PATH` apenas dentro dos testes.

- [ ] **Step 1: Escrever o teste que falha (parser, comandos, log, erros)**

`tests/test_drive.py`:

```python
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
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_drive -v`
Expected: ERROR com `ImportError: cannot import name 'drive' from 'studio'` (`FAILED (errors=1)`).

- [ ] **Step 3: Implementar a primeira parte de `studio/drive.py`**

`studio/drive.py`:

```python
"""Envio dos videos finais para uma pasta do Google Drive via rclone (so stdlib)."""

import json
import os
import re
import shutil
from urllib.parse import parse_qs, urlparse

from studio.config import RCLONE_REMOTE, get_modelo, metadata_tags


class DriveError(Exception):
    pass


MSG_NO_RCLONE = "rclone não instalado — veja o README"
MSG_NO_CONFIG = "Drive não configurado — rode a configuração"
MSG_RELOGIN = "Login do Drive expirou — clique Reconectar"
MSG_NO_ACCESS = "Essa conta Google não tem acesso a essa pasta (ou o link está errado)"
MSG_QUOTA = "Seu Drive está cheio — os envios contam na sua cota"
MSG_CANCELLED = "Envio cancelado"
# erros que valem para a pasta inteira: nao adianta tentar os proximos arquivos
FATAL_MSGS = (MSG_NO_CONFIG, MSG_RELOGIN, MSG_NO_ACCESS, MSG_QUOTA)

# link -> ID (base: probes/2026-09-26/drive/drive_probe.py, 12 casos verificados)
_ID = r"[A-Za-z0-9_-]{10,}"
_BARE_ID = re.compile(rf"^{_ID}$")
_FOLDER_PATH = re.compile(rf"/folders/({_ID})")
_FILE_PATH = re.compile(rf"/file/d/({_ID})")
_HOSTS = {"drive.google.com", "docs.google.com"}


def parse_folder_link(s: str) -> tuple[str, str | None]:
    s = (s or "").strip()
    if not s:
        raise DriveError("Cole o link da pasta do Drive")
    if _BARE_ID.match(s):
        return s, None
    try:
        u = urlparse(s if "://" in s else "https://" + s)
        host = (u.hostname or "").lower()
    except ValueError:
        raise DriveError("Isso não é um link do Google Drive") from None
    if host not in _HOSTS:
        raise DriveError("Isso não é um link do Google Drive")
    if _FILE_PATH.search(u.path):
        raise DriveError("Esse link é de um arquivo, não de uma pasta")
    q = parse_qs(u.query)
    rk = (q.get("resourcekey") or [None])[0]
    m = _FOLDER_PATH.search(u.path)
    if m:
        return m.group(1), rk
    ids = q.get("id")
    if ids and _BARE_ID.match(ids[0]):
        return ids[0], rk
    raise DriveError("Não encontrei o ID da pasta no link")


def rclone_bin() -> str | None:
    found = shutil.which("rclone")
    if found:
        return found
    # o atalho do desktop pode nao ter ~/.local/bin no PATH
    local = os.path.expanduser("~/.local/bin/rclone")
    return local if os.path.isfile(local) and os.access(local, os.X_OK) else None


def _bin() -> str:
    return rclone_bin() or "rclone"


def _folder_flags(folder_id: str, resource_key: str | None) -> list[str]:
    # a pasta vai em cada execucao (nao fica no remote): trocar de pasta nao exige novo login
    flags = ["--drive-root-folder-id", folder_id]
    if resource_key:
        flags += ["--drive-resource-key", resource_key]
    return flags


# spec 9.2 + --error-on-no-transfer: rc 9 = arquivo ja igual no Drive (verificado no rclone v1.75.1)
COPY_FLAGS = ["--use-json-log", "--stats", "1s", "--stats-log-level", "NOTICE", "-v",
              "--retries", "3", "--retries-sleep", "10s", "--low-level-retries", "10",
              "--drive-chunk-size", "64M", "--transfers", "1", "--drive-stop-on-upload-limit",
              "--error-on-no-transfer"]


def build_copyto_cmd(local: str, dest_name: str, folder_id: str, resource_key: str | None = None,
                     description: str | None = None) -> list[str]:
    cmd = [_bin(), "copyto", local, f"{RCLONE_REMOTE}:{dest_name}", *_folder_flags(folder_id, resource_key),
           *COPY_FLAGS]
    if description:
        cmd += ["-M", "--metadata-set", f"description={description}"]
    return cmd


def build_lsjson_cmd(dest_name: str, folder_id: str, resource_key: str | None = None) -> list[str]:
    return [_bin(), "lsjson", f"{RCLONE_REMOTE}:{dest_name}", *_folder_flags(folder_id, resource_key),
            "--stat", "--hash", "--hash-type", "md5"]


def reconnect_cmd() -> list[str]:
    return [_bin(), "config", "update", RCLONE_REMOTE, "config_refresh_token=true"]


def parse_log_line(line: str) -> dict | None:
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    return entry if isinstance(entry, dict) else None


def _last_messages(text: str, n: int = 3) -> str:
    msgs = []
    for line in text.splitlines():
        entry = parse_log_line(line)
        if entry is not None and "stats" in entry:
            continue
        msg = str(entry.get("msg", "")) if entry is not None else line
        if msg.strip():
            msgs.append(msg.strip())
    return "\n".join(msgs[-n:])


# pasta inexistente/sem permissao: rc 3 (verificado) ou 404/403 da API do Drive [I]
_ACCESS_HINTS = ("directory not found", "File not found", "insufficientFilePermissions")


def classify_error(rc: int, log_text: str) -> str:
    text = log_text or ""
    if "didn't find section in config" in text:
        return MSG_NO_CONFIG
    if "invalid_grant" in text or "config reconnect" in text:
        return MSG_RELOGIN
    if "storageQuotaExceeded" in text:
        return MSG_QUOTA
    if rc == 3 or any(h in text for h in _ACCESS_HINTS):
        return MSG_NO_ACCESS
    if rc < 0:
        return f"rclone foi interrompido (sinal {-rc})"
    detail = _last_messages(text)
    msg = f"Falha no envio (código {rc})"
    return f"{msg}:\n{detail}" if detail else msg


_VIDEO_NAME = re.compile(r"^(?P<take>[\w-]+)_(?P<model>[a-z0-9]+)_IA\.mp4$")
DEFAULT_DESCRIPTION = "Voz sintética gerada por IA (conversão RVC). Paródia/homenagem."


def split_video_name(name: str) -> tuple[str, str] | None:
    m = _VIDEO_NAME.match(name)
    return (m.group("take"), m.group("model")) if m else None


def drive_description(dest_name: str) -> str:
    parts = split_video_name(dest_name)
    if parts is None:
        return DEFAULT_DESCRIPTION
    try:
        return metadata_tags(get_modelo(parts[1]))["comment"]
    except ValueError:
        return DEFAULT_DESCRIPTION
```

- [ ] **Step 4: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_drive -v`
Expected: `OK` (15 testes)

- [ ] **Step 5: Commit**

```bash
git add studio/drive.py tests/test_drive.py
git commit -m "feat(studio): drive com parser de link, comandos rclone, log JSON e erros em PT

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: Criar o rclone falso e o teste de envio que falha**

`tests/fakebin/rclone` (depois de criar: `chmod 755 tests/fakebin/rclone`):

```python
#!/usr/bin/env python3
"""rclone FALSO para os testes: simula o Drive numa pasta local (nunca usa rede nem ~/.config).

Ambiente:
  FAKE_RCLONE_DRIVE   pasta que faz o papel do Drive; a pasta compartilhada e <DRIVE>/<folder_id>/
  FAKE_RCLONE_MODE    ok | noconfig | invalid_grant | quota | fail | metadata_reject | hang
  FAKE_RCLONE_LSJSON  ok | badmd5
  FAKE_RCLONE_CALLS   arquivo onde cada chamada e anotada (uma linha JSON {"pid", "argv"})
As mensagens imitam as do rclone v1.75.1 (probes/2026-09-26/relatorios/probe_drive-rclone.md).
"""

import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone

FORBIDDEN = {"sync", "move", "delete", "deletefile", "purge", "dedupe", "link", "--no-check-dest", "-vv",
             "--config"}


def log(json_log, level, msg, **extra):
    if json_log:
        entry = {"time": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg, **extra,
                 "source": "fake"}
        line = json.dumps(entry)
    else:
        line = f"2026/09/26 00:00:00 {level.upper()}: {msg}"
    print(line, file=sys.stderr, flush=True)


def stats(done, total, transfers):
    log(True, "notice", f"\nTransferred: {done} / {total}\n",
        stats={"bytes": done, "totalBytes": total, "transfers": transfers, "errors": 0})


def opt(args, name):
    return args[args.index(name) + 1] if name in args else None


def die(json_log, rc, msg):
    log(json_log, "critical", msg)
    sys.exit(rc)


def folder(args, json_log):
    fid = opt(args, "--drive-root-folder-id")
    if not fid:
        die(json_log, 2, "fake rclone: falta --drive-root-folder-id")
    return os.path.join(os.environ["FAKE_RCLONE_DRIVE"], fid)


def remote_name(arg, json_log):
    if not arg.startswith("iavoz:"):
        die(json_log, 2, f"fake rclone: destino inesperado {arg}")
    return arg[len("iavoz:"):]


def same_file(src, dst):
    if not os.path.exists(dst):
        return False
    a, b = os.stat(src), os.stat(dst)
    return a.st_size == b.st_size and a.st_mtime_ns // 1_000_000 == b.st_mtime_ns // 1_000_000


def copyto(args, mode, json_log):
    src, name = args[1], remote_name(args[2], json_log)
    root = folder(args, json_log)
    if mode == "quota":
        log(json_log, "error", "Received upload limit error: googleapi: Error 403: The user's Drive storage "
                               "quota has been exceeded., storageQuotaExceeded")
        die(json_log, 7, "Failed to copyto: fatal error encountered")
    if mode == "fail":
        log(json_log, "error", "Failed to copyto: googleapi: Error 500: Internal Error, backendError")
        sys.exit(5)
    if mode == "metadata_reject" and "--metadata-set" in args:
        die(json_log, 1, "Failed to copyto: failed to set metadata: googleapi: Error 400: Invalid field")
    if not os.path.isdir(root):
        log(json_log, "error", "Failed to copyto: directory not found")
        sys.exit(3)
    dst = os.path.join(root, name)
    size = os.path.getsize(src)
    if same_file(src, dst):
        stats(0, 0, 0)
        sys.exit(9 if "--error-on-no-transfer" in args else 0)
    if "--dry-run" in args:
        log(json_log, "notice", f"Skipped copy as --dry-run is set (size {size})", skipped="copy", size=size,
            object=name)
        stats(size, size, 1)
        sys.exit(0)
    if mode == "hang":
        deadline = time.monotonic() + 60   # rede de seguranca: nunca vira processo eterno
        done = 0
        while time.monotonic() < deadline:
            done = min(size - 1, done + 1)
            stats(done, size, 0)
            time.sleep(0.05)
        sys.exit(1)
    existed = os.path.exists(dst)
    stats(0, size, 0)
    stats(size // 2, size, 0)
    shutil.copyfile(src, dst + ".tmp")
    os.replace(dst + ".tmp", dst)
    st = os.stat(src)
    os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
    if "--metadata-set" in args:
        meta_dir = os.path.join(root, ".meta")
        os.makedirs(meta_dir, exist_ok=True)
        key, value = opt(args, "--metadata-set").split("=", 1)
        with open(os.path.join(meta_dir, name + ".json"), "w", encoding="utf-8") as f:
            json.dump({key: value}, f, ensure_ascii=False)
    log(json_log, "info", "Copied (replaced existing)" if existed else "Copied (new)", size=size, object=name)
    stats(size, size, 1)
    sys.exit(0)


def lsjson(args, json_log):
    name = remote_name(args[1], json_log)
    dst = os.path.join(folder(args, json_log), name)
    if "--stat" not in args or not os.path.isfile(dst):
        die(json_log, 3, "Failed to lsjson: directory not found")
    with open(dst, "rb") as f:
        md5 = hashlib.md5(f.read()).hexdigest()
    if os.environ.get("FAKE_RCLONE_LSJSON") == "badmd5":
        md5 = "0" * 32
    info = {"Path": name, "Name": name, "Size": os.path.getsize(dst), "MimeType": "video/mp4",
            "ModTime": "2026-09-26T01:14:20.218776181-03:00", "IsDir": False, "Hashes": {"md5": md5},
            "ID": "fake-" + md5[:12]}
    print(json.dumps(info, indent="\t"))
    sys.exit(0)


def main():
    args = sys.argv[1:]
    calls = os.environ.get("FAKE_RCLONE_CALLS")
    if calls:
        with open(calls, "a", encoding="utf-8") as f:
            f.write(json.dumps({"pid": os.getpid(), "argv": args}) + "\n")
    json_log = "--use-json-log" in args
    print("linha de texto antes do log JSON", file=sys.stderr, flush=True)   # o parser tem que ignorar
    bad = FORBIDDEN.intersection(args)
    if bad or not args:
        die(json_log, 2, f"fake rclone: comando/flag proibido {sorted(bad)}")
    mode = os.environ.get("FAKE_RCLONE_MODE", "ok")
    if mode == "noconfig":
        die(json_log, 1, 'Failed to create file system for destination "iavoz:": didn\'t find section in '
                         'config file ("iavoz")')
    if mode == "invalid_grant":
        die(json_log, 1, 'Failed to create file system for "iavoz:": couldn\'t fetch token: invalid_grant: '
                         'maybe token expired? - try refreshing with "rclone config reconnect iavoz:"')
    if args[0] == "copyto":
        copyto(args, mode, json_log)
    elif args[0] == "lsjson":
        lsjson(args, json_log)
    die(json_log, 2, f"fake rclone: comando nao suportado {args[0]}")


if __name__ == "__main__":
    main()
```

`tests/test_drive_upload.py`:

```python
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
NAMES = ("2026-09-26_101500_silvio_IA.mp4", "2026-09-26_101700_orochi_IA.mp4")


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
        d = cls.tmp.name
        cls.good = make_mp4(os.path.join(d, NAMES[0]))
        cls.no_comment = make_mp4(os.path.join(d, "2026-09-26_101600_silvio_IA.mp4"), comment=None)
        cls.other_comment = make_mp4(os.path.join(d, "2026-09-26_101601_silvio_IA.mp4"), comment="feito em casa")
        cls.junk = os.path.join(d, "2026-09-26_101602_silvio_IA.mp4")
        with open(cls.junk, "wb") as f:
            f.write(b"isto nao e um video" * 100)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def copy_as(self, name: str) -> str:
        path = os.path.join(self.tmp.name, name)
        shutil.copyfile(self.good, path)
        return path

    def test_ok(self):
        self.assertIsNone(drive.check_uploadable(self.good))

    def test_part_and_name(self):
        for name in (NAMES[0] + ".part", "render.part.mp4", "x.part.y_IA.mp4"):
            with self.subTest(name=name):
                self.assertIn("arquivo incompleto (.part)", drive.check_uploadable(self.copy_as(name)))
        self.assertIn("só vídeos *_IA.mp4", drive.check_uploadable(self.copy_as("video.mp4")))

    def test_missing(self):
        path = os.path.join(self.tmp.name, "2026-01-01_000000_silvio_IA.mp4")
        self.assertEqual(drive.check_uploadable(path), "2026-01-01_000000_silvio_IA.mp4: arquivo não encontrado")

    def test_fail_closed_on_metadata(self):
        for path in (self.no_comment, self.other_comment):
            with self.subTest(path=path):
                self.assertIn("sem o aviso de IA nos metadados", drive.check_uploadable(path))
        self.assertIn("vídeo ilegível", drive.check_uploadable(self.junk))


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
```

- [ ] **Step 7: Rodar e ver falhar**

Run: `chmod 755 tests/fakebin/rclone && Applio/.venv/bin/python -m unittest tests.test_drive_upload`
Expected: `FAILED (failures=1, errors=27)` (21 testes; os subtestes contam à parte), com
`AttributeError: module 'studio.drive' has no attribute 'check_uploadable'` (e `upload_files`, `md5_file`,
`list_videos`, `UploadLock`, `record_sent`) e, no teste da trava entre processos, `AssertionError: '' != 'travado'`.

- [ ] **Step 8: Implementar o envio (versão final de `studio/drive.py`)**

Substitua `studio/drive.py` inteiro por esta versão final. Ela é a da Step 3 com o bloco de imports trocado e as
funções de envio acrescentadas no fim (depois de `drive_description`); nada do que já existia muda.

`studio/drive.py` (arquivo completo final):

```python
"""Envio dos videos finais para uma pasta do Google Drive via rclone (so stdlib)."""

import collections
import fcntl
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from studio import procs
from studio.config import RCLONE_REMOTE, REC_DIR, VIDEOS_DIR, get_modelo, metadata_tags
from studio.takes import Take


class DriveError(Exception):
    pass


MSG_NO_RCLONE = "rclone não instalado — veja o README"
MSG_NO_CONFIG = "Drive não configurado — rode a configuração"
MSG_RELOGIN = "Login do Drive expirou — clique Reconectar"
MSG_NO_ACCESS = "Essa conta Google não tem acesso a essa pasta (ou o link está errado)"
MSG_QUOTA = "Seu Drive está cheio — os envios contam na sua cota"
MSG_CANCELLED = "Envio cancelado"
# erros que valem para a pasta inteira: nao adianta tentar os proximos arquivos
FATAL_MSGS = (MSG_NO_CONFIG, MSG_RELOGIN, MSG_NO_ACCESS, MSG_QUOTA)

# link -> ID (base: probes/2026-09-26/drive/drive_probe.py, 12 casos verificados)
_ID = r"[A-Za-z0-9_-]{10,}"
_BARE_ID = re.compile(rf"^{_ID}$")
_FOLDER_PATH = re.compile(rf"/folders/({_ID})")
_FILE_PATH = re.compile(rf"/file/d/({_ID})")
_HOSTS = {"drive.google.com", "docs.google.com"}


def parse_folder_link(s: str) -> tuple[str, str | None]:
    s = (s or "").strip()
    if not s:
        raise DriveError("Cole o link da pasta do Drive")
    if _BARE_ID.match(s):
        return s, None
    try:
        u = urlparse(s if "://" in s else "https://" + s)
        host = (u.hostname or "").lower()
    except ValueError:
        raise DriveError("Isso não é um link do Google Drive") from None
    if host not in _HOSTS:
        raise DriveError("Isso não é um link do Google Drive")
    if _FILE_PATH.search(u.path):
        raise DriveError("Esse link é de um arquivo, não de uma pasta")
    q = parse_qs(u.query)
    rk = (q.get("resourcekey") or [None])[0]
    m = _FOLDER_PATH.search(u.path)
    if m:
        return m.group(1), rk
    ids = q.get("id")
    if ids and _BARE_ID.match(ids[0]):
        return ids[0], rk
    raise DriveError("Não encontrei o ID da pasta no link")


def rclone_bin() -> str | None:
    found = shutil.which("rclone")
    if found:
        return found
    # o atalho do desktop pode nao ter ~/.local/bin no PATH
    local = os.path.expanduser("~/.local/bin/rclone")
    return local if os.path.isfile(local) and os.access(local, os.X_OK) else None


def _bin() -> str:
    return rclone_bin() or "rclone"


def _folder_flags(folder_id: str, resource_key: str | None) -> list[str]:
    # a pasta vai em cada execucao (nao fica no remote): trocar de pasta nao exige novo login
    flags = ["--drive-root-folder-id", folder_id]
    if resource_key:
        flags += ["--drive-resource-key", resource_key]
    return flags


# spec 9.2 + --error-on-no-transfer: rc 9 = arquivo ja igual no Drive (verificado no rclone v1.75.1)
COPY_FLAGS = ["--use-json-log", "--stats", "1s", "--stats-log-level", "NOTICE", "-v",
              "--retries", "3", "--retries-sleep", "10s", "--low-level-retries", "10",
              "--drive-chunk-size", "64M", "--transfers", "1", "--drive-stop-on-upload-limit",
              "--error-on-no-transfer"]


def build_copyto_cmd(local: str, dest_name: str, folder_id: str, resource_key: str | None = None,
                     description: str | None = None) -> list[str]:
    cmd = [_bin(), "copyto", local, f"{RCLONE_REMOTE}:{dest_name}", *_folder_flags(folder_id, resource_key),
           *COPY_FLAGS]
    if description:
        cmd += ["-M", "--metadata-set", f"description={description}"]
    return cmd


def build_lsjson_cmd(dest_name: str, folder_id: str, resource_key: str | None = None) -> list[str]:
    return [_bin(), "lsjson", f"{RCLONE_REMOTE}:{dest_name}", *_folder_flags(folder_id, resource_key),
            "--stat", "--hash", "--hash-type", "md5"]


def reconnect_cmd() -> list[str]:
    return [_bin(), "config", "update", RCLONE_REMOTE, "config_refresh_token=true"]


def parse_log_line(line: str) -> dict | None:
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    return entry if isinstance(entry, dict) else None


def _last_messages(text: str, n: int = 3) -> str:
    msgs = []
    for line in text.splitlines():
        entry = parse_log_line(line)
        if entry is not None and "stats" in entry:
            continue
        msg = str(entry.get("msg", "")) if entry is not None else line
        if msg.strip():
            msgs.append(msg.strip())
    return "\n".join(msgs[-n:])


# pasta inexistente/sem permissao: rc 3 (verificado) ou 404/403 da API do Drive [I]
_ACCESS_HINTS = ("directory not found", "File not found", "insufficientFilePermissions")


def classify_error(rc: int, log_text: str) -> str:
    text = log_text or ""
    if "didn't find section in config" in text:
        return MSG_NO_CONFIG
    if "invalid_grant" in text or "config reconnect" in text:
        return MSG_RELOGIN
    if "storageQuotaExceeded" in text:
        return MSG_QUOTA
    if rc == 3 or any(h in text for h in _ACCESS_HINTS):
        return MSG_NO_ACCESS
    if rc < 0:
        return f"rclone foi interrompido (sinal {-rc})"
    detail = _last_messages(text)
    msg = f"Falha no envio (código {rc})"
    return f"{msg}:\n{detail}" if detail else msg


_VIDEO_NAME = re.compile(r"^(?P<take>[\w-]+)_(?P<model>[a-z0-9]+)_IA\.mp4$")
DEFAULT_DESCRIPTION = "Voz sintética gerada por IA (conversão RVC). Paródia/homenagem."


def split_video_name(name: str) -> tuple[str, str] | None:
    m = _VIDEO_NAME.match(name)
    return (m.group("take"), m.group("model")) if m else None


def drive_description(dest_name: str) -> str:
    parts = split_video_name(dest_name)
    if parts is None:
        return DEFAULT_DESCRIPTION
    try:
        return metadata_tags(get_modelo(parts[1]))["comment"]
    except ValueError:
        return DEFAULT_DESCRIPTION


def _is_part(name: str) -> bool:
    return name.endswith(".part") or ".part." in name


def check_uploadable(path: str) -> str | None:
    name = os.path.basename(path)
    if _is_part(name):
        return f"{name}: arquivo incompleto (.part) — não é enviado"
    if not name.endswith("_IA.mp4"):
        return f"{name}: só vídeos *_IA.mp4 de videos_finais/ são enviados"
    if not os.path.isfile(path):
        return f"{name}: arquivo não encontrado"
    try:
        data = procs.ffprobe_json(path, "-show_entries", "format_tags")
    except procs.ProcError:
        return f"{name}: vídeo ilegível — não é enviado"
    tags = (data.get("format") or {}).get("tags") or {}
    comment = next((str(v) for k, v in tags.items() if k.lower() == "comment"), "")
    # fail-closed: sem o aviso de IA nos metadados o video nao sai da maquina
    if "IA" not in comment:
        return f"{name}: vídeo sem o aviso de IA nos metadados — não é enviado"
    return None


def md5_file(path: str) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


LOCK_NAME = ".envio.lock"


class UploadLock:
    # flock nao bloqueante compartilhado entre GUI e CLI; o kernel solta a trava se o processo morrer
    def __init__(self, videos_dir: str = VIDEOS_DIR):
        self.path = os.path.join(os.path.abspath(videos_dir), LOCK_NAME)
        self._fd: int | None = None

    def __enter__(self) -> "UploadLock":
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise DriveError("Outro envio já está em andamento") from None
        self._fd = fd
        return self

    def __exit__(self, *exc) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def list_videos(videos_dir: str = VIDEOS_DIR) -> list[str]:
    pattern = os.path.join(glob.escape(os.path.abspath(videos_dir)), "*_IA.mp4")
    return sorted(p for p in glob.glob(pattern) if os.path.isfile(p) and not _is_part(os.path.basename(p)))


LSJSON_TIMEOUT_S = 120
KILL_AFTER_S = 10
LOG_KEEP = 200
MSG_NO_DESCRIPTION = "O Drive recusou a descrição do arquivo; enviado sem ela"


def _result(path: str, **kw) -> dict:
    res = {"arquivo": path, "ok": False, "pulado": False, "md5": "", "erro": "", "aviso": ""}
    res.update(kw)
    return res


def _cancelled(cancel: threading.Event | None) -> bool:
    return cancel is not None and cancel.is_set()


def _fraction(entry: dict) -> float | None:
    st = entry.get("stats")
    try:
        total = float(st.get("totalBytes") or 0)
        done = float(st.get("bytes") or 0)
    except (AttributeError, TypeError, ValueError):
        return None
    return max(0.0, min(1.0, done / total)) if total > 0 else None


def _watch_cancel(p: subprocess.Popen, cancel: threading.Event, done: threading.Event) -> None:
    while not done.wait(0.1):
        if cancel.is_set():
            p.terminate()   # SIGTERM direto no rclone: o setpriv faz exec, o pid e o dele
            if not done.wait(KILL_AFTER_S):
                p.kill()
            return


def _run_copy(cmd: list[str], name: str, on_progress, cancel: threading.Event | None) -> tuple[int, str]:
    # o rclone escreve tudo no stderr, uma linha JSON por evento; o stdout fica vazio
    kept = collections.deque(maxlen=LOG_KEEP)
    p = procs.spawn(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace")
    done = threading.Event()
    watcher = None
    if cancel is not None:
        watcher = threading.Thread(target=_watch_cancel, args=(p, cancel, done), daemon=True)
        watcher.start()
    try:
        for line in p.stderr:
            entry = parse_log_line(line)
            if entry is not None and "stats" in entry:
                frac = _fraction(entry)
                if frac is not None and on_progress is not None:
                    on_progress(name, frac)
                continue
            if line.strip():
                kept.append(line.rstrip())
        rc = p.wait()
    except BaseException:
        p.terminate()
        try:
            p.wait(timeout=KILL_AFTER_S)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
        raise
    finally:
        done.set()
        p.stderr.close()
        if watcher is not None:
            watcher.join()
    return rc, "\n".join(kept)


def _verify(path: str, name: str, folder_id: str, rk: str | None) -> tuple[str, str]:
    # (md5, erro): compara tamanho e MD5 do arquivo no Drive com o local
    local_md5 = md5_file(path)
    try:
        r = procs.run(build_lsjson_cmd(name, folder_id, rk), timeout=LSJSON_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return "", "O Drive não respondeu a tempo na conferência do envio"
    if r.returncode != 0:
        return "", classify_error(r.returncode, r.stderr)
    try:
        info = json.loads(r.stdout)
        size = info.get("Size")
        remote_md5 = str((info.get("Hashes") or {}).get("md5") or "").lower()
    except (ValueError, AttributeError):
        return "", "Resposta inválida do rclone na conferência do envio"
    if size != os.path.getsize(path) or remote_md5 != local_md5:
        return "", "O arquivo no Drive não confere com o local (tamanho ou MD5 diferente)"
    return local_md5, ""


def _upload_one(path: str, folder_id: str, rk: str | None, on_progress, cancel: threading.Event | None,
                dry_run: bool) -> dict:
    res = _result(path)
    name = os.path.basename(path)
    motivo = check_uploadable(path)
    if motivo:
        res["erro"] = motivo
        return res
    if on_progress is not None:
        on_progress(name, 0.0)
    extra = ["--dry-run"] if dry_run else []
    cmd = build_copyto_cmd(path, name, folder_id, rk, description=drive_description(name)) + extra
    rc, log = _run_copy(cmd, name, on_progress, cancel)
    no_description = False
    if rc not in (0, 9) and not _cancelled(cancel) and "metadata" in log.lower():
        # o Drive recusou a descricao: repete sem ela (spec 9.2)
        no_description = True
        rc, log = _run_copy(build_copyto_cmd(path, name, folder_id, rk) + extra, name, on_progress, cancel)
    if rc not in (0, 9):
        res["erro"] = MSG_CANCELLED if _cancelled(cancel) else classify_error(rc, log)
        return res
    if not dry_run:
        res["md5"], res["erro"] = _verify(path, name, folder_id, rk)
        if res["erro"]:
            return res
    # rc 9 (--error-on-no-transfer) = ja estava igual no Drive
    res.update(ok=True, pulado=rc == 9, aviso=MSG_NO_DESCRIPTION if no_description else "")
    if on_progress is not None:
        on_progress(name, 1.0)
    return res


def upload_files(paths: list[str], folder_link: str, on_progress=None, cancel: threading.Event | None = None,
                 dry_run: bool = False, videos_dir: str = VIDEOS_DIR) -> list[dict]:
    folder_id, rk = parse_folder_link(folder_link)
    if rclone_bin() is None:
        raise DriveError(MSG_NO_RCLONE)
    results = []
    with UploadLock(videos_dir):
        fatal = ""
        for path in paths:
            if _cancelled(cancel):
                break
            path = os.path.abspath(path)
            if fatal:
                results.append(_result(path, erro=fatal))
                continue
            res = _upload_one(path, folder_id, rk, on_progress, cancel, dry_run)
            results.append(res)
            if res["erro"] in FATAL_MSGS:
                fatal = res["erro"]
    return results


def record_sent(result: dict, rec_dir: str = REC_DIR, now: datetime | None = None) -> str | None:
    # grava saidas[modelo]["enviado"] no take.json; so a thread principal da GUI (ou a CLI) chama
    if not result.get("ok") or not result.get("md5"):
        return None
    parts = split_video_name(os.path.basename(result.get("arquivo", "")))
    if parts is None:
        return None
    take_id, model_key = parts
    try:
        take = Take.load(os.path.join(rec_dir, take_id))
    except (OSError, ValueError, TypeError):
        return None
    saida = take.saidas.setdefault(model_key, {})
    if (saida.get("enviado") or {}).get("md5") != result["md5"]:
        saida["enviado"] = {"md5": result["md5"], "quando": (now or datetime.now()).isoformat(timespec="seconds")}
        take.save()
    return take.id
```

- [ ] **Step 9: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_drive tests.test_drive_upload`
Expected: `OK` (36 testes: 15 + 21; o de envio leva ~3 s)

Run: `pgrep -af tests/fakebin/rclone`
Expected: nenhum processo do rclone falso vivo (se o comando rodar dentro de um `bash -c` que contém esse
texto, a linha do próprio `bash` aparece; ignore-a).

- [ ] **Step 10: Commit**

```bash
git add studio/drive.py tests/fakebin/rclone tests/test_drive_upload.py
git commit -m "feat(studio): envio pro Drive com trava, checagem fail-closed, progresso, cancelar e conferencia MD5

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

Confira que o bit de execução foi junto: `git ls-files -s tests/fakebin/rclone` → começa com `100755`.

- [ ] **Step 11: Escrever o teste da CLI que falha**

`tests/test_enviar_drive.py`:

```python
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
```

- [ ] **Step 12: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_enviar_drive -v`
Expected: `FAILED (failures=10)`, com `can't open file '.../enviar_drive.py': [Errno 2] No such file or directory`
(e `AssertionError: Tuples differ: (1, '') != (0, 'ok\n')` no teste de só-stdlib).

- [ ] **Step 13: Implementar `enviar_drive.py`**

`enviar_drive.py` (depois de criar: `chmod 755 enviar_drive.py`):

```python
#!/usr/bin/env python3
"""Envia os videos prontos (videos_finais/*_IA.mp4) para a pasta do Google Drive (so stdlib; pode ir pro cron)."""

import argparse
import os
import sys

from studio import drive
from studio.config import ESTADO_PATH, RCLONE_REMOTE, REC_DIR, VIDEOS_DIR, load_estado, save_estado


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Envia os vídeos prontos para a pasta do Google Drive (via rclone). "
                                            "Nunca apaga nada no Drive; o que já está igual é pulado.")
    p.add_argument("--pasta", metavar="LINK", help="link da pasta do Drive (fica salvo em estado.json)")
    p.add_argument("--arquivo", metavar="CAMINHO", action="append",
                   help="envia só este vídeo (pode repetir); sem ele, envia todos de videos_finais/")
    p.add_argument("--dry-run", action="store_true", help="só mostra o que seria enviado, sem enviar")
    p.add_argument("--estado", default=ESTADO_PATH, help="caminho do estado.json (testes)")
    p.add_argument("--videos-dir", default=VIDEOS_DIR, help="pasta dos vídeos finais (testes)")
    p.add_argument("--rec-dir", default=REC_DIR, help="pasta das tomadas, para anotar o envio (testes)")
    return p


class Progress:
    # uma linha a cada 10% por arquivo (legivel no terminal e no log do cron)
    def __init__(self):
        self.last: dict[str, int] = {}

    def __call__(self, name: str, frac: float) -> None:
        step = int(frac * 100) // 10 * 10
        if self.last.get(name) == step:
            return
        self.last[name] = step
        print(f"  {name}: {step}%", flush=True)


def describe(res: dict, dry_run: bool) -> str:
    if not res["ok"]:
        return "FALHOU — " + res["erro"].replace("\n", "\n    ")
    if res["pulado"]:
        return "já estava igual no Drive (pulado)"
    return "seria enviado" if dry_run else "enviado"


def run(args) -> int:
    estado, aviso = load_estado(args.estado)
    if aviso:
        print(aviso, file=sys.stderr)
    if args.pasta is not None:
        try:
            drive.parse_folder_link(args.pasta)
        except drive.DriveError as e:
            print(f"Link inválido: {e}", file=sys.stderr)
            return 2
        estado["drive_pasta"] = args.pasta.strip()
        save_estado(estado, args.estado)
        print("Pasta do Drive salva em estado.json")
    link = str(estado.get("drive_pasta") or "").strip()
    if not link:
        print("Nenhuma pasta do Drive configurada — use --pasta <link> (veja o README)", file=sys.stderr)
        return 2
    try:
        drive.parse_folder_link(link)
    except drive.DriveError as e:
        print(f"Link da pasta salvo em estado.json é inválido: {e}", file=sys.stderr)
        return 2
    paths = [os.path.abspath(a) for a in args.arquivo] if args.arquivo else drive.list_videos(args.videos_dir)
    if not paths:
        print(f"Nenhum vídeo para enviar em {args.videos_dir}")
        return 0
    if args.dry_run:
        print(f"Simulação (--dry-run): {len(paths)} vídeo(s), nada é enviado")
    else:
        print(f"Enviando {len(paths)} vídeo(s) para o Drive…")
    try:
        results = drive.upload_files(paths, link, on_progress=Progress(), dry_run=args.dry_run,
                                     videos_dir=args.videos_dir)
    except drive.DriveError as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 1
    for res in results:
        print(f"{os.path.basename(res['arquivo'])}: {describe(res, args.dry_run)}")
        if res["aviso"]:
            print(f"  aviso: {res['aviso']}")
        if res["erro"] == drive.MSG_RELOGIN:
            print(f"  no terminal: rclone config reconnect {RCLONE_REMOTE}:")
        if not args.dry_run:
            drive.record_sent(res, args.rec_dir)
    sent = sum(1 for r in results if r["ok"] and not r["pulado"])
    skipped = sum(1 for r in results if r["pulado"])
    failed = len(paths) - sent - skipped
    if args.dry_run:
        print(f"Resumo (simulação): {sent} a enviar, {skipped} pulado(s), {failed} falha(s)")
    else:
        print(f"Resumo: {sent} enviado(s), {skipped} pulado(s), {failed} falha(s)")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        # o Ctrl-C tambem chega ao rclone (mesmo grupo de processos)
        print("\nEnvio cancelado", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 14: Rodar e ver passar**

Run: `chmod 755 enviar_drive.py && Applio/.venv/bin/python -m unittest tests.test_enviar_drive -v`
Expected: `OK` (10 testes)

Run: `./run_tests.sh`
Expected: `Ran 329 tests` (~39 s) e `OK (skipped=6)`, contando as Tasks 1–8 (as correções desta task vêm depois).

Run: `ls -d ~/.config/rclone`
Expected: se não existia antes da task, continua não existindo (`Arquivo ou diretório inexistente`): os testes
nunca criam a config do rclone.

- [ ] **Step 15: Commit**

```bash
git add enviar_drive.py tests/test_enviar_drive.py
git commit -m "feat: enviar_drive.py (CLI de envio pro Drive, pode ir pro cron)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 16: Escrever o README (configuração inicial do Drive)**

`README.md` (arquivo novo; a Task 12 acrescenta as seções de uso e calibração depois desta):

````markdown
# Voice Studio (Orochi/Silvio)

Grava a webcam e o microfone, troca a voz por IA (RVC) e gera vídeos com marca d'água avisando que a voz é
gerada por IA. Os vídeos prontos ficam em `videos_finais/` e podem ser enviados para uma pasta do Google Drive.

## Google Drive: configuração inicial

Isto é feito **uma vez só**. Depois, o botão **Enviar** do app e o script `enviar_drive.py` usam essa
configuração.

O envio usa o [rclone](https://rclone.org) com uma credencial OAuth **sua** no Google Cloud (o client
compartilhado do rclone deixa de funcionar durante 2026). Os vídeos vão para uma pasta que alguém compartilhou
com você com permissão de **edição**.

### 1. Instalar o rclone v1.75.1

O rclone do `apt` (1.60) é velho demais. Instale o binário oficial em `~/.local/bin` (não precisa de sudo):

```bash
cd "$(mktemp -d)"
curl -fLO https://downloads.rclone.org/v1.75.1/rclone-v1.75.1-linux-amd64.zip
curl -fLO https://downloads.rclone.org/v1.75.1/SHA256SUMS
grep ' rclone-v1.75.1-linux-amd64.zip$' SHA256SUMS | sha256sum -c -
mkdir -p ~/.local/bin && unzip -j rclone-v1.75.1-linux-amd64.zip 'rclone-v1.75.1-linux-amd64/rclone' -d ~/.local/bin && chmod 755 ~/.local/bin/rclone
rclone version
```

- O `sha256sum -c` tem que responder `rclone-v1.75.1-linux-amd64.zip: SUCESSO` (ou `: OK`, em inglês). Se
  der `FALHOU`, apague o zip e baixe de novo; **não instale**.
- Para conferência extra, o SHA256 do zip conferido em 26/09/2026 é
  `982b5aa772841168f8e380f139e9e787b2a105403e32b94da8676a0e1c0a13ab`.
- Opcional, mais forte: conferir a assinatura PGP do `SHA256SUMS` (a chave tem que ser
  `FBF737ECE9F8AB18604BD2AC93935E02FF3B54FA`, de Nick Craig-Wood; o mesmo valor aparece em
  `dig key.rclone.org txt`):

  ```bash
  gpg --keyserver keyserver.ubuntu.com --receive-keys FBF737ECE9F8AB18604BD2AC93935E02FF3B54FA
  gpg --verify SHA256SUMS
  ```

  O `gpg` tem que dizer "Assinatura correta" (ou "Good signature") com essa chave.
- Se `rclone version` disser "comando não encontrado", abra um terminal novo (o `~/.local/bin` entra no PATH no
  login). O app e o script acham o rclone em `~/.local/bin` mesmo sem ele no PATH.

### 2. Criar a credencial OAuth no Google Cloud

Faça tudo logado com a conta Google que tem **edição** na pasta (veja a recomendação de conta dedicada em
"Segurança", abaixo). Os nomes dos menus podem aparecer em português ou em inglês.

1. Abra <https://console.cloud.google.com/> e crie um projeto (por exemplo, "voice-studio-drive").
2. **APIs e serviços → Biblioteca** ("APIs & Services → Library"): procure "Google Drive API" e clique em
   **Ativar** ("Enable").
3. **Tela de permissão OAuth** ("OAuth consent screen" → "Get started"):
   - Nome do app: "Voice Studio"; e-mail de suporte: o seu.
   - Público-alvo ("Audience"): **Externo** ("External").
   - Informações de contato: o seu e-mail. Aceite os termos e crie.
4. **Acesso a dados** ("Data Access") → **Adicionar ou remover escopos** ("Add or remove scopes"): adicione
   `https://www.googleapis.com/auth/drive` e clique em **Salvar** ("Save").
5. **Público-alvo** ("Audience") → **Usuários de teste** ("Test users") → adicione o seu e-mail.
6. **Clientes** ("Clients") → **Criar cliente** ("Create OAuth client") → tipo **App para computador**
   ("Desktop app") → Criar. Anote o **ID do cliente** (client ID) e a **chave secreta** (client secret).
7. **Público-alvo** ("Audience") → **PUBLICAR APP** ("PUBLISH APP") → Confirmar. **Não pule este passo:** em
   modo "Teste" ("Testing") o login expira a cada 7 dias.
   - Se o botão estiver cinza, o Google pede antes, em **Branding**, uma "página inicial do app" e um "link da
     política de privacidade" (uma página simples no GitHub Pages serve).
   - Não precisa mandar o app para verificação. No login vai aparecer "O Google não verificou este app"; para
     uso pessoal isso é esperado.

### 3. Criar o remote `iavoz`

No terminal (troque pelos valores do passo 2.6):

```bash
rclone config create iavoz drive client_id=SEU_CLIENT_ID client_secret=SUA_CHAVE_SECRETA scope=drive
```

- O navegador abre sozinho. Escolha a conta, clique em **Avançado → Acessar Voice Studio (não seguro)**
  ("Advanced → Go to … (unsafe)") e permita o acesso ao Drive. O comando termina quando a página mostrar
  "Success".
- Se o navegador não abrir, copie o link `http://127.0.0.1:53682/auth?...` que o rclone mostra. A porta 53682
  precisa estar livre.
- `scope=drive` é obrigatório: com `drive.file` o rclone não consegue gravar numa pasta que ele não criou.
- **Não** coloque `root_folder_id` aqui. A pasta vai em cada envio (vem do `estado.json`), então dá para trocar de
  pasta sem fazer login de novo.
- A chave secreta fica visível no histórico do shell. Ela não é um segredo forte para app de desktop, mas não a
  coloque no git. Dica: comece o comando com um espaço para ele não ir para o histórico.

### 4. Colar o link da pasta

No Drive, abra a pasta compartilhada com você → **Compartilhar → Copiar link**. Depois:

- no app: **Configurar Drive** e cole o link; ou
- no terminal: `python3 enviar_drive.py --pasta 'LINK_DA_PASTA' --dry-run`

O link fica salvo em `estado.json`. São aceitos links `…/folders/<ID>`, `…/u/0/folders/<ID>`, `open?id=`,
`folderview?id=`, com `resourcekey`, e o ID puro. Links de **arquivo** (`/file/d/…`) são recusados.

### 5. Testar

```bash
rclone lsjson iavoz: --drive-root-folder-id ID_DA_PASTA --max-depth 1; echo "saída: $?"
```

Tem que listar o conteúdo da pasta (`[]` se estiver vazia) e terminar com `saída: 0`. O `ID_DA_PASTA` é o trecho
depois de `/folders/` no link. Depois:

```bash
python3 enviar_drive.py --dry-run     # mostra o que seria enviado, sem enviar nada
python3 enviar_drive.py               # envia todos os videos_finais/*_IA.mp4
python3 enviar_drive.py --arquivo videos_finais/2026-09-26_101500_silvio_IA.mp4
```

- Rodar de novo **não duplica** nada: o que já está igual no Drive aparece como "pulado". Se um vídeo mudou, ele
  vira uma nova versão do mesmo arquivo no Drive.
- Depois de cada envio, o script confere o tamanho e o MD5 do arquivo no Drive com o arquivo local.
- Só sobem vídeos `*_IA.mp4` com o aviso de IA nos metadados. Arquivos `.part` nunca sobem.
- Um envio por vez: se o app estiver enviando, o script avisa "Outro envio já está em andamento".
- Código de saída: `0` = tudo enviado ou pulado; `1` = alguma falha; `2` = pasta não configurada ou link
  inválido.
- Pode ir para o cron, por exemplo a cada hora:
  `0 * * * * cd ~/orochi-ia-homenagem && python3 enviar_drive.py >> envio_drive.log 2>&1`

### Mensagens de erro

| Mensagem | O que fazer |
|---|---|
| rclone não instalado — veja o README | Passo 1. |
| Drive não configurado — rode a configuração | Passo 3 (o remote `iavoz` não existe). |
| Login do Drive expirou — clique Reconectar | No app: **Reconectar** (abre o navegador). No terminal: `rclone config reconnect iavoz:`. Se acontece toda semana, o app ficou em "Teste": faça o passo 2.7. |
| Essa conta Google não tem acesso a essa pasta (ou o link está errado) | Confira o link (passo 4) e se a pasta foi compartilhada com **edição** com a conta usada no passo 3. |
| Seu Drive está cheio — os envios contam na sua cota | Os vídeos contam na cota da **sua** conta (15 GB grátis, ~45 MB por minuto de vídeo), não na do dono da pasta. |
| Falha no envio (código N) + últimas linhas do log | Erro de rede ou do Google; tente de novo mais tarde. |

### Segurança e onde fica o token

- O login fica em `~/.config/rclone/rclone.conf` (modo 0600), **fora** do projeto. Nunca copie esse arquivo
  para a pasta do projeto nem use `--config` apontando para cá.
- Com `scope=drive`, esse token acessa o Drive **inteiro** da conta. Recomendação (não obrigatória): use uma
  conta Google **dedicada**, que só tenha acesso à pasta compartilhada.
- Para mostrar a configuração a alguém, use `rclone config redacted` (esconde o token), nunca
  `rclone config show`.
- O app e o script nunca apagam nem movem nada no Drive: só usam `rclone copyto` e `rclone lsjson`.
- Para revogar o acesso: <https://myaccount.google.com/permissions> → remova o app; e apague o remote com
  `rclone config delete iavoz`.
````

- [ ] **Step 17: Commit**

```bash
git add README.md
git commit -m "docs: README com a configuracao inicial do Google Drive (rclone v1.75.1 + OAuth proprio)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

**Correções da revisão (Steps 18–32).** (1) Com `"drive_pasta": 123` no `estado.json`, o
`parse_folder_link` levantava `AttributeError` e derrubava a abertura da GUI. (2) O `check_uploadable` aceitava
qualquer comment que contivesse "IA" (até "MEDIA") e qualquer caminho, então o `enviar_drive.py --arquivo`
mandava para o Drive um vídeo sem a marca d'água, violando o fail-closed. (3) O `enviar_drive.py --pasta` gravava
o dicionário inteiro por cima de um `estado.json` corrompido, e o que dava para salvar dele se perdia.

- [ ] **Step 18: Teste do link que não é texto (falha)**

Em `tests/test_drive.py`, substituir isto:

```python
            with self.subTest(link=link):
                with self.assertRaises(drive.DriveError):
                    drive.parse_folder_link(link)


class RcloneBinTest(unittest.TestCase):
```

por isto:

```python
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
```

- [ ] **Step 19: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_drive -v`
Expected: `FAILED (errors=6)` (`Ran 16 tests`), um por subteste: `AttributeError: 'int' object has no attribute
'strip'` (e o mesmo para `float`, `bool`, `list` e `dict`) e, para `bytes`, `TypeError: cannot use a string pattern
on a bytes-like object`

- [ ] **Step 20: Recusar o que não é `str`**

Em `studio/drive.py`, substituir isto:

```python
def parse_folder_link(s: str) -> tuple[str, str | None]:
    s = (s or "").strip()
```

por isto:

```python
def parse_folder_link(s: str) -> tuple[str, str | None]:
    if s is not None and not isinstance(s, str):
        raise DriveError("O link da pasta do Drive tem que ser um texto")    # ex.: "drive_pasta": 123
    s = (s or "").strip()
```

- [ ] **Step 21: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_drive -v`
Expected: `Ran 16 tests` e `OK`

- [ ] **Step 22: Commit**

```bash
git add studio/drive.py tests/test_drive.py
git commit -m "fix(drive): parse_folder_link recusa valor que nao e texto com DriveError

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 23: Testes do envio fail-closed de verdade (falha)**

Os testes pedem três coisas: só sai vídeo de `videos_finais/` (sem symlink para fora), o comment tem que ser
**igual** ao aviso do modelo do nome, e o `upload_files` e a CLI respeitam isso. Os dois vídeos de `NAMES` passam a
ser do Silvio, porque o MP4 modelo dos testes leva o aviso do Silvio. O do Orochi ganha teste próprio, com o aviso
dele. Em `tests/test_drive_upload.py`, substituir isto:

```python
NAMES = ("2026-09-26_101500_silvio_IA.mp4", "2026-09-26_101700_orochi_IA.mp4")
```

por isto:

```python
OROCHI_COMMENT = "Voz sintética gerada por IA (conversão RVC). Não é a voz real do Orochi."
# os dois do Silvio: o MP4 modelo leva o aviso do Silvio, e check_uploadable exige o aviso exato do modelo do nome
NAMES = ("2026-09-26_101500_silvio_IA.mp4", "2026-09-26_101700_silvio_IA.mp4")
```

E em `tests/test_drive_upload.py`, substituir isto (a classe `CheckUploadableTest` inteira):

```python
class CheckUploadableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        cls.good = make_mp4(os.path.join(d, NAMES[0]))
        cls.no_comment = make_mp4(os.path.join(d, "2026-09-26_101600_silvio_IA.mp4"), comment=None)
        cls.other_comment = make_mp4(os.path.join(d, "2026-09-26_101601_silvio_IA.mp4"), comment="feito em casa")
        cls.junk = os.path.join(d, "2026-09-26_101602_silvio_IA.mp4")
        with open(cls.junk, "wb") as f:
            f.write(b"isto nao e um video" * 100)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def copy_as(self, name: str) -> str:
        path = os.path.join(self.tmp.name, name)
        shutil.copyfile(self.good, path)
        return path

    def test_ok(self):
        self.assertIsNone(drive.check_uploadable(self.good))

    def test_part_and_name(self):
        for name in (NAMES[0] + ".part", "render.part.mp4", "x.part.y_IA.mp4"):
            with self.subTest(name=name):
                self.assertIn("arquivo incompleto (.part)", drive.check_uploadable(self.copy_as(name)))
        self.assertIn("só vídeos *_IA.mp4", drive.check_uploadable(self.copy_as("video.mp4")))

    def test_missing(self):
        path = os.path.join(self.tmp.name, "2026-01-01_000000_silvio_IA.mp4")
        self.assertEqual(drive.check_uploadable(path), "2026-01-01_000000_silvio_IA.mp4: arquivo não encontrado")

    def test_fail_closed_on_metadata(self):
        for path in (self.no_comment, self.other_comment):
            with self.subTest(path=path):
                self.assertIn("sem o aviso de IA nos metadados", drive.check_uploadable(path))
        self.assertIn("vídeo ilegível", drive.check_uploadable(self.junk))
```

por isto:

```python
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
```

E em `tests/test_drive_upload.py`, substituir isto:

```python
        self.assertTrue(results[1]["ok"])
        self.assertEqual([argv[1] for argv in self.copy_calls()], [self.paths[0]])
```

por isto:

```python
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
```

E em `tests/test_enviar_drive.py`, substituir isto:

```python
    def test_preflight_errors_exit_1(self):
```

por isto:

```python
    def test_file_outside_videos_dir_is_refused(self):
        outside = os.path.join(self.tmp, NAMES[0])
        shutil.copy2(self.template, outside)
        r = self.cli("--pasta", LINK, "--arquivo", outside)
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"{NAMES[0]}: FALHOU — {NAMES[0]}: fora de videos_finais/", r.stdout)
        self.assertEqual(self.remote_files(), [])

    def test_preflight_errors_exit_1(self):
```

- [ ] **Step 24: Rodar e ver falhar**

Run: `chmod 755 tests/fakebin/rclone && Applio/.venv/bin/python -m unittest tests.test_drive_upload tests.test_enviar_drive`
Expected: `FAILED (failures=2, errors=12)` (`Ran 34 tests`; os subtestes contam à parte): todo teste de
`CheckUploadableTest` com `TypeError: check_uploadable() takes 1 positional argument but 2 were given`;
`test_video_outside_videos_dir_never_reaches_rclone` com `AssertionError: '' != '2026-09-26_101500_silvio_IA.mp4: fora
de [52 chars]ados'` (o vídeo de fora foi enviado); e `test_file_outside_videos_dir_is_refused` com `AssertionError: 0 != 1`
(a CLI enviou e saiu 0)

- [ ] **Step 25: Só `videos_finais/` e só o aviso exato do modelo**

Em `studio/drive.py`, substituir isto:

```python
def check_uploadable(path: str) -> str | None:
    name = os.path.basename(path)
    if _is_part(name):
        return f"{name}: arquivo incompleto (.part) — não é enviado"
    if not name.endswith("_IA.mp4"):
        return f"{name}: só vídeos *_IA.mp4 de videos_finais/ são enviados"
    if not os.path.isfile(path):
        return f"{name}: arquivo não encontrado"
    try:
        data = procs.ffprobe_json(path, "-show_entries", "format_tags")
    except procs.ProcError:
        return f"{name}: vídeo ilegível — não é enviado"
    tags = (data.get("format") or {}).get("tags") or {}
    comment = next((str(v) for k, v in tags.items() if k.lower() == "comment"), "")
    # fail-closed: sem o aviso de IA nos metadados o video nao sai da maquina
    if "IA" not in comment:
        return f"{name}: vídeo sem o aviso de IA nos metadados — não é enviado"
    return None
```

por isto:

```python
def _expected_comment(name: str) -> str | None:
    # o aviso exato que o render grava para o modelo do nome (<tomada>_<modelo>_IA.mp4)
    parts = split_video_name(name)
    try:
        return metadata_tags(get_modelo(parts[1]))["comment"] if parts else None
    except ValueError:
        return None


def check_uploadable(path: str, videos_dir: str = VIDEOS_DIR) -> str | None:
    name = os.path.basename(path)
    if _is_part(name):
        return f"{name}: arquivo incompleto (.part) — não é enviado"
    if not name.endswith("_IA.mp4"):
        return f"{name}: só vídeos *_IA.mp4 de videos_finais/ são enviados"
    # fail-closed: so o que o render publicou em videos_finais/ sai da maquina (symlink para fora nao vale)
    if os.path.dirname(os.path.realpath(path)) != os.path.realpath(videos_dir):
        return f"{name}: fora de videos_finais/ — só vídeos gerados pelo app são enviados"
    expected = _expected_comment(name)
    if expected is None:
        return f"{name}: nome sem um modelo conhecido (<tomada>_<modelo>_IA.mp4) — não é enviado"
    if not os.path.isfile(path):
        return f"{name}: arquivo não encontrado"
    try:
        data = procs.ffprobe_json(path, "-show_entries", "format_tags")
    except procs.ProcError:
        return f"{name}: vídeo ilegível — não é enviado"
    tags = (data.get("format") or {}).get("tags") or {}
    comment = next((str(v) for k, v in tags.items() if k.lower() == "comment"), "")
    # fail-closed: o comment tem que ser o aviso de IA exato do modelo do nome ("MEDIA" tambem tem "IA")
    if comment != expected:
        return f"{name}: vídeo sem o aviso de IA nos metadados — não é enviado"
    return None
```

E em `studio/drive.py`, substituir isto:

```python
def _upload_one(path: str, folder_id: str, rk: str | None, on_progress, cancel: threading.Event | None,
                dry_run: bool) -> dict:
    res = _result(path)
    name = os.path.basename(path)
    motivo = check_uploadable(path)
```

por isto:

```python
def _upload_one(path: str, folder_id: str, rk: str | None, on_progress, cancel: threading.Event | None,
                dry_run: bool, videos_dir: str) -> dict:
    res = _result(path)
    name = os.path.basename(path)
    motivo = check_uploadable(path, videos_dir)
```

E em `studio/drive.py`, substituir isto:

```python
            res = _upload_one(path, folder_id, rk, on_progress, cancel, dry_run)
```

por isto:

```python
            res = _upload_one(path, folder_id, rk, on_progress, cancel, dry_run, videos_dir)
```

- [ ] **Step 26: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_drive_upload tests.test_enviar_drive -v`
Expected: `Ran 34 tests` (~4,6 s) e `OK`

- [ ] **Step 27: Commit**

```bash
git add studio/drive.py tests/test_drive_upload.py tests/test_enviar_drive.py
git commit -m "fix(drive): check_uploadable exige videos_finais/ (realpath) e o aviso de IA exato do modelo

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 28: Teste do `--pasta` com `estado.json` corrompido (falha)**

Em `tests/test_enviar_drive.py`, substituir isto:

```python
    def test_preflight_errors_exit_1(self):
```

por isto:

```python
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
```

- [ ] **Step 29: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_enviar_drive -v`
Expected: `FAILED (failures=1)` (`Ran 12 tests`) com `AssertionError: 'estado.json corrompido — cópia guardada em
estado.json.corrompido' not found in 'estado.json corrompido — usando padrões\n'`

- [ ] **Step 30: `--pasta` grava com `merge_estado`**

Em `enviar_drive.py`, substituir isto:

```python
from studio.config import ESTADO_PATH, RCLONE_REMOTE, REC_DIR, VIDEOS_DIR, load_estado, save_estado
```

por isto:

```python
from studio.config import ESTADO_PATH, RCLONE_REMOTE, REC_DIR, VIDEOS_DIR, load_estado, merge_estado
```

E em `enviar_drive.py`, substituir isto:

```python
        estado["drive_pasta"] = args.pasta.strip()
        save_estado(estado, args.estado)
        print("Pasta do Drive salva em estado.json")
```

por isto:

```python
        # grava so a pasta, relendo o arquivo; um estado.json corrompido fica guardado em .corrompido
        estado, aviso = merge_estado({"drive_pasta": args.pasta.strip()}, args.estado)
        if aviso:
            print(aviso, file=sys.stderr)
        print("Pasta do Drive salva em estado.json")
```

- [ ] **Step 31: Rodar e ver passar (módulos e suíte inteira)**

Run: `Applio/.venv/bin/python -m unittest tests.test_drive tests.test_drive_upload tests.test_enviar_drive -v`
Expected: `Ran 51 tests` (~7 s) e `OK`

Run: `./run_tests.sh` e depois `pgrep -a ffmpeg`
Expected: `Ran 334 tests` (~48 s) e `OK (skipped=6)`, contando as Tasks 1–8 com as correções; o `pgrep` não
mostra nada

- [ ] **Step 32: Commit**

```bash
git add enviar_drive.py tests/test_enviar_drive.py
git commit -m "fix(enviar_drive): --pasta grava com merge_estado e guarda o estado.json corrompido

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

**Notas para o executor**

- **Base verificada com o rclone v1.75.1 real, só em cópias locais** (binário conferido pelo probe, `HOME` e
  `RCLONE_CONFIG` apontando para uma pasta temporária): arquivo novo → rc 0 com `"Copied (new)"`; arquivo igual
  com `--error-on-no-transfer` → **rc 9**, com ou sem `--dry-run`; só o mtime mudou → `"Updated modification time
  in destination"` e rc 9 (também conta como pulado); `--dry-run` de arquivo novo → rc 0 com `"Skipped copy as
  --dry-run is set"`. `-M --metadata-set description=…` foi aceito (o `lsjson -M` mostrou a descrição no backend
  local); no Drive real isso segue [I], por isso existe a repetição sem descrição quando o log fala em "metadata".
- `rclone lsjson --stat` de um arquivo que não existe sai com **rc 3** e `Failed to lsjson: directory not found`
  (verificado). Sem o remote configurado, `copyto` e `lsjson` saem com rc 1 e `didn't find section in config file
  ("iavoz")`: no `copyto` a linha é JSON (nível `critical`), no `lsjson` é texto. O rclone falso imita essas
  mensagens.
- **Nunca rode o rclone de verdade nos testes.** Qualquer comando dele, até `rclone config file`, cria
  `~/.config/rclone/` se `RCLONE_CONFIG` não estiver definido. O `fake_env()` define `RCLONE_CONFIG` num tempdir
  por segurança, e o rclone falso recusa (rc 2) `sync`, `move`, `delete`, `dedupe`, `link`, `--no-check-dest`,
  `-vv` e `--config`, e exige `--drive-root-folder-id`.
- O `setpriv` e o `/usr/bin/env python3` do rclone falso fazem `exec`: o `p.pid` do `Popen` é o do próprio
  rclone, então o `terminate()` do cancelamento chega direto nele. O `--pdeathsig` vale para a **thread** que
  criou o filho: `upload_files` inteiro precisa rodar na mesma thread de trabalho (a thread de cancelamento só
  manda sinal, não cria processo).
- `flock` é por descrição de arquivo aberto: duas `UploadLock` no mesmo processo também se bloqueiam (isso
  protege contra clique duplo na GUI), e o kernel solta a trava se o processo morrer.
- `tests/test_enviar_drive.py` importa `FID`, `LINK`, `NAMES`, `fake_env`, `make_mp4` e `md5` de
  `tests.test_drive_upload`; rode sempre da raiz do repositório.
- O modo `hang` do rclone falso termina sozinho depois de 60 s, para um teste com defeito nunca deixar processo
  eterno. O teste de cancelamento confere que o pid anotado em `FAKE_RCLONE_CALLS` morreu.
- Erros que valem para a pasta inteira (`FATAL_MSGS`: sem configuração, login expirado, sem acesso, Drive cheio)
  interrompem o lote: os arquivos seguintes recebem o mesmo erro sem chamar o rclone (cada falha real custa até
  3 tentativas com 10 s de espera).
- A conferência calcula o MD5 local de todo arquivo enviado **ou pulado** (≈ 1 s a cada 500 MB); com muitos vídeos
  no cron isso relê a pasta inteira a cada rodada, o que é aceitável.
- `test_stdlib_only` importa `enviar_drive` com `python -S` (sem site-packages), prova de que `drive.py` e a CLI
  só usam a stdlib; ele cria um `__pycache__/` na raiz, que já está no `.gitignore`.
- **Correções da revisão (Steps 18–32):**
  - Os dois vídeos de `NAMES` agora são do Silvio. O MP4 modelo dos testes leva o aviso do Silvio, e o
    `check_uploadable` exige o aviso exato do modelo do nome, então um `*_orochi_IA.mp4` com o aviso do Silvio é
    recusado. O caso do Orochi tem teste próprio, em `CheckUploadableTest`, com o aviso dele.
  - O caminho é conferido com `os.path.realpath` dos dois lados e exige o arquivo **direto** em `videos_dir`, como o
    `list_videos`. Um symlink dentro de `videos_finais/` que aponta para fora é recusado. O `check_uploadable(path)`
    sem `videos_dir` compara com o `videos_finais/` do projeto: um teste confere que um vídeo bom numa pasta
    temporária é recusado assim.
  - A GUI (Task 11) já passa `videos_dir` ao `upload_files`, e o vídeo que ela envia é o que o render publicou
    lá, então nada muda para ela.

---

### Task 9: Esqueleto da GUI (eventos, filas de jobs, App e ponto de entrada fino)

**Files:**
- Create: `studio/events.py`
- Create: `studio/gui.py`
- Modify: `orochi_studio.py` (vira fino: só `from studio.gui import main` + `main()`; o código antigo sai inteiro)
- Test: `tests/test_events.py`, `tests/test_gui_shell.py`

**Interfaces:**
- Consumes:
  - Task 1, `studio.config`: `ESTADO_PATH`, `LOGS_DIR`, `REC_DIR`, `STUDIO_LOG`, `MODELOS`, `DEFAULT_ESTADO`,
    `Modelo(key, label, nome, aviso)`, `get_modelo(key: str) -> Modelo` (ValueError se desconhecido),
    `find_latest_checkpoint(model_key: str, logs_dir: str = LOGS_DIR) -> str | None`,
    `load_estado(path: str = ESTADO_PATH) -> tuple[dict, str | None]`, `save_estado(estado: dict, path: str = ESTADO_PATH) -> None`;
    `merge_estado(changes: dict, path: str = ESTADO_PATH) -> tuple[dict, str | None]` (Task 1, Step 28; usado no
    Step 15: relê o arquivo, grava só as chaves de `changes` e guarda o corrompido em `estado.json.corrompido`;
    levanta `OSError`).
  - Task 1, `studio.procs`: `os_error_message(e: OSError) -> str` (Task 1, Step 38; usado no Step 15: "Disco
    cheio — libere espaço" ou "Erro ao acessar o disco (<nome>): <strerror>").
  - Task 1, `studio.takes`: `Take` (`id`, `dir`, `modo`, `status`, `raw_path`, `save()`, `Take.load(dir)`),
    `new_take(modo, mic, camera="", rec_dir=REC_DIR, now=None) -> Take`,
    `latest_take(rec_dir: str = REC_DIR, usable_only: bool = False) -> Take | None` (Task 1, Step 33; o Step 15 usa
    `usable_only=True`: status "gravado", "convertido" ou "renderizado" e `raw_path` existente),
    `recover_takes(rec_dir: str = REC_DIR) -> list[str]` (mensagens PT, ex.: "Tomada <id>: gravação interrompida recuperada").
  - Task 7, `studio.rvc_client`: `RvcClient(python=VENV_PYTHON, log_path=RVC_LOG, env=None)` com `start()` (thread principal),
    `alive() -> bool`, `load() -> dict`, `close() -> None`; `RvcError` (str(e) = mensagem PT). Env `STUDIO_RVC_FAKE=1` = worker falso.
- Produces:
  - `studio/events.py` (só stdlib):
    - `@dataclass class Event: kind: str; data: dict`
    - `class EventBus`: `post(kind: str, **data) -> None` (qualquer thread), `drain() -> list[Event]`.
    - `error_message(exc: BaseException) -> str`: usa `exc.message` se existir, senão `str(exc)`; exceções da stdlib ganham
      o tipo na frente (`"ValueError: boom"`); mensagem vazia vira o nome do tipo.
    - `CANCELLED_MSG = "Cancelado: o app está fechando"`
    - `class JobRunner(bus: EventBus, name: str)`: uma thread daemon `job-<name>` com fila serial.
      `submit(job: str, fn, *args, **kwargs) -> None` (RuntimeError depois do `close()`); todo job termina, no `finally`, em
      `Event("job_ok", {"job", "runner", "result"})` ou `Event("job_fail", {"job", "runner", "message", "traceback"})`
      (captura até `BaseException`); `busy` (property: há job na fila ou rodando; já é `False` quando o último evento
      chega); `current` (property: nome do job rodando ou `None`); `close() -> None` (não bloqueia; o job atual termina,
      os da fila viram `job_fail` com `CANCELLED_MSG` e a thread sai).
  - `studio/gui.py` (único módulo que importa tkinter):
    - constantes `TITLE`, `GEOMETRY = "1100x720"`, `MIN_SIZE = (980, 640)`, `LEFT_MIN_W = 500`, `PUMP_MS = 50`,
      `JOB_LOAD_MODEL = "load_model"`, `JOB_LABELS: dict[str, str]` (job → texto PT na confirmação ao fechar; as tasks
      10/11 acrescentam os seus), `RUNNER_LABELS`, `COLOR_BAD`, `COLOR_BUSY`, `COLOR_OK`.
    - `class App(root: tk.Tk, rvc_factory=RvcClient, rec_dir: str = REC_DIR, estado_path: str = ESTADO_PATH,
      log_path: str = STUDIO_LOG, logs_dir: str = LOGS_DIR, ask_confirm=None)` (`ask_confirm(title, message) -> bool`;
      padrão `messagebox.askyesno`). Atributos: `root`, `rec_dir`, `estado_path`, `log_path`, `logs_dir` (os caminhos
      recebidos), `left`, `right` (colunas), `log_box` (tk.Text), `model_frame`,
      `model_var`, `model_box`, `checkpoint_label`, `model_status`, `btn_load`, `bus`, `gpu_jobs`, `io_jobs`,
      `upload_jobs`, `rvc` (`None` até `ensure_rvc()`), `model_loaded`, `estado`, `take`, `closing`, `closed`.
      Métodos: `add_section(title: str, parent=None) -> ttk.LabelFrame` (empilha na coluna direita por padrão);
      `log(msg: str)` (hora no painel + linha datada em `log_path`; chamado de outra thread vira evento `"log"`);
      `report_error(exc, where="interface")` (linha curta PT + traceback em `log_path`);
      `report_callback_exception(exc_type, exc, tb)`; `on(kind: str, fn)` (`fn(ev: Event)` na thread principal);
      `dispatch_events()` (o pump chama a cada `PUMP_MS`; exceção de um assinante vira linha no log e não para os outros);
      `update_estado(**changes)` (Step 15: grava só `changes` com `merge_estado`, sem desfazer o que outro programa
      gravou; `self.estado` passa a ser o que ficou no arquivo; o aviso vira `AVISO: …` no log; `OSError` vira
      "Não foi possível salvar estado.json: <os_error_message>" e a mudança vale só na memória);
      `modelo() -> Modelo`; `on_model_change(event=None)` (posta `"model_changed"` com `modelo=Modelo`);
      `set_take(take: Take | None)` (posta `"take_changed"` com `take=`); `ensure_rvc() -> RvcClient` (só na thread
      principal, senão RuntimeError; recria o worker morto e loga "Conversor reiniciado"); `on_load_model()`;
      a abertura carrega a última tomada **utilizável** (Step 15; uma que falhou ao começar é pulada);
      `register_closer(fn)` (`fn() -> None | str`); `add_shutdown_hook(fn)`; `close_reasons() -> list[str]`;
      `on_close()` (WM_DELETE_WINDOW); `shutdown()`.
    - Eventos que o App publica/consome: `"log"` `{msg}`, `"error"` `{message}`, `"take_changed"` `{take}`,
      `"model_changed"` `{modelo}`, `"job_ok"`/`"job_fail"` (todo `job_fail` com traceback vai para `log_path`).
    - `main() -> None`: `tk.Tk()`, `App(root)`, `root.mainloop()`.
  - `orochi_studio.py`: `from studio.gui import main` (o atalho `iniciar_orochi_studio.sh` continua igual).

Todos os comandos rodam da raiz do repositório (`~/orochi-ia-homenagem`). Os testes da GUI precisam de um display X11
(`DISPLAY`); a janela fica escondida (`root.withdraw()`) e nenhum teste usa câmera, microfone ou GPU.

- [ ] **Step 1: Escrever o teste do barramento e das filas que falha**

`tests/test_events.py`:

```python
import threading
import time
import unittest

from studio.events import Event, EventBus, JobRunner, error_message


def wait_events(bus: EventBus, n: int, timeout: float = 5.0) -> list[Event]:
    got: list[Event] = []
    end = time.monotonic() + timeout
    while len(got) < n and time.monotonic() < end:
        got.extend(bus.drain())
        time.sleep(0.005)
    return got


class PtError(Exception):
    """Excecao do projeto: str(e) ja e a mensagem em PT."""


class EventBusTest(unittest.TestCase):
    def test_post_and_drain_in_order(self):
        bus = EventBus()
        bus.post("a", x=1)
        bus.post("b")
        evs = bus.drain()
        self.assertEqual([Event("a", {"x": 1}), Event("b", {})], evs)
        self.assertEqual([], bus.drain())

    def test_post_from_many_threads(self):
        bus = EventBus()

        def producer(k):
            for i in range(200):
                bus.post("n", k=k, i=i)

        threads = [threading.Thread(target=producer, args=(k,)) for k in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        evs = bus.drain()
        self.assertEqual(800, len(evs))
        for k in range(4):
            self.assertEqual(list(range(200)), [e.data["i"] for e in evs if e.data["k"] == k])


class ErrorMessageTest(unittest.TestCase):
    def test_project_errors_keep_message(self):
        self.assertEqual("Câmera não encontrada", error_message(PtError("Câmera não encontrada")))

    def test_builtin_errors_get_type(self):
        self.assertEqual("ValueError: boom", error_message(ValueError("boom")))

    def test_message_attribute_wins(self):
        e = PtError("detalhe tecnico")
        e.message = "Mensagem em PT"
        self.assertEqual("Mensagem em PT", error_message(e))

    def test_empty_message_uses_type(self):
        self.assertEqual("PtError", error_message(PtError()))


class JobRunnerTest(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.runner = JobRunner(self.bus, "teste")
        self.addCleanup(self.runner.close)

    def test_ok_event_with_result(self):
        self.runner.submit("soma", lambda a, b=0: a + b, 2, b=3)
        evs = wait_events(self.bus, 1)
        self.assertEqual([Event("job_ok", {"job": "soma", "runner": "teste", "result": 5})], evs)

    def test_fail_event_with_message_and_traceback(self):
        def bad():
            raise ValueError("boom")

        self.runner.submit("ruim", bad)
        (ev,) = wait_events(self.bus, 1)
        self.assertEqual("job_fail", ev.kind)
        self.assertEqual("ruim", ev.data["job"])
        self.assertEqual("ValueError: boom", ev.data["message"])
        self.assertIn("Traceback", ev.data["traceback"])
        self.assertIn("in bad", ev.data["traceback"])

    def test_runner_survives_failures_even_base_exceptions(self):
        def exits():
            raise SystemExit(3)

        self.runner.submit("sai", exits)
        self.runner.submit("depois", lambda: "ok")
        evs = wait_events(self.bus, 2)
        self.assertEqual(["job_fail", "job_ok"], [e.kind for e in evs])
        self.assertEqual("ok", evs[1].data["result"])

    def test_jobs_run_serially_in_order(self):
        running, peak, order = [0], [0], []
        lock = threading.Lock()

        def job(i):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            time.sleep(0.02)
            order.append(i)
            with lock:
                running[0] -= 1
            return i

        for i in range(5):
            self.runner.submit(f"j{i}", job, i)
        evs = wait_events(self.bus, 5)
        self.assertEqual(list(range(5)), order)
        self.assertEqual(1, peak[0])
        self.assertEqual([f"j{i}" for i in range(5)], [e.data["job"] for e in evs])

    def test_runs_in_worker_thread(self):
        self.runner.submit("thread", lambda: threading.current_thread().name)
        (ev,) = wait_events(self.bus, 1)
        self.assertNotEqual(threading.current_thread().name, ev.data["result"])
        self.assertIn("teste", ev.data["result"])

    def test_busy_and_current(self):
        gate = threading.Event()
        started = threading.Event()

        def waits():
            started.set()
            gate.wait(5)

        self.assertFalse(self.runner.busy)
        self.assertIsNone(self.runner.current)
        self.runner.submit("espera", waits)
        self.assertTrue(self.runner.busy)
        self.assertTrue(started.wait(5))
        self.assertEqual("espera", self.runner.current)
        gate.set()
        (ev,) = wait_events(self.bus, 1)
        # o evento so chega depois que o runner ja nao esta ocupado
        self.assertEqual("job_ok", ev.kind)
        self.assertFalse(self.runner.busy)
        self.assertIsNone(self.runner.current)

    def test_busy_is_false_when_last_event_arrives(self):
        for i in range(20):
            self.runner.submit(f"j{i}", time.sleep, 0.001)
        seen = []
        end = time.monotonic() + 5
        while len(seen) < 20 and time.monotonic() < end:
            for ev in self.bus.drain():
                seen.append((ev.data["job"], self.runner.busy))
        self.assertEqual(("j19", False), seen[-1])

    def test_close_cancels_queued_jobs_and_stops(self):
        gate = threading.Event()
        started = threading.Event()
        ran = []

        def first():
            started.set()
            gate.wait(5)
            return "primeiro"

        self.runner.submit("primeiro", first)
        self.runner.submit("segundo", ran.append, "segundo")
        self.assertTrue(started.wait(5))
        self.runner.close()
        with self.assertRaises(RuntimeError):
            self.runner.submit("depois", ran.append, "depois")
        gate.set()
        evs = wait_events(self.bus, 2)
        self.assertEqual(["job_ok", "job_fail"], [e.kind for e in evs])
        self.assertEqual("segundo", evs[1].data["job"])
        self.assertIn("fechando", evs[1].data["message"])
        self.assertEqual([], ran)
        self.runner._thread.join(5)
        self.assertFalse(self.runner._thread.is_alive())
        self.assertFalse(self.runner.busy)

    def test_close_is_idempotent(self):
        self.runner.close()
        self.runner.close()
        self.runner._thread.join(5)
        self.assertFalse(self.runner._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_events -v`
Expected: ERROR com "ModuleNotFoundError: No module named 'studio.events'"

- [ ] **Step 3: Implementar `studio/events.py`**

`studio/events.py`:

```python
"""Fila de eventos das threads de trabalho para a thread do Tk e filas seriais de jobs (so stdlib)."""

import queue
import threading
import traceback
from dataclasses import dataclass

CANCELLED_MSG = "Cancelado: o app está fechando"


@dataclass
class Event:
    kind: str
    data: dict


class EventBus:
    def __init__(self):
        self._q: queue.SimpleQueue = queue.SimpleQueue()

    def post(self, kind: str, **data) -> None:
        # pode ser chamado de qualquer thread
        self._q.put(Event(kind, data))

    def drain(self) -> list[Event]:
        out = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                return out


def error_message(exc: BaseException) -> str:
    # excecoes do projeto ja trazem a mensagem em PT; as da stdlib ganham o tipo na frente
    msg = getattr(exc, "message", None) or str(exc)
    if not msg:
        return type(exc).__name__
    if type(exc).__module__ == "builtins":
        return f"{type(exc).__name__}: {msg}"
    return msg


class JobRunner:
    """Uma thread de trabalho longa com fila serial; todo job termina em job_ok ou job_fail."""

    def __init__(self, bus: EventBus, name: str):
        self.bus = bus
        self.name = name
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._pending = 0
        self._current: str | None = None
        self._closed = False
        self._thread = threading.Thread(target=self._loop, name=f"job-{name}", daemon=True)
        self._thread.start()

    def submit(self, job: str, fn, *args, **kwargs) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError(f"JobRunner {self.name} fechado")
            self._pending += 1
        self._q.put((job, fn, args, kwargs))

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._pending > 0

    @property
    def current(self) -> str | None:
        with self._lock:
            return self._current

    def close(self) -> None:
        # nao bloqueia: o job atual termina, os da fila viram job_fail e a thread sai
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._q.put(None)

    def _loop(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            job, fn, args, kwargs = item
            with self._lock:
                cancelled = self._closed
                if not cancelled:
                    self._current = job
            if cancelled:
                self._finish("job_fail", job=job, message=CANCELLED_MSG, traceback="")
            else:
                self._run(job, fn, args, kwargs)

    def _run(self, job: str, fn, args, kwargs) -> None:
        kind, data = "job_fail", {"message": "Falha desconhecida", "traceback": ""}
        try:
            data = {"result": fn(*args, **kwargs)}
            kind = "job_ok"
        except BaseException as e:
            data = {"message": error_message(e), "traceback": "".join(traceback.format_exception(e))}
        finally:
            self._finish(kind, job=job, **data)

    def _finish(self, kind: str, **data) -> None:
        # libera o "ocupado" antes de postar: quem recebe o ultimo evento ja ve busy == False
        with self._lock:
            self._pending -= 1
            self._current = None
        self.bus.post(kind, runner=self.name, **data)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_events -v`
Expected: `OK` (15 testes)

- [ ] **Step 5: Commit**

```bash
git add studio/events.py tests/test_events.py
git commit -m "feat(studio): events com EventBus e JobRunner serial que sempre posta job_ok/job_fail

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: Escrever o teste do esqueleto da GUI que falha**

`tests/test_gui_shell.py`:

```python
import gc
import json
import os
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest import mock

import numpy as np
import soundfile as sf

from studio import gui
from studio.config import DEFAULT_ESTADO
from studio.rvc_client import RvcClient, RvcError
from studio.takes import Take, new_take

HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


class FakeRvc:
    def __init__(self, load_error: Exception | None = None):
        self.load_error = load_error
        self.start_threads: list[str] = []
        self.loads = 0
        self.closed = 0
        self._alive = False

    def start(self):
        self.start_threads.append(threading.current_thread().name)
        self._alive = True

    def alive(self):
        return self._alive

    def load(self):
        self.loads += 1
        if self.load_error:
            raise self.load_error
        return {"id": "1", "ok": True}

    def close(self):
        self.closed += 1
        self._alive = False


def pump_until(app, cond, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.root.update()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def write_audio_take(rec_dir: str, take_id: str, status: str, seconds: float = 0.5) -> Take:
    take = Take(id=take_id, dir=os.path.join(rec_dir, take_id), modo="audio", status=status, mic="mic")
    os.makedirs(take.dir)
    sf.write(take.raw_path, np.zeros(int(48000 * seconds), dtype=np.int16), 48000, subtype="PCM_16")
    take.save()
    return take


@unittest.skipUnless(HAS_DISPLAY, "sem display")
class GuiShellTest(unittest.TestCase):
    def setUp(self):
        # roda por ultimo: o lixo do Tk (StringVar, Tk) tem que morrer na thread principal; se o GC
        # automatico o coletar numa thread de trabalho, o Tcl aborta o processo (Tcl_AsyncDelete)
        self.addCleanup(gc.collect)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rec_dir = os.path.join(self.tmp.name, "recordings")
        self.estado_path = os.path.join(self.tmp.name, "estado.json")
        self.log_path = os.path.join(self.tmp.name, "studio.log")
        self.logs_dir = os.path.join(self.tmp.name, "logs")
        self.confirm_calls: list[tuple[str, str]] = []
        self.confirm_answer = False
        self.hook_before = threading.excepthook

    def confirm(self, title: str, message: str) -> bool:
        self.confirm_calls.append((title, message))
        return self.confirm_answer

    def make_app(self, **kw) -> gui.App:
        root = tk.Tk()
        root.withdraw()
        kw.setdefault("rvc_factory", FakeRvc)
        app = gui.App(root, rec_dir=self.rec_dir, estado_path=self.estado_path, log_path=self.log_path,
                      logs_dir=self.logs_dir, ask_confirm=self.confirm, **kw)
        self.addCleanup(self.close_app, app)
        return app

    def close_app(self, app: gui.App) -> None:
        if not app.closed:
            app.shutdown()
        self.assertIs(self.hook_before, threading.excepthook)

    def log_text(self, app: gui.App) -> str:
        return app.log_box.get("1.0", "end")

    def file_text(self) -> str:
        with open(self.log_path, encoding="utf-8") as f:
            return f.read()

    # ---------- janela ----------

    def test_window_shell(self):
        app = self.make_app()
        self.assertEqual("Voice Studio (Orochi / Silvio)", app.root.title())
        self.assertEqual((True, True), app.root.resizable())
        w, h = app.root.minsize()
        self.assertGreaterEqual(w, 900)
        self.assertGreaterEqual(h, 600)
        self.assertIs(app.root, app.left.winfo_toplevel())
        self.assertIs(app.root, app.right.winfo_toplevel())
        self.assertEqual("1. Modelo", app.model_frame.cget("text"))
        self.assertIs(app.right, app.model_frame.master)
        self.assertEqual("gpu", app.gpu_jobs.name)
        self.assertEqual("io", app.io_jobs.name)
        self.assertEqual("upload", app.upload_jobs.name)
        self.assertIsNone(app.rvc)       # o worker so nasce quando alguem pede
        self.assertIsNone(app.take)

    def test_add_section_goes_to_right_column(self):
        app = self.make_app()
        frame = app.add_section("2. Volume")
        self.assertIs(app.right, frame.master)
        self.assertEqual("2. Volume", frame.cget("text"))

    # ---------- log ----------

    def test_log_writes_panel_and_file(self):
        app = self.make_app()
        app.log("Olá, gravação ✓")
        self.assertRegex(self.log_text(app), r"\[\d\d:\d\d:\d\d\] Olá, gravação ✓")
        self.assertRegex(self.file_text(), r"\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] Olá, gravação ✓")

    def test_log_from_other_thread_goes_through_bus(self):
        app = self.make_app()
        t = threading.Thread(target=app.log, args=("vindo da thread",))
        t.start()
        t.join()
        self.assertNotIn("vindo da thread", self.log_text(app))
        app.dispatch_events()
        self.assertIn("vindo da thread", self.log_text(app))

    def test_log_event_from_worker(self):
        app = self.make_app()
        app.bus.post("log", msg="mensagem do job")
        self.assertTrue(pump_until(app, lambda: "mensagem do job" in self.log_text(app)))

    # ---------- estado ----------

    def test_estado_corrompido_gera_aviso(self):
        with open(self.estado_path, "w") as f:
            f.write("{nao e json")
        app = self.make_app()
        self.assertEqual(DEFAULT_ESTADO, app.estado)
        self.assertIn("AVISO: estado.json corrompido", self.log_text(app))

    def test_estado_saved_on_change(self):
        app = self.make_app()
        self.assertFalse(os.path.exists(self.estado_path))
        app.update_estado(mic="alsa_input.usb-teste", av_offset_ms=40)
        with open(self.estado_path, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual("alsa_input.usb-teste", saved["mic"])
        self.assertEqual(40, saved["av_offset_ms"])
        self.assertEqual("alsa_input.usb-teste", app.estado["mic"])

    def test_estado_save_error_is_logged(self):
        app = self.make_app()
        app.estado_path = os.path.join(self.tmp.name, "nao", "existe", "estado.json")
        app.update_estado(mic="x")
        self.assertIn("Não foi possível salvar estado.json", self.log_text(app))

    # ---------- modelo ----------

    def test_model_from_estado_and_change(self):
        os.makedirs(os.path.join(self.logs_dir, "silvio"))
        for name in ("silvio_100e_1000s.pth", "silvio_350e_3500s.pth"):
            open(os.path.join(self.logs_dir, "silvio", name), "w").close()
        with open(self.estado_path, "w") as f:
            json.dump({"modelo": "orochi"}, f)
        app = self.make_app()
        got = []
        app.on("model_changed", got.append)
        self.assertEqual("orochi", app.modelo().key)
        self.assertIn("NENHUM CHECKPOINT", app.checkpoint_label.cget("text"))
        self.assertIn("AVISO: nenhum checkpoint", self.log_text(app))

        app.model_var.set("Silvio Santos")
        app.model_box.event_generate("<<ComboboxSelected>>")
        app.dispatch_events()
        self.assertEqual("silvio", app.modelo().key)
        self.assertEqual("Checkpoint: silvio_350e_3500s.pth", app.checkpoint_label.cget("text"))
        self.assertEqual("silvio", got[0].data["modelo"].key)
        with open(self.estado_path, encoding="utf-8") as f:
            self.assertEqual("silvio", json.load(f)["modelo"])
        self.assertIn("Modelo selecionado: Silvio Santos", self.log_text(app))

    def test_unknown_model_in_estado_falls_back(self):
        with open(self.estado_path, "w") as f:
            json.dump({"modelo": "xyz"}, f)
        app = self.make_app()
        self.assertEqual("orochi", app.modelo().key)
        self.assertEqual("Orochi", app.model_var.get())

    # ---------- erros ----------

    def test_callback_exception_vira_linha_no_log(self):
        app = self.make_app()

        def bad():
            return 1 / 0

        app.root.after(0, bad)
        self.assertTrue(pump_until(app, lambda: "ZeroDivisionError" in self.log_text(app)))
        line = [x for x in self.log_text(app).splitlines() if "ZeroDivisionError" in x][0]
        self.assertIn("Erro inesperado", line)
        self.assertIn("studio.log", line)
        self.assertNotIn("Traceback", self.log_text(app))
        self.assertIn("Traceback", self.file_text())
        self.assertIn("in bad", self.file_text())

    def test_thread_exception_vira_linha_no_log(self):
        app = self.make_app()

        def boom():
            raise RuntimeError("falhou na thread")

        t = threading.Thread(target=boom, name="leitor-teste")
        t.start()
        t.join()
        self.assertIn("in boom", self.file_text())
        self.assertIn("leitor-teste", self.file_text())
        app.dispatch_events()
        self.assertIn("RuntimeError: falhou na thread", self.log_text(app))

    def test_subscriber_exception_does_not_stop_others(self):
        app = self.make_app()
        got = []

        def bad(ev):
            raise KeyError("x")

        app.on("ping", bad)
        app.on("ping", got.append)
        app.bus.post("ping")
        app.dispatch_events()
        self.assertEqual(1, len(got))
        self.assertIn("KeyError", self.log_text(app))

    def test_job_fail_traceback_goes_to_file(self):
        app = self.make_app()

        def job():
            raise OSError("disco cheio")

        app.io_jobs.submit("teste", job)
        self.assertTrue(pump_until(app, lambda: "in job" in (self.file_text() if os.path.exists(self.log_path)
                                                            else "")))
        self.assertIn("teste", self.file_text())

    # ---------- tomada ----------

    def test_take_changed_chega_aos_assinantes(self):
        app = self.make_app()
        got = []
        app.on("take_changed", got.append)
        take = new_take("audio", "mic", rec_dir=self.rec_dir)
        app.set_take(take)
        self.assertIs(take, app.take)
        self.assertTrue(pump_until(app, lambda: got))
        self.assertIs(take, got[0].data["take"])

    def test_startup_recovers_and_loads_latest_take(self):
        write_audio_take(self.rec_dir, "2026-09-26_100000", "convertido")
        write_audio_take(self.rec_dir, "2026-09-26_110000", "gravando")
        app = self.make_app()
        self.assertEqual("2026-09-26_110000", app.take.id)
        self.assertEqual("gravado", app.take.status)
        self.assertEqual("gravado", Take.load(app.take.dir).status)
        self.assertIn("Tomada 2026-09-26_110000: gravação interrompida recuperada", self.log_text(app))
        self.assertIn("Última tomada: 2026-09-26_110000", self.log_text(app))

    def test_startup_without_takes(self):
        app = self.make_app()
        self.assertIsNone(app.take)
        self.assertIn("Pasta de gravações", self.log_text(app))

    # ---------- conversor ----------

    def test_load_model_button(self):
        app = self.make_app()
        app.btn_load.invoke()
        self.assertEqual([threading.current_thread().name], app.rvc.start_threads)
        self.assertEqual("disabled", str(app.btn_load.cget("state")))
        self.assertTrue(pump_until(app, lambda: "carregado" in app.model_status.cget("text")))
        self.assertEqual(1, app.rvc.loads)
        self.assertTrue(app.model_loaded)
        self.assertIn("Modelo carregado", self.log_text(app))

    def test_load_model_failure_reenables_button(self):
        app = self.make_app(rvc_factory=lambda: FakeRvc(load_error=RvcError("GPU sem memória")))
        app.btn_load.invoke()
        self.assertTrue(pump_until(app, lambda: "falhou" in app.model_status.cget("text")))
        self.assertIn("GPU sem memória", self.log_text(app))
        self.assertEqual("normal", str(app.btn_load.cget("state")))
        self.assertFalse(app.model_loaded)

    def test_ensure_rvc_restarts_dead_worker(self):
        app = self.make_app()
        rvc = app.ensure_rvc()
        self.assertIs(rvc, app.ensure_rvc())
        self.assertEqual(1, len(rvc.start_threads))
        app.model_loaded = True
        rvc._alive = False                  # worker morreu
        self.assertIs(rvc, app.ensure_rvc())
        self.assertEqual(2, len(rvc.start_threads))
        self.assertIn("Conversor reiniciado", self.log_text(app))
        self.assertFalse(app.model_loaded)
        self.assertIn("não carregado", app.model_status.cget("text"))

    def test_ensure_rvc_refuses_worker_thread(self):
        app = self.make_app()
        errors = []

        def call():
            try:
                app.ensure_rvc()
            except RuntimeError as e:
                errors.append(e)

        t = threading.Thread(target=call)
        t.start()
        t.join()
        self.assertEqual(1, len(errors))
        self.assertIsNone(app.rvc)

    def test_load_model_with_fake_worker_process(self):
        rvc_log = os.path.join(self.tmp.name, "rvc.log")
        app = self.make_app(rvc_factory=lambda: RvcClient(log_path=rvc_log, env={"STUDIO_RVC_FAKE": "1"}))
        app.btn_load.invoke()
        self.assertTrue(pump_until(app, lambda: app.model_loaded, timeout=30))
        self.assertTrue(app.rvc.alive())
        app.on_close()
        self.assertTrue(app.closed)
        self.assertIsNone(app.rvc._proc)     # worker encerrado junto

    # ---------- fechar ----------

    def test_on_close_sem_nada_rodando_fecha(self):
        app = self.make_app()
        rvc = app.ensure_rvc()
        root = app.root
        app.on_close()
        self.assertEqual([], self.confirm_calls)
        self.assertTrue(app.closed)
        self.assertEqual(1, rvc.closed)
        self.assertRaises(tk.TclError, root.winfo_exists)
        for runner in (app.gpu_jobs, app.io_jobs, app.upload_jobs):
            with self.assertRaises(RuntimeError):
                runner.submit("depois", print)
        self.assertIs(self.hook_before, threading.excepthook)

    def test_close_button_is_wired(self):
        app = self.make_app()
        self.assertIn("on_close", app.root.protocol("WM_DELETE_WINDOW"))

    def test_on_close_asks_panels_and_respects_no(self):
        app = self.make_app()
        app.register_closer(lambda: None)
        app.register_closer(lambda: "Envio para o Drive em andamento")
        app.on_close()
        self.assertFalse(app.closed)
        self.assertEqual(1, len(self.confirm_calls))
        self.assertIn("Envio para o Drive em andamento", self.confirm_calls[0][1])
        self.confirm_answer = True
        app.on_close()
        self.assertTrue(app.closed)

    def test_on_close_asks_when_job_running(self):
        app = self.make_app()
        gate, started = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        app.gpu_jobs.submit(gui.JOB_LOAD_MODEL, lambda: (started.set(), gate.wait(5)))
        app.upload_jobs.submit("fila", gate.wait, 5)
        self.assertTrue(started.wait(5))
        app.on_close()
        self.assertFalse(app.closed)
        self.assertIn("Carregando o modelo", self.confirm_calls[0][1])
        # job ainda sem nome conhecido: cai no rotulo da fila
        self.assertIn("envio para o Drive", self.confirm_calls[0][1])

    def test_closer_error_counts_as_reason(self):
        app = self.make_app()

        def broken():
            raise ValueError("x")

        app.register_closer(broken)
        app.on_close()
        self.assertFalse(app.closed)
        self.assertEqual(1, len(self.confirm_calls))

    def test_shutdown_hooks_run_before_destroy(self):
        app = self.make_app()
        seen = []
        app.add_shutdown_hook(lambda: seen.append(app.root.winfo_exists()))

        def broken():
            raise RuntimeError("hook quebrado")

        app.add_shutdown_hook(broken)
        app.on_close()
        self.assertEqual([1], seen)
        self.assertTrue(app.closed)
        self.assertIn("hook quebrado", self.file_text())


class MainTest(unittest.TestCase):
    def test_main_creates_tk_and_app(self):
        with mock.patch.object(gui.tk, "Tk") as tk_cls, mock.patch.object(gui, "App") as app_cls:
            gui.main()
        app_cls.assert_called_once_with(tk_cls.return_value)
        tk_cls.return_value.mainloop.assert_called_once_with()

    def test_entrypoint_is_thin(self):
        import orochi_studio
        self.assertIs(gui.main, orochi_studio.main)
        with open(orochi_studio.__file__, encoding="utf-8") as f:
            self.assertLess(len(f.read().splitlines()), 15)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 7: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_shell -v`
Expected: ERROR com "ImportError: cannot import name 'gui' from 'studio'"

- [ ] **Step 8: Implementar `studio/gui.py`**

`studio/gui.py`:

```python
"""Janela do Voice Studio (Tk): duas colunas + log, fila de eventos, filas de jobs e o conversor RVC.

Unico modulo que importa tkinter. So a thread principal mexe no estado do App e nos widgets.
"""

import os
import threading
import tkinter as tk
import traceback
from datetime import datetime
from tkinter import messagebox, ttk

from studio.config import (ESTADO_PATH, LOGS_DIR, MODELOS, REC_DIR, STUDIO_LOG, Modelo, find_latest_checkpoint,
                           get_modelo, load_estado, save_estado)
from studio.events import Event, EventBus, JobRunner, error_message
from studio.rvc_client import RvcClient
from studio.takes import Take, latest_take, recover_takes

TITLE = "Voice Studio (Orochi / Silvio)"
GEOMETRY = "1100x720"
MIN_SIZE = (980, 640)
LEFT_MIN_W = 500          # preview 480x270 + margens
PUMP_MS = 50
JOB_LOAD_MODEL = "load_model"
# texto PT de cada job na confirmacao ao fechar (as outras tasks acrescentam os seus)
JOB_LABELS = {JOB_LOAD_MODEL: "Carregando o modelo"}
RUNNER_LABELS = {"gpu": "modelo, conversão ou vídeo", "io": "arquivos", "upload": "envio para o Drive"}
COLOR_BAD, COLOR_BUSY, COLOR_OK = "#a33", "#a80", "#2a2"


class App:
    def __init__(self, root: tk.Tk, rvc_factory=RvcClient, rec_dir: str = REC_DIR,
                 estado_path: str = ESTADO_PATH, log_path: str = STUDIO_LOG, logs_dir: str = LOGS_DIR,
                 ask_confirm=None):
        self.root = root
        self.rec_dir = rec_dir
        self.estado_path = estado_path
        self.log_path = log_path
        self.logs_dir = logs_dir
        self._rvc_factory = rvc_factory
        self._ask_confirm = ask_confirm or self._ask_yes_no
        self.rvc = None
        self._rvc_started = False
        self.model_loaded = False
        self.take: Take | None = None
        self.closing = False
        self.closed = False
        self._subs: dict[str, list] = {}
        self._closers: list = []
        self._shutdown_hooks: list = []
        self._file_lock = threading.Lock()
        self._models_by_label = {m.label: m for m in MODELOS}

        self.bus = EventBus()
        self.gpu_jobs = JobRunner(self.bus, "gpu")
        self.io_jobs = JobRunner(self.bus, "io")
        self.upload_jobs = JobRunner(self.bus, "upload")
        self.estado, aviso_estado = load_estado(estado_path)

        root.title(TITLE)
        root.geometry(GEOMETRY)
        root.minsize(*MIN_SIZE)
        root.resizable(True, True)
        self._build_layout()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.report_callback_exception = self.report_callback_exception
        self._prev_excepthook = threading.excepthook
        threading.excepthook = self._thread_excepthook
        self.on("log", lambda ev: self.log(ev.data["msg"]))
        self.on("error", lambda ev: self.log(ev.data["message"]))
        self.on("job_ok", self._on_job_ok)
        self.on("job_fail", self._on_job_fail)

        self._build_model_section()
        # os paineis das outras tasks entram aqui, antes da abertura (assim recebem o take_changed inicial)
        self._startup(aviso_estado)
        self._pump_id = root.after(PUMP_MS, self._pump)

    # ---------- layout ----------

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=0, minsize=LEFT_MIN_W)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)
        self.left = ttk.Frame(outer)
        self.left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.right = ttk.Frame(outer)
        self.right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        box = ttk.LabelFrame(outer, text="Log")
        box.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        self.log_box = tk.Text(box, height=8, state="disabled", wrap="word")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y", padx=(0, 6), pady=6)
        self.log_box.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)

    def add_section(self, title: str, parent=None) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent if parent is not None else self.right, text=title)
        frame.pack(fill="x", pady=(0, 8))
        return frame

    def _build_model_section(self) -> None:
        f = self.model_frame = self.add_section("1. Modelo")
        row = ttk.Frame(f)
        row.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(row, text="Voz:").pack(side="left")
        self.model_var = tk.StringVar(master=self.root, value=self._initial_model().label)
        self.model_box = ttk.Combobox(row, textvariable=self.model_var, values=[m.label for m in MODELOS],
                                      width=20, state="readonly")
        self.model_box.pack(side="left", padx=6)
        self.model_box.bind("<<ComboboxSelected>>", self.on_model_change)
        self.checkpoint_label = ttk.Label(f, text=self._checkpoint_text())
        self.checkpoint_label.pack(anchor="w", padx=8, pady=(6, 0))
        self.model_status = ttk.Label(f)
        self.model_status.pack(anchor="w", padx=8)
        self._set_model_status("não carregado", COLOR_BAD)
        self.btn_load = ttk.Button(f, text="Carregar modelo", command=self.on_load_model)
        self.btn_load.pack(anchor="w", padx=8, pady=(4, 8))

    # ---------- log e erros ----------

    def log(self, msg: str) -> None:
        # de outra thread vira evento: so a thread principal toca no Tk
        if threading.current_thread() is not threading.main_thread():
            self.bus.post("log", msg=msg)
            return
        now = datetime.now()
        self._append_file(f"[{now:%Y-%m-%d %H:%M:%S}] {msg}\n")
        if self.closed:
            return
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{now:%H:%M:%S}] {msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _append_file(self, text: str) -> None:
        try:
            with self._file_lock, open(self.log_path, "a", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass

    def _write_traceback(self, where: str, tb_text: str) -> None:
        self._append_file(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] ERRO ({where}):\n{tb_text.rstrip()}\n")

    def _error_line(self, message: str) -> str:
        return f"Erro inesperado: {message} (detalhes em {os.path.basename(self.log_path)})"

    def report_error(self, exc: BaseException, where: str = "interface") -> None:
        # linha curta no painel + traceback no studio.log
        self._write_traceback(where, "".join(traceback.format_exception(exc)))
        self.log(self._error_line(error_message(exc)))

    def report_callback_exception(self, exc_type, exc, tb) -> None:
        self._write_traceback("interface", "".join(traceback.format_exception(exc_type, exc, tb)))
        self.log(self._error_line(error_message(exc)))

    def _thread_excepthook(self, args) -> None:
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread is not None else "?"
        self._write_traceback(f"thread {name}", "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
        msg = error_message(args.exc_value) if args.exc_value is not None else args.exc_type.__name__
        self.bus.post("error", message=self._error_line(msg))

    # ---------- eventos ----------

    def on(self, kind: str, fn) -> None:
        # fn(ev: Event) roda na thread principal
        self._subs.setdefault(kind, []).append(fn)

    def dispatch_events(self) -> None:
        for ev in self.bus.drain():
            if self.closed:
                return
            for fn in list(self._subs.get(ev.kind, ())):
                try:
                    fn(ev)
                except Exception as e:
                    self.report_error(e, f"evento {ev.kind}")

    def _pump(self) -> None:
        try:
            self.dispatch_events()
        finally:
            if not self.closed:
                self._pump_id = self.root.after(PUMP_MS, self._pump)

    def _on_job_ok(self, ev: Event) -> None:
        if ev.data["job"] == JOB_LOAD_MODEL:
            self.model_loaded = True
            self._set_model_status("carregado ✓", COLOR_OK)
            self.log("Modelo carregado. Pronto para converter rápido.")

    def _on_job_fail(self, ev: Event) -> None:
        d = ev.data
        if d.get("traceback"):
            self._write_traceback(f"job {d['job']} ({d.get('runner', '?')})", d["traceback"])
        if d["job"] == JOB_LOAD_MODEL:
            self._model_load_failed(d["message"])

    # ---------- estado, modelo e tomada ----------

    def update_estado(self, **changes) -> None:
        self.estado.update(changes)
        try:
            save_estado(self.estado, self.estado_path)
        except OSError as e:
            self.log(f"Não foi possível salvar estado.json: {error_message(e)}")

    def _initial_model(self) -> Modelo:
        try:
            return get_modelo(self.estado.get("modelo"))
        except ValueError:
            return MODELOS[0]

    def modelo(self) -> Modelo:
        return self._models_by_label.get(self.model_var.get(), MODELOS[0])

    def _checkpoint_text(self) -> str:
        ck = find_latest_checkpoint(self.modelo().key, self.logs_dir)
        return f"Checkpoint: {os.path.basename(ck) if ck else 'NENHUM CHECKPOINT ENCONTRADO'}"

    def _warn_missing_checkpoint(self, m: Modelo) -> None:
        if not find_latest_checkpoint(m.key, self.logs_dir):
            self.log(f"AVISO: nenhum checkpoint encontrado em {os.path.join(self.logs_dir, m.key)}")

    def on_model_change(self, event=None) -> None:
        m = self.modelo()
        self.checkpoint_label.configure(text=self._checkpoint_text())
        self.log(f"Modelo selecionado: {m.label}")
        self._warn_missing_checkpoint(m)
        self.update_estado(modelo=m.key)
        self.bus.post("model_changed", modelo=m)

    def set_take(self, take: Take | None) -> None:
        self.take = take
        self.bus.post("take_changed", take=take)

    def _startup(self, aviso_estado: str | None) -> None:
        self.log(f"Pasta de gravações: {self.rec_dir}")
        if aviso_estado:
            self.log(f"AVISO: {aviso_estado}")
        self._warn_missing_checkpoint(self.modelo())
        try:
            for msg in recover_takes(self.rec_dir):
                self.log(msg)
            take = latest_take(self.rec_dir)
        except Exception as e:
            self.report_error(e, "abertura")
            return
        if take is not None:
            self.log(f"Última tomada: {take.id} ({take.status})")
            self.set_take(take)

    # ---------- conversor RVC ----------

    def ensure_rvc(self):
        # thread principal: o PDEATHSIG do worker vale enquanto a thread que fez o spawn viver
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("ensure_rvc() só pode ser chamado na thread principal")
        if self.rvc is None:
            self.rvc = self._rvc_factory()
        if not self.rvc.alive():
            restarting = self._rvc_started
            self.rvc.start()
            self._rvc_started = True
            if restarting:
                self.log("Conversor reiniciado")
                self.model_loaded = False
                self._set_model_status("não carregado", COLOR_BAD)
                self.btn_load.configure(state="normal")
        return self.rvc

    def _set_model_status(self, text: str, color: str) -> None:
        self.model_status.configure(text=f"Status: {text}", foreground=color)

    def on_load_model(self) -> None:
        try:
            rvc = self.ensure_rvc()
        except Exception as e:
            self._write_traceback("iniciar conversor", "".join(traceback.format_exception(e)))
            self._model_load_failed(f"não foi possível iniciar o conversor: {error_message(e)}")
            return
        self.btn_load.configure(state="disabled")
        self._set_model_status("carregando… (pode levar alguns segundos)", COLOR_BUSY)
        self.log("Carregando o modelo (o conversor importa torch/RVC)…")
        self.gpu_jobs.submit(JOB_LOAD_MODEL, rvc.load)

    def _model_load_failed(self, message: str) -> None:
        self.model_loaded = False
        self._set_model_status("falhou ao carregar", COLOR_BAD)
        self.log(f"ERRO ao carregar o modelo: {message}")
        self.btn_load.configure(state="normal")

    # ---------- fechar ----------

    def register_closer(self, fn) -> None:
        # fn() -> None (pode fechar) | str (motivo em PT para pedir confirmacao)
        self._closers.append(fn)

    def add_shutdown_hook(self, fn) -> None:
        # fn() roda ao fechar de verdade, com a janela ainda visivel (ex.: parar a gravacao)
        self._shutdown_hooks.append(fn)

    def close_reasons(self) -> list[str]:
        reasons = []
        for runner in (self.gpu_jobs, self.io_jobs, self.upload_jobs):
            if runner.busy:
                job = runner.current
                reasons.append(JOB_LABELS.get(job) or
                               f"Trabalho em andamento ({RUNNER_LABELS.get(runner.name, runner.name)})")
        for fn in self._closers:
            try:
                reason = fn()
            except Exception as e:
                self._write_traceback("fechar", "".join(traceback.format_exception(e)))
                reason = "Não foi possível conferir se há trabalho em andamento"
            if reason:
                reasons.append(reason)
        return list(dict.fromkeys(reasons))

    def _ask_yes_no(self, title: str, message: str) -> bool:
        return messagebox.askyesno(title, message, icon="warning", parent=self.root)

    def on_close(self) -> None:
        if self.closing:
            return
        self.closing = True
        ok = False
        try:
            reasons = self.close_reasons()
            ok = not reasons or self._ask_confirm(
                "Fechar o Voice Studio?",
                "\n".join(f"• {r}" for r in reasons) + "\n\nFechar mesmo assim? O que está em andamento será interrompido.")
        finally:
            if not ok:
                self.closing = False
        if ok:
            self.shutdown()

    def shutdown(self) -> None:
        if self.closed:
            return
        self.closing = True
        self.log("Fechando…")
        for fn in self._shutdown_hooks:
            try:
                fn()
            except Exception as e:
                self._write_traceback("fechar", "".join(traceback.format_exception(e)))
        try:
            self.root.withdraw()
        except tk.TclError:
            pass
        for runner in (self.gpu_jobs, self.io_jobs, self.upload_jobs):
            runner.close()
        if self.rvc is not None:
            try:
                self.rvc.close()
            except Exception as e:
                self._write_traceback("fechar", "".join(traceback.format_exception(e)))
        self.closed = True
        if threading.excepthook == self._thread_excepthook:
            threading.excepthook = self._prev_excepthook
        try:
            self.root.after_cancel(self._pump_id)
        except (AttributeError, tk.TclError):
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()
```

- [ ] **Step 9: Trocar `orochi_studio.py` pelo ponto de entrada fino**

Substitua o arquivo inteiro (o código antigo, com `parecord` e `import core` no processo da GUI, sai todo; ele continua
no histórico do git). `orochi_studio.py`:

```python
#!/usr/bin/env python3
"""Voice Studio: grava webcam + mic, troca a voz por IA (RVC), gera o vídeo com marca d'água e envia pro Drive."""

from studio.gui import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 10: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_shell -v`
Expected: `OK` (30 testes)

Run: `./run_tests.sh`
Expected: `Ran 379 tests` e `OK (skipped=6)` (as 334 das Tasks 1–8 com as correções mais os 45 testes novos)

Run: `pgrep -af rvc_worker`
Expected: nenhuma linha (o teste com o worker falso fecha o processo junto com a janela)

- [ ] **Step 11: Conferir a janela (opcional, visual)**

Com a sessão desbloqueada, rode da raiz do repositório. Com a tela bloqueada o `ImageGrab` devolve tudo preto; nesse
caso rode o mesmo comando dentro de um Xephyr (`Xephyr :7 -screen 1120x740 -ac -nolisten tcp &`, depois `DISPLAY=:7`
na frente do comando e `kill %1` no fim). O script abre a janela com o `FakeRvc` dos testes, um `estado.json`
corrompido e uma tomada "gravando" (interrompida) num diretório temporário, clica "Carregar modelo", captura e fecha:

```bash
Applio/.venv/bin/python - <<'EOF'
import os, tempfile, time, tkinter as tk
from PIL import ImageGrab
from studio import gui
from tests.test_gui_shell import FakeRvc, write_audio_take

tmp = tempfile.TemporaryDirectory()
rec = os.path.join(tmp.name, "recordings")
write_audio_take(rec, "2026-09-26_101500", "gravando")       # tomada interrompida: a abertura recupera
estado = os.path.join(tmp.name, "estado.json")
with open(estado, "w") as f:
    f.write("{nao e json")                                      # corrompido: aviso e padroes
root = tk.Tk()
app = gui.App(root, rvc_factory=FakeRvc, rec_dir=rec, estado_path=estado,
              log_path=os.path.join(tmp.name, "studio.log"), ask_confirm=lambda t, m: True)
root.geometry("1100x720+0+0")       # depois do App (ele fixa so o tamanho)


def grab():
    root.update()
    time.sleep(0.3)                   # o compositor desenha a janela
    root.update()
    x0, y0 = root.winfo_rootx(), root.winfo_rooty()
    ImageGrab.grab(bbox=(x0, y0, x0 + root.winfo_width(), y0 + root.winfo_height())).save("_shot_9.png")
    print("coluna esquerda:", app.left.winfo_width(), "px |", app.checkpoint_label.cget("text"), "|",
          app.model_status.cget("text"))
    print(app.log_box.get("1.0", "end").strip())
    app.on_close()


app.btn_load.invoke()
root.after(1500, grab)
root.mainloop()
tmp.cleanup()
EOF
```

Expected (a hora e a pasta temporária mudam):

```text
coluna esquerda: 494 px | Checkpoint: orochi_350e_18550s.pth | Status: carregado ✓
[21:49:19] Pasta de gravações: /tmp/tmpmgrm7bwo/recordings
[21:49:19] AVISO: estado.json corrompido — usando padrões
[21:49:19] Tomada 2026-09-26_101500: gravação interrompida recuperada
[21:49:19] Última tomada: 2026-09-26_101500 (gravado)
[21:49:19] Carregando o modelo (o conversor importa torch/RVC)…
[21:49:19] Modelo carregado. Pronto para converter rápido.
```

O checkpoint é o do `Applio/logs` de verdade (o script não passa `logs_dir`; só lista a pasta). Em `_shot_9.png`: a
coluna esquerda vazia (reservada para a Task 10; a coluna tem 500 px, dos quais 6 são o espaço até a direita), "1.
Modelo" no topo da coluna direita com "Voz: Orochi", o checkpoint, "Status: carregado ✓" em verde e "Carregar
modelo" cinza, e o log embaixo com as mesmas linhas. Olhe e apague (`rm _shot_9.png`); não versione a captura.

- [ ] **Step 12: Commit**

```bash
git add studio/gui.py tests/test_gui_shell.py orochi_studio.py
git commit -m "feat(studio): esqueleto da GUI (colunas, log, eventos, jobs, conversor, fechar) e orochi_studio.py fino

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

**Correções da revisão (Steps 13–17).** (1) Uma tomada que falhou ao começar (câmera em uso, duplo clique em
Gravar) virava a "última tomada" na abertura seguinte e escondia a boa anterior: Converter, Gerar vídeo e Enviar
ficavam desabilitados, e não há seletor de tomadas. Agora a abertura usa `latest_take(..., usable_only=True)` (Task 1,
Step 33). (2) O `update_estado` regravava o `estado.json` inteiro que estava na memória. Assim, o
`av_offset_ms` salvo pelo `calibrar_av.py --salvar` e a pasta salva pelo `enviar_drive.py --pasta` com o app aberto
voltavam ao valor antigo no próximo clique, e um `estado.json` com erro de sintaxe era sobrescrito pelos padrões
(o link do Drive sumia). Agora ele usa o `merge_estado` da Task 1 (Step 28).

- [ ] **Step 13: Testes da abertura e do `estado.json` (falha)**

Em `tests/test_gui_shell.py`, substituir isto:

```python
    def test_estado_save_error_is_logged(self):
        app = self.make_app()
        app.estado_path = os.path.join(self.tmp.name, "nao", "existe", "estado.json")
        app.update_estado(mic="x")
        self.assertIn("Não foi possível salvar estado.json", self.log_text(app))
```

por isto:

```python
    def test_estado_save_error_is_logged(self):
        app = self.make_app()
        app.estado_path = os.path.join(self.tmp.name, "nao", "existe", "estado.json")
        app.update_estado(mic="x")
        self.assertIn("Não foi possível salvar estado.json: Erro ao acessar o disco", self.log_text(app))
        self.assertEqual("x", app.estado["mic"])            # a escolha vale nesta sessao mesmo sem salvar

    def test_estado_written_outside_is_not_undone(self):
        # calibrar_av.py --salvar ou enviar_drive.py --pasta com o app aberto: o app grava so o que mudou
        with open(self.estado_path, "w", encoding="utf-8") as f:
            json.dump({"av_offset_ms": 0}, f)
        app = self.make_app()
        link = "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUv"
        with open(self.estado_path, "w", encoding="utf-8") as f:
            json.dump({"av_offset_ms": 40, "drive_pasta": link}, f)
        app.model_var.set("Silvio Santos")
        app.model_box.event_generate("<<ComboboxSelected>>")
        with open(self.estado_path, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual((40, link, "silvio"), (saved["av_offset_ms"], saved["drive_pasta"], saved["modelo"]))
        self.assertEqual((40, link), (app.estado["av_offset_ms"], app.estado["drive_pasta"]))

    def test_corrupted_estado_is_kept_before_first_save(self):
        broken = '{"drive_pasta": "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUv",}'
        with open(self.estado_path, "w", encoding="utf-8") as f:
            f.write(broken)                                  # virgula sobrando: JSON invalido
        app = self.make_app()
        app.update_estado(mic="alsa_input.usb-teste")
        with open(self.estado_path + ".corrompido", encoding="utf-8") as f:
            self.assertEqual(broken, f.read())               # o link continua la para recuperar a mao
        with open(self.estado_path, encoding="utf-8") as f:
            self.assertEqual("alsa_input.usb-teste", json.load(f)["mic"])
        self.assertIn("AVISO: estado.json corrompido — cópia guardada em estado.json.corrompido", self.log_text(app))
```

E em `tests/test_gui_shell.py`, substituir isto:

```python
    def test_startup_without_takes(self):
        app = self.make_app()
        self.assertIsNone(app.take)
        self.assertIn("Pasta de gravações", self.log_text(app))
```

por isto:

```python
    def test_startup_without_takes(self):
        app = self.make_app()
        self.assertIsNone(app.take)
        self.assertIn("Pasta de gravações", self.log_text(app))

    def test_startup_skips_failed_take(self):
        # a tomada que falhou ao comecar (camera em uso, duplo clique) nao esconde a boa anterior
        write_audio_take(self.rec_dir, "2026-09-26_100000", "convertido")
        failed = Take(id="2026-09-26_110000", dir=os.path.join(self.rec_dir, "2026-09-26_110000"), modo="av",
                      status="falhou", mic="mic", erro="Câmera em uso por outro programa (Meet/Zoom/OBS?)")
        os.makedirs(failed.dir)
        failed.save()                                        # sem raw.mkv
        app = self.make_app()
        self.assertEqual("2026-09-26_100000", app.take.id)
        self.assertIn("Última tomada: 2026-09-26_100000 (convertido)", self.log_text(app))
```

- [ ] **Step 14: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_shell`
Expected: `Ran 33 tests` e `FAILED (failures=3, errors=1)`:
- `test_corrupted_estado_is_kept_before_first_save`: `FileNotFoundError: [Errno 2] No such file or directory: '…/estado.json.corrompido'`
- `test_estado_save_error_is_logged`: `AssertionError: 'Não foi possível salvar estado.json: Erro ao acessar o disco' not found in "… Não foi possível salvar estado.json: FileNotFoundError: [Errno 2] No such file or directory: '…/nao/existe/.estado.json.….tmp'\n\n"`
- `test_estado_written_outside_is_not_undone`: `AssertionError: Tuples differ: (40, 'https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUv', 'silvio') != (0, '', 'silvio')`
- `test_startup_skips_failed_take`: `AssertionError: '2026-09-26_100000' != '2026-09-26_110000'`

- [ ] **Step 15: `update_estado` com `merge_estado` e abertura na última tomada utilizável**

Em `studio/gui.py`, substituir isto:

```python
from studio.config import (ESTADO_PATH, LOGS_DIR, MODELOS, REC_DIR, STUDIO_LOG, Modelo, find_latest_checkpoint,
                           get_modelo, load_estado, save_estado)
```

por isto:

```python
from studio import procs
from studio.config import (ESTADO_PATH, LOGS_DIR, MODELOS, REC_DIR, STUDIO_LOG, Modelo, find_latest_checkpoint,
                           get_modelo, load_estado, merge_estado)
```

E em `studio/gui.py`, substituir isto:

```python
    def update_estado(self, **changes) -> None:
        self.estado.update(changes)
        try:
            save_estado(self.estado, self.estado_path)
        except OSError as e:
            self.log(f"Não foi possível salvar estado.json: {error_message(e)}")
```

por isto:

```python
    def update_estado(self, **changes) -> None:
        # rele o arquivo e grava so estas chaves: o que outro programa gravou com o app aberto (calibrar_av.py
        # --salvar, enviar_drive.py --pasta) nao volta atras; o corrompido vira estado.json.corrompido
        try:
            self.estado, aviso = merge_estado(changes, self.estado_path)
        except OSError as e:
            self.estado.update(changes)
            self.log(f"Não foi possível salvar estado.json: {procs.os_error_message(e)}")
            return
        if aviso:
            self.log(f"AVISO: {aviso}")
```

E em `studio/gui.py`, substituir isto:

```python
            for msg in recover_takes(self.rec_dir):
                self.log(msg)
            take = latest_take(self.rec_dir)
```

por isto:

```python
            for msg in recover_takes(self.rec_dir):
                self.log(msg)
            # a que falhou ao comecar (camera em uso, duplo clique) nao esconde a ultima boa
            take = latest_take(self.rec_dir, usable_only=True)
```

O `import` fica numa linha própria, antes do `studio.config` (e não no meio dos outros `from studio.… import`): as
trocas das Tasks 10 e 11 procuram as linhas `from studio.events …` e `from studio.rvc_client …` juntas.

- [ ] **Step 16: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_shell`
Expected: `Ran 33 tests` e `OK`

Run: `./run_tests.sh`
Expected: `Ran 382 tests` e `OK (skipped=6)` (as Tasks 1–9 com as correções)

- [ ] **Step 17: Commit**

```bash
git add studio/gui.py tests/test_gui_shell.py
git commit -m "fix(gui): abre na ultima tomada utilizavel e grava o estado.json com merge_estado

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

**Notas para o executor**

- **Lixo do Tk coletado fora da thread principal aborta o processo.** Na primeira rodada, a suíte da GUI morreu com
  `Tcl_AsyncDelete: async handler deleted by the wrong thread`, logo depois de
  `Exception ignored in: <function Variable.__del__ …> RuntimeError: main thread is not in main loop`. O `App` de um
  teste anterior (há um ciclo `App` ↔ `root` pelos métodos ligados) ficou para o GC automático, e o GC rodou numa
  thread de trabalho. Por isso o `setUp` registra `self.addCleanup(gc.collect)` **primeiro**: ele roda por último,
  na thread principal, depois do `shutdown()`. No app de verdade vale a mesma regra: `StringVar`, `PhotoImage` e
  afins ficam presos em atributos do App ou dos painéis e nunca viram lixo no meio da execução.
- **Ordem no `__init__`:** os painéis das Tasks 10 e 11 devem ser montados **antes** de `self._startup(...)`, no ponto
  marcado pelo comentário. Assim eles assinam `take_changed` a tempo de receber a tomada inicial (o evento é
  entregue no primeiro pump, 50 ms depois). A recuperação (`recover_takes`) roda de propósito na thread principal,
  porque só ela altera `take.json` (spec 3.2). Isso só demora quando há tomada interrompida para remontar.
- **`busy` × `current`:** um job recém-submetido fica alguns instantes na fila com `current is None`. O teste de
  fechar com job rodando falhou uma vez por isso (a mensagem saiu "Trabalho em andamento (gpu)"). Ele agora espera o
  job começar, e o texto de reserva usa `RUNNER_LABELS`.
- **Fechar:** o App já pede confirmação sozinho quando alguma fila está ocupada (texto de `JOB_LABELS`, ou de
  `RUNNER_LABELS` como reserva). `register_closer` serve para motivos específicos dos painéis (ex.: "Gravando").
  `add_shutdown_hook` roda com a janela ainda visível (ex.: parar a gravação com `q` e mostrar
  "Finalizando gravação…"). Depois dos ganchos, a janela some (`withdraw`). O `rvc.close()` pode levar até 13 s se
  houver conversão rodando: são 10 s de espera, depois SIGTERM e mais 3 s.
- **Threads de trabalho nunca tocam no Tk.** `app.log()` chamado de outra thread vira evento `"log"`, e também dá para
  postar direto `bus.post("log", msg=...)`. O traceback de todo `job_fail` vai sozinho para `studio.log`, então o painel
  só loga a linha curta (`ev.data["message"]`).
- **`ensure_rvc()` recusa outra thread** com RuntimeError: o PDEATHSIG do worker vale enquanto viver a thread que fez o
  spawn. O padrão é chamar `rvc = app.ensure_rvc()` no clique e depois `app.gpu_jobs.submit(nome, rvc.convert, ...)`.
- **`estado.json` compartilhado (Steps 13–17).** O `calibrar_av.py --salvar` (Task 12) e o `enviar_drive.py --pasta`
  (Task 8) gravam o arquivo com o app aberto. Por isso o `update_estado` nunca regrava o dicionário da memória: o
  `merge_estado` relê o arquivo, troca só as chaves pedidas e devolve o resultado, que vira o `self.estado`. Quem precisa
  de um valor que outro programa pode ter mudado lê o arquivo na hora (o render da Task 11 faz isso com o
  `av_offset_ms`). Um arquivo corrompido na abertura só gera o aviso; no 1º clique que grava, ele vira
  `estado.json.corrompido` (o link do Drive continua lá para recuperar à mão).
- **Abertura:** "utilizável" é status "gravado", "convertido" ou "renderizado" com o `raw.mkv`/`raw.wav` no disco. A
  recuperação (`recover_takes`) roda antes, então uma tomada "gravando" que se recupera volta a contar.
- `event_generate("<<ComboboxSelected>>")` funciona com a janela escondida e dispara o handler na hora. O teste com o
  `RvcClient` de verdade (`STUDIO_RVC_FAKE=1`) leva ~0,5 s. Conferi com `find ~/orochi-ia-homenagem -newer marca` que
  ele não grava nada no Applio.

---

### Task 10: Painel de captura (câmera com preview e marca d'água, microfone, Gravar/Parar)

**Files:**
- Create: `studio/gui_capture.py`
- Modify: `studio/gui.py` (importa o painel, junta `CAPTURE_JOB_LABELS` ao `JOB_LABELS`, aceita `capture_factory` e
  `hardware` no `App` e monta o `CapturePanel` na coluna esquerda)
- Test: `tests/test_gui_capture.py`

**Interfaces:**
- Consumes:
  - Task 1: `studio.config.MAX_TAKE_S = 300`, `MIN_FREE_BYTES = 2 * 1024**3`, `Modelo`, `get_modelo(key: str) -> Modelo`
    (testes); `studio.procs.FFMPEG_EXIT_MSGS` (254 = "Câmera não encontrada"); `studio.takes.Take` (`id`, `dir`,
    `modo`, `status`, `mic`, `camera`, `video`, `audio_fit`, `erro`, `raw_path`, `audio_path`, `path(name)`, `save()`,
    `Take.load(dir)`), `new_take(modo: str, mic: str, camera: str = "", rec_dir: str = REC_DIR, now=None) -> Take`,
    `list_takes(rec_dir: str = REC_DIR) -> list[Take]` (testes); `tests.helpers.make_synthetic_take(path, duration_s=...,
    flash_frame=..., size=...) -> str`.
  - Task 2: `studio.devices.Source(index: int, name: str)`, `list_mics() -> list[Source]`,
    `list_cameras(by_id_dir: str = "/dev/v4l/by-id") -> list[str]`, `free_bytes(path: str) -> int`;
    `studio.audio.volumedetect(path: str) -> tuple[float | None, float | None]` (`(mean_db, max_db)`).
  - Task 3: `studio.watermark.make_watermark(w: int, h: int, modelo: Modelo) -> PIL.Image.Image` (RGBA do tamanho exato).
  - Task 4: `studio.timeline.extract_aligned_audio(raw_mkv: str, out_wav: str) -> tuple[VideoInfo, AudioFit]`
    (dataclasses; `VideoInfo.fps_medido`, `AudioFit.gaps`); `timeline.GAP_S = 0.030` (Step 21).
  - Task 6: `studio.capture.PREVIEW_W, PREVIEW_H = 480, 270`, `ACCEPTED_RC`, `MIN_FPS = 12.0`, `MSG_MIC`,
    `build_av_cmd(mic: str, cam: str, out_mkv: str)`, `build_audio_cmd(mic: str, out_wav: str)`, `build_preview_cmd(cam: str)`,
    `CaptureProcess(argv: list[str], log_path: str, with_preview: bool)` com `start()` (thread principal),
    `early_failure() -> str | None`, `latest_frame() -> tuple[int, bytes | None]`, `fps_measured() -> float | None`,
    `request_stop()`, `wait_stopped(t_q=5, t_close=3, t_term=3) -> int`, `pid`, `started_monotonic`,
    `last_frame_monotonic`; `verify_capture(path: str, need_video: bool) -> str | None`;
    `check_mic(pid: int, expected_index: int) -> str | None`; `Watchdog(frame_timeout=2.0, first_frame_timeout=5.0, ...)`
    com `check_frames(now, last_frame, started) -> str | None` e `check_fps(fps, now, started) -> str | None`.
    Da correção da Task 6 (Steps 12–21): `mic_status(pid: int, expected_index: int) -> str` com `MIC_OK = "ok"`,
    `MIC_SWAPPED = "trocado"` e `MIC_UNKNOWN = "desconhecido"` (pactl falhou ou estourou o timeout),
    `MSG_MIC_UNKNOWN = "Não deu para conferir o microfone (o pactl não respondeu)"` (Step 16) e
    `MSG_SHORT = "Gravação curta demais"` (Step 11; o `verify_capture` também a devolve para A/V com menos de 3
    pacotes de vídeo).
  - Task 9: `studio.events.Event(kind, data)`, `error_message(exc) -> str`; `studio.gui.App` com `root`, `left`, `estado`,
    `rec_dir`, `io_jobs` (`submit(job, fn, *args)`, `busy`), `bus.post(kind, **data)`, `closing`, `closed`,
    `add_section(title: str, parent=None) -> ttk.LabelFrame`, `log(msg)`, `report_error(exc, where)`, `on(kind, fn)`,
    `update_estado(**changes)`, `modelo() -> Modelo`, `set_take(take)`, `register_closer(fn)`, `add_shutdown_hook(fn)`;
    eventos `"model_changed"` `{modelo}`, `"job_ok"` `{job, runner, result}`, `"job_fail"` `{job, runner, message, traceback}`;
    `JOB_LABELS`. Os testes usam também `close_reasons()`, `on_close()`, `shutdown()`, `model_var`, `model_box`, `log_box`.
- Produces:
  - `studio/gui_capture.py` (importa tkinter e `PIL.ImageTk`):
    - jobs na fila io: `JOB_PREVIEW_STOP = "capture_preview_stop"`, `JOB_STOP = "capture_stop"`,
      `JOB_FINISH = "capture_finish"`, `JOB_MIC = "capture_mic_check"`, e `CAPTURE_JOB_LABELS` (job → texto PT ao fechar).
    - estados do painel: `IDLE, STARTING, RECORDING, STOPPING, FINISHING = "idle", "starting", "recording", "stopping",
      "finishing"`; tempos `TICK_MS = 66`, `WATCH_MS = 500`, `MIC_CHECK_S = 2.0`, `PREVIEW_IDLE_S = 300`,
      `SHUTDOWN_WAIT_S = 5.0`, `MIN_REC_S = 1.0` (Step 11: o Parar só libera 1 s depois do início e, no A/V, depois do
      1º frame; duração abaixo disso é recusada com `capture.MSG_SHORT`); `MIC_SWAPS_TO_STOP = 2` (Step 16);
      `MUTE_DB = -45.0`; cores `COLOR_REC_ON`, `COLOR_REC_OFF`; `MSG_BAD_DURATION`, `MSG_LIMIT`.
    - `@dataclass(frozen=True) class Hardware` com `list_cameras`, `list_mics`, `free_bytes`, `check_mic` e (Step 16)
      `mic_status` (padrão: as funções de `devices`/`capture`). O painel consulta só o `mic_status`; o `check_mic`
      continua aceito porque a Task 11 monta o `Hardware` com ele.
    - `camera_label(path: str) -> str`; `parse_duration(text: str) -> float | None` (vazio = `None`; `ValueError(MSG_BAD_DURATION)`);
      `make_overlay(modelo: Modelo) -> Image` (RGBA 480×270: marca d'água + guias 9:16); `compose(frame: bytes | None,
      overlay) -> Image`; `mic_check_job(check, pid, expected_index) -> tuple[int, str | None]`;
      `finish_capture(raw: str, need_video: bool, audio_out: str) -> dict` (`{"erro": msg}` ou
      `{"erro": None, "video": dict, "audio_fit": dict, "volume": (mean_db, max_db)}`);
      `gaps_warning(take: Take) -> str | None` (Step 21: `"AVISO: o áudio da tomada <id> teve <n> buraco(s) acima de
      30 ms; o alinhamento usou o modo assíncrono — confira a sincronia"` quando `take.audio_fit["gaps"] > 0`; a Task 11
      reusa).
    - `class CapturePanel(app, parent)`: atributos `frame`, `state`, `preview`, `rec`, `take` (a tomada em gravação ou
      conferência), `watchdog`, e os widgets `camera_var`, `camera_box`, `camera_on`, `cam_check`, `canvas`, `photo`,
      `rec_label`, `fps_label`, `time_label`, `mic_var`, `mic_box`, `video_on`, `video_check`, `duration_var`,
      `duration_entry`, `btn_record`, `status_label`. Métodos `refresh_devices()`, `camera_path() -> str`, `on_record()`
      (Gravar/Parar), `stop_recording(reason: str | None = None, failure: str | None = None)`.
    - Evento novo `"recording"` `{active: bool, take: Take | None}`: `active=True` quando o gravador inicia (tomada
      "gravando"); `active=False` quando o painel volta a parado (tomada já "gravado" ou "falhou").
    - Quando a tomada vira "gravado" (na thread principal): `take.video` e `take.audio_fit` preenchidos e `audio.wav`
      alinhado no disco (A/V), `take.save()` e `app.set_take(take)` → `"take_changed"`; com buraco no áudio, o aviso do
      `gaps_warning` vai para o log. Tomada que falha fica "falhou" com `erro` e **não** troca `app.take`.
    - Microfone (Step 16): `"desconhecido"` só gera `AVISO: <MSG_MIC_UNKNOWN>` (uma vez por sequência) e a gravação
      segue; `"trocado"` para a gravação na 2ª checagem seguida; `"ok"` zera a contagem.
  - `studio/gui.py`: `App(..., capture_factory=None, hardware=None)` com os atributos `capture_factory`, `hardware` e
    `capture_panel`; `JOB_LABELS` passa a incluir os 4 jobs do painel.

Todos os comandos rodam da raiz do repositório (`~/orochi-ia-homenagem`). Os testes do painel precisam de um display X11
(`DISPLAY`), deixam a janela escondida (`root.withdraw()`) e **nunca** abrem a câmera nem o microfone: um
`CaptureProcess` falso entrega frames sintéticos e, no `wait_stopped`, copia uma tomada sintética (ou um WAV mudo) para o
`raw.mkv`/`raw.wav`, de modo que `verify_capture`, `extract_aligned_audio` e `volumedetect` rodam de verdade.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_gui_capture.py`:

```python
import gc
import itertools
import json
import os
import shutil
import tempfile
import threading
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np
import soundfile as sf
from PIL import ImageTk

from studio import capture, gui, gui_capture
from studio.config import get_modelo
from studio.devices import Source
from studio.takes import Take, list_takes
from tests import helpers

HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
MIC = "alsa_input.usb-Generalplus_Usb_Audio_Device-00.mono-fallback"
OTHER_MIC = "alsa_input.pci-0000_00_1f.3.analog-stereo"
PIXELS = capture.PREVIEW_W * capture.PREVIEW_H
RED = bytes((200, 40, 40)) * PIXELS
BLUE = bytes((30, 60, 220)) * PIXELS
_pids = itertools.count(40000)


class FakeCapture:
    """CaptureProcess falso: frames sinteticos sob demanda; no wait_stopped 'grava' o raw copiando uma midia pronta."""

    def __init__(self, argv, log_path, with_preview, raw_source=None, fail=None):
        self.argv = list(argv)
        self.log_path = log_path
        self.with_preview = with_preview
        self.raw_source = raw_source
        self.fail = fail
        self.pid = next(_pids)
        self.start_thread = None
        self.started_monotonic = None
        self.last_frame_monotonic = None
        self.fps = None
        self.stop_requested = False
        self.stopped_at = None
        self.wait_threads: list[str] = []
        self.wait_args: list[tuple] = []
        self._seq = 0
        self._frame = None

    def start(self):
        self.start_thread = threading.current_thread().name
        self.started_monotonic = time.monotonic()

    @property
    def running(self):
        return self.started_monotonic is not None and not self.stop_requested and self.fail is None

    def early_failure(self):
        return None if self.stop_requested else self.fail

    def push(self, frame: bytes = RED):
        self._seq += 1
        self._frame = frame
        self.last_frame_monotonic = time.monotonic()

    def latest_frame(self):
        return self._seq, self._frame

    def fps_measured(self):
        return self.fps

    def request_stop(self):
        self.stop_requested = True

    def wait_stopped(self, t_q=5, t_close=3, t_term=3):
        self.wait_threads.append(threading.current_thread().name)
        self.wait_args.append((t_q, t_close, t_term))
        self.stop_requested = True
        time.sleep(0.05)                        # o ffmpeg de verdade leva ~0,2 s para sair com o q
        out = next((a for a in self.argv if a.endswith(("raw.mkv", "raw.wav"))), None)
        if out and self.raw_source and self.fail is None and not os.path.exists(out):
            shutil.copyfile(self.raw_source, out)
        self.stopped_at = time.monotonic()
        return 0


def pump_until(app, cond, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.root.update()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def pump_for(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.root.update()
        time.sleep(0.01)


def pixel(panel, xy) -> tuple:
    return ImageTk.getimage(panel.photo).getpixel(xy)[:3]


class HelpersTest(unittest.TestCase):
    def test_parse_duration(self):
        self.assertIsNone(gui_capture.parse_duration("  "))
        self.assertEqual(2.5, gui_capture.parse_duration("2,5"))
        self.assertEqual(30.0, gui_capture.parse_duration(" 30 "))
        for bad in ("abc", "0", "-3", "nan", "inf"):
            with self.subTest(bad):
                with self.assertRaises(ValueError) as cm:
                    gui_capture.parse_duration(bad)
                self.assertEqual(gui_capture.MSG_BAD_DURATION, str(cm.exception))

    def test_camera_label(self):
        self.assertEqual("A4tech_FHD_720P_PC_Camera",
                         gui_capture.camera_label("/dev/v4l/by-id/usb-A4tech_FHD_720P_PC_Camera-video-index0"))

    def test_overlay_and_compose(self):
        ov = gui_capture.make_overlay(get_modelo("silvio"))
        self.assertEqual(("RGBA", (480, 270)), (ov.mode, ov.size))
        self.assertEqual((17, 17, 17), gui_capture.compose(None, ov).getpixel((10, 10))[:3])
        img = gui_capture.compose(RED, ov)
        self.assertEqual((480, 270), img.size)
        self.assertEqual((200, 40, 40), img.getpixel((10, 10))[:3])
        other = gui_capture.compose(RED, gui_capture.make_overlay(get_modelo("orochi")))
        band = (0, 200, 480, 270)
        self.assertNotEqual(img.crop(band).tobytes(), other.crop(band).tobytes())   # aviso muda por modelo

    def test_finish_capture_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            res = gui_capture.finish_capture(os.path.join(d, "raw.mkv"), True, os.path.join(d, "audio.wav"))
        self.assertEqual({"erro": "Gravação não encontrada: raw.mkv"}, res)


@unittest.skipUnless(HAS_DISPLAY, "sem display")
class CapturePanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = tempfile.TemporaryDirectory()
        cls.av_raw = helpers.make_synthetic_take(os.path.join(cls.media.name, "av.mkv"), duration_s=2.0,
                                                 flash_frame=30, size="320x240")
        cls.silent_wav = os.path.join(cls.media.name, "mudo.wav")
        sf.write(cls.silent_wav, np.zeros(48000, dtype=np.int16), 48000, subtype="PCM_16")

    @classmethod
    def tearDownClass(cls):
        cls.media.cleanup()

    def setUp(self):
        # roda por ultimo: o lixo do Tk (App, PhotoImage, StringVar) morre na thread principal
        self.addCleanup(gc.collect)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rec_dir = os.path.join(self.tmp.name, "recordings")
        self.estado_path = os.path.join(self.tmp.name, "estado.json")
        self.log_path = os.path.join(self.tmp.name, "studio.log")
        by_id = os.path.join(self.tmp.name, "by-id")
        os.makedirs(by_id)
        self.cam = os.path.join(by_id, "usb-A4tech_FHD_720P_PC_Camera-video-index0")
        open(self.cam, "w").close()
        self.cams = [self.cam]
        self.mics = [Source(54, MIC), Source(56, OTHER_MIC)]
        self.free = 50 * 1024**3
        self.mic_result = None
        self.mic_checks: list[tuple] = []
        self.caps: list[FakeCapture] = []
        self.fail_next = None
        self.confirm_calls: list[tuple[str, str]] = []
        self.confirm_answer = False
        patcher = mock.patch.multiple(gui_capture, TICK_MS=20, WATCH_MS=40, MIC_CHECK_S=0.2)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ---------- fabricas falsas ----------

    def factory(self, argv, log_path, with_preview):
        raw = self.silent_wav if "wav" in argv[-1] else self.av_raw
        cap = FakeCapture(argv, log_path, with_preview, raw_source=raw, fail=self.fail_next)
        self.fail_next = None
        self.caps.append(cap)
        return cap

    def check_mic(self, pid, expected_index):
        self.mic_checks.append((threading.current_thread().name, pid, expected_index))
        return self.mic_result

    def confirm(self, title, message):
        self.confirm_calls.append((title, message))
        return self.confirm_answer

    def make_app(self, estado: dict | None = None) -> gui.App:
        if estado is not None:
            with open(self.estado_path, "w", encoding="utf-8") as f:
                json.dump(estado, f)
        root = tk.Tk()
        root.withdraw()
        hw = gui_capture.Hardware(list_cameras=lambda: list(self.cams), list_mics=lambda: list(self.mics),
                                  free_bytes=lambda path: self.free, check_mic=self.check_mic)
        app = gui.App(root, rec_dir=self.rec_dir, estado_path=self.estado_path, log_path=self.log_path,
                      logs_dir=os.path.join(self.tmp.name, "logs"), ask_confirm=self.confirm,
                      capture_factory=self.factory, hardware=hw)
        self.addCleanup(self.close_app, app)
        return app

    def close_app(self, app):
        if not app.closed:
            pump_until(app, lambda: not app.io_jobs.busy)
            app.shutdown()
        for cap in self.caps:
            self.assertTrue(cap.stop_requested, f"captura esquecida ligada: {cap.argv[:4]}")

    def log_text(self, app) -> str:
        return app.log_box.get("1.0", "end")

    def file_text(self) -> str:
        with open(self.log_path, encoding="utf-8") as f:
            return f.read()

    def start_recording(self, app) -> tuple[Take, FakeCapture]:
        p = app.capture_panel
        n = len(self.caps)
        p.btn_record.invoke()
        self.assertTrue(pump_until(app, lambda: p.state == "recording"), self.log_text(app))
        self.assertEqual(n + 1, len(self.caps))
        return p.take, self.caps[-1]

    def wait_idle(self, app, timeout: float = 20.0) -> None:
        p = app.capture_panel
        self.assertTrue(pump_until(app, lambda: p.state == "idle" and not app.io_jobs.busy, timeout),
                        self.log_text(app))

    # ---------- montagem ----------

    def test_layout_devices_and_estado(self):
        app = self.make_app({"mic": OTHER_MIC, "gravar_video": True})
        p = app.capture_panel
        self.assertIs(app.left, p.frame.master)
        self.assertEqual("Gravação", p.frame.cget("text"))
        self.assertEqual(("480", "270"), (str(p.canvas.cget("width")), str(p.canvas.cget("height"))))
        self.assertEqual([MIC, OTHER_MIC], list(p.mic_box.cget("values")))
        self.assertEqual(OTHER_MIC, p.mic_var.get())
        self.assertEqual(["A4tech_FHD_720P_PC_Camera"], list(p.camera_box.cget("values")))
        self.assertEqual(self.cam, p.camera_path())
        self.assertTrue(p.video_on.get())
        self.assertFalse(p.camera_on.get())          # a camera nunca liga sozinha
        self.assertEqual("● Gravar", p.btn_record.cget("text"))
        self.assertEqual("idle", p.state)
        self.assertEqual([], self.caps)
        self.assertIn("capture_stop", gui.JOB_LABELS)
        p.mic_var.set(MIC)
        p.mic_box.event_generate("<<ComboboxSelected>>")
        with open(self.estado_path, encoding="utf-8") as f:
            self.assertEqual(MIC, json.load(f)["mic"])

    def test_saved_mic_missing_falls_back_with_warning(self):
        app = self.make_app({"mic": "alsa_input.sumiu"})
        self.assertEqual(MIC, app.capture_panel.mic_var.get())
        self.assertIn("alsa_input.sumiu", self.log_text(app))

    # ---------- preview ----------

    def test_preview_paints_canvas_with_watermark_guides_and_fps(self):
        app = self.make_app()
        p = app.capture_panel
        p.cam_check.invoke()
        self.assertTrue(p.camera_on.get())
        (cap,) = self.caps
        self.assertEqual(capture.build_preview_cmd(self.cam), cap.argv)
        self.assertTrue(cap.with_preview)
        self.assertEqual(threading.main_thread().name, cap.start_thread)
        cap.fps = 14.6
        cap.push(RED)
        self.assertTrue(pump_until(app, lambda: pixel(p, (10, 10)) == (200, 40, 40)))
        r, g, b = pixel(p, (10, 265))                 # faixa escura da marca d'agua
        self.assertLess(r, 120)
        col = round(capture.PREVIEW_H * 9 / 16)
        x0 = (capture.PREVIEW_W - col) // 2
        self.assertNotEqual((200, 40, 40), pixel(p, (x0, 60)))       # guia 9:16
        self.assertEqual((200, 40, 40), pixel(p, (x0 + 5, 60)))
        self.assertIn("14,6", p.fps_label.cget("text"))
        self.assertEqual(gui_capture.COLOR_REC_OFF, str(p.rec_label.cget("foreground")))   # preview nao e REC

        # fps baixo (depois da carencia) vira aviso
        cap.started_monotonic -= 10
        cap.fps = 8.0
        cap.push(BLUE)
        self.assertTrue(pump_until(app, lambda: "abaixo de 12" in p.fps_label.cget("text")))
        self.assertTrue(pump_until(app, lambda: pixel(p, (10, 10)) == (30, 60, 220)))

        # trocar o modelo refaz a marca no preview
        band = (0, 200, 480, 270)
        before = ImageTk.getimage(p.photo).crop(band).tobytes()
        app.model_var.set("Silvio Santos")
        app.model_box.event_generate("<<ComboboxSelected>>")
        self.assertTrue(pump_until(app, lambda: ImageTk.getimage(p.photo).crop(band).tobytes() != before))

    def test_preview_stops_on_uncheck_unmap_and_idle(self):
        app = self.make_app()
        p = app.capture_panel
        p.cam_check.invoke()
        p.cam_check.invoke()                          # desmarcou
        first = self.caps[0]
        self.assertTrue(first.stop_requested)
        self.assertIsNone(p.preview)
        self.assertTrue(pump_until(app, lambda: first.wait_threads == ["job-io"]))

        p.cam_check.invoke()
        self.assertTrue(pump_until(app, lambda: len(self.caps) == 2))
        p._on_unmap(SimpleNamespace(widget=p.canvas))  # Unmap de um filho nao conta
        self.assertFalse(self.caps[1].stop_requested)
        p._on_unmap(SimpleNamespace(widget=app.root))  # janela minimizada
        self.assertTrue(self.caps[1].stop_requested)
        p._on_map(SimpleNamespace(widget=app.root))    # volta: religa so depois de a anterior soltar a camera
        self.assertTrue(pump_until(app, lambda: len(self.caps) == 3))
        self.assertEqual(["job-io"], self.caps[1].wait_threads)

        with mock.patch.object(gui_capture, "PREVIEW_IDLE_S", 0.2):
            self.assertTrue(pump_until(app, lambda: self.caps[2].stop_requested))
        self.assertFalse(p.camera_on.get())
        self.assertIn("sem uso", self.log_text(app))
        self.assertEqual(3, len(self.caps))

    def test_video_unchecked_means_no_preview(self):
        app = self.make_app({"gravar_video": False})
        p = app.capture_panel
        self.assertFalse(p.video_on.get())
        p.cam_check.invoke()
        app.root.update()
        self.assertEqual([], self.caps)
        p.video_check.invoke()                        # marcou "Gravar vídeo": agora liga
        self.assertEqual(1, len(self.caps))
        with open(self.estado_path, encoding="utf-8") as f:
            self.assertTrue(json.load(f)["gravar_video"])

    def test_preview_early_failure_turns_camera_off(self):
        app = self.make_app()
        p = app.capture_panel
        self.fail_next = "Câmera em uso por outro programa (Meet/Zoom/OBS?)"
        p.cam_check.invoke()
        self.assertTrue(pump_until(app, lambda: not p.camera_on.get()))
        self.assertIn("Câmera em uso por outro programa", self.log_text(app))
        self.assertTrue(pump_until(app, lambda: self.caps[0].wait_threads == ["job-io"]))

    # ---------- gravar ----------

    def test_record_av_creates_take_and_changes_button(self):
        app = self.make_app()
        p = app.capture_panel
        got = []
        app.on("recording", got.append)
        p.cam_check.invoke()
        preview = self.caps[0]
        p.btn_record.invoke()
        (take,) = list_takes(self.rec_dir)
        self.assertEqual(("gravando", "av", MIC, self.cam), (take.status, take.modo, take.mic, take.camera))
        self.assertTrue(pump_until(app, lambda: p.state == "recording"))
        rec = self.caps[1]
        self.assertEqual(["job-io"], preview.wait_threads)
        self.assertLess(preview.stopped_at, rec.started_monotonic)  # o preview soltou a camera antes
        self.assertEqual(capture.build_av_cmd(MIC, self.cam, take.raw_path), rec.argv)
        self.assertEqual(take.path("ffmpeg.log"), rec.log_path)
        self.assertTrue(rec.with_preview)
        self.assertEqual(threading.main_thread().name, rec.start_thread)
        self.assertEqual("■ Parar", p.btn_record.cget("text"))
        self.assertEqual("disabled", str(p.mic_box.cget("state")))
        pump_for(app, 0.15)
        self.assertEqual(gui_capture.COLOR_REC_OFF, str(p.rec_label.cget("foreground")))   # sem frame ainda
        rec.push(RED)
        self.assertTrue(pump_until(app, lambda: str(p.rec_label.cget("foreground")) == gui_capture.COLOR_REC_ON))
        self.assertTrue(pump_until(app, lambda: pixel(p, (10, 10)) == (200, 40, 40)))
        with open(self.estado_path, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual((MIC, self.cam, True), (saved["mic"], saved["camera"], saved["gravar_video"]))
        self.assertTrue(any("Gravação em andamento" in r for r in app.close_reasons()))
        self.assertTrue(pump_until(app, lambda: got))
        self.assertTrue(got[0].data["active"])
        self.assertEqual(take.id, got[0].data["take"].id)
        p.btn_record.invoke()
        self.wait_idle(app)

    def test_stop_takes_take_to_gravado(self):
        app = self.make_app()
        p = app.capture_panel
        changed, recording = [], []
        app.on("take_changed", changed.append)
        app.on("recording", recording.append)
        p.cam_check.invoke()
        take, rec = self.start_recording(app)
        rec.push(RED)
        p.btn_record.invoke()                         # Parar
        self.assertEqual("stopping", p.state)
        self.assertTrue(rec.stop_requested)
        self.wait_idle(app)
        self.assertEqual(["job-io"], rec.wait_threads)
        self.assertIs(take, app.take)
        self.assertEqual("gravado", app.take.status)
        saved = Take.load(take.dir)
        self.assertEqual("gravado", saved.status)
        self.assertEqual((320, 240), (saved.video["w"], saved.video["h"]))
        self.assertGreater(saved.video["n_frames"], 50)
        self.assertIn("taxa_real", saved.audio_fit)
        self.assertTrue(os.path.exists(take.audio_path))
        self.assertTrue(pump_until(app, lambda: changed))
        self.assertIs(take, changed[-1].data["take"])
        self.assertEqual([True, False], [e.data["active"] for e in recording])
        self.assertIn(f"Gravação concluída: {take.id}", self.log_text(app))
        self.assertIn("Volume da gravação", self.log_text(app))
        self.assertNotIn("Microfone mudo?", self.log_text(app))
        self.assertEqual("● Gravar", p.btn_record.cget("text"))
        self.assertEqual("normal", str(p.btn_record.cget("state")))
        self.assertEqual("readonly", str(p.mic_box.cget("state")))
        # camera continua marcada: o preview volta
        self.assertTrue(pump_until(app, lambda: len(self.caps) == 3 and p.preview is self.caps[2]))

    def test_audio_only(self):
        app = self.make_app({"gravar_video": False})
        p = app.capture_panel
        p.cam_check.invoke()                          # sem "Gravar vídeo" a camera nao liga
        take, rec = self.start_recording(app)
        self.assertEqual(("audio", ""), (take.modo, take.camera))
        self.assertEqual(capture.build_audio_cmd(MIC, take.raw_path), rec.argv)
        self.assertTrue(take.raw_path.endswith("raw.wav"))
        self.assertFalse(rec.with_preview)
        self.assertTrue(pump_until(app, lambda: str(p.rec_label.cget("foreground")) == gui_capture.COLOR_REC_ON))
        p.btn_record.invoke()
        self.wait_idle(app)
        self.assertEqual("gravado", app.take.status)
        self.assertEqual({}, app.take.video)
        self.assertEqual(take.raw_path, app.take.audio_path)
        self.assertIn("Microfone mudo?", self.log_text(app))       # o raw falso e silencio
        self.assertEqual(1, len(self.caps))

    def test_checks_block_recording(self):
        app = self.make_app()
        p = app.capture_panel
        cases = [
            ("free", 1536 * 1024**2, "Pouco espaço em disco: 1,5 GB livres (mínimo 2 GB)"),
            ("mics", [Source(56, OTHER_MIC)], "Microfone não encontrado — escolha outro na lista"),
            ("cam", None, "Câmera não encontrada"),
            ("duration", "abc", "Duração inválida"),
        ]
        for what, value, msg in cases:
            with self.subTest(what):
                self.free, self.mics = 50 * 1024**3, [Source(54, MIC), Source(56, OTHER_MIC)]
                p.duration_var.set("")
                if what == "free":
                    self.free = value
                elif what == "mics":
                    self.mics = value
                elif what == "cam":
                    os.rename(self.cam, self.cam + ".x")
                    self.addCleanup(os.rename, self.cam + ".x", self.cam)
                else:
                    p.duration_var.set(value)
                p.btn_record.invoke()
                self.assertIn(msg, self.log_text(app))
                self.assertEqual("idle", p.state)
                self.assertEqual([], list_takes(self.rec_dir))
                self.assertEqual([], self.caps)

    def test_early_failure_shows_message_and_goes_back_to_idle(self):
        app = self.make_app()
        p = app.capture_panel
        self.fail_next = "Câmera em uso por outro programa (Meet/Zoom/OBS?)"
        p.btn_record.invoke()
        self.assertTrue(pump_until(app, lambda: len(self.caps) == 1))
        self.wait_idle(app)
        rec = self.caps[0]
        self.assertIn("Câmera em uso por outro programa (Meet/Zoom/OBS?)", self.log_text(app))
        self.assertEqual(["job-io"], rec.wait_threads)                # colhido mesmo depois da falha
        (take,) = list_takes(self.rec_dir)
        self.assertEqual("falhou", take.status)
        self.assertEqual("Câmera em uso por outro programa (Meet/Zoom/OBS?)", take.erro)
        self.assertIsNone(app.take)
        self.assertEqual("● Gravar", p.btn_record.cget("text"))
        self.assertEqual("normal", str(p.btn_record.cget("state")))

    def test_watchdog_stops_when_camera_stalls(self):
        app = self.make_app()
        p = app.capture_panel
        p.watchdog = capture.Watchdog(frame_timeout=0.3)
        take, rec = self.start_recording(app)
        rec.push(RED)
        self.wait_idle(app)
        self.assertIn("A câmera parou de enviar imagem", self.log_text(app))
        self.assertEqual(["job-io"], rec.wait_threads)
        self.assertEqual("gravado", Take.load(take.dir).status)       # parada graciosa: o que gravou fica

    def test_mic_switched_stops_recording(self):
        app = self.make_app()
        p = app.capture_panel
        self.mic_result = capture.MSG_MIC
        take, rec = self.start_recording(app)
        rec.push(RED)
        self.wait_idle(app)
        self.assertEqual(("job-io", rec.pid, 54), self.mic_checks[0])
        self.assertIn("Microfone desconectado ou trocado", self.log_text(app))
        self.assertEqual("gravado", Take.load(take.dir).status)

    def test_duration_and_limit_stop_by_themselves(self):
        app = self.make_app({"gravar_video": False})
        p = app.capture_panel
        p.duration_var.set("0,3")
        self.start_recording(app)
        self.wait_idle(app)
        self.assertIn("Duração de 0,3 s atingida", self.log_text(app))
        p.duration_var.set("")
        with mock.patch.object(gui_capture, "MAX_TAKE_S", 0.3):
            self.start_recording(app)
            self.wait_idle(app)
        self.assertIn("Limite de 5 min atingido — gravação encerrada", self.log_text(app))
        self.assertEqual(["gravado", "gravado"], [t.status for t in list_takes(self.rec_dir)])

    # ---------- fechar ----------

    def test_close_while_recording_uses_closer_and_hook(self):
        app = self.make_app()
        p = app.capture_panel
        take, rec = self.start_recording(app)
        rec.push(RED)
        app.on_close()                                # respondeu "não"
        self.assertFalse(app.closed)
        self.assertIn("Gravação em andamento", self.confirm_calls[0][1])
        self.assertFalse(rec.stop_requested)
        self.confirm_answer = True
        app.on_close()
        self.assertTrue(app.closed)
        self.assertEqual([threading.main_thread().name], rec.wait_threads)
        self.assertEqual(5, rec.wait_args[0][0])
        self.assertIn("Finalizando gravação…", self.file_text())
        # a tomada fica "gravando": a abertura seguinte confere e recupera (recover_takes)
        self.assertEqual("gravando", Take.load(take.dir).status)

    def test_close_with_preview_only_stops_it_without_asking(self):
        app = self.make_app()
        app.capture_panel.cam_check.invoke()
        app.on_close()
        self.assertTrue(app.closed)
        self.assertEqual([], self.confirm_calls)
        self.assertEqual([threading.main_thread().name], self.caps[0].wait_threads)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_capture -v`
Expected: ERROR ao importar o módulo de teste, com
`ImportError: cannot import name 'gui_capture' from 'studio'`

- [ ] **Step 3: Implementar `studio/gui_capture.py`**

`studio/gui_capture.py`:

```python
"""Painel de captura (coluna esquerda): camera com preview e marca d'agua, microfone e Gravar/Parar.

So a thread principal mexe nos widgets, na Take e nos processos (spawn na thread principal); o que bloqueia
(parar o ffmpeg, conferir o arquivo, alinhar o audio, pactl) roda na fila io do App.
"""

import math
import os
import time
import tkinter as tk
from dataclasses import asdict, dataclass
from tkinter import ttk
from typing import Callable

from PIL import Image, ImageDraw, ImageTk

from studio import audio, capture, devices, timeline
from studio.capture import (ACCEPTED_RC, MIN_FPS, PREVIEW_H, PREVIEW_W, CaptureProcess, Watchdog, build_audio_cmd,
                            build_av_cmd, build_preview_cmd, verify_capture)
from studio.config import MAX_TAKE_S, MIN_FREE_BYTES, Modelo
from studio.events import Event, error_message
from studio.procs import FFMPEG_EXIT_MSGS
from studio.takes import Take, new_take
from studio.watermark import make_watermark

JOB_PREVIEW_STOP = "capture_preview_stop"
JOB_STOP = "capture_stop"
JOB_FINISH = "capture_finish"
JOB_MIC = "capture_mic_check"
# texto de cada job na confirmacao ao fechar (o gui.py junta com os seus)
CAPTURE_JOB_LABELS = {JOB_PREVIEW_STOP: "Desligando a câmera", JOB_STOP: "Finalizando a gravação",
                      JOB_FINISH: "Conferindo a gravação", JOB_MIC: "Conferindo o microfone"}

IDLE, STARTING, RECORDING, STOPPING, FINISHING = "idle", "starting", "recording", "stopping", "finishing"
TICK_MS = 66              # consulta do ultimo frame (spec 10)
WATCH_MS = 500            # falha rapida, watchdog de frames e agenda da checagem do mic
MIC_CHECK_S = 2.0         # a 1a checagem sai 2 s depois do inicio (o pactl leva ~1,2 s para ver o gravador)
PREVIEW_IDLE_S = 300      # preview sem uso por 5 min: desliga (Meet/Zoom nao ficam bloqueados)
REC_AUDIO_S = 0.5         # so-audio nao tem frame: o REC acende quando passa da falha rapida
SHUTDOWN_WAIT_S = 5.0
MUTE_DB = -45.0
PREVIEW_LOG = "preview.log"
TXT_RECORD, TXT_STOP = "● Gravar", "■ Parar"
COLOR_REC_ON, COLOR_REC_OFF, COLOR_WARN, COLOR_TEXT = "#d11", "#bbb", "#a80", "#333"
GUIDE_RGBA = (255, 255, 255, 70)
IDLE_RGBA = (17, 17, 17, 255)
MSG_NO_MIC = "Microfone não encontrado — escolha outro na lista"
MSG_NO_CAMERA = "Nenhuma câmera encontrada — desmarque \"Gravar vídeo\" para gravar só o áudio"
MSG_BAD_DURATION = "Duração inválida: use segundos (ex.: 30) ou deixe vazio"
MSG_LIMIT = f"Limite de {MAX_TAKE_S // 60} min atingido — gravação encerrada"


@dataclass(frozen=True)
class Hardware:
    # o que o painel consulta no sistema; os testes injetam falsos
    list_cameras: Callable[[], list[str]] = devices.list_cameras
    list_mics: Callable[[], list[devices.Source]] = devices.list_mics
    free_bytes: Callable[[str], int] = devices.free_bytes
    check_mic: Callable[[int, int], str | None] = capture.check_mic


def camera_label(path: str) -> str:
    # ".../usb-A4tech_FHD_720P_PC_Camera-video-index0" -> "A4tech_FHD_720P_PC_Camera"
    return os.path.basename(path).removesuffix("-video-index0").removeprefix("usb-")


def parse_duration(text: str) -> float | None:
    # vazio = ate clicar Parar; aceita virgula
    text = text.strip().replace(",", ".")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise ValueError(MSG_BAD_DURATION) from None
    if not (math.isfinite(value) and value > 0):
        raise ValueError(MSG_BAD_DURATION)
    return value


def _dec(x: float, fmt: str = ".1f") -> str:
    return format(x, fmt).replace(".", ",")


def make_overlay(modelo: Modelo) -> Image.Image:
    # marca d'agua do modelo em 480x270 por cima das guias tenues da coluna 9:16
    img = Image.new("RGBA", (PREVIEW_W, PREVIEW_H), (0, 0, 0, 0))
    col = round(PREVIEW_H * 9 / 16)
    x0 = (PREVIEW_W - col) // 2
    d = ImageDraw.Draw(img)
    for x in (x0, x0 + col - 1):
        d.line((x, 0, x, PREVIEW_H - 1), fill=GUIDE_RGBA)
    return Image.alpha_composite(img, make_watermark(PREVIEW_W, PREVIEW_H, modelo))


def compose(frame: bytes | None, overlay: Image.Image) -> Image.Image:
    if frame is None:
        base = Image.new("RGBA", (PREVIEW_W, PREVIEW_H), IDLE_RGBA)
    else:
        base = Image.frombuffer("RGB", (PREVIEW_W, PREVIEW_H), frame, "raw", "RGB", 0, 1).convert("RGBA")
    return Image.alpha_composite(base, overlay)


def mic_check_job(check, pid: int, expected_index: int) -> tuple[int, str | None]:
    # thread de trabalho: so consulta o pactl
    return pid, check(pid, expected_index)


def finish_capture(raw: str, need_video: bool, audio_out: str) -> dict:
    # thread de trabalho: confere o arquivo, alinha o audio (A/V) e mede o volume; nao toca na Take
    erro = verify_capture(raw, need_video)
    if erro:
        return {"erro": erro}
    out = {"erro": None, "video": {}, "audio_fit": {}}
    if need_video:
        vi, fit = timeline.extract_aligned_audio(raw, audio_out)
        out["video"], out["audio_fit"] = asdict(vi), asdict(fit)
    out["volume"] = audio.volumedetect(audio_out)
    return out


class CapturePanel:
    def __init__(self, app, parent):
        self.app = app
        self.root = app.root
        self.hw: Hardware = app.hardware or Hardware()
        self._factory = app.capture_factory or CaptureProcess
        self.watchdog = Watchdog()
        self.state = IDLE
        self.preview: CaptureProcess | None = None
        self.rec: CaptureProcess | None = None
        self.take: Take | None = None           # tomada sendo gravada ou conferida
        self._plan: dict = {}
        self._stops_pending = 0                 # previews parando na fila io (a camera ainda esta presa)
        self._hidden = False
        self._last_activity = time.monotonic()
        self._rec_started = 0.0
        self._rec_elapsed = 0.0
        self._next_mic_check = 0.0
        self._mic_pending = False
        self._failure: str | None = None
        self._fps_warned = False
        self._painted: tuple = (None, None)
        self._fps_shown: tuple = ()
        self._timers: dict[str, str] = {}
        self._cams: dict[str, str] = {}
        self._overlay = make_overlay(app.modelo())
        self._build(parent)
        self._init_devices()

        app.on("model_changed", self._on_model_changed)
        app.on("job_ok", self._on_job)
        app.on("job_fail", self._on_job)
        app.register_closer(self._close_reason)
        app.add_shutdown_hook(self._on_shutdown)
        self.root.bind("<Unmap>", self._on_unmap, add="+")
        self.root.bind("<Map>", self._on_map, add="+")
        self.root.bind_all("<ButtonPress>", self._touch, add="+")
        self.root.bind_all("<KeyPress>", self._touch, add="+")
        self._schedule("tick", TICK_MS, self._tick)
        self._schedule("watch", WATCH_MS, self._watch)

    # ---------- layout ----------

    def _build(self, parent) -> None:
        root = self.root
        f = self.frame = self.app.add_section("Gravação", parent)
        row = ttk.Frame(f)
        row.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(row, text="Câmera:").pack(side="left")
        self.camera_var = tk.StringVar(master=root)
        self.camera_box = ttk.Combobox(row, textvariable=self.camera_var, width=30, state="readonly",
                                       postcommand=self.refresh_devices)
        self.camera_box.pack(side="left", padx=6)
        self.camera_box.bind("<<ComboboxSelected>>", self._on_camera_selected)
        self.camera_on = tk.BooleanVar(master=root, value=False)
        self.cam_check = ttk.Checkbutton(row, text="Câmera ligada", variable=self.camera_on,
                                         command=self._on_camera_toggle)
        self.cam_check.pack(side="left")

        self.canvas = tk.Canvas(f, width=PREVIEW_W, height=PREVIEW_H, bg="#111", highlightthickness=0)
        self.canvas.pack(padx=8, pady=6)
        # um PhotoImage so, reaproveitado com paste() a cada frame novo
        self.photo = ImageTk.PhotoImage("RGB", (PREVIEW_W, PREVIEW_H), master=root)
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self._hint = self.canvas.create_text(PREVIEW_W // 2, PREVIEW_H // 2 - 30, fill="#ddd", text="")

        row = ttk.Frame(f)
        row.pack(fill="x", padx=8)
        self.rec_label = ttk.Label(row, text="● REC", foreground=COLOR_REC_OFF)
        self.rec_label.pack(side="left")
        self.fps_label = ttk.Label(row, text="fps: —", foreground=COLOR_TEXT)
        self.fps_label.pack(side="left", padx=12)
        self.time_label = ttk.Label(row, text="")
        self.time_label.pack(side="right")

        row = ttk.Frame(f)
        row.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(row, text="Microfone:").pack(side="left")
        self.mic_var = tk.StringVar(master=root)
        self.mic_box = ttk.Combobox(row, textvariable=self.mic_var, width=40, state="readonly",
                                    postcommand=self.refresh_devices)
        self.mic_box.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.mic_box.bind("<<ComboboxSelected>>", self._on_mic_selected)

        row = ttk.Frame(f)
        row.pack(fill="x", padx=8, pady=6)
        self.video_on = tk.BooleanVar(master=root, value=bool(self.app.estado.get("gravar_video", True)))
        self.video_check = ttk.Checkbutton(row, text="Gravar vídeo", variable=self.video_on,
                                           command=self._on_video_toggle)
        self.video_check.pack(side="left")
        ttk.Label(row, text="Duração (s):").pack(side="left", padx=(16, 0))
        self.duration_var = tk.StringVar(master=root)
        self.duration_entry = ttk.Entry(row, textvariable=self.duration_var, width=6)
        self.duration_entry.pack(side="left", padx=6)
        ttk.Label(row, text="vazio = até clicar Parar").pack(side="left")

        row = ttk.Frame(f)
        row.pack(fill="x", padx=8, pady=(0, 8))
        self.btn_record = ttk.Button(row, text=TXT_RECORD, command=self.on_record)
        self.btn_record.pack(side="left")
        self.status_label = ttk.Label(row, text="Parado.")
        self.status_label.pack(side="left", padx=10)

    # ---------- dispositivos ----------

    def _init_devices(self) -> None:
        saved_mic, saved_cam = self.app.estado.get("mic") or "", self.app.estado.get("camera") or ""
        self.mic_var.set(saved_mic)
        if saved_cam:
            self.camera_var.set(camera_label(saved_cam))
            self._cams = {camera_label(saved_cam): saved_cam}
        self.refresh_devices()
        mics = list(self.mic_box.cget("values"))
        if not mics:
            self.app.log("AVISO: nenhum microfone encontrado (pactl)")
        elif saved_mic and saved_mic not in mics:
            self.app.log(f"AVISO: o microfone salvo não está conectado ({saved_mic}); usando {mics[0]}")
            self.mic_var.set(mics[0])
        if saved_cam and self.camera_path() != saved_cam:
            self.app.log(f"AVISO: a câmera salva não está conectada ({saved_cam})")

    def refresh_devices(self) -> None:
        # a escolha atual fica mesmo se sumiu: gravar avisa em vez de trocar de microfone sem avisar
        names = [s.name for s in self.hw.list_mics()]
        self.mic_box.configure(values=names)
        if not self.mic_var.get() and names:
            self.mic_var.set(names[0])
        current = self.camera_path()
        paths = list(self.hw.list_cameras())
        self._cams = {camera_label(p): p for p in paths}
        self.camera_box.configure(values=list(self._cams))
        if current not in paths:
            self.camera_var.set(camera_label(paths[0]) if paths else "")

    def camera_path(self) -> str:
        return self._cams.get(self.camera_var.get(), "")

    def _on_mic_selected(self, event=None) -> None:
        self.app.update_estado(mic=self.mic_var.get())

    def _on_camera_selected(self, event=None) -> None:
        self.app.update_estado(camera=self.camera_path())
        if self.preview is not None:
            self._stop_preview()          # a nova liga quando a antiga soltar o dispositivo
            self._maybe_start_preview()

    def _on_camera_toggle(self) -> None:
        self._touch()
        if self.camera_on.get():
            self._maybe_start_preview()
        else:
            self._stop_preview()

    def _on_video_toggle(self) -> None:
        self.app.update_estado(gravar_video=bool(self.video_on.get()))
        if self.video_on.get():
            self._maybe_start_preview()
        else:
            self._stop_preview()

    # ---------- preview (PreviewOnly) ----------

    def _maybe_start_preview(self) -> None:
        if (self.preview is not None or self._stops_pending or self._hidden or self.app.closing
                or self.state not in (IDLE, FINISHING) or not (self.camera_on.get() and self.video_on.get())):
            return
        cam = self.camera_path()
        if not cam or not os.path.exists(cam):
            self.app.log("Câmera não encontrada — confira o cabo e escolha a câmera na lista")
            self.camera_on.set(False)
            return
        try:
            os.makedirs(self.app.rec_dir, exist_ok=True)
            cap = self._factory(build_preview_cmd(cam), os.path.join(self.app.rec_dir, PREVIEW_LOG), True)
            cap.start()           # thread principal: o pdeathsig vale enquanto ela viver
        except Exception as e:
            self.app.report_error(e, "ligar a câmera")
            self.camera_on.set(False)
            return
        self.preview = cap
        self._last_activity = time.monotonic()

    def _stop_preview(self) -> None:
        cap, self.preview = self.preview, None
        if cap is None:
            return
        cap.request_stop()
        self._stops_pending += 1
        if not self._submit(JOB_PREVIEW_STOP, cap.wait_stopped):
            self._stops_pending -= 1

    def _preview_failed(self, msg: str) -> None:
        self.app.log(f"Câmera: {msg}")
        self.camera_on.set(False)
        self._stop_preview()

    def _on_unmap(self, event) -> None:
        # o bind do toplevel tambem recebe o Unmap dos filhos
        if event.widget is not self.root:
            return
        self._hidden = True
        self._stop_preview()

    def _on_map(self, event) -> None:
        if event.widget is not self.root:
            return
        self._hidden = False
        self._touch()
        self._maybe_start_preview()

    def _touch(self, event=None) -> None:
        self._last_activity = time.monotonic()

    # ---------- gravar ----------

    def on_record(self) -> None:
        if self.state == RECORDING:
            self.stop_recording()
            return
        if self.state != IDLE or self.app.closing:
            return
        self._touch()
        plan, err = self._prepare()
        if err:
            self.app.log(f"Não foi possível gravar: {err}")
            self._set_status(err)
            return
        self.app.update_estado(mic=plan["mic"], camera=self.camera_path(), gravar_video=plan["av"])
        try:
            take = new_take("av" if plan["av"] else "audio", plan["mic"], plan["cam"], rec_dir=self.app.rec_dir)
        except OSError as e:
            self.app.report_error(e, "criar a tomada")
            return
        self.take, self._plan, self.state = take, plan, STARTING
        self._set_status("Preparando…")
        self._refresh_controls()
        self._stop_preview()
        if not self._stops_pending:
            self._launch()        # senao, o fim do JOB_PREVIEW_STOP chama o _launch

    def _prepare(self) -> tuple[dict | None, str | None]:
        # checagens antes de criar a tomada (spec 5.4.1)
        try:
            duration = parse_duration(self.duration_var.get())
        except ValueError as e:
            return None, str(e)
        av = bool(self.video_on.get())
        mic = self.mic_var.get()
        index = next((s.index for s in self.hw.list_mics() if s.name == mic), None)
        if not mic or index is None:
            return None, MSG_NO_MIC
        cam = self.camera_path() if av else ""
        if av and not cam:
            return None, MSG_NO_CAMERA
        if av and not os.path.exists(cam):
            return None, FFMPEG_EXIT_MSGS[254]
        free = self.hw.free_bytes(self.app.rec_dir)
        if free < MIN_FREE_BYTES:
            return None, (f"Pouco espaço em disco: {_dec(free / 1024**3)} GB livres "
                          f"(mínimo {MIN_FREE_BYTES // 1024**3} GB)")
        return {"av": av, "mic": mic, "mic_index": index, "cam": cam, "duration": duration}, None

    def _launch(self) -> None:
        take, plan = self.take, self._plan
        if plan["av"]:
            argv = build_av_cmd(plan["mic"], plan["cam"], take.raw_path)
        else:
            argv = build_audio_cmd(plan["mic"], take.raw_path)
        try:
            cap = self._factory(argv, take.path("ffmpeg.log"), plan["av"])
            cap.start()           # thread principal (spec 5.4.2)
        except Exception as e:
            self.app.report_error(e, "iniciar a gravação")
            self._fail_take(take, f"Não foi possível iniciar a gravação: {error_message(e)}")
            self._back_to_idle()
            return
        self.rec = cap
        self.state = RECORDING
        self._rec_started = time.monotonic()
        self._next_mic_check = self._rec_started + MIC_CHECK_S
        self._mic_pending = False
        self._fps_warned = False
        self._schedule_duration(plan["duration"])
        self._set_status("Gravando…")
        self._refresh_controls()
        what = "vídeo + áudio" if plan["av"] else "só o áudio"
        self.app.log(f"Gravando {what} → {take.id} (fale agora!)")
        self.app.bus.post("recording", active=True, take=take)

    def _schedule_duration(self, duration: float | None) -> None:
        # nunca -t no gravador: a duracao e um after que dispara a parada normal
        if duration is not None and duration <= MAX_TAKE_S:
            ms, msg = duration * 1000, f"Duração de {_dec(duration, 'g')} s atingida — parando a gravação"
        else:
            if duration is not None:
                self.app.log(f"AVISO: a duração passa do limite; a gravação para em {MAX_TAKE_S // 60} min")
            ms, msg = MAX_TAKE_S * 1000, f"AVISO: {MSG_LIMIT}"
        self._schedule("duration", ms, lambda: self.stop_recording(msg))

    def stop_recording(self, reason: str | None = None, failure: str | None = None) -> None:
        # thread principal: pede o q e espera na fila io
        if self.state != RECORDING:
            return
        self.state = STOPPING
        self._cancel("duration")
        self._rec_elapsed = time.monotonic() - self._rec_started
        self._failure = failure
        if reason:
            self.app.log(reason)
        cap = self.rec
        cap.request_stop()
        self._set_rec_lit(False)
        self._set_status("Finalizando a gravação…")
        self._refresh_controls()
        self._submit(JOB_STOP, cap.wait_stopped)

    # ---------- checagens periodicas ----------

    def _watch(self) -> None:
        try:
            if self.preview is not None:
                msg = self.preview.early_failure()
                if msg:
                    self._preview_failed(msg)
            if self.state == RECORDING:
                self._watch_recording(time.monotonic())
        finally:
            if not self.app.closed:
                self._schedule("watch", WATCH_MS, self._watch)

    def _watch_recording(self, now: float) -> None:
        cap = self.rec
        msg = cap.early_failure()
        if msg:
            self.stop_recording(f"ERRO: {msg}", failure=msg)
            return
        if self._plan["av"]:
            msg = self.watchdog.check_frames(now, cap.last_frame_monotonic, cap.started_monotonic)
            if msg:
                self.stop_recording(f"AVISO: {msg} — parando a gravação")
                return
        if not self._mic_pending and now >= self._next_mic_check:
            self._next_mic_check = now + MIC_CHECK_S
            self._mic_pending = self._submit(JOB_MIC, mic_check_job, self.hw.check_mic, cap.pid,
                                             self._plan["mic_index"])

    def _tick(self) -> None:
        try:
            self._refresh_view(time.monotonic())
        finally:
            if not self.app.closed:
                self._schedule("tick", TICK_MS, self._tick)

    def _refresh_view(self, now: float) -> None:
        cap = self.rec if (self.rec is not None and self._plan.get("av")) else self.preview
        seq, frame = cap.latest_frame() if cap is not None else (0, None)
        if frame is None:
            self._show_hint(self._hint_text())
        elif self._painted != (cap, seq):
            self.photo.paste(compose(frame, self._overlay))
            self.canvas.itemconfigure(self._hint, state="hidden")
            self._painted = (cap, seq)
        self._refresh_fps(cap, now)
        if self.state == RECORDING:
            self.time_label.configure(text=f"{_dec(now - self._rec_started)} s")
            lit = seq > 0 if self._plan["av"] else now - self._rec_started >= REC_AUDIO_S
            if lit and str(self.rec_label.cget("foreground")) != COLOR_REC_ON:
                self._set_rec_lit(True)
        if self.preview is not None and now - self._last_activity > PREVIEW_IDLE_S:
            self.app.log(f"Câmera desligada após {PREVIEW_IDLE_S / 60:g} min sem uso "
                         "(marque \"Câmera ligada\" para voltar)")
            self.camera_on.set(False)
            self._stop_preview()

    def _refresh_fps(self, cap, now: float) -> None:
        fps = cap.fps_measured() if cap is not None else None
        warn = self.watchdog.check_fps(fps, now, cap.started_monotonic) if fps is not None else None
        shown = (warn or ("fps: —" if fps is None else f"Câmera: {_dec(fps)} fps"), COLOR_WARN if warn else COLOR_TEXT)
        if shown != self._fps_shown:
            self.fps_label.configure(text=shown[0], foreground=shown[1])
            self._fps_shown = shown
        if warn and self.state == RECORDING and not self._fps_warned:
            self._fps_warned = True
            self.app.log(f"AVISO: {warn}")

    def _hint_text(self) -> str:
        if self.state in (RECORDING, STOPPING) and not self._plan.get("av"):
            return "Gravando só o áudio"
        if not self.video_on.get():
            return "Só áudio (\"Gravar vídeo\" desmarcado)"
        if not self.camera_on.get() and self.rec is None:
            return "Câmera desligada"
        return "Aguardando a imagem da câmera…"

    def _show_hint(self, text: str) -> None:
        if self._painted == ("hint", text):
            return
        self.photo.paste(compose(None, self._overlay))
        self.canvas.itemconfigure(self._hint, text=text, state="normal")
        self._painted = ("hint", text)

    def _on_model_changed(self, ev: Event) -> None:
        self._overlay = make_overlay(ev.data["modelo"])
        self._painted = (None, None)          # o proximo tick repinta com a marca nova

    # ---------- fim da gravacao (eventos da fila io) ----------

    def _on_job(self, ev: Event) -> None:
        job = ev.data["job"]
        if job == JOB_PREVIEW_STOP:
            self._preview_stopped(ev)
        elif job == JOB_STOP:
            self._recorder_stopped(ev)
        elif job == JOB_FINISH:
            self._finished(ev)
        elif job == JOB_MIC:
            self._mic_checked(ev)

    def _preview_stopped(self, ev: Event) -> None:
        self._stops_pending -= 1
        if ev.kind == "job_fail":
            self.app.log(f"AVISO: a câmera não desligou direito: {ev.data['message']}")
        if self._stops_pending:
            return
        if self.state == STARTING:
            self._launch()
        else:
            self._maybe_start_preview()

    def _recorder_stopped(self, ev: Event) -> None:
        self.rec = None
        rc = ev.data.get("result")
        if ev.kind == "job_fail":
            self.app.log(f"AVISO: o gravador não parou direito: {ev.data['message']}")
        elif rc not in ACCEPTED_RC and not self._failure:
            self.app.log(f"AVISO: o gravador saiu com código {rc}")
        self.state = FINISHING
        self._set_status("Conferindo a gravação…")
        take = self.take
        if not self._submit(JOB_FINISH, finish_capture, take.raw_path, take.modo == "av", take.audio_path):
            return
        self._maybe_start_preview()           # a camera ja esta livre

    def _finished(self, ev: Event) -> None:
        take = self.take
        try:
            res = ev.data["result"] if ev.kind == "job_ok" else {"erro": ev.data["message"]}
            if res["erro"]:
                self._fail_take(take, self._failure or res["erro"])
            else:
                self._take_recorded(take, res)
        finally:
            self._back_to_idle()

    def _take_recorded(self, take: Take, res: dict) -> None:
        # so a thread principal altera o take.json (spec 3.2)
        take.video, take.audio_fit = res["video"], res["audio_fit"]
        take.status, take.erro = "gravado", ""
        take.save()
        fps = take.video.get("fps_medido")
        extra = f", vídeo a {_dec(fps)} fps" if fps else ""
        self.app.log(f"Gravação concluída: {take.id} ({_dec(self._rec_elapsed)} s{extra})")
        if self._failure:
            self.app.log("AVISO: a gravação parou antes da hora; o que foi gravado até ali foi mantido")
        if fps and fps < MIN_FPS:
            self.app.log(f"AVISO: a câmera gravou a {_dec(fps)} fps (abaixo de {MIN_FPS:g}) — pouca luz?")
        mean, peak = res["volume"]
        if peak is not None:
            self.app.log(f"Volume da gravação: média {_dec(mean or 0)} dB, pico {_dec(peak)} dB")
            if peak < MUTE_DB:
                self.app.log(f"AVISO: Microfone mudo? O pico ficou em {_dec(peak)} dB — confira o microfone escolhido")
        self._set_status(f"Gravado: {take.id}")
        self.app.set_take(take)

    def _fail_take(self, take: Take, erro: str) -> None:
        take.status, take.erro = "falhou", erro
        try:
            take.save()
        except OSError as e:
            self.app.report_error(e, "salvar take.json")
        self.app.log(f"ERRO na gravação {take.id}: {erro}")
        self._set_status(f"Falhou: {erro}")

    def _back_to_idle(self) -> None:
        take = self.take
        self.state, self.take, self._plan, self._failure = IDLE, None, {}, None
        self.rec = None
        self._set_rec_lit(False)
        self.time_label.configure(text="")
        self._refresh_controls()
        self._last_activity = time.monotonic()
        self.app.bus.post("recording", active=False, take=take)
        self._maybe_start_preview()

    def _mic_checked(self, ev: Event) -> None:
        self._mic_pending = False
        if ev.kind != "job_ok":
            return
        pid, msg = ev.data["result"]
        if msg and self.state == RECORDING and self.rec is not None and self.rec.pid == pid:
            self.stop_recording(f"AVISO: {msg} — parando a gravação")

    # ---------- fechar ----------

    def _close_reason(self) -> str | None:
        if self.state in (STARTING, RECORDING):
            return "Gravação em andamento (ela será finalizada antes de fechar)"
        return None

    def _on_shutdown(self) -> None:
        # roda com a janela ainda visivel; a tomada fica "gravando" e a abertura seguinte a recupera
        for name in list(self._timers):
            self._cancel(name)
        if self.state == RECORDING and self.rec is not None:
            self._set_status("Finalizando gravação…")
            self.app.log("Finalizando gravação…")
            try:
                self.root.update_idletasks()
            except tk.TclError:
                pass
            self.rec.request_stop()
            rc = self.rec.wait_stopped(t_q=SHUTDOWN_WAIT_S, t_close=1, t_term=1)
            self.app.log(f"Gravação {self.take.id} parada ao fechar (código {rc}); "
                         "ela será conferida na próxima abertura")
        elif self.state == STARTING and self.take is not None:
            self._fail_take(self.take, "Gravação cancelada: o app foi fechado antes de começar")
        cap, self.preview = self.preview, None
        if cap is not None:
            cap.request_stop()
            cap.wait_stopped(t_q=1, t_close=0.5, t_term=0.5)

    # ---------- utilitarios ----------

    def _submit(self, job: str, fn, *args) -> bool:
        try:
            self.app.io_jobs.submit(job, fn, *args)
            return True
        except RuntimeError:          # fila fechada: o app esta fechando
            return False

    def _schedule(self, name: str, ms: float, fn) -> None:
        self._cancel(name)
        self._timers[name] = self.root.after(max(1, round(ms)), fn)

    def _cancel(self, name: str) -> None:
        timer = self._timers.pop(name, None)
        if timer is not None:
            try:
                self.root.after_cancel(timer)
            except tk.TclError:
                pass

    def _set_status(self, text: str) -> None:
        self.status_label.configure(text=text)

    def _set_rec_lit(self, on: bool) -> None:
        self.rec_label.configure(foreground=COLOR_REC_ON if on else COLOR_REC_OFF)

    def _refresh_controls(self) -> None:
        idle = self.state == IDLE
        for box in (self.mic_box, self.camera_box):
            box.configure(state="readonly" if idle else "disabled")
        for w in (self.video_check, self.duration_entry):
            w.configure(state="normal" if idle else "disabled")
        if self.state == RECORDING:
            self.btn_record.configure(text=TXT_STOP, state="normal")
        else:
            self.btn_record.configure(text=TXT_RECORD, state="normal" if idle else "disabled")
```

- [ ] **Step 4: Rodar e ver o próximo erro (o `App` ainda não monta o painel)**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_capture`
Expected: `Ran 20 tests` e `FAILED (errors=16)`: os 4 testes de `HelpersTest` passam, e os 16 do painel dão
`TypeError: App.__init__() got an unexpected keyword argument 'capture_factory'`

- [ ] **Step 5: Montar o painel no `studio/gui.py`**

São cinco trocas pequenas; o resto do arquivo da Task 9 fica igual.

1. O import do painel entra junto dos outros imports de `studio`. Em `studio/gui.py`, substituir isto:

```python
from studio.events import Event, EventBus, JobRunner, error_message
from studio.rvc_client import RvcClient
```

por isto:

```python
from studio.events import Event, EventBus, JobRunner, error_message
from studio.gui_capture import CAPTURE_JOB_LABELS, CapturePanel
from studio.rvc_client import RvcClient
```

2. Os rótulos dos jobs do painel entram na confirmação ao fechar. Em `studio/gui.py`, substituir isto:

```python
JOB_LABELS = {JOB_LOAD_MODEL: "Carregando o modelo"}
```

por isto:

```python
JOB_LABELS = {JOB_LOAD_MODEL: "Carregando o modelo", **CAPTURE_JOB_LABELS}
```

3. O `App` aceita a fábrica do `CaptureProcess` e o acesso ao hardware (os testes injetam falsos). Em `studio/gui.py`, substituir isto:

```python
                 ask_confirm=None):
        self.root = root
```

por isto:

```python
                 ask_confirm=None, capture_factory=None, hardware=None):
        self.root = root
```

4. Guarde os dois no `App`, logo depois do `_ask_confirm`. Em `studio/gui.py`, substituir isto:

```python
        self._ask_confirm = ask_confirm or self._ask_yes_no
        self.rvc = None
```

por isto:

```python
        self._ask_confirm = ask_confirm or self._ask_yes_no
        # fabrica do CaptureProcess e acesso ao hardware (None = os de verdade); os testes injetam falsos
        self.capture_factory = capture_factory
        self.hardware = hardware
        self.rvc = None
```

5. Monte o painel no ponto marcado pela Task 9, antes da abertura (assim ele já existe quando a tomada inicial chega). Em `studio/gui.py`, substituir isto:

```python
        # os paineis das outras tasks entram aqui, antes da abertura (assim recebem o take_changed inicial)
        self._startup(aviso_estado)
```

por isto:

```python
        # os paineis das outras tasks entram aqui, antes da abertura (assim recebem o take_changed inicial)
        self.capture_panel = CapturePanel(self, self.left)
        self._startup(aviso_estado)
```

- [ ] **Step 6: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_capture -v`
Expected: `OK` (20 testes, ~6 s)

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_shell -v`
Expected: `OK` (30 testes). Os testes da Task 9 montam o painel com o hardware de verdade (só listam
`/dev/v4l/by-id` e rodam `pactl list short sources`); a câmera continua desligada porque "Câmera ligada" começa
desmarcado.

Run: `./run_tests.sh`
Expected: `Ran 402 tests` e `OK (skipped=6)` (as 382 das Tasks 1–9 com as correções mais os 20 testes novos)

Run: `pgrep -a ffmpeg`
Expected: nenhuma linha

- [ ] **Step 7: Conferir a janela (opcional, visual)**

Com a sessão desbloqueada (com a tela bloqueada o `ImageGrab` devolve tudo preto), rode da raiz do repositório. O script
abre a janela com o `FakeCapture` dos testes e frames sintéticos (gradiente + um círculo "rosto"), liga a câmera, captura,
clica Gravar, captura de novo e fecha:

```bash
Applio/.venv/bin/python - <<'EOF'
import os, tempfile, time, tkinter as tk
import numpy as np
from PIL import ImageGrab
from studio import gui, gui_capture
from studio.devices import Source
from tests.test_gui_capture import FakeCapture

tmp = tempfile.TemporaryDirectory()
cam = os.path.join(tmp.name, "usb-A4tech_FHD_720P_PC_Camera-video-index0")
open(cam, "w").close()
caps = []

def factory(argv, log_path, with_preview):
    caps.append(FakeCapture(argv, log_path, with_preview))
    return caps[-1]

hw = gui_capture.Hardware(list_cameras=lambda: [cam], list_mics=lambda: [Source(54, "alsa_input.usb-teste.mono")],
                          free_bytes=lambda p: 50 * 1024**3, check_mic=lambda pid, idx: None)
root = tk.Tk()
app = gui.App(root, rec_dir=os.path.join(tmp.name, "rec"), estado_path=os.path.join(tmp.name, "estado.json"),
              log_path=os.path.join(tmp.name, "studio.log"), logs_dir=os.path.join(tmp.name, "logs"),
              ask_confirm=lambda t, m: True, capture_factory=factory, hardware=hw)
root.geometry("1100x720+0+0")      # depois do App (ele fixa so o tamanho)
p = app.capture_panel
y, x = np.mgrid[0:270, 0:480]
img = np.stack([x * 255 // 480, y * 255 // 270, np.full_like(x, 120)], axis=-1).astype(np.uint8)
img[(x - 240) ** 2 + (y - 115) ** 2 < 3600] = (230, 190, 160)       # "rosto" sintetico
frame = img.tobytes()

def feed():
    if caps and not caps[-1].stop_requested:
        caps[-1].fps = 14.6
        caps[-1].push(frame)
    root.after(66, feed)

def grab(name):
    root.update()
    time.sleep(0.3)                  # o compositor desenha a janela
    root.update()
    x0, y0 = root.winfo_rootx(), root.winfo_rooty()
    ImageGrab.grab(bbox=(x0, y0, x0 + root.winfo_width(), y0 + root.winfo_height())).save(name)

def record():
    grab("_shot_1.png")
    p.btn_record.invoke()
    root.after(1500, finish)

def finish():
    grab("_shot_2.png")
    app.on_close()

p.cam_check.invoke()
feed()
root.after(1200, record)
root.mainloop()
tmp.cleanup()
print("capturas (preview, parada):", [(c.with_preview, c.stop_requested) for c in caps])
EOF
```

Expected: `capturas (preview, parada): [(True, True), (True, True)]` (o preview e o gravador foram parados) e dois PNGs.
Em `_shot_1.png`: coluna esquerda "Gravação" com a câmera "A4tech_FHD_720P_PC_Camera" e "Câmera ligada" marcado; o
preview 480×270 com o selo "● IA" no canto superior direito, a faixa escura com "VOZ GERADA POR IA" / "Não é a voz real
do Orochi" / "paródia · homenagem" e duas guias verticais tênues da coluna 9:16; embaixo "● REC" apagado (cinza) e
"Câmera: 14,6 fps"; microfone, "Gravar vídeo" marcado, "Duração (s):" vazio e o botão "● Gravar" com "Parado.". Em
`_shot_2.png`: "● REC" vermelho, o tempo correndo à direita ("1,4 s"), o botão "■ Parar", "Gravando…", as listas e
"Gravar vídeo" desabilitados, e o log com "Gravando vídeo + áudio → <id> (fale agora!)". Olhe e apague
(`rm _shot_*.png`); não versione as capturas.

- [ ] **Step 8: Commit**

```bash
git add studio/gui_capture.py studio/gui.py tests/test_gui_capture.py
git commit -m "feat(studio): painel de captura (preview com marca d'agua, Gravar/Parar, watchdogs, fechar gravando)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

**Correções da revisão (Steps 9–23).** (1) **Duplo clique em Gravar parava a gravação na hora:** sem preview para
desligar, o 1º clique já deixava o botão como "■ Parar" habilitado, e o 2º mandava o `q` antes do 1º pacote (a tomada
virava "falhou", e na abertura seguinte escondia a boa; a Task 9 já pula essa tomada). Agora o Parar fica desabilitado
por `MIN_REC_S = 1.0` s depois do início e, no A/V, até chegar o 1º frame (o que vier por último). Uma duração abaixo
de 1 s é recusada com a `capture.MSG_SHORT` da Task 6. (2) **Falha do pactl parava a gravação** como se o microfone
tivesse sumido. Com a Task 6 corrigida, o painel usa `capture.mic_status`: "desconhecido" (pactl falhou ou estourou
o timeout) só gera um aviso no log; "trocado" só para a gravação na 2ª checagem seguida; "ok" zera a contagem. (3)
**Buraco no áudio sem aviso:** a spec 6.3.3 pede um aviso no log quando `audio_fit["gaps"] > 0`, porque aí o
alinhamento usa o modo assíncrono.

- [ ] **Step 9: Testes do duplo clique e da duração mínima (falha)**

Os testes antigos clicam Parar logo depois do 1º frame, então o `setUp` zera o `MIN_REC_S`. Os testes novos o
ligam de novo com `mock.patch.object`. Em `tests/test_gui_capture.py`, substituir isto:

```python
        patcher = mock.patch.multiple(gui_capture, TICK_MS=20, WATCH_MS=40, MIC_CHECK_S=0.2)
```

por isto:

```python
        # MIN_REC_S=0: os testes que nao sao do duplo clique podem parar logo depois do 1o frame
        patcher = mock.patch.multiple(gui_capture, TICK_MS=20, WATCH_MS=40, MIC_CHECK_S=0.2, MIN_REC_S=0)
```

E em `tests/test_gui_capture.py`, substituir isto:

```python
        take, rec = self.start_recording(app)
        rec.push(RED)
        p.btn_record.invoke()                         # Parar
```

por isto:

```python
        take, rec = self.start_recording(app)
        rec.push(RED)
        # o Parar so libera quando o tick ve o 1o frame
        self.assertTrue(pump_until(app, lambda: str(p.btn_record.cget("state")) == "normal"))
        p.btn_record.invoke()                         # Parar
```

E em `tests/test_gui_capture.py`, substituir isto:

```python
    def test_finish_capture_missing_file(self):
```

por isto:

```python
    def test_min_rec_s(self):
        self.assertEqual(1.0, gui_capture.MIN_REC_S)

    def test_finish_capture_missing_file(self):
```

E em `tests/test_gui_capture.py`, substituir isto:

```python
    def test_early_failure_shows_message_and_goes_back_to_idle(self):
```

por isto:

```python
    def test_double_click_does_not_stop_recording(self):
        # duplo clique em Gravar: o 2o clique cai no Parar desabilitado (MIN_REC_S e, no A/V, o 1o frame)
        app = self.make_app()
        p = app.capture_panel
        with mock.patch.object(gui_capture, "MIN_REC_S", 0.3):
            p.btn_record.invoke()
            p.btn_record.invoke()
            self.assertEqual("recording", p.state)
            self.assertEqual(("■ Parar", "disabled"), (p.btn_record.cget("text"), str(p.btn_record.cget("state"))))
            rec = self.caps[-1]
            pump_for(app, 0.45)                       # passou do MIN_REC_S, mas sem frame: continua travado
            p.btn_record.invoke()
            self.assertEqual(("recording", "disabled"), (p.state, str(p.btn_record.cget("state"))))
            rec.push(RED)
            self.assertTrue(pump_until(app, lambda: str(p.btn_record.cget("state")) == "normal"))
            p.btn_record.invoke()
            self.assertEqual("stopping", p.state)
        self.wait_idle(app)
        self.assertEqual("gravado", app.take.status)

    def test_audio_only_stop_waits_min_rec_s(self):
        app = self.make_app({"gravar_video": False})
        p = app.capture_panel
        with mock.patch.object(gui_capture, "MIN_REC_S", 0.3):
            p.btn_record.invoke()
            p.btn_record.invoke()
            self.assertEqual(("recording", "disabled"), (p.state, str(p.btn_record.cget("state"))))
            self.assertTrue(pump_until(app, lambda: str(p.btn_record.cget("state")) == "normal"))
            self.assertGreaterEqual(time.monotonic() - self.caps[-1].started_monotonic, 0.3)
            p.btn_record.invoke()
            self.assertEqual("stopping", p.state)
        self.wait_idle(app)
        self.assertEqual("gravado", app.take.status)

    def test_duration_below_min_rec_s_is_refused(self):
        app = self.make_app({"gravar_video": False})
        p = app.capture_panel
        p.duration_var.set("0,5")
        with mock.patch.object(gui_capture, "MIN_REC_S", 1.0):
            p.btn_record.invoke()
        self.assertIn("Não foi possível gravar: Gravação curta demais (mínimo 1 s)", self.log_text(app))
        self.assertEqual(("idle", [], []), (p.state, list_takes(self.rec_dir), self.caps))

    def test_early_failure_shows_message_and_goes_back_to_idle(self):
```

- [ ] **Step 10: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_capture`
Expected: `Ran 24 tests` e `FAILED (errors=20)`: os 19 testes do painel param no `setUp` com
`AttributeError: <module 'studio.gui_capture' from '…/studio/gui_capture.py'> does not have the attribute 'MIN_REC_S'`,
e o `test_min_rec_s` dá `AttributeError: module 'studio.gui_capture' has no attribute 'MIN_REC_S'`

- [ ] **Step 11: Parar desabilitado por `MIN_REC_S` (e até o 1º frame no A/V)**

Em `studio/gui_capture.py`, substituir isto:

```python
REC_AUDIO_S = 0.5         # so-audio nao tem frame: o REC acende quando passa da falha rapida
```

por isto:

```python
REC_AUDIO_S = 0.5         # so-audio nao tem frame: o REC acende quando passa da falha rapida
MIN_REC_S = 1.0           # o Parar so libera 1 s depois do inicio (e, no A/V, depois do 1o frame): duplo clique
```

E em `studio/gui_capture.py`, substituir isto:

```python
        self._rec_started = 0.0
        self._rec_elapsed = 0.0
```

por isto:

```python
        self._rec_started = 0.0
        self._rec_elapsed = 0.0
        self._stop_ready = False                # Parar liberado (MIN_REC_S e, no A/V, o 1o frame)
```

E em `studio/gui_capture.py`, substituir isto:

```python
    def on_record(self) -> None:
        if self.state == RECORDING:
            self.stop_recording()
            return
```

por isto:

```python
    def on_record(self) -> None:
        if self.state == RECORDING:
            if self._stop_ready:              # o botao fica desabilitado ate la; isto e so a garantia
                self.stop_recording()
            return
```

E em `studio/gui_capture.py`, substituir isto:

```python
        try:
            duration = parse_duration(self.duration_var.get())
        except ValueError as e:
            return None, str(e)
```

por isto:

```python
        try:
            duration = parse_duration(self.duration_var.get())
        except ValueError as e:
            return None, str(e)
        if duration is not None and duration < MIN_REC_S:
            return None, f"{capture.MSG_SHORT} (mínimo {_dec(MIN_REC_S, 'g')} s)"
```

E em `studio/gui_capture.py`, substituir isto:

```python
        self.rec = cap
        self.state = RECORDING
        self._rec_started = time.monotonic()
```

por isto:

```python
        self.rec = cap
        self.state = RECORDING
        self._stop_ready = False              # o tick libera o Parar (MIN_REC_S e, no A/V, o 1o frame)
        self._rec_started = time.monotonic()
```

E em `studio/gui_capture.py`, substituir isto:

```python
            if lit and str(self.rec_label.cget("foreground")) != COLOR_REC_ON:
                self._set_rec_lit(True)
```

por isto:

```python
            if lit and str(self.rec_label.cget("foreground")) != COLOR_REC_ON:
                self._set_rec_lit(True)
            if not self._stop_ready and now - self._rec_started >= MIN_REC_S and (seq > 0 or not self._plan["av"]):
                self._stop_ready = True           # duplo clique em Gravar nao para a gravacao
                self._refresh_controls()
```

E em `studio/gui_capture.py`, substituir isto:

```python
        if self.state == RECORDING:
            self.btn_record.configure(text=TXT_STOP, state="normal")
```

por isto:

```python
        if self.state == RECORDING:
            self.btn_record.configure(text=TXT_STOP, state="normal" if self._stop_ready else "disabled")
```

- [ ] **Step 12: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_capture`
Expected: `Ran 24 tests` e `OK`

- [ ] **Step 13: Commit**

```bash
git add studio/gui_capture.py tests/test_gui_capture.py
git commit -m "fix(gui_capture): Parar so libera apos MIN_REC_S (e o 1o frame no A/V); duracao minima de 1 s

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 14: Testes do microfone "desconhecido" e "trocado" (falha)**

O falso do hardware passa a responder como o `capture.mic_status` (`"ok"`, `"trocado"` ou `"desconhecido"`), com
uma fila de respostas. Em `tests/test_gui_capture.py`, substituir isto:

```python
import gc
import itertools
```

por isto:

```python
import dataclasses
import gc
import itertools
```

E em `tests/test_gui_capture.py`, substituir isto:

```python
        self.mic_result = None
        self.mic_checks: list[tuple] = []
```

por isto:

```python
        self.mic_state = capture.MIC_OK         # resposta do mic_status falso quando a fila mic_states acaba
        self.mic_states: list[str] = []
        self.mic_checks: list[tuple] = []
```

E em `tests/test_gui_capture.py`, substituir isto:

```python
    def check_mic(self, pid, expected_index):
        self.mic_checks.append((threading.current_thread().name, pid, expected_index))
        return self.mic_result
```

por isto:

```python
    def mic_status(self, pid, expected_index):
        self.mic_checks.append((threading.current_thread().name, pid, expected_index))
        return self.mic_states.pop(0) if self.mic_states else self.mic_state
```

E em `tests/test_gui_capture.py`, substituir isto:

```python
                                  free_bytes=lambda path: self.free, check_mic=self.check_mic)
```

por isto:

```python
                                  free_bytes=lambda path: self.free, mic_status=self.mic_status)
```

E em `tests/test_gui_capture.py`, substituir isto:

```python
        self.mic_result = capture.MSG_MIC
        take, rec = self.start_recording(app)
        rec.push(RED)
        self.wait_idle(app)
        self.assertEqual(("job-io", rec.pid, 54), self.mic_checks[0])
        self.assertIn("Microfone desconectado ou trocado", self.log_text(app))
        self.assertEqual("gravado", Take.load(take.dir).status)
```

por isto:

```python
        self.mic_state = capture.MIC_SWAPPED
        take, rec = self.start_recording(app)
        rec.push(RED)
        self.wait_idle(app)
        self.assertEqual(("job-io", rec.pid, 54), self.mic_checks[0])
        self.assertEqual(2, len(self.mic_checks))                  # para na 2a checagem seguida
        self.assertIn("Microfone desconectado ou trocado", self.log_text(app))
        self.assertEqual("gravado", Take.load(take.dir).status)

    def test_mic_swapped_stops_only_on_second_in_a_row(self):
        # "trocado" isolado nao para ("ok" zera a contagem); "desconhecido" no meio nao conta nem zera
        app = self.make_app({"gravar_video": False})
        swapped, ok, unknown = capture.MIC_SWAPPED, capture.MIC_OK, capture.MIC_UNKNOWN
        self.mic_states = [swapped, ok, swapped, unknown]
        self.mic_state = swapped
        self.start_recording(app)
        self.wait_idle(app)
        self.assertEqual(5, len(self.mic_checks))
        self.assertIn("AVISO: Microfone desconectado ou trocado — parando a gravação", self.log_text(app))
        self.assertEqual(1, self.log_text(app).count(capture.MSG_MIC_UNKNOWN))

    def test_pactl_failure_is_unknown_and_does_not_stop(self):
        # pactl que falha ou estoura o timeout (5 s) e "nao sei": um aviso no log e a gravacao segue
        app = self.make_app({"gravar_video": False})
        p = app.capture_panel
        seen = []

        def real_status(pid, expected_index):
            seen.append(capture.mic_status(pid, expected_index))
            return seen[-1]

        p.hw = dataclasses.replace(p.hw, mic_status=real_status)
        with mock.patch("studio.devices._pactl", return_value=None):
            self.start_recording(app)
            self.assertTrue(pump_until(app, lambda: len(seen) >= 3))
            pump_for(app, 0.1)
            self.assertEqual("recording", p.state)
            p.btn_record.invoke()
            self.wait_idle(app)
        self.assertEqual({capture.MIC_UNKNOWN}, set(seen))
        self.assertEqual(1, self.log_text(app).count(f"AVISO: {capture.MSG_MIC_UNKNOWN}"))
        self.assertEqual("gravado", app.take.status)
```

- [ ] **Step 15: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_capture`
Expected: `Ran 26 tests` e `FAILED (errors=21)`: os 21 testes do painel param no `make_app` com
`TypeError: Hardware.__init__() got an unexpected keyword argument 'mic_status'`

- [ ] **Step 16: `mic_status` no `Hardware` e parada só no 2º "trocado" seguido**

Em `studio/gui_capture.py`, substituir isto:

```python
MIC_CHECK_S = 2.0         # a 1a checagem sai 2 s depois do inicio (o pactl leva ~1,2 s para ver o gravador)
```

por isto:

```python
MIC_CHECK_S = 2.0         # a 1a checagem sai 2 s depois do inicio (o pactl leva ~1,2 s para ver o gravador)
MIC_SWAPS_TO_STOP = 2     # "trocado" em 2 checagens seguidas para a gravacao ("desconhecido" nao conta nem zera)
```

E em `studio/gui_capture.py`, substituir isto:

```python
    free_bytes: Callable[[str], int] = devices.free_bytes
    check_mic: Callable[[int, int], str | None] = capture.check_mic
```

por isto:

```python
    free_bytes: Callable[[str], int] = devices.free_bytes
    check_mic: Callable[[int, int], str | None] = capture.check_mic   # so o texto; o painel usa o mic_status
    mic_status: Callable[[int, int], str] = capture.mic_status        # MIC_OK | MIC_SWAPPED | MIC_UNKNOWN
```

E em `studio/gui_capture.py`, substituir isto:

```python
        self._next_mic_check = 0.0
        self._mic_pending = False
        self._failure: str | None = None
```

por isto:

```python
        self._next_mic_check = 0.0
        self._mic_pending = False
        self._mic_swaps = 0                     # "trocado" seguidos
        self._mic_unknown = False               # o aviso do "desconhecido" ja saiu nesta sequencia
        self._failure: str | None = None
```

E em `studio/gui_capture.py`, substituir isto:

```python
        self._next_mic_check = self._rec_started + MIC_CHECK_S
        self._mic_pending = False
```

por isto:

```python
        self._next_mic_check = self._rec_started + MIC_CHECK_S
        self._mic_pending = False
        self._mic_swaps, self._mic_unknown = 0, False
```

E em `studio/gui_capture.py`, substituir isto:

```python
            self._mic_pending = self._submit(JOB_MIC, mic_check_job, self.hw.check_mic, cap.pid,
                                             self._plan["mic_index"])
```

por isto:

```python
            self._mic_pending = self._submit(JOB_MIC, mic_check_job, self.hw.mic_status, cap.pid,
                                             self._plan["mic_index"])
```

E em `studio/gui_capture.py`, substituir isto:

```python
    def _mic_checked(self, ev: Event) -> None:
        self._mic_pending = False
        if ev.kind != "job_ok":
            return
        pid, msg = ev.data["result"]
        if msg and self.state == RECORDING and self.rec is not None and self.rec.pid == pid:
            self.stop_recording(f"AVISO: {msg} — parando a gravação")
```

por isto:

```python
    def _mic_checked(self, ev: Event) -> None:
        # "desconhecido" (pactl falhou/estourou o timeout) so avisa; "trocado" para no 2o seguido; "ok" zera
        self._mic_pending = False
        if ev.kind != "job_ok":
            return
        pid, status = ev.data["result"]
        if self.state != RECORDING or self.rec is None or self.rec.pid != pid:
            return
        if status == capture.MIC_UNKNOWN:
            if not self._mic_unknown:
                self._mic_unknown = True
                self.app.log(f"AVISO: {capture.MSG_MIC_UNKNOWN}")
            return
        self._mic_unknown = False
        if status != capture.MIC_SWAPPED:
            self._mic_swaps = 0
            return
        self._mic_swaps += 1
        if self._mic_swaps >= MIC_SWAPS_TO_STOP:
            self.stop_recording(f"AVISO: {capture.MSG_MIC} — parando a gravação")
```

O campo `check_mic` do `Hardware` fica: a Task 11 (teste e Step 7) monta o `Hardware` com ele.

- [ ] **Step 17: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_capture`
Expected: `Ran 26 tests` e `OK`

- [ ] **Step 18: Commit**

```bash
git add studio/gui_capture.py tests/test_gui_capture.py
git commit -m "fix(gui_capture): pactl sem resposta so avisa; microfone trocado para na 2a checagem seguida

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 19: Teste do aviso de buraco no áudio (falha)**

O teste grava duas vezes: a tomada sintética não tem buraco, e a 2ª passa por um `extract_aligned_audio` que
devolve `gaps=2`. Em `tests/test_gui_capture.py`, substituir isto:

```python
    def test_watchdog_stops_when_camera_stalls(self):
```

por isto:

```python
    def test_audio_gaps_warn_in_log(self):
        # buraco no audio (spec 6.3.3): o alinhamento usa o modo assincrono e o log avisa
        app = self.make_app()
        p = app.capture_panel
        real = gui_capture.timeline.extract_aligned_audio

        def with_gaps(raw, out):
            vi, fit = real(raw, out)
            fit.gaps = 2
            return vi, fit

        def record_and_stop() -> Take:
            take, rec = self.start_recording(app)
            rec.push(RED)
            self.assertTrue(pump_until(app, lambda: str(p.btn_record.cget("state")) == "normal"))
            p.btn_record.invoke()
            self.wait_idle(app)
            return take

        record_and_stop()
        self.assertNotIn("buraco", self.log_text(app))
        with mock.patch.object(gui_capture.timeline, "extract_aligned_audio", with_gaps):
            take = record_and_stop()
        self.assertEqual(2, Take.load(take.dir).audio_fit["gaps"])
        self.assertIn(f"AVISO: o áudio da tomada {take.id} teve 2 buraco(s) acima de 30 ms; o alinhamento usou o "
                      "modo assíncrono — confira a sincronia", self.log_text(app))

    def test_watchdog_stops_when_camera_stalls(self):
```

- [ ] **Step 20: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_capture`
Expected: `Ran 27 tests` e `FAILED (failures=1)`: `AssertionError: 'AVISO: o áudio da tomada … teve 2 buraco(s) acima
de 30 ms; o alinhamento usou o modo assíncrono — confira a sincronia' not found in '…'`

- [ ] **Step 21: `gaps_warning` e o aviso no fim da gravação**

Em `studio/gui_capture.py`, substituir isto:

```python
    out["volume"] = audio.volumedetect(audio_out)
    return out
```

por isto:

```python
    out["volume"] = audio.volumedetect(audio_out)
    return out


def gaps_warning(take: Take) -> str | None:
    # spec 6.3.3: com buraco no audio o alinhamento usa o modo assincrono (aresample=async)
    gaps = (take.audio_fit or {}).get("gaps") or 0
    if gaps <= 0:
        return None
    return (f"AVISO: o áudio da tomada {take.id} teve {gaps} buraco(s) acima de {timeline.GAP_S * 1000:g} ms; "
            "o alinhamento usou o modo assíncrono — confira a sincronia")
```

E em `studio/gui_capture.py`, substituir isto:

```python
        if fps and fps < MIN_FPS:
            self.app.log(f"AVISO: a câmera gravou a {_dec(fps)} fps (abaixo de {MIN_FPS:g}) — pouca luz?")
```

por isto:

```python
        if fps and fps < MIN_FPS:
            self.app.log(f"AVISO: a câmera gravou a {_dec(fps)} fps (abaixo de {MIN_FPS:g}) — pouca luz?")
        warn = gaps_warning(take)
        if warn:
            self.app.log(warn)
```

- [ ] **Step 22: Rodar e ver passar (módulo e suíte inteira)**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_capture`
Expected: `Ran 27 tests` e `OK`

Run: `./run_tests.sh` e depois `pgrep -a ffmpeg`
Expected: `Ran 409 tests` e `OK (skipped=6)` (as Tasks 1–10 com as correções); o `pgrep` não mostra nada

- [ ] **Step 23: Commit**

```bash
git add studio/gui_capture.py tests/test_gui_capture.py
git commit -m "fix(gui_capture): aviso no log quando o audio da tomada tem buraco (alinhamento assincrono)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

**Notas para o executor**

- **A câmera nunca liga sozinha.** "Câmera ligada" começa desmarcado e não vai para o `estado.json` (só `mic`, `camera` e
  `gravar_video` vão). Assim abrir o app não acende a luz da webcam nem bloqueia o Meet/Zoom, e os testes da Task 9, que
  montam o `App` com o hardware de verdade, não abrem a câmera. Clicar Gravar com "Gravar vídeo" marcado grava A/V mesmo
  com a câmera desmarcada; o preview então vem do próprio gravador.
- **A câmera só pode ser aberta depois que o ffmpeg anterior a soltou.** Religar o preview (ou iniciar o gravador) enquanto
  o `wait_stopped` do anterior ainda roda na fila io dá "Câmera em uso" (rc 240). Por isso existe `_stops_pending`: todo
  início espera o `JOB_PREVIEW_STOP` pendente, e o Gravar com preview ligado passa por `"starting"` até esse job chegar.
  O `FakeCapture.wait_stopped` dorme 50 ms e o teste compara `preview.stopped_at < rec.started_monotonic`; sem isso a
  mutação "iniciar o gravador sem esperar" passava nos testes.
- **O `<Unmap>` ligado no toplevel também recebe o `<Unmap>` de todo widget filho** (medido: um `pack_forget` num botão
  chegou ao bind do `root` com `event.widget` = o botão). Por isso o filtro `event.widget is self.root`. Nesta sessão
  GNOME/X11 o `root.iconify()` não chegou a minimizar a janela (o estado ficou `normal` e nenhum `<Unmap>` chegou), então o
  desligar-ao-minimizar não foi visto de ponta a ponta; o desligar após 5 min sem uso cobre o resto.
- **Com a janela escondida (`withdraw`) o Tk não entrega eventos gerados**: `event_generate("<Unmap>")` e cliques gerados
  num widget não chegam aos binds. Os testes chamam `_on_unmap`/`_on_map` direto com `SimpleNamespace(widget=...)` e
  testam o tempo ocioso trocando `PREVIEW_IDLE_S`. Já `Checkbutton.invoke()`, `Button.invoke()` e
  `event_generate("<<ComboboxSelected>>")` funcionam escondidos.
- **Tempos nos testes:** o painel lê `TICK_MS`, `WATCH_MS`, `MIC_CHECK_S`, `PREVIEW_IDLE_S` e `MAX_TAKE_S` do módulo na
  hora de usar, então os testes fazem `mock.patch.multiple(gui_capture, TICK_MS=20, WATCH_MS=40, MIC_CHECK_S=0.2)` **antes**
  de criar o `App` (o primeiro `after` já sai com o valor curto). O aviso do limite continua dizendo "5 min" porque
  `MSG_LIMIT` é calculado no import.
- **Antes de conferir que o REC ainda está apagado, deixe o tick rodar** (`pump_for(app, 0.15)`): logo depois do estado
  virar `"recording"` nenhum tick rodou ainda, e uma versão que acende o REC antes do 1º frame passava no teste.
- **A 1ª checagem do microfone sai 2 s depois do início, não 1 s.** Com o `pactl` respondendo, gravador ainda não
  registrado conta como "trocado", e na Task 6 o registro só apareceu em ~1,2 s; checar antes pararia toda gravação.
  O `pactl` roda na fila io, e `_mic_pending` impede empilhar checagens se a fila estiver ocupada.
- **Microfone "desconhecido" × "trocado" (Steps 14–18).** O `pactl` que falha ou estoura o timeout de 5 s não diz
  nada sobre o microfone: o painel loga `AVISO: Não deu para conferir o microfone (o pactl não respondeu)` uma vez e
  continua gravando. A gravação só para com o `pactl` respondendo "trocado" em 2 checagens seguidas (~4 s): um
  "ok" zera a contagem, e um "desconhecido" no meio não conta nem zera. O teste do pactl usa o `capture.mic_status`
  de verdade com `studio.devices._pactl` trocado por um que devolve `None` (a falha), trocando `p.hw` por
  `dataclasses.replace(p.hw, mic_status=...)` depois do `make_app`.
- **Duplo clique (Steps 9–13).** O Parar fica desabilitado (com o texto "■ Parar") até `MIN_REC_S` e, no A/V, até o
  tick ver o 1º frame; quem o libera é o `_refresh_view`, no mesmo tick que acende o REC. `ttk.Button.invoke()` num
  botão desabilitado não faz nada, e é assim que o teste prova que o 2º clique não para. Os testes antigos clicam
  Parar logo depois do `push`, por isso o `setUp` zera o `MIN_REC_S` e o `test_stop_takes_take_to_gravado` espera o
  botão liberar. Uma "Duração" abaixo de 1 s é recusada antes de criar a tomada ("Gravação curta demais (mínimo
  1 s)"). As paradas automáticas (duração, limite, câmera travada, microfone) não dependem do botão.
- **Aviso de buraco no áudio (Steps 19–23):** o `gaps_warning` fica em `gui_capture.py` porque a Task 11 grava o
  `audio_fit` de novo na tomada recuperada e reusa o mesmo texto. O teste troca `timeline.extract_aligned_audio` por um
  que devolve `gaps=2` (a tomada sintética não tem buraco).
- **A falha rápida é conferida a cada 500 ms durante toda a gravação**, não só uma vez: um ffmpeg que morre no meio
  (câmera desplugada) também cai na parada. Depois o `verify_capture` decide: arquivo bom fica "gravado" com o aviso
  "a gravação parou antes da hora"; arquivo ruim vira "falhou" com a mensagem do código de saída (mais útil que
  "Gravação não encontrada"). Parada por câmera travada, microfone trocado, duração ou limite é graciosa: o que foi
  gravado fica como "gravado", com o motivo no log.
- **Fechar gravando:** o gancho de saída roda na thread principal, mostra "Finalizando gravação…", manda o `q` e espera
  até 5 s (`wait_stopped(t_q=5, t_close=1, t_term=1)`). Ele **não** confere nem alinha: a tomada fica "gravando" e o
  `recover_takes` da próxima abertura a marca "gravado", mas sem `audio.wav` e sem `take.video`.
- `ImageTk.PhotoImage("RGB", (480, 270), master=root)`: passe o `master`, porque os testes criam e destroem vários `Tk()`.
  O 1º `paste` levou 4,5 ms; os seguintes são rápidos. Nos testes, `ImageTk.getimage(photo)` devolve a imagem mostrada
  no canvas (é assim que eles conferem o frame, a faixa da marca, a guia 9:16 e a troca de modelo).
- O stderr do PreviewOnly vai para `recordings/preview.log` (o `list_takes` ignora, porque não é pasta com `take.json`);
  o do gravador vai para `recordings/<id>/ffmpeg.log`.
- A regra da Task 9 contra o lixo do Tk vale aqui: o `setUp` registra `self.addCleanup(gc.collect)` primeiro, e o
  `PhotoImage`, os `StringVar` e os `BooleanVar` ficam presos no painel até o `shutdown()`.

---

### Task 11: Painel de volume, conversão, vídeo e Drive (coluna direita, etapas 2 a 5)

**Files:**
- Create: `studio/gui_pipeline.py`
- Modify: `studio/gui.py` (importa o painel, junta `PIPELINE_JOB_LABELS` ao `JOB_LABELS`, aceita `pipeline_deps` no
  `App`, monta o `PipelinePanel` na coluna direita e passa para o log a sobra e o aperto de altura da janela)
- Test: `tests/test_gui_pipeline.py`

**Interfaces:**
- Consumes:
  - Task 1: `studio.config.VIDEOS_DIR`, `Modelo(key, label, nome, aviso)`, `load_estado(path: str = ESTADO_PATH) ->
    tuple[dict, str | None]` (Step 11; com a correção da Task 1 os tipos já vêm certos, e `av_offset_ms` é `int`);
    `studio.procs.MSG_DISK_FULL = "Disco cheio — libere espaço"` e `os_error_message(e: OSError) -> str` (Step 16);
    `studio.procs.spawn(argv: list[str],
    **popen_kwargs) -> subprocess.Popen`, `procs.run(argv: list[str], timeout: float | None = None, **kw) ->
    subprocess.CompletedProcess`, `procs.media_info(path: str) -> dict` (testes); `studio.takes.Take` (`id`, `dir`,
    `modo`, `status`, `erro`, `video`, `audio_fit`, `saidas`, `raw_path`, `audio_path`, `path(name)`, `save()`,
    `Take.load(dir)`), `final_video_name(take_id: str, model_key: str) -> str` (testes).
  - Task 2: `studio.audio.boost_volume(inp: str, out: str, gain_db: float) -> None`,
    `volumedetect(path: str) -> tuple[float | None, float | None]`, `export_mp3(wav: str, mp3: str, modelo: Modelo) -> None`.
  - Task 4: `studio.timeline.extract_aligned_audio(raw_mkv: str, out_wav: str) -> tuple[VideoInfo, AudioFit]`
    (dataclasses; o painel grava o `asdict` na Take).
  - Task 5: `studio.render.render_final(take: Take, modelo: Modelo, conv_wav: str, av_offset_ms: int = 0,
    videos_dir: str = VIDEOS_DIR, cancel: threading.Event | None = None) -> str`, `RenderError` (str = mensagem PT;
    com a correção da Task 5 o `render_final` não deixa escapar `OSError`: disco cheio vira "Disco cheio — libere
    espaço"), `CANCEL_MSG = "Render cancelado"` (testes).
  - Task 7: `studio.rvc_client.RvcClient` com `convert(inp: str, out: str, model_key: str) -> dict` e `alive() -> bool`;
    `RvcError` (também pode trazer "Disco cheio — libere espaço"); env `STUDIO_RVC_FAKE=1` (worker falso, testes).
  - Task 8: `studio.drive.parse_folder_link(s: str) -> tuple[str, str | None]`, `DriveError` (str = mensagem PT),
    `upload_files(paths: list[str], folder_link: str, on_progress=None, cancel: threading.Event | None = None,
    dry_run: bool = False, videos_dir: str = VIDEOS_DIR) -> list[dict]` (cada item `{"arquivo", "ok", "pulado", "md5",
    "erro", "aviso"}`; `on_progress(nome: str, fracao: float)`), `reconnect_cmd() -> list[str]`,
    `rclone_bin() -> str | None`, `classify_error(rc: int, log_text: str) -> str`, `MSG_NO_RCLONE`, `MSG_NO_CONFIG`,
    `MSG_RELOGIN`, `MSG_CANCELLED`.
  - Task 9: `studio.events.Event(kind, data)`, `error_message(exc) -> str`; `studio.gui.App` com `root`, `right`,
    `estado`, `estado_path` (Step 11), `take`, `bus.post(kind, **data)`, `gpu_jobs`/`io_jobs`/`upload_jobs`
    (`submit(job, fn, *args, **kwargs)`,
    `busy`), `model_loaded`, `model_status`, `btn_load`, `add_section(title: str, parent=None) -> ttk.LabelFrame`,
    `log(msg)`, `report_error(exc, where)`, `on(kind, fn)`, `update_estado(**changes)`, `modelo() -> Modelo`,
    `set_take(take)`, `ensure_rvc()` (só na thread principal; recria o worker morto), `register_closer(fn)`,
    `add_shutdown_hook(fn)`; eventos `"take_changed"` `{take}`, `"model_changed"` `{modelo}`,
    `"job_ok"` `{job, runner, result}`, `"job_fail"` `{job, runner, message, traceback}`; `JOB_LABELS`. Os testes usam
    também `close_reasons()`, `on_close()`, `shutdown()`, `dispatch_events()`, `model_var`, `model_box` e `log_box`.
  - Task 10: evento `"recording"` `{active: bool, take}`; `App(..., capture_factory=None, hardware=None)` e o
    `CapturePanel` montado antes; `studio.gui_capture.Hardware(list_cameras, list_mics, free_bytes, check_mic)` e
    `studio.devices.Source(index, name)` (testes); `tests.helpers.make_synthetic_take(path, duration_s=...,
    flash_frame=..., size=...) -> str`; `studio.gui_capture.gaps_warning(take: Take) -> str | None` (Task 10, Step 21;
    usado no Step 26); `app.capture_panel.mic_box` e `camera_path()` (testes).
- Produces:
  - `studio/gui_pipeline.py` (importa tkinter):
    - jobs `JOB_EXTRACT = "pipeline_extract"` e `JOB_BOOST = "pipeline_boost"` (fila io, curtos), `JOB_CONVERT =
      "pipeline_convert"` e `JOB_RENDER = "pipeline_render"` (fila gpu), `JOB_UPLOAD = "pipeline_upload"` e
      `JOB_RECONNECT = "pipeline_reconnect"` (fila upload); `PIPELINE_JOB_LABELS` (job → texto PT ao fechar), com
      `LABEL_RENDER = "Gerando o vídeo"` e `LABEL_UPLOAD = "Envio para o Drive em andamento"`.
    - evento novo `EV_PROGRESS = "drive_progress"` `{nome: str, fracao: float}` (postado pela thread do envio).
    - `DEFAULT_GAIN_DB = 15`, `MIN_GAIN_DB, MAX_GAIN_DB = 1, 40`, `RECONNECT_TIMEOUT_S = 300`, `BOOSTED = "boosted.wav"`,
      `FFPLAY = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error"]`, `OPENER = "xdg-open"`, cores `COLOR_BAD`,
      `COLOR_BUSY`, `COLOR_OK`, `COLOR_TEXT`; mensagens `MSG_WORKER_DIED = "Conversor reiniciado — clique Converter de
      novo"`, `MSG_BAD_GAIN`, `MSG_RECONNECT_TIMEOUT`, `MSG_NO_WAV`.
    - `parse_gain(text: str) -> float` (aceita vírgula; `ValueError(MSG_BAD_GAIN)` fora de 1–40 dB);
      `reconnect_drive(run=procs.run, timeout: float = RECONNECT_TIMEOUT_S) -> None` (`DriveError` com mensagem PT);
      `boost_job`, `convert_job` e `upload_job` (o que roda nas threads de trabalho).
    - `@dataclass(frozen=True) class PipelineDeps` com `boost_volume`, `volumedetect`, `export_mp3`,
      `extract_aligned_audio`, `render_final`, `upload_files`, `reconnect`, `spawn`, `ask_link`
      (`(titulo, texto, inicial) -> str | None`; `None` = `simpledialog.askstring`) e `videos_dir` (padrão: as funções
      reais e `VIDEOS_DIR`).
    - `class PipelinePanel(app, parent)`: atributos `busy` (etapa rodando ou `None`), `uploading` (`JOB_UPLOAD`,
      `JOB_RECONNECT` ou `None`), `recording` e os widgets `gain_var`, `gain_entry`, `btn_boost`, `volume_label`,
      `btn_convert`, `btn_play_rec`, `btn_play_out`, `convert_label`, `btn_render`, `btn_cancel_render`, `btn_watch`,
      `btn_open_folder`, `video_label`, `btn_upload`, `btn_drive_config`, `btn_reconnect`, `progress`,
      `btn_cancel_upload`, `drive_label`. Métodos `refresh()`, `on_boost()`, `on_convert()`, `on_render()`,
      `on_cancel_render()`, `on_upload()`, `on_cancel_upload()`, `configure_drive() -> str | None`, `on_reconnect()`,
      `on_play_recording()`, `on_play_result()`, `on_watch()`, `on_open_folder()`.
    - `take.json`, gravado só na thread principal: `saidas[modelo] = {"wav": "<modelo>.wav", "mp3": "<modelo>_IA.mp3",
      "mp4": "../../videos_finais/<id>_<modelo>_IA.mp4", "enviado": {"md5", "quando"}, "erro": "<msg PT>"}`; status
      `"convertido"` ou `"renderizado"`. Desde o Step 16, erro de conversão ou de vídeo vai para
      `saidas[modelo]["erro"]` (o sucesso seguinte apaga) e **não** mexe no status; `"falhou"` (com `erro`) fica só para
      o áudio da gravação que não se lê (extração da tomada recuperada). Cancelar o vídeo e Aumentar volume não mexem
      no status. Um `take.json` que não grava (ex.: disco cheio) desfaz a mudança na memória, mostra "Falhou: <msg>"
      na seção e não segue para a etapa seguinte.
    - O render lê o `av_offset_ms` do `estado.json` no clique (Step 11). Com buraco no áudio da tomada recuperada, o
      aviso do `gaps_warning` vai para o log (Step 26). O `on_upload` confere de novo o `uploading` depois do diálogo
      do link (Step 21).
  - `studio/gui.py`: `App(..., pipeline_deps=None)` com os atributos `pipeline_deps` e `pipeline_panel`; `JOB_LABELS`
    inclui os 6 jobs do painel; a linha do log (`row=1`) recebe o peso da grade.

Todos os comandos rodam da raiz do repositório (`~/orochi-ia-homenagem`). Os testes do painel precisam de um display X11
(`DISPLAY`), deixam a janela escondida (`root.withdraw()`) e **nunca** usam GPU, Drive, câmera, microfone nem som. O
conversor é um `FakeRvc` que copia a entrada (um teste usa o worker de verdade com `STUDIO_RVC_FAKE=1`). O render, o
envio, o Reconectar, o diálogo do link e o `spawn` (ffplay/xdg-open) são falsos injetados por `PipelineDeps`. Já
`boost_volume`, `volumedetect`, `export_mp3` e `extract_aligned_audio` rodam de verdade (ffmpeg na CPU, com uma tomada
sintética de 2 s).

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_gui_pipeline.py`:

```python
import gc
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import tkinter as tk
import unittest
from dataclasses import asdict
from tkinter import ttk
from types import SimpleNamespace
from unittest import mock

import numpy as np
import soundfile as sf

from studio import drive, gui, gui_capture, gui_pipeline, procs, render, timeline
from studio.devices import Source
from studio.rvc_client import RvcClient, RvcError
from studio.takes import Take, final_video_name
from tests import helpers

HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
MIC = "alsa_input.usb-teste.mono"
TAKE_ID = "2026-09-26_101500"
FOLDER_ID = "1AbCdEfGhIjKlMnOpQrStUv"
LINK = f"https://drive.google.com/drive/folders/{FOLDER_ID}?usp=sharing"
MD5 = "0123456789abcdef0123456789abcdef"
FAIL_MSG = "O vídeo gerado não passou na verificação: Texto da marca d'água não aparece no(s) frame(s) 0"


class FakeRvc:
    """Conversor falso: copia a entrada; die=True simula o worker morrendo no meio do job."""

    def __init__(self):
        self.starts = 0
        self.calls: list[tuple] = []
        self.die = False
        self._alive = False

    def start(self):
        self.starts += 1
        self._alive = True

    def alive(self):
        return self._alive

    def load(self):
        return {"id": "1", "ok": True}

    def close(self):
        self._alive = False

    def convert(self, inp, out, model_key):
        self.calls.append((threading.current_thread().name, inp, out, model_key))
        if self.die:
            self.die = False
            self._alive = False
            raise RvcError("O conversor fechou inesperadamente (código -9) — veja studio_rvc.log")
        data, sr = sf.read(inp, dtype="int16")
        sf.write(out, data, sr, subtype="PCM_16")
        return {"id": "2", "ok": True, "duracao": len(data) / sr, "sr": sr, "pedacos": 1}


class FakeRender:
    """render_final falso (sem GPU): 'ok' cria o MP4 final, 'fail' levanta, 'block' espera o Cancelar."""

    def __init__(self):
        self.mode = "ok"
        self.calls: list[SimpleNamespace] = []
        self.started = threading.Event()

    def __call__(self, take, modelo, conv_wav, av_offset_ms=0, videos_dir="", cancel=None):
        self.calls.append(SimpleNamespace(thread=threading.current_thread().name, take=take, modelo=modelo,
                                          conv_wav=conv_wav, av_offset_ms=av_offset_ms, videos_dir=videos_dir,
                                          cancel=cancel))
        self.started.set()
        if self.mode == "block":
            if not cancel.wait(10):
                raise AssertionError("ninguém cancelou o render")
            raise render.RenderError(render.CANCEL_MSG)
        if self.mode == "fail":
            raise render.RenderError(FAIL_MSG)
        os.makedirs(videos_dir, exist_ok=True)
        final = os.path.join(videos_dir, final_video_name(take.id, modelo.key))
        with open(final, "wb") as f:
            f.write(b"mp4 falso")
        return final


class FakeUpload:
    """upload_files falso (sem Drive): posta progresso 0 e 42 %; block=True espera o gate ou o Cancelar."""

    def __init__(self):
        self.calls: list[SimpleNamespace] = []
        self.block = False
        self.gate = threading.Event()
        self.result: dict | None = None

    def __call__(self, paths, folder_link, on_progress=None, cancel=None, dry_run=False, videos_dir=""):
        self.calls.append(SimpleNamespace(thread=threading.current_thread().name, paths=list(paths),
                                          link=folder_link, videos_dir=videos_dir, cancel=cancel))
        name = os.path.basename(paths[0])
        on_progress(name, 0.0)
        on_progress(name, 0.42)
        end = time.monotonic() + 10
        while self.block and not (self.gate.is_set() or cancel.is_set()) and time.monotonic() < end:
            time.sleep(0.01)
        res = {"arquivo": paths[0], "ok": True, "pulado": False, "md5": MD5, "erro": "", "aviso": ""}
        if cancel.is_set():
            res.update(ok=False, md5="", erro=drive.MSG_CANCELLED)
        elif self.result is not None:
            res.update(self.result)
        return [res]


class FakeProc:
    def __init__(self, argv, kw):
        self.argv, self.kw = argv, kw
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True


def pump_until(app, cond, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.root.update()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def state(widget) -> str:
    return str(widget.cget("state"))


def zombie(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return True


class ReconnectDriveTest(unittest.TestCase):
    def test_runs_reconnect_cmd_with_timeout(self):
        calls = []

        def run(argv, timeout=None):
            calls.append((argv, timeout))
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(drive, "rclone_bin", return_value="/opt/bin/rclone"):
            gui_pipeline.reconnect_drive(run=run)
            self.assertEqual([(drive.reconnect_cmd(), 300)], calls)
            self.assertEqual("/opt/bin/rclone", calls[0][0][0])

    def test_errors_in_portuguese(self):
        def timeout(argv, timeout=None):
            raise subprocess.TimeoutExpired(argv, timeout)

        def no_config(argv, timeout=None):
            return subprocess.CompletedProcess(argv, 1, "", "Failed: didn't find section in config file")

        with mock.patch.object(drive, "rclone_bin", return_value="/opt/bin/rclone"):
            with self.assertRaises(drive.DriveError) as cm:
                gui_pipeline.reconnect_drive(run=timeout)
            self.assertEqual(gui_pipeline.MSG_RECONNECT_TIMEOUT, str(cm.exception))
            with self.assertRaises(drive.DriveError) as cm:
                gui_pipeline.reconnect_drive(run=no_config)
            self.assertEqual(drive.MSG_NO_CONFIG, str(cm.exception))
        with mock.patch.object(drive, "rclone_bin", return_value=None):
            with self.assertRaises(drive.DriveError) as cm:
                gui_pipeline.reconnect_drive(run=no_config)
            self.assertEqual(drive.MSG_NO_RCLONE, str(cm.exception))


@unittest.skipUnless(HAS_DISPLAY, "sem display")
class PipelinePanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = tempfile.TemporaryDirectory()
        cls.av_raw = helpers.make_synthetic_take(os.path.join(cls.media.name, "raw.mkv"), duration_s=2.0,
                                                 flash_frame=30, size="320x240")
        cls.av_audio = os.path.join(cls.media.name, "audio.wav")
        vi, fit = timeline.extract_aligned_audio(cls.av_raw, cls.av_audio)
        cls.vi, cls.fit = asdict(vi), asdict(fit)
        cls.tone = os.path.join(cls.media.name, "tom.wav")
        t = np.arange(48000) / 48000
        sf.write(cls.tone, (0.2 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16), 48000, subtype="PCM_16")

    @classmethod
    def tearDownClass(cls):
        cls.media.cleanup()

    def setUp(self):
        # roda por ultimo: o lixo do Tk (App, StringVar, PhotoImage) morre na thread principal
        self.addCleanup(gc.collect)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rec_dir = os.path.join(self.tmp.name, "recordings")
        self.videos_dir = os.path.join(self.tmp.name, "videos_finais")
        self.estado_path = os.path.join(self.tmp.name, "estado.json")
        self.log_path = os.path.join(self.tmp.name, "studio.log")
        self.rvc = FakeRvc()
        self.render = FakeRender()
        self.upload = FakeUpload()
        self.spawned: list[FakeProc] = []
        self.answers: list[str | None] = []
        self.asked: list[tuple[str, str, str]] = []
        self.reconnects: list[str] = []
        self.reconnect_error: Exception | None = None
        self.extract_threads: list[str] = []
        self.confirm_calls: list[tuple[str, str]] = []
        self.confirm_answer = False
        # so a thread principal grava o take.json (spec 3.2)
        self.save_threads: list[str] = []
        original_save = Take.save

        def spy_save(take):
            self.save_threads.append(threading.current_thread().name)
            original_save(take)

        patcher = mock.patch.object(Take, "save", spy_save)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ---------- falsos ----------

    def spawn(self, argv, **kw):
        self.spawned.append(FakeProc(argv, kw))
        return self.spawned[-1]

    def ask(self, title, prompt, initial):
        self.asked.append((title, prompt, initial))
        return self.answers.pop(0) if self.answers else None

    def reconnect(self):
        self.reconnects.append(threading.current_thread().name)
        if self.reconnect_error:
            raise self.reconnect_error

    def extract(self, raw, out):
        self.extract_threads.append(threading.current_thread().name)
        return timeline.extract_aligned_audio(raw, out)

    def no_capture(self, *args):
        raise AssertionError("o painel de vídeo não pode abrir a câmera")

    def confirm(self, title, message):
        self.confirm_calls.append((title, message))
        return self.confirm_answer

    # ---------- montagem ----------

    def make_take(self, modo: str = "av", with_audio: bool = True, take_id: str = TAKE_ID) -> Take:
        take = Take(id=take_id, dir=os.path.join(self.rec_dir, take_id), modo=modo, status="gravado", mic=MIC)
        os.makedirs(take.dir)
        if modo == "av":
            shutil.copyfile(self.av_raw, take.raw_path)
            if with_audio:
                shutil.copyfile(self.av_audio, take.audio_path)
                take.video, take.audio_fit = dict(self.vi), dict(self.fit)
        else:
            shutil.copyfile(self.tone, take.raw_path)
        take.save()
        return take

    def converted(self, take: Take, key: str = "orochi", mp4: bool = False) -> str:
        # tomada ja convertida (e renderizada, com mp4=True), como o painel deixa
        shutil.copyfile(self.tone, take.path(f"{key}.wav"))
        shutil.copyfile(self.tone, take.path(f"{key}_IA.mp3"))
        take.saidas[key] = {"wav": f"{key}.wav", "mp3": f"{key}_IA.mp3"}
        take.status = "convertido"
        final = os.path.join(self.videos_dir, final_video_name(take.id, key))
        if mp4:
            os.makedirs(self.videos_dir, exist_ok=True)
            with open(final, "wb") as f:
                f.write(b"mp4 falso")
            take.saidas[key]["mp4"] = os.path.relpath(final, take.dir)
            take.status = "renderizado"
        take.save()
        return final

    def make_app(self, estado: dict | None = None, rvc_factory=None, **deps_kw) -> gui.App:
        if estado is not None:
            with open(self.estado_path, "w", encoding="utf-8") as f:
                json.dump(estado, f)
        root = tk.Tk()
        root.withdraw()
        hw = gui_capture.Hardware(list_cameras=lambda: [], list_mics=lambda: [Source(54, MIC)],
                                  free_bytes=lambda path: 50 * 1024**3, check_mic=lambda pid, idx: None)
        kw = dict(extract_aligned_audio=self.extract, render_final=self.render, upload_files=self.upload,
                  reconnect=self.reconnect, spawn=self.spawn, ask_link=self.ask, videos_dir=self.videos_dir)
        deps = gui_pipeline.PipelineDeps(**{**kw, **deps_kw})
        app = gui.App(root, rvc_factory=rvc_factory or (lambda: self.rvc), rec_dir=self.rec_dir,
                      estado_path=self.estado_path, log_path=self.log_path,
                      logs_dir=os.path.join(self.tmp.name, "logs"), ask_confirm=self.confirm,
                      capture_factory=self.no_capture, hardware=hw, pipeline_deps=deps)
        self.addCleanup(self.close_app, app)
        app.dispatch_events()                   # take_changed da abertura
        return app

    def close_app(self, app):
        if not app.closed:
            pump_until(app, lambda: not (app.gpu_jobs.busy or app.io_jobs.busy or app.upload_jobs.busy), 20)
            app.shutdown()
        self.assertLessEqual(set(self.save_threads), {"MainThread"})

    def log_text(self, app) -> str:
        return app.log_box.get("1.0", "end")

    def wait_done(self, app, timeout: float = 20.0) -> None:
        p = app.pipeline_panel
        self.assertTrue(pump_until(app, lambda: p.busy is None and p.uploading is None and not app.gpu_jobs.busy
                                   and not app.io_jobs.busy and not app.upload_jobs.busy, timeout),
                        self.log_text(app))

    # ---------- montagem da coluna ----------

    def test_sections_and_labels(self):
        app = self.make_app()
        p = app.pipeline_panel
        titles = [w.cget("text") for w in app.right.winfo_children() if isinstance(w, ttk.LabelFrame)]
        self.assertEqual(["1. Modelo", "2. Aumentar volume", "3. Converter", "4. Vídeo", "5. Drive"], titles)
        labels = [p.btn_boost, p.btn_convert, p.btn_play_rec, p.btn_play_out, p.btn_render, p.btn_cancel_render,
                  p.btn_watch, p.btn_open_folder, p.btn_upload, p.btn_drive_config, p.btn_reconnect,
                  p.btn_cancel_upload]
        self.assertEqual(["Aumentar volume", "Converter", "▶ Ouvir gravação", "▶ Ouvir resultado", "Gerar vídeo",
                          "Cancelar", "Assistir", "Abrir pasta", "Enviar", "Configurar Drive", "Reconectar",
                          "Cancelar"], [b.cget("text") for b in labels])
        self.assertIn("pipeline_render", gui.JOB_LABELS)
        # sem tomada: so o Drive e a pasta ficam liberados
        for b in (p.btn_boost, p.btn_convert, p.btn_play_rec, p.btn_play_out, p.btn_render, p.btn_cancel_render,
                  p.btn_watch, p.btn_upload, p.btn_cancel_upload):
            self.assertEqual("disabled", state(b), b.cget("text"))
        for b in (p.btn_open_folder, p.btn_drive_config, p.btn_reconnect):
            self.assertEqual("normal", state(b), b.cget("text"))
        self.assertIn("Nenhuma tomada", p.convert_label.cget("text"))
        self.assertIn("não configurada", p.drive_label.cget("text"))

    # ---------- converter e gerar o video ----------

    def test_convert_av_renders_automatically(self):
        take = self.make_take()
        app = self.make_app({"av_offset_ms": 40})
        p = app.pipeline_panel
        self.assertEqual("normal", state(p.btn_convert))
        self.assertEqual("disabled", state(p.btn_render))          # ainda nao convertido
        p.btn_convert.invoke()
        for b in (p.btn_convert, p.btn_boost, p.btn_render):
            self.assertEqual("disabled", state(b))
        self.wait_done(app)
        self.assertEqual([("job-gpu", take.audio_path, take.path("orochi.wav"), "orochi")], self.rvc.calls)
        (r,) = self.render.calls
        self.assertEqual("job-gpu", r.thread)
        self.assertIsNot(app.take, r.take)                         # o job recebe uma copia
        self.assertEqual(take.id, r.take.id)
        self.assertEqual(("orochi", take.path("orochi.wav"), 40, self.videos_dir),
                         (r.modelo.key, r.conv_wav, r.av_offset_ms, r.videos_dir))
        self.assertIsInstance(r.cancel, threading.Event)
        final = os.path.join(self.videos_dir, final_video_name(take.id, "orochi"))
        saved = Take.load(take.dir)
        self.assertEqual("renderizado", saved.status)
        self.assertEqual({"wav": "orochi.wav", "mp3": "orochi_IA.mp3",
                          "mp4": os.path.join("..", "..", "videos_finais", os.path.basename(final))},
                         saved.saidas["orochi"])
        self.assertEqual(saved.saidas, app.take.saidas)
        self.assertIn("IA", procs.media_info(take.path("orochi_IA.mp3"))["format"]["tags"]["comment"])
        self.assertIn(f"Pronto: {os.path.basename(final)}", p.video_label.cget("text"))
        for b in (p.btn_convert, p.btn_render, p.btn_watch, p.btn_upload, p.btn_play_out):
            self.assertEqual("normal", state(b), b.cget("text"))
        self.assertEqual("disabled", state(p.btn_cancel_render))
        self.assertIn(f"Voz convertida para Orochi: {take.id}", self.log_text(app))
        self.assertIn(f"Vídeo pronto: {os.path.basename(final)}", self.log_text(app))
        self.assertTrue(app.model_loaded)                          # a conversao ja carregou o modelo
        self.assertIn("carregado ✓", app.model_status.cget("text"))

    def test_audio_only_take_does_not_render(self):
        take = self.make_take(modo="audio")
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_convert.invoke()
        self.wait_done(app)
        self.assertEqual(take.raw_path, self.rvc.calls[0][1])
        self.assertEqual([], self.render.calls)
        saved = Take.load(take.dir)
        self.assertEqual("convertido", saved.status)
        self.assertEqual({"orochi": {"wav": "orochi.wav", "mp3": "orochi_IA.mp3"}}, saved.saidas)
        self.assertEqual("disabled", state(p.btn_render))
        self.assertEqual("disabled", state(p.btn_upload))
        self.assertEqual("normal", state(p.btn_play_out))
        self.assertIn("só de áudio", p.video_label.cget("text"))

    def test_recovered_take_extracts_audio_before_converting(self):
        take = self.make_take(with_audio=False)                    # como o recover_takes deixa
        self.assertFalse(os.path.exists(take.audio_path))
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_convert.invoke()
        self.wait_done(app)
        self.assertEqual(["job-io"], self.extract_threads)
        self.assertEqual(take.audio_path, self.rvc.calls[0][1])
        saved = Take.load(take.dir)
        self.assertEqual(self.vi["n_frames"], saved.video["n_frames"])
        self.assertIn("taxa_real", saved.audio_fit)
        self.assertEqual("renderizado", saved.status)
        self.assertEqual(saved.video, self.render.calls[0].take.video)
        self.assertIn(f"Áudio alinhado da tomada {take.id} preparado", self.log_text(app))

    def test_boost_then_convert_uses_boosted(self):
        take = self.make_take(modo="audio")
        app = self.make_app()
        p = app.pipeline_panel
        self.assertEqual(str(gui_pipeline.DEFAULT_GAIN_DB), p.gain_var.get())
        p.gain_var.set("abc")
        p.btn_boost.invoke()
        self.assertIn("Ganho inválido", self.log_text(app))
        self.assertIsNone(p.busy)
        p.gain_var.set("6")
        p.btn_boost.invoke()
        self.assertEqual("disabled", state(p.btn_convert))
        self.wait_done(app)
        self.assertTrue(os.path.exists(take.path("boosted.wav")))
        self.assertIn("Volume aumentado em 6 dB", self.log_text(app))
        self.assertIn("pico", p.volume_label.cget("text"))
        p.btn_convert.invoke()
        self.wait_done(app)
        self.assertEqual(take.path("boosted.wav"), self.rvc.calls[0][1])

    def test_render_failure_shows_message_and_reenables(self):
        take = self.make_take()
        self.render.mode = "fail"
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_convert.invoke()
        self.wait_done(app)
        saved = Take.load(take.dir)
        self.assertEqual(("falhou", FAIL_MSG), (saved.status, saved.erro))
        self.assertNotIn("mp4", saved.saidas["orochi"])
        self.assertIn(f"ERRO ao gerar o vídeo: {FAIL_MSG}", self.log_text(app))
        self.assertIn("Falhou", p.video_label.cget("text"))
        self.assertEqual(gui_pipeline.COLOR_BAD, str(p.video_label.cget("foreground")))
        for b in (p.btn_convert, p.btn_render, p.btn_boost):
            self.assertEqual("normal", state(b), b.cget("text"))
        for b in (p.btn_cancel_render, p.btn_watch, p.btn_upload):
            self.assertEqual("disabled", state(b), b.cget("text"))
        # Gerar video refaz so o video, sem converter de novo
        self.render.mode = "ok"
        p.btn_render.invoke()
        self.wait_done(app)
        self.assertEqual(1, len(self.rvc.calls))
        self.assertEqual(("renderizado", ""), (app.take.status, app.take.erro))
        self.assertIn("Pronto", p.video_label.cget("text"))

    def test_missing_wav_does_not_render(self):
        take = self.make_take()

        def mp3_and_lose_wav(wav, mp3, modelo):
            shutil.copyfile(wav, mp3)
            os.remove(wav)                                         # o WAV sumiu antes do render

        app = self.make_app(export_mp3=mp3_and_lose_wav)
        p = app.pipeline_panel
        p.btn_convert.invoke()
        self.wait_done(app)
        self.assertEqual([], self.render.calls)
        self.assertIn(f"ERRO ao gerar o vídeo: {gui_pipeline.MSG_NO_WAV}", self.log_text(app))
        self.assertIn(gui_pipeline.MSG_NO_WAV, p.video_label.cget("text"))
        self.assertEqual("normal", state(p.btn_convert))

    def test_cancel_render(self):
        take = self.make_take()
        self.converted(take)
        self.render.mode = "block"
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_render.invoke()
        self.assertTrue(self.render.started.wait(5))
        self.assertEqual("normal", state(p.btn_cancel_render))
        self.assertEqual("disabled", state(p.btn_render))
        p.btn_cancel_render.invoke()
        self.wait_done(app)
        self.assertTrue(self.render.calls[0].cancel.is_set())
        self.assertIn("Vídeo cancelado", self.log_text(app))
        self.assertNotIn("ERRO", self.log_text(app))
        self.assertEqual("convertido", Take.load(take.dir).status)
        self.assertEqual("normal", state(p.btn_render))
        self.assertEqual("disabled", state(p.btn_cancel_render))

    def test_dead_worker_asks_to_click_again_and_restarts(self):
        take = self.make_take(modo="audio")
        rvc_log = os.path.join(self.tmp.name, "rvc.log")
        app = self.make_app(rvc_factory=lambda: RvcClient(log_path=rvc_log, env={"STUDIO_RVC_FAKE": "1"}))
        p = app.pipeline_panel
        p.btn_convert.invoke()
        self.wait_done(app, timeout=30)
        self.assertEqual("convertido", Take.load(take.dir).status)
        pid = app.rvc._proc.pid
        # segura a fila gpu para o worker morrer entre o clique e o job
        gate = threading.Event()
        self.addCleanup(gate.set)
        app.gpu_jobs.submit("segura", gate.wait, 10)
        p.btn_convert.invoke()
        os.kill(pid, signal.SIGKILL)
        end = time.monotonic() + 5
        while not zombie(pid) and time.monotonic() < end:
            time.sleep(0.01)
        gate.set()
        self.wait_done(app)
        self.assertIn(gui_pipeline.MSG_WORKER_DIED, self.log_text(app))
        self.assertIn(gui_pipeline.MSG_WORKER_DIED, p.convert_label.cget("text"))
        self.assertEqual("normal", state(p.btn_convert))
        self.assertFalse(app.rvc.alive())
        p.btn_convert.invoke()                                     # recria o worker
        self.wait_done(app, timeout=30)
        self.assertNotEqual(pid, app.rvc._proc.pid)
        self.assertEqual("convertido", Take.load(take.dir).status)
        self.assertNotIn(gui_pipeline.MSG_WORKER_DIED, p.convert_label.cget("text"))

    def test_fake_worker_death_marks_take(self):
        take = self.make_take(modo="audio")
        self.rvc.die = True
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_convert.invoke()
        self.wait_done(app)
        self.assertEqual(1, self.rvc.starts)
        saved = Take.load(take.dir)
        self.assertEqual("falhou", saved.status)
        self.assertIn("fechou inesperadamente", saved.erro)
        p.btn_convert.invoke()
        self.wait_done(app)
        self.assertEqual(2, self.rvc.starts)
        self.assertEqual(("convertido", ""), (app.take.status, app.take.erro))

    # ---------- gravacao e troca de tomada ----------

    def test_recording_disables_processing_buttons(self):
        take = self.make_take()
        self.converted(take, mp4=True)
        app = self.make_app()
        p = app.pipeline_panel
        busy_while_recording = (p.btn_boost, p.btn_convert, p.btn_render, p.btn_play_rec, p.btn_play_out,
                                p.btn_watch)
        for b in busy_while_recording:
            self.assertEqual("normal", state(b), b.cget("text"))
        app.bus.post("recording", active=True, take=None)
        app.dispatch_events()
        for b in busy_while_recording:
            self.assertEqual("disabled", state(b), b.cget("text"))
        self.assertEqual("normal", state(p.btn_upload))           # o envio nao usa a camera nem o mic
        p.btn_convert.invoke()
        self.assertEqual([], self.rvc.calls)
        app.bus.post("recording", active=False, take=None)
        app.dispatch_events()
        for b in busy_while_recording:
            self.assertEqual("normal", state(b), b.cget("text"))

    def test_take_changed_updates_status_and_buttons(self):
        av = self.make_take()
        self.converted(av, mp4=True)
        app = self.make_app()
        p = app.pipeline_panel
        self.assertIn("Pronto", p.video_label.cget("text"))
        self.assertIn(av.id, p.convert_label.cget("text"))
        audio_take = self.make_take(modo="audio", take_id="2026-09-26_110000")
        app.set_take(audio_take)
        self.assertTrue(pump_until(app, lambda: "só de áudio" in p.video_label.cget("text")))
        self.assertIn(audio_take.id, p.convert_label.cget("text"))
        for b in (p.btn_render, p.btn_watch, p.btn_upload, p.btn_play_out):
            self.assertEqual("disabled", state(b), b.cget("text"))
        for b in (p.btn_convert, p.btn_boost, p.btn_play_rec):
            self.assertEqual("normal", state(b), b.cget("text"))
        app.set_take(None)
        self.assertTrue(pump_until(app, lambda: "Nenhuma tomada" in p.convert_label.cget("text")))
        for b in (p.btn_convert, p.btn_boost, p.btn_play_rec, p.btn_render):
            self.assertEqual("disabled", state(b), b.cget("text"))

    def test_model_change_updates_buttons(self):
        take = self.make_take()
        self.converted(take, mp4=True)
        app = self.make_app()
        p = app.pipeline_panel
        self.assertEqual("normal", state(p.btn_render))
        app.model_var.set("Silvio Santos")
        app.model_box.event_generate("<<ComboboxSelected>>")
        app.dispatch_events()
        for b in (p.btn_render, p.btn_play_out, p.btn_watch, p.btn_upload):
            self.assertEqual("disabled", state(b), b.cget("text"))
        self.assertIn("Silvio Santos", p.convert_label.cget("text"))

    # ---------- ouvir, assistir e abrir pasta ----------

    def test_players_and_openers(self):
        take = self.make_take()
        final = self.converted(take, mp4=True)
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_play_rec.invoke()
        p.btn_play_out.invoke()
        p.btn_watch.invoke()
        p.btn_open_folder.invoke()
        self.assertEqual([["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", take.audio_path],
                          ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", take.path("orochi.wav")],
                          ["xdg-open", final], ["xdg-open", self.videos_dir]], [s.argv for s in self.spawned])
        self.assertTrue(self.spawned[0].terminated)                # um som por vez
        self.assertFalse(self.spawned[1].terminated)
        for s in self.spawned:
            self.assertEqual(subprocess.DEVNULL, s.kw["stdout"])
            self.assertEqual(subprocess.DEVNULL, s.kw["stderr"])
        app.shutdown()
        self.assertTrue(self.spawned[1].terminated)                # fechar para o som

    # ---------- Drive ----------

    def test_upload_without_folder_opens_config_then_sends(self):
        take = self.make_take()
        final = self.converted(take, mp4=True)
        self.answers = [LINK]
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_upload.invoke()
        self.assertEqual(1, len(self.asked))
        with open(self.estado_path, encoding="utf-8") as f:
            self.assertEqual(LINK, json.load(f)["drive_pasta"])
        self.wait_done(app)
        (call,) = self.upload.calls
        self.assertEqual(("job-upload", [final], LINK, self.videos_dir),
                         (call.thread, call.paths, call.link, call.videos_dir))
        enviado = Take.load(take.dir).saidas["orochi"]["enviado"]
        self.assertEqual(MD5, enviado["md5"])
        self.assertRegex(enviado["quando"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
        self.assertEqual(enviado, app.take.saidas["orochi"]["enviado"])
        self.assertIn(f"Enviado pro Drive: {os.path.basename(final)}", self.log_text(app))
        self.assertEqual(100, float(p.progress.cget("value")))
        self.assertIn("Enviado", p.drive_label.cget("text"))

    def test_upload_dialog_cancelled_sends_nothing(self):
        take = self.make_take()
        self.converted(take, mp4=True)
        app = self.make_app()
        app.pipeline_panel.btn_upload.invoke()
        self.assertEqual(1, len(self.asked))
        app.root.update()
        self.assertEqual([], self.upload.calls)
        self.assertEqual("", app.estado["drive_pasta"])

    def test_invalid_link_is_rejected(self):
        app = self.make_app({"drive_pasta": LINK})
        p = app.pipeline_panel
        self.assertIn(FOLDER_ID, p.drive_label.cget("text"))
        self.answers = [f"https://drive.google.com/file/d/{FOLDER_ID}/view", "https://example.com/folders/x", None]
        p.btn_drive_config.invoke()
        self.assertEqual(3, len(self.asked))
        self.assertEqual(LINK, self.asked[0][2])                    # comeca com o link atual
        self.assertIn("Esse link é de um arquivo, não de uma pasta", self.asked[1][1])
        self.assertIn("Isso não é um link do Google Drive", self.asked[2][1])
        self.assertIn("Link do Drive recusado: Esse link é de um arquivo", self.log_text(app))
        self.assertEqual(LINK, app.estado["drive_pasta"])
        with open(self.estado_path, encoding="utf-8") as f:
            self.assertEqual(LINK, json.load(f)["drive_pasta"])
        # link valido: salva
        self.answers = [f"  https://drive.google.com/drive/u/1/folders/{FOLDER_ID}x  "]
        p.btn_drive_config.invoke()
        self.assertEqual(f"https://drive.google.com/drive/u/1/folders/{FOLDER_ID}x", app.estado["drive_pasta"])
        self.assertIn(f"Pasta do Drive configurada (ID {FOLDER_ID}x)", self.log_text(app))

    def test_progress_updates_bar(self):
        take = self.make_take()
        self.converted(take, mp4=True)
        self.upload.block = True
        app = self.make_app({"drive_pasta": LINK})
        p = app.pipeline_panel
        p.btn_upload.invoke()
        self.assertTrue(pump_until(app, lambda: float(p.progress.cget("value")) == 42))
        self.assertIn("42%", p.drive_label.cget("text"))
        for b in (p.btn_upload, p.btn_drive_config, p.btn_reconnect):
            self.assertEqual("disabled", state(b), b.cget("text"))
        self.assertEqual("normal", state(p.btn_cancel_upload))
        self.assertIn("Envio para o Drive em andamento", app.close_reasons())
        self.upload.gate.set()
        self.wait_done(app)
        self.assertEqual(100, float(p.progress.cget("value")))
        self.assertEqual("normal", state(p.btn_upload))
        self.assertEqual("disabled", state(p.btn_cancel_upload))

    def test_cancel_upload(self):
        take = self.make_take()
        self.converted(take, mp4=True)
        self.upload.block = True
        app = self.make_app({"drive_pasta": LINK})
        p = app.pipeline_panel
        p.btn_upload.invoke()
        self.assertTrue(pump_until(app, lambda: float(p.progress.cget("value")) == 42))
        p.btn_cancel_upload.invoke()
        self.wait_done(app)
        self.assertTrue(self.upload.calls[0].cancel.is_set())
        self.assertIn("Envio cancelado", self.log_text(app))
        self.assertNotIn("enviado", Take.load(take.dir).saidas["orochi"])
        self.assertEqual(0, float(p.progress.cget("value")))
        self.assertEqual("normal", state(p.btn_upload))

    def test_upload_error_is_shown(self):
        take = self.make_take()
        self.converted(take, mp4=True)
        self.upload.result = {"ok": False, "md5": "", "erro": drive.MSG_RELOGIN}
        app = self.make_app({"drive_pasta": LINK})
        p = app.pipeline_panel
        p.btn_upload.invoke()
        self.wait_done(app)
        self.assertIn(f"ERRO no envio: {drive.MSG_RELOGIN}", self.log_text(app))
        self.assertIn(drive.MSG_RELOGIN, p.drive_label.cget("text"))
        self.assertEqual(gui_pipeline.COLOR_BAD, str(p.drive_label.cget("foreground")))
        self.assertNotIn("enviado", Take.load(take.dir).saidas["orochi"])

    def test_reconnect(self):
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_reconnect.invoke()
        self.assertEqual("disabled", state(p.btn_reconnect))
        self.wait_done(app)
        self.assertEqual(["job-upload"], self.reconnects)
        self.assertIn("Drive reconectado", self.log_text(app))
        self.reconnect_error = drive.DriveError(drive.MSG_NO_RCLONE)
        p.btn_reconnect.invoke()
        self.wait_done(app)
        self.assertIn(f"ERRO ao reconectar o Drive: {drive.MSG_NO_RCLONE}", self.log_text(app))
        self.assertEqual("normal", state(p.btn_reconnect))

    # ---------- fechar ----------

    def test_close_asks_while_rendering_and_cancels(self):
        take = self.make_take()
        self.converted(take)
        self.render.mode = "block"
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_render.invoke()
        self.assertTrue(self.render.started.wait(5))
        app.on_close()                                            # respondeu "nao"
        self.assertFalse(app.closed)
        self.assertIn("Gerando o vídeo", self.confirm_calls[0][1])
        self.assertFalse(self.render.calls[0].cancel.is_set())
        self.confirm_answer = True
        app.on_close()
        self.assertTrue(app.closed)
        self.assertTrue(self.render.calls[0].cancel.is_set())     # o ffmpeg para e o .part sai


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_pipeline -v`
Expected: ERROR ao importar o módulo de teste, com `ImportError: cannot import name 'gui_pipeline' from 'studio'`

- [ ] **Step 3: Implementar `studio/gui_pipeline.py`**

`studio/gui_pipeline.py`:

```python
"""Coluna direita, etapas 2 a 5: aumentar volume, converter (RVC), gerar o video e enviar pro Drive.

So a thread principal mexe nos widgets e na Take (spec 3.2): os jobs recebem copias das entradas e devolvem
resultados, e o handler do job_ok aplica o resultado na Take e grava o take.json.
"""

import copy
import math
import os
import subprocess
import threading
import tkinter as tk
from dataclasses import asdict, dataclass
from datetime import datetime
from tkinter import simpledialog, ttk
from typing import Callable

from studio import audio, drive, procs, render, timeline
from studio.config import VIDEOS_DIR, Modelo
from studio.events import Event, error_message
from studio.takes import Take

JOB_EXTRACT = "pipeline_extract"      # fila io (curto)
JOB_BOOST = "pipeline_boost"          # fila io (curto)
JOB_CONVERT = "pipeline_convert"      # fila gpu
JOB_RENDER = "pipeline_render"        # fila gpu
JOB_UPLOAD = "pipeline_upload"        # fila upload
JOB_RECONNECT = "pipeline_reconnect"  # fila upload
LABEL_RENDER = "Gerando o vídeo"
LABEL_UPLOAD = "Envio para o Drive em andamento"
# texto de cada job na confirmacao ao fechar (o gui.py junta com os seus)
PIPELINE_JOB_LABELS = {JOB_EXTRACT: "Preparando o áudio da tomada", JOB_BOOST: "Aumentando o volume",
                       JOB_CONVERT: "Convertendo a voz", JOB_RENDER: LABEL_RENDER, JOB_UPLOAD: LABEL_UPLOAD,
                       JOB_RECONNECT: "Reconectando o Drive"}
BUSY_TEXT = {JOB_EXTRACT: "Preparando o áudio da tomada…", JOB_BOOST: "Aumentando o volume…",
             JOB_CONVERT: "Convertendo para {label}… (a 1ª vez carrega o modelo)",
             JOB_RENDER: "Gerando o vídeo…"}
EV_PROGRESS = "drive_progress"        # {nome, fracao}, postado pela thread do envio

DEFAULT_GAIN_DB = 15
MIN_GAIN_DB, MAX_GAIN_DB = 1, 40
RECONNECT_TIMEOUT_S = 300
BOOSTED = "boosted.wav"
FFPLAY = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error"]
OPENER = "xdg-open"
MIN_WRAP = 120
COLOR_BAD, COLOR_BUSY, COLOR_OK, COLOR_TEXT = "#a33", "#a80", "#2a2", "#333"
TITLE_DRIVE = "Configurar Drive"
MSG_ASK_LINK = "Cole o link da pasta do Google Drive compartilhada com você (Compartilhar → Copiar link):"
MSG_WORKER_DIED = "Conversor reiniciado — clique Converter de novo"
MSG_BAD_GAIN = f"Ganho inválido: use de {MIN_GAIN_DB} a {MAX_GAIN_DB} dB (ex.: {DEFAULT_GAIN_DB})"
MSG_RECONNECT_TIMEOUT = "O login do Drive não terminou em 5 min — clique Reconectar de novo"
MSG_NO_WAV = "Áudio convertido não encontrado — clique Converter de novo"


def parse_gain(text: str) -> float:
    try:
        gain = float(text.strip().replace(",", "."))
    except ValueError:
        raise ValueError(MSG_BAD_GAIN) from None
    if not (math.isfinite(gain) and MIN_GAIN_DB <= gain <= MAX_GAIN_DB):
        raise ValueError(MSG_BAD_GAIN)
    return gain


def _num(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def _db(x: float | None) -> str:
    return "—" if x is None else f"{x:.1f} dB".replace(".", ",")


# ---------- jobs (threads de trabalho: nao tocam no Tk nem na Take) ----------

def reconnect_drive(run=procs.run, timeout: float = RECONNECT_TIMEOUT_S) -> None:
    # refaz o login do remote (abre o navegador); spec 9.3
    if drive.rclone_bin() is None:
        raise drive.DriveError(drive.MSG_NO_RCLONE)
    try:
        r = run(drive.reconnect_cmd(), timeout=timeout)
    except subprocess.TimeoutExpired:
        raise drive.DriveError(MSG_RECONNECT_TIMEOUT) from None
    if r.returncode != 0:
        raise drive.DriveError(drive.classify_error(r.returncode, r.stderr or ""))


def boost_job(boost, detect, inp: str, out: str, gain_db: float) -> tuple[float | None, float | None]:
    boost(inp, out, gain_db)
    return detect(out)


def convert_job(rvc, export_mp3, inp: str, wav: str, mp3: str, modelo: Modelo) -> dict:
    info = rvc.convert(inp, wav, modelo.key)
    export_mp3(wav, mp3, modelo)
    return info


def upload_job(upload_files, bus, mp4: str, link: str, videos_dir: str, cancel: threading.Event) -> dict:
    def progress(nome: str, fracao: float) -> None:
        bus.post(EV_PROGRESS, nome=nome, fracao=fracao)

    results = upload_files([mp4], link, on_progress=progress, cancel=cancel, videos_dir=videos_dir)
    if not results:           # cancelado antes de comecar
        raise drive.DriveError(drive.MSG_CANCELLED)
    return results[0]


@dataclass(frozen=True)
class PipelineDeps:
    # o que o painel chama fora do processo da GUI; os testes injetam falsos (sem GPU, Drive nem som)
    boost_volume: Callable = audio.boost_volume
    volumedetect: Callable = audio.volumedetect
    export_mp3: Callable = audio.export_mp3
    extract_aligned_audio: Callable = timeline.extract_aligned_audio
    render_final: Callable = render.render_final
    upload_files: Callable = drive.upload_files
    reconnect: Callable[[], None] = reconnect_drive
    spawn: Callable = procs.spawn
    ask_link: Callable | None = None      # (titulo, texto, inicial) -> str | None; None = simpledialog
    videos_dir: str = VIDEOS_DIR


class PipelinePanel:
    def __init__(self, app, parent):
        self.app = app
        self.root = app.root
        self.deps: PipelineDeps = app.pipeline_deps or PipelineDeps()
        self.busy: str | None = None           # etapa rodando: JOB_EXTRACT, JOB_BOOST, JOB_CONVERT ou JOB_RENDER
        self.uploading: str | None = None      # JOB_UPLOAD ou JOB_RECONNECT
        self.recording = False
        self._ctx: dict = {}                   # tomada, modelo e extras da etapa em andamento
        self._up: dict = {}                    # tomada, modelo e arquivo do envio em andamento
        self._errors: dict[str, str] = {}      # secao -> ultimo erro (texto do rotulo)
        self._volume: dict[str, tuple] = {}    # take.id -> (media, pico) depois do ganho
        self._progress_text = ""
        self._player = None
        self._opened: list = []
        self._build(parent)

        app.on("take_changed", self._on_take_changed)
        app.on("model_changed", self._on_take_changed)
        app.on("recording", self._on_recording)
        app.on("job_ok", self._on_job)
        app.on("job_fail", self._on_job)
        app.on(EV_PROGRESS, self._on_progress)
        app.register_closer(self._render_reason)
        app.register_closer(self._upload_reason)
        app.add_shutdown_hook(self._on_shutdown)
        self.refresh()

    # ---------- layout ----------

    def _build(self, parent) -> None:
        # compacto: a coluna direita inteira cabe acima do log em 1100x720
        f = self.volume_frame = self.app.add_section("2. Aumentar volume", parent)
        row = self._row(f, last=True)
        ttk.Label(row, text="Ganho (dB):").pack(side="left")
        self.gain_var = tk.StringVar(master=self.root, value=str(DEFAULT_GAIN_DB))
        self.gain_entry = ttk.Entry(row, textvariable=self.gain_var, width=5)
        self.gain_entry.pack(side="left", padx=6)
        self.btn_boost = self._button(row, "Aumentar volume", self.on_boost)
        self.volume_label = self._status(row, side="left")

        f = self.convert_frame = self.app.add_section("3. Converter", parent)
        row = self._row(f)
        self.btn_convert = self._button(row, "Converter", self.on_convert)
        self.btn_play_rec = self._button(row, "▶ Ouvir gravação", self.on_play_recording)
        self.btn_play_out = self._button(row, "▶ Ouvir resultado", self.on_play_result)
        self.convert_label = self._status(f)

        f = self.video_frame = self.app.add_section("4. Vídeo", parent)
        row = self._row(f)
        self.btn_render = self._button(row, "Gerar vídeo", self.on_render)
        self.btn_cancel_render = self._button(row, "Cancelar", self.on_cancel_render)
        self.btn_watch = self._button(row, "Assistir", self.on_watch)
        self.btn_open_folder = self._button(row, "Abrir pasta", self.on_open_folder)
        self.video_label = self._status(f)

        f = self.drive_frame = self.app.add_section("5. Drive", parent)
        row = self._row(f)
        self.btn_upload = self._button(row, "Enviar", self.on_upload)
        self.btn_drive_config = self._button(row, "Configurar Drive", self.configure_drive)
        self.btn_reconnect = self._button(row, "Reconectar", self.on_reconnect)
        self.btn_cancel_upload = ttk.Button(row, text="Cancelar", command=self.on_cancel_upload)
        self.btn_cancel_upload.pack(side="right")
        self.progress = ttk.Progressbar(row, maximum=100, mode="determinate", length=80)
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.drive_label = self._status(f)

    @staticmethod
    def _row(parent, last: bool = False) -> ttk.Frame:
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=(4, 6 if last else 0))
        return row

    @staticmethod
    def _button(row, text: str, command) -> ttk.Button:
        btn = ttk.Button(row, text=text, command=command)
        btn.pack(side="left", padx=(0, 6))
        return btn

    @staticmethod
    def _status(parent, side: str = "top") -> ttk.Label:
        # quebra a linha na largura real (a janela pode encolher ate MIN_SIZE)
        label = ttk.Label(parent, text="", justify="left")
        if side == "top":
            label.pack(anchor="w", fill="x", padx=8, pady=(2, 6))
        else:
            label.pack(side=side, fill="x", expand=True)
        label.bind("<Configure>", lambda e: label.configure(wraplength=max(MIN_WRAP, e.width - 4)))
        return label

    # ---------- estado da tomada ----------

    @staticmethod
    def _has_source(take: Take) -> bool:
        # A/V recuperada sem audio.wav ainda serve: o audio e extraido antes
        if take.status == "gravando":
            return False
        return os.path.isfile(take.audio_path) or (take.modo == "av" and os.path.isfile(take.raw_path))

    @staticmethod
    def _recording_audio(take: Take | None) -> str | None:
        if take is None:
            return None
        for path in (take.audio_path, take.raw_path):
            if os.path.isfile(path):
                return path
        return None

    @staticmethod
    def _saida(take: Take | None, m: Modelo) -> dict:
        saida = take.saidas.get(m.key) if take is not None else None
        return saida if isinstance(saida, dict) else {}

    def _output(self, take: Take | None, m: Modelo, key: str) -> str | None:
        # caminho absoluto de saidas[modelo][key] ("mp4" e relativo a pasta da tomada), se existir
        name = self._saida(take, m).get(key)
        if not name:
            return None
        path = os.path.normpath(os.path.join(take.dir, name))
        return path if os.path.isfile(path) else None

    def _av_offset(self) -> int:
        value = self.app.estado.get("av_offset_ms", 0)
        try:
            return int(value)
        except (TypeError, ValueError):
            self.app.log(f"AVISO: av_offset_ms inválido no estado.json ({value!r}); usando 0")
            return 0

    # ---------- botoes e rotulos ----------

    def refresh(self) -> None:
        take, m = self.app.take, self.app.modelo()
        ready = take is not None and not self.recording
        free = ready and self.busy is None
        wav, mp4 = self._output(take, m, "wav"), self._output(take, m, "mp4")
        source = free and self._has_source(take)
        self._enable(self.btn_boost, source)
        self._enable(self.btn_convert, source)
        self._enable(self.btn_play_rec, ready and self._recording_audio(take) is not None)
        self._enable(self.btn_play_out, ready and wav is not None)
        self._enable(self.btn_render, free and take.modo == "av" and wav is not None)
        self._enable(self.btn_cancel_render, self.busy == JOB_RENDER)
        self._enable(self.btn_watch, ready and mp4 is not None)
        self._enable(self.btn_upload, self.uploading is None and mp4 is not None)
        self._enable(self.btn_drive_config, self.uploading is None)
        self._enable(self.btn_reconnect, self.uploading is None)
        self._enable(self.btn_cancel_upload, self.uploading == JOB_UPLOAD)
        self._set(self.volume_label, *self._volume_text(take))
        self._set(self.convert_label, *self._convert_text(take, m, wav))
        self._set(self.video_label, *self._video_text(take, m, wav, mp4))
        self._set(self.drive_label, *self._drive_text(take, m))

    @staticmethod
    def _enable(widget, on: bool) -> None:
        widget.configure(state="normal" if on else "disabled")

    @staticmethod
    def _set(label, text: str, color: str) -> None:
        label.configure(text=text, foreground=color)

    def _busy_in(self, section: str) -> str | None:
        # texto da etapa rodando, so na secao dela (a extracao aparece na secao de quem pediu)
        if self.busy is None:
            return None
        where = {JOB_BOOST: "volume", JOB_CONVERT: "convert", JOB_RENDER: "video"}.get(self.busy)
        if where is None:
            where = "volume" if self._ctx.get("then") == JOB_BOOST else "convert"
        if where != section:
            return None
        text = BUSY_TEXT[self.busy].format(label=self._ctx["modelo"].label)
        take = self._ctx["take"]
        return text if take is self.app.take else f"{text} (tomada {take.id})"

    def _volume_text(self, take: Take | None) -> tuple[str, str]:
        busy = self._busy_in("volume")
        if busy:
            return busy, COLOR_BUSY
        if "volume" in self._errors:
            return self._errors["volume"], COLOR_BAD
        if take is None:
            return "", COLOR_TEXT
        if os.path.isfile(take.path(BOOSTED)):
            if take.id in self._volume:
                mean, peak = self._volume[take.id]
                return f"{BOOSTED}: média {_db(mean)}, pico {_db(peak)}", COLOR_OK
            return f"A conversão usa o {BOOSTED}", COLOR_OK
        return "Opcional: se a voz ficou baixa", COLOR_TEXT

    def _convert_text(self, take: Take | None, m: Modelo, wav: str | None) -> tuple[str, str]:
        busy = self._busy_in("convert")
        if busy:
            return busy, COLOR_BUSY
        if "convert" in self._errors:
            return self._errors["convert"], COLOR_BAD
        if take is None:
            return "Nenhuma tomada — grave primeiro", COLOR_TEXT
        head = f"Tomada {take.id}"
        if not self._has_source(take):
            return f"{head}: {take.erro or 'gravação não encontrada'}", COLOR_BAD
        if take.status == "falhou" and take.erro:
            return f"{head}: falhou — {take.erro}", COLOR_BAD
        if wav:
            return f"{head}: convertida para {m.label} ✓", COLOR_OK
        return f"{head}: pronta para converter para {m.label}", COLOR_TEXT

    def _video_text(self, take: Take | None, m: Modelo, wav: str | None, mp4: str | None) -> tuple[str, str]:
        busy = self._busy_in("video")
        if busy:
            return busy, COLOR_BUSY
        if "video" in self._errors:
            return self._errors["video"], COLOR_BAD
        if take is None:
            return "", COLOR_TEXT
        if take.modo != "av":
            return "Tomada só de áudio: sem vídeo", COLOR_TEXT
        if mp4:
            return f"Pronto: {os.path.basename(mp4)}", COLOR_OK
        if wav:
            return "Clique Gerar vídeo para gerar o MP4", COLOR_TEXT
        return "O vídeo sai sozinho depois de converter", COLOR_TEXT

    def _drive_text(self, take: Take | None, m: Modelo) -> tuple[str, str]:
        if self.uploading == JOB_RECONNECT:
            return "Reconectando: conclua o login no navegador…", COLOR_BUSY
        if self.uploading == JOB_UPLOAD:
            return self._progress_text, COLOR_BUSY
        if "drive" in self._errors:
            return self._errors["drive"], COLOR_BAD
        link = self.app.estado.get("drive_pasta") or ""
        if not link:
            return "Pasta do Drive não configurada — clique Configurar Drive", COLOR_TEXT
        try:
            folder_id = drive.parse_folder_link(link)[0]
        except drive.DriveError as e:
            return f"Link da pasta inválido ({e}) — clique Configurar Drive", COLOR_BAD
        sent = self._saida(take, m).get("enviado")
        if isinstance(sent, dict) and sent.get("md5"):
            return f"Enviado em {str(sent.get('quando', '')).replace('T', ' ')} (pasta {folder_id})", COLOR_OK
        return f"Pasta: {folder_id}", COLOR_TEXT

    # ---------- eventos ----------

    def _on_take_changed(self, ev: Event) -> None:
        # tomada ou modelo novo: os erros da etapa anterior nao valem mais
        for section in ("volume", "convert", "video"):
            self._errors.pop(section, None)
        self.refresh()

    def _on_recording(self, ev: Event) -> None:
        self.recording = bool(ev.data.get("active"))
        self.refresh()

    def _on_progress(self, ev: Event) -> None:
        if self.uploading != JOB_UPLOAD:
            return
        pct = round(100 * max(0.0, min(1.0, float(ev.data["fracao"]))))
        self.progress.configure(value=pct)
        self._progress_text = f"Enviando {ev.data['nome']}… {pct}%"
        self._set(self.drive_label, self._progress_text, COLOR_BUSY)

    def _on_job(self, ev: Event) -> None:
        handlers = {JOB_EXTRACT: self._extracted, JOB_BOOST: self._boosted, JOB_CONVERT: self._converted,
                    JOB_RENDER: self._rendered, JOB_UPLOAD: self._uploaded, JOB_RECONNECT: self._reconnected}
        job = ev.data["job"]
        handler = handlers.get(job)
        if handler is None:
            return
        # reabilita antes de aplicar: um erro no handler nao deixa botao preso
        if job in (JOB_UPLOAD, JOB_RECONNECT):
            self.uploading = None
        else:
            self.busy = None
        ok = ev.kind == "job_ok"
        try:
            handler(ok, ev.data.get("result") if ok else ev.data.get("message", ""))
        finally:
            self.refresh()

    # ---------- 2 e 3: volume e conversao ----------

    def _can_process(self, take: Take | None) -> bool:
        return take is not None and not self.recording and self.busy is None and self._has_source(take)

    def on_boost(self) -> None:
        take = self.app.take
        if not self._can_process(take):
            return
        try:
            gain = parse_gain(self.gain_var.get())
        except ValueError as e:
            self.app.log(str(e))
            self._errors["volume"] = str(e)
            self.refresh()
            return
        self._errors.pop("volume", None)
        self._prepare(take, JOB_BOOST, gain=gain)
        self.refresh()

    def on_convert(self) -> None:
        take = self.app.take
        if not self._can_process(take):
            return
        for section in ("convert", "video"):
            self._errors.pop(section, None)
        self._prepare(take, JOB_CONVERT)
        self.refresh()

    def _prepare(self, take: Take, then: str, **extra) -> None:
        # tomada A/V recuperada (crash ou fechar gravando) nao tem audio.wav: extrai antes, na fila io
        m = self.app.modelo()
        if take.modo == "av" and not os.path.isfile(take.audio_path):
            if self._submit(self.app.io_jobs, JOB_EXTRACT, self.deps.extract_aligned_audio,
                            take.raw_path, take.audio_path):
                self._ctx = {"take": take, "modelo": m, "then": then, **extra}
                self.busy = JOB_EXTRACT
                self.app.log(f"Preparando o áudio alinhado da tomada {take.id}…")
            return
        if then == JOB_BOOST:
            self._start_boost(take, m, extra["gain"])
        else:
            self._start_convert(take, m)

    def _extracted(self, ok: bool, value) -> None:
        ctx = self._ctx
        take = ctx["take"]
        if not ok:
            section = "volume" if ctx["then"] == JOB_BOOST else "convert"
            self._failed(take, section, f"ERRO ao preparar o áudio da tomada {take.id}", value, mark=True)
            return
        vi, fit = value
        take.video, take.audio_fit = asdict(vi), asdict(fit)
        take.save()
        self.app.log(f"Áudio alinhado da tomada {take.id} preparado")
        if ctx["then"] == JOB_BOOST:
            self._start_boost(take, ctx["modelo"], ctx["gain"])
        else:
            self._start_convert(take, ctx["modelo"])

    def _start_boost(self, take: Take, m: Modelo, gain: float) -> None:
        # sempre a partir da gravacao original: aumentar de novo nao acumula ganho
        if self._submit(self.app.io_jobs, JOB_BOOST, boost_job, self.deps.boost_volume, self.deps.volumedetect,
                        take.audio_path, take.path(BOOSTED), gain):
            self._ctx = {"take": take, "modelo": m, "then": None, "gain": gain}
            self.busy = JOB_BOOST
            self.app.log(f"Aumentando o volume em {_num(gain)} dB ({take.id})…")

    def _boosted(self, ok: bool, value) -> None:
        take, gain = self._ctx["take"], self._ctx["gain"]
        if not ok:
            self._failed(take, "volume", "ERRO ao aumentar o volume", value, mark=False)
            return
        mean, peak = value
        self._volume[take.id] = (mean, peak)
        self.app.log(f"Volume aumentado em {_num(gain)} dB → {BOOSTED} (média {_db(mean)}, pico {_db(peak)})")

    def _start_convert(self, take: Take, m: Modelo) -> None:
        try:
            rvc = self.app.ensure_rvc()      # thread principal; recria o worker que morreu
        except Exception as e:
            self.app.report_error(e, "iniciar o conversor")
            self._errors["convert"] = f"Falhou: não foi possível iniciar o conversor ({error_message(e)})"
            return
        inp = take.path(BOOSTED) if os.path.isfile(take.path(BOOSTED)) else take.audio_path
        wav, mp3 = take.path(f"{m.key}.wav"), take.path(f"{m.key}_IA.mp3")
        if self._submit(self.app.gpu_jobs, JOB_CONVERT, convert_job, rvc, self.deps.export_mp3, inp, wav, mp3, m):
            self._ctx = {"take": take, "modelo": m, "then": None, "rvc": rvc}
            self.busy = JOB_CONVERT
            self.app.log(f"Convertendo {os.path.basename(inp)} ({take.id}) para {m.label}…")

    def _converted(self, ok: bool, value) -> None:
        ctx = self._ctx
        take, m = ctx["take"], ctx["modelo"]
        if not ok:
            dead = not ctx["rvc"].alive()
            self._failed(take, "convert", "ERRO na conversão", value, mark=True)
            if dead:
                # o proximo clique chama ensure_rvc(), que sobe um worker novo
                self.app.log(MSG_WORKER_DIED)
                self._errors["convert"] = MSG_WORKER_DIED
            return
        saida = self._saida(take, m)
        saida.update(wav=f"{m.key}.wav", mp3=f"{m.key}_IA.mp3")
        take.saidas[m.key] = saida
        take.status, take.erro = "convertido", ""
        take.save()
        self._mark_model_loaded()
        self.app.log(f"Voz convertida para {m.label}: {take.id} ({saida['wav']} e {saida['mp3']})")
        if take.modo == "av":
            self._start_render(take, m)       # o video sai sozinho depois de converter

    def _mark_model_loaded(self) -> None:
        # a conversao carrega o Applio no worker: "Carregar modelo" nao faz mais falta
        if not self.app.model_loaded:
            self.app.model_loaded = True
            self.app.model_status.configure(text="Status: carregado ✓", foreground=COLOR_OK)
            self.app.btn_load.configure(state="disabled")

    # ---------- 4: video ----------

    def on_render(self) -> None:
        take, m = self.app.take, self.app.modelo()
        if (take is None or self.recording or self.busy is not None or take.modo != "av"
                or self._output(take, m, "wav") is None):
            return
        self._errors.pop("video", None)
        self._start_render(take, m)
        self.refresh()

    def _start_render(self, take: Take, m: Modelo) -> None:
        wav = self._output(take, m, "wav")
        if wav is None:
            self._failed(take, "video", "ERRO ao gerar o vídeo", MSG_NO_WAV, mark=False)
            return
        cancel = threading.Event()
        # o job recebe uma copia da Take: a thread principal pode mexer na original enquanto isso
        if self._submit(self.app.gpu_jobs, JOB_RENDER, self.deps.render_final, copy.deepcopy(take), m, wav,
                        av_offset_ms=self._av_offset(), videos_dir=self.deps.videos_dir, cancel=cancel):
            self._ctx = {"take": take, "modelo": m, "then": None, "cancel": cancel}
            self.busy = JOB_RENDER
            self.app.log(f"Gerando o vídeo de {take.id} ({m.label})…")

    def on_cancel_render(self) -> None:
        cancel = self._ctx.get("cancel") if self.busy == JOB_RENDER else None
        if cancel is not None and not cancel.is_set():
            cancel.set()
            self.app.log("Cancelando o vídeo…")
            self._set(self.video_label, "Cancelando…", COLOR_BUSY)

    def _rendered(self, ok: bool, value) -> None:
        ctx = self._ctx
        take, m = ctx["take"], ctx["modelo"]
        if not ok:
            if ctx["cancel"].is_set():
                self.app.log(f"Vídeo cancelado ({take.id})")
                return
            self._failed(take, "video", "ERRO ao gerar o vídeo", value, mark=True)
            return
        saida = self._saida(take, m)
        saida["mp4"] = os.path.relpath(value, take.dir)
        saida.pop("enviado", None)            # o arquivo mudou: ainda nao foi enviado
        take.saidas[m.key] = saida
        take.status, take.erro = "renderizado", ""
        take.save()
        self.app.log(f"Vídeo pronto: {os.path.basename(value)} (em {os.path.dirname(value)})")

    # ---------- 5: Drive ----------

    def on_upload(self) -> None:
        take, m = self.app.take, self.app.modelo()
        mp4 = self._output(take, m, "mp4")
        if self.uploading is not None or mp4 is None:
            return
        link = self.app.estado.get("drive_pasta") or self.configure_drive()
        if not link:
            return
        cancel = threading.Event()
        if not self._submit(self.app.upload_jobs, JOB_UPLOAD, upload_job, self.deps.upload_files, self.app.bus,
                            mp4, link, self.deps.videos_dir, cancel):
            return
        self._up = {"take": take, "modelo": m, "mp4": mp4, "cancel": cancel}
        self.uploading = JOB_UPLOAD
        self._errors.pop("drive", None)
        self._progress_text = f"Enviando {os.path.basename(mp4)}…"
        self.progress.configure(value=0)
        self.app.log(f"Enviando pro Drive: {os.path.basename(mp4)}")
        self.refresh()

    def on_cancel_upload(self) -> None:
        cancel = self._up.get("cancel") if self.uploading == JOB_UPLOAD else None
        if cancel is not None and not cancel.is_set():
            cancel.set()
            self.app.log("Cancelando o envio…")
            self._set(self.drive_label, "Cancelando o envio…", COLOR_BUSY)

    def _uploaded(self, ok: bool, value) -> None:
        take, m, name = self._up["take"], self._up["modelo"], os.path.basename(self._up["mp4"])
        res = value if ok else {"ok": False, "erro": value}
        if res.get("ok"):
            self.progress.configure(value=100)
            if res.get("md5"):
                saida = self._saida(take, m)
                saida["enviado"] = {"md5": res["md5"], "quando": datetime.now().isoformat(timespec="seconds")}
                take.saidas[m.key] = saida
                take.save()
            what = "Já estava igual no Drive (pulado)" if res.get("pulado") else "Enviado pro Drive"
            self.app.log(f"{what}: {name}")
            if res.get("aviso"):
                self.app.log(f"AVISO: {res['aviso']}")
            return
        self.progress.configure(value=0)
        erro = res.get("erro") or "Falha desconhecida no envio"
        if erro == drive.MSG_CANCELLED:
            self.app.log(f"Envio cancelado: {name}")
            return
        self.app.log(f"ERRO no envio: {erro}")
        self._errors["drive"] = f"Falhou: {erro}"

    def configure_drive(self) -> str | None:
        # pede o link ate ser valido (ou Cancelar); salva no estado.json
        if self.uploading is not None:
            return None
        ask = self.deps.ask_link or self._ask_link
        prompt, initial = MSG_ASK_LINK, self.app.estado.get("drive_pasta") or ""
        while True:
            text = ask(TITLE_DRIVE, prompt, initial)
            if text is None:
                return None
            try:
                folder_id, _rk = drive.parse_folder_link(text)
            except drive.DriveError as e:
                self.app.log(f"Link do Drive recusado: {e}")
                prompt, initial = f"{e}.\n\n{MSG_ASK_LINK}", text
                continue
            link = text.strip()
            self.app.update_estado(drive_pasta=link)
            self._errors.pop("drive", None)
            self.app.log(f"Pasta do Drive configurada (ID {folder_id})")
            self.refresh()
            return link

    def _ask_link(self, title: str, prompt: str, initial: str) -> str | None:
        return simpledialog.askstring(title, prompt, initialvalue=initial, parent=self.root)

    def on_reconnect(self) -> None:
        if self.uploading is not None:
            return
        if not self._submit(self.app.upload_jobs, JOB_RECONNECT, self.deps.reconnect):
            return
        self.uploading = JOB_RECONNECT
        self._errors.pop("drive", None)
        self.app.log("Reconectando o Drive: conclua o login no navegador que vai abrir (até 5 min)…")
        self.refresh()

    def _reconnected(self, ok: bool, value) -> None:
        if ok:
            self.app.log("Drive reconectado")
            return
        self.app.log(f"ERRO ao reconectar o Drive: {value}")
        self._errors["drive"] = f"Falhou: {value}"

    # ---------- ouvir, assistir, abrir pasta ----------

    def on_play_recording(self) -> None:
        if not self.recording:
            self._play(self._recording_audio(self.app.take))

    def on_play_result(self) -> None:
        if not self.recording:
            self._play(self._output(self.app.take, self.app.modelo(), "wav"))

    def _play(self, path: str | None) -> None:
        if not path:
            return
        self._stop_player()                   # um som por vez
        self._player = self._spawn([*FFPLAY, path])

    def _stop_player(self) -> None:
        player, self._player = self._player, None
        if player is not None and player.poll() is None:
            try:
                player.terminate()
            except OSError:
                pass

    def on_watch(self) -> None:
        mp4 = self._output(self.app.take, self.app.modelo(), "mp4")
        if mp4 and not self.recording:
            self._spawn([OPENER, mp4])

    def on_open_folder(self) -> None:
        os.makedirs(self.deps.videos_dir, exist_ok=True)
        self._spawn([OPENER, self.deps.videos_dir])

    def _spawn(self, argv: list[str]):
        self._opened = [p for p in self._opened if p.poll() is None]     # colhe os que ja sairam
        try:
            p = self.deps.spawn(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            self.app.log(f"Não foi possível abrir {argv[0]}: {error_message(e)}")
            return None
        self._opened.append(p)
        return p

    # ---------- falhas, fechar e utilitarios ----------

    def _failed(self, take: Take, section: str, what: str, message: str, mark: bool) -> None:
        # log + rotulo da secao; mark=True grava "falhou" no take.json (thread principal)
        self.app.log(f"{what}: {message}")
        if take is self.app.take:
            self._errors[section] = f"Falhou: {message}"
        if mark:
            take.status, take.erro = "falhou", message
            take.save()

    def _render_reason(self) -> str | None:
        return LABEL_RENDER if self.busy == JOB_RENDER else None

    def _upload_reason(self) -> str | None:
        return LABEL_UPLOAD if self.uploading == JOB_UPLOAD else None

    def _on_shutdown(self) -> None:
        # fechar mesmo assim: o render e o envio param (SIGTERM) e o .part sai; o som para
        for cancel in (self._ctx.get("cancel"), self._up.get("cancel")):
            if cancel is not None:
                cancel.set()
        self._stop_player()

    @staticmethod
    def _submit(runner, job: str, fn, *args, **kwargs) -> bool:
        try:
            runner.submit(job, fn, *args, **kwargs)
            return True
        except RuntimeError:                  # fila fechada: o app esta fechando
            return False
```

- [ ] **Step 4: Rodar e ver o próximo erro (o `App` ainda não monta o painel)**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_pipeline`
Expected: `Ran 24 tests` e `FAILED (errors=22)`. Os 2 testes de `ReconnectDriveTest` passam, e os 22 do painel dão
`TypeError: App.__init__() got an unexpected keyword argument 'pipeline_deps'`

- [ ] **Step 5: Montar o painel no `studio/gui.py`**

São seis trocas pequenas; o resto do arquivo (Tasks 9 e 10) fica igual.

1. O import do painel entra junto dos outros imports de `studio`. Em `studio/gui.py`, substituir isto:

```python
from studio.gui_capture import CAPTURE_JOB_LABELS, CapturePanel
from studio.rvc_client import RvcClient
```

por isto:

```python
from studio.gui_capture import CAPTURE_JOB_LABELS, CapturePanel
from studio.gui_pipeline import PIPELINE_JOB_LABELS, PipelinePanel
from studio.rvc_client import RvcClient
```

2. Os rótulos dos jobs do painel entram na confirmação ao fechar. Em `studio/gui.py`, substituir isto:

```python
JOB_LABELS = {JOB_LOAD_MODEL: "Carregando o modelo", **CAPTURE_JOB_LABELS}
```

por isto:

```python
JOB_LABELS = {JOB_LOAD_MODEL: "Carregando o modelo", **CAPTURE_JOB_LABELS, **PIPELINE_JOB_LABELS}
```

3. O `App` aceita as dependências do painel (os testes injetam falsos). Em `studio/gui.py`, substituir isto:

```python
                 ask_confirm=None, capture_factory=None, hardware=None):
        self.root = root
```

por isto:

```python
                 ask_confirm=None, capture_factory=None, hardware=None, pipeline_deps=None):
        self.root = root
```

4. Guarde-as no `App`, logo depois do `hardware`. Em `studio/gui.py`, substituir isto:

```python
        self.capture_factory = capture_factory
        self.hardware = hardware
        self.rvc = None
```

por isto:

```python
        self.capture_factory = capture_factory
        self.hardware = hardware
        # o que o painel de conversao/video/Drive chama fora do processo (None = os de verdade)
        self.pipeline_deps = pipeline_deps
        self.rvc = None
```

5. Monte o painel na coluna direita, depois do painel de captura e antes da abertura (assim ele recebe o `take_changed` da tomada inicial). Em `studio/gui.py`, substituir isto:

```python
        self.capture_panel = CapturePanel(self, self.left)
        self._startup(aviso_estado)
```

por isto:

```python
        self.capture_panel = CapturePanel(self, self.left)
        self.pipeline_panel = PipelinePanel(self, self.right)
        self._startup(aviso_estado)
```

6. O log passa a absorver a sobra e o aperto de altura. Com o peso na linha das colunas (Task 9), o `grid` encolhia a coluna direita: numa primeira versão do painel, a seção "5. Drive" recebia 42 dos 128 px pedidos e ficava cortada, e no tamanho mínimo (980×640) seria pior. Agora as colunas de controles ficam sempre inteiras e o log encolhe (≈ 9 linhas em 1100×720, ≈ 4 em 980×640). Em `studio/gui.py`, substituir isto:

```python
        outer.rowconfigure(0, weight=1)
```

por isto:

```python
        # o log absorve a sobra e o aperto: as colunas de controles ficam sempre inteiras
        outer.rowconfigure(1, weight=1)
```

- [ ] **Step 6: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_pipeline -v`
Expected: `OK` (24 testes, ~4,5 s)

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_shell tests.test_gui_capture`
Expected: `OK` (50 testes). O painel novo aparece nos testes das Tasks 9 e 10 sem tomada ou com tomadas sem saídas, e
nada roda sozinho.

Run: `./run_tests.sh`
Expected: `Ran 433 tests` e `OK (skipped=6)` (as 409 das Tasks 1–10 com as correções mais os 24 testes novos)

Run: `pgrep -a ffmpeg`
Expected: nenhuma linha

Run: `pgrep -af rvc_worker`
Expected: nenhuma linha (o teste com o worker de verdade fecha os dois processos junto com a janela)

- [ ] **Step 7: Conferir a janela (opcional, visual)**

Com a sessão desbloqueada, rode da raiz do repositório. Com a tela bloqueada o `ImageGrab` devolve tudo preto; nesse
caso rode o mesmo comando dentro de um Xephyr (`Xephyr :7 -screen 1120x740 -ac -nolisten tcp &`, depois `DISPLAY=:7`
na frente do comando e `kill %1` no fim). O script abre a janela com uma tomada sintética já renderizada, o `FakeUpload`
dos testes parado em 42 % e nenhum hardware, captura, clica Enviar, captura de novo e fecha:

```bash
Applio/.venv/bin/python - <<'EOF'
import json, os, tempfile, time, tkinter as tk
from PIL import ImageGrab
from studio import gui, gui_capture, gui_pipeline
from studio.devices import Source
from studio.takes import Take, final_video_name
from tests.test_gui_pipeline import LINK, FakeRender, FakeRvc, FakeUpload

tmp = tempfile.TemporaryDirectory()
rec, vids = os.path.join(tmp.name, "recordings"), os.path.join(tmp.name, "videos_finais")
take = Take(id="2026-09-26_101500", dir=os.path.join(rec, "2026-09-26_101500"), modo="av", status="renderizado",
            mic="alsa_input.usb-teste.mono")
os.makedirs(take.dir)
os.makedirs(vids)
for name in ("raw.mkv", "audio.wav", "orochi.wav", "orochi_IA.mp3"):
    open(take.path(name), "wb").close()          # vazios: o painel so confere se existem
final = os.path.join(vids, final_video_name(take.id, "orochi"))
open(final, "wb").close()
take.saidas = {"orochi": {"wav": "orochi.wav", "mp3": "orochi_IA.mp3", "mp4": os.path.relpath(final, take.dir)}}
take.save()
with open(os.path.join(tmp.name, "estado.json"), "w") as f:
    json.dump({"drive_pasta": LINK}, f)
upload = FakeUpload()
upload.block = True                              # para em 42 % ate o gate abrir
hw = gui_capture.Hardware(list_cameras=lambda: [], list_mics=lambda: [Source(54, "alsa_input.usb-teste.mono")],
                          free_bytes=lambda path: 50 * 1024**3, check_mic=lambda pid, idx: None)
deps = gui_pipeline.PipelineDeps(render_final=FakeRender(), upload_files=upload, reconnect=lambda: None,
                                 spawn=lambda argv, **kw: None, ask_link=lambda *a: None, videos_dir=vids)
root = tk.Tk()
app = gui.App(root, rvc_factory=FakeRvc, rec_dir=rec, estado_path=os.path.join(tmp.name, "estado.json"),
              log_path=os.path.join(tmp.name, "studio.log"), logs_dir=os.path.join(tmp.name, "logs"),
              ask_confirm=lambda t, m: True, capture_factory=lambda *a: None, hardware=hw, pipeline_deps=deps)
root.geometry("1100x720+0+0")
p = app.pipeline_panel


def grab(name):
    root.update()
    time.sleep(0.4)
    root.update()
    x0, y0 = root.winfo_rootx(), root.winfo_rooty()
    ImageGrab.grab(bbox=(x0, y0, x0 + root.winfo_width(), y0 + root.winfo_height())).save(name)


def send():
    grab("_shot_1.png")
    print("coluna direita inteira:", app.right.winfo_height() >= app.right.winfo_reqheight())
    p.btn_upload.invoke()
    root.after(800, finish)


def finish():
    grab("_shot_2.png")
    print("barra:", float(p.progress.cget("value")), "| Enviar:", str(p.btn_upload.cget("state")),
          "| Cancelar:", str(p.btn_cancel_upload.cget("state")))
    upload.gate.set()
    root.after(500, app.on_close)


root.after(1000, send)
root.mainloop()
tmp.cleanup()
EOF
```

Expected: `coluna direita inteira: True` e `barra: 42.0 | Enviar: disabled | Cancelar: normal`, e dois PNGs. Em
`_shot_1.png`, a coluna direita mostra, embaixo de "1. Modelo":
- "2. Aumentar volume", com "Ganho (dB): 15", o botão "Aumentar volume" e "Opcional: se a voz ficou baixa" na mesma
  linha;
- "3. Converter", com "Converter", "▶ Ouvir gravação", "▶ Ouvir resultado" e, em verde, "Tomada 2026-09-26_101500:
  convertida para Orochi ✓";
- "4. Vídeo", com "Gerar vídeo", "Cancelar" (cinza), "Assistir", "Abrir pasta" e, em verde, "Pronto:
  2026-09-26_101500_orochi_IA.mp4";
- "5. Drive", com "Enviar", "Configurar Drive", "Reconectar", a barra vazia, "Cancelar" (cinza) e "Pasta:
  1AbCdEfGhIjKlMnOpQrStUv".

O log fica embaixo, inteiro. Em `_shot_2.png`, "Enviar", "Configurar Drive" e "Reconectar" estão cinza, a barra está
em 42 %, "Cancelar" está ativo, o status diz "Enviando 2026-09-26_101500_orochi_IA.mp4… 42%" e o log mostra "Enviando
pro Drive: …". Olhe e apague (`rm _shot_*.png`); não versione as capturas.

- [ ] **Step 8: Commit**

```bash
git add studio/gui_pipeline.py studio/gui.py tests/test_gui_pipeline.py
git commit -m "feat(studio): painel de volume, conversao, video e Drive (render automatico, cancelar, progresso do envio)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

**Correções da revisão (Steps 9–28).** (1) **`av_offset_ms` da memória:** o render usava o valor lido na abertura,
e o `calibrar_av.py --salvar` feito com o app aberto só valia depois de reabrir. Agora o render lê o `estado.json` no
clique (a Task 9 já não desfaz o valor gravado por fora). (2) **Erro de conversão ou de vídeo marcava a tomada
inteira como "falhou":** Converter, Gerar vídeo e Enviar sumiam, o rótulo mostrava o erro do Orochi com o Silvio
escolhido, e na abertura seguinte a tomada era pulada. Agora o erro fica no modelo (`saidas[modelo]["erro"]`), o
status não muda ("falhou" fica só para a gravação), o rótulo mostra o erro do modelo escolhido e o sucesso limpa o
erro. (3) **Disco cheio no `take.json`:** um `Take.save` que falha no handler de sucesso deixava "convertida ✓" na
tela sem nada gravado e ainda disparava o vídeo. Agora o painel desfaz a mudança na memória, mostra o erro
("Disco cheio — libere espaço") e não segue para o vídeo. (4) **Enviar em dobro:** o diálogo do link roda o laço de
eventos, e um 2º clique em Enviar enfileirava outro envio. (5) **Buraco no áudio da tomada recuperada:** o mesmo
aviso da Task 10 (spec 6.3.3).

- [ ] **Step 9: Testes do `av_offset_ms` lido no clique e do `estado.json` com tipo errado (falha)**

Em `tests/test_gui_pipeline.py`, substituir isto:

```python
    def test_audio_only_take_does_not_render(self):
```

por isto:

```python
    def test_render_reads_av_offset_from_file(self):
        # calibrar_av.py --salvar com o app aberto: o render usa o valor do arquivo, e o app nao o desfaz
        take = self.make_take()
        self.converted(take)
        app = self.make_app({"av_offset_ms": 0})
        p = app.pipeline_panel
        with open(self.estado_path, encoding="utf-8") as f:
            estado = json.load(f)
        estado["av_offset_ms"] = 40
        with open(self.estado_path, "w", encoding="utf-8") as f:
            json.dump(estado, f)
        p.btn_render.invoke()
        self.wait_done(app)
        app.capture_panel.mic_box.event_generate("<<ComboboxSelected>>")     # o app grava o estado.json
        p.btn_render.invoke()
        self.wait_done(app)
        self.assertEqual([40, 40], [c.av_offset_ms for c in self.render.calls])
        with open(self.estado_path, encoding="utf-8") as f:
            self.assertEqual(40, json.load(f)["av_offset_ms"])

    def test_wrong_types_in_estado_open_with_defaults(self):
        # estado.json editado a mao com tipo errado: o app abre com os padroes e avisa (correcao da Task 1)
        take = self.make_take()
        self.converted(take)
        app = self.make_app({"drive_pasta": 123, "camera": 5, "av_offset_ms": "x"})
        p = app.pipeline_panel
        self.assertIn("AVISO: estado.json: valor inválido em camera, drive_pasta, av_offset_ms — usando o padrão",
                      self.log_text(app))
        self.assertIn("não configurada", p.drive_label.cget("text"))
        self.assertEqual("", app.capture_panel.camera_path())
        p.btn_render.invoke()
        self.wait_done(app)
        self.assertEqual(0, self.render.calls[0].av_offset_ms)

    def test_audio_only_take_does_not_render(self):
```

- [ ] **Step 10: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_pipeline`
Expected: `Ran 26 tests` e `FAILED (failures=1)`: `test_render_reads_av_offset_from_file` dá
`AssertionError: Lists differ: [40, 40] != [0, 40]` (o 1º render usou o valor da memória; o 2º já vem certo porque o
`update_estado` da Task 9 relê o arquivo). O `test_wrong_types_in_estado_open_with_defaults` já passa: ele fixa a
correção da Task 1.

- [ ] **Step 11: O render lê o `av_offset_ms` do arquivo**

Em `studio/gui_pipeline.py`, substituir isto:

```python
from studio.config import VIDEOS_DIR, Modelo
```

por isto:

```python
from studio.config import VIDEOS_DIR, Modelo, load_estado
```

E em `studio/gui_pipeline.py`, substituir isto:

```python
    def _av_offset(self) -> int:
        value = self.app.estado.get("av_offset_ms", 0)
        try:
            return int(value)
        except (TypeError, ValueError):
            self.app.log(f"AVISO: av_offset_ms inválido no estado.json ({value!r}); usando 0")
            return 0
```

por isto:

```python
    def _av_offset(self) -> int:
        # le o arquivo no clique: o calibrar_av.py --salvar pode ter gravado com o app aberto
        estado, aviso = load_estado(self.app.estado_path)
        if aviso:
            self.app.log(f"AVISO: {aviso}")
        return estado["av_offset_ms"]           # o load_estado ja garante int
```

- [ ] **Step 12: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_pipeline`
Expected: `Ran 26 tests` e `OK`

- [ ] **Step 13: Commit**

```bash
git add studio/gui_pipeline.py tests/test_gui_pipeline.py
git commit -m "fix(gui_pipeline): render le o av_offset_ms do estado.json no clique

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 14: Testes do erro por modelo e do disco cheio (falha)**

Dois testes antigos esperavam a tomada "falhou" depois de um erro de vídeo ou de conversão; agora esperam o erro em
`saidas["orochi"]["erro"]`. Em `tests/test_gui_pipeline.py`, substituir isto:

```python
import gc
import json
```

por isto:

```python
import errno
import gc
import json
```

E em `tests/test_gui_pipeline.py`, substituir isto:

```python
        saved = Take.load(take.dir)
        self.assertEqual(("falhou", FAIL_MSG), (saved.status, saved.erro))
        self.assertNotIn("mp4", saved.saidas["orochi"])
```

por isto:

```python
        saved = Take.load(take.dir)
        # o erro fica no modelo; "falhou" e so da gravacao (a tomada continua utilizavel)
        self.assertEqual(("convertido", ""), (saved.status, saved.erro))
        self.assertEqual(FAIL_MSG, saved.saidas["orochi"]["erro"])
        self.assertNotIn("mp4", saved.saidas["orochi"])
```

E em `tests/test_gui_pipeline.py`, substituir isto:

```python
        self.assertEqual(("renderizado", ""), (app.take.status, app.take.erro))
        self.assertIn("Pronto", p.video_label.cget("text"))
```

por isto:

```python
        self.assertEqual(("renderizado", ""), (app.take.status, app.take.erro))
        self.assertNotIn("erro", Take.load(take.dir).saidas["orochi"])      # o sucesso limpa o erro
        self.assertIn("Pronto", p.video_label.cget("text"))
```

E em `tests/test_gui_pipeline.py`, substituir isto:

```python
        saved = Take.load(take.dir)
        self.assertEqual("falhou", saved.status)
        self.assertIn("fechou inesperadamente", saved.erro)
        p.btn_convert.invoke()
        self.wait_done(app)
        self.assertEqual(2, self.rvc.starts)
        self.assertEqual(("convertido", ""), (app.take.status, app.take.erro))
```

por isto:

```python
        saved = Take.load(take.dir)
        self.assertEqual(("gravado", ""), (saved.status, saved.erro))         # a gravacao continua boa
        self.assertIn("fechou inesperadamente", saved.saidas["orochi"]["erro"])
        p.btn_convert.invoke()
        self.wait_done(app)
        self.assertEqual(2, self.rvc.starts)
        self.assertEqual(("convertido", ""), (app.take.status, app.take.erro))
        self.assertNotIn("erro", Take.load(take.dir).saidas["orochi"])       # o sucesso limpa o erro
```

E em `tests/test_gui_pipeline.py`, substituir isto:

```python
    # ---------- gravacao e troca de tomada ----------
```

por isto:

```python
    def test_model_error_follows_the_chosen_model(self):
        # erro de conversao do Orochi: some com o Silvio escolhido, volta com o Orochi e sobrevive a reabertura
        take = self.make_take(modo="audio")

        def no_checkpoint(inp, out, model_key):
            raise RvcError(f"Nenhum checkpoint do modelo {model_key}")

        self.rvc.convert = no_checkpoint
        app = self.make_app()
        p = app.pipeline_panel
        p.btn_convert.invoke()
        self.wait_done(app)
        self.assertEqual("Falhou: Nenhum checkpoint do modelo orochi", p.convert_label.cget("text"))
        for label in ("Silvio Santos", "Orochi"):
            app.model_var.set(label)
            app.model_box.event_generate("<<ComboboxSelected>>")
            app.dispatch_events()
            if label == "Silvio Santos":
                self.assertEqual(f"Tomada {take.id}: pronta para converter para Silvio Santos",
                                 p.convert_label.cget("text"))
        expected = f"Tomada {take.id}: a conversão para Orochi falhou — Nenhum checkpoint do modelo orochi"
        self.assertEqual(expected, p.convert_label.cget("text"))
        self.assertEqual(gui_pipeline.COLOR_BAD, str(p.convert_label.cget("foreground")))
        app.shutdown()
        again = self.make_app()                                    # reabrir: a tomada continua a ultima
        self.assertEqual(take.id, again.take.id)
        self.assertEqual(expected, again.pipeline_panel.convert_label.cget("text"))
        self.assertEqual("normal", state(again.pipeline_panel.btn_convert))

    def test_disk_full_errors_reach_the_labels(self):
        # "Disco cheio" do render (RenderError) e da conversao (RvcError) aparecem no rotulo e reabilitam
        take = self.make_take()
        self.converted(take)

        def render_full(*args, **kwargs):
            raise render.RenderError(procs.MSG_DISK_FULL)

        def convert_full(inp, out, model_key):
            raise RvcError(procs.MSG_DISK_FULL)

        app = self.make_app(render_final=render_full)
        p = app.pipeline_panel
        p.btn_render.invoke()
        self.wait_done(app)
        self.assertEqual("Falhou: Disco cheio — libere espaço", p.video_label.cget("text"))
        self.assertIn("ERRO ao gerar o vídeo: Disco cheio — libere espaço", self.log_text(app))
        self.assertEqual(procs.MSG_DISK_FULL, Take.load(take.dir).saidas["orochi"]["erro"])
        self.rvc.convert = convert_full
        p.btn_convert.invoke()
        self.wait_done(app)
        self.assertEqual("Falhou: Disco cheio — libere espaço", p.convert_label.cget("text"))
        for b in (p.btn_convert, p.btn_render, p.btn_boost):
            self.assertEqual("normal", state(b), b.cget("text"))

    def test_take_save_failure_shows_error_and_does_not_render(self):
        # disco cheio ao gravar o take.json depois de converter: erro no rotulo, nada de "convertida" e sem video
        take = self.make_take()
        app = self.make_app()
        p = app.pipeline_panel
        with mock.patch.object(Take, "save", side_effect=OSError(errno.ENOSPC, "No space left on device")):
            p.btn_convert.invoke()
            self.wait_done(app)
        self.assertEqual([], self.render.calls)
        self.assertEqual("Falhou: Disco cheio — libere espaço", p.convert_label.cget("text"))
        self.assertIn("ERRO ao salvar a tomada: Disco cheio — libere espaço", self.log_text(app))
        self.assertEqual(({}, "gravado"), (app.take.saidas, app.take.status))    # a memoria volta ao take.json
        self.assertEqual({}, Take.load(take.dir).saidas)
        self.assertEqual("normal", state(p.btn_convert))
        self.assertEqual("disabled", state(p.btn_render))

    # ---------- gravacao e troca de tomada ----------
```

- [ ] **Step 15: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_pipeline`
Expected: `Ran 29 tests` e `FAILED (failures=4, errors=1)`:
- `test_disk_full_errors_reach_the_labels`: `KeyError: 'erro'`
- `test_fake_worker_death_marks_take`: `AssertionError: Tuples differ: ('gravado', '') != ('falhou', 'O conversor fechou inesperadame[34 chars]log')`
- `test_model_error_follows_the_chosen_model`: `AssertionError: 'Tomada 2026-09-26_101500: pronta para converter para Silvio Santos' != 'Tomada 2026-09-26_101500: falhou — Nenhum checkpoint do modelo orochi'`
- `test_render_failure_shows_message_and_reenables`: `AssertionError: Tuples differ: ('convertido', '') != ('falhou', "O vídeo gerado não passou na ve[58 chars]) 0")`
- `test_take_save_failure_shows_error_and_does_not_render`: `AssertionError: 'Falhou: Disco cheio — libere espaço' != 'Tomada 2026-09-26_101500: convertida para Orochi ✓'`
  (o `OSError` do `save` escapou do handler, e a memória ficou com a conversão que não foi gravada)

- [ ] **Step 16: Erro por modelo, sem mexer no status, e `take.json` que não grava desfaz a mudança**

Os rótulos passam a mostrar o erro gravado do modelo escolhido: na seção 3 quando não há WAV convertido, na
seção 4 quando há WAV e não há MP4. Em `studio/gui_pipeline.py`, substituir isto:

```python
        if wav:
            return f"{head}: convertida para {m.label} ✓", COLOR_OK
        return f"{head}: pronta para converter para {m.label}", COLOR_TEXT
```

por isto:

```python
        if wav:
            return f"{head}: convertida para {m.label} ✓", COLOR_OK
        erro = self._saida(take, m).get("erro")          # ultimo erro deste modelo (o de outro nao aparece)
        if erro:
            return f"{head}: a conversão para {m.label} falhou — {erro}", COLOR_BAD
        return f"{head}: pronta para converter para {m.label}", COLOR_TEXT
```

E em `studio/gui_pipeline.py`, substituir isto:

```python
        if mp4:
            return f"Pronto: {os.path.basename(mp4)}", COLOR_OK
        if wav:
            return "Clique Gerar vídeo para gerar o MP4", COLOR_TEXT
```

por isto:

```python
        if mp4:
            return f"Pronto: {os.path.basename(mp4)}", COLOR_OK
        erro = self._saida(take, m).get("erro")          # o ultimo video deste modelo falhou
        if wav and erro:
            return f"Falhou: {erro}", COLOR_BAD
        if wav:
            return "Clique Gerar vídeo para gerar o MP4", COLOR_TEXT
```

A tomada A/V recuperada grava o `video`/`audio_fit` pelo `_commit` (novo, mais abaixo).
Em `studio/gui_pipeline.py`, substituir isto:

```python
        take = ctx["take"]
        if not ok:
            section = "volume" if ctx["then"] == JOB_BOOST else "convert"
            self._failed(take, section, f"ERRO ao preparar o áudio da tomada {take.id}", value, mark=True)
            return
        vi, fit = value
        take.video, take.audio_fit = asdict(vi), asdict(fit)
        take.save()
```

por isto:

```python
        take = ctx["take"]
        section = "volume" if ctx["then"] == JOB_BOOST else "convert"
        if not ok:
            self._failed(take, section, f"ERRO ao preparar o áudio da tomada {take.id}", value, mark=True)
            return
        vi, fit = value
        if not self._commit(take, section, video=asdict(vi), audio_fit=asdict(fit)):
            return
```

E em `studio/gui_pipeline.py`, substituir isto:

```python
            self._failed(take, "convert", "ERRO na conversão", value, mark=True)
            if dead:
                # o proximo clique chama ensure_rvc(), que sobe um worker novo
                self.app.log(MSG_WORKER_DIED)
                self._errors["convert"] = MSG_WORKER_DIED
            return
        saida = self._saida(take, m)
        saida.update(wav=f"{m.key}.wav", mp3=f"{m.key}_IA.mp3")
        take.saidas[m.key] = saida
        take.status, take.erro = "convertido", ""
        take.save()
        self._mark_model_loaded()
```

por isto:

```python
            self._failed(take, "convert", "ERRO na conversão", value, modelo=m)
            if dead:
                # o proximo clique chama ensure_rvc(), que sobe um worker novo
                self.app.log(MSG_WORKER_DIED)
                self._errors["convert"] = MSG_WORKER_DIED
            return
        saida = {k: v for k, v in self._saida(take, m).items() if k != "erro"}     # o sucesso limpa o erro
        saida.update(wav=f"{m.key}.wav", mp3=f"{m.key}_IA.mp3")
        if not self._commit(take, "convert", saidas={**take.saidas, m.key: saida}, status="convertido", erro=""):
            return                            # o take.json nao foi gravado: sem video
        self._mark_model_loaded()
```

E em `studio/gui_pipeline.py`, substituir isto:

```python
            self._failed(take, "video", "ERRO ao gerar o vídeo", MSG_NO_WAV, mark=False)
```

por isto:

```python
            self._failed(take, "video", "ERRO ao gerar o vídeo", MSG_NO_WAV, modelo=m)
```

E em `studio/gui_pipeline.py`, substituir isto:

```python
            self._failed(take, "video", "ERRO ao gerar o vídeo", value, mark=True)
            return
        saida = self._saida(take, m)
        saida["mp4"] = os.path.relpath(value, take.dir)
        saida.pop("enviado", None)            # o arquivo mudou: ainda nao foi enviado
        take.saidas[m.key] = saida
        take.status, take.erro = "renderizado", ""
        take.save()
        self.app.log(f"Vídeo pronto: {os.path.basename(value)} (em {os.path.dirname(value)})")
```

por isto:

```python
            self._failed(take, "video", "ERRO ao gerar o vídeo", value, modelo=m)
            return
        # sem "enviado" (o arquivo mudou: ainda nao foi enviado) e sem "erro" (o sucesso limpa)
        saida = {k: v for k, v in self._saida(take, m).items() if k not in ("enviado", "erro")}
        saida["mp4"] = os.path.relpath(value, take.dir)
        if not self._commit(take, "video", saidas={**take.saidas, m.key: saida}, status="renderizado", erro=""):
            return
        self.app.log(f"Vídeo pronto: {os.path.basename(value)} (em {os.path.dirname(value)})")
```

O `enviado` também passa pelo `_commit`; a linha "Enviado pro Drive" sai antes, porque o arquivo já está no Drive
mesmo que o `take.json` não grave. Em `studio/gui_pipeline.py`, substituir isto:

```python
        if res.get("ok"):
            self.progress.configure(value=100)
            if res.get("md5"):
                saida = self._saida(take, m)
                saida["enviado"] = {"md5": res["md5"], "quando": datetime.now().isoformat(timespec="seconds")}
                take.saidas[m.key] = saida
                take.save()
            what = "Já estava igual no Drive (pulado)" if res.get("pulado") else "Enviado pro Drive"
            self.app.log(f"{what}: {name}")
            if res.get("aviso"):
                self.app.log(f"AVISO: {res['aviso']}")
            return
```

por isto:

```python
        if res.get("ok"):
            self.progress.configure(value=100)
            what = "Já estava igual no Drive (pulado)" if res.get("pulado") else "Enviado pro Drive"
            self.app.log(f"{what}: {name}")
            if res.get("aviso"):
                self.app.log(f"AVISO: {res['aviso']}")
            if res.get("md5"):
                sent = {"md5": res["md5"], "quando": datetime.now().isoformat(timespec="seconds")}
                self._commit(take, "drive", saidas={**take.saidas, m.key: {**self._saida(take, m), "enviado": sent}})
            return
```

E em `studio/gui_pipeline.py`, substituir isto:

```python
    def _failed(self, take: Take, section: str, what: str, message: str, mark: bool) -> None:
        # log + rotulo da secao; mark=True grava "falhou" no take.json (thread principal)
        self.app.log(f"{what}: {message}")
        if take is self.app.take:
            self._errors[section] = f"Falhou: {message}"
        if mark:
            take.status, take.erro = "falhou", message
            take.save()
```

por isto:

```python
    def _failed(self, take: Take, section: str, what: str, message: str, mark: bool = False,
                modelo: Modelo | None = None) -> None:
        # log + rotulo da secao; no take.json (thread principal): mark=True -> "falhou" (so a gravacao: o audio
        # da tomada nao se le); modelo -> saidas[modelo]["erro"] (conversao e video), sem mexer no status
        self.app.log(f"{what}: {message}")
        if take is self.app.take:
            self._errors[section] = f"Falhou: {message}"
        if not mark and modelo is None:
            return
        if mark:
            take.status, take.erro = "falhou", message
        if modelo is not None:
            take.saidas[modelo.key] = {**self._saida(take, modelo), "erro": message}
        try:
            take.save()
        except OSError as e:
            self.app.log(f"AVISO: o erro não foi gravado no take.json: {procs.os_error_message(e)}")

    def _commit(self, take: Take, section: str, **changes) -> bool:
        # aplica e grava o take.json; se nao gravar (ex.: disco cheio), desfaz na memoria e mostra o erro
        before = {k: copy.deepcopy(getattr(take, k)) for k in changes}
        for k, v in changes.items():
            setattr(take, k, v)
        try:
            take.save()
        except OSError as e:
            for k, v in before.items():
                setattr(take, k, v)
            self._failed(take, section, "ERRO ao salvar a tomada", procs.os_error_message(e))
            return False
        return True
```

- [ ] **Step 17: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_pipeline`
Expected: `Ran 29 tests` e `OK`

- [ ] **Step 18: Commit**

```bash
git add studio/gui_pipeline.py tests/test_gui_pipeline.py
git commit -m "fix(gui_pipeline): erro de conversao/video fica no modelo; take.json que nao grava nao dispara o video

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 19: Teste do Enviar clicado com o diálogo aberto (falha)**

Em `tests/test_gui_pipeline.py`, substituir isto:

```python
    def test_upload_dialog_cancelled_sends_nothing(self):
```

por isto:

```python
    def test_upload_click_during_dialog_sends_once(self):
        # o dialogo do link roda o laco de eventos: um 2o clique em Enviar nao pode enfileirar outro envio
        take = self.make_take()
        self.converted(take, mp4=True)
        asked = []

        def ask(title, prompt, initial):
            asked.append(title)
            if len(asked) == 1:
                app.pipeline_panel.on_upload()                     # o clique que chega com o dialogo aberto
            return LINK

        app = self.make_app(ask_link=ask)
        app.pipeline_panel.btn_upload.invoke()
        self.wait_done(app)
        self.assertEqual(2, len(asked))
        self.assertEqual(1, len(self.upload.calls))

    def test_upload_dialog_cancelled_sends_nothing(self):
```

- [ ] **Step 20: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_pipeline`
Expected: `Ran 30 tests` e `FAILED (failures=1)`: `AssertionError: 1 != 2` (dois envios na fila)

- [ ] **Step 21: `on_upload` confere de novo depois do diálogo**

Em `studio/gui_pipeline.py`, substituir isto:

```python
        link = self.app.estado.get("drive_pasta") or self.configure_drive()
        if not link:
            return
```

por isto:

```python
        link = self.app.estado.get("drive_pasta") or self.configure_drive()
        # o dialogo do link roda o laco de eventos: um 2o clique pode ter comecado um envio enquanto isso
        if not link or self.uploading is not None:
            return
```

- [ ] **Step 22: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_pipeline`
Expected: `Ran 30 tests` e `OK`

- [ ] **Step 23: Commit**

```bash
git add studio/gui_pipeline.py tests/test_gui_pipeline.py
git commit -m "fix(gui_pipeline): Enviar clicado com o dialogo do link aberto nao enfileira outro envio

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 24: Teste do aviso de buraco na tomada recuperada (falha)**

Em `tests/test_gui_pipeline.py`, substituir isto:

```python
        self.assertIn(f"Áudio alinhado da tomada {take.id} preparado", self.log_text(app))
```

por isto:

```python
        self.assertIn(f"Áudio alinhado da tomada {take.id} preparado", self.log_text(app))
        self.assertNotIn("buraco", self.log_text(app))

    def test_recovered_take_with_gaps_warns(self):
        # buraco no audio (spec 6.3.3): o alinhamento usa o modo assincrono e o log avisa
        take = self.make_take(with_audio=False)

        def extract_with_gaps(raw, out):
            vi, fit = timeline.extract_aligned_audio(raw, out)
            fit.gaps = 3
            return vi, fit

        app = self.make_app(extract_aligned_audio=extract_with_gaps)
        app.pipeline_panel.btn_convert.invoke()
        self.wait_done(app)
        self.assertEqual(3, Take.load(take.dir).audio_fit["gaps"])
        self.assertIn(f"AVISO: o áudio da tomada {take.id} teve 3 buraco(s) acima de 30 ms; o alinhamento usou o "
                      "modo assíncrono — confira a sincronia", self.log_text(app))
```

- [ ] **Step 25: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_pipeline`
Expected: `Ran 31 tests` e `FAILED (failures=1)`: `AssertionError: 'AVISO: o áudio da tomada 2026-09-26_101500 teve 3
buraco(s) acima de 30 ms; o alinhamento usou o modo assíncrono — confira a sincronia' not found in '…'`

- [ ] **Step 26: O aviso depois de preparar o áudio**

Em `studio/gui_pipeline.py`, substituir isto:

```python
from studio.events import Event, error_message
```

por isto:

```python
from studio.events import Event, error_message
from studio.gui_capture import gaps_warning
```

E em `studio/gui_pipeline.py`, substituir isto:

```python
        self.app.log(f"Áudio alinhado da tomada {take.id} preparado")
```

por isto:

```python
        self.app.log(f"Áudio alinhado da tomada {take.id} preparado")
        warn = gaps_warning(take)
        if warn:
            self.app.log(warn)
```

- [ ] **Step 27: Rodar e ver passar (módulo e suíte inteira)**

Run: `Applio/.venv/bin/python -m unittest tests.test_gui_pipeline`
Expected: `Ran 31 tests` e `OK`

Run: `./run_tests.sh`, `pgrep -a ffmpeg` e `pgrep -af rvc_worker`
Expected: `Ran 440 tests` e `OK (skipped=6)` (as Tasks 1–11 com as correções); os dois `pgrep` não mostram nada

- [ ] **Step 28: Commit**

```bash
git add studio/gui_pipeline.py tests/test_gui_pipeline.py
git commit -m "fix(gui_pipeline): aviso de buraco no audio tambem na tomada recuperada

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

**Notas para o executor**

- **Altura da coluna direita.** A primeira versão do painel (um status em linha própria em cada seção e a barra do
  Drive numa linha separada) pedia 605 px de altura. Com o peso da grade na linha das colunas, sobravam ~520 px acima
  do log, e a seção "5. Drive" recebia 42 dos 128 px: ficava cortada, e os testes passavam do mesmo jeito. Por isso o
  painel é compacto: o status do volume fica na mesma linha do botão, e a barra e o Cancelar ficam na linha dos botões
  do Drive. A troca 6 passa o peso para o log. Os status quebram a linha na largura real (bind de `<Configure>`), e
  a janela pode encolher até 980×640. O Step 7 confere `app.right.winfo_height() >= app.right.winfo_reqheight()`.
- **Tela bloqueada:** o `ImageGrab` devolve tudo preto (`loginctl show-session <id> -p LockedHint` diz `yes`). O
  Xephyr resolve, com outra fonte: as medidas mudam um pouco, mas a janela é a mesma.
- **Worker morto:** o painel sabe que o worker morreu olhando `rvc.alive()` depois do `RvcError` (a mensagem do
  `RvcClient` varia entre "fechou inesperadamente" e "não está rodando"). Aí mostra "Conversor reiniciado — clique
  Converter de novo"; o próximo clique chama `ensure_rvc()`, que sobe outro worker. No teste com o worker de verdade,
  matar o processo *antes* do clique não serve, porque o `ensure_rvc()` do clique vê o worker morto e sobe outro em
  silêncio. O teste segura a fila gpu com um job-portão, clica, mata o worker e só então solta o portão. Ele espera o
  estado `Z` em `/proc/<pid>/stat` em vez de chamar `alive()`, que colheria o processo antes da hora.
- **Só a thread principal grava o `take.json`** (spec 3.2). Os jobs recebem cópias das entradas (`copy.deepcopy(take)`
  no render) e devolvem resultados; o handler do `job_ok` aplica e salva. O teste troca `Take.save` por um espião que
  anota a thread, e o `close_app` confere que só apareceu `MainThread`. O `enviado` é gravado na mesma Take da tela.
  O `drive.record_sent` não serve aqui: ele relê o `take.json` do disco, e o próximo `save()` do `app.take` apagaria o
  `enviado`.
- **Botões presos:** o `_on_job` zera `busy`/`uploading` *antes* de chamar o handler. Se o handler levantar, o App
  loga, e o `refresh()` do `finally` reabilita tudo. Na cadeia converter → render, o handler do `job_ok` da conversão
  volta a pôr `busy`.
- **`take.json` que não grava (Steps 14–18).** Os handlers de sucesso gravam pelo `_commit`: ele aplica as mudanças,
  chama o `save()` e, se vier `OSError`, devolve os valores antigos (cópia funda), mostra "Falhou: Disco cheio —
  libere espaço" na seção e devolve `False`, e aí o handler não segue (a conversão não dispara o vídeo). Sem isso, a
  tela dizia "convertida ✓" com o `take.json` sem a conversão. O `enviado` também passa pelo `_commit`, mas a linha
  "Enviado pro Drive" sai antes, porque o arquivo já está no Drive. O teste troca `Take.save` (o espião do `setUp`)
  por um `mock` com `side_effect=OSError(errno.ENOSPC, ...)` só durante o clique.
- **Erro por modelo (Steps 14–18).** A tomada em que a conversão ou o vídeo falhou continua utilizável: a abertura
  (Task 9) a carrega de novo, e os botões seguem liberados. O rótulo da seção 3 mostra o erro gravado do modelo
  escolhido quando ainda não há WAV dele, e o da seção 4 quando há WAV e não há MP4. Na mesma sessão, o texto
  "Falhou: …" da última etapa (`_errors`) vem antes e some ao trocar de tomada ou de modelo.
- **Enviar em dobro (Steps 19–23):** o `simpledialog` roda um laço de eventos próprio, e um clique em Enviar que chega
  com ele aberto entra no `on_upload` de novo. Depois do diálogo, o `on_upload` confere o `uploading` outra vez. O
  teste chama o `on_upload` de dentro do `ask_link` falso.
- **`av_offset_ms` (Steps 9–13):** o render lê o arquivo no clique, não o `app.estado`. O teste grava 40 por fora e
  clica Gerar vídeo antes de qualquer outra gravação do app: com o valor da memória, o 1º render sairia com 0.
- **`ttk.Button.invoke()` num botão desabilitado não faz nada.** É assim que os testes provam que o botão fica travado
  durante a gravação: o clique não chega ao conversor.
- **Durante a gravação** ficam desabilitados Volume, Converter, Gerar vídeo, os dois Ouvir e Assistir: o som tocaria
  nas caixas e entraria no microfone. Enviar e Configurar Drive continuam liberados. Um render já na fila continua
  rodando, porque a gravação usa só a fila io.
- **Tomada recuperada:** uma A/V "gravado" sem `audio.wav` (o app fechou gravando ou travou) passa por
  `extract_aligned_audio` na fila io antes de aumentar o volume ou converter. O `take.video` e o `take.audio_fit` são
  gravados no handler. "Ouvir gravação" toca o `raw.mkv` direto (`ffplay -nodisp`).
- **Progresso:** `0.42 * 100` dá `42.00000000000001`, então o painel arredonda para inteiro. Os eventos
  `drive_progress` de um envio chegam antes do `job_ok` dele (mesma fila); os que chegam fora de um envio são
  ignorados.
- **Rótulos:** seguem a spec 10 e o README: "Aumentar volume", "Converter", "Gerar vídeo", "Cancelar", "Assistir",
  "Abrir pasta", "Enviar", "Configurar Drive", "Reconectar", "▶ Ouvir gravação" e "▶ Ouvir resultado". O "Enviar" do
  app manda o MP4 da tomada e do modelo que estão na tela; para enviar todos os `videos_finais/*_IA.mp4`, use o
  `enviar_drive.py`. Sem pasta configurada, "Enviar" abre "Configurar Drive" e segue com o envio se o link for válido.
  Um link inválido é recusado e pedido de novo, com o motivo no texto do diálogo.
- **Fechar:** um render ou envio em andamento vira motivo de confirmação, pelo `JOB_LABELS` e pelos dois
  `register_closer`. Se o usuário confirma, o gancho de saída liga os `threading.Event` de cancelamento (o render manda
  SIGTERM ao ffmpeg e apaga o `.part`) e para o `ffplay`. Só um `ffplay` toca por vez, e os `Popen` já terminados são
  colhidos no próximo `spawn`.
- **Mutações conferidas:** cada uma destas 18 mutações do `gui_pipeline.py` derruba a suíte nova:
  - tirar o render automático;
  - passar a Take original ao render;
  - ignorar a gravação;
  - pular a extração;
  - não detectar o worker morto;
  - ignorar o Cancelar do vídeo;
  - não mexer na barra;
  - não salvar o `enviado`;
  - não pedir de novo o link inválido;
  - não reabilitar na falha;
  - não cancelar ao fechar;
  - ignorar o `av_offset_ms`;
  - ignorar o `boosted.wav`;
  - sobrepor dois sons;
  - não reagir à troca de modelo;
  - não marcar "falhou";
  - não abrir a configuração sem pasta;
  - gravar o `take.json` na thread do job.

  O teste do WAV sumido (`MSG_NO_WAV`) foi visto falhar antes da guarda existir. Com as correções, "não marcar
  'falhou'" passou a ser "não gravar o erro no modelo" (os testes do vídeo que falha e do worker morto conferem
  `saidas["orochi"]["erro"]`), e "não desfazer a memória quando o `save()` falha" derruba o
  `test_take_save_failure_shows_error_and_does_not_render`.

---

### Task 12: Teste real ponta a ponta, calibração por palmas e README de uso

**Files:**
- Create: `tests/test_hardware.py` (ponta a ponta com a câmera, o microfone e a GPU reais; só roda com `RUN_HARDWARE=1`)
- Create: `calibrar_av.py` (na raiz; ferramenta de terminal que sugere o `av_offset_ms`)
- Test: `tests/test_calibrar_av.py` (tomada sintética com palmas: dois retângulos que colidem + bip no contato)
- Modify: `README.md` (acrescenta "Como usar", "Calibrar a sincronia (palmas)" e "Solução de problemas"; a seção do
  Drive da Task 8 fica igual)

**Interfaces:**
- Consumes:
  - Task 1: `studio.config.get_modelo(key: str) -> Modelo`, `ESTADO_PATH`, `DEFAULT_ESTADO`,
    `load_estado(path: str = ESTADO_PATH) -> tuple[dict, str | None]`, `save_estado(estado: dict, path: str = ESTADO_PATH) -> None`,
    `merge_estado(changes: dict, path: str = ESTADO_PATH) -> tuple[dict, str | None]` (Task 1, Step 28; usado no
    Step 17);
    `studio.procs.run(argv, timeout=None, **kw) -> CompletedProcess` (aceita `text=False`), `media_info(path) -> dict`,
    `tail(path, n=15) -> str`, `ProcError` (`str(e)` é a mensagem em PT);
    `studio.takes.new_take(modo, mic, camera="", rec_dir=REC_DIR) -> Take`, `Take.save()`, `Take.load(take_dir)`,
    `Take.path(name)`, `Take.raw_path`, `Take.audio_path`, `final_video_name(take_id, model_key) -> str`.
  - Task 2: `studio.audio.volumedetect(path) -> tuple[float | None, float | None]`,
    `export_mp3(wav: str, mp3: str, modelo: Modelo) -> None`.
  - Task 3: `studio.watermark.watermark_path(take_dir, w, h, modelo) -> str`.
  - Task 4: `studio.timeline.extract_aligned_audio(raw_mkv: str, out_wav: str) -> tuple[VideoInfo, AudioFit]`
    (`VideoInfo.w`, `.h`, `.ancora_pts`, `.n_frames`, `.fps_medido`; o `audio.wav` tem t=0 no 2º frame do vídeo).
  - Task 5: `studio.render.render_final(take, modelo, conv_wav, av_offset_ms=0, videos_dir=VIDEOS_DIR, cancel=None) -> str`,
    `verify_render(mp4, n_frames, wm_png) -> list[str]`, `render.FPS` (30), `render.PART_NAME` (`"render.part.mp4"`).
  - Task 6: `studio.capture.preflight(mic, cam, rec_dir) -> tuple[int | None, str | None]`,
    `build_av_cmd(mic, cam, out_mkv) -> list[str]`, `CaptureProcess(argv, log_path, with_preview)` (`start()`,
    `latest_frame()`, `early_failure()`, `fps_measured()`, `request_stop()`, `wait_stopped()`, `stop_steps`, `running`,
    `pid`), `check_mic(pid, expected_index) -> str | None`, `verify_capture(path, need_video) -> str | None`, `ACCEPTED_RC`;
    `mic_status(pid, expected_index) -> str` e `MIC_OK` (Task 6, Step 14; usados no Step 20).
  - Task 7: `studio.rvc_client.RvcClient(python=VENV_PYTHON, log_path=RVC_LOG, env=None)` (`start()`, `load()`,
    `convert(inp, out, model_key) -> {"duracao", "sr", "pedacos", ...}`, `close()`, `alive()`).
- Produces (`calibrar_av.py`; nenhuma task importa, porque a spec 4 deixa o `av_offset_ms` sem controle na GUI):
  - CLI: `Applio/.venv/bin/python calibrar_av.py <pasta da tomada> [--salvar] [--estado CAMINHO]`. Sai com 0 (ok),
    1 (erro, ou `--salvar` recusado com menos de 5 palmas) ou 2 (argumentos errados, pelo `argparse`). O `--salvar`
    grava só o `av_offset_ms`, com `merge_estado` (Step 17): pode rodar com o app aberto.
  - `clap_onsets(x: np.ndarray, sr: int) -> list[float]`,
    `contact_time(d: np.ndarray, t: np.ndarray, lo: float, hi: float) -> float | None`,
    `calibrate(take_dir: str) -> Calibration`, `main(argv: list[str] | None = None) -> int`.
  - `Clap(audio_s: float, video_s: float, offset_ms: float)`;
    `Calibration(claps, audio_onsets, suggested_ms, spread_ms, fps, region, warnings, confidence)`; `CalibrationError`.
  - Convenção: `offset_ms = (video_s − audio_s)·1000`, ou seja, quanto atrasar o áudio. É o mesmo sinal do
    `av_offset_ms` do render (positivo = áudio mais tarde).

Todos os comandos rodam da raiz do repositório (`~/orochi-ia-homenagem`).

- [ ] **Step 1: Escrever o teste real ponta a ponta**

O teste grava 5 s de A/V com a câmera A4tech e o microfone Generalplus. O gravador e o worker do RVC nascem na thread
principal do teste, como na GUI (o pdeathsig vale enquanto a thread que fez o spawn viver). Depois ele confere a
captura, extrai o áudio alinhado, converte com o RVC real (modelo silvio), gera o MP3, renderiza para um
`videos_finais/` temporário e confere o MP4 de novo. Toda a mídia fica num `TemporaryDirectory`, apagado no fim, e
o conteúdo dos frames nunca é olhado (só a contagem).

`tests/test_hardware.py`:

```python
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
            self.assertIsNone(capture.check_mic(cap.pid, index))
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
        take.saidas[modelo.key].update(mp3="silvio_IA.mp3", mp4=final)
        take.save()

        self.assertEqual(procs.run(["pgrep", "-af", self.root]).stdout, "")    # nenhum processo sobrou
        print(f"\n  [hw] preview {seq} frames a {fps and round(fps, 1)} fps; MKV {vi.n_frames} frames "
              f"({vi.fps_medido} fps medidos), âncora {vi.ancora_pts:.3f} s; mic início {fit.inicio:.3f} s, "
              f"taxa {fit.taxa_real} Hz, resíduo {fit.residuo_ms} ms; volume médio {mean_db} dB, pico {max_db} dB",
              file=sys.stderr)
        print("  [hw] tempos: " + ", ".join(f"{k} {v:.2f} s" for k, v in self.times.items()), file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar sem hardware (fica pulado)**

Run: `Applio/.venv/bin/python -m unittest tests.test_hardware -v`
Expected:

```text
test_av_silvio_end_to_end (tests.test_hardware.EndToEndTest.test_av_silvio_end_to_end) ... skipped 'precisa de câmera, microfone, GPU e do modelo silvio (RUN_HARDWARE=1)'

----------------------------------------------------------------------
Ran 1 test in 0.000s

OK (skipped=1)
```

Este teste não tem código novo para implementar: ele só exercita as Tasks 1–8 juntas. Se o passo seguinte falhar
por um bug num módulo, a correção vai para a task dona do módulo, com um teste que fixa o bug e um commit separado.

- [ ] **Step 3: Rodar o teste real (a luz da câmera acende)**

Antes, feche Meet, Zoom e OBS: `fuser /dev/video0` tem que sair com código 1 (ninguém usando a câmera). A GPU
precisa de ~2 GB livres. Rode só este módulo: `RUN_HARDWARE=1 ./run_tests.sh` ligaria a câmera várias vezes.

Run: `RUN_HARDWARE=1 Applio/.venv/bin/python -m unittest tests.test_hardware -v`
Expected (valores medidos aqui; os números variam um pouco):

```text
test_av_silvio_end_to_end (tests.test_hardware.EndToEndTest.test_av_silvio_end_to_end) ... 
  [hw] preview 76 frames a 15.0 fps; MKV 151 frames (14.97 fps medidos), âncora 1.011 s; mic início -0.058 s, taxa 48000.0 Hz, resíduo 5.89 ms; volume médio -38.7 dB, pico -21.7 dB
  [hw] tempos: 1o frame (REC) 1.18 s, parada 0.26 s, verify_capture 0.05 s, audio alinhado 0.17 s, RVC carregar 10.58 s, RVC converter 6.49 s, MP3 2.43 s, render + verificação 1.29 s, verificação extra 0.36 s
ok

----------------------------------------------------------------------
Ran 1 test in 27.948s

OK
```

Depois, `pgrep -a ffmpeg` não mostra nada e `ls -d /tmp/studio_hw_*` não acha nenhuma pasta.

- [ ] **Step 4: Commit**

```bash
git add tests/test_hardware.py
git commit -m "test(hardware): ponta a ponta real (camera+mic -> RVC silvio -> MP3 -> MP4 verificado)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: Escrever o teste da calibração (vídeo sintético)**

A tomada sintética imita o `raw.mkv` do gravador: MJPEG + PCM mono 48 kHz. Duas "mãos" (retângulos claros) se
aproximam em 0,3 s, ficam paradas 0,1 s no contato e se afastam em 0,4 s. Os 6 contatos caem em fases diferentes
em relação aos frames. No áudio, um bip de 1 kHz começa no contato, deslocado de `audio_delay_s` (o offset
injetado). Um quadrado anda sem parar no alto do quadro, como distração, e há ruído de sensor. O `nudges` põe um bip
sem palma (o clique do botão Parar) enquanto a mão direita escorrega 4 px.

`tests/test_calibrar_av.py`:

```python
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np

import calibrar_av
from studio.config import DEFAULT_ESTADO, load_estado, save_estado

SR = 48000
W, H = 640, 360
GAP_PX = 200            # distancia entre os retangulos em repouso
APPROACH_S = 0.3        # aproximacao ate o contato
HOLD_S = 0.1            # maos paradas depois do contato
SEPARATE_S = 0.4
BEEP_S = 0.04
# contatos com fases diferentes em relacao aos frames (15 e 30 fps)
CONTACTS = [1.013, 2.047, 3.071, 4.109, 5.138, 6.162]


def gap_at(t: float, contacts: list[float]) -> float:
    g = float(GAP_PX)
    for c in contacts:
        if c - APPROACH_S <= t < c:
            g = min(g, GAP_PX * (c - t) / APPROACH_S)
        elif c <= t < c + HOLD_S:
            g = 0.0
        elif c + HOLD_S <= t < c + HOLD_S + SEPARATE_S:
            g = min(g, GAP_PX * (t - c - HOLD_S) / SEPARATE_S)
    return g


def draw_frame(t: float, contacts: list[float], rng, nudges=()) -> np.ndarray:
    img = np.full((H, W), 40.0)
    half = round(gap_at(t, contacts) / 2)
    cx = W // 2
    # "clique" sem palma: a mao direita escorrega 4 px em 0,1 s (movimento fraco que para)
    nudge = round(sum(4 * min(max((t - s) / 0.1, 0.0), 1.0) for s in nudges))
    img[200:320, cx - half - 80:cx - half] = 220          # "mao" esquerda
    img[200:320, cx + half + nudge:cx + half + nudge + 80] = 220   # "mao" direita
    # distracao: quadrado que anda sem parar no alto (nao pode virar a regiao escolhida)
    x = 20 + round(abs((150 * t) % 480 - 240))
    img[30:90, x:x + 60] = 160
    img += rng.normal(0, 2, img.shape)                     # ruido de sensor
    return np.clip(img, 0, 255).astype(np.uint8)


def make_clap_take(take_dir: str, contacts: list[float], audio_delay_s: float, fps: int,
                   duration_s: float = 7.5, nudges=()) -> str:
    # raw.mkv como o do gravador (MJPEG + PCM mono 48k); bip no contato (e nos nudges) atrasado audio_delay_s
    rng = np.random.default_rng(7)
    frames = [draw_frame(i / fps, contacts, rng, nudges) for i in range(round(duration_s * fps))]
    x = rng.normal(0, 0.003, round(duration_s * SR))
    for c in [*contacts, *nudges]:
        i0 = round((c + audio_delay_s) * SR)
        n = round(BEEP_S * SR)
        x[i0:i0 + n] += 0.5 * np.sin(2 * np.pi * 1000 * np.arange(n) / SR)
    os.makedirs(take_dir, exist_ok=True)
    wav = os.path.join(take_dir, "beeps.part.wav")
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "s16le",
                    "-ar", str(SR), "-ac", "1", "-i", "-", wav], input=pcm, check=True)
    raw = os.path.join(take_dir, "raw.mkv")
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}", "-framerate", str(fps), "-i", "-",
                    "-i", wav, "-map", "0:v", "-map", "1:a", "-c:v", "mjpeg", "-q:v", "3",
                    "-c:a", "pcm_s16le", "-f", "matroska", raw],
                   input=b"".join(f.tobytes() for f in frames), check=True)
    os.remove(wav)
    return raw


def run_main(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = calibrar_av.main(list(argv))
    return rc, out.getvalue()


class OnsetTest(unittest.TestCase):
    def test_beeps_and_claps_found_to_the_sample(self):
        rng = np.random.default_rng(1)
        x = rng.normal(0, 0.002, 6 * SR)
        beeps, claps = [0.5123, 1.9001, 3.3337], [2.7004, 4.4444]
        for t in beeps:
            i = round(t * SR)
            x[i:i + 1920] += 0.4 * np.sin(2 * np.pi * 1000 * np.arange(1920) / SR)
        for t in claps:          # palma: ruido que cai rapido
            i = round(t * SR)
            x[i:i + 2400] += 0.5 * rng.normal(0, 1, 2400) * np.exp(-np.arange(2400) / 300)
        got = calibrar_av.clap_onsets(x, SR)
        self.assertEqual(len(got), 5)
        for g, t in zip(got, sorted(beeps + claps)):
            self.assertAlmostEqual(g, t, delta=0.0005)

    def test_silence_has_no_onsets(self):
        self.assertEqual(calibrar_av.clap_onsets(np.zeros(SR), SR), [])
        noise = np.random.default_rng(2).normal(0, 0.002, 2 * SR)
        self.assertEqual(calibrar_av.clap_onsets(noise, SR), [])

    def test_ringing_clap_counts_once(self):
        x = np.zeros(2 * SR)
        i = SR // 2
        x[i:i + 9600] = np.sin(2 * np.pi * 800 * np.arange(9600) / SR) * np.exp(-np.arange(9600) / 3000)
        self.assertEqual(len(calibrar_av.clap_onsets(x, SR)), 1)


class ContactTest(unittest.TestCase):
    def test_peak_then_drop_interpolates_last_interval(self):
        t = np.arange(12) / 15
        d = np.array([0, 0, 0, 2, 6, 10, 10, 4, 0, 0, 0, 0], dtype=float)
        # ultimo intervalo cheio termina no frame 6; 40 % do seguinte ate o contato
        self.assertAlmostEqual(calibrar_av.contact_time(d, t, 0.0, 0.8), t[6] + 0.4 / 15, places=9)

    def test_full_last_interval_lands_on_next_frame(self):
        t = np.arange(10) / 30
        d = np.array([0, 3, 8, 8, 0, 0, 0, 0, 0, 0], dtype=float)
        self.assertAlmostEqual(calibrar_av.contact_time(d, t, 0.0, 0.3), t[3], places=9)

    def test_motion_without_stop_is_rejected(self):
        t = np.arange(10) / 15
        d = np.array([0, 5, 9, 10, 9, 10, 9, 10, 9, 0], dtype=float)
        self.assertIsNone(calibrar_av.contact_time(d, t, 0.1, 0.5))

    def test_window_without_motion_is_rejected(self):
        t = np.arange(10) / 15
        self.assertIsNone(calibrar_av.contact_time(np.zeros(10), t, 0.0, 0.6))


class SyntheticTakeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir15 = os.path.join(cls.tmp.name, "palmas15")
        make_clap_take(cls.dir15, CONTACTS, audio_delay_s=0.080, fps=15)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check(self, cal, expected_ms: float, tol_ms: float):
        errs = [c.offset_ms - expected_ms for c in cal.claps]
        print(f"\n  [calib] {cal.fps} fps: sugerido {cal.suggested_ms} ms (esperado {expected_ms:+.0f}), "
              f"erro por palma {[round(e, 1) for e in errs]} ms, dispersão {cal.spread_ms} ms", file=sys.stderr)
        self.assertEqual(len(cal.claps), len(CONTACTS))
        self.assertLessEqual(abs(cal.suggested_ms - expected_ms), tol_ms)
        self.assertLessEqual(max(abs(e) for e in errs), 2 * tol_ms)

    def test_15fps_audio_late_suggests_negative_offset(self):
        cal = calibrar_av.calibrate(self.dir15)
        self.check(cal, -80, 5)
        x0, y0, x1, y1 = cal.region
        self.assertGreaterEqual(y0, 150, "a região não pode pegar o quadrado que anda no alto")
        self.assertTrue(x0 < W // 2 < x1 and y0 < 260 < y1)
        self.assertEqual(len(cal.warnings), 1)
        self.assertIn("15,0 fps", cal.warnings[0])                  # aviso de fps baixo
        self.assertTrue(cal.confidence.startswith("média"))
        self.assertFalse(os.path.exists(os.path.join(self.dir15, "audio.wav")))   # nao mexe na tomada

    def test_30fps_audio_early_suggests_positive_offset(self):
        d = os.path.join(self.tmp.name, "palmas30")
        make_clap_take(d, CONTACTS, audio_delay_s=-0.045, fps=30)
        cal = calibrar_av.calibrate(d)
        self.check(cal, 45, 3)
        self.assertEqual((cal.warnings, cal.confidence), ([], "alta"))

    def test_click_without_clap_is_ignored(self):
        d = os.path.join(self.tmp.name, "clique")
        make_clap_take(d, CONTACTS, audio_delay_s=0.080, fps=30, duration_s=7.8, nudges=[7.2])
        cal = calibrar_av.calibrate(d)
        self.assertEqual(cal.audio_onsets, len(CONTACTS) + 1)
        self.check(cal, -80, 3)

    def test_main_prints_and_saves_median(self):
        estado = os.path.join(self.tmp.name, "estado.json")
        save_estado({**DEFAULT_ESTADO, "mic": "meu-mic", "av_offset_ms": 12}, estado)
        rc, out = run_main(self.dir15, "--salvar", "--estado", estado)
        self.assertEqual(rc, 0, out)
        cal = calibrar_av.calibrate(self.dir15)
        self.assertIn(f"av_offset_ms sugerido: {cal.suggested_ms:+d} ms", out)
        self.assertIn("Confiança", out)
        saved, warn = load_estado(estado)
        self.assertIsNone(warn)
        self.assertEqual((saved["av_offset_ms"], saved["mic"]), (cal.suggested_ms, "meu-mic"))

    def test_without_salvar_estado_is_untouched(self):
        estado = os.path.join(self.tmp.name, "intocado.json")
        rc, out = run_main(self.dir15, "--estado", estado)
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(estado))


class FewClapsTest(unittest.TestCase):
    def test_refuses_to_save_with_less_than_5_claps(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "tres")
            make_clap_take(d, CONTACTS[:3], audio_delay_s=0.0, fps=30, duration_s=4.0)
            estado = os.path.join(tmp, "estado.json")
            rc, out = run_main(d, "--salvar", "--estado", estado)
            self.assertEqual(rc, 1, out)
            self.assertIn("Poucas palmas", out)
            self.assertIn("Não salvei", out)
            self.assertIn("Confiança: baixa", out)
            self.assertFalse(os.path.exists(estado))

    def test_missing_take_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = run_main(os.path.join(tmp, "nao-existe"))
        self.assertEqual(rc, 1)
        self.assertIn("raw.mkv", out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_calibrar_av -v`
Expected: ERROR ao importar o módulo de teste, com `ModuleNotFoundError: No module named 'calibrar_av'`
(`Ran 1 test`, `FAILED (errors=1)`).

- [ ] **Step 7: Implementar `calibrar_av.py`**

O método, em cinco partes:

1. O áudio vem de `timeline.extract_aligned_audio` num diretório temporário: t=0 no 2º frame, o mesmo relógio do
   render. A tomada não é alterada.
2. Cada palma do áudio (transiente) abre uma janela de ±0,4 s, cortada no meio do caminho até as palmas vizinhas.
   É nessa janela que se procura o contato no vídeo.
3. O vídeo é decodificado em 160 px de largura, em cinza, com `-copyts` e `showinfo`. O tempo de cada frame é o
   `pts_time` do `showinfo` menos a `ancora_pts`.
4. A região das mãos é escolhida sozinha: são as células de 10×10 px cuja maior queda de movimento dentro das
   janelas soma pelo menos 40 % da melhor célula. O que anda sem parar não tem queda e fica de fora.
5. O contato é o 1º intervalo que encolhe depois do pico de movimento, na fração que ele andou:
   `t[q-1] + d[q]/d[q-1]·(t[q] − t[q-1])`. Isso tira a quantização de ±1 frame.

`calibrar_av.py`:

```python
#!/usr/bin/env python3
"""Calibra o av_offset_ms com uma tomada de palmas.

Uso (da raiz do projeto, com o python do venv do Applio):
    Applio/.venv/bin/python calibrar_av.py recordings/<id> [--salvar]

Acha o inicio de cada palma no audio alinhado (transiente) e, no video, o instante do contato das maos
(pico de movimento entre frames seguido de queda, numa regiao escolhida sozinha). Imprime a mediana dos
offsets como sugestao para av_offset_ms (positivo = audio mais tarde).
"""

import argparse
import os
import re
import sys
import tempfile
import wave
from dataclasses import dataclass, field

import numpy as np

from studio import procs, timeline
from studio.config import ESTADO_PATH, load_estado, save_estado

MIN_CLAPS = 5
# audio
ONSET_SNR = 10.0         # limiar >= 10x o ruido de fundo ...
ONSET_PEAK_FRAC = 0.1    # ... e >= 10 % da palma mais forte
ONSET_FRAC = 0.2         # inicio = 1a amostra com 20 % do pico da palma
REFRACTORY_S = 0.25      # eco/ressonancia da mesma palma
# video
ANALYSIS_W = 160         # frames reduzidos para 160 px de largura, em cinza
CELL = 10                # celulas de 10x10 px para escolher a regiao das maos
NOISE_LEVEL = 8          # diferenca de luma abaixo disso e ruido
REGION_FRAC = 0.4        # celulas com >= 40 % da melhor pontuacao entram na regiao
SEARCH_S = 0.4           # procura o contato a +-0,4 s de cada palma do audio
FULL_FRAC = 0.8          # intervalo com >= 80 % do anterior ainda e movimento cheio
STOP_FRAC = 0.5          # depois do contato o movimento cai abaixo de 50 % do pico
WEAK_FRAC = 0.25         # pico < 25 % da mediana das palmas = clique/barulho sem palma
# confianca
MAX_SPREAD_MS = 15.0
LOW_FPS = 20.0

_SHOWINFO = re.compile(r"\bn:\s*\d+\s+pts:\s*-?\d+\s+pts_time:\s*(-?[0-9.]+(?:e[-+]?\d+)?)")


class CalibrationError(Exception):
    pass


@dataclass
class Clap:
    audio_s: float       # inicio da palma no audio alinhado (t=0 no 2o frame)
    video_s: float       # contato das maos no mesmo relogio
    offset_ms: float     # video - audio: quanto atrasar o audio


@dataclass
class Calibration:
    claps: list[Clap]
    audio_onsets: int
    suggested_ms: int | None
    spread_ms: float | None
    fps: float
    region: tuple[int, int, int, int]      # (x0, y0, x1, y1) em pixels do video
    warnings: list[str] = field(default_factory=list)
    confidence: str = ""


def _decimal(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


# --- audio ---

def clap_onsets(x: np.ndarray, sr: int) -> list[float]:
    # inicio (s) de cada transiente forte; envelope = maximo por bloco de 1 ms
    a = np.abs(np.asarray(x, dtype=np.float64))
    hop = max(1, sr // 1000)
    n = len(a) // hop
    if n == 0:
        return []
    env = a[:n * hop].reshape(n, hop).max(axis=1)
    peak = float(env.max())
    thr = max(ONSET_SNR * float(np.median(env)), ONSET_PEAK_FRAC * peak)
    if peak <= 0 or thr >= peak:
        return []
    onsets, refractory = [], round(REFRACTORY_S * 1000)
    j = 0
    above = np.flatnonzero(env > thr)
    while True:
        later = above[above >= j]
        if len(later) == 0:
            return onsets
        j = int(later[0])
        p = j + int(np.argmax(env[j:j + 20]))           # pico nos 20 ms seguintes
        s0, s1 = max(0, (j - 10) * hop), (p + 1) * hop
        k = s0 + int(np.argmax(a[s0:s1] >= ONSET_FRAC * env[p]))
        onsets.append(k / sr)
        j += refractory


def _read_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768
    return x.reshape(-1, ch).mean(axis=1), sr


# --- video ---

def decode_gray(raw_mkv: str, w: int, h: int, timeout: float) -> tuple[np.ndarray, np.ndarray]:
    # frames (n, ah, aw) uint8 e pts (s) de cada frame decodificado (showinfo, com -copyts)
    aw, ah = ANALYSIS_W, max(CELL, round(ANALYSIS_W * h / w / CELL) * CELL)
    r = procs.run(["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-loglevel", "info", "-copyts",
                   "-i", raw_mkv, "-map", "0:v:0", "-vf", f"scale={aw}:{ah}:flags=area,format=gray,showinfo",
                   "-fps_mode", "passthrough", "-f", "rawvideo", "-"], timeout=timeout, text=False)
    err = r.stderr.decode("utf-8", "replace")
    if r.returncode != 0:
        raise procs.ProcError("Não foi possível decodificar o vídeo", r.returncode, err[-2000:])
    pts = np.array([float(m) for m in _SHOWINFO.findall(err)])
    n = len(r.stdout) // (aw * ah)
    if n < 3 or n != len(pts):
        raise CalibrationError(f"Leitura do vídeo inconsistente ({n} frames, {len(pts)} tempos)")
    return np.frombuffer(r.stdout, np.uint8, n * aw * ah).reshape(n, ah, aw), pts


def motion_cells(frames: np.ndarray) -> np.ndarray:
    # (n, celulas): soma da diferenca acima do ruido entre o frame i e o i-1, por celula; linha 0 = 0
    n, ah, aw = frames.shape
    out = np.zeros((n, ah // CELL, aw // CELL))
    prev = frames[0].astype(np.int16)
    for i in range(1, n):
        cur = frames[i].astype(np.int16)
        d = np.maximum(np.abs(cur - prev) - NOISE_LEVEL, 0)
        out[i] = d.reshape(ah // CELL, CELL, aw // CELL, CELL).sum(axis=(1, 3))
        prev = cur
    return out.reshape(n, -1)


def search_windows(onsets: list[float]) -> list[tuple[float, float]]:
    out = []
    for i, ta in enumerate(onsets):
        lo, hi = ta - SEARCH_S, ta + SEARCH_S
        if i > 0:
            lo = max(lo, (onsets[i - 1] + ta) / 2)
        if i + 1 < len(onsets):
            hi = min(hi, (ta + onsets[i + 1]) / 2)
        out.append((lo, hi))
    return out


def region_score(m: np.ndarray, t: np.ndarray, windows: list[tuple[float, float]]) -> np.ndarray:
    # por celula: soma, nas janelas das palmas, da maior queda de movimento de um frame para o seguinte
    score = np.zeros(m.shape[1])
    for lo, hi in windows:
        idx = np.flatnonzero((t >= lo) & (t <= hi))
        idx = idx[idx + 1 < len(t)]
        if len(idx):
            score += np.clip(np.max(m[idx] - m[idx + 1], axis=0), 0, None)
    return score


def contact_time(d: np.ndarray, t: np.ndarray, lo: float, hi: float) -> float | None:
    # pico de movimento na janela, segue enquanto o movimento continua cheio; o contato cai dentro do
    # 1o intervalo que encolhe, na fracao que ele andou: t[q-1] + d[q]/d[q-1] * (t[q] - t[q-1])
    idx = np.flatnonzero((t >= lo) & (t <= hi))
    if len(idx) == 0:
        return None
    p = int(idx[np.argmax(d[idx])])
    peak = d[p]
    if peak <= 0:
        return None
    q = p + 1
    while q < len(d) and d[q] >= FULL_FRAC * d[q - 1]:
        q += 1
    if q >= len(d) or t[q - 1] > hi:
        return None
    after = d[q + 1] if q + 1 < len(d) else 0.0
    if min(d[q], after) >= STOP_FRAC * peak:
        return None                     # desacelerou mas nao parou: nao e palma
    return float(t[q - 1] + d[q] / d[q - 1] * (t[q] - t[q - 1]))


def _region_box(mask: np.ndarray, w: int, h: int, shape: tuple[int, int]) -> tuple[int, int, int, int]:
    ah, aw = shape
    rows, cols = np.nonzero(mask.reshape(ah // CELL, aw // CELL))
    sx, sy = w / aw, h / ah
    return (round(cols.min() * CELL * sx), round(rows.min() * CELL * sy),
            round((cols.max() + 1) * CELL * sx), round((rows.max() + 1) * CELL * sy))


# --- calibracao ---

def _pair_claps(onsets: list[float], d: np.ndarray, t: np.ndarray) -> list[Clap]:
    windows = search_windows(onsets)
    peaks = [float(d[(t >= lo) & (t <= hi)].max(initial=0.0)) for lo, hi in windows]
    weak = WEAK_FRAC * float(np.median(peaks))
    claps = []
    for ta, (lo, hi), peak in zip(onsets, windows, peaks):
        tv = contact_time(d, t, lo, hi) if peak > weak else None
        if tv is not None:
            claps.append(Clap(round(ta, 4), round(tv, 4), round((tv - ta) * 1000, 1)))
    return claps


def _assess(n_claps: int, n_onsets: int, spread: float, fps: float) -> tuple[list[str], str]:
    # (avisos, confianca): poucas palmas ou dispersao alta = baixa; so o fps baixo = media
    warnings = []
    if n_claps < MIN_CLAPS:
        warnings.append(f"Poucas palmas válidas ({n_claps} de {n_onsets} no áudio; o mínimo é {MIN_CLAPS})")
    if spread > MAX_SPREAD_MS:
        warnings.append(f"Dispersão alta (±{_decimal(spread)} ms): use boa luz e deixe as mãos inteiras no quadro")
    confidence = "baixa — refaça a tomada se puder" if warnings else "alta"
    if fps < LOW_FPS:
        warnings.append(f"Câmera a {_decimal(fps)} fps (um frame a cada {round(1000 / max(fps, 1))} ms): "
                        "a 30 fps a medida é mais firme (veja \"fps baixo\" no README)")
        if confidence == "alta":
            confidence = "média — câmera abaixo de 20 fps"
    return warnings, confidence


def calibrate(take_dir: str) -> Calibration:
    raw = os.path.join(take_dir, "raw.mkv")
    if not os.path.isfile(raw):
        raise CalibrationError(f"Não achei raw.mkv em {take_dir} (a tomada precisa ter vídeo)")
    with tempfile.TemporaryDirectory(prefix="calibrar_av_") as tmp:     # nao mexe na tomada
        wav = os.path.join(tmp, "audio.wav")
        vi, _ = timeline.extract_aligned_audio(raw, wav)
        x, sr = _read_wav(wav)
    onsets = clap_onsets(x, sr)
    if not onsets:
        raise CalibrationError("Nenhuma palma encontrada no áudio")
    frames, pts = decode_gray(raw, vi.w, vi.h, timeout=60 + vi.n_frames / 30)
    t = pts - vi.ancora_pts                  # mesmo relogio do audio alinhado (e do render)
    m = motion_cells(frames)
    score = region_score(m, t, search_windows(onsets))
    if score.max() <= 0:
        raise CalibrationError("Nenhum movimento de palmas no vídeo")
    mask = score >= REGION_FRAC * score.max()
    claps = _pair_claps(onsets, m[:, mask].sum(axis=1), t)
    if not claps:
        raise CalibrationError("Nenhuma palma foi vista no vídeo (mãos fora do quadro ou pouca luz?)")
    offs = np.array([c.offset_ms for c in claps])
    med = float(np.median(offs))
    spread = round(1.4826 * float(np.median(np.abs(offs - med))), 1)     # desvio robusto (MAD)
    warnings, confidence = _assess(len(claps), len(onsets), spread, vi.fps_medido)
    return Calibration(claps=claps, audio_onsets=len(onsets), suggested_ms=int(round(med)), spread_ms=spread,
                       fps=vi.fps_medido, region=_region_box(mask, vi.w, vi.h, frames.shape[1:]),
                       warnings=warnings, confidence=confidence)


def print_report(take_dir: str, cal: Calibration) -> None:
    offs = [c.offset_ms for c in cal.claps]
    x0, y0, x1, y1 = cal.region
    print(f"Tomada: {os.path.basename(os.path.normpath(take_dir))} (vídeo a {_decimal(cal.fps)} fps; "
          f"{cal.audio_onsets} palmas no áudio, {len(cal.claps)} vistas no vídeo)")
    print(f"Região das mãos: x {x0}–{x1}, y {y0}–{y1}")
    print(" palma   áudio (s)   vídeo (s)   offset (ms)")
    for i, c in enumerate(cal.claps, 1):
        print(f" {i:5d}   {c.audio_s:9.3f}   {c.video_s:9.3f}   {c.offset_ms:+11.1f}")
    print(f"av_offset_ms sugerido: {cal.suggested_ms:+d} ms (mediana de {len(offs)} palmas; dispersão "
          f"±{_decimal(cal.spread_ms)} ms; de {min(offs):+.0f} a {max(offs):+.0f})")
    print("Positivo = áudio mais tarde. A medida é feita sem offset: o valor substitui o atual.")
    for w in cal.warnings:
        print(f"Aviso: {w}")
    print(f"Confiança: {cal.confidence}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sugere o av_offset_ms a partir de uma tomada com 5 ou mais palmas.")
    ap.add_argument("tomada", help="pasta da tomada (recordings/<id>) com raw.mkv")
    ap.add_argument("--salvar", action="store_true", help="grava o valor sugerido em estado.json")
    ap.add_argument("--estado", default=ESTADO_PATH, help="caminho do estado.json (padrão: o do projeto)")
    args = ap.parse_args(argv)
    try:
        cal = calibrate(os.path.abspath(args.tomada))
    except (CalibrationError, procs.ProcError) as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 1
    print_report(args.tomada, cal)
    if not args.salvar:
        return 0
    if len(cal.claps) < MIN_CLAPS:
        print(f"Não salvei: são precisas pelo menos {MIN_CLAPS} palmas vistas no vídeo.")
        return 1
    estado, aviso = load_estado(args.estado)
    if aviso:
        print(f"Aviso: {aviso}", file=sys.stderr)
    antes = estado.get("av_offset_ms", 0)
    estado["av_offset_ms"] = cal.suggested_ms
    save_estado(estado, args.estado)
    print(f"av_offset_ms = {cal.suggested_ms:+d} ms salvo em {args.estado} (antes: {antes})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 8: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_calibrar_av -v`
Expected (as linhas `[calib]` são a precisão medida):

```text
test_full_last_interval_lands_on_next_frame (tests.test_calibrar_av.ContactTest.test_full_last_interval_lands_on_next_frame) ... ok
test_motion_without_stop_is_rejected (tests.test_calibrar_av.ContactTest.test_motion_without_stop_is_rejected) ... ok
test_peak_then_drop_interpolates_last_interval (tests.test_calibrar_av.ContactTest.test_peak_then_drop_interpolates_last_interval) ... ok
test_window_without_motion_is_rejected (tests.test_calibrar_av.ContactTest.test_window_without_motion_is_rejected) ... ok
test_missing_take_is_error (tests.test_calibrar_av.FewClapsTest.test_missing_take_is_error) ... ok
test_refuses_to_save_with_less_than_5_claps (tests.test_calibrar_av.FewClapsTest.test_refuses_to_save_with_less_than_5_claps) ... ok
test_beeps_and_claps_found_to_the_sample (tests.test_calibrar_av.OnsetTest.test_beeps_and_claps_found_to_the_sample) ... ok
test_ringing_clap_counts_once (tests.test_calibrar_av.OnsetTest.test_ringing_clap_counts_once) ... ok
test_silence_has_no_onsets (tests.test_calibrar_av.OnsetTest.test_silence_has_no_onsets) ... ok
test_15fps_audio_late_suggests_negative_offset (tests.test_calibrar_av.SyntheticTakeTest.test_15fps_audio_late_suggests_negative_offset) ... 
  [calib] 15.0 fps: sugerido -80 ms (esperado -80), erro por palma [-1.4, 1.9, -1.5, -0.1, 0.8, 1.3] ms, dispersão 1.9 ms
ok
test_30fps_audio_early_suggests_positive_offset (tests.test_calibrar_av.SyntheticTakeTest.test_30fps_audio_early_suggests_positive_offset) ... 
  [calib] 30.0 fps: sugerido 46 ms (esperado +45), erro por palma [-1.0, 1.1, -1.6, 0.0, 1.0, 5.0] ms, dispersão 1.6 ms
ok
test_click_without_clap_is_ignored (tests.test_calibrar_av.SyntheticTakeTest.test_click_without_clap_is_ignored) ... 
  [calib] 30.0 fps: sugerido -80 ms (esperado -80), erro por palma [-1.0, 1.0, -1.6, 0.0, 1.0, 5.0] ms, dispersão 1.5 ms
ok
test_main_prints_and_saves_median (tests.test_calibrar_av.SyntheticTakeTest.test_main_prints_and_saves_median) ... ok
test_without_salvar_estado_is_untouched (tests.test_calibrar_av.SyntheticTakeTest.test_without_salvar_estado_is_untouched) ... ok

----------------------------------------------------------------------
Ran 14 tests in 4.502s

OK
```

- [ ] **Step 9: Conferir a saída no terminal (tomada sintética a 15 fps, áudio 80 ms atrasado)**

Run:

```bash
T=$(mktemp -d) && Applio/.venv/bin/python -c "from tests.test_calibrar_av import make_clap_take, CONTACTS; make_clap_take('$T/2026-09-26_101500', CONTACTS, 0.080, 15)" && Applio/.venv/bin/python calibrar_av.py "$T/2026-09-26_101500" --salvar --estado "$T/estado.json"; echo "saída: $?"; grep av_offset "$T/estado.json"; rm -rf "$T"
```

Expected (a pasta temporária muda a cada vez):

```text
Tomada: 2026-09-26_101500 (vídeo a 15,0 fps; 6 palmas no áudio, 6 vistas no vídeo)
Região das mãos: x 120–520, y 200–320
 palma   áudio (s)   vídeo (s)   offset (ms)
     1       1.026       0.945         -81.4
     2       2.060       1.982         -78.1
     3       3.084       3.002         -81.5
     4       4.122       4.042         -80.1
     5       5.151       5.072         -79.2
     6       6.175       6.096         -78.7
av_offset_ms sugerido: -80 ms (mediana de 6 palmas; dispersão ±1,9 ms; de -82 a -78)
Positivo = áudio mais tarde. A medida é feita sem offset: o valor substitui o atual.
Aviso: Câmera a 15,0 fps (um frame a cada 67 ms): a 30 fps a medida é mais firme (veja "fps baixo" no README)
Confiança: média — câmera abaixo de 20 fps
av_offset_ms = -80 ms salvo em /tmp/tmp.uznvqHnTw1/estado.json (antes: 0)
saída: 0
  "av_offset_ms": -80,
```

- [ ] **Step 10: Commit**

```bash
git add calibrar_av.py tests/test_calibrar_av.py
git commit -m "feat: calibrar_av.py sugere o av_offset_ms com uma tomada de palmas

Onset de cada palma no audio alinhado x contato das maos no video (pico de
movimento seguido de queda, regiao escolhida sozinha, fracao do ultimo
intervalo); mediana, dispersao robusta e confianca; --salvar grava no estado.json.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 11: README — "Como usar" e "Calibrar a sincronia (palmas)"**

Os rótulos dos botões são os da GUI (Task 11). Em `README.md`, substituir isto:

````markdown
gerada por IA. Os vídeos prontos ficam em `videos_finais/` e podem ser enviados para uma pasta do Google Drive.

## Google Drive: configuração inicial
````

por isto:

````markdown
gerada por IA. Os vídeos prontos ficam em `videos_finais/` e podem ser enviados para uma pasta do Google Drive.

## Como usar

Abra pelo atalho **Voice Studio (Orochi/Silvio)** ou, no terminal, com `./iniciar_orochi_studio.sh`. O app abre na
última tomada, para você continuar de onde parou (uma gravação que falhou ao começar é pulada). Uma tomada
interrompida por travamento é recuperada sozinha, e o resultado aparece no log.

O fluxo é um clique por etapa: **Gravar** → **Parar** → **Converter** (o vídeo sai sozinho) → **Enviar**.

### 1. Gravar (coluna da esquerda)

1. Escolha a **câmera** na lista e marque **Câmera ligada**. O preview mostra a marca d'água e duas guias da coluna
   9:16. Mantenha a boca acima da faixa escura e dentro das guias: é essa coluna que sobra num corte vertical
   (Reels, TikTok, Status).
2. Escolha o **microfone**. Com **Gravar vídeo** desmarcado, o app grava só o áudio, como antes.
3. Opcional: preencha a **duração** em segundos (mínimo 1 s). Vazio = grava até você clicar **Parar**. O limite é
   5 min: aí o app para sozinho e avisa.
4. Clique **Gravar**. O indicador **REC** acende quando chega a 1ª imagem (de 0,5 a 1,2 s depois), e o fps real
   da câmera aparece ao lado.
5. Clique **Parar**. Ele só libera 1 s depois de começar e, com vídeo, depois da 1ª imagem: um clique duplo em
   **Gravar** não para a gravação. A tomada fica em `recordings/AAAA-MM-DD_HHMMSS/`. Se o som ficou quase inaudível,
   o app avisa "Microfone mudo?".

### 2. Converter e gerar o vídeo (coluna da direita)

1. **Modelo:** escolha Silvio Santos ou Orochi. **Carregar modelo** pré-aquece o conversor. É opcional: sem ele, a
   1ª conversão demora uns 10 s a mais.
2. **Aumentar volume:** se a voz ficou baixa, clique **Aumentar volume** (ganho em dB, com limitador). A conversão
   passa a usar o áudio aumentado.
3. **Converter:** troca a voz. Na pasta da tomada saem `<modelo>.wav` e `<modelo>_IA.mp3` (com o aviso de IA nas
   tags do MP3).
4. **Vídeo:** numa tomada com vídeo, o MP4 é gerado sozinho depois de converter:
   `videos_finais/AAAA-MM-DD_HHMMSS_<modelo>_IA.mp4`. Ele leva a voz convertida, a marca d'água em todos os frames
   (selo "● IA" e a faixa "VOZ GERADA POR IA") e o aviso nos metadados.
   - **Gerar vídeo** refaz o MP4 sem converter de novo (por exemplo, depois de calibrar a sincronia).
   - **Assistir** abre o vídeo; **Abrir pasta** abre `videos_finais/`.
   - Se o vídeo não passar na conferência final (frames, duração, marca d'água, metadados), nada vai para
     `videos_finais/`. O motivo aparece no log e na seção "4. Vídeo", e o arquivo parcial fica na pasta da tomada. A
     tomada continua boa: dá para clicar **Gerar vídeo** ou **Converter** de novo.
5. **Ouvir gravação** e **Ouvir resultado** tocam o áudio original e o convertido.

### 3. Enviar para o Drive

Faça antes a configuração inicial (seção "Google Drive", abaixo). Depois:

- **Enviar** manda para a pasta do Drive o vídeo da tomada e do modelo que estão na tela, com barra de progresso.
  **Cancelar** interrompe. Se ele já estiver igual no Drive, é pulado. Para mandar todos os de `videos_finais/`, use
  `python3 enviar_drive.py` no terminal (veja "5. Testar").
- **Configurar Drive** troca o link da pasta. **Reconectar** refaz o login quando ele expira.

Fechar a janela durante a gravação pede confirmação e finaliza o arquivo antes de sair ("Finalizando gravação…").
Com uma conversão, um vídeo sendo gerado ou um envio em andamento, o app também pede confirmação.

## Calibrar a sincronia (palmas)

O app alinha a voz à imagem pelo relógio do sistema (erro de até 1 frame nos testes). A câmera e o microfone, porém,
têm atrasos internos que só uma medida real mostra. A correção é o `av_offset_ms` do `estado.json`, aplicado na
hora de gerar o vídeo. Calibre uma vez, e de novo se trocar de câmera ou de microfone:

1. Se puder, deixe a câmera a 30 fps (veja "fps baixo" em "Solução de problemas") e acenda a luz.
2. Grave uma tomada **com vídeo** de uns 15 s. Deixe as mãos inteiras no quadro e bata **pelo menos 5 palmas**
   secas, com cerca de 1 s entre elas, parando as mãos por um instante depois de cada uma. Não fale durante a
   tomada.
3. No terminal, na pasta do projeto (o app pode ficar aberto: ele não desfaz o valor salvo e lê o `av_offset_ms` na
   hora de gerar o vídeo):

   ```bash
   Applio/.venv/bin/python calibrar_av.py recordings/AAAA-MM-DD_HHMMSS
   ```

   O script mostra uma linha por palma (áudio, vídeo e a diferença) e, no fim, algo como:

   ```text
   av_offset_ms sugerido: -80 ms (mediana de 6 palmas; dispersão ±1,9 ms; de -82 a -78)
   Positivo = áudio mais tarde. A medida é feita sem offset: o valor substitui o atual.
   Confiança: alta
   ```

4. Se a confiança for **alta** ou **média**, grave o valor:

   ```bash
   Applio/.venv/bin/python calibrar_av.py recordings/AAAA-MM-DD_HHMMSS --salvar
   ```

   Ele só grava quando vê pelo menos 5 palmas no vídeo.
5. No app, clique **Gerar vídeo** numa tomada já convertida para conferir. O offset entra no render, sem converter de
   novo.

- **Confiança baixa:** poucas palmas vistas, ou palmas discordando entre si (dispersão acima de 15 ms). Refaça
  com mais luz, as mãos inteiras no quadro e palmas mais secas.
- **Confiança média:** a câmera estava abaixo de 20 fps. O valor serve, mas a 30 fps a medida fica mais firme.
- Para voltar ao padrão, ponha `"av_offset_ms": 0` no `estado.json`. Se o arquivo ficar com erro de sintaxe, o app
  avisa, usa os padrões e guarda o arquivo quebrado em `estado.json.corrompido`.
- O script não altera a tomada. Depois de calibrar, ela pode ser apagada.

## Google Drive: configuração inicial
````

- [ ] **Step 12: README — "Solução de problemas", no fim do arquivo**

Em `README.md`, substituir isto (as duas últimas linhas da seção do Drive):

````markdown
- Para revogar o acesso: <https://myaccount.google.com/permissions> → remova o app; e apague o remote com
  `rclone config delete iavoz`.
````

por isto:

````markdown
- Para revogar o acesso: <https://myaccount.google.com/permissions> → remova o app; e apague o remote com
  `rclone config delete iavoz`.

## Solução de problemas

O log embaixo, no app, mostra uma linha curta por problema. Os detalhes técnicos ficam em `studio.log` (e em
`studio_rvc.log`, para o conversor de voz).

### "Câmera em uso por outro programa (Meet/Zoom/OBS?)"

Só um programa por vez pode usar a câmera. Feche a chamada ou o OBS, ou só desligue a câmera neles. Para ver quem
está com ela:

```bash
fuser -v /dev/video0
```

O próprio preview do app também segura a câmera. Ele desliga sozinho quando a janela é minimizada e depois de
5 min sem uso; desmarque **Câmera ligada** para liberar a câmera na hora.

- "Câmera não encontrada": a câmera foi desconectada. Reconecte e escolha de novo na lista.
- "Dispositivo de vídeo errado": escolha a entrada que termina em `-video-index0`.
- "A câmera parou de enviar imagem": problema no cabo ou na porta USB. A gravação é encerrada, e o que já foi
  gravado fica salvo.

### "Microfone mudo?" ou "Microfone desconectado ou trocado"

- **"Microfone mudo?"** aparece quando o pico da gravação fica abaixo de −45 dB.
  - Confira se o microfone certo está escolhido no app.
  - Confira se ele não está mudo ou com volume zero no sistema: Configurações → Som → Entrada, ou o `pavucontrol`
    (aba "Dispositivos de entrada"). No terminal, `pactl list short sources` lista os microfones, e
    `pactl get-source-mute NOME` tem que responder "não" (ou "no").
  - Se a voz só ficou baixa, e não muda, use **Aumentar volume** antes de converter.
- **"Microfone desconectado ou trocado"**: durante a tomada, o microfone sumiu ou o sistema passou a gravar de outro
  (o app confere a cada 2 s e para na 2ª vez seguida). Reconecte, escolha o microfone de novo e grave outra vez.
- **"Não deu para conferir o microfone (o pactl não respondeu)"**: só um aviso; a gravação continua. Se aparecer
  sempre, confira se o som do sistema está funcionando (`pactl info`).

### fps baixo (câmera a ~15 fps)

A câmera A4tech entrega ~15 fps com o ajuste "Exposure, Dynamic Framerate" ligado, que é como ela está hoje.
Desligado, ela faz ~30 fps. Com pouca luz, o fps cai ainda mais, e o app avisa "Câmera a X fps (abaixo de 12) —
pouca luz?". O app mostra o fps real, mas não mexe nesse ajuste.

- A sincronia continua certa a 15 fps: o vídeo final sai sempre a 30 fps, repetindo frames. Só o movimento da boca
  fica mais "aos saltos".
- Primeiro, ponha mais luz na frente do rosto.
- Para ter 30 fps, desligue o ajuste com o `v4l2-ctl`, com o app fechado. Estes comandos **não foram testados nesta
  máquina** e precisam do pacote `v4l-utils` (`sudo apt install v4l-utils`):

  ```bash
  CAM=/dev/v4l/by-id/usb-Sonix_Technology_Co.__Ltd._A4tech_HD_720P_PC_Camera_SN0001-video-index0
  v4l2-ctl -d "$CAM" --list-ctrls | grep -i dynamic     # mostra o valor atual (1 = ligado)
  v4l2-ctl -d "$CAM" -c exposure_dynamic_framerate=0    # 30 fps; com pouca luz a imagem fica mais escura
  v4l2-ctl -d "$CAM" -c exposure_dynamic_framerate=1    # volta como estava
  ```

  O valor pode mudar quando a câmera é reconectada ou quando outro programa mexe nele. Confira com a 1ª linha.

Sem instalar nada, o mesmo ajuste muda pelo `ioctl` que o probe `docs/superpowers/probes/2026-09-26/captura/t10_fps.py`
usou para medir os 29,3 fps (`VIDIOC_S_CTRL` no controle `0x9a0903`, "Exposure, Dynamic Framerate"; valor `0` =
~30 fps, `1` = como está hoje). Com o app fechado e o `CAM` acima (troque o `0` por `1` para voltar):

```bash
python3 - "$CAM" 0 <<'EOF'
import fcntl, os, struct, sys
CID = 0x9a0903                              # "Exposure, Dynamic Framerate"
S_CTRL, G_CTRL = 0xC008561C, 0xC008561B     # VIDIOC_S_CTRL e VIDIOC_G_CTRL (como no probe t10_fps.py)
fd = os.open(sys.argv[1], os.O_RDWR | os.O_NONBLOCK)
try:
    fcntl.ioctl(fd, S_CTRL, bytearray(struct.pack("<Ii", CID, int(sys.argv[2]))))
    c = bytearray(struct.pack("<Ii", CID, 0))
    fcntl.ioctl(fd, G_CTRL, c)
    print("Exposure, Dynamic Framerate =", struct.unpack("<Ii", c)[1])
finally:
    os.close(fd)
EOF
```

A última linha mostra o valor que ficou.

### Drive: login expirou ou Drive cheio

- **"Login do Drive expirou — clique Reconectar"**: clique **Reconectar**, que abre o navegador para entrar de novo.
  No terminal, o equivalente é `rclone config reconnect iavoz:`. Se isso acontece toda semana, o app OAuth ficou
  em modo "Teste": faça o passo 2.7 da configuração do Drive (**PUBLICAR APP**).
- **"Seu Drive está cheio — os envios contam na sua cota"**: os vídeos contam na cota da **sua** conta Google
  (15 GB grátis, ~45 MB por minuto de vídeo), e não na do dono da pasta. Libere espaço (o uso aparece em
  **Armazenamento**, no menu do Drive, e a lixeira também conta) ou use uma conta com mais espaço. Depois clique
  **Enviar** de novo: o que já subiu é pulado.
- As outras mensagens do Drive estão na tabela "Mensagens de erro", na seção do Drive.
````

- [ ] **Step 13: Commit**

```bash
git add README.md
git commit -m "docs: README com o uso do app, a calibracao por palmas e a solucao de problemas

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 14: Suíte inteira e limpeza**

Run: `./run_tests.sh`
Expected: `Ran 455 tests` e `OK (skipped=7)` (as 440 das Tasks 1–11 com as correções mais os 15 desta task), com os
pulados sendo os testes de `RUN_HARDWARE`.

Run: `pgrep -a ffmpeg; git status --short`
Expected: nenhuma linha. Nenhum ffmpeg sobrando e nenhum arquivo fora do git: o plano (`docs/superpowers/…`) foi
commitado antes da Task 1, e a mídia (`recordings/`, `videos_finais/`, `estado.json`, logs) está no `.gitignore`.

**Correções da revisão (Steps 15–24).** (1) O `calibrar_av.py --salvar` regravava o `estado.json` inteiro: um
arquivo com erro de sintaxe virava os padrões (o link do Drive sumia). Agora ele grava só o `av_offset_ms` com o
`merge_estado` da Task 1, como a GUI (Task 9) e o `enviar_drive.py --pasta` (Task 8); com isso, e com o render lendo o
valor no clique (Task 11), o app pode ficar aberto durante a calibração. (2) O teste real grava o `mp4` relativo à
pasta da tomada, como a GUI, e confere o microfone com o `mic_status` da Task 6. (3) O passo manual final, com o
usuário, fecha o que só o hardware e a conta Google verificam.

- [ ] **Step 15: Teste do `--salvar` que preserva o `estado.json` corrompido (falha)**

Em `tests/test_calibrar_av.py`, substituir isto:

```python
    def test_without_salvar_estado_is_untouched(self):
```

por isto:

```python
    def test_salvar_merges_and_keeps_corrupted_estado(self):
        # --salvar grava so o av_offset_ms (merge_estado): um estado.json quebrado fica guardado em .corrompido
        estado = os.path.join(self.tmp.name, "quebrado.json")
        broken = '{"drive_pasta": "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUv",}'
        with open(estado, "w", encoding="utf-8") as f:
            f.write(broken)
        rc, out = run_main(self.dir15, "--salvar", "--estado", estado)
        self.assertEqual(rc, 0, out)
        with open(estado + ".corrompido", encoding="utf-8") as f:
            self.assertEqual(broken, f.read())
        self.assertIn("cópia guardada em quebrado.json.corrompido", out)
        saved, warn = load_estado(estado)
        self.assertIsNone(warn)
        self.assertEqual(calibrar_av.calibrate(self.dir15).suggested_ms, saved["av_offset_ms"])

    def test_without_salvar_estado_is_untouched(self):
```

- [ ] **Step 16: Rodar e ver falhar**

Run: `Applio/.venv/bin/python -m unittest tests.test_calibrar_av`
Expected: `Ran 15 tests` e `FAILED (errors=1)`: `FileNotFoundError: [Errno 2] No such file or directory:
'…/quebrado.json.corrompido'`

- [ ] **Step 17: `--salvar` com `merge_estado`**

Em `calibrar_av.py`, substituir isto:

```python
from studio.config import ESTADO_PATH, load_estado, save_estado
```

por isto:

```python
from studio.config import ESTADO_PATH, load_estado, merge_estado
```

E em `calibrar_av.py`, substituir isto:

```python
    estado, aviso = load_estado(args.estado)
    if aviso:
        print(f"Aviso: {aviso}", file=sys.stderr)
    antes = estado.get("av_offset_ms", 0)
    estado["av_offset_ms"] = cal.suggested_ms
    save_estado(estado, args.estado)
    print(f"av_offset_ms = {cal.suggested_ms:+d} ms salvo em {args.estado} (antes: {antes})")
```

por isto:

```python
    antes = load_estado(args.estado)[0]["av_offset_ms"]
    # grava so o av_offset_ms, relendo o arquivo: o app pode estar aberto (ele tambem so grava o que muda)
    _, aviso = merge_estado({"av_offset_ms": cal.suggested_ms}, args.estado)
    if aviso:
        print(f"Aviso: {aviso}", file=sys.stderr)
    print(f"av_offset_ms = {cal.suggested_ms:+d} ms salvo em {args.estado} (antes: {antes})")
```

- [ ] **Step 18: Rodar e ver passar**

Run: `Applio/.venv/bin/python -m unittest tests.test_calibrar_av`
Expected: `Ran 15 tests` e `OK`

- [ ] **Step 19: Commit**

```bash
git add calibrar_av.py tests/test_calibrar_av.py
git commit -m "fix(calibrar_av): --salvar grava so o av_offset_ms com merge_estado (preserva o estado.json corrompido)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 20: Teste real: `mp4` relativo e microfone pelo `mic_status`**

O `take.json` que a GUI grava tem o `mp4` relativo à pasta da tomada (`../../videos_finais/…`); o teste real passa a
gravar igual. A checagem do microfone fica mais estrita: `check_mic` devolve `None` também quando o pactl não
responde, e o `mic_status` distingue os dois. Em `tests/test_hardware.py`, substituir isto:

```python
            self.assertIsNone(capture.check_mic(cap.pid, index))
```

por isto:

```python
            self.assertEqual(capture.mic_status(cap.pid, index), capture.MIC_OK)     # "desconhecido" tambem falha
```

E em `tests/test_hardware.py`, substituir isto:

```python
        take.saidas[modelo.key].update(mp3="silvio_IA.mp3", mp4=final)
        take.save()
```

por isto:

```python
        take.saidas[modelo.key].update(mp3="silvio_IA.mp3", mp4=os.path.relpath(final, take.dir))   # como a GUI
        take.save()
        self.assertTrue(os.path.isfile(os.path.join(take.dir, takes.Take.load(take.dir).saidas[modelo.key]["mp4"])))
```

- [ ] **Step 21: Rodar sem hardware (fica pulado)**

Run: `Applio/.venv/bin/python -m unittest tests.test_hardware -v`
Expected: `Ran 1 test` e `OK (skipped=1)`, com o mesmo `skipped 'precisa de câmera, microfone, GPU e do modelo silvio
(RUN_HARDWARE=1)'` do Step 2. A rodada com `RUN_HARDWARE=1` fica para o Step 24, com o usuário (a câmera acende).

- [ ] **Step 22: Commit**

```bash
git add tests/test_hardware.py
git commit -m "test(hardware): mp4 relativo a pasta da tomada (como a GUI) e microfone conferido com mic_status

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 23: Suíte inteira e limpeza, de novo**

Run: `./run_tests.sh`
Expected: `Ran 456 tests` e `OK (skipped=7)` (as 12 tasks com as correções), com os pulados sendo os testes de
`RUN_HARDWARE`

Run: `pgrep -a ffmpeg; git status --short`
Expected: nenhuma linha

- [ ] **Step 24: Passo manual com o usuário (o executor não faz sozinho)**

Este passo **não tem código novo** e **não pode ser feito pelo executor sozinho**: precisa do usuário na frente da
câmera, do microfone ligado e do login na conta Google. O executor mostra os comandos, o usuário roda e os dois
conferem juntos a saída. O que for achado vira correção na task dona do módulo (teste que falha → correção → commit),
como diz o Step 2.

A pasta de destino é `https://drive.google.com/drive/folders/<ID_DA_PASTA>` (ID
`<ID_DA_PASTA>`, sem `resourcekey`). Ela é de outra conta e está compartilhada com
`<sua conta Google>`, então **o login OAuth do rclone é feito com `<sua conta Google>`**. O link não entra no
código nem no `DEFAULT_ESTADO`: ele fica no `estado.json`, gravado pelo `--pasta` abaixo.

**(a) Drive configurado.**

1. Instalar o rclone v1.75.1 pela seção "1. Instalar o rclone" do README. O `sha256sum -c` tem que dizer `SUCESSO`
   (ou `OK`); se disser `FALHOU`, não instale.
2. Criar a credencial OAuth própria pela seção "2. Criar a credencial OAuth" do README, logado como
   `<sua conta Google>` (incluindo o **PUBLICAR APP** do passo 2.7).
3. Criar o remote pela seção "3. Criar o remote `iavoz`". No navegador, entrar com `<sua conta Google>`.
4. Conferir o acesso à pasta e salvar o link:

   ```bash
   rclone lsjson iavoz: --drive-root-folder-id <ID_DA_PASTA> --max-depth 1; echo "saída: $?"
   python3 enviar_drive.py --pasta 'https://drive.google.com/drive/folders/<ID_DA_PASTA>' --dry-run
   ```

   Esperado: o `lsjson` lista a pasta (`[]` se vazia) e termina com `saída: 0`; o `enviar_drive.py` diz "Pasta do
   Drive salva em estado.json" e simula o envio sem erro (ou "Nenhum vídeo para enviar"). Se o `lsjson` falhar com
   "directory not found", a pasta não está compartilhada com essa conta, ou o login foi feito com outra.

**(b) Ponta a ponta com o app.**

1. Rodar o teste real (Steps 3 e 20): `RUN_HARDWARE=1 Applio/.venv/bin/python -m unittest tests.test_hardware -v` →
   `OK`, com `pgrep -a ffmpeg` vazio depois.
2. Abrir o app (`./iniciar_orochi_studio.sh`), gravar uns 5 s com vídeo, escolher **Silvio Santos** e clicar
   **Converter**. Esperado: "convertida para Silvio Santos ✓" e, sozinho, "Pronto: <id>_silvio_IA.mp4" na seção 4.
3. Fechar o app (assim ele não regrava o `take.json` por cima do que a CLI anota) e enviar pela CLI:

   ```bash
   python3 enviar_drive.py --arquivo videos_finais/<id>_silvio_IA.mp4
   python3 enviar_drive.py --arquivo videos_finais/<id>_silvio_IA.mp4
   grep -A3 '"enviado"' recordings/<id>/take.json
   ```

   Esperado: a 1ª vez termina com `<id>_silvio_IA.mp4: enviado` e `Resumo: 1 enviado(s), 0 pulado(s), 0 falha(s)`;
   a 2ª com `já estava igual no Drive (pulado)`; o `take.json` tem `saidas.silvio.enviado.md5` (32 dígitos hex) e o
   `quando`. O vídeo aparece na pasta do Drive.
4. Anotar se apareceu `aviso:` no envio (o Drive recusou a descrição, e o envio foi repetido sem ela; spec 9.2).
5. Abrir o app e clicar **Reconectar** uma vez: o navegador abre, o login é com `<sua conta Google>`, e o log
   mostra "Drive reconectado". Anotar se o comando pediu algo além do login (a spec marca o `rclone config update`
   como [I]).

**(c) Calibração da sincronia.**

1. Gravar no app uma tomada **com vídeo** de ~15 s com pelo menos 5 palmas, como diz a seção "Calibrar a sincronia
   (palmas)" do README.
2. Fechar o app e rodar `Applio/.venv/bin/python calibrar_av.py recordings/<id-palmas>` e, com confiança alta ou
   média, `Applio/.venv/bin/python calibrar_av.py recordings/<id-palmas> --salvar`. Esperado: "av_offset_ms = … ms
   salvo em …". Anotar o valor e a confiança.
3. Tirar a tomada de palmas de `recordings/` (por exemplo, `mv recordings/<id-palmas> /tmp/`): o app abre na última
   tomada utilizável e não tem seletor de tomadas, então assim ele volta à tomada do item (b).
4. Abrir o app e clicar **Gerar vídeo** (seção 4): o vídeo da tomada do item (b) é refeito com o `av_offset_ms` novo,
   sem converter de novo ("Vídeo pronto" no log). Clicar **Assistir** e conferir que a boca bate com a voz.

Depois, rodar de novo `pgrep -a ffmpeg` (vazio).

**Notas para o executor**

- **O teste real passou de primeira (uma rodada, 26/09) e não expôs bug nas Tasks 1–8**, então nenhuma delas ganhou
  passo extra. Tempos medidos:
  - 1º frame em 1,18 s; parada com `q` em 0,26 s (`stop_steps == ["q"]`, rc aceito).
  - Áudio alinhado em 0,17 s.
  - RVC: `load` em 10,6 s (import do torch e do Applio a frio) e a 1ª conversão de 5 s de áudio em 6,5 s (inclui
    carregar o `.pth`).
  - Render NVENC + verificação em 1,3 s; teste inteiro em 28 s.
  - O "MP3 2.43 s" do relatório inclui o `client.close()` (o worker libera a CUDA e sai), porque o cronômetro só é
    lido depois do `export_mp3`. O `export_mp3` sozinho leva 0,07 s para 5 s de áudio.
- **A câmera estava a 15 fps (Dynamic Framerate ligado).** O MKV teve 151 frames de saída, com 14,97 fps medidos, e o
  preview contou 76 frames em 5 s. O controle foi lido só para conferência (`VIDIOC_G_CTRL` no id `0x9a0903`): nome
  "Exposure, Dynamic Framerate", padrão 0, atual 1. O `v4l2-ctl` citado no README não está instalado nesta máquina,
  então aqueles comandos não foram rodados. `exposure_dynamic_framerate` é o nome que o `v4l2-ctl` deriva de
  "Exposure, Dynamic Framerate".
- **Âncora em 1,011 s é normal.** O microfone é a 1ª entrada e abre antes; a 1ª imagem chega ~1 s depois. O
  `audio.wav` alinhado descarta esse trecho. O teste aceita 0,5 s de diferença entre a duração do `audio.wav` e
  `n_frames/30`, porque o fim do áudio e o do vídeo não coincidem.
- **`taxa 48000.0 Hz` e `resíduo 5.89 ms` numa tomada de 5 s são esperados, não bug.**
  - Nos primeiros segundos os pts do PulseAudio ainda estão assentando. Em janelas de 6,3 s das tabelas reais
    `tests/fixtures/*.framemd5`, a inclinação vai de −1496 a +2395 ppm no começo e fica em ±100–500 ppm depois,
    contra +48,7 e −57,3 ppm nos 4 min inteiros.
  - Acima de 1000 ppm, o `fit_audio_clock` fica com a taxa nominal (`MAX_DRIFT`). O `asetrate` só entra com deriva
    acumulada acima de 5 ms, o que não acontece numa tomada curta.
  - O erro que sobra (poucos ms) fica bem abaixo de 1 frame, e a parte constante vai para o `av_offset_ms`.
- **O teste real não afirma nada sobre o volume**, porque a sala pode estar em silêncio. Aqui deu média −38,7 dB e
  pico −21,7 dB. Ele só exige que o `volumedetect` responda.
- **`pgrep -af <pasta temporária>`** no fim do teste pega qualquer ffmpeg ou worker que ainda cite a pasta. O `setpriv`
  executa o `pgrep`, que se exclui da busca, então a saída vazia é confiável.
- **Tempo de cada frame: `showinfo`, não a lista de pacotes.** O `ffprobe` lista pacotes, mas o 1º JPEG costuma vir
  truncado e pode não decodificar, e aí o índice dos frames decodificados escorrega. O `showinfo` mostra o
  `pts_time` de cada frame que saiu de fato. Com `-copyts`, esse pts é o mesmo da `ancora_pts` da Task 4. O
  `-nostats` evita que a linha de progresso (com `\r`) se misture às do `showinfo`.
- **Precisão medida na tomada sintética:**
  - 15 fps, áudio 80 ms atrasado: sugerido −80 ms; erro por palma de −1,5 a +1,9 ms; dispersão 1,9 ms.
  - 30 fps, áudio 45 ms adiantado: sugerido +46 ms (1 ms de erro na mediana); a pior palma errou 5,0 ms.
  - Sem a fração do último intervalo, o erro de cada palma seria de até 1 frame (67 ms a 15 fps).
  - A tomada real vai ser menos limpa: mãos não são retângulos e há desfoque de movimento. Por isso a sugestão é a
    mediana, a dispersão é robusta (1,4826 × MAD) e a confiança tem 3 níveis: baixa (menos de 5 palmas ou dispersão
    acima de 15 ms), média (câmera abaixo de 20 fps) e alta.
- **O clique do botão Parar entra no áudio.** Por isso a palma cujo pico de movimento na janela fica abaixo de 25 % da
  mediana das palmas é descartada (`WEAK_FRAC`). Sem esse filtro, `test_click_without_clap_is_ignored` falha com
  `AssertionError: 7 != 6`.
- **Os limiares do áudio supõem uma tomada só de palmas:** 10× o ruído de fundo, 10 % da palma mais forte e 250 ms de
  refratário. Falar alto durante a tomada gera onsets a mais. O filtro de movimento costuma descartá-los, mas a
  dispersão sobe.
- **`--salvar` com o app aberto (Steps 15–19).** O `--salvar` grava só o `av_offset_ms` com o `merge_estado`; a GUI
  também só grava o que muda (Task 9) e o render lê o valor no clique (Task 11). Um `estado.json` com erro de sintaxe
  fica guardado em `estado.json.corrompido`. O `--salvar` recusa (saída 1) com menos de 5 palmas vistas no vídeo.
- **A calibração com palmas reais (spec 12, item 4) não foi feita aqui:** precisa do usuário na frente da câmera. O
  1º uso do `calibrar_av.py` no hardware é esse, no Step 24.
- **O Step 20 não rodou com hardware** (nesta leva não se liga câmera, microfone nem GPU): ele só foi conferido pulado
  e com `py_compile`. A rodada com `RUN_HARDWARE=1` está no Step 24, item (b).
- **O `ioctl` do README** é o do probe `captura/t10_fps.py` (os números `0xC008561C`/`0xC008561B` saem do
  `IOWR(28, 8)`/`IOWR(27, 8)` de lá). O trecho do README foi rodado só contra `/dev/null` (`OSError: [Errno 25]
  Inappropriate ioctl for device`), para conferir a sintaxe sem abrir a câmera.
