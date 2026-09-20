# TRD 06: Proposed changes for the team to accept or reject

Written Sunday Sept 20. Verdict: the fundamentals are finalist-grade and nothing needs a rewrite. The gap to winning outright is what a judge experiences in the first ninety seconds: the sky sounds alive, the system sees a conflict before it happens, it knows when it is unsure and asks, the judge gets to be the pilot, and every number survives a hard question. Tick yes or no on each line. Costs are rough single-person estimates. Gap analysis behind this is `05-spec-v1-gap.md`.

## A. Make the system visibly intelligent
- [ ] A1. Live conflict cones: per-tick rollouts 90 to 120 s ahead with noise, cone with time-to-conflict above a threshold, before the replan. Half a day
- [ ] A2. Confidence on every instruction from cost margin plus rollout risk, shown on card and strip. Two hours after A1
- [ ] A3. Escalation panel: hold below threshold, reason, proposal, alternative, approve or reject by voice or click, countdown falling back to a hold. Three hours
- [ ] A4. Grounded "why": the agent answers from the planner's reason and cost breakdown and focuses the plane. Two hours
- [ ] A5. Agent drives the screen: focus, follow, show plan, show conflict, set speed. Two hours

## B. Make the frequency alive and let the judge in
- [ ] B1. Un-park Tower's own voice (`set_auto_voice`). One hour plus testing
- [ ] B2. ElevenLabs: one controller voice, distinct pilot voice per airline, pre-generated common phrases. Two hours
- [ ] B3. Pilot mode: judge picks an aircraft, mic becomes its radio, its AI pilot mutes. Half a day with B4 and B5
- [ ] B4. Pilot request parser: descent, climb, deviation, direct, emergency, say again. Two hours
- [ ] B5. Tower answers the pilot: clear or deny with a reason, speak it, check the readback. Three hours
- [ ] B6. Voice round trip under 2 s measured against the deployed model, on the scoreboard. One hour

## C. Make the perturbations physical
- [ ] C1. Drag a plane off its route. Three hours
- [ ] C2. Polygon storms with drift. Half a day. Optional
- [ ] C3. NORDO: silence a radio, treat as non-cooperating. Two hours
- [ ] C4. Timed non-compliance as one click. One hour
- [ ] C5. Delay and fuel delta on every replan versus doing nothing. Two hours

## D. Make the numbers survive a hard question
- [ ] D1. Honest human baseline in the eval: sequential, 5 to 10 s reaction, conservative buffers, attention cap, assumptions on the scoreboard. Three hours
- [ ] D2. Money metrics as distributions: delay minutes, extra miles, fuel, CO2, throughput, instructions per hour, escalations per hour. Two hours
- [ ] D3. Calibration readout from the adsb.lol days. Three hours. Optional
- [ ] D4. Extrapolated impact line with the source on screen. One hour
- [ ] D5. Rule: one efficiency number per slide, never the synthetic 8 percent next to the real 0.5 percent

## E. Planner upgrades
- [ ] E1. Widen the candidate menu into a lattice. One hour
- [ ] E2. Joint search over conflict components of two to four flights. Three hours
- [ ] E3. A* for the conflict component only, heading in the state. Half a day. Only with C1 or C2
- [ ] E4. Safety-efficiency slider that reweights cost and replans. Two hours
- [ ] E5. Per-type performance envelopes. Two hours

## F. Rewind and fork
- [ ] F1. Replayable event log. Two hours. Prerequisite for F2 and F3
- [ ] F2. Scrub bar and fork here. Half a day
- [ ] F3. Split screen with one scoreboard. Three hours. Most impressive, most cuttable

## G. Ship hygiene
- [ ] G1. Merge `kavir/elastic-memory`; decide the resolver's 5 s budget. Thirty minutes
- [ ] G2. Fix the mock-mode "backend out of date" badge. Fifteen minutes
- [ ] G3. Fix the shadowed `STALL_S` in `backend/tower/conform.py`. Fifteen minutes
- [ ] G4. Refresh README status table, TRD checkboxes, `frontend/README.md`. One hour
- [ ] G5. Three-minute script for the current screen, rehearsal with a stranger, backup video. Half a day. Non-negotiable
- [ ] G6. Devpost: prizes, badge IDs, attribution; find out when judging starts. Thirty minutes

## Suggested yes list
A1, A2, A3, B1 to B5, C1, C5, D1, D2, D4, E1, G1 to G6. Everything else is stretch or cut.
