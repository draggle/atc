# 03. Architecture

Two tiers. Tier 1 is a fast fixed pipeline that handles most traffic. Tier 2 is a tool-calling agent that wakes only for ambiguous cases.

```
mic / file
   |
   v
VAD + chunker ----> utterance clips, 0.5 to 10 s, 16 kHz mono
   |
   v
ASR: fine-tuned Whisper on Baseten ----> text + confidence
   |
   v
normalizer, deterministic ----> digits, ICAO callsigns, tagged units
   |
   v
turn classifier ----> controller | pilot | unknown
   |
   +-- controller --> clearance extractor (LLM, JSON) --> open clearance in state store
   |
   +-- pilot -------> readback extractor (LLM, JSON) --> checker --> match | mismatch | partial | ambiguous
                                                                                               |
                                                                                   resolver agent (tier 2)
                                                                                               |
                                                                                 alert | dismiss | uncertain
   |
   v
WebSocket events ----> live screen
```

## Tier 1 components

| Component | What it does | Notes |
|---|---|---|
| Audio ingest | Browser mic or uploaded clip over WebSocket to the backend | For the stage demo, apply a bandpass filter and light static to the pilot mic so it sounds like radio |
| VAD | Silero VAD splits the stream into utterances and drops silence | Whisper hallucinates on sub-second clips. Drop or pad anything under about 0.5 s |
| ASR | Our fine-tuned Whisper, served on Baseten | Return text plus average log-probability as confidence. Keep stock Whisper deployed for the side-by-side toggle |
| Normalizer | Pure Python, no LLM | Rules in `02-domain.md`. Must be deterministic so the checker compares like with like |
| Turn classifier | Who is speaking | Heuristics first: controller leads with the callsign then an instruction, pilot ends with the callsign. In the demo, two mics is an acceptable shortcut |
| Extractors | One structured-output LLM call each | Few-shot with about a dozen real phraseology examples |
| State store | In-memory dict keyed by callsign | Redis only if we need it |
| Checker | Rules first, then our fine-tuned small model for fuzzy cases | Every mandatory item must appear with the same value |

**Latency budget:** under 2 seconds from end of transmission to a tier 1 verdict.

## Data contracts

These Pydantic models are the contract between teammates. Change this doc and the code together.

```python
class Transmission(BaseModel):
    id: str
    t_start: float            # seconds since session start
    t_end: float
    audio_ref: str            # path or key for the clip
    text_raw: str             # ASR output
    text_norm: str            # after normalizer
    asr_confidence: float     # 0 to 1
    speaker: Literal["controller", "pilot", "unknown"]

class Item(BaseModel):
    type: Literal["altitude", "heading", "speed", "frequency", "squawk",
                  "altimeter", "runway", "route", "hold_short", "other"]
    value: str | float | int  # 240, 270, 124.65, "24L", "BOSOX"
    unit: Literal["FL", "ft", "deg", "kt", "MHz", "hPa", "inHg", None] = None
    action: str | None = None # "descend", "climb", "turn_left", "contact", "cleared_land"
    mandatory: bool = True    # must it be read back

class Extraction(BaseModel):
    transmission_id: str
    callsign: str | None      # ICAO form, for example "ACA123"
    items: list[Item]

class OpenClearance(BaseModel):
    id: str
    callsign: str
    items: list[Item]
    issued_at: float
    timeout_s: float = 15.0   # tune this
    status: Literal["open", "matched", "mismatched", "partial", "missing", "uncertain"]

class Verdict(BaseModel):
    clearance_id: str
    readback_transmission_id: str | None
    result: Literal["match", "mismatch", "partial", "missing", "ambiguous"]
    error_type: str | None    # from the taxonomy in 02-domain.md
    expected: list[Item]
    heard: list[Item]
    confidence: float
    reason: str               # one line, human readable
    decided_by: Literal["rules", "checker_model", "resolver"]
```

## State machine events

`clearance_issued`, `readback_received`, `matched`, `mismatched`, `partial`, `missing` after timeout, and `similar_callsign_warning` when two active callsigns differ by one character.

## Tier 2: the resolver agent

**Triggers**

- Low ASR confidence on a mandatory value
- Readback matches a different aircraft's open clearance
- Two similar callsigns active on frequency
- Partial readback of a multi-part clearance
- Checker model confidence in the uncertain middle

**Tools**

| Tool | Returns |
|---|---|
| `relisten(transmission_id)` | Re-transcription with the larger Whisper, plus top alternative hypotheses with scores |
| `frequency_history(callsign, n)` | The last n exchanges for that callsign |
| `active_aircraft()` | Every callsign currently on frequency with its open clearances |
| `sanity_check(item)` | Whether an altitude, heading, runway, frequency, or waypoint is plausible here |
| `raise_alert(verdict)` | Terminal action |
| `dismiss(reason)` | Terminal action |
| `mark_uncertain(reason)` | Terminal action. A human should look |

**Rules**

- At most 4 tool calls and about 5 seconds. An alert that arrives 20 seconds late is worthless.
- Out of budget means `mark_uncertain`, never silence.
- Log every step with its evidence. That log is shown on screen in the demo and is our audit trail.
- Save confident resolver verdicts as new training pairs for the checker. This is the loop we show Baseten.

**Harness**

A hand-rolled tool-calling loop on the OpenAI SDK pointed at Baseten. About a hundred lines. No LangGraph, CrewAI, or similar. Check the live model catalog before choosing a model, because tool calling and structured outputs vary by model.

- Extractors: a small, fast hosted model.
- Resolver: a larger reasoning model.

## WebSocket events to the frontend

One JSON object per message, each with a `type` field.

| type | Payload |
|---|---|
| `transcript` | `Transmission`, plus `text_stock` when the stock toggle is on |
| `clearance_opened` | `OpenClearance` |
| `clearance_updated` | `OpenClearance` with new status |
| `alert` | `Verdict` plus `audio_ref` |
| `resolver_step` | `{clearance_id, step, tool, args, result_summary}` |
| `stats` | rolling latency, counts of matches and alerts |

## Actions on alert

- Banner with expected versus heard, confidence, and a play button for the clip
- A suggested correction phrase for the controller
- Optional: speak the correction through ElevenLabs
- Everything appended to a scrollable timeline

## Configuration

Environment variables, see `.env.example`.

| Variable | Purpose |
|---|---|
| `BASETEN_API_KEY` | All inference |
| `ASR_MODEL_URL` | Endpoint for our fine-tuned Whisper |
| `ASR_STOCK_MODEL_URL` | Endpoint for stock Whisper, for the toggle |
| `EXTRACTOR_MODEL` | Model slug for the extractors |
| `RESOLVER_MODEL` | Model slug for the resolver |
| `CHECKER_MODEL_URL` | Endpoint for our fine-tuned checker |
| `ELEVENLABS_API_KEY` | Optional |

## What to build for real and what to shortcut

- **Real:** ASR, normalizer, extractors, checker, state store, resolver, screen. Nothing in the audio path is faked. The fine-tuned model must genuinely beat stock on the same input in front of judges.
- **Acceptable shortcuts:** two mics instead of a speaker classifier on stage, and held-out dataset clips instead of a live feed.
