# frontend/

Next.js live screen. It is what judges look at, and "design" in the Hack the North rubric means intuitive and user-friendly, not pretty. The proposed user flow is in `../docs/01-project.md`.

## Contract

Consume the WebSocket events defined in `../docs/03-architecture.md` and section 3 of `../docs/07-build-spec.md`. Build against mock events first so you are never blocked on the backend.

## The feeling

Calm and quiet. Tower says nothing unless it has something worth saying, and it shows one decision at a time.

## Views

- **Radar.** Aircraft with labels, planned paths, blocked zones, and intruders. Changed routes flash when a replan lands. Click to drop an intruder or a storm.
- **Plan toggle.** Fixed routes versus Tower's plan, with a savings counter.
- **Instruction cards.** One per instruction Tower wants issued: the phrase to say, a one-line reason, and urgency. States are pending, spoken, validated, verified, and error. Push-to-talk to speak a card.
- **Alert.** A red card with expected versus heard, error type, confidence, a play button for the clip, and the correction to say.
- **Agent trace.** Expandable steps the resolver took and what it found. This is the Rox demo.
- **Transcript.** Speaker tag, callsign, text, and a confidence bar, with the stock versus tuned toggle.
- **Scoreboard.** Miles and time saved, losses of separation, errors caught, response times. Only numbers we measured.
- **Sliders.** Separation buffer and chaos level: noise, pilot error rate, traffic density.

## Rules

- A judge should understand the screen in five seconds without explanation.
- An alert must be impossible to miss and must never fire for a correct readback in the demo path.
- Everything must work on one laptop in a loud room. Push-to-talk is required.
- Cap the number of cards on screen. The exact cap is an open team decision.
