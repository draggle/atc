"""Per-callsign six-state machine (HAAWAII) with an OpenClearance store and timeouts."""
from __future__ import annotations

from collections import deque
from enum import Enum
from typing import NamedTuple

from schemas import ClearanceStatus, Extraction, Item, OpenClearance, Transmission
from tower.callsign import similar_pairs

DEFAULT_TIMEOUT_S = 25.0


class State(str, Enum):
    UNKNOWN = "UNKNOWN"
    EXPECTING_READBACK = "EXPECTING_READBACK"
    READBACK_OK = "READBACK_OK"
    READBACK_ERROR = "READBACK_ERROR"
    MISSING_READBACK = "MISSING_READBACK"
    PILOT_REPORTING = "PILOT_REPORTING"


_STATUS_TO_STATE: dict[str, State] = {
    "open": State.EXPECTING_READBACK,
    "matched": State.READBACK_OK,
    "mismatched": State.READBACK_ERROR,
    "partial": State.READBACK_ERROR,
    "missing": State.MISSING_READBACK,
    "uncertain": State.READBACK_ERROR,
}


class Exchange(NamedTuple):
    transmission: Transmission
    extraction: Extraction
    clearance_id: str | None


class Match(NamedTuple):
    clearance: OpenClearance | None
    by_callsign: bool  # False when matched on item overlap only (possible wrong aircraft)


def _item_key(i: Item) -> tuple[str, str]:
    return (i.type, str(i.value))


class Track:
    """Everything Tower remembers about one callsign."""

    def __init__(self, callsign: str, history_len: int = 50) -> None:
        self.callsign = callsign
        self.state = State.UNKNOWN
        self.clearances: list[OpenClearance] = []
        self.history: deque[Exchange] = deque(maxlen=history_len)

    def open_clearances(self) -> list[OpenClearance]:
        return [c for c in self.clearances if c.status == "open"]


class StateStore:
    """In-memory store keyed by callsign. Synchronous; the integrator serializes access."""

    def __init__(self, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self.timeout_s = timeout_s
        self.tracks: dict[str, Track] = {}
        self._by_id: dict[str, OpenClearance] = {}
        self._seq = 0

    # -- helpers ---------------------------------------------------------------------------------

    def track(self, callsign: str) -> Track:
        t = self.tracks.get(callsign)
        if t is None:
            t = self.tracks[callsign] = Track(callsign)
        return t

    def next_id(self) -> str:
        self._seq += 1
        return f"c{self._seq}"

    def get(self, clearance_id: str) -> OpenClearance | None:
        return self._by_id.get(clearance_id)

    def state_of(self, callsign: str) -> State:
        t = self.tracks.get(callsign)
        return t.state if t else State.UNKNOWN

    # -- transitions -----------------------------------------------------------------------------

    def open(self, clearance: OpenClearance) -> OpenClearance:
        """Register a controller clearance; the aircraft is now EXPECTING_READBACK."""
        if not clearance.timeout_s:
            clearance.timeout_s = self.timeout_s
        t = self.track(clearance.callsign)
        t.clearances.append(clearance)
        self._by_id[clearance.id] = clearance
        if any(i.mandatory for i in clearance.items):
            t.state = State.EXPECTING_READBACK
        return clearance

    def record(self, callsign: str | None, tx: Transmission, ext: Extraction,
               clearance_id: str | None = None) -> None:
        if callsign:
            self.track(callsign).history.append(Exchange(tx, ext, clearance_id))

    def find_clearance_for(self, ext: Extraction) -> Match:
        """Which open clearance is this pilot transmission answering?

        1. The heard callsign's most recent open clearance.
        2. Otherwise another aircraft's open clearance whose items overlap what was heard
           (candidate wrong-aircraft readback).
        3. Otherwise, with no callsign heard and exactly one open clearance anywhere, that one.
        """
        if ext.callsign:
            own = self.track(ext.callsign).open_clearances()
            if own:
                return Match(own[-1], True)
        heard = {_item_key(i) for i in ext.items}
        if heard:
            best: OpenClearance | None = None
            best_overlap = 0
            for c in self.all_open():
                if ext.callsign and c.callsign == ext.callsign:
                    continue
                overlap = len(heard & {_item_key(i) for i in c.items})
                if overlap > best_overlap:
                    best, best_overlap = c, overlap
            if best is not None:
                return Match(best, False)
        if not ext.callsign:
            opens = self.all_open()
            if len(opens) == 1:
                return Match(opens[0], False)
        return Match(None, False)

    def on_pilot_transmission(self, ext: Extraction, tx: Transmission) -> OpenClearance | None:
        """Route a pilot transmission to the clearance it answers, or mark PILOT_REPORTING."""
        m = self.find_clearance_for(ext)
        self.record(ext.callsign, tx, ext, m.clearance.id if m.clearance else None)
        if m.clearance is None and ext.callsign:
            t = self.track(ext.callsign)
            if t.state != State.EXPECTING_READBACK:
                t.state = State.PILOT_REPORTING
        return m.clearance

    def resolve(self, clearance_id: str, status: ClearanceStatus) -> OpenClearance | None:
        """Close a clearance with its final status and move the aircraft's state."""
        c = self._by_id.get(clearance_id)
        if c is None:
            return None
        c.status = status
        t = self.track(c.callsign)
        if status == "open" or t.open_clearances():
            t.state = State.EXPECTING_READBACK
        else:
            t.state = _STATUS_TO_STATE[status]
        return c

    def tick(self, now: float) -> list[OpenClearance]:
        """Time out open clearances with no readback. Returns those newly marked missing."""
        out: list[OpenClearance] = []
        for c in self.all_open():
            if now - c.issued_at >= c.timeout_s:
                self.resolve(c.id, "missing")
                out.append(c)
        return out

    # -- queries ---------------------------------------------------------------------------------

    def all_open(self) -> list[OpenClearance]:
        return [c for t in self.tracks.values() for c in t.open_clearances()]

    def open_clearances(self, callsign: str) -> list[OpenClearance]:
        t = self.tracks.get(callsign)
        return t.open_clearances() if t else []

    def active_callsigns(self) -> list[str]:
        return sorted(self.tracks)

    def history(self, callsign: str, n: int = 5) -> list[Exchange]:
        t = self.tracks.get(callsign)
        if not t:
            return []
        return list(t.history)[-n:]

    def similar_callsign_warnings(self, active: list[str] | None = None) -> list[tuple[str, str]]:
        return similar_pairs(active if active is not None else self.active_callsigns())
