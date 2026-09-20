"""Monte Carlo conflict prediction. See docs/trd/07-monte-carlo-spec.md.

Roll every aircraft forward `horizon_s` under noise, `n` times, and count how often each nearby
pair loses separation (under 5 NM and 1,000 ft at the same sample). Pure numpy, no World imports.

This file starts as a skeleton with the agreed signatures; agent R1 fills in the vectorized body.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from schemas import AircraftState, PlannedPath, Zone

SEP_NM = 5.0
SEP_FT = 1000.0
SHOW_P = 0.05      # display floor
REPLAN_P = 0.30    # replan trigger
PRUNE_NM = 60.0
PRUNE_FT = 4000.0


@dataclass
class Noise:
    compliance_delay_s: float = 15.0   # uniform 0..this before a pending turn or level change begins
    gs_pct: float = 0.03               # ground speed +-
    hdg_sigma_deg: float = 2.0
    vs_pct: float = 0.20               # climb/descent rate +-
    zone_drift_deg: float = 15.0

    @staticmethod
    def none() -> "Noise":
        return Noise(0.0, 0.0, 0.0, 0.0, 0.0)


@dataclass
class PairRisk:
    a: str
    b: str
    p_max: float
    t_first_s: float | None
    eta_s: float
    min_sep_nm_p5: float
    curve: list[tuple[float, float]]
    cpa_xy: tuple[float, float]
    spread_a_nm: float
    spread_b_nm: float


@dataclass
class RiskReport:
    pairs: list[PairRisk] = field(default_factory=list)
    horizon_s: float = 120.0
    n_rollouts: int = 0
    elapsed_ms: float = 0.0
    futures_per_s: float = 0.0
    seed: int = 0

    def pair(self, a: str, b: str) -> PairRisk | None:
        for p in self.pairs:
            if {p.a, p.b} == {a, b}:
                return p
        return None


def predict(states: list[AircraftState], paths: dict[str, PlannedPath], zones: list[Zone],
            now_t: float, *, horizon_s: float = 120.0, dt_s: float = 5.0, n: int = 256,
            seed: int = 0, noise: Noise | None = None) -> RiskReport:
    """Skeleton: returns an empty report. R1 replaces this with the vectorized rollout."""
    return RiskReport(horizon_s=horizon_s, n_rollouts=n, seed=seed)


def margin_factor(best_cost: float | None, runner_up_cost: float | None) -> float:
    """1.0 when the chosen candidate beat the runner-up by 20 percent or more of its cost,
    falling linearly to 0.5 when they tied. 1.0 when there was no runner-up."""
    if best_cost is None or runner_up_cost is None or best_cost <= 0:
        return 1.0
    rel = max(0.0, (runner_up_cost - best_cost) / best_cost)
    return 0.5 + 0.5 * min(1.0, rel / 0.20)


def score_after(before: RiskReport, after: RiskReport, callsigns: set[str]) -> float:
    """Residual p_max on the pairs that involve any of `callsigns`, from the report after a replan."""
    worst = 0.0
    for p in after.pairs:
        if p.a in callsigns or p.b in callsigns:
            worst = max(worst, p.p_max)
    return worst
