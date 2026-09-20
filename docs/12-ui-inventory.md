# 12. UI inventory: every piece, what it does, every interaction

As of Sunday afternoon Sept 20 on `joey/command-bar`. Items marked *coming* are being built by agents on `joey/command-bar` and `joey/agent-mode`.

## Pieces

| Piece | Where | What it shows | Interactions |
|---|---|---|---|
| Boot screen | full screen at open | "squack." rising in, radar ping, pulses until the first state | none |
| Top bar | top | wordmark, scenario, clock, lifecycle, speed, squack on/off, Voice off/on, counters, connection | Setup, Start/Resume, Pause, Reset, speed, the two pills; *coming:* "normal / squack decides" toggle |
| Setup sheet | modal | Real day, Live snapshot, Simulated cards; region, day, hour, cap, scenario, density; Voice | pick, Load sky, Cancel |
| Map | full screen | routes, plan, replans, ghosts, zones, intruders, conflict wedges, rings, chips, vectors, sector | pan, zoom, tilt, rotate, click aircraft, click to place a disruption, hover |
| Disrupt | top left | Random, Choose, kinds, active chips | place, remove |
| View | bottom left | tilt, lines, altitude, legend | toggles, slider |
| Instruction cards | right | one card per instruction: tag, phrase, reason, confidence, urgency; later flights collapsed; done log | hold Space and say it, let squack say it, click to focus |
| Alert card | right, on demand | wrong readback, not flying the clearance, no readback, partial, unclear, checking, watching, corrected; expected vs heard, reason, correction, clip, agent steps | click to focus, play, dismiss, sound on/off |
| Flight strip | left, on click | issue, level, speed, heading, cleared, route, plan changes, last instructions, predicted conflict, confidence | follow, close, remove, storm ahead, launch ahead, fighter, mayday |
| Frequency | bottom left | every transmission, speaker, callsign, confidence, on air | tuned vs stock toggle |
| Demo | right | frequency mute, next readback script | chips; *coming:* squack speaks toggle |
| Scoreboard | right | 16 measured numbers | none |
| Chaos | right | buffer, error rate, noise | sliders |
| Command bar | bottom centre | mic, input, route chip, reply card | hold Space / Shift+Space, type, Enter, ⌘K, Escape; *coming:* typewriter examples, focus lift, live dictation, spoken replies, answer dock with cards |
| Notices | top centre | toasts | dismiss |
| *Stage (agent mode)* | right, replaces the rail | up to three cards squack places from the registry | flip the toggle; cards carry their own actions |

## Interactions by channel

**Radio (Space):** standard phraseology to any aircraft; plain English (turn around, go up another two thousand, resume own navigation, take it round the north side of the storm); corrections; anything unplaceable goes to the interpreter agent with the radar picture.

**squack (Shift+Space or the bar):** today: load scenario, add flight, any disruption kind, multiply traffic, error rate, noise, buffer, voice, squack on/off, describe. *Coming:* focus, follow, camera, lines, speed, panels; queries over aircraft, pairs, cards, log, timeline with a table back; why did you turn X; Monte Carlo and density sweep with a chart back; multi-step requests with a step trace; nudge one flight apart.

**Click:** everything above, plus disruption placement, aircraft selection, alert focus, card actions, camera.

**Keys:** Space, Shift+Space, ⌘K, Escape, Enter.

**The system acting on you:** cards, alerts, conflict wedges, replans, pilots answering on the frequency; *coming:* squack speaking back, and in agent mode, deciding what is on screen.

## Not yet possible

Being a pilot on the headset, approving or rejecting a low-confidence instruction, dragging a plane, rewind, polygon storms. See `docs/trd/06-decisions.md`.
