# TRD 01: What is missing before Tower ships for judging

Written Sunday morning Sept 20 by the overnight session. "Ship" means: the three-minute demo runs live on one laptop in a loud room, the backup video exists, Devpost is submitted, and every number on the scoreboard was measured by us. Ordered by what would hurt most if it were still missing at judging time.

The state of the code is in `README.md`. The three stream TRDs (02, 03, 04) split this list among three teammates. Joey owns integration and the core.

## P0: without these there is no demo

| Gap | Why it matters | Where | Owner TRD |
|---|---|---|---|
| **Rehearsed three-minute demo with a stand-in judge** | Judges score the demo, not the codebase. Nothing here has been run in front of a stranger | `docs/01-project.md` demo script, `joey-notes.md` section 2 | 04 |
| **Backup video recorded** | The live loop depends on a mic, a CPU Whisper model, and a loud room | Record from the screen at `localhost:3000` with the pilot audio played through speakers | 04 |
| **Devpost: prizes selected by 2 PM EDT Saturday, badge IDs, repo link, README attribution** | Hard deadline that already passed once in the docs; confirm it was done | Devpost | 04 |
| **Mic push-to-talk verified on the demo laptop in a real browser** | The client captures with ScriptProcessor and downsamples to 16 kHz; verified with Playwright and synthesized audio, never with a human voice through a real mic | `frontend/lib/audio.ts`, `backend/app.py` ptt handling | 04 |
| **Whisper fine-tune on Baseten with a measured stock versus tuned comparison** | It is the Baseten prize and the visible before/after. The laptop tiny run, if it finished, is a fallback only. Without a key nothing was submitted | `training/whisper/config.py`, `training/BASETEN.md` | 02 |
| **A real LLM behind the resolver and world builder** | Both run on deterministic mocks today. The Rox trace is scripted policy, not a model choosing tools. Needs `BASETEN_API_KEY` and a model slug that supports tool calling | `backend/tower/llm.py`, `backend/world_agent.py` | 02 |

## P1: the demo works but is weaker than it should be

| Gap | Why it matters | Where | Owner TRD |
|---|---|---|---|
| ElevenLabs voices | MLH ElevenLabs prize. Client is written to the documented API but never exercised. macOS `say` is the fallback | `backend/pilots/tts.py` | 04 |
| n-best hypotheses in tier 1 | The n-best rule, the main false-alarm defence, rarely fires locally because faster-whisper returns one hypothesis. Baseten model should return top 5, or enable temperature re-decodes and pay the latency | `backend/tower/asr.py`, `docs/09-overnight-findings.md` | 02 |
| Checker cross-encoder wired into the live pipeline | Trained and served (`training/serve_checker.py`) but the backend only calls it if `CHECKER_MODEL_URL` is set, and the combination logic (agree raises confidence, disagree goes ambiguous) is untested against the real model | `backend/tower/check.py` RemoteChecker | 02 |
| Radar verification visible on screen | Conformance verdicts are emitted as alerts with a "Radar:" reason, and cards go to verified after 30 s, but there is no visual on the radar showing "watching this plane" | `backend/tower/conform.py`, `frontend/components/Radar.tsx` | 04 |
| Resolver trace has never been shown for a genuinely ambiguous case live | The mock policy produces a trace; a real garbled clip with two plausible altitudes has not been driven end to end through the UI | `backend/tower/resolver`, drive with `radio_text` and a low-confidence clip | 02 |
| Miles-saved counter is wrong after the first replan | Remaining distance versus full-route baseline. Either recompute the baseline from the same time or freeze the counter at the initial plan | `backend/world.py` scoreboard, `backend/planner/plan.py` | 03 |
| Density multiplier and the density curve | The pitch in `joey-notes.md` section 14 depends on "load Chicago, times two" and a curve of LoS rate against traffic density. The world builder can multiply traffic; the eval curve does not exist | `backend/eval/montecarlo.py` density arg, a sweep script | 03 |
| Entry delays have no card | Planner delays a not-yet-airborne flight by 1 to 3 minutes and nothing on screen says so | `backend/planner/cards.py` | 03 |
| Real ADS-B starting traffic | The "real traffic" claim in the pitch. Not started. Four-hour timebox, one person | `joey-notes.md` sections 3 and 12 | 03 |

## P2: polish and honesty

| Gap | Where | Owner TRD |
|---|---|---|
| Aircraft positions jump at 1 Hz; interpolate on the client | `frontend/components/Radar.tsx` | 04 |
| Card urgency countdown starts at client arrival, not server time | `frontend/components/InstructionCards.tsx` | 04 |
| Transcript callsign column is a regex guess; the backend should attach the extraction's callsign to the transcript event | `backend/world.py` `_new_tx` | 04 |
| Slider for separation buffer changes future replans only; say so in the UI or replan on change | `backend/world.py` set_sliders | 03 |
| `frontend` dev script is pinned to port 3000, which was occupied on the build machine; document `-p` | `frontend/package.json` | 04 |
| The audio path stores every clip under `data/audio` forever; add a cap or a sweep | `backend/world.py` | 04 |
| README numbers table needs the tuned Whisper row filled from `training/results/wer_comparison.json` | `README.md` | 02 |
| `docs/03` and `docs/07` should absorb the findings in `docs/09` once the team agrees | `docs/` | Joey |

## What is deliberately not on this list

Generative UI, full 3D, close-call mining, learned traffic generator, RL, Sentry, VATSIM, the insights view. All in the what's-next slide per `joey-notes.md` section 15 and the cut list in `docs/06-plan.md`.

## Suggested order for Sunday

1. Morning, everyone: pull the branch, run the three commands in `README.md`, confirm the loop works on your own laptop. Report anything that does not in the team channel.
2. Models teammate: Baseten key into `.env`, submit the Whisper job, pick a tool-calling model slug, flip the resolver to real. Everything else in TRD 02 after that.
3. Planner teammate: fix the miles counter, add the density sweep, then the ADS-B timebox.
4. Screen teammate: real mic test, ElevenLabs, then the demo script and the backup video by early afternoon.
5. Joey: keep `main` green, merge the PR, integrate what lands, and rehearse.
