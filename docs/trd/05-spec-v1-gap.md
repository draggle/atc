# TRD 05: Product spec v1 versus what is built

Written Sunday Sept 20. Diff of the "Agentic Air Traffic Control, Product Spec v1" against main at d773801 (356 backend tests). Each spec item is marked built, partial, or missing, with the code that covers it. The feature requests at the end are what it takes to reach the spec, ordered by the spec's own three demo moments. `docs/10-roadmap.md` remains the team's working plan; this file is the gap list against the spec, for the team to pull from.

## 1. What the user interacts with, in the spec's product

You sit at one full-screen radar scope with about a dozen aircraft in a sector. A radio channel is playing: the agent's clipped controller voice and the pilots' readbacks. Every transmission drops into a strip on the right rail with who said it, what, when, and how sure the agent was. You talk to it like a controller. Hold the mic and say "United 12, descend flight level 240" and it goes out on the frequency. Hold the mic and say "why did you turn United 12?" or "show me the plan for Delta 427" and a second agent answers you and moves the screen, rendering a route card or a conflict card instead of paragraphs.

Before a conflict exists, a translucent cone appears between two aircraft with a time to conflict, because the agent is simulating thousands of futures a second. The agent resolves it and the strip shows its confidence. When it is not confident, it stops: the decision freezes in an escalation panel with a one-line reason, the proposed instruction, alternatives, and a countdown to when deferring becomes unsafe. You approve, edit, or reject by voice or click. If you do nothing, it issues the safest fallback and flags it.

You break things. Drag an aircraft off its route, draw a storm and watch it drift, declare an emergency, silence a radio, add an arrival, make a pilot ignore an instruction. The airspace re-solves jointly, old routes fade, new ones animate in, and a counter shows aircraft rerouted, delay added or saved, fuel added or saved. A slider trades safety against efficiency and the plan visibly changes.

You put on a headset and become a pilot. Say "Delta 427 requesting descent" and the agent clears or denies you in phraseology within two seconds. Read it back wrong and it corrects you. Then you scrub back thirty seconds, fork the timeline, change one thing, and watch two futures run side by side with a scoreboard. At the end the numbers come from a simulator calibrated to real traffic distributions, evaluated against an honest sequential human baseline whose assumptions are on screen, and extrapolated to annual flights with sources cited.

## 2. Checklist

| Spec item | Status | What exists | Gap |
|---|---|---|---|
| Deterministic tick sim, event log, headless | Partial | `backend/sim/engine.py` seeded 1 s ticks, up to 60x, headless in `backend/eval`. Pilot ground truth logged to `data/ground_truth.jsonl` | No replayable event log of every action and perturbation; reset rebuilds from the scenario rather than replaying |
| Aircraft kinematics and separation constraints | Built | 1.5 deg/s turns, 1,500 fpm, accel 1 kt/s, 5 NM and 1,000 ft hard floor in planner and `SeparationMonitor` | One performance envelope for all types; spec wants per-type turn, climb, speed range |
| Weather cells, static and moving | Built | `backend/disruptions.py`, zones with drift, swell, floor, ceiling, expiry | Circles only; spec wants drawn polygons |
| Monte Carlo conflict prediction and conflict cone | Missing live | `backend/eval/montecarlo.py` runs offline arms; the planner's conflict test is deterministic on sampled trajectories | No per-tick rollouts under uncertainty, no per-pair probability versus time, no cone on the map |
| 4D A* routing | Missing | `backend/planner/plan.py` prioritized candidate search: delays, speed and level changes, doglegs, escape legs, `flyable` turns; cost is time, distance, level change, deviation | Not a grid search. Functionally covers the same cost terms; would need a rewrite to claim A* |
| Joint replanning over conflict components | Partial | Replan moves only conflicting or deviated flights, widens to neighbours up to three rounds, improvement pass reorders | Within the component it is still sequential by priority, not a joint search |
| MCTS action selection with a safety-efficiency knob | Missing, knob partial | Separation buffer slider adds margin; cost weights are constants | No tree search over controller actions; no cost reweighting slider |
| Confidence scoring, escalation panel, countdown, fallback | Partial | Readback verdicts carry confidence; resolver ends alert, dismiss, or uncertain; "Checking" and "Watching" cards with a countdown; said-versus-card hold with "Send as heard" | Planner decisions carry no confidence; no approve, edit, reject flow; no timer that falls back to a hold |
| STT with phraseology post-processor | Built | whisper-small tuned on Baseten (0.155 WER on real clips), local fallback, `tower/normalize.py`, callsign and waypoint snapping, `ATC_PROMPT_PREFIX` | |
| TTS with radio filter under 2 s round trip | Partial | `pilots/radio.py` band-pass, clip, noise, squelch; macOS `say` or ElevenLabs; Baseten ASR 0.8 s at beam 3 | Round trip measured at about 4 s locally; not measured against the deployed model end to end |
| Airspace agent: decisions and spoken clearances | Partial | Planner plus instruction cards; voice off sends everything by data link instantly; Tower speaking cards itself (`set_auto_voice`) exists but is parked | Re-enable and test Tower's own voice; the spec's default is the agent transmitting |
| IO agent: voice to UI control, generative UI, why explanations | Partial | `backend/world_agent.py` builds the world by voice or text (load, spawn, disrupt, multiply, settings, describe) | Cannot drive the screen (zoom, focus, show plan), cannot explain a decision, renders text not components |
| Pilot mode: judge on headset, readback checking | Partial | Readback checking, correction, and closure are built; AI pilots are deliberate fixtures that never listen | No "become this aircraft" flow; no parser for pilot requests (descent, climb, deviation, direct, emergency); no agent that answers a pilot |
| Perturbations | Partial | Storm, closed airspace, fighter, drone, balloon, unknown, rocket, emergency on an existing flight, inject flight by voice, random | Drag an aircraft off route: missing. NORDO: missing. Pilot non-compliance for N seconds: only via injected readback errors and missing readbacks |
| Replan visualization | Built | Ghost paths for 30 s, flash on change, rerouted count, first-turn reaction time, in-zone counters | Delay and fuel delta per replan: missing (only planned miles and time saved at load) |
| Rewind, fork, split screen | Missing | Sim is deterministic and seeded, so feasible; only Reset exists | |
| Human baseline with surfaced assumptions | Partial | Monte Carlo "fixed routes" arm has no controller at all; roadmap phase 7 already calls this out | Sequential first-come-first-served controller with reaction latency and buffers, and its assumptions on screen |
| Sim calibration from historical data, fit readout | Partial | Real adsb.lol replays and live snapshots as scenarios (`backend/scenarios/real`, `sim/live.py`) | No fitted distributions for arrival rate, density, weather; no "sim matches real delays within X percent" readout |
| Batch eval harness, scoreboard, distributions | Built | `eval/montecarlo.py`, `eval/sweep.py`, p5 and p50 closest approach, density curve; live `ScoreboardPanel` | Delay minutes, fuel, CO2, throughput, instructions per hour, escalation rate not in the metrics |
| Extrapolated impact panel with sources | Missing | | |
| Demo script wired end to end | Missing | Roadmap phase 9 unticked | |

## 3. Where the spec and the team's decisions disagree

- The spec puts real ADS-B feeds out of scope. The team built real replays and a live snapshot and learned from them that real cruise traffic saves only about 0.5 percent in miles. Keep them: they answer "is the data real" and they calibrate the sim.
- The spec's pitch line "simulating thousands of futures a second" is not true of the current planner, which is deterministic. Either build item 6.2 or do not say it. Hard rule 6.
- The spec's default has the agent transmitting; the team parked Tower's own voice and opens with voice off (data link). The code path exists.
- The spec wants ~12 aircraft in one sector. The demo scenario has 8 and the real ones 80 to 159. The 12-aircraft scenario is a scenario file, not a feature.
- Both agree on no learned world model, no RL, an honest baseline, and no backtest claims.

## 4. Feature requests, ordered by the spec's three demo moments

### Moment 1: the judge tries to crash a plane

1. **Drag an aircraft off its route** on the map. Pointer drag sets a heading or a position offset, sends a new client message, the sim applies it as a deviation, radar verification and the replan react. Needs a deck.gl drag handler, one client message, one `SimCommand` kind.
2. **Draw a storm as a polygon** instead of clicking a circle, with a drift vector. Zones become polygons in `schemas.Zone`, the planner's zone test and the map's extrusion follow.
3. **NORDO**: a radio-failure perturbation. The aircraft ignores instructions, the state machine marks it non-responsive after the timeout, the planner treats it as a non-cooperating intruder with the growing buffer.
4. **Pilot non-compliance for N seconds**: an aircraft acknowledges and does not turn. Radar verification already catches it; expose it as a one-click perturbation and a `next_readback` option.
5. **Delta counter on every replan**: aircraft rerouted, delay added or saved in minutes, fuel in kg from a per-type burn rate, versus doing nothing. Extend `Scoreboard` and `plan_update`.
6. **Live conflict prediction and cones**: per-tick vectorized rollouts 90 to 120 s ahead with compliance delay, speed and heading noise, zone drift; per-pair probability of loss of separation versus time; a translucent cone with ETA on the map above a threshold. The eval's disturbance model already exists in `montecarlo.py`; it needs to run incrementally every tick.
7. **Joint replanning inside a conflict component**: search over the component's flights together instead of by priority. Smallest honest version: enumerate candidate combinations for components of up to three or four flights and pick the joint minimum; fall back to prioritized above that.
8. **Safety-efficiency slider** that reweights the planner's cost (time and distance versus margin) and re-plans on change, with both extremes visibly different.
9. **Per-type performance envelopes**: turn rate, climb rate, speed range by aircraft type from a small table, used by the sim, the planner's `flyable`, and conformance.
10. **Replayable event log**: append every command, transmission, verdict, and perturbation with a sim timestamp so a world can be rebuilt by replay. Prerequisite for rewind and fork.

### Moment 2: the judge is the pilot

11. **Pilot mode**: pick an aircraft to become; the mic routes as that aircraft's radio; the AI pilot for that callsign is muted. One client message, a `speaker: "pilot"` path in `World` that bypasses the fixture.
12. **Pilot request parser**: descent, climb, weather deviation, direct-to, emergency, readback, "say again", in `tower/parse.py` with the pilot speaker role.
13. **An agent that answers a pilot**: check the request against the plan and separation, issue the clearance or a denial with a reason, speak it in phraseology within 2 s, open the clearance so the judge's readback is checked and corrected. Reuses the planner's conflict test and the existing correction path.
14. **Tower's own voice on by default**, un-park `set_auto_voice`, with the queue rules the roadmap already built, so the frequency sounds like a controller working traffic before the judge touches anything.
15. **Voice round trip under 2 s measured end to end** against the deployed Baseten model: warm replica, beam 3, TTS streaming or pre-generated clips for the common phrases.

### Moment 3: rewind and fork

16. **Scrub bar** over the last N minutes, driven by periodic state snapshots plus the event log from item 10.
17. **Fork here**: clone the world at a snapshot, apply a different perturbation or controller policy, run both forward at speed.
18. **Split-screen** with two map views and one scoreboard; the frontend already isolates world state by `world_id`.

### The agent the user talks to

19. **IO agent tools that drive the screen**: focus aircraft, follow, show plan, show conflict, open flight strip, set line view, set speed. Add to `world_agent.py` tools and emit a `ui_command` event the frontend applies.
20. **"Why did you do that"**: every card and replan already carries a `reason` and `cause`; expose them through the IO agent with the planner's cost breakdown so the answer is grounded, not generated.
21. **Generative UI**: the IO agent returns a component descriptor (route card, conflict card, metrics panel) that the frontend renders, instead of a chat bubble.
22. **Planner confidence and the escalation panel**: score each instruction by margin between best and second-best candidate cost, minimum predicted separation, and rollout variance once item 6 exists; below threshold, freeze it in a panel with rationale, proposal, alternatives, a countdown, approve, edit, reject by voice or click, and a hold as the timed fallback. Tune the threshold so 80 to 90 percent auto-transmit.

### The defensible number

23. **Honest human baseline controller** in the eval: sequential first-come-first-served conflict handling with 5 to 10 s reaction latency, conservative buffers, and a cap on simultaneous aircraft under attention; assumptions printed in the UI and the report. Roadmap phase 7 already lists this.
24. **Sim calibration from the adsb.lol data**: fit arrival rate, density, level and speed distributions per region and hour, and show the sim reproduces them within X percent.
25. **Metrics**: delay minutes versus filed plan, fuel and CO2 per type, throughput per hour, instructions per hour, escalation count and share, voice latency, near-misses resolved; in the live scoreboard and the batch report, as distributions.
26. **Extrapolated impact panel**: per-flight deltas times annual flight volume with the sources cited on screen and the assumptions visible.
27. **Demo script wired end to end** per spec section 9, with every step one keypress or one phrase away, rehearsed with a stranger, and a backup video.

## 5. Suggested order if the day is short

Items 1, 5, 11 to 14, and 22 make the three moments real with the code that exists. Item 6 is the biggest single credibility gain and the only way to say "futures a second" honestly. Items 16 to 18 are the most impressive and the most cuttable. Items 23 to 27 are what a sharp judge asks about; 23 and 27 first.
