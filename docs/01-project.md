# 01. Project: what Tower is and what we are trying to win

## One line

A second set of ears on the frequency that never misses a readback.

## The problem

1. A controller issues a clearance: "Air Canada 123, descend and maintain flight level two four zero."
2. The pilot must read back the safety-critical parts: "Descend flight level two four zero, Air Canada 123."
3. The controller listens for a mismatch. This is called hearback.
4. Sometimes the controller misses it. They are working ten aircraft, the readback was clipped, or an accent made "two four" sound like "two one." The pilot then flies the wrong altitude. That is a hearback error, and it is a known cause of incidents.

Research puts readback errors at roughly 1 to 2 percent of transmissions. More background is in `02-domain.md`.

## What Tower does

- Transcribes both sides of the radio with a Whisper model fine-tuned on ATC audio.
- Turns each controller transmission into a structured clearance and opens it on a per-aircraft notepad.
- Turns each pilot transmission into a structured readback and compares it.
- Alerts on a mismatch, showing expected value, heard value, confidence, and the audio clip.
- Alerts when a clearance gets no readback before a timeout.
- Hands ambiguous cases to a resolver agent that gathers more evidence before deciding.

## What Tower is not

- Not an operational tool, not certified, not connected to any real ATC system.
- Not listening to live real-world radio. See `05-data-and-legal.md`.
- Not a claim that any specific accident would have been prevented.

## What we are targeting

| Target | Winners | What they want | How we show it |
|---|---|---|---|
| Rox: Best AI Agent | 2, $10K and $2K | LLM agents operating on messy, noisy, incomplete, conflicting real-world data and taking meaningful actions. Judged on technical complexity, creativity, handling messiness, practical utility. | Radio static, accents, clipped transmissions, two speakers on one channel, similar callsigns, paraphrased readbacks. The resolver agent visibly gathers evidence and commits to an action with a reason. |
| Baseten: Best Use of Baseten | 2, grand prize is an SF trip plus final-round interviews | Creative, real use of their platform. Inference APIs, H100 training, or Baseten Switch. | Two models trained on their H100s and served through their API. Before and after chart. Latency and cost of small tuned models versus a large prompted one. |
| Hack the North finalist | 12, no ranking | WOW factor, technical ability, originality, design. | A judge plays the pilot and gets caught live. |

Possible extras if they fall out naturally. Do not contort the project for them.

- **MLH Best Use of ElevenLabs:** synthetic pilot voices for training augmentation, or a spoken correction phrase on alert.
- **Sentry:** tracing plus AI agent monitoring across the pipeline. Needs at least two products beyond error monitoring.

**Deadline that matters:** every sponsor prize we want must be selected on Devpost before 2:00 PM EDT Saturday.

## Official Hack the North judging criteria

Criteria: WOW factor, technical ability, originality, design meaning a user-friendly and intuitive experience.

Explicitly not criteria: practicality and entrepreneurship, visual appeal on its own.

The judging pitch must be a live demo, not slides or a product pitch.

## Lessons from past winners

- Of the twelve 2025 finalists, only one also won a sponsor prize. Winning both takes deliberate design.
- Finalist hooks fit in one line and usually involve the judge or an object on the table.
- Lavoe won Rox's $10K in 2025 with one LLM call and four tools driving a visible UI. Judges score the demo, not the codebase. The agent acting on a real, visible surface is what sells.

## Demo script, about three minutes

1. **Hook, 15 seconds.** "Pilots repeat every instruction back. Controllers are supposed to catch mistakes. Sometimes they do not. Tower always does."
2. **Stock versus ours, 30 seconds.** Play one real ATC test clip. Show stock Whisper's transcript next to our fine-tuned one.
3. **Judge plays the pilot, 60 seconds.** Hand them the mic with the radio filter and a card: "Read this back, but say the wrong altitude." The alert fires with expected, heard, and the clip.
4. **Hard case, 45 seconds.** A garbled readback with a similar callsign on frequency. Show the resolver's steps on screen: re-listen, check active aircraft, check history, decide.
5. **Numbers, 20 seconds.** Word error rate before and after, checker accuracy and false alarm rate, end-to-end latency. All measured by us.
6. **Close, 10 seconds.** What we trained this weekend, on what, and what is next.

Always have a backup video recorded Sunday morning.

## Questions judges will ask, and our answers

- **Is this not already done?** It is an active research area, not a product in towers. The European HAAWAII project reached over 80 percent detection with under 20 percent false alarms in lab tests. We built a live end-to-end version in a weekend with open models we fine-tuned ourselves.
- **What about false alarms?** That is the real problem, and it is why the resolver agent exists. Show the confidence scores and the measured false alarm rate.
- **Where did the data come from?** Public research datasets on Hugging Face, plus synthetic readback pairs we generated. Real errors are too rare to collect, and the leading research project also trained on synthetic errors.
- **Why not live radio?** LiveATC's terms forbid it, and Canadian law restricts using intercepted radio. We designed the demo around a human pilot instead.
- **What did you train versus take off the shelf?** Be precise. Name the base models, the datasets, the hours of training, and what is ours.
- **What did each of you build?** Everyone demos a piece.

## Submission checklist

- [ ] Sponsor prizes selected on Devpost before 2:00 PM EDT Saturday
- [ ] Link to this repo, including design assets
- [ ] Badge ID of every team member, exactly as printed under the QR code
- [ ] Demo video, optional but recommended
- [ ] README lists every third-party model, dataset, and library
