#!/usr/bin/env bash
# Entry point for the Baseten training job (see config.py). Also runs locally:
#   BT_CHECKPOINT_DIR=/tmp/ckpt MAX_STEPS=20 WHISPER_BASE=openai/whisper-tiny ./run.sh
set -euo pipefail
cd "$(dirname "$0")"

# The job workspace is this folder plus whatever BASETEN.md's staging step copied in.
# Locally, the scripts live one level up.
if [ -f ../finetune_whisper.py ]; then TRAIN_DIR=..; else TRAIN_DIR=.; fi

pip install -q -r "$TRAIN_DIR/requirements.txt"
apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq libsndfile1 ffmpeg >/dev/null 2>&1 || true

DATA="${DATA_DIR:-$TRAIN_DIR/../data}"
mkdir -p "$DATA"
# Download and decode jacktol/atc-dataset (public, ~820 MB) if manifests are missing.
if [ ! -f "$DATA/asr/manifests/train.jsonl" ]; then
  python "$TRAIN_DIR/prep_data.py"
fi

CKPT="${BT_CHECKPOINT_DIR:-$DATA/checkpoints/whisper-atc}"
EXTRA=""
if [ -n "${MAX_STEPS:-}" ]; then EXTRA="--max-steps $MAX_STEPS"; fi

python "$TRAIN_DIR/finetune_whisper.py" \
  --model "${WHISPER_BASE:-openai/whisper-small}" \
  --train "$DATA/asr/manifests/train.jsonl" \
  --val "$DATA/asr/manifests/val.jsonl" \
  --output "$CKPT" \
  --label "${RUN_LABEL:-baseten}" \
  $EXTRA

# Held-out numbers: stock vs tuned, and a CTranslate2 export for faster-whisper serving.
python "$TRAIN_DIR/eval_wer.py" --manifest "$DATA/asr/manifests/test.jsonl" \
  --stock "${WHISPER_BASE:-openai/whisper-small}" --tuned "$CKPT/best" --limit "${EVAL_LIMIT:-500}" \
  --tag "baseten_${RUN_LABEL:-run}" --label "${RUN_LABEL:-baseten}-heldout"
python "$TRAIN_DIR/export_ct2.py" --ckpt "$CKPT/best" --out "$CKPT/ct2-float16" --quantization float16 --no-check
cp "$TRAIN_DIR/RUNS.md" "$CKPT/RUNS.md" 2>/dev/null || true
cp -r "$TRAIN_DIR/results" "$CKPT/results" 2>/dev/null || true
echo "done. checkpoints in $CKPT"
