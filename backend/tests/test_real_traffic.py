"""Real recorded traffic as a scenario. docs/10-roadmap.md phase 4."""
import asyncio
import json
import re

import pytest

from airlines import ICAO_TO_TELEPHONY
from planner.plan import baseline, plan
from sim import scenarios as SC
from world import World, scenario_catalog

REALS = [n for n in SC.list_scenarios() if n.startswith("real/")]
pytestmark = pytest.mark.skipif(not REALS, reason="no built real scenarios; run tools/real_build.py")
EUROPE = next((n for n in REALS if "europe-core" in n and n.endswith("1600")), REALS[0] if REALS else "")


def test_built_scenarios_are_real_shaped():
    sc = SC.load(EUROPE)
    assert sc.source == "real" and sc.geo.shape == "circle" and sc.meta["date"]
    assert "adsb.lol" in sc.meta["attribution"]
    gates = [w for w in sc.waypoints if w.kind == "gate"]
    assert 5 <= len(gates) <= 60 and all(re.fullmatch(r"[A-Z]{5}", g.name) for g in gates)
    R = sc.meta["radius_nm"]
    for g in gates:
        assert abs((g.x_nm ** 2 + g.y_nm ** 2) ** 0.5 - R) < 0.5, "gates sit on the region boundary"
    names = {w.name for w in sc.waypoints}
    for f in sc.flights:
        assert re.fullmatch(r"[A-Z]{3}\d[A-Z0-9]{0,3}", f.callsign)
        assert set(f.route) <= names and f.route[-1] in {g.name for g in gates}
        assert 24000 <= f.alt_ft <= 47000 and 150 <= f.gs_kt <= 650 and f.entry_time_s >= 0
    assert len({f.callsign for f in sc.flights}) == len(sc.flights)


def test_thin_keeps_gates_and_drops_unused_track_points():
    sc = SC.load(EUROPE)
    small = SC.thin(sc, 40)
    assert len(small.flights) == 40 and small.meta["flights_available"] == len(sc.flights)
    assert [w.name for w in small.waypoints if w.kind == "gate"] == [w.name for w in sc.waypoints if w.kind == "gate"]
    used = {w for f in small.flights for w in f.route}
    assert all(w.kind != "hidden" or w.name in used for w in small.waypoints)
    assert SC.thin(sc, None) is sc and SC.thin(sc, 10_000) is sc
    # spread over the hour, not the first 40
    assert max(f.entry_time_s for f in small.flights) > 0.6 * max(f.entry_time_s for f in sc.flights)


def test_planner_handles_real_density_with_zero_conflicts():
    sc = SC.thin(SC.load(EUROPE), 80)
    p = plan(sc, sc.waypoints, sc.zones, sc.separation_buffer_nm, time_budget_s=1.0)
    assert p.conflicts == 0 and len(p.paths) == 80
    b = baseline(sc)
    assert p.total_distance_nm <= b.total_distance_nm + 1e-6
    # honest expectation: cruise traffic in a 300 NM region already flies nearly straight
    assert (b.total_distance_nm - p.total_distance_nm) / b.total_distance_nm < 0.05


def test_hidden_track_points_never_reach_the_screen_or_the_radio():
    events = []
    w = World(events.append, synthesize=False, realtime=False)
    w.load(EUROPE, max_flights=40)
    st = [e for e in events if e["type"] == "state"][-1]["payload"]
    assert st["source"] == "real" and st["geo"]["shape"] == "circle" and st["meta"]["max_flights"] == 40
    assert st["waypoints"] and all(wp["kind"] == "gate" for wp in st["waypoints"])
    assert all(re.fullmatch(r"[A-Z]{5}", n) for n in w.spoken_waypoints())
    assert len(json.dumps([e for e in events if e["type"] == "plan"][-1])) < 400_000
    for card in w.cards.values():
        assert not re.search(r"\bT\d{3}[A-Z]\b", card.phrase), card.phrase


def test_real_callsign_round_trip_with_letter_suffix():
    events = []
    w = World(events.append, synthesize=False, realtime=False)
    w.load(EUROPE, max_flights=80)
    w.start()
    w.fleet.set_error_rate(0.0)
    for _ in range(3):
        asyncio.run(w.tick(1.0))
    cs = next(c for c in w.sim.active if re.search(r"[A-Z]$", c) and c[:3] in ICAO_TO_TELEPHONY)
    gate = w.sim.get(cs).route[-1]
    from planner.cards import say_callsign

    spoken = say_callsign(cs)
    assert spoken.split()[0].lower() == ICAO_TO_TELEPHONY[cs[:3]].split()[0].lower()
    events.clear()
    asyncio.run(w.controller_text(f"{spoken}, climb and maintain flight level three nine zero"))
    asyncio.run(w.tick(1.0)); asyncio.run(w.tick(1.0))
    opened = [e for e in events if e["type"] == "clearance_opened"]
    assert opened and opened[0]["payload"]["callsign"] == cs, [e["payload"].get("text_norm") for e in events if e["type"] == "transcript"]
    assert not [e for e in events if e["type"] == "alert"]
    assert [e for e in events if e["type"] == "clearance_updated"][-1]["payload"]["status"] == "matched"
    assert gate


def test_catalog_lists_real_scenarios_with_their_origin():
    cat = {c["name"]: c for c in scenario_catalog()}
    real = cat[EUROPE]
    assert real["source"] == "real" and real["meta"]["region"] == "europe-core" and real["meta"]["date"]
    assert cat["demo"]["source"] == "sim"


def test_one_airline_table_covers_most_real_traffic():
    total = known = 0
    for n in REALS:
        for f in SC.load(n).flights:
            total += 1
            known += f.callsign[:3] in ICAO_TO_TELEPHONY
    assert known / total > 0.9, f"{known}/{total}"
