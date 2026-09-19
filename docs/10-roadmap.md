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
- [ ] Round-trip error under 0.1 NM across a 600 NM region in tests
- [ ] Every position-bearing event includes lat and lon, documented in `08-ws-protocol.md`

### Phase 3. The map. About 4 to 5 hours. Can run in parallel with phases 1 and 2 against mock events
Replace the canvas radar with deck.gl on MapLibre. Build to parity first, then add depth.
Parity: basemap, aircraft icons rotated to heading with labels, standard lines, Tower lines, waypoints or gates, disruptions, click to place a disruption, alert and watching highlights.
Depth: tilt and rotate with the mouse, altitude in 3D with an exaggeration slider, vertical stems to the ground, fading trails, replanned routes flash, separation rings on the pair in conflict, translucent 3D volumes for disruptions, globe projection toggle, click a plane for a flight strip (callsign, level, speed, current clearance, card history), follow-camera, hover tooltips, legend.
Layout: map full-bleed, panels floating over it. Load the `frontend-design` skill before writing this.
- [ ] Everything the old radar showed is on the new map
- [ ] Pan, zoom, tilt, and rotate are smooth with 150 aircraft at 60 fps on the demo laptop
- [ ] Mock mode still works with no backend
- [ ] The old canvas radar is deleted, not left beside it

### Phase 4. Real traffic. About 4 hours. Parallel with phase 3
`tools/build_real_scenario.py`: streams one adsb.lol day, keeps aircraft that cross the region box inside the time window above a floor altitude, cleans each track (drop stale points, split legs, resample to 10 s), and writes a compact scenario: flights with entry and exit gate, entry time, level, speed, type, and the **actual track**. Output lives in `data/real/`, a few hundred KB each. Backend loads it like any scenario. In real mode the standard line is the actual track.
Setup panel: Data source (Simulated or Real), then region, day, hour window, and maximum flights.
Scoreboard in real mode: miles flown versus Tower, time, closest approach actual versus Tower, with the caveats on screen.
- [ ] One region and one day builds end to end and loads in under 3 seconds
- [ ] Actual tracks draw as grey lines, and Tower's lines draw over them when the plan view is on
- [ ] Telephony table covers at least 95 percent of callsigns in the built scenarios, and the rest are spelled out
- [ ] Two regions and two days available in the picker
- [ ] Attribution for adsb.lol in `README.md` and in the app footer
- [ ] Stretch: ghost markers flying the actual tracks alongside Tower's aircraft

### Phase 5. Disruptions. About 2 hours
The unified `Disruption` schema, planner input, simulator motion, and one Disrupt control: choose a kind and click, or press Random.
- [ ] Every kind produces a conflict-free replan, or an explicit "no solution, emergency layer used" message
- [ ] New routes flash on the map within 2 seconds of the disruption appearing
- [ ] Random is seeded and repeatable
- [ ] Old intruder and storm buttons removed

### Phase 6. Instructions: Manual and Auto. About 3 hours
Manual: arm a card, record, review what Tower heard against the card, send. Auto: the voice queue described above, the data link path, and human takeover. Fix the voice budget: ElevenLabs for voiced exchanges, macOS voices as the fallback, a visible counter of characters left.
- [ ] Manual: a misspoken card is flagged before it goes out
- [ ] Auto at 1x: cards are spoken one at a time, most urgent first, and each waits for its readback
- [ ] Auto above 1x, or when the queue is long: instructions go by data link and the planes comply
- [ ] Holding the mic in Auto pauses Tower's speech and the human's transmission goes first
- [ ] A wrong readback in Auto triggers Tower's spoken correction and the corrected readback is checked

### Phase 7. Scale and robustness. About 2 hours
- [ ] Planner: initial plan for 150 flights in under 5 seconds, replans inside their budget. If not, cap the scenario and say so
- [ ] The investigating agent runs off the clock's critical path so the map never freezes while it thinks
- [ ] WebSocket reconnect restores full state: lifecycle, plan, cards, disruptions
- [ ] A failing TTS, ASR, or model call degrades visibly and never stalls the sim
- [ ] Monte Carlo baseline gets a simple separation-keeping controller, so the safety comparison is fair
- [ ] The "errors caught" number comes from the real pipeline, not from the assumption in the eval

### Phase 8. Models. Runs in parallel all day, models owner
- [ ] Full Whisper small run on Baseten finished, numbers in `training/RUNS.md`
- [ ] Tuned Whisper deployed, `ASR_MODEL_URL` set, stock versus tuned toggle live
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
