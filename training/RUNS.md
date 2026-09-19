# Training runs

Appended automatically by `runlog.log_run`. Every entry is a run that actually executed. Labels say what it was for; SMOKE means a pipeline check, not a result to quote.

## 2026-09-19 04:31 EDT | whisper_finetune | SMOKE

- base model: `openai/whisper-tiny`
- data: `{"n_train": 40, "n_val": 6, "train": "../data/asr/manifests/synth_train.jsonl", "val": "../data/asr/manifests/synth_val.jsonl"}`
- hyperparameters: `{"augment": true, "batch": 4, "device": "mps", "epochs": 10, "fp16": false, "grad_accum": 1, "lr": 1e-05, "max_steps": 20, "patience": 3, "warmup": 2}`
- duration: 0.4 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"best_dir": "../data/checkpoints/whisper-smoke/best", "first_loss": 5.9152061462402346, "last_loss": 2.7117755889892576, "steps": 20, "val_wer": 0.2388}`

## 2026-09-19 04:32 EDT | wer_eval | SMOKE-synthetic-audio

- base model: `stock:openai/whisper-tiny`
- data: `{"manifest": "../data/asr/manifests/synth_test.jsonl", "n_clips": 12}`
- hyperparameters: `{"greedy": true, "normalizer": "text_norm.normalize"}`
- duration: 0.0 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"deletions": 5, "insertions": 18, "seconds_per_clip": 0.115, "substitutions": 37, "wer": 0.4878}`

## 2026-09-19 04:32 EDT | wer_eval | SMOKE-synthetic-audio

- base model: `tuned:../data/checkpoints/whisper-smoke/best`
- data: `{"manifest": "../data/asr/manifests/synth_test.jsonl", "n_clips": 12}`
- hyperparameters: `{"greedy": true, "normalizer": "text_norm.normalize"}`
- duration: 0.0 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"deletions": 2, "insertions": 7, "seconds_per_clip": 0.041, "substitutions": 26, "wer": 0.2846}`

## 2026-09-19 04:34 EDT | wer_eval | stock-baseline-real-heldout

- base model: `stock:openai/whisper-tiny`
- data: `{"manifest": "../data/asr/manifests/test.jsonl", "n_clips": 100}`
- hyperparameters: `{"greedy": true, "normalizer": "text_norm.normalize"}`
- duration: 0.1 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"deletions": 115, "insertions": 318, "seconds_per_clip": 0.053, "substitutions": 605, "wer": 1.0411}`

## 2026-09-19 04:34 EDT | wer_eval | stock-baseline-real-heldout

- base model: `stock-base`
- data: `{"manifest": "../data/asr/manifests/test.jsonl", "n_clips": 100}`
- hyperparameters: `{"greedy": true, "normalizer": "text_norm.normalize"}`
- duration: 0.2 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"deletions": 94, "insertions": 288, "seconds_per_clip": 0.102, "substitutions": 534, "wer": 0.9188}`

## 2026-09-19 04:34 EDT | wer_eval | stock-baseline-real-heldout

- base model: `stock-small`
- data: `{"manifest": "../data/asr/manifests/test.jsonl", "n_clips": 100}`
- hyperparameters: `{"greedy": true, "normalizer": "text_norm.normalize"}`
- duration: 0.5 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"deletions": 90, "insertions": 215, "seconds_per_clip": 0.3, "substitutions": 365, "wer": 0.672}`

## 2026-09-19 04:36 EDT | checker_finetune | SMOKE

- base model: `distilroberta-base`
- data: `{"n_train": 2000, "n_val": 1000, "train": "../data/checker/train.jsonl", "val": "../data/checker/heldout.jsonl"}`
- hyperparameters: `{"batch": 16, "device": "mps", "eval_steps": 100, "fp16": false, "lr": 2e-05, "max_len": 128, "max_steps": 200, "patience": 4, "warmup": 20}`
- duration: 1.2 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"best_dir": "../data/checkpoints/checker-smoke/best", "chance": 0.125, "first_loss": 2.068870391845703, "heldout_accuracy": 0.533, "heldout_false_alarm_rate": 0.9921, "last_loss": 1.2596484375, "steps": 200}`

## 2026-09-19 04:38 EDT | checker_eval | SMOKE

- base model: `../data/checkpoints/checker-smoke/best`
- data: `{"data": "../data/checker/heldout.jsonl", "n": 1000}`
- hyperparameters: `{"batch_size": 64}`
- duration: 0.1 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"accuracy": 0.534, "detection_rate": 0.9977, "false_alarm_rate": 0.9921, "latency_ms": {"batched_per_pair": 6.971, "single_pair": {"p50": 29.11, "p95": 205.32}}}`

## 2026-09-19 04:46 EDT | checker_finetune | laptop-distilroberta-12k-1200steps

- base model: `distilroberta-base`
- data: `{"n_train": 12000, "n_val": 1000, "train": "../data/checker/train.jsonl", "val": "../data/checker/heldout.jsonl"}`
- hyperparameters: `{"batch": 32, "device": "mps", "eval_steps": 300, "fp16": false, "lr": 2e-05, "max_len": 128, "max_steps": 1200, "patience": 4, "warmup": 100}`
- duration: 9.7 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"best_dir": "../data/checkpoints/checker-laptop/best", "chance": 0.125, "first_loss": 2.095775146484375, "heldout_accuracy": 0.89, "heldout_false_alarm_rate": 0.1181, "last_loss": 0.2772675514221191, "steps": 1200}`
