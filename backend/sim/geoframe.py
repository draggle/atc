"""The bridge between the flat simulator plane and the real Earth.

The simulator, planner and conflict checks all work in a flat plane: x east, y north, nautical
miles. A real map, and real flight data, use latitude and longitude. A GeoFrame pins the flat plane
to a point on Earth so we can convert at the edges and leave the tested flat-plane code alone.

Projection: azimuthal equidistant on a sphere, centred on the frame. Distance and bearing from the
centre are exact. Between two arbitrary points the flat distance differs slightly from the great
circle. Measured on 4,000 random pairs inside a 600 NM square: worst case 0.17 percent, mean 0.03
percent (tests/test_geoframe.py). The error grows with the square of the distance from the centre,
so regions much larger than that should say so on screen.

All functions accept scalars or numpy arrays.
"""
from __future__ import annotations

import numpy as np

from schemas import GeoFrame

EARTH_RADIUS_NM = 3440.065  # mean Earth radius
DEFAULT_FRAME = GeoFrame(lat0=43.6777, lon0=-79.6248, name="Toronto Pearson (CYYZ)")


def to_latlon(frame: GeoFrame, x_nm, y_nm):
    """Flat plane (NM) -> (lat, lon) in degrees."""
    x = np.asarray(x_nm, dtype=float)
    y = np.asarray(y_nm, dtype=float)
    phi0, lam0 = np.radians(frame.lat0), np.radians(frame.lon0)
    rho = np.hypot(x, y)
    c = rho / EARTH_RADIUS_NM
    safe = np.where(rho > 1e-12, rho, 1.0)
    sin_c, cos_c = np.sin(c), np.cos(c)
    phi = np.arcsin(np.clip(cos_c * np.sin(phi0) + (y * sin_c * np.cos(phi0)) / safe, -1.0, 1.0))
    lam = lam0 + np.arctan2(x * sin_c, safe * np.cos(phi0) * cos_c - y * np.sin(phi0) * sin_c)
    lat = np.where(rho > 1e-12, np.degrees(phi), frame.lat0)
    lon = np.where(rho > 1e-12, np.degrees(lam), frame.lon0)
    lon = (lon + 180.0) % 360.0 - 180.0
    if lat.ndim == 0:
        return float(lat), float(lon)
    return lat, lon


def to_xy(frame: GeoFrame, lat, lon):
    """(lat, lon) in degrees -> flat plane (x east, y north) in NM."""
    phi, lam = np.radians(np.asarray(lat, dtype=float)), np.radians(np.asarray(lon, dtype=float))
    phi0, lam0 = np.radians(frame.lat0), np.radians(frame.lon0)
    dlam = lam - lam0
    cos_c = np.clip(np.sin(phi0) * np.sin(phi) + np.cos(phi0) * np.cos(phi) * np.cos(dlam), -1.0, 1.0)
    c = np.arccos(cos_c)
    sin_c = np.sin(c)
    k = np.where(sin_c > 1e-12, c / np.where(sin_c > 1e-12, sin_c, 1.0), 1.0)
    x = EARTH_RADIUS_NM * k * np.cos(phi) * np.sin(dlam)
    y = EARTH_RADIUS_NM * k * (np.cos(phi0) * np.sin(phi) - np.sin(phi0) * np.cos(phi) * np.cos(dlam))
    if x.ndim == 0:
        return float(x), float(y)
    return x, y


def great_circle_nm(lat1, lon1, lat2, lon2):
    """Haversine distance in NM. The ground truth the projection is tested against."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi, dlam = p2 - p1, np.radians(np.asarray(lon2, dtype=float) - np.asarray(lon1, dtype=float))
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_NM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def bounds(frame: GeoFrame, half_nm: float) -> list[list[float]]:
    """[[west, south], [east, north]] covering a square of +-half_nm around the centre."""
    xs = np.array([-half_nm, 0, half_nm, -half_nm, half_nm, -half_nm, 0, half_nm])
    ys = np.array([-half_nm, -half_nm, -half_nm, 0, 0, half_nm, half_nm, half_nm])
    lat, lon = to_latlon(frame, xs, ys)
    return [[round(float(lon.min()), 5), round(float(lat.min()), 5)],
            [round(float(lon.max()), 5), round(float(lat.max()), 5)]]


def simplify_samples(samples: list[tuple[float, float, float, float]], max_gap_s: float = 300.0,
                     turn_deg: float = 0.5) -> list[tuple[float, float, float, float]]:
    """Drop samples that add nothing to the drawn line.

    Planned paths are sampled every 10 s, mostly along straight legs. For drawing we keep the first
    and last point, every vertex where the track turns or the climb changes, and at least one point
    every max_gap_s so long straight legs still follow the Earth's curve on a map.
    """
    n = len(samples)
    if n <= 2:
        return list(samples)
    keep = [samples[0]]
    last_t = samples[0][0]
    for i in range(1, n - 1):
        t, x, y, alt = samples[i]
        px, py, palt = samples[i - 1][1], samples[i - 1][2], samples[i - 1][3]
        nx, ny, nalt = samples[i + 1][1], samples[i + 1][2], samples[i + 1][3]
        b_in = np.degrees(np.arctan2(x - px, y - py))
        b_out = np.degrees(np.arctan2(nx - x, ny - y))
        turn = abs((b_out - b_in + 180.0) % 360.0 - 180.0)
        climb_change = abs((nalt - alt) - (alt - palt)) > 1.0
        if turn > turn_deg or climb_change or t - last_t >= max_gap_s:
            keep.append(samples[i])
            last_t = t
    keep.append(samples[-1])
    return keep


def path_lonlat(frame: GeoFrame, samples: list[tuple[float, float, float, float]]) -> list[list[float]]:
    """A planned path as [[lon, lat, alt_ft, t], ...] for the map. GeoJSON order, simplified."""
    pts = simplify_samples(samples)
    if not pts:
        return []
    arr = np.asarray(pts, dtype=float)
    lat, lon = to_latlon(frame, arr[:, 1], arr[:, 2])
    lat, lon = np.atleast_1d(lat), np.atleast_1d(lon)
    return [[round(float(lo), 5), round(float(la), 5), round(float(a)), round(float(t), 1)]
            for lo, la, a, t in zip(lon, lat, arr[:, 3], arr[:, 0])]


__all__ = ["DEFAULT_FRAME", "EARTH_RADIUS_NM", "bounds", "great_circle_nm", "path_lonlat",
           "simplify_samples", "to_latlon", "to_xy"]
