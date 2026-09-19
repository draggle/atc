# 02. Domain: how ATC radio works

Read this before writing prompts, the normalizer, the extractor, or the checker. Most bugs in this project will be domain bugs.

## The radio

- Every aircraft in a sector shares one frequency with the controller.
- Only one party can transmit at a time. Two simultaneous transmissions block each other. Pilots call this being **stepped on**.
- While your own mic is keyed you hear nothing.
- Audio is narrow-band AM with static, clipping, and cockpit noise. Speech is fast and clipped.

## The safety loop

1. **Clearance.** Controller issues an instruction, starting with the aircraft's callsign.
2. **Readback.** Pilot repeats the safety-critical parts, usually ending with their callsign.
3. **Hearback.** Controller listens and corrects any mismatch.

Tower automates step 3.

## What must be read back

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

## What counts as a match

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

## Our error taxonomy

Used for generating synthetic checker data and for reporting accuracy per type.

1. Wrong value: altitude, heading, speed, frequency, squawk, or altimeter
2. Wrong runway, including left versus right
3. Wrong direction: climb versus descend, left versus right turn
4. Wrong unit or type: flight level versus feet, heading digits read back as a speed
5. Omitted mandatory item in a multi-part clearance
6. Acknowledgement only, such as "roger" or "wilco," where a full readback is required
7. Wrong aircraft: another callsign reads back the clearance
8. Missing readback: nothing before the timeout

## Why errors happen

Similar callsigns on one frequency, accents, fast speech, non-standard phrasing, frequency congestion, high workload, and expectation bias, where people hear what they expected to hear.

## Phraseology the normalizer must handle

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

## Vocabulary

- **Squawk:** the four-digit transponder code a controller assigns.
- **Hold short:** stop before a runway. Getting this wrong causes runway incursions.
- **Level bust:** deviating more than 300 feet from a cleared altitude.
- **Stepped on:** a transmission blocked by someone else keying up.
- **Direct to:** a shortcut clearance straight to a waypoint further along the route.
- **Wilco:** will comply.

## Real incidents, for context only

Handle these respectfully. Never claim Tower would have prevented them.

- **Tenerife, 1977.** Two 747s collided on a foggy runway and 583 people died. Ambiguous phrasing and a blocked transmission were central. It is why the word "takeoff" is now only spoken in an actual takeoff clearance.
- **Washington DCA, January 29, 2025.** A regional jet and an Army helicopter collided and 67 people died. The NTSB found the helicopter crew may never have heard the words "pass behind the" because their own mic was keyed for about 0.8 seconds. The controller had no way to know. It was one of several factors. It illustrates the class of failure: a message that did not land, with nobody aware.

## Prior work

- **HAAWAII.** European research project with the German aerospace centre DLR, the UK provider NATS, and Iceland's Isavia. Early results were 82 percent detection with a 67 percent false alarm rate. Later lab tests on real recordings reached over 80 percent detection with under 20 percent false alarms. They used both a rule-based detector and a neural one trained on 129,000 synthetic examples, 79,000 of them errors across eight error kinds. This validates our synthetic-data plan, and it tells us false alarms are the hard part.
- **SCOPE, 2026.** Readback monitoring with a speech front end and a lightly trained LLM that compares transcript to instruction and classifies the error.
- **Whisper-ATC, TU Delft.** Fine-tuned Whisper large models set the state of the art on the ATCO2 and ATCOSIM datasets.
- **Synthetic ATC audio, 2026.** Text-to-speech plus noise augmentation improves word error rate when added to training data.

## Sources

- SKYbrary, read-back or hear-back: https://skybrary.aero/articles/read-back-or-hear-back
- ICAO paper on readback and hearback: https://www.icao.int/sites/default/files/APAC/Meetings/2025/2025%20ATMSG13/04-Information%20Papers/IP06%20Importance%20of%20ATC%20Readback%20and%20Hearback%20%20.pdf
- HAAWAII results: https://cordis.europa.eu/article/id/442201-better-automatic-speech-recognition-for-safer-air-traffic-control
- HAAWAII readback paper: https://www.sesarju.eu/sites/default/files/documents/sid/2022/paper_3.pdf
- SCOPE: https://arxiv.org/pdf/2605.29543
- Whisper-ATC: https://github.com/jlvdoorn/WhisperATC
- Synthetic ATC audio: https://arxiv.org/pdf/2606.21340
- NTSB DCA briefing: https://www.ntsb.gov/investigations/Documents/Feb.14.2025_Briefing_Mid-air_Collision%20near%20DCA.pdf
