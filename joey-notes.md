# Joey's proposal: Tower as the sector that scales

Written Saturday Sept 19. A proposal for the team, not a decision. The numbered docs stay the source of truth until we agree. Where this differs from `docs/07-build-spec.md`, section 6 lists it.

## 1. The pitch

The sky is about to get crowded. Airline traffic is forecast to roughly double over the next two decades, before drones, air taxis, and launches share the same airspace. Air traffic control still runs the way it did in 1970: fixed highways in the sky, a human juggling planes by hand, every instruction over a crackly radio and repeated back. The US is already thousands of controllers short. The system does not scale and does not fail gracefully. When a sector gets too busy, controllers slow everything down, and when they cannot, messages stop landing.

Tower is what the sector looks like when it is built to scale. It plans the best path for every plane, keeps them safely apart, and adapts the moment anything changes: a storm, a fighter jet, a plane that drifts. It speaks each instruction to the pilots, listens to what they say back, and checks that they heard it right and then actually flew it. When something does not add up, garbled audio or two planes with similar names, an agent investigates before deciding whether to interrupt a human. A supervisor watches and can grab the mic at any time.

We proved it on real traffic, then on more traffic than exists yet. We replayed days of real flights over a real sector through Tower. Same planes, same times, X percent fewer miles, and no two planes closer than the real day. Then we doubled the traffic and injected wrong readbacks, late flights, and intruders thousands of times in simulation. Zero losses of separation at twice today's load, Y percent of wrong readbacks caught within Z seconds.

Under the hood: a planner built on search, a speech model fine-tuned on real cockpit radio, a readback checker, and an investigating agent, trained and served on Baseten. Pilot voices from ElevenLabs. The traffic is real, then real times two.

Come talk to it. Load a real afternoon over Chicago, double it, be a pilot, read something back wrong, and watch it catch you.

**Source before saying on stage:** the traffic forecast (IATA and Airbus outlooks) and the controller shortfall (FAA, NATCA). Cite, never from memory.

## 2. The user experience, in plain language

A tilted map of a real sector from above. A microphone icon and a headset icon. That is the interface.

1. **Build the world by talking.** Headset key: "Load Chicago at four in the afternoon, times two." A voice answers and planes slide onto the map with thin route lines and short labels.
2. **See the plan.** Toggle "Today" to "Tower." Lines straighten, a counter ticks: miles saved, zero conflicts.
3. **Tower runs the sector.** It speaks an instruction over the radio. A pilot crackles back through static. The card turns green. On the map the plane starts doing it. A few seconds later a check appears: radar confirms it.
4. **A pilot gets it wrong.** The judge is a pilot. Tower says "turn left heading 270." The judge reads back "250." Red card: expected 270, heard 250, play button, the correction to say. Tower says the correction, the judge reads it back right, green.
5. **Tower off, same mistake.** No red card. The plane quietly turns toward another aircraft and a warning ring lights up. That is today.
6. **A messy one.** Half static. Amber card: "Checking." It expands: "Re-listening: 240 likely, 210 possible. Two Air Canada flights on frequency. Watching the plane 20 seconds." Then "Leveled at 240, all good" and it fades, or red.
7. **Break the sky.** Headset key: "Put a fighter jet through the middle." Routes flash and bend. New instructions go out, sorted by urgency.
8. **Scoreboard.** Miles saved, losses of separation, wrong readbacks made versus caught, seconds to alert, tuned versus stock speech accuracy on real recordings.

Closing line: you talk to the sky, and it listens back.

## 3. Two agents, separated

| Agent | Talks to | Tools | Never does |
|---|---|---|---|
| World builder | The supervisor, conversationally | load real day, spawn aircraft, set time and weather, multiply traffic, add intruder or storm | Issue clearances. Anything a plane does goes over the radio so the readback loop is never bypassed |
| Resolver | Nobody. Wakes on ambiguous readbacks | relisten, active_aircraft, frequency_history, aircraft_state, watch, alert, dismiss, uncertain | Touch the world |

Two push-to-talk keys: headset for the agent, radio for the frequency. Tower should not guess who you are addressing.

**The Rox story is the resolver, not the world builder.** In one line: given a garbled, partial, possibly misattributed transcript of a pilot's reply, decide within five seconds whether a busy human needs to be interrupted, and be right.

| Messy how | Example | Tool |
|---|---|---|
| Noisy | "descend two [static] zero," 240 at 55%, 210 at 40% | `relisten` |
| Conflicting | Transcript says 210, radar shows leveling at 240 | `aircraft_state`, `watch` |
| Incomplete | "left two seven zero" for a two-part clearance, or silence | `frequency_history`, timeout |
| Misattributed | ACA123 and ACA133 both active, garbled reply callsign | `active_aircraft` plus history |

The right evidence differs per case, the model picks based on what the last tool returned, and it can buy time with `watch`. Same shape as Lavoe's 2025 Rox win. The trace must be on screen.

## 4. Evidence: what we can honestly claim

| Claim | Source | Caveat to say out loud |
|---|---|---|
| Fewer miles | Replay real ADS-B entries through Tower, compare to real flown tracks | Wind, weather, closed airspace, runway config are invisible in the data. Some "waste" was a constraint |
| No closer than the real day | Closest-point-of-approach distribution, every pair, real tracks versus Tower's plan | Restrict to en-route above about 10,000 ft. Filter fake close calls from stale altitude and gaps |
| Zero losses of separation under stress | Monte Carlo in our sim: jittered speeds, shifted entries, delayed instructions, injected readback errors at 1 to 2 percent, doubled density | The error rate is an input from the literature. Pilots, winds, performance are simulated. Doubled traffic is synthetic, sampled from real patterns |
| Readback errors caught, false alarms, seconds to alert | Same Monte Carlo runs | Sim audio is cleaner than real radio |
| Speech accuracy | Stock versus tuned Whisper on held-out real recordings | Never on synthetic audio alone |

Three arms in every run: fixed routes with no Tower, Tower's plan with validation off, Tower's plan with validation on. Safety gain is arm 3 versus 2. Efficiency gain is arm 2 or 3 versus 1. One more curve: loss rate and miles saved against traffic density. Fixed routes should degrade as density rises, Tower should not.

Sentence to avoid: "backtested over years and proven safer." Safety is not backtestable: real data has near-zero losses already, and once Tower issues a different instruction the record has no counterfactual. Say "in the replay" every time.

## 5. Things we are not doing, and why

- **Learned dynamics model.** ADS-B shows what planes did under instructions we cannot see. A model learns "follow airways as told," which is the behavior we want to remove, and cannot answer "what if I issue this instruction." Physics is fifty lines and exact. If we learn anything from real data, it is the traffic pattern (entries, rates, types), not the flight physics.
- **Reinforcement learning.** Convergence by Sunday is a coin flip, the planner is search, and Baseten's training product is a job runner, not a rollout system. A working cross-encoder that trains in minutes is a better Baseten story.
- **"World model" as a term.** Invites a hard question. The agent's picture of the airspace is the state store plus two tools.
- **Full 3D as the primary view.** Decisive moments are "how close are these two" and "did it level at 240 or 210," which the eye judges from above. 2.5D on a real map with altitude stems gets most of the effect at a fraction of the risk. 3D as a scripted transition only, if at all.
- **Generative UI, close-call scenario mining, learned traffic generator.** What's-next slide.

## 6. What this changes versus the docs

| Docs today | This proposal | Build cost |
|---|---|---|
| Human controller speaks every instruction | Auto-speak on. Agent issues by voice. Human is supervisor with the mic available | Small. Open decision in docs, already have cards and ElevenLabs |
| "Controller stays in charge" | "Supervisor watches, can take over, gets every low-confidence case" | Positioning only. Needs to be airtight for an aviation judge |
| Judge plays the controller | Judge plays a pilot | None. This was the original v1 demo |
| Traffic is fully synthetic | Starting traffic from real ADS-B for one sector, a few days | Medium. OpenSky research account, pull, clean, project, filter. Timebox to four hours, one person |
| Toronto sector | Whichever sector has good ADS-B coverage, likely US | None |
| Scenario buttons | World-builder agent by voice, loads real days by name, traffic multiplier | Small. Tools are the sim's spawn and apply behind a schema |
| Cursor for flight control | The sector that scales, density thesis | Positioning only |
| Sim-only evaluation | Replay comparison chart plus Monte Carlo plus density curve | Medium. Replay harness is new |

Unchanged: simulator, planner as search, Whisper fine-tune, cross-encoder checker, resolver, radar verification, all hard rules.

## 7. Scope and cut line

- **Tonight, non-negotiable:** docs steps 0 to 4. A plane moves when told, an AI pilot reads back wrong, Tower catches it, the plane visibly goes wrong with Tower off.
- **In parallel, one person, four-hour timebox:** one day of ADS-B for one sector rendering as starting traffic. If it fails, synthetic Chicago and "modeled on" instead of "real."
- **Sunday morning:** replay chart if the data landed, density multiplier, resolver trace on screen. Then rehearse with a stranger as judge and record the backup video.
- **Not this weekend:** close-call mining, generative UI, full 3D, learned traffic generator, RL.

## 8. Decisions needed from the team

- [ ] Adopt supervisor mode and auto-speak, or keep the human controller speaking every instruction
- [ ] Real ADS-B starting traffic: yes with a four-hour timebox, or synthetic only
- [ ] Which sector, decided by coverage
- [ ] 2.5D map or 2D scope
- [ ] Workstream owners. Still four TODOs in `docs/06-plan.md`
- [ ] Who owns the backup video

## 9. Honest verdict

The idea is top-five material at Hack the North and a strong Rox favorite. The risk is entirely scope: the docs had twelve steps and no code this morning, and this proposal adds real data and a replay harness. It wins if we hold the line at steps 0 to 4 plus one real dataset and let everything else be the story of what's next.
