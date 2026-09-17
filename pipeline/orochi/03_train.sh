#!/bin/bash
# Pre-processa, extrai features e treina o modelo RVC do Orochi via CLI do Applio.
# Treino original: 250 epocas, depois retomado ate 350 (mesmo comando com --total-epoch 350).
set -euo pipefail
source "$(dirname "$0")/../env.sh"
activate_applio
cd "$APPLIO_DIR"

MODEL=orochi
DATASET="$DATA_DIR/orochi/dataset/orochi"
TOTAL_EPOCH="${TOTAL_EPOCH:-350}"

python core.py preprocess --model-name "$MODEL" --dataset-path "$DATASET" \
  --sample-rate 40000 --cpu-cores 8 --cut-preprocess Automatic \
  --process-effects --noise-reduction

python core.py extract --model-name "$MODEL" --f0-method rmvpe --gpu 0 \
  --sample-rate 40000 --embedder-model contentvec

python core.py train --model-name "$MODEL" --vocoder HiFi-GAN \
  --save-every-epoch 25 --save-only-latest --total-epoch "$TOTAL_EPOCH" \
  --sample-rate 40000 --batch-size 8 --gpu 0 --pretrained --index-algorithm Auto \
  2>&1 | tee "$ROOT/training/orochi/train_${TOTAL_EPOCH}e.local.log"

# Gera o .index caso o treino nao tenha gerado
[ -f "logs/$MODEL/$MODEL.index" ] || python core.py index --model-name "$MODEL" --index-algorithm Auto
