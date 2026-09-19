# TRD 02: Models and Baseten

For the teammate who owns training, serving, and the LLM calls. Read `README.md`, `training/README.md`, `training/RUNS.md`, `training/BASETEN.md`, `docs/04-training.md`, and `docs/07-build-spec.md` section 11 first. Your Claude session should also read `docs/09-overnight-findings.md`, Speech and Training sections.

## Where things stand

| Piece | State |
|---|---|
| Dataset | jacktol/atc-dataset downloaded and prepared under `data/asr/`: 11,268 train, 593 val, 2,926 held-out test. Manifests in `data/asr/manifests/` |
| Stock WER | Measured on 100 real held-out clips: tiny 1.04, base 0.92, small 0.67. Greedy, both sides normalized with `training/text_norm.py` |
| Whisper fine-tune script | `training/finetune_whisper.py`, follows the recipe in docs 04. Smoke-tested on tiny. A laptop run of whisper-tiny on the full train set was launched overnight; check `training/results/wer_comparison.json` and `RUNS.md` for whether it finished and what it scored |
| Baseten training config | `training/whisper/config.py` and `run.sh`, `training/checker/config.py` and `run.sh`, exactly per docs 07 section 11. Never submitted, no key |
| Checker | `training/gen_checker_data.py` produces balanced 8-class pairs. `finetune_checker.py` trained distilroberta-base on 12k pairs in 10 minutes on the laptop: accuracy 0.894, false alarms 0.085, detection 0.908 on 5,000 synthetic held-out pairs. Checkpoint under `data/checkpoints/checker-laptop/best`. `serve_checker.py` serves `POST /predict {controller, pilot} -> {label, confidence}` |
| Backend clients | `backend/tower/asr.py` BasetenWhisper (request shape assumed, see the file), `backend/tower/check.py` RemoteChecker, `backend/tower/llm.py` LLM with 429 backoff. All fall back to local or mock when their env var is unset |

## Tasks, in order

### 1. Keys and model slugs, 20 minutes

- Redeem credits, get training access at the booth, put `BASETEN_API_KEY` in `.env`.
- `curl https://inference.baseten.co/v1/models -H "Authorization: Bearer $BASETEN_API_KEY"` and pick two slugs: a small fast one for `EXTRACTOR_MODEL`, a larger one that supports **tool calling** for `RESOLVER_MODEL`. The resolver uses `tool_choice="required"`; if the model rejects it, change to `"auto"` in `backend/tower/llm.py` and make sure `Resolver` still forces a terminal action.
- Restart the backend and type a garbled readback through the radio box, for example `air canada one two three descend two zero air canada one two` after a clearance to FL240. Confirm `resolver_step` events show a real model choosing tools. Send Joey the trace.

### 2. Whisper fine-tune on an H100, submit early, 2 to 4 hours wall clock

- Read `training/BASETEN.md` for the staging trick and the exact `truss train push` command.
- Start with whisper-small on the full train manifest, the recipe defaults (lr 1e-5, 500 warmup, 16 x 2, early stopping on val WER). Estimate under an hour on one H100. Then medium.en if time allows.
- Checkpoints must land under `$BT_CHECKPOINT_DIR` or they are lost. `run.sh` already does this.
- Download the best checkpoint, `training/export_ct2.py` it, and evaluate with `training/eval_wer.py --tuned <ckpt> --stock openai/whisper-small --limit 300` on the same 300 held-out clips. Log to `RUNS.md`. Put the row in `README.md`.
- Deploy as a Truss with faster-whisper (Baseten's example is linked in docs 07 section 14). Request shape the backend sends: `{"audio": <base64 16 kHz PCM16 WAV>, "prompt": str, "beam_size": 5, "language": "en"}`. Return `{"text", "avg_logprob", "n_best": [...]}`. Adjust `BasetenWhisper._extract` if your Truss returns something else. **Return top-5 hypotheses**; the n-best rule in `backend/tower/check.py` is the main false-alarm defence and it is starved locally.
- Set `ASR_MODEL_URL` and `ASR_STOCK_MODEL_URL` (deploy stock small too, same Truss, different weights) so the transcript toggle shows both.
- Fallback if training slips: serve `jacktol/whisper-medium.en-fine-tuned-for-ATC` and say so on stage. Hard rule 6.

### 3. Checker on Baseten and wired live, 1 hour

- Regenerate at 50k pairs (`gen_checker_data.py --n 50000`), train roberta-base with `training/checker/config.py` on Baseten, or on the laptop if the queue is long (20 minutes for 50k on MPS is plausible).
- Serve `training/serve_checker.py` (Truss or a small box) and set `CHECKER_MODEL_URL`. The backend's `RemoteChecker` posts `{controller, pilot}`.
- Test the combination logic: a correct readback stays silent, a wrong value alerts, and a disagreement between rules and model goes to the resolver. Watch the false alarm counter on the scoreboard over 20 exchanges at 0 percent pilot error rate. It must stay at 0.
- Report per-type accuracy from `eval_checker.py`. wrong_aircraft is the weak class; say so.

### 4. Synthetic audio augmentation, if time

- Every pilot response writes a wav and a ground-truth line under `data/`. `prep_data.py --synthetic` also generates clips with `say`. Mix a few thousand into training as augmentation. Keep the real test split untouched. Report real-clip WER only.

## Numbers you owe the scoreboard

- Stock versus tuned WER on the same 300 real held-out clips, model sizes named.
- Checker accuracy, false alarm rate, detection rate, per-type, on held-out synthetic pairs; and if possible on 50 hand-labelled real transcript pairs from the dataset.
- Latency per Baseten call for ASR, checker, and resolver.

## Gotchas already hit

- A bare callsign list as Whisper prompt hurts. Use `build_prompt()`; it prefixes phraseology.
- 16 GB laptop cannot fine-tune whisper-base; tiny only. Do not try locally.
- `truss_train` ships inside the `truss` package; there is no `truss-train` on PyPI.
- Stock Whisper hallucinates loops on short clips. VAD drops under 0.5 s; keep it.
