import time

import numpy as np

from planner.cards import cards_from_plan, item_to_sim_command, say_altitude, say_callsign
from planner.conflicts import closest_approach, losses_of_separation, pairwise_conflicts
from planner.plan import baseline, plan, replan
from planner.trajectory import sample_path, to_planned_path
from schemas import AircraftState, Disruption, Item
from sim.engine import Simulator
from sim.scenarios import load


def _path(cs, pts, gs=420, t0=0.0, alt=30000):
    return to_planned_path(cs, sample_path(pts, gs, t0, alt))


def test_conflict_test_head_on_and_separated():
    a = _path("A", [(-50, 0), (50, 0)])
    b = _path("B", [(50, 0), (-50, 0)])
    c = _path("C", [(-50, 20), (50, 20)])
    d = _path("D", [(50, 0), (-50, 0)], alt=32000)
    assert [(x, y) for x, y, _ in pairwise_conflicts([a, b])] == [("A", "B")]
    assert pairwise_conflicts([a, c]) == []
    assert pairwise_conflicts([a, d]) == []
    assert losses_of_separation([a, b]) == 1 and losses_of_separation([a, c]) == 0
    ca = closest_approach([a, c])
    assert ca[0][:2] == ("A", "C") and abs(ca[0][2] - 20) < 1e-6


def test_plan_demo_no_conflicts_under_two_seconds():
    sc = load("demo")
    t = time.perf_counter()
    p = plan(sc, sc.waypoints, sc.zones, 3.0)
    assert time.perf_counter() - t < 2.0
    assert p.conflicts == 0
    assert pairwise_conflicts(p.paths, 8.0) == []
    assert losses_of_separation(p.paths) == 0
    assert p.total_distance_nm < p.baseline_distance_nm
    assert len(p.paths) == 8
    changed = [x for x in p.paths if any(not c.startswith("direct") for c in x.changes)]
    assert changed, "the DAL789/UAL210 direct-path crossing must force a change"


def test_plan_never_below_hard_floor_at_zero_buffer():
    sc = load("dense")
    p = plan(sc, sc.waypoints, sc.zones, 0.0)
    assert p.conflicts == 0
    assert losses_of_separation(p.paths) == 0


def test_baseline_demo_has_conflict():
    sc = load("demo")
    b = baseline(sc)
    assert b.conflicts >= 1
    assert {a for a, _, _ in pairwise_conflicts(b.paths, 5.0)} & {"ACA123", "WJA456"}


def test_replan_with_intruder_changes_only_affected_flights():
    sc = load("demo")
    p0 = plan(sc, sc.waypoints, sc.zones, 3.0)
    sim = Simulator(sc)
    issued: set[str] = set()
    for _ in range(300):
        sim.step(1.0)
        for c in cards_from_plan(p0, None, 0.0):  # fly the plan as soon as each flight is on frequency
            if c.callsign not in issued and sim.get(c.callsign) is not None:
                issued.add(c.callsign)
                for item in c.items:
                    sim.apply(c.callsign, item_to_sim_command(item))
    d = Disruption(id="VIPER11", kind="intruder", x_nm=-60, y_nm=-20, hdg_deg=70, gs_kt=550)
    sim.add_disruption(d, alt_ft=33000)
    states = sim.aircraft()
    p1 = replan(p0, states, sc.waypoints, sc.zones, 3.0, d, frozen_s=60, flights=sc.flights, now_t=sim.t)
    assert p1.trigger == "intruder"
    assert p1.conflicts == 0
    prev = {x.callsign: x.changes for x in p0.paths}
    changed = [x.callsign for x in p1.paths if x.changes != prev.get(x.callsign)]
    assert 0 < len(changed) < len(p1.paths)
    # Frozen window kept: the first 60 s of an airborne changed flight match the previous plan.
    for x in p1.paths:
        if x.callsign in changed and x.callsign in {s.callsign for s in states}:
            old = {round(t): (xx, yy) for t, xx, yy, _ in prev_path(p0, x.callsign)}
            for t, xx, yy, _ in x.samples:
                if sim.t <= t <= sim.t + 60 and round(t) in old:
                    assert np.hypot(old[round(t)][0] - xx, old[round(t)][1] - yy) < 1e-6
            break


def prev_path(p, cs):
    return next(x.samples for x in p.paths if x.callsign == cs)


def test_cards_phrase_digits():
    assert say_callsign("ACA123") == "Air Canada one two three"
    assert say_callsign("JZA8102") == "Jazz eight one zero two"
    assert say_altitude(24000) == "flight level two four zero"
    assert say_altitude(12500) == "one two thousand five hundred"
    sc = load("demo")
    p = plan(sc, sc.waypoints, sc.zones, 3.0)
    cards = cards_from_plan(p, None, 0.0)
    assert cards and cards == sorted(cards, key=lambda c: c.urgency_s)
    by_cs = {c.callsign: c for c in cards}
    assert "Air Canada one three three, proceed direct BUNTS" == by_cs["ACA133"].phrase
    alt_cards = [c for c in cards if any(i.type == "altitude" for i in c.items)]
    assert alt_cards
    item = next(i for i in alt_cards[0].items if i.type == "altitude")
    assert item.unit == "FL" and "flight level" in alt_cards[0].phrase
    assert item_to_sim_command(item).kind == "altitude" and item_to_sim_command(item).value == item.value * 100
    assert item_to_sim_command(Item(type="route", value="BUNTS", action="direct")).value == "BUNTS"
    assert item_to_sim_command(Item(type="frequency", value=124.65, unit="MHz")).kind == "none"
    # No card for flights that did not change against the previous plan.
    assert cards_from_plan(p, p, 0.0) == []
