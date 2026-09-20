"""Readback error injection, covering every type in the 02-domain.md taxonomy.

`inject_error` takes the true items and returns what the pilot will actually say.
Two types are markers rather than item edits:

- ack_only: the readback is just "roger". Items are returned unchanged because the pilot
  did hear correctly; the plane still flies the clearance.
- missing_readback: no transmission at all. Same reasoning.

Default weights follow HAAWAII's finding that frequency changes, speed/heading confusion
and similar callsigns dominate real readback errors.
"""
from __future__ import annotations

import random
import re
from typing import get_args

from schemas import ErrorType, Item

from .readback import ICAO_TO_TELEPHONY, split_callsign

ALL_ERROR_TYPES: tuple[ErrorType, ...] = get_args(ErrorType)

DEFAULT_WEIGHTS: dict[ErrorType, float] = {
    "wrong_value": 3.0,      # frequency, altitude, heading, speed digit slips
    "wrong_runway": 1.0,
    "wrong_direction": 1.0,
    "wrong_unit": 1.5,       # speed/heading confusion
    "omitted_item": 1.5,
    "ack_only": 1.0,
    "wrong_aircraft": 2.0,   # similar callsign
    "missing_readback": 1.0,
}

# Within wrong_value, prefer the item types that fail most often in practice.
VALUE_ITEM_WEIGHTS: dict[str, float] = {
    "frequency": 3.0,
    "altitude": 2.0,
    "heading": 2.0,
    "speed": 1.5,
    "squawk": 1.0,
    "altimeter": 0.5,
    "route": 2.0,  # only when the pilot is given the sector's fix names, see _mutate_route
}


def sample_error_type(rng: random.Random, weights: dict[str, float] | None = None) -> ErrorType:
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update({k: float(v) for k, v in weights.items()})
    kinds = [k for k in ALL_ERROR_TYPES if w.get(k, 0) > 0]
    return rng.choices(kinds, weights=[w[k] for k in kinds], k=1)[0]


# ---------------------------------------------------------------------------
# Individual mutators. Each returns (new_items, description) or None if not applicable.
# ---------------------------------------------------------------------------


def _clone(items: list[Item]) -> list[Item]:
    return [it.model_copy() for it in items]


def _perturb_digit_number(value: int, rng: random.Random, step_choices: list[int], lo: int, hi: int) -> int:
    for _ in range(10):
        delta = rng.choice(step_choices)
        new = value + delta
        if lo <= new <= hi and new != value:
            return new
    return value + step_choices[0] if value + step_choices[0] <= hi else value - step_choices[0]


def _mutate_altitude(it: Item, rng: random.Random) -> str:
    old = it.value
    if it.unit == "FL":
        # FL240 -> FL210/230/250/270/200/220 etc: one digit off
        it.value = _perturb_digit_number(int(old), rng, [-30, -20, -10, 10, 20, 30, 100, -100], 50, 450)
    else:
        v = int(old)
        if v >= 10000:
            it.value = _perturb_digit_number(v, rng, [-1000, 1000, -2000, 2000], 1000, 45000)
        else:
            it.value = _perturb_digit_number(v, rng, [-1000, 1000, -500, 500], 1000, 17000)
    return f"altitude {old} read back as {it.value}"


def _mutate_heading(it: Item, rng: random.Random) -> str:
    old = int(it.value)
    new = _perturb_digit_number(old, rng, [-20, -10, 10, 20, -100, 100], 1, 360)
    if new == old:
        new = (old + 10) % 360 or 360
    it.value = new
    return f"heading {old:03d} read back as {new:03d}"


def _mutate_speed(it: Item, rng: random.Random) -> str:
    old = int(it.value)
    it.value = _perturb_digit_number(old, rng, [-10, 10, -20, 20, -30, 30], 120, 350)
    return f"speed {old} read back as {it.value}"


def _mutate_frequency(it: Item, rng: random.Random) -> str:
    old = str(it.value)
    whole, _, frac = old.partition(".")
    frac = frac or "0"
    frac_digits = list(frac.ljust(2, "0")[:2])
    choice = rng.random()
    if choice < 0.6:
        # decimal digit slip: 124.65 -> 124.75 / 124.55 / 124.62
        idx = rng.randrange(len(frac_digits))
        d = int(frac_digits[idx])
        frac_digits[idx] = str((d + rng.choice([-1, 1, 3, -3])) % 10)
    else:
        # swap decimals: 124.65 -> 124.56, or whole slip 124 -> 121
        if frac_digits[0] != frac_digits[1] and rng.random() < 0.5:
            frac_digits.reverse()
        else:
            w = list(whole)
            w[-1] = str((int(w[-1]) + rng.choice([-1, 1, 3])) % 10)
            whole = "".join(w)
    new_frac = "".join(frac_digits).rstrip("0") or "0"
    new = f"{whole}.{new_frac}"
    if new == old:
        new = f"{whole}.{str((int(frac_digits[0]) + 1) % 10)}{frac_digits[1]}".rstrip("0")
    it.value = float(new) if re.fullmatch(r"\d+\.\d+", new) else new
    return f"frequency {old} read back as {new}"


def _mutate_squawk(it: Item, rng: random.Random) -> str:
    old = str(it.value).zfill(4)
    digits = list(old)
    idx = rng.randrange(4)
    digits[idx] = str((int(digits[idx]) + rng.choice([-1, 1, 2])) % 8)  # squawk digits are octal
    new = "".join(digits)
    if new == old:
        digits[idx] = str((int(digits[idx]) + 1) % 8)
        new = "".join(digits)
    it.value = new
    return f"squawk {old} read back as {new}"


def _mutate_altimeter(it: Item, rng: random.Random) -> str:
    old = it.value
    if it.unit == "hPa":
        it.value = int(old) + rng.choice([-10, 10, -1, 1, -3, 3])
    else:
        it.value = round(float(old) + rng.choice([-0.1, 0.1, -0.01, 0.01, 0.03]), 2)
    return f"altimeter {old} read back as {it.value}"


def _mutate_route(it: Item, rng: random.Random, waypoints: list[str]) -> str | None:
    """Direct to the wrong fix. Made-up five-letter names that sound alike are the realistic slip,
    so the pick leans towards the names closest to the cleared one."""
    from rapidfuzz import fuzz
    old = str(it.value).upper()
    others = sorted({w.upper() for w in waypoints} - {old})
    if not others:
        return None
    ranked = sorted(others, key=lambda w: (-fuzz.ratio(old, w), w))
    new = rng.choice(ranked[:4])
    it.value = new
    return f"route {old} read back as {new}"


_VALUE_MUTATORS = {
    "altitude": _mutate_altitude,
    "heading": _mutate_heading,
    "speed": _mutate_speed,
    "frequency": _mutate_frequency,
    "squawk": _mutate_squawk,
    "altimeter": _mutate_altimeter,
}


def _wrong_value(items: list[Item], rng: random.Random,
                 waypoints: list[str] | None = None) -> tuple[list[Item], str] | None:
    # A route item can only go wrong if the pilot knows another fix to say instead.
    fixes = {w.upper() for w in (waypoints or [])}
    cands = [i for i, it in enumerate(items)
             if it.type in _VALUE_MUTATORS or (it.type == "route" and fixes - {str(it.value).upper()})]
    if not cands:
        return None
    weights = [VALUE_ITEM_WEIGHTS.get(items[i].type, 1.0) for i in cands]
    idx = rng.choices(cands, weights=weights, k=1)[0]
    out = _clone(items)
    if out[idx].type == "route":
        desc = _mutate_route(out[idx], rng, sorted(fixes))
    else:
        desc = _VALUE_MUTATORS[out[idx].type](out[idx], rng)
    return (out, desc) if desc else None


def _wrong_runway(items: list[Item], rng: random.Random) -> tuple[list[Item], str] | None:
    cands = [i for i, it in enumerate(items) if it.type in ("runway", "hold_short")]
    if not cands:
        return None
    idx = rng.choice(cands)
    out = _clone(items)
    old = str(out[idx].value).upper()
    m = re.fullmatch(r"(\d{1,2})([LRC]?)", old)
    if not m:
        return None
    digits, suffix = m.groups()
    if suffix in ("L", "R") and rng.random() < 0.6:
        new = digits + ("R" if suffix == "L" else "L")
    elif suffix == "C" and rng.random() < 0.6:
        new = digits + rng.choice(["L", "R"])
    else:
        n = int(digits)
        n2 = (n + rng.choice([-1, 1, 10, -10])) % 36 or 36
        new = f"{n2:02d}{suffix}"
    out[idx].value = new
    return out, f"runway {old} read back as {new}"


def _wrong_direction(items: list[Item], rng: random.Random) -> tuple[list[Item], str] | None:
    cands = []
    for i, it in enumerate(items):
        a = (it.action or "").lower()
        if it.type == "altitude" and (a.startswith("climb") or a.startswith("desc")):
            cands.append(i)
        elif it.type == "heading" and ("left" in a or "right" in a):
            cands.append(i)
    if not cands:
        return None
    idx = rng.choice(cands)
    out = _clone(items)
    it = out[idx]
    a = it.action or ""
    if it.type == "altitude":
        new = "descend" if a.startswith("climb") else "climb"
    else:
        new = a.replace("left", "right") if "left" in a else a.replace("right", "left")
    it.action = new
    return out, f"direction '{a}' read back as '{new}'"


def _wrong_unit(items: list[Item], rng: random.Random) -> tuple[list[Item], str] | None:
    cands = [i for i, it in enumerate(items) if it.type in ("altitude", "heading", "speed")]
    if not cands:
        return None
    idx = rng.choice(cands)
    out = _clone(items)
    it = out[idx]
    if it.type == "altitude":
        if it.unit == "FL":
            # flight level two four zero -> two four thousand... pilots say "twenty four thousand"
            old = f"FL{int(it.value)}"
            it.unit = "ft"
            it.value = int(it.value) * 100
            it.action = it.action or "descend"
            return out, f"{old} read back as {it.value} feet"
        old = f"{int(it.value)} ft"
        it.unit = "FL"
        it.value = max(50, int(it.value) // 100)
        return out, f"{old} read back as flight level {it.value}"
    if it.type == "heading":
        old = f"heading {int(it.value):03d}"
        v = int(it.value)
        it.type = "speed"
        it.unit = "kt"
        it.value = v if 120 <= v <= 350 else 250
        it.action = "reduce" if rng.random() < 0.5 else None
        return out, f"{old} read back as speed {it.value} knots"
    old = f"speed {int(it.value)}"
    v = int(it.value)
    it.type = "heading"
    it.unit = "deg"
    it.value = v if 1 <= v <= 360 else 270
    it.action = rng.choice(["turn_left", "turn_right", "fly"])
    return out, f"{old} read back as heading {it.value:03d}"


def _omitted_item(items: list[Item], rng: random.Random) -> tuple[list[Item], str] | None:
    mandatory = [i for i, it in enumerate(items) if it.mandatory]
    if len(items) < 2 or not mandatory:
        return None
    idx = rng.choice(mandatory)
    out = [it.model_copy() for j, it in enumerate(items) if j != idx]
    return out, f"omitted {items[idx].type} {items[idx].value}"


def _callsign_similarity(a: str, b: str) -> float:
    pa, ta = split_callsign(a)
    pb, tb = split_callsign(b)
    score = 0.0
    if pa and pa == pb:
        score += 2.0
    shared = sum(1 for x, y in zip(ta, tb) if x == y)
    score += shared
    if set(ta) == set(tb) and ta != tb:
        score += 1.5  # transposed digits: 123 vs 132
    if ta[-2:] == tb[-2:]:
        score += 1.0
    return score


def pick_similar_callsign(callsign: str, active: list[str], rng: random.Random) -> str | None:
    others = [c for c in active if c.upper() != callsign.upper()]
    if not others:
        return None
    weights = [1.0 + _callsign_similarity(callsign, c) ** 2 for c in others]
    return rng.choices(others, weights=weights, k=1)[0]


def _wrong_aircraft(callsign: str, active: list[str], rng: random.Random) -> tuple[str, str] | None:
    other = pick_similar_callsign(callsign, active, rng)
    if other is None:
        return None
    return other, f"{other} answered a clearance for {callsign}"


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def inject_error(
    items: list[Item],
    callsign: str,
    active_callsigns: list[str],
    rng: random.Random,
    weights: dict[str, float] | None = None,
    error_type: ErrorType | None = None,
    waypoints: list[str] | None = None,
) -> tuple[list[Item], str, ErrorType | None, str]:
    """Return (spoken_items, spoken_callsign, error_type, description).

    Picks an error type by weight (or uses `error_type`), and if that type does not apply
    to this clearance (e.g. wrong_runway with no runway item) falls through the other
    applicable types. Returns error_type None only when nothing at all applies.

    `waypoints` are the fix names the pilot could say instead of the cleared one. Without them a
    direct cannot get a wrong value, because there is no other fix to read back.
    """
    order: list[ErrorType]
    if error_type is not None:
        order = [error_type] + [k for k in ALL_ERROR_TYPES if k != error_type]
    else:
        first = sample_error_type(rng, weights)
        rest = [k for k in ALL_ERROR_TYPES if k != first]
        rng.shuffle(rest)
        order = [first] + rest

    for kind in order:
        if kind == "wrong_value":
            r = _wrong_value(items, rng, waypoints)
            if r:
                return r[0], callsign, kind, r[1]
        elif kind == "wrong_runway":
            r = _wrong_runway(items, rng)
            if r:
                return r[0], callsign, kind, r[1]
        elif kind == "wrong_direction":
            r = _wrong_direction(items, rng)
            if r:
                return r[0], callsign, kind, r[1]
        elif kind == "wrong_unit":
            r = _wrong_unit(items, rng)
            if r:
                return r[0], callsign, kind, r[1]
        elif kind == "omitted_item":
            r = _omitted_item(items, rng)
            if r:
                return r[0], callsign, kind, r[1]
        elif kind == "ack_only":
            if any(it.mandatory for it in items):
                return _clone(items), callsign, kind, "acknowledged with roger only"
        elif kind == "wrong_aircraft":
            r = _wrong_aircraft(callsign, active_callsigns, rng)
            if r:
                return _clone(items), r[0], kind, r[1]
        elif kind == "missing_readback":
            return _clone(items), callsign, kind, "no readback before timeout"
        # If we were asked for a specific type and it did not apply, keep falling through.
    return _clone(items), callsign, None, ""


__all__ = [
    "ALL_ERROR_TYPES",
    "DEFAULT_WEIGHTS",
    "ICAO_TO_TELEPHONY",
    "inject_error",
    "pick_similar_callsign",
    "sample_error_type",
]
