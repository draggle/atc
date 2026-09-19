"""Fine-tune a RoBERTa cross-encoder readback checker.

Input: `controller </s></s> pilot` (the tokenizer's sentence-pair encoding).
Output: one of 8 classes (correct + 7 error kinds). Recipe from HAAWAII via
docs/07-build-spec.md: lr 2e-5, batch 64, AdamW, up to 20,000 steps, eval every
N steps with early stopping on held-out accuracy.

Checkpoints go under $BT_CHECKPOINT_DIR when set, else --output. The best
model lands in <output>/best with labels.json next to it.

Smoke on a laptop (2000 examples, 200 steps, mps):
  .venv/bin/python finetune_checker.py --model distilroberta-base \
      --train ../data/checker/train.jsonl --val ../data/checker/heldout.jsonl \
      --limit 2000 --max-steps 200 --batch-size 16 --eval-steps 100 \
      --output ../data/checkpoints/checker-smoke --label SMOKE

Real (H100):
  python finetune_checker.py --model roberta-base --train data/checker/train.jsonl \
      --val data/checker/heldout.jsonl --label roberta-base-50k
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_checker_data import LABELS  # noqa: E402
from runlog import log_run  # noqa: E402

LABEL2ID = {l: i for i, l in enumerate(LABELS)}


def read_jsonl(path: str, limit: int | None = None) -> list[dict]:
    rows = [json.loads(l) for l in open(path) if l.strip()]
    return rows[:limit] if limit else rows


class PairDataset(torch.utils.data.Dataset):
    def __init__(self, rows: list[dict], tokenizer, max_len: int = 128):
        enc = tokenizer([r["controller"] for r in rows], [r["pilot"] for r in rows],
                        truncation=True, max_length=max_len)
        self.enc = enc
        self.labels = [LABEL2ID[r["label"]] for r in rows]

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, i):
        item = {k: torch.tensor(v[i]) for k, v in self.enc.items()}
        item["labels"] = torch.tensor(self.labels[i])
        return item


def pick_device(flag: str) -> str:
    if flag != "auto":
        return flag
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="roberta-base")
    ap.add_argument("--train", required=True)
    ap.add_argument("--val", required=True, help="held-out split; used for early stopping and reporting")
    ap.add_argument("--output", default=str(Path(__file__).resolve().parents[1] / "data" / "checkpoints" / "checker"))
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--max-steps", type=int, default=20000)
    ap.add_argument("--eval-steps", type=int, default=500)
    ap.add_argument("--patience", type=int, default=4)
    ap.add_argument("--warmup-steps", type=int, default=200)
    ap.add_argument("--max-len", type=int, default=128)
    ap.add_argument("--limit", type=int, default=None, help="cap train rows (smoke)")
    ap.add_argument("--val-limit", type=int, default=None)
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "mps", "cpu"])
    ap.add_argument("--label", default="run")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    from transformers import (AutoModelForSequenceClassification, AutoTokenizer, DataCollatorWithPadding,
                              EarlyStoppingCallback, Trainer, TrainerCallback, TrainingArguments)

    device = pick_device(args.device)
    out_dir = Path(os.environ.get("BT_CHECKPOINT_DIR") or args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)
    print(f"device={device} output={out_dir}")

    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.model, num_labels=len(LABELS), id2label=dict(enumerate(LABELS)), label2id=LABEL2ID)

    train_rows = read_jsonl(args.train, args.limit)
    val_rows = read_jsonl(args.val, args.val_limit)
    train_ds = PairDataset(train_rows, tok, args.max_len)
    val_ds = PairDataset(val_rows, tok, args.max_len)

    def compute_metrics(p):
        preds = p.predictions.argmax(-1)
        acc = float((preds == p.label_ids).mean())
        correct_mask = p.label_ids == LABEL2ID["correct"]
        false_alarm = float((preds[correct_mask] != LABEL2ID["correct"]).mean()) if correct_mask.any() else 0.0
        return {"accuracy": acc, "false_alarm_rate": false_alarm}

    class LossLog(TrainerCallback):
        def __init__(self):
            self.losses = []

        def on_log(self, a, state, control, logs=None, **kw):
            if logs and "loss" in logs:
                self.losses.append((state.global_step, logs["loss"]))

    loss_log = LossLog()

    targs = TrainingArguments(
        output_dir=str(out_dir),
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size * 2,
        learning_rate=args.lr,
        warmup_steps=args.warmup_steps,
        weight_decay=0.01,
        max_steps=args.max_steps,
        fp16=(device == "cuda"),
        use_cpu=(device == "cpu"),
        eval_strategy="steps",
        eval_steps=args.eval_steps,
        save_strategy="steps",
        save_steps=args.eval_steps,
        save_total_limit=2,
        logging_steps=max(1, min(50, args.eval_steps // 2)),
        load_best_model_at_end=True,
        metric_for_best_model="accuracy",
        greater_is_better=True,
        report_to=[],
        seed=args.seed,
        dataloader_num_workers=0,
    )
    trainer = Trainer(
        model=model, args=targs, train_dataset=train_ds, eval_dataset=val_ds,
        data_collator=DataCollatorWithPadding(tok), compute_metrics=compute_metrics,
        processing_class=tok,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=args.patience), loss_log],
    )

    t0 = time.time()
    trainer.train()
    duration = time.time() - t0
    final = trainer.evaluate()
    best = out_dir / "best"
    trainer.save_model(str(best))
    tok.save_pretrained(str(best))
    (best / "labels.json").write_text(json.dumps(LABELS))
    print(f"saved best model to {best}")

    result = {
        "heldout_accuracy": round(float(final["eval_accuracy"]), 4),
        "heldout_false_alarm_rate": round(float(final["eval_false_alarm_rate"]), 4),
        "chance": round(1 / len(LABELS), 4),
        "first_loss": loss_log.losses[0][1] if loss_log.losses else None,
        "last_loss": loss_log.losses[-1][1] if loss_log.losses else None,
        "steps": trainer.state.global_step,
        "best_dir": str(best),
    }
    print(json.dumps(result, indent=2))
    log_run("checker_finetune", args.label, base_model=args.model,
            data={"train": args.train, "n_train": len(train_rows), "val": args.val, "n_val": len(val_rows)},
            hyperparams={"lr": args.lr, "batch": args.batch_size, "max_steps": args.max_steps,
                         "eval_steps": args.eval_steps, "patience": args.patience, "warmup": args.warmup_steps,
                         "max_len": args.max_len, "device": device, "fp16": device == "cuda"},
            duration_s=duration, result=result)


if __name__ == "__main__":
    main()
