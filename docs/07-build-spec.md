# 07. Build spec: researched architecture for Tower

Written Saturday Sept 19, 2026 after reading the primary research and the tool docs. This is the concrete plan. Where it disagrees with `03-architecture.md` or `04-training.md`, this file wins. The changes are listed in section 12.

Every claim marked **verified** was checked against a source listed in section 13. Anything marked **estimate** or **assumption** was not.

## 1. What the product does

Tower is an advisory assistant for an air traffic controller. It does three things on one shared picture of the airspace.

1. **Suggests** safe shortcuts and cheap conflict fixes.
2. **Validates** that each pilot readback matches the instruction.
3. **Verifies** on radar that each aircraft does what it was told.

It runs against a simulator, so the radar picture is simulated and the radio audio is real sound passing through the real speech pipeline. The controller stays in charge throughout.

## 2. What the research says, and what we take from it

### HAAWAII readback error detection, DLR with NATS and Isavia. Verified from the paper

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

**Risk note on word error rate.** With speech accuracy worse than theirs, a naive checker will raise too many false alarms on real recordings. Four mitigations are built into this design: callsign snapping to the active list, the n-best rule in section 6, radar verification in section 7, and the resolver agent. In the simulator the audio is cleaner than real radio, so accuracy will be much better there. Report both numbers and say which is which.

### Contextual biasing. Verified

Biasing recognition toward callsigns known to be on frequency gave up to 60 percent relative improvement in callsign recognition in the ATCO2 work. We do it two ways: pass active callsigns and nearby waypoints to Whisper as a prompt, then snap the recognized callsign to the closest active one.

### SCOPE, 2026. Verified from the paper

A frozen language model plus a small plug-in classifier and retrieved examples reached 91 percent detection accuracy, but at **3.17 seconds per sample** on a 4B model. That is too slow for every transmission. It confirms our split: a fast classifier for everything, a language model only for the hard cases.

### Virtual simulation-pilot agent, Idiap. Verified from the abstract

An AI pilot for controller training built as four modules: speech recognition, a BERT entity parser, a response generator, and text-to-speech. It reported 5.5 and 15.9 percent word error rate on good and poor audio, over 96 percent callsign accuracy with surveillance data, and optional **deliberate readback errors** for trainee assessment. Our AI pilots follow the same shape.

### Off-the-shelf ATC models on Hugging Face. Verified to exist

| Model | Use |
|---|---|
| `Jzuluaga/bert-base-speaker-role-atc-en-uwb-atcc` | Text classifier, pilot versus controller. Use on real recordings where we do not know the speaker |
| `Jzuluaga/bert-base-ner-atc-en-atco2-1h` | Tags callsign, command, and value spans. Optional helper for the parser |
| `jacktol/whisper-medium.en-fine-tuned-for-ATC` | Fallback speech model |

## 3. System overview

```
                         +---------------------------+
                         |        SIMULATOR          |
                         | aircraft, routes, radar   |
                         +----+---------------+------+
             state (1 Hz)     |               ^ commands
                              v               |
+-----------+   audio   +-----------+   +-----+------+   suggestions   +------------+
| controller|---------->|   TOWER   |<->|  ADVISOR   |---------------->|   SCREEN   |
| mic       |           |   CORE    |   | shortcuts, |                 | radar,     |
+-----------+           | hear,     |   | conflicts  |                 | transcript,|
+-----------+   audio   | understand|   +------------+                 | alerts,    |
| AI pilots |---------->| track,    |----------- alerts, trace ------->| advisor    |
| LLM + TTS |<----------| check     |                                  +------------+
+-----------+ clearance +-----------+
```

Five services in one Python process to start with: simulator, Tower core, advisor, pilot agents, and a WebSocket hub. Split them only if needed.

## 4. Simulator

### Decision

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

### Our own simulator

- Flat x, y plane in nautical miles. A sector about 200 by 200 NM with 15 to 25 named waypoints and 4 to 6 routes through them.
- Each tick: turn toward the target heading or next waypoint at 3 degrees per second, climb or descend toward the target altitude at about 1,500 feet per minute, move at ground speed.
- Runs in real time for the live demo and as fast as possible for batch evaluation. Seeded random scenarios so runs are repeatable.

### BlueSky option. Calls verified from the BlueSky-Gym source

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

### Clearance to simulator command

| Item | Simulator effect |
|---|---|
| altitude or flight level | set target altitude |
| heading | set target heading, leave the route |
| direct to waypoint | drop route entries before that waypoint, resume route |
| speed | set target speed |
| frequency, squawk, altimeter | no motion. Tracked for readback only |

**The plane obeys the pilot's readback, not the controller's clearance.** That single rule is what makes a readback error visible on radar.

## 5. Advisor: shortcuts and conflict fixes

Plain geometry and search. No machine learning, and the language model never does the math.

### Trajectory prediction

For each aircraft, walk its route at constant ground speed and sample a position every 10 seconds for a 15 minute look-ahead. Altitude moves linearly toward the target. This gives an array of `(t, x, y, alt)`.

### Conflict test

Two aircraft conflict if, at the same sample time, they are within **5 NM horizontally and 1,000 ft vertically**. These are the standard en-route separation minima. For suggestions, test against a larger buffer of 8 NM so advice is never marginal.

### Shortcut search, per aircraft

```
skip if the aircraft has an open, unconfirmed clearance
for k from the last waypoint down to next+1:
    candidate = [present position -> waypoint k -> rest of route]
    if candidate crosses a blocked zone: continue
    if candidate conflicts with any other aircraft's predicted path: continue
    saving = length(current route) - length(candidate)
    if saving >= 3 NM: propose it and stop
```

Rank proposals by saving. Rate-limit to one per aircraft every few minutes so the controller is not nagged.

### Conflict resolution

When the predictor finds a conflict, generate candidate fixes for one of the two aircraft: level change of 1,000 or 2,000 ft, a heading change of 10, 20, or 30 degrees for a few minutes then direct back to the route, or a speed change. Keep the candidates that are conflict-free and choose the one with the least added distance. Prefer a level change when added distance ties.

### What the agent adds

- Decides whether a suggestion is worth interrupting for, given workload and how many alerts are open.
- Writes the one-line reason.
- Phrases it in correct radio language, for example "Air Canada one two three, proceed direct BOSOX."

### Closing the loop

An accepted suggestion becomes a clearance like any other. The controller speaks it, the pilot reads it back, Tower validates the readback, and radar verification confirms the turn. A wrong readback on a shortcut sends a plane to the wrong waypoint, so validation matters more here, not less.

### Evaluation

Run the same seeded scenarios twice in fast time, baseline and with the advisor auto-accepted. Report total distance, total flight time, conflicts, and number of suggestions. Fuel is an **assumption**: distance times a constant burn per NM by aircraft type, labeled as an estimate on screen. The OpenAP library gives better numbers if someone has time.

## 6. Tower core: hear, understand, track, check

### 6.1 Audio in

- Browser captures mic audio as 16 kHz mono PCM and streams it over a WebSocket. Push-to-talk, like a real radio.
- AI pilot speech is synthesized, run through the radio effect, and fed into **the same ingest path**. Tower must hear the pilots, never read their text.
- Silero VAD closes an utterance after about 300 ms of silence. Drop anything under 0.5 s.
- Radio effect: band-pass 300 to 3,400 Hz, light clipping, additive noise at a chosen level. The chaos slider controls the noise level.

### 6.2 Speech recognition

- Tier 1: our fine-tuned Whisper, converted to CTranslate2 and served with faster-whisper in a Truss on Baseten. Input is base64 WAV. Output is text plus average log-probability.
- Pass a **prompt** built from the active callsigns and nearby waypoint names on every call.
- For the agent's re-listen tool: the larger Hugging Face model with beam search returning the top 5 hypotheses and scores.
- Keep stock Whisper deployed for the side-by-side comparison.

Conversion command:

```bash
ct2-transformers-converter --model ./whisper-atc-checkpoint --output_dir ./whisper-atc-ct2 --quantization float16
```

### 6.3 Normalizer

Deterministic Python. Phonetic letters, digit words including niner, tree, and fife, decimals, flight levels, thousands and hundreds, runway suffixes, and airline telephony names to ICAO codes. Unit-tested, because most domain bugs live here.

### 6.4 Callsign snapping

Match the recognized callsign against the simulator's active list with a fuzzy score on the normalized form. Accept the best match above a threshold. Pilots often shorten callsigns, so allow suffix matches. If two active callsigns both score high, mark the transmission ambiguous and warn about similar callsigns.

### 6.5 Concept extraction

Two stages.

1. **Grammar parser.** Regular expressions over normalized text for the dozen command types we support: climb, descend, maintain, heading, turn, direct, speed, contact, squawk, altimeter, cleared, hold short. Runs in milliseconds.
2. **Language model fallback.** If the parser leaves too many unexplained words, or finds a command keyword without a value, make one structured-output call on Baseten.

Both produce the same `Extraction` with a list of `Item`s as defined in `03-architecture.md`.

### 6.6 Speaker role

In the simulator we know the channel, so it is ground truth. On real recordings use the published speaker-role classifier.

### 6.7 State machine

Per callsign, the six HAAWAII states. A controller transmission with mandatory items moves the aircraft to `EXPECTING_READBACK` and starts a 20 to 30 second timer. A pilot transmission with no commands while idle is `PILOT_REPORTING` and is ignored by the checker.

### 6.8 Checker

Three layers, cheapest first.

1. **Rules on concepts.** Pair items by type. Same value is a match. A different value is a candidate mismatch. A mandatory item missing from a multi-part readback is partial. A bare "roger" to a mandatory item is an error.
2. **The n-best rule.** Before alerting on a candidate mismatch, check whether the expected value appears in any of the top 5 speech hypotheses. If it does, the case is **ambiguous**, not a mismatch. This rule alone removes most false alarms caused by mis-hearing.
3. **Cross-encoder.** Our fine-tuned RoBERTa-base gives a class and confidence on the text pair. Agreement between rules and model raises confidence. Disagreement makes the case ambiguous.

Clear match: close silently. Clear mismatch with high confidence: alert. Everything else goes to tier 2.

## 7. Tier 2: the resolver agent, and radar verification

### Radar verification

A background check on every aircraft with a recently closed clearance.

- Altitude: the aircraft should move toward the cleared level and stop there. Alert if it passes through by more than 300 ft or moves the wrong way for more than 20 s.
- Heading or direct: the track should converge on the cleared heading or the bearing to the waypoint within about 60 s.
- This catches a correct readback followed by wrong flying, which no readback check can see.

### Resolver tools

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

## 8. AI pilots

One agent per simulated aircraft.

1. Receives the **true clearance as structured data from the simulator side**, plus what Tower's pipeline heard. If Tower's hearing of the controller was bad, the pilot says "say again," as a real pilot would.
2. Builds the readback from templates, shortened the way pilots shorten. A language model call adds phrasing variety. It is optional and can be cached.
3. With probability p, injects one error from the taxonomy in `02-domain.md`, weighted toward frequency changes, speed and heading confusion, and similar callsigns.
4. Speaks it with ElevenLabs, one voice per aircraft, through the radio effect, into Tower's audio ingest.
5. Sends the simulator the command matching **what the pilot said**.
6. Logs ground truth: true clearance, spoken text, injected error type, audio file.

Knobs: error probability, accent mix, speech rate, noise level, and how much pilots shorten.

## 9. The data engine

Add a scripted controller that issues clearances from scenario files and advisor suggestions. The world then runs unattended and produces labeled data.

| Output | Trains or tests |
|---|---|
| audio plus exact transcript | Whisper, as augmentation. Keep real recordings as the test set |
| controller text, pilot text, and error label | The cross-encoder checker |
| full exchanges with ground truth | End-to-end detection rate and false alarm rate |

Rule: never evaluate speech accuracy on synthetic audio alone. Report real-recording word error rate separately.

## 10. Models, training, and serving on Baseten

| Model | Base | Data | Training | Serving |
|---|---|---|---|---|
| Speech | Whisper small first, then medium.en | Public ATC datasets, plus simulator audio later | Hugging Face trainer on one H100. Time is an **estimate**: under an hour for small, one to three hours for medium.en | faster-whisper in a Truss |
| Checker | RoBERTa-base cross-encoder | 50,000 to 100,000 synthetic pairs, N+1 classes | Learning rate 2e-5, batch 64, AdamW. Minutes on an H100 | Small Truss, or in-process on CPU as a fallback |
| Extractor fallback, resolver, pilot phrasing, advisor phrasing | Hosted models on Baseten Model APIs | none | none | OpenAI-compatible API |

### Training job. Verified from Baseten's docs

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

## 11. Build order and the path that must work first

Interfaces first, then parallel work against mocks.

| Order | Deliverable | Depends on |
|---|---|---|
| 0 | Agree `AircraftState`, `Extraction`, `Verdict`, and the WebSocket event list. Frontend and backend both mock them | nothing |
| 1 | Own simulator stepping with 5 aircraft on routes, radar view drawing them | 0 |
| 2 | Mic to stock Whisper to transcript on screen | 0 |
| 3 | Normalizer, grammar parser, callsign snapping, state machine, rule checker. **Spoken clearance moves a plane** | 1, 2 |
| 4 | One AI pilot: template readback, ElevenLabs voice, radio effect, error injection. **First alert fires** | 3 |
| 5 | Fine-tuned Whisper deployed, stock versus ours comparison, measured word error rate | training stream |
| 6 | Advisor: shortcut search, suggestion card, accept by voice | 1, 3 |
| 7 | Radar verification and the `watch` tool | 3 |
| 8 | Cross-encoder checker trained and combined with rules, n-best rule | 4 for data |
| 9 | Resolver agent with its trace on screen | 7, 8 |
| 10 | Batch evaluation: advisor savings, detection and false alarm rates | 6, 8 |
| 11 | Chaos slider, absurd scenarios, conflict resolution advice, data engine | everything |

Steps 0 to 4 are the project. If only those work, there is still a complete demo: speak a clearance, a plane moves, an AI pilot reads back wrong, Tower catches it, and the plane visibly goes wrong when Tower is off.

**Suggested split for four people:** simulator and advisor, Tower core, models and evaluation, screen and AI pilots.

## 12. What changed from the earlier docs

- The checker model is a RoBERTa-base cross-encoder classifier, not a LoRA on a 1B to 3B language model.
- The state machine uses the six HAAWAII states.
- Extraction is a grammar parser first, with the language model as fallback, not a language model on every transmission.
- New: callsign snapping and Whisper prompts built from the active aircraft list.
- New: the n-best rule before any mismatch alert.
- New: radar verification and the `watch` tool.
- New components: simulator, advisor, AI pilots, data engine.
- The honest accuracy caveat: published detection results needed 5 to 10 percent word error rate, which is better than Whisper fine-tunes reach on real noisy recordings.

## 13. Sources

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
