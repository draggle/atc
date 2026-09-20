"""CLI: python -m eval.run_eval --scenario demo --runs 20 [--seed 0] [--error-rate 0.02] [--density 1.0]"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from eval.montecarlo import ARMS, run
from sim.scenarios import list_scenarios, load

OUT_DIR = Path(__file__).resolve().parent / "out"


def _fmt(v, nd=2):
    return "-" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def print_table(res: dict) -> None:
    cols = ["arm", "LoS", "flt h", "LoS/h", "closest", "p5", "p50", "miles", "vs fixed %", "err inj", "err caught", "predicted", "resolved"]
    rows = []
    for arm, a in res["arms"].items():
        rows.append([arm, a["los_total"], _fmt(a["flight_hours"]), _fmt(a["los_per_flight_hour"], 3),
                     _fmt(a["closest_min_nm"]), _fmt(a["closest_p5_nm"]), _fmt(a["closest_p50_nm"]),
                     _fmt(a["miles_mean"], 0), _fmt(a["miles_vs_baseline_pct"]), a["errors_injected"], a["errors_caught"],
                     a.get("conflicts_predicted", 0), a.get("conflicts_resolved", 0)])
    widths = [max(len(str(r[i])) for r in [cols] + rows) for i in range(len(cols))]
    line = "  ".join(c.ljust(w) for c, w in zip(cols, widths))
    print(f"{res['scenario']}: {res['n_runs']} runs, error rate {res['error_rate']}, buffer {res['buffer_nm']} NM, plan conflicts {res['plan_conflicts']}")
    print(line)
    print("-" * len(line))
    for r in rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths)))


def main() -> None:
    ap = argparse.ArgumentParser(description="Monte Carlo evaluation of the planner and Tower.")
    ap.add_argument("--scenario", default="demo", choices=list_scenarios())
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--error-rate", type=float, default=0.02)
    ap.add_argument("--density", type=float, default=1.0)
    ap.add_argument("--buffer", type=float, default=None, help="extra NM on top of 5 NM; default from scenario")
    ap.add_argument("--arms", default=",".join(ARMS))
    args = ap.parse_args()
    sc = load(args.scenario)
    t0 = time.perf_counter()
    res = run(sc, args.runs, args.seed, tuple(args.arms.split(",")), args.error_rate, args.density, args.buffer)
    res["wall_s"] = round(time.perf_counter() - t0, 2)
    print_table(res)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{args.scenario}_r{args.runs}_s{args.seed}_e{args.error_rate}_d{args.density}.json"
    out.write_text(json.dumps(res, indent=2))
    print(f"\nwrote {out} in {res['wall_s']} s")


if __name__ == "__main__":
    main()
