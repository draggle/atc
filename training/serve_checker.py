"""Readback checker inference: import `predict()` in-process, or run as a tiny
FastAPI service the backend calls at CHECKER_MODEL_URL.

JSON contract
  POST /predict   {"controller": str, "pilot": str}
              ->  {"label": str, "confidence": float, "probs": {label: float}}
  POST /predict_batch  {"pairs": [{"controller", "pilot"}, ...]} -> {"results": [...]}
  GET  /health    {"status": "ok", "model": path, "labels": [...]}

label is one of gen_checker_data.LABELS: correct, wrong_value, wrong_runway,
wrong_direction, wrong_unit, omitted_item, ack_only, wrong_aircraft.
confidence is the softmax probability of the returned label.

Run:
  CHECKER_CKPT=../data/checkpoints/checker-smoke/best .venv/bin/uvicorn serve_checker:app --port 8600
  curl -X POST localhost:8600/predict -H 'content-type: application/json' \
       -d '{"controller":"lufthansa two five three descend flight level two four zero","pilot":"descend flight level two one zero lufthansa two five three"}'

In-process:
  from serve_checker import Checker
  checker = Checker("path/to/best"); label, conf = checker.predict(controller, pilot)
"""
from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from text_norm import normalize  # noqa: E402

DEFAULT_CKPT = os.environ.get(
    "CHECKER_CKPT",
    str(Path(__file__).resolve().parents[1] / "data" / "checkpoints" / "checker" / "best"),
)


class Checker:
    def __init__(self, ckpt: str = DEFAULT_CKPT, device: str | None = None):
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.ckpt = ckpt
        self.device = device or ("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
        self.tok = AutoTokenizer.from_pretrained(ckpt)
        self.model = AutoModelForSequenceClassification.from_pretrained(ckpt).to(self.device).eval()
        labels_file = Path(ckpt) / "labels.json"
        if labels_file.exists():
            self.labels = json.loads(labels_file.read_text())
        else:
            self.labels = [self.model.config.id2label[i] for i in range(self.model.config.num_labels)]

    @torch.no_grad()
    def predict_batch(self, pairs: list[tuple[str, str]]) -> list[dict]:
        ctl = [normalize(c) for c, _ in pairs]
        plt = [normalize(p) for _, p in pairs]
        enc = self.tok(ctl, plt, truncation=True, max_length=128, padding=True, return_tensors="pt").to(self.device)
        probs = torch.softmax(self.model(**enc).logits.float(), dim=-1).cpu()
        out = []
        for row in probs:
            i = int(row.argmax())
            out.append({"label": self.labels[i], "confidence": round(float(row[i]), 4),
                        "probs": {l: round(float(p), 4) for l, p in zip(self.labels, row)}})
        return out

    def predict(self, controller: str, pilot: str) -> tuple[str, float]:
        r = self.predict_batch([(controller, pilot)])[0]
        return r["label"], r["confidence"]


@lru_cache(maxsize=1)
def get_checker() -> Checker:
    return Checker()


def predict(controller: str, pilot: str) -> tuple[str, float]:
    """Module-level convenience: loads the default checkpoint once."""
    return get_checker().predict(controller, pilot)


# ---------------------------------------------------------------------------
# FastAPI app (optional; only built if fastapi is importable)
# ---------------------------------------------------------------------------
try:
    from fastapi import FastAPI
    from pydantic import BaseModel

    class Pair(BaseModel):
        controller: str
        pilot: str

    class Batch(BaseModel):
        pairs: list[Pair]

    app = FastAPI(title="Tower readback checker")

    @app.get("/health")
    def health():
        c = get_checker()
        return {"status": "ok", "model": c.ckpt, "labels": c.labels, "device": c.device}

    @app.post("/predict")
    def predict_route(pair: Pair):
        return get_checker().predict_batch([(pair.controller, pair.pilot)])[0]

    @app.post("/predict_batch")
    def predict_batch_route(batch: Batch):
        return {"results": get_checker().predict_batch([(p.controller, p.pilot) for p in batch.pairs])}

except ImportError:  # pragma: no cover
    app = None


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default=DEFAULT_CKPT)
    ap.add_argument("--port", type=int, default=8600)
    ap.add_argument("controller", nargs="?")
    ap.add_argument("pilot", nargs="?")
    a = ap.parse_args()
    os.environ["CHECKER_CKPT"] = a.ckpt
    if a.controller and a.pilot:
        print(json.dumps(Checker(a.ckpt).predict_batch([(a.controller, a.pilot)])[0], indent=2))
    else:
        import uvicorn

        uvicorn.run(app, host="0.0.0.0", port=a.port)
