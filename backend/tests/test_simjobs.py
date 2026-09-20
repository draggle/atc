"""Sim jobs in a subprocess: progress in order, cancel, caps, one at a time, honest captions."""
import asyncio
import threading
import time

import pytest

from tools import simjobs as SJ


def _wait_free(timeout: float = 5.0) -> None:
    """Tests share the one-job-at-a-time registry; wait for whatever the last test left."""
    t0 = time.perf_counter()
    while SJ.current_job() is not None and time.perf_counter() - t0 < timeout:
        time.sleep(0.05)


def test_montecarlo_job_completes_with_rows_and_ordered_progress():
    _wait_free()
    events: list[dict] = []
    jid = SJ.start_job("montecarlo", {"scenario": "demo", "runs": 2, "seed": 1}, events.append)
    assert isinstance(jid, str) and jid
    st = SJ.wait(jid, timeout=40)
    assert st["status"] == "done", st.get("error")
    assert st["progress"] == 1.0 and st["eta_s"] == 0.0
    rows = st["result"]["rows"]
    assert [r["arm"] for r in rows] == ["fixed", "tower_off", "tower_on"]
    for r in rows:
        assert {"arm", "los_per_h", "closest_p5_nm", "miles_vs_fixed_pct", "errors_caught", "errors_injected"} <= set(r)
    # progress: running at 0, then strictly inside (0, 1), then done at 1
    statuses = [e["status"] for e in events]
    assert statuses[0] == "running" and statuses[-1] == "done" and "done" not in statuses[:-1]
    ps = [e["progress"] for e in events]
    assert ps[0] == 0.0 and ps[-1] == 1.0
    assert all(0 < p < 1 for p in ps[1:-1]) and len(ps) >= 3
    assert ps == sorted(ps)
    assert all(e["job_id"] == jid and e["kind"] == "montecarlo" for e in events)
    assert events[1]["eta_s"] is not None and events[1]["eta_s"] >= 0
    caption = st["result"]["caption"]
    for label in ("fixed routes", "squack off", "squack"):
        assert label in caption
    assert "2 runs" in caption and "demo" in caption and "2% readback errors" in caption
    assert st["result"]["file"] and st["result"]["file"].endswith(f"{jid}.json")
    assert SJ.job_status(jid)["status"] == "done"


def test_cancel_stops_a_running_job():
    _wait_free()
    events: list[dict] = []
    jid = SJ.start_job("montecarlo", {"scenario": "dense", "runs": 20}, events.append)
    time.sleep(0.5)
    proc = SJ._JOBS[jid].proc
    SJ.cancel_job(jid)
    st = SJ.wait(jid, timeout=10)
    assert st["status"] == "cancelled"
    assert "result" not in st
    assert events[-1]["status"] == "cancelled"
    assert not proc.is_alive()
    assert SJ.current_job() is None
    SJ.cancel_job(jid)  # cancelling a finished job is a no-op
    assert SJ.job_status(jid)["status"] == "cancelled"


def test_caps_clamp_and_say_so():
    public, child = SJ.normalize_params("montecarlo", {"scenario": "demo", "runs": 50, "density": 9, "error_rate": -1})
    assert public["runs"] == 20 and child["runs"] == 20
    assert public["density"] == SJ.DENSITY_MAX and public["error_rate"] == 0.0
    assert any("runs clamped from 50 to 20" == n for n in public["notes"])
    assert public["buffer_nm"] == 3.0  # the scenario's own buffer when none is given
    public, _ = SJ.normalize_params("sweep", {"scenario": "demo", "densities": [1, 1.5, 2, 2.5, 3], "buffers": [1, 2, 3], "runs": 9})
    assert public["densities"] == [1.0, 1.5, 2.0, 2.5] and public["buffers"] == [1.0, 2.0] and public["runs"] == 4
    assert any("densities cut" in n for n in public["notes"]) and any("buffers cut" in n for n in public["notes"])
    public, _ = SJ.normalize_params("montecarlo", None)
    assert public["runs"] == SJ.MC_RUNS_DEFAULT and public["notes"] == []
    with pytest.raises(ValueError):
        SJ.normalize_params("nope", {})
    # a started job carries the clamped params in every payload
    _wait_free()
    events: list[dict] = []
    jid = SJ.start_job("montecarlo", {"scenario": "demo", "runs": 50}, events.append)
    assert events[0]["params"]["runs"] == 20 and "runs clamped from 50 to 20" in events[0]["params"]["notes"]
    SJ.cancel_job(jid)
    SJ.wait(jid, timeout=10)


def test_second_request_returns_the_running_job():
    _wait_free()
    a: list[dict] = []
    b: list[dict] = []
    jid = SJ.start_job("montecarlo", {"scenario": "dense", "runs": 20}, a.append)
    jid2 = SJ.start_job("sweep", {"scenario": "demo"}, b.append)
    assert jid2 == jid
    assert b == []  # nothing was started for the second caller
    st = SJ.job_status(jid)
    assert st["status"] == "running" and "already running" in st["notice"]
    SJ.cancel_job(jid)
    assert SJ.wait(jid, timeout=10)["status"] == "cancelled"


def test_sweep_job_rows_and_caption():
    _wait_free()
    events: list[dict] = []
    jid = SJ.start_job("sweep", {"scenario": "demo", "densities": [1.0], "buffers": [3.0], "runs": 1}, events.append)
    st = SJ.wait(jid, timeout=40)
    assert st["status"] == "done", st.get("error")
    rows = st["result"]["rows"]
    assert len(rows) == 3 and {r["arm"] for r in rows} == {"fixed", "tower_off", "tower_on"}
    for r in rows:
        assert {"density", "buffer_nm", "arm", "los_per_h", "miles_vs_fixed_pct"} <= set(r)
        assert r["density"] == 1.0 and r["buffer_nm"] == 3.0
    assert "fixed routes" in st["result"]["caption"] and "squack" in st["result"]["caption"]
    assert st["params"]["densities"] == [1.0] and st["params"]["runs"] == 1


def test_sweep_caption_names_the_density_where_fixed_routes_break():
    rows = [
        {"density": 1.0, "buffer_nm": 3.0, "arm": "fixed", "los_per_h": 0.4, "miles_vs_fixed_pct": 0.0},
        {"density": 1.5, "buffer_nm": 3.0, "arm": "fixed", "los_per_h": 1.3, "miles_vs_fixed_pct": 0.0},
        {"density": 1.0, "buffer_nm": 3.0, "arm": "tower_on", "los_per_h": 0.0, "miles_vs_fixed_pct": -7.0},
        {"density": 1.5, "buffer_nm": 3.0, "arm": "tower_on", "los_per_h": 0.02, "miles_vs_fixed_pct": -6.0},
    ]
    cap = SJ.sweep_caption({"scenario": "dense", "runs": 2}, rows)
    assert "first exceed 1 LoS per flight hour at 1.5x" in cap and "squack peaks at 0.02" in cap


async def test_progress_is_delivered_on_the_event_loop():
    """From inside a running loop, on_progress runs on the loop thread, never the watcher thread."""
    _wait_free()
    main_thread = threading.get_ident()
    seen: list[tuple[int, str]] = []
    done = asyncio.Event()

    def on_progress(p: dict) -> None:
        seen.append((threading.get_ident(), p["status"]))
        if p["status"] != "running":
            done.set()

    jid = SJ.start_job("montecarlo", {"scenario": "demo", "runs": 1, "seed": 2}, on_progress)
    await asyncio.wait_for(done.wait(), timeout=40)
    assert all(t == main_thread for t, _ in seen)
    assert seen[0][1] == "running" and seen[-1][1] == "done"
    assert SJ.job_status(jid)["status"] == "done"


def test_unknown_job_status():
    st = SJ.job_status("nope")
    assert st["status"] == "unknown" and st["error"] == "no such job"
