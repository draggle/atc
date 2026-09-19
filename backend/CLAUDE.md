# backend/

One FastAPI process holding five services: simulator, planner, AI pilots, Tower core, and a WebSocket hub. The design is in `../docs/07-build-spec.md`. Schemas and base events are in `../docs/03-architecture.md`. Domain rules are in `../docs/02-domain.md`.

## Contract

- Implement the Pydantic models exactly: `Transmission`, `Item`, `Extraction`, `OpenClearance`, `Verdict` from `03`, and `AircraftState` plus the `Sim` protocol from `07`.
- Emit the WebSocket events listed in both files. The frontend builds against them.
- Audio is 16 kHz mono. AI pilot speech goes through the radio effect and into the same ingest path as the mic. Tower must hear the pilots, never read their text.

## Rules

- **The plane obeys the pilot's readback, not the controller's clearance.**
- The planner is search and geometry only. It never plans below 5 NM and 1,000 ft. The slider changes only the extra buffer.
- Replanning freezes the next 60 to 90 seconds of every path and disturbs as few flights as possible.
- Tier 1 is a fixed pipeline. Grammar parser first, one structured-output LLM call only as fallback. Target under 2 s per transmission.
- The normalizer is pure Python and deterministic, with unit tests.
- Snap callsigns to the simulator's active list, and pass that list to Whisper as a prompt.
- Never alert on a mismatch if the expected value appears in any of the top 5 speech hypotheses. Send it to the resolver.
- The resolver is a hand-rolled tool-calling loop on the OpenAI SDK with Baseten's base URL. Max 4 tool calls, and it always ends in alert, dismiss, or uncertain. Emit every step as a `resolver_step` event.
- Log ground truth for every simulated exchange: true clearance, spoken text, injected error, audio file. That log is training data.
- Seed every scenario so runs are repeatable.
- Retry Baseten 429s with exponential backoff. Read keys and model slugs from the environment.

## Suggested layout

```
backend/
  app.py            FastAPI app and WebSocket hub
  schemas.py        the Pydantic contract
  sim/              simulator, scenarios, optional BlueSky adapter
  planner/          trajectories, conflict test, plan, replan, emergency layer, cards
  pilots/           readback templates, error injection, TTS, radio effect
  tower/
    audio.py        ingest, VAD
    asr.py          Baseten Whisper clients, stock and tuned, n-best
    normalize.py    phraseology to digits and ICAO codes
    callsign.py     snapping to the active list
    parse.py        grammar parser and LLM fallback
    state.py        six-state machine, timeouts
    check.py        rules, n-best rule, cross-encoder client
    conform.py      radar verification
    resolver/       agent loop and tools
  eval/             batch and Monte Carlo runs, metrics
  tests/
```
