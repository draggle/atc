# TRD 04: Screen, pilots, and the demo

For the teammate who owns what judges see and hear. Read `README.md`, `frontend/README.md`, `frontend/CLAUDE.md`, `docs/08-ws-protocol.md`, `docs/01-project.md` demo script, `joey-notes.md` sections 2, 6, 7, 13, 14, and `docs/09-overnight-findings.md` Integration section first. Code: `frontend/`, `backend/pilots/`, `backend/world.py` for the events.

## Where things stand

- The screen renders live against the backend and in mock mode (`?mock=1`). Radar, plan toggle, cards, alert card with agent trace, transcript with stock toggle, scoreboard, sliders, push-to-talk with two channels, typed fallbacks for both channels.
- Verified with Playwright: clicking "Say it" produces a controller transcript from Tower's own voice and a pilot transcript from the AI pilot's voice within about 5 seconds, and a wrong readback turns the card red with a correction phrase.
- Pilot voices are macOS `say` through a radio band-pass, soft clip, noise, and squelch clicks. Six voices survived Whisper; see findings.
- ElevenLabs client is written in `backend/pilots/tts.py` against the documented endpoint with `output_format=pcm_16000`, never run.

## Tasks, in order

### 1. Real microphone on the demo laptop, 30 minutes, do this first

Open `localhost:3000` in Chrome, hold Space, read a card aloud, release. You should see a controller transcript within 2 seconds and a pilot reply a few seconds later. Check: the browser asked for mic permission, the level meter moves, `backend` log shows no `ptt` errors, and the transcript is roughly right. If the transcript is empty, the utterance was under 0.4 s or the downsampler produced silence; check `frontend/lib/audio.ts`. Test in the noisiest room you can find. If it fails there, the typed radio box is the fallback and the demo script should say so.

### 2. ElevenLabs, 45 minutes

Put `ELEVENLABS_API_KEY` in `.env`, optionally `ELEVENLABS_VOICE_IDS` as a comma list. Restart the backend and run `python -m pilots.demo_voice wrong_value 0.3`; `afplay` the file. Confirm the pilot transcripts still come through Whisper at similar confidence; if ElevenLabs voices are cleaner than `say`, lower the radio noise slider so it still sounds like a radio. Pick distinct voices per airline. Give Tower itself a voice for auto-speak (`CONTROLLER_VOICE` in `backend/world.py` is a `say` name today; make it a backend-agnostic voice id). This is the MLH ElevenLabs prize; the judge should hear at least three different pilots.

### 3. Radar verification on screen, 45 minutes

Conformance verdicts arrive as `alert` events whose reason starts with "Radar:" and cards move to verified after 30 s. Add: a small "watching" ring on an aircraft whose clearance is being verified (the backend can emit `clearance_updated` with a `watching: true` flag, or expose `ConformanceMonitor.watching()` through the state event), and a distinct alert style for radar-sourced alerts ("Read back right, flying wrong"). This is the says-versus-does story.

### 4. Small fixes from the overnight gap list, 1 hour total

- Interpolate aircraft positions between 1 Hz radar ticks.
- Card urgency countdown from server `t`, not client arrival.
- Transcript callsign: ask Joey to attach `callsign` to the transcript event (it is in the extraction), then drop the regex.
- Auto-play the alert's audio clip once when a red card appears, with a mute toggle.
- Show "Tower OFF" as a persistent grey banner across the radar so the off-state is unmistakable in the demo.
- Cap visible cards at 4 (done) and show "+N" (done); confirm the sort keeps error cards on top after they are dismissed.

### 5. The demo, the rest of the day

Write `docs/10-demo-script.md` from `joey-notes.md` sections 7 and 14 and `docs/01-project.md`. Three minutes, timed, with exactly which button or phrase happens at each second. Two versions: live and backup. Rehearse with a stand-in judge who has never seen it. Things that must be one keypress away: load scenario, a pre-recorded controller clip for each demo card (record them with the real voice and play through `radio_text` or a new `play_clip` message if the mic dies), Tower off, drop intruder.

Record the backup video by early afternoon Sunday from a clean run. Screen capture at 1080p with system audio so the pilot voices are audible.

Devpost: confirm sponsor prizes were selected before the Saturday 2 PM deadline (Rox, Baseten, ElevenLabs, GoDaddy). Badge IDs of every teammate. Repo link. The README attribution list is current as of the overnight build; add ElevenLabs voice names and anything new.

## Honesty on the screen

The scoreboard says "measured this session" and that must stay true. Do not seed it. If a number is from the Monte Carlo rather than the live session, label it. The stock-versus-tuned toggle must show the real stock text from a real stock model; if `text_stock` is null because no stock endpoint is configured, hide the toggle rather than fake it.
