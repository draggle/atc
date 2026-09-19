# Tower frontend

Next.js 15 (App Router, TypeScript, Tailwind 4) live screen. One page: radar, instruction cards, alert with agent trace, transcript, scoreboard, sliders, push-to-talk.

## Run

```bash
cd frontend
npm install
npm run dev          # http://localhost:3000
npm run build        # must pass with zero type errors
```

- `http://localhost:3000` connects to the backend WebSocket. If it cannot connect within 1.5 s, it falls back to the scripted mock (`lib/mock.ts`) and shows a MOCK badge.
- `http://localhost:3000/?mock=1` forces mock mode.

Environment (copy `.env.example` to `.env.local`):

| Variable | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_TOWER_WS` | `ws://localhost:8000/ws` | WebSocket endpoint |
| `NEXT_PUBLIC_TOWER_HTTP` | `http://localhost:8000` | Base for `/audio/<audio_ref>` clips |

## Layout

```
app/page.tsx              entry (Suspense wrapper for useSearchParams)
components/TowerApp.tsx   store + WebSocket client providers, grid layout
components/TopBar.tsx     scenario, sim clock, Tower/Auto-speak toggles, Today/Tower plan, savings, connection badge
components/Radar.tsx      Canvas 2D radar, click-to-drop intruder/storm
components/InstructionCards.tsx  cards (4 visible, rest counted), Say it button
components/AlertCard.tsx  alert (red mismatch/missing, amber ambiguous/partial), agent trace, CHECKING card while resolver runs
components/Transcript.tsx newest at bottom, confidence bar, stock vs tuned toggle
components/ScoreboardPanel.tsx
components/SlidersPanel.tsx  buffer NM / pilot error rate / noise, debounced set_sliders
components/PushToTalk.tsx    hold Space = radio, Shift+Space = headset; agent chat; typed radio fallback
lib/types.ts              TypeScript mirror of backend/schemas.py
lib/store.tsx             React context + reducer
lib/ws.ts                 WebSocket client with mock fallback
lib/mock.ts               scripted mock backend
lib/audio.ts              mic -> PCM16 mono 16 kHz frames
lib/geo.ts                NM <-> px projection
```

## Protocol

### Server -> client

JSON `{type, payload, t}` where `payload` is the matching model from `backend/schemas.py`:

| type | payload |
|---|---|
| `state` | `{scenario, tower_enabled, auto_speak, t, waypoints: Waypoint[], zones: Zone[], sector_nm}` |
| `radar` | `AircraftState[]` (also accepts `{aircraft: AircraftState[], t}`) |
| `plan` | `Plan`; optional `baseline_paths: PlannedPath[]` draws the "Today" view |
| `plan_update` | `Plan` whose `paths` are the changed flights; optional `changed: string[]`; changed callsigns flash 4 s |
| `instruction_card` | `InstructionCard`; same `id` again updates status |
| `transcript` | `Transmission` |
| `clearance_opened` / `clearance_updated` | `OpenClearance` |
| `alert` | `Verdict` + `audio_ref` (+ optional `callsign`). `result: "match"` is never shown |
| `resolver_step` | `ResolverStep`; shows a CHECKING card until the `alert` for that `clearance_id` arrives |
| `disruption` | `Disruption` |
| `scoreboard` | `Scoreboard` |
| `stats` | free-form |
| `agent_reply` | `{text, actions: string[]}` |

### Client -> server

JSON:

```
{type:"ptt_start", channel:"radio"|"agent"}
{type:"ptt_stop"}
{type:"agent_text", text}
{type:"radio_text", text}
{type:"load_scenario", name}
{type:"set_tower", enabled}
{type:"set_auto_speak", enabled}
{type:"add_disruption", kind:"intruder"|"storm", x_nm, y_nm}
{type:"speak_card", id}
{type:"set_sliders", buffer_nm, error_rate, noise}
```

Binary: raw PCM16 mono 16 kHz frames while push-to-talk is held. `ptt_start` is sent before the first frame and `ptt_stop` after the last.

Coordinates: `x_nm` east, `y_nm` north, origin at the bottom-left of the `sector_nm` square.
