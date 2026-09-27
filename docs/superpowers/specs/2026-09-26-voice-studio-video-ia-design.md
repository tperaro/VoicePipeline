# Voice Studio v2 — vídeo da webcam com voz trocada por IA, marca d'água e envio pro Drive

- **Data:** 2026-09-26
- **Status:** aprovada em 26/09/2026. Emendada no mesmo dia depois da revisão do plano de implementação (ver seção 15)
- **Evidência:** os testes feitos nesta máquina (webcam, microfone, GPU, modelo silvio_350e) estão em
  `docs/superpowers/probes/2026-09-26/`. Os relatórios completos ficam em `relatorios/`.
  Marcação usada nesta spec: **[V]** = medido/rodado aqui; **[I]** = inferido (docs/código), não rodado.

## 1. Objetivo

Transformar o Voice Studio (atalho "Voice Studio (Orochi/Silvio)") numa ferramenta confiável de ponta a ponta.
O fluxo completo é:

1. Gravar a **webcam e o microfone juntos**, vendo o preview da câmera na própria janela.
2. Converter a voz para Silvio Santos ou Orochi com RVC, como já é feito hoje.
3. Gerar um **MP4 com a voz convertida no lugar da original**, com a boca sincronizada e uma **marca d'água
   permanente** avisando que a voz é gerada por IA, para ninguém confundir com deepfake.
4. **Subir os vídeos prontos** para uma pasta do Google Drive **compartilhada com o usuário**, por script e por
   botão no app.

### Critérios de sucesso

- O fluxo roda com um clique por etapa: Gravar → Parar → Converter (o vídeo é gerado sozinho) → Enviar. O
  resultado é `videos_finais/AAAA-MM-DD_HHMMSS_<modelo>_IA.mp4`.
- A voz fica alinhada com a imagem. O erro sistemático é ≤ 1 frame nos testes sintéticos. No real, o erro é
  calibrável com `av_offset_ms` (seção 6.4).
- A marca d'água aparece **em todos os frames**, e o aviso em texto **sobrevive a um corte vertical 9:16**
  centralizado (Reels, TikTok, Status).
- Nenhum arquivo quebrado ou sem marca d'água chega em `videos_finais/` nem no Drive (*fail-closed*).
- Rodar `enviar_drive.py` de novo não duplica nada no Drive, e ele nunca apaga nada lá.
- Um travamento do app não deixa a câmera nem o microfone gravando escondidos.
- O modo "só áudio" continua funcionando. As gravações antigas em `recordings/` não são tocadas.

### Fora do escopo

- Upload automático após o render. O usuário escolheu script + botão.
- Controle do fps da câmera pelo app. O usuário escolheu deixar como está (ver 5.6).
- C2PA ou assinatura de proveniência. Não há ferramenta instalada, e as plataformas removem.
- Marca d'água no áudio.
- Editor de corte.
- Várias câmeras ao mesmo tempo.
- Outros sistemas operacionais.

## 2. Decisões registradas

| # | Decisão | Por quê |
|---|---|---|
| D1 | Marca d'água: selo "● IA" no canto superior direito + faixa escura embaixo, o vídeo inteiro | Escolha do usuário |
| D2 | O texto da faixa vira 3 linhas curtas que cabem na coluna central 9:16 | Com o layout de 1 linha longa, um corte 9:16 deixava só "DA POR IA · PARÓDIA/HO" [V] |
| D3 | Preview ao vivo dentro do app | Escolha do usuário |
| D4 | Drive: script `enviar_drive.py` + botão no app | Escolha do usuário |
| D5 | Pasta compartilhada **com** o usuário (edição), acessada pelo ID do link | Escolha do usuário. `--drive-shared-with-me` é a ferramenta errada [V: código do rclone] |
| D6 | rclone v1.75.1 em `~/.local/bin`, com client OAuth **próprio** do usuário | O client compartilhado do rclone será desativado em 2026 [V: docs e código v1.75.1] |
| D7 | `git init` no projeto; commits por etapa; sem push | Aprovado pelo usuário |
| D8 | Câmera sem controle de fps pelo app; o app só mostra o fps real | Escolha do usuário |
| D9 | Um único processo ffmpeg captura microfone (1ª entrada) + câmera | Mesmo relógio. Com a câmera primeiro, faltaram até 2,2 s de áudio [V] |
| D10 | RVC roda num processo *worker* separado, do mesmo venv | Isola travamentos e VRAM, e fixa o diretório de trabalho do Applio |
| D11 | Testes com `unittest` da stdlib, sem instalar nada no venv do Applio | Não mexer no ambiente do Applio |

## 3. Arquitetura

```
orochi_studio.py          ponto de entrada (o atalho continua igual): monta a GUI
studio/
  config.py               caminhos, MODELOS, defaults, estado.json (leitura/escrita atômica)
  procs.py                spawn com setpriv --pdeathsig, execução de ffmpeg/ffprobe, mapa de códigos de saída
  devices.py              microfones (pactl), câmeras (/dev/v4l/by-id), verificação de source-output, espaço em disco
  capture.py              Recorder (A/V + preview), PreviewOnly, AudioRecorder; FrameReader
  timeline.py             pacotes via ffprobe, ajuste do relógio do mic, extract_aligned_audio
  watermark.py            PNG RGBA da marca d'água (PIL), layout na coluna segura
  render.py               render_final: .part → verificação → os.replace para videos_finais/
  takes.py                Take + take.json, varredura de recuperação na abertura
  rvc_worker.py           roda como subprocesso: carrega o Applio uma vez, protocolo JSON por linha, conversão em pedaços
  rvc_client.py           lado do app: inicia o worker, envia os jobs, recebe os resultados
  drive.py                só stdlib: link → ID, comandos rclone, trava, leitura do log JSON, erros em PT-BR
  gui.py                  App Tk (fila de eventos, estados, layout)
enviar_drive.py           CLI (roda com o python3 do sistema ou o do venv)
tests/                    unittest + fixtures (tabelas de tempos reais do mic)
```

**Regras de fronteira**

- Só `gui.py` importa `tkinter`.
- Só `rvc_worker.py` importa torch ou Applio. O processo da GUI nunca importa torch, então abre rápido e não
  segura VRAM.
- `drive.py` e `enviar_drive.py` usam **só a stdlib**.
- Todo caminho é absoluto. Ninguém chama `os.chdir` no processo da GUI.

### 3.1 Pastas e artefatos

```
recordings/<id>/            id = AAAA-MM-DD_HHMMSS (uma pasta por tomada nova)
  take.json                 estado e metadados da tomada (ver 3.2)
  raw.mkv | raw.wav         captura crua (A/V ou só áudio)
  audio.wav                 áudio alinhado: t=0 no 1º frame bom do vídeo (no só-áudio, é o próprio raw)
  boosted.wav               após "Aumentar volume" (opcional)
  <modelo>.wav              saída do RVC (taxa do modelo; silvio = 40 kHz [V])
  <modelo>_IA.mp3           MP3 com tags ID3 de aviso
  wm_<W>x<H>_<modelo>.png   marca d'água usada no render
  render.part.mp4           render em andamento (nunca sobe pro Drive)
videos_finais/
  <id>_<modelo>_IA.mp4      só vídeos verificados; é a única pasta que vai pro Drive
  .envio.lock               trava compartilhada entre GUI e CLI
estado.json                 preferências que a GUI grava: mic, câmera, link da pasta, av_offset_ms
studio.log                  log técnico (tracebacks, stderr resumido dos processos)
```

### 3.2 `take.json`

```json
{
  "id": "2026-09-26_101500", "modo": "av",
  "status": "gravando|gravado|convertido|renderizado|falhou",
  "mic": "alsa_input...", "camera": "/dev/v4l/by-id/...-video-index0",
  "video": {"w": 1280, "h": 720, "ancora_pts": 0.132, "n_frames": 300, "fps_medido": 14.6},
  "audio_fit": {"inicio": 0.061, "taxa_real": 48002, "gaps": 0, "residuo_ms": 1.1},
  "saidas": {"silvio": {"wav": "silvio.wav", "mp3": "silvio_IA.mp3", "mp4": "../../videos_finais/...mp4",
                        "enviado": {"md5": "...", "quando": "..."}}}
}
```

- A escrita é atômica: grava em `.tmp` e depois faz `os.replace`.
- Só a thread principal da GUI (ou a CLI) altera o `take.json`.
- Um erro de conversão ou de vídeo fica **por modelo**, em `saidas[<modelo>]["erro"]`, e um sucesso posterior o
  apaga. O status `"falhou"` fica reservado para a gravação (ou para o áudio ilegível de uma tomada recuperada).
- Se o `take.json` não puder ser gravado (ex.: disco cheio), a mudança é desfeita na memória e nada segue.

## 4. Configuração

- `MODELOS` fica no código:
  - `silvio`: rótulo "Silvio Santos", aviso "Não é a voz real de Silvio Santos"
  - `orochi`: rótulo "Orochi", aviso "Não é a voz real do Orochi"
- Os textos fixos também ficam no código: "VOZ GERADA POR IA", "paródia · homenagem", e os metadados em PT e EN.
- `render()` **recusa** gerar vídeo sem texto de aviso para o modelo.
- `estado.json` é escrito pela GUI, por `calibrar_av.py --salvar` e por `enviar_drive.py --pasta`, sempre com
  gravação atômica e **só das chaves alteradas**: quem grava relê o arquivo e mescla. Assim um programa não
  desfaz o que outro gravou com o app aberto.
- Se o arquivo estiver corrompido, o app mostra um aviso e usa os defaults, sem cair. Antes de gravar por cima,
  o arquivo quebrado é preservado como `estado.json.corrompido`. Um valor de tipo errado (ex.:
  `"drive_pasta": 123`) volta para o default, com aviso. Os campos são:
  - mic
  - câmera
  - `gravar_video`
  - `drive_pasta` (link)
  - `av_offset_ms` (default 0, sem controle na GUI)

## 5. Captura

### 5.1 Comando A/V (verificado [V])

```python
["setpriv", "--pdeathsig", "TERM", "--",
 "ffmpeg", "-hide_banner", "-loglevel", "info", "-y", "-copyts",
 "-f", "pulse", "-thread_queue_size", "1024", "-sample_rate", "48000", "-channels", "1", "-i", MIC,
 "-f", "v4l2", "-input_format", "mjpeg", "-video_size", "1280x720", "-framerate", "30",
 "-ts", "mono2abs", "-thread_queue_size", "512", "-i", CAM,
 "-map", "1:v", "-map", "0:a", "-c:v", "copy", "-c:a", "pcm_s16le",
 "-avoid_negative_ts", "make_zero", "-f", "matroska", RAW_MKV,
 "-map", "1:v", "-vf", "scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2,fps=15",
 "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
```

**Por que cada opção existe:**

- **Microfone como 1ª entrada:** o áudio sempre cobre o frame 0 [V].
- **`-ts mono2abs` + `-copyts`:**
  - Sem `mono2abs`, a câmera usa uptime e o áudio usa o relógio do sistema. Nesse caso o ffmpeg grava só
    0,05 s de áudio, com rc=0 e **sem aviso** [V].
  - Sem `copyts`, cada entrada é zerada separadamente e o desvio entre áudio e vídeo se perde [V].
- **`make_zero`:** sem ele, a duração vira ~1,79e9 s [V].
- **MJPEG copiado dentro de MKV:** gasta 18,5 % de um núcleo e 115 MB de RAM, e o MKV sobrevive a
  travamentos [V].
- **Frame do preview:** tem sempre exatamente 480×270×3 = 388 800 bytes, graças ao `pad`.
- **Câmera por caminho estável:** usa `/dev/v4l/by-id/*-video-index0`, que existe para a A4tech [V]. O
  `/dev/video1` é o nó de metadados e falha com rc 231 [V].
- **Duração:** **nunca** usar `-t`, `-to` ou `-frames` no gravador. Com `-copyts`, isso gera um arquivo
  vazio com rc=0 [V]. A duração opcional é um `root.after` que dispara a parada normal.

### 5.2 Preview antes de gravar e só-áudio

- **Preview antes de gravar (PreviewOnly):**
  - É um processo que só tem a saída `pipe:1`.
  - Liga apenas com "Câmera ligada" marcado **e** "Gravar vídeo" marcado.
  - Desliga quando a janela é minimizada (`<Unmap>`) e após 5 min sem atividade. Assim o Meet e o Zoom não
    ficam bloqueados.
  - Ao clicar Gravar, o PreviewOnly é parado com `q` e o Recorder é iniciado. O buraco no preview é de
    ~0,7 s [V].
- **Só-áudio (AudioRecorder):**
  - Comando: `ffmpeg -f pulse … -i MIC -c:a pcm_s16le -f wav raw.wav`.
  - Substitui o `parecord`.
  - O cabeçalho WAV fica correto parando com `q`, SIGTERM ou SIGINT [V].

### 5.3 Leitor de frames (FrameReader)

- É uma thread que lê o `stdout` com um laço `readinto` até completar 388 800 bytes. Com `bufsize=0`, cada
  leitura devolve no máximo 64 KiB.
- Guarda só o último frame e um número de sequência, protegidos por lock. Nunca toca no Tk.
- Tem `try/finally` que fecha `p.stdout`. Se o leitor morrer, o ffmpeg trava e a tomada se perde [V]. Com o
  pipe fechado, o ffmpeg continua gravando o arquivo e termina com rc 224 [V].
- Continua drenando até EOF, inclusive depois do pedido de parada.

### 5.4 Início (thread principal do Tk)

1. **Checagens antes de iniciar:**
   - O microfone está em `pactl list short sources`, sem os `.monitor`.
   - O nó da câmera existe.
   - Há pelo menos 2 GB livres.
2. **Processo:** `Popen` na **thread principal**, com:
   - `stdin=PIPE`, `stdout=PIPE`;
   - `stderr` indo para o arquivo `recordings/<id>/ffmpeg.log` (nunca um pipe);
   - `bufsize=0`.
   - O `setpriv` faz o ffmpeg morrer se o app morrer [V].
   - `preexec_fn` fica proibido porque não é seguro com threads.
   - O PDEATHSIG vale enquanto a *thread* que fez o spawn viver [V]. Por isso o gravador nasce na thread
     principal.
3. **Falha rápida:** se o processo sair em menos de 0,5 s, a mensagem vem do código de saída:

   | Código | Mensagem |
   |---|---|
   | 240 | "Câmera em uso por outro programa (Meet/Zoom/OBS?)" |
   | 254 | "Câmera não encontrada" |
   | 231 | "Dispositivo de vídeo errado" |

4. **Indicador REC:** só acende quando chega o 1º frame. O início leva de 0,44 s a 1,2 s [V].
   - **Parar** fica desabilitado por 1 s depois do início e, no A/V, até chegar o 1º frame (o que vier por
     último). Assim um duplo clique em Gravar não para a gravação na hora. A duração mínima é 1 s.
   - Uma gravação A/V com menos de 3 pacotes de vídeo é recusada com "Gravação curta demais".
5. **Microfone certo:**
   - Após ~2 s, e depois a cada 2 s, o app roda `LC_ALL=C pactl list source-outputs`. O gravador só aparece
     no `pactl` depois de ~1,2 s.
   - No bloco com `application.process.id == pid`, confere se `Source:` corresponde ao índice do microfone
     escolhido.
   - O `setpriv` executa o ffmpeg no mesmo processo, então o pid é o do ffmpeg.
   - Se o índice não bater ou sumir em **2 checagens seguidas**, o app para e mostra "Microfone desconectado ou
     trocado". Um nome inválido grava do mic padrão sem erro [V].
   - Se o próprio `pactl` falhar ou estourar o tempo, o resultado é "não sei": o app registra um aviso no log
     e **não** para a gravação.
6. **Câmera parada:** se nenhum frame novo chegar por 2 s durante a gravação, o app faz uma parada graciosa e
   mostra "A câmera parou de enviar imagem".
7. **Limite de duração:** 5 min. Aos 5 min o app para sozinho e avisa, porque o RVC foi validado até 300 s.

### 5.5 Parada (sem travar a UI)

1. Escreve `b"q\n"` no `stdin`. O processo sai em 0,21–0,26 s com rc 0 [V].
2. Uma thread trabalhadora espera até 5 s → fecha `stdout` e espera 3 s → envia SIGTERM e espera 3 s →
   envia SIGKILL.
3. Os códigos 0, 255 e 224 são aceitos. O **sucesso** é decidido pelo `ffprobe`: os dois streams existem e a
   duração é maior que 0.
4. O resultado volta para a GUI pela fila de eventos.
5. Depois roda o `volumedetect`, como o app já faz. Se `max_volume` for menor que −45 dB, o app mostra
   "Microfone mudo?".

### 5.6 Fps da câmera

- A câmera entrega ~14,5 fps com o ajuste atual "Exposure, Dynamic Framerate" = 1. Desligado, ela faz
  29,3 fps [V].
- O app **não** mexe nesse ajuste (D8). Ele mostra o fps real, contado pela sequência do preview e pelos
  pacotes gravados, e avisa se ficar abaixo de 12 fps.

## 6. Alinhamento boca/voz

### 6.1 Âncora de vídeo

- O 1º frame MJPEG costuma vir truncado ("EOI missing" em 11 de 30 logs [V]).
- Por isso a âncora é o pts do **2º pacote** de vídeo (`ancora_pts`).
- No render, o vídeo começa nesse frame (`trim=start_frame=1`).

### 6.2 Ajuste do relógio do microfone (`timeline.fit_audio_clock`)

**O problema**

- O timestamp do 1º pacote de áudio erra entre −13 e +91 ms, e o erro varia de tomada para tomada [V].
- Os microfones derivam em relação ao relógio do sistema [V]:
  - Generalplus: +48,6 ppm
  - ME6S: −56,1 ppm
  - Isso dá ~15 ms a cada 5 min.

**O método**

- A partir dos pacotes (`ffprobe -show_entries packet=pts_time,size`), o app ajusta
  `pts_i ≈ inicio + s · amostras_acum_i / 48000` por mínimos quadrados.
- O ajuste usa os pacotes depois de 2 s. Tomadas com menos de 4 s usam os pacotes depois de 0,5 s.
- O resultado guardado em `take.json` é:
  - `inicio`: tempo real da amostra 0;
  - `taxa_real = 48000 / s`;
  - `residuo_ms`;
  - `gaps`: quantos saltos maiores que 30 ms existem.

### 6.3 `extract_aligned_audio(take) → audio.wav`

1. Calcula `delta = inicio − ancora_pts`.
   - Se `delta > 0`, usa `adelay=<n>S:all=1`.
   - Se `delta < 0`, usa `atrim=start_sample=<n>`.
   - Nos dois casos `n = round(|delta|·48000)`, e o filtro vem entre `asetpts=N/SR/TB`.
2. Se `|taxa_real − 48000|·duração/48000 > 5 ms`, acrescenta
   `asetrate=<taxa_real arredondada>,aresample=48000:resampler=soxr` [I: filtros padrão; teste unitário
   obrigatório].
3. Se `gaps > 0`, o caminho passa a ser `aresample=async=1:min_hard_comp=0.03:first_pts=0`, que foi validado
   num buraco de 0,5 s [V]. O app registra no log: "AVISO: o áudio da tomada <id> teve <n> buraco(s) acima de
   30 ms; o alinhamento usou o modo assíncrono — confira a sincronia".
4. No teste sintético, bip e flash ficaram alinhados com offset de 0,08 ms (áudio 0,40 s atrasado, 0,25 s
   adiantado, deslocado +5 s, VFR) [V].
5. O WAV sai mono, 48 kHz, PCM16.

### 6.4 `av_offset_ms`

- É a correção constante para o atraso interno da câmera e do mic, que é desconhecido.
- É aplicada **no render**, então mudar o valor não exige converter de novo. O render lê o valor do
  `estado.json` no clique, então calibrar com o app aberto funciona.
- Valor positivo = áudio mais tarde.
- A calibração é feita uma vez com palmas diante da câmera: mediana de pelo menos 5 palmas, de preferência
  com boa luz, porque a 15 fps a precisão é ±33 ms.

## 7. Conversão (RVC)

### 7.1 Worker

- É iniciado pela GUI na thread principal:
  `setpriv --pdeathsig TERM -- <venv>/python -m studio.rvc_worker`, com `cwd=Applio/`.
- Faz `os.chdir(APPLIO_DIR)` **uma vez**. O Applio lê `os.getcwd()` no import e usa caminhos relativos
  (`rmvpe.pt`, `assets/config.json`) [V: código].
- **Protocolo:** JSON por linha, num descritor **dedicado**. Na partida, o worker duplica o fd 1 original para
  o protocolo e redireciona o fd 1 para o fd 2, para que os prints do Applio e das libs nativas não corrompam
  o protocolo. O `stderr` vai para `studio_rvc.log`.
- **Operações:** `ping`, `load` (pré-aquece; é o botão "Carregar modelo") e `convert`
  (`{id, input, output, model, pth, index}`).
- **Respostas:** `{id, ok, duracao, sr, pedacos}` ou `{id, ok:false, erro}`.
- **Entrada:** `boosted.wav` se existir, senão `audio.wav`, como hoje.
- **Parâmetros:** os mesmos de hoje: pitch 0, index_rate 0.75, volume_envelope 1.0, protect 0.33, rmvpe,
  `split_audio=False`, `clean_audio=False`, WAV, contentvec. `split_audio=True` descarta o silêncio final [V].
- **Depois de cada job:** `torch.cuda.empty_cache()`.
- **Fila:** os jobs de GPU rodam um por vez, em fila única (RVC, depois render com NVENC). O pico medido é
  6,4 GB de 8 GB [V].
- Se o worker morrer, a GUI mostra "Conversor reiniciado" e recria o worker no próximo job.
- A saída é gravada em `.part`, validada e então movida com `os.replace`.

### 7.2 Tomadas longas

- Até ~41 s o RVC preserva o tempo: início e fim com diferença de até 2 ms [V].
- Acima disso, os cortes internos do RVC adiantam 10 ms a cada ~38 s [V].
- **Tomadas com mais de 40 s:** o worker corta em pedaços de no máximo 30 s, no quadro de 10 ms mais
  silencioso dentro de ±3 s. Cada pedaço é convertido com 0,5 s de contexto de cada lado e recolocado no
  offset exato, com crossfade de 5 ms.
- Resultado: deriva 0 ms em 90 s e em 300 s, com pico de VRAM de ~3,3 GB [V].
- Referência: `probes/2026-09-26/rvc/anchored.py`.

### 7.3 MP3

- Gerado a partir do WAV convertido, com o nome `<modelo>_IA.mp3`.
- Tags ID3: `title` e `comment` com o aviso de voz gerada por IA.
- O render **nunca** usa o MP3, por causa do atraso do encoder.

### 7.4 Aumentar volume

- É mantido. O filtro passa a ser `volume=<ganho>dB,alimiter=limit=0.97:latency=1`.
- +15 dB em s16 cortava os picos. `latency=1` evita o atraso de 5 ms do limitador [V: `ffmpeg -h`].

## 8. Vídeo final e marca d'água

### 8.1 Marca d'água (`watermark.py`)

- É um PNG RGBA do **tamanho exato do frame da tomada**, lido pelo `ffprobe`. O render confere
  `PNG == frame` antes de começar. Um PNG 1280×720 sobre um frame 640×480 deixou **0 pixels** de marca
  visíveis [V].
- **Fontes:** DejaVu Sans Bold e Regular, carregadas por caminho. A Noto, que é a "Sans" do fontconfig, não
  tem "●" [V]. O ponto do selo é desenhado como círculo, então não depende de glifo.
- **Selo:** canto superior direito, margem de 3 % da altura, cápsula escura translúcida, ponto vermelho e
  "IA" em branco (D1).
- **Faixa:**
  - Largura total, preto a 55 %.
  - **3 linhas centralizadas**, cada uma cabendo na coluna central 9:16, com
    `max_w = round(h·9/16) − 2·margem` (361 px em 720p):
    1. "VOZ GERADA POR IA", negrito
    2. "Não é a voz real de Silvio Santos", regular (texto do modelo)
    3. "paródia · homenagem", regular menor
  - O tamanho da fonte é proporcional à altura e encolhe até caber.
- **O mesmo PNG no preview:** reduzido para 480×270 e composto sobre o preview (0,34 ms/frame), mais guias
  tênues da coluna 9:16. Assim o usuário mantém a boca acima da faixa.

### 8.2 Render (`render.py`, base verificada [V])

```python
["setpriv", "--pdeathsig", "TERM", "--", "nice", "-n", "10",
 "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
 "-i", RAW_MKV, "-i", CONV_WAV, "-i", WM_PNG,
 "-filter_complex",
   "[0:v]trim=start_frame=1,setpts=PTS-STARTPTS,fps=30,tpad=stop_mode=clone:stop=2,"
   "trim=end_frame={N},setpts=PTS-STARTPTS,format=yuv420p[v0];"
   "[2:v]format=yuva420p[wm];[v0][wm]overlay=0:0:format=yuv420,format=yuv420p[v];"
   "[1:a]aresample=48000:resampler=soxr,asetpts=N/SR/TB{OFFSET},"
   "apad=whole_len={S},atrim=end_sample={S},asetpts=N/SR/TB[a]",
 "-map", "[v]", "-map", "[a]",
 "-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "21", "-b:v", "0",
 "-maxrate", "6M", "-bufsize", "12M", "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", "30",
 "-colorspace", "smpte170m", "-color_primaries", "smpte170m", "-color_trc", "smpte170m", "-color_range", "tv",
 "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "1", "-movflags", "+faststart",
 "-metadata", "title=Paródia/homenagem - voz gerada por IA",
 "-metadata", "comment=Voz sintética gerada por IA (conversão RVC). <aviso do modelo>.",
 "-metadata", "description=AI-generated/synthetic voice (RVC voice conversion). Parody/tribute. Not the real voice of <nome>.",
 "-f", "mp4", RENDER_PART]
```

- **Número de frames:** `N = round((pts_último + dur_último − ancora_pts) · 30)`, calculado com os pacotes do
  `ffprobe`, sem decodificar. O cabeçalho diz 30 fps mesmo quando a câmera entrega 15. O DURATION do MKV não
  serve [V].
- **Amostras de áudio:** `S = N · 1600`.
- **`{OFFSET}`:** vazio quando `av_offset_ms == 0`. Caso contrário vira `,adelay=<n>S:all=1` ou
  `,atrim=start_sample=<n>`.
- **Proibido:** `apad` + `-shortest`. Essa combinação trava no ffmpeg 6.1.1 [V].
- **`aresample=48000` explícito:** sem ele o AAC vai sozinho para 44,1 kHz [V].
- **Fallback:** se o NVENC falhar, troca pelas flags `-c:v libx264 -preset veryfast -crf 20 -maxrate 6M -bufsize 12M -profile:v high` [V].
- **Timeout:** 5× a duração da tomada + 60 s.
- **Tomada curta:** com menos de 2 frames úteis, o render recusa com "Gravação curta demais para gerar o vídeo".
- **Disco:** um erro de disco vira mensagem em PT-BR ("Disco cheio — libere espaço" no ENOSPC), sem deixar
  `.part` fora da pasta da tomada.
- **Cancelamento:** botão Cancelar envia SIGTERM.
- **Desempenho:** NVENC faz 60 s de 720p em ~9,5 s [V].

### 8.3 Verificação (*fail-closed*)

Antes do `os.replace(render.part.mp4 → videos_finais/<id>_<modelo>_IA.mp4)`, o app confere:

1. O `ffprobe` mostra 1 stream de vídeo com `N` frames (via `-count_packets`) e 1 de áudio.
2. A duração do áudio fica a até 1 frame da duração do vídeo.
3. O `comment` contém "IA", e `title` e `description` existem.
4. **Pixels da marca:** o app decodifica os frames 0, N/2 e N−1. Nos pixels em que o PNG tem texto branco
   opaco, a luminância de saída precisa ser maior que 200 em pelo menos 95 % deles. Na área da faixa, ela
   precisa ser menor que a da imagem-fonte.

Se qualquer item falhar, o `.part` fica em `recordings/<id>/`, o erro vai para `saidas[<modelo>]["erro"]` (ver
3.2) e o motivo aparece em PT-BR. **Não existe caminho de código que renderize sem a sobreposição.**

## 9. Google Drive

### 9.1 Instalação e configuração (uma vez)

1. **rclone v1.75.1:**
   - Baixar o zip oficial `linux-amd64` e conferir o `SHA256SUMS`, que é assinado com PGP pela chave
     `FBF737EC…FF3B54FA` [V].
   - Descompactar com `unzip -j` o binário em `~/.local/bin/rclone`, que já está no PATH [V].
   - O rclone do apt (1.60) é velho demais [V].
2. **Credencial OAuth própria no Google Cloud**, com a conta Google que tem edição na pasta:
   - Criar um projeto.
   - Ativar a Drive API.
   - Tela de consentimento: External, escopo `…/auth/drive`.
   - Criar um client "Desktop app".
   - **PUBLISH APP**: em modo Testing o token expira em 7 dias [V: docs].
   - O guia passo a passo em PT-BR vai para o README.
3. **Criar o remote:** `rclone config create iavoz drive client_id=… client_secret=… scope=drive`. Esse
   comando abre o navegador para o login e aceita a tela de "app não verificado".
   - O `root_folder_id` **não** fica salvo no remote. Ele vai em cada execução a partir de `estado.json`,
     então dá para trocar de pasta sem logar de novo.
   - `scope=drive` é obrigatório. `drive.file` não escreve numa pasta que o app não criou [V: docs].
4. **Link da pasta:** colado na GUI em "Configurar Drive" ou passado com `enviar_drive.py --pasta <link>`.
   - Fica salvo em `estado.json`.
   - O parser aceita `/folders/<ID>`, `/u/N/folders/`, `open?id=`, `folderview?id=`, `resourcekey` e o ID puro
     (12 casos [V]).
   - Rejeita links `/file/d/` e hosts que não são do Google.

### 9.2 Envio (`drive.py`)

**Antes de enviar**

- O nome casa com `*_IA.mp4`.
- O arquivo não é `.part`.
- O caminho real (`realpath`) fica dentro de `videos_finais/`.
- O `ffprobe` confirma que o `comment` é **exatamente** o comentário de IA do modelo tirado do nome. Isso é
  *fail-closed*: vale também para `enviar_drive.py --arquivo`.

**Comando por arquivo**

```python
["rclone", "copyto", LOCAL, f"iavoz:{NOME}",
 "--drive-root-folder-id", FOLDER_ID, *(["--drive-resource-key", RK] if RK else []),
 "--use-json-log", "--stats", "1s", "--stats-log-level", "NOTICE", "-v",
 "--retries", "3", "--retries-sleep", "10s", "--low-level-retries", "10",
 "--drive-chunk-size", "64M", "--transfers", "1", "--drive-stop-on-upload-limit"]
```

**Idempotência**

- O `copyto` pula arquivos idênticos (tamanho e mtime) e atualiza os mudados como nova revisão, sem duplicar
  [V: local e código].
- **Proibidos:** `sync`, `move`, `delete`, `dedupe`, `link` e `--no-check-dest`.

**Durante e depois**

- **Progresso:** o app lê o JSON do `stderr` linha a linha e ignora as linhas que não são JSON.
- **Verificação:** roda `rclone lsjson iavoz:NOME --stat --hash --hash-type md5` com a mesma pasta e compara
  tamanho e MD5 com o `hashlib.md5` local. O resultado vai para o `take.json` (`enviado`).
- **Trava:** `fcntl.flock` em `videos_finais/.envio.lock`, compartilhado entre a GUI e a CLI. Os envios
  acontecem um de cada vez.
- **Cancelar:** envia SIGTERM ao rclone.

**Descrição no Drive** (tentativa [I])

- A flag é `-M --metadata-set "description=Voz gerada por IA…"`.
- Se o Drive recusar, o app repete o envio sem ela e registra no log.

### 9.3 Erros em PT-BR

| Sinal | Mensagem |
|---|---|
| `rclone` não encontrado | "rclone não instalado — veja o README" |
| rc 1 + "didn't find section in config" | "Drive não configurado — rode a configuração" |
| `invalid_grant` / "config reconnect" | "Login do Drive expirou — clique Reconectar" (`rclone config update iavoz config_refresh_token=true` [I]) |
| rc 3 / "directory not found" | "Essa conta Google não tem acesso a essa pasta (ou o link está errado)" |
| rc 7 + `storageQuotaExceeded` | "Seu Drive está cheio — os envios contam na sua cota" |
| outro | últimas linhas do log + código |

### 9.4 CLI `enviar_drive.py`

- `enviar_drive.py [--pasta LINK] [--arquivo CAMINHO] [--dry-run]`.
- Sem `--arquivo`, envia todos os `videos_finais/*_IA.mp4`. Os que já estão iguais no Drive são pulados.
- Mostra progresso e um resumo no fim (enviados, pulados, falhas).
- Sai com código diferente de 0 se algum envio falhar.
- Pode ir para o cron.

### 9.5 Segurança

- O token fica em `~/.config/rclone/rclone.conf`, com modo 0600, e **nunca** dentro do projeto.
- O app nunca loga `config show` nem usa `-vv`.
- Com `scope=drive`, o token acessa o Drive inteiro da conta. Recomendação ao usuário, sem obrigatoriedade:
  usar uma conta Google dedicada que só tenha acesso à pasta.

## 10. Interface (Tk)

- Janela de ~1100×720, redimensionável, com tamanho mínimo e duas colunas.
- **Esquerda:**
  - câmera (combobox by-id) e "Câmera ligada";
  - canvas do preview 480×270 com a marca d'água e as guias 9:16;
  - fps real e indicador REC;
  - microfone, "Gravar vídeo", duração, Gravar/Parar.
- **Direita:**
  1. Modelo (Carregar modelo)
  2. Aumentar volume (ganho em dB, com limitador)
  3. Converter, com os botões "▶ Ouvir gravação" e "▶ Ouvir resultado" (ficam aqui para economizar altura)
  4. Vídeo: "Gerar vídeo" (automático após converter quando a tomada tem vídeo), status, Assistir, Abrir pasta
  5. Drive: Enviar, Configurar Drive, Reconectar, barra de progresso, Cancelar. **Enviar** manda o MP4 da tomada
     e do modelo que estão na tela. Para mandar todos os de `videos_finais/`, o caminho é `enviar_drive.py`.
- **Embaixo:** o log. Ele fica com a sobra de altura, para a coluna direita aparecer sempre inteira.
- Durante a gravação ficam desabilitados Volume, Converter, Gerar vídeo, os dois Ouvir e Assistir, porque o som
  tocaria nas caixas e entraria no microfone.

**Regras de concorrência**

- **Fila única de eventos:** as threads trabalhadoras publicam eventos e a thread principal consome com
  `root.after(50 ms)`. Só a thread principal altera o estado do App.
- **Entradas dos jobs:** cada job recebe uma cópia das entradas no momento do clique e nunca lê `StringVar`.
- **Preview:** um `ImageTk.PhotoImage` reutilizado, com `paste()` a cada frame novo (0,15 ms [V]), consultado
  a cada 66 ms.
- **Botões:** todo job publica "concluído" ou "falhou" num `finally`, e isso reabilita os botões.
- **Erros não tratados:** `report_callback_exception` e `threading.excepthook` escrevem uma linha curta em
  PT-BR no log e o traceback em `studio.log`.
- **Fechar a janela:**
  - Gravando: o app pede confirmação, para com `q` e mostra "Finalizando gravação…" antes de fechar. A tomada
    é conferida na próxima abertura.
  - Com conversão, render ou envio em andamento: o app pede confirmação.
- **Abertura:**
  - O app carrega a última tomada **utilizável** (gravado, convertido ou renderizado, com a gravação no
    disco), para continuar de onde parou. Uma tentativa que falhou ao começar não esconde a tomada boa
    anterior.
  - Tomadas com status "gravando" são recuperadas: se o `ffprobe` ler o arquivo com duração maior que 0,
    viram "gravado". Se não ler, o app tenta um remux `-c copy`. O resultado vai para o log.

## 11. Processos

- `procs.spawn` sempre prefixa `setpriv --pdeathsig TERM --`.
- **Quem faz o spawn:**
  - O Recorder, o PreviewOnly e o worker do RVC nascem na thread principal.
  - O render, o ffprobe e o rclone nascem na mesma thread que espera por eles.
- Todo ffmpeg que não é o gravador recebe `-nostdin` e `stdin=DEVNULL`.
- O render roda com `nice 10`.

## 12. Testes

Os testes usam `python -m unittest discover -s tests`, com o python do venv do Applio. As partes que só usam
stdlib também rodam com `python3`.

| Módulo | O que é testado |
|---|---|
| watermark | Tamanho do PNG = tamanho pedido (720p, 480p, 600p). Todo pixel de texto opaco fica na coluna 9:16. O texto muda por modelo. Recusa modelo sem aviso. |
| timeline | Ajuste sobre as **tabelas reais** `deriva-mic/*.framemd5` (deriva ≈ +48,6 e −56,1 ppm). Tomada sintética (lavfi) com áudio 0,40 s atrasado e 0,25 s adiantado → bip na amostra do flash (≤ 1 ms). Buraco de 0,5 s → caminho async. |
| render | Tomada sintética flash+bip → MP4 com offset ≤ 1 frame. Duração do áudio = duração do vídeo. Tags presentes. `av_offset_ms` desloca o áudio corretamente. Verificação falha → `.part` não é movido. PNG com tamanho errado → recusa. |
| capture | Montagem dos comandos (mic primeiro, flags obrigatórias, sem `-t`). Mapa de códigos de saída. Protocolo de parada com um **ffmpeg falso** (script que ignora `q`, trava o stdout etc.). Leitura de frames com leitura parcial. |
| rvc | Protocolo do worker com um conversor falso (identidade). Matemática dos pedaços: saída remontada == entrada, fora do crossfade. Mensagens de stdout do "Applio" não quebram o protocolo. |
| drive | Parser de link (12 casos). Comandos do rclone. Leitura do log JSON. Mapa de erros. Trava. Checagem *fail-closed*. Tudo com um **rclone falso** no PATH. |
| takes | Gravação atômica e recuperação de tomada "gravando". |

**Teste real** (manual, com `RUN_HARDWARE=1`)

1. 5 s de A/V com a câmera e o mic reais.
2. Conversão com o modelo silvio.
3. Render e verificação.
4. Tomada de palmas para calibrar `av_offset_ms`.
5. Envio de 1 vídeo para a pasta de verdade, depois da configuração do usuário.

## 13. Riscos ainda não verificados

- **Sincronia labial real:** depende do atraso interno da câmera. Só a tomada de palmas mede. É o motivo de
  existir o `av_offset_ms`.
- **Mic ou câmera desconectados no meio da tomada:** o comportamento foi inferido. Os watchdogs dos itens
  5.4.5 e 5.4.6 cobrem esses casos.
- **Descrição do arquivo no Drive e o comando de reconexão:** não foram testados contra o Drive real. Os dois
  têm fallback.
- **Metadados apagados pelo WhatsApp e pelo Instagram ao recodificar:** é provável [I]. A proteção que vale é
  a marca d'água gravada na imagem.
- **Cota:** os envios contam na cota de 15 GB do próprio usuário (~45 MB por minuto de vídeo).

## 14. Ordem de implementação (a detalhar no plano)

1. Base: `config`, `procs`, `takes`, a estrutura de testes.
2. `watermark` e `render`, que são testáveis só com mídia sintética.
3. `timeline` (`extract_aligned_audio` e o ajuste do relógio).
4. `capture` (com o ffmpeg falso, depois o teste real).
5. `rvc_worker` e `rvc_client` (com a conversão em pedaços).
6. `drive` e `enviar_drive.py`, além da instalação do rclone e do README com o passo a passo.
7. `gui`: novo layout, fila de eventos, preview e as etapas 4 e 5.
8. Teste real ponta a ponta e calibração.

## 15. Emendas após a revisão do plano (26/09/2026)

O plano de implementação foi prototipado com testes, e dois revisores o atacaram. As emendas abaixo já estão
aplicadas no texto acima; esta lista só registra o que mudou em relação à versão aprovada:

- **3.2:** erro de conversão ou de vídeo fica por modelo, em `saidas[<modelo>]["erro"]`; o status "falhou" fica
  só para a gravação. Se o `take.json` não gravar, nada segue.
- **4:** `estado.json` gravado por mescla das chaves alteradas (GUI, `calibrar_av.py`, `enviar_drive.py`), com o
  arquivo corrompido preservado como `.corrompido` e tipos errados voltando ao default.
- **5.4:** Parar desabilitado por 1 s (e até o 1º frame no A/V); gravação A/V com menos de 3 pacotes recusada.
- **5.4.5:** 1ª checagem do mic em ~2 s; para só em 2 checagens seguidas; `pactl` sem resposta só gera aviso.
- **6.3:** texto exato do aviso de buraco no áudio.
- **6.4:** o render lê `av_offset_ms` do arquivo no clique.
- **8.2 e 8.3:** tomada curta demais e erro de disco com mensagens em PT-BR; o erro vai para `saidas`.
- **9.2:** antes de enviar, o arquivo precisa estar dentro de `videos_finais/` e ter o comentário exato de IA.
- **10:** rótulo "Aumentar volume", botões Ouvir dentro de "Converter", Enviar manda o vídeo da tela, fechar
  gravando pede confirmação, a abertura carrega a última tomada utilizável e o log absorve a sobra de altura.
- **Pasta do Drive de destino:** `https://drive.google.com/drive/folders/<ID_DA_PASTA>`. É
  de outra conta, compartilhada com `<sua conta Google>`, que é a conta do login OAuth do rclone. O
  link vai no `estado.json`, não no código.
