# training/

Everything that produces a model or a number. Contracts: `CLAUDE.md` here, `../docs/04-training.md`,
`../docs/07-build-spec.md` section 11. Baseten runbook: `BASETEN.md`. Every executed run: `RUNS.md`.

## Setup

```bash
cd training
uv venv .venv --python 3.13
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/pytest tests -q
```

Tested on macOS (Apple Silicon, MPS) with torch 2.14, transformers 5.17, datasets 5.0, jiwer 4.0,
librosa 1.0, faster-whisper 1.2.1, ctranslate2 4.8, truss 0.18.30. The Baseten job image brings
its own CUDA torch.

## Scripts

| Script | Does |
|---|---|
| `phrases.py` | Templated controller and pilot lines: callsigns, altitude, heading, speed, frequency, squawk, runway, direct. Dataset convention (lowercase, digits spelled out). Shared by the two generators |
| `text_norm.py` | Evaluation normalizer applied to both reference and hypothesis before WER: lowercase, punctuation, digits to words, niner/tree/fife, alpha/alfa, juliet/juliett, center/centre. Tests in `tests/` |
| `prep_data.py` | Downloads `jacktol/atc-dataset` (public parquet, no token) to `../data/asr/raw/`, decodes to 16 kHz mono WAV, drops clips under 0.5 s or over 15 s or with empty text, writes `../data/asr/manifests/{train,val,test}.jsonl`. The dataset's own test split is our held-out test and is never trained on. `--synthetic --n N` instead synthesizes N clips with macOS `say` plus a radio effect for offline smoke runs. `--atco2` adds `jlvdoorn/atco2-asr-atcosim` |
| `finetune_whisper.py` | Seq2SeqTrainer fine-tune per the docs recipe: lr 1e-5, 500 warm-up, batch 16 x accum 2, up to 10 epochs, early stopping patience 3 on val WER, on-the-fly augmentation (noise, pitch shift, time stretch, clipping) decaying to zero. Saves under `$BT_CHECKPOINT_DIR` or `--output`; best model in `<out>/best`. Appends to `RUNS.md` |
| `eval_wer.py` | WER with jiwer on a manifest for `--stock <hub id>`, `--tuned <ckpt>`, `--ct2 <dir>`, extra `--model NAME=PATH`. Table on stdout, JSON with worst offenders in `results/` |
| `export_ct2.py` | `ct2-transformers-converter` wrapper (float16 on CUDA, int8 on CPU) that then loads the result in faster-whisper and transcribes a clip |
| `gen_checker_data.py` | Synthetic clearance / readback pairs, 8 balanced classes (`correct` + 7 error kinds from `02-domain.md`), paraphrased and shortened readbacks, one injected error per negative, ASR-style noise on 30 percent, weighted toward frequency, heading/speed, and similar callsigns. Deterministic under `--seed`. Writes `../data/checker/{train,heldout}.jsonl` |
| `finetune_checker.py` | RoBERTa cross-encoder (`controller </s></s> pilot`) sequence classifier, lr 2e-5, batch 64, AdamW, up to 20k steps, early stopping on held-out accuracy. Saves `<out>/best` with `labels.json` |
| `eval_checker.py` | Accuracy per error type, false alarm rate on correct readbacks, detection rate, clean vs ASR-noise accuracy, latency per pair, confusion matrix. Writes `results/checker_eval*.json` |
| `serve_checker.py` | `Checker(ckpt).predict(controller, pilot) -> (label, confidence)` and a FastAPI app. `POST /predict {"controller","pilot"} -> {"label","confidence","probs"}`. Inputs are normalized with `text_norm` first |
| `runlog.py` | `log_run(...)` appends every run to `RUNS.md` |
| `whisper/config.py`, `whisper/run.sh` | Baseten `truss_train` job for the Whisper fine-tune, exactly the section 11 shape. `run.sh` installs, downloads data, trains, evaluates stock vs tuned, exports CT2 |
| `checker/config.py`, `checker/run.sh` | Same for the cross-encoder |

## Smoke path (laptop, no GPU, no keys; each step under a few minutes)

```bash
cd training
.venv/bin/python prep_data.py --synthetic --n 60
.venv/bin/python finetune_whisper.py --model openai/whisper-tiny \
    --train ../data/asr/manifests/synth_train.jsonl --val ../data/asr/manifests/synth_val.jsonl \
    --output ../data/checkpoints/whisper-smoke --max-steps 20 --batch-size 4 --grad-accum 1 \
    --warmup-steps 2 --eval-steps 10 --limit 40 --label SMOKE
.venv/bin/python eval_wer.py --manifest ../data/asr/manifests/synth_test.jsonl \
    --stock openai/whisper-tiny --tuned ../data/checkpoints/whisper-smoke/best --tag smoke_synth --label SMOKE
.venv/bin/python export_ct2.py --ckpt ../data/checkpoints/whisper-smoke/best \
    --out ../data/checkpoints/whisper-smoke-ct2 --check-clip ../data/asr/synthetic/synth_00000.wav

.venv/bin/python gen_checker_data.py --n 50000 --seed 0
.venv/bin/python finetune_checker.py --model distilroberta-base \
    --train ../data/checker/train.jsonl --val ../data/checker/heldout.jsonl \
    --limit 2000 --val-limit 1000 --max-steps 200 --batch-size 16 --eval-steps 100 --warmup-steps 20 \
    --output ../data/checkpoints/checker-smoke --label SMOKE
.venv/bin/python eval_checker.py --ckpt ../data/checkpoints/checker-smoke/best \
    --data ../data/checker/heldout.jsonl --limit 1000 --out results/checker_eval_smoke.json --label SMOKE
.venv/bin/python serve_checker.py --ckpt ../data/checkpoints/checker-smoke/best \
    "lufthansa two five three descend flight level two four zero" "descend flight level two one zero lufthansa two five three"
```

## Real path

```bash
.venv/bin/python prep_data.py                       # 14,795 clips, about 3 minutes after download
.venv/bin/python eval_wer.py --manifest ../data/asr/manifests/test.jsonl \
    --stock openai/whisper-small --limit 100 --tag stock_small --label stock-baseline

# Whisper fine-tune on Baseten (see BASETEN.md), or locally on a CUDA box:
python finetune_whisper.py --model openai/whisper-small \
    --train ../data/asr/manifests/train.jsonl --val ../data/asr/manifests/val.jsonl --label small-full
python eval_wer.py --manifest ../data/asr/manifests/test.jsonl \
    --stock openai/whisper-small --tuned ../data/checkpoints/whisper-atc/best
python export_ct2.py --ckpt ../data/checkpoints/whisper-atc/best --out ../data/checkpoints/whisper-atc-ct2 --quantization float16

# Checker
python finetune_checker.py --model roberta-base --train ../data/checker/train.jsonl \
    --val ../data/checker/heldout.jsonl --label roberta-base-50k
python eval_checker.py --ckpt ../data/checkpoints/checker/best --data ../data/checker/heldout.jsonl
CHECKER_CKPT=../data/checkpoints/checker/best .venv/bin/uvicorn serve_checker:app --port 8600
```

## Where things land

- `../data/asr/raw/` downloaded parquet; `../data/asr/jacktol/{train,test}/*.wav` decoded 16 kHz clips (1.5 GB); `../data/asr/synthetic/`; `../data/asr/manifests/*.jsonl`
- `../data/checker/{train,heldout}.jsonl`, `labels.json`
- `../data/checkpoints/<name>/best` (transformers), `<name>-ct2` (faster-whisper)
- `results/*.json` eval outputs (committed, small); `RUNS.md` run log (committed)

Everything under `../data/` is gitignored. Never commit it.

## Numbers so far (from `RUNS.md`, measured on this laptop, Apple Silicon MPS)

Data: `jacktol/atc-dataset` downloaded in full. After filtering (8 clips outside 0.5 to 15 s, 0 empty
transcripts): **11,268 train / 593 val / 2,926 held-out test clips**, 10.6 h / 0.5 h / 2.7 h.

| What | Result | Label |
|---|---|---|
| Stock `whisper-tiny`, 100 real held-out clips, greedy, normalized | **WER 1.041** (S 605 D 115 I 318 over 997 ref words) | real baseline |
| Stock `whisper-base`, same 100 clips | **WER 0.919** | real baseline |
| Stock `whisper-small`, same 100 clips | **WER 0.672** | real baseline; matches the published 63 percent |
| Whisper-tiny smoke fine-tune, 40 synthetic clips, 20 steps, MPS, 23 s | loss 5.92 to 2.71; val WER 0.27 to 0.24 on 6 synthetic clips | SMOKE, not a result |
| Stock vs smoke-tuned tiny on 12 synthetic clips | 0.488 vs 0.285 | SMOKE, synthetic audio, do not quote |
| Checker smoke: `distilroberta-base`, 2,000 pairs, 200 steps, MPS, 70 s | held-out accuracy **0.533** (chance 0.125), false alarm 0.99 (has not learned `correct` yet) | SMOKE |
| Checker laptop run: `distilroberta-base`, 12,000 pairs, 1,200 steps, batch 32, MPS, 9.7 min | full 5,000-pair held-out: **accuracy 0.894**, **false alarm 0.085**, detection 0.908; clean 0.901 vs ASR-noise 0.876. Weakest: `wrong_aircraft` 0.54 (confused with `correct`), `wrong_value` 0.79 | laptop, synthetic held-out |
| Checker latency, distilroberta on MPS | 3.6 ms per pair batched, single pair p50 7 ms, p95 75 ms | this laptop |

Insertions dominate the stock WER: Whisper loops ("one six one six ...") and hallucinates
("thanks for watching") on short noisy clips, which is why WER exceeds 100 percent for tiny.
These are greedy decodes with no prompt; the served model will get an active-callsign prompt.

`wrong_aircraft` is the gap: with the callsign spoken only as digits, a one-digit-off callsign looks like a shortened correct one. The backend's callsign snapping to the active list is the intended fix; the full roberta-base 50k run should also help.

Nothing has been trained on a GPU yet. No Baseten job has been submitted. Checker numbers are on
synthetic held-out pairs from the same generator; real-recording checker accuracy is unmeasured.

## Attribution

- `jacktol/atc-dataset` (Hugging Face, MIT per its card; built from ATCO2 1h and UWB-ATCC), recipe from https://github.com/jack-tol/fine-tuning-whisper-on-atc-data
- OpenAI Whisper via `transformers`; CTranslate2 and faster-whisper for serving
- `distilroberta-base` / `roberta-base` (Hugging Face)
- Checker design from HAAWAII (see `../docs/02-domain.md` sources)
