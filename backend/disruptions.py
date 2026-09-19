"""Every kind of disruption Tower can be hit with, in one table.

A disruption is either a moving point (something flying that does not talk to us) or a circle
of blocked airspace. The table below is the only place that says how big, fast, high and
long-lived each kind is, and how much room the planner gives it. `world.py` builds them,
`sim/engine.py` moves them, `planner/plan.py` plans around them, and the screen builds its
Disrupt menu from `catalog()`.

Placement is seeded: the same scenario and the same sequence of presses gives the same
disruptions, so a demo can be rehearsed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

ALL_LEVELS_FT = 99999.0


@dataclass(frozen=True)
class Profile:
    kind: str
    label: str
    blurb: str
    shape: str  # "point" or "circle"
    prefix: str  # id prefix, read out in card reasons: "clear of STORM2"
    weight: float  # how often Random picks it
    # moving points
    gs_kt: tuple[float, float] = (0.0, 0.0)
    base_nm: float = 10.0  # room the planner gives it now
    growth_nm_per_min: float = 1.0  # and how fast that grows with look-ahead: we cannot trust its track
    max_nm: float = 25.0  # the growth stops here. The plan is repaired every minute from its real position
    vert_ft: float = 2000.0
    climb_fpm: float = 1500.0
    actype: str = "UNKN"
    # circles
    radius_nm: tuple[float, float] = (0.0, 0.0)
    drift_kt: tuple[float, float] = (0.0, 0.0)
    swell_nm_per_min: float = 0.0  # the circle itself growing
    band_ft: tuple[float, float] | None = None  # (below, above) the local traffic level; None = all levels
    # both
    duration_s: tuple[float, float] | None = None  # None = until it leaves the sector


PROFILES: dict[str, Profile] = {p.kind: p for p in [
    Profile("fighter", "Fighter jet", "Fast, straight through, not talking to anyone.", "point", "VIPER", 3.0,
            gs_kt=(480, 600), base_nm=10, growth_nm_per_min=1.0, max_nm=25, vert_ft=2000, actype="F18"),
    Profile("drone", "Drone", "Slow and small, loitering at cruise level.", "point", "DRONE", 1.0,
            gs_kt=(90, 160), base_nm=6, growth_nm_per_min=0.5, max_nm=14, vert_ft=1500, actype="UAV",
            duration_s=(1200, 1800)),
    Profile("balloon", "Balloon", "Drifting with the wind. Level is a guess, so it gets extra height.", "point",
            "BALLOON", 1.0, gs_kt=(15, 40), base_nm=8, growth_nm_per_min=0.3, max_nm=14, vert_ft=3000, actype="BALL",
            duration_s=(1500, 2100)),
    Profile("emergency", "Emergency aircraft", "One of our flights declares a mayday, descends and diverts. Everyone else moves.",
            "point", "", 2.0, base_nm=12, growth_nm_per_min=0.5, max_nm=20, vert_ft=3000, climb_fpm=3500),
    Profile("unknown", "Unknown target", "A radar return with no height and no identity. Blocked at every level.", "point",
            "UNKNOWN", 1.5, gs_kt=(200, 350), base_nm=10, growth_nm_per_min=1.5, max_nm=26, vert_ft=ALL_LEVELS_FT,
            actype="ZZZZ", duration_s=(900, 1200)),
    Profile("storm", "Storm cell", "Drifts and swells. Blocked from the ground up.", "circle", "STORM", 3.0,
            radius_nm=(12, 20), drift_kt=(8, 18), swell_nm_per_min=0.2, duration_s=(1500, 2100)),
    Profile("closed", "Closed airspace", "A block of levels shut for a while. Flights can go around, over or under.", "circle",
            "AREA", 2.0, radius_nm=(15, 24), band_ft=(2000, 1000), duration_s=(1080, 1500)),
    Profile("rocket", "Rocket launch", "A tall column, shut at every level, gone in minutes.", "circle", "LAUNCH", 1.5,
            radius_nm=(18, 25), duration_s=(420, 600)),
]}

ALIASES = {"intruder": "fighter", "jet": "fighter", "storm_cell": "storm", "closed_airspace": "closed",
           "launch": "rocket", "mayday": "emergency"}


def resolve(kind: str) -> str:
    k = (kind or "").strip().lower()
    k = ALIASES.get(k, k)
    if k != "random" and k not in PROFILES:
        raise ValueError(f"unknown disruption kind: {kind}")
    return k


def profile(kind: str | None) -> Profile:
    """Planner-side lookup. Anything unrecognised is treated like a fighter: the widest default."""
    return PROFILES.get(ALIASES.get(kind or "", kind or ""), PROFILES["fighter"])


def catalog() -> list[dict[str, str]]:
    return [{"kind": p.kind, "label": p.label, "blurb": p.blurb, "shape": p.shape} for p in PROFILES.values()]


# What the Random button draws from. Two kinds for now, one of each shape, until the reaction to
# them is perfect: every other kind is one of these with different numbers, and stays in the menu.
RANDOM_KINDS = ("storm", "fighter")


def pick_kind(rng: np.random.Generator, allow_emergency: bool) -> str:
    kinds = [p for p in PROFILES.values() if p.kind in RANDOM_KINDS and (allow_emergency or p.kind != "emergency")]
    w = np.array([p.weight for p in kinds], dtype=float)
    return kinds[int(rng.choice(len(kinds), p=w / w.sum()))].kind


def uniform(rng: np.random.Generator, lo_hi: tuple[float, float]) -> float:
    lo, hi = lo_hi
    return float(lo if hi <= lo else rng.uniform(lo, hi))


def round_level(alt_ft: float) -> float:
    return float(round(alt_ft / 1000.0) * 1000.0)


def inside_sector(x: float, y: float, half_nm: float, circle: bool, margin_nm: float = 0.0) -> bool:
    lim = half_nm + margin_nm
    return math.hypot(x, y) <= lim if circle else (abs(x) <= lim and abs(y) <= lim)


def edge_distance(x: float, y: float, hdg_deg: float, half_nm: float, circle: bool) -> float:
    """How far to the sector boundary flying `hdg_deg` from (x, y)."""
    r = math.radians(hdg_deg)
    ux, uy = math.sin(r), math.cos(r)
    if circle:
        b = x * ux + y * uy
        c = x * x + y * y - half_nm * half_nm
        disc = b * b - c
        return max(0.0, -b + math.sqrt(disc)) if disc >= 0 else 0.0
    best = float("inf")
    for p, u in ((x, ux), (y, uy)):
        if abs(u) > 1e-9:
            d = ((half_nm if u > 0 else -half_nm) - p) / u
            if d >= 0:
                best = min(best, d)
    return 0.0 if best == float("inf") else best
