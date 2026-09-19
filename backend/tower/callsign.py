"""Callsign snapping against the simulator's active list, and similar-callsign detection."""
from __future__ import annotations

import re
from typing import NamedTuple

from rapidfuzz import fuzz

from tower.normalize import ICAO_TO_TELEPHONY, TELEPHONY, normalize

ACCEPT_SCORE = 70.0
AMBIGUOUS_MARGIN = 10.0
_SUFFIX_SCORE = 90.0
_CALLSIGN_TOKEN = re.compile(r"^[A-Z]{3}\d{1,4}[A-Z]{0,2}$|^[A-Z]{4,6}$|^N\d{1,5}[A-Z]{0,2}$")
_DIGITS = re.compile(r"\d+")
_LETTER_PREFIX = re.compile(r"^[A-Z]{3}")


class Snap(NamedTuple):
    best: str | None
    score: float
    ambiguous: bool
    runner_up: str | None


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", normalize(text)).upper()


def _digit_tail(cand: str) -> str | None:
    """Trailing digit group of a heard callsign, e.g. '123' from 'canada 123' or 'ACA123'."""
    m = _DIGITS.findall(cand)
    return m[-1] if m else None


def _telephony_prefix(text: str) -> str | None:
    """ICAO code implied by a (possibly partial) telephony word, e.g. 'canada' -> ACA."""
    words = text.lower().split()
    for name, icao in TELEPHONY.items():
        if any(w in name.split() for w in words if len(w) > 2):
            return icao
    return None


def snap(callsign_or_text: str | None, active: list[str]) -> Snap:
    """Snap a heard callsign (or a short transmission) to the closest active callsign.

    Full callsigns are compared by fuzzy ratio. Shortened callsigns ("123", "canada 123") match
    by digit suffix. Two active callsigns within AMBIGUOUS_MARGIN of each other -> ambiguous.
    """
    if not callsign_or_text or not active:
        return Snap(None, 0.0, False, None)
    raw = callsign_or_text.strip()
    cand = _compact(raw)
    tail = _digit_tail(cand)
    prefix_hint = _telephony_prefix(raw)
    scores: list[tuple[float, str]] = []
    for a in active:
        a_up = a.upper()
        s = float(fuzz.ratio(cand, a_up))
        if tail and len(tail) >= 2 and a_up.endswith(tail):
            suffix = _SUFFIX_SCORE
            if prefix_hint and not a_up.startswith(prefix_hint):
                suffix -= 20
            elif prefix_hint and a_up.startswith(prefix_hint):
                suffix = 97
            s = max(s, suffix)
        scores.append((s, a))
    scores.sort(key=lambda t: (-t[0], t[1]))
    best_score, best = scores[0]
    runner = scores[1] if len(scores) > 1 else None
    if best_score < ACCEPT_SCORE:
        return Snap(None, best_score, False, None)
    ambiguous = runner is not None and runner[0] >= ACCEPT_SCORE \
        and best_score - runner[0] < AMBIGUOUS_MARGIN
    return Snap(best, best_score, ambiguous, runner[1] if ambiguous else None)


def is_callsign_token(tok: str) -> bool:
    return bool(_CALLSIGN_TOKEN.match(tok)) and tok not in ICAO_TO_TELEPHONY


def _one_edit_apart(a: str, b: str) -> bool:
    if len(a) == len(b):
        diffs = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        if len(diffs) == 1:
            return True
        if len(diffs) == 2 and diffs[1] == diffs[0] + 1:
            i = diffs[0]
            return a[i] == b[i + 1] and a[i + 1] == b[i]  # adjacent transposition
        return False
    return False


def similar_pairs(active: list[str]) -> list[tuple[str, str]]:
    """Pairs of active callsigns differing by one character or one adjacent transposition."""
    out: list[tuple[str, str]] = []
    ups = sorted({a.upper() for a in active})
    for i, a in enumerate(ups):
        for b in ups[i + 1:]:
            if _one_edit_apart(a, b):
                out.append((a, b))
            else:
                # same digits, different airline (ACA123 / DAL123) is the other classic confusion
                da, db = _digit_tail(a), _digit_tail(b)
                if da and db and da == db and len(da) >= 3:
                    out.append((a, b))
    return out
