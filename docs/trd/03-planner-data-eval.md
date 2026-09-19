# TRD 03: Planner, real data, and evaluation

For the teammate who owns the simulator, the planner, the safety numbers, and the real-traffic story. Read `README.md`, `docs/07-build-spec.md` sections 4, 5, 6, `joey-notes.md` sections 3, 8, 12, 14, and `docs/09-overnight-findings.md` Planner and Monte Carlo sections first. Code: `backend/sim`, `backend/planner`, `backend/eval`, `backend/scenarios`.

## Where things stand

- Simulator, planner, replan, emergency layer, cards, and a three-arm Monte Carlo all work and are tested. Demo scenario plans in 14 ms with zero conflicts; the fixed-route baseline has two conflicting pairs.
- Measured, demo scenario, 20 runs, 2 percent readback errors: fixed 0.34 LoS per flight hour, Tower 0, 7.8 to 8.3 percent fewer miles. Dense at 5 percent errors: fixed 154, Tower without validation 1, with validation 0.
- `python -m eval.run_eval --scenario <name> --runs N --error-rate E --density D --buffer B` prints the table and writes JSON to `backend/eval/out/`.
- The world-builder agent can `multiply_traffic(factor)` and `spawn_flight`; both re-plan.

## Tasks, in order

### 1. Fix the miles-saved counter after replans, 30 minutes

`backend/world.py` computes `baseline.total_distance_nm - plan.total_distance_nm`, but after a replan the plan holds remaining distance while the baseline holds the full route. Options: track flown distance per aircraft in the sim (`Aircraft.distance_nm` exists) and report flown plus remaining against the baseline's full route; or freeze the counter at the initial plan and label it "planned savings". Pick one, make the scoreboard honest, add a test.

### 2. The density curve, 1 hour

The pitch's one-picture thesis: LoS rate and miles saved against traffic density, fixed routes versus Tower, validation on and off. Write `backend/eval/sweep.py` that runs `montecarlo.run` over density in {1.0, 1.5, 2.0, 2.5, 3.0} and buffer in {1, 3, 5} on `dense`, 10 runs each, and writes one CSV plus a PNG (matplotlib is fine) to `backend/eval/out/`. Expect fixed routes to fall apart with density and Tower to hold at zero until the planner starts failing to find candidates; report where that happens and what `Plan.conflicts` says. Give Joey the PNG for the honesty slide.

### 3. Entry-delay cards, 30 minutes

The planner delays not-yet-airborne flights by 1 to 3 minutes and produces no card. Either emit an `InstructionCard` with a `route` item and action `hold` and a phrase like "Delta seven eight nine, hold at BUNTS, expect onward clearance in two minutes", or emit a `plan_update` reason string the screen can show. The sim already applies the delay to spawn time in eval; the live app does not apply it at all. Make live match eval.

### 4. Real ADS-B starting traffic, four-hour hard timebox

This is the "real traffic" sentence in the pitch. If the timebox runs out, stop, and the world builder says "modeled on" instead of "real".

1. OpenSky Network: create an account, request historical access if needed, or use the REST `states/all` snapshots with a bounding box over a well-covered sector (Chicago or Atlanta en-route, roughly a 200 NM box, above 10,000 ft). Read their terms first; add to `README.md` attribution and `docs/05-data-and-legal.md`.
2. Write `backend/sim/adsb.py`: fetch or load a snapshot, project lat/lon to the flat sector frame centred on the box (equirectangular is fine at this scale), map each aircraft to a `FlightSpec` with entry position, heading, altitude, ground speed, and a synthetic exit waypoint on the far edge along its track. Real callsigns from the ADS-B `callsign` field. Save as a scenario YAML under `backend/scenarios/real_<sector>_<yyyymmdd_hhmm>.yaml`.
3. Add `load_real(sector, when)` to the world-builder tools so "load Chicago at four pm" works. Keyword parser: match "chicago|atlanta" and a time.
4. Run the three-arm Monte Carlo on the real scenario. That is the "same traffic, fewer miles" number. Caveat wind and constraints on the slide.
5. If time remains: closest-point-of-approach histogram on the real snapshot's own tracks over 15 minutes versus Tower's plan. See `joey-notes.md` section 12 for the caveats that must travel with it.

### 5. Planner gaps to know about

- Heading commands hold forever; aircraft leave via the sector edge. Fine for the demo, wrong for a long run. A follow-up card (`followup_cards`) already turns doglegs back toward the exit; extend it to headings that have drifted past the exit bearing.
- No winds, no aircraft performance envelopes. Say so on stage.
- The intruder prediction is straight-line with a growing buffer. A turning intruder will surprise it; the emergency layer covers the last two minutes.
- `Plan.paths` excludes intruders; the screen draws `Disruption.predicted_path` instead.
- The buffer slider only affects future replans. Either trigger a replan on change or say so in the UI.

## Numbers you owe the scoreboard

- LoS per flight hour and closest-approach p5/p50 for the three arms on demo, dense, intruder, and the real scenario if it exists.
- Miles saved, corrected for the replan bug.
- The density sweep PNG.
- Seconds from intruder appearing to a conflict-free plan (already in the eval JSON as replans; surface it).
