"""Flat-plane geometry helpers shared by the simulator and planner.

Convention: x east, y north, nautical miles. Heading 0 = north, clockwise, degrees.
"""
from __future__ import annotations

import math

NM_PER_KT_S = 1.0 / 3600.0  # NM travelled per second at 1 kt


def bearing_deg(x0: float, y0: float, x1: float, y1: float) -> float:
    """Compass bearing from (x0, y0) to (x1, y1)."""
    return math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360.0


def dist_nm(x0: float, y0: float, x1: float, y1: float) -> float:
    return math.hypot(x1 - x0, y1 - y0)


def wrap180(d: float) -> float:
    """Wrap a heading difference into (-180, 180]."""
    return (d + 180.0) % 360.0 - 180.0


def unit_vector(hdg_deg: float) -> tuple[float, float]:
    r = math.radians(hdg_deg)
    return math.sin(r), math.cos(r)
