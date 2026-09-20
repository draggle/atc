"""Prove the Elasticsearch memory works against the real cluster in .env. Run on a laptop:

    cd backend && .venv/bin/python tools/elastic_check.py

It connects, creates the tower-* indices, writes a tiny scripted session (three radio calls, one
clearance, a few radar frames, three waypoints), runs every search the resolver uses, and prints
what came back. Exit code 0 means the memory is live; the app will pick it up on next start.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from schemas import event
from tower.memory import ElasticMemory, index_name


def main() -> int:
    url = os.environ.get("ELASTIC_URL", "")
    if not url or not os.environ.get("ELASTIC_API_KEY"):
        print("ELASTIC_URL and ELASTIC_API_KEY are not set in .env")
        return 2
    mem = ElasticMemory.from_env()
    if mem is None:
        print(f"could not reach {url}; see the warning above")
        return 1
    print(f"connected to {url}")
    mem.close()  # use a synchronous writer for the check so nothing is left in flight
    mem = ElasticMemory(mem.client, sync=True, session=f"check-{int(time.time())}")
    print(f"session {mem.session}; indices: {', '.join(index_name(k) for k in ('transmissions', 'radar', 'waypoints'))} ...")

    calls = [("controller", "ACA123 climb flight level 350", 1.0),
             ("controller", "ACA123 turn left heading 270", 2.0),
             ("controller", "ACA123 descend flight level 240", 3.0)]
    for i, (spk, text, t) in enumerate(calls):
        mem.observe(event("transcript", {"id": f"chk-{i}", "t_end": t, "speaker": spk, "callsign": "ACA123",
                                         "text_raw": text, "text_norm": text, "asr_confidence": 0.9}, t=t))
    mem.observe(event("clearance_opened", {"id": "chk-c1", "callsign": "ACA123", "status": "open", "issued_at": 3.0,
                                           "items": [{"type": "altitude", "value": 240, "unit": "FL", "action": "descend"}]}, t=3.0))
    for t, alt in [(0.0, 25000), (10.0, 24600), (20.0, 24200)]:
        mem.observe(event("radar", {"t": t, "aircraft": [
            {"callsign": "ACA123", "x_nm": 0, "y_nm": 0, "alt_ft": alt, "target_alt_ft": 24000, "hdg_deg": 90,
             "gs_kt": 420, "lat": 43.68, "lon": -79.63},
            {"callsign": "ACA133", "x_nm": 12, "y_nm": 0, "alt_ft": 30000, "target_alt_ft": 30000, "hdg_deg": 90,
             "gs_kt": 420, "lat": 43.68, "lon": -79.35},
            {"callsign": "DAL456", "x_nm": 90, "y_nm": 0, "alt_ft": 30000, "target_alt_ft": 30000, "hdg_deg": 90,
             "gs_kt": 420, "lat": 43.68, "lon": -77.55}]}, t=t))
    mem.index_waypoints([{"name": "ESTIR", "x_nm": 0, "y_nm": 0, "lat": 43.7, "lon": -79.6},
                         {"name": "PIKAR", "x_nm": 30, "y_nm": 0, "lat": 43.7, "lon": -79.0},
                         {"name": "BOSOX", "x_nm": 60, "y_nm": 0, "lat": 43.7, "lon": -78.4}])
    mem.flush()  # one refresh call, so everything above is searchable now
    print(f"indexed {mem.docs_indexed} documents, {mem.errors} errors, refreshed")

    ok = True

    def show(name: str, got: object, want: bool) -> None:
        nonlocal ok
        ok = ok and want
        print(f"  {'OK ' if want else 'BAD'} {name}: {got}")

    h = mem.history("ACA123", n=3, query="left heading 2 7")
    show("history ranked by BM25", [x["text"] for x in (h or [])], bool(h) and "270" in h[0]["text"])
    n = mem.nearby("ACA123", radius_nm=30)
    show("nearby by geo query", n, bool(n) and [a["callsign"] for a in n] == ["ACA133"])
    tr = mem.track("ACA123", seconds=30)
    show("track over 30 s", tr, bool(tr) and tr["trend"] == "descending" and tr["samples"] == 3)
    w = mem.closest_waypoint("estor")
    show("closest waypoint (fuzzy)", w, bool(w) and w["name"] == "ESTIR")

    print("memory is live: restart the backend and the resolver trace will show [Elasticsearch]" if ok
          else "some searches failed; check the index mappings in Kibana (Dev Tools: GET tower-*/_mapping)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
