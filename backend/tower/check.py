"""Readback checker: rules on concepts, the n-best rule, optional cross-encoder.

Layer 1 pairs items by type and compares values, producing an error type from the taxonomy.
Layer 2 downgrades a candidate mismatch to "ambiguous" when the expected value appears in any
speech hypothesis. Layer 3 asks an optional CheckerModel and marks disagreement ambiguous.
"""
from __future__ import annotations

import os
from typing import Protocol

from schemas import ErrorType, Extraction, Item, OpenClearance, Transmission, Verdict
from tower import parse as P
from tower.callsign import snap
from tower.normalize import phrase_from_items, spoken_callsign

LOW_ASR_CONFIDENCE = 0.6
_DIRECTIONS = {"climb", "descend"}
_TURNS = {"turn_left", "turn_right"}
_RUNWAY_TYPES = {"runway", "hold_short"}


class CheckerModel(Protocol):
    """Cross-encoder over (controller text, pilot text) -> (label, confidence).

    Label is "correct" or one of the ErrorType values.
    """

    def predict(self, controller_text: str, pilot_text: str) -> tuple[str, float] | None: ...


class NullChecker:
    def predict(self, controller_text: str, pilot_text: str) -> tuple[str, float] | None:
        return None


class RemoteChecker:
    """HTTP client for the fine-tuned cross-encoder. POST {controller, pilot} -> {label, confidence}."""

    def __init__(self, url: str, timeout_s: float = 1.0) -> None:
        self.url = url
        self.timeout_s = timeout_s

    def predict(self, controller_text: str, pilot_text: str) -> tuple[str, float] | None:
        try:
            import httpx

            r = httpx.post(self.url, json={"controller": controller_text, "pilot": pilot_text},
                           timeout=self.timeout_s)
            r.raise_for_status()
            data = r.json()
            return str(data["label"]), float(data.get("confidence", 0.5))
        except Exception:  # noqa: BLE001 - the model is an assist, never a dependency
            return None


def checker_from_env() -> CheckerModel:
    url = os.environ.get("CHECKER_MODEL_URL")
    return RemoteChecker(url) if url else NullChecker()


# ---------------------------------------------------------------------------
# Layer 1: rules on concepts
# ---------------------------------------------------------------------------


def _num(v: object) -> float | None:
    try:
        return float(str(v).rstrip("LRC"))
    except ValueError:
        return None


def _same_value(a: Item, b: Item) -> bool:
    if a.type in _RUNWAY_TYPES or a.type in ("route", "squawk"):
        return str(a.value).upper().lstrip("0") == str(b.value).upper().lstrip("0")
    na, nb = _num(a.value), _num(b.value)
    if na is None or nb is None:
        return str(a.value) == str(b.value)
    return abs(na - nb) < 1e-6


def _describe(i: Item) -> str:
    if i.type == "altitude":
        return f"FL{int(i.value)}" if i.unit == "FL" else f"{int(i.value)} ft"
    if i.type == "heading":
        return f"heading {int(i.value):03d}"
    if i.type == "speed":
        return f"{int(i.value)} kt"
    if i.type == "frequency":
        return f"{i.value}"
    if i.type in _RUNWAY_TYPES:
        return f"runway {i.value}"
    return f"{i.type} {i.value}"


def _compare(exp: Item, heard: Item) -> tuple[ErrorType | None, str]:
    """Compare one expected item with the heard item of the same concept."""
    if exp.type == "altitude":
        if exp.unit != heard.unit and heard.unit is not None and exp.unit is not None:
            return "wrong_unit", f"expected {_describe(exp)}, heard {_describe(heard)}"
        if exp.action in _DIRECTIONS and heard.action in _DIRECTIONS and exp.action != heard.action:
            return "wrong_direction", f"expected {exp.action} {_describe(exp)}, heard {heard.action} {_describe(heard)}"
        if not _same_value(exp, heard):
            return "wrong_value", f"expected {_describe(exp)}, heard {_describe(heard)}"
        return None, ""
    if exp.type == "heading":
        if exp.action in _TURNS and heard.action in _TURNS and exp.action != heard.action:
            return "wrong_direction", f"expected {exp.action.replace('_', ' ')} {_describe(exp)}, heard {heard.action.replace('_', ' ')} {_describe(heard)}"
        if not _same_value(exp, heard):
            return "wrong_value", f"expected {_describe(exp)}, heard {_describe(heard)}"
        return None, ""
    if exp.type in _RUNWAY_TYPES:
        if not _same_value(exp, heard):
            return "wrong_runway", f"expected {_describe(exp)}, heard {_describe(heard)}"
        if exp.action and heard.action and exp.action != heard.action:
            return "wrong_value", f"expected {exp.action.replace('_', ' ')}, heard {heard.action.replace('_', ' ')}"
        return None, ""
    if not _same_value(exp, heard):
        return "wrong_value", f"expected {exp.type} {_describe(exp)}, heard {_describe(heard)}"
    return None, ""


def _pair(exp: Item, heard: list[Item], used: set[int]) -> int | None:
    for j, h in enumerate(heard):
        if j in used:
            continue
        if h.type == exp.type or (exp.type in _RUNWAY_TYPES and h.type in _RUNWAY_TYPES):
            return j
    return None


def rules(clearance: OpenClearance, ext: Extraction, text_norm: str) -> Verdict:
    """Layer 1 only. Returns match / partial / mismatch with an error type."""
    expected = [i for i in clearance.items if i.mandatory]
    heard = list(ext.items)
    v = Verdict(clearance_id=clearance.id, readback_transmission_id=ext.transmission_id,
                result="match", expected=expected, heard=heard, confidence=0.95, decided_by="rules")
    if ext.callsign and ext.callsign != clearance.callsign:
        v.result, v.error_type = "mismatch", "wrong_aircraft"
        v.reason = f"Readback came from {ext.callsign}, clearance was for {clearance.callsign}"
        return v
    if not expected:
        v.reason = "No mandatory items; acknowledgement is enough"
        return v
    if not heard:
        if P.is_ack_only(text_norm, ext) or not text_norm.strip():
            v.result, v.error_type = "mismatch", "ack_only"
            v.reason = f"Acknowledgement only; {', '.join(_describe(i) for i in expected)} must be read back"
        else:
            v.result, v.error_type = "mismatch", "omitted_item"
            v.reason = f"No readback of {', '.join(_describe(i) for i in expected)}"
        v.confidence = 0.9
        return v

    used: set[int] = set()
    errors: list[tuple[ErrorType, str]] = []
    omitted: list[Item] = []
    matched = 0
    for exp in expected:
        j = _pair(exp, heard, used)
        if j is None:
            # heading digits read back as a speed (or vice versa): same number, other concept
            twin = next((h for k, h in enumerate(heard) if k not in used and h.type != exp.type
                         and _num(h.value) is not None and _num(h.value) == _num(exp.value)), None)
            if twin is not None:
                used.add(heard.index(twin))
                errors.append(("wrong_unit", f"expected {_describe(exp)}, read back as {twin.type} {_describe(twin)}"))
            else:
                omitted.append(exp)
            continue
        used.add(j)
        err, why = _compare(exp, heard[j])
        if err:
            errors.append((err, why))
        else:
            matched += 1

    if errors:
        priority = ["wrong_direction", "wrong_runway", "wrong_unit", "wrong_value"]
        errors.sort(key=lambda e: priority.index(e[0]) if e[0] in priority else 99)
        v.result, v.error_type = "mismatch", errors[0][0]
        v.reason = errors[0][1][0].upper() + errors[0][1][1:]
        v.confidence = 0.9
    elif omitted:
        v.result, v.error_type = ("partial", "omitted_item") if matched else ("mismatch", "omitted_item")
        v.reason = f"Readback omitted {', '.join(_describe(i) for i in omitted)}"
        v.confidence = 0.85
    else:
        v.reason = "Readback matches"
    return v


# ---------------------------------------------------------------------------
# Layer 2: the n-best rule
# ---------------------------------------------------------------------------


def _hypothesis_supports(clearance: OpenClearance, verdict: Verdict, hyp: str,
                         active: list[str] | None) -> bool:
    ext = P.parse(hyp, active, "pilot")
    if verdict.error_type == "wrong_aircraft":
        s = snap(ext.callsign or hyp, [clearance.callsign] + (active or []))
        return s.best == clearance.callsign
    heard_keys = {(h.type if h.type != "hold_short" else "runway", str(h.value).upper().lstrip("0")) for h in ext.items}
    for exp in verdict.expected:
        key = (exp.type if exp.type != "hold_short" else "runway", str(exp.value).upper().lstrip("0"))
        if key in heard_keys:
            # a hypothesis containing the expected value for the disputed item is enough
            if verdict.error_type in ("omitted_item", "ack_only"):
                return True
            paired = next((h for h in ext.items if (h.type == exp.type or exp.type in _RUNWAY_TYPES and h.type in _RUNWAY_TYPES)), None)
            if paired is not None and _compare(exp, paired)[0] is None:
                return True
    return False


def n_best_supports_expected(clearance: OpenClearance, verdict: Verdict, tx: Transmission,
                             active: list[str] | None = None) -> str | None:
    """The first n-best hypothesis in which the expected reading appears, or None."""
    for hyp in tx.n_best:
        if hyp and hyp.strip() and _hypothesis_supports(clearance, verdict, hyp, active):
            return hyp
    return None


# ---------------------------------------------------------------------------
# Full check
# ---------------------------------------------------------------------------


def correction_phrase(clearance: OpenClearance, verdict: Verdict) -> str:
    items = verdict.expected or clearance.items
    body = phrase_from_items("", items)
    if verdict.error_type == "wrong_aircraft":
        return f"{spoken_callsign(clearance.callsign)}, that readback was for you, {body}"
    if verdict.error_type == "missing_readback":
        return f"{spoken_callsign(clearance.callsign)}, confirm {body}"
    if verdict.error_type in ("omitted_item", "ack_only"):
        return f"{spoken_callsign(clearance.callsign)}, read back {body}"
    return f"{spoken_callsign(clearance.callsign)}, negative, {body}"


def check(clearance: OpenClearance, readback: Extraction, tx: Transmission,
          model: CheckerModel | None = None, active: list[str] | None = None) -> Verdict:
    """Rules, then the n-best rule, then the optional cross-encoder. Never raises."""
    text = tx.text_norm or tx.text_raw
    v = rules(clearance, readback, text)
    if v.result == "match":
        return v

    v.correction_phrase = correction_phrase(clearance, v)

    hyp = n_best_supports_expected(clearance, v, tx, active)
    if hyp is not None:
        v.result = "ambiguous"
        v.confidence = 0.5
        v.reason = f"{v.reason}; but hypothesis '{hyp}' contains the expected reading"
        return v

    if tx.asr_confidence < LOW_ASR_CONFIDENCE:
        v.result = "ambiguous"
        v.confidence = 0.5
        v.reason = f"{v.reason}; low speech confidence {tx.asr_confidence:.2f}"
        return v

    if model is not None:
        controller_text = phrase_from_items(clearance.callsign, clearance.items)
        pred = model.predict(controller_text, text)
        if pred is not None:
            label, conf = pred
            model_says_error = label != "correct"
            if model_says_error:
                v.confidence = min(0.99, max(v.confidence, (v.confidence + conf) / 2 + 0.05))
                v.decided_by = "checker_model" if label == v.error_type else "rules"
            elif conf >= 0.5:
                v.result = "ambiguous"
                v.confidence = 0.5
                v.reason = f"{v.reason}; checker model disagrees ({conf:.2f} correct)"
                v.decided_by = "checker_model"
    return v


def missing_verdict(clearance: OpenClearance) -> Verdict:
    """Verdict for a clearance that timed out with no readback."""
    expected = [i for i in clearance.items if i.mandatory]
    v = Verdict(clearance_id=clearance.id, readback_transmission_id=None, result="missing",
                error_type="missing_readback", expected=expected, heard=[], confidence=0.9,
                reason=f"No readback from {clearance.callsign} within {int(clearance.timeout_s)} s",
                decided_by="rules")
    v.correction_phrase = correction_phrase(clearance, v)
    return v
