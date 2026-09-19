# frontend/

Next.js live screen. It is what judges look at, and "design" in the Hack the North rubric means intuitive and user-friendly, not pretty.

## Contract

Consume the WebSocket events defined in `../docs/03-architecture.md`. Build against mock events first so you are never blocked on the backend.

## Views

- **Live transcript.** Speaker tag, callsign, text, and a confidence bar per transmission.
- **Open clearances.** One card per aircraft with its pending items and a timeout countdown.
- **Alert feed.** Expected versus heard, error type, confidence, a play button for the clip, and a suggested correction phrase.
- **Resolver trace.** When the agent runs, show each step and what it found. This is the Rox demo.
- **Stock versus tuned toggle.** Same clip, two transcripts side by side.
- **Results.** Word error rate, checker accuracy, false alarm rate, latency. Only numbers we measured.

## Rules

- A judge should understand the screen in five seconds without explanation.
- An alert must be impossible to miss and must never fire for a correct readback in the demo path.
- Everything must work on one laptop in a loud room. Add push-to-talk.
