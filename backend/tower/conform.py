"""Radar verification: does the aircraft do what it read back?

A correct readback followed by wrong flying is invisible to any readback check. The monitor
watches altitude, heading, and direct-to clearances against simulator/radar states.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from schemas import AircraftState, Item, OpenClearance, Verdict

ALT_TOLERANCE_FT = 300.0
WRONG_WAY_S = 20.0
STALL_S = 30.0  # no movement toward the cleared level at all
STALL_MIN_PROGRESS_FT = 200.0
HDG_CONVERGE_S = 60.0
HDG_TOLERANCE_DEG = 10.0
DIRECT_TOLERANCE_DEG = 15.0
MAX_WATCH_S = 180.0


def _ang_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _bearing(dx: float, dy: float) -> float:
    """Compass bearing (deg, north = +y) from origin to (dx, dy)."""
    return math.degrees(math.atan2(dx, dy)) % 360.0


@dataclass
class Watch:
    clearance: OpenClearance
    item: Item
    started_at: float
    start_alt: float | None = None
    last_alt: float | None = None
    last_t: float | None = None
    wrong_way_since: float | None = None
    reached: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def callsign(self) -> str:
        return self.clearance.callsign


class ConformanceMonitor:
    """Register watches after a readback closes; call tick() with fresh radar states."""

    def __init__(self, waypoints: dict[str, tuple[float, float]] | None = None) -> None:
        self.waypoints = {k.upper(): v for k, v in (waypoints or {}).items()}
        self.watches: list[Watch] = []

    def watch(self, clearance: OpenClearance, items: list[Item] | None = None,
              now: float | None = None) -> list[Watch]:
        """Watch the motion-affecting items of a clearance (what the pilot read back, ideally)."""
        t0 = now if now is not None else clearance.issued_at
        out: list[Watch] = []
        for item in (items if items is not None else clearance.items):
            if item.type in ("altitude", "heading", "route"):
                if item.type == "route" and str(item.value).upper() not in self.waypoints:
                    continue
                w = Watch(clearance=clearance, item=item, started_at=t0)
                self.watches.append(w)
                out.append(w)
        return out

    def watching(self, callsign: str) -> bool:
        return any(w.callsign == callsign for w in self.watches)

    def tick(self, states: list[AircraftState], now: float) -> list[Verdict]:
        by_cs = {s.callsign: s for s in states}
        verdicts: list[Verdict] = []
        keep: list[Watch] = []
        for w in self.watches:
            s = by_cs.get(w.callsign)
            if s is None:
                if now - w.started_at < MAX_WATCH_S:
                    keep.append(w)
                continue
            v = self._evaluate(w, s, now)
            if v is not None:
                verdicts.append(v)
            elif not w.reached and now - w.started_at < MAX_WATCH_S:
                keep.append(w)
        self.watches = keep
        return verdicts

    # -- per-item checks -------------------------------------------------------------------------

    def _verdict(self, w: Watch, heard: Item, reason: str) -> Verdict:
        return Verdict(clearance_id=w.clearance.id, readback_transmission_id=None, result="mismatch",
                       error_type="wrong_value", expected=[w.item], heard=[heard], confidence=0.9,
                       reason=f"Radar: {reason}", decided_by="rules")

    def _evaluate(self, w: Watch, s: AircraftState, now: float) -> Verdict | None:
        elapsed = now - w.started_at
        if w.item.type == "altitude":
            return self._altitude(w, s, now, elapsed)
        if w.item.type == "heading":
            return self._heading(w, s, elapsed)
        if w.item.type == "route":
            return self._direct(w, s, elapsed)
        w.reached = True
        return None

    def _altitude(self, w: Watch, s: AircraftState, now: float, elapsed: float) -> Verdict | None:
        cleared = float(w.item.value) * 100 if w.item.unit == "FL" else float(w.item.value)
        alt = s.alt_ft
        if w.start_alt is None:
            w.start_alt, w.last_alt, w.last_t = alt, alt, now
            if abs(alt - cleared) <= ALT_TOLERANCE_FT:
                w.reached = True
            return None
        needed = cleared - w.start_alt  # sign of the required motion
        observed = Item(type="altitude", value=int(round(alt)), unit="ft")
        if abs(alt - cleared) <= ALT_TOLERANCE_FT:
            w.reached = True
            return None
        # passed through the cleared level
        if needed > 0 and alt > cleared + ALT_TOLERANCE_FT or needed < 0 and alt < cleared - ALT_TOLERANCE_FT:
            w.reached = True
            return self._verdict(w, observed, f"{w.callsign} passed through cleared level {int(cleared)} ft, now at {int(alt)} ft")
        # moving the wrong way
        dt = now - w.last_t if w.last_t is not None else 0.0
        rate = (alt - (w.last_alt if w.last_alt is not None else alt)) / dt if dt > 0 else 0.0
        wrong_way = needed != 0 and rate * needed < 0 and abs(rate) > 1.0  # > 60 ft/min
        if wrong_way:
            w.wrong_way_since = w.wrong_way_since if w.wrong_way_since is not None else now
            if now - w.wrong_way_since >= WRONG_WAY_S:
                w.reached = True
                direction = "climbing" if rate > 0 else "descending"
                return self._verdict(w, observed, f"{w.callsign} {direction} away from cleared level {int(cleared)} ft, now at {int(alt)} ft")
        else:
            w.wrong_way_since = None
        # never started
        if elapsed >= STALL_S and abs(alt - w.start_alt) < STALL_MIN_PROGRESS_FT and abs(needed) > ALT_TOLERANCE_FT:
            w.reached = True
            return self._verdict(w, observed, f"{w.callsign} has not left {int(w.start_alt)} ft after {int(elapsed)} s; cleared {int(cleared)} ft")
        w.last_alt, w.last_t = alt, now
        return None

    def _heading(self, w: Watch, s: AircraftState, elapsed: float) -> Verdict | None:
        cleared = float(w.item.value) % 360
        diff = _ang_diff(s.hdg_deg, cleared)
        if diff <= HDG_TOLERANCE_DEG:
            w.reached = True
            return None
        if elapsed >= HDG_CONVERGE_S:
            w.reached = True
            return self._verdict(w, Item(type="heading", value=int(round(s.hdg_deg)) % 360, unit="deg"),
                                 f"{w.callsign} heading {int(s.hdg_deg):03d} has not converged on {int(cleared):03d} after {int(elapsed)} s")
        return None

    def _direct(self, w: Watch, s: AircraftState, elapsed: float) -> Verdict | None:
        wp = self.waypoints.get(str(w.item.value).upper())
        if wp is None:
            w.reached = True
            return None
        brg = _bearing(wp[0] - s.x_nm, wp[1] - s.y_nm)
        dist = math.hypot(wp[0] - s.x_nm, wp[1] - s.y_nm)
        if dist < 2.0 or _ang_diff(s.hdg_deg, brg) <= DIRECT_TOLERANCE_DEG:
            w.reached = True
            return None
        if elapsed >= HDG_CONVERGE_S:
            w.reached = True
            return self._verdict(w, Item(type="heading", value=int(round(s.hdg_deg)) % 360, unit="deg"),
                                 f"{w.callsign} heading {int(s.hdg_deg):03d} is not tracking to {w.item.value} (bearing {int(brg):03d})")
        return None
