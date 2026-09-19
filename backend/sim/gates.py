"""Named exit gates for a circular real-traffic region.

Real data has no fix names, and the radio needs one ("proceed direct KOVAL"). Exit points cluster
naturally where airways leave the region, so we group them (at most 8 NM from a gate) and give each
group a pronounceable five-letter name. Real fix names are made-up words too. These are ours, and
the screen says so. Used by tools/real_build.py (recorded hours) and sim/live.py (live snapshot).
"""
from __future__ import annotations

import math
import random

import numpy as np

from schemas import Waypoint

GATE_SPREAD_NM = 16.0     # a gate serves exits within +-8 NM along the boundary


def gate_names(n: int, seed: int, taken: set[str]) -> list[str]:
    rng = random.Random(seed)
    cons, vow = "BDGKLMNPRSTVZ", "AEIOU"
    out: list[str] = []
    while len(out) < n:
        name = rng.choice(cons) + rng.choice(vow) + rng.choice(cons) + rng.choice(vow) + rng.choice("KLMNRSTX")
        if name not in taken:
            taken.add(name)
            out.append(name)
    return out


def cluster_exits(exit_angles: list[float], radius_nm: float, seed: int,
                  spread_nm: float = GATE_SPREAD_NM) -> tuple[list[Waypoint], list[str]]:
    """Group exits by angle around the boundary. Returns (gates, the gate name of each exit).

    `exit_angles` are math angles (atan2(y, x), radians) of where each flight leaves the circle.
    Walking round the boundary, an exit joins the open group while it is within `spread_nm` of the
    group's first member. Each gate sits on the circle at its group's mean angle. Names depend only
    on `seed` and the number of groups.
    """
    R = radius_nm
    order = sorted(range(len(exit_angles)), key=lambda i: exit_angles[i])
    clusters: list[list[int]] = []
    for i in order:
        if clusters and (exit_angles[i] - exit_angles[clusters[-1][0]]) * R <= spread_nm:
            clusters[-1].append(i)
        else:
            clusters.append([i])
    names = gate_names(len(clusters), seed=seed, taken=set())
    gates: list[Waypoint] = []
    gate_of = [""] * len(exit_angles)
    for name, members in zip(names, clusters):
        ang = float(np.mean([exit_angles[i] for i in members]))
        gates.append(Waypoint(name=name, x_nm=round(R * math.cos(ang), 2), y_nm=round(R * math.sin(ang), 2), kind="gate"))
        for i in members:
            gate_of[i] = name
    return gates, gate_of


__all__ = ["GATE_SPREAD_NM", "cluster_exits", "gate_names"]
