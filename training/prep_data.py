"""Prepare ASR data: download, resample to 16 kHz mono, filter, split, write manifests.

Two modes:

  Real data (default):
    python prep_data.py [--subset N] [--atco2]
    Downloads jacktol/atc-dataset parquet files (public, no token) into
    data/asr/raw/jacktol/, decodes every clip to 16 kHz mono WAV under
    data/asr/jacktol/{train,val,test}/, drops clips < 0.5 s or > 15 s or with an
    empty transcript, and writes data/asr/manifests/{train,val,test}.jsonl.
    The dataset's own test split becomes our held-out test set. Nothing in
    train or val overlaps it. val is a 5 percent slice of the dataset's train.

  Synthetic (offline smoke path):
    python prep_data.py --synthetic --n 40
    Uses macOS `say` with several voices to synthesize templated ATC lines from
    phrases.py, adds light noise and band-limiting so it sounds a bit like radio,
    and writes data/asr/synthetic/ plus data/asr/manifests/synth_{train,val,test}.jsonl.

Manifest rows: {"path": "<abs wav path>", "text": "<normalized transcript>", "duration": <s>}

Datasets and licences: docs/05-data-and-legal.md. No LiveATC.
"""
from __future__ import annotations

import argparse
import io
import json
import random
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from text_norm import normalize  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "asr"
MANIFESTS = DATA / "manifests"
SR = 16000
MIN_S, MAX_S = 0.5, 15.0

HF_BASE = "https://huggingface.co/datasets"
SOURCES = {
    "jacktol": {
        "repo": "jacktol/atc-dataset",
        "files": {
            "test": ["data/test-00000-of-00001.parquet"],
            "train": ["data/train-00000-of-00002.parquet", "data/train-00001-of-00002.parquet"],
        },
    },
    # Optional second source. Its file names are resolved from the Hub API at run time.
    "atco2": {"repo": "jlvdoorn/atco2-asr-atcosim", "files": None},
    # ATCOSIM: 10 h of controllers in en-route simulations, clean headset audio. New to us: jacktol is
    # ATCO2 + UWB-ATCC. Free for research (Graz University of Technology and Eurocontrol).
    "atcosim": {"repo": "Jzuluaga/atcosim_corpus", "files": None},
}

SAY_VOICES = ["Daniel", "Karen", "Moira", "Rishi", "Samantha", "Tessa"]


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def to_16k_mono(audio: np.ndarray, sr: int) -> np.ndarray:
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.astype(np.float32)
    if sr != SR:
        import librosa

        audio = librosa.resample(audio, orig_sr=sr, target_sr=SR)
    return audio


def decode_bytes(b: bytes) -> tuple[np.ndarray, int]:
    audio, sr = sf.read(io.BytesIO(b), dtype="float32")
    return audio, sr


def write_row(fh, path: Path, text: str, duration: float) -> None:
    fh.write(json.dumps({"path": str(path), "text": text, "duration": round(duration, 3)}) + "\n")


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def hub_files(repo: str) -> list[str]:
    with urllib.request.urlopen(f"https://huggingface.co/api/datasets/{repo}", timeout=30) as r:
        info = json.load(r)
    return [s["rfilename"] for s in info["siblings"] if s["rfilename"].endswith(".parquet")]


def download(repo: str, rel: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    url = f"{HF_BASE}/{repo}/resolve/main/{rel}"
    print(f"downloading {url} -> {dest}")
    tmp = dest.with_suffix(".part")
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, length=1 << 20)
    tmp.rename(dest)
    return dest


# ---------------------------------------------------------------------------
# Real data
# ---------------------------------------------------------------------------

def iter_parquet(path: Path):
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(path)
    for batch in pf.iter_batches(batch_size=64, columns=["audio", "text"]):
        for audio, text in zip(batch.column("audio").to_pylist(), batch.column("text").to_pylist()):
            yield audio["bytes"], text or ""


def prep_source(name: str, subset: int | None, seed: int, stats: dict) -> dict[str, list[dict]]:
    spec = SOURCES[name]
    repo = spec["repo"]
    files = spec["files"]
    if files is None:
        parquet = hub_files(repo)
        files = {"train": [f for f in parquet if "train" in f], "test": [f for f in parquet if "train" not in f]}
    raw = DATA / "raw" / name
    out = DATA / name
    rows: dict[str, list[dict]] = {"train": [], "test": []}
    for split, rels in files.items():
        (out / split).mkdir(parents=True, exist_ok=True)
        i = 0
        for rel in rels:
            local = download(repo, rel, raw / Path(rel).name)
            for b, text in iter_parquet(local):
                if subset and i >= subset:
                    break
                stats["seen"] += 1
                norm = normalize(text)
                if not norm:
                    stats["empty_text"] += 1
                    continue
                try:
                    audio, sr = decode_bytes(b)
                except Exception as e:  # noqa: BLE001
                    stats["decode_error"] += 1
                    print(f"decode error: {e}", file=sys.stderr)
                    continue
                audio = to_16k_mono(audio, sr)
                dur = len(audio) / SR
                if dur < MIN_S or dur > MAX_S:
                    stats["bad_duration"] += 1
                    continue
                wav = out / split / f"{name}_{split}_{i:06d}.wav"
                sf.write(wav, audio, SR, subtype="PCM_16")
                rows[split].append({"path": str(wav), "text": norm, "duration": round(dur, 3)})
                i += 1
            if subset and i >= subset:
                break
        print(f"{name}/{split}: kept {len(rows[split])}")
    return rows


def write_manifests(train: list[dict], val: list[dict], test: list[dict], prefix: str = "") -> None:
    MANIFESTS.mkdir(parents=True, exist_ok=True)
    for split, rows in (("train", train), ("val", val), ("test", test)):
        p = MANIFESTS / f"{prefix}{split}.jsonl"
        with open(p, "w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        hours = sum(r["duration"] for r in rows) / 3600
        print(f"wrote {p}  ({len(rows)} clips, {hours:.2f} h)")


def run_real(args: argparse.Namespace) -> None:
    rng = random.Random(args.seed)
    stats = {"seen": 0, "empty_text": 0, "decode_error": 0, "bad_duration": 0}
    sources = ["jacktol"] + (["atco2"] if args.atco2 else [])
    train_all, test_all = [], []
    for s in sources:
        rows = prep_source(s, args.subset, args.seed, stats)
        train_all += rows["train"]
        test_all += rows["test"]
    rng.shuffle(train_all)
    n_val = max(1, int(len(train_all) * args.val_frac))
    val, train = train_all[:n_val], train_all[n_val:]
    # Extra training data is added AFTER the split, so val and test are the same clips as in every
    # earlier run and the numbers stay comparable. Its own test clips get their own manifests.
    extra_tests: dict[str, list[dict]] = {}
    if args.atcosim:
        rows = prep_source("atcosim", args.subset, args.seed, stats)
        train += rows["train"]
        extra_tests["atcosim_test"] = rows["test"]
    if args.sim_dir:
        sim = Path(args.sim_dir).resolve()
        for split in ("train", "test"):
            rows = [json.loads(line) for line in open(sim / f"sim_{split}.jsonl") if line.strip()]
            rows = [{"path": str(sim / r["path"]), "text": r["text"], "duration": r["duration"]} for r in rows]
            rows = [r for r in rows if Path(r["path"]).exists() and r["text"]]
            print(f"sim/{split}: kept {len(rows)}")
            if split == "train":
                train += rows
            else:
                extra_tests["sim_test"] = rows
    if extra_tests:
        random.Random(args.seed + 1).shuffle(train)
    write_manifests(train, val, test_all)
    for name, rows in extra_tests.items():
        with open(MANIFESTS / f"{name}.jsonl", "w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        print(f"wrote {MANIFESTS / (name + '.jsonl')}  ({len(rows)} clips)")
    print("filter stats:", json.dumps(stats))
    # Spot-check: print a few random transcripts so a human can eyeball label quality.
    print("spot check (10 random training rows):")
    for r in rng.sample(train, min(10, len(train))):
        print(f"  {r['duration']:5.2f}s  {r['text']}")


# ---------------------------------------------------------------------------
# Synthetic data with macOS `say`
# ---------------------------------------------------------------------------

def radio_effect(audio: np.ndarray, rng: random.Random) -> np.ndarray:
    """Cheap radio flavour: band-pass 300 to 3400 Hz, light clipping, additive noise."""
    from scipy.signal import butter, sosfilt

    sos = butter(4, [300, 3400], btype="band", fs=SR, output="sos")
    y = sosfilt(sos, audio).astype(np.float32)
    y = y / (np.max(np.abs(y)) + 1e-6)
    gain = rng.uniform(1.0, 2.5)
    y = np.clip(y * gain, -0.8, 0.8)
    snr_db = rng.uniform(10, 25)
    noise = np.random.default_rng(rng.randrange(1 << 30)).normal(0, 1, len(y)).astype(np.float32)
    sig_p = np.mean(y ** 2) + 1e-9
    noise_p = sig_p / (10 ** (snr_db / 10))
    y = y + noise * np.sqrt(noise_p)
    return np.clip(y, -1, 1)


def synth_clip(text: str, wav: Path, voice: str, rate: int) -> None:
    subprocess.run(
        ["say", "-v", voice, "-r", str(rate), "-o", str(wav), "--data-format=LEI16@16000", text],
        check=True, capture_output=True,
    )


def run_synthetic(args: argparse.Namespace) -> None:
    from phrases import random_line

    if shutil.which("say") is None:
        sys.exit("--synthetic needs macOS `say`. On Linux, generate clips another way or use real data.")
    rng = random.Random(args.seed)
    out = DATA / "synthetic"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(args.n):
        text = random_line(rng)
        wav = out / f"synth_{i:05d}.wav"
        voice = rng.choice(SAY_VOICES)
        try:
            synth_clip(text, wav, voice, rate=rng.randint(190, 260))
        except subprocess.CalledProcessError:
            synth_clip(text, wav, "Samantha", rate=220)
        audio, sr = sf.read(wav, dtype="float32")
        audio = to_16k_mono(audio, sr)
        if args.radio:
            audio = radio_effect(audio, rng)
        dur = len(audio) / SR
        if dur < MIN_S or dur > MAX_S:
            continue
        sf.write(wav, audio, SR, subtype="PCM_16")
        rows.append({"path": str(wav), "text": normalize(text), "duration": round(dur, 3)})
        if (i + 1) % 20 == 0:
            print(f"synthesized {i + 1}/{args.n}")
    rng.shuffle(rows)
    n_test = max(1, int(len(rows) * 0.2))
    n_val = max(1, int(len(rows) * 0.1))
    test, val, train = rows[:n_test], rows[n_test:n_test + n_val], rows[n_test + n_val:]
    write_manifests(train, val, test, prefix="synth_")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subset", type=int, default=None, help="keep at most N clips per split (tiny runs)")
    ap.add_argument("--atco2", action="store_true", help="also pull jlvdoorn/atco2-asr-atcosim")
    ap.add_argument("--atcosim", action="store_true", help="add Jzuluaga/atcosim_corpus to training (its test split gets its own manifest)")
    ap.add_argument("--sim-dir", default=None, help="folder from gen_sim_audio.py: our own phrases through our own radio effect")
    ap.add_argument("--val-frac", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--synthetic", action="store_true", help="generate clips with macOS say instead")
    ap.add_argument("--n", type=int, default=40, help="number of synthetic clips")
    ap.add_argument("--no-radio", dest="radio", action="store_false", help="skip the radio effect on synthetic audio")
    args = ap.parse_args()
    if args.synthetic:
        run_synthetic(args)
    else:
        run_real(args)


if __name__ == "__main__":
    main()
