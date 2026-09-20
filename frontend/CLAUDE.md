# frontend/

Next.js live screen. It is what judges look at, and "design" in the Hack the North rubric means intuitive and user-friendly, not pretty. The proposed user flow is in `../docs/01-project.md`.

## Contract

Consume the WebSocket events defined in `../docs/03-architecture.md` and section 3 of `../docs/07-build-spec.md`. Build against mock events first so you are never blocked on the backend.

## The feeling

Calm and quiet. Tower says nothing unless it has something worth saying, and it shows one decision at a time.

## Views

- **Map.** `components/MapView.tsx`: deck.gl layers over a MapLibre basemap, full screen, everything else floats over it. Draw from `lat`/`lon` and `lonlat`, never from `x_nm`. Altitude is real but exaggerated (slider). Aircraft on stems with trails and data blocks, standard routes dashed grey, Tower's plan cyan, replans flash amber, storms as 3D columns, intruders red with a predicted track, rings for alert / checking / watching. Click a plane for the flight strip, click the map to place a disruption (`latLonToNm` turns the click into sector NM).
- **Disrupt this flight.** On the flight strip of a selected aircraft: Storm ahead, Launch ahead, Fighter, Mayday. Sends `add_disruption` with `target`; the backend picks the spot on that flight's path. This is the reliable way to make something happen to a particular aircraft.
- **Disrupt.** `components/DisruptMenu.tsx`: one round button in the top bar, right of the speed control, opening a popover. Four kinds on the menu (`menu !== false`). "Surprise me" asks the backend to put something where it will matter; picking a kind arms it (`state.dropMode`, in the store because the button and the map are in different trees) and the next map click places it, with a hint over the map and Escape to cancel. A placing click is read at the level the traffic is drawn at, not on the ground: with height exaggerated and the view tilted, the ground under the cursor is about 40 NM from the line under the cursor. Active disruptions are chips in the popover with minutes left and a remove button. squack opens the popover with `ui_command {command:"panel", args:{name:"disrupt"}}`. A disruption event with `active: false` removes it. Zones are extruded between `floor_ft` and `ceiling_ft`, and the radar frame carries fresh `zones` while one is moving.
- **Flight strip.** `components/FlightStrip.tsx`: everything Tower knows about the selected aircraft, with follow-camera.
- **Plan toggle.** Fixed routes versus Tower's plan, with a savings counter.
- **The instruction panel has two jobs and two layouts.** Voice on: "Say these", a to-do list of cards for flights on frequency, most urgent first, the top one ringed. Voice off: "Sent by Tower", a log, newest first, because nothing there needs a human. In both, cards for flights that have not entered the sector collapse into one line, and every card carries a tag saying why it exists (Initial plan, Reroute · STORM1, Conflict · callsign, Emergency, Back on course, All clear), built from the card's `origin`, `cause` and `emergency`.
- **Instruction cards.** One per instruction Tower wants issued: the phrase to say, a one-line reason, and urgency. States are pending, spoken, validated, verified, and error. Push-to-talk to speak a card.
- **Alert.** A red card with expected versus heard, error type, confidence, a play button for the clip, and the correction to say. Click the card (or Enter on it; Space stays push-to-talk) to `focus` the aircraft: the camera flies in and follows, the flight strip opens with the same issue block on top, and the map draws the issue from `lib/issue.ts`: cyan is what was cleared, red is what was read back or is being flown. Level: rings on the stem. Fix: lines to each fix. Heading: two vectors. Anything else: the label alone.
- **Agent trace.** Expandable steps the resolver took and what it found. This is the Rox demo.
- **Transcript.** Speaker tag, callsign, text, and a confidence bar. Always the tuned model; what stock Whisper heard is a hover tooltip, not a control.
- **Analytics.** Miles and time saved, losses of separation, errors caught, response times. Only numbers we measured. Collapsed by default to one bar carrying losses and conflicts predicted; open, it ends in two example questions that ask squack.
- **Sliders.** Separation buffer and chaos level: noise, pilot error rate, traffic density.

## The bottom row

Frequency, the chat bar and the instruction panel sit along the bottom edge as one row. The two side
boxes are `BOTTOM_ROW_H` tall (`lib/layout.ts`) with scrolling interiors; the answer dock grows up
from the bar to the same top edge and scrolls inside rather than growing past it. Anything that
needs the row's geometry reads `lib/layout.ts`; nothing hard-codes it twice.

Nothing floats in the middle of the screen. There is no ready/paused/ended banner, and only `error`
notices are drawn, in the dock. squack never speaks unprompted: every answer is a reply.

## Look

squack in the boot screen sets the direction: one black ground (`--bg`), white ink, Plus Jakarta Sans for every label, hierarchy by weight and opacity rather than colour, hairline `1px` borders in `--line`, 8px radius, no blur, glow, gradients, corner ticks, uppercase eyebrows or cyan. Think Notion or Linear in dark mode: quiet, generous spacing, sentence case, the product is "squack." (lowercase, with the period) and never "Tower" in anything a judge reads. Colour only when it means something: `--ok` green for live, correct, on frequency; `--bad` red for wrong; `--warn` amber for "squack is asking". Reuse the shared classes at the end of `app/globals.css` (`.btn`, `.btn-primary`, `.seg`, `.pill`, `.chip`, `.dot`, `.stat`, `.card-pick`, `.scrim`, `.hint`) before inventing a look; floating panels use `.panel` or `.glass`, and do not put `position` in those classes, it overrides Tailwind's `absolute`. B612 Mono stays for data that has to line up: callsigns, levels, transcripts. The basemap is context, not content: keep it dimmer than the traffic.

No globe projection: with the deck.gl overlay it drops every aircraft icon, label and ring and leaves only the lines. The toggle was removed after it blanked the traffic mid-test.

Pinned: `maplibre-gl@5`. Version 6 fails to load its worker under Next.js dev.

## Rules

- A judge should understand the screen in five seconds without explanation.
- An alert must be impossible to miss and must never fire for a correct readback in the demo path.
- Everything must work on one laptop in a loud room. Push-to-talk is required.
- Cap the number of cards on screen. The exact cap is an open team decision.
