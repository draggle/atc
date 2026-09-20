"""Monte Carlo conflict prediction. See docs/trd/07-monte-carlo-spec.md.

Roll every aircraft forward `horizon_s` under noise, `n` times, and count how often each nearby
pair loses separation (under 5 NM and 1,000 ft at the same sample). Pure numpy, no World imports.

Each rollout draws, per aircraft, a compliance delay (it keeps its current heading and level
that long before following its intended track), a ground speed factor, a constant bearing error
that grows along track, and a vertical rate factor. The intended track is the plan's samples
resampled to the grid, or a straight projection when there is no plan. Zones are drawn for but
do not yet move anyone; their drift is reserved for a later pass.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from schemas import AircraftState, PlannedPath, Zone

SEP_NM = 5.0
SEP_FT = 1000.0
SHOW_P = 0.05      # display floor
REPLAN_P = 0.30    # replan trigger
PRUNE_NM = 60.0
PRUNE_FT = 4000.0
CLIMB_FPM = 1500.0  # straight projection without a plan; keep in step with sim/engine.py


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


def _intended_track(st: AircraftState, path: PlannedPath | None, now_t: float, tau: np.ndarray) -> np.ndarray:
    """(Te, 3) intended (x, y, alt) at nominal seconds `tau` from now.

    From the plan's samples when there are at least two ahead of now, anchored to where the
    aircraft actually is and carried straight along its last heading past the end. Otherwise
    a straight projection of the current heading and speed, levelling at target_alt_ft.
    """
    v = st.gs_kt / 3600.0
    if path is not None:
        s = np.asarray(path.samples, dtype=float).reshape(-1, 4)
        s = s[s[:, 0] >= now_t - 1e-6]
        if s.shape[0] and s[0, 0] > now_t + 1e-6:
            s = np.vstack([[now_t, st.x_nm, st.y_nm, st.alt_ft], s])
        if s.shape[0] >= 2 and s[-1, 0] > s[0, 0]:
            ts = s[:, 0] - now_t
            x = np.interp(tau, ts, s[:, 1])
            y = np.interp(tau, ts, s[:, 2])
            alt = np.interp(tau, ts, s[:, 3])
            beyond = tau > ts[-1]
            if beyond.any():
                dx, dy, dts = s[-1, 1] - s[-2, 1], s[-1, 2] - s[-2, 2], s[-1, 0] - s[-2, 0]
                seg = math.hypot(dx, dy)
                if seg > 1e-9 and dts > 0:
                    vx, vy = dx / seg * (seg / dts), dy / seg * (seg / dts)
                    x[beyond] = s[-1, 1] + vx * (tau[beyond] - ts[-1])
                    y[beyond] = s[-1, 2] + vy * (tau[beyond] - ts[-1])
            # Anchor to the radar position: a plane off its plan is still where it is.
            x += st.x_nm - x[0]
            y += st.y_nm - y[0]
            alt += st.alt_ft - alt[0]
            return np.column_stack([x, y, alt])
    r = math.radians(st.hdg_deg)
    x = st.x_nm + math.sin(r) * v * tau
    y = st.y_nm + math.cos(r) * v * tau
    d_alt = st.target_alt_ft - st.alt_ft
    alt = st.alt_ft + np.sign(d_alt) * np.minimum(CLIMB_FPM / 60.0 * tau, abs(d_alt))
    return np.column_stack([x, y, alt])


def _gather(track: np.ndarray, tau_nom: np.ndarray, dt_s: float) -> np.ndarray:
    """Linear interpolation of `track` (A, Te, C) at nominal times `tau_nom` (n, A, T) -> (n, A, T, C)."""
    A, Te = track.shape[0], track.shape[1]
    idx = np.clip(tau_nom / dt_s, 0.0, Te - 1 - 1e-9)
    lo = idx.astype(np.intp)
    fr = (idx - lo)[..., None]
    ai = np.arange(A, dtype=np.intp)[None, :, None]
    return track[ai, lo] * (1.0 - fr) + track[ai, lo + 1] * fr


def rollouts(states: list[AircraftState], paths: dict[str, PlannedPath], zones: list[Zone], now_t: float,
             *, horizon_s: float, dt_s: float, n: int, rng: np.random.Generator, noise: Noise) -> np.ndarray:
    """Positions (n, A, T, 3) of every aircraft under `n` noisy futures on the grid tau = k * dt_s."""
    A = len(states)
    T = int(round(horizon_s / dt_s)) + 1
    tau = np.arange(T) * dt_s
    # The nominal track is read at up to tau * (1 + gs_pct) past the horizon: sample it longer.
    Te = int(math.ceil(horizon_s * (1.0 + noise.gs_pct + noise.vs_pct) / dt_s)) + 3
    tau_e = np.arange(Te) * dt_s
    track = np.stack([_intended_track(st, paths.get(st.callsign), now_t, tau_e) for st in states]) \
        if A else np.zeros((0, Te, 3))
    hdg = np.radians([st.hdg_deg for st in states])
    u = np.column_stack([np.sin(hdg), np.cos(hdg)])                      # (A, 2)
    v = np.array([st.gs_kt for st in states]) / 3600.0                   # (A,)
    p0 = np.array([[st.x_nm, st.y_nm] for st in states]).reshape(A, 2)
    level = np.array([abs(st.target_alt_ft - st.alt_ft) < 50.0 for st in states]).reshape(A)

    # Draws, in a fixed order so a seed pins the report.
    delay = rng.uniform(0.0, noise.compliance_delay_s, size=(n, A))
    f_gs = 1.0 + rng.uniform(-noise.gs_pct, noise.gs_pct, size=(n, A))
    err = np.radians(rng.normal(0.0, noise.hdg_sigma_deg, size=(n, A))) if noise.hdg_sigma_deg > 0 \
        else np.zeros((n, A))
    f_vs = 1.0 + rng.uniform(-noise.vs_pct, noise.vs_pct, size=(n, A))
    rng.uniform(-noise.zone_drift_deg, noise.zone_drift_deg, size=(n, len(zones)))  # reserved: zones do not move aircraft yet

    tau3 = tau[None, None, :]
    d3, f3 = delay[..., None], f_gs[..., None]
    # Horizontal: straight on the current heading until the delay is up, then the intended track
    # from its start, shifted in time and carried by the distance already flown straight.
    tau_h = np.maximum(tau3 - d3, 0.0) * f3
    xy = _gather(track[:, :, :2], tau_h, dt_s)                           # (n, A, T, 2)
    flown = (v[None, :, None] * f3 * np.minimum(tau3, d3))[..., None]    # (n, A, T, 1)
    xy = xy + u[None, :, None, :] * flown
    # Vertical: a level change that has not started waits for the delay; one under way does not.
    d_v = d3 * level[None, :, None]
    tau_v = np.maximum(tau3 - d_v, 0.0) * f3 * f_vs[..., None]
    alt = _gather(track[:, :, 2:], tau_v, dt_s)                          # (n, A, T, 1)
    # Bearing error: rotate the displacement from the start point, so it grows along track.
    rel = xy - p0[None, :, None, :]
    c, s = np.cos(err)[..., None], np.sin(err)[..., None]
    rx = rel[..., 0] * c - rel[..., 1] * s
    ry = rel[..., 0] * s + rel[..., 1] * c
    out = np.empty((n, A, T, 3))
    out[..., 0] = p0[None, :, None, 0] + rx
    out[..., 1] = p0[None, :, None, 1] + ry
    out[..., 2] = alt[..., 0]
    return out


def predict(states: list[AircraftState], paths: dict[str, PlannedPath], zones: list[Zone],
            now_t: float, *, horizon_s: float = 120.0, dt_s: float = 5.0, n: int = 256,
            seed: int = 0, noise: Noise | None = None) -> RiskReport:
    """Monte Carlo loss-of-separation risk for every nearby pair. See module docstring."""
    t_start = time.perf_counter()
    noise = Noise() if noise is None else noise
    rng = np.random.default_rng(seed)
    A = len(states)
    report = RiskReport(horizon_s=horizon_s, n_rollouts=n, seed=seed)
    if A < 2 or n < 1:
        report.elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        return report

    # Prune on the current picture before rolling anything forward.
    px = np.array([st.x_nm for st in states])
    py = np.array([st.y_nm for st in states])
    pa = np.array([st.alt_ft for st in states])
    ia, ib = np.triu_indices(A, 1)
    near = (np.hypot(px[ia] - px[ib], py[ia] - py[ib]) < PRUNE_NM) & (np.abs(pa[ia] - pa[ib]) < PRUNE_FT)
    ia, ib = ia[near], ib[near]
    if ia.size == 0:
        report.elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        report.futures_per_s = n * A / max(report.elapsed_ms / 1000.0, 1e-9)
        return report

    pos = rollouts(states, paths, zones, now_t, horizon_s=horizon_s, dt_s=dt_s, n=n, rng=rng, noise=noise)
    T = pos.shape[2]
    tau = np.arange(T) * dt_s
    pa_, pb_ = pos[:, ia], pos[:, ib]                                    # (n, P, T, 3)
    dh = np.hypot(pa_[..., 0] - pb_[..., 0], pa_[..., 1] - pb_[..., 1])  # (n, P, T)
    same_level = np.abs(pa_[..., 2] - pb_[..., 2]) < SEP_FT
    los = (dh < SEP_NM) & same_level
    p_t = los.mean(axis=0)                                               # (P, T)
    p_max = p_t.max(axis=1)
    keep = np.where(p_max >= SHOW_P)[0]
    order = keep[np.argsort(-p_max[keep], kind="stable")]

    pairs: list[PairRisk] = []
    for k in order:
        curve = p_t[k]
        k_eta = int(np.argmax(curve))
        above = np.where(curve >= SHOW_P)[0]
        # Per-rollout minimum separation, counting only samples inside the vertical band.
        d_band = np.where(same_level[:, k], dh[:, k], np.inf)
        min_sep = d_band.min(axis=1)
        p5 = float(np.percentile(min_sep, 5, method="lower"))
        # Closest approach: the midpoint at each rollout's tightest sample, averaged.
        j = np.argmin(dh[:, k], axis=1)
        rows = np.arange(pos.shape[0])
        mid = 0.5 * (pa_[rows, k, j, :2] + pb_[rows, k, j, :2])
        cpa = mid.mean(axis=0)
        pairs.append(PairRisk(
            a=states[ia[k]].callsign, b=states[ib[k]].callsign, p_max=float(p_max[k]),
            t_first_s=float(tau[above[0]]) if above.size else None, eta_s=float(tau[k_eta]),
            min_sep_nm_p5=p5, curve=[(float(t), float(p)) for t, p in zip(tau, curve)],
            cpa_xy=(float(cpa[0]), float(cpa[1])),
            spread_a_nm=_lateral_spread(pos[:, ia[k]], k_eta), spread_b_nm=_lateral_spread(pos[:, ib[k]], k_eta),
        ))
    report.pairs = pairs
    report.elapsed_ms = (time.perf_counter() - t_start) * 1000.0
    report.futures_per_s = n * A / max(report.elapsed_ms / 1000.0, 1e-9)
    return report


def _lateral_spread(traj: np.ndarray, k: int) -> float:
    """p95 minus p5 of the across-track offset of one aircraft's rollouts at sample k. traj: (n, T, 3)."""
    at = traj[:, k, :2]
    mean = at.mean(axis=0)
    ref = traj[:, k - 1, :2].mean(axis=0) if k > 0 else None
    if ref is None or np.hypot(*(mean - ref)) < 1e-9:
        if traj.shape[1] > k + 1:
            ref, mean_dir = mean, traj[:, k + 1, :2].mean(axis=0) - mean
        else:
            return 0.0
    else:
        mean_dir = mean - ref
    norm = np.hypot(*mean_dir)
    if norm < 1e-9:
        return 0.0
    ux, uy = mean_dir / norm
    lateral = (at[:, 0] - mean[0]) * uy - (at[:, 1] - mean[1]) * ux
    lo, hi = np.percentile(lateral, [5, 95])
    return float(hi - lo)


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
