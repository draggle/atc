"""Density sweep: the "sector that scales" curve. docs/trd/03-planner-data-eval.md task 2.

Runs `montecarlo.run` for every (density, buffer) pair with all three arms and writes
one CSV plus one two-panel PNG to eval/out/. Left panel: losses of separation per flight
hour against traffic density, one line per arm. Right panel: miles flown relative to the
fixed-route arm, for the two planned arms. Everything is simulated, no winds.

    python -m eval.sweep --scenario dense --densities 1,1.5,2,2.5 --buffers 1,3 --runs 4 --error-rate 0.02
"""
from __future__ import annotations

import argparse
import csv
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Callable, Sequence

from eval.montecarlo import ARMS, run
from schemas import Scenario
from sim.scenarios import list_scenarios, load

OUT_DIR = Path(__file__).resolve().parent / "out"

ARM_LABEL = {"fixed": "Fixed routes", "tower_off": "Tower, validation off", "tower_on": "Tower, validation on"}
# First three dark-mode categorical slots from the dataviz reference palette, validated all-pairs
# against the dark surface below (CVD dE 9.4, normal-vision dE 20.9, contrast >= 3:1).
ARM_COLOR = {"fixed": "#3987e5", "tower_off": "#d95926", "tower_on": "#199e70"}
ARM_MARKER = {"fixed": "o", "tower_off": "s", "tower_on": "^"}
SURFACE = "#1a1a19"
INK, INK_2, INK_MUTED, GRID = "#ffffff", "#c3c2b7", "#8a8980", "#33332f"


@dataclass(frozen=True)
class SweepRow:
    density: float
    buffer_nm: float
    arm: str
    runs: int
    flight_hours: float
    los: int
    los_per_hour: float
    closest_min_nm: float | None
    closest_p5_nm: float | None
    miles: float
    miles_vs_fixed_pct: float | None
    errors_injected: int
    errors_caught: int
    plan_conflicts: int
    seconds: float


COLUMNS = [f.name for f in fields(SweepRow)]


def _rows_from_result(res: dict, density: float, buffer_nm: float, seconds: float) -> list[SweepRow]:
    rows = []
    for arm, a in res["arms"].items():
        rows.append(SweepRow(
            density=density, buffer_nm=buffer_nm, arm=arm, runs=res["n_runs"],
            flight_hours=round(a["flight_hours"], 3), los=a["los_total"],
            los_per_hour=round(a["los_per_flight_hour"], 4),
            closest_min_nm=None if a["closest_min_nm"] is None else round(a["closest_min_nm"], 2),
            closest_p5_nm=None if a["closest_p5_nm"] is None else round(a["closest_p5_nm"], 2),
            miles=round(a["miles_mean"], 1),
            miles_vs_fixed_pct=None if a["miles_vs_baseline_pct"] is None else round(a["miles_vs_baseline_pct"], 2),
            errors_injected=a["errors_injected"], errors_caught=a["errors_caught"],
            plan_conflicts=res["plan_conflicts"], seconds=round(seconds, 1),
        ))
    return rows


def sweep(scenario: Scenario, densities: Sequence[float], buffers: Sequence[float], n_runs: int,
          error_rate: float = 0.02, seed: int = 0, arms: tuple[str, ...] = ARMS,
          on_point: Callable[[list[SweepRow]], None] | None = None) -> list[SweepRow]:
    """One `montecarlo.run` per (density, buffer). `seconds` is the wall time of that one call."""
    rows: list[SweepRow] = []
    for buffer_nm in buffers:
        for density in densities:
            t0 = time.perf_counter()
            res = run(scenario, n_runs=n_runs, seed=seed, arms=arms, error_rate=error_rate,
                      density=density, buffer_nm=buffer_nm)
            point = _rows_from_result(res, density, buffer_nm, time.perf_counter() - t0)
            rows.extend(point)
            if on_point is not None:
                on_point(point)
    return rows


def write_csv(rows: list[SweepRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in asdict(r).items()})


def read_csv(path: Path) -> list[SweepRow]:
    types = {f.name: f.type for f in fields(SweepRow)}
    rows: list[SweepRow] = []
    with path.open() as fh:
        for rec in csv.DictReader(fh):
            kw: dict[str, object] = {}
            for k, v in rec.items():
                t = types[k]
                if v == "":
                    kw[k] = None
                elif t == "str":
                    kw[k] = v
                elif t == "int":
                    kw[k] = int(v)
                else:
                    kw[k] = float(v)
            rows.append(SweepRow(**kw))  # type: ignore[arg-type]
    return rows


def _fmt(v: object, nd: int = 2) -> str:
    if v is None:
        return "-"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def format_table(rows: list[SweepRow]) -> str:
    cols = ["density", "buffer", "arm", "LoS", "flt h", "LoS/h", "closest", "p5", "miles", "vs fixed %",
            "err inj", "caught", "conflicts", "s"]
    body = [[_fmt(r.density, 1), _fmt(r.buffer_nm, 0), r.arm, r.los, _fmt(r.flight_hours, 1), _fmt(r.los_per_hour, 3),
             _fmt(r.closest_min_nm), _fmt(r.closest_p5_nm), _fmt(r.miles, 0), _fmt(r.miles_vs_fixed_pct),
             r.errors_injected, r.errors_caught, r.plan_conflicts, _fmt(r.seconds, 0)] for r in rows]
    widths = [max(len(str(x[i])) for x in [cols] + body) for i in range(len(cols))]
    lines = ["  ".join(str(c).ljust(w) for c, w in zip(cols, widths))]
    lines.append("-" * len(lines[0]))
    lines += ["  ".join(str(c).ljust(w) for c, w in zip(r, widths)) for r in body]
    return "\n".join(lines)


def _series(rows: list[SweepRow], arm: str, buffer_nm: float, attr: str) -> tuple[list[float], list[float]]:
    pts = sorted((r.density, getattr(r, attr)) for r in rows if r.arm == arm and r.buffer_nm == buffer_nm)
    pts = [(d, v) for d, v in pts if v is not None]
    return [d for d, _ in pts], [v for _, v in pts]


def plot(rows: list[SweepRow], path: Path, scenario: str, n_runs: int, error_rate: float,
         highlight_buffer: float = 3.0) -> None:
    """Two panels on a dark surface. The highlighted buffer is drawn solid; other buffers are faint."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    buffers = sorted({r.buffer_nm for r in rows})
    if highlight_buffer not in buffers:
        highlight_buffer = buffers[-1]
    arms = [a for a in ARMS if any(r.arm == a for r in rows)]

    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "text.color": INK, "axes.labelcolor": INK_2, "xtick.color": INK_2, "ytick.color": INK_2,
        "axes.edgecolor": GRID, "grid.color": GRID, "font.size": 13, "axes.titlesize": 15,
        "axes.spines.top": False, "axes.spines.right": False,
    })
    fig, (ax_los, ax_mi) = plt.subplots(1, 2, figsize=(15, 6.5))

    def label_ends(ax: plt.Axes, ends: list[tuple[str, float, float]], min_gap_pt: float = 15.0) -> None:
        """Direct labels at the last point of each solid line, pushed apart when series coincide."""
        ax.relim()
        ax.autoscale_view()
        px_to_pt = 72.0 / fig.dpi
        disp = sorted((ax.transData.transform((x, y))[1] * px_to_pt, arm, x, y) for arm, x, y in ends)
        shifted: list[float] = []
        for py, *_ in disp:
            shifted.append(py if not shifted else max(py, shifted[-1] + min_gap_pt))
        for (py, arm, x, y), sy in zip(disp, shifted):
            ax.annotate(ARM_LABEL[arm], (x, y), xytext=(8, sy - py), textcoords="offset points",
                        color=INK_2, fontsize=11, va="center")

    def draw(ax: plt.Axes, arm_list: list[str], attr: str) -> None:
        ends: list[tuple[str, float, float]] = []
        for buffer_nm in buffers:
            hot = buffer_nm == highlight_buffer
            for arm in arm_list:
                xs, ys = _series(rows, arm, buffer_nm, attr)
                if not xs:
                    continue
                ax.plot(xs, ys, color=ARM_COLOR[arm], marker=ARM_MARKER[arm], markersize=8 if hot else 5,
                        linewidth=2.2 if hot else 1.2, alpha=1.0 if hot else 0.35, zorder=3 if hot else 2,
                        markeredgecolor=SURFACE, markeredgewidth=1.5)
                if hot:
                    ends.append((arm, xs[-1], ys[-1]))
        ax.grid(True, axis="y", linewidth=0.6)
        ax.set_xlabel("Traffic density (x scenario)")
        ax.set_xticks(sorted({r.density for r in rows}))
        ax.margins(x=0.25)
        label_ends(ax, ends)

    draw(ax_los, arms, "los_per_hour")
    ax_los.set_title("Losses of separation per flight hour", loc="left")
    ax_los.set_ylabel("LoS / flight hour")
    ax_los.set_ylim(bottom=0)

    draw(ax_mi, [a for a in arms if a != "fixed"], "miles_vs_fixed_pct")
    ax_mi.axhline(0, color=INK_MUTED, linewidth=1, linestyle="--", zorder=1)
    ax_mi.set_title("Miles flown vs fixed routes", loc="left")
    ax_mi.set_ylabel("Percent (negative = fewer miles)")

    handles = [Line2D([], [], color=ARM_COLOR[a], marker=ARM_MARKER[a], markersize=8, linewidth=2.2,
                      label=ARM_LABEL[a]) for a in arms]
    others = [b for b in buffers if b != highlight_buffer]
    if others:
        handles.append(Line2D([], [], color=INK_2, linewidth=1.2, alpha=0.35,
                              label=f"faint: buffer {', '.join(f'{b:g}' for b in others)} NM"))
    fig.legend(handles=handles, loc="lower center", ncol=len(handles), frameon=False, fontsize=11,
               bbox_to_anchor=(0.5, 0.0))
    fig.suptitle(f"{scenario} scenario, {n_runs} runs per point, readback error rate {error_rate:g}, "
                 f"buffer {highlight_buffer:g} NM solid. Simulated, no winds.", x=0.02, ha="left", fontsize=14,
                 color=INK)
    fig.tight_layout(rect=(0, 0.07, 1, 0.95))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def first_unresolved(rows: list[SweepRow]) -> list[tuple[float, float, int]]:
    """(density, buffer, conflicts) for every point where the planner left conflicts unresolved."""
    seen = sorted({(r.density, r.buffer_nm, r.plan_conflicts) for r in rows if r.plan_conflicts > 0})
    return seen


def _floats(text: str) -> list[float]:
    return [float(x) for x in text.split(",") if x.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description="Density sweep of the Monte Carlo evaluation.")
    ap.add_argument("--scenario", default="dense", choices=list_scenarios())
    ap.add_argument("--densities", default="1,1.5,2,2.5")
    ap.add_argument("--buffers", default="1,3")
    ap.add_argument("--runs", type=int, default=4)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--error-rate", type=float, default=0.02)
    ap.add_argument("--highlight-buffer", type=float, default=3.0)
    ap.add_argument("--replot", action="store_true", help="redraw the PNG from the existing CSV, no simulation")
    args = ap.parse_args()
    csv_path = OUT_DIR / f"sweep_{args.scenario}.csv"
    png_path = OUT_DIR / f"sweep_{args.scenario}.png"
    if args.replot:
        rows = read_csv(csv_path)
        plot(rows, png_path, args.scenario, rows[0].runs, args.error_rate, args.highlight_buffer)
        print(format_table(rows))
        print(f"wrote {png_path}")
        return

    def progress(point: list[SweepRow]) -> None:
        r = point[0]
        los = ", ".join(f"{p.arm} {p.los}" for p in point)
        print(f"density {r.density:g} buffer {r.buffer_nm:g}: LoS {los}; plan conflicts {r.plan_conflicts}; "
              f"{r.seconds:.0f} s", flush=True)

    t0 = time.perf_counter()
    rows = sweep(load(args.scenario), _floats(args.densities), _floats(args.buffers), args.runs,
                 args.error_rate, args.seed, on_point=progress)
    write_csv(rows, csv_path)
    plot(rows, png_path, args.scenario, args.runs, args.error_rate, args.highlight_buffer)
    print()
    print(format_table(rows))
    unresolved = first_unresolved(rows)
    print("\nplanner unresolved conflicts: " + (", ".join(f"d{d:g}/b{b:g}={c}" for d, b, c in unresolved) or "none"))
    print(f"wrote {csv_path} and {png_path} in {time.perf_counter() - t0:.0f} s")


if __name__ == "__main__":
    main()
