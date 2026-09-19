"""Sample a path into (t, x, y, alt) rows every DT seconds on a shared time grid.

All trajectories share the grid t = k * DT so two flights can be compared column by column.
"""
from __future__ import annotations

import math

import numpy as np

from schemas import PlannedPath, Waypoint

DT = 10.0  # seconds between samples
CLIMB_FPM = 1500.0
T_MAX_S = 3 * 3600.0  # nothing is planned beyond three hours


def grid_index(t: float) -> int:
    return int(math.ceil(t / DT - 1e-9))


def polyline_length(points: list[tuple[float, float]]) -> float:
    return float(sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:])))


def sample_path(points: list[tuple[float, float]], gs_kt: float, t0: float, alt0: float,
                alt_target: float | None = None, climb_fpm: float = CLIMB_FPM,
                dt: float = DT) -> np.ndarray:
    """Fly `points` in order at constant `gs_kt`, starting at time `t0`.

    Altitude moves linearly from alt0 toward alt_target at climb_fpm. Returns an (N, 4)
    array of (t, x, y, alt) with t on the shared grid. Empty (0, 4) if the path has no length.
    """
    if len(points) < 2 or gs_kt <= 0:
        return np.zeros((0, 4))
    pts = np.asarray(points, dtype=float)
    seg = np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1]))
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(cum[-1])
    if total <= 0:
        return np.zeros((0, 4))
    v = gs_kt / 3600.0  # NM/s
    t_first = grid_index(t0) * dt
    t_last = min(t0 + total / v, T_MAX_S)
    ts = np.arange(t_first, t_last + 1e-9, dt)
    if ts.size == 0:
        return np.zeros((0, 4))
    d = (ts - t0) * v
    x = np.interp(d, cum, pts[:, 0])
    y = np.interp(d, cum, pts[:, 1])
    if alt_target is None:
        alt = np.full_like(ts, alt0)
    else:
        step = climb_fpm / 60.0 * (ts - t0)
        alt = alt0 + np.sign(alt_target - alt0) * np.minimum(step, abs(alt_target - alt0))
    return np.column_stack([ts, x, y, alt])


def sample_straight(x: float, y: float, hdg_deg: float, gs_kt: float, t0: float, alt: float,
                    horizon_s: float, dt: float = DT) -> np.ndarray:
    """Straight-line prediction from a position and heading (used for intruders)."""
    r = math.radians(hdg_deg)
    L = gs_kt / 3600.0 * horizon_s
    end = (x + math.sin(r) * L, y + math.cos(r) * L)
    return sample_path([(x, y), end], gs_kt, t0, alt, None, dt=dt)


TURN_RATE_DEG_S = 1.5  # keep in step with sim/engine.py
FLYBY_MAX_NM = 12.0


def flyable(points: list[tuple[float, float]], hdg0: float | None, gs_kt: float) -> list[tuple[float, float]]:
    """The same corners, the way an aircraft flies them.

    A planned path is a few straight legs. The simulator cannot turn on the spot: it rolls onto
    the first leg from the heading it has, and it starts each later turn early and cuts the
    corner. Planning on the sharp version put aircraft a mile or two off their own plan, which
    looked like a deviation and caused a replan every few seconds. This returns the polyline
    with those curves in it, so the plan, the line on the map and the aircraft agree.
    """
    if len(points) < 2 or gs_kt <= 0:
        return list(points)
    v = gs_kt / 3600.0
    radius = v / math.radians(TURN_RATE_DEG_S)
    out: list[tuple[float, float]] = [points[0]]
    rest = list(points[1:])
    if hdg0 is not None:
        x, y = points[0]
        hdg, dt = hdg0 % 360.0, 4.0
        for _ in range(80):  # roll onto the first leg exactly as the simulator does
            tx, ty = rest[0]
            if math.hypot(tx - x, ty - y) < max(1.0, v * dt):
                break
            want = math.degrees(math.atan2(tx - x, ty - y)) % 360.0
            d = (want - hdg + 180.0) % 360.0 - 180.0
            if abs(d) < 1.0:
                break
            hdg = (hdg + max(-TURN_RATE_DEG_S * dt, min(TURN_RATE_DEG_S * dt, d))) % 360.0
            x += math.sin(math.radians(hdg)) * v * dt
            y += math.cos(math.radians(hdg)) * v * dt
            out.append((x, y))
    for i, b in enumerate(rest):
        a = out[-1]
        if i == len(rest) - 1:
            out.append(b)
            break
        c = rest[i + 1]
        ab, bc = math.hypot(b[0] - a[0], b[1] - a[1]), math.hypot(c[0] - b[0], c[1] - b[1])
        if ab < 1e-6 or bc < 1e-6:
            out.append(b)
            continue
        u1 = ((b[0] - a[0]) / ab, (b[1] - a[1]) / ab)
        u2 = ((c[0] - b[0]) / bc, (c[1] - b[1]) / bc)
        turn = math.degrees(math.acos(max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))))
        lead = min(radius * math.tan(math.radians(min(turn, 150.0)) / 2.0), FLYBY_MAX_NM, 0.45 * ab, 0.45 * bc)
        if turn < 3.0 or lead < 0.3:
            out.append(b)
            continue
        t1 = (b[0] - u1[0] * lead, b[1] - u1[1] * lead)
        t2 = (b[0] + u2[0] * lead, b[1] + u2[1] * lead)
        out.append(t1)
        for k in (0.25, 0.5, 0.75):  # a quadratic curve through the corner: close to the circular arc
            out.append(((1 - k) ** 2 * t1[0] + 2 * (1 - k) * k * b[0] + k * k * t2[0],
                        (1 - k) ** 2 * t1[1] + 2 * (1 - k) * k * b[1] + k * k * t2[1]))
        out.append(t2)
    return out


def route_points(route: list[str], waypoints: dict[str, Waypoint]) -> list[tuple[float, float]]:
    return [(waypoints[n].x_nm, waypoints[n].y_nm) for n in route if n in waypoints]


def to_planned_path(callsign: str, samples: np.ndarray, cost: float = 0.0,
                    changes: list[str] | None = None, via: list[tuple[float, float]] | None = None) -> PlannedPath:
    if samples.shape[0] >= 2:
        dist = float(np.hypot(np.diff(samples[:, 1]), np.diff(samples[:, 2])).sum())
        dur = float(samples[-1, 0] - samples[0, 0])
    else:
        dist, dur = 0.0, 0.0
    return PlannedPath(
        callsign=callsign, samples=[tuple(map(float, r)) for r in samples], cost=cost,
        changes=list(changes or []), distance_nm=dist, time_s=dur, via=list(via or []),
    )


def samples_array(path: PlannedPath) -> np.ndarray:
    return np.asarray(path.samples, dtype=float).reshape(-1, 4)
