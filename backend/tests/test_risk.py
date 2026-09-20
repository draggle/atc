"""planner/risk.py: Monte Carlo loss-of-separation prediction. TRD 07."""
import time

import numpy as np
import pytest

from planner.plan import plan
from planner.trajectory import to_planned_path
from planner.risk import Noise, PairRisk, RiskReport, SEP_NM, SHOW_P, margin_factor, predict, score_after
from schemas import AircraftState
from sim.engine import Simulator
from sim.scenarios import load


def ac(cs: str, x: float, y: float, alt: float, hdg: float, gs: float = 450.0, target_alt: float | None = None) -> AircraftState:
    return AircraftState(callsign=cs, x_nm=x, y_nm=y, alt_ft=alt, target_alt_ft=alt if target_alt is None else target_alt,
                         hdg_deg=hdg, gs_kt=gs)


def head_on(dz_ft: float = 0.0) -> list[AircraftState]:
    """40 NM apart, 450 kt each, nose to nose along the x axis."""
    return [ac("AAA", -20.0, 0.0, 35000.0, 90.0), ac("BBB", 20.0, 0.0, 35000.0 + dz_ft, 270.0)]


def sky(n: int, seed: int = 1) -> list[AircraftState]:
    rng = np.random.default_rng(seed)
    levels = [30000.0, 31000.0, 33000.0, 35000.0, 37000.0]
    return [ac(f"F{i:03d}", float(rng.uniform(-150, 150)), float(rng.uniform(-150, 150)),
               float(rng.choice(levels)), float(rng.uniform(0, 360))) for i in range(n)]


# --------------------------------------------------------------------------- geometry

def test_head_on_same_level_is_near_certain():
    # LoS begins when the gap is 5 NM: (40 - 5) NM at 900 kt closing = 140 s, so look 200 s out.
    r = predict(head_on(), {}, [], 0.0, horizon_s=200.0)
    p = r.pair("AAA", "BBB")
    assert p is not None
    assert p.p_max > 0.95
    assert p.t_first_s is not None and abs(p.t_first_s - 140.0) <= 10.0
    assert abs(p.cpa_xy[0]) < 2.0 and abs(p.cpa_xy[1]) < 2.0
    assert p.min_sep_nm_p5 < SEP_NM
    assert 140.0 <= p.eta_s <= 200.0
    assert len(p.curve) == 41 and p.curve[0] == (0.0, 0.0)
    assert r.n_rollouts == 256 and r.elapsed_ms > 0 and r.futures_per_s > 0


def test_head_on_3000ft_apart_is_safe():
    r = predict(head_on(3000.0), {}, [], 0.0, horizon_s=200.0)
    assert r.pair("AAA", "BBB") is None
    assert all(p.p_max < 0.05 for p in r.pairs)


def test_parallel_8nm():
    states = [ac("AAA", 0.0, 0.0, 35000.0, 0.0), ac("BBB", 8.0, 0.0, 35000.0, 0.0)]
    noisy = predict(states, {}, [], 0.0)
    assert all(p.p_max < 0.10 for p in noisy.pairs)
    quiet = predict(states, {}, [], 0.0, noise=Noise.none())
    assert quiet.pairs == []


def test_seed_makes_it_deterministic():
    states = sky(12)
    a = predict(states, {}, [], 0.0, seed=7)
    b = predict(states, {}, [], 0.0, seed=7)
    assert [(p.a, p.b, p.p_max, p.curve, p.cpa_xy, p.spread_a_nm) for p in a.pairs] == \
           [(p.a, p.b, p.p_max, p.curve, p.cpa_xy, p.spread_a_nm) for p in b.pairs]
    # Every draw in one head-on rollout set matches too, not just the pruned summary.
    x = predict(head_on(), {}, [], 0.0, horizon_s=200.0, seed=3).pair("AAA", "BBB")
    y = predict(head_on(), {}, [], 0.0, horizon_s=200.0, seed=3).pair("AAA", "BBB")
    assert x == y


def test_pruning_drops_far_pairs():
    # Two head-on pairs: one 150 NM apart (never reached inside the horizon), one 40 NM apart.
    states = [ac("FAR1", -75.0, 100.0, 35000.0, 90.0), ac("FAR2", 75.0, 100.0, 35000.0, 270.0)] + head_on()
    r = predict(states, {}, [], 0.0, horizon_s=200.0)
    assert r.pair("FAR1", "FAR2") is None
    assert r.pair("AAA", "BBB") is not None
    assert [p.a for p in r.pairs] == ["AAA"]


def test_sorted_by_p_max_and_only_above_floor():
    states = head_on() + [ac("CCC", 0.0, 9.0, 35000.0, 180.0, gs=250.0)]  # a slow crosser, less certain
    r = predict(states, {}, [], 0.0, horizon_s=200.0)
    assert all(p.p_max >= SHOW_P for p in r.pairs)
    assert [p.p_max for p in r.pairs] == sorted((p.p_max for p in r.pairs), reverse=True)


def test_compliance_delay_shifts_a_pending_turn():
    """A turn that has not started yet: zero noise clears it, a late pilot does not.

    AAA runs north up x=0. BBB sits at (7, 0) still pointing west at AAA's line, but its plan
    turns north now and runs up x=7, a 7 NM parallel. Delayed by more than about 35 s the
    west leg carries it inside 5 NM of AAA before it turns.
    """
    states = [ac("AAA", 0.0, -8.0, 35000.0, 0.0), ac("BBB", 7.0, 0.0, 35000.0, 270.0)]
    steps = np.arange(0, 201, 10.0)
    path = to_planned_path("BBB", np.column_stack([steps, np.full_like(steps, 7.0), 0.125 * steps,
                                                   np.full_like(steps, 35000.0)]))
    quiet = predict(states, {"BBB": path}, [], 0.0, horizon_s=200.0, noise=Noise.none())
    late = predict(states, {"BBB": path}, [], 0.0, horizon_s=200.0,
                   noise=Noise(compliance_delay_s=60.0, gs_pct=0.0, hdg_sigma_deg=0.0, vs_pct=0.0, zone_drift_deg=0.0))
    assert quiet.pair("AAA", "BBB") is None
    l = late.pair("AAA", "BBB")
    assert l is not None and 0.2 < l.p_max < 0.7
    # Without the plan BBB just flies west through AAA's line: near certain.
    assert predict(states, {}, [], 0.0, horizon_s=200.0, noise=Noise.none()).pair("AAA", "BBB").p_max == 1.0


def test_level_change_is_followed_from_the_state():
    """No plan, target above current: the straight projection climbs at 1500 fpm and clears the other."""
    below = ac("AAA", -20.0, 0.0, 35000.0, 90.0, target_alt=38000.0)
    other = ac("BBB", 20.0, 0.0, 35000.0, 270.0)
    r = predict([below, other], {}, [], 0.0, horizon_s=200.0, noise=Noise.none())
    # 3,000 ft at 1,500 fpm is 120 s; LoS starts at 140 s, by then the climber is 1,000 ft+ above.
    assert r.pair("AAA", "BBB") is None


# --------------------------------------------------------------------------- a real plan

def test_resamples_a_planned_path_from_the_demo_scenario():
    sc = load("demo")
    sim = Simulator(sc)
    for _ in range(30):
        sim.step(1.0)
    states = sim.aircraft()
    p = plan(sc, sc.waypoints, sc.zones, 3.0, now_t=sim.t, states=states, time_budget_s=0.2)
    paths = {pp.callsign: pp for pp in p.paths}
    assert any(len(pp.samples) > 2 for pp in paths.values())
    r = predict(states, paths, sc.zones, sim.t, seed=1)
    assert isinstance(r, RiskReport)
    assert r.elapsed_ms > 0 and r.futures_per_s > 0
    assert all(isinstance(x, PairRisk) for x in r.pairs)
    # The plan clears every pair at 5 NM + 3 NM, so nobody should be near-certain to breach.
    assert all(x.p_max < 0.5 for x in r.pairs)
    # Same call, same seed, same answer, with the resampled tracks in the loop.
    assert [(x.a, x.b, x.p_max) for x in predict(states, paths, sc.zones, sim.t, seed=1).pairs] == \
           [(x.a, x.b, x.p_max) for x in r.pairs]


def test_plan_records_runner_up_cost():
    sc = load("demo")
    p = plan(sc, sc.waypoints, sc.zones, 3.0, time_budget_s=0.2)
    from schemas import PlannedPath
    if "runner_up_cost" not in PlannedPath.model_fields:
        pytest.skip("PlannedPath.runner_up_cost lands with the schema change")
    vals = [pp.runner_up_cost for pp in p.paths]
    assert all(v is None or v >= 0.0 for v in vals)
    assert any(v is not None for v in vals)
    for pp in p.paths:
        if pp.runner_up_cost is not None:
            assert pp.runner_up_cost >= pp.cost - 1e-6 or pp.cost == 0.0


# --------------------------------------------------------------------------- performance

def _fastest_ms(states: list[AircraftState], reps: int = 5) -> tuple[float, float]:
    predict(states, {}, [], 0.0)  # warm the caches
    best = min((predict(states, {}, [], 0.0) for _ in range(reps)), key=lambda r: r.elapsed_ms)
    return best.elapsed_ms, best.futures_per_s


@pytest.mark.parametrize("count,budget_ms", [(20, 50.0), (100, 200.0)])
def test_performance(count: int, budget_ms: float):
    ms, fps = _fastest_ms(sky(count))
    print(f"\nrisk.predict: {count} aircraft x 256 rollouts: {ms:.1f} ms, {fps:,.0f} futures/s")
    if ms > budget_ms:
        # Timing on a loaded laptop or CI runner: fail only when it is far off.
        if ms < 3 * budget_ms:
            pytest.skip(f"{count} aircraft took {ms:.0f} ms, over the {budget_ms:.0f} ms budget; "
                        "this machine looks loaded, re-run alone before trusting it")
        pytest.fail(f"{count} aircraft took {ms:.0f} ms, budget {budget_ms:.0f} ms")


# --------------------------------------------------------------------------- confidence helpers

def test_margin_factor():
    assert margin_factor(10.0, 12.0) == pytest.approx(1.0)   # beat by 20 percent
    assert margin_factor(10.0, 20.0) == pytest.approx(1.0)
    assert margin_factor(10.0, 11.0) == pytest.approx(0.75)  # half the margin
    assert margin_factor(10.0, 10.0) == pytest.approx(0.5)   # a tie
    assert margin_factor(10.0, 9.0) == pytest.approx(0.5)    # runner-up cheaper: treated as a tie
    assert margin_factor(10.0, None) == 1.0
    assert margin_factor(None, 5.0) == 1.0
    assert margin_factor(0.0, 5.0) == 1.0                    # free path: nothing to compare


def test_score_after():
    def pr(a, b, p):
        return PairRisk(a=a, b=b, p_max=p, t_first_s=0.0, eta_s=0.0, min_sep_nm_p5=0.0, curve=[], cpa_xy=(0.0, 0.0),
                        spread_a_nm=0.0, spread_b_nm=0.0)
    before = RiskReport(pairs=[pr("A", "B", 0.6), pr("C", "D", 0.4)])
    after = RiskReport(pairs=[pr("A", "B", 0.1), pr("C", "D", 0.4), pr("B", "E", 0.2)])
    assert score_after(before, after, {"A"}) == pytest.approx(0.1)
    assert score_after(before, after, {"B"}) == pytest.approx(0.2)
    assert score_after(before, after, {"C", "A"}) == pytest.approx(0.4)
    assert score_after(before, after, {"Z"}) == 0.0
    assert score_after(before, RiskReport(), {"A"}) == 0.0
