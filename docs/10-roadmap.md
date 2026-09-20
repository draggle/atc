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

### Phase 4b. Live sky. Saturday evening
A third data source: one snapshot of the real sky from adsb.lol, loaded as an ordinary scenario. `backend/sim/live.py`; regions shared with the extractor in `backend/sim/regions.py`, gate clustering shared with `real_build.py` in `backend/sim/gates.py`. A snapshot, not a stream, and miles saved is zero by construction: see "Live sky" in `09-overnight-findings.md`.
- [x] `configure` with `source: "live"` and a region loads a snapshot; the fetch runs in a thread, so the clock and the socket never wait for the network
- [x] Same filters as the replay tools: airline callsign, at or above the region floor, level, fresh position. Inside flights start where they are, outer-ring flights enter on the boundary when they would arrive
- [x] Planner leaves zero conflicts on a recorded snapshot capped at 80 flights (test), and on real snapshots of all four regions (checked by hand Saturday 20:07 UTC: 141, 36, 91 and 77 flights)
- [x] Fallback chain tested: saved snapshot, then committed replay, then an error notice with the world untouched. Each fallback raises a `warn` notice
- [x] Start, step and Reset return to the snapshot without fetching again
- [x] No test touches the network: a recorded response is in `backend/tests/fixtures/`
- [ ] Load each region once on the demo laptop before judging, so a saved snapshot exists if the Wi-Fi fails

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

### Phase 6b. Reaction: the centre of the demo. Saturday evening

The team's call: the path reaction is the show, the voice side waits. What we found when we looked at why it felt slow, and what changed:

- **Voice was throttling the paths.** A plane only turns after a full radio exchange (6 to 10 s), one exchange at a time, so the planner started every new path 60 s ahead. Auto now has a **Voice** switch. Off (the default): every instruction goes by data link in the same second the plan changes (`World._auto_links`, called from `_replan`). On: one aircraft is talked round at a time and everything else still goes by link.
- **A spoken reroute cannot put a plane on the drawn line.** It is a heading now and a "direct" later, so the plane drifted off the orange path, was seen as deviating, and was replanned again. By data link a reroute is the planned path itself (`SimCommand(kind="route", via=[...])`), and the simulator flies it.
- **The planner planned sharp corners and the simulator could not fly them.** Turn rate is now 1.5 deg/s (a 25 degree bank at cruise, radius near 5 NM; the old 3 deg/s was a light aircraft), the simulator starts each turn early and cuts the corner, and `planner/trajectory.py::flyable` puts the same curves into every planned path. The plan, the line on the map and the aircraft now agree to within a mile or two.
- **Detours came from a coarse menu.** `_tighten` pulls a working dogleg back toward the direct track by bisection: the smallest detour that is still safe, never at the price of more time inside a zone.
- **Jitter.** The worst case was a storm on a flight's exit gate: nothing avoids it, and every re-check produced a near-identical path with a heading a degree or two different, sent as a new instruction (nine in a row). Now the path a flight is already flying competes first and wins ties (`keep_ok`), a heading within 4 degrees of the last one issued is not re-sent, and random zones land mid-sector.
- Paths are re-checked every 15 s while a disruption is active (60 s otherwise). Small shortcuts that are not worth a transmission still go out quietly by data link in silent Auto (`InstructionCard.minor`), so on real traffic every aircraft is on its cyan line.
- Random draws only **storm** and **fighter** for now (`disruptions.RANDOM_KINDS`): one of each shape, until the reaction to them is perfect. The other six are still in the Choose menu.
- On the map: **Original / Tower / Both / Changed** line views, and the shadow of a reroute: the path the flight was going to fly stays as a fading dotted line for 30 s with a **ghost aircraft** still flying it.
- The scoreboard leads with the reaction: seconds to first turn, flights rerouted, aircraft in a zone now and ever.

Measured in silent Auto, six disruptions (storm, fighter, alternating) four minutes apart, nobody at the controls: demo, dense, Europe 16:00Z (80 flights), Toronto 21:00Z (92 flights). **No new loss of separation in any. Reroutes leave in the same tick; a replan takes 15 to 155 ms. At most 2 or 3 instructions per flight across all six disruptions** (it was 9 and 11). One aircraft in the dense run clips a storm by 1.7 NM: the storm formed too close for it to avoid and Tower says so at the time. Furthest any aircraft got from its planned line: 1.6 to 2.8 NM.

**Merged with live sky (Ayan's `sim/live.py`) Saturday evening.** A live snapshot is an ordinary scenario, so everything above applies to it unchanged. Checked on the 145-flight Europe fixture with 107 aircraft airborne at once, silent Auto, two storms and two fighters: no new loss of separation, nobody in a zone, reroutes out in the same tick, worst replan about 0.6 s, furthest from a planned line 2.3 NM. Live routes are already straight, so there are no shortcut cards and "miles saved" reads n/a: on live traffic the whole show is the reaction. The line-view switch moved from the top bar to the map's View panel, because with the live label and the Voice switch the top bar wrapped onto a second row and covered the Disrupt control.

Not done: the tripwire (replan one flight the moment its projected track enters a zone, instead of waiting for the 15 s check), a click-a-plane comparison of original, current and shadow with the miles, and none of this has been watched at length by a human yet.

### Phase 6c. The spoken loop. Saturday night

One switch now, **Voice: Off | On**, replaces Manual and Auto. Off is the path demo (Tower sends everything by data link, instantly, and the Whisper model is not involved). On is the real loop: Tower proposes a card, the controller says it, the AI pilot reads it back, our Whisper model hears both, and the checker compares. Tower speaking the cards itself is parked; the code is still there (`set_auto_voice`). The screen opens with Voice off (`TOWER_VOICE=on` to change that); `World` itself still defaults to voice on, which is what the tests drive.

**The pilot is a test fixture, not the product.** It is handed the clearance as data and decides, per transmission, to read it back right or make one of the documented pilot errors. It does not listen to the controller. The validator only ever gets the pilot's audio. That is deliberate: a pilot agent that mishears would just be a second speech model making errors we could not count, and we would lose the ground truth that makes "errors caught / injected" an honest number.

Built and checked in a browser with real audio (macOS voices, the deployed run 2 model):
- **You can hear the frequency.** Before this, a pilot's readback only ever played if it raised an alert. Now every clip goes on the air as a `radio_audio` event *before* it is transcribed, the browser plays clips one at a time in order (`frontend/lib/radio.ts`), the aircraft pulses green while it transmits, and the transcript header says who is speaking. There is a mute.
- **What you said is checked against the card.** A conflict (heading 210 where the card says 120) is held: nothing goes to the pilot, the card shows what Tower heard, and the controller either says it again or presses **Send as heard**. Tower cannot tell a controller slip from Whisper mishearing the controller, so it asks. Saying only part of a card goes through and the card stays open for the rest. Keying the mic with no callsign or no instruction now says so instead of doing nothing.
- **The next readback can be scripted:** by chance, correct, wrong value, wrong plane, no reply. One shot. A wrong value on a routing is now a similar-looking *other fix* (`pilots/errors.py::_mutate_route`), and the aircraft really flies to it.
- **A correction closes the loop.** Saying the correction belongs to the original exchange: the pilot reads it back right, the alert turns green ("corrected and read back right in N s", `alert_resolved`), the card goes to readback OK, the ring comes off the aircraft, and no second alert fires.
- Cards for flights not yet on frequency say so and have no button.

**Two real bugs this found.**
- `frontend/lib/ws.ts` has a whitelist of event types and drops anything else without a word. Three new events vanished until they were added there. A new backend event needs a line in three places: that set, `EventMap`, and the reducer.
- **Waypoint snapping overwrote a clearly spoken fix.** With the aircraft routed to ZAMIR (after a wrong readback), the controller's "proceed direct ESTIR" was rewritten to ZAMIR, because fixes on the aircraft's route were preferred at a very low bar. The correction therefore asked for the wrong fix and the alert could never close. A heard word that *is* a known fix (similarity 88 or more) now stays that fix, and the route preference yields when another fix fits far better.

**Merged with Ayan's voice work (PRs 3 and 4), Saturday 21:00.** Both of us had built a wrong-fix readback and a correction that clears its alert. Kept: his wrong-fix injector (`pilots/errors.py`, fix names passed down the call chain, not a module global), his `trust_hint` in `snap_waypoints` with the route hint still winning for the controller's own voice, his guard against guessed instructions, his double-press guard on "Say it", his clickable callsigns and "overdue". Added on top: a heard word that clearly is a known fix stays that fix (`SURE_FIX`), the said-versus-card hold, scripted next readback, a correction is read back right and the alert shows "corrected in N s" for ten seconds before it leaves (his filter drops it, mine marks it; resolved alerts are exempt from the filter until they time out). 344 backend tests.

The deployed Whisper scales to zero when idle. The first call after a quiet half hour timed out (two 8 s attempts) and the local model took over, as designed, but that is a 16 s stall on the first transmission of a demo. Warm it before judging: run `backend/tools/asr_smoke.py` once, or set a minimum of one replica on the Baseten page.

Still to do from the voice plan: do not replace a card while the mic is open (periodic replans already wait; a readback-triggered one does not); the resolver agent still runs inline and can pause the radar for 1 to 3 s; a fixed five-plane voice demo scenario with pre-generated ElevenLabs clips; the ElevenLabs character counter.

### Phase 6d. A spoken reroute is two cards, and both must survive a queue. Sunday, early

By voice a reroute is **a heading now, then "proceed direct <exit>"** tagged Back on course. That part is simple. What made Voice on feel unreliable was timing: the planner works an instruction out for one place and one moment (25 s ahead, `FROZEN_MANUAL_S`), and a spoken instruction takes effect whenever its card gets said and read back. One storm in the demo scenario reroutes six flights; one controller says one card every 14 s or so; the last card waits over a minute. Simulating exactly that (one voice, top card first) found three faults, none of which show when every card is said five seconds after it appears:

1. **A heading read back, then a near copy two seconds later** ("heading 039", then "heading 034"). `_tighten` trims a detour until it just clears the 3 NM zone margin for a turn at one exact moment. The real turn came a few seconds off, the same heading grazed the margin by under half a mile, and the planner replaced the heading it had just issued. Fix: a heading that is already being flown is judged at `HOLD_MARGIN_NM` (1 NM) along the held leg; the leg back to the exit still needs the full margin, and the zone itself is never entered.
2. **Direct, heading, direct, heading, for as long as anyone kept reading.** "Proceed direct" was offered when direct was clear *from a point 25 s ahead*. Said at once, the aircraft turned short of that point, clipped the margin, and got a new heading. Fix: the follow-up is offered only when going direct is clear **from where the aircraft is this second** (`plan._hold_heading` checks that first). That answer stays right however late the card is said, because further along the heading the way back only gets clearer. Until then `path.via` holds the expected turn-back point for the map, every flight on a heading is looked at again on each replan, and `World._back_on_course` asks again as the aircraft reaches that point.
3. **A heading card going stale while it waits.** Voice on now plans `as_flown`: the frozen start of every airborne path is what the aircraft is cleared to do right now (its assigned heading, or its own route), not what the previous plan hoped. Flights with a heading card nobody has said yet (`World._unsaid_headings`) are planned again from where they are on every replan, and the card is replaced once the heading on it is more than `SAME_HEADING_DEG` from the one to say. That comparison is now against the card, not the previous plan: measured plan to plan, a heading creeping two degrees every 15 s never counted as new. A waiting heading the plan no longer wants is taken down.

Measured after, one voice, 14 s per exchange, storm at 10 min: demo scenario, six flights rerouted, one heading each, follow-ups 4 to 6 sim minutes later (the storm is 20 NM across; at 20x that is 15 s), no loss of separation, nobody in the storm. Dense: five rerouted, one heading each, same. Voice off is untouched (`as_flown` and `unsaid` are voice-on only): the six-disruption stress run gives the same numbers, line for line, as the commit before. Checked in a browser with real audio: heading said, clock runs on at 20x, Back on course appears, said, aircraft goes direct; a wrong-aircraft readback injected on the way was caught and re-said.

**A bug in the merge that froze every screen.** `_trust_the_card` put a pydantic object into an event payload. `json.dumps` raised inside `Hub.pump`, the task died without a word, and the backend carried on with every browser stuck on its last frame. It only happened when a two-item card was heard in a different order. Fixed at both ends: the payload is dumped, the comparison ignores order, the pump now logs and skips an event it cannot send, `_json_default` handles models, and the clock's try covers the whole tick. Two tests.

**The clock slows itself (voice on).** `World.clock_speed()`: 1x whenever something needs saying or an exchange is in progress, the chosen speed otherwise. The chip in the top bar says which and why. A short demo can sit at 20x and never miss a card. `radar.clock_speed` carries it.

The instruction panel puts reroutes, conflicts and follow-ups above opening shortcuts, so an overdue "saves 6 NM" direct never sits on top of a storm reroute.

For the pitch: a Random storm goes where it hurts most, which by voice means six cards to read. Use **Choose** and click the storm onto one or two flights' paths instead. 354 backend tests.

### Phase 6e. Say anything, and see it happen at once. Saturday night into Sunday

The controller's complaint after an evening at the mic: "what I say, I should see happening", and it was taking four to six seconds, or not happening at all. Measured by replaying his own recordings into the backend over the websocket, exactly as the browser sends them.

**Responsiveness. Key released to aircraft turning: about 1 s (was 4 to 6 s).**
- The AI pilot now decides its reply, the aircraft acts on it, and only then is the voice synthesized (`AIPilot.respond(speak=False)` then `voice_it`). The voice is a network call, 1 to 3 s, and nothing on the radar moved until it came back. The aircraft still obeys the pilot's version, so a wrong readback still shows on radar.
- The reply no longer waits in a queue served once a tick (1.5 to 2.5 s of nothing). With a screen attached it is a task with a 0.15 s key-up (`PILOT_KEY_UP_S`).
- The controller's own voice is transcribed `fast`: one beam, one hypothesis (0.3 s on the T4 against 0.8 s). The alternatives are for judging a pilot's readback, not for hearing the person at the desk. The stock model's version for the side-by-side is no longer waited for: it arrives afterwards and the transcript line is sent again with it (the screen now upserts transcript lines by id).
- The connection to Baseten is opened when the key goes **down** (`asr.warm()`, a GET the front door answers, no GPU). It went idle between transmissions and DNS, TCP and TLS were 0.4 s of every one.
- A radar frame goes out the moment an aircraft accepts an instruction, and the planner re-plans round it then, not after the readback has been checked.
- On the map: a green chip on the aircraft with what Tower understood ("✓ H270 ↑FL360"), the cleared heading as a vector from the aircraft (amber while it turns, cyan once established), cleared heading and level as a third line of the data block. A turn at 1.5 degrees a second takes half a minute to see; these take none.
- **Cold start.** The deployed Whisper scales to zero after about 20 idle minutes and takes a minute to return; met cold on the air that was a 20 s transmission. Now: woken at startup and on Voice on (`wake()`, half a second of silence), kept warm every `ASR_KEEP_WARM_S` (240 s) while voice is on and a screen is connected, and the controller's transcription makes one 3.5 s attempt before the local model hears it. One local model is shared by the fallback and the stock comparison, so the fallback is never loaded for the first time at the moment it is needed.

**Plain English.** Whisper had heard "Delta seven eight nine, turn around and turn left and raise your elevation three hundred feet" perfectly. The grammar knows only standard phraseology, the fallback language model copied out "heading: around", formatting that raised inside a background task, and the transmission vanished: no notice, no pilot, nothing in the log.
- `tower/freeform.py`: plain English resolved against the aircraft it is said to, by patterns, so it costs no time. Turn around, turn left 30 degrees, turn slightly right, fly north, go up another two thousand, level off, speed up, slow down, resume own navigation, go straight to a fix. Everything comes out as a standard absolute item (turn around on 018 is "turn left heading 198"), so the pilot reads back standard phraseology, the checker compares like with like, and the simulator needs no idea of "relative". What it uses is blanked out and the grammar reads the rest of the sentence.
- New in the simulator: a three sixty (`SimCommand` kind `orbit`) and a hold that lasts until the next instruction. `AircraftState.manoeuvre` says so, the data block shows it, and Tower issues no cards for an aircraft that was told to circle. Known limit: other flights are planned against its old path, not the circle (the circle is about 4.5 NM across, inside the 5 NM they keep anyway).
- "Disregard" puts an aircraft back to what it was cleared to do before the last instruction. Something no airliner does ("do a barrel roll") gets "unable" from the pilot and changes nothing.
- **The interpreter agent** (`tower/interpreter.py`), for what patterns cannot place: "take it round the north side of the storm", "go back to where you were going". One tool-calling model call on Baseten (the resolver's model), given the picture as a pilot would say it (aircraft, fixes, zones and traffic as bearing and distance) and the aircraft's controls as tools: `fly_heading`, `change_level`, `change_speed`, `direct_to`, `circle`, `unable`. Measured 0.5 to 3.4 s. It runs off the clock in a thread with a 6 s cap, only when grammar and patterns found nothing, and its answers are validated and read back like any other instruction. It replaces the older "what was heard" fallback for the controller's speech, which knew nothing of the aircraft and ran inside the lock. Tried live: north side of a storm bearing 147 at 56 NM gave heading 118; "get above that weather" gave unable, because the tops are above the aircraft's ceiling.
- Every item is validated before a clearance opens (`freeform.valid`), and every background task on the socket is guarded (`app._spawn`): an error is logged and shown as a notice, never swallowed.

**The controller outranks the card.** The "Tower heard something else, send as heard?" hold is gone. What was said is what happens; the card is marked "you said something else, and the aircraft is doing it", and the planner, which already plans from what each aircraft is really doing, re-plans everyone round it at once. If the instruction itself is the problem, the next card says so. One exception, because our simulated pilot acts on Tower's transcript, which no real pilot does: a *standard* phrase that clashes with the card while the speech model itself was unsure (confidence under 0.6) is taken as the card, with a notice.

Also fixed on the way: the item validator first rejected heading 000, so a card for north could never be said (cards now say "three six zero"); a circle is read back "making a three sixty to the left", never "left three sixty", which is also how a pilot shortens "turn left heading three six zero".

396 backend tests. `tests/test_freeform.py` has the phrases; add to it when a phrase is missed at the mic.

**A false "NOT FLYING THE CLEARANCE" with voice off, fixed.** By data link an aircraft is sent a path ("heading 155 for 8 miles, then direct ESTIR") and flies it, but radar verification was still told to wait for heading 155. On a short leg with a big turn the aircraft never points down the leg: the turn alone needs five or six miles, and it has to start back before the corner. It flew the route exactly, and a minute later was reported for it (2 of 15 reroutes in a six-disruption run). `ConformanceMonitor.watch(..., path=)` now judges a data-link reroute on the line it was sent, as `flyable` draws it: within `PATH_TOLERANCE_NM` (4), confirmed once it is on the line *and going the way the line goes*, reported after 20 s off it. Voice on is unchanged: there the aircraft really is given a heading. The test flies the real simulator through such a jog, asserts the old check raises the false alert, the new one does not, and that an aircraft which really leaves its route is still reported.

### Phase 6f. Disruptions that land where you mean them. Sunday, after midnight

Placing a storm or a launch "on a path" by hand usually missed, and it looked as if Tower had ignored it. Three reasons, the first by far the biggest:

1. **The click was read on the ground; the traffic is drawn in the air.** Aircraft and their lines are drawn at height, exaggerated six times: FL350 is 64 km up, and in the tilted view that is about 40 NM up the screen from the ground beneath it. A click on a line was unprojected to the ground *behind* it, so a 12 to 25 NM zone landed 40 NM from the path that was clicked. Placement now unprojects the click at the level the traffic is drawn at (`viewport.unproject(..., {targetZ})`, median level of the flights on radar). Checked in the browser: a launch clicked on ACA133's line landed 9 NM off its ground track (it used to be about 40), well inside the zone's 20 NM, and ACA133 was rerouted.
2. A launch lasts 7 to 10 minutes. Put further ahead than the aircraft can reach in that time, the planner rightly ignores it.
3. Closed airspace blocks only a band of levels, and drones and balloons fly low and slow. Both often affect nobody at cruise.

**Disrupt this flight.** Select an aircraft and the flight strip has four buttons: Storm ahead, Launch ahead, Fighter, Mayday. `{"type":"add_disruption","kind","target":"<callsign>"}` and `World._ahead_of` puts it on that flight's own planned path: a zone centred its radius plus 13 to 22 NM ahead (room to go round it, near enough that the detour starts now, the small end of the kind's size so the way round is short, nobody else underneath if that can be had); an intruder timed to meet the flight four minutes on; a mayday is that flight. Tried on every flight in the demo at three moments, 51 presses: the chosen aircraft was rerouted every time, never entered the zone, no loss of separation. This is the button for the pitch: "watch Delta 789. Storm."

**Four kinds on the menu**, storm, rocket launch, fighter, emergency (`disruptions.MENU_KINDS`, `menu` in `state.disruption_kinds`). Drone, balloon, unknown and closed airspace stay in the table and the headset agent can still ask for them.

### Phase 6g. Seeing conflicts before they exist. Sunday

The planner's conflict test is exact and blind: it asks whether the plan, flown perfectly, keeps 5 NM and 1,000 ft. It says nothing about a pilot who acknowledged and has not turned yet, a slow turn, a storm drifting into a path, or a wrong heading that will not be a conflict for another minute. Those were caught by the 15 s or 60 s periodic replan, or by the loss of separation itself. TRD 07 has the spec.

**What it is.** Every tick after `sim.step`, `backend/planner/risk.py` rolls the whole sky forward 120 s a few hundred times with noise (compliance delay 0 to 15 s, ground speed ±3 percent, heading σ 2 degrees, climb and descent rate ±20 percent, zone drift ±15 degrees, all seeded) and counts how often each nearby pair would lose separation at the same 5 s sample. Pure numpy, no World imports, positions as one `(n, aircraft, samples, 3)` array, pairs pruned to those within 60 NM and 4,000 ft now. It is Monte Carlo over disturbances feeding the deterministic planner, not MCTS over actions: the risk decides *when* to replan, the existing planner decides *what*.

**Thresholds.** `REPLAN_P` 0.30: any pair at or above it, with its first crossing inside the horizon, calls `_replan("risk A/B")` at once, rate-limited to one risk replan per pair per 20 s. `SHOW_P` 0.05: the display floor; pairs below it are not in the report. Both live in `risk.py` and nowhere else.

**Budget.** 256 rollouts by default, floor 32. The count halves when the last call ran over its budget (25 ms at 12 aircraft, 150 ms at 100) and doubles back up when it ran under half, so the clock loop never stalls on the 150-flight Europe scenario. Above 1x the prediction runs every 2 s of sim time, not every tick.

**Confidence.** Every card now carries `confidence = (1 - risk_after) × margin_factor`, clamped to [0.05, 0.99]. `risk_after` is the residual `p_max` on the pairs the card's aircraft is in, re-scored on the new plan before the card goes out. `margin_factor` is 1 when the chosen path beat the runner-up candidate by 20 percent or more of its cost, falling linearly to 0.5 when they tied (`PlannedPath.runner_up_cost`). Shown, not acted on: gating below a threshold is TRD 06 item A3. The strip shows both terms, "risk after 0.03, margin 1.0", so the number can be explained when a judge asks.

**What the judge sees.** A translucent red wedge between two aircraft before anything is wrong, labelled "LoS 42% · 71 s", its width the p5 to p95 lateral spread of the rollouts at the closest approach, brightening as the probability rises and gone when the replan clears it. A confidence on every card and strip. Three new scoreboard tiles: conflicts predicted, resolved before they happened, and futures simulated per second, the last one measured from `n_rollouts × aircraft / elapsed` that tick. The slide says "a few hundred futures a tick", never "thousands" unless the counter does.

Measured Sunday 00:40 on the demo laptop: 2.3 ms and about 545,000 futures per second with 5 airborne (demo), 61 ms and about 273,000 with 65 airborne (Europe replay), n=256. Live scoreboard during a run: 200,000 to 450,000. In a head-on test with Voice off the periodic replan vectored the pair apart before risk reached 0.30, which is the planner working; the risk trigger earns its keep when a hazard develops faster than the 15 s check, and that case has not been watched live yet..

- [ ] Drop a storm on `demo`: the cone appears before the replan fires, and clears after it
- [ ] Force a wrong heading toward another aircraft: the risk replan fires earlier than the periodic check would have
- [x] Confidence shown on every card and strip, in [0.05, 0.99] (seen live: 0.99 on directs, 0.5 on a tied candidate)
- [x] Futures per second on the scoreboard is the measured number for this laptop, not a constant
- [x] 437 tests pass, including `test_risk.py` and `test_world_risk.py`

**Integration pass after merging main (Sunday, early).** Main merged into this branch with no conflicts; 438 backend tests, production build clean. What the PR had not measured:
- **Scale.** Tick cost with the prediction on, real Europe hour: 33 aircraft 23 ms at 1x and 65 ms at 20x; about 60 aircraft 57 ms at 1x, and at 20x a median of 141 ms with 318 ms at the 95th percentile, over the 250 ms a tick has above 1x, so the clock fell behind. The prediction's budget above 1x is now 40 ms (it was 150 ms whatever the clock was doing); it settles at 64 to 128 rollouts there and the same run is 69 ms median, 248 ms at the 95th percentile, which is the periodic replan, not the prediction.
- **It never speaks with voice off.** Demo, dense and the real Europe hour with four disruptions each: no pair ever reached the 5 % display floor. Tower's plan keeps everyone 8 NM apart and data link applies it in the same second, so there is nothing left to predict. Cones are a voice-on thing.
- **Voice on is where it earns its place.** With cards left unsaid, pairs appear and climb to 100 % (demo: 4 pairs, 5 predicted; dense: 3 pairs, 7 predicted), which is the conflict the unsaid card was for. For the pitch: leave the DAL789/UAL210 card unsaid, watch the wedge grow, say the card, watch it clear.
- **No extra churn.** One controller saying one card every 14 s after a storm: still one heading and one back-on-course card per flight, no loss of separation, nobody in the storm. Voice off stress run (six disruptions, four scenarios): same numbers as before the merge.
- The whole test suite now takes about 2.5 minutes instead of 35 s, because every tick of every world in every test runs the prediction. `World.risk_predict` is injectable if that becomes a nuisance.

**A custom sky in Setup.** Beside Demo, Dense and Intruder the simulated tab has **Custom**: aircraft (2 to 80), how fast they arrive (calm, normal, busy) and Shuffle for another draw. `sim/scenarios.py::custom` generates it on the demo route network, three already entering when Start is pressed so the screen is never empty, the upstream 3 minutes per entry fix still kept, so "busy" with many aircraft is a long queue and the description says how many share the sector at the peak. Its name is its recipe (`custom/24/busy/7`), so loading, Reset and repeatability need nothing stored.

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
