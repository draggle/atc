"""Gate naming and clustering, shared by tools/real_build.py and sim/live.py."""
import importlib.util
import json
import math
import zlib
from pathlib import Path

import pytest

from sim import regions
from sim.gates import GATE_SPREAD_NM, cluster_exits, gate_names
from sim.scenarios import REAL_DIR

COMMITTED = sorted(REAL_DIR.glob("*.json")) if REAL_DIR.exists() else []


@pytest.mark.skipif(not COMMITTED, reason="no built real scenarios")
@pytest.mark.parametrize("path", COMMITTED, ids=lambda p: p.stem)
def test_gate_names_reproduce_the_committed_scenarios(path):
    # The extracted source data is not in the repo, so the scenarios cannot be rebuilt. This pins
    # the naming instead: same seed, same count -> the names already on disk, in the same order.
    raw = json.loads(path.read_text())
    meta = raw["meta"]
    seed = zlib.crc32(f"{meta['region']}-{meta['hour_utc']}".encode())
    on_disk = [w["name"] for w in raw["waypoints"] if w["kind"] == "gate"]
    assert len(on_disk) == meta["gates"]
    assert gate_names(meta["gates"], seed=seed, taken=set()) == on_disk


def test_cluster_exits_groups_by_distance_along_the_boundary():
    R = 150.0
    step = 5.0 / R  # 5 NM along the boundary
    angles = [0.0, step, 2 * step, 1.0, 1.0 + 4 * step, -2.0]
    gates, gate_of = cluster_exits(angles, R, seed=1)
    assert len(gates) == 4 and len(set(gate_of)) == 4
    assert gate_of[0] == gate_of[1] == gate_of[2] and gate_of[3] != gate_of[4]
    by_name = {g.name: g for g in gates}
    for a, name in zip(angles, gate_of):
        g = by_name[name]
        assert g.kind == "gate" and abs(math.hypot(g.x_nm, g.y_nm) - R) < 0.01
        assert math.hypot(g.x_nm - R * math.cos(a), g.y_nm - R * math.sin(a)) <= GATE_SPREAD_NM
    first = by_name[gate_of[0]]
    assert math.atan2(first.y_nm, first.x_nm) == pytest.approx(step, abs=1e-3), "gate sits at the mean angle"
    assert [g.name for g in cluster_exits(angles, R, seed=1)[0]] == [g.name for g in gates], "seeded"
    assert cluster_exits([], R, seed=1) == ([], [])


def test_regions_lookup():
    r = regions.get("europe-core")
    assert (r.lat0, r.lon0, r.radius_nm, r.floor_ft) == (50.6, 6.2, 150.0, 24500)
    assert {"key": "toronto", "label": r.label} not in regions.catalog()
    assert [c["key"] for c in regions.catalog()] == [x.key for x in regions.REGIONS]
    with pytest.raises(KeyError, match="unknown region"):
        regions.get("atlantis")
    name, _lat, _lon, _rad, _floor, hours, _label = regions.REGIONS[0]  # real_extract.py unpacks like this
    assert name == "europe-core" and hours == [10, 16]


def test_real_build_still_builds_with_the_shared_helpers(tmp_path, monkeypatch):
    # tools/real_build.py cannot be re-run on the real archive here, so run it on a tiny made-up
    # extract: eight straight crossings of the uk region, two of them leaving at the same place.
    spec = importlib.util.spec_from_file_location("real_build", Path(__file__).parents[1] / "tools" / "real_build.py")
    rb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rb)
    monkeypatch.setattr(rb, "OUT", tmp_path / "out")
    reg = regions.get("uk")
    flights = []
    for i, brg in enumerate([0, 2, 45, 90, 135, 180, 225, 270]):
        # fly across the centre from the far side to bearing `brg`, a point every 30 s at 480 kt
        pts = []
        for k in range(76):
            d = (-150.0 + 4.0 * k) / 60.0  # degrees of arc from the centre along the track
            pts.append([30.0 * k, reg.lat0 + d * math.cos(math.radians(brg)),
                        reg.lon0 + d * math.sin(math.radians(brg)) / math.cos(math.radians(reg.lat0)), 36000, 480.0, float(brg)])
        flights.append({"callsign": f"BAW{i + 1}A", "actype": "A320", "inside_at_start": False, "points": pts})
    src = tmp_path / "uk_2026-09-18_1000.json"
    src.write_text(json.dumps({"region": "uk", "label": reg.label, "date": "2026-09-18", "hour_utc": 10, "window_s": 3600,
                               "lat0": reg.lat0, "lon0": reg.lon0, "radius_nm": reg.radius_nm, "floor_ft": reg.floor_ft,
                               "flights": flights}))
    dest = rb.build(src)
    from schemas import Scenario
    sc = Scenario.model_validate(json.loads(dest.read_text()))
    gates = [w.name for w in sc.waypoints if w.kind == "gate"]
    assert len(sc.flights) == 8 and sc.meta["gates"] == len(gates) == 7, "bearings 0 and 2 share a gate"
    assert gates == gate_names(7, seed=zlib.crc32(b"uk-10"), taken=set())
    assert all(f.route[-1] in gates and len(f.route) >= 2 for f in sc.flights)
