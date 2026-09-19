# Tower

An AI second set of ears on an air traffic control frequency. Built at Hack the North 2026.

Pilots must repeat every instruction back to the controller, and the controller is supposed to catch mistakes. Sometimes they miss one. Tower listens to both sides, transcribes the radio with a Whisper model fine-tuned on ATC audio, checks every readback against its instruction, and alerts when they do not match or when no readback arrives.

## For teammates

Start with [CLAUDE.md](CLAUDE.md), then [docs/01-project.md](docs/01-project.md). Claude Code loads `CLAUDE.md` automatically, so your sessions start with the shared context.

| Doc | What is in it |
|---|---|
| [docs/01-project.md](docs/01-project.md) | The idea, prize targets, judging criteria, demo script, judge questions |
| [docs/02-domain.md](docs/02-domain.md) | How ATC radio works, what must be read back, phraseology, prior work |
| [docs/03-architecture.md](docs/03-architecture.md) | Pipeline, schemas, resolver agent, WebSocket events |
| [docs/04-training.md](docs/04-training.md) | Datasets, fine-tuning recipes, evaluation, Baseten setup |
| [docs/05-data-and-legal.md](docs/05-data-and-legal.md) | What audio we may and may not use |
| [docs/06-plan.md](docs/06-plan.md) | Deadlines, owners, milestones, risks, cut list |

## Setup

```bash
cp .env.example .env
```

Then fill in your keys. Run instructions will be added here as code lands.

## Third-party models, datasets, and libraries

Keep this list current. Hack the North requires attribution.

- Datasets: [jacktol/atc-dataset](https://huggingface.co/datasets/jacktol/atc-dataset), [jlvdoorn/atco2-asr-atcosim](https://huggingface.co/datasets/jlvdoorn/atco2-asr-atcosim)
- Base models: OpenAI Whisper. Others to be added
- Infrastructure: Baseten
