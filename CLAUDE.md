# Tower

An AI system for air traffic control, running against our own simulator. Built at Hack the North 2026, Sept 18 to 20, University of Waterloo.

**Tower plans the best path for every flight, adapts the moment anything changes, and makes sure every instruction is heard and flown correctly.**

1. **Plans** an ideal, conflict-free path for every flight, at the same safety margins used today.
2. **Replans** when anything changes: a late flight, a storm, an intruder, or a plane that deviates.
3. **Listens** to the noisy radio with a Whisper model we fine-tune on ATC audio.
4. **Validates** that each pilot readback means the same as the instruction.
5. **Verifies** on radar that each plane does what it was told.

AI pilots fly the simulated planes and answer by voice, sometimes wrongly. An AI agent investigates the cases too messy for rules. The controller stays in charge.

Why the pieces belong together: a tightly optimized plan only works if every instruction is heard and followed exactly, so the listening and checking side is what makes the optimization safe.

## Status

As of Sunday morning Sept 20: steps 0 to 4 work end to end on one laptop with no keys, plus most of 6, 7, 8, 9, 10 in local or mock form. `README.md` has the full table and numbers. `docs/trd/01-pre-ship.md` has what is missing, ordered. Items follow the build order in section 12 of `docs/07-build-spec.md`.

- [x] 0. Shared schemas and WebSocket events agreed and mocked (`backend/schemas.py`, `docs/08-ws-protocol.md`)
- [x] 1. Simulator stepping aircraft on routes, radar view drawing them
- [x] 2. Mic to stock Whisper to transcript on screen (local faster-whisper; Baseten client written)
- [x] 3. Spoken clearance moves a plane: normalizer, parser, callsign snapping, state machine, rule checker
- [x] 4. One AI pilot reads back by voice with injected errors, first alert fires (macOS `say`; ElevenLabs client written)
- [ ] 5. Fine-tuned Whisper deployed on Baseten, stock comparison, measured word error rate. Scripts and Baseten config done; laptop whisper-tiny run only; see `training/RUNS.md`
- [x] 6. Planner: conflict-free plan, fixed-route baseline, instruction cards
- [x] 7. Radar verification and the watch tool (backend done; no radar visual yet)
- [~] 8. Checker cross-encoder trained (laptop, 0.89 accuracy on synthetic pairs) and served; not wired live without `CHECKER_MODEL_URL`
- [~] 9. Resolver agent with its trace on screen (runs on a deterministic mock without `BASETEN_API_KEY`)
- [x] 10. Replanning around intruders, then Monte Carlo safety evaluation (three arms, LoS per flight hour, closest approach)
- [~] 11. Sliders and world-builder agent done; absurd scenarios and data engine not started

Steps 0 to 4 are a complete demo alone and they work.

## Doc map

Read `docs/01-project.md` first, whatever you are working on. Then:

| If you are working on | Read |
|---|---|
| Anything involving ATC phrases, callsigns, what counts as a mismatch | `docs/02-domain.md` |
| Backend pipeline, schemas, the agent, the WebSocket protocol | `docs/03-architecture.md` |
| Fine-tuning Whisper, the readback checker, Baseten setup, evaluation | `docs/04-training.md` |
| Any new audio source or dataset | `docs/05-data-and-legal.md` before you download anything |
| What to build next, who owns what, the demo script | `docs/06-plan.md` |
| **The researched build spec: simulator, planner, safety metrics, AI pilots, checker design, Baseten commands. Wins over 03 and 04 where they differ** | `docs/07-build-spec.md` |
| The WebSocket protocol the screen and backend speak | `docs/08-ws-protocol.md` |
| Things the overnight build learned that the spec did not know | `docs/09-overnight-findings.md` |
| What is missing before judging, and the three teammate TRDs | `docs/trd/` |

Each of `backend/`, `training/`, and `frontend/` has its own short `CLAUDE.md` with that component's contract.

`docs/00-full-context.md` is a single-file snapshot of everything, including background that is not in the numbered docs: how routes work, BlueSky, the insights idea, and the decision history. It is about 1,400 lines, so do not load it by default. Use it for onboarding or for pasting into a tool that cannot see the repo. The numbered docs win if they disagree.

## Architecture in ten lines

```
controller mic --audio--> TOWER CORE <----state---- SIMULATOR <--commands-- AI PILOTS
AI pilot voices --audio-> hear, understand,             ^                      ^
                          track, check                  |                      | clearance
                              |   ^                  PLANNER ---- instruction cards
                 ambiguous    |   | radar            plan, replan
                              v   |
                        resolver agent  --> alert | dismiss | uncertain --> SCREEN
```

- **Planner** is plain search and geometry. It plans every flight, then repairs the plan when anything changes. No machine learning.
- **Tower core tier 1** is a fixed pipeline: VAD, tuned Whisper, normalizer, callsign snapping, grammar parser, state machine, checker. Target under 2 seconds per transmission.
- **Tier 2** is a tool-calling agent that wakes only on ambiguous cases. It re-listens, checks who is on frequency, looks at the radar, and can watch a plane before deciding.
- **The plane obeys the pilot's readback, not the clearance.** That is what makes a readback error visible on radar.
- All language model calls go through Baseten's OpenAI-compatible API. Both fine-tuned models are trained and served on Baseten.

## Repo layout

```
backend/    FastAPI service: simulator, planner, AI pilots, Tower core, resolver agent, WebSocket events
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
8. **Never plan below the separation minimum,** 5 NM and 1,000 ft. The slider changes only the extra buffer, except in a scenario labeled absurd.
9. **Never pitch "planes fly closer."** The claim is ideal paths at the same margins. See `docs/01-project.md`.
10. **The planner is not the language model's job.** Search and geometry do the math. The model phrases and prioritizes.
11. **Everything is built this weekend.** Third-party code, models, and datasets are fine with attribution. List them in `README.md`.

## Conventions

These are proposals. If the team decides otherwise, change them here so every Claude session picks it up.

- Python 3.11+, FastAPI, Pydantic models for every schema in `docs/03-architecture.md`.
- The Pydantic schemas are the contract between teammates. Change the doc and the code together, and tell the team.
- ASR training text follows the dataset convention: lowercase, numbers spelled out, for example `lufthansa two five three descend flight level two four zero`. Digits and ICAO codes only appear after the normalizer.
- Audio is 16 kHz mono everywhere.
- Small commits to `main` are fine during the hackathon. Pull before you push. Do not force-push.

## Commands

```bash
cd backend && uv venv .venv && uv pip install -e ".[dev]"   # once
cd backend && .venv/bin/pytest -q                              # 145 tests
cd backend && .venv/bin/uvicorn app:app --port 8000            # backend, first run downloads whisper base.en
cd frontend && npm install && npm run dev                      # screen at http://localhost:3000, mock mode if no backend
cd backend && .venv/bin/python -m eval.run_eval --scenario demo --runs 20   # Monte Carlo table
cd training && uv venv .venv && uv pip install -r requirements.txt && .venv/bin/pytest tests -q
```

Full run instructions and environment variables are in `README.md`.

## Keeping this useful

If you learn something a teammate would otherwise have to rediscover, such as a dataset quirk, a Baseten gotcha, or a prompt that works, write it in the matching doc in the same commit.
