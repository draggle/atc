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


def route_points(route: list[str], waypoints: dict[str, Waypoint]) -> list[tuple[float, float]]:
    return [(waypoints[n].x_nm, waypoints[n].y_nm) for n in route if n in waypoints]


def to_planned_path(callsign: str, samples: np.ndarray, cost: float = 0.0,
                    changes: list[str] | None = None) -> PlannedPath:
    if samples.shape[0] >= 2:
        dist = float(np.hypot(np.diff(samples[:, 1]), np.diff(samples[:, 2])).sum())
        dur = float(samples[-1, 0] - samples[0, 0])
    else:
        dist, dur = 0.0, 0.0
    return PlannedPath(
        callsign=callsign, samples=[tuple(map(float, r)) for r in samples], cost=cost,
        changes=list(changes or []), distance_nm=dist, time_s=dur,
    )


def samples_array(path: PlannedPath) -> np.ndarray:
    return np.asarray(path.samples, dtype=float).reshape(-1, 4)
