"""Scenario loading and generation.

YAML files live in backend/scenarios/. Format:

    name: demo
    seed: 7
    sector_nm: 200
    waypoints: [{name: CENTA, x_nm: 0, y_nm: 0}, ...]
    routes: {R_WE: [WAKOL, MIDOX, CENTA, LEMKO, ESTIR], ...}
    flights: [{callsign: ACA123, route: R_WE, entry_time_s: 0, alt_ft: 33000, gs_kt: 420}, ...]

A flight's `route` is a route name or an explicit waypoint list. Intruders set
`is_intruder: true` with `x_nm`, `y_nm`, `hdg_deg` and an empty route.
"""
from __future__ import annotations

import copy
import json
import math
import random
from pathlib import Path

import yaml

from schemas import FlightSpec, Scenario, Waypoint, Zone

SCENARIO_DIR = Path(__file__).resolve().parent.parent / "scenarios"

AIRLINES = ["ACA", "WJA", "POE", "JZA", "DAL", "UAL", "AAL"]
ACTYPES = {"ACA": "A320", "WJA": "B738", "POE": "E195", "JZA": "CRJ9", "DAL": "A321", "UAL": "B739", "AAL": "A321"}
LEVELS_FT = [28000, 30000, 32000, 34000, 36000]


REAL_DIR = SCENARIO_DIR / "real"  # built from recorded traffic by tools/real_build.py


def list_scenarios() -> list[str]:
    sims = sorted(p.stem for p in SCENARIO_DIR.glob("*.yaml"))
    reals = sorted(f"real/{p.stem}" for p in REAL_DIR.glob("*.json")) if REAL_DIR.exists() else []
    return sims + reals


def thin(scenario: Scenario, max_flights: int | None) -> Scenario:
    """At most max_flights, evenly spread over the entry order so the rhythm of the hour survives.

    Hidden track vertices that no remaining flight uses are dropped. Gates stay, so the map keeps
    the same names whatever the cap.
    """
    regular = [f for f in scenario.flights if not f.is_intruder]
    if not max_flights or max_flights <= 0 or len(regular) <= max_flights:
        return scenario
    sc = copy.deepcopy(scenario)
    regular = sorted((f for f in sc.flights if not f.is_intruder), key=lambda f: f.entry_time_s)
    step = len(regular) / max_flights
    keep = [regular[int(i * step)] for i in range(max_flights)]
    used = {w for f in keep for w in f.route}
    sc.flights = keep + [f for f in sc.flights if f.is_intruder]
    sc.waypoints = [w for w in sc.waypoints if w.kind != "hidden" or w.name in used]
    sc.meta = {**sc.meta, "flights_available": len(regular), "max_flights": max_flights}
    return sc


def load(name: str) -> Scenario:
    if name.startswith("real/"):
        path = REAL_DIR / f"{name.split('/', 1)[1]}.json"
        if not path.exists():
            raise FileNotFoundError(f"no real scenario {name}; run tools/real_build.py")
        return Scenario.model_validate(json.loads(path.read_text()))
    return _load_yaml(name)


def _load_yaml(name: str) -> Scenario:
    """Load `backend/scenarios/<name>.yaml` (or a path) and expand route names."""
    path = Path(name) if name.endswith(".yaml") else SCENARIO_DIR / f"{name}.yaml"
    raw = yaml.safe_load(path.read_text())
    return from_dict(raw)


def from_dict(raw: dict) -> Scenario:
    routes: dict[str, list[str]] = raw.pop("routes", {}) or {}
    flights = []
    for f in raw.get("flights", []):
        f = dict(f)
        r = f.get("route", [])
        if isinstance(r, str):
            if r not in routes:
                raise ValueError(f"flight {f.get('callsign')} references unknown route {r}")
            f["route"] = list(routes[r])
        f.setdefault("actype", ACTYPES.get(str(f.get("callsign", ""))[:3], "A320"))
        flights.append(FlightSpec(**f))
    raw["flights"] = flights
    raw["waypoints"] = [Waypoint(**w) for w in raw.get("waypoints", [])]
    raw["zones"] = [Zone(**z) for z in raw.get("zones", [])]
    sc = Scenario(**raw)
    sc.__dict__["_routes"] = routes  # keep named routes for generators; not part of the schema
    return sc


def routes_of(scenario: Scenario) -> dict[str, list[str]]:
    """Named routes if the scenario was loaded from YAML, else one route per distinct flight route."""
    named = scenario.__dict__.get("_routes")
    if named:
        return dict(named)
    out: dict[str, list[str]] = {}
    for f in scenario.flights:
        if f.route and not f.is_intruder:
            out.setdefault("/".join(f.route), list(f.route))
    return out


def save(scenario: Scenario, path: Path | str) -> None:
    """Write a scenario back to YAML with explicit routes per flight."""
    d = scenario.model_dump()
    Path(path).write_text(yaml.safe_dump(d, sort_keys=False))


def _network(sector_nm: float) -> tuple[list[Waypoint], dict[str, list[str]]]:
    base = load("demo")
    k = sector_nm / base.sector_nm
    wps = [Waypoint(name=w.name, x_nm=w.x_nm * k, y_nm=w.y_nm * k) for w in base.waypoints]
    return wps, routes_of(base)


def _new_callsign(rng: random.Random, used: set[str]) -> str:
    while True:
        cs = rng.choice(AIRLINES) + str(rng.randint(100, 2999))
        if cs not in used:
            used.add(cs)
            return cs


MIN_ENTRY_SPACING_S = 180.0  # the upstream sector hands flights over at least this far apart per fix


def _sample_flights(rng: random.Random, routes: dict[str, list[str]], n: int, window_s: float,
                    used: set[str], t0: float = 0.0, existing: list[FlightSpec] | None = None) -> list[FlightSpec]:
    names = sorted(routes)
    entries: dict[str, list[float]] = {}
    for f in existing or []:
        if f.route:
            entries.setdefault(f.route[0], []).append(f.entry_time_s)
    out = []
    for _ in range(n):
        cs = _new_callsign(rng, used)
        route = list(routes[rng.choice(names)])
        for _try in range(200):
            t = float(round(t0 + rng.uniform(0, window_s), -1))
            if all(abs(t - e) >= MIN_ENTRY_SPACING_S for e in entries.get(route[0], [])):
                break
        else:
            window_s *= 1.5  # too dense for this fix: widen the window and take what we have
        entries.setdefault(route[0], []).append(t)
        out.append(FlightSpec(
            callsign=cs, actype=ACTYPES[cs[:3]], route=route, entry_time_s=t,
            alt_ft=float(rng.choice(LEVELS_FT)), gs_kt=float(rng.randint(40, 46) * 10),
        ))
    out.sort(key=lambda f: f.entry_time_s)
    return out


def generate(seed: int, n_flights: int, sector_nm: float = 200.0) -> Scenario:
    """Random traffic on the demo route network, for Monte Carlo. Entry window grows with n."""
    rng = random.Random(seed)
    wps, routes = _network(sector_nm)
    window = max(600.0, n_flights * 75.0)
    flights = _sample_flights(rng, routes, n_flights, window, set())
    return Scenario(name=f"gen-{seed}-{n_flights}", seed=seed, sector_nm=sector_nm,
                    waypoints=wps, flights=flights, description="generated")


def multiply(scenario: Scenario, factor: float) -> Scenario:
    """Traffic multiplier. factor>1 adds seeded flights on the same routes; factor<1 drops the latest."""
    sc = copy.deepcopy(scenario)
    if abs(factor - 1.0) < 1e-9:
        return sc
    regular = [f for f in sc.flights if not f.is_intruder]
    intruders = [f for f in sc.flights if f.is_intruder]
    target = max(1, round(len(regular) * factor))
    if target < len(regular):
        regular = sorted(regular, key=lambda f: f.entry_time_s)[:target]
    else:
        rng = random.Random(sc.seed * 1000 + int(factor * 100))
        window = max((f.entry_time_s for f in regular), default=600.0) or 600.0
        used = {f.callsign for f in sc.flights}
        regular += _sample_flights(rng, routes_of(sc), target - len(regular), window, used, existing=regular)
    sc.flights = sorted(regular, key=lambda f: f.entry_time_s) + intruders
    sc.traffic_multiplier = scenario.traffic_multiplier * factor
    sc.name = f"{scenario.name}-x{factor:g}"
    return sc


def distance_of_route(scenario: Scenario, route: list[str]) -> float:
    wp = {w.name: w for w in scenario.waypoints}
    pts = [wp[n] for n in route if n in wp]
    return sum(math.hypot(b.x_nm - a.x_nm, b.y_nm - a.y_nm) for a, b in zip(pts, pts[1:]))
