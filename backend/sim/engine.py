"""Own simulator: flat x,y plane in NM, kinematic aircraft, waypoint routes.

Implements the `Sim` protocol from schemas.py. Each `step(dt)`:
turn toward the target heading (or the bearing to the next waypoint) at TURN_RATE,
climb or descend toward the target altitude at CLIMB_FPM, accelerate toward the
target speed at ACCEL_KT_S, then advance the position by ground speed.
Runs in real time (dt=1.0 at 1 Hz) or as fast as the caller can loop for batch evaluation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from schemas import AircraftState, Disruption, FlightSpec, Scenario, SimCommand, Waypoint, Zone
from sim.geo import NM_PER_KT_S, bearing_deg, dist_nm, unit_vector, wrap180

TURN_RATE_DEG_S = 3.0
CLIMB_FPM = 1500.0
ACCEL_KT_S = 1.0
CAPTURE_NM = 1.0
ABEAM_NM = 3.0  # if closer than this and the distance starts growing, count the waypoint as passed
EXIT_MARGIN_NM = 20.0  # aircraft beyond the sector edge plus this are removed
DEFAULT_INTRUDER_ALT_FT = 30000.0


@dataclass
class Aircraft:
    """Mutable internal state. `to_state()` produces the pydantic contract object."""

    callsign: str
    x: float
    y: float
    alt: float
    target_alt: float
    hdg: float
    gs: float
    target_gs: float
    route: list[str] = field(default_factory=list)
    target_hdg: float | None = None  # set => route following suspended
    actype: str = "A320"
    is_intruder: bool = False
    spawned_at: float = 0.0
    distance_nm: float = 0.0
    airborne_s: float = 0.0
    _last_wp_dist: float | None = None

    def to_state(self, t: float) -> AircraftState:
        return AircraftState(
            callsign=self.callsign, x_nm=self.x, y_nm=self.y, alt_ft=self.alt,
            target_alt_ft=self.target_alt, hdg_deg=self.hdg, target_hdg_deg=self.target_hdg,
            gs_kt=self.gs, target_gs_kt=self.target_gs, route=list(self.route),
            actype=self.actype, is_intruder=self.is_intruder, t=t,
        )


class Simulator:
    """Flat-plane kinematic simulator. See module docstring."""

    def __init__(self, scenario: Scenario | None = None) -> None:
        self.t: float = 0.0
        self.sector_nm: float = 200.0
        self.waypoints: dict[str, Waypoint] = {}
        self.zones: list[Zone] = []
        self.active: dict[str, Aircraft] = {}
        self.removed: dict[str, Aircraft] = {}
        self.pending: list[FlightSpec] = []
        self.rng = np.random.default_rng(0)
        if scenario is not None:
            self.spawn(scenario)

    # -- Sim protocol -------------------------------------------------------

    def spawn(self, scenario: Scenario) -> None:
        """Register the scenario's waypoints, zones and flights. Flights appear at entry_time_s."""
        self.sector_nm = scenario.sector_nm
        self.rng = np.random.default_rng(scenario.seed)
        self.waypoints.update({w.name: w for w in scenario.waypoints})
        self.zones.extend(scenario.zones)
        self.pending.extend(scenario.flights)
        self.pending.sort(key=lambda f: f.entry_time_s)
        self._spawn_due()

    def aircraft(self) -> list[AircraftState]:
        return [a.to_state(self.t) for a in self.active.values()]

    def apply(self, callsign: str, cmd: SimCommand) -> None:
        """Apply a clearance. The plane obeys whatever it is told, right or wrong."""
        a = self.active.get(callsign)
        if a is None or cmd.kind == "none" or cmd.value is None:
            return
        if cmd.kind == "altitude":
            a.target_alt = float(cmd.value)
        elif cmd.kind == "heading":
            a.target_hdg = float(cmd.value) % 360.0
        elif cmd.kind == "speed":
            a.target_gs = max(100.0, float(cmd.value))
        elif cmd.kind == "direct":
            name = str(cmd.value)
            if name not in self.waypoints:
                return
            if name in a.route:
                a.route = a.route[a.route.index(name):]
            else:
                a.route = [name]
            a.target_hdg = None
            a._last_wp_dist = None

    def step(self, dt: float) -> None:
        self.t += dt
        self._spawn_due()
        for a in list(self.active.values()):
            self._advance(a, dt)
            if self._should_remove(a):
                self.removed[a.callsign] = self.active.pop(a.callsign)

    # -- extras -------------------------------------------------------------

    def add_disruption(self, d: Disruption, alt_ft: float = DEFAULT_INTRUDER_ALT_FT) -> None:
        """Spawn an intruder (straight line at hdg/gs) or add a blocked zone."""
        if d.kind == "intruder":
            self.active[d.id] = Aircraft(
                callsign=d.id, x=d.x_nm, y=d.y_nm, alt=alt_ft, target_alt=alt_ft,
                hdg=(d.hdg_deg or 0.0) % 360.0, gs=d.gs_kt or 450.0, target_gs=d.gs_kt or 450.0,
                target_hdg=(d.hdg_deg or 0.0) % 360.0, actype="F18", is_intruder=True,
                spawned_at=self.t,
            )
        else:
            self.zones.append(Zone(id=d.id, x_nm=d.x_nm, y_nm=d.y_nm, radius_nm=d.radius_nm, kind=d.kind))

    def get(self, callsign: str) -> Aircraft | None:
        return self.active.get(callsign)

    def done(self) -> bool:
        return not self.active and not self.pending

    def next_waypoint(self, a: Aircraft) -> Waypoint | None:
        while a.route and a.route[0] not in self.waypoints:
            a.route.pop(0)
        return self.waypoints[a.route[0]] if a.route else None

    # -- internals ----------------------------------------------------------

    def _spawn_due(self) -> None:
        while self.pending and self.pending[0].entry_time_s <= self.t:
            f = self.pending.pop(0)
            self.active[f.callsign] = self._make_aircraft(f)

    def _make_aircraft(self, f: FlightSpec) -> Aircraft:
        route = [w for w in f.route if w in self.waypoints]
        if f.x_nm is not None and f.y_nm is not None:
            x, y = f.x_nm, f.y_nm
        elif route:
            x, y = self.waypoints[route[0]].x_nm, self.waypoints[route[0]].y_nm
            route = route[1:]
        else:
            x, y = 0.0, 0.0
        if f.hdg_deg is not None:
            hdg = f.hdg_deg % 360.0
        elif route:
            w = self.waypoints[route[0]]
            hdg = bearing_deg(x, y, w.x_nm, w.y_nm)
        else:
            hdg = 0.0
        return Aircraft(
            callsign=f.callsign, x=x, y=y, alt=f.alt_ft, target_alt=f.alt_ft, hdg=hdg,
            gs=f.gs_kt, target_gs=f.gs_kt, route=route, actype=f.actype,
            is_intruder=f.is_intruder, spawned_at=self.t,
            target_hdg=hdg if (f.is_intruder or not route) else None,
        )

    def _advance(self, a: Aircraft, dt: float) -> None:
        # Desired heading: explicit target, else bearing to the next waypoint.
        desired = a.target_hdg
        if desired is None:
            wp = self.next_waypoint(a)
            if wp is not None:
                desired = bearing_deg(a.x, a.y, wp.x_nm, wp.y_nm)
        if desired is not None:
            delta = wrap180(desired - a.hdg)
            max_turn = TURN_RATE_DEG_S * dt
            a.hdg = (a.hdg + max(-max_turn, min(max_turn, delta))) % 360.0
        # Altitude and speed.
        da = a.target_alt - a.alt
        a.alt += max(-CLIMB_FPM / 60 * dt, min(CLIMB_FPM / 60 * dt, da))
        dg = a.target_gs - a.gs
        a.gs += max(-ACCEL_KT_S * dt, min(ACCEL_KT_S * dt, dg))
        # Position.
        ux, uy = unit_vector(a.hdg)
        d = a.gs * NM_PER_KT_S * dt
        a.x += ux * d
        a.y += uy * d
        a.distance_nm += d
        a.airborne_s += dt
        # Waypoint capture.
        if a.target_hdg is None:
            wp = self.next_waypoint(a)
            if wp is not None:
                dist = dist_nm(a.x, a.y, wp.x_nm, wp.y_nm)
                # Scale with the step distance so coarse batch steps cannot fly past a fix.
                capture = max(CAPTURE_NM, d)
                abeam = max(ABEAM_NM, 2 * d)
                passed = a._last_wp_dist is not None and dist < abeam and dist > a._last_wp_dist
                if dist <= capture or passed:
                    a.route.pop(0)
                    a._last_wp_dist = None
                else:
                    a._last_wp_dist = dist

    def _should_remove(self, a: Aircraft) -> bool:
        if a.target_hdg is None and not a.route and not a.is_intruder:
            return True  # past the exit waypoint
        lim = self.sector_nm / 2 + EXIT_MARGIN_NM
        return abs(a.x) > lim or abs(a.y) > lim

    def state_of(self, callsign: str) -> AircraftState | None:
        a = self.active.get(callsign)
        return a.to_state(self.t) if a else None

    def __repr__(self) -> str:
        return f"Simulator(t={self.t:.0f}s, active={len(self.active)}, pending={len(self.pending)})"


def bearing_between(a: Aircraft, b: Aircraft) -> float:
    return bearing_deg(a.x, a.y, b.x, b.y)


def horizontal_nm(a: Aircraft, b: Aircraft) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)
