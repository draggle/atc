"""The interpreter agent: what did the controller mean, for this aircraft, right now?

Standard phraseology is read by the grammar and the common plain-English forms by patterns
(tower/freeform.py), both in no time. This is for the rest: "take it round the north side of the
storm", "follow United two one zero", "get above that weather", "point it at the lake and slow it
right down". None of that means anything without the picture, so the agent is given the picture (the
aircraft, the fixes, the zones, the traffic near it) and the aircraft's controls as tools. It answers
by calling them. One model call on Baseten, about a second.

It never flies anything itself. Its tool calls become the same standard items as everything else,
are validated like everything else, go to the pilot to be read back, and are checked. If it cannot
tell what is wanted it must say `unable`, and nothing moves. The planner is not its job: it says
what the controller asked for, and the planner then plans round that.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any

from schemas import AircraftState, Item, Zone
from tower import freeform as FF

log = logging.getLogger("tower.interpreter")

NEARBY_NM = 80.0
MAX_FIXES = 14

SYSTEM = """You are the flight-deck side of an air traffic control simulator. A controller has given one aircraft an \
instruction in plain English. Turn it into aircraft commands by calling the tools. You are given the aircraft's \
state and what is around it: use them to resolve anything relative ("round the north side of the storm", "follow \
that traffic", "above the weather", "towards the lake" is not a fix so use a heading).

Rules: call one tool per distinct instruction, all in this one reply. Headings are degrees magnetic 1 to 360. \
Bearings given to you are from the aircraft. To pass a zone on one side, pick a heading that clears its edge by \
about 10 NM. Levels are feet. Never invent a fix name: use only the fixes listed. If the request is something an \
airliner cannot or must not do, or you cannot tell what is wanted, call `unable` with a short reason and nothing else."""

TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "fly_heading", "description": "Turn onto a heading and hold it.",
        "parameters": {"type": "object", "properties": {
            "heading_deg": {"type": "integer", "minimum": 1, "maximum": 360},
            "turn": {"type": "string", "enum": ["left", "right", "shortest"]}}, "required": ["heading_deg"]}}},
    {"type": "function", "function": {
        "name": "change_level", "description": "Climb or descend to a level, in feet (35000 is FL350).",
        "parameters": {"type": "object", "properties": {"level_ft": {"type": "integer", "minimum": 1000, "maximum": 45000}},
                       "required": ["level_ft"]}}},
    {"type": "function", "function": {
        "name": "change_speed", "description": "Fly this ground speed in knots.",
        "parameters": {"type": "object", "properties": {"speed_kt": {"type": "integer", "minimum": 180, "maximum": 560}},
                       "required": ["speed_kt"]}}},
    {"type": "function", "function": {
        "name": "direct_to", "description": "Proceed direct to a named fix from the list. The aircraft's own exit fix puts it back on course.",
        "parameters": {"type": "object", "properties": {"fix": {"type": "string"}}, "required": ["fix"]}}},
    {"type": "function", "function": {
        "name": "circle", "description": "Fly full circles where it is: one three-sixty, or a hold until told otherwise.",
        "parameters": {"type": "object", "properties": {"side": {"type": "string", "enum": ["left", "right"]},
                                                        "until_advised": {"type": "boolean"}}, "required": ["side"]}}},
    {"type": "function", "function": {
        "name": "unable", "description": "The aircraft will not do this, or the instruction cannot be understood.",
        "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}}},
]


def _bearing(dx: float, dy: float) -> int:
    return int(round(math.degrees(math.atan2(dx, dy)))) % 360 or 360


def picture(state: AircraftState, waypoints: dict[str, tuple[float, float]], zones: list[Zone],
            traffic: list[AircraftState]) -> dict[str, Any]:
    """What the agent is shown: everything as bearing and distance from the aircraft, the way a
    pilot or a controller would say it. Small on purpose: tokens are latency."""
    fixes = sorted(((math.hypot(x - state.x_nm, y - state.y_nm), n, x, y) for n, (x, y) in waypoints.items()))[:MAX_FIXES]
    return {
        "aircraft": {"callsign": state.callsign, "heading": int(round(state.hdg_deg)) % 360 or 360,
                     "cleared_heading": state.target_hdg_deg, "level_ft": int(round(state.alt_ft, -2)),
                     "cleared_level_ft": int(round(state.target_alt_ft, -2)), "speed_kt": int(round(state.gs_kt)),
                     "route_ahead": list(state.route), "exit_fix": state.route[-1] if state.route else None,
                     "circling": state.manoeuvre},
        "fixes": [{"name": n, "bearing": _bearing(x - state.x_nm, y - state.y_nm), "nm": round(d)} for d, n, x, y in fixes],
        "zones": [{"id": z.id, "kind": z.kind, "bearing_to_centre": _bearing(z.x_nm - state.x_nm, z.y_nm - state.y_nm),
                   "nm_to_centre": round(math.hypot(z.x_nm - state.x_nm, z.y_nm - state.y_nm)), "radius_nm": round(z.radius_nm),
                   "floor_ft": int(z.floor_ft), "ceiling_ft": int(min(z.ceiling_ft, 60000))} for z in zones],
        "traffic": [{"callsign": t.callsign, "bearing": _bearing(t.x_nm - state.x_nm, t.y_nm - state.y_nm),
                     "nm": round(math.hypot(t.x_nm - state.x_nm, t.y_nm - state.y_nm)), "heading": int(round(t.hdg_deg)),
                     "level_ft": int(round(t.alt_ft, -2)), "intruder": t.is_intruder}
                    for t in traffic if t.callsign != state.callsign
                    and math.hypot(t.x_nm - state.x_nm, t.y_nm - state.y_nm) <= NEARBY_NM],
    }


class Interpreter:
    def __init__(self, llm: Any) -> None:
        self.llm = llm

    @property
    def available(self) -> bool:
        return self.llm is not None and not getattr(self.llm, "is_mock", False) and hasattr(self.llm, "chat")

    def interpret(self, said: str, state: AircraftState, waypoints: dict[str, tuple[float, float]],
                  zones: list[Zone], traffic: list[AircraftState]) -> tuple[list[Item], str]:
        """(items, a line for the screen). No items means nothing is issued; the line says why."""
        if not self.available:
            return [], ""
        user = json.dumps({"controller_said": said, **picture(state, waypoints, zones, traffic)}, separators=(",", ":"))
        try:
            resp = self.llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                                 tools=TOOLS, tool_choice="required", max_tokens=200)
        except Exception as exc:  # the radio must survive a model that is down
            log.warning("interpreter call failed: %s", exc)
            return [], "Tower could not reach its interpreter. Say it in standard phraseology."
        return self._items(resp.tool_calls, state, {n.upper() for n in waypoints})

    @staticmethod
    def _items(calls: list[Any], state: AircraftState, fixes: set[str]) -> tuple[list[Item], str]:
        items: list[Item] = []
        for c in calls:
            a = c.arguments or {}
            try:
                if c.name == "unable":
                    return [Item(type="manoeuvre", value=f"UNABLE {str(a.get('reason', '')).upper()}"[:80].strip(),
                                 action="unable", mandatory=False)], str(a.get("reason", ""))
                if c.name == "fly_heading":
                    hdg = int(a["heading_deg"]) % 360 or 360
                    turn = a.get("turn") or "shortest"
                    if turn == "shortest":
                        turn = "right" if ((hdg - state.hdg_deg + 540) % 360) - 180 >= 0 else "left"
                    items.append(Item(type="heading", value=hdg, unit="deg", action=f"turn_{turn}"))
                elif c.name == "change_level":
                    items.append(FF._altitude_item(float(a["level_ft"]), state.alt_ft))
                elif c.name == "change_speed":
                    kt = int(a["speed_kt"])
                    items.append(Item(type="speed", value=kt, unit="kt", action="increase" if kt > state.gs_kt else "reduce"))
                elif c.name == "direct_to":
                    fix = str(a["fix"]).upper()
                    if fix in fixes:
                        items.append(Item(type="route", value=fix, unit=None, action="direct"))
                elif c.name == "circle":
                    side = "left" if str(a.get("side", "right")).lower().startswith("l") else "right"
                    hold = bool(a.get("until_advised"))
                    items.append(Item(type="manoeuvre", value=f"{'HOLD' if hold else '360'} {side.upper()}",
                                      action=f"{'hold' if hold else 'orbit'}_{side}"))
            except (KeyError, TypeError, ValueError):
                continue
        seen: set[str] = set()
        out = []
        for i in items:  # one of each kind, and only what can be said and flown
            if FF.valid(i) and i.type not in seen:
                seen.add(i.type)
                out.append(i)
        return out, "" if out else "Tower could not turn that into an instruction."
