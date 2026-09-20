"""Background sim jobs for the squack agent: Monte Carlo and density sweeps in a subprocess.

    job_id = start_job("montecarlo", {"scenario": "demo", "runs": 8}, on_progress)
    job_status(job_id)      -> the same dict shape the last on_progress call carried
    cancel_job(job_id)

Why a subprocess and not a thread: the live clock (`app.clock`, 1 Hz to 4 Hz) and the risk
rollouts share the GIL with anything in-process, and one Monte Carlo run is seconds of pure Python
and numpy. A spawned child has its own interpreter, so the tick never waits on a run. The child
gets a scenario name (or a JSON-dumped Scenario) and sends progress over a pipe; a watcher thread
in the parent turns pipe messages into `sim_job` payloads and hands them to the asyncio loop with
`call_soon_threadsafe`, so `on_progress` always runs on the loop that started the job (or, with
no loop, on the watcher thread).

Payload shape, same for progress, status and the final event:
    {job_id, kind, status: running|done|failed|cancelled, progress: 0..1, eta_s, params,
     result?: {rows, caption, file}, error?, notice?}

Rules: one job at a time (a second request gets the running job's id and a notice), caps on runs
and points, a 90 s wall-clock cap after which the child is killed and the job reported failed.
The child writes nothing; the parent saves the raw result as JSON under eval/out/<job_id>.json.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import math
import multiprocessing as mp
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

OUT_DIR = Path(__file__).resolve().parent.parent / "eval" / "out"

Kind = Literal["montecarlo", "sweep"]
KINDS: tuple[str, ...] = ("montecarlo", "sweep")

# Caps. The agent runs while the demo is live, so a job must finish inside a coffee sip.
MC_RUNS_DEFAULT, MC_RUNS_CAP = 8, 20
SWEEP_RUNS_DEFAULT, SWEEP_RUNS_CAP = 2, 4
SWEEP_DENSITIES_DEFAULT, SWEEP_DENSITIES_CAP = (1.0, 1.5, 2.0), 4
SWEEP_BUFFERS_CAP = 2
ERROR_RATE_DEFAULT = 0.02
DENSITY_MIN, DENSITY_MAX = 0.25, 4.0
BUFFER_MIN, BUFFER_MAX = 0.0, 10.0
WALL_CAP_S = 90.0
POLL_S = 0.1

ARM_LABEL = {"fixed": "fixed routes", "tower_off": "squack off", "tower_on": "squack"}


# ----------------------------------------------------------------------------- job records

@dataclass
class Job:
    job_id: str
    kind: str
    params: dict[str, Any]
    status: str = "running"
    progress: float = 0.0
    eta_s: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    notice: str | None = None
    started: float = field(default_factory=time.perf_counter)
    finished: float | None = None
    proc: Any = None
    cancel_requested: bool = False
    loop: asyncio.AbstractEventLoop | None = None
    on_progress: Callable[[dict], None] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "job_id": self.job_id, "kind": self.kind, "status": self.status,
            "progress": round(self.progress, 4), "eta_s": None if self.eta_s is None else round(self.eta_s, 1),
            "params": dict(self.params),
            "elapsed_s": round((self.finished or time.perf_counter()) - self.started, 2),
        }
        if self.result is not None:
            out["result"] = self.result
        if self.error is not None:
            out["error"] = self.error
        if self.notice is not None:
            out["notice"] = self.notice
        return out


_JOBS: dict[str, Job] = {}
_CURRENT: Job | None = None
_REG_LOCK = threading.Lock()


# ----------------------------------------------------------------------------- params

def _clamp(v: Any, lo: float, hi: float, default: float, name: str, notes: list[str]) -> float:
    try:
        x = float(v) if v is not None else default
    except (TypeError, ValueError):
        x = default
    if math.isnan(x):
        x = default
    c = min(hi, max(lo, x))
    if c != x:
        notes.append(f"{name} clamped from {x:g} to {c:g}")
    return c


def _scenario_arg(params: dict[str, Any]) -> tuple[str | dict, str, float]:
    """(what the child loads, display name, the scenario's own buffer). A Scenario instance is
    passed as its JSON dict so the child never needs to unpickle a class from the parent."""
    sc = params.get("scenario", "demo")
    if hasattr(sc, "model_dump"):
        d = sc.model_dump(mode="json")
        return d, str(d.get("name", "scenario")), float(d.get("separation_buffer_nm", 3.0))
    if isinstance(sc, dict):
        return sc, str(sc.get("name", "scenario")), float(sc.get("separation_buffer_nm", 3.0))
    name = str(sc or "demo")
    from sim.scenarios import load  # cheap: yaml or a small json
    loaded = load(name)
    return name, name, float(loaded.separation_buffer_nm)


def normalize_params(kind: str, params: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns (public params for the payload, child params). Caps are applied and named in
    `params["notes"]` so the agent can say "runs clamped from 50 to 20" honestly."""
    params = dict(params or {})
    notes: list[str] = []
    sc_child, sc_name, sc_buf = _scenario_arg(params)
    seed = int(params.get("seed", 0) or 0)
    error_rate = _clamp(params.get("error_rate"), 0.0, 1.0, ERROR_RATE_DEFAULT, "error_rate", notes)
    if kind == "montecarlo":
        runs = int(_clamp(params.get("runs"), 1, MC_RUNS_CAP, MC_RUNS_DEFAULT, "runs", notes))
        density = _clamp(params.get("density"), DENSITY_MIN, DENSITY_MAX, 1.0, "density", notes)
        buf_in = params.get("buffer_nm")
        buffer_nm = sc_buf if buf_in is None else _clamp(buf_in, BUFFER_MIN, BUFFER_MAX, sc_buf, "buffer_nm", notes)
        public = {"scenario": sc_name, "runs": runs, "density": density, "error_rate": error_rate,
                  "buffer_nm": buffer_nm, "seed": seed, "notes": notes}
        child = {**public, "scenario": sc_child}
        return public, child
    if kind == "sweep":
        runs = int(_clamp(params.get("runs"), 1, SWEEP_RUNS_CAP, SWEEP_RUNS_DEFAULT, "runs", notes))
        dens_in = params.get("densities") or list(SWEEP_DENSITIES_DEFAULT)
        if not isinstance(dens_in, (list, tuple)):
            dens_in = [dens_in]
        densities = [_clamp(d, DENSITY_MIN, DENSITY_MAX, 1.0, "density", notes) for d in dens_in]
        if len(densities) > SWEEP_DENSITIES_CAP:
            notes.append(f"densities cut from {len(densities)} to {SWEEP_DENSITIES_CAP} points")
            densities = densities[:SWEEP_DENSITIES_CAP]
        bufs_in = params.get("buffers")
        if bufs_in is None:
            bufs_in = [sc_buf]
        if not isinstance(bufs_in, (list, tuple)):
            bufs_in = [bufs_in]
        buffers = [_clamp(b, BUFFER_MIN, BUFFER_MAX, sc_buf, "buffer_nm", notes) for b in bufs_in]
        if len(buffers) > SWEEP_BUFFERS_CAP:
            notes.append(f"buffers cut from {len(buffers)} to {SWEEP_BUFFERS_CAP}")
            buffers = buffers[:SWEEP_BUFFERS_CAP]
        public = {"scenario": sc_name, "runs": runs, "densities": densities, "buffers": buffers,
                  "error_rate": error_rate, "seed": seed, "notes": notes}
        child = {**public, "scenario": sc_child}
        return public, child
    raise ValueError(f"unknown job kind {kind!r}; expected one of {KINDS}")


# ----------------------------------------------------------------------------- the child

def _child_main(kind: str, params: dict[str, Any], conn: Any) -> None:
    """Runs in the spawned process. Sends ("progress", done, total), then ("done", result) or
    ("error", text). Nothing is written to disk here."""
    try:
        from eval.montecarlo import run
        from eval.sweep import sweep
        from schemas import Scenario
        from sim.scenarios import load

        sc = params["scenario"]
        scenario = Scenario.model_validate(sc) if isinstance(sc, dict) else load(sc)

        def progress(done: int, total: int) -> None:
            conn.send(("progress", done, total))

        if kind == "montecarlo":
            res = run(scenario, n_runs=params["runs"], seed=params["seed"], error_rate=params["error_rate"],
                      density=params["density"], buffer_nm=params["buffer_nm"], on_run=progress)
            conn.send(("done", res))
        else:
            from dataclasses import asdict
            rows = sweep(scenario, params["densities"], params["buffers"], params["runs"],
                         error_rate=params["error_rate"], seed=params["seed"], on_run=progress)
            conn.send(("done", {"rows": [asdict(r) for r in rows]}))
    except BaseException as e:  # noqa: BLE001 - the parent must hear about every failure
        with contextlib.suppress(Exception):
            conn.send(("error", f"{type(e).__name__}: {e}"))
    finally:
        with contextlib.suppress(Exception):
            conn.close()


# ----------------------------------------------------------------------------- results and captions

def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{abs(v):.1f}% {'fewer' if v < 0 else 'more'} miles"


def montecarlo_rows(res: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for arm, a in res["arms"].items():
        rows.append({
            "arm": arm,
            "los_per_h": round(float(a["los_per_flight_hour"]), 4),
            "closest_p5_nm": None if a["closest_p5_nm"] is None else round(float(a["closest_p5_nm"]), 2),
            "miles_vs_fixed_pct": None if a["miles_vs_baseline_pct"] is None else round(float(a["miles_vs_baseline_pct"]), 2),
            "errors_caught": int(a["errors_caught"]),
            "errors_injected": int(a["errors_injected"]),
            "los_total": int(a["los_total"]),
            "flight_hours": round(float(a["flight_hours"]), 2),
        })
    return rows


def montecarlo_caption(params: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    by = {r["arm"]: r for r in rows}
    n, d, e = params["runs"], params["density"], params["error_rate"]
    head = f"{n} run{'s' if n != 1 else ''} on {params['scenario']} at {d:g}x traffic, {e * 100:g}% readback errors, " \
           f"buffer {params['buffer_nm']:g} NM"
    parts = []
    off, on = by.get("tower_off"), by.get("tower_on")
    if off is not None and on is not None and off["los_total"] == 0 and on["los_total"] == 0:
        collapsed = {"tower_off", "tower_on"}
    else:
        collapsed = set()
    for arm in ("fixed", "tower_off", "tower_on"):
        r = by.get(arm)
        if r is None:
            continue
        label = ARM_LABEL[arm]
        if arm in collapsed:
            if arm == "tower_off":
                parts.append(f"squack off and squack 0 LoS in {r['flight_hours']:g} flight hours")
            continue
        if r["los_total"] == 0:
            parts.append(f"{label} 0 LoS in {r['flight_hours']:g} flight hours")
        else:
            parts.append(f"{label} {r['los_per_h']:.3g} LoS per flight hour" if arm == "fixed"
                         else f"{label} {r['los_per_h']:.3g}")
    tail = ""
    on = by.get("tower_on")
    if on is not None and on["miles_vs_fixed_pct"] is not None:
        tail = f", {_pct(on['miles_vs_fixed_pct'])}"
        if on["errors_injected"]:
            tail += f", {on['errors_caught']} of {on['errors_injected']} injected errors caught"
    return f"{head}: {', '.join(parts)}{tail}. Simulated, no winds."


def sweep_rows(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{
        "density": r["density"], "buffer_nm": r["buffer_nm"], "arm": r["arm"],
        "los_per_h": round(float(r["los_per_hour"]), 4),
        "miles_vs_fixed_pct": None if r.get("miles_vs_fixed_pct") is None else round(float(r["miles_vs_fixed_pct"]), 2),
        "closest_p5_nm": r.get("closest_p5_nm"), "los_total": int(r["los"]),
    } for r in raw_rows]


def sweep_caption(params: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    dens = sorted({r["density"] for r in rows})
    bufs = sorted({r["buffer_nm"] for r in rows})
    n = params["runs"]
    head = f"Sweep on {params['scenario']}, {n} run{'s' if n != 1 else ''} per point, " \
           f"buffer {' and '.join(f'{b:g}' for b in bufs)} NM, density {dens[0]:g} to {dens[-1]:g}x"
    b0 = bufs[0]
    fixed = sorted((r["density"], r["los_per_h"]) for r in rows if r["arm"] == "fixed" and r["buffer_nm"] == b0)
    over = [d for d, v in fixed if v > 1.0]
    if over:
        fixed_part = f"fixed routes first exceed 1 LoS per flight hour at {over[0]:g}x"
    elif fixed:
        fixed_part = f"fixed routes stay under 1 LoS per flight hour through {fixed[-1][0]:g}x (peak {max(v for _, v in fixed):.3g})"
    else:
        fixed_part = "no fixed-route arm"
    on = [(r["density"], r["los_per_h"]) for r in rows if r["arm"] == "tower_on" and r["buffer_nm"] == b0]
    on_part = ""
    if on:
        worst = max(v for _, v in on)
        on_part = f"; squack peaks at {worst:.3g} LoS per flight hour" if worst > 0 else "; squack has 0 LoS at every point"
    return f"{head}: {fixed_part}{on_part}. Simulated, no winds."


def _build_result(job: Job, raw: dict[str, Any]) -> dict[str, Any]:
    if job.kind == "montecarlo":
        rows = montecarlo_rows(raw)
        caption = montecarlo_caption(job.params, rows)
    else:
        rows = sweep_rows(raw["rows"])
        caption = sweep_caption(job.params, rows)
    path = _save(job, raw, rows, caption)
    return {"rows": rows, "caption": caption, "file": str(path) if path else None}


def _save(job: Job, raw: dict[str, Any], rows: list[dict], caption: str) -> Path | None:
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUT_DIR / f"job_{job.kind}_{job.job_id}.json"
        path.write_text(json.dumps({"job_id": job.job_id, "kind": job.kind, "params": job.params,
                                    "caption": caption, "rows": rows, "raw": raw,
                                    "wall_s": round(time.perf_counter() - job.started, 2)}, indent=1, default=str))
        return path
    except OSError:
        return None


# ----------------------------------------------------------------------------- the watcher

def _deliver(job: Job) -> None:
    """Call on_progress on the loop that started the job when there is one, else right here."""
    if job.on_progress is None:
        return
    payload = job.payload()
    loop = job.loop
    if loop is not None and not loop.is_closed():
        try:
            loop.call_soon_threadsafe(job.on_progress, payload)
            return
        except RuntimeError:  # loop closed between the check and the call
            pass
    with contextlib.suppress(Exception):
        job.on_progress(payload)


def _finish(job: Job, status: str, *, error: str | None = None, result: dict | None = None) -> None:
    global _CURRENT
    with job.lock:
        if job.status != "running":
            return
        job.status = status
        job.error = error
        job.result = result
        job.finished = time.perf_counter()
        job.eta_s = 0.0 if status == "done" else None
        if status == "done":
            job.progress = 1.0
    with _REG_LOCK:
        if _CURRENT is job:
            _CURRENT = None
    _deliver(job)


def _kill(proc: Any) -> None:
    with contextlib.suppress(Exception):
        if proc.is_alive():
            proc.terminate()
            proc.join(2.0)
        if proc.is_alive():
            proc.kill()
            proc.join(1.0)
    # No proc.close(): the handle stays inspectable (is_alive, exitcode) and is released on GC.


def _watch(job: Job, conn: Any) -> None:
    proc = job.proc
    try:
        while True:
            if job.cancel_requested:
                _kill(proc)
                _finish(job, "cancelled", error="cancelled")
                return
            if time.perf_counter() - job.started > WALL_CAP_S:
                _kill(proc)
                _finish(job, "failed", error=f"wall-clock cap of {WALL_CAP_S:g} s hit; try fewer runs or a smaller sweep")
                return
            got = False
            try:
                got = conn.poll(POLL_S)
            except (OSError, EOFError):
                got = False
            if got:
                try:
                    msg = conn.recv()
                except (EOFError, OSError):
                    msg = None
                if msg is None:
                    pass
                elif msg[0] == "progress":
                    _, done, total = msg
                    elapsed = time.perf_counter() - job.started
                    with job.lock:
                        job.progress = min(0.999, done / total) if total else 0.0
                        job.eta_s = (elapsed / done) * (total - done) if done else None
                    if done < total:  # the last run's event is folded into "done"
                        _deliver(job)
                    continue
                elif msg[0] == "done":
                    result = _build_result(job, msg[1])
                    _kill(proc)
                    _finish(job, "done", result=result)
                    return
                elif msg[0] == "error":
                    _kill(proc)
                    _finish(job, "failed", error=str(msg[1]))
                    return
            if not proc.is_alive() and not conn.poll(0):
                code = proc.exitcode
                _kill(proc)
                _finish(job, "failed", error=f"subprocess exited with code {code} before reporting a result")
                return
    except Exception as e:  # noqa: BLE001 - the watcher must never die silently
        _kill(proc)
        _finish(job, "failed", error=f"watcher error: {type(e).__name__}: {e}")
    finally:
        with contextlib.suppress(Exception):
            conn.close()


# ----------------------------------------------------------------------------- public API

def start_job(kind: Kind, params: dict[str, Any] | None, on_progress: Callable[[dict], None]) -> str:
    """Start a job and return its id at once. If a job is already running, its id comes back
    instead and `job_status(id)["notice"]` says so; nothing new is started."""
    global _CURRENT
    if kind not in KINDS:
        raise ValueError(f"unknown job kind {kind!r}; expected one of {KINDS}")
    public, child_params = normalize_params(kind, params)
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    with _REG_LOCK:
        if _CURRENT is not None and _CURRENT.status == "running":
            _CURRENT.notice = (f"a {_CURRENT.kind} job is already running ({_CURRENT.progress:.0%}); "
                               f"your {kind} request was not started. Cancel it or wait.")
            return _CURRENT.job_id
        job = Job(job_id=uuid.uuid4().hex[:8], kind=kind, params=public, loop=loop, on_progress=on_progress)
        _JOBS[job.job_id] = job
        _CURRENT = job
        if len(_JOBS) > 50:  # keep the registry small; finished jobs are on disk anyway
            for jid in [j for j, v in _JOBS.items() if v.status != "running"][:-20]:
                _JOBS.pop(jid, None)
    ctx = mp.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child_main, args=(kind, child_params, child_conn), daemon=True,
                       name=f"simjob-{job.job_id}")
    try:
        proc.start()
    except Exception as e:  # noqa: BLE001
        job.proc = proc
        _finish(job, "failed", error=f"could not start subprocess: {e}")
        return job.job_id
    child_conn.close()  # the parent's copy; the child keeps its own
    job.proc = proc
    _deliver(job)  # status running, progress 0: the bar can say "running N runs"
    threading.Thread(target=_watch, args=(job, parent_conn), name=f"simjob-watch-{job.job_id}", daemon=True).start()
    return job.job_id


def job_status(job_id: str) -> dict[str, Any]:
    job = _JOBS.get(job_id)
    if job is None:
        return {"job_id": job_id, "kind": None, "status": "unknown", "progress": 0.0, "eta_s": None,
                "params": {}, "error": "no such job"}
    return job.payload()


def cancel_job(job_id: str) -> None:
    job = _JOBS.get(job_id)
    if job is None or job.status != "running":
        return
    job.cancel_requested = True


def current_job() -> dict[str, Any] | None:
    """The running job's payload, or None. For the agent's "is anything running" question."""
    with _REG_LOCK:
        j = _CURRENT
    return None if j is None or j.status != "running" else j.payload()


def wait(job_id: str, timeout: float = WALL_CAP_S + 5) -> dict[str, Any]:
    """Block until the job leaves "running" (tests and scripts; never call this on the loop)."""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        st = job_status(job_id)
        if st["status"] != "running":
            return st
        time.sleep(0.05)
    return job_status(job_id)
