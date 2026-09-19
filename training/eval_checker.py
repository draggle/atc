"""Evaluate a checker checkpoint on a held-out jsonl.

Reports accuracy per error type, the false alarm rate on correct readbacks
(the number aviation judges ask for), detection rate (any error flagged as some
error), latency per pair on this machine, and a confusion matrix.
Writes training/results/checker_eval.json (or --out).

  .venv/bin/python eval_checker.py --ckpt ../data/checkpoints/checker-smoke/best \
      --data ../data/checker/heldout.jsonl --limit 1000 --label SMOKE
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runlog import log_run  # noqa: E402
from serve_checker import Checker  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--out", default=str(RESULTS / "checker_eval.json"))
    ap.add_argument("--label", default="eval")
    ap.add_argument("--device", default=None)
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(args.data) if l.strip()]
    if args.limit:
        rows = rows[:args.limit]
    checker = Checker(args.ckpt, device=args.device)
    labels = checker.labels

    preds: list[str] = []
    confs: list[float] = []
    t0 = time.time()
    for i in range(0, len(rows), args.batch_size):
        chunk = rows[i:i + args.batch_size]
        for r in checker.predict_batch([(x["controller"], x["pilot"]) for x in chunk]):
            preds.append(r["label"])
            confs.append(r["confidence"])
    batched_ms = (time.time() - t0) * 1000 / len(rows)

    # Single-pair latency, what the backend sees per transmission.
    single = []
    for r in rows[:50]:
        t = time.time()
        checker.predict(r["controller"], r["pilot"])
        single.append((time.time() - t) * 1000)
    single.sort()
    single_ms = {"p50": round(single[len(single) // 2], 2), "p95": round(single[int(len(single) * 0.95)], 2)}

    gold = [r["label"] for r in rows]
    n = len(gold)
    acc = sum(p == g for p, g in zip(preds, gold)) / n
    per_type = {}
    for l in labels:
        idx = [i for i, g in enumerate(gold) if g == l]
        if idx:
            per_type[l] = {"n": len(idx), "accuracy": round(sum(preds[i] == l for i in idx) / len(idx), 4)}
    correct_idx = [i for i, g in enumerate(gold) if g == "correct"]
    error_idx = [i for i, g in enumerate(gold) if g != "correct"]
    false_alarm = sum(preds[i] != "correct" for i in correct_idx) / max(1, len(correct_idx))
    detection = sum(preds[i] != "correct" for i in error_idx) / max(1, len(error_idx))
    confusion = defaultdict(Counter)
    for g, p in zip(gold, preds):
        confusion[g][p] += 1
    noisy = [i for i, r in enumerate(rows) if r.get("meta", {}).get("asr_noise")]
    clean = [i for i, r in enumerate(rows) if not r.get("meta", {}).get("asr_noise")]

    report = {
        "ckpt": args.ckpt,
        "data": args.data,
        "n": n,
        "device": checker.device,
        "accuracy": round(acc, 4),
        "chance": round(1 / len(labels), 4),
        "false_alarm_rate": round(false_alarm, 4),
        "detection_rate": round(detection, 4),
        "accuracy_clean": round(sum(preds[i] == gold[i] for i in clean) / max(1, len(clean)), 4),
        "accuracy_asr_noise": round(sum(preds[i] == gold[i] for i in noisy) / max(1, len(noisy)), 4),
        "per_type": per_type,
        "latency_ms": {"batched_per_pair": round(batched_ms, 3), "single_pair": single_ms},
        "confusion": {g: dict(c) for g, c in confusion.items()},
        "mean_confidence": round(sum(confs) / n, 4),
    }

    print(f"accuracy {acc:.4f} (chance {1 / len(labels):.3f})  false alarm {false_alarm:.4f}  detection {detection:.4f}")
    print(f"latency: {batched_ms:.2f} ms/pair batched, single p50 {single_ms['p50']} ms p95 {single_ms['p95']} ms on {checker.device}")
    print(f"{'type':16s} {'n':>5s} {'acc':>7s}")
    for l, v in per_type.items():
        print(f"{l:16s} {v['n']:5d} {v['accuracy']:7.4f}")
    print("\nconfusion (rows gold, cols pred):")
    short = [l[:6] for l in labels]
    print(" " * 16 + " ".join(f"{s:>6s}" for s in short))
    for g in labels:
        print(f"{g:16s}" + " ".join(f"{confusion[g][p]:6d}" for p in labels))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2))
    print(f"\nwrote {args.out}")
    log_run("checker_eval", args.label, base_model=args.ckpt, data={"data": args.data, "n": n},
            hyperparams={"batch_size": args.batch_size}, duration_s=batched_ms * n / 1000,
            result={k: report[k] for k in ("accuracy", "false_alarm_rate", "detection_rate", "latency_ms")})


if __name__ == "__main__":
    main()
