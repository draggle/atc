"""Live sky: one snapshot of adsb.lol turned into an ordinary scenario. docs/10-roadmap.md phase 4b.

Nothing here touches the network. The fixture is a recorded response: 555 aircraft within 250 NM of
the europe-core centre.
"""
import asyncio
import json
import math
import re
from pathlib import Path

import pytest

from planner.plan import baseline, plan
from schemas import Scenario
from sim import live, regions
from sim import scenarios as SC
from sim.engine import Simulator
from sim.gates import GATE_SPREAD_NM
from sim.geo import bearing_deg, wrap180
from sim.geoframe import to_latlon
from world import World

FIXTURE = Path(__file__).parent / "fixtures" / "adsb_lol_europe_core.json"
REGION = "europe-core"
R = regions.get(REGION).radius_nm
NOW_MS = 1789847654500


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.fixture(scope="module")
def sc(raw) -> Scenario:
    return live.build_scenario(raw, REGION)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch, tmp_path):
    """Any test that reaches the real fetch fails loudly, and snapshots go to a temp directory."""
    def boom(*a, **k):
        raise AssertionError("tests must not hit the network")
    monkeypatch.setattr(live.httpx, "get", boom)
    monkeypatch.setattr(live, "LIVE_DIR", tmp_path / "live")


def _start(sc: Scenario, f) -> tuple[float, float]:
    """Where a flight appears: its hidden entry waypoint (the engine takes route[0] as the start)."""
    wp = {w.name: w for w in sc.waypoints}
    assert f.x_nm is None and len(f.route) == 2 and wp[f.route[0]].kind == "hidden"
    return wp[f.route[0]].x_nm, wp[f.route[0]].y_nm


def _true_track(frame, x_nm: float, y_nm: float, hdg_deg: float) -> float:
    """The true track that shows as `hdg_deg` in the flat plane at (x, y): initial great-circle bearing."""
    (la1, lo1), (la2, lo2) = (to_latlon(frame, x_nm + k * math.sin(math.radians(hdg_deg)),
                                        y_nm + k * math.cos(math.radians(hdg_deg))) for k in (0.0, 1.0))
    p1, p2, dl = math.radians(la1), math.radians(la2), math.radians(lo2 - lo1)
    return math.degrees(math.atan2(math.sin(dl) * math.cos(p2),
                                   math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))) % 360


def _ac(callsign="DLH123", x_nm=0.0, y_nm=0.0, hdg=90.0, **over) -> dict:
    """One synthetic aircraft at flat position (x, y) NM from the europe-core centre, flying `hdg` in the flat plane."""
    frame = live.frame_of(regions.get(REGION))
    lat, lon = to_latlon(frame, x_nm, y_nm)
    ac = {"hex": "abc123", "flight": f"{callsign:<8}", "lat": lat, "lon": lon, "alt_baro": 36000, "gs": 450.0,
          "track": _true_track(frame, x_nm, y_nm, hdg), "baro_rate": 0, "geom_rate": 0, "seen_pos": 1.0,
          "t": "A320", "category": "A3"}
    ac.update(over)
    return {k: v for k, v in ac.items() if v is not None}


def _crowd(n=6) -> list[dict]:
    """Enough ordinary flights that a scenario builds, spread out so none interact."""
    return [_ac(f"KLM{100 + i}", x_nm=-60.0 + 20 * i, y_nm=-100.0 + 30 * i, hdg=45.0 * i) for i in range(n)]


def _build(*extra: dict, **kw) -> Scenario:
    return live.build_scenario({"now": NOW_MS, "ac": _crowd() + list(extra)}, REGION, **kw)


# ---------------------------------------------------------------- filters

@pytest.mark.parametrize("reason,over", [
    ("low", {"alt_baro": "ground"}),
    ("low", {"alt_baro": 12000}),
    ("low", {"alt_baro": None}),
    ("not_level", {"baro_rate": 1800}),
    ("not_level", {"baro_rate": None, "geom_rate": -900}),
    ("stale", {"seen_pos": 42.0}),
    ("no_callsign", {"flight": None}),
    ("no_callsign", {"flight": "        "}),
    ("not_airline", {"flight": "GABCD   "}),
    ("not_airline", {"flight": "N123AB  "}),
    ("speed", {"gs": 140.0}),
    ("speed", {"gs": None}),
    ("no_position", {"lat": None}),
    ("no_position", {"track": None}),
])
def test_filter_drops(reason, over):
    built = _build(_ac("BAW27G", 10.0, 10.0, **over))
    assert "BAW27G" not in {f.callsign for f in built.flights}
    assert built.meta["dropped"][reason] == 1 and sum(built.meta["dropped"].values()) == 1


def test_level_flight_with_missing_rates_is_kept_and_duplicates_are_not():
    built = _build(_ac("BAW27G", 10.0, 10.0, baro_rate=None, geom_rate=None), _ac("BAW27G", 40.0, 40.0),
                   _ac("RYR4JL", 30.0, -20.0, baro_rate=-400))
    assert [f.callsign for f in built.flights].count("BAW27G") == 1
    assert "RYR4JL" in {f.callsign for f in built.flights}
    assert built.meta["dropped"]["duplicate"] == 1


def test_fewer_than_five_flights_is_an_error():
    with pytest.raises(ValueError, match="only 2"):
        live.build_scenario({"now": NOW_MS, "ac": _crowd(2)}, REGION)
    with pytest.raises(ValueError):
        live.build_scenario({"now": NOW_MS}, REGION)
    with pytest.raises(KeyError):
        live.build_scenario({"now": NOW_MS, "ac": _crowd()}, "atlantis")


def test_garbage_aircraft_never_crash_the_build():
    junk = [{}, {"flight": 7}, {"flight": "DLH1", "lat": "x", "lon": None}, "not a dict", None,
            _ac("DLH9", alt_baro=True), _ac("DLH8", gs="fast"), _ac("DLH7", track=float("nan"))]
    built = live.build_scenario({"now": NOW_MS, "ac": _crowd() + junk}, REGION)
    assert len(built.flights) == 6


# ---------------------------------------------------------------- geometry

def test_heading_convention_track_090_exits_east():
    built = _build(_ac("DLH400", 0.0, 0.0, track=90.0), _ac("DLH500", 0.0, 0.0, track=0.0, alt_baro=38000),
                   _ac("DLH600", 0.0, 0.0, track=225.0, alt_baro=40000))
    wp = {w.name: w for w in built.waypoints}
    by = {f.callsign: f for f in built.flights}
    east, north, sw = (wp[by[c].route[-1]] for c in ("DLH400", "DLH500", "DLH600"))
    assert east.x_nm > 0.99 * R and abs(east.y_nm) < 8
    assert north.y_nm > 0.99 * R and abs(north.x_nm) < 8
    assert sw.x_nm < -0.6 * R and sw.y_nm < -0.6 * R
    assert by["DLH400"].hdg_deg == pytest.approx(90.0, abs=0.1), "at the centre the flat heading is the track"


def test_track_is_corrected_for_the_projection_away_from_the_centre():
    # 200 NM west of the centre a true track of 090 follows the parallel, which curves back down
    # toward the centre's latitude on the flat map: a few degrees south of flat east.
    west = _build(_ac("DLH400", -200.0, 0.0, track=90.0))
    east = _build(_ac("DLH400", 120.0, 0.0, track=90.0))
    assert 92.0 < next(f for f in west.flights if f.callsign == "DLH400").hdg_deg < 97.0
    assert 86.0 < next(f for f in east.flights if f.callsign == "DLH400").hdg_deg < 89.5
    for x, y, hdg in [(-200.0, 50.0, 80.0), (100.0, -90.0, 310.0), (0.0, 0.0, 200.0)]:
        f = next(f for f in _build(_ac("DLH400", x, y, hdg)).flights if f.callsign == "DLH400")
        assert f.hdg_deg == pytest.approx(hdg, abs=0.1), "the test helper's inverse agrees"


def test_outer_ring_flight_becomes_a_future_entry_on_the_boundary():
    # 50 NM west of the boundary, flying east through the centre at 480 kt: enters after 375 s.
    built = _build(_ac("AFR88", -(R + 50.0), 0.0, 90.0, gs=480.0))
    f = next(f for f in built.flights if f.callsign == "AFR88")
    x, y = _start(built, f)
    assert f.entry_time_s == pytest.approx(375.0, abs=1.0)
    assert (x, y) == pytest.approx((-R, 0.0), abs=0.1)
    gate = {w.name: w for w in built.waypoints}[f.route[-1]]
    assert gate.x_nm > 0.99 * R
    assert built.meta["kept"] == {"inside": 6, "inbound": 1}


def test_outer_ring_rejects_flyaways_grazes_and_late_arrivals():
    built = _build(_ac("AFR1", -(R + 50.0), 0.0, 270.0),            # flying away
                   _ac("AFR2", -(R + 30.0), R + 5.0, 90.0),          # passes north of the circle
                   _ac("AFR3", 0.0, 400.0, 180.0),                   # inbound, but beyond the 250 NM feed radius
                   _ac("AFR4", -(R + 30.0), R - 1.0, 90.0),          # clips the top: a 34 NM chord
                   _ac("AFR5", -245.0, 0.0, 58.0, gs=250.0),         # oblique: 133 NM to the boundary, 1900 s
                   _ac("AFR6", -(R + 99.0), 0.0, 90.0, gs=250.0))    # head on: 99 NM, 1426 s, kept
    d = built.meta["dropped"]
    assert (d["not_inbound"], d["graze"], d["late"]) == (3, 1, 1)
    assert [f.callsign for f in built.flights if f.entry_time_s > 0] == ["AFR6"]
    assert built.flights[-1].entry_time_s == pytest.approx(1425.6, abs=1.0) and live.MAX_ENTRY_S == 1800


def test_inside_flight_about_to_leave_is_skipped():
    built = _build(_ac("SAS10", R - 12.0, 0.0, 90.0), _ac("SAS11", R - 12.0, 0.0, 270.0, alt_baro=38000))
    assert "SAS10" not in {f.callsign for f in built.flights} and built.meta["dropped"]["leaving"] == 1
    assert "SAS11" in {f.callsign for f in built.flights}


# ---------------------------------------------------------------- the recorded snapshot

def test_fixture_builds_a_real_shaped_scenario(sc):
    assert sc.name == "live/europe-core" and sc.source == "real" and sc.geo.shape == "circle"
    assert (sc.geo.lat0, sc.geo.lon0, sc.sector_nm, sc.seed, sc.separation_buffer_nm) == (50.6, 6.2, 2 * R, 7, 3.0)
    m = sc.meta
    assert m["live"] is True and m["region"] == REGION and m["radius_nm"] == R and m["floor_ft"] == 24500
    assert m["snapshot_utc"] == "2026-09-19T19:54:14Z"
    assert "adsb.lol" in m["attribution"] and "zero by construction" in m["caveats"]
    assert m["seen"] == 555 and m["kept"]["inside"] + m["kept"]["inbound"] == len(sc.flights) >= 50
    assert m["seen"] == len(sc.flights) + sum(m["dropped"].values()), "every aircraft is kept or counted once"
    assert re.fullmatch(rf"{len(sc.flights)} airline flights at cruise over .+, live snapshot 19:54 UTC\.", sc.description)
    gates = [w for w in sc.waypoints if w.kind == "gate"]
    assert m["gates"] == len(gates) and 5 <= len(gates) <= 60
    assert all(re.fullmatch(r"[A-Z]{5}", g.name) for g in gates)
    assert all(abs(math.hypot(g.x_nm, g.y_nm) - R) < 0.5 for g in gates), "gates sit on the region boundary"
    assert len({w.name for w in sc.waypoints}) == len(sc.waypoints)
    assert len({f.callsign for f in sc.flights}) == len(sc.flights)
    for f in sc.flights:
        assert re.fullmatch(r"[A-Z]{3}\d[A-Z0-9]{0,3}", f.callsign)
        assert f.alt_ft >= 24000 and f.alt_ft % 1000 == 0 and 250 <= f.gs_kt <= 650 and f.actype
    assert [f.entry_time_s for f in sc.flights] == sorted(f.entry_time_s for f in sc.flights)


def test_inside_flights_start_inside_now_and_inbound_flights_start_on_the_boundary_later(sc):
    inside = [f for f in sc.flights if f.entry_time_s == 0]
    inbound = [f for f in sc.flights if f.entry_time_s > 0]
    assert len(inside) == sc.meta["kept"]["inside"] and len(inbound) == sc.meta["kept"]["inbound"]
    assert inside and inbound
    for f in inside:
        assert math.hypot(*_start(sc, f)) <= R + 0.01
    for f in inbound:
        assert abs(math.hypot(*_start(sc, f)) - R) < 0.5 and 0 < f.entry_time_s <= live.MAX_ENTRY_S


def test_every_route_ends_at_a_gate_that_lies_along_the_track(sc):
    wp = {w.name: w for w in sc.waypoints}
    for f in sc.flights:
        x, y = _start(sc, f)
        gate = wp[f.route[-1]]
        assert gate.kind == "gate"
        remaining = math.hypot(gate.x_nm - x, gate.y_nm - y)
        assert remaining >= live.MIN_REMAINING_NM - GATE_SPREAD_NM
        # The gate is the cluster's mean, at most GATE_SPREAD_NM along the boundary from this
        # flight's own projected exit. Seen from the start that is a small angle.
        off = abs(wrap180(bearing_deg(x, y, gate.x_nm, gate.y_nm) - f.hdg_deg))
        assert off <= math.degrees(math.atan2(GATE_SPREAD_NM, remaining)) + 0.5, (f.callsign, off, remaining)


def test_inbound_entry_points_match_the_great_circle_each_flight_is_really_on(sc, raw):
    # Walk each inbound flight along its true track on the sphere until it crosses the boundary.
    # With the raw track used as a flat heading the worst miss was 24.6 NM; corrected it is 0.15.
    from sim.geoframe import EARTH_RADIUS_NM, to_xy
    feed = {a["flight"].strip(): a for a in raw["ac"] if isinstance(a.get("flight"), str)}
    for f in (f for f in sc.flights if f.entry_time_s > 0):
        a = feed[f.callsign]
        p1, brg, d = math.radians(a["lat"]), math.radians(a["track"]), 0.0
        while True:
            c = d / EARTH_RADIUS_NM
            p2 = math.asin(math.sin(p1) * math.cos(c) + math.cos(p1) * math.sin(c) * math.cos(brg))
            l2 = math.radians(a["lon"]) + math.atan2(math.sin(brg) * math.sin(c) * math.cos(p1),
                                                     math.cos(c) - math.sin(p1) * math.sin(p2))
            x, y = to_xy(sc.geo, math.degrees(p2), math.degrees(l2))
            if math.hypot(x, y) <= R or d > 300:
                break
            d += 0.25
        sx, sy = _start(sc, f)
        assert math.hypot(x - sx, y - sy) < 1.0, f.callsign
        assert f.entry_time_s == pytest.approx(d / f.gs_kt * 3600, abs=10)


def test_gate_names_are_stable_between_snapshots_of_a_region(sc, raw):
    again = live.build_scenario({"now": raw["now"] + 60_000, "ac": raw["ac"][: len(raw["ac"]) // 2]}, REGION)
    a = [w.name for w in sc.waypoints if w.kind == "gate"]
    b = [w.name for w in again.waypoints if w.kind == "gate"]
    assert b == a[: len(b)], "same seed: a smaller snapshot uses a prefix of the same names"


def test_build_is_pure_and_round_trips(sc, raw):
    before = json.dumps(raw, sort_keys=True)
    again = live.build_scenario(raw, REGION)
    assert json.dumps(raw, sort_keys=True) == before
    assert again.model_dump() == sc.model_dump()
    assert Scenario.model_validate(json.loads(json.dumps(sc.model_dump()))).model_dump() == sc.model_dump()


def test_max_flights_caps_like_real_mode(sc, raw):
    small = live.build_scenario(raw, REGION, max_flights=40)
    assert len(small.flights) == 40 and small.meta["max_flights"] == 40
    assert small.meta["flights_available"] == len(sc.flights)
    assert small.description.startswith("40 airline flights")
    assert [w.name for w in small.waypoints if w.kind == "gate"] == [w.name for w in sc.waypoints if w.kind == "gate"]
    used = {n for f in small.flights for n in f.route}
    assert all(w.kind != "hidden" or w.name in used for w in small.waypoints)
    assert any(f.entry_time_s > 0 for f in small.flights), "the cap keeps some inbound flights too"
    assert len(live.build_scenario(raw, REGION, max_flights=10_000).flights) == len(sc.flights)


# ---------------------------------------------------------------- simulator, planner, world

def test_simulator_flies_the_snapshot_along_its_tracks(raw):
    sc = live.build_scenario(raw, REGION, max_flights=120)
    sim = Simulator(sc)
    inside = [f for f in sc.flights if f.entry_time_s == 0]
    assert set(sim.active) == {f.callsign for f in inside}
    start = {f.callsign: _start(sc, f) for f in inside}
    assert all(sim.get(f.callsign).hdg == pytest.approx(f.hdg_deg % 360) for f in inside)
    for _ in range(300):
        sim.step(1.0)
    moved = 0
    for f in inside:
        a = sim.get(f.callsign)
        if a is None:
            continue  # reached its gate inside 300 s
        x0, y0 = start[f.callsign]
        assert math.hypot(a.x - x0, a.y - y0) == pytest.approx(f.gs_kt * 300 / 3600, rel=0.02)
        assert abs(wrap180(bearing_deg(x0, y0, a.x, a.y) - f.hdg_deg)) < 8.0, f.callsign
        moved += 1
    assert moved > 0.8 * len(inside)
    entered = [f for f in sc.flights if 0 < f.entry_time_s <= 290]
    assert entered and all(f.callsign in sim.active or f.callsign in sim.removed for f in entered)


def test_planner_clears_the_snapshot(raw):
    sc = live.build_scenario(raw, REGION, max_flights=80)
    p = plan(sc, sc.waypoints, sc.zones, sc.separation_buffer_nm, time_budget_s=1.0)
    assert len(p.paths) == 80 and p.conflicts == 0
    b = baseline(sc)
    # Each route is already the straight line to its gate, so there is nothing to save.
    assert abs(b.total_distance_nm - p.total_distance_nm) / b.total_distance_nm < 0.01


def test_world_loads_starts_steps_and_resets_to_the_snapshot(raw):
    events = []
    w = World(events.append, synthesize=False, realtime=False)
    sc = live.build_scenario(raw, REGION, max_flights=60)
    w.load_scenario(sc)
    st = [e for e in events if e["type"] == "state"][-1]["payload"]
    assert st["scenario"] == "live/europe-core" and st["source"] == "real" and st["lifecycle"] == "ready"
    for k in ("live", "snapshot_utc", "attribution", "caveats"):
        assert st["meta"][k] == sc.meta[k]
    assert {"key": "europe-core", "label": regions.get(REGION).label} in st["live_regions"]
    assert st["waypoints"] and all(wp["kind"] == "gate" and "lat" in wp for wp in st["waypoints"])
    n0 = len(w.sim.active)
    assert n0 == sum(1 for f in sc.flights if f.entry_time_s == 0) and w.sim.t == 0
    for card in w.cards.values():
        assert not re.search(r"\bT\d{3}[A-Z]\b", card.phrase), card.phrase
    assert w.start()
    for _ in range(30):
        asyncio.run(w.tick(1.0))
    assert w.sim.t == 30
    assert w.reset()
    assert w.sim.t == 0 and w.lifecycle == "ready" and len(w.sim.active) == n0
    assert w.scenario.name == "live/europe-core" and len(w.scenario.flights) == 60


def test_state_offers_live_regions_before_anything_is_loaded():
    events = []
    World(events.append, synthesize=False, realtime=False).emit_state()
    assert [r["key"] for r in events[-1]["payload"]["live_regions"]] == [r.key for r in regions.REGIONS]


# ---------------------------------------------------------------- snapshots on disk

def test_save_and_reload_the_newest_snapshot(raw):
    assert live.latest_snapshot(REGION) is None
    older = {**raw, "now": raw["now"] - 3_600_000}
    p1, p2 = live.save_snapshot(older, REGION), live.save_snapshot(raw, REGION)
    assert p1.name == "europe-core_20260919T185414.json" and p2.name == "europe-core_20260919T195414.json"
    live.save_snapshot({"now": raw["now"] + 1000, "ac": []}, "uk")
    assert live.latest_snapshot(REGION)["now"] == raw["now"]
    assert live.latest_snapshot("toronto") is None


def test_save_snapshot_never_raises(raw, monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(live, "LIVE_DIR", blocker / "live")  # a file where the directory should be
    assert live.save_snapshot(raw, REGION) is None
    assert live.latest_snapshot(REGION) is None


def test_corrupt_snapshot_is_skipped(raw):
    live.save_snapshot(raw, REGION)
    (live.LIVE_DIR / "europe-core_20270101T000000.json").write_text("{not json")
    assert live.latest_snapshot(REGION)["now"] == raw["now"]


# ---------------------------------------------------------------- fetch

class _Resp:
    def __init__(self, status=200, body: object = None, text=None):
        self.status_code = status
        self._body, self.text = body, text if text is not None else json.dumps(body)

    def json(self):
        return json.loads(self.text)


def test_fetch_asks_adsb_lol_for_the_region(monkeypatch, raw):
    seen = {}

    def fake_get(url, **kw):
        seen.update(url=url, **kw)
        return _Resp(200, {"now": raw["now"], "ac": raw["ac"][:3]})
    monkeypatch.setattr(live.httpx, "get", fake_get)
    out = live.fetch(REGION, timeout_s=3.0)
    assert seen["url"] == "https://api.adsb.lol/v2/lat/50.6/lon/6.2/dist/250"
    assert "tower" in seen["headers"]["User-Agent"].lower() and seen["timeout"] == 3.0
    assert len(out["ac"]) == 3 and out["now"] == raw["now"]


@pytest.mark.parametrize("resp,match", [
    (_Resp(429, {}), "HTTP 429"),
    (_Resp(200, text="<html>busy</html>"), "not JSON"),
    (_Resp(200, {"msg": "nothing here"}), "no aircraft list"),
    (_Resp(200, [1, 2]), "no aircraft list"),
])
def test_fetch_failures_are_one_clear_exception(monkeypatch, resp, match):
    monkeypatch.setattr(live.httpx, "get", lambda url, **kw: resp)
    with pytest.raises(live.LiveFeedError, match=match):
        live.fetch(REGION)


def test_fetch_timeout_is_a_live_feed_error(monkeypatch):
    def slow(url, **kw):
        raise live.httpx.ConnectTimeout("timed out")
    monkeypatch.setattr(live.httpx, "get", slow)
    with pytest.raises(live.LiveFeedError, match="ConnectTimeout"):
        live.fetch(REGION)


# ---------------------------------------------------------------- load_into and its fallbacks

def _world():
    events = []
    return World(events.append, synthesize=False, realtime=False), events


def _notices(events, level=None):
    return [e["payload"] for e in events if e["type"] == "notice" and level in (None, e["payload"]["level"])]


def _down(region_key):
    raise live.LiveFeedError("adsb.lol: ConnectTimeout")


def test_load_into_loads_the_live_snapshot_and_saves_it(raw):
    w, events = _world()
    assert live.load_into(w, REGION, 50, fetcher=lambda key: raw) == "live"
    assert w.scenario.name == "live/europe-core" and len(w.scenario.flights) == 50 and w.lifecycle == "ready"
    assert w.scenario.meta["live"] is True and "fallback" not in w.scenario.meta
    assert live.latest_snapshot(REGION)["now"] == raw["now"]
    assert not _notices(events, "warn") and not _notices(events, "error")


def test_load_into_falls_back_to_the_saved_snapshot(raw):
    live.save_snapshot(raw, REGION)
    w, events = _world()
    assert live.load_into(w, REGION, 50, fetcher=_down) == "saved_snapshot"
    assert w.scenario.name == "live/europe-core" and w.scenario.meta["fallback"] == "saved_snapshot"
    assert w.scenario.meta["snapshot_utc"] == "2026-09-19T19:54:14Z"
    st = [e for e in events if e["type"] == "state"][-1]["payload"]
    assert st["meta"]["fallback"] == "saved_snapshot"
    (warn,) = _notices(events, "warn")
    assert warn["text"] == ("Live feed unavailable (adsb.lol: ConnectTimeout). "
                            "Loaded the snapshot from 19:54 UTC on 2026-09-19 instead.")
    assert w.reset() and w.scenario.meta["fallback"] == "saved_snapshot"


def test_load_into_falls_back_to_the_committed_replay():
    if not any(n.startswith(f"real/{REGION}_") for n in SC.list_scenarios()):
        pytest.skip("no committed replay for the region")
    w, events = _world()
    assert live.load_into(w, REGION, 40, fetcher=_down) == "replay"
    newest = max(n for n in SC.list_scenarios() if n.startswith(f"real/{REGION}_"))
    assert w.scenario.name == newest and len(w.scenario.flights) == 40
    assert w.scenario.meta["fallback"] == "replay" and "live" not in w.scenario.meta
    (warn,) = _notices(events, "warn")
    assert warn["text"].startswith("Live feed unavailable (adsb.lol: ConnectTimeout). Loaded the recorded hour")
    assert "instead." in warn["text"]


def test_too_few_live_flights_also_falls_back_and_is_not_saved(raw):
    live.save_snapshot(raw, REGION)
    w, events = _world()
    thin_sky = {"now": raw["now"] + 5_000, "ac": _crowd(3)}
    assert live.load_into(w, REGION, None, fetcher=lambda key: thin_sky) == "saved_snapshot"
    assert "only 3" in _notices(events, "warn")[0]["text"]
    assert live.latest_snapshot(REGION)["now"] == raw["now"], "an unusable snapshot is never the fallback"


def test_nothing_to_fall_back_to_leaves_the_world_alone(monkeypatch):
    monkeypatch.setattr(live.SC, "list_scenarios", lambda: ["demo"])
    w, events = _world()
    w.load("demo")
    wid = w.world_id
    assert live.load_into(w, REGION, None, fetcher=_down) is None
    assert w.scenario.name == "demo" and w.world_id == wid
    (err,) = _notices(events, "error")
    assert "Live feed unavailable" in err["text"] and "nothing" in err["text"].lower()


def test_unknown_region_is_an_error_notice_and_nothing_is_fetched():
    w, events = _world()
    assert live.load_into(w, "atlantis", None, fetcher=lambda key: pytest.fail("fetched")) is None
    assert w.scenario is None and "atlantis" in _notices(events, "error")[0]["text"]


async def test_async_load_keeps_the_loop_free_and_ignores_an_overlapping_request(raw):
    import threading
    import time
    w, events = _world()
    threads = []

    def slow(key):
        threads.append(threading.current_thread())
        time.sleep(0.3)
        return raw

    first = asyncio.create_task(live.load_into_async(w, REGION, 30, fetcher=slow))
    ticks = 0
    while not first.done():
        await asyncio.sleep(0.02)
        ticks += 1
        if ticks == 2:
            assert await live.load_into_async(w, REGION, 30, fetcher=slow) is None  # second one is ignored
    assert await first == "live" and ticks >= 8, "the event loop kept running during the fetch"
    assert threads and all(t is not threading.main_thread() for t in threads) and len(threads) == 1
    assert any("already" in n["text"] for n in _notices(events, "info"))
    assert len(w.scenario.flights) == 30 and w.live_loading is False


async def test_async_load_gives_up_on_a_hung_feed_and_drops_a_stale_result(raw):
    import time
    live.save_snapshot(raw, REGION)
    w, events = _world()
    assert await live.load_into_async(w, REGION, 30, fetcher=lambda key: time.sleep(0.5), deadline_s=0.1) == "saved_snapshot"
    assert "no answer" in _notices(events, "warn")[0]["text"]
    # The user loads something else while the fetch is in flight: the late snapshot must not replace it.
    task = asyncio.create_task(live.load_into_async(w, REGION, 30, fetcher=lambda key: (time.sleep(0.2), raw)[1]))
    await asyncio.sleep(0.05)
    w.load("demo")
    assert await task is None and w.scenario.name == "demo"


# ---------------------------------------------------------------- the WebSocket handler

def _read_until(ws, want, limit=400):
    """Messages up to and including the first one `want` accepts. Bounded so a bug cannot hang the suite."""
    got = []
    for _ in range(limit):
        got.append(json.loads(ws.receive_text()))
        if want(got[-1]):
            return got
    raise AssertionError(f"not seen in {limit} messages: {[m['type'] for m in got][-10:]}")


def test_configure_live_over_the_websocket(monkeypatch, raw):
    import sys

    from fastapi.testclient import TestClient
    monkeypatch.setenv("TOWER_SYNTHESIZE", "0")  # no TTS and no speech model in this test
    monkeypatch.delenv("TOWER_SCENARIO", raising=False)
    sys.modules.pop("app", None)
    import app as A
    monkeypatch.setattr(live, "fetch", lambda key, **kw: raw)

    def loaded(name):
        return lambda m: m["type"] == "state" and m["payload"]["scenario"] == name

    try:
        with TestClient(A.app) as client, client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "configure", "source": "live", "region": REGION, "max_flights": 30}))
            msgs = _read_until(ws, loaded("live/europe-core"))
            st = msgs[-1]["payload"]
            assert st["meta"]["live"] is True and st["meta"]["max_flights"] == 30 and st["lifecycle"] == "ready"
            assert any(m["type"] == "notice" and "Fetching" in m["payload"]["text"] for m in msgs)
            radar = _read_until(ws, lambda m: m["type"] == "radar")[-1]["payload"]
            assert len(radar["aircraft"]) == len(A.world.sim.active) > 0 and "lat" in radar["aircraft"][0]

            ws.send_text(json.dumps({"type": "configure", "source": "live", "region": "atlantis"}))
            err = _read_until(ws, lambda m: m["type"] == "notice" and m["payload"]["level"] == "error")[-1]
            assert "atlantis" in err["payload"]["text"] and A.world.scenario.name == "live/europe-core"

            monkeypatch.setattr(live, "fetch", _down)  # feed down, snapshot saved a moment ago: fallback (a)
            ws.send_text(json.dumps({"type": "configure", "source": "live", "region": REGION, "max_flights": "junk"}))
            warn = _read_until(ws, lambda m: m["type"] == "notice" and m["payload"]["level"] == "warn")[-1]
            assert "Loaded the snapshot from 19:54 UTC" in warn["payload"]["text"]
            assert A.world.scenario.meta["fallback"] == "saved_snapshot"

            ws.send_text(json.dumps({"type": "configure", "source": "sim", "scenario": "demo"}))  # the old path
            _read_until(ws, loaded("demo"))
            assert A.world.scenario.source == "sim" and not A.world.live_loading
    finally:
        sys.modules.pop("app", None)
