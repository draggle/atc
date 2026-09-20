"""Prioritized planning, fixed-route baseline, and replanning. docs/07-build-spec.md section 5.

Order flights (airborne first, then by entry time). For each flight build candidates
(ideal direct path, entry delays, speed and altitude changes, doglegs around the first
conflict point), sort by cost, keep the first that neither conflicts with already-planned
flights nor crosses a blocked zone. Then an improvement pass reorders the highest-cost
flight while the time budget lasts.

The planner never tests below HARD_SEP_NM / HARD_SEP_FT. `buffer_nm` only adds to it.
A SAMPLING_MARGIN_NM is added on top so the 10 s sampling cannot hide a breach.

Change strings follow a fixed grammar that planner/cards.py parses:
    direct <WPT> saves <nm> NM [from t=<s>s]
    delay entry <s> s to clear <CS>
    speed <kt> kt (<+/-pct>%) [from t=<s>s] to clear <CS>
    altitude <ft> ft (<+/-delta>) [from t=<s>s] to clear <CS>
    heading <hhh> from t=<s>s (<n> NM <left|right> dogleg, then direct <WPT>) to clear <CS>
The optional "from t=" appears when replanning an airborne flight: the change starts when the
frozen window ends, and the card's urgency is derived from it.
    emergency turn <left|right> heading <hhh> to clear <CS>
    emergency <climb|descend> <ft> ft to clear <CS>
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

import disruptions as DZ
from planner.conflicts import Grid, SEP_FT, SEP_NM, ZONE_VERT_MARGIN_FT, crosses_zone, pairwise_conflicts, zone_at, zone_mask
from planner.trajectory import DT, flyable, polyline_length, route_points, sample_path, sample_straight, samples_array, to_planned_path
from schemas import AircraftState, Disruption, FlightSpec, Plan, PlannedPath, Scenario, Waypoint, Zone
from sim.geo import bearing_deg

HARD_SEP_NM = SEP_NM
HARD_SEP_FT = SEP_FT
SAMPLING_MARGIN_NM = 1.0
INTRUDER_BASE_NM = 10.0  # the fighter's numbers. Every kind has its own: disruptions.py
INTRUDER_GROWTH_NM_PER_MIN = 1.0
INTRUDER_SEP_FT = 2000.0
INTRUDER_HORIZON_S = 1200.0
ZONE_MARGIN_NM = 3.0  # new paths keep this far outside a zone: a plane on a heading wanders a mile or two
# A heading that is already being flown is judged more gently than a new one. The detour was
# trimmed until it just cleared the margin, for a turn at the moment the planner assumed. By voice
# the turn comes a few seconds early or late, so the same heading grazes the margin by a fraction
# of a mile, and without this the controller is handed a second heading five degrees from the first.
# The zone itself is still never entered, and the leg back to the exit still keeps the full margin.
HOLD_MARGIN_NM = 1.0
ESCAPE_S_PER_S = 6.0  # cost of each second spent inside a zone that appeared on top of the flight
ESCAPE_CLEAR_NM = 6.0  # an escape leg ends this far outside the zone
ZONE_LEVEL_REACH_FT = 6000.0  # how far a flight will climb or descend to go over or under a zone
# On an assigned heading: how far along it before turning direct. Tried shortest first, so the
# "back on course" instruction comes as early as it is safe, not when the aircraft is abeam.
HOLD_HEADING_NM = (0, 2, 4, 6, 8, 10, 13, 16, 20, 25, 30, 36, 44, 54, 66, 80, 100, 125, 150)
EMERGENCY_LOOKAHEAD_S = 120.0
EMERGENCY_LEG_S = 90.0
DEVIATION_NM = 2.0  # off the previous path by more than this => replan from the real position
MIN_ALT_FT, MAX_ALT_FT = 20000.0, 41000.0

# Cost weights, all in "seconds equivalent".
ALT_PENALTY_S_PER_1000FT = 45.0
SPEED_PENALTY_S_PER_PCT = 6.0
PREV_DEVIATION_S_PER_NM = 4.0
DOGLEG_FIXED_S = 20.0


@dataclass
class _Flight:
    callsign: str
    start: tuple[float, float]
    start_t: float
    alt0: float
    pref_alt: float
    gs: float
    exit: tuple[float, float]
    exit_name: str
    airborne: bool
    is_intruder: bool = False
    hdg: float | None = None
    threat: str | None = None
    prefix: np.ndarray = field(default_factory=lambda: np.zeros((0, 4)))
    prev: np.ndarray | None = None
    prev_changes: list[str] = field(default_factory=list)
    prev_via: list[tuple[float, float]] = field(default_factory=list)
    route_pts: list[tuple[float, float]] = field(default_factory=list)
    deviated: bool = False
    keep_ok: bool = False  # its current path may compete with the new candidates (see _plan_one)
    now_t: float = 0.0
    # Flying a heading a controller assigned, not its route. It stays on that heading until it is
    # told otherwise, so its plan is "this heading for the shortest safe distance, then direct".
    assigned_hdg: float | None = None


@dataclass
class _Result:
    samples: np.ndarray
    cost: float
    changes: list[str]
    conflicts_with: list[str]
    via: list[tuple[float, float]] = field(default_factory=list)
    runner_up_cost: float | None = None  # the next candidate that also cleared, for the card's confidence


# --------------------------------------------------------------------------- helpers

def _wp_dict(waypoints) -> dict[str, Waypoint]:
    if isinstance(waypoints, dict):
        return waypoints
    return {w.name: w for w in waypoints}


def _nearest_wp(x: float, y: float, wps: dict[str, Waypoint]) -> str:
    return min(wps.values(), key=lambda w: math.hypot(w.x_nm - x, w.y_nm - y)).name if wps else "EXIT"


def _flights_of(src: Scenario | list[FlightSpec]) -> list[FlightSpec]:
    return list(src.flights) if isinstance(src, Scenario) else list(src)


def predict_intruder(x: float, y: float, hdg: float, gs: float, alt: float, now_t: float,
                     horizon_s: float = INTRUDER_HORIZON_S) -> np.ndarray:
    """Straight-line prediction samples (t, x, y, alt) for an uncooperative aircraft."""
    return sample_straight(x, y, hdg, gs, now_t, alt, horizon_s)


def intruder_radius(samples: np.ndarray, now_t: float, threat: str | None = None) -> np.ndarray:
    """Required horizontal separation from an intruder, growing with look-ahead time."""
    prof = DZ.profile(threat)
    return np.minimum(prof.base_nm + prof.growth_nm_per_min * np.maximum(samples[:, 0] - now_t, 0) / 60.0,
                      prof.max_nm)


def _build_flights(specs: list[FlightSpec], wps: dict[str, Waypoint], states: list[AircraftState] | None,
                   now_t: float, previous: Plan | None, frozen_s: float,
                   as_flown: bool = False) -> list[_Flight]:
    """`as_flown` (voice on): the frozen stretch of every airborne path is what the aircraft is
    cleared to do right now, its assigned heading or its own route, not what the previous plan
    says. By voice the two differ whenever a card has not been said yet, or was said early or
    late, and a plan that starts from a place the aircraft will never be is wrong all the way."""
    st = {s.callsign: s for s in (states or [])}
    prev = {p.callsign: p for p in previous.paths} if previous else {}
    out: list[_Flight] = []
    for f in specs:
        pts = route_points(f.route, wps)
        s = st.get(f.callsign)
        if s is None and states is not None and f.entry_time_s <= now_t:
            continue  # already left the sector
        if f.is_intruder or (s is not None and s.is_intruder):
            # Also a scenario flight that has declared an emergency: it no longer takes instructions.
            if s is None and f.entry_time_s > now_t:
                continue
            x, y = (s.x_nm, s.y_nm) if s else (f.x_nm or 0.0, f.y_nm or 0.0)
            hdg = (s.target_hdg_deg if s.target_hdg_deg is not None else s.hdg_deg) if s else (f.hdg_deg or 0.0)
            gs = s.gs_kt if s else f.gs_kt
            alt = s.alt_ft if s else f.alt_ft
            out.append(_Flight(f.callsign, (x, y), max(now_t, f.entry_time_s), alt,
                               s.target_alt_ft if s else alt, gs, (x, y), "", True, True, hdg,
                               threat=(s.threat if s else None) or f.threat))
            continue
        if not pts and not (f.x_nm is not None and f.y_nm is not None):
            continue
        exit_pt = pts[-1] if pts else (f.x_nm, f.y_nm)
        exit_name = f.route[-1] if f.route else _nearest_wp(*exit_pt, wps)
        if s is not None:
            start, start_t, alt0, airborne = (s.x_nm, s.y_nm), now_t, s.alt_ft, True
            gs = s.gs_kt
            hdg = s.hdg_deg
        else:
            start = pts[0] if f.x_nm is None else (f.x_nm, f.y_nm)
            start_t, alt0, gs, hdg = f.entry_time_s, f.alt_ft, f.gs_kt, f.hdg_deg
            airborne = f.entry_time_s <= now_t
        # Airborne: the preferred level is whatever the flight is currently cleared to, so the
        # ideal candidate holds it and level changes are expressed relative to it.
        pref_alt = s.target_alt_ft if s is not None else f.alt_ft
        fl = _Flight(f.callsign, start, start_t, alt0, pref_alt, gs, exit_pt, exit_name, airborne,
                     False, hdg, route_pts=pts)
        if s is not None and s.target_hdg_deg is not None:
            fl.assigned_hdg = float(s.target_hdg_deg)
        p = prev.get(f.callsign)
        if p is not None:
            arr = samples_array(p)
            fl.prev = arr
            fl.prev_changes = list(p.changes)
            fl.prev_via = list(p.via)
            if airborne and arr.shape[0] and s is not None:
                k = int(np.argmin(np.abs(arr[:, 0] - now_t)))
                off = math.hypot(arr[k, 1] - s.x_nm, arr[k, 2] - s.y_nm) if abs(arr[k, 0] - now_t) <= DT else float("inf")
                fl.deviated = off > DEVIATION_NM
            if airborne and frozen_s > 0 and arr.shape[0] and not fl.deviated and fl.assigned_hdg is None and not as_flown:
                keep = (arr[:, 0] >= now_t - 1e-6) & (arr[:, 0] <= now_t + frozen_s + 1e-6)
                fl.prefix = arr[keep]
        if s is not None and fl.assigned_hdg is not None:
            # What it will really do for the next while: finish rolling onto the assigned heading and
            # hold it. At least one sample, so the new leg always starts from the aircraft itself.
            ahead = max(frozen_s, DT) * s.gs_kt / 3600.0 + 1.0
            r = math.radians(fl.assigned_hdg)
            pts_h = flyable([(s.x_nm, s.y_nm), (s.x_nm + math.sin(r) * ahead, s.y_nm + math.cos(r) * ahead)], s.hdg_deg, s.gs_kt)
            pre = sample_path(pts_h, s.gs_kt, now_t, s.alt_ft, s.target_alt_ft)
            fl.prefix = pre[pre[:, 0] <= now_t + max(frozen_s, DT) + 1e-6]
        elif s is not None and as_flown and frozen_s > 0 and s.route:
            own = [(s.x_nm, s.y_nm)] + route_points(list(s.route), wps)
            if len(own) >= 2:
                pre = sample_path(flyable(own, s.hdg_deg, s.gs_kt), s.gs_kt, now_t, s.alt_ft, s.target_alt_ft)
                fl.prefix = pre[pre[:, 0] <= now_t + frozen_s + 1e-6]
        if s is not None and fl.assigned_hdg is None and frozen_s > 0 and fl.prefix.shape[0] == 0:
            # Off plan (or no plan): freeze a straight projection of what the plane is doing now.
            fl.prefix = sample_straight(s.x_nm, s.y_nm, s.hdg_deg, s.gs_kt, now_t, s.alt_ft, frozen_s)
            if s.target_alt_ft != s.alt_ft and fl.prefix.shape[0]:
                step = 1500.0 / 60.0 * (fl.prefix[:, 0] - now_t)
                fl.prefix[:, 3] = s.alt_ft + np.sign(s.target_alt_ft - s.alt_ft) * np.minimum(step, abs(s.target_alt_ft - s.alt_ft))
        out.append(fl)
    return out


def _candidate_samples(fl: _Flight, points: list[tuple[float, float]], gs: float, alt_target: float,
                       t0: float) -> np.ndarray:
    """Frozen prefix followed by the new leg. `points[0]` must be the leg start."""
    if fl.prefix.shape[0]:
        last = fl.prefix[-1]
        hdg0 = fl.hdg
        if fl.prefix.shape[0] >= 2:  # the heading it will have when the frozen stretch ends
            dx, dy = last[1] - fl.prefix[-2, 1], last[2] - fl.prefix[-2, 2]
            if dx or dy:
                hdg0 = math.degrees(math.atan2(dx, dy)) % 360.0
        pts = flyable([(float(last[1]), float(last[2]))] + points[1:], hdg0, gs)
        leg = sample_path(pts, gs, float(last[0]), float(last[3]), alt_target)
        leg = leg[leg[:, 0] > last[0] + 1e-6] if leg.shape[0] else leg
        return np.vstack([fl.prefix, leg]) if leg.shape[0] else fl.prefix
    return sample_path(flyable(points, fl.hdg if fl.airborne else None, gs), gs, t0, fl.alt0, alt_target)


def _leg_start(fl: _Flight) -> tuple[tuple[float, float], float, float]:
    """(point, t, alt) where new planning begins: end of the frozen prefix or the entry."""
    if fl.prefix.shape[0]:
        last = fl.prefix[-1]
        return (float(last[1]), float(last[2])), float(last[0]), float(last[3])
    return fl.start, fl.start_t, fl.alt0


def _prev_deviation(fl: _Flight, samples: np.ndarray) -> float:
    if fl.prev is None or samples.shape[0] == 0:
        return 0.0
    a = fl.prev
    ia = np.rint(a[:, 0] / DT).astype(int)
    ib = np.rint(samples[:, 0] / DT).astype(int)
    common, xa, xb = np.intersect1d(ia, ib, return_indices=True)
    if common.size == 0:
        return 0.0
    d = np.hypot(a[xa, 1] - samples[xb, 1], a[xa, 2] - samples[xb, 2])
    return float(d.mean()) * PREV_DEVIATION_S_PER_NM


@dataclass
class _Cand:
    samples: np.ndarray
    cost: float
    changes: list[str]
    tag: str
    dog: tuple[float, float] | None = None  # the turn point of a dogleg, so it can be tightened
    who: str = "traffic"


def _candidates(fl: _Flight, grid: Grid, zones: list[Zone], with_direct: bool,
                blocker: str | None = None) -> list[_Cand]:
    """First-tier candidates. `blocker` names who the ideal conflicted with, for the change text."""
    start, t0, alt0 = _leg_start(fl)
    ideal_pts = [start, fl.exit]
    direct_len = polyline_length(ideal_pts)
    ideal = _candidate_samples(fl, ideal_pts, fl.gs, fl.pref_alt, t0)
    ideal_time = direct_len / fl.gs * 3600.0
    base_changes: list[str] = []
    if with_direct:
        fixed = polyline_length(fl.route_pts) if fl.route_pts else direct_len
        saved = fixed - direct_len if not fl.airborne else _remaining_fixed(fl) - direct_len
        if saved > 0.5:
            base_changes.append(f"direct {fl.exit_name} saves {saved:.1f} NM")
    crossed = [z for z in zones if crosses_zone(ideal, [z], ZONE_MARGIN_NM)]
    who = blocker or (crossed[0].id if crossed else "traffic")
    at = f" from t={t0:.0f}s" if fl.prefix.shape[0] else ""
    if at and base_changes:
        base_changes[0] += at
    cands = [_Cand(ideal, _prev_deviation(fl, ideal), list(base_changes), "ideal")]

    if not fl.airborne:
        for d in (60, 120, 180):
            s = _candidate_samples(fl, ideal_pts, fl.gs, fl.pref_alt, t0 + d)
            cands.append(_Cand(s, d + _prev_deviation(fl, s), base_changes + [f"delay entry {d} s to clear {who}"], "delay"))
    for pct in (-5, 5, -10, 10):
        gs = round(fl.gs * (1 + pct / 100.0) / 5) * 5
        s = _candidate_samples(fl, ideal_pts, gs, fl.pref_alt, t0)
        added_t = max(0.0, direct_len / gs * 3600.0 - ideal_time)
        cost = added_t + abs(pct) * SPEED_PENALTY_S_PER_PCT + _prev_deviation(fl, s)
        cands.append(_Cand(s, cost, base_changes + [f"speed {gs:.0f} kt ({pct:+d}%){at} to clear {who}"], "speed"))
    for dalt in (1000, -1000, 2000, -2000):
        alt = fl.pref_alt + dalt
        if not (MIN_ALT_FT <= alt <= MAX_ALT_FT):
            continue
        s = _candidate_samples(fl, ideal_pts, fl.gs, alt, t0)
        cost = abs(dalt) / 1000.0 * ALT_PENALTY_S_PER_1000FT + _prev_deviation(fl, s)
        cands.append(_Cand(s, cost, base_changes + [f"altitude {alt:.0f} ft ({dalt:+d}){at} to clear {who}"], "altitude"))

    # Over or under a zone that only blocks some levels.
    for z in crossed:
        over = math.ceil((z.ceiling_ft + ZONE_VERT_MARGIN_FT) / 1000.0) * 1000.0
        under = math.floor((z.floor_ft - ZONE_VERT_MARGIN_FT) / 1000.0) * 1000.0
        for alt in (over, under):
            dalt = alt - fl.pref_alt
            if not (MIN_ALT_FT <= alt <= MAX_ALT_FT) or abs(dalt) > ZONE_LEVEL_REACH_FT or abs(dalt) <= 2000:
                continue  # within 2,000 ft is already tried above
            s = _candidate_samples(fl, ideal_pts, fl.gs, alt, t0)
            cost = abs(dalt) / 1000.0 * ALT_PENALTY_S_PER_1000FT + _prev_deviation(fl, s)
            cands.append(_Cand(s, cost, base_changes + [f"altitude {alt:.0f} ft ({dalt:+.0f}){at} to clear {z.id}"], "altitude"))

    # Doglegs around the first conflict point (traffic) and around crossed zones.
    pivots: list[tuple[float, float, float, str]] = []  # (x, y, extra offset, who)
    wide = False  # the blocker is a threat with a wide buffer: ordinary doglegs will not get round it
    fc = grid.first_conflict(ideal)
    if fc is not None:
        j = fc[1]
        pivots.append((float(ideal[j, 1]), float(ideal[j, 2]), 0.0, who))
        wide = float(np.nanmax(grid.rad[grid.names.index(fc[0])])) > HARD_SEP_NM + 4.5
    ux, uy = fl.exit[0] - start[0], fl.exit[1] - start[1]
    L = math.hypot(ux, uy) or 1.0
    ux, uy = ux / L, uy / L
    nx, ny = uy, -ux  # right-hand normal
    dogs: list[tuple[tuple[float, float], str]] = []
    for z in crossed:
        # Where the zone will be when the flight gets there, not where it is now.
        zx, zy, zr = zone_at(z, t0)
        eta = t0 + math.hypot(zx - start[0], zy - start[1]) / fl.gs * 3600.0
        zx, zy, zr = zone_at(z, eta)
        px, py = _project(start, fl.exit, (zx, zy))
        pivots.append((px, py, zr, z.id))
        zx, zy, zr = zone_at(z, t0)
        dist = math.hypot(start[0] - zx, start[1] - zy)
        clear = zr + ESCAPE_CLEAR_NM
        if dist < zr + ZONE_MARGIN_NM:
            # The zone appeared on top of this flight. Offer the short ways out.
            out = math.atan2(start[0] - zx, start[1] - zy) if dist > 1e-6 else math.atan2(ux, uy)
            for turn in (0.0, 0.7, -0.7):
                a = out + turn
                dogs.append(((zx + math.sin(a) * clear, zy + math.cos(a) * clear), z.id))
        elif dist < 4 * clear:
            # Close ahead: an abeam dogleg would still cut the corner. Skim past on a tangent.
            to_centre = math.atan2(zx - start[0], zy - start[1])
            half_angle = math.asin(min(1.0, clear / dist))
            reach = math.sqrt(max(dist * dist - clear * clear, 0.0))
            for sign in (1.0, -1.0):
                a = to_centre + sign * (half_angle + 0.06)
                for run in (reach, reach + clear):
                    dogs.append(((start[0] + math.sin(a) * run, start[1] + math.cos(a) * run), z.id))
    for px, py, extra, pw in pivots:
        for off in (5, -5, 10, -10) + ((18, -18, 28, -28, 40, -40) if wide and not extra else ()):
            o = off + math.copysign(extra + ZONE_MARGIN_NM + 1.0, off) if extra else off
            dogs.append(((px + nx * o, py + ny * o), pw))
    for dog, pw in dogs:
        cands.append(_dogleg(fl, start, t0, dog, pw, base_changes, direct_len))
    cands.sort(key=lambda c: c.cost)
    return cands


def _dogleg(fl: _Flight, start: tuple[float, float], t0: float, dog: tuple[float, float], who: str,
            base_changes: list[str], direct_len: float) -> _Cand:
    """One turn point, then direct to the exit."""
    pts = [start, dog, fl.exit]
    s = _candidate_samples(fl, pts, fl.gs, fl.pref_alt, t0)
    added_d = polyline_length(pts) - direct_len
    cost = DOGLEG_FIXED_S + added_d * 3600.0 / fl.gs * 2 + _prev_deviation(fl, s)
    hdg = bearing_deg(start[0], start[1], dog[0], dog[1])
    ux, uy = fl.exit[0] - start[0], fl.exit[1] - start[1]
    L = math.hypot(ux, uy) or 1.0
    o = (dog[0] - start[0]) * (uy / L) + (dog[1] - start[1]) * (-ux / L)  # signed offset from the direct track
    side = "right" if o > 0 else "left"
    txt = f"heading {hdg:03.0f} from t={t0:.0f}s ({abs(o):.0f} NM {side} dogleg, then direct {fl.exit_name}) to clear {who}"
    return _Cand(s, cost, list(base_changes) + [txt], "dogleg", dog=dog, who=who)


def _tighten(fl: _Flight, c: _Cand, grid: Grid, zones: list[Zone]) -> _Cand:
    """The smallest detour that is still safe.

    Doglegs come from a coarse menu of offsets, so the first one that works usually goes wider
    than it has to. Pull the turn point back toward the direct track by bisection and keep the
    tightest version that still clears the traffic and the zones. Four steps: within a mile or two.
    """
    if c.dog is None:
        return c
    start, t0, _alt0 = _leg_start(fl)
    px, py = _project(start, fl.exit, c.dog)
    off = (c.dog[0] - px, c.dog[1] - py)
    if math.hypot(*off) < 3.0:
        return c
    direct_len = polyline_length([start, fl.exit])
    base = c.changes[:-1]
    _, inside_s = _zone_verdict(fl, c.samples, zones)  # never buy a shorter path with longer in a zone
    best, lo, hi = c, 0.0, 1.0
    for _ in range(4):
        mid = (lo + hi) / 2.0
        trial = _dogleg(fl, start, t0, (px + off[0] * mid, py + off[1] * mid), c.who, base, direct_len)
        ok, escape_s = _zone_verdict(fl, trial.samples, zones)
        if ok and escape_s <= inside_s and not grid.conflicts(trial.samples).any():
            trial.cost += escape_s * ESCAPE_S_PER_S
            best, hi = trial, mid
        else:
            lo = mid
    return best


def _zone_verdict(fl: _Flight, samples: np.ndarray, zones: list[Zone],
                  mask: np.ndarray | None = None) -> tuple[bool, float]:
    """(acceptable, seconds spent escaping) for a candidate against the blocked zones.

    The frozen prefix cannot be changed, so it is not held against the candidate. If the
    flight is inside a zone when its free path begins, because the zone appeared on top of
    it, the candidate may spend that first stretch getting out, at a cost, but it may never
    go back in. A flight that is not yet flying gets no such allowance.
    """
    m = zone_mask(samples, zones, ZONE_MARGIN_NM) if mask is None else mask
    if not m.any():
        return True, 0.0
    free = m[fl.prefix.shape[0]:]
    if not free.any():
        return True, 0.0
    if not fl.airborne or not free[0]:
        return False, 0.0
    outside = np.where(~free)[0]
    if outside.size == 0:
        return False, 0.0
    j = int(outside[0])
    return (not free[j:].any()), j * DT


def _second_tier(fl: _Flight, first: list[_Cand]) -> list[_Cand]:
    """Dogleg x altitude combinations, tried only when every first-tier candidate failed."""
    out = []
    doglegs = [c for c in first if c.tag == "dogleg"]
    start, t0, alt0 = _leg_start(fl)
    for c in doglegs:
        # Recover the dogleg point from the samples' turn: cheaper to re-derive from text is messy,
        # so rebuild geometry from the candidate's max lateral offset sample.
        s = c.samples
        if s.shape[0] < 3:
            continue
        k = _farthest_from_line(s, start, fl.exit)
        dog = (float(s[k, 1]), float(s[k, 2]))
        for dalt in (1000, -1000, 2000, -2000):
            alt = fl.pref_alt + dalt
            if not (MIN_ALT_FT <= alt <= MAX_ALT_FT):
                continue
            pts = [start, dog, fl.exit]
            ss = _candidate_samples(fl, pts, fl.gs, alt, t0)
            cost = c.cost + abs(dalt) / 1000.0 * ALT_PENALTY_S_PER_1000FT
            who = c.changes[-1].rsplit(" to clear ", 1)[-1] if c.changes else "traffic"
            at = f" from t={t0:.0f}s" if fl.prefix.shape[0] else ""
            out.append(_Cand(ss, cost, c.changes + [f"altitude {alt:.0f} ft ({dalt:+d}){at} to clear {who}"], "combo"))
    out.sort(key=lambda c: c.cost)
    return out


def _farthest_from_line(s: np.ndarray, a: tuple[float, float], b: tuple[float, float]) -> int:
    ux, uy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(ux, uy) or 1.0
    d = np.abs((s[:, 1] - a[0]) * uy - (s[:, 2] - a[1]) * ux) / L
    return int(np.argmax(d))


def _project(a: tuple[float, float], b: tuple[float, float], p: tuple[float, float]) -> tuple[float, float]:
    ux, uy = b[0] - a[0], b[1] - a[1]
    L2 = ux * ux + uy * uy or 1.0
    t = max(0.0, min(1.0, ((p[0] - a[0]) * ux + (p[1] - a[1]) * uy) / L2))
    return a[0] + ux * t, a[1] + uy * t


def _remaining_fixed(fl: _Flight) -> float:
    """Length of the fixed route from the flight's current position, joining at the nearest leg."""
    pts = fl.route_pts
    if len(pts) < 2:
        return math.hypot(fl.exit[0] - fl.start[0], fl.exit[1] - fl.start[1])
    if math.hypot(pts[0][0] - fl.start[0], pts[0][1] - fl.start[1]) < 0.5:
        return polyline_length(pts)
    best_i, best_d = 0, float("inf")
    for i in range(len(pts) - 1):
        px, py = _project(pts[i], pts[i + 1], fl.start)
        d = math.hypot(px - fl.start[0], py - fl.start[1])
        if d < best_d:
            best_i, best_d = i, d
    return polyline_length([fl.start] + pts[best_i + 1:])


def _hold_heading(fl: _Flight, grid: Grid, zones: list[Zone]) -> _Result | None:
    """On an assigned heading: go direct now if that is clear from here, else the shortest stretch
    of the heading after which it will be. The first gives a plan with no heading in it, which is
    what puts the "proceed direct" card up (cards.followup_cards); the second records the expected
    turn-back point in `via` for the map.

    Returns None if no distance works, which means the heading itself has become unsafe and the
    flight needs a new instruction like anyone else.
    """
    # First: is going direct safe from where it is this second? Only then is it offered, because
    # that is the one answer that stays right however long the card takes to say: further along
    # the heading the way back only gets clearer. "Safe from a point 25 s ahead" is not the same
    # thing. Told at once, the aircraft turns short of that point, clips the margin, and is given
    # a new heading, then another direct, for as long as anyone keeps reading the cards out.
    here = sample_path(flyable([fl.start, fl.exit], fl.hdg, fl.gs), fl.gs, fl.start_t, fl.alt0, fl.pref_alt)
    if (here.shape[0] >= 2 and not zone_mask(here, zones, ZONE_MARGIN_NM).any()
            and not grid.conflicts(here).any()):
        return _Result(here, 0.0, [f"direct {fl.exit_name} saves 0.0 NM from t={fl.start_t:.0f}s"], [], [])
    start, t0, _alt0 = _leg_start(fl)
    r = math.radians(fl.assigned_hdg or 0.0)
    ux, uy = math.sin(r), math.cos(r)
    direct_len = polyline_length([start, fl.exit])
    who = next((c.rsplit(" to clear ", 1)[-1] for c in reversed(fl.prev_changes) if " to clear " in c), "traffic")
    for d in HOLD_HEADING_NM:
        turn = (start[0] + ux * d, start[1] + uy * d)
        pts = [start, fl.exit] if d == 0 else [start, turn, fl.exit]
        samples = _candidate_samples(fl, pts, fl.gs, fl.pref_alt, t0)
        mask = zone_mask(samples, zones, ZONE_MARGIN_NM)
        if d > 0 and mask.any():
            # The stretch still on the heading gets the gentler margin; from where it starts to
            # turn back (it leaves the heading line) the full one applies again.
            cross = np.abs((samples[:, 1] - start[0]) * uy - (samples[:, 2] - start[1]) * ux)
            left = np.where((cross > 0.5) & (np.arange(samples.shape[0]) >= fl.prefix.shape[0]))[0]
            k = int(left[0]) if left.size else samples.shape[0]
            mask = np.concatenate([zone_mask(samples[:k], zones, HOLD_MARGIN_NM), mask[k:]])
        ok, _ = _zone_verdict(fl, samples, zones, mask)
        if not ok or grid.conflicts(samples).any():
            continue
        ex, ey = fl.exit[0] - start[0], fl.exit[1] - start[1]
        L = math.hypot(ex, ey) or 1.0
        off = (turn[0] - start[0]) * (ey / L) + (turn[1] - start[1]) * (-ex / L)
        txt = (f"heading {(fl.assigned_hdg or 0.0) % 360:03.0f} from t={t0:.0f}s ({abs(off):.0f} NM "
               f"{'right' if off > 0 else 'left'} dogleg, then direct {fl.exit_name}) to clear {who}")
        added_s = (polyline_length(pts) - direct_len) * 3600.0 / fl.gs
        return _Result(samples, max(added_s, 0.0), [txt], [], [turn])
    return None


def _plan_one(fl: _Flight, grid: Grid, zones: list[Zone], buffer_nm: float, with_direct: bool) -> _Result:
    if fl.assigned_hdg is not None and fl.airborne:
        held = _hold_heading(fl, grid, zones)
        if held is not None:
            return held
    cands = _candidates(fl, grid, zones, with_direct)
    fc = grid.first_conflict(cands[0].samples) if cands else None
    if fc is not None:
        cands = _candidates(fl, grid, zones, with_direct, blocker=fc[0])
    if fl.keep_ok and fl.prev is not None:
        # Stability: the path it is already flying goes first. It wins if it is still safe, and
        # when nothing is safe (a storm on its exit) it wins ties, so a flight that cannot be
        # helped is not sent a slightly different heading every few seconds.
        kept = fl.prev[fl.prev[:, 0] >= fl.now_t - 1e-6]
        if kept.shape[0] >= 2:
            cands.insert(0, _Cand(kept, -1.0, list(fl.prev_changes), "keep",
                                  dog=fl.prev_via[0] if fl.prev_via else None))
    tiers = [cands]
    least: tuple[int, _Cand, list[str]] | None = None
    through: tuple[int, _Cand] | None = None  # nothing avoids the zone: the least time inside it
    for tier_i in range(2):
        ranked = []
        for c in tiers[tier_i]:
            ok, escape_s = _zone_verdict(fl, c.samples, zones)
            if ok:
                c.cost += escape_s * ESCAPE_S_PER_S
                ranked.append(c)
            elif not grid.conflicts(c.samples).any():
                inside = int(zone_mask(c.samples, zones, ZONE_MARGIN_NM)[fl.prefix.shape[0]:].sum())
                if through is None or inside < through[0]:
                    through = (inside, c)
        ranked.sort(key=lambda c: c.cost)
        for ci, c in enumerate(ranked):
            m = grid.conflicts(c.samples)
            if not m.any():
                runner_up = next((max(o.cost, 0.0) for o in ranked[ci + 1:]
                                  if not grid.conflicts(o.samples).any()), None)
                if c.tag != "keep":
                    c = _tighten(fl, c, grid, zones)
                return _Result(c.samples, max(c.cost, 0.0), [x for x in c.changes if not x.startswith("unresolved")],
                               [], [c.dog] if c.dog else [], runner_up_cost=runner_up)
            n = int(m.sum())
            if least is None or n < least[0]:
                least = (n, c, [grid.names[i] for i in np.where(m.any(axis=1))[0]])
        if tier_i == 0:
            tiers.append(_second_tier(fl, cands))
    if least is None:  # nothing stays out of the zone: take the shortest way through, and say so
        c = through[1] if through is not None else cands[0]
        changes = [x for x in c.changes if not x.startswith("unresolved")] + ["unresolved: crosses blocked zone"]
        return _Result(c.samples, max(c.cost, 0.0), changes, [], [c.dog] if c.dog else [])
    n, c, who = least
    return _Result(c.samples, c.cost + 1e6, c.changes + [f"unresolved conflict with {', '.join(who)}"], who)


def _add_to_grid(grid: Grid, fl: _Flight, samples: np.ndarray, buffer_nm: float, now_t: float) -> None:
    if fl.is_intruder:
        grid.add(fl.callsign, samples, intruder_radius(samples, now_t, fl.threat), DZ.profile(fl.threat).vert_ft)
    else:
        grid.add(fl.callsign, samples, HARD_SEP_NM + max(0.0, buffer_nm) + SAMPLING_MARGIN_NM, HARD_SEP_FT)


def _sequential(order: list[_Flight], grid: Grid, zones: list[Zone], buffer_nm: float, now_t: float,
                with_direct: bool) -> dict[str, _Result]:
    results: dict[str, _Result] = {}
    for fl in order:
        r = _plan_one(fl, grid, zones, buffer_nm, with_direct)
        results[fl.callsign] = r
        _add_to_grid(grid, fl, r.samples, buffer_nm, now_t)
    return results


def _intruder_grid(flights: list[_Flight], now_t: float) -> Grid:
    grid = Grid()
    for fl in flights:
        if fl.is_intruder:
            s = predict_intruder(fl.start[0], fl.start[1], fl.hdg or 0.0, fl.gs, fl.alt0, fl.start_t)
            if s.shape[0] and fl.pref_alt != fl.alt0:  # an emergency descent
                moved = DZ.profile(fl.threat).climb_fpm / 60.0 * (s[:, 0] - fl.start_t)
                s[:, 3] = fl.alt0 + np.sign(fl.pref_alt - fl.alt0) * np.minimum(moved, abs(fl.pref_alt - fl.alt0))
            _add_to_grid(grid, fl, s, 0.0, now_t)
    return grid


def _finish(results: dict[str, _Result], flights: list[_Flight], buffer_nm: float,
            base_dist: float, base_time: float, trigger: str) -> Plan:
    paths: list[PlannedPath] = []
    intruder_conf = 0
    for fl in flights:
        if fl.is_intruder or fl.callsign not in results:
            continue
        r = results[fl.callsign]
        cost = r.cost if r.cost < 1e6 else r.cost - 1e6
        path = to_planned_path(fl.callsign, r.samples, cost, r.changes, r.via)
        if "runner_up_cost" in PlannedPath.model_fields:  # lands with the schema change (TRD 07)
            path.runner_up_cost = r.runner_up_cost
        paths.append(path)
        intruder_conf += sum(1 for w in r.conflicts_with if any(f.callsign == w and f.is_intruder for f in flights))
    conflicts = len(pairwise_conflicts(paths, HARD_SEP_NM + max(0.0, buffer_nm), HARD_SEP_FT)) + intruder_conf
    return Plan(
        paths=paths, total_distance_nm=sum(p.distance_nm for p in paths),
        total_time_s=sum(p.time_s for p in paths), baseline_distance_nm=base_dist,
        baseline_time_s=base_time, conflicts=conflicts, trigger=trigger,
    )


# --------------------------------------------------------------------------- public API

def baseline(scenario: Scenario, buffer_nm: float | None = None) -> Plan:
    """Same flights on their fixed waypoint routes, first come first served, no changes.

    `conflicts` counts pairs that breach 5 NM + buffer / 1,000 ft. Reported honestly.
    """
    wps = _wp_dict(scenario.waypoints)
    buf = scenario.separation_buffer_nm if buffer_nm is None else buffer_nm
    paths = []
    for f in scenario.flights:
        if f.is_intruder:
            continue
        pts = flyable(route_points(f.route, wps), None, f.gs_kt)  # corners cut the way the aircraft cut them
        s = sample_path(pts, f.gs_kt, f.entry_time_s, f.alt_ft, None)
        paths.append(to_planned_path(f.callsign, s, 0.0, []))
    dist = sum(p.distance_nm for p in paths)
    dur = sum(p.time_s for p in paths)
    return Plan(paths=paths, total_distance_nm=dist, total_time_s=dur, baseline_distance_nm=dist,
                baseline_time_s=dur, conflicts=len(pairwise_conflicts(paths, HARD_SEP_NM + buf, HARD_SEP_FT)),
                trigger="baseline")


def plan(scenario_or_flights: Scenario | list[FlightSpec], waypoints, zones: list[Zone],
         buffer_nm: float, previous: Plan | None = None, frozen_s: float = 0.0,
         time_budget_s: float = 1.5, *, now_t: float = 0.0, states: list[AircraftState] | None = None,
         trigger: str = "initial") -> Plan:
    """Prioritized plan for every flight. See module docstring.

    `states` (current radar picture) makes airborne flights start from where they are.
    `previous` adds a deviation penalty and, with `frozen_s`, freezes the near-term path.
    """
    t_start = time.perf_counter()
    wps = _wp_dict(waypoints)
    specs = _flights_of(scenario_or_flights)
    zones = list(zones or [])
    flights = _build_flights(specs, wps, states, now_t, previous, frozen_s)
    regular = [f for f in flights if not f.is_intruder]
    order = sorted(regular, key=lambda f: (not f.airborne, f.start_t))

    def run(seq: list[_Flight]) -> tuple[dict[str, _Result], float]:
        grid = _intruder_grid(flights, now_t)
        res = _sequential(seq, grid, zones, buffer_nm, now_t, True)
        return res, sum(r.cost for r in res.values())

    best, best_cost = run(order)
    tried: set[str] = set()
    while time.perf_counter() - t_start < time_budget_s and len(tried) < len(order):
        worst = max((f for f in order if f.callsign not in tried), key=lambda f: best[f.callsign].cost, default=None)
        if worst is None:
            break
        tried.add(worst.callsign)
        if best[worst.callsign].cost <= 0:
            break
        cand_order = [worst] + [f for f in order if f is not worst]
        res, cost = run(cand_order)
        if cost < best_cost - 1e-6:
            best, best_cost, order = res, cost, cand_order

    if previous is not None:
        base_d, base_t = previous.baseline_distance_nm, previous.baseline_time_s
    elif isinstance(scenario_or_flights, Scenario):
        b = baseline(scenario_or_flights, buffer_nm)
        base_d, base_t = b.total_distance_nm, b.total_time_s
    else:
        base_d = sum(polyline_length(f.route_pts) for f in regular)
        base_t = sum(polyline_length(f.route_pts) / f.gs * 3600 for f in regular)
    return _finish(best, flights, buffer_nm, base_d, base_t, trigger)


def _specs_from_plan(previous: Plan, states: list[AircraftState], wps: dict[str, Waypoint]) -> list[FlightSpec]:
    """Reconstruct flight specs when the caller has no scenario: exit = last sample, alt = final alt."""
    st = {s.callsign: s for s in states}
    specs = []
    for p in previous.paths:
        a = samples_array(p)
        if a.shape[0] < 2:
            continue
        gs = float(np.hypot(np.diff(a[:, 1]), np.diff(a[:, 2])).mean() / DT * 3600)
        exit_name = _nearest_wp(a[-1, 1], a[-1, 2], wps)
        s = st.get(p.callsign)
        specs.append(FlightSpec(callsign=p.callsign, entry_time_s=float(a[0, 0]), route=[exit_name],
                                alt_ft=float(a[-1, 3]), gs_kt=gs, x_nm=float(a[0, 1]), y_nm=float(a[0, 2]),
                                actype=s.actype if s else "A320"))
    for s in states:
        if s.is_intruder:
            specs.append(FlightSpec(callsign=s.callsign, route=[], entry_time_s=s.t, alt_ft=s.alt_ft, gs_kt=s.gs_kt,
                                    is_intruder=True, threat=s.threat, x_nm=s.x_nm, y_nm=s.y_nm,
                                    hdg_deg=s.hdg_deg, actype=s.actype))
    return specs


def _emergency(fl: _Flight, threats: Grid, now_t: float) -> _Result | None:
    """If a LoS at the hard floor is predicted within EMERGENCY_LOOKAHEAD_S, pick an immediate escape."""
    if fl.prev is None or fl.hdg is None:
        return None
    near = fl.prev[(fl.prev[:, 0] >= now_t) & (fl.prev[:, 0] <= now_t + EMERGENCY_LOOKAHEAD_S)]
    fc = threats.first_conflict(near)
    if fc is None:
        return None
    who = fc[0]
    leg_nm = fl.gs / 3600.0 * EMERGENCY_LEG_S
    options: list[tuple[float, np.ndarray, str]] = []
    for side, sign in (("left", -1), ("right", 1)):
        hdg = (fl.hdg + sign * 30) % 360
        r = math.radians(hdg)
        p1 = (fl.start[0] + math.sin(r) * leg_nm, fl.start[1] + math.cos(r) * leg_nm)
        s = sample_path([fl.start, p1, fl.exit], fl.gs, now_t, fl.alt0, fl.pref_alt)
        options.append((threats.min_distance(s), s, f"emergency turn {side} heading {hdg:03.0f} to clear {who}"))
    for dalt in (1000, -1000):
        alt = fl.alt0 + dalt
        if MIN_ALT_FT <= alt <= MAX_ALT_FT:
            s = sample_path([fl.start, fl.exit], fl.gs, now_t, fl.alt0, alt)
            verb = "climb" if dalt > 0 else "descend"
            options.append((threats.min_distance(s), s, f"emergency {verb} {alt:.0f} ft to clear {who}"))
    d, s, txt = max(options, key=lambda o: o[0])
    return _Result(s, 0.0, [txt], [])


def replan(previous_plan: Plan, states: list[AircraftState], waypoints, zones: list[Zone], buffer: float,
           disruption: Disruption | None = None, frozen_s: float = 60.0, *,
           flights: list[FlightSpec] | None = None, now_t: float | None = None,
           time_budget_s: float = 1.0, release: set[str] | None = None,
           repin: set[str] | None = None, unsaid: set[str] | None = None,
           as_flown: bool = False) -> Plan:
    """Repair the previous plan: only conflicting flights move, widened to neighbours if needed.

    `release` names disruptions that have ended. Flights that were moved to clear them are
    planned again, so they go back to the better route instead of flying around nothing.

    `unsaid` names flights whose heading card is still waiting to be said (voice on). A heading is
    only right for the place it was worked out for, so these are planned again from where they
    really are, every time, and the card on the screen is always one that can be said now.
    A flight holding an assigned heading is looked at again every time too: the shortest safe
    stretch of that heading gets shorter as the storm moves, and the turn back should not wait.
    `as_flown` is voice on: see _build_flights.

    Intruders (from `states` or `disruption`) are predicted straight-line with a buffer that
    starts at 10 NM and grows 1 NM per minute. Storm/closed disruptions become zones.
    The first `frozen_s` of every airborne path is kept as flown. Emergency layer: a LoS
    at the hard floor predicted within 120 s gets an immediate 30-degree turn or 1,000 ft
    level change, skipping optimization.
    """
    t_start = time.perf_counter()
    wps = _wp_dict(waypoints)
    now = now_t if now_t is not None else (max((s.t for s in states), default=0.0))
    zones = list(zones or [])
    specs = list(flights) if flights is not None else _specs_from_plan(previous_plan, states, wps)
    trigger = "replan"
    # Trigger label: disruption kind, else "deviation" if someone is off plan, else "replan".
    if disruption is not None:
        trigger = disruption.kind
        if DZ.profile(disruption.kind).shape == "point":
            if not any(s.callsign == disruption.id for s in specs):
                specs.append(FlightSpec(callsign=disruption.id, route=[], entry_time_s=now, is_intruder=True,
                                        threat=DZ.profile(disruption.kind).kind,
                                        x_nm=disruption.x_nm, y_nm=disruption.y_nm, hdg_deg=disruption.hdg_deg or 0.0,
                                        gs_kt=disruption.gs_kt or 450.0, alt_ft=_intruder_alt(disruption, states)))
        elif not any(z.id == disruption.id for z in zones):
            zones.append(Zone(id=disruption.id, x_nm=disruption.x_nm, y_nm=disruption.y_nm,
                              radius_nm=disruption.radius_nm, kind=DZ.profile(disruption.kind).kind,  # type: ignore[arg-type]
                              floor_ft=disruption.floor_ft, ceiling_ft=disruption.ceiling_ft,
                              hdg_deg=disruption.hdg_deg or 0.0, gs_kt=disruption.gs_kt or 0.0,
                              swell_nm_per_min=disruption.swell_nm_per_min, max_radius_nm=disruption.max_radius_nm,
                              t0=now, expires_t=disruption.expires_t))
    # Every intruder on radar is a threat on every replan, not only the one that triggered this
    # call. Without this, a periodic repair forgets the fighter and sends traffic back at it.
    known = {f.callsign for f in specs}
    for st in states:
        if st.is_intruder and st.callsign not in known:
            specs.append(FlightSpec(callsign=st.callsign, route=[], entry_time_s=now, is_intruder=True,
                                    threat=st.threat, x_nm=st.x_nm, y_nm=st.y_nm, hdg_deg=st.hdg_deg,
                                    gs_kt=st.gs_kt, alt_ft=st.alt_ft, actype=st.actype))
    all_fl = _build_flights(specs, wps, states, now, previous_plan, frozen_s, as_flown)
    regular = [f for f in all_fl if not f.is_intruder]
    if disruption is None and any(f.deviated for f in regular):
        trigger = "deviation"
    threats = _intruder_grid(all_fl, now)

    # Emergency layer first.
    fixed: dict[str, _Result] = {}
    for fl in regular:
        if fl.airborne:
            r = _emergency(fl, threats, now)
            if r is not None:
                fixed[fl.callsign] = r

    # Hold everyone else on their previous path; find who now conflicts.
    def prev_from_now(fl: _Flight) -> np.ndarray:
        if fl.prev is None:
            return np.zeros((0, 4))
        return fl.prev[fl.prev[:, 0] >= now - 1e-6]

    held = [fl for fl in regular if fl.callsign not in fixed]
    grid = Grid()
    for name, r in fixed.items():
        fl = next(f for f in all_fl if f.callsign == name)
        _add_to_grid(grid, fl, r.samples, buffer, now)
    for i in range(len(threats.names)):
        grid.names.append(threats.names[i])
        grid.X = np.vstack([grid.X, threats.X[i]]); grid.Y = np.vstack([grid.Y, threats.Y[i]])
        grid.A = np.vstack([grid.A, threats.A[i]]); grid.rad = np.vstack([grid.rad, threats.rad[i]])
        grid.vert = np.vstack([grid.vert, threats.vert[i:i + 1]])
    moving: list[_Flight] = []
    ended = tuple(f" to clear {name}" for name in (release or ()))
    for fl in held:
        s = prev_from_now(fl)
        freed = bool(ended) and any(c.endswith(ended) for c in fl.prev_changes)
        if repin and fl.callsign in repin:
            freed = True  # it has just started or stopped flying a heading: plan it from where it is
        if unsaid and fl.callsign in unsaid and fl.airborne:
            freed = True  # its turn has not been said yet: the heading has to be worked out again from here
        recheck = fl.assigned_hdg is not None and fl.airborne  # is an earlier turn back safe by now?
        fl.now_t = now
        fl.keep_ok = not freed and not fl.deviated and fl.airborne
        if (freed or fl.deviated or recheck or s.shape[0] == 0 or grid.conflicting_names(s)
                or crosses_zone(s, zones, SAMPLING_MARGIN_NM)):
            moving.append(fl)
        else:
            _add_to_grid(grid, fl, s, buffer, now)
    # Pairwise among held flights (prev plan may already be stale).
    held_paths = [to_planned_path(fl.callsign, prev_from_now(fl)) for fl in held if fl not in moving]
    for a, b, _t in pairwise_conflicts(held_paths, HARD_SEP_NM + max(0.0, buffer), HARD_SEP_FT):
        for name in (a, b):
            fl = next(f for f in held if f.callsign == name)
            if fl not in moving:
                moving.append(fl)
                grid.remove(name)

    results: dict[str, _Result] = dict(fixed)
    for fl in held:
        if fl not in moving:
            results[fl.callsign] = _Result(prev_from_now(fl), 0.0, list(fl.prev_changes), [], list(fl.prev_via))

    for _round in range(4):
        order = sorted(moving, key=lambda f: (not f.airborne, f.start_t))
        g = _clone(grid)
        res = _sequential(order, g, zones, buffer, now, True)
        bad = [cs for cs, r in res.items() if r.conflicts_with]
        results.update(res)
        if not bad or time.perf_counter() - t_start > time_budget_s:
            break
        neighbours = {w for cs in bad for w in res[cs].conflicts_with}
        widened = [fl for fl in held if fl.callsign in neighbours and fl not in moving]
        if not widened:
            widened = [fl for fl in held if fl not in moving]  # last resort: replan everyone
        if not widened:
            break
        for fl in widened:
            grid.remove(fl.callsign)
            moving.append(fl)
    return _finish(results, all_fl, buffer, previous_plan.baseline_distance_nm, previous_plan.baseline_time_s, trigger)


def _clone(g: Grid) -> Grid:
    return Grid(list(g.names), g.X.copy(), g.Y.copy(), g.A.copy(), g.rad.copy(), g.vert.copy())


def _intruder_alt(d: Disruption, states: list[AircraftState]) -> float:
    for s in states:
        if s.callsign == d.id:
            return s.alt_ft
    return d.alt_ft if d.alt_ft is not None else 30000.0
