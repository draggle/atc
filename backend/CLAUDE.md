# backend/

FastAPI service. Audio in, WebSocket events out. Full design is in `../docs/03-architecture.md`. Domain rules are in `../docs/02-domain.md`.

## Contract

- Implement the Pydantic models in `03-architecture.md` exactly. They are shared with `training/` and `frontend/`.
- Emit the WebSocket event types listed there. The frontend builds against them.
- Audio is 16 kHz mono.

## Rules

- Tier 1 is a fixed pipeline with single structured-output LLM calls. No loops. Target under 2 s per transmission.
- The normalizer is pure Python and deterministic. Give it unit tests, since it is where most domain bugs will live.
- The resolver agent is a hand-rolled tool-calling loop on the OpenAI SDK with Baseten's base URL. Max 4 tool calls, about 5 s, and it always ends in alert, dismiss, or uncertain.
- Log every resolver step with its evidence and emit it as a `resolver_step` event.
- Retry Baseten 429s with exponential backoff.
- Read all keys and model slugs from environment variables. See `../.env.example`.

## Suggested layout

```
backend/
  app.py            FastAPI app and WebSocket endpoint
  audio/            ingest, VAD, radio filter
  asr.py            Baseten Whisper client, stock and tuned
  normalize.py      phraseology to digits and ICAO codes
  extract.py        clearance and readback extractors
  state.py          per-callsign open clearances, timeouts, events
  check.py          rule checker plus checker model client
  resolver/         agent loop and tools
  schemas.py        the Pydantic contract
  tests/
```
