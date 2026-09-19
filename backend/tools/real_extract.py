"""Stage 1 of real traffic: one pass over an adsb.lol daily archive, several regions at once.

    python tools/real_extract.py --date 2026-09-18

Reads data/real/raw/v<date>-planes-readsb-prod-0.tar.a? as one stream (nothing is unpacked to disk),
parses every aircraft trace in a process pool, and keeps the airline flights that crossed each
region at cruise inside each time window. Writes data/real/extracted/<region>_<date>_<hhmm>.json
with the raw lat/lon tracks, so stage 2 (real_build.py) can be re-run in seconds.

Source: https://github.com/adsblol/globe_history_2026 (ODbL 1.0 and CC0). Trace format:
https://github.com/wiedehopf/readsb/blob/dev/README-json.md#trace-jsons
Point: [dt_s, lat, lon, alt_ft | "ground", gs_kt, track_deg, flags, vrate, details | None, ...]
"""
from __future__ import annotations

import argparse
import glob
import gzip
import io
import json
import math
import sys
import tarfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from airlines import AIRLINE_CALLSIGN  # noqa: E402
from sim.regions import REGIONS  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "real" / "raw"
OUT = REPO / "data" / "real" / "extracted"
R_NM = 3440.065

WINDOW_S = 3600
MIN_INSIDE_S = 240
MAX_LEVEL_CHANGE_FT = 4000
MAX_GAP_S = 150


class Cat(io.RawIOBase):
    """Several files read as one stream, so the split tar never has to be joined on disk."""

    def __init__(self, paths):
        self.files = [open(p, "rb") for p in paths]
        self.i = 0

    def readable(self):
        return True

    def readinto(self, b):
        while self.i < len(self.files):
            n = self.files[self.i].readinto(b)
            if n:
                return n
            self.i += 1
        return 0


def _dist_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * R_NM * math.asin(min(1.0, math.sqrt(a)))


def parse_one(raw: bytes) -> list[dict]:
    """All qualifying (region, window) crossings for one aircraft. Runs in a worker process."""
    try:
        d = json.loads(gzip.decompress(raw))
    except OSError:
        d = json.loads(raw)
    except Exception:
        return []
    trace = d.get("trace") or []
    if len(trace) < 20:
        return []
    # Cheap reject: bounding box of the whole trace against every region's bounding box.
    lats = [p[1] for p in trace]
    lons = [p[2] for p in trace]
    lo_lat, hi_lat, lo_lon, hi_lon = min(lats), max(lats), min(lons), max(lons)
    out: list[dict] = []
    for name, clat, clon, rad, floor, hours, _label in REGIONS:
        dlat = rad / 60.0
        dlon = rad / (60.0 * max(0.2, math.cos(math.radians(clat))))
        if hi_lat < clat - dlat or lo_lat > clat + dlat or hi_lon < clon - dlon or lo_lon > clon + dlon:
            continue
        # contiguous runs of points that are inside the circle and at or above the floor
        runs: list[list] = []
        cur: list = []
        last_t = None
        callsign = None
        for p in trace:
            alt = p[3]
            det = p[8] if len(p) > 8 and isinstance(p[8], dict) else None
            if det and det.get("flight"):
                callsign = det["flight"].strip()
            new_leg = bool(p[6] & 2) if isinstance(p[6], int) else False
            inside = isinstance(alt, (int, float)) and alt >= floor and _dist_nm(clat, clon, p[1], p[2]) <= rad
            gap = last_t is not None and p[0] - last_t > MAX_GAP_S
            if cur and (not inside or gap or new_leg):
                runs.append(cur)
                cur = []
            if inside:
                cur.append((p[0], p[1], p[2], alt, p[4], p[5], callsign))
            last_t = p[0]
        if cur:
            runs.append(cur)
        for run in runs:
            if run[-1][0] - run[0][0] < MIN_INSIDE_S or len(run) < 12:
                continue
            alts = [q[3] for q in run]
            if max(alts) - min(alts) > MAX_LEVEL_CHANGE_FT:
                continue  # climbing or descending through: not the en-route traffic we model
            cs = Counter(q[6] for q in run if q[6]).most_common(1)
            cs = cs[0][0] if cs else None
            if not cs or not AIRLINE_CALLSIGN.match(cs):
                continue
            for h in hours:
                w0, w1 = h * 3600, h * 3600 + WINDOW_S
                if run[0][0] >= w1 or run[-1][0] <= w0:
                    continue
                # keep the part of the crossing from the window start on; entry is whichever is later
                pts = [q for q in run if q[0] >= w0]
                if len(pts) < 12 or pts[0][0] >= w1:
                    continue
                out.append({
                    "region": name, "hour": h, "icao": d.get("icao"), "callsign": cs,
                    "actype": d.get("t") or "A320", "reg": d.get("r"),
                    "inside_at_start": run[0][0] < w0,
                    "points": [[round(q[0] - w0, 1), round(q[1], 5), round(q[2], 5), int(q[3]),
                                None if q[4] is None else round(q[4], 1),
                                None if q[5] is None else round(q[5], 1)] for q in pts],
                })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-09-18")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="stop after N traces (smoke test)")
    args = ap.parse_args()
    tag = args.date.replace("-", ".")
    parts = sorted(glob.glob(str(RAW / f"v{tag}-planes-readsb-*-0.tar.a?")))
    if not parts:
        sys.exit(f"no archive parts for {args.date} under {RAW}")
    OUT.mkdir(parents=True, exist_ok=True)
    found: dict[tuple[str, int], list[dict]] = {}
    t0 = time.time()
    n = 0
    tf = tarfile.open(fileobj=io.BufferedReader(Cat(parts), 1 << 22), mode="r|")
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        pending = []

        def drain(block: bool) -> None:
            nonlocal pending
            keep = []
            for fut in pending:
                if block or fut.done():
                    for hit in fut.result():
                        found.setdefault((hit["region"], hit["hour"]), []).append(hit)
                else:
                    keep.append(fut)
            pending = keep

        for m in tf:
            if not m.isfile() or "/traces/" not in m.name:
                continue
            raw = tf.extractfile(m).read()
            pending.append(pool.submit(parse_one, raw))
            n += 1
            if len(pending) > 400:
                drain(block=False)
                while len(pending) > 1200:  # do not let raw bytes pile up in memory
                    time.sleep(0.05)
                    drain(block=False)
            if n % 10000 == 0:
                hits = sum(len(v) for v in found.values())
                print(f"{n:>7} traces, {hits} crossings, {time.time() - t0:.0f} s", flush=True)
            if args.limit and n >= args.limit:
                break
        drain(block=True)

    labels = {r[0]: r for r in REGIONS}
    for (region, hour), flights in sorted(found.items()):
        name, clat, clon, rad, floor, _hours, label = labels[region]
        path = OUT / f"{region}_{args.date}_{hour:02d}00.json"
        path.write_text(json.dumps({
            "region": region, "label": label, "date": args.date, "hour_utc": hour, "window_s": WINDOW_S,
            "lat0": clat, "lon0": clon, "radius_nm": rad, "floor_ft": floor,
            "source": "adsb.lol globe_history, ODbL 1.0 and CC0",
            "flights": sorted(flights, key=lambda f: f["points"][0][0]),
        }))
        print(f"{path.name}: {len(flights)} flights")
    print(f"done: {n} traces in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
