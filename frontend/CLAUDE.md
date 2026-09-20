# frontend/

Next.js live screen. It is what judges look at, and "design" in the Hack the North rubric means intuitive and user-friendly, not pretty. The proposed user flow is in `../docs/01-project.md`.

## Contract

Consume the WebSocket events defined in `../docs/03-architecture.md` and section 3 of `../docs/07-build-spec.md`. Build against mock events first so you are never blocked on the backend.

## The feeling

Calm and quiet. Tower says nothing unless it has something worth saying, and it shows one decision at a time.

## Views

- **Map.** `components/MapView.tsx`: deck.gl layers over a MapLibre basemap, full screen, everything else floats over it. Draw from `lat`/`lon` and `lonlat`, never from `x_nm`. Altitude is real but exaggerated (slider). Aircraft on stems with trails and data blocks, standard routes dashed grey, Tower's plan cyan, replans flash amber, storms as 3D columns, intruders red with a predicted track, rings for alert / checking / watching. Click a plane for the flight strip, click the map to place a disruption (`latLonToNm` turns the click into sector NM). Camera: drag pans; two fingers on the trackpad swing the view round the scene and tilt it, like a 3D viewer, and pinch zooms (the View panel's "2 fingers: Orbit | Zoom" switch gives a mouse wheel its zoom back). It is a capture-phase `wheel` listener on the map's wrapper that acts only over a canvas, so panels still scroll, and lets ctrl+wheel (a pinch) through to MapLibre.
- **Disrupt this flight.** On the flight strip of a selected aircraft: Storm ahead, Launch ahead, Fighter, Mayday. Sends `add_disruption` with `target`; the backend picks the spot on that flight's path. This is the reliable way to make something happen to a particular aircraft.
- **Disrupt.** Four kinds on the menu (`menu !== false`). A placing click is read at the level the traffic is drawn at, not on the ground: with height exaggerated and the view tilted, the ground under the cursor is about 40 NM from the line under the cursor. One control, top left of the map. Random asks the backend to put something where it will matter; Choose lists the kinds from `state.disruption_kinds` and the next map click places one. Active disruptions are chips with minutes left and a remove button. A disruption event with `active: false` removes it. Zones are extruded between `floor_ft` and `ceiling_ft`, and the radar frame carries fresh `zones` while one is moving.
- **Flight strip.** `components/FlightStrip.tsx`: everything Tower knows about the selected aircraft, with follow-camera.
- **Plan toggle.** Fixed routes versus Tower's plan, with a savings counter.
- **The instruction panel has two jobs and two layouts.** Voice on: "Say these", a to-do list of cards for flights on frequency, most urgent first, the top one ringed. Voice off: "Sent by Tower", a log, newest first, because nothing there needs a human. In both, cards for flights that have not entered the sector collapse into one line, and every card carries a tag saying why it exists (Initial plan, Reroute · STORM1, Conflict · callsign, Emergency, Back on course, All clear), built from the card's `origin`, `cause` and `emergency`.
- **Instruction cards.** One per instruction Tower wants issued: the phrase to say, a one-line reason, and urgency. States are pending, spoken, validated, verified, and error. Push-to-talk to speak a card.
- **Alert.** A red card with expected versus heard, error type, confidence, a play button for the clip, and the correction to say. Click the card (or Enter on it; Space stays push-to-talk) to `focus` the aircraft: the camera flies in and follows, the flight strip opens with the same issue block on top, and the map draws the issue from `lib/issue.ts`: cyan is what was cleared, red is what was read back or is being flown. Level: rings on the stem. Fix: lines to each fix. Heading: two vectors. Anything else: the label alone.
- **Agent trace.** Expandable steps the resolver took and what it found. This is the Rox demo.
- **Transcript.** Speaker tag, callsign, text, and a confidence bar, with the stock versus tuned toggle.
- **Scoreboard.** Miles and time saved, losses of separation, errors caught, response times. Only numbers we measured.
- **Sliders.** Separation buffer and chaos level: noise, pilot error rate, traffic density.

## Look

Night operations room. Near-black ink, one cool signal colour for Tower's plan (`--accent`), one warm annunciator colour for anything that changed (`--warn`), red only for something wrong. Type is B612 and B612 Mono, the faces Airbus designed for cockpit displays. Floating panels use `.panel` or `.glass`; do not put `position` in those classes, it overrides Tailwind's `absolute`. The basemap is context, not content: keep it dimmer than the traffic.

No globe projection: with the deck.gl overlay it drops every aircraft icon, label and ring and leaves only the lines. The toggle was removed after it blanked the traffic mid-test.

Pinned: `maplibre-gl@5`. Version 6 fails to load its worker under Next.js dev.

## Rules

- A judge should understand the screen in five seconds without explanation.
- An alert must be impossible to miss and must never fire for a correct readback in the demo path.
- Everything must work on one laptop in a loud room. Push-to-talk is required.
- Cap the number of cards on screen. The exact cap is an open team decision.
