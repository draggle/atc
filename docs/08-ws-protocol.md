# 08. WebSocket protocol between the screen and the backend

Added Saturday night Sept 19 during the overnight build. Extends the event list in `03-architecture.md` and `07-build-spec.md` section 3. Payload shapes are the Pydantic models in `backend/schemas.py`. The frontend mirrors them in `frontend/lib/types.ts`.

## Endpoint

`ws://localhost:8000/ws`. One connection per screen. Audio clips referenced by `audio_ref` are served at `http://localhost:8000/audio/<ref>`.

## Server to client

Every message is one JSON object `{"type": ..., "payload": {...}, "t": <sim seconds>}`.

| type | payload | when |
|---|---|---|
| `state` | `{scenario, lifecycle, speed, world_id, scenarios: ScenarioInfo[], tower_enabled, auto_speak, t, waypoints: Waypoint[], zones: Zone[], sector_nm, buffer_nm, error_rate, noise, watching}` | on connect, on every lifecycle change, and whenever a setting changes |
| `radar` | `{aircraft: AircraftState[]}` | once per second |
| `plan` | `Plan` | after initial planning and every replan |
| `plan_update` | `{changed: string[], reason, trigger}` | with every replan |
| `instruction_card` | `InstructionCard` | when created or when its status changes |
| `transcript` | `Transmission` | after every utterance is transcribed |
| `clearance_opened` | `OpenClearance` | controller transmission with mandatory items |
| `clearance_updated` | `OpenClearance` | status change |
| `alert` | `Verdict` plus `audio_ref` | mismatch, missing, or resolver alert or uncertain. Never for match |
| `resolver_step` | `ResolverStep` | each tool call of the resolver |
| `disruption` | `Disruption` | when an intruder or zone is added |
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
| `{"type":"start"}` | `ready` or `paused` to `running` |
| `{"type":"pause"}` | `running` to `paused` |
| `{"type":"reset"}` | back to the world as it was loaded: clock at zero, nothing issued. New `world_id` |
| `{"type":"set_speed","speed"}` | sim seconds per real second, clamped to 0.25 to 120. The screen offers 1, 5, 20, 60 |
| `{"type":"set_tower","enabled"}` | Tower on or off. Off means readbacks are not checked and the plane flies what the pilot said |
| `{"type":"set_auto_speak","enabled"}` | the agent speaks instruction cards itself |
| `{"type":"add_disruption","kind":"intruder"\|"storm","x_nm","y_nm"}` | drop an intruder or storm at a point |
| `{"type":"speak_card","id"}` | speak one card by TTS now |
| `{"type":"set_sliders","buffer_nm","error_rate","noise"}` | separation buffer, pilot error rate, radio noise |

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
