#!/usr/bin/env bash
# Entry point for the Baseten checker training job (see config.py). Also runs locally:
#   BT_CHECKPOINT_DIR=/tmp/ckpt MAX_STEPS=200 N_PAIRS=2000 CHECKER_BASE=distilroberta-base ./run.sh
set -euo pipefail
cd "$(dirname "$0")"
if [ -f ../finetune_checker.py ]; then TRAIN_DIR=..; else TRAIN_DIR=.; fi

pip install -q -r "$TRAIN_DIR/requirements.txt"

DATA="${DATA_DIR:-$TRAIN_DIR/../data}"
CKPT="${BT_CHECKPOINT_DIR:-$DATA/checkpoints/checker}"
python "$TRAIN_DIR/gen_checker_data.py" --n "${N_PAIRS:-50000}" --seed 0 --out "$DATA/checker"

python "$TRAIN_DIR/finetune_checker.py" \
  --model "${CHECKER_BASE:-roberta-base}" \
  --train "$DATA/checker/train.jsonl" --val "$DATA/checker/heldout.jsonl" \
  --max-steps "${MAX_STEPS:-20000}" --batch-size "${BATCH:-64}" --eval-steps "${EVAL_STEPS:-500}" \
  --output "$CKPT" --label "${RUN_LABEL:-baseten}"

python "$TRAIN_DIR/eval_checker.py" --ckpt "$CKPT/best" --data "$DATA/checker/heldout.jsonl" \
  --out "$CKPT/checker_eval.json" --label "${RUN_LABEL:-baseten}-heldout"
cp "$TRAIN_DIR/RUNS.md" "$CKPT/RUNS.md" 2>/dev/null || true
echo "done. checkpoint in $CKPT/best"
