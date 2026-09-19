"""Live sky: one snapshot of the real sky from adsb.lol, loaded as an ordinary scenario.

Live is a third data source next to simulated and real-replay. It is a snapshot, not a stream: we
ask adsb.lol once for every aircraft within 250 NM of a region centre, keep the airline flights in
level cruise, and hand the simulator a Scenario. From then on the simulator, planner, AI pilots and
readback checks run exactly as they do for any other scenario. Nothing polls afterwards, because a
real aircraft will not follow a Tower clearance and the two pictures would drift apart at once.

Each flight's route is its current track projected straight to the region boundary:
  - inside the circle now: it starts where it is, at t=0
  - in the outer ring (150 to 250 NM) and heading in: it enters on the boundary when it would arrive
Exit points are clustered into named gates with the same code as the recorded hours (sim/gates.py).
Because every route is already a straight line, miles saved is zero by construction in live mode.
Efficiency numbers come from the replay scenarios, where the flown track is known.

`build_scenario` is pure. `fetch`, `save_snapshot` and `latest_snapshot` do the I/O. `load_into`
holds the fallback chain: live feed, else the newest saved snapshot, else the newest committed
replay of the region, else an error notice and the world left as it was.

Data: adsb.lol, ODbL 1.0 and CC0. The same licence and field names as the archive behind
tools/real_extract.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import zlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from airlines import is_airline_callsign
from schemas import FlightSpec, GeoFrame, Scenario, Waypoint
from sim import regions
from sim import scenarios as SC
from sim.gates import cluster_exits
from sim.geo import bearing_deg, unit_vector
from sim.geoframe import EARTH_RADIUS_NM, to_xy

if TYPE_CHECKING:
    from world import World

log = logging.getLogger("tower.live")

API = "https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{dist}"
USER_AGENT = "tower-atc-sim/0.1 (Hack the North 2026 student project; one request per scenario load)"
LIVE_DIR = Path(os.environ.get("TOWER_DATA_DIR", Path(__file__).resolve().parents[2] / "data")) / "live"

FEED_RADIUS_NM = 250      # the most the API serves around a point
MAX_SEEN_POS_S = 15.0     # older positions than this are dropped
LEVEL_FPM = 500.0         # at most this much vertical rate counts as level cruise
MIN_GS_KT, MAX_GS_KT = 250.0, 650.0
MIN_REMAINING_NM = 20.0   # an inside flight closer than this to its exit is about to leave
MIN_CHORD_NM = 40.0       # an inbound flight that crosses less than this only grazes the region
MAX_ENTRY_S = 1800.0      # inbound flights further out than this are not in the scenario
MIN_FLIGHTS = 5
DROP_REASONS = ("no_callsign", "not_airline", "low", "not_level", "stale", "speed", "no_position",
                "duplicate", "not_inbound", "graze", "late", "leaving")

ATTRIBUTION = "Flight data: adsb.lol, ODbL 1.0 and CC0. Gate names are ours."
CAVEATS = ("Each flight's route is its current track projected straight to the region boundary, so miles saved "
           "is zero by construction in live mode (slightly negative once the plan adds a dogleg to resolve a "
           "conflict). Efficiency numbers come from the replay scenarios. Levels and speeds are held at the "
           "snapshot values.")


class LiveFeedError(RuntimeError):
    """The live feed could not be read. The message is short enough to show on screen."""


# --------------------------------------------------------------------------- fetch

def fetch(region_key: str, *, timeout_s: float = 8.0) -> dict[str, Any]:
    """One GET to adsb.lol for everything within 250 NM of the region centre. Blocking.

    Raises LiveFeedError on a timeout, a non-200 answer, or a body that is not the expected JSON.
    """
    reg = regions.get(region_key)
    url = API.format(lat=reg.lat0, lon=reg.lon0, dist=FEED_RADIUS_NM)
    try:
        resp = httpx.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                         timeout=timeout_s, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise LiveFeedError(f"adsb.lol: {type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise LiveFeedError(f"adsb.lol: HTTP {resp.status_code}")
    try:
        raw = resp.json()
    except ValueError as exc:
        raise LiveFeedError("adsb.lol: answer was not JSON") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("ac"), list):
        raise LiveFeedError("adsb.lol: answer had no aircraft list")
    if not _num(raw.get("now")):
        raw["now"] = datetime.now(UTC).timestamp() * 1000.0
    return raw


# --------------------------------------------------------------------------- build

def frame_of(reg: regions.Region) -> GeoFrame:
    return GeoFrame(lat0=reg.lat0, lon0=reg.lon0, name=reg.label, shape="circle")


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def snapshot_time(raw: dict[str, Any]) -> datetime | None:
    """raw["now"] as UTC. The feed sends milliseconds; seconds are accepted too."""
    now = raw.get("now") if isinstance(raw, dict) else None
    if not _num(now) or now <= 0:
        return None
    try:
        return datetime.fromtimestamp(now / 1000.0 if now > 1e11 else now, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _flat_track(frame: GeoFrame, lat: float, lon: float, track_deg: float, x: float, y: float) -> float:
    """The aircraft's track as a heading in the flat plane.

    Away from the centre, north on the map is not straight up in the projection (about 4 degrees
    at 250 NM in Europe). Project a point 1 NM ahead along the true track and take the bearing to it.
    """
    d, brg, phi1 = 1.0 / EARTH_RADIUS_NM, math.radians(track_deg), math.radians(lat)
    phi2 = math.asin(math.sin(phi1) * math.cos(d) + math.cos(phi1) * math.sin(d) * math.cos(brg))
    lam2 = math.radians(lon) + math.atan2(math.sin(brg) * math.sin(d) * math.cos(phi1),
                                          math.cos(d) - math.sin(phi1) * math.sin(phi2))
    x2, y2 = to_xy(frame, math.degrees(phi2), math.degrees(lam2))
    return bearing_deg(x, y, x2, y2)


def _candidate(ac: Any, reg: regions.Region, frame: GeoFrame, seen: set[str]) -> dict[str, Any] | str:
    """One feed entry -> a flight in flat coordinates, or the reason it is not in the scenario."""
    if not isinstance(ac, dict):
        return "no_callsign"
    flight = ac.get("flight")
    callsign = flight.strip().upper() if isinstance(flight, str) else ""
    if not callsign:
        return "no_callsign"
    if not is_airline_callsign(callsign):
        return "not_airline"
    alt = ac.get("alt_baro")  # an int, or the string "ground"
    if not _num(alt) or alt < reg.floor_ft:
        return "low"
    rate = ac.get("baro_rate") if _num(ac.get("baro_rate")) else ac.get("geom_rate")
    if _num(rate) and abs(rate) > LEVEL_FPM:
        return "not_level"
    if _num(ac.get("seen_pos")) and ac["seen_pos"] > MAX_SEEN_POS_S:
        return "stale"
    gs = ac.get("gs")
    if not _num(gs) or not (MIN_GS_KT <= gs <= MAX_GS_KT):
        return "speed"
    lat, lon, track = ac.get("lat"), ac.get("lon"), ac.get("track")
    if not (_num(lat) and _num(lon) and _num(track)) or abs(lat) > 90 or abs(lon) > 180:
        return "no_position"
    if callsign in seen:
        return "duplicate"

    R = reg.radius_nm
    x, y = to_xy(frame, lat, lon)
    hdg = _flat_track(frame, lat, lon, track, x, y)
    # Ray from (x, y) along hdg against the circle |p| = R: |p + s u|^2 = R^2.
    ux, uy = unit_vector(hdg)
    b = x * ux + y * uy
    disc = b * b - (x * x + y * y - R * R)
    r = math.hypot(x, y)
    if r <= R:
        s_in, s_out = 0.0, -b + math.sqrt(max(disc, 0.0))
        if s_out < MIN_REMAINING_NM:
            return "leaving"
    else:
        if r > FEED_RADIUS_NM or disc <= 0 or -b - math.sqrt(disc) <= 0:
            return "not_inbound"  # the track never reaches the circle
        s_in, s_out = -b - math.sqrt(disc), -b + math.sqrt(disc)
        if s_out - s_in < MIN_CHORD_NM:
            return "graze"
        if s_in / gs * 3600.0 > MAX_ENTRY_S:
            return "late"
    actype = ac.get("t")
    return {
        "callsign": callsign, "actype": (actype.strip()[:4] if isinstance(actype, str) and actype.strip() else "A320"),
        "alt": float(round(alt / 1000.0) * 1000), "gs": float(round(gs)), "hdg": round(hdg, 1),
        "entry_t": round(s_in / gs * 3600.0, 1), "start": (x + s_in * ux, y + s_in * uy),
        "exit_angle": math.atan2(y + s_out * uy, x + s_out * ux),
    }


def build_scenario(raw: dict[str, Any], region_key: str, *, max_flights: int | None = None) -> Scenario:
    """A feed snapshot -> a Scenario. Pure: no I/O, and the only clock is raw["now"].

    Raises KeyError for an unknown region and ValueError if fewer than 5 flights survive.
    """
    reg = regions.get(region_key)
    frame = frame_of(reg)
    R = reg.radius_nm
    feed = raw.get("ac") if isinstance(raw, dict) else None
    feed = feed if isinstance(feed, list) else []
    dropped = dict.fromkeys(DROP_REASONS, 0)
    seen: set[str] = set()
    found: list[dict[str, Any]] = []
    for ac in feed:
        try:
            c = _candidate(ac, reg, frame, seen)
        except (TypeError, ValueError, ArithmeticError):  # a malformed entry is not worth the snapshot
            c = "no_position"
        if isinstance(c, str):
            dropped[c] += 1
            continue
        seen.add(c["callsign"])
        found.append(c)
    if len(found) < MIN_FLIGHTS:
        raise ValueError(f"only {len(found)} airline flights at cruise over {reg.label} right now, need {MIN_FLIGHTS}")
    found.sort(key=lambda c: (c["entry_t"], c["callsign"]))

    gates, gate_of = cluster_exits([c["exit_angle"] for c in found], R, seed=zlib.crc32(f"{reg.key}-live".encode()))
    waypoints: list[Waypoint] = list(gates)
    specs: list[FlightSpec] = []
    for i, c in enumerate(found):
        # The start is a hidden waypoint, like the track vertices of a recorded hour, and not
        # x_nm/y_nm: the engine then starts the flight there and flies to the gate, and
        # planner.baseline (which only reads routes) measures the same straight line.
        entry = f"T{i:03d}A"
        waypoints.append(Waypoint(name=entry, x_nm=round(c["start"][0], 2), y_nm=round(c["start"][1], 2), kind="hidden"))
        specs.append(FlightSpec(callsign=c["callsign"], actype=c["actype"], entry_time_s=c["entry_t"],
                                route=[entry, gate_of[i]], alt_ft=c["alt"], gs_kt=c["gs"], hdg_deg=c["hdg"]))

    when = snapshot_time(raw)
    inside = sum(1 for c in found if c["entry_t"] == 0)
    sc = Scenario(
        name=f"live/{reg.key}", seed=7, sector_nm=2 * R, waypoints=waypoints, flights=specs,
        separation_buffer_nm=3.0, geo=frame, source="real",
        meta={"region": reg.key, "label": reg.label, "radius_nm": R, "floor_ft": reg.floor_ft, "gates": len(gates),
              "live": True, "snapshot_utc": when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else None,
              "seen": len(feed), "kept": {"inside": inside, "inbound": len(found) - inside}, "dropped": dropped,
              "attribution": ATTRIBUTION, "caveats": CAVEATS},
    )
    sc = SC.thin(sc, max_flights)
    at = f" {when:%H:%M} UTC" if when else ""
    sc.description = f"{len(sc.flights)} airline flights at cruise over {reg.label}, live snapshot{at}."
    return sc


# --------------------------------------------------------------------------- snapshots on disk

def save_snapshot(raw: dict[str, Any], region_key: str) -> Path | None:
    """Keep the raw answer under data/live/ (gitignored) for the fallback. Best effort: never raises."""
    try:
        when = snapshot_time(raw) or datetime.now(UTC)
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        path = LIVE_DIR / f"{region_key}_{when:%Y%m%dT%H%M%S}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, separators=(",", ":")))
        tmp.replace(path)  # a half-written file must never be the newest snapshot
        return path
    except Exception:  # a full disk must not cost us the live load
        log.exception("could not save the live snapshot")
        return None


def latest_snapshot(region_key: str) -> dict[str, Any] | None:
    """The newest readable saved snapshot of the region, or None. Never raises."""
    try:
        paths = sorted(LIVE_DIR.glob(f"{region_key}_*.json"), reverse=True)  # the timestamp sorts by name
    except OSError:
        return None
    for path in paths:
        try:
            raw = json.loads(path.read_text())
            if isinstance(raw, dict) and isinstance(raw.get("ac"), list):
                return raw
        except (OSError, ValueError):
            log.warning("skipping unreadable snapshot %s", path.name)
    return None


# --------------------------------------------------------------------------- loading into the world

Fetcher = Callable[[str], dict[str, Any]]


def _reason(exc: BaseException) -> str:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "no answer in time"
    text = str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)
    return text or type(exc).__name__


def _load(world: World, region_key: str, max_flights: int | None, raw: dict[str, Any] | None,
          error: BaseException | None) -> str | None:
    """Put the best available picture of the region into the world. Runs on the event loop.

    Returns "live", "saved_snapshot" or "replay", or None if nothing could be loaded (the world is
    then untouched and the screen has an error notice).
    """
    if error is None:
        try:
            sc = build_scenario(raw, region_key, max_flights=max_flights)
            world.load_scenario(sc)
            save_snapshot(raw, region_key)  # only snapshots that built: the fallback must be usable
            return "live"
        except Exception as exc:  # noqa: BLE001 - whatever went wrong, fall back
            log.warning("live snapshot of %s unusable: %s", region_key, exc)
            error = exc
    why = f"Live feed unavailable ({_reason(error)})."

    saved = latest_snapshot(region_key)
    if saved is not None:
        try:
            sc = build_scenario(saved, region_key, max_flights=max_flights)
            sc.meta = {**sc.meta, "fallback": "saved_snapshot"}
            world.load_scenario(sc)
            when = snapshot_time(saved)
            world.notice(f"{why} Loaded the snapshot from {when:%H:%M UTC on %Y-%m-%d} instead." if when
                         else f"{why} Loaded the last saved snapshot instead.", "warn")
            return "saved_snapshot"
        except Exception:
            log.exception("saved snapshot of %s unusable", region_key)

    replays = sorted(n for n in SC.list_scenarios() if n.startswith(f"real/{region_key}_"))
    if replays:
        try:
            sc = SC.thin(SC.load(replays[-1]), max_flights)
            sc.meta = {**sc.meta, "fallback": "replay"}
            world.load_scenario(sc)
            m = sc.meta
            hour = f"{m['date']} {int(m['hour_utc']):02d}:00 UTC" if "date" in m and "hour_utc" in m else replays[-1]
            world.notice(f"{why} Loaded the recorded hour {hour} instead.", "warn")
            return "replay"
        except Exception:
            log.exception("replay %s unusable", replays[-1])

    world.notice(f"{why} Nothing saved or recorded for this region to fall back to.", "error")
    return None


def _region_or_notice(world: World, region_key: str) -> regions.Region | None:
    try:
        return regions.get(str(region_key))
    except KeyError as exc:
        world.notice(f"Could not load the live sky: {_reason(exc)}.", "error")
        return None


def load_into(world: World, region_key: str, max_flights: int | None = None, *,
              fetcher: Fetcher | None = None) -> str | None:
    """Fetch, build and load, with the fallback chain. Blocking: for tests and scripts."""
    if _region_or_notice(world, region_key) is None:
        return None
    raw, error = None, None
    try:
        raw = (fetcher or fetch)(region_key)
    except Exception as exc:  # noqa: BLE001
        error = exc
    return _load(world, region_key, max_flights, raw, error)


async def load_into_async(world: World, region_key: str, max_flights: int | None = None, *,
                          fetcher: Fetcher | None = None, deadline_s: float = 12.0) -> str | None:
    """load_into for the running app. Never raises.

    The fetch runs in a thread so the WebSocket loop and the 1 Hz clock keep going. A second call
    while one is in flight is ignored, and a result that arrives after the user has loaded
    something else is dropped.
    """
    try:
        reg = _region_or_notice(world, region_key)
        if reg is None:
            return None
        if world.live_loading:
            world.notice("A live snapshot is already loading.", "info")
            return None
        world.live_loading = True
        try:
            world.notice(f"Fetching the live sky over {reg.label}.", "info")
            world_id = world.world_id
            raw, error = None, None
            try:
                raw = await asyncio.wait_for(asyncio.to_thread(fetcher or fetch, reg.key), timeout=deadline_s)
            except Exception as exc:  # noqa: BLE001
                error = exc
            if world.world_id != world_id:
                world.notice("Live snapshot dropped: another scenario was loaded while it was on its way.", "info")
                return None
            return _load(world, reg.key, max_flights, raw, error)
        finally:
            world.live_loading = False
    except Exception:  # the socket handler must survive anything
        log.exception("live load failed")
        world.notice("Could not load the live sky. See the backend log.", "error")
        return None


__all__ = ["LIVE_DIR", "LiveFeedError", "build_scenario", "fetch", "frame_of", "latest_snapshot", "load_into",
           "load_into_async", "save_snapshot", "snapshot_time"]
