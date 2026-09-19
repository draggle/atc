"""Density sweep: CSV shape, density actually scales traffic, fixed routes do not get safer with density."""
import csv

from eval.sweep import COLUMNS, sweep, write_csv
from sim.scenarios import load, multiply


def test_density_scales_traffic():
    sc = load("demo")
    regular = [f for f in sc.flights if not f.is_intruder]
    assert len([f for f in multiply(sc, 1.5).flights if not f.is_intruder]) == round(len(regular) * 1.5)


def test_sweep_writes_csv(tmp_path):
    rows = sweep(load("demo"), densities=[1.0, 1.5], buffers=[3.0], n_runs=2, error_rate=0.02, seed=1)
    assert len(rows) == 2 * 1 * 3
    out = tmp_path / "sweep_demo.csv"
    write_csv(rows, out)
    assert out.exists()
    with out.open() as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == COLUMNS
        recs = list(reader)
    assert len(recs) == len(rows)
    fixed = {float(r["density"]): int(r["los"]) for r in recs if r["arm"] == "fixed"}
    assert fixed[1.5] >= fixed[1.0] or (fixed[1.0] == 0 and fixed[1.5] == 0)
    hours = {float(r["density"]): float(r["flight_hours"]) for r in recs if r["arm"] == "fixed"}
    assert hours[1.5] > hours[1.0]
