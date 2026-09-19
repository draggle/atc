"""Synthetic clearance / readback pairs for the cross-encoder readback checker.

Each row: {"controller": str, "pilot": str, "label": str, "meta": {...}}
Labels (N+1 = 8 classes, from docs/02-domain.md; missing_readback is a timeout,
not a text pair, so it is not a class here):

  correct, wrong_value, wrong_runway, wrong_direction, wrong_unit,
  omitted_item, ack_only, wrong_aircraft

Design, following HAAWAII (docs/07-build-spec.md section 2):
  - clearances come from phrases.py, weighted toward frequency changes, heading and speed
  - correct readbacks are paraphrased and shortened, callsign first or last
  - exactly one error per negative example, so the label is unambiguous
  - about 30 percent of rows get ASR-style noise: a dropped word, a swapped digit,
    a garbled callsign. Noise never changes the label: a swapped digit is only
    applied to non-value words on correct rows
  - wrong_aircraft uses a confusable callsign (one digit off or two swapped)
  - classes are balanced; the split is by clearance so no clearance text leaks
    between train and held-out

  .venv/bin/python gen_checker_data.py --n 50000 --seed 0
  -> data/checker/train.jsonl, data/checker/heldout.jsonl (10 percent)
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phrases import (DIGIT_WORDS, Clearance, ClearanceItem, WAYPOINTS, controller_text,  # noqa: E402
                     make_item, pilot_readback, random_clearance, say_digits, similar_callsign,
                     speak_item)

LABELS = ["correct", "wrong_value", "wrong_runway", "wrong_direction", "wrong_unit",
          "omitted_item", "ack_only", "wrong_aircraft"]

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "checker"


# ---------------------------------------------------------------------------
# Error injection. Each returns a modified pilot text or None if not applicable.
# ---------------------------------------------------------------------------

def _pick_item(c: Clearance, rng: random.Random, types: set[str] | None = None) -> ClearanceItem | None:
    cands = [i for i in c.items if types is None or i.type in types]
    return rng.choice(cands) if cands else None


def _readback_with(c: Clearance, rng: random.Random, replace: ClearanceItem, new: ClearanceItem) -> str:
    c2 = c.clone()
    c2.items = [new if i is replace else i for i in c.items]
    return pilot_readback(c2, rng)


def _shift_digits(value: str, rng: random.Random) -> str:
    digits = [i for i, ch in enumerate(value) if ch.isdigit()]
    v = list(value)
    i = rng.choice(digits)
    v[i] = str((int(v[i]) + rng.randint(1, 9)) % 10)
    return "".join(v)


def inject_wrong_value(c: Clearance, rng: random.Random) -> str | None:
    item = _pick_item(c, rng, {"altitude", "heading", "speed", "frequency", "squawk", "direct"})
    if item is None:
        return None
    new = ClearanceItem(item.type, item.action, item.value, item.unit)
    if item.type == "direct":
        new.value = rng.choice([w for w in WAYPOINTS if w != item.value])
    elif item.type == "altitude":
        if item.unit == "FL":
            opts = [v for v in (60, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160, 180, 200, 210, 220,
                                230, 240, 250, 260, 270, 280, 290, 300, 310, 320, 330, 340, 350, 360, 370, 380, 390)
                    if v != int(item.value)]
            # bias toward confusable neighbours (one digit off)
            near = [v for v in opts if sum(a != b for a, b in zip(f"{v:03d}", f"{int(item.value):03d}")) == 1]
            new.value = str(rng.choice(near or opts))
        else:
            opts = [v for v in (2000, 2500, 3000, 3500, 4000, 4500, 5000, 6000, 7000, 8000, 9000, 10000, 11000, 12000)
                    if v != int(item.value)]
            new.value = str(rng.choice(opts))
    elif item.type == "heading":
        h = int(item.value)
        delta = rng.choice([-100, -10, -5, 5, 10, 100, 20, -20])
        new.value = f"{((h + delta - 1) % 360) + 1:03d}"
    elif item.type == "speed":
        new.value = str(int(item.value) + rng.choice([-10, 10, -20, 20, 100, -100]) if int(item.value) > 200 else int(item.value) + 10)
    elif item.type == "frequency":
        whole, frac = item.value.split(".")
        if rng.random() < 0.5:
            whole = str(int(whole) + rng.choice([-1, 1, 10, -10]))
        else:
            frac = _shift_digits(frac, rng)
        new.value = f"{whole}.{frac}"
    elif item.type == "squawk":
        v = _shift_digits(item.value, rng)
        new.value = "".join(str(min(int(ch), 7)) for ch in v)
        if new.value == item.value:
            new.value = _shift_digits(item.value, rng)
    if new.value == item.value:
        return None
    return _readback_with(c, rng, item, new)


def inject_wrong_runway(c: Clearance, rng: random.Random) -> str | None:
    item = _pick_item(c, rng, {"runway"})
    if item is None:
        return None
    num, side = item.value[:2], item.value[2:]
    if side and rng.random() < 0.6:
        side = rng.choice([s for s in ("L", "R", "C") if s != side])
    else:
        n = int(num)
        n = rng.choice([x for x in (n - 1, n + 1, n + 10, n - 10, (n + 18 - 1) % 36 + 1) if 1 <= x <= 36])
        num = f"{n:02d}"
    new = ClearanceItem("runway", item.action, f"{num}{side}")
    return _readback_with(c, rng, item, new)


def inject_wrong_direction(c: Clearance, rng: random.Random) -> str | None:
    cands = [i for i in c.items if (i.type == "altitude" and i.action in ("climb", "descend"))
             or (i.type == "heading" and i.action in ("turn_left", "turn_right"))]
    if not cands:
        return None
    item = rng.choice(cands)
    flip = {"climb": "descend", "descend": "climb", "turn_left": "turn_right", "turn_right": "turn_left"}
    new = ClearanceItem(item.type, flip[item.action], item.value, item.unit)
    return _readback_with(c, rng, item, new)


def inject_wrong_unit(c: Clearance, rng: random.Random) -> str | None:
    """Flight level read back as thousands of feet (or vice versa), or a heading
    read back as a speed / speed as heading."""
    cands = [i for i in c.items if i.type in ("altitude", "heading", "speed")]
    if not cands:
        return None
    item = rng.choice(cands)
    if item.type == "altitude":
        if item.unit == "FL":
            # FL 240 -> "two four thousand" or "two thousand four hundred"
            fl = int(item.value)
            if fl % 10 == 0 and fl // 10 <= 12:
                new = ClearanceItem("altitude", item.action, str(fl * 100), "ft")
            else:
                new = ClearanceItem("altitude", item.action, str((fl // 10) * 1000 + (fl % 10) * 100), "ft")
        else:
            ft = int(item.value)
            new = ClearanceItem("altitude", item.action, str(ft // 100), "FL")
    elif item.type == "heading":
        new = ClearanceItem("speed", "maintain", str(int(item.value)) if 160 <= int(item.value) <= 320 else item.value)
    else:
        new = ClearanceItem("heading", "fly", f"{int(item.value) % 360:03d}")
    return _readback_with(c, rng, item, new)


def inject_omitted_item(c: Clearance, rng: random.Random) -> str | None:
    if len(c.items) < 2:
        return None
    c2 = c.clone()
    drop = rng.randrange(len(c2.items))
    c2.items = [i for k, i in enumerate(c2.items) if k != drop]
    return pilot_readback(c2, rng)


def inject_ack_only(c: Clearance, rng: random.Random) -> str | None:
    ack = rng.choice(["roger", "wilco", "copied", "roger wilco", "okay", "roger that", "copy"])
    cs = c.callsign if rng.random() < 0.6 else say_digits(c.number)
    return rng.choice([f"{ack} {cs}", f"{cs} {ack}", f"{ack}"])


def inject_wrong_aircraft(c: Clearance, rng: random.Random) -> str | None:
    other = similar_callsign(rng, c.airline, c.number) if rng.random() < 0.8 else None
    c2 = c.clone()
    if other is None:
        from phrases import random_callsign

        other, c2.airline, c2.number = random_callsign(rng)
    else:
        c2.number = "".join(ch for ch in other if ch.isdigit()) or c2.number
        # similar_callsign returns spoken digits; recover digits from the spoken words
        c2.number = "".join(str(DIGIT_WORDS.index(w)) for w in other.split() if w in DIGIT_WORDS)
    c2.callsign = other
    return pilot_readback(c2, rng)


INJECTORS = {
    "wrong_value": inject_wrong_value,
    "wrong_runway": inject_wrong_runway,
    "wrong_direction": inject_wrong_direction,
    "wrong_unit": inject_wrong_unit,
    "omitted_item": inject_omitted_item,
    "ack_only": inject_ack_only,
    "wrong_aircraft": inject_wrong_aircraft,
}


# ---------------------------------------------------------------------------
# ASR-style noise. Must not change the label.
# ---------------------------------------------------------------------------

_VALUE_WORDS = set(DIGIT_WORDS) | {"left", "right", "centre", "climb", "descend", "thousand", "hundred", "decimal"} | set(WAYPOINTS)
_GARBLE = {"alfa": "alpha", "three": "tree", "five": "fife", "nine": "niner", "speedbird": "speed bird",
           "lufthansa": "lufthanser", "ryanair": "ryan air", "air canada": "air canada", "westjet": "west jet",
           "easy": "easyjet", "klm": "k l m", "csa": "c s a", "flight": "fly", "level": "levels",
           "contact": "contact", "squawk": "squak", "heading": "headin", "maintain": "maintaining"}


def asr_noise(text: str, rng: random.Random, label: str) -> str:
    words = text.split()
    if len(words) < 3:
        return text
    kind = rng.choice(["drop", "drop", "garble", "swap_digit", "insert"])
    if kind == "drop":
        # drop a non-value word so correct stays correct
        idx = [i for i, w in enumerate(words) if w not in _VALUE_WORDS]
        if idx:
            words.pop(rng.choice(idx))
    elif kind == "garble":
        idx = [i for i, w in enumerate(words) if w in _GARBLE]
        if idx:
            i = rng.choice(idx)
            words[i] = _GARBLE[words[i]]
    elif kind == "swap_digit":
        # Only inside the callsign region on error rows, or nowhere on correct rows:
        # swapping a clearance digit would silently flip the label.
        if label != "correct":
            idx = [i for i, w in enumerate(words[:4]) if w in DIGIT_WORDS]
            if idx:
                i = rng.choice(idx)
                words[i] = rng.choice([d for d in DIGIT_WORDS if d != words[i]])
    elif kind == "insert":
        i = rng.randrange(len(words) + 1)
        words.insert(i, rng.choice(["uh", "er", "and", "the", "ah"]))
    return " ".join(words)


# ---------------------------------------------------------------------------
# Main generation
# ---------------------------------------------------------------------------

def generate(n: int, seed: int, noise_frac: float) -> list[dict]:
    rng = random.Random(seed)
    per_class = n // len(LABELS)
    counts = {l: 0 for l in LABELS}
    rows: list[dict] = []
    clearance_id = 0
    attempts = 0
    while any(v < per_class for v in counts.values()) and attempts < n * 20:
        attempts += 1
        # Some error kinds need multi-item or runway clearances. Bias the sampler so
        # the balanced target is reachable without a long tail of rejects.
        need = [l for l in LABELS if counts[l] < per_class]
        label = rng.choice(need)
        if label in ("wrong_runway",):
            c = random_clearance(rng, n_items=1)
            c.items = [make_item(rng, "runway")]
        elif label == "omitted_item":
            c = random_clearance(rng, n_items=rng.choice([2, 3]))
        else:
            c = random_clearance(rng)
        controller = controller_text(c, rng)
        if label == "correct":
            pilot = pilot_readback(c, rng)
        else:
            pilot = INJECTORS[label](c, rng)
            if pilot is None:
                continue
        noisy = rng.random() < noise_frac
        if noisy:
            pilot = asr_noise(pilot, rng, label)
            if rng.random() < 0.3:
                controller = asr_noise(controller, rng, "controller")
        if pilot.strip() == "" or pilot == controller:
            continue
        clearance_id += 1
        rows.append({
            "controller": controller,
            "pilot": pilot,
            "label": label,
            "meta": {"clearance_id": clearance_id, "n_items": len(c.items),
                     "item_types": [i.type for i in c.items], "asr_noise": noisy},
        })
        counts[label] += 1
    rng.shuffle(rows)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=50000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--noise-frac", type=float, default=0.3)
    ap.add_argument("--heldout-frac", type=float, default=0.1)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    rows = generate(args.n, args.seed, args.noise_frac)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    n_held = int(len(rows) * args.heldout_frac)
    held, train = rows[:n_held], rows[n_held:]
    for name, part in (("train", train), ("heldout", held)):
        p = out / f"{name}.jsonl"
        with open(p, "w") as fh:
            for r in part:
                fh.write(json.dumps(r) + "\n")
        dist = {l: sum(1 for r in part if r["label"] == l) for l in LABELS}
        print(f"wrote {p}: {len(part)} rows  {dist}")
    (out / "labels.json").write_text(json.dumps(LABELS))
    print("examples:")
    for r in train[:6]:
        print(f"  [{r['label']}]\n    C: {r['controller']}\n    P: {r['pilot']}")


if __name__ == "__main__":
    main()
