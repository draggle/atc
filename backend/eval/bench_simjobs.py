"""Measure the sim-job runner: wall time of a Monte Carlo job in its subprocess, and whether a
running job starves the live clock.

    python -m eval.bench_simjobs            # both measurements
    python -m eval.bench_simjobs --times    # just the wall times
    python -m eval.bench_simjobs --ticks    # just the contention check

Contention: a World on `demo` ticks at 1 Hz for 30 s with no job, then again with a runs=8
job on `dense` running in the subprocess; the tick's own duration (not the sleep) is sampled and
the p50 / p95 / max reported for both. Writes nothing.
"""
from __future__ import annotations

import argparse
import asyncio
import time

import numpy as np

from tools import simjobs as SJ


def wall_time(scenario: str, runs: int) -> tuple[float, dict]:
    t0 = time.perf_counter()
    jid = SJ.start_job("montecarlo", {"scenario": scenario, "runs": runs}, lambda p: None)
    st = SJ.wait(jid)
    return time.perf_counter() - t0, st


async def tick_times(seconds: float, with_job: bool, job_scenario: str = "dense", job_runs: int = 8) -> dict:
    from world import World
    w = World(lambda e: None, synthesize=False, realtime=False)
    w.load("demo")
    w.start()
    jid = None
    if with_job:
        jid = SJ.start_job("montecarlo", {"scenario": job_scenario, "runs": job_runs}, lambda p: None)
    samples: list[float] = []
    loop = asyncio.get_running_loop()
    t_end = loop.time() + seconds
    while loop.time() < t_end:
        t0 = loop.time()
        await w.tick(1.0)
        samples.append(loop.time() - t0)
        await asyncio.sleep(max(0.02, 1.0 - (loop.time() - t0)))
    job_state = None
    if jid is not None:
        job_state = SJ.job_status(jid)["status"]
        SJ.cancel_job(jid)
        SJ.wait(jid, timeout=10)
    a = np.array(samples) * 1000
    return {"n": len(samples), "p50_ms": float(np.percentile(a, 50)), "p95_ms": float(np.percentile(a, 95)),
            "max_ms": float(a.max()), "job_state_at_end": job_state}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--times", action="store_true")
    ap.add_argument("--ticks", action="store_true")
    ap.add_argument("--seconds", type=float, default=30.0)
    args = ap.parse_args()
    both = not (args.times or args.ticks)
    if args.times or both:
        for sc in ("demo", "dense"):
            wall, st = wall_time(sc, 8)
            print(f"montecarlo runs=8 on {sc}: {wall:.1f} s wall in the subprocess, status {st['status']}")
            if st["status"] == "done":
                print("  " + st["result"]["caption"])
            else:
                print("  " + str(st.get("error")))
    if args.ticks or both:
        for with_job in (False, True):
            r = asyncio.run(tick_times(args.seconds, with_job))
            print(f"tick at 1 Hz for {args.seconds:g} s {'with' if with_job else 'without'} a job: "
                  f"n={r['n']} p50 {r['p50_ms']:.1f} ms p95 {r['p95_ms']:.1f} ms max {r['max_ms']:.1f} ms"
                  + (f" (job {r['job_state_at_end']} at the end)" if with_job else ""))


if __name__ == "__main__":
    main()
