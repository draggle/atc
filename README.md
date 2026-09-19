# Tower

An AI system for air traffic control, built at Hack the North 2026.

Tower plans the best path for every flight, adapts the moment anything changes, and makes sure every instruction is heard and flown correctly. It plans and replans conflict-free paths in a simulator we built, transcribes noisy radio with a Whisper model fine-tuned on ATC audio, checks every pilot readback against its instruction, and verifies on radar that each plane complies. AI pilots answer by voice, and an AI agent investigates the cases too messy for rules.

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
| [docs/07-build-spec.md](docs/07-build-spec.md) | The researched build spec: simulator, planner, safety metrics, Tower core, AI pilots, Baseten commands |
| [docs/00-full-context.md](docs/00-full-context.md) | Everything in one file, for onboarding |

## Setup

```bash
cp .env.example .env
```

Then fill in your keys. Run instructions will be added here as code lands.

## Third-party models, datasets, and libraries

Keep this list current. Hack the North requires attribution.

- Datasets: [jacktol/atc-dataset](https://huggingface.co/datasets/jacktol/atc-dataset), [jlvdoorn/atco2-asr-atcosim](https://huggingface.co/datasets/jlvdoorn/atco2-asr-atcosim)
- Base models: OpenAI Whisper, RoBERTa-base. Others to be added
- Voices: ElevenLabs
- Infrastructure: Baseten
