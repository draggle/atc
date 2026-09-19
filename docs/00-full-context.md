# Tower: full context

Everything we know and have decided about the project in one file. Snapshot written Saturday, Sept 19, 2026, during Hack the North.

**How to use this file.** Read it once to get up to speed, or paste it into an assistant that has no access to the repo. For day-to-day work, the numbered docs `01` to `06` are the source of truth. Parts 3 to 8 below are copies of them as of this snapshot. Parts 1, 2, and 9 to 12 contain material that is not in the other docs yet.

## Contents

1. The idea in one page
2. How it works, step by step
3. Project, prizes, and demo (copy of `01-project.md`)
4. Domain: how ATC radio works (copy of `02-domain.md`)
5. Architecture (copy of `03-architecture.md`)
6. Training, evaluation, and Baseten (copy of `04-training.md`)
7. Data sources and legal constraints (copy of `05-data-and-legal.md`)
8. Plan (copy of `06-plan.md`)
9. Background: how aircraft routes work
10. Ideas discussed since the docs were written
11. How we got here
12. Open decisions

---

# Part 1. The idea in one page

**Tower is an AI second set of ears on an air traffic control frequency.**

When a controller gives an instruction such as "descend to flight level 240," the pilot must repeat it back. The controller is supposed to catch it if the pilot repeats it wrong, for example "210" instead of "240." Busy controllers sometimes miss this, and it has caused real incidents. Tower listens to the radio, transcribes both sides, tracks every instruction, compares each readback against it, and raises an alert when they do not match or when no readback arrives.

**What we build**

- **A speech model that understands radio.** Stock Whisper gets most ATC audio wrong. We fine-tune it on about 20,000 free, labeled ATC clips using Baseten's H100s. Published results show error rates dropping from above 60 percent to around 15 to 20 percent.
- **A readback checker.** A small text model trained on synthetic examples of correct and wrong readbacks. It knows "down to two four zero" matches "descend flight level 240" and that "two one zero" does not.
- **The agent in between.** It splits audio into transmissions, works out who is speaking, pulls out callsigns, altitudes, headings, and frequencies, tracks every open instruction per aircraft, and decides when to alert.
- **A live screen.** Running transcript, open instructions, an alert feed showing expected versus heard with the audio clip, and a toggle comparing stock Whisper with ours.

**The demo.** A judge plays the pilot through a radio-filtered mic and deliberately reads back the wrong altitude. The alert fires in front of them. Then the same clip goes through stock Whisper, which produces nonsense.

**Why it fits our targets**

- **Rox, Best AI Agent:** static, accents, overlapping speakers, similar callsigns, and paraphrased readbacks. The agent decides under uncertainty and acts.
- **Baseten:** two models trained on their hardware and served through their API, with a clear before and after.
- **Hack the North finalist:** original, technically deep, and the judge takes part in the demo.

**Mental model:** Whisper hears, the notepad remembers, and the agent handles the cases too messy for simple rules.

---

# Part 2. How it works, step by step

Think of Tower as a sharp intern sitting beside the controller with a notepad. The intern writes down every instruction, listens for the pilot's reply, ticks it off if it matches, and taps the controller on the shoulder if it does not. The project is that intern, built from three parts: ears, a notepad, and judgment.

## An easy case, where no agent is needed

```
Controller: "Air Canada 123, descend and maintain flight level two four zero."
Pilot:      "Descend flight level two four zero, Air Canada 123."
```

1. **Ears.** Our fine-tuned Whisper turns each transmission into text.
2. **Clean-up.** Code converts "two four zero" to 240 and "Air Canada 123" to ACA123.
3. **Understanding.** One quick language model call turns the controller's sentence into structured data: aircraft ACA123, descend, flight level 240.
4. **Notepad.** The system records an open instruction for ACA123 and starts a timer.
5. **Check.** The pilot's reply is processed the same way and compared. Same aircraft, same altitude. The item is ticked off and nothing else happens.

If the pilot had clearly said "two one zero," step 5 fires an alert straight away. Most traffic is handled like this, fast and mechanical.

## A hard case, where the agent wakes up

```
Controller: "Air Canada 123, descend flight level two four zero."
Pilot:      "Descend two [static] zero, Air Canada one... three."
```

The ears are not sure. It might be 240 or 210, and the callsign is garbled. A simple script would either false-alarm or stay silent. The agent is a language model that chooses its own next steps from a small set of tools:

1. It **re-listens** to that clip with the bigger speech model and gets two guesses: 240 at 55 percent, 210 at 40 percent.
2. It **checks who else is on frequency.** There is also an Air Canada 133. Was this reply even from the right aircraft?
3. It **looks at recent history.** Air Canada 133 was just told to climb, so a "descend" reply fits 123, not 133.
4. It **decides.** The speaker is probably the right aircraft, but the altitude is genuinely uncertain and altitude is critical. It raises a caution: "Readback unclear for ACA123, expected 240, possibly heard 210," with the audio clip attached.

Nobody scripted that order of steps. The agent picked them based on what it found. That is the difference between an agent and a pipeline, and it is what Rox means by handling messy data and deciding under uncertainty.

## What it can do at the end

Exactly one of three actions. It can **alert**, showing what was expected and what was heard. It can **dismiss**, with a logged reason. Or it can mark the item **uncertain** so a human looks at it. It also alerts when an instruction's timer runs out with no readback at all.

## Why two tiers and no agent framework

Tower is a real-time stream. Chat-style agent loops are too slow and unpredictable for the hot path, so most traffic goes through a fixed pipeline with single structured-output model calls, targeting under 2 seconds per transmission. The tool-calling agent only runs on ambiguous cases, capped at about 4 tool calls and 5 seconds, because an alert that arrives 20 seconds late is worthless.

Harness decision: a hand-rolled tool-calling loop on the OpenAI SDK pointed at Baseten's API, about a hundred lines.

| Option | Verdict |
|---|---|
| Hand-rolled loop on the OpenAI SDK with Baseten's base URL | Use this. Full control, nothing to debug but our own code |
| Pydantic AI | Fine if someone already knows it. Typed outputs suit the extractors |
| Pipecat | Real-time voice framework with VAD built in. Only if a teammate has used it, since it assumes a bot that talks back and Tower mostly listens |
| LangGraph, CrewAI, JiuwenSwarm | Skip. They add latency and a learning curve and solve problems we do not have |

A small fast hosted model handles extraction. A larger reasoning model handles the resolver. Check Baseten's live model catalog first, because tool calling and structured output support varies by model.

---

# Part 3. Project: what Tower is and what we are trying to win

### One line

A second set of ears on the frequency that never misses a readback.

### The problem

1. A controller issues a clearance: "Air Canada 123, descend and maintain flight level two four zero."
2. The pilot must read back the safety-critical parts: "Descend flight level two four zero, Air Canada 123."
3. The controller listens for a mismatch. This is called hearback.
4. Sometimes the controller misses it. They are working ten aircraft, the readback was clipped, or an accent made "two four" sound like "two one." The pilot then flies the wrong altitude. That is a hearback error, and it is a known cause of incidents.

Research puts readback errors at roughly 1 to 2 percent of transmissions. More background is in `02-domain.md`.

### What Tower does

- Transcribes both sides of the radio with a Whisper model fine-tuned on ATC audio.
- Turns each controller transmission into a structured clearance and opens it on a per-aircraft notepad.
- Turns each pilot transmission into a structured readback and compares it.
- Alerts on a mismatch, showing expected value, heard value, confidence, and the audio clip.
- Alerts when a clearance gets no readback before a timeout.
- Hands ambiguous cases to a resolver agent that gathers more evidence before deciding.

### What Tower is not

- Not an operational tool, not certified, not connected to any real ATC system.
- Not listening to live real-world radio. See `05-data-and-legal.md`.
- Not a claim that any specific accident would have been prevented.

### What we are targeting

| Target | Winners | What they want | How we show it |
|---|---|---|---|
| Rox: Best AI Agent | 2, $10K and $2K | LLM agents operating on messy, noisy, incomplete, conflicting real-world data and taking meaningful actions. Judged on technical complexity, creativity, handling messiness, practical utility. | Radio static, accents, clipped transmissions, two speakers on one channel, similar callsigns, paraphrased readbacks. The resolver agent visibly gathers evidence and commits to an action with a reason. |
| Baseten: Best Use of Baseten | 2, grand prize is an SF trip plus final-round interviews | Creative, real use of their platform. Inference APIs, H100 training, or Baseten Switch. | Two models trained on their H100s and served through their API. Before and after chart. Latency and cost of small tuned models versus a large prompted one. |
| Hack the North finalist | 12, no ranking | WOW factor, technical ability, originality, design. | A judge plays the pilot and gets caught live. |

Possible extras if they fall out naturally. Do not contort the project for them.

- **MLH Best Use of ElevenLabs:** synthetic pilot voices for training augmentation, or a spoken correction phrase on alert.
- **Sentry:** tracing plus AI agent monitoring across the pipeline. Needs at least two products beyond error monitoring.

**Deadline that matters:** every sponsor prize we want must be selected on Devpost before 2:00 PM EDT Saturday.

### Official Hack the North judging criteria

Criteria: WOW factor, technical ability, originality, design meaning a user-friendly and intuitive experience.

Explicitly not criteria: practicality and entrepreneurship, visual appeal on its own.

The judging pitch must be a live demo, not slides or a product pitch.

### Lessons from past winners

- Of the twelve 2025 finalists, only one also won a sponsor prize. Winning both takes deliberate design.
- Finalist hooks fit in one line and usually involve the judge or an object on the table.
- Lavoe won Rox's $10K in 2025 with one LLM call and four tools driving a visible UI. Judges score the demo, not the codebase. The agent acting on a real, visible surface is what sells.

### Demo script, about three minutes

1. **Hook, 15 seconds.** "Pilots repeat every instruction back. Controllers are supposed to catch mistakes. Sometimes they do not. Tower always does."
2. **Stock versus ours, 30 seconds.** Play one real ATC test clip. Show stock Whisper's transcript next to our fine-tuned one.
3. **Judge plays the pilot, 60 seconds.** Hand them the mic with the radio filter and a card: "Read this back, but say the wrong altitude." The alert fires with expected, heard, and the clip.
4. **Hard case, 45 seconds.** A garbled readback with a similar callsign on frequency. Show the resolver's steps on screen: re-listen, check active aircraft, check history, decide.
5. **Numbers, 20 seconds.** Word error rate before and after, checker accuracy and false alarm rate, end-to-end latency. All measured by us.
6. **Close, 10 seconds.** What we trained this weekend, on what, and what is next.

Always have a backup video recorded Sunday morning.

### Questions judges will ask, and our answers

- **Is this not already done?** It is an active research area, not a product in towers. The European HAAWAII project reached over 80 percent detection with under 20 percent false alarms in lab tests. We built a live end-to-end version in a weekend with open models we fine-tuned ourselves.
- **What about false alarms?** That is the real problem, and it is why the resolver agent exists. Show the confidence scores and the measured false alarm rate.
- **Where did the data come from?** Public research datasets on Hugging Face, plus synthetic readback pairs we generated. Real errors are too rare to collect, and the leading research project also trained on synthetic errors.
- **Why not live radio?** LiveATC's terms forbid it, and Canadian law restricts using intercepted radio. We designed the demo around a human pilot instead.
- **What did you train versus take off the shelf?** Be precise. Name the base models, the datasets, the hours of training, and what is ours.
- **What did each of you build?** Everyone demos a piece.

### Submission checklist

- [ ] Sponsor prizes selected on Devpost before 2:00 PM EDT Saturday
- [ ] Link to this repo, including design assets
- [ ] Badge ID of every team member, exactly as printed under the QR code
- [ ] Demo video, optional but recommended
- [ ] README lists every third-party model, dataset, and library

---

# Part 4. Domain: how ATC radio works

Read this before writing prompts, the normalizer, the extractor, or the checker. Most bugs in this project will be domain bugs.

### The radio

- Every aircraft in a sector shares one frequency with the controller.
- Only one party can transmit at a time. Two simultaneous transmissions block each other. Pilots call this being **stepped on**.
- While your own mic is keyed you hear nothing.
- Audio is narrow-band AM with static, clipping, and cockpit noise. Speech is fast and clipped.

### The safety loop

1. **Clearance.** Controller issues an instruction, starting with the aircraft's callsign.
2. **Readback.** Pilot repeats the safety-critical parts, usually ending with their callsign.
3. **Hearback.** Controller listens and corrects any mismatch.

Tower automates step 3.

### What must be read back

International rules require a readback of:

- ATC route clearances
- Any clearance or instruction to enter, land on, take off from, hold short of, cross, or backtrack on a runway
- Runway in use
- Altimeter settings
- Transponder codes, called squawk codes
- Level instructions: altitudes and flight levels
- Heading and speed instructions
- Transition levels

This list is the checker's spec. Items outside it, such as traffic information or weather, can be acknowledged with "roger" and should not raise alerts.

### What counts as a match

Same meaning, not same words.

| Controller | Pilot | Verdict |
|---|---|---|
| descend and maintain flight level two four zero | down to flight level two four zero | Match |
| descend flight level two four zero | descend flight level two one zero | Mismatch: wrong value |
| turn left heading two seven zero, descend four thousand | left two seven zero | Partial: altitude omitted |
| climb flight level three one zero | descend three one zero | Mismatch: wrong direction |
| contact departure one two four decimal six five | one two four six five, good day | Match |
| hold short runway two four left | roger | Mismatch: mandatory item needs a full readback |
| Air Canada 123, descend... | reply from Air Canada 133 | Mismatch: wrong aircraft took the clearance |

### Our error taxonomy

Used for generating synthetic checker data and for reporting accuracy per type.

1. Wrong value: altitude, heading, speed, frequency, squawk, or altimeter
2. Wrong runway, including left versus right
3. Wrong direction: climb versus descend, left versus right turn
4. Wrong unit or type: flight level versus feet, heading digits read back as a speed
5. Omitted mandatory item in a multi-part clearance
6. Acknowledgement only, such as "roger" or "wilco," where a full readback is required
7. Wrong aircraft: another callsign reads back the clearance
8. Missing readback: nothing before the timeout

### Why errors happen

Similar callsigns on one frequency, accents, fast speech, non-standard phrasing, frequency congestion, high workload, and expectation bias, where people hear what they expected to hear.

### Phraseology the normalizer must handle

- **Phonetic alphabet:** alfa, bravo, charlie, delta, echo, foxtrot, golf, hotel, india, juliett, kilo, lima, mike, november, oscar, papa, quebec, romeo, sierra, tango, uniform, victor, whiskey, xray, yankee, zulu. Datasets spell some of these inconsistently, for example `alpha` and `juliet`.
- **Digits:** spoken one at a time. `niner` is 9, `tree` is 3, `fife` is 5.
- **Decimals:** `decimal` or `point` in frequencies. "one two four decimal six five" is 124.65.
- **Flight level:** altitude in hundreds of feet. Flight level 240 is 24,000 feet.
- **Thousands and hundreds:** "four thousand five hundred" is 4500 feet. "one one thousand" is 11,000.
- **Runways:** two digits plus optional left, right, or centre. "two four left" is 24L.
- **Callsigns:** an airline telephony name plus digits, or a registration spelled phonetically. Pilots often shorten their callsign after first contact.

Starter telephony table. Extend it from whatever shows up in the datasets, which are mostly European.

| Spoken | ICAO | Spoken | ICAO |
|---|---|---|---|
| air canada | ACA | lufthansa | DLH |
| westjet | WJA | speedbird | BAW |
| jazz | JZA | air france | AFR |
| porter | POE | klm | KLM |
| delta | DAL | ryanair | RYR |
| united | UAL | easy | EZY |
| american | AAL | csa | CSA |

- **Waypoints:** five-letter made-up words such as BOSOX. Stock speech models never get these right. The resolver can match a garbled one against a list of real fixes near the airport. FAA NASR data includes fixes. OurAirports has airports, runways, frequencies, and navaids.

### Vocabulary

- **Squawk:** the four-digit transponder code a controller assigns.
- **Hold short:** stop before a runway. Getting this wrong causes runway incursions.
- **Level bust:** deviating more than 300 feet from a cleared altitude.
- **Stepped on:** a transmission blocked by someone else keying up.
- **Direct to:** a shortcut clearance straight to a waypoint further along the route.
- **Wilco:** will comply.

### Real incidents, for context only

Handle these respectfully. Never claim Tower would have prevented them.

- **Tenerife, 1977.** Two 747s collided on a foggy runway and 583 people died. Ambiguous phrasing and a blocked transmission were central. It is why the word "takeoff" is now only spoken in an actual takeoff clearance.
- **Washington DCA, January 29, 2025.** A regional jet and an Army helicopter collided and 67 people died. The NTSB found the helicopter crew may never have heard the words "pass behind the" because their own mic was keyed for about 0.8 seconds. The controller had no way to know. It was one of several factors. It illustrates the class of failure: a message that did not land, with nobody aware.

### Prior work

- **HAAWAII.** European research project with the German aerospace centre DLR, the UK provider NATS, and Iceland's Isavia. Early results were 82 percent detection with a 67 percent false alarm rate. Later lab tests on real recordings reached over 80 percent detection with under 20 percent false alarms. They used both a rule-based detector and a neural one trained on 129,000 synthetic examples, 79,000 of them errors across eight error kinds. This validates our synthetic-data plan, and it tells us false alarms are the hard part.
- **SCOPE, 2026.** Readback monitoring with a speech front end and a lightly trained LLM that compares transcript to instruction and classifies the error.
- **Whisper-ATC, TU Delft.** Fine-tuned Whisper large models set the state of the art on the ATCO2 and ATCOSIM datasets.
- **Synthetic ATC audio, 2026.** Text-to-speech plus noise augmentation improves word error rate when added to training data.

### Sources

- SKYbrary, read-back or hear-back: https://skybrary.aero/articles/read-back-or-hear-back
- ICAO paper on readback and hearback: https://www.icao.int/sites/default/files/APAC/Meetings/2025/2025%20ATMSG13/04-Information%20Papers/IP06%20Importance%20of%20ATC%20Readback%20and%20Hearback%20%20.pdf
- HAAWAII results: https://cordis.europa.eu/article/id/442201-better-automatic-speech-recognition-for-safer-air-traffic-control
- HAAWAII readback paper: https://www.sesarju.eu/sites/default/files/documents/sid/2022/paper_3.pdf
- SCOPE: https://arxiv.org/pdf/2605.29543
- Whisper-ATC: https://github.com/jlvdoorn/WhisperATC
- Synthetic ATC audio: https://arxiv.org/pdf/2606.21340
- NTSB DCA briefing: https://www.ntsb.gov/investigations/Documents/Feb.14.2025_Briefing_Mid-air_Collision%20near%20DCA.pdf

---

# Part 5. Architecture

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

### Tier 1 components

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

### Data contracts

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

### State machine events

`clearance_issued`, `readback_received`, `matched`, `mismatched`, `partial`, `missing` after timeout, and `similar_callsign_warning` when two active callsigns differ by one character.

### Tier 2: the resolver agent

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

### WebSocket events to the frontend

One JSON object per message, each with a `type` field.

| type | Payload |
|---|---|
| `transcript` | `Transmission`, plus `text_stock` when the stock toggle is on |
| `clearance_opened` | `OpenClearance` |
| `clearance_updated` | `OpenClearance` with new status |
| `alert` | `Verdict` plus `audio_ref` |
| `resolver_step` | `{clearance_id, step, tool, args, result_summary}` |
| `stats` | rolling latency, counts of matches and alerts |

### Actions on alert

- Banner with expected versus heard, confidence, and a play button for the clip
- A suggested correction phrase for the controller
- Optional: speak the correction through ElevenLabs
- Everything appended to a scrollable timeline

### Configuration

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

### What to build for real and what to shortcut

- **Real:** ASR, normalizer, extractors, checker, state store, resolver, screen. Nothing in the audio path is faked. The fine-tuned model must genuinely beat stock on the same input in front of judges.
- **Acceptable shortcuts:** two mics instead of a speaker classifier on stage, and held-out dataset clips instead of a live feed.

---

# Part 6. Training, evaluation, and Baseten

We train two models. The before and after on each is the core of the Baseten pitch and a large part of the Rox pitch.

### Model 1: Whisper fine-tuned on ATC audio

#### Why

Stock Whisper fails badly on ATC radio. Published numbers:

| Model | Word error rate on ATC |
|---|---|
| Whisper small, stock | 63% |
| Whisper small, fine-tuned on ATCO2 | 23% |
| Whisper medium.en, stock | 95% |
| Whisper medium.en, fine-tuned on ATCO2 plus UWB-ATCC | 15% |

Part of the stock medium.en number is hallucination on short clips and output format mismatch. We must measure our own numbers with proper normalization. Do not quote these as ours.

#### Datasets

| Dataset | Size | Notes |
|---|---|---|
| `jacktol/atc-dataset` on Hugging Face | 14,795 clips, 11.9k train and 2.93k test, 822 MB | Built from the ATCO2 one-hour test subset and the UWB-ATCC corpus. Card says MIT. Clips are 0.3 to 15 s. Transcripts are lowercase with numbers spelled out |
| `jlvdoorn/atco2-asr-atcosim` on Hugging Face | 8,092 train and 2,026 validation | ATCO2 plus ATCOSIM, used by the Whisper-ATC work |

Check the underlying corpus licenses before using anything beyond this hackathon. They are research datasets.

**Do not add LiveATC audio.** See `05-data-and-legal.md`.

#### Recipe, from the published medium.en fine-tune

- 16 kHz resampling
- 10 epochs with early stopping, patience 3, on validation word error rate
- Batch size 16 with gradient accumulation 2, so effective 32
- Learning rate 1e-5 with 500 warm-up steps
- Dynamic augmentation: Gaussian noise, pitch shift, time stretch, clipping distortion, with intensity decaying over training
- That run used two A100 80 GB cards

Reference code: https://github.com/jack-tol/fine-tuning-whisper-on-atc-data and https://github.com/jlvdoorn/WhisperATC

Our estimate, unverified: on one H100, Whisper small finishes in under an hour and medium.en in one to three hours. Start with small to prove the pipeline end to end, then launch medium.en.

#### Known gotchas

- About 40 samples in the dataset have wrong ground truth. The published work removed them by hand. Spot-check for obviously broken labels.
- Stock Whisper hallucinates on very short or unclear clips, for example repeating one word. Use VAD and a minimum clip length.
- Normalize both reference and hypothesis identically before computing word error rate. Numerals versus spelled numbers and `alfa` versus `alpha` will otherwise inflate the error.
- Keep a held-out test split that no training run ever sees. All reported numbers come from it.

#### Stretch: synthetic audio

Research shows text-to-speech ATC audio with noise augmentation improves word error rate. Generate pilot and controller lines with ElevenLabs in varied voices, add radio static and band-limiting, and mix into training. This also qualifies us for the ElevenLabs track.

#### Fallback

`jacktol/whisper-medium.en-fine-tuned-for-ATC` on Hugging Face is a published fine-tune. If our training fails, we can serve it, and we must say so plainly.

### Model 2: the readback checker

#### Why

Real readback errors are 1 to 2 percent of traffic, far too rare to collect. The leading research project trained its detector on 129,000 synthetic examples for the same reason.

#### Data generation

1. Sample realistic clearances. Use the extractor on dataset transcripts to get real ones, and template more.
2. For each clearance, generate a correct readback in several paraphrases.
3. Inject errors from the taxonomy in `02-domain.md`, one type per example, with the type as the label.
4. Add ASR-style noise to some examples: dropped words, a swapped digit, a garbled callsign. The checker sees ASR output in production, not clean text.
5. Aim for a few thousand examples, roughly balanced between correct and each error type.

#### Training

LoRA on a 1B to 3B instruction-tuned text model. Input is the structured clearance plus the normalized readback text. Output is JSON with result, error type, and confidence.

#### Evaluation

Report on a held-out set:

- Accuracy per error type
- **False alarm rate** on correct readbacks. This is the number aviation-aware judges will ask for
- Latency and cost per check versus a large prompted model on the same set

#### The loop

Confident resolver verdicts from tier 2 get logged as new labeled pairs. Retrain the checker on them. "The agent generates its own training data" is the line for Baseten judges.

### Evaluation we show on stage

| Metric | Compare |
|---|---|
| Word error rate | Stock Whisper versus ours, same held-out clips |
| Checker accuracy and false alarm rate | Large prompted model versus our small tuned model |
| Latency per transmission | Tier 1 end to end |
| Cost per 1,000 transmissions | Large prompted model versus our small models |

### Baseten

Event guide: https://github.com/basetenlabs/Hack-the-North-2026

#### Setup

- Credits: promo code is in the event Slack channel `#spons-baseten-2026`. Redeem once per workspace under Billing and usage. If we share a workspace, its creator must redeem.
- **Training access: go to the Baseten booth first.** They enable H100 access and help pick a setup.

#### Inference

OpenAI-compatible. Never commit the key.

```python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["BASETEN_API_KEY"],
    base_url="https://inference.baseten.co/v1",
)
```

List live models:

```bash
curl https://inference.baseten.co/v1/models -H "Authorization: Bearer $BASETEN_API_KEY"
```

Streaming, tool calling, structured outputs, JSON mode, vision, and audio are supported, but support varies by model. Check before choosing.

#### Training flow

1. Install the Baseten CLI and sign in.
2. Define the training image, GPU, commands, secrets, cache, and checkpoint storage.
3. Submit the job and follow logs from the CLI or dashboard.
4. Save outputs under the Baseten checkpoint directory so they persist after the job stops.
5. Deploy the finished checkpoint as an endpoint.

#### Rate limits and errors

- 429 means a request or token limit. Retry with exponential backoff, never in a tight loop. Submit the event rate-limit form from the attendee channel.
- 402 means credits are not redeemed. 404 means a wrong model slug.
- Keep shared instructions and few-shot examples at the start of prompts so prompt caching can reuse them.
- When asking for help, bring the request ID and full error, or the training job ID and log lines.

#### Baseten Switch

Routes Claude Code or Codex through Baseten-hosted models. Using it for part of the build is worth a mention to their judges.

```bash
brew install basetenlabs/baseten/baseten-switch
baseten-switch setup
baseten-switch up --install
baseten-switch claude on
baseten-switch doctor --probe
```

Restart Claude Code afterwards. It is beta and macOS only.

---

# Part 7. Data sources and legal constraints

Read this before downloading or recording any audio. None of this is legal advice.

### Allowed

| Source | Use |
|---|---|
| `jacktol/atc-dataset` and `jlvdoorn/atco2-asr-atcosim` on Hugging Face | Training, evaluation, and demo clips. Real recordings with transcripts |
| Audio we record ourselves: teammates or judges playing pilot and controller | Training augmentation and the live demo |
| Synthetic audio from text-to-speech with added radio noise | Training augmentation |
| Synthetic clearance and readback text pairs | Checker training |
| FAA NASR data and OurAirports | Airports, runways, frequencies, navaids, fixes for sanity checks |

### Not allowed: LiveATC.net

We read their full terms of use on Sept 18, 2026. https://www.liveatc.net/legal/

- Access is for personal, non-commercial use only, and not for personal gain. A project competing for cash prizes is hard to square with that.
- No program, robot, or collection agent may retrieve content without their permission. That rules out scraping archives and piping a stream into our pipeline.
- Their streams may not be made available through another application.
- The site may not be used for aviation or operational activity that relies on its accuracy.
- Copying and editing clips is allowed if LiveATC is credited as the source. A handful of clips downloaded by hand through a browser is the most defensible use, but the prize money keeps it grey. We decided not to build on it.

It also would not help training. LiveATC audio has no transcripts.

If someone wants to ask permission, they have a contact form. A reply inside the hackathon is unlikely.

### Not allowed: our own radio receiver

As we understand it, Canada's Radiocommunication Act restricts using or sharing intercepted radio communications. This is why LiveATC has almost no Canadian feeds. Do not bring a scanner or software-defined radio and tune it to a Canadian airport.

### Stretch option: VATSIM

VATSIM is a flight-simulation network where hobbyists act as controllers and pilots using real phraseology, live around the clock. Their code of conduct allows account holders to record and stream sessions. It is not intercepted radio. Setup takes time, and whoever picks this up must read the policy first: https://vatsim.net/docs/policy/code-of-conduct/

### How the demo stays clean

- A judge or teammate plays the pilot through a radio-filtered mic.
- Non-interactive segments use held-out clips from the public datasets.

### Attribution

List every dataset, base model, and library in `README.md` with a link. Hack the North requires the project to be built during the event and substantially our own work.

---

# Part 8. Plan

Fill in names and exact times. Keep this current, since it is the first thing a teammate's Claude session should check before picking up work.

### Fixed deadlines

| When | What |
|---|---|
| Saturday 2:00 PM EDT | Sponsor prizes selected on Devpost. Rox and Baseten at minimum |
| TODO | Hacking ends and Devpost submission closes. Confirm on the event schedule |
| TODO | Judging starts |

### Workstreams

| Stream | Owner | Scope |
|---|---|---|
| A. Speech | TODO | Data prep, Whisper fine-tune on Baseten, evaluation, serving both stock and tuned endpoints |
| B. Pipeline | TODO | Audio ingest, VAD, normalizer, extractors, state machine, WebSocket events |
| C. Checker and agent | TODO | Synthetic readback data, checker fine-tune, resolver agent and tools |
| D. Screen and demo | TODO | Next.js live screen, radio mic filter, demo script, backup video, Devpost write-up |

Streams B and D should agree on the WebSocket event shapes in `03-architecture.md` first, then work in parallel against mock data.

### Milestones

1. **Friday night to early Saturday.** Datasets downloaded. Baseten credits redeemed and training access granted at the booth. First Whisper small run launched before anyone sleeps.
2. **Saturday morning.** Stock Whisper endpoint works end to end: speak into the mic, see a transcript on the screen. Normalizer passes its unit tests.
3. **Saturday 2:00 PM.** Sponsor prizes selected.
4. **Saturday afternoon.** Extractors and state machine working on dataset transcripts. Tuned Whisper small deployed. Launch medium.en. Checker data generated.
5. **Saturday evening. The whole demo path works once, however roughly.** Mic to alert on screen. This is the most important milestone.
6. **Saturday night.** Checker fine-tune. Resolver agent with at least re-listen and active-aircraft tools. Measure all four evaluation metrics.
7. **Sunday morning.** Polish, failure cases, rehearse the three-minute demo with a stand-in judge, record the backup video, finish Devpost.

### Risks and fallbacks

| Risk | Fallback |
|---|---|
| Whisper training does not converge or finishes late | Serve Whisper small instead of medium.en. Last resort is the published fine-tune on Hugging Face, stated openly |
| Real-time latency too high | Smaller ASR model, shorter chunks, skip the checker model when rules are decisive |
| Noisy venue breaks the live demo | Push-to-talk, a close mic, pre-recorded pilot clips one keypress away, and the backup video |
| Speaker classification is flaky | Two mics on stage |
| Checker false alarms too high | Raise thresholds and route more cases to `uncertain`. Show the trade-off honestly |
| Baseten rate limits | Exponential backoff, the event rate-limit form, and the booth |

### Cut list, in order

Drop from the bottom first.

1. Core loop: tuned ASR, extractor, state, rule checker, alert on screen
2. Stock versus tuned toggle and measured word error rate
3. Missing-readback timeout alerts
4. Fine-tuned checker model with measured false alarm rate
5. Resolver agent with visible steps
6. Similar-callsign warnings
7. Resolver verdicts fed back as checker training data
8. Waypoint lookup for garbled fixes
9. ElevenLabs synthetic training audio or spoken corrections
10. Sentry tracing
11. VATSIM live source

### Definition of done for the demo

- A stranger can pick up the mic, read a card, and trigger a correct alert within 3 seconds.
- A correct readback from the same stranger triggers nothing.
- The stock versus tuned difference is visible on one real clip.
- Every number on the results view was measured by us on held-out data.

---

# Part 9. Background: how aircraft routes work

This came up while discussing route instructions and the optimization idea. It matters because route clearances are some of the hardest things Tower will hear.

Planes do not fly the straight-line shortest path. They fly a planned route through a structured network, and ATC adjusts it in real time. The result is usually close to optimal but not quite.

**How a route gets chosen**

- **The sky has a road network.** Routes are built from named waypoints, most of them five-letter made-up words, connected by published airways. Think highways with exits.
- **Airports have on-ramps and off-ramps.** Departures and arrivals follow published procedures that funnel traffic in and out in an orderly way, even when that adds distance.
- **The airline picks the route, not ATC.** Before the flight, airline dispatch software chooses among available routes to minimize cost. Wind matters more than distance. A longer path with a strong tailwind often beats the short one, so "optimal" rarely means "shortest."
- **ATC approves and amends it.** The filed route can change for traffic, weather, military airspace, or congestion. In flight, controllers issue headings, altitude changes, and holds to keep aircraft separated.

**Where it gets closer to optimal**

- **Shortcuts.** When traffic allows, controllers clear a plane "direct to" a waypoint further along, cutting corners off the filed route. Pilots ask for these constantly.
- **Free route airspace.** Much of Europe's upper airspace lets airlines plan straight lines between entry and exit points instead of following airways.
- **Oceans.** Over the North Atlantic, tracks have long been redrawn daily to follow the jet stream, and satellite tracking is making those routes more flexible.

**How much is lost.** Studies in Europe put the average flown route at a few percent longer than the ideal. The bigger inefficiencies are often vertical: being held below the preferred altitude, or circling in a hold near a busy airport.

**Why this matters for Tower**

- "Proceed direct BOSOX" contains a made-up word stock speech models never get right. It is another place the fine-tune shows its value.
- The resolver agent can look up a garbled waypoint against a list of real fixes near the airport and pick the closest plausible match. FAA NASR data includes fixes. OurAirports has airports, runways, frequencies, and navaids.

---

# Part 10. Ideas discussed since the docs were written

None of these are committed scope. They are recorded here so the team can decide.

## 10.1 A route optimization agent. Verdict: do not build

The idea was an agent that studies the planes flying each day and proposes better routes for all of them.

Why it is a bad fit this weekend:

- **It is a different project.** Tower is about hearing messy radio and catching errors. Route optimization is an operations research problem on clean structured data and shares almost no code with Tower.
- **It weakens both sponsor stories.** Rox wants noisy, unstructured input, and flight tracks are tidy tables. There is nothing natural to fine-tune, so the Baseten angle disappears.
- **"Optimal" is much harder than it sounds.** It depends on winds aloft, aircraft weight, airspace restrictions, military zones, and every other aircraft. Airlines already run commercial optimizers daily. A weekend version would be a great-circle line with a wind layer.
- **It cannot be demoed convincingly.** Nothing happens live, a judge cannot take part, and we cannot prove a route is better without simulating the whole airspace.

## 10.2 Insights mined from what Tower already hears. Verdict: stretch goal and a strong "what's next" answer

Tower ends up with a structured log of every instruction on the frequency. That log contains real inefficiency signals:

- How often controllers give a "direct to" shortcut, and to which waypoint
- How often aircraft are put in holds, vectored off course, or stopped at an intermediate altitude
- Which filed routes almost always get amended the same way

An agent could read a day's log and produce findings such as "flights filed via this waypoint were cleared direct to the next one 80 percent of the time, so file it that way and save the distance." Airlines care about this, because a shortcut you can plan for saves fuel you do not have to carry.

This keeps the messy-data story, since the insights come from noisy radio transcripts, and it reuses the pipeline instead of competing with it. Closing line for the pitch: Tower catches errors in real time, and over time the same data shows where the airspace wastes fuel.

If built, it is one "insights" view on Sunday morning, only after everything above it on the cut list works.

## 10.3 A simulator. Verdict: good idea, build after the core path works

The idea was a sandboxed simulation environment that shows Tower working, including absurd scenarios. The simulator half is strong. It gives Tower four things.

- **A visible surface reacting.** Today the demo ends at a banner that says "mismatch." With a simple radar screen, a pilot reads back the wrong altitude and the simulated plane actually descends to it and heads toward another aircraft. Run it again with Tower on: the alert fires, the controller corrects, and the conflict never happens. That side-by-side is the WOW moment, and it is what made Lavoe's 2025 demo work.
- **Training and test data.** Simulated traffic produces clearances and readbacks with errors injected at a known rate. Run them through text-to-speech with radio noise to get labeled audio for the whole pipeline. This solves the problem that real errors are too rare to collect.
- **An honest scoreboard.** Run a thousand simulated exchanges and report detection rate, false alarm rate, and conflicts prevented. Measured numbers, not claims.
- **Absurd scenarios.** Twelve aircraft with near-identical callsigns, a pilot who gets everything wrong, static at maximum, a controller at double speed. Let a judge pick the chaos level with a slider. It is playful in the way the finalist rubric rewards, and it stress-tests mess handling for Rox.

**How to build it without derailing**

- **Default: write our own tiny simulator.** Planes with a position, heading, altitude, and target altitude, plus a rule that a plane obeys its own readback. A couple hundred lines, and we control every scenario.
- The frontend owner can start the radar panel now against scripted scenarios.
- Order matters. The mic-to-alert path must work Saturday night. The data generator helps the training stream today. The with-and-without replay and the absurd scenarios are Sunday morning polish.

## 10.4 Reinforcement learning. Verdict: skip

- It may not converge in the hours left. A policy that flies planes badly is worse than no policy.
- It is still a separate project that shares no code with the listening pipeline and adds nothing to the Rox story.
- It already exists. TU Delft publishes BlueSky-Gym, a library of ready-made reinforcement learning environments for air traffic. A judge who knows the field will ask what we added.

**The defensible optimization angle:** use the simulator to tune Tower itself. Sweep the alert threshold across thousands of simulated exchanges, plot missed errors against false alarms, pick the operating point, and show the curve. This is real optimization of our own system. Do not call it reinforcement learning, because it is not.

## 10.5 BlueSky, the existing open-source simulator

BlueSky is an open-source air traffic simulator from TU Delft, written in Python. It simulates the planes and the airspace, not the radio.

What it does:

- **Flies simulated aircraft** with realistic performance models and an autopilot that follows routes through real waypoints and airports from a built-in navigation database.
- **Takes text commands like a controller would give.** For example `ALT KL204 FL240` or `HDG KL204 270`. There are commands to create aircraft, set speed, and send a plane direct to a waypoint.
- **Runs scripted scenarios** from files of timestamped commands, so the same traffic situation can be replayed exactly.
- **Detects conflicts** when two aircraft are predicted to lose safe separation, and includes standard algorithms that resolve them.
- **Shows a radar-style screen** and runs faster than real time. It supports wind and plugins.

What it does not do: no voice, no radio audio, no readbacks. It assumes every command is received perfectly. That gap is where Tower lives.

Why it matters: BlueSky's commands are almost identical to our clearance schema. A possible connection:

1. Tower hears the controller's clearance and the pilot's readback.
2. The simulated plane obeys the readback, which is what a real pilot would do.
3. If the readback was wrong, BlueSky's own conflict detector shows the plane heading into trouble.
4. With Tower on, the alert fires and the corrected command goes in instead.

**Decision rule.** Give one person 30 to 45 minutes to install it and send it a command from Python. If it works, use it and gain a realistic radar screen and conflict detection for free. If it fights us, fall back to the tiny homemade simulator. Either way, not before the mic-to-alert path works.

```bash
pip install bluesky-simulator
```

Companion library with seven reinforcement learning environments: https://github.com/TUDelft-CNS-ATM/bluesky-gym

---

# Part 11. How we got here

Short history, so nobody re-litigates settled questions.

- **Starting vision:** target Rox and Baseten, possibly a third track, with a finance angle.
- **Key insight on the two sponsors:** Rox rewards an agent that processes noisy unstructured data and acts on it. Baseten rewards real use of their platform, and the strongest version is training a model on their H100s. So the ideal project has an input where a general model is visibly bad and a fine-tuned one is visibly better. Audio, jargon, and odd formats have the biggest gap. ATC radio has all three.
- **Evidence from past winners:** finalists have one-line hooks and judge participation. Only one of twelve 2025 finalists also won a sponsor prize, so a double win must be designed for. The official rubric is WOW factor, technical ability, originality, and design. Practicality is explicitly not a criterion.
- **Ideas considered and set aside:** a scam-call screening agent, a portfolio de-risking agent, several quant ideas, a medication reconciliation agent, a tariff code classifier, a wildfire situation map, an argument referee with a buzzer, an open-outcry trading pit, a hackathon stock exchange, and a negotiation agent. Tower was chosen for its dramatic fine-tune gap, public data, and interactive demo.
- **LiveATC:** we read their full terms and decided not to use it. Details are in Part 7.

---

# Part 12. Open decisions

- [ ] Owners for the four workstreams, and exact end-of-hacking and judging times. See Part 8.
- [ ] Confirm or change the proposed conventions: Python 3.11+, FastAPI, Pydantic, Next.js.
- [ ] Simulator: build it or not. If yes, homemade or BlueSky, decided by a 30 to 45 minute spike.
- [ ] Insights view: stretch goal or pitch-only.
- [ ] Whisper size for the demo: small for speed or medium.en for accuracy, decided by measured latency and word error rate.
- [ ] Extra tracks: ElevenLabs and Sentry, only if they fall out naturally. Prize selection on Devpost closes Saturday 2:00 PM EDT.

## Additional sources for Parts 9 and 10

- BlueSky-Gym repo: https://github.com/TUDelft-CNS-ATM/bluesky-gym
- BlueSky-Gym paper: https://www.sesarju.eu/sites/default/files/documents/sid/2024/papers/SIDs_2024_paper_021%20final.pdf
- Lavoe, 2025 Rox winner: https://devpost.com/software/lavoe
- Hack the North finalist museum: https://museum.hackthenorth.com/
- LiveATC terms of use: https://www.liveatc.net/legal/
