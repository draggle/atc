# TRD 07: Monte Carlo conflict prediction, cones, and confidence

Spec for Claude to implement with subagents once approved. Sunday Sept 20, against main at 1c915c8 (396 backend tests).

## What the user sees

Before any conflict exists, a translucent red wedge appears between two aircraft on the map with a label: "LoS 42% in 71 s". The wedge brightens as the probability rises and vanishes when Tower's replan clears it. Every instruction card and strip shows a confidence, for example "confidence 0.91", derived from how clearly the chosen instruction beat the alternatives and how much risk remains after it. The scoreboard gains "conflicts predicted", "resolved before they happened", and "futures simulated per second", the last one an honest measured count. Nothing else on screen changes.

## What it is, in one line

Every tick, roll the whole sky forward 120 seconds a few hundred times with noise, count how often each pair of aircraft would lose separation, replan the moment any pair's risk crosses a threshold, and carry that risk into a confidence on each instruction.

## Design decisions

| Decision | Choice | Why |
|---|---|---|
| Where the math lives | New pure module `backend/planner/risk.py`, numpy only, no World imports | Testable alone, reusable by the eval harness |
| What each aircraft is assumed to intend | Its planned path from `Plan.paths` samples when it has one, else a straight projection of its current heading, level and speed | Matches what the planner believes and what `radar_payload` draws |
| Disturbances per rollout | Compliance delay 0 to 15 s before any pending turn or level change begins; ground speed ±3 percent; heading noise σ 2 degrees; climb and descent rate ±20 percent; zone drift heading ±15 degrees. All seeded | Same family as `eval/montecarlo.py`, sized to a 120 s horizon |
| Sample grid | dt 5 s, horizon 120 s, so 24 samples per rollout | Coarse enough to be cheap, fine enough that 5 NM at 500 kt closing (36 s) spans several samples |
| Rollout count | 256 by default, adaptive: halve if the previous call took over its budget, double back up to 256 when it is under half | Keeps the tick under budget on 150 real flights |
| Pair pruning | Only pairs currently within 60 NM horizontally and 4,000 ft vertically | 150 aircraft is 11,000 pairs; pruning leaves a few hundred |
| Loss of separation test | Same hard floor as everywhere: under 5 NM and under 1,000 ft at the same sample | Hard rule 8 |
| Output per pair | probability curve p(t), p_max, first time p crosses 0.05, expected minimum separation, p5 of minimum separation | Enough for the cone, the trigger, and the confidence |
| Replan trigger | Any pair with p_max at or above 0.30 and first crossing inside the horizon triggers `_replan("risk A/B")` at once, rate-limited to one risk replan per pair per 20 s | Earlier than the 15 s or 60 s periodic check, and fires on risks the exact test misses: a slow turn, a drifting storm, a pilot who acknowledged and has not moved |
| Confidence on a card | `confidence = (1 - p_max_after) * margin_factor`, where p_max_after is the residual risk on the pairs the card touches, re-scored after the replan, and margin_factor is 1 when the chosen candidate beat the runner-up by more than 20 percent of its cost, scaling linearly down to 0.5 when they tied. Clamped to [0.05, 0.99] | Cheap, explainable, and both terms already exist or are one line away in `plan.py` |
| Escalation | Not in this spec. Confidence is shown, not acted on. Gating below a threshold is TRD 06 item A3 | Keep the change small and shippable today |
| Cadence and budget | Called from `World.tick` after `sim.step`, every tick at 1x, every 2 s of sim time above 1x. Budget 25 ms at 12 aircraft, 150 ms at 100. Measured and reported | The clock loop cannot stall; the adaptive count enforces it |
| Event | New `risk` event at most once per second: `{pairs: [{a, b, p_max, t_first_s, eta_s, min_sep_nm_p5, curve: [[t, p], ...]}], horizon_s, n_rollouts, elapsed_ms, futures_per_s}`; only pairs with p_max at or above 0.05 | One new event, three frontend edits per the convention |
| Cone geometry | For each pair above threshold: a wedge from each aircraft's current position along its intended track to the point of closest approach at eta, half-width equal to the p5 to p95 lateral spread of that aircraft's rollouts at eta. Two wedges per pair, one polygon layer, opacity and red intensity scaled by p_max, label at the closest-approach midpoint | Shows uncertainty as width, not as a fixed shape |
| Honest wording | Scoreboard says "futures simulated per second: N", from `n_rollouts × aircraft / elapsed`, measured that tick. Slide says "a few hundred," never "thousands" unless the counter says so | Hard rule 6 |

## Backend

### `backend/planner/risk.py` (new)

```python
@dataclass
class PairRisk:
    a: str; b: str
    p_max: float
    t_first_s: float | None      # first sample where p >= 0.05
    eta_s: float                 # sample of maximum p
    min_sep_nm_p5: float
    curve: list[tuple[float, float]]
    cpa_xy: tuple[float, float]  # mean closest-approach midpoint, flat NM
    spread_a_nm: float; spread_b_nm: float  # lateral p5..p95 at eta

@dataclass
class RiskReport:
    pairs: list[PairRisk]        # sorted by p_max desc, only p_max >= 0.05
    horizon_s: float; n_rollouts: int; elapsed_ms: float; futures_per_s: float
    seed: int

def predict(states: list[AircraftState], paths: dict[str, PlannedPath], zones: list[Zone],
            now_t: float, *, horizon_s=120.0, dt_s=5.0, n=256, seed=0,
            noise: Noise = Noise()) -> RiskReport
```

Vectorized: positions array of shape (n, A, T, 3). Intended track per aircraft built from the plan's samples resampled to the 5 s grid, or from a straight projection. Disturbances applied per rollout. Pairs pruned by current separation, then distances computed on the pruned index pairs only. `Noise` is a dataclass with the five disturbance magnitudes so tests and the eval can zero them.

Also `score_after(report_before, report_after, callsigns) -> float` returning the residual p_max on the pairs involving those callsigns, and `margin_factor(best_cost, runner_up_cost) -> float`.

### `backend/planner/plan.py`

Expose the runner-up cost per flight: `PlannedPath` gains `runner_up_cost: float | None`, set from the second candidate that cleared, if any. One line where `_Result` is built.

### `backend/schemas.py`

- `InstructionCard.confidence: float | None = None` and `risk_after: float | None = None`
- `Scoreboard.conflicts_predicted: int = 0`, `conflicts_resolved: int = 0`, `futures_per_s: float | None = None`, `cones_now: int = 0`
- `EventType` gains `"risk"`

### `backend/world.py`

- After `sim.step` in `tick`: build `paths` from `self.plan`, call `risk.predict`, keep `self.risk` and the last report per pair.
- Trigger: for each pair with p_max at or above 0.30 not replanned in the last 20 s, call `_replan(f"risk {a}/{b}")` once for the whole batch, then re-run `predict` on the new plan for the affected pairs and set `confidence` and `risk_after` on the new cards before they are emitted.
- Counters: `conflicts_predicted` increments when a pair first crosses 0.30; `conflicts_resolved` increments when that pair later drops below 0.05 without a loss of separation.
- Emit `risk` at most once per second with the report; emit nothing when there are no pairs above 0.05 and the previous report also had none.
- Adaptive `n`: read `report.elapsed_ms` against the budget and adjust for the next call.

### `backend/eval/montecarlo.py`

Optional, if time: record `conflicts_predicted` and `conflicts_resolved` per arm from the same module so the scoreboard and the batch report agree.

## Frontend

Three edits per the convention: `EVENT_TYPES` in `lib/ws.ts`, `EventMap` and a `RiskReport` type in `lib/types.ts`, a `risk` case in `lib/store.tsx` that keeps the latest report and a map of pair to first-seen time for a fade-in.

- `components/MapView.tsx`: a `PolygonLayer` with id `risk-cones` drawn under `aircraft` and above `tower-plan`. Two wedges per pair built in lon/lat from `cpa_xy`, the two aircraft positions, and the spreads via the existing NM to lat/lon helper. Fill red with alpha 0.10 + 0.35 × p_max, thin outline. A `TextLayer` `risk-labels` at the CPA midpoint: "LoS 42% · 71 s". Legend gains "red wedge = predicted conflict".
- `components/InstructionCards.tsx` and `FlightStrip.tsx`: show `confidence` as a small bar and number when present; the strip shows the aircraft's highest current pair risk.
- `components/ScoreboardPanel.tsx`: three new tiles, predicted, resolved, futures per second.
- `lib/mock.ts`: emit a `risk` event during the scripted conflict so mock mode exercises the cone.

## Tests

`backend/tests/test_risk.py`:
- Two aircraft head-on at the same level 40 NM apart at 450 kt: p_max above 0.95, t_first within 10 s of the analytic closing time, cpa near the midpoint.
- Same pair 3,000 ft apart: p_max under 0.05.
- Two parallel aircraft 8 NM apart: p_max under 0.10 with default noise, 0 with zero noise.
- Determinism: same seed, identical report.
- Pruning: a pair 150 NM apart is not in the report.
- Performance: 20 aircraft, 256 rollouts under 50 ms; 100 aircraft under 200 ms (skip on CI-like slowness with a clear message).
- `margin_factor` and `score_after` unit cases.

`backend/tests/test_world_risk.py`:
- Load `demo`, force a pilot to fly a wrong heading toward another aircraft, tick until the pair crosses 0.30, assert a `risk` event was emitted, a replan with trigger starting "risk" happened within 20 s of sim time, the new card carries a confidence in [0.05, 0.99], and `conflicts_predicted` incremented; keep ticking and assert `conflicts_resolved` incremented with no loss of separation.
- No aircraft near each other: no `risk` event is emitted twice in a row with empty pairs.
- Adaptive count: with an injected slow `predict`, `n` halves.

Frontend: `npm run build` clean with `NEXT_DIST_DIR=.next-verify`; mock mode renders a wedge (checked with Playwright).

## Docs

- `docs/08-ws-protocol.md`: the `risk` event and the new card and scoreboard fields.
- `docs/09-overnight-findings.md`: measured elapsed and futures per second at 12, 80, and 150 aircraft.
- `docs/10-roadmap.md`: a phase 6f entry with what landed and the numbers.
- `CLAUDE.md` conventions: one line, "risk thresholds live in `planner/risk.py`; the replan trigger is 0.30 and the display floor 0.05."

## Work split, four subagents in parallel then one integration pass

| Agent | Owns | Depends on |
|---|---|---|
| R1 backend risk module | `planner/risk.py`, `tests/test_risk.py`, the `runner_up_cost` line in `plan.py` | Nothing. Builds against the signature above |
| R2 world integration | `world.py` tick, trigger, counters, card confidence, `risk` event, `schemas.py` fields, `tests/test_world_risk.py` | R1's signature only; uses a stub until R1 lands, then swaps |
| R3 frontend | the three edits, `MapView` cones and labels, card and strip confidence, scoreboard tiles, mock | The event shape above |
| R4 docs and eval | protocol doc, findings, roadmap entry, CLAUDE.md line, optional eval counters | R1 and R2 results for the numbers |
| Integration (me) | Run the full suite, boot backend and screen, drop a storm on the demo scenario, watch a cone appear and clear in Playwright, measure futures per second at 12 and 150 aircraft, commit and push | All four |

## Out of scope

Escalation gating on confidence (A3), MCTS over actions, per-type performance envelopes, rollouts inside the eval arms beyond the optional counters, polygon storms.

## Risks

- 150-flight Europe scenario blows the budget: the adaptive count handles it; the floor is 32 rollouts, and the report says so on the scoreboard.
- Cones flicker at the 0.05 floor: hysteresis, show at 0.05, hide below 0.02, and a 1 s fade in the store.
- A risk replan fights the periodic replan: both go through `_replan`, which already coalesces; the per-pair 20 s rate limit prevents thrash.
- Confidence looks arbitrary to a judge: the strip shows both terms, "risk after 0.03, margin 1.0", so the number is explainable on demand.
