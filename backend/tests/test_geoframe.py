"""The flat simulator plane pinned to the real Earth. docs/10-roadmap.md phase 2."""
import asyncio

import numpy as np

from schemas import GeoFrame
from sim import scenarios as SC
from sim.geoframe import (DEFAULT_FRAME, bounds, great_circle_nm, path_lonlat, simplify_samples,
                          to_latlon, to_xy)
from world import World

EUROPE = GeoFrame(lat0=50.5, lon0=6.0, name="Western Europe core")


def test_round_trip_is_exact_across_a_600_nm_region():
    rng = np.random.default_rng(1)
    for frame in (DEFAULT_FRAME, EUROPE, GeoFrame(lat0=-33.9, lon0=151.2, name="south")):
        x, y = rng.uniform(-300, 300, 3000), rng.uniform(-300, 300, 3000)
        lat, lon = to_latlon(frame, x, y)
        x2, y2 = to_xy(frame, lat, lon)
        assert float(np.max(np.hypot(x - x2, y - y2))) < 0.1  # roadmap bar; measured ~1e-12
        assert np.all((lon >= -180) & (lon < 180)) and np.all(np.abs(lat) <= 90)


def test_centre_and_scalars():
    assert to_latlon(EUROPE, 0.0, 0.0) == (EUROPE.lat0, EUROPE.lon0)
    assert np.allclose(to_xy(EUROPE, EUROPE.lat0, EUROPE.lon0), (0.0, 0.0), atol=1e-9)
    lat, lon = to_latlon(EUROPE, 0.0, 60.0)
    assert isinstance(lat, float) and abs(lat - (EUROPE.lat0 + 1.0)) < 0.01 and abs(lon - EUROPE.lon0) < 1e-9


def test_distance_and_bearing_from_the_centre_are_exact():
    # Toronto Pearson to Ottawa: the flat distance from the centre must equal the great circle.
    ottawa = (45.3225, -75.6692)
    x, y = to_xy(DEFAULT_FRAME, *ottawa)
    gc = float(great_circle_nm(DEFAULT_FRAME.lat0, DEFAULT_FRAME.lon0, *ottawa))
    assert 180 < gc < 210
    assert abs(np.hypot(x, y) - gc) < 0.01
    assert x > 0 and y > 0  # Ottawa is north-east of Toronto


def test_pair_distances_stay_within_half_a_percent_in_a_600_nm_region():
    rng = np.random.default_rng(2)
    x, y = rng.uniform(-300, 300, 1500), rng.uniform(-300, 300, 1500)
    lat, lon = to_latlon(EUROPE, x, y)
    i, j = rng.integers(0, 1500, 4000), rng.integers(0, 1500, 4000)
    flat = np.hypot(x[i] - x[j], y[i] - y[j])
    gc = great_circle_nm(lat[i], lon[i], lat[j], lon[j])
    far = flat > 50
    assert float(np.max(np.abs(flat[far] - gc[far]) / gc[far])) < 0.005


def test_bounds_wrap_the_sector():
    (w, s), (e, n) = bounds(DEFAULT_FRAME, 100)
    assert w < DEFAULT_FRAME.lon0 < e and s < DEFAULT_FRAME.lat0 < n
    assert 3.0 < n - s < 3.6  # 200 NM is about 3.3 degrees of latitude


def test_simplify_keeps_corners_and_ends():
    leg1 = [(t, float(t), 0.0, 33000.0) for t in range(0, 600, 10)]        # east
    leg2 = [(600 + t, 600.0, float(t), 33000.0) for t in range(0, 600, 10)]  # then north
    out = simplify_samples(leg1 + leg2)
    assert out[0] == leg1[0] and out[-1] == leg2[-1]
    assert len(out) < 12
    assert any(abs(p[1] - 600.0) < 11 and abs(p[2]) < 11 for p in out), "the corner must survive"
    drawn = path_lonlat(DEFAULT_FRAME, leg1 + leg2)
    assert len(drawn) == len(out) and len(drawn[0]) == 4
    assert drawn[0][2] == 33000 and drawn[0][3] == 0.0


def test_scenarios_carry_a_frame_and_yaml_can_set_one():
    assert SC.load("demo").geo.lat0 == DEFAULT_FRAME.lat0
    raw = {"name": "x", "waypoints": [{"name": "A", "x_nm": 0, "y_nm": 0}], "flights": [],
           "geo": {"lat0": 50.5, "lon0": 6.0, "name": "Western Europe core"}}
    assert SC.from_dict(raw).geo.lon0 == 6.0
    assert SC.multiply(SC.load("demo"), 2).geo == SC.load("demo").geo


def test_every_position_event_carries_lat_lon():
    events = []
    w = World(events.append, synthesize=False, realtime=False)
    w.load("demo")

    def last(kind):
        return [e for e in events if e["type"] == kind][-1]["payload"]

    st = last("state")
    assert st["geo"]["lat0"] == DEFAULT_FRAME.lat0 and len(st["geo"]["bounds"]) == 2
    assert all("lat" in wp and "lon" in wp for wp in st["waypoints"])
    ac = last("radar")["aircraft"]
    assert ac and all(-90 <= a["lat"] <= 90 and -180 <= a["lon"] < 180 for a in ac)
    # the flat values are still there for the planner-facing code and the old radar
    assert all("x_nm" in a and "y_nm" in a for a in ac)
    plan = last("plan")
    for path in plan["paths"] + plan["baseline_paths"]:
        assert path["lonlat"] and len(path["lonlat"]) <= len(path["samples"])
        lon, lat, alt, t = path["lonlat"][0]
        x, y = to_xy(w.frame, lat, lon)
        assert abs(x - path["samples"][0][1]) < 0.05 and abs(y - path["samples"][0][2]) < 0.05

    w.start()
    d = w.add_disruption("intruder", -60.0, -80.0)
    dp = last("disruption")
    assert dp["id"] == d.id and "lat" in dp and dp["predicted_lonlat"]
    w.add_disruption("storm", 30.0, 20.0)
    assert all("lat" in z for z in last("state")["zones"])
    asyncio.run(w.tick(1.0))
    assert all("lat" in a for a in last("radar")["aircraft"])
