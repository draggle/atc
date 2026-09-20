"""Grammar parser: normalized text -> Extraction. Regexes over the dozen command types.

Runs in milliseconds and is deterministic. `parse_with_fallback` makes one structured-output
LLM call only when the grammar leaves too many words unexplained or a command keyword has no value.
"""
from __future__ import annotations

import re
from typing import Any, Protocol

from schemas import Extraction, Item, Speaker
from tower import callsign as cs

# Words that carry no command content and should not count as unexplained.
FILLER = {
    "and", "to", "the", "a", "for", "now", "then", "roger", "wilco", "copied", "copy", "affirm",
    "affirmative", "good", "day", "morning", "afternoon", "evening", "night", "hello", "bye",
    "thanks", "thank", "you", "sir", "maam", "ok", "okay", "uh", "um", "please", "correct",
    "tower", "ground", "approach", "departure", "center", "centre", "radar", "control",
    "with", "is", "at", "on", "of", "we", "will", "are", "so", "long", "cheers",
}
ACK_WORDS = {"roger", "wilco", "copied", "copy", "affirm", "affirmative", "ok", "okay"}
CHATTER_TOLERANCE = 0.15

_DIR_VERBS = r"(?:descend(?:ing)?|climb(?:ing)?|maintain(?:ing)?|down|up)"
_ALT_PREFIX = rf"(?:(?P<verb>{_DIR_VERBS})(?: and maintain| to| and)?\s+)?"
RE_FL = re.compile(rf"\b{_ALT_PREFIX}flight level (?P<fl>\d{{2,3}})\b")
RE_FT = re.compile(
    rf"\b(?P<verb>{_DIR_VERBS})(?: and maintain| to| and)?\s+(?P<ft>\d{{3,5}})(?!\s+knots)(?:\s+(?:feet|ft))?\b"
)  # not "maintain 545 knots": that is a speed, and it used to alert as a wrong unit
RE_FT_UNIT = re.compile(r"\b(?P<ft>\d{3,5}) (?:feet|ft)\b")
RE_HDG_TURN = re.compile(r"\b(?:turn(?:ing)?\s+)?(?P<dir>left|right)(?:\s+(?:turn\s+)?heading|\s+turn|\s+to)?\s+(?P<hdg>\d{3})\b")
RE_HDG = re.compile(r"\b(?:fly\s+|maintain\s+)?heading\s+(?P<hdg>\d{3})\b")
RE_DIRECT = re.compile(r"\b(?:proceed\s+|cleared\s+)?direct(?:\s+to)?\s+(?P<wpt>[A-Za-z]{3,6})\b")
# "ESTIR direct": pilots shorten a direct this way. Only when no fix follows "direct", so
# "proceed direct ESTIR" is never read as a direct to PROCEED.
# ... and a word followed by digits is a spelled-out callsign ("ESTIR direct NRL 614"), not a fix.
RE_DIRECT_POST = re.compile(r"\b(?P<wpt>[A-Za-z]{3,6})\s+direct\b(?!\s+(?:to\s+)?[A-Za-z]{3,6}\b(?!\s+\d))")
RE_SPEED = re.compile(
    r"\b(?:(?:reduce|increase|maintain)\s+)?(?:speed|indicated|mach)?\s*(?:to\s+)?(?P<spd>\d{2,3})\s+knots\b"
    r"|\b(?:(?:reduce|increase|maintain)\s+)?speed\s+(?:to\s+)?(?P<spd2>\d{2,3})\b"
    r"|\b(?:reduce|increase)\s+(?:to\s+)?(?P<spd3>\d{2,3})\b"
    r"|\b(?P<spd4>\d{2,3})\s+on\s+the\s+speed\b"
)
RE_FREQ = re.compile(r"\b(?:contact\s+(?:[a-z]+\s+){0,2})?(?P<freq>1[123]\d\.\d{1,3})\b")
RE_FREQ_NODOT = re.compile(r"\b(?:contact\s+(?:[a-z]+\s+){0,2})?(?P<freq>1[123]\d{3,4})\b")
RE_SQUAWK = re.compile(r"\bsquawk(?:ing)?\s+(?P<sq>\d{4})\b")
RE_ALTIMETER = re.compile(r"\b(?P<kind>altimeter|qnh)\s+(?P<val>\d{4}|\d{2}\.\d{2}|\d{3})\b")
RE_RWY = re.compile(
    r"\b(?P<verb>cleared to land|cleared for takeoff|cleared for take off|cleared takeoff|hold short(?: of)?|line up and wait|lineup and wait|cleared to cross|cross)?"
    r"(?:\s*runway)?\s+(?P<rwy>(?:0?[1-9]|[12]\d|3[0-6])[LRC]?)\b"
)
RE_RWY_LEAD = re.compile(r"\brunway\s+(?P<rwy>(?:0?[1-9]|[12]\d|3[0-6])[LRC]?)\b")

_RWY_ACTIONS = {
    "cleared to land": "cleared_land", "cleared for takeoff": "cleared_takeoff",
    "cleared for take off": "cleared_takeoff", "cleared takeoff": "cleared_takeoff",
    "hold short": "hold_short", "hold short of": "hold_short", "line up and wait": "line_up_wait",
    "lineup and wait": "line_up_wait", "cleared to cross": "cross", "cross": "cross",
}
_RWY_VERB_WORDS = re.compile(r"\b(cleared to land|cleared for take ?off|hold short|line ?up and wait)\b")

# Words a pilot says next to "direct" that can never be the fix. "unable direct" is a refusal and
# "say again direct" is a question: neither is a readback of a direct to UNABLE or to AGAIN.
NOT_A_FIX = {
    "unable", "say", "again", "negative", "standby", "stand", "by", "request", "requesting", "confirm",
    "proceed", "proceeding", "cleared", "going", "turning", "climbing", "descending", "maintaining",
    "when", "able", "expect", "via", "was", "that", "did", "not", "no", "yes",
}

COMMAND_KEYWORDS: dict[str, str] = {
    "descend": "altitude", "climb": "altitude", "heading": "heading", "direct": "route",
    "speed": "speed", "contact": "frequency", "squawk": "squawk", "altimeter": "altimeter",
    "qnh": "altimeter", "runway": "runway", "cleared": "runway", "hold": "hold_short",
}

_CALLSIGN_TOKEN = re.compile(r"^[A-Z]{3}\d{1,4}[A-Z]{0,2}$|^[A-Z]{4,6}$|^N\d{1,5}[A-Z]{0,2}$")
_SHORT_CALLSIGN = re.compile(r"^\d{2,4}$")


class _Span:
    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens = text.split()
        self.offsets: list[tuple[int, int]] = []
        pos = 0
        for t in self.tokens:
            start = text.index(t, pos)
            self.offsets.append((start, start + len(t)))
            pos = start + len(t)
        self.covered = [False] * len(self.tokens)

    def mark(self, start: int, end: int) -> None:
        for i, (s, e) in enumerate(self.offsets):
            if s < end and e > start:
                self.covered[i] = True

    def is_covered_range(self, start: int, end: int) -> bool:
        return any(self.covered[i] for i, (s, e) in enumerate(self.offsets) if s < end and e > start)


def _direction(verb: str | None) -> str | None:
    if not verb:
        return None
    if verb.startswith("desc") or verb == "down":
        return "descend"
    if verb.startswith("climb") or verb == "up":
        return "climb"
    if verb.startswith("maintain"):
        return "maintain"
    return None


def _extract_items(span: _Span) -> list[Item]:
    text = span.text
    items: list[Item] = []

    def add(m: re.Match[str], item: Item) -> None:
        span.mark(m.start(), m.end())
        items.append(item)

    for m in RE_FL.finditer(text):
        add(m, Item(type="altitude", value=int(m.group("fl")), unit="FL",
                    action=_direction(m.group("verb"))))
    for m in RE_FT.finditer(text):
        if span.is_covered_range(m.start(), m.end()):
            continue
        val = int(m.group("ft"))
        if val < 1000:  # "descend 310" without a unit is a flight level
            add(m, Item(type="altitude", value=val, unit="FL", action=_direction(m.group("verb"))))
        else:
            add(m, Item(type="altitude", value=val, unit="ft", action=_direction(m.group("verb"))))
    for m in RE_FT_UNIT.finditer(text):
        if span.is_covered_range(m.start(), m.end()):
            continue
        add(m, Item(type="altitude", value=int(m.group("ft")), unit="ft", action=None))
    for m in RE_HDG_TURN.finditer(text):
        if span.is_covered_range(m.start(), m.end()):
            continue
        add(m, Item(type="heading", value=int(m.group("hdg")), unit="deg",
                    action="turn_left" if m.group("dir") == "left" else "turn_right"))
    for m in RE_HDG.finditer(text):
        if span.is_covered_range(m.start(), m.end()):
            continue
        add(m, Item(type="heading", value=int(m.group("hdg")), unit="deg", action="fly_heading"))
    for m in RE_DIRECT.finditer(text):
        wpt = m.group("wpt")
        if wpt.lower() in COMMAND_KEYWORDS or wpt.lower() in FILLER:
            continue
        if re.match(r"\s+\d", text[m.end():]) and (len(wpt) == 3 or _is_airline_word(wpt)):
            continue  # a callsign, not a fix: spelled out ("NRL 614") or shortened ("canada 123")
        add(m, Item(type="route", value=wpt.upper(), unit=None, action="direct"))
    for m in RE_DIRECT_POST.finditer(text):
        wpt = m.group("wpt")
        if wpt.lower() in COMMAND_KEYWORDS or wpt.lower() in FILLER or wpt.lower() in NOT_A_FIX:
            continue
        if span.is_covered_range(m.start(), m.end()):
            continue
        add(m, Item(type="route", value=wpt.upper(), unit=None, action="direct"))
    for m in RE_SPEED.finditer(text):
        if span.is_covered_range(m.start(), m.end()):
            continue
        spd = m.group("spd") or m.group("spd2") or m.group("spd3") or m.group("spd4")
        add(m, Item(type="speed", value=int(spd), unit="kt", action="speed"))
    for m in RE_FREQ.finditer(text):
        add(m, Item(type="frequency", value=float(m.group("freq")), unit="MHz",
                    action="contact" if m.group(0).startswith("contact") else None))
    for m in RE_FREQ_NODOT.finditer(text):
        if span.is_covered_range(m.start(), m.end()):
            continue
        raw = m.group("freq")
        add(m, Item(type="frequency", value=float(f"{raw[:3]}.{raw[3:]}"), unit="MHz",
                    action="contact" if m.group(0).startswith("contact") else None))
    for m in RE_SQUAWK.finditer(text):
        add(m, Item(type="squawk", value=m.group("sq"), unit=None, action="squawk"))
    for m in RE_ALTIMETER.finditer(text):
        raw = m.group("val")
        if m.group("kind") == "qnh" or raw[0] in "01" or len(raw) == 3:
            add(m, Item(type="altimeter", value=int(float(raw)), unit="hPa", action="altimeter"))
        else:
            val = float(raw) if "." in raw else int(raw) / 100
            add(m, Item(type="altimeter", value=round(val, 2), unit="inHg", action="altimeter"))
    # Runways: verb + designator, or "runway NN" anywhere, with the verb found nearby.
    verbs = [(vm.start(), vm.end(), vm.group(1)) for vm in _RWY_VERB_WORDS.finditer(text)]
    for m in RE_RWY.finditer(text):
        if span.is_covered_range(m.start("rwy"), m.end("rwy")):
            continue
        verb = m.group("verb")
        has_runway_word = "runway" in m.group(0)
        if not verb and not has_runway_word:
            continue
        if verb is None:
            for vs, ve, vtext in verbs:
                if abs(vs - m.start()) < 40:
                    verb = vtext
                    span.mark(vs, ve)
                    break
        action = _RWY_ACTIONS.get(re.sub(r"\s+", " ", verb).replace("take off", "takeoff")) if verb else None
        rwy = m.group("rwy")
        rwy = rwy.zfill(3 if rwy[-1].isalpha() else 2) if len(rwy) < (3 if rwy[-1].isalpha() else 2) else rwy
        itype = "hold_short" if action == "hold_short" else "runway"
        add(m, Item(type=itype, value=rwy, unit=None, action=action))
    return items


def _find_callsign(span: _Span, speaker: Speaker, active: list[str] | None) -> tuple[str | None, bool]:
    """Return (callsign, ambiguous). Controller leads with it, pilot trails with it."""
    toks = span.tokens
    candidates = [(i, t) for i, t in enumerate(toks) if _CALLSIGN_TOKEN.match(t)
                  and not span.covered[i] and cs.is_callsign_token(t)]
    idx: int | None = None
    if candidates:
        idx = candidates[0][0] if speaker == "controller" else candidates[-1][0]
    else:
        # shortened callsign: bare 2-4 digit group not consumed by any item, at the edge
        free = [(i, t) for i, t in enumerate(toks) if _SHORT_CALLSIGN.match(t) and not span.covered[i]]
        if free:
            idx = free[0][0] if speaker == "controller" else free[-1][0]
    if idx is None:
        return None, False
    heard = toks[idx]
    span.covered[idx] = True
    # "canada 123": pull the word before a bare number in to help snapping
    if _SHORT_CALLSIGN.match(heard) and idx > 0 and not span.covered[idx - 1] \
            and toks[idx - 1].isalpha() and toks[idx - 1] not in FILLER \
            and toks[idx - 1] not in COMMAND_KEYWORDS:
        heard = f"{toks[idx - 1]} {heard}"
        span.covered[idx - 1] = True
    if active:
        s = cs.snap(heard, active)
        if s.best:
            return s.best, s.ambiguous
        if _SHORT_CALLSIGN.match(toks[idx]):
            return None, False
    return heard.replace(" ", "").upper() if not _SHORT_CALLSIGN.match(toks[idx]) else toks[idx], False


def _is_airline_word(word: str) -> bool:
    """One word of an airline's radio name: "canada" of "air canada", "speedbird"."""
    w = word.lower()
    return any(w in name.split() for name in cs.TELEPHONY)


def parse(text_norm: str, active_callsigns: list[str] | None = None, speaker: Speaker = "unknown",
          transmission_id: str = "") -> Extraction:
    """Grammar-parse normalized text into an Extraction. Never raises on odd input."""
    span = _Span(text_norm.strip())
    items = _extract_items(span)
    callsign, _ambiguous = _find_callsign(span, speaker, active_callsigns)
    unexplained = sum(1 for i, t in enumerate(span.tokens)
                      if not span.covered[i] and t.lower() not in FILLER)
    return Extraction(transmission_id=transmission_id, callsign=callsign, items=items,
                      unexplained_words=unexplained, method="grammar")


def callsign_is_ambiguous(text_norm: str, active_callsigns: list[str], speaker: Speaker) -> cs.Snap:
    """Re-run the callsign snap for callers that need the runner-up (the pipeline)."""
    span = _Span(text_norm.strip())
    _extract_items(span)
    toks = span.tokens
    cands = [t for i, t in enumerate(toks) if not span.covered[i]
             and (_CALLSIGN_TOKEN.match(t) or _SHORT_CALLSIGN.match(t))]
    if not cands:
        return cs.Snap(None, 0.0, False, None)
    heard = cands[0] if speaker == "controller" else cands[-1]
    return cs.snap(heard, active_callsigns)


def is_ack_only(text_norm: str, extraction: Extraction) -> bool:
    """Bare 'roger' / 'wilco' / callsign-only replies with no command content."""
    if extraction.items:
        return False
    words = [w.lower() for w in text_norm.split()]
    content = [w for w in words if w not in FILLER and not _CALLSIGN_TOKEN.match(w.upper())
               and not _SHORT_CALLSIGN.match(w)]
    return len(content) == 0


def needs_fallback(text_norm: str, extraction: Extraction) -> bool:
    """True when the grammar left too many words unexplained or a keyword has no value."""
    n = len(text_norm.split())
    if n == 0:
        return False
    if extraction.unexplained_words >= 2 and extraction.unexplained_words / n > CHATTER_TOLERANCE:
        return True
    found = {i.type for i in extraction.items}
    for word in text_norm.lower().split():
        want = COMMAND_KEYWORDS.get(word)
        if want and want not in found and not (want == "hold_short" and "runway" in found) \
                and not (want == "runway" and "hold_short" in found):
            return True
    return False


class Extractor(Protocol):
    def extract(self, text: str, active: list[str] | None = None,
                transmission_id: str = "") -> Extraction: ...


def parse_with_fallback(text_norm: str, active: list[str] | None, speaker: Speaker,
                        llm: Extractor | None, transmission_id: str = "") -> Extraction:
    """Grammar first; one LLM structured-output call only when the grammar is clearly insufficient."""
    ext = parse(text_norm, active, speaker, transmission_id)
    if llm is None or not needs_fallback(text_norm, ext):
        return ext
    try:
        out = llm.extract(text_norm, active, transmission_id=transmission_id)
    except Exception:  # noqa: BLE001 - the fallback must never take down tier 1
        return ext
    if out.callsign is None:
        out.callsign = ext.callsign
    if not out.items:
        out.items = ext.items
    # "llm" means the model supplied or changed what was heard. Stray words can wake the fallback
    # on a transmission the grammar read completely ("JZA9 1 2 confirm turn left heading 018"): if
    # the model only agrees, nothing was guessed, and nobody downstream should treat it as a guess.
    if ext.items and [(i.type, i.value) for i in out.items] == [(i.type, i.value) for i in ext.items]:
        out.method = "grammar"
    return out


def item_summary(items: list[Item]) -> dict[str, Any]:
    return {f"{i.type}{'/' + i.action if i.action else ''}": i.value for i in items}
