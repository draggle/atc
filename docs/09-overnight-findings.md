# 09. What the overnight build learned

Saturday night Sept 19 to Sunday Sept 20. Things the spec did not know that a teammate would otherwise rediscover. Update the numbered docs when the team agrees.

## Speech

- **A bare callsign list is a harmful Whisper prompt.** Passing "air canada, westjet" as `initial_prompt` made Whisper treat it as prior text and drop the start of the utterance. A sentence of ATC phraseology as a prefix roughly doubled confidence on every voice. `tower/asr.py` prepends `ATC_PROMPT_PREFIX` automatically; use `build_prompt(callsigns, waypoints)`.
- **Stock Whisper cannot hear waypoint names.** "ESTIR" came back as "at better" and "after"; "PIKAR" as "figure". Two fixes are in: `world.snap_waypoints` fuzzy-matches the words after "direct" against the sector's waypoints, preferring the addressed aircraft's own route with a low bar, and a spoken card that Tower mishears still opens its clearance from the card's items. The real fix is the fine-tune; this is the demo insurance.
- **Local whisper base.en on CPU is the right size for tier 1 on a laptop.** 1.2 to 1.5 s per clip with beam 5, near-perfect on the `say` voices through the radio filter at noise 0.3. small.en was 3.3 to 5.5 s, too slow.
- **faster-whisper exposes no beam alternatives.** n-best for the resolver's re-listen comes from temperature re-decodes (`extra_hypotheses=True`, about 4x latency) or from the Baseten model. Local single-hypothesis mode means the n-best rule rarely fires locally.
- **Some macOS voices are unintelligible to Whisper** even on clean audio: Kathy, Reed, Sandy, Fred. Kept Daniel, Karen, Moira, Tessa, Rishi, Samantha.
- **Stock WER on real clips, 100 held-out:** tiny 1.04, base 0.92, small 0.67. Insertions dominate: looping digits and "thanks for watching" on short clips. This is why the published numbers look absurd and why VAD plus a minimum clip length matters.

## Training on the laptop

- The full public dataset downloads in about a minute with plain HTTPS, no token. 11,268 train, 593 val, 2,926 test after filtering.
- A 16 GB MacBook Air cannot fine-tune whisper-base at batch 8: the process reaches 11 GB and swaps. whisper-tiny fits. Anything bigger is a Baseten job.
- The checker cross-encoder trains in 10 minutes on the laptop GPU to 0.89 accuracy on synthetic pairs. The weak class is wrong_aircraft, because a one-digit-off callsign looks like a shortened correct one. That is exactly why the backend snaps callsigns against the active list before the checker sees them.

## Planner and simulator

- Airborne flights must plan from their current cleared level, not the spec's preferred level, or the planner silently assumes a plane returns to its original altitude after a level change.
- Heading instructions hold forever in the sim, so a plane given a wrong heading leaves via the sector edge. That is the mechanism that makes an uncorrected wrong readback visible.
- After a replan, `total_distance_nm` is remaining distance while the baseline stays the full route, so miles saved is exact only for the initial plan. The scoreboard shows it anyway; fix in TRD 03.
- Entry delays produce no instruction card. The eval applies them to spawn time. The live app has no way to present an upstream hold yet.

## Integration

- The simulator is centred on the origin, x from -100 to +100. The first frontend build assumed 0 to 200. Fixed in `frontend/lib/geo.ts`.
- Pilot speech synthesis and transcription must run as tasks, not inside the clock tick, or the radar freezes for 3 seconds per readback.
- With Tower off, the core still runs so the transcript stays live, but alert and resolver events are dropped before they reach the screen and no correction is spoken.
- Auto-correct opens a second clearance with id `<original>-fix` so the pilot's corrected readback is checked like any other.

## Monte Carlo

Demo scenario, 20 runs, 2 percent readback errors, buffer 3 NM: fixed routes 0.34 losses of separation per flight hour with a closest approach of 0.04 NM at the CENTA funnel; Tower's plan 0 per flight hour with closest 9.4 NM, and 7.8 to 8.3 percent fewer miles. Dense scenario at 5 percent errors: fixed 154, Tower without validation 1 (an uncorrected wrong readback to 3.07 NM), Tower with validation 0. That last row is the "validation earns the tighter plan" story with real numbers.

## Density sweep

`python -m eval.sweep --scenario dense --densities 1,1.5,2,2.5 --buffers 1,3 --runs 4 --error-rate 0.02`, 220 s. Chart at `docs/img/density-sweep.png`, data at `docs/img/density-sweep.csv`.

| Density | Fixed routes LoS/h | Tower, validation off | Tower, validation on | Miles vs fixed |
|---|---|---|---|---|
| 1.0x (22 flights) | 0.63 | 0 | 0 | -6.7 to -7.6% |
| 1.5x (33) | 0.92 | 0 | 0 | -6.5 to -7.2% |
| 2.0x (44) | 1.26 | 0.011 to 0.046 | 0 to 0.011 | -6.4 to -7.2% |
| 2.5x (55) | 1.60 | 0 to 0.009 | 0 to 0.009 | -7.2 to -8.1% |

Fixed routes degrade linearly with density. Tower holds at zero through 1.5x and leaks single events at 2x and above. Those leaks are not readback errors slipping through; they come from the 60 s replan cadence and frozen window when 44 or more aircraft need repair. The planner reported zero unresolved conflicts at every point, so the knee where it fails to find candidates is beyond 2.5x. Four runs per point is thin; rerun at 10 runs before quoting on a slide.
