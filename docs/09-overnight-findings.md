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

## Monte Carlo risk, Sunday

The planner's exact conflict test only sees the plan flown perfectly, so the things that go wrong on the day (a pilot who acknowledged and has not turned, a drifting storm, a wrong heading that is not yet a conflict) were left to the periodic replan or the loss of separation itself. `backend/planner/risk.py` rolls the sky forward 120 s a few hundred times under seeded noise and replans when any pair's probability of losing separation reaches 0.30, which fires earlier than the 15 s or 60 s check and on cases the exact test cannot see. The same residual risk becomes the confidence on each card, so the number a judge sees has two terms that can each be explained. TRD 07 has the design; roadmap phase 6f has what landed.

Measured on the demo laptop, 256 rollouts unless the adaptive count backed off (then `n_rollouts` says so):

| Aircraft | Scenario | `elapsed_ms` | `n_rollouts` | `futures_per_s` |
|---|---|---|---|---|
| 12 (demo, 5 airborne) | 2.3 | 256 | about 545,000 |
| 80 (Europe replay, 65 airborne) | 61 | 256 | about 273,000 |
| 150 | not measured in the integration pass; adaptive count is the guard | | |

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

## Live sky, Saturday Sept 19 evening

A third data source next to simulated and replay: `configure` with `source: "live"` asks adsb.lol once for everything within 250 NM of a region centre and loads the airline flights in level cruise as a scenario. Code in `backend/sim/live.py`, protocol in `08-ws-protocol.md`.

- **A snapshot, not a stream.** Real aircraft will not obey Tower. The moment Tower turns one flight, the simulated sky and the real one are different skies, and polling would only drag the planes back. So we fetch once and the simulator owns the aircraft from then on. Say "a snapshot of the sky as it was at 19:54 UTC", never "live tracking".
- **Miles saved is zero by construction in live mode.** A snapshot has no flown track, only a position and a track angle. Each route is that track projected straight to the region boundary, which is already the shortest path to its gate. The plan can only add distance (a dogleg to clear a conflict), so the scoreboard shows zero or slightly below. Efficiency numbers come from the replay scenarios. Live mode shows that the planner, the pilots and the readback checks cope with whatever is overhead right now.
- **Measured on a Saturday evening snapshot of Western Europe:** 555 aircraft in the feed, 145 kept (101 inside the 150 NM circle, 44 entering within 30 minutes), 38 gates. Dropped: 203 below FL245 or on the ground, 92 outside and not heading in, 76 climbing or descending, 27 not airline callsigns or none, 12 about to leave. The fetch takes under a second. At 80 flights the plan has zero conflicts left in about 1 s. At all 145 one flight sometimes stays unresolved, depending on how far the 1 s planning budget gets, so keep the cap at 80 to 120 for a demo.
- **The map's north is not the aircraft's north away from the centre.** The flat plane is an azimuthal projection, so 200 NM west of the centre a true track of 090 is about 094 on the flat map (up to 5 degrees in the recorded snapshot). Using the raw track put the 44 inbound flights 4.7 NM off at the boundary on average and 24.6 NM at worst, measured against each flight's great-circle path. `live.py` converts each track by projecting a point 1 NM ahead; with that the worst is 0.15 NM.
- **Why adsb.lol and not the others.** It is the same source as the archive behind the replay scenarios: same ODbL and CC0 licence, same readsb field names, no key, so one attribution line and one notion of "airline flight at cruise" cover both modes. OpenSky's licence is research-only and its API now wants registered credentials. AirLabs and aviationstack are commercial APIs behind a key with small free quotas and proprietary terms, which would also stop us keeping snapshots; we did not evaluate them further. We make one request per load and send a descriptive User-Agent.
- **Fallback chain, because venue Wi-Fi fails.** Live feed, else the newest snapshot saved under `data/live/` (every snapshot that builds is saved there, gitignored), else the newest committed replay hour of the same region, else an error notice and the world stays as it was. Each fallback says so in a `warn` notice and in `meta.fallback`. Before a demo, load each region once on the demo laptop so a saved snapshot exists.
- **Feed quirks.** `flight` is padded with spaces and sometimes missing. `alt_baro` is an int or the string `"ground"`. `baro_rate`, `geom_rate`, `track`, `gs` and `t` can each be missing. A missing vertical rate counts as level.

## Wrong fixes, Saturday Sept 19 evening

Found while checking the alert-to-map view against the real backend. Two gaps, both now closed, and one left open.

- **An AI pilot could never read back a wrong fix.** `pilots/errors.py` had value mutators for altitude, heading, speed, frequency, squawk and altimeter, and none for a route item, so "proceed direct ESTIR" could only degrade to ack-only, an omitted item or the wrong aircraft. The spec's headline failure, a plane going to the wrong waypoint after a bad readback, could not happen. `inject_error` now takes the sector's sayable fix names (`waypoints`), and a direct can be read back to another real fix, leaning towards the names that sound most like the cleared one. `World` passes `spoken_waypoints()`. Without a fix list nothing changes.
- **The route hint in `snap_waypoints` could erase a wrong readback.** A garbled fix was matched against the addressed aircraft's own route first, at a bar of 30. For the controller's voice that is right. For a pilot's readback the route is what we expect to hear, so "tulick" from a pilot cleared PIKAR became PIKAR and the readback matched. Pilot speech now uses `trust_hint=False`: a clear match (60 or more, with a margin) to any real fix is taken first, and the hint only rescues what matches nothing. "at better" still becomes ESTIR and "jigor" still becomes GEGOR.
- **Measured on the demo scenario with real pilot audio (macOS voices, local Whisper, the real resolver), three directs with every pilot forced to a wrong value:** cleared ESTIR and said "direct fenix": caught by the rules. Cleared PIKAR and said "cleared direct estir" at speech confidence 0.60: caught by the resolver after a re-listen. Both aircraft then flew to the fix they had read back. Cleared ESTIR and said "centa direct": alerted as ack-only. That last one led to the next finding.
- **"ESTIR direct" was a false alarm on a correct readback.** `pilots/readback.py` shortens a direct to "{fix} direct" one time in four. The parser and `snap_waypoints` only read "direct {fix}", so a correct "estir direct, air canada one two three" had no route item and alerted as "Acknowledgement only". Both now read the fix-first order, only when no fix follows "direct" (so "proceed direct ESTIR" is never a direct to PROCEED), and snapping rewrites it to "direct ESTIR". Words that can never be a fix (`NOT_A_FIX` in `tower/parse.py`: unable, say again, negative, standby, request and so on) are neither parsed as a fix nor rescued into one: "say again direct" scored 31 against ESTIR, above the hint bar of 30, and was being turned into a correct readback. With real audio: "pikar direct" was heard as "Kicka direct" and now matches. "estir direct" was heard as "SD direct", which scores 29 against ESTIR in either word order, so it goes to the resolver and ends uncertain. No false alarm, no match: string similarity cannot rescue that, the tuned Whisper can.
- **Do not expect a verdict inside 40 s on a garbled readback.** The resolver's `watch` tool waits 60 s on radar before it decides. That is by design, and it is easy to mistake for a missed error.
