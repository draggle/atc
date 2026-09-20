"""Turn plan changes into instruction cards with correct radio phraseology.

Parses the change-string grammar written by planner/plan.py. One card per changed flight,
sorted by urgency (seconds until the change must take effect).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

from planner.trajectory import samples_array
from airlines import ICAO_TO_TELEPHONY
from schemas import AircraftState, InstructionCard, Item, Plan, PlannedPath, SimCommand

TELEPHONY = ICAO_TO_TELEPHONY  # one shared table, see backend/airlines.py
DIGITS = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
          "6": "six", "7": "seven", "8": "eight", "9": "nine"}
NATO = {c: w for c, w in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", [
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliett",
    "kilo", "lima", "mike", "november", "oscar", "papa", "quebec", "romeo", "sierra", "tango",
    "uniform", "victor", "whiskey", "xray", "yankee", "zulu"])}
TRANSITION_FT = 18000
MIN_DIRECT_SAVING_NM = 3.0  # below this a direct routing is not worth the radio time
DOGLEG_CAPTURE_NM = 2.0
SAME_HEADING_DEG = 4.0  # a new heading this close to the last one issued is not worth a transmission

_AT = re.compile(r" from t=(\d+)s")
_RE = {
    "direct": re.compile(r"^direct (\w+)"),
    "delay": re.compile(r"^delay entry (\d+) s"),
    "speed": re.compile(r"^speed (\d+) kt \(([+-]\d+)%\)"),
    "altitude": re.compile(r"^altitude (\d+) ft \(([+-]\d+)\)"),
    "heading": re.compile(r"^heading (\d{3}) from t=(\d+)s \((\d+) NM (left|right) dogleg, then direct (\w+)\)"),
    "emerg_turn": re.compile(r"^emergency turn (left|right) heading (\d{3})"),
    "emerg_alt": re.compile(r"^emergency (climb|descend) (\d+) ft"),
}


@dataclass
class Change:
    kind: str
    value: str | float
    text: str
    at_t: float | None = None
    extra: dict | None = None

    @property
    def key(self) -> tuple[str, str]:
        return self.kind, str(self.value)

    @property
    def reason_who(self) -> str:
        m = re.search(r"to clear (\S+)$", self.text)
        return m.group(1) if m else "traffic"


def parse_change(text: str) -> Change | None:
    """Structured view of one change string; None for unresolved/notes."""
    at = _AT.search(text)
    at_t = float(at.group(1)) if at else None
    if m := _RE["direct"].match(text):
        saved = re.search(r"saves ([\d.]+) NM", text)
        return Change("direct", m.group(1), text, at_t, extra={"saves": float(saved.group(1)) if saved else 0.0})
    if m := _RE["delay"].match(text):
        return Change("delay", float(m.group(1)), text)
    if m := _RE["speed"].match(text):
        return Change("speed", float(m.group(1)), text, at_t, extra={"pct": int(m.group(2))})
    if m := _RE["altitude"].match(text):
        return Change("altitude", float(m.group(1)), text, at_t, extra={"delta": int(m.group(2))})
    if m := _RE["heading"].match(text):
        return Change("heading", float(m.group(1)), text, at_t=float(m.group(2)),
                      extra={"offset": float(m.group(3)), "side": m.group(4), "then_direct": m.group(5)})
    if m := _RE["emerg_turn"].match(text):
        return Change("heading", float(m.group(2)), text, at_t=0.0, extra={"side": m.group(1), "emergency": True})
    if m := _RE["emerg_alt"].match(text):
        return Change("altitude", float(m.group(2)), text, at_t=0.0,
                      extra={"delta": 1000 if m.group(1) == "climb" else -1000, "emergency": True})
    return None


# --------------------------------------------------------------------------- phraseology

def say_digits(s: str | int | float) -> str:
    s = str(int(s)) if isinstance(s, float) and s.is_integer() else str(s)
    return " ".join(DIGITS.get(ch, NATO.get(ch.upper(), ch)) for ch in s)


def say_callsign(callsign: str) -> str:
    m = re.match(r"^([A-Z]{3})(\w+)$", callsign)
    if m and m.group(1) in TELEPHONY:
        return f"{TELEPHONY[m.group(1)]} {say_digits(m.group(2))}"
    return say_digits(callsign)


def say_altitude(alt_ft: float) -> str:
    if alt_ft >= TRANSITION_FT:
        return f"flight level {say_digits(int(round(alt_ft / 100)))}"
    thousands, rem = divmod(int(round(alt_ft)), 1000)
    parts = [f"{say_digits(thousands)} thousand"] if thousands else []
    if rem:
        parts.append(f"{say_digits(rem // 100)} hundred")
    return " ".join(parts)


def say_item(item: Item, current_alt_ft: float | None = None) -> str:
    if item.type == "altitude":
        ft = item.value * 100 if item.unit == "FL" else item.value
        verb = item.action or "maintain"
        return f"{verb} and maintain {say_altitude(float(ft))}"
    if item.type == "heading":
        hdg = f"{int(item.value):03d}"
        if item.action in ("turn_left", "turn_right"):
            return f"turn {item.action.split('_')[1]} heading {say_digits(hdg)}"
        return f"fly heading {say_digits(hdg)}"
    if item.type == "speed":
        verb = {"reduce": "reduce speed to", "increase": "increase speed to"}.get(item.action or "", "maintain")
        return f"{verb} {say_digits(int(item.value))} knots"
    if item.type == "route":
        return f"proceed direct {item.value}"
    return str(item.value)


def phrase_for(callsign: str, items: list[Item]) -> str:
    return f"{say_callsign(callsign)}, " + ", ".join(say_item(i) for i in items)


# --------------------------------------------------------------------------- items

def item_for(ch: Change, current_alt_ft: float | None = None) -> Item | None:
    if ch.kind == "altitude":
        ft = float(ch.value)
        delta = (ch.extra or {}).get("delta", 0)
        if current_alt_ft is not None:
            delta = ft - current_alt_ft
        action = "climb" if delta > 0 else "descend" if delta < 0 else "maintain"
        if ft >= TRANSITION_FT:
            return Item(type="altitude", value=int(round(ft / 100)), unit="FL", action=action)
        return Item(type="altitude", value=int(round(ft)), unit="ft", action=action)
    if ch.kind == "heading":
        side = (ch.extra or {}).get("side")
        return Item(type="heading", value=int(ch.value), unit="deg", action=f"turn_{side}" if side else "fly")
    if ch.kind == "speed":
        pct = (ch.extra or {}).get("pct", 0)
        return Item(type="speed", value=int(ch.value), unit="kt", action="reduce" if pct < 0 else "increase")
    if ch.kind == "direct":
        return Item(type="route", value=str(ch.value), unit=None, action="direct")
    return None


def item_to_sim_command(item: Item) -> SimCommand:
    """What the plane does with an item. Frequency/squawk/altimeter have no motion effect."""
    if item.type == "altitude":
        ft = float(item.value) * 100 if item.unit == "FL" else float(item.value)
        return SimCommand(kind="altitude", value=ft)
    if item.type == "heading":
        return SimCommand(kind="heading", value=float(item.value))
    if item.type == "speed":
        return SimCommand(kind="speed", value=float(item.value))
    if item.type == "route" and item.action == "direct":
        return SimCommand(kind="direct", value=str(item.value))
    return SimCommand(kind="none")


def reason_for(ch: Change, callsign: str) -> str:
    who = ch.reason_who
    ex = ch.extra or {}
    if ex.get("emergency"):
        return f"Immediate: predicted loss of separation with {who} within two minutes."
    if ch.kind == "direct":
        return f"Direct routing saves {ex.get('saves', 0):.0f} NM with no conflicts on the direct track."
    if ch.kind == "speed":
        return f"{'Slower' if ex.get('pct', 0) < 0 else 'Faster'} by {abs(ex.get('pct', 0))}% so {callsign} crosses behind {who}."
    if ch.kind == "altitude":
        return f"Level change of {abs(ex.get('delta', 0)):.0f} ft keeps {callsign} vertically clear of {who}."
    if ch.kind == "heading":
        return f"{ex.get('offset', 0):.0f} NM dogleg {ex.get('side', '')} of the crossing with {who}, then direct {ex.get('then_direct', '')}."
    return f"Keeps {callsign} clear of {who}."


# --------------------------------------------------------------------------- cards

def _changes(path: PlannedPath) -> list[Change]:
    return [c for c in (parse_change(t) for t in path.changes) if c is not None]


def cards_from_plan(plan: Plan, previous_plan: Plan | None = None, now_t: float = 0.0,
                    states: list[AircraftState] | None = None,
                    issued: dict[str, float] | None = None) -> list[InstructionCard]:
    """One card per flight whose plan changed (relative to previous_plan, or to nothing).

    Entry delays produce no card: they are applied upstream, not on frequency.
    Cards are sorted by urgency: seconds until the change must be flying.

    `issued` is the heading on each flight's card that is still waiting to be said. While it
    waits the plan is worked out again every few seconds and creeps a degree or two each time;
    measured against the previous plan no step is ever big enough to count, and the card on the
    screen falls further and further behind. Measured against the card, it is replaced as soon
    as it is really out of date.
    """
    prev = {p.callsign: {c.key for c in _changes(p)} for p in previous_plan.paths} if previous_plan else {}
    prev_hdg = {p.callsign: [float(c.value) for c in _changes(p) if c.kind == "heading"]
                for p in previous_plan.paths} if previous_plan else {}
    st = {s.callsign: s for s in (states or [])}
    cards: list[InstructionCard] = []

    def is_new(callsign: str, c: Change) -> bool:
        waiting = (issued or {}).get(callsign) if c.kind == "heading" else None
        if waiting is None and c.key in prev.get(callsign, set()):
            return False
        if c.kind == "heading" and not (c.extra or {}).get("emergency"):
            # A heading within a few degrees of the one already issued is the same instruction.
            known = [waiting] if waiting is not None else prev_hdg.get(callsign, [])
            return not any(abs((float(c.value) - h + 180) % 360 - 180) <= SAME_HEADING_DEG for h in known)
        return True

    def already_flying(callsign: str, c: Change) -> bool:
        """Direct to the fix it is already going direct to. The follow-up card got it there; the
        plan catching up with that a moment later is not a second instruction."""
        s = st.get(callsign)
        return (c.kind == "direct" and s is not None and s.target_hdg_deg is None
                and [w.upper() for w in s.route] == [str(c.value).upper()])

    for path in plan.paths:
        changes = [c for c in _changes(path) if c.kind != "delay" and is_new(path.callsign, c)
                   and not already_flying(path.callsign, c)]
        # A shortcut that saves next to nothing is not worth a transmission. Real cruise traffic
        # already flies nearly straight, so without this the controller drowns in "saves 0 NM"
        # cards. A direct is still issued when it comes with another change (it is then part of a
        # conflict fix), and the planner's path is unaffected either way.
        minor = bool(changes) and all(c.kind == "direct" and (c.extra or {}).get("saves", 0.0) < MIN_DIRECT_SAVING_NM
                                      for c in changes)
        if not changes:
            continue
        alt_now = st[path.callsign].alt_ft if path.callsign in st else None
        items = [i for i in (item_for(c, alt_now) for c in changes) if i is not None]
        if any(i.type == "heading" for i in items):
            items = [i for i in items if i.type != "route"]  # the heading supersedes; direct comes as a follow-up
        if not items:
            continue
        arr = samples_array(path)
        start_t = float(arr[0, 0]) if arr.shape[0] else now_t
        at = [c.at_t for c in changes if c.at_t is not None]
        urgency = max(0.0, (min(at) if at else start_t) - now_t) if not any((c.extra or {}).get("emergency") for c in changes) else 0.0
        primary = max(changes, key=lambda c: 2 if (c.extra or {}).get("emergency") else 1 if c.kind != "direct" else 0)
        cards.append(InstructionCard(
            id=f"card-{path.callsign}-{int(now_t)}-{'-'.join(sorted(k for k, _ in (c.key for c in changes)))}",
            callsign=path.callsign, items=items, phrase=phrase_for(path.callsign, items),
            reason=reason_for(primary, path.callsign), urgency_s=urgency, minor=minor,
            origin="initial" if previous_plan is None else "replan",
            cause=None if primary.kind == "direct" else (primary.reason_who if primary.reason_who != "traffic" else None),
            emergency=bool((primary.extra or {}).get("emergency")),
        ))
    cards.sort(key=lambda c: c.urgency_s)
    return cards


def turning(plan: Plan) -> set[str]:
    """Callsigns whose planned path still holds a heading instruction."""
    return {p.callsign for p in plan.paths if any(c.kind == "heading" for c in _changes(p))}


def followup_cards(plan: Plan, states: list[AircraftState], now_t: float,
                   causes: dict[str, str] | None = None) -> list[InstructionCard]:
    """The second card of a spoken reroute: "proceed direct <exit>", the moment it is safe.

    By voice a reroute is a heading now and a direct later. For a flight on an assigned heading
    the planner first asks whether going direct is clear from where the aircraft is this second
    (plan._hold_heading). When it is, the plan for that flight holds no heading any more, and that
    is the signal here. The card is never offered ahead of time: an instruction worked out for a
    point further on is wrong for an aircraft that is told, and turns, before it gets there.
    Until then `path.via` holds the expected turn-back point, which is what the map draws.
    `causes` is callsign -> what its heading was for ("STORM1"), for the card's tag and reason.
    """
    st = {s.callsign: s for s in states}
    out = []
    for path in plan.paths:
        s = st.get(path.callsign)
        if s is None or s.is_intruder or s.target_hdg_deg is None or not s.route:
            continue
        if any(c.kind == "heading" for c in _changes(path)):
            continue  # still holding the heading: turning back now would not be clear
        who = (causes or {}).get(path.callsign) or ""
        exit_name = s.route[-1]
        item = Item(type="route", value=exit_name, unit=None, action="direct")
        clear = f"Clear of {who}. " if who else ""
        out.append(InstructionCard(
            id=f"card-{path.callsign}-{int(now_t)}-direct", callsign=path.callsign, items=[item],
            phrase=phrase_for(path.callsign, [item]), reason=f"{clear}Back on course, direct {exit_name}.",
            urgency_s=0.0, origin="followup", cause=who or None))
    return out


def release_cards(plan: Plan, states: list[AircraftState], released: set[str], now_t: float,
                  why: str) -> list[InstructionCard]:
    """'Direct <exit>' for flights still on a heading for something that is no longer there.

    `released` is the flights the planner just freed. One whose new path holds no heading
    change, but which is still flying an assigned heading, has to be told to go direct: its
    "direct" change is not new, so cards_from_plan alone would stay silent.
    """
    st = {s.callsign: s for s in states}
    out = []
    for path in plan.paths:
        s = st.get(path.callsign)
        if path.callsign not in released or s is None or s.target_hdg_deg is None or not s.route:
            continue
        if any(c.kind == "heading" or (c.extra or {}).get("emergency") for c in _changes(path)):
            continue
        item = Item(type="route", value=s.route[-1], unit=None, action="direct")
        out.append(InstructionCard(
            id=f"card-{path.callsign}-{int(now_t)}-release", callsign=path.callsign, items=[item],
            phrase=phrase_for(path.callsign, [item]), reason=why, urgency_s=0.0, origin="release"))
    return out


def _dogleg_index(arr: np.ndarray) -> int:
    a, b = arr[0, 1:3], arr[-1, 1:3]
    u = b - a
    L = float(np.hypot(*u)) or 1.0
    d = np.abs((arr[:, 1] - a[0]) * u[1] - (arr[:, 2] - a[1]) * u[0]) / L
    return int(np.argmax(d))
