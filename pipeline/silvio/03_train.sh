#!/bin/bash
# Treina o modelo RVC do Silvio chamando os scripts internos do Applio
# com os mesmos parametros usados no treino original (350 epocas).
set -euo pipefail
source "$(dirname "$0")/../env.sh"
activate_applio
cd "$APPLIO_DIR"

MODEL=silvio
DATASET="$DATA_DIR/silvio/dataset/silvio"
CPU="${CPU:-12}"
mkdir -p "logs/$MODEL"

# exp_dir dataset sr cpus cut effects noise_red noise_strength chunk_len overlap normalization
python rvc/train/preprocess/preprocess.py \
  "logs/$MODEL" "$DATASET" 40000 "$CPU" Automatic False False 0.5 3.0 0.3 post

# exp_dir f0 cpus gpu sr embedder embedder_custom include_mutes
python rvc/train/extract/extract.py \
  "logs/$MODEL" rmvpe "$CPU" 0 40000 contentvec None 2

# model save_every total_epoch pretrainG pretrainD gpu batch sr
# save_only_latest save_every_weights cache_in_gpu cleanup vocoder checkpointing
python rvc/train/train.py \
  "$MODEL" 25 350 \
  rvc/models/pretraineds/hifi-gan/f0G40k.pth \
  rvc/models/pretraineds/hifi-gan/f0D40k.pth \
  0 4 40000 True True False True HiFi-GAN False \
  2>&1 | tee "$ROOT/training/silvio/train_350e.local.log"

python rvc/train/process/extract_index.py "logs/$MODEL" Auto
