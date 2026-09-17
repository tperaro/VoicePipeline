# VoicePipeline

Pipeline de clonagem de voz com RVC ([Applio](https://github.com/IAHispano/Applio)) para duas vozes, **Orochi** e **Silvio Santos**, mais o **Voice Studio**, um app desktop (Tkinter) para gravar sua voz, aumentar o volume e converter para uma das vozes treinadas.

Projeto pessoal de homenagem, sem fins comerciais. Respeite os [termos de uso do Applio](https://github.com/IAHispano/Applio/blob/main/TERMS_OF_USE.md) e não use as vozes para enganar ninguém nem se passar por essas pessoas.

## Estrutura

```
app/
  voice_studio.py            # app: modelo -> gravar -> volume -> converter
  iniciar_voice_studio.sh    # abre o app com o venv do Applio
  voice-studio.desktop       # atalho de desktop (ajuste o caminho)
pipeline/
  env.sh                     # caminhos compartilhados + commit fixo do Applio
  00_setup.sh                # instala o Applio, as dependências e liga os modelos
  orochi/01_download.sh      # 5 faixas oficiais do YouTube
  orochi/02_separate_vocals.sh  # Demucs htdemucs -> só a voz -> dataset
  orochi/03_train.sh         # preprocess + extract + train (CLI core.py)
  silvio/01_download.sh      # entrevista no YouTube
  silvio/02_build_dataset.sh # recorta os trechos em que só o Silvio fala
  silvio/03_train.sh         # preprocess + extract + train + index
  infer.sh                   # converte um áudio via linha de comando
  tools/record.sh            # grava do microfone (parecord)
  tools/compare_checkpoints.py  # mesmo áudio em várias épocas
  tools/mix_with_instrumental.sh # efeito na voz + mixagem com o instrumental
models/                      # modelos finais (Git LFS)
  orochi/orochi_350e_18550s.pth, orochi.index
  silvio/silvio_350e_13650s.pth, silvio.index
training/                    # config.json, model_info.json e logs dos treinos originais
```

O repo **não** inclui áudio bruto, stems, datasets, features extraídas, checkpoints intermediários, G/D de retomada de treino nem resultados de inferência. O pipeline recria tudo isso em `data/`, `Applio/logs/` e `recordings/`, que ficam no `.gitignore`.

## Setup

Requisitos: Linux, GPU NVIDIA (CUDA 12.8), `git`, `git-lfs`, [`uv`](https://docs.astral.sh/uv/), `ffmpeg`, `pulseaudio-utils` (`parecord`/`pactl`) e `python3-tk`.

```bash
git lfs install
git clone git@github.com:tperaro/VoicePipeline.git
cd VoicePipeline
pipeline/00_setup.sh
```

O `00_setup.sh` clona o Applio no commit `7b9f3fa` dentro de `Applio/`, cria o venv (Python 3.12), baixa os pré-treinados e cria symlinks de `models/<voz>/` para `Applio/logs/<voz>/`.

## Voice Studio (app)

```bash
app/iniciar_voice_studio.sh
```

1. Escolha a voz (Orochi / Silvio Santos) e clique em **Carregar modelo**.
2. Escolha o microfone e clique em **Gravar**. Deixe a duração em branco para parar manualmente.
3. **Aumentar volume** (padrão +15 dB) é opcional.
4. **Converter** gera `recordings/take_<data>_<voz>.wav` e o `.mp3`.

O app usa o checkpoint com mais épocas em `Applio/logs/<voz>/`. Se o Applio estiver em outro lugar, use `VOICE_STUDIO_HOME=/raiz` ou `APPLIO_DIR=/caminho/Applio`.

Pela linha de comando:

```bash
pipeline/tools/record.sh 15 minha_voz.wav
pipeline/infer.sh orochi minha_voz.wav        # ganho padrão de 15 dB
```

Parâmetros de inferência: `f0=rmvpe`, `index_rate=0.75`, `protect=0.33`, `pitch=0` e embedder `contentvec`.

## Retreinar do zero

### Orochi

```bash
pipeline/orochi/01_download.sh         # data/orochi/raw_audio
pipeline/orochi/02_separate_vocals.sh  # data/orochi/separated + dataset/orochi
pipeline/orochi/03_train.sh            # TOTAL_EPOCH=350 por padrão
```

O Demucs rodou num env conda separado (`conda create -n applio python=3.10 && pip install demucs numpy`).

### Silvio Santos

```bash
pipeline/silvio/01_download.sh
pipeline/silvio/02_build_dataset.sh
pipeline/silvio/03_train.sh
```

### Configuração dos treinos originais

| | Orochi | Silvio Santos |
|---|---|---|
| Fonte | 5 músicas oficiais, só a voz (Demucs) | Entrevista, 5 trechos |
| Duração do dataset | 20m19s | 6m03s |
| Sample rate | 40k | 40k |
| Preprocess | Automatic + effects + noise reduction | Automatic, sem effects, normalização `post` |
| F0 / embedder | rmvpe / contentvec | rmvpe / contentvec |
| Vocoder / pré-treinado | HiFi-GAN f0G40k/f0D40k | HiFi-GAN f0G40k/f0D40k |
| Batch | 8 | 4 |
| Épocas | 250, depois retomado até 350 | 350 |
| Checkpoint versionado | `orochi_350e_18550s.pth` | `silvio_350e_13650s.pth` |

No Silvio, a comparação de checkpoints (`tools/compare_checkpoints.py silvio take.wav 225 250 350`) apontou o 225e como perto do ponto ótimo pelas curvas de loss. O modelo versionado é o final (350e), o mesmo que o app usa.
