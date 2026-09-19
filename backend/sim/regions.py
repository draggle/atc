"""The regions real traffic is cut from: one circle of cruise airspace each.

Shared by the archive extractor (tools/real_extract.py), which also uses the replay hours, and by
live mode (sim/live.py), which takes one snapshot of the same circle.
"""
from __future__ import annotations

from typing import NamedTuple


class Region(NamedTuple):
    # A tuple on purpose: tools/real_extract.py unpacks it positionally in a hot loop.
    key: str
    lat0: float
    lon0: float
    radius_nm: float
    floor_ft: int
    hours_utc: list[int]  # replay window starts; live mode ignores them
    label: str


REGIONS = [
    Region("europe-core", 50.6, 6.2, 150.0, 24500, [10, 16], "Western Europe core (Maastricht, Rhine, Benelux)"),
    Region("uk", 52.6, -1.4, 150.0, 24500, [10, 16], "United Kingdom (Midlands and the London approaches)"),
    Region("us-northeast", 40.9, -75.2, 150.0, 24000, [14, 21], "US Northeast corridor (New York, Philadelphia)"),
    Region("toronto", 43.6777, -79.6248, 150.0, 24000, [14, 21], "Southern Ontario (Toronto Pearson)"),
]
_BY_KEY = {r.key: r for r in REGIONS}


def get(key: str) -> Region:
    """The region called `key`. Raises KeyError with the valid keys if there is none."""
    try:
        return _BY_KEY[key]
    except KeyError:
        raise KeyError(f"unknown region {key!r}; known: {', '.join(_BY_KEY)}") from None


def catalog() -> list[dict[str, str]]:
    """[{key, label}] for the setup panel."""
    return [{"key": r.key, "label": r.label} for r in REGIONS]


__all__ = ["REGIONS", "Region", "catalog", "get"]
