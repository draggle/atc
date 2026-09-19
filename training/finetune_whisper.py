"""Fine-tune Whisper on ATC audio with the Hugging Face Seq2SeqTrainer.

Recipe (docs/04-training.md): 16 kHz, lr 1e-5, 500 warm-up steps, batch 16 x
grad accum 2, up to 10 epochs, early stopping patience 3 on validation WER,
dynamic augmentation (gaussian noise, pitch shift, time stretch, clipping) whose
intensity decays linearly to zero over training.

Checkpoints go under $BT_CHECKPOINT_DIR when set (Baseten training jobs), else
--output. The best model by val WER is copied to <output>/best.

Smoke test on a laptop:
  .venv/bin/python finetune_whisper.py --model openai/whisper-tiny \
      --train ../data/asr/manifests/synth_train.jsonl --val ../data/asr/manifests/synth_val.jsonl \
      --output ../data/checkpoints/whisper-smoke --max-steps 20 --batch-size 4 --grad-accum 1 \
      --warmup-steps 2 --eval-steps 10 --label SMOKE

Real run (H100):
  python finetune_whisper.py --model openai/whisper-small \
      --train data/asr/manifests/train.jsonl --val data/asr/manifests/val.jsonl --label small-full
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import warnings

import numpy as np
import soundfile as sf
import torch

warnings.filterwarnings("ignore", category=FutureWarning, module="librosa")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runlog import log_run  # noqa: E402
from text_norm import normalize  # noqa: E402

SR = 16000


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def read_manifest(path: str, limit: int | None = None) -> list[dict]:
    rows = [json.loads(l) for l in open(path) if l.strip()]
    return rows[:limit] if limit else rows


class AugmentState:
    """Shared mutable augmentation intensity in [0, 1]. A TrainerCallback decays it."""

    def __init__(self, start: float = 1.0):
        self.intensity = start


def augment(audio: np.ndarray, rng: random.Random, intensity: float) -> np.ndarray:
    """Apply a random subset of augmentations scaled by intensity."""
    if intensity <= 0:
        return audio
    import librosa

    y = audio
    if rng.random() < 0.5 * intensity + 0.1:
        snr_db = rng.uniform(8, 30) + (1 - intensity) * 10
        noise = np.random.default_rng(rng.randrange(1 << 30)).normal(0, 1, len(y)).astype(np.float32)
        p = np.mean(y ** 2) + 1e-9
        y = y + noise * np.sqrt(p / 10 ** (snr_db / 10))
    if rng.random() < 0.3 * intensity:
        y = librosa.effects.pitch_shift(y, sr=SR, n_steps=rng.uniform(-2, 2) * intensity)
    if rng.random() < 0.3 * intensity:
        y = librosa.effects.time_stretch(y, rate=rng.uniform(0.9, 1.15))
    if rng.random() < 0.3 * intensity:
        gain = rng.uniform(1.5, 4.0)
        y = np.clip(y * gain, -0.9, 0.9) / gain
    return np.nan_to_num(np.clip(y, -1, 1)).astype(np.float32)


class ManifestDataset(torch.utils.data.Dataset):
    def __init__(self, rows: list[dict], processor, aug: AugmentState | None, seed: int = 0):
        self.rows = rows
        self.processor = processor
        self.aug = aug
        self.rng = random.Random(seed)

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        r = self.rows[i]
        audio, sr = sf.read(r["path"], dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != SR:
            import librosa

            audio = librosa.resample(audio, orig_sr=sr, target_sr=SR)
        if self.aug is not None:
            audio = augment(audio, self.rng, self.aug.intensity)
        feats = self.processor.feature_extractor(audio, sampling_rate=SR, return_tensors="np").input_features[0]
        labels = self.processor.tokenizer(r["text"]).input_ids
        return {"input_features": feats, "labels": labels}


@dataclass
class Collator:
    processor: object

    def __call__(self, batch):
        feats = torch.tensor(np.stack([b["input_features"] for b in batch]))
        label_feats = [{"input_ids": b["labels"]} for b in batch]
        labels_batch = self.processor.tokenizer.pad(label_feats, return_tensors="pt")
        labels = labels_batch["input_ids"].masked_fill(labels_batch.attention_mask.ne(1), -100)
        # Trainer prepends the decoder start token itself; drop it if the tokenizer added it.
        if (labels[:, 0] == self.processor.tokenizer.convert_tokens_to_ids("<|startoftranscript|>")).all():
            labels = labels[:, 1:]
        return {"input_features": feats, "labels": labels}


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

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
    ap.add_argument("--model", default="openai/whisper-small")
    ap.add_argument("--train", required=True)
    ap.add_argument("--val", required=True)
    ap.add_argument("--output", default=str(Path(__file__).resolve().parents[1] / "data" / "checkpoints" / "whisper-atc"))
    ap.add_argument("--max-steps", type=int, default=-1)
    ap.add_argument("--epochs", type=float, default=10)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--warmup-steps", type=int, default=500)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--grad-accum", type=int, default=2)
    ap.add_argument("--eval-steps", type=int, default=500)
    ap.add_argument("--patience", type=int, default=3)
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "mps", "cpu"])
    ap.add_argument("--no-augment", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="cap train and val rows (smoke)")
    ap.add_argument("--val-limit", type=int, default=None, help="cap val rows used at eval points (laptop)")
    ap.add_argument("--eval-batch-size", type=int, default=None, help="default batch-size // 2")
    ap.add_argument("--grad-checkpoint", action="store_true", help="gradient checkpointing (less memory, ~30%% slower)")
    ap.add_argument("--label", default="run", help="tag for RUNS.md, e.g. SMOKE")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    from transformers import (EarlyStoppingCallback, Seq2SeqTrainer, Seq2SeqTrainingArguments,
                              TrainerCallback, WhisperForConditionalGeneration, WhisperProcessor)

    device = pick_device(args.device)
    out_dir = Path(os.environ.get("BT_CHECKPOINT_DIR") or args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"device={device}  output={out_dir}")
    torch.manual_seed(args.seed)

    processor = WhisperProcessor.from_pretrained(args.model)
    model = WhisperForConditionalGeneration.from_pretrained(args.model)
    model.generation_config.language = "en"
    model.generation_config.task = "transcribe"
    model.generation_config.forced_decoder_ids = None
    model.config.forced_decoder_ids = None
    model.config.use_cache = False
    if args.grad_checkpoint:
        model.gradient_checkpointing_enable()

    train_rows = read_manifest(args.train, args.limit)
    val_rows = read_manifest(args.val, args.val_limit or args.limit)
    aug = None if args.no_augment else AugmentState(1.0)
    train_ds = ManifestDataset(train_rows, processor, aug, seed=args.seed)
    val_ds = ManifestDataset(val_rows, processor, None)

    def compute_metrics(pred):
        import jiwer

        pred_ids = pred.predictions
        label_ids = np.where(pred.label_ids != -100, pred.label_ids, processor.tokenizer.pad_token_id)
        hyps = [normalize(t) for t in processor.batch_decode(pred_ids, skip_special_tokens=True)]
        refs = [normalize(t) for t in processor.batch_decode(label_ids, skip_special_tokens=True)]
        pairs = [(r, h) for r, h in zip(refs, hyps) if r]
        if not pairs:
            return {"wer": 1.0}
        return {"wer": jiwer.wer([r for r, _ in pairs], [h for _, h in pairs])}

    class DecayAugment(TrainerCallback):
        def on_step_begin(self, a, state, control, **kw):
            if aug is not None and state.max_steps:
                aug.intensity = max(0.0, 1.0 - state.global_step / state.max_steps)

    class LossLog(TrainerCallback):
        def __init__(self):
            self.losses = []

        def on_log(self, a, state, control, logs=None, **kw):
            if logs and "loss" in logs:
                self.losses.append((state.global_step, logs["loss"]))

    loss_log = LossLog()

    targs = Seq2SeqTrainingArguments(
        output_dir=str(out_dir),
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.eval_batch_size or max(1, args.batch_size // 2),
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        warmup_steps=args.warmup_steps,
        num_train_epochs=args.epochs,
        max_steps=args.max_steps,
        fp16=(device == "cuda"),
        use_cpu=(device == "cpu"),
        eval_strategy="steps",
        eval_steps=args.eval_steps,
        save_strategy="steps",
        save_steps=args.eval_steps,
        save_total_limit=2,
        logging_steps=max(1, min(25, args.eval_steps // 2)),
        predict_with_generate=True,
        generation_max_length=128,
        load_best_model_at_end=True,
        metric_for_best_model="wer",
        greater_is_better=False,
        report_to=[],
        dataloader_num_workers=0,
        remove_unused_columns=False,
        seed=args.seed,
    )

    trainer = Seq2SeqTrainer(
        model=model,
        args=targs,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        data_collator=Collator(processor),
        compute_metrics=compute_metrics,
        processing_class=processor.feature_extractor,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=args.patience), DecayAugment(), loss_log],
    )

    t0 = time.time()
    trainer.train()
    duration = time.time() - t0
    final = trainer.evaluate()
    best = out_dir / "best"
    trainer.save_model(str(best))
    processor.save_pretrained(str(best))
    print(f"saved best model to {best}")

    result = {
        "val_wer": round(float(final.get("eval_wer", float("nan"))), 4),
        "first_loss": loss_log.losses[0][1] if loss_log.losses else None,
        "last_loss": loss_log.losses[-1][1] if loss_log.losses else None,
        "steps": trainer.state.global_step,
        "best_dir": str(best),
    }
    print(json.dumps(result, indent=2))
    log_run(
        "whisper_finetune", args.label,
        base_model=args.model,
        data={"train": args.train, "n_train": len(train_rows), "val": args.val, "n_val": len(val_rows)},
        hyperparams={"lr": args.lr, "warmup": args.warmup_steps, "batch": args.batch_size,
                     "grad_accum": args.grad_accum, "max_steps": args.max_steps, "epochs": args.epochs,
                     "patience": args.patience, "augment": not args.no_augment, "fp16": device == "cuda",
                     "device": device, "grad_checkpoint": args.grad_checkpoint, "eval_steps": args.eval_steps},
        duration_s=duration,
        result=result,
    )


if __name__ == "__main__":
    main()
