# TRD 00: Overnight autonomous build

Written by Claude, Saturday night Sept 19, 2026, at Joey's request. This is the plan the autonomous session executes. It is scoped to what can be built and verified on one laptop with no API keys and no GPU credits. Everything external gets a real client behind an environment variable plus a local fallback so the system runs end to end without keys.

## Objective

Land docs steps 0 to 4 as working code, plus as much of steps 5 to 10 as can be verified locally, on branch `joey/overnight-build`, as one PR. Leave the team with run instructions, a status table, and TRDs for what remains.

## Constraints discovered at start

| Constraint | Consequence |
|---|---|
| No `.env`, no Baseten or ElevenLabs keys | All LLM calls go through one client with a deterministic mock mode. TTS falls back to macOS `say`. Tuned Whisper endpoint is stubbed; stock Whisper runs locally via faster-whisper |
| Python 3.13, uv, torch with Apple GPU | Local Whisper inference is real. A smoke fine-tune of whisper-tiny on a few hundred clips is attempted only if the dataset downloads in time |
| No GPU credits | Training scripts are written and dry-run tested on tiny data. Baseten job config is written, not submitted |
| One laptop, one night | Parallel subagents on disjoint directories. Shared files (`schemas.py`, `pyproject.toml`) are owned by the orchestrator |

## Workstreams and deliverables

| Stream | Directory | Deliverable | Verification |
|---|---|---|---|
| A. Simulator, planner, eval | `backend/sim`, `backend/planner`, `backend/eval`, `backend/scenarios` | Flat-plane kinematic sim, seeded scenarios, prioritized planner with conflict test, fixed-route baseline, replan on intruder, instruction cards, Monte Carlo harness | pytest: conflict test, planner produces zero conflicts on the demo scenario, replan resolves an intruder |
| B. Tower core | `backend/tower` | Normalizer, callsign snapping, grammar parser with LLM fallback, six-state machine, rule checker with n-best rule, radar conformance, resolver agent loop with tools and mock LLM | pytest: normalizer table from 02-domain, parser on real phraseology, checker on the match table, resolver terminates within 4 calls |
| C. Frontend | `frontend` | Next.js screen: 2.5D-ish canvas radar, plan toggle, instruction cards, alert card, agent trace, transcript, scoreboard, push-to-talk mic to WebSocket, mock event mode | `npm run build` passes, mock mode renders every event type |
| D. Pilots and audio | `backend/pilots`, `backend/tower/audio.py`, `backend/tower/asr.py` | Readback templates, error injection per taxonomy, TTS (ElevenLabs or `say`), radio effect, VAD, ASR clients (Baseten tuned, local faster-whisper stock) | pytest: error injection covers every type, radio effect preserves length, ASR client mock round trip |
| E. Training | `training` | prep_data, finetune_whisper, eval_wer, gen_checker_data, finetune_checker, eval_checker, Baseten config, RUNS.md | Dry run of each script on tiny synthetic data; WER eval on 20 local clips if dataset downloads |
| F. Integration (orchestrator) | `backend/app.py`, `backend/world.py`, `README.md`, `docs/trd` | FastAPI app wiring sim, planner, pilots, core, resolver, world-builder agent tools, WebSocket hub, scenario runner, end-to-end test | pytest end-to-end: scripted clearance to alert without audio; then with local Whisper on synthesized audio |

## Build order

1. Schemas and pyproject (done before this file was written)
2. Streams A to E in parallel as subagents
3. Integration: app.py, end-to-end test, mock event fixtures for the frontend
4. Smoke run: backend up, frontend build, one scripted demo scenario producing an alert
5. README, status table, run instructions, TRDs, PR

## Out of scope tonight

Real ADS-B pull, Baseten training submission, ElevenLabs voices without a key, generative UI, full 3D, close-call mining, learned traffic generator. Each gets a TRD.

## Definition of done for the night

- `uv run pytest` green in `backend/`
- `npm run build` green in `frontend/`
- One command starts the backend, one starts the frontend, and the demo scenario runs to a red card with a resolver trace, using local Whisper and `say`
- PR open against `main` with README, status, and four TRDs
