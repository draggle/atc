# 04. Training, evaluation, and Baseten

> **Updated by `07-build-spec.md`.** The checker is now a RoBERTa-base cross-encoder, not a LoRA on a language model. Baseten training and serving commands are in section 11 of that file.

We train two models. The before and after on each is the core of the Baseten pitch and a large part of the Rox pitch.

## Model 1: Whisper fine-tuned on ATC audio

### Why

Stock Whisper fails badly on ATC radio. Published numbers:

| Model | Word error rate on ATC |
|---|---|
| Whisper small, stock | 63% |
| Whisper small, fine-tuned on ATCO2 | 23% |
| Whisper medium.en, stock | 95% |
| Whisper medium.en, fine-tuned on ATCO2 plus UWB-ATCC | 15% |

Part of the stock medium.en number is hallucination on short clips and output format mismatch. We must measure our own numbers with proper normalization. Do not quote these as ours.

### Datasets

| Dataset | Size | Notes |
|---|---|---|
| `jacktol/atc-dataset` on Hugging Face | 14,795 clips, 11.9k train and 2.93k test, 822 MB | Built from the ATCO2 one-hour test subset and the UWB-ATCC corpus. Card says MIT. Clips are 0.3 to 15 s. Transcripts are lowercase with numbers spelled out |
| `jlvdoorn/atco2-asr-atcosim` on Hugging Face | 8,092 train and 2,026 validation | ATCO2 plus ATCOSIM, used by the Whisper-ATC work |

Check the underlying corpus licenses before using anything beyond this hackathon. They are research datasets.

**Do not add LiveATC audio.** See `05-data-and-legal.md`.

### Recipe, from the published medium.en fine-tune

- 16 kHz resampling
- 10 epochs with early stopping, patience 3, on validation word error rate
- Batch size 16 with gradient accumulation 2, so effective 32
- Learning rate 1e-5 with 500 warm-up steps
- Dynamic augmentation: Gaussian noise, pitch shift, time stretch, clipping distortion, with intensity decaying over training
- That run used two A100 80 GB cards

Reference code: https://github.com/jack-tol/fine-tuning-whisper-on-atc-data and https://github.com/jlvdoorn/WhisperATC

Our estimate, unverified: on one H100, Whisper small finishes in under an hour and medium.en in one to three hours. Start with small to prove the pipeline end to end, then launch medium.en.

### Known gotchas

- About 40 samples in the dataset have wrong ground truth. The published work removed them by hand. Spot-check for obviously broken labels.
- Stock Whisper hallucinates on very short or unclear clips, for example repeating one word. Use VAD and a minimum clip length.
- Normalize both reference and hypothesis identically before computing word error rate. Numerals versus spelled numbers and `alfa` versus `alpha` will otherwise inflate the error.
- Keep a held-out test split that no training run ever sees. All reported numbers come from it.

### Stretch: synthetic audio

Research shows text-to-speech ATC audio with noise augmentation improves word error rate. Generate pilot and controller lines with ElevenLabs in varied voices, add radio static and band-limiting, and mix into training. This also qualifies us for the ElevenLabs track.

### Fallback

`jacktol/whisper-medium.en-fine-tuned-for-ATC` on Hugging Face is a published fine-tune. If our training fails, we can serve it, and we must say so plainly.

## Model 2: the readback checker

### Why

Real readback errors are 1 to 2 percent of traffic, far too rare to collect. The leading research project trained its detector on 129,000 synthetic examples for the same reason.

### Data generation

1. Sample realistic clearances. Use the extractor on dataset transcripts to get real ones, and template more.
2. For each clearance, generate a correct readback in several paraphrases.
3. Inject errors from the taxonomy in `02-domain.md`, one type per example, with the type as the label.
4. Add ASR-style noise to some examples: dropped words, a swapped digit, a garbled callsign. The checker sees ASR output in production, not clean text.
5. Aim for a few thousand examples, roughly balanced between correct and each error type.

### Training

LoRA on a 1B to 3B instruction-tuned text model. Input is the structured clearance plus the normalized readback text. Output is JSON with result, error type, and confidence.

### Evaluation

Report on a held-out set:

- Accuracy per error type
- **False alarm rate** on correct readbacks. This is the number aviation-aware judges will ask for
- Latency and cost per check versus a large prompted model on the same set

### The loop

Confident resolver verdicts from tier 2 get logged as new labeled pairs. Retrain the checker on them. "The agent generates its own training data" is the line for Baseten judges.

## Evaluation we show on stage

| Metric | Compare |
|---|---|
| Word error rate | Stock Whisper versus ours, same held-out clips |
| Checker accuracy and false alarm rate | Large prompted model versus our small tuned model |
| Latency per transmission | Tier 1 end to end |
| Cost per 1,000 transmissions | Large prompted model versus our small models |

## Baseten

Event guide: https://github.com/basetenlabs/Hack-the-North-2026

### Setup

- Credits: promo code is in the event Slack channel `#spons-baseten-2026`. Redeem once per workspace under Billing and usage. If we share a workspace, its creator must redeem.
- **Training access: go to the Baseten booth first.** They enable H100 access and help pick a setup.

### Inference

OpenAI-compatible. Never commit the key.

```python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["BASETEN_API_KEY"],
    base_url="https://inference.baseten.co/v1",
)
```

List live models:

```bash
curl https://inference.baseten.co/v1/models -H "Authorization: Bearer $BASETEN_API_KEY"
```

Streaming, tool calling, structured outputs, JSON mode, vision, and audio are supported, but support varies by model. Check before choosing.

### Training flow

1. Install the Baseten CLI and sign in.
2. Define the training image, GPU, commands, secrets, cache, and checkpoint storage.
3. Submit the job and follow logs from the CLI or dashboard.
4. Save outputs under the Baseten checkpoint directory so they persist after the job stops.
5. Deploy the finished checkpoint as an endpoint.

### Rate limits and errors

- 429 means a request or token limit. Retry with exponential backoff, never in a tight loop. Submit the event rate-limit form from the attendee channel.
- 402 means credits are not redeemed. 404 means a wrong model slug.
- Keep shared instructions and few-shot examples at the start of prompts so prompt caching can reuse them.
- When asking for help, bring the request ID and full error, or the training job ID and log lines.

### Baseten Switch

Routes Claude Code or Codex through Baseten-hosted models. Using it for part of the build is worth a mention to their judges.

```bash
brew install basetenlabs/baseten/baseten-switch
baseten-switch setup
baseten-switch up --install
baseten-switch claude on
baseten-switch doctor --probe
```

Restart Claude Code afterwards. It is beta and macOS only.
