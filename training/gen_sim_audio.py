"""Synthetic ATC clips in OUR simulator's voice of the world, for the second Whisper run.

Why: the first fine-tune only ever heard real European controllers and pilots. The demo is
synthetic voices through our radio effect saying callsigns and fix names we made up, and the
tuned model hears "direct ESTIR" as "direct to six". No public dataset can fix that.

What this makes: clips whose TEXT comes from the app's own phrase builders
(`backend/planner/cards.py::phrase_for`, `backend/pilots/readback.py`), whose callsigns and fix
names come from every scenario we ship (simulated and real), and whose SOUND goes through the
app's own radio effect (`backend/pilots/radio.py`). Voices are macOS `say`, so it costs nothing.
One voice is held out entirely for the test set.

    cd training && ../backend/.venv/bin/python gen_sim_audio.py --train 2400 --test 300

Writes data/asr/sim/{train,test}/*.flac and data/asr/sim/{sim_train,sim_test}.jsonl with paths
relative to data/asr/sim, so the folder can be shipped to a training job as is.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import tempfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pilots.radio import radio_effect  # noqa: E402
from pilots.readback import build_readback, roger, say_again, say_callsign, say_feet  # noqa: E402
from planner.cards import phrase_for  # noqa: E402
from schemas import Item  # noqa: E402
from sim import scenarios as SC  # noqa: E402
from text_norm import normalize  # noqa: E402

OUT = ROOT / "data" / "asr" / "sim"
SR = 16000
TRAIN_VOICES = ["Daniel", "Karen", "Moira", "Rishi", "Samantha", "Kathy", "Ralph", "Fred"]
TEST_VOICE = "Tessa"  # never in training: the test measures new-speaker performance


def world() -> tuple[list[str], list[str]]:
    """Every callsign and every sayable fix or gate name across all shipped scenarios."""
    callsigns, fixes = set(), set()
    for name in SC.list_scenarios():
        sc = SC.load(name)
        callsigns.update(f.callsign for f in sc.flights if not f.is_intruder)
        fixes.update(w.name for w in sc.waypoints if w.kind != "hidden")
    return sorted(callsigns), sorted(fixes)


def random_items(rng: random.Random, fixes: list[str]) -> list[Item]:
    def heading() -> Item:
        return Item(type="heading", value=rng.randrange(5, 365, 5) % 360 or 360, unit="deg",
                    action=rng.choice(["turn_left", "turn_right", "fly"]))

    def level() -> Item:
        return Item(type="altitude", value=rng.randrange(240, 420, 10), unit="FL", action=rng.choice(["climb", "descend"]))

    def speed() -> Item:
        return Item(type="speed", value=rng.randrange(380, 520, 5), unit="kt", action=rng.choice(["reduce", "increase"]))

    def direct() -> Item:
        return Item(type="route", value=rng.choice(fixes), unit=None, action="direct")

    # Routing is weighted up: fix names are what the first model cannot hear.
    kind = rng.choices(["direct", "heading", "level", "speed", "heading+level", "direct+level"],
                       weights=[38, 20, 14, 8, 10, 10])[0]
    return {"direct": [direct()], "heading": [heading()], "level": [level()], "speed": [speed()],
            "heading+level": [heading(), level()], "direct+level": [direct(), level()]}[kind]


def random_line(rng: random.Random, callsigns: list[str], fixes: list[str]) -> str:
    cs = rng.choice(callsigns)
    r = rng.random()
    if r < 0.42:
        return phrase_for(cs, random_items(rng, fixes))  # what Tower or the controller says
    if r < 0.90:
        return build_readback(cs, random_items(rng, fixes), style_rng=rng)  # what the pilot says back
    if r < 0.94:
        return say_again(cs, rng)
    if r < 0.97:
        return roger(cs, rng)
    hdg = " ".join(f"{rng.randrange(0, 360):03d}")
    return f"mayday mayday mayday {say_callsign(cs)} engine failure descending {say_feet(10000)} heading {hdg}"


def make_clip(job: tuple[int, str, str, int, float, Path]) -> dict | None:
    i, text, voice, rate, noise, dest = job
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
        r = subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", tmp.name, "--data-format=LEI16@16000", text],
                           capture_output=True)
        if r.returncode != 0:
            return None
        audio, sr = sf.read(tmp.name, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != SR or audio.size < SR // 2:
        return None
    noisy = radio_effect(audio, SR, noise_level=noise, rng=zlib.crc32(f"{i}{text}".encode()))
    sf.write(dest, np.clip(noisy, -1, 1), SR, format="FLAC", subtype="PCM_16")
    return {"path": f"{dest.parent.name}/{dest.name}", "text": normalize(text), "duration": round(len(noisy) / SR, 2),
            "voice": voice}


def build(split: str, n: int, voices: list[str], seed: int, callsigns: list[str], fixes: list[str]) -> list[dict]:
    rng = random.Random(seed)
    folder = OUT / split
    folder.mkdir(parents=True, exist_ok=True)
    jobs, seen = [], set()
    while len(jobs) < n:
        text = random_line(rng, callsigns, fixes)
        if text in seen:
            continue
        seen.add(text)
        i = len(jobs)
        jobs.append((i, text, rng.choice(voices), rng.randrange(170, 215), rng.uniform(0.05, 0.45),
                     folder / f"{split}_{i:05d}.flac"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = [r for r in pool.map(make_clip, jobs) if r]
    with open(OUT / f"sim_{split}.jsonl", "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train", type=int, default=2400)
    ap.add_argument("--test", type=int, default=300)
    args = ap.parse_args()
    callsigns, fixes = world()
    print(f"{len(callsigns)} callsigns, {len(fixes)} fix and gate names")
    for split, n, voices, seed in (("train", args.train, TRAIN_VOICES, 11), ("test", args.test, [TEST_VOICE], 12)):
        rows = build(split, n, voices, seed, callsigns, fixes)
        hours = sum(r["duration"] for r in rows) / 3600
        print(f"{split}: {len(rows)} clips, {hours:.2f} h, e.g. {rows[0]['text']!r}")
    size = sum(f.stat().st_size for f in OUT.rglob("*.flac")) / 1e6
    print(f"{size:.0f} MB in {OUT}")


if __name__ == "__main__":
    main()
