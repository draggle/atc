"""Resolver tools: OpenAI tool schemas plus the injected callables that back them."""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from schemas import AircraftState, Item, OpenClearance

TERMINAL_TOOLS = {"raise_alert", "dismiss", "mark_uncertain", "watch"}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "relisten",
        "description": "Re-transcribe the transmission with the larger speech model. Returns the top 5 hypotheses.",
        "parameters": {"type": "object", "properties": {"transmission_id": {"type": "string"}},
                       "required": ["transmission_id"]}}},
    {"type": "function", "function": {
        "name": "active_aircraft",
        "description": "Every callsign on frequency with its open clearances.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "frequency_history",
        "description": "The last n exchanges for a callsign.",
        "parameters": {"type": "object", "properties": {"callsign": {"type": "string"},
                                                        "n": {"type": "integer", "default": 3}},
                       "required": ["callsign"]}}},
    {"type": "function", "function": {
        "name": "aircraft_state",
        "description": "Radar: position, altitude, heading and their targets for one callsign.",
        "parameters": {"type": "object", "properties": {"callsign": {"type": "string"}},
                       "required": ["callsign"]}}},
    {"type": "function", "function": {
        "name": "nearby_aircraft",
        "description": "Radar search: every other aircraft within radius_nm of this callsign right now, with distance. Use it to check whether a similar callsign or a conflicting aircraft is close.",
        "parameters": {"type": "object", "properties": {"callsign": {"type": "string"},
                                                        "radius_nm": {"type": "number", "default": 30}},
                       "required": ["callsign"]}}},
    {"type": "function", "function": {
        "name": "aircraft_track",
        "description": "Radar history: what this aircraft's altitude and heading did over the last N seconds (trend, start and end values). Use it to see whether the aircraft is already flying the expected value or the heard one.",
        "parameters": {"type": "object", "properties": {"callsign": {"type": "string"},
                                                        "seconds": {"type": "number", "default": 30}},
                       "required": ["callsign"]}}},
    {"type": "function", "function": {
        "name": "sanity_check",
        "description": "Whether an item value is plausible: altitude in range, valid frequency, real waypoint, valid runway.",
        "parameters": {"type": "object", "properties": {
            "type": {"type": "string"}, "value": {"type": ["string", "number"]},
            "unit": {"type": ["string", "null"]}}, "required": ["type", "value"]}}},
    {"type": "function", "function": {
        "name": "watch",
        "description": "TERMINAL. Defer: let radar verification decide by watching the aircraft for some seconds.",
        "parameters": {"type": "object", "properties": {"callsign": {"type": "string"},
                                                        "seconds": {"type": "integer", "default": 60}},
                       "required": ["callsign"]}}},
    {"type": "function", "function": {
        "name": "raise_alert",
        "description": "TERMINAL. The readback is wrong; alert the controller.",
        "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}}},
    {"type": "function", "function": {
        "name": "dismiss",
        "description": "TERMINAL. The readback was actually fine; close without alerting.",
        "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}}},
    {"type": "function", "function": {
        "name": "mark_uncertain",
        "description": "TERMINAL. Cannot tell; a human should look.",
        "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}}},
]


@dataclass
class WatchRequest:
    clearance_id: str
    callsign: str
    seconds: float


def default_sanity_check(item: dict[str, Any], waypoints: set[str] | None = None) -> dict[str, Any]:
    t, v, unit = item.get("type"), item.get("value"), item.get("unit")
    ok, why = True, "plausible"
    try:
        if t == "altitude":
            ft = float(v) * 100 if unit == "FL" else float(v)
            ok = 1000 <= ft <= 45000
            why = "altitude between 1,000 and 45,000 ft" if ok else f"{int(ft)} ft is outside 1,000 to 45,000 ft"
        elif t == "heading":
            ok = 0 <= float(v) <= 360
            why = "valid heading" if ok else "heading outside 000 to 360"
        elif t == "speed":
            ok = 100 <= float(v) <= 400
            why = "valid speed" if ok else "speed outside 100 to 400 kt"
        elif t == "frequency":
            f = float(v)
            ok = 118.0 <= f <= 136.975
            why = "valid VHF frequency" if ok else "not a VHF COM frequency"
        elif t == "squawk":
            s = str(v)
            ok = len(s) == 4 and all(c in "01234567" for c in s)
            why = "valid squawk" if ok else "squawk digits must be 0 to 7"
        elif t in ("runway", "hold_short"):
            ok = bool(re.fullmatch(r"(0?[1-9]|[12]\d|3[0-6])[LRC]?", str(v)))
            why = "valid runway designator" if ok else "not a runway designator"
        elif t == "route":
            if waypoints:
                ok = str(v).upper() in waypoints
                why = "known waypoint" if ok else "unknown waypoint"
            else:
                ok = bool(re.fullmatch(r"[A-Z]{3,5}", str(v).upper()))
                why = "looks like a waypoint" if ok else "not a waypoint name"
    except (TypeError, ValueError):
        ok, why = False, "unparseable value"
    return {"plausible": ok, "reason": why}


@dataclass
class ResolverTools:
    """Injected data access for the resolver. Every callable is optional; missing ones return empty."""

    relisten: Callable[[str], list[str]] | None = None
    active_aircraft: Callable[[], list[dict[str, Any]]] | None = None
    frequency_history: Callable[[str, int], list[dict[str, Any]]] | None = None
    aircraft_state: Callable[[str], AircraftState | None] | None = None
    waypoints: set[str] = field(default_factory=set)
    sanity_check: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    nearby_aircraft: Callable[[str, float], list[dict[str, Any]]] | None = None
    aircraft_track: Callable[[str, float], dict[str, Any] | None] | None = None
    source: str = "in-memory"  # where the evidence comes from, shown in the trace

    def execute(self, name: str, args: dict[str, Any]) -> Any:
        """Run a non-terminal tool. Returns JSON-serializable data."""
        if name == "relisten":
            return list(self.relisten(args.get("transmission_id", ""))) if self.relisten else []
        if name == "active_aircraft":
            return list(self.active_aircraft()) if self.active_aircraft else []
        if name == "frequency_history":
            if not self.frequency_history:
                return []
            return list(self.frequency_history(args.get("callsign", ""), int(args.get("n", 3) or 3)))
        if name == "aircraft_state":
            if not self.aircraft_state:
                return None
            s = self.aircraft_state(args.get("callsign", ""))
            return s.model_dump() if s is not None else None
        if name == "nearby_aircraft":
            if not self.nearby_aircraft:
                return []
            return list(self.nearby_aircraft(args.get("callsign", ""), float(args.get("radius_nm", 30) or 30)))
        if name == "aircraft_track":
            if not self.aircraft_track:
                return None
            return self.aircraft_track(args.get("callsign", ""), float(args.get("seconds", 30) or 30))
        if name == "sanity_check":
            fn = self.sanity_check or (lambda item: default_sanity_check(item, self.waypoints))
            return fn(args)
        return {"error": f"unknown tool {name}"}


def summarize(name: str, result: Any, source: str | None = None) -> str:
    """One line for the resolver_step event. `source` names where the evidence came from
    (for example "Elasticsearch") for the tools that search the frequency memory."""
    line = _summarize(name, result)
    if source and source != "in-memory" and name in SEARCH_TOOLS:
        return f"[{source}] {line}"
    return line


SEARCH_TOOLS = {"frequency_history", "nearby_aircraft", "aircraft_track", "sanity_check"}


def _summarize(name: str, result: Any) -> str:
    if name == "relisten":
        hyps = result or []
        return f"{len(hyps)} hypotheses: " + " | ".join(str(h) for h in hyps[:5])
    if name == "active_aircraft":
        cs = [a.get("callsign", "?") for a in (result or [])]
        return f"{len(cs)} on frequency: {', '.join(cs)}"
    if name == "frequency_history":
        rows = result or []
        if not rows:
            return "no prior exchanges"
        top = rows[0]
        best = f": best match \"{top.get('text', '')[:60]}\"" if top.get("score") is not None else ""
        return f"{len(rows)} prior exchanges{best}"
    if name == "nearby_aircraft":
        rows = result or []
        if not rows:
            return "nobody within range"
        return f"{len(rows)} within range: " + ", ".join(
            f"{a.get('callsign')} {a.get('distance_nm')} NM" for a in rows[:4])
    if name == "aircraft_track":
        if not result:
            return "no radar history"
        return (f"{result.get('trend')} over {result.get('seconds')} s: "
                f"{result.get('alt_start_ft')} to {result.get('alt_end_ft')} ft, "
                f"hdg {int(result.get('hdg_start_deg', 0)):03d} to {int(result.get('hdg_end_deg', 0)):03d}")
    if name == "aircraft_state":
        if not result:
            return "no radar track"
        return f"alt {int(result.get('alt_ft', 0))} ft, hdg {int(result.get('hdg_deg', 0)):03d}"
    if name == "sanity_check":
        return f"{'plausible' if (result or {}).get('plausible') else 'implausible'}: {(result or {}).get('reason', '')}"
    return str(result)[:200]


def clearance_brief(c: OpenClearance) -> dict[str, Any]:
    return {"id": c.id, "callsign": c.callsign, "status": c.status,
            "items": [_item_brief(i) for i in c.items]}


def _item_brief(i: Item) -> dict[str, Any]:
    return {"type": i.type, "value": i.value, "unit": i.unit, "action": i.action}
