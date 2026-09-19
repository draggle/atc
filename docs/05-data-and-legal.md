# 05. Data sources and legal constraints

Read this before downloading or recording any audio. None of this is legal advice.

## Allowed

| Source | Use |
|---|---|
| `jacktol/atc-dataset` and `jlvdoorn/atco2-asr-atcosim` on Hugging Face | Training, evaluation, and demo clips. Real recordings with transcripts |
| Audio we record ourselves: teammates or judges playing pilot and controller | Training augmentation and the live demo |
| Synthetic audio from text-to-speech with added radio noise | Training augmentation |
| Synthetic clearance and readback text pairs | Checker training |
| FAA NASR data and OurAirports | Airports, runways, frequencies, navaids, fixes for sanity checks |

## Not allowed: LiveATC.net

We read their full terms of use on Sept 18, 2026. https://www.liveatc.net/legal/

- Access is for personal, non-commercial use only, and not for personal gain. A project competing for cash prizes is hard to square with that.
- No program, robot, or collection agent may retrieve content without their permission. That rules out scraping archives and piping a stream into our pipeline.
- Their streams may not be made available through another application.
- The site may not be used for aviation or operational activity that relies on its accuracy.
- Copying and editing clips is allowed if LiveATC is credited as the source. A handful of clips downloaded by hand through a browser is the most defensible use, but the prize money keeps it grey. We decided not to build on it.

It also would not help training. LiveATC audio has no transcripts.

If someone wants to ask permission, they have a contact form. A reply inside the hackathon is unlikely.

## Not allowed: our own radio receiver

As we understand it, Canada's Radiocommunication Act restricts using or sharing intercepted radio communications. This is why LiveATC has almost no Canadian feeds. Do not bring a scanner or software-defined radio and tune it to a Canadian airport.

## Stretch option: VATSIM

VATSIM is a flight-simulation network where hobbyists act as controllers and pilots using real phraseology, live around the clock. Their code of conduct allows account holders to record and stream sessions. It is not intercepted radio. Setup takes time, and whoever picks this up must read the policy first: https://vatsim.net/docs/policy/code-of-conduct/

## How the demo stays clean

- A judge or teammate plays the pilot through a radio-filtered mic.
- Non-interactive segments use held-out clips from the public datasets.

## Attribution

List every dataset, base model, and library in `README.md` with a link. Hack the North requires the project to be built during the event and substantially our own work.
