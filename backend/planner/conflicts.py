"""Pairwise conflict tests on sampled trajectories. Vectorized numpy.

Two flights conflict when, at the same sample time, they are within `sep_nm` horizontally
and less than `sep_ft` vertically. The hard floor is 5 NM / 1,000 ft.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from planner.trajectory import DT, T_MAX_S, grid_index, samples_array
from schemas import PlannedPath, Zone

SEP_NM = 5.0
SEP_FT = 1000.0
_T = int(T_MAX_S / DT) + 1


@dataclass
class Grid:
    """Planned trajectories stacked on the shared time grid, NaN where a flight is absent.

    `rad` holds the horizontal separation each row demands (5 NM + buffer, or the growing
    intruder buffer). `vert` is the vertical separation per row.
    """

    names: list[str] = field(default_factory=list)
    X: np.ndarray = field(default_factory=lambda: np.full((0, _T), np.nan))
    Y: np.ndarray = field(default_factory=lambda: np.full((0, _T), np.nan))
    A: np.ndarray = field(default_factory=lambda: np.full((0, _T), np.nan))
    rad: np.ndarray = field(default_factory=lambda: np.full((0, _T), np.nan))
    vert: np.ndarray = field(default_factory=lambda: np.zeros((0, 1)))

    def add(self, name: str, samples: np.ndarray, sep_nm: float | np.ndarray, sep_ft: float = SEP_FT) -> None:
        row = np.full((4, _T), np.nan)
        if samples.shape[0]:
            idx = np.rint(samples[:, 0] / DT).astype(int)
            keep = (idx >= 0) & (idx < _T)
            idx, s = idx[keep], samples[keep]
            row[0, idx], row[1, idx], row[2, idx] = s[:, 1], s[:, 2], s[:, 3]
            row[3, idx] = sep_nm if np.isscalar(sep_nm) else np.asarray(sep_nm)[keep]
        self.names.append(name)
        self.X = np.vstack([self.X, row[0]])
        self.Y = np.vstack([self.Y, row[1]])
        self.A = np.vstack([self.A, row[2]])
        self.rad = np.vstack([self.rad, row[3]])
        self.vert = np.vstack([self.vert, [[sep_ft]]])

    def remove(self, name: str) -> None:
        if name not in self.names:
            return
        i = self.names.index(name)
        self.names.pop(i)
        for attr in ("X", "Y", "A", "rad", "vert"):
            setattr(self, attr, np.delete(getattr(self, attr), i, axis=0))

    def conflicts(self, samples: np.ndarray, extra_nm: float = 0.0) -> np.ndarray:
        """Boolean (n_rows, n_samples) mask of conflicts between `samples` and every row."""
        if samples.shape[0] == 0 or not self.names:
            return np.zeros((len(self.names), samples.shape[0]), dtype=bool)
        idx = np.rint(samples[:, 0] / DT).astype(int)
        ok = (idx >= 0) & (idx < _T)
        out = np.zeros((len(self.names), samples.shape[0]), dtype=bool)
        idx = idx[ok]
        dx = self.X[:, idx] - samples[ok, 1][None, :]
        dy = self.Y[:, idx] - samples[ok, 2][None, :]
        da = np.abs(self.A[:, idx] - samples[ok, 3][None, :])
        r = self.rad[:, idx] + extra_nm
        with np.errstate(invalid="ignore"):
            hit = (dx * dx + dy * dy < r * r) & (da < self.vert)
        out[:, ok] = hit
        return out

    def first_conflict(self, samples: np.ndarray, extra_nm: float = 0.0) -> tuple[str, int, float] | None:
        """(other callsign, sample index, t) of the earliest conflict, or None."""
        m = self.conflicts(samples, extra_nm)
        if not m.any():
            return None
        cols = np.where(m.any(axis=0))[0]
        j = int(cols[0])
        i = int(np.where(m[:, j])[0][0])
        return self.names[i], j, float(samples[j, 0])

    def conflicting_names(self, samples: np.ndarray, extra_nm: float = 0.0) -> list[str]:
        m = self.conflicts(samples, extra_nm)
        return [self.names[i] for i in np.where(m.any(axis=1))[0]]

    def min_distance(self, samples: np.ndarray) -> float:
        """Smallest horizontal distance to any row while vertically within its `vert`."""
        if samples.shape[0] == 0 or not self.names:
            return float("inf")
        idx = np.rint(samples[:, 0] / DT).astype(int)
        ok = (idx >= 0) & (idx < _T)
        idx = idx[ok]
        dx = self.X[:, idx] - samples[ok, 1][None, :]
        dy = self.Y[:, idx] - samples[ok, 2][None, :]
        da = np.abs(self.A[:, idx] - samples[ok, 3][None, :])
        d = np.hypot(dx, dy)
        with np.errstate(invalid="ignore"):
            d = np.where(da < self.vert, d, np.inf)
        d = np.where(np.isnan(d), np.inf, d)
        return float(d.min()) if d.size else float("inf")


def _stack(paths: list[PlannedPath]) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray, int]:
    arrs = [samples_array(p) for p in paths]
    n = len(paths)
    T = max((grid_index(a[-1, 0]) + 1 for a in arrs if a.shape[0]), default=1)
    X = np.full((n, T), np.nan)
    Y = np.full((n, T), np.nan)
    A = np.full((n, T), np.nan)
    for i, a in enumerate(arrs):
        if a.shape[0]:
            idx = np.rint(a[:, 0] / DT).astype(int)
            X[i, idx], Y[i, idx], A[i, idx] = a[:, 1], a[:, 2], a[:, 3]
    return [p.callsign for p in paths], X, Y, A, T


def pairwise_conflicts(paths: list[PlannedPath], sep_nm: float = SEP_NM, sep_ft: float = SEP_FT
                       ) -> list[tuple[str, str, float]]:
    """(cs1, cs2, t_first) for every pair that conflicts at any common sample time."""
    if len(paths) < 2:
        return []
    names, X, Y, A, T = _stack(paths)
    out = []
    for i in range(len(names)):
        dx = X[i + 1:] - X[i]
        dy = Y[i + 1:] - Y[i]
        da = np.abs(A[i + 1:] - A[i])
        with np.errstate(invalid="ignore"):
            hit = (dx * dx + dy * dy < sep_nm * sep_nm) & (da < sep_ft)
        for k in np.where(hit.any(axis=1))[0]:
            j = i + 1 + int(k)
            t = float(np.argmax(hit[k]) * DT)
            out.append((names[i], names[j], t))
    return out


def closest_approach(paths: list[PlannedPath], sep_ft: float = SEP_FT) -> list[tuple[str, str, float, float]]:
    """(cs1, cs2, min_nm, t) per pair, over sample times when the pair is within `sep_ft` vertically.

    Pairs that never share a sample time within `sep_ft` vertically are left out.
    """
    if len(paths) < 2:
        return []
    names, X, Y, A, T = _stack(paths)
    out = []
    for i in range(len(names)):
        d = np.hypot(X[i + 1:] - X[i], Y[i + 1:] - Y[i])
        da = np.abs(A[i + 1:] - A[i])
        with np.errstate(invalid="ignore"):
            d = np.where(da < sep_ft, d, np.nan)
        for k in range(d.shape[0]):
            row = d[k]
            if np.all(np.isnan(row)):
                continue
            j = int(np.nanargmin(row))
            out.append((names[i], names[i + 1 + k], float(row[j]), float(j * DT)))
    return out


def losses_of_separation(paths: list[PlannedPath], buffer: float = 0.0) -> int:
    """Count LoS events (contiguous breaches per pair) at 5 NM + buffer / 1,000 ft."""
    if len(paths) < 2:
        return 0
    names, X, Y, A, T = _stack(paths)
    r = SEP_NM + buffer
    n = 0
    for i in range(len(names)):
        dx = X[i + 1:] - X[i]
        dy = Y[i + 1:] - Y[i]
        da = np.abs(A[i + 1:] - A[i])
        with np.errstate(invalid="ignore"):
            hit = (dx * dx + dy * dy < r * r) & (da < SEP_FT)
        if hit.size:
            padded = np.concatenate([np.zeros((hit.shape[0], 1), bool), hit], axis=1)
            n += int((padded[:, 1:] & ~padded[:, :-1]).sum())
    return n


ZONE_VERT_MARGIN_FT = 1000.0  # a flight must be this far above the ceiling or below the floor
ZONE_EXPIRY_MARGIN_S = 120.0  # a flight running early must not meet a zone the plan thought would be gone


def zone_mask(samples: np.ndarray, zones: list[Zone], margin_nm: float = 0.0) -> np.ndarray:
    """Per-sample: is the flight inside any zone, at that moment and at that level.

    A zone is true at its own `t0`; from there it drifts, swells, and stops existing at
    `expires_t`. It blocks `floor_ft` to `ceiling_ft` plus ZONE_VERT_MARGIN_FT either side.
    """
    n = samples.shape[0]
    if n == 0 or not zones:
        return np.zeros(n, dtype=bool)
    t = samples[:, 0][:, None]
    alt = samples[:, 3][:, None]
    t0 = np.array([z.t0 for z in zones])[None, :]
    dt = np.maximum(t - t0, 0.0)
    rad = np.radians([z.hdg_deg for z in zones])
    v = np.array([z.gs_kt for z in zones]) / 3600.0
    zx = np.array([z.x_nm for z in zones])[None, :] + (np.sin(rad) * v)[None, :] * dt
    zy = np.array([z.y_nm for z in zones])[None, :] + (np.cos(rad) * v)[None, :] * dt
    r = np.array([z.radius_nm for z in zones])[None, :] + np.array([z.swell_nm_per_min for z in zones])[None, :] * dt / 60.0
    r = np.minimum(r, np.array([z.max_radius_nm if z.max_radius_nm is not None else np.inf for z in zones])[None, :])
    r = r + margin_nm
    alive = t < np.array([z.expires_t + ZONE_EXPIRY_MARGIN_S if z.expires_t is not None else np.inf
                          for z in zones])[None, :]
    lo = np.array([z.floor_ft for z in zones])[None, :] - ZONE_VERT_MARGIN_FT
    hi = np.array([z.ceiling_ft for z in zones])[None, :] + ZONE_VERT_MARGIN_FT
    dx = samples[:, 1][:, None] - zx
    dy = samples[:, 2][:, None] - zy
    hit = (dx * dx + dy * dy < r * r) & alive & (alt > lo) & (alt < hi)
    return hit.any(axis=1)


def crosses_zone(samples: np.ndarray, zones: list[Zone], margin_nm: float = 0.0) -> bool:
    """True if any sample lies inside any zone: see zone_mask."""
    return bool(zone_mask(samples, zones, margin_nm).any())


def zone_at(z: Zone, t: float) -> tuple[float, float, float]:
    """(x, y, radius) of a zone at time t."""
    dt = max(0.0, t - z.t0)
    a = np.radians(z.hdg_deg)
    r = z.radius_nm + z.swell_nm_per_min * dt / 60.0
    if z.max_radius_nm is not None:
        r = min(r, z.max_radius_nm)
    return (z.x_nm + float(np.sin(a)) * z.gs_kt / 3600.0 * dt, z.y_nm + float(np.cos(a)) * z.gs_kt / 3600.0 * dt, r)


def zone_crossings(paths: list[PlannedPath], zones: list[Zone]) -> list[str]:
    return [p.callsign for p in paths if crosses_zone(samples_array(p), zones)]
