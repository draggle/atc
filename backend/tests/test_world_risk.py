"""World integration of the Monte Carlo risk module (TRD 07): trigger, counters, confidence, event.

The prediction itself is stubbed through `World.risk_predict` so these tests pin the world's
behaviour whatever the numbers from planner/risk.py are. One test runs the real predict.
"""
import asyncio
import json
import time

import pytest

import planner.risk as R
from world import RISK_N_MAX, World


def make_world():
    events: list[dict] = []
    w = World(events.append, synthesize=False, realtime=False)
    w.load("demo")
    w.start()
    return w, events


class Stub:
    """A controllable predict: one pair at `p` (None for an empty report). Records every call."""

    def __init__(self, a: str, b: str, p: float | None = 0.6, sleep_s: float = 0.0) -> None:
        self.a, self.b, self.p, self.sleep_s = a, b, p, sleep_s
        self.calls: list[int] = []

    def __call__(self, states, paths, zones, now_t, *, n=256, seed=0, **kw) -> R.RiskReport:
        self.calls.append(n)
        if self.sleep_s:
            time.sleep(self.sleep_s)
        pairs = []
        if self.p is not None:
            pairs = [R.PairRisk(a=self.a, b=self.b, p_max=self.p, t_first_s=30.0, eta_s=60.0,
                                min_sep_nm_p5=2.0, curve=[(0.0, 0.0), (30.0, self.p / 2), (60.0, self.p)],
                                cpa_xy=(1.0, 2.0), spread_a_nm=1.5, spread_b_nm=1.0)]
        return R.RiskReport(pairs=pairs, n_rollouts=n, elapsed_ms=self.sleep_s * 1000.0,
                            futures_per_s=1000.0, seed=seed)


@pytest.fixture
def risky():
    w, ev = make_world()
    a, b = [x.callsign for x in w.sim.aircraft()[:2]]
    stub = Stub(a, b, 0.6)
    w.risk_predict = stub
    ev.clear()
    return w, ev, stub


def risk_replans(events):
    return [e for e in events if e["type"] == "plan_update" and str(e["payload"].get("trigger", "")).startswith("risk")]


def test_a_pair_over_the_threshold_replans_counts_and_scores_the_cards(risky):
    w, ev, stub = risky
    asyncio.run(w.tick(1.0))
    replans = risk_replans(ev)
    assert len(replans) == 1 and replans[0]["payload"]["trigger"] == f"risk {stub.a}/{stub.b}"
    assert w.plan.trigger.startswith("risk")
    assert w.conflicts_predicted == 1 and w.conflicts_resolved == 0
    risk_events = [e for e in ev if e["type"] == "risk"]
    assert len(risk_events) == 1
    p = risk_events[0]["payload"]
    assert p["pairs"][0]["a"] == stub.a and p["pairs"][0]["p_max"] == 0.6
    assert p["pairs"][0]["curve"] == [[0.0, 0.0], [30.0, 0.3], [60.0, 0.6]]
    assert p["n_rollouts"] == RISK_N_MAX and p["futures_per_s"] == 1000.0
    json.dumps(risk_events[0])  # plain JSON, no default hook needed
    # the prediction ran once on the old plan and once more on the new one (rescore)
    assert len(stub.calls) == 2
    # every card, from the initial plan or this replan, carries a confidence and a residual risk
    cards = [e["payload"] for e in ev if e["type"] == "instruction_card"]
    for c in list(w.cards.values()):
        assert c.confidence is not None and 0.05 <= c.confidence <= 0.99
        assert c.risk_after is not None
    for c in cards:
        assert 0.05 <= c["confidence"] <= 0.99 and c["risk_after"] is not None
    # a card issued by this replan for one of the risky pair carries that pair's residual risk
    # (cards from the initial plan were scored at load, before the stub, and keep their number)
    touched = [c for c in cards if c["callsign"] in (stub.a, stub.b) and c["status"] == "pending"]
    for c in touched:
        assert c["risk_after"] == 0.6 and c["confidence"] <= 0.4 + 1e-9
    sb = w.scoreboard()
    assert sb.conflicts_predicted == 1 and sb.cones_now == 1 and sb.futures_per_s == 1000


def test_the_same_pair_is_not_replanned_again_within_twenty_seconds(risky):
    w, ev, stub = risky
    for _ in range(19):
        asyncio.run(w.tick(1.0))
    assert len(risk_replans(ev)) == 1
    assert w.conflicts_predicted == 1  # still the same predicted conflict, not a new one every tick
    asyncio.run(w.tick(1.0))
    asyncio.run(w.tick(1.0))
    assert len(risk_replans(ev)) == 2  # 20 s later it may be planned again


def test_a_pair_that_drops_below_the_floor_counts_as_resolved(risky):
    w, ev, stub = risky
    asyncio.run(w.tick(1.0))
    assert w.conflicts_predicted == 1 and w.conflicts_resolved == 0
    stub.p = 0.0
    asyncio.run(w.tick(1.0))
    assert w.conflicts_resolved == 1
    asyncio.run(w.tick(1.0))
    assert w.conflicts_resolved == 1  # counted once
    stub.p = 0.6
    asyncio.run(w.tick(1.0))
    assert w.conflicts_predicted == 2  # rising again is a new prediction


def test_a_pair_that_lost_separation_is_not_counted_as_resolved(risky):
    w, ev, stub = risky
    asyncio.run(w.tick(1.0))
    w.monitor.events.append((stub.a, stub.b, w.sim.t, 3.0))  # a loss of separation happened meanwhile
    stub.p = None
    asyncio.run(w.tick(1.0))
    assert w.conflicts_resolved == 0


def test_empty_reports_emit_at_most_one_risk_event():
    w, ev = make_world()
    a, b = [x.callsign for x in w.sim.aircraft()[:2]]
    stub = Stub(a, b, None)
    w.risk_predict = stub
    ev.clear()
    asyncio.run(w.tick(1.0))
    asyncio.run(w.tick(1.0))
    asyncio.run(w.tick(1.0))
    assert [e for e in ev if e["type"] == "risk"] == []  # nothing was ever shown, nothing to clear
    stub.p = 0.1
    asyncio.run(w.tick(1.0))
    stub.p = None
    asyncio.run(w.tick(1.0))
    asyncio.run(w.tick(1.0))
    kinds = [len(e["payload"]["pairs"]) for e in ev if e["type"] == "risk"]
    assert kinds == [1, 0]  # one to show the cone, one to clear it, no more


def test_a_slow_predict_halves_the_rollout_count():
    w, ev = make_world()
    a, b = [x.callsign for x in w.sim.aircraft()[:2]]
    w.risk_predict = Stub(a, b, None, sleep_s=0.2)
    assert w._risk_n == RISK_N_MAX
    asyncio.run(w.tick(1.0))
    assert w._risk_n == RISK_N_MAX // 2
    asyncio.run(w.tick(1.0))
    assert w._risk_n == RISK_N_MAX // 4
    w.risk_predict = Stub(a, b, None)
    asyncio.run(w.tick(1.0))
    assert w._risk_n == RISK_N_MAX // 2  # fast again: doubles back up


def test_the_rollout_count_never_drops_below_the_floor():
    w, ev = make_world()
    a, b = [x.callsign for x in w.sim.aircraft()[:2]]
    w.risk_predict = Stub(a, b, None, sleep_s=0.05)
    for _ in range(6):
        asyncio.run(w.tick(1.0))
    assert w._risk_n == 32


def test_above_one_x_the_prediction_runs_every_two_seconds():
    w, ev = make_world()
    a, b = [x.callsign for x in w.sim.aircraft()[:2]]
    stub = Stub(a, b, None)
    w.risk_predict = stub
    w.set_speed(4.0)
    asyncio.run(w.tick(8.0))  # eight 1 s steps
    assert len(stub.calls) == 4


def test_the_real_predict_runs_in_the_world():
    w, ev = make_world()
    for _ in range(5):
        asyncio.run(w.tick(1.0))
    assert isinstance(w.risk, R.RiskReport)
    assert w.risk.n_rollouts >= 32
    for c in w.cards.values():
        assert c.confidence is not None and 0.05 <= c.confidence <= 0.99
    sb = w.scoreboard()
    assert sb.cones_now == len(w.risk.pairs)
    for e in ev:
        json.dumps(e) if e["type"] == "risk" else None
