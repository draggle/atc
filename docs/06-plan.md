# 06. Plan

Updated Saturday Sept 19, 2026. Fill in names and exact times. A teammate's Claude session should check this file before picking up work. The detailed build order is in section 12 of `07-build-spec.md`.

## Fixed deadlines

| When | What |
|---|---|
| Saturday 2:00 PM EDT | Sponsor prizes selected on Devpost: Rox, Baseten, ElevenLabs, GoDaddy, and Sentry if someone owns it |
| TODO | Hacking ends and Devpost submission closes. Confirm on the event schedule |
| TODO | Judging starts |

## Workstreams

| Stream | Owner | Scope |
|---|---|---|
| A. Simulator and planner | TODO | Simulator, conflict test, planner, replanning, intruders, batch evaluation of efficiency and safety |
| B. Tower core | TODO | Audio ingest, VAD, normalizer, callsign snapping, parser, state machine, checker rules, radar verification, resolver agent |
| C. Models and evaluation | TODO | Whisper fine-tune on Baseten, checker cross-encoder, serving, word error rate and detection metrics |
| D. Screen and AI pilots | TODO | Radar view, instruction cards, alerts, agent trace, AI pilot voices with radio effect, demo script, backup video, Devpost |

All four agree the shared schemas and WebSocket events first, then build against mocks.

## Milestones

1. **Now.** Interfaces agreed and mocked. Datasets downloading. Baseten credits redeemed, training access granted at the booth, first Whisper small run launched.
2. **Saturday morning.** Simulator steps five aircraft and the radar draws them. Mic to stock Whisper to transcript on screen.
3. **Saturday 2:00 PM.** Sponsor prizes selected.
4. **Saturday afternoon.** A spoken clearance moves a plane. One AI pilot reads back by voice and the first alert fires. Planner produces a conflict-free plan for one scenario.
5. **Saturday evening. The whole demo path works once, however roughly.** Plan, speak, readback, alert, plane deviates with Tower off. This is the most important milestone.
6. **Saturday night.** Tuned Whisper deployed. Checker trained on simulator data. Replanning around an intruder. Radar verification.
7. **Sunday early morning.** Resolver agent with its trace. Batch evaluation for every number on the scoreboard.
8. **Sunday morning.** Polish, rehearse the three-minute demo with a stand-in judge, record the backup video, finish Devpost.

## Risks and fallbacks

| Risk | Fallback |
|---|---|
| Scope is now large | The cut list below. Steps 0 to 4 of the build order are a complete demo alone |
| Whisper training does not converge or finishes late | Serve Whisper small. Last resort is the published fine-tune on Hugging Face, stated openly |
| Serving a custom Whisper on Baseten takes too long | Ask the booth early. Fall back to their hosted Whisper for tier 1 and keep our fine-tune for the comparison |
| Planner finds no solution in a dense scenario | Widen the replan set, then relax cost limits, then fall back to the emergency layer. Never drop below the separation minimum |
| Real-time latency too high | Smaller speech model, grammar parser only, skip the checker model when rules are decisive |
| Noisy venue breaks the live demo | Push-to-talk, a close mic, pre-recorded controller clips one keypress away, and the backup video |
| Checker false alarms too high | Raise thresholds and route more cases to uncertain or to radar watching. Show the trade-off honestly |
| Baseten rate limits | Exponential backoff, the event rate-limit form, and the booth |

## Cut list, in order

Drop from the bottom first.

1. Simulator with aircraft on routes and a radar view
2. Spoken clearance moves a plane: mic, speech, parser, state machine
3. One AI pilot reading back by voice with injected errors, and rule-based alerts
4. Tuned Whisper with the stock comparison and measured word error rate
5. Planner producing a conflict-free plan, with the fixed-route baseline comparison
6. Replanning around an intruder or storm
7. Radar verification and the watch tool
8. Checker cross-encoder with measured detection and false alarm rates
9. Resolver agent with visible steps
10. Monte Carlo safety evaluation and the trade-off curve
11. Separation slider and absurd scenarios
12. Self-running data engine with an AI controller
13. Sentry tracing, insights view, waypoint lookup, VATSIM

## Definition of done for the demo

- A stranger can drop an intruder into the airspace and see a conflict-free replan within a few seconds.
- A stranger can speak an instruction card, hear a wrong readback, and see a correct alert within 3 seconds.
- A correct readback triggers nothing.
- The stock versus tuned speech difference is visible on one real clip.
- Every number on the scoreboard was measured by us, and we can say how.
