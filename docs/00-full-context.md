# Tower: full context

Everything we know and have decided about the project in one file. Snapshot regenerated Saturday, Sept 19, 2026, during Hack the North, after the product grew to include the simulator, the planner, and AI pilots.

**How to use this file.** Read it once to get up to speed, or paste it into an assistant that cannot see the repo. For day-to-day work the numbered docs are the source of truth, and they win if this file disagrees. Parts 3 to 9 are copies of them as of this snapshot. `07-build-spec.md`, copied as Part 9, wins over `03` and `04` where they differ.

## Contents

1. The product in one page
2. How the listening and checking side works, step by step
3. Project, prizes, user flow, and demo (copy of `01-project.md`)
4. Domain: how ATC radio works (copy of `02-domain.md`)
5. Architecture: schemas and base events (copy of `03-architecture.md`)
6. Training, evaluation, and Baseten (copy of `04-training.md`)
7. Data sources and legal constraints (copy of `05-data-and-legal.md`)
8. Plan (copy of `06-plan.md`)
9. The researched build spec (copy of `07-build-spec.md`)
10. Background: how aircraft routes work
11. Background: BlueSky, and the insights idea
12. How we got here: the decision history

---

# Part 1. The product in one page

**Tower is an AI system for air traffic control. It plans the best path for every flight, adapts the moment anything changes, and makes sure every instruction is heard and flown correctly.**

**The problem**

- Planes fly fixed highways in the sky from waypoint to waypoint, not their ideal paths. That wastes time and fuel on every flight.
- When something unexpected happens, such as a storm or an unknown aircraft, controllers reroute everyone by hand and under pressure.
- Every instruction travels over a noisy shared radio. Pilots repeat instructions back, and busy controllers sometimes miss a wrong readback. The plane then flies the wrong altitude or heading.

**What Tower does**

1. **Plans.** Every flight gets its ideal path. Conflicts are resolved ahead of time with small changes to timing, speed, or altitude, at the same safety margins used today.
2. **Replans.** It repairs the plan when flights run late, or when a fighter jet, a storm, or an unknown object enters the airspace. It reroutes only the affected flights.
3. **Listens.** It transcribes the noisy radio with a speech model we train on air traffic audio. Standard speech models fail badly on it.
4. **Validates.** It checks that each pilot's readback means the same as the instruction, and alerts on a wrong readback, a missing readback, or the wrong aircraft answering.
5. **Verifies.** It watches the radar to confirm each plane does what it was told. A plane that deviates is one more unexpected change: Tower alerts the controller and replans the others around it.

**Why the pieces belong together.** A tightly optimized plan only works if every instruction is heard and followed exactly. The tighter the plan, the more one wrong readback matters. The listening and checking side is what makes the optimization safe to use.

**Where the agent comes in.** Most checks are fast and simple. When audio is garbled or two callsigns sound alike, an agent investigates. It re-listens, checks which aircraft are on frequency, looks at the radar, and can watch a plane before deciding. Then it alerts, dismisses with a reason, or flags the case for a human. The controller stays in charge.

**How we show it.** Tower runs inside a simulator we build, with AI pilots that answer by voice and sometimes make mistakes.

- **Efficiency:** the same traffic on fixed routes, then on Tower's plan.
- **Disruption:** a judge drops a fighter jet or a storm into the airspace and the routes bend around it.
- **Communication:** a judge plays the controller, a pilot reads back wrong, and Tower catches it. With Tower off, that plane flies into trouble.
- **Separation slider:** normal is the product. Absurd shows the trade-off between efficiency and margin.

**What we build:** a speech model and a readback checker trained on Baseten, the simulator, the planner, the AI pilots, the investigating agent, and a live screen.

**Targets:** Rox Best AI Agent, Baseten, ElevenLabs, and a Hack the North finalist spot.

---

# Part 2. How the listening and checking side works, step by step

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

Updated Saturday Sept 19, 2026. The product grew from a readback monitor into a planner plus a safety net. This file describes the current product.

### One line

Tower plans the best path for every flight, adapts the moment anything changes, and makes sure every instruction is heard and flown correctly.

### The problem

- Planes fly fixed highways in the sky from waypoint to waypoint, not their ideal paths. That wastes time and fuel on every flight.
- When something unexpected happens, such as a storm or an unknown aircraft, controllers reroute everyone by hand and under pressure.
- Every instruction travels over a noisy shared radio. Pilots repeat instructions back, and busy controllers sometimes miss a wrong readback. The plane then flies the wrong altitude or heading. Research puts readback errors at 1 to 2 percent of transmissions.

### What Tower does

1. **Plans.** Every flight gets its ideal path. Conflicts are resolved ahead of time with small changes to timing, speed, or altitude, at the same safety margins used today.
2. **Replans.** The plan is never fixed. Tower repairs it when flights run late, or when a fighter jet, a storm, or an unknown object enters the airspace. It reroutes only the affected flights.
3. **Listens.** New instructions go out by voice. Tower transcribes the noisy radio with a Whisper model we fine-tune on ATC audio.
4. **Validates.** It checks that each pilot's readback means the same thing as the instruction. It alerts on a wrong readback, a missing readback, or the wrong aircraft answering.
5. **Verifies.** It watches the radar to confirm each plane does what it was told. A plane that deviates is one more unexpected change: Tower alerts the controller and replans the others around it.

Most checks are fast and simple. When audio is garbled or two callsigns sound alike, an AI agent investigates. It re-listens, checks which aircraft are on frequency, looks at the radar, then alerts, dismisses with a reason, or flags the case for a human. The controller stays in charge throughout.

### Why the pieces belong together

A tightly optimized plan only works if every instruction is heard and followed exactly. The tighter the plan, the more one wrong readback matters. The listening and checking side is what makes the optimization safe to use. This is the core of the pitch.

### How we talk about separation

- **The claim:** every plane gets its ideal path, and conflicts are solved ahead of time, at the same safety margins.
- **Not the claim:** planes can fly closer. Separation rules cover real uncertainty: position error, wind, wake turbulence, and human reaction time. Anyone from aviation will reject "closer is fine if the math says no collision."
- **The honest version of the closeness idea:** plot efficiency against buffer size against loss-of-separation rate. With validation on, errors are caught early, so the same safety level holds at a smaller buffer. See the safety section of `07-build-spec.md`.
- Separation is a slider in the simulator. At the normal setting it is the product. Dragged to absurd it is a demo scenario.

### What Tower is not

- Not an operational tool, not certified, not connected to any real ATC system. It runs against our simulator.
- Not listening to live real-world radio. See `05-data-and-legal.md`.
- Not a claim that any specific accident would have been prevented.
- Not a once-a-day plan. It replans continuously.

### What we are targeting

Every sponsor prize we want must be selected on Devpost **before 2:00 PM EDT Saturday**.

| Target | What they score | Our evidence | Demo moment | Risk |
|---|---|---|---|---|
| **Rox: Best AI Agent**, $10K and $2K | An LLM agent handling noisy, conflicting, incomplete real-world data and taking meaningful actions | Garbled radio, similar callsigns, a radar source that can contradict the radio, and an agent that investigates then commits | The red card with the agent's trace | They may ask if the data is real. Include real recordings and their measured accuracy |
| **Baseten** | Real, creative use of their platform | Two models trained on their H100s, all inference through their API, a simulator that generates its own training data | Stock Whisper versus ours on one clip, plus speed and cost of small tuned models | If training slips, there is little to show |
| **MLH: Best Use of ElevenLabs** | Voice central to the experience | Every AI pilot is a distinct voice | The judge talks to a sky full of pilots | Low |
| **Hack the North finalist** | WOW factor, technical ability, originality, design | Planner, intruder scenario, voice loop | The judge drops a fighter jet in, then personally causes and catches a near miss | Doing too much and finishing nothing |

Also select:

- **GoDaddy best domain name.** Register a good domain and point it at the project. Five minutes.
- **Sentry,** only if someone commits to owning it. Needs two products beyond error monitoring, such as tracing plus AI agent monitoring. The prize is guaranteed interviews.

Only if they cost nothing: Huawei multi-agent if our agents really interact, Tiger Data if we need a time-series database anyway, Aramco beginner prize if every teammate has attended one or fewer hackathons.

Skip: OpenAI and Gemini undercut the Baseten story. Huawei OMNI needs vision. QNX needs their operating system. Elastic, Shopify, RBC, and Warp do not fit without bending the project.

The planner earns nothing directly from Rox or Baseten. It earns the finalist vote and makes the story coherent. The listening, checking, and training work decides the sponsor prizes, so protect that time.

### Official Hack the North judging criteria

Criteria: WOW factor, technical ability, originality, and design, meaning a user-friendly and intuitive experience.

Explicitly not criteria: practicality and entrepreneurship, visual appeal on its own.

The judging pitch must be a live demo, not slides or a product pitch.

### Lessons from past winners

- Of the twelve 2025 finalists, only one also won a sponsor prize. Winning both takes deliberate design.
- Finalist hooks fit in one line and usually involve the judge or an object on the table.
- Lavoe won Rox's $10K in 2025 with one LLM call and four tools driving a visible UI. Judges score the demo, not the codebase.

### Proposed user flow. The team should confirm this

The user is a controller at one screen. The feeling is calm and quiet. Tower says nothing unless it has something worth saying, and it shows one decision at a time.

1. **Plan view.** Open a scenario. Toggle between today's fixed routes and Tower's plan, and watch a savings counter change.
2. **Live view.** Traffic flows. Each instruction Tower wants issued appears as a card with a one-line reason. The controller holds push-to-talk and says it. The pilot answers by voice. The card turns green once the readback is validated and the radar confirms the plane complied.
3. **Something goes wrong.** A wrong readback turns the card red. It shows what was expected, what was heard, a play button for the audio, and the correction to say. The agent's reasoning is one click away.
4. **Disruption.** The user drops a jet or a storm into the airspace. Affected routes flash and bend, and new cards arrive sorted by urgency.
5. **Review.** A scoreboard shows miles saved, losses of separation, errors caught, and response times.

#### Open decisions for the team

- [ ] Is the main user a working controller, a trainee, or a supervisor?
- [ ] Does the controller speak every instruction, or is there an auto-speak mode?
- [ ] What is the maximum number of cards on screen at once?
- [ ] Is the separation slider a product setting or a demo-only toy?

### Demo script, about three minutes

1. **Hook, 15 seconds.** "Planes fly fixed highways, and every instruction goes over a noisy radio. Tower gives every flight its best path and makes sure every instruction is heard and flown correctly."
2. **Efficiency, 30 seconds.** Same traffic on fixed routes, then on Tower's plan. Show miles, time, and conflicts.
3. **Disruption, 30 seconds.** The judge drops a fighter jet into the airspace. Routes bend around it.
4. **Communication, 60 seconds.** The judge plays the controller and speaks an instruction card. An AI pilot reads it back wrong. The card goes red. With Tower off, that plane flies into trouble.
5. **Hard case and the speech model, 30 seconds.** A garbled readback. Show the agent's steps, then stock Whisper against ours on one real ATC clip.
6. **Numbers and close, 15 seconds.** Losses of separation, miles saved, errors caught, word error rate. All measured by us.

Always have a backup video recorded Sunday morning.

### Questions judges will ask, and our answers

- **Is this not already done?** Pieces are active research, not products. HAAWAII reached about 80 percent readback error detection at an 11 percent false alarm rate in trials. Trajectory-based operations is the stated goal of US and European modernization. We built an end-to-end version in a weekend with open models we fine-tuned ourselves.
- **Do planes really fly closer?** No. Same margins, better paths. Show the trade-off curve.
- **What about false alarms?** It is the real problem. Our defences are callsign snapping, the n-best rule, radar verification, and the agent. Show the measured rate.
- **Is the data real?** The speech model trains and is tested on real public ATC recordings. The traffic and the pilots are simulated, and we say which numbers come from which.
- **Is the planner AI?** No. It is a classic search algorithm, and that is the right tool. The language model decides when to interrupt, explains, and phrases instructions. The agent handles messy audio.
- **Why not live radio?** LiveATC's terms forbid it, and Canadian law restricts using intercepted radio.
- **What did you train versus take off the shelf?** Be precise: base models, datasets, hours, what is ours.
- **What did each of you build?** Everyone demos a piece.

### Submission checklist

- [ ] Sponsor prizes selected on Devpost before 2:00 PM EDT Saturday: Rox, Baseten, ElevenLabs, GoDaddy, and Sentry if owned
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

> **Updated by `07-build-spec.md`.** The schemas and WebSocket events here still hold. The checker design, state machine, extraction strategy, simulator, planner, safety metrics, AI pilots, and extra WebSocket events are specified there, and that file wins where they differ.

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

> **Updated by `07-build-spec.md`.** The checker is now a RoBERTa-base cross-encoder, not a LoRA on a language model. Baseten training and serving commands are in section 11 of that file.

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

Updated Saturday Sept 19, 2026. Fill in names and exact times. A teammate's Claude session should check this file before picking up work. The detailed build order is in section 12 of `07-build-spec.md`.

### Fixed deadlines

| When | What |
|---|---|
| Saturday 2:00 PM EDT | Sponsor prizes selected on Devpost: Rox, Baseten, ElevenLabs, GoDaddy, and Sentry if someone owns it |
| TODO | Hacking ends and Devpost submission closes. Confirm on the event schedule |
| TODO | Judging starts |

### Workstreams

| Stream | Owner | Scope |
|---|---|---|
| A. Simulator and planner | TODO | Simulator, conflict test, planner, replanning, intruders, batch evaluation of efficiency and safety |
| B. Tower core | TODO | Audio ingest, VAD, normalizer, callsign snapping, parser, state machine, checker rules, radar verification, resolver agent |
| C. Models and evaluation | TODO | Whisper fine-tune on Baseten, checker cross-encoder, serving, word error rate and detection metrics |
| D. Screen and AI pilots | TODO | Radar view, instruction cards, alerts, agent trace, AI pilot voices with radio effect, demo script, backup video, Devpost |

All four agree the shared schemas and WebSocket events first, then build against mocks.

### Milestones

1. **Now.** Interfaces agreed and mocked. Datasets downloading. Baseten credits redeemed, training access granted at the booth, first Whisper small run launched.
2. **Saturday morning.** Simulator steps five aircraft and the radar draws them. Mic to stock Whisper to transcript on screen.
3. **Saturday 2:00 PM.** Sponsor prizes selected.
4. **Saturday afternoon.** A spoken clearance moves a plane. One AI pilot reads back by voice and the first alert fires. Planner produces a conflict-free plan for one scenario.
5. **Saturday evening. The whole demo path works once, however roughly.** Plan, speak, readback, alert, plane deviates with Tower off. This is the most important milestone.
6. **Saturday night.** Tuned Whisper deployed. Checker trained on simulator data. Replanning around an intruder. Radar verification.
7. **Sunday early morning.** Resolver agent with its trace. Batch evaluation for every number on the scoreboard.
8. **Sunday morning.** Polish, rehearse the three-minute demo with a stand-in judge, record the backup video, finish Devpost.

### Risks and fallbacks

| Risk | Fallback |
|---|---|
| Scope is now large | The cut list below. Steps 0 to 4 of the build order are a complete demo alone |
| Whisper training does not converge or finishes late | Serve Whisper small. Last resort is the published fine-tune on Hugging Face, stated openly |
| Serving a custom Whisper on Baseten takes too long | Ask the booth early. Fall back to their hosted Whisper for tier 1 and keep our fine-tune for the comparison |
| Planner finds no solution in a dense scenario | Widen the replan set, then relax cost limits, then fall back to the emergency layer. Never drop below the separation minimum |
| Real-time latency too high | Smaller speech model, grammar parser only, skip the checker model when rules are decisive |
| Noisy venue breaks the live demo | Push-to-talk, a close mic, pre-recorded controller clips one keypress away, and the backup video |
| Checker false alarms too high | Raise thresholds and route more cases to uncertain or to radar watching. Show the trade-off honestly |
| Baseten rate limits | Exponential backoff, the event rate-limit form, and the booth |

### Cut list, in order

Drop from the bottom first.

1. Simulator with aircraft on routes and a radar view
2. Spoken clearance moves a plane: mic, speech, parser, state machine
3. One AI pilot reading back by voice with injected errors, and rule-based alerts
4. Tuned Whisper with the stock comparison and measured word error rate
5. Planner producing a conflict-free plan, with the fixed-route baseline comparison
6. Replanning around an intruder or storm
7. Radar verification and the watch tool
8. Checker cross-encoder with measured detection and false alarm rates
9. Resolver agent with visible steps
10. Monte Carlo safety evaluation and the trade-off curve
11. Separation slider and absurd scenarios
12. Self-running data engine with an AI controller
13. Sentry tracing, insights view, waypoint lookup, VATSIM

### Definition of done for the demo

- A stranger can drop an intruder into the airspace and see a conflict-free replan within a few seconds.
- A stranger can speak an instruction card, hear a wrong readback, and see a correct alert within 3 seconds.
- A correct readback triggers nothing.
- The stock versus tuned speech difference is visible on one real clip.
- Every number on the scoreboard was measured by us, and we can say how.

---

# Part 9. Build spec: researched architecture for Tower

Written Saturday Sept 19, 2026 after reading the primary research and the tool docs. This is the concrete plan. Where it disagrees with `03-architecture.md` or `04-training.md`, this file wins. The changes are listed in section 13.

Every claim marked **verified** was checked against a source listed in section 14. Anything marked **estimate** or **assumption** was not.

### 1. What the product does

Tower is an advisory system for an air traffic controller. It does five things on one shared picture of the airspace.

1. **Plans** an ideal, conflict-free path for every flight.
2. **Replans** when anything changes: a late flight, a storm, an intruder, or a plane that deviates.
3. **Listens** to the radio with a speech model we fine-tune.
4. **Validates** that each pilot readback matches the instruction.
5. **Verifies** on radar that each aircraft does what it was told.

It runs against a simulator, so the radar picture is simulated while the radio audio is real sound passing through the real speech pipeline. The controller stays in charge throughout. The full product description and the separation framing are in `01-project.md`.

### 2. What the research says, and what we take from it

#### HAAWAII readback error detection, DLR with NATS and Isavia. Verified from the paper

| Finding | What we do with it |
|---|---|
| Compare at **concept level**, not word level. The pilot says "one eighty to DME four, tower one eighteen seven" for a clearance worded completely differently, and it is still correct | The checker compares extracted items, never raw strings |
| A six-state machine per aircraft: `UNKNOWN`, `EXPECTING_READBACK`, `READBACK_OK`, `READBACK_ERROR`, `MISSING_READBACK`, `PILOT_REPORTING`. Missing readback fires after a timeout, 30 s in their work | Adopt these states as-is |
| Their neural detector is a **cross-encoder**: RoBERTa-base fed `controller text [SEP] pilot text`, classifying into N+1 classes, one for correct and N error kinds. Trained on 129,000 synthetic pairs, learning rate 2e-5, batch 64, AdamW, up to 20,000 steps | Our checker model is this, not a LoRA on a large language model. It trains in minutes and answers in milliseconds |
| Best result combines rules and the neural model: 81 percent of real errors detected. In trials, 80 percent detection at an **11 percent false alarm rate** | Build both and combine them |
| Those results needed word error rates of **5 percent for controllers and 10 percent for pilots**. One provider's controller speech reached 2.8 percent | Our published Whisper baselines are 15 to 23 percent on real noisy audio. See the risk note below |
| Pilots shorten their transmissions, which wrecks command extraction for pilot speech. Callsign extraction is rescued by **surveillance data**: knowing which aircraft are actually in the sector | We have that list for free from the simulator. Use it everywhere |
| About one third of en-route readback errors involve frequency changes. About 10 percent of communication errors are speed confused with heading. About 20 percent involve similar callsigns on one frequency | Weight the synthetic data and the demo scenarios toward these |
| About 15 percent of controller words are outside any command, such as greetings and chatter | The parser must tolerate unknown words |

**Risk note on word error rate.** With speech accuracy worse than theirs, a naive checker will raise too many false alarms on real recordings. Four mitigations are built into this design: callsign snapping to the active list, the n-best rule in section 7, radar verification in section 8, and the resolver agent. In the simulator the audio is cleaner than real radio, so accuracy will be much better there. Report both numbers and say which is which.

#### Contextual biasing. Verified

Biasing recognition toward callsigns known to be on frequency gave up to 60 percent relative improvement in callsign recognition in the ATCO2 work. We do it two ways: pass active callsigns and nearby waypoints to Whisper as a prompt, then snap the recognized callsign to the closest active one.

#### SCOPE, 2026. Verified from the paper

A frozen language model plus a small plug-in classifier and retrieved examples reached 91 percent detection accuracy, but at **3.17 seconds per sample** on a 4B model. That is too slow for every transmission. It confirms our split: a fast classifier for everything, a language model only for the hard cases.

#### Virtual simulation-pilot agent, Idiap. Verified from the abstract

An AI pilot for controller training built as four modules: speech recognition, a BERT entity parser, a response generator, and text-to-speech. It reported 5.5 and 15.9 percent word error rate on good and poor audio, over 96 percent callsign accuracy with surveillance data, and optional **deliberate readback errors** for trainee assessment. Our AI pilots follow the same shape.

#### Off-the-shelf ATC models on Hugging Face. Verified to exist

| Model | Use |
|---|---|
| `Jzuluaga/bert-base-speaker-role-atc-en-uwb-atcc` | Text classifier, pilot versus controller. Use on real recordings where we do not know the speaker |
| `Jzuluaga/bert-base-ner-atc-en-atco2-1h` | Tags callsign, command, and value spans. Optional helper for the parser |
| `jacktol/whisper-medium.en-fine-tuned-for-ATC` | Fallback speech model |

### 3. System overview

```
                         +---------------------------+
                         |        SIMULATOR          |
                         | aircraft, routes, radar   |
                         +----+---------------+------+
             state (1 Hz)     |               ^ commands
                              v               |
+-----------+   audio   +-----------+   +-----+------+   instructions  +------------+
| controller|---------->|   TOWER   |<->|  PLANNER   |---------------->|   SCREEN   |
| mic       |           |   CORE    |   | plan,      |                 | radar,     |
+-----------+           | hear,     |   | replan     |                 | transcript,|
+-----------+   audio   | understand|   +------------+                 | alerts,    |
| AI pilots |---------->| track,    |----------- alerts, trace ------->| cards      |
| LLM + TTS |<----------| check     |                                  +------------+
+-----------+ clearance +-----------+
```

Five services in one Python process to start with: simulator, Tower core, planner, pilot agents, and a WebSocket hub. Split them only if needed.

#### WebSocket events added by this spec

These extend the list in `03-architecture.md`.

| type | Payload |
|---|---|
| `radar` | list of `AircraftState`, once per second |
| `plan` | per-flight planned path, plus totals for the plan and the fixed-route baseline |
| `plan_update` | which flights changed, why, and the trigger |
| `instruction_card` | `{id, callsign, items, phrase, reason, urgency_s, status}` where status is pending, spoken, validated, verified, or error |
| `disruption` | an intruder, storm, or closed zone that was added, with its predicted path |
| `scoreboard` | miles and time saved, losses of separation, errors caught, response times |

### 4. Simulator

#### Decision

Start with our own small simulator. Spend at most 45 minutes trying BlueSky in parallel. Both sit behind the same interface, so swapping is cheap.

```python
class Sim(Protocol):
    def step(self, dt: float) -> None: ...
    def aircraft(self) -> list[AircraftState]: ...
    def apply(self, callsign: str, cmd: SimCommand) -> None: ...
    def spawn(self, scenario: Scenario) -> None: ...

class AircraftState(BaseModel):
    callsign: str
    x_nm: float; y_nm: float          # local flat projection
    alt_ft: float; target_alt_ft: float
    hdg_deg: float; target_hdg_deg: float | None
    gs_kt: float
    route: list[str]                   # remaining waypoint names
    actype: str
```

#### Our own simulator

- Flat x, y plane in nautical miles. A sector about 200 by 200 NM with 15 to 25 named waypoints and 4 to 6 routes through them.
- Each tick: turn toward the target heading or next waypoint at 3 degrees per second, climb or descend toward the target altitude at about 1,500 feet per minute, move at ground speed.
- Runs in real time for the live demo and as fast as possible for batch evaluation. Seeded random scenarios so runs are repeatable.

#### BlueSky option. Calls verified from the BlueSky-Gym source

```python
import bluesky as bs
bs.init(mode='sim', detached=True)          # headless
bs.stack.stack('DT 5;FF')                   # 5 s steps, fast-forward
bs.traf.cre('ACA123', actype='A320', acspd=250, aclat=43.6, aclon=-79.6, achdg=90, acalt=10000)
bs.stack.stack('ACA123 addwpt 43.9 -78.9')
bs.stack.stack('HDG ACA123 270')
bs.stack.stack('SPD ACA123 220')
bs.sim.step()
i = bs.traf.id2idx('ACA123')
bs.traf.lat[i], bs.traf.lon[i], bs.traf.alt[i], bs.traf.hdg[i], bs.traf.tas[i]
bs.tools.geo.kwikqdrdist(lat1, lon1, lat2, lon2)   # bearing, distance
```

BlueSky works in SI units internally, so convert altitude and speed. Check the exact `ALT` and `DIRECT` command syntax in BlueSky's command reference before relying on them. Install with `pip install bluesky-simulator`.

#### Clearance to simulator command

| Item | Simulator effect |
|---|---|
| altitude or flight level | set target altitude |
| heading | set target heading, leave the route |
| direct to waypoint | drop route entries before that waypoint, resume route |
| speed | set target speed |
| frequency, squawk, altimeter | no motion. Tracked for readback only |

**The plane obeys the pilot's readback, not the controller's clearance.** That single rule is what makes a readback error visible on radar.

### 5. Planner: plan every flight, then keep repairing the plan

This replaces the earlier shortcut advisor. A shortcut is just one kind of repair. It is plain search and geometry. No machine learning, and the language model never does the math.

**Source note.** The conflict minima and BlueSky calls in this file were verified. The planning method below comes from general knowledge of the field, which calls the goal trajectory-based operations. It was not checked against papers during the hackathon.

#### The problem

Each flight has an entry point, an exit point, an entry time, a speed, and a preferred altitude. Produce a path in space and time for every flight that minimizes total cost, such that no two flights ever come closer than the separation minimum plus a buffer, and no path crosses a blocked zone.

#### Why prioritized planning

Planning all flights jointly to a true optimum grows exponentially with the number of aircraft. Reactive methods that dodge at the last moment are fast but never give an efficient plan. Prioritized planning is the practical middle: fast, simple, and good, though not guaranteed optimal.

#### Trajectory representation and conflict test

- A trajectory is an array of `(t, x, y, alt)` sampled every 10 seconds along the path at the planned speed. Altitude moves linearly toward its target.
- Two flights conflict if at the same sample time they are within **5 NM horizontally and 1,000 ft vertically**. These are the standard en-route minima.
- The planner tests against the minimum plus a **buffer**. The buffer is the separation slider. Default 3 NM extra.
- For 40 aircraft and a 30 minute horizon this is a small numpy computation.

#### Planning

```
order flights by priority: airborne first, then by entry time
for each flight:
    candidates = [ideal direct path at preferred speed and altitude]
               + entry delayed by 1, 2, 3 min          (only if not yet in the sector)
               + speed changed by 5 or 10 percent
               + altitude changed by 1,000 or 2,000 ft
               + a dogleg of 5 or 10 NM either side of the conflict point
    sort candidates by cost
    keep the first that does not conflict with flights already planned
    and does not cross a blocked zone
```

**Cost** is added time plus added distance, a penalty for altitude changes, and during replanning a penalty for deviating from the previous plan so routes do not flicker.

**Improvement pass.** Take the flight that paid the highest cost, move it earlier in the order, replan, and keep the result only if total cost drops. Repeat until a time budget of a second or two runs out.

#### Replanning

Triggers: an intruder, a storm or closed zone, a late flight, or a plane that deviates after a bad readback. Radar verification in section 8 raises that last one.

- **Predict the newcomer.** Assume it keeps its current speed and heading. Give it a larger buffer that grows with look-ahead time, because it is not cooperating. Start at 10 NM plus 1 NM per minute.
- **Disturb as little as possible.** Replan only the flights whose trajectories now conflict and hold everyone else fixed. Widen the set to their neighbours only if no solution exists.
- **Respect the radio.** Freeze the next 60 to 90 seconds of every path. An instruction must be spoken, read back, and flown before it takes effect.
- **Emergency layer.** If a loss of separation is predicted within about two minutes, skip optimization and issue an immediate turn away or level change.

#### From plan to instructions

Each change to a flight's plan becomes one instruction card: direct to a waypoint, a heading, a speed, or a level. Cards are sorted by urgency, meaning time until the change must take effect. The language model decides whether a non-urgent card is worth interrupting for, writes the one-line reason, and phrases it in correct radio language.

An issued card is a clearance like any other. The controller speaks it, the pilot reads it back, Tower validates the readback, and radar verification confirms compliance. A wrong readback on a reroute sends a plane somewhere the plan did not expect, so validation matters more here, not less.

#### Baseline for comparison

The same traffic on fixed waypoint routes, first come first served. Winds and detailed aircraft performance are left out. Say so on stage.

### 6. Safety: how we define and measure it

#### The hard floor

A **loss of separation** is two aircraft within 5 NM horizontally and 1,000 ft vertically at the same moment. The planner must produce zero of these. It never plans below the minimum, whatever the slider says. The slider only changes the extra buffer, except in an explicitly labeled absurd scenario.

#### Robustness is the real definition

A plan that is only safe when everything goes perfectly is not safe. Test each plan under disturbance with Monte Carlo runs. In each run, vary speeds by a few percent, shift entry times by about a minute, delay some instructions, and inject readback errors at a realistic rate of 1 to 2 percent or higher.

| Measure | Definition |
|---|---|
| Loss-of-separation rate | Events per simulated flight hour. The headline number |
| Closest approach | Distribution of minimum distances between pairs, not only the worst case |
| Severity | How deep into the protected zone a breach went, and for how long |
| Readback detection | Share of injected readback errors detected, false alarm rate on correct readbacks, seconds from error to alert |
| Conformance | How far a plane deviated, in feet and seconds, before radar verification flagged it |
| Disruption response | Seconds from an intruder appearing to a conflict-free plan, and the closest anyone came to it |
| Efficiency | Total distance and time versus the fixed-route baseline |

#### The trade-off curve

Plot efficiency against buffer size against loss-of-separation rate, with Tower's validation on and off. With validation on, errors are caught early, so the same safety level holds at a smaller buffer. This is the honest form of the closeness argument: better communication safety is what earns tighter plans.

#### Headline sentence to aim for

Zero losses of separation across N simulated hours with a realistic readback error rate, while flying X percent fewer miles than fixed routes. Fill in N and X from our own runs.

### 7. Tower core: hear, understand, track, check

#### 7.1 Audio in

- Browser captures mic audio as 16 kHz mono PCM and streams it over a WebSocket. Push-to-talk, like a real radio.
- AI pilot speech is synthesized, run through the radio effect, and fed into **the same ingest path**. Tower must hear the pilots, never read their text.
- Silero VAD closes an utterance after about 300 ms of silence. Drop anything under 0.5 s.
- Radio effect: band-pass 300 to 3,400 Hz, light clipping, additive noise at a chosen level. The chaos slider controls the noise level.

#### 7.2 Speech recognition

- Tier 1: our fine-tuned Whisper, converted to CTranslate2 and served with faster-whisper in a Truss on Baseten. Input is base64 WAV. Output is text plus average log-probability.
- Pass a **prompt** built from the active callsigns and nearby waypoint names on every call.
- For the agent's re-listen tool: the larger Hugging Face model with beam search returning the top 5 hypotheses and scores.
- Keep stock Whisper deployed for the side-by-side comparison.

Conversion command:

```bash
ct2-transformers-converter --model ./whisper-atc-checkpoint --output_dir ./whisper-atc-ct2 --quantization float16
```

#### 7.3 Normalizer

Deterministic Python. Phonetic letters, digit words including niner, tree, and fife, decimals, flight levels, thousands and hundreds, runway suffixes, and airline telephony names to ICAO codes. Unit-tested, because most domain bugs live here.

#### 7.4 Callsign snapping

Match the recognized callsign against the simulator's active list with a fuzzy score on the normalized form. Accept the best match above a threshold. Pilots often shorten callsigns, so allow suffix matches. If two active callsigns both score high, mark the transmission ambiguous and warn about similar callsigns.

#### 7.5 Concept extraction

Two stages.

1. **Grammar parser.** Regular expressions over normalized text for the dozen command types we support: climb, descend, maintain, heading, turn, direct, speed, contact, squawk, altimeter, cleared, hold short. Runs in milliseconds.
2. **Language model fallback.** If the parser leaves too many unexplained words, or finds a command keyword without a value, make one structured-output call on Baseten.

Both produce the same `Extraction` with a list of `Item`s as defined in `03-architecture.md`.

#### 7.6 Speaker role

In the simulator we know the channel, so it is ground truth. On real recordings use the published speaker-role classifier.

#### 7.7 State machine

Per callsign, the six HAAWAII states. A controller transmission with mandatory items moves the aircraft to `EXPECTING_READBACK` and starts a 20 to 30 second timer. A pilot transmission with no commands while idle is `PILOT_REPORTING` and is ignored by the checker.

#### 7.8 Checker

Three layers, cheapest first.

1. **Rules on concepts.** Pair items by type. Same value is a match. A different value is a candidate mismatch. A mandatory item missing from a multi-part readback is partial. A bare "roger" to a mandatory item is an error.
2. **The n-best rule.** Before alerting on a candidate mismatch, check whether the expected value appears in any of the top 5 speech hypotheses. If it does, the case is **ambiguous**, not a mismatch. This rule alone removes most false alarms caused by mis-hearing.
3. **Cross-encoder.** Our fine-tuned RoBERTa-base gives a class and confidence on the text pair. Agreement between rules and model raises confidence. Disagreement makes the case ambiguous.

Clear match: close silently. Clear mismatch with high confidence: alert. Everything else goes to tier 2.

### 8. Tier 2: the resolver agent, and radar verification

#### Radar verification

A background check on every aircraft with a recently closed clearance.

- Altitude: the aircraft should move toward the cleared level and stop there. Alert if it passes through by more than 300 ft or moves the wrong way for more than 20 s.
- Heading or direct: the track should converge on the cleared heading or the bearing to the waypoint within about 60 s.
- This catches a correct readback followed by wrong flying, which no readback check can see.

#### Resolver tools

| Tool | Returns |
|---|---|
| `relisten(transmission_id)` | Top 5 hypotheses from the larger speech model |
| `active_aircraft()` | Callsigns on frequency with their open clearances |
| `frequency_history(callsign, n)` | The last n exchanges |
| `aircraft_state(callsign)` | Position, altitude, heading, and their trends |
| `sanity_check(item)` | Whether a value is plausible: a real waypoint, a valid frequency, an altitude in range |
| `watch(callsign, seconds)` | Defer the decision and let radar verification settle it |
| `raise_alert`, `dismiss`, `mark_uncertain` | Terminal actions |

`watch` is the important new one. When Tower cannot tell 240 from 210, the honest move is to watch where the aircraft levels off and alert only if it goes wrong. It is also the clearest example of resolving a noisy source with a clean one.

Limits are unchanged: at most 4 tool calls, about 5 seconds unless watching, and it always ends in exactly one terminal action. It is a hand-rolled loop on the OpenAI SDK pointed at Baseten.

### 9. AI pilots

One agent per simulated aircraft.

1. Receives the **true clearance as structured data from the simulator side**, plus what Tower's pipeline heard. If Tower's hearing of the controller was bad, the pilot says "say again," as a real pilot would.
2. Builds the readback from templates, shortened the way pilots shorten. A language model call adds phrasing variety. It is optional and can be cached.
3. With probability p, injects one error from the taxonomy in `02-domain.md`, weighted toward frequency changes, speed and heading confusion, and similar callsigns.
4. Speaks it with ElevenLabs, one voice per aircraft, through the radio effect, into Tower's audio ingest.
5. Sends the simulator the command matching **what the pilot said**.
6. Logs ground truth: true clearance, spoken text, injected error type, audio file.

Knobs: error probability, accent mix, speech rate, noise level, and how much pilots shorten.

### 10. The data engine

Add a scripted controller that issues clearances from scenario files and planner instructions. The world then runs unattended and produces labeled data.

| Output | Trains or tests |
|---|---|
| audio plus exact transcript | Whisper, as augmentation. Keep real recordings as the test set |
| controller text, pilot text, and error label | The cross-encoder checker |
| full exchanges with ground truth | End-to-end detection rate and false alarm rate |

Rule: never evaluate speech accuracy on synthetic audio alone. Report real-recording word error rate separately.

### 11. Models, training, and serving on Baseten

| Model | Base | Data | Training | Serving |
|---|---|---|---|---|
| Speech | Whisper small first, then medium.en | Public ATC datasets, plus simulator audio later | Hugging Face trainer on one H100. Time is an **estimate**: under an hour for small, one to three hours for medium.en | faster-whisper in a Truss |
| Checker | RoBERTa-base cross-encoder | 50,000 to 100,000 synthetic pairs, N+1 classes | Learning rate 2e-5, batch 64, AdamW. Minutes on an H100 | Small Truss, or in-process on CPU as a fallback |
| Extractor fallback, resolver, pilot phrasing, instruction phrasing | Hosted models on Baseten Model APIs | none | none | OpenAI-compatible API |

#### Training job. Verified from Baseten's docs

```python
# training/whisper/config.py
from truss_train import (TrainingProject, TrainingJob, Image, Compute, Runtime,
                         CacheConfig, CheckpointingConfig)
from truss.base.truss_config import AcceleratorSpec

runtime = Runtime(
    start_commands=["chmod +x ./run.sh && ./run.sh"],
    cache_config=CacheConfig(enabled=True),
    checkpointing_config=CheckpointingConfig(enabled=True),
)
job = TrainingJob(
    image=Image(base_image="pytorch/pytorch:2.7.0-cuda12.8-cudnn9-runtime"),
    compute=Compute(accelerator=AcceleratorSpec(accelerator="H100", count=1)),
    runtime=runtime,
)
project = TrainingProject(name="tower-whisper-atc", job=job)
```

- `run.sh` installs requirements and launches the training script.
- The script **must save checkpoints under `$BT_CHECKPOINT_DIR`**, or they are lost when the job ends.
- Submit with `truss train push config.py`. Baseten's newer docs show the same thing as `baseten train push --config config.py`. Use whichever the installed CLI accepts.
- Follow logs with `baseten train job logs --job-id <id> --tail`.
- One-command checkpoint deployment is documented for language models. For Whisper and RoBERTa, download the checkpoint and deploy it in our own Truss.
- Baseten's cookbook has LoRA and PyTorch examples but no Whisper example, so ask the booth for help early.

### 12. Build order and the path that must work first

Interfaces first, then parallel work against mocks.

| Order | Deliverable | Depends on |
|---|---|---|
| 0 | Agree `AircraftState`, `Extraction`, `Verdict`, and the WebSocket event list. Frontend and backend both mock them | nothing |
| 1 | Own simulator stepping with 5 aircraft on routes, radar view drawing them | 0 |
| 2 | Mic to stock Whisper to transcript on screen | 0 |
| 3 | Normalizer, grammar parser, callsign snapping, state machine, rule checker. **Spoken clearance moves a plane** | 1, 2 |
| 4 | One AI pilot: template readback, ElevenLabs voice, radio effect, error injection. **First alert fires** | 3 |
| 5 | Fine-tuned Whisper deployed, stock versus ours comparison, measured word error rate | training stream |
| 6 | Planner: conflict-free plan for a scenario, fixed-route baseline, instruction cards spoken by voice | 1, 3 |
| 7 | Radar verification and the `watch` tool | 3 |
| 8 | Cross-encoder checker trained and combined with rules, n-best rule | 4 for data |
| 9 | Resolver agent with its trace on screen | 7, 8 |
| 10 | Replanning around an intruder or storm, with the freeze window and emergency layer | 6 |
| 10b | Batch and Monte Carlo evaluation: efficiency, loss-of-separation rate, detection and false alarm rates, trade-off curve | 6, 8 |
| 11 | Separation and chaos sliders, absurd scenarios, data engine | everything |

Steps 0 to 4 are the project. If only those work, there is still a complete demo: speak a clearance, a plane moves, an AI pilot reads back wrong, Tower catches it, and the plane visibly goes wrong when Tower is off.

**Suggested split for four people:** simulator and planner, Tower core, models and evaluation, screen and AI pilots.

### 13. What changed from the earlier docs

- The checker model is a RoBERTa-base cross-encoder classifier, not a LoRA on a 1B to 3B language model.
- The state machine uses the six HAAWAII states.
- Extraction is a grammar parser first, with the language model as fallback, not a language model on every transmission.
- New: callsign snapping and Whisper prompts built from the active aircraft list.
- New: the n-best rule before any mismatch alert.
- New: radar verification and the `watch` tool.
- New components: simulator, planner, AI pilots, data engine.
- The shortcut advisor was replaced by a planner that plans every flight and repairs the plan when anything changes. Shortcuts are one kind of repair.
- New section 6 defines safety and how we measure it, including Monte Carlo robustness runs and the trade-off curve.
- Separation framing: same margins and better paths, never "planes fly closer."
- The honest accuracy caveat: published detection results needed 5 to 10 percent word error rate, which is better than Whisper fine-tunes reach on real noisy recordings.

### 14. Sources

- HAAWAII readback error detection paper: https://www.sesarju.eu/sites/default/files/documents/sid/2022/paper_3.pdf
- SCOPE: https://arxiv.org/pdf/2605.29543
- Virtual simulation-pilot agent: https://arxiv.org/abs/2304.07842
- Callsign boosting with surveillance data: https://arxiv.org/pdf/2108.12156 and https://arxiv.org/pdf/2202.03725
- ATCO2 lessons learned: https://arxiv.org/pdf/2305.01155
- Speaker role model: https://huggingface.co/Jzuluaga/bert-base-speaker-role-atc-en-uwb-atcc
- ATC entity tagger: https://huggingface.co/Jzuluaga/bert-base-ner-atc-en-atco2-1h
- BlueSky: https://github.com/TUDelft-CNS-ATM/bluesky
- BlueSky-Gym, source of the verified calls: https://github.com/TUDelft-CNS-ATM/bluesky-gym
- Baseten training getting started: https://docs.baseten.co/training/getting-started
- Baseten ML cookbook: https://github.com/basetenlabs/ml-cookbook
- Baseten faster-whisper Truss example: https://github.com/basetenlabs/truss-examples/tree/main/whisper/faster-whisper-small
- Baseten Whisper streaming tutorial: https://www.baseten.co/blog/zero-to-real-time-transcription-the-complete-whisper-v3-websockets-tutorial/

---

# Part 10. Background: how aircraft routes work

This is the background behind the planner. It matters because route clearances are some of the hardest things Tower will hear.

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

# Part 11. Background: BlueSky, and the insights idea

## BlueSky, the existing open-source simulator

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

## The insights idea. Status: not in scope, and our answer to "what is next"

Tower ends up with a structured log of every instruction on the frequency. That log contains real inefficiency signals:

- How often controllers give a "direct to" shortcut, and to which waypoint
- How often aircraft are put in holds, vectored off course, or stopped at an intermediate altitude
- Which filed routes almost always get amended the same way

An agent could read a day's log and produce findings such as "flights filed via this waypoint were cleared direct to the next one 80 percent of the time, so file it that way and save the distance." Airlines care about this, because a shortcut you can plan for saves fuel you do not have to carry.

This keeps the messy-data story, since the insights come from noisy radio transcripts, and it reuses the pipeline instead of competing with it. Closing line for the pitch: Tower catches errors in real time, and over time the same data shows where the airspace wastes fuel.

It sits at the very bottom of the cut list.

---

# Part 12. How we got here: the decision history

Recorded so nobody re-litigates settled questions.

- **Starting vision:** target Rox and Baseten, possibly a third track, with a finance angle.
- **Key insight on the two sponsors:** Rox rewards an agent that processes noisy unstructured data and acts on it. Baseten rewards real use of their platform, and the strongest version is training a model on their H100s. The ideal project has an input where a general model is visibly bad and a fine-tuned one is visibly better. ATC radio is that input.
- **Evidence from past winners:** finalists have one-line hooks and judge participation. Only one of twelve 2025 finalists also won a sponsor prize. The rubric is WOW factor, technical ability, originality, and design. Practicality is explicitly not a criterion.
- **Ideas considered and set aside:** a scam-call screening agent, a portfolio de-risking agent, several quant ideas, a medication reconciliation agent, a tariff code classifier, a wildfire situation map, an argument referee with a buzzer, an open-outcry trading pit, a hackathon stock exchange, and a negotiation agent.
- **Version 1, the readback monitor.** Listen, check readbacks, alert. Demo: a judge plays the pilot.
- **LiveATC ruled out** after reading their full terms. See Part 7.
- **Route optimization, first proposed as a separate agent.** Initially argued against: a different project, no fit with Rox or Baseten, hard to demo. The team kept returning to it, and it was adopted in a form that connects to validation.
- **Simulator adopted.** It gives a visible surface, labeled training data, an honest scoreboard, and room for absurd scenarios.
- **Reinforcement learning rejected.** It may not converge in time, it is a separate project, and ready-made environments already exist. The optimization we do is search, plus tuning our own thresholds.
- **Demo flipped.** The judge plays the controller and AI pilots answer by voice. This made ElevenLabs a natural track.
- **Says versus does.** Radar verification was added so Tower catches a correct readback followed by wrong flying, and so the agent can resolve unclear audio by watching the plane.
- **Research pass.** Reading the HAAWAII and SCOPE papers changed the checker to a small cross-encoder, adopted the six-state machine, and surfaced the false alarm risk and its four defences. See Part 9.
- **Shortcut advisor generalized into a planner.** Plan every flight up front, then repair the plan on any change, including intruders. Shortcuts became one kind of repair.
- **Separation framing settled.** The claim is ideal paths at the same margins, never "planes fly closer." The slider and the trade-off curve carry the closeness idea honestly.
- **Safety defined.** Zero losses of separation as the hard floor, and robustness under Monte Carlo disturbance as the real measure.

## Open decisions

- [ ] Owners for the four workstreams, and exact end-of-hacking and judging times
- [ ] Main user: working controller, trainee, or supervisor
- [ ] Speak every instruction, or offer an auto-speak mode
- [ ] Maximum cards on screen
- [ ] Separation slider: product setting or demo-only
- [ ] Own simulator or BlueSky, decided by a 45 minute trial
- [ ] Whisper size for the demo, decided by measured latency and word error rate
- [ ] Whether someone owns Sentry
