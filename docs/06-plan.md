# 06. Plan

Fill in names and exact times. Keep this current, since it is the first thing a teammate's Claude session should check before picking up work.

## Fixed deadlines

| When | What |
|---|---|
| Saturday 2:00 PM EDT | Sponsor prizes selected on Devpost. Rox and Baseten at minimum |
| TODO | Hacking ends and Devpost submission closes. Confirm on the event schedule |
| TODO | Judging starts |

## Workstreams

| Stream | Owner | Scope |
|---|---|---|
| A. Speech | TODO | Data prep, Whisper fine-tune on Baseten, evaluation, serving both stock and tuned endpoints |
| B. Pipeline | TODO | Audio ingest, VAD, normalizer, extractors, state machine, WebSocket events |
| C. Checker and agent | TODO | Synthetic readback data, checker fine-tune, resolver agent and tools |
| D. Screen and demo | TODO | Next.js live screen, radio mic filter, demo script, backup video, Devpost write-up |

Streams B and D should agree on the WebSocket event shapes in `03-architecture.md` first, then work in parallel against mock data.

## Milestones

1. **Friday night to early Saturday.** Datasets downloaded. Baseten credits redeemed and training access granted at the booth. First Whisper small run launched before anyone sleeps.
2. **Saturday morning.** Stock Whisper endpoint works end to end: speak into the mic, see a transcript on the screen. Normalizer passes its unit tests.
3. **Saturday 2:00 PM.** Sponsor prizes selected.
4. **Saturday afternoon.** Extractors and state machine working on dataset transcripts. Tuned Whisper small deployed. Launch medium.en. Checker data generated.
5. **Saturday evening. The whole demo path works once, however roughly.** Mic to alert on screen. This is the most important milestone.
6. **Saturday night.** Checker fine-tune. Resolver agent with at least re-listen and active-aircraft tools. Measure all four evaluation metrics.
7. **Sunday morning.** Polish, failure cases, rehearse the three-minute demo with a stand-in judge, record the backup video, finish Devpost.

## Risks and fallbacks

| Risk | Fallback |
|---|---|
| Whisper training does not converge or finishes late | Serve Whisper small instead of medium.en. Last resort is the published fine-tune on Hugging Face, stated openly |
| Real-time latency too high | Smaller ASR model, shorter chunks, skip the checker model when rules are decisive |
| Noisy venue breaks the live demo | Push-to-talk, a close mic, pre-recorded pilot clips one keypress away, and the backup video |
| Speaker classification is flaky | Two mics on stage |
| Checker false alarms too high | Raise thresholds and route more cases to `uncertain`. Show the trade-off honestly |
| Baseten rate limits | Exponential backoff, the event rate-limit form, and the booth |

## Cut list, in order

Drop from the bottom first.

1. Core loop: tuned ASR, extractor, state, rule checker, alert on screen
2. Stock versus tuned toggle and measured word error rate
3. Missing-readback timeout alerts
4. Fine-tuned checker model with measured false alarm rate
5. Resolver agent with visible steps
6. Similar-callsign warnings
7. Resolver verdicts fed back as checker training data
8. Waypoint lookup for garbled fixes
9. ElevenLabs synthetic training audio or spoken corrections
10. Sentry tracing
11. VATSIM live source

## Definition of done for the demo

- A stranger can pick up the mic, read a card, and trigger a correct alert within 3 seconds.
- A correct readback from the same stranger triggers nothing.
- The stock versus tuned difference is visible on one real clip.
- Every number on the results view was measured by us on held-out data.
