"""Word error rate on a held-out manifest for stock Whisper and/or a fine-tuned checkpoint.

Both reference and hypothesis go through text_norm.normalize. Results print as a
table and land in training/results/wer_<tag>.json with the per-clip worst offenders.

  # stock whisper-tiny on 50 real held-out clips
  .venv/bin/python eval_wer.py --manifest ../data/asr/manifests/test.jsonl --stock openai/whisper-tiny --limit 50

  # stock vs tuned side by side
  .venv/bin/python eval_wer.py --manifest ../data/asr/manifests/test.jsonl \
      --stock openai/whisper-small --tuned ../data/checkpoints/whisper-atc/best --limit 200

  # a CTranslate2 export via faster-whisper
  .venv/bin/python eval_wer.py --manifest ... --ct2 ../data/checkpoints/whisper-atc-ct2

Any model can also be given as --model NAME=PATH to label it yourself.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import jiwer
import numpy as np
import soundfile as sf
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runlog import log_run  # noqa: E402
from text_norm import normalize  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"
SR = 16000


def read_manifest(path: str, limit: int | None) -> list[dict]:
    rows = [json.loads(l) for l in open(path) if l.strip()]
    return rows[:limit] if limit else rows


def load_audio(path: str) -> np.ndarray:
    audio, sr = sf.read(path, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != SR:
        import librosa

        audio = librosa.resample(audio, orig_sr=sr, target_sr=SR)
    return audio


def pick_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class HFTranscriber:
    """Transformers Whisper, stock or fine-tuned checkpoint dir."""

    def __init__(self, name_or_path: str, device: str):
        from transformers import WhisperForConditionalGeneration, WhisperProcessor

        self.processor = WhisperProcessor.from_pretrained(name_or_path)
        self.model = WhisperForConditionalGeneration.from_pretrained(name_or_path).to(device).eval()
        self.device = device

    @torch.no_grad()
    def transcribe(self, audios: list[np.ndarray]) -> list[str]:
        feats = self.processor.feature_extractor(audios, sampling_rate=SR, return_tensors="pt").input_features.to(self.device)
        ids = self.model.generate(feats, language="en", task="transcribe", max_new_tokens=128)
        return self.processor.batch_decode(ids, skip_special_tokens=True)


class CT2Transcriber:
    """faster-whisper on a CTranslate2 export."""

    def __init__(self, path: str, device: str):
        from faster_whisper import WhisperModel

        dev = "cuda" if device == "cuda" else "cpu"
        self.model = WhisperModel(path, device=dev, compute_type="float16" if dev == "cuda" else "int8")

    def transcribe(self, audios: list[np.ndarray]) -> list[str]:
        out = []
        for a in audios:
            segs, _ = self.model.transcribe(a, language="en", beam_size=1)
            out.append(" ".join(s.text for s in segs))
        return out


def evaluate(name: str, transcriber, rows: list[dict], batch_size: int) -> dict:
    refs, hyps, per_clip = [], [], []
    t0 = time.time()
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        raw = transcriber.transcribe([load_audio(r["path"]) for r in chunk])
        for r, h in zip(chunk, raw):
            ref, hyp = normalize(r["text"]), normalize(h)
            if not ref:
                continue
            refs.append(ref)
            hyps.append(hyp)
            per_clip.append({"path": r["path"], "ref": ref, "hyp": hyp, "wer": jiwer.wer(ref, hyp) if hyp else 1.0})
    elapsed = time.time() - t0
    out = jiwer.process_words(refs, hyps)
    audio_s = sum(r.get("duration", 0) for r in rows)
    worst = sorted(per_clip, key=lambda c: -c["wer"])[:15]
    return {
        "model": name,
        "n_clips": len(refs),
        "wer": round(out.wer, 4),
        "substitutions": out.substitutions,
        "deletions": out.deletions,
        "insertions": out.insertions,
        "hits": out.hits,
        "seconds_per_clip": round(elapsed / max(1, len(refs)), 3),
        "real_time_factor": round(elapsed / audio_s, 3) if audio_s else None,
        "worst": worst,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--stock", default=None, help="hub id, e.g. openai/whisper-small")
    ap.add_argument("--tuned", default=None, help="fine-tuned checkpoint dir (transformers format)")
    ap.add_argument("--ct2", default=None, help="CTranslate2 export dir for faster-whisper")
    ap.add_argument("--model", action="append", default=[], help="extra NAME=PATH transformers model")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--tag", default=None, help="results file tag; default derived from models")
    ap.add_argument("--label", default="eval", help="label for RUNS.md")
    ap.add_argument("--no-log", action="store_true", help="do not append to RUNS.md")
    args = ap.parse_args()

    device = pick_device()
    rows = read_manifest(args.manifest, args.limit)
    specs: list[tuple[str, str, str]] = []
    if args.stock:
        specs.append((f"stock:{args.stock}", "hf", args.stock))
    if args.tuned:
        specs.append((f"tuned:{args.tuned}", "hf", args.tuned))
    if args.ct2:
        specs.append((f"ct2:{args.ct2}", "ct2", args.ct2))
    for m in args.model:
        n, p = m.split("=", 1)
        specs.append((n, "hf", p))
    if not specs:
        sys.exit("give at least one of --stock, --tuned, --ct2, --model")

    results = []
    for name, kind, path in specs:
        print(f"\n== {name} on {len(rows)} clips ({device}) ==")
        tr = HFTranscriber(path, device) if kind == "hf" else CT2Transcriber(path, device)
        res = evaluate(name, tr, rows, args.batch_size if kind == "hf" else 1)
        results.append(res)
        print(f"WER {res['wer']:.4f}  S={res['substitutions']} D={res['deletions']} I={res['insertions']}  {res['seconds_per_clip']}s/clip")

    print(f"\n{'model':60s} {'clips':>6s} {'WER':>8s} {'s/clip':>8s}")
    for r in results:
        print(f"{r['model'][:60]:60s} {r['n_clips']:6d} {r['wer']:8.4f} {r['seconds_per_clip']:8.3f}")
    print("\nworst offenders (first model):")
    for c in results[0]["worst"][:5]:
        print(f"  wer={c['wer']:.2f}\n    ref: {c['ref']}\n    hyp: {c['hyp']}")

    RESULTS.mkdir(exist_ok=True)
    tag = args.tag or "_".join(s[0].split(":")[0] + "-" + Path(s[2]).name for s in specs)
    out = RESULTS / f"wer_{tag}.json"
    out.write_text(json.dumps({"manifest": args.manifest, "limit": args.limit, "device": device, "results": results}, indent=2))
    print(f"\nwrote {out}")

    if not args.no_log:
        for r in results:
            log_run("wer_eval", args.label, base_model=r["model"],
                    data={"manifest": args.manifest, "n_clips": r["n_clips"]},
                    hyperparams={"normalizer": "text_norm.normalize", "greedy": True},
                    duration_s=r["seconds_per_clip"] * r["n_clips"],
                    result={k: r[k] for k in ("wer", "substitutions", "deletions", "insertions", "seconds_per_clip")})


if __name__ == "__main__":
    main()
