# training/

Everything that produces a model or a number. Read section 11 of `../docs/07-build-spec.md` first, then `../docs/04-training.md`. The checker is a RoBERTa-base cross-encoder trained on pairs the simulator generates. Read `../docs/05-data-and-legal.md` before adding any data source.

## Rules

- No LiveATC audio. Public Hugging Face datasets, our own recordings, and synthetic data only.
- Datasets, audio, and checkpoints go under `../data/`, which is gitignored. Never commit them.
- Keep one held-out test split that no training run sees. Every reported number comes from it.
- Normalize reference and hypothesis identically before computing word error rate.
- Record every run: base model, data, hyperparameters, duration, hardware, and result. Append to `RUNS.md` in this folder. Judges will ask exactly what we trained.
- Save checkpoints under the Baseten checkpoint directory so they survive the job ending.

## Suggested layout

```
training/
  prep_data.py          download, resample, clean, split
  finetune_whisper.py
  eval_wer.py           stock versus tuned on the held-out split
  gen_checker_data.py   synthetic clearance and readback pairs with injected errors
  finetune_checker.py   RoBERTa-base cross-encoder, N+1 classes
  whisper/config.py     Baseten training job definition
  eval_checker.py       accuracy per error type, false alarm rate, latency
  RUNS.md               run log
```
