# Tower

An AI second set of ears on an air traffic control frequency. Built at Hack the North 2026, Sept 18 to 20, University of Waterloo.

When a controller gives an instruction, the pilot must repeat it back, and the controller is supposed to catch any mistake. Busy controllers sometimes miss one. Tower listens to both sides of the radio, transcribes it with a Whisper model we fine-tune on ATC audio, tracks every open instruction per aircraft, checks each readback against it, and alerts when they do not match or when no readback arrives.

Mental model: **Whisper hears, the notepad remembers, the agent handles the cases too messy for rules.**

## Status

Docs only. No code yet. Update this section as pieces land so nobody has to guess what works.

- [ ] Training: Whisper fine-tune running on Baseten
- [ ] Backend: audio in, transcript out
- [ ] Backend: extractor, state machine, checker
- [ ] Backend: resolver agent
- [ ] Frontend: live transcript, open clearances, alert feed
- [ ] Demo: pilot mic with radio filter, stock vs tuned toggle, backup video

## Doc map

Read `docs/01-project.md` first, whatever you are working on. Then:

| If you are working on | Read |
|---|---|
| Anything involving ATC phrases, callsigns, what counts as a mismatch | `docs/02-domain.md` |
| Backend pipeline, schemas, the agent, the WebSocket protocol | `docs/03-architecture.md` |
| Fine-tuning Whisper, the readback checker, Baseten setup, evaluation | `docs/04-training.md` |
| Any new audio source or dataset | `docs/05-data-and-legal.md` before you download anything |
| What to build next, who owns what, the demo script | `docs/06-plan.md` |

Each of `backend/`, `training/`, and `frontend/` has its own short `CLAUDE.md` with that component's contract.

## Architecture in ten lines

```
audio -> VAD -> tuned Whisper -> normalizer -> extractor -> state machine -> checker
                                                                              |
                                                clear match / clear mismatch <+
                                                                              | ambiguous
                                                                              v
                                                                     resolver agent + tools
                                                                              |
                                                                alert | dismiss | uncertain
```

- **Tier 1** is a fixed pipeline. No agent loops. Target under 2 seconds per transmission.
- **Tier 2** is a tool-calling agent that wakes only on ambiguous cases. It re-listens, checks who else is on frequency, looks at history, then commits to an action with a reason.
- All language model calls go through Baseten's OpenAI-compatible API. Both fine-tuned models are trained and served on Baseten.

## Repo layout

```
backend/    FastAPI service: audio ingest, pipeline, state, resolver agent, WebSocket events
training/   data prep, Whisper fine-tune, checker data generation and fine-tune, evaluation
frontend/   Next.js live screen
docs/       shared context
data/       local datasets, audio, checkpoints. Gitignored. Never commit.
```

## Hard rules

1. **No LiveATC audio** in training data or piped into the app, and no radio receivers. Their terms forbid automated access and non-personal use. See `docs/05-data-and-legal.md`.
2. **Never commit** secrets, datasets, audio files, or model checkpoints. Keys live in `.env`, which is gitignored. Copy `.env.example`.
3. **Tier 1 stays fast and deterministic.** If you are adding a multi-step LLM loop to the hot path, it belongs in tier 2.
4. **The resolver is never silent.** It ends in exactly one of: alert, dismiss with a reason, or uncertain. Cap it at 4 tool calls and about 5 seconds.
5. **Inference goes through Baseten.** Do not add another LLM provider without telling the team. It weakens the Baseten track story.
6. **Report real numbers.** Word error rate, checker accuracy, false alarm rate, and latency must be measured by us on a held-out set. If we fall back to a published fine-tuned model, we say so.
7. **Do not claim Tower would have prevented any specific accident.** Real incidents are context, handled respectfully.
8. **Everything is built this weekend.** Third-party code, models, and datasets are fine with attribution. List them in `README.md`.

## Conventions

These are proposals. If the team decides otherwise, change them here so every Claude session picks it up.

- Python 3.11+, FastAPI, Pydantic models for every schema in `docs/03-architecture.md`.
- The Pydantic schemas are the contract between teammates. Change the doc and the code together, and tell the team.
- ASR training text follows the dataset convention: lowercase, numbers spelled out, for example `lufthansa two five three descend flight level two four zero`. Digits and ICAO codes only appear after the normalizer.
- Audio is 16 kHz mono everywhere.
- Small commits to `main` are fine during the hackathon. Pull before you push. Do not force-push.

## Commands

None yet. When you add a way to run, test, or train something, put the exact command here.

## Keeping this useful

If you learn something a teammate would otherwise have to rediscover, such as a dataset quirk, a Baseten gotcha, or a prompt that works, write it in the matching doc in the same commit.
