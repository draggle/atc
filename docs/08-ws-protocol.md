# 08. WebSocket protocol between the screen and the backend

Added Saturday night Sept 19 during the overnight build. Extends the event list in `03-architecture.md` and `07-build-spec.md` section 3. Payload shapes are the Pydantic models in `backend/schemas.py`. The frontend mirrors them in `frontend/lib/types.ts`.

## Endpoint

`ws://localhost:8000/ws`. One connection per screen. Audio clips referenced by `audio_ref` are served at `http://localhost:8000/audio/<ref>`.

## Server to client

Every message is one JSON object `{"type": ..., "payload": {...}, "t": <sim seconds>}`.

| type | payload | when |
|---|---|---|
| `state` | `{scenario, tower_enabled, auto_speak, t, waypoints: Waypoint[], zones: Zone[], sector_nm}` | on connect and whenever a setting changes |
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

## Client to server, JSON

| message | effect |
|---|---|
| `{"type":"ptt_start","channel":"radio"\|"agent"}` | start of push-to-talk. Binary PCM frames follow |
| `{"type":"ptt_stop"}` | end of push-to-talk. The utterance is transcribed and routed to the channel |
| `{"type":"agent_text","text"}` | typed request to the world-builder agent |
| `{"type":"radio_text","text"}` | typed controller transmission, fallback when there is no mic |
| `{"type":"load_scenario","name"}` | load a scenario by name |
| `{"type":"set_tower","enabled"}` | Tower on or off. Off means readbacks are not checked and the plane flies what the pilot said |
| `{"type":"set_auto_speak","enabled"}` | the agent speaks instruction cards itself |
| `{"type":"add_disruption","kind":"intruder"\|"storm","x_nm","y_nm"}` | drop an intruder or storm at a point |
| `{"type":"speak_card","id"}` | speak one card by TTS now |
| `{"type":"set_sliders","buffer_nm","error_rate","noise"}` | separation buffer, pilot error rate, radio noise |

## Client to server, binary

Raw PCM16 mono 16 kHz frames between `ptt_start` and `ptt_stop`.

## Channels

- **radio**: the utterance goes through the full Tower pipeline as a controller transmission. AI pilots hear the parsed clearance and reply by voice into the same pipeline.
- **agent**: the utterance is transcribed with stock speech recognition and handed to the world-builder agent, which can only change the world, never issue a clearance.
