# 08. WebSocket protocol between the screen and the backend

Added Saturday night Sept 19 during the overnight build. Extends the event list in `03-architecture.md` and `07-build-spec.md` section 3. Payload shapes are the Pydantic models in `backend/schemas.py`. The frontend mirrors them in `frontend/lib/types.ts`.

## Endpoint

`ws://localhost:8000/ws`. One connection per screen. Audio clips referenced by `audio_ref` are served at `http://localhost:8000/audio/<ref>`.

## Server to client

Every message is one JSON object `{"type": ..., "payload": {...}, "t": <sim seconds>}`.

| type | payload | when |
|---|---|---|
| `state` | `{scenario, lifecycle, speed, world_id, scenarios: ScenarioInfo[], live_regions: {key, label}[], tower_enabled, auto_speak, t, waypoints: Waypoint[], zones: Zone[], sector_nm, buffer_nm, error_rate, noise, watching}` | on connect, on every lifecycle change, and whenever a setting changes |
| `radar` | `{aircraft: AircraftState[], zones?: Zone[]}` | once per second. `zones` is present while any zone is drifting or swelling and replaces `state.zones`. An intruder's `AircraftState` carries `threat`: fighter, drone, balloon, emergency or unknown |
| `plan` | `Plan` | after initial planning and every replan |
| `plan_update` | `{changed: string[], reason, trigger}` | with every replan |
| `instruction_card` | `InstructionCard` | when created or when its status changes. Status `superseded` means a newer plan replaced a card nobody had spoken: drop it |
| `transcript` | `Transmission` | after every utterance is transcribed |
| `clearance_opened` | `OpenClearance` | controller transmission with mandatory items |
| `clearance_updated` | `OpenClearance` | status change |
| `alert` | `Verdict` plus `audio_ref` | mismatch, missing, or resolver alert or uncertain. Never for match |
| `resolver_step` | `ResolverStep` | each tool call of the resolver |
| `disruption` | `Disruption` | when a disruption is added, and again with `active: false` when it expires, leaves the sector or is removed. See Disruptions below |
| `scoreboard` | `Scoreboard` | every few seconds and after every verdict |
| `stats` | `{tier1_latency_s, transmissions, matches, alerts}` | rolling |
| `agent_reply` | `{text, actions: string[]}` | after the world-builder agent handles a request |
| `notice` | `{text, level: "info"\|"warn"\|"error"}` | an action was refused or something failed, for example the radio keyed before Start |

## Client to server, JSON

| message | effect |
|---|---|
| `{"type":"ptt_start","channel":"radio"\|"agent"}` | start of push-to-talk. Binary PCM frames follow |
| `{"type":"ptt_stop"}` | end of push-to-talk. The utterance is transcribed and routed to the channel |
| `{"type":"agent_text","text"}` | typed request to the world-builder agent |
| `{"type":"radio_text","text"}` | typed controller transmission, fallback when there is no mic |
| `{"type":"configure","source":"sim","scenario","density"}` | build a world and its plan. Lifecycle becomes `ready`. **The clock does not start.** `load_scenario` with `name` still works as an alias |
| `{"type":"configure","source":"real","scenario":"real/<region>_<date>_<hhmm>","max_flights"}` | load recorded traffic, thinned evenly over the hour to at most `max_flights`. `state` then carries `source: "real"`, `meta` (region, label, date, hour_utc, gates, attribution, caveats), `geo.shape: "circle"`, and waypoints with `kind: "gate"`. Hidden track vertices are never sent |
| `{"type":"configure","source":"live","region":"<key>","max_flights"}` | take **one snapshot** of the real sky over a region from adsb.lol and load it as a scenario (`backend/sim/live.py`). `region` is a `key` from `state.live_regions`. The fetch runs off the clock: the backend answers at once with an info `notice` ("Fetching the live sky over ..."), and the `state`, `plan` and `radar` of the new world follow when the snapshot is in, usually under 2 s, at most about 12 s. Nothing polls afterwards. A second live `configure` while one is in flight is ignored with an info `notice`. An unknown region is an error `notice` |
| `{"type":"start"}` | `ready` or `paused` to `running` |
| `{"type":"pause"}` | `running` to `paused` |
| `{"type":"reset"}` | back to the world as it was loaded: clock at zero, nothing issued. New `world_id` |
| `{"type":"set_speed","speed"}` | sim seconds per real second, clamped to 0.25 to 120. The screen offers 1, 5, 20, 60 |
| `{"type":"set_tower","enabled"}` | Tower on or off. Off means readbacks are not checked and the plane flies what the pilot said |
| `{"type":"set_auto_speak","enabled"}` | the agent speaks instruction cards itself |
| `{"type":"add_disruption","kind","x_nm"?,"y_nm"?}` | drop a disruption. `kind` is fighter, drone, balloon, emergency, unknown, storm, closed, rocket, or `random`. With no position, or for `random`, Tower puts it on the path of a flight a few minutes ahead. `intruder` still works and means fighter. A position outside the sector is refused with a `notice` |
| `{"type":"remove_disruption","id"}` | take one out by hand. An emergency aircraft cannot be removed: it is a real flight |
| `{"type":"speak_card","id"}` | speak one card by TTS now |
| `{"type":"set_sliders","buffer_nm","error_rate","noise"}` | separation buffer, pilot error rate, radio noise |

### Live mode

`state.live_regions` lists the regions live mode can load, `[{key, label}]`. It comes from `backend/sim/regions.py`, not from files on disk, so it is present even when no replay scenario is installed.

A live world looks like a real-replay world to the screen: `scenario: "live/<region>"`, `source: "real"` (there is no third value), `geo.shape: "circle"`, gates as waypoints. It is told apart by `meta`:

| meta key | value |
|---|---|
| `live` | `true` for a world built from a live snapshot. Absent otherwise |
| `snapshot_utc` | when the snapshot was taken, ISO 8601, for example `2026-09-19T19:54:14Z` |
| `fallback` | absent when the live feed answered. `"saved_snapshot"`: the feed failed and the newest snapshot saved under `data/live/` was loaded, so `live` is still `true` and `snapshot_utc` is older. `"replay"`: no saved snapshot either, so the newest committed `real/<region>_*` hour was loaded, and `live` is absent |
| `attribution`, `caveats` | as in real mode. The live caveat says each route is the current track projected straight to the boundary, so miles saved is zero by construction |
| `seen`, `kept: {inside, inbound}`, `dropped: {reason: count}` | how many aircraft the feed held, how many are in the scenario (inside the circle now, or entering within 30 minutes), and why the rest were left out |
| `region`, `label`, `radius_nm`, `floor_ft`, `gates`, `max_flights`, `flights_available` | as in real mode |

Every fallback comes with a `notice` at level `warn` that says what happened, for example "Live feed unavailable (adsb.lol: ConnectTimeout). Loaded the snapshot from 19:54 UTC on 2026-09-19 instead." If there is nothing to fall back to, the `notice` is level `error` and the world is left as it was. `reset` returns to the loaded snapshot and does not fetch again.

## Geography

The simulator and planner work in a flat plane: x east, y north, nautical miles, centred on zero. Every scenario carries a `GeoFrame` that pins that plane to a point on Earth (`backend/sim/geoframe.py`, azimuthal equidistant). The backend converts at the edge, so **every position-bearing payload carries both**:

| Payload | Flat fields | Real-world fields |
|---|---|---|
| `radar.aircraft[]` | `x_nm`, `y_nm` | `lat`, `lon` |
| `state.waypoints[]`, `state.zones[]` | `x_nm`, `y_nm` | `lat`, `lon` |
| `state.geo` | | `{lat0, lon0, projection: "aeqd", name, half_nm, bounds: [[west, south], [east, north]]}` |
| `plan.paths[]`, `plan.baseline_paths[]`, and the same in `plan_update` | `samples`: **endpoints only** on the wire, `[first, last]`. The full 10 s samples stay in the backend | `lonlat: [lon, lat, alt_ft, t][]`, simplified for drawing: first, last, every corner and level change, and at least one point per 5 minutes |
| `disruption` | `x_nm`, `y_nm`, `predicted_path: [t, x, y][]` | `lat`, `lon`, `predicted_lonlat: [lon, lat, t][]` |

- Order is `[lon, lat]` in arrays, GeoJSON style, which is what deck.gl and MapLibre expect. Named fields are `lat` and `lon`.
- Client messages still use sector NM (`add_disruption` takes `x_nm`, `y_nm`). To turn a map click into NM use `latLonToNm` in `frontend/lib/geo.ts`, which mirrors the backend projection to 1e-13 degrees.
- Accuracy: round trips are exact. Pairwise distances inside a 600 NM region differ from the great circle by at most 0.17 percent. Regions much larger than that should say so on screen.
- The built-in scenarios default to a frame centred on Toronto Pearson. A scenario YAML can set its own: `geo: {lat0: 50.5, lon0: 6.0, name: "Western Europe core"}`.

## Lifecycle

`idle` (no world) -> `ready` (loaded, previewable, clock at zero) -> `running` <-> `paused` -> `ended` (every flight has left).

- The server starts `idle` and runs nothing. `TOWER_SCENARIO=demo` preloads a world to `ready`. `TOWER_AUTOSTART=1` also starts it, for headless runs.
- `world_id` bumps on every load and reset. The screen drops the previous world's cards, transcript, alerts, and tracks when it changes.
- The radio only works while `running`. A transmission at any other time gets a `notice`, not a clearance.
- Above 1x the server ticks four times a second and sends one radar frame per tick. The simulator still steps at most 1 s at a time inside a tick, so timeouts and separation checks never skip.
- A screen that connects to a `ready` or `paused` world receives the current radar frame and scoreboard straight away.

## Client to server, binary

Raw PCM16 mono 16 kHz frames between `ptt_start` and `ptt_stop`.

## Channels

- **radio**: the utterance goes through the full Tower pipeline as a controller transmission. AI pilots hear the parsed clearance and reply by voice into the same pipeline.
- **agent**: the utterance is transcribed with stock speech recognition and handed to the world-builder agent, which can only change the world, never issue a clearance.

## Disruptions

One type, many kinds. Every number that describes a kind (speed, size, levels, how long it lasts, how much room the planner gives it, how often Random picks it) lives in `backend/disruptions.py` and nowhere else. The screen builds its Disrupt menu from `state.disruption_kinds` (`kind`, `label`, `blurb`, `shape`), and `state.disruptions` lists the ones still active so a reconnect restores them.

| shape | kinds | what it is in the world |
|---|---|---|
| `point` | fighter, drone, balloon, unknown | an aircraft with `is_intruder: true` and `threat: <kind>` that flies a straight line and answers nobody. `id` is its callsign (`VIPER3`, `DRONE1`). Payload has `hdg_deg`, `gs_kt`, `alt_ft`, `predicted_path` |
| `point` | emergency | not a new aircraft: one of our flights (`id` is its callsign) stops taking instructions, descends at 3,500 fpm to 10,000 ft and diverts to the nearest edge. It says so on frequency, and that mayday goes through the radio effect and Whisper like any other pilot call |
| `circle` | storm, closed, rocket | a `Zone` with the same `id`. `radius_nm`, `floor_ft` to `ceiling_ft` (99999 = every level), optional drift (`hdg_deg`, `gs_kt`) and swell. Storms drift and grow. Closed airspace blocks only a band of levels, so flights can go over or under |

`expires_t` is the sim time it ends by itself; `null` means it lasts until it leaves the sector. When a disruption ends, flights that were moved to clear it are planned again without it, and any of them still flying an assigned heading gets a "direct" card.

Random is seeded by the scenario's seed and the number of disruptions so far: the same scenario and the same presses give the same disruptions. A random zone is dropped ahead of the traffic, never on top of an aircraft. A zone placed by hand can land on one: the planner then takes the shortest way out and never goes back in.

## Manual and Auto

`state.auto_speak` (also `state.mode`: `manual` or `auto`) says who issues the instructions. The client flips it with `{"type":"set_auto_speak","enabled":bool}` or `{"type":"set_mode","mode":"manual"|"auto"}`. **Aircraft only move when an instruction is issued**, so in Manual with nobody talking the plan changes and the traffic does not.

In Auto, Tower issues pending cards itself: one voice exchange at a time, the rest by data link when the clock is above 1.5x, more than three cards are waiting, or a card is due before the voice could reach it. Cards for flights that have not entered the sector wait. `ptt_start` on the radio channel makes Tower hold its voice until `ptt_stop`.

- `InstructionCard.via`: `human`, `voice` or `datalink`, set when the card is issued.
- A data link instruction appears as a `transcript` event with `speaker: "datalink"`, no audio, `asr_confidence: 1`, and a `clearance_opened` whose status is already `matched`. Radar verification watches it like any other.
- `{"type":"set_auto_voice","enabled":bool}` and `state.auto_voice`: in Auto, whether Tower also speaks. Off is the default and is instant: everything by data link.
- A data link reroute is the planned path: the transcript line reads "reroute heading 120 for 59 miles then direct PIKAR", and the aircraft flies the plan's own turn point (`PlannedPath.via`), then direct to its exit.
- `InstructionCard.minor`: a shortcut too small to be worth a transmission. Never sent to the screen. In silent Auto it is applied quietly by data link.
- `scoreboard` gained `rerouted`, `reaction_s`, `datalink_sent`, `in_zone_now`, `zone_incursions`.

## Voice on, voice off

`{"type":"set_voice","enabled":bool}` is the one switch (`state.voice`; `state.auto_speak` is its opposite and `set_auto_speak` still works). On sets the clock to 1x. Off sends anything still pending by data link at once.

| Event | Payload | When |
|---|---|---|
| `radio_audio` | `{speaker: "pilot"\|"controller", callsign, audio_ref, duration_s}` | a clip is on the air, sent before it is transcribed. Play `GET /audio/<audio_ref>`, one at a time, in order. The human's own mic recording is not sent back |
| `said_check` | `{clearance_id, card_id, callsign, heard, expected, detail}` | what the controller said conflicts with the card. Nothing went to the pilot. The card carries `heard_instead` until it is resolved |
| `alert_resolved` | `{clearance_id, callsign, by: "correction", seconds}` | the correction was read back right: close that alert |

| Client message | Effect |
|---|---|
| `{"type":"confirm_heard","clearance_id"}` | the controller meant what Tower heard: issue it as heard. The card stays open |
| `{"type":"set_next_readback","mode"}` | `random`, `correct`, `wrong_value`, `wrong_aircraft`, `omitted_item`, `missing_readback`. One shot, reported in `state.next_readback` |

**The screen drops unknown event types.** `frontend/lib/ws.ts` has a whitelist. Add new events there, in `EventMap`, and in the reducer.

