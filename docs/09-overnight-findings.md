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

- **The fine-tune works and the number is real.** whisper-tiny, 1,200 steps, 62 minutes on the laptop GPU: 0.217 WER on 300 never-seen real clips, against 1.18 for stock tiny, 1.11 for stock base, and 0.69 for stock small on the same clips. Table in `training/RUNS.md`.
- **The tuned model is worse on our synthetic pilot voices.** It hears "air canada" as "air china" and drops waypoints, because it learned European radio and the demo voices are macOS `say` through a filter. Stock base.en stays as tier 1 for the local demo; the tuned model is the real-clip comparison. The fix is to mix simulator audio into training (the ground-truth log under `data/` already exists for this), or to use ElevenLabs voices that sound more like the training set. Say this on stage: it is the honest version of "sim audio is not real radio."

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

## Found when the real keys went in, Saturday Sept 19 afternoon

- **ElevenLabs free accounts cannot call the older "library" voices through the API.** Six of the eight default voice ids returned 402, and the client turned each failure into a cached beep with no log line. Defaults are now eight premade voices a free account can use, the beep is never cached under the real phrase, and failures are logged.
- **Tower's own voice was a macOS voice name ("Alex").** That is a 404 on ElevenLabs, and Alex is not installed on recent macOS either. `TTS.controller_voice()` now returns a voice valid for the active backend.
- **macOS voices needed ffmpeg.** They now fall back to `afconvert`, which ships with macOS.
- **The page could get stuck in mock mode with a healthy backend.** Browsers resolve `localhost` to IPv6 first and uvicorn binds IPv4, which costs about 600 ms per connection. Under page load that beat the 1.5 s timeout, and mock mode never retried. The screen now connects to `127.0.0.1`, waits 4 s, and keeps retrying the backend while the mock is showing.
- **A garbled fix name produced a confident false alarm.** The pilot said "direct estir", Whisper heard "direct to 6", and the rules called it an omitted item at 0.90. If the pilot audibly read back a routing and only the fix name is missing, the verdict is now `ambiguous`. The resolver on GLM-5.3-Fast re-listens and then watches the aircraft on radar, in about 3 s. "Roger" with no routing is still an error, and a different recognisable fix is still a wrong value.
- **Never run `npm run build` while `npm run dev` is running.** It overwrites `.next` and the dev page loses its CSS. Use `NEXT_DIST_DIR=.next-verify npm run build`.
- **The resolver runs inline.** With a real model the radar can pause for 1 to 3 s while it thinks. Bounded by a 10 s client timeout. Moving it off the clock's critical path is phase 7.

## Found when real traffic went in, Saturday Sept 19 afternoon

Full write-up under phase 4 in `10-roadmap.md`. The short version for anyone writing the pitch or a slide:

- **Real cruise traffic already flies nearly straight.** Tower's plan is about 0.5 percent shorter than what was really flown on Sept 18, in every region we built. The 7 to 8 percent above is a property of our simulated scenarios, which route everything through one central fix. Never put the two numbers on the same slide without saying which is which.
- **The "flown" baseline in a real scenario is a replay model, not the real day.** Each flight is held at its median level and speed, so the small adjustments real controllers made are gone and the baseline shows conflicts that never happened. Say "in the replay model".
- **The planner handles a real sector-hour.** 159 flights, zero conflicts left, about 2 s. 80 flights in 0.3 s.
- **The plan message must stay small.** With per-second samples it was 1.28 MB and the page stuttered on every replan. Samples are now trimmed to the endpoints on the wire and the screen draws from `lonlat`. If you add a field per sample, check the message size.
- **There were three airline-name tables** (cards, normalizer, pilots) that disagreed, so a real callsign could be spoken one way and parsed another. There is now one, `backend/airlines.py`.
- **adsb.lol, not OpenSky.** OpenSky's licence is research-only and wants identifiers anonymised. adsb.lol is ODbL and CC0. Keep the attribution in the README and on the screen.

## Found when disruptions went in, Saturday Sept 19 evening

Full write-up under phase 5 in `10-roadmap.md`. The one to know about: **`replan()` forgot intruders after the call that introduced them**, so every periodic repair planned as if the fighter were not there. Fixed in `planner/plan.py` with a regression test in `tests/test_disruptions.py`. Any intruder-scenario number measured before Saturday 17:00 is suspect. Also: cards are no longer spoken to flights that have not entered the sector, superseded cards are withdrawn from the screen, and the top bar's "miles saved" no longer grows after a replan.

## The fine-tuned Whisper, Saturday Sept 19

`training/RUNS.md` has the full record. whisper-small, 61 minutes on one Baseten H100, 11,268 real ATC clips: **word error rate 0.708 stock to 0.159 tuned on 1,000 held-out clips.** Not deployed yet.

