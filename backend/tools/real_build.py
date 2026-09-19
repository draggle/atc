"""Stage 2 of real traffic: extracted crossings -> a scenario the simulator can load.

    python tools/real_build.py            # builds every file in data/real/extracted/
    python tools/real_build.py --only europe-core_2026-09-18_1000

For each flight that really crossed the region at cruise:
  - project its recorded track into the flat sector plane around the region centre
  - its ROUTE is the track it actually flew, simplified to a handful of vertices (hidden fixes),
    ending at a named exit GATE. Left alone, the simulator flies what the aircraft really flew.
    That is the "standard line". Tower's plan is the direct path to the same gate.
  - level = median recorded altitude rounded to a flight level, speed = median ground speed

Gates: real data has no fix names, and the radio needs one ("proceed direct KOVAL"). Exit points
cluster naturally where airways leave the region, so we group them (at most 8 NM from a gate) and
give each group a pronounceable five-letter name. Real fix names are made-up words too. These are
ours, and the screen says so. The clustering and naming live in sim/gates.py, shared with live mode.

Output: backend/scenarios/real/<region>_<date>_<hhmm>.json, a few hundred KB, committed to the repo
so nobody else needs the 4 GB archive. Data: adsb.lol, ODbL 1.0 and CC0.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from schemas import FlightSpec, GeoFrame, Scenario, Waypoint  # noqa: E402
from sim.gates import cluster_exits  # noqa: E402
from sim.geoframe import to_xy  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EXTRACTED = REPO / "data" / "real" / "extracted"
OUT = REPO / "backend" / "scenarios" / "real"

EDGE_FRACTION = 0.85      # a clean crossing starts and ends near the boundary
SIMPLIFY_NM = 1.5         # Douglas-Peucker tolerance for the flown track
MAX_VERTICES = 8


def douglas_peucker(pts: np.ndarray, tol: float) -> list[int]:
    """Indices of the points kept. pts is (n, 2)."""
    keep = {0, len(pts) - 1}
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        seg = pts[b] - pts[a]
        L = float(np.hypot(*seg)) or 1e-9
        rel = pts[a + 1:b] - pts[a]
        d = np.abs(seg[0] * rel[:, 1] - seg[1] * rel[:, 0]) / L
        k = int(np.argmax(d))
        if d[k] > tol:
            m = a + 1 + k
            keep.add(m)
            stack += [(a, m), (m, b)]
    return sorted(keep)


def build(path: Path) -> Path | None:
    raw = json.loads(path.read_text())
    frame = GeoFrame(lat0=raw["lat0"], lon0=raw["lon0"], name=raw["label"], shape="circle")
    R = float(raw["radius_nm"])
    flights = []
    dropped = {"edge": 0, "short": 0, "duplicate": 0}
    seen: set[str] = set()
    for f in raw["flights"]:
        pts = np.asarray([[p[0], p[1], p[2], p[3], p[4] if p[4] is not None else np.nan] for p in f["points"]], dtype=float)
        x, y = to_xy(frame, pts[:, 1], pts[:, 2])
        xy = np.column_stack([x, y])
        r = np.hypot(x, y)
        if r[-1] < EDGE_FRACTION * R or (not f["inside_at_start"] and r[0] < EDGE_FRACTION * R):
            dropped["edge"] += 1  # the recording stops or starts mid-region: not a clean crossing
            continue
        flown = float(np.sum(np.hypot(np.diff(x), np.diff(y))))
        dur = float(pts[-1, 0] - pts[0, 0])
        if flown < 40 or dur < 240:
            dropped["short"] += 1
            continue
        if f["callsign"] in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(f["callsign"])
        gs = float(np.nanmedian(pts[:, 4])) if np.isfinite(pts[:, 4]).any() else flown / dur * 3600.0
        if not (150 <= gs <= 650):
            gs = flown / dur * 3600.0
        level = int(round(float(np.median(pts[:, 3])) / 1000.0) * 1000)
        idx = douglas_peucker(xy, SIMPLIFY_NM)
        tol = SIMPLIFY_NM
        while len(idx) > MAX_VERTICES:
            tol *= 1.5
            idx = douglas_peucker(xy, tol)
        flights.append({"callsign": f["callsign"], "actype": (f.get("actype") or "A320")[:4], "entry_t": max(0.0, float(pts[0, 0])),
                        "gs": round(gs), "alt": level, "verts": xy[idx], "exit": xy[-1], "flown_nm": flown,
                        "exit_angle": math.atan2(xy[-1, 1], xy[-1, 0])})
    if len(flights) < 5:
        print(f"{path.name}: only {len(flights)} clean crossings, skipped")
        return None

    # ---- gates: cluster exits by angle around the boundary
    gates, gate_names_by_flight = cluster_exits(
        [f["exit_angle"] for f in flights], R, seed=zlib.crc32(f"{raw['region']}-{raw['hour_utc']}".encode()))
    waypoints: list[Waypoint] = list(gates)
    gate_of: dict[int, str] = dict(enumerate(gate_names_by_flight))

    specs: list[FlightSpec] = []
    flown_total = 0.0
    for i, f in enumerate(sorted(range(len(flights)), key=lambda k: flights[k]["entry_t"])):
        fl = flights[f]
        route: list[str] = []
        verts = fl["verts"][:-1]  # the last vertex is replaced by the gate
        for k, (vx, vy) in enumerate(verts):
            nm = f"T{i:03d}{chr(65 + k)}"
            waypoints.append(Waypoint(name=nm, x_nm=round(float(vx), 2), y_nm=round(float(vy), 2), kind="hidden"))
            route.append(nm)
        route.append(gate_of[f])
        specs.append(FlightSpec(callsign=fl["callsign"], actype=fl["actype"], entry_time_s=round(fl["entry_t"], 1),
                                route=route, alt_ft=fl["alt"], gs_kt=fl["gs"]))
        flown_total += fl["flown_nm"]

    hh = int(raw["hour_utc"])
    sc = Scenario(
        name=f"real/{raw['region']}_{raw['date']}_{hh:02d}00", seed=7, sector_nm=2 * R, waypoints=waypoints, flights=specs,
        separation_buffer_nm=3.0, geo=frame, source="real",
        description=f"{len(specs)} airline flights that really crossed {raw['label']} at cruise, {raw['date']} {hh:02d}:00 to {hh + 1:02d}:00 UTC.",
        meta={"region": raw["region"], "label": raw["label"], "date": raw["date"], "hour_utc": hh,
              "window_s": raw["window_s"], "radius_nm": R, "floor_ft": raw["floor_ft"], "gates": len(gates),
              "flown_nm_total": round(flown_total, 1), "dropped": dropped,
              "attribution": "Flight data: adsb.lol, ODbL 1.0 and CC0. Gate names are ours.",
              "caveats": "Recorded tracks were shaped by wind, weather and closed airspace we cannot see, so miles saved is an upper bound. Levels and speeds are held constant at each flight's median."},
    )
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"{raw['region']}_{raw['date']}_{hh:02d}00.json"
    dest.write_text(json.dumps(sc.model_dump(), separators=(",", ":")))
    print(f"{dest.name}: {len(specs)} flights, {len(gates)} gates, {dest.stat().st_size // 1024} KB, dropped {dropped}")
    return dest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    args = ap.parse_args()
    files = sorted(EXTRACTED.glob("*.json"))
    if args.only:
        files = [f for f in files if f.stem == args.only]
    if not files:
        sys.exit(f"nothing to build under {EXTRACTED}. Run tools/real_extract.py first.")
    for f in files:
        build(f)


if __name__ == "__main__":
    main()
