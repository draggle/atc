"""Runtime separation monitor: counts losses of separation and tracks closest approach per pair."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from sim.engine import Aircraft

SEP_NM = 5.0
SEP_FT = 1000.0


@dataclass
class SeparationMonitor:
    """Call `observe(aircraft, t)` every sim step. A LoS event is one contiguous breach per pair."""

    sep_nm: float = SEP_NM
    sep_ft: float = SEP_FT
    include_intruders: bool = True
    events: list[tuple[str, str, float, float]] = field(default_factory=list)  # (a, b, t_start, min_nm)
    closest: dict[tuple[str, str], tuple[float, float]] = field(default_factory=dict)  # pair -> (nm, t)
    _open: dict[tuple[str, str], tuple[float, float]] = field(default_factory=dict)

    def observe(self, aircraft: list[Aircraft], t: float) -> None:
        acs = aircraft if self.include_intruders else [a for a in aircraft if not a.is_intruder]
        seen: set[tuple[str, str]] = set()
        for i in range(len(acs)):
            a = acs[i]
            for j in range(i + 1, len(acs)):
                b = acs[j]
                if a.is_intruder and b.is_intruder:
                    continue
                if abs(a.alt - b.alt) >= self.sep_ft:
                    continue
                d = math.hypot(a.x - b.x, a.y - b.y)
                key = (a.callsign, b.callsign) if a.callsign < b.callsign else (b.callsign, a.callsign)
                prev = self.closest.get(key)
                if prev is None or d < prev[0]:
                    self.closest[key] = (d, t)
                if d < self.sep_nm:
                    seen.add(key)
                    if key in self._open:
                        t0, m = self._open[key]
                        self._open[key] = (t0, min(m, d))
                    else:
                        self._open[key] = (t, d)
        for key in [k for k in self._open if k not in seen]:
            t0, m = self._open.pop(key)
            self.events.append((key[0], key[1], t0, m))

    def finish(self) -> None:
        for key, (t0, m) in list(self._open.items()):
            self.events.append((key[0], key[1], t0, m))
        self._open.clear()

    @property
    def losses(self) -> int:
        return len(self.events) + len(self._open)

    def closest_nm(self) -> float | None:
        return min((v[0] for v in self.closest.values()), default=None)
