# 01. Project: what Tower is and what we are trying to win

Updated Saturday Sept 19, 2026. The product grew from a readback monitor into a planner plus a safety net. This file describes the current product.

## One line

Tower plans the best path for every flight, adapts the moment anything changes, and makes sure every instruction is heard and flown correctly.

## The problem

- Planes fly fixed highways in the sky from waypoint to waypoint, not their ideal paths. That wastes time and fuel on every flight.
- When something unexpected happens, such as a storm or an unknown aircraft, controllers reroute everyone by hand and under pressure.
- Every instruction travels over a noisy shared radio. Pilots repeat instructions back, and busy controllers sometimes miss a wrong readback. The plane then flies the wrong altitude or heading. Research puts readback errors at 1 to 2 percent of transmissions.

## What Tower does

1. **Plans.** Every flight gets its ideal path. Conflicts are resolved ahead of time with small changes to timing, speed, or altitude, at the same safety margins used today.
2. **Replans.** The plan is never fixed. Tower repairs it when flights run late, or when a fighter jet, a storm, or an unknown object enters the airspace. It reroutes only the affected flights.
3. **Listens.** New instructions go out by voice. Tower transcribes the noisy radio with a Whisper model we fine-tune on ATC audio.
4. **Validates.** It checks that each pilot's readback means the same thing as the instruction. It alerts on a wrong readback, a missing readback, or the wrong aircraft answering.
5. **Verifies.** It watches the radar to confirm each plane does what it was told. A plane that deviates is one more unexpected change: Tower alerts the controller and replans the others around it.

Most checks are fast and simple. When audio is garbled or two callsigns sound alike, an AI agent investigates. It re-listens, checks which aircraft are on frequency, looks at the radar, then alerts, dismisses with a reason, or flags the case for a human. The controller stays in charge throughout.

## Why the pieces belong together

A tightly optimized plan only works if every instruction is heard and followed exactly. The tighter the plan, the more one wrong readback matters. The listening and checking side is what makes the optimization safe to use. This is the core of the pitch.

## How we talk about separation

- **The claim:** every plane gets its ideal path, and conflicts are solved ahead of time, at the same safety margins.
- **Not the claim:** planes can fly closer. Separation rules cover real uncertainty: position error, wind, wake turbulence, and human reaction time. Anyone from aviation will reject "closer is fine if the math says no collision."
- **The honest version of the closeness idea:** plot efficiency against buffer size against loss-of-separation rate. With validation on, errors are caught early, so the same safety level holds at a smaller buffer. See the safety section of `07-build-spec.md`.
- Separation is a slider in the simulator. At the normal setting it is the product. Dragged to absurd it is a demo scenario.

## What Tower is not

- Not an operational tool, not certified, not connected to any real ATC system. It runs against our simulator.
- Not listening to live real-world radio. See `05-data-and-legal.md`.
- Not a claim that any specific accident would have been prevented.
- Not a once-a-day plan. It replans continuously.

## What we are targeting

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

## Official Hack the North judging criteria

Criteria: WOW factor, technical ability, originality, and design, meaning a user-friendly and intuitive experience.

Explicitly not criteria: practicality and entrepreneurship, visual appeal on its own.

The judging pitch must be a live demo, not slides or a product pitch.

## Lessons from past winners

- Of the twelve 2025 finalists, only one also won a sponsor prize. Winning both takes deliberate design.
- Finalist hooks fit in one line and usually involve the judge or an object on the table.
- Lavoe won Rox's $10K in 2025 with one LLM call and four tools driving a visible UI. Judges score the demo, not the codebase.

## Proposed user flow. The team should confirm this

The user is a controller at one screen. The feeling is calm and quiet. Tower says nothing unless it has something worth saying, and it shows one decision at a time.

1. **Plan view.** Open a scenario. Toggle between today's fixed routes and Tower's plan, and watch a savings counter change.
2. **Live view.** Traffic flows. Each instruction Tower wants issued appears as a card with a one-line reason. The controller holds push-to-talk and says it. The pilot answers by voice. The card turns green once the readback is validated and the radar confirms the plane complied.
3. **Something goes wrong.** A wrong readback turns the card red. It shows what was expected, what was heard, a play button for the audio, and the correction to say. The agent's reasoning is one click away.
4. **Disruption.** The user drops a jet or a storm into the airspace. Affected routes flash and bend, and new cards arrive sorted by urgency.
5. **Review.** A scoreboard shows miles saved, losses of separation, errors caught, and response times.

### Open decisions for the team

- [ ] Is the main user a working controller, a trainee, or a supervisor?
- [ ] Does the controller speak every instruction, or is there an auto-speak mode?
- [ ] What is the maximum number of cards on screen at once?
- [ ] Is the separation slider a product setting or a demo-only toy?

## Demo script, about three minutes

1. **Hook, 15 seconds.** "Planes fly fixed highways, and every instruction goes over a noisy radio. Tower gives every flight its best path and makes sure every instruction is heard and flown correctly."
2. **Efficiency, 30 seconds.** Same traffic on fixed routes, then on Tower's plan. Show miles, time, and conflicts.
3. **Disruption, 30 seconds.** The judge drops a fighter jet into the airspace. Routes bend around it.
4. **Communication, 60 seconds.** The judge plays the controller and speaks an instruction card. An AI pilot reads it back wrong. The card goes red. With Tower off, that plane flies into trouble.
5. **Hard case and the speech model, 30 seconds.** A garbled readback. Show the agent's steps, then stock Whisper against ours on one real ATC clip.
6. **Numbers and close, 15 seconds.** Losses of separation, miles saved, errors caught, word error rate. All measured by us.

Always have a backup video recorded Sunday morning.

## Questions judges will ask, and our answers

- **Is this not already done?** Pieces are active research, not products. HAAWAII reached about 80 percent readback error detection at an 11 percent false alarm rate in trials. Trajectory-based operations is the stated goal of US and European modernization. We built an end-to-end version in a weekend with open models we fine-tuned ourselves.
- **Do planes really fly closer?** No. Same margins, better paths. Show the trade-off curve.
- **What about false alarms?** It is the real problem. Our defences are callsign snapping, the n-best rule, radar verification, and the agent. Show the measured rate.
- **Is the data real?** The speech model trains and is tested on real public ATC recordings. The traffic and the pilots are simulated, and we say which numbers come from which.
- **Is the planner AI?** No. It is a classic search algorithm, and that is the right tool. The language model decides when to interrupt, explains, and phrases instructions. The agent handles messy audio.
- **Why not live radio?** LiveATC's terms forbid it, and Canadian law restricts using intercepted radio.
- **What did you train versus take off the shelf?** Be precise: base models, datasets, hours, what is ours.
- **What did each of you build?** Everyone demos a piece.

## Submission checklist

- [ ] Sponsor prizes selected on Devpost before 2:00 PM EDT Saturday: Rox, Baseten, ElevenLabs, GoDaddy, and Sentry if owned
- [ ] Link to this repo, including design assets
- [ ] Badge ID of every team member, exactly as printed under the QR code
- [ ] Demo video, optional but recommended
- [ ] README lists every third-party model, dataset, and library
