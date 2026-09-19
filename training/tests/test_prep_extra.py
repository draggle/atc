"""Extra training data must never change the validation or held-out test clips."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prep_data as P  # noqa: E402


def fake_source(name, subset, seed, stats):
    n = {"jacktol": 200, "atcosim": 50}[name]
    return {"train": [{"path": f"/x/{name}_tr_{i}.wav", "text": f"{name} train {i}", "duration": 3.0} for i in range(n)],
            "test": [{"path": f"/x/{name}_te_{i}.wav", "text": f"{name} test {i}", "duration": 3.0} for i in range(20)]}


def run(tmp_path, monkeypatch, **flags):
    out = tmp_path / ("plain" if not flags else "extra")
    monkeypatch.setattr(P, "prep_source", fake_source)
    monkeypatch.setattr(P, "MANIFESTS", out)
    args = argparse.Namespace(subset=None, atco2=False, atcosim=False, sim_dir=None, val_frac=0.05, seed=13)
    for k, v in flags.items():
        setattr(args, k, v)
    P.run_real(args)
    return {p.stem: [json.loads(line) for line in open(p)] for p in out.glob("*.jsonl")}


def test_extra_data_leaves_val_and_test_alone(tmp_path, monkeypatch):
    sim = tmp_path / "sim"
    (sim / "train").mkdir(parents=True)
    (sim / "test").mkdir()
    for split, n in (("train", 30), ("test", 5)):
        with open(sim / f"sim_{split}.jsonl", "w") as fh:
            for i in range(n):
                (sim / split / f"{split}_{i}.flac").write_bytes(b"x")
                fh.write(json.dumps({"path": f"{split}/{split}_{i}.flac", "text": f"direct estir {i}", "duration": 2.5}) + "\n")
    plain = run(tmp_path, monkeypatch)
    extra = run(tmp_path, monkeypatch, atcosim=True, sim_dir=str(sim))
    assert plain["val"] == extra["val"] and plain["test"] == extra["test"]
    assert len(extra["train"]) == len(plain["train"]) + 50 + 30
    assert {r["path"] for r in plain["train"]} <= {r["path"] for r in extra["train"]}
    assert len(extra["sim_test"]) == 5 and len(extra["atcosim_test"]) == 20
    assert all(Path(r["path"]).is_absolute() for r in extra["sim_test"])
    held_out = {r["path"] for r in extra["val"] + extra["test"] + extra["sim_test"] + extra["atcosim_test"]}
    assert not held_out & {r["path"] for r in extra["train"]}
