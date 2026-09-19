"""Ask the deployed Whisper to transcribe a clip, through the same client the app uses.

    cd backend && .venv/bin/python tools/asr_smoke.py                 # uses ASR_MODEL_URL from ../.env
    cd backend && .venv/bin/python tools/asr_smoke.py <url> [clip.wav]

Prints what the model heard, its alternatives, which model answered and how long it took.
The API key is read from ../.env and never printed.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from tower.asr import BasetenWhisper, build_prompt  # noqa: E402
from tower.audio import read_wav  # noqa: E402

DEFAULT_CLIP = ROOT / "data" / "pilot_audio" / "c1-ACA123-1.wav"


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ASR_MODEL_URL", "")
    clip = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_CLIP
    if not url:
        print("No URL. Pass one, or set ASR_MODEL_URL in .env.")
        return 2
    if not os.environ.get("BASETEN_API_KEY"):
        print("BASETEN_API_KEY is not set in .env.")
        return 2
    if not clip.exists():
        print(f"No clip at {clip}. Run the app once so a pilot speaks, or pass a WAV.")
        return 2
    samples, sr = read_wav(clip)
    asr = BasetenWhisper(url, timeout_s=120.0, max_retries=1, name="baseten:tuned")  # the first call may be a cold start
    prompt = build_prompt(["air canada one two three", "westjet four five six"], ["ESTIR", "CENTA"])
    for attempt in (1, 2):
        t0 = time.perf_counter()
        data = asr._post({"audio": __import__("base64").b64encode(__import__("tower.audio", fromlist=["wav_bytes"]).wav_bytes(samples, sr)).decode(),
                          "prompt": prompt or "", "beam_size": 5, "n_best": 5, "language": "en"})
        took = time.perf_counter() - t0
        out = data.get("model_output", data) if isinstance(data, dict) else {"text": str(data)}
        print(f"call {attempt}: {took:.2f} s round trip, {out.get('seconds', '?')} s in the model, served by: {out.get('model', '?')}")
        print(f"  heard : {out.get('text')}")
        for h in (out.get("n_best") or [])[1:]:
            print(f"  or    : {h.get('text') if isinstance(h, dict) else h}")
    res = asr.transcribe(samples)
    print(f"through the app's client: '{res.text}'  confidence {res.confidence:.2f}  {len(res.n_best)} hypotheses")
    if "FALLBACK" in str(out.get("model", "")):
        print("WARNING: the deployment could not find our checkpoint and is serving the stock model.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
