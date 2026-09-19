# Baseten: training and serving runbook

Everything here was checked against the `truss` 0.18.30 CLI installed in `training/.venv`
(`truss_train` ships inside the `truss` package; there is no separate `truss-train` on PyPI).
Nothing has been submitted yet. We have no Baseten key on this machine.

## 0. Before anything: the booth

Go to the Baseten booth first (docs/04-training.md). Ask for:

1. **Training access enabled** on our workspace, and H100 capacity. `truss train capacity` shows what is available.
2. Whether the promo credits are redeemed on the workspace that will run the jobs. A `402` on inference means they are not.
3. Whether `truss train push` or `baseten train push --config` is current for their CLI build. Both are documented; the installed `truss` accepts `truss train push CONFIG`.
4. **Whisper serving.** Their cookbook has no Whisper example. Ask for the recommended Truss for faster-whisper on an H100 or an L4, and whether a base64-WAV in, `{text, avg_logprob, n_best}` out custom Truss is the right shape. We need two endpoints: stock Whisper (side-by-side demo) and ours.
5. Checkpoint deployment for non-LLM models. `truss train deploy_checkpoints` is documented for LoRA via vLLM only. For Whisper and RoBERTa the answer is probably "download the checkpoint and `truss push` your own Truss". Confirm.
6. If we hit a rate limit on Model APIs during the demo, which form to file. Bring the request ID.

## 1. Login

```bash
cd training
.venv/bin/truss login            # pastes an API key; stored in ~/.trussrc
# or: BASETEN_API_KEY=... in ../.env (never commit)
```

## 2. Stage a job directory

`truss train push` uploads the directory that holds `config.py`. The run scripts import
`../finetune_whisper.py` etc., so stage a flat copy rather than uploading 1.5 GB of `data/`:

```bash
cd training
stage() {  # usage: stage whisper|checker
  rm -rf /tmp/tower-$1 && mkdir -p /tmp/tower-$1
  cp $1/config.py $1/run.sh *.py requirements.txt /tmp/tower-$1/
  echo /tmp/tower-$1
}
```

`run.sh` detects whether the scripts are beside it (staged) or one level up (repo) and
downloads the public dataset itself inside the job, so nothing large is uploaded.

## 3. Submit

```bash
# Whisper (edit whisper/config.py env vars first: WHISPER_BASE, MAX_STEPS, RUN_LABEL)
cd "$(stage whisper)" && truss train push config.py --tail

# Checker
cd "$(stage checker)" && truss train push config.py --tail
```

Useful flags: `--job-name`, `--accelerator H100:1` (overrides config), `--spot` (cheaper,
interruptible), `--interactive on_failure` (SSH into a failed job).

**First Whisper job:** set `MAX_STEPS=300` and `WHISPER_BASE=openai/whisper-small` so the
whole path (download, train, eval, CT2 export, checkpoint upload) is proven in about 15
minutes before spending an hour. Then clear `MAX_STEPS` for the real run, then medium.en.

## 4. Follow

```bash
truss train view                                  # all jobs, with ids
truss train logs --job-id <id> --tail             # docs also show: baseten train job logs --job-id <id> --tail
truss train metrics --job-id <id>
truss train stop --job-id <id>
```

The scripts print JSON summaries and append to `RUNS.md` inside the job; both are copied into
the checkpoint directory at the end so they survive.

## 5. Get the checkpoint

```bash
truss train checkpoints list --job-id <id>
truss train download --job-id <id> --target-directory ../data/checkpoints/from-baseten
```

The Whisper job leaves `best/` (transformers format) and `ct2-float16/` (faster-whisper).
The checker job leaves `best/` with `labels.json`.

## 6. Deploy

Whisper: built, see **Serving the tuned Whisper** at the end of this file. It is `training/serve_asr/`,
it takes `{"audio": <base64 wav>, "prompt", "beam_size", "n_best"}` and returns
`{"text", "avg_logprob", "n_best": [{"text", "avg_logprob"}], "model", "seconds"}`. For the stock side
of the toggle, either push the same package with the checkpoint reference removed (it then serves
stock `openai/whisper-small`) and put that URL in `ASR_STOCK_MODEL_URL`, or set `ASR_STOCK_LOCAL=1`
to run a local stock model beside the deployed tuned one.

Checker: either the same pattern (`AutoModelForSequenceClassification` over `best/`, contract in
`serve_checker.py`), or run `serve_checker.py` in-process on CPU. The in-process path measured
about 7 ms per pair batched on the M-series laptop, so a Truss is optional for the demo.

Put the resulting URLs in `.env` as `ASR_MODEL_URL`, `ASR_STOCK_MODEL_URL`, `CHECKER_MODEL_URL`.

## 7. Inference API for the agent and extractor

```bash
curl https://inference.baseten.co/v1/models -H "Authorization: Bearer $BASETEN_API_KEY"
```

OpenAI-compatible; `base_url="https://inference.baseten.co/v1"`. 429 means back off, 402 means
credits, 404 means a wrong slug.

## What to say to the Baseten judges

- Two fine-tunes trained on Baseten H100s (Whisper small or medium.en, RoBERTa-base cross-encoder), served on Baseten, agent calls on Baseten Model APIs.
- Real measured numbers from `RUNS.md`: stock vs tuned WER on the same held-out real clips; checker accuracy and false alarm rate on held-out pairs; latency per pair.
- The loop: confident resolver verdicts become checker training rows, so the agent generates its own next training set.

## Serving the tuned Whisper

`training/serve_asr/` is the deployment. It holds no weights: `config.yaml` names the training job and Baseten copies `best/` in from it. The server is `transformers` on a T4, the same stack the job's own evaluation used, with beam search, so every answer carries the top hypotheses and their scores. That is what makes the checker's n-best rule work live.

```bash
cd training/serve_asr && ../.venv/bin/truss push --team "13" --tail     # build and deploy, about 10 minutes the first time
```

When it is up, copy the model's predict URL from the Baseten page (it ends in `/predict`) into `.env` as `ASR_MODEL_URL`, then:

```bash
cd backend && .venv/bin/python tools/asr_smoke.py        # what it heard, alternatives, which model answered, how long
```

Things to know:
- `truss train deploy_checkpoints` also works for Whisper, but it serves through vLLM's transcription API: no beam alternatives, no score, and a different request shape from the one `backend/tower/asr.py` sends. We do not use it.
- If the checkpoint reference is wrong the server falls back to stock `openai/whisper-small` and says so in every response (`model: "FALLBACK ..."`) and in the smoke test. Never quote numbers from a fallback.
- To serve another run, change `training_job_id` in `config.yaml` and push again.
- The app does not depend on it. If a call fails, `WithFallback` in `backend/tower/asr.py` hears that transmission with the local model and skips Baseten for 45 s. Point `ASR_LOCAL_MODEL` at a CTranslate2 export of the tuned model (the job wrote one to `ct2-float16/`) and the fallback is as good as the deployment.
- Shared workspace: the model is named `k7-asr` on purpose. Scale it to zero or deactivate it when we are not testing, and delete it after the event.

**Deployed Saturday Sept 19, 15:53.** Model `k7-asr`, id `q40o4j9w`, one T4, transformers 5.17.0 (the first push pinned 4.57.1 and could not read the tokenizer the training job had saved: serve with the transformers that trained). Predict URL: `https://model-q40o4j9w.api.baseten.co/environments/production/predict`.

Measured from the laptop on a 2.7 s pilot clip, steady state, round trip: beam 1 = 0.3 s, beam 3 = 0.8 s, beam 5 = 1.7 s (1.5 s of that inside the model). The app uses beam 3 (`ASR_BEAM_SIZE`). Through the app's real path, tuned on Baseten plus the stock comparison run locally at the same time: 0.8 to 0.9 s per transmission. The first call after an idle period is slower.

**What it does not fix.** Our simulator's made-up fix names. "direct ESTIR" comes back as "direct to six" from the tuned model (confidence 0.95) and "direct to sit" from stock, with or without the fix names in the prompt. The training data is real ATC audio and has never heard ESTIR. The pipeline already treats "routing read back but fix not understood" as ambiguous and hands it to the agent, so this does not raise a false alarm, but the right fix is a second run with a few thousand synthetic clips of our own phrases mixed in.

