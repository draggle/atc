# 10. Roadmap: from the overnight build to the product we want

Written Saturday Sept 19, 2026, about 1:00 PM. This is the working plan. We attack it one phase at a time, and each phase has a definition of done. Tick the boxes as they land. Where this disagrees with `06-plan.md` or the TRDs in `docs/trd/`, this file wins.

## The target, in the team's words

1. Opening the page does **not** start anything. You set up a world, preview it, and press Start.
2. You can switch between **simulated** traffic and **real** traffic.
3. Real traffic: pick a **region** and a **day in the past**, and that day's actual flights load.
4. The map is a **real, movable map**: pan, zoom, tilt, rotate, with altitude shown in 3D. Not a square with dots.
5. You see the **standard lines** (what was actually flown, or the fixed routes in sim mode). With Tower on, the **optimized lines** draw on top.
6. One **Disrupt** control adds an arbitrary intruder of a chosen type, or a random one at a random place. Tower reacts and the new routes appear live.
7. On the right, the **instructions** Tower generates for the controller. The controller records one and sends it. The pilot hears it and answers. Tower validates the readback.
8. An **Auto** mode where Tower issues everything itself and you watch it happen.
9. It is robust. It does not fall over in front of a judge.

## What research settled

| Question | Finding | Decision |
|---|---|---|
| Real historical flight data we can legally use | **adsb.lol** publishes every day's full traffic, including yesterday's, under ODbL and CC0. One day is 4.2 GB in three files. **OpenSky's** public samples are smaller (180 MB per hour) but their license is non-profit research only and requires hiding aircraft identifiers in anything shown publicly. ADS-B Exchange samples have unclear terms | **Use adsb.lol.** Do not use OpenSky. Process each day offline into a small scenario file. "Pick a day" means pick from the days we have processed |
| Which region | Open ADS-B coverage is dense over Europe and the US, thin over Canada, and has gaps mid-ocean. The North Atlantic would show planes vanishing mid-crossing | Presets: **Western Europe core**, **UK and Ireland**, **US Northeast**. North Atlantic only as a stretch, labeled as partial coverage |
| Map technology | **deck.gl on MapLibre**, through `react-map-gl`. Free dark basemap from CARTO, no key. Supports tilt, rotate, globe projection, 3D paths with altitude, animated trails, 3D models. Fits the existing Next.js app | Use it. No Mapbox token, no Cesium |
| Format of the real data | One gzip JSON per aircraft per day. Each point: seconds offset, lat, lon, altitude ft, ground speed kt, track, flags, vertical rate. Callsign is in the aircraft details | A streaming builder script. Never unpack 4 GB to disk |

## Design decisions that make it hold together

**One world, two sources.** Sim and real traffic produce the same `Scenario`. Everything downstream (planner, Tower core, pilots, screen) does not care which it is.

**A geographic frame.** The simulator and planner keep working in flat nautical miles. Every scenario gains a `GeoFrame`: a centre latitude and longitude and a projection. The backend converts at the edge, so every aircraft, path, and disruption event carries lat and lon as well as x and y. Projection is azimuthal equidistant around the region centre, which keeps distance error under about 1 percent for regions up to roughly 600 NM across. State that limit on screen for bigger regions.

**What "standard line" means.**
- Sim mode: the fixed waypoint route, as today.
- Real mode: the track the aircraft **actually flew** that day.
Tower's line is the planned path from the same entry point and time to the same exit point. In real mode the honest claims are "fewer miles than were flown" and "no closer than the real day." We cannot claim "safer," because a replay has no counterfactual. Winds, weather, and closed airspace that shaped the real tracks are invisible to us, so miles saved is an upper bound. Say "in the replay."

**Named gates.** Real data has no waypoint names, and the radio needs one: "proceed direct ESTIR." The builder clusters entry and exit points on the region boundary into 12 to 20 **gates** and gives each a pronounceable five-letter name. Real fixes are made-up words too. Label them as ours.

**Real callsigns.** Real flights are `DLH4AB` and `BAW27G`, spoken "Lufthansa four alfa bravo" and "Speedbird two seven golf." We need a telephony table for the top 100 or so airlines, with a fallback that spells unknown codes phonetically, in the pilots, the normalizer, the callsign snapper, and the Whisper prompt.

**Scale, and the honest answer to it.** A real region can hold hundreds of aircraft. A radio frequency carries one transmission at a time, so a controller manages perhaps six exchanges a minute. Real ATC solves this with sectors and with **data link** (CPDLC), where clearances go as text. We copy that:
- **Voice** for the urgent instructions and for whatever the human chooses to speak.
- **Data link** for the rest in Auto mode: the instruction is sent as text, the pilot acknowledges, the plane complies. No audio, no readback to mishear.
This also protects our two scarce resources: ElevenLabs characters (10,000, about 75 voiced exchanges) and local Whisper time (about 1.5 s per clip).
- The builder caps a scenario at a chosen number of flights, default about 80, by altitude band and time window.

**Time control.** Real traffic at true speed is slow to watch. The clock gets 1x, 5x, 20x, 60x. Voice only runs at 1x. Above that, instructions go by data link, and the clock drops to 1x when the human keys the mic.

**Disruptions, unified.** "Intruder" and "storm" become one `Disruption` with a `kind` (fighter jet, drone, balloon, emergency aircraft, storm cell, closed airspace, rocket launch, unknown), a shape (moving point or circle), size, heading and speed, altitude band, and duration. The planner already handles both shapes. The Disrupt control either places a chosen kind where you click, or picks kind and place at random, biased toward busy airspace so it always matters. Seeded, so a demo is repeatable.

**Two issue modes, owned by the controller.**
- **Manual:** cards appear. The controller arms a card, records, and Tower shows what it heard against the card before sending. If the controller misspoke, Tower says so. This catches controller slips as well as pilot ones.
- **Auto:** Tower issues cards itself, one at a time on the voice frequency, most urgent first, close to when each must take effect, and waits for the readback before the next. Overflow goes by data link. The controller can key the mic at any time and takes priority.

## Phases

Each phase ends in something demoable. Do not start the next until the boxes are ticked, except where a phase is marked parallel.

### Phase 0. Housekeeping. 30 minutes, now
- [ ] Devpost prizes selected before **2:00 PM today**
- [ ] Commit the pending changes: voice fixes, training config rename, this roadmap
- [ ] Proving job `k7-run` on Baseten: read its result, then launch the full run (models owner)
- [x] Team answers the five decisions at the bottom of this file (region, day, owners answered; ElevenLabs stays on the free tier until someone says otherwise; end time still unknown)

### Phase 1. Lifecycle: nothing runs until Start. About 1.5 hours
Backend: `World` gains states `idle`, `ready`, `running`, `paused`, `ended`. Loading builds the world and the plan but does not start the clock. New client messages: `configure`, `start`, `pause`, `reset`, `set_speed`. The `state` event carries the lifecycle state and the speed. The server no longer auto-loads and auto-runs `demo` at startup.
Frontend: a setup panel on first open, and Start, Pause, Reset, and speed in the top bar.
- [x] Open the page: empty map, setup panel, clock at 00:00, nothing moving
- [x] Load shows aircraft at their entry points and the standard lines, still not moving
- [x] Start runs, Pause freezes, Reset returns to the loaded state, speed changes take effect
- [x] Existing 148 tests pass, plus tests for each transition (154 now)

Done Saturday afternoon. Verified in a browser against a live backend: idle, load, start, 20x, pause, reset. Speed control was previously wired to nothing; it works now. Real traffic in the setup panel is a placeholder until phase 4.

### Phase 2. Geographic frame. About 1 hour
`GeoFrame` on `Scenario`. Projection helpers with round-trip tests. `AircraftState`, plan samples, waypoints, zones, and disruptions carry lat and lon in events. The three existing scenarios get a frame so they sit somewhere real (default: centred on Toronto Pearson).
- [x] Round-trip error under 0.1 NM across a 600 NM region in tests (measured about 1e-12 NM)
- [x] Every position-bearing event includes lat and lon, documented in `08-ws-protocol.md`

Done Saturday afternoon. `backend/sim/geoframe.py`, 8 new tests, 165 passing. Pairwise distance error inside a 600 NM region is at most 0.17 percent, better than the 1 percent we planned for. Mock mode emits the same fields, and `frontend/lib/geo.ts` mirrors the projection, so phase 3 can be built against mock data. Nothing changes on screen yet.

### Phase 3. The map. About 4 to 5 hours. Can run in parallel with phases 1 and 2 against mock events
Replace the canvas radar with deck.gl on MapLibre. Build to parity first, then add depth.
Parity: basemap, aircraft icons rotated to heading with labels, standard lines, Tower lines, waypoints or gates, disruptions, click to place a disruption, alert and watching highlights.
Depth: tilt and rotate with the mouse, altitude in 3D with an exaggeration slider, vertical stems to the ground, fading trails, replanned routes flash, separation rings on the pair in conflict, translucent 3D volumes for disruptions, globe projection toggle, click a plane for a flight strip (callsign, level, speed, current clearance, card history), follow-camera, hover tooltips, legend.
Layout: map full-bleed, panels floating over it. Load the `frontend-design` skill before writing this.
- [x] Everything the old radar showed is on the new map
- [ ] Pan, zoom, tilt, and rotate are smooth with 150 aircraft at 60 fps on the demo laptop (**not measured yet**: the test browser runs hidden and throttles animation. No slow frames with 22 aircraft. Check by eye, and again with real traffic in phase 4)
- [x] Mock mode still works with no backend (mock emits the same lat/lon fields)
- [x] The old canvas radar is deleted, not left beside it

Parity and most of the depth landed Saturday afternoon: deck.gl on MapLibre 5 with the free CARTO dark basemap, dimmed so traffic is the brightest thing on screen. Tilt, rotate, top-down, altitude in 3D with an exaggeration slider, stems, trails, flashing replans, 3D storm columns, intruders with predicted tracks, alert / checking / watching rings, click a plane for a flight strip with follow-camera, click the map to drop a disruption, hover tooltips. Panels float over a full-screen map. Type is B612, the face Airbus designed for cockpit displays. If the basemap cannot load, the airspace still draws on plain ink.

Still open in this phase: separation rings on a conflicting pair, the globe view is wired but untested, 3D aircraft models (possible Higgsfield use), and the frame-rate check above.

Two things found on the way: MapLibre 6 does not load its worker under Next.js dev, so we pin MapLibre 5. And the plan message was 1.28 MB for 55 flights because it carried every 10 s sample; it now carries only the drawable path and is about 70 KB.

### Phase 4. Real traffic. About 4 hours. Parallel with phase 3
`tools/build_real_scenario.py`: streams one adsb.lol day, keeps aircraft that cross the region box inside the time window above a floor altitude, cleans each track (drop stale points, split legs, resample to 10 s), and writes a compact scenario: flights with entry and exit gate, entry time, level, speed, type, and the **actual track**. Output lives in `data/real/`, a few hundred KB each. Backend loads it like any scenario. In real mode the standard line is the actual track.
Setup panel: Data source (Simulated or Real), then region, day, hour window, and maximum flights.
Scoreboard in real mode: miles flown versus Tower, time, closest approach actual versus Tower, with the caveats on screen.
- [x] One region and one day builds end to end and loads in under 3 seconds (one pass over the 4.2 GB archive takes 57 s for all four regions; a built scenario loads and plans in well under a second at 80 flights, about 2 s at 159)
- [x] Actual tracks draw as grey lines, and Tower's lines draw over them when the plan view is on
- [ ] Telephony table covers at least 95 percent of callsigns in the built scenarios, and the rest are spelled out (**93 percent** of 631 real flights; the rest are business jets and state aircraft with obscure codes, spelled phonetically. One shared table now: `backend/airlines.py`)
- [ ] Two regions and two days available in the picker (**four regions, one day, two hours each**. A second day means downloading another 4.2 GB archive and re-running the two tools)
- [x] Attribution for adsb.lol in `README.md` and in the app (view panel and setup panel)
- [ ] Stretch: ghost markers flying the actual tracks alongside Tower's aircraft

Done Saturday afternoon. `backend/tools/real_extract.py` streams the archive (nothing is unpacked) and keeps airline flights that crossed a region at cruise, level, inside an hour. `backend/tools/real_build.py` turns them into scenarios in `backend/scenarios/real/`, 11 to 48 KB each and committed, so nobody else needs the archive. Sept 18, 2026: Western Europe core 135 and 159 flights, southern Ontario 80 and 92, UK 55 and 27, US Northeast 39 and 44.

How a real flight is modelled: its route is the track it actually flew, simplified to a few hidden vertices, ending at a named exit gate. Left alone the simulator flies what the aircraft really flew. Tower's plan is the direct path to the same gate. Level and speed are held at the flight's median.

**What we learned, and it changes the pitch.**
- **Real cruise traffic already flies nearly straight.** Across every region Tower's plan is about 0.5 percent shorter than what was flown, not the 7 to 8 percent the simulated scenarios show. Those scenarios bend every route through one central fix on purpose. Do not quote the simulated figure as if it described real airspace. The honest efficiency line is "about half a percent in the replay, which is roughly 120 NM in one hour in one sector", and the real value is the conflict-free plan, the reaction to disruptions, and the communication safety net.
- **The planner scales.** 159 real flights planned with zero conflicts in about 2 s, 80 flights in 0.3 s. The risk flagged for phase 7 is mostly retired.
- **The replay model invents conflicts the real day did not have.** Holding each flight at its median level and speed removes the small level and speed changes real controllers used, so the "flown" baseline shows 5 to 18 conflicts and an unattended run can show a loss of separation. Say "in the replay model". It is not evidence the real day was unsafe.
- **Instruction cards needed a floor.** Real traffic produced 62 cards at load, mostly "direct, saves 0 NM". A direct now needs to save 3 NM to earn a card. The same load gives 10, all of them conflict fixes or real shortcuts.
- Busy-sky decluttering on the map: one-line labels unless an aircraft matters right now, smaller icons, and only airborne flights draw routes once the clock runs.

### Phase 5. Disruptions. About 2 hours
The unified `Disruption` schema, planner input, simulator motion, and one Disrupt control: choose a kind and click, or press Random.
- [x] Every kind produces a conflict-free replan, or an explicit message naming the flights that could not be resolved
- [x] New routes flash on the map within 2 seconds of the disruption appearing (the replan takes 10 to 60 ms at 30 aircraft, under 0.5 s with 60 airborne)
- [x] Random is seeded and repeatable
- [x] Old intruder and storm buttons removed

Done Saturday evening. Eight kinds in one table, `backend/disruptions.py`: fighter jet, drone, balloon, emergency aircraft, unknown target, storm cell, closed airspace, rocket launch. One Disrupt control on the map: **Random**, or **Choose** a kind and click. Active disruptions show as chips with minutes left and a remove button. Zones are drawn between their own floor and ceiling, so closed airspace floats; storms drift and swell; everything timed expires by itself and the flights it moved are sent back. The emergency is one of our own flights, and it calls mayday in its own voice.

Measured with every card spoken and obeyed, six Random presses four minutes apart: demo, dense, Toronto 21:00Z (all 92 flights), Europe 16:00Z (80 flights) and Europe 16:00Z (all 159, 50 to 60 airborne) all finished with **no new loss of separation and no aircraft inside a zone**, apart from one flight that Tower had announced was too close to avoid a storm, which clipped it by 0.4 NM. Worst replan 0.4 s.

**What this phase found.**
- **A periodic replan forgot every intruder.** `replan()` only knew about an intruder on the call that introduced it. A minute later the repair pass planned as if it were not there and sent traffic back across its track: 0.57 NM from a fighter in one run. Fixed, with a regression test. This was in the build since Friday night and the Monte Carlo numbers for the intruder scenario predate the fix: rerun them before quoting.
- **Cards for flights not yet in the sector were being spoken into nothing.** The flight then entered at its old level. `speak_card` now refuses with a notice until the flight has checked in. Phase 6's Auto queue must do the same.
- **Superseded cards never left the screen.** The backend dropped them silently. It now sends status `superseded` and the screen removes them.
- **The top bar invented miles.** It subtracted the live plan (what is left to fly) from the full baseline, so "miles saved" climbed into the thousands after any replan. It now shows the backend's figure, frozen at the first plan.
- **Card churn under a drifting storm.** One flight got nine heading changes in 25 minutes with six disruptions stacked. Each is individually right. A controller would hate it. Phase 7: keep the assigned heading when the new one is within a few degrees and still clear.
- The planner run time is wall-clock bounded, so a loaded laptop plans worse. Do not run the evaluation in parallel with anything else.

### Phase 6. Instructions: Manual and Auto. About 3 hours
Manual: arm a card, record, review what Tower heard against the card, send. Auto: the voice queue described above, the data link path, and human takeover. Fix the voice budget: ElevenLabs for voiced exchanges, macOS voices as the fallback, a visible counter of characters left.
- [ ] Manual: a misspoken card is flagged before it goes out
- [x] Auto at 1x: cards are spoken one at a time, most urgent first, and each waits for its readback
- [x] Auto above 1x, or when the queue is long: instructions go by data link and the planes comply
- [x] Holding the mic in Auto pauses Tower's speech and the human's transmission goes first (tested in the backend; not yet tried with a real mic)
- [ ] A wrong readback in Auto triggers Tower's spoken correction and the corrected readback is checked

Auto landed Saturday evening, because without it the product looks broken: **aircraft only turn when an instruction is said and read back, so in Manual with nobody talking the orange replanned lines appear and the planes fly straight through the storm.** That is correct behaviour and it is exactly what a judge will see if the switch is left on Manual and nobody speaks.

How Auto works (`World._auto_dispatch`): pending cards for flights already on frequency, most urgent first. One voice exchange at a time, and the channel is busy until the readback is validated or 30 s pass. A card goes by **data link** instead (text, accepted with WILCO, cannot be misheard, still radar-verified) when the clock is faster than 1.5x, when more than three cards are waiting, or when it is due before the voice could reach it. Holding the mic makes Tower wait. Each card records `via`: human, voice or datalink. Measured with nobody at the controls and four Random disruptions: demo and Europe 16:00Z (80 flights) both finished with no new loss of separation and nobody inside a zone; the same run in Manual with nobody talking had planes 17 NM deep in a storm.

Still open in this phase: the Manual review step (see what Tower heard against the card before it goes out), the ElevenLabs character counter, and a wrong readback in Auto with real voices has not been watched end to end.

### Phase 7. Scale and robustness. About 2 hours
- [ ] Planner: initial plan for 150 flights in under 5 seconds, replans inside their budget. If not, cap the scenario and say so
- [ ] The investigating agent runs off the clock's critical path so the map never freezes while it thinks
- [ ] WebSocket reconnect restores full state: lifecycle, plan, cards, disruptions
- [ ] A failing TTS, ASR, or model call degrades visibly and never stalls the sim
- [ ] Monte Carlo baseline gets a simple separation-keeping controller, so the safety comparison is fair
- [ ] The "errors caught" number comes from the real pipeline, not from the assumption in the eval

### Phase 8. Models. Runs in parallel all day, models owner
- [x] Full Whisper small run on Baseten finished, numbers in `training/RUNS.md` (WER 0.708 stock to 0.159 tuned, 1,000 held-out clips)
- [x] Tuned Whisper deployed, `ASR_MODEL_URL` set, stock versus tuned toggle live (`training/serve_asr`, T4, 0.8 s per transmission at beam 3; stock side runs locally in parallel with `ASR_STOCK_LOCAL=1`; falls back to local Whisper per transmission if Baseten cannot be reached)
- [ ] A run with our simulator's audio mixed in, to fix the demo-voice regression
- [ ] Checker trained on Baseten and wired in
- [ ] Per-word confidence and calibrated scores for the agent

### Phase 9. Demo. Sunday morning
- [ ] Fix the wrong dates in the README, `CLAUDE.md`, and the TRDs ("Sunday", "deadline passed")
- [ ] Three-minute script rewritten for the new flow and rehearsed with a stranger
- [ ] Backup video recorded
- [ ] Devpost written, badge IDs in, attribution complete

## If we run short, cut in this order
1. North Atlantic region, ghost markers, globe projection
2. Data link, replaced by a cap on traffic in Auto mode
3. Second region and second day
4. 3D models for aircraft, keeping icons and altitude stems
5. The review step in Manual, keeping plain push-to-talk
Never cut: Start button, the real map, one real day, the Disrupt control, Auto mode.

## Risks
| Risk | Mitigation |
|---|---|
| 4.2 GB download on venue wifi | Start it now on whichever laptop has the best connection. One day is enough for the demo |
| Real traffic overwhelms the planner | The flight cap, and phase 7 measures it early |
| ElevenLabs runs out mid-demo | Data link for bulk, macOS voices as fallback, counter on screen |
| The new map eats the day | Parity first. The old radar stays until parity is reached |
| Stock Whisper mishears real airline names and gates | Callsign snapping to the active list already covers most of it. The tuned model and the prompt cover more. Cards opened from their own items as today |
| Shared Baseten workspace is rate limited during judging | Ask the booth now what the limits are. Local Whisper stays as tier 1 |

## Decisions the team needs to make
- [x] **Region for the hero demo.** Western Europe core
- [x] **Which day.** Sept 18, 2026
- [ ] **ElevenLabs budget.** Stay on the free 10,000 characters, or pay a few dollars for the next tier
- [x] **Owners.** Agreed as suggested: map (phase 3), real data (phase 4), backend lifecycle, disruptions, and modes (phases 1, 2, 5, 6), models (phase 8)
- [ ] **When hacking ends and judging starts.** Still unknown in our docs

## Sources
- adsb.lol historical data: https://www.adsb.lol/docs/open-data/historical/ and https://github.com/adsblol/globe_history_2026
- Trace file format: https://github.com/wiedehopf/readsb/blob/dev/README-json.md
- OpenSky sample data license, the reason we do not use it: https://s3.opensky-network.org/data-samples/states/LICENSE.txt
- deck.gl with MapLibre: https://deck.gl/docs/developer-guide/base-maps/using-with-maplibre
