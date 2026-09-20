"""The squack agent's tools. PRD sections 3 and 4.

Five namespaces: `world.*` changes the world through the World methods the screen already uses;
`ui.*` does nothing here but emit a `ui_command` the screen applies; `query.*` reads live state
into a table or list card; `explain.*` reads the planner's own reasons (card reason and cause,
path changes, cost against the runner-up, confidence) and never invents one (hard rule 10);
`sim.*` hands a Monte Carlo or a sweep to the background job runner (`tools/simjobs.py`).

No tool opens a clearance: nothing here touches the frequency. The one tool that acts on traffic
is `world.nudge`, which asks the planner for a new path with a bigger buffer and produces a
card, the same as any replan (hard rule 8: the floor stays in the planner).

Every tool takes `(world, args)` and returns a JSON-able dict. `summary` is the one-line trace,
`card` an optional card descriptor (agent/cards.py), `undo` the inverse action when there is one.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agent import cards as CD
from planner.risk import REPLAN_P, SHOW_P
from schemas import AircraftState, InstructionCard, PlannedPath, event

if TYPE_CHECKING:
    from world import World

NUDGE_EXTRA_NM = 2.0  # world.nudge: the temporary extra buffer for the one flight being re-planned
MC_RUNS_DEFAULT, MC_RUNS_MAX = 8, 20
SWEEP_POINTS_MAX = 4
TIMELINE_DEFAULT_S = 300.0
PAIRS_DEFAULT_NM = 10.0
UI_MODES = ("normal", "agent")
LINE_VIEWS = ("today", "tower", "both", "changed")
LIFECYCLE_ACTIONS = ("start", "pause", "reset")


@dataclass
class Tool:
    name: str  # "group.verb"
    description: str
    parameters: dict[str, Any]
    fn: Callable[["World", dict[str, Any]], dict[str, Any]]
    acts_on_traffic: bool = False
    required: list[str] = field(default_factory=list)

    @property
    def wire_name(self) -> str:
        """OpenAI function names allow no dots."""
        return self.name.replace(".", "__")

    def schema(self) -> dict[str, Any]:
        params = {"type": "object", "properties": self.parameters}
        if self.required:
            params["required"] = list(self.required)
        return {"type": "function", "function": {"name": self.wire_name, "description": self.description,
                                                 "parameters": params}}


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, parameters: dict[str, Any] | None = None, *, required: list[str] | None = None,
         acts_on_traffic: bool = False) -> Callable[[Callable[..., dict[str, Any]]], Callable[..., dict[str, Any]]]:
    def deco(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        REGISTRY[name] = Tool(name, description, parameters or {}, fn, acts_on_traffic, required or [])
        return fn
    return deco


def resolve_name(name: str) -> str:
    return name.replace("__", ".") if "__" in name else name


def schemas(namespaces: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    return [t.schema() for t in REGISTRY.values() if namespaces is None or t.name.split(".")[0] in namespaces]


def names() -> list[str]:
    return list(REGISTRY)


def execute(world: "World", name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one tool. Never raises: an error is a result with `error`, so the loop stays on its feet."""
    name = resolve_name(name)
    t = REGISTRY.get(name)
    if t is None:
        return {"error": f"unknown tool {name}", "summary": f"unknown tool {name}"}
    if t.acts_on_traffic and name != "world.nudge":
        return {"error": f"{name} acts on traffic and the agent may not call it",
                "summary": f"refused {name}: acts on traffic"}
    try:
        out = t.fn(world, dict(args or {}))
    except Exception as exc:  # noqa: BLE001 - never silent, never fatal
        out = {"error": f"{type(exc).__name__}: {exc}"}
    out.setdefault("summary", out.get("error") or "ok")
    if isinstance(out.get("card"), CD.Card):
        out["card"] = CD.dump(out["card"])
    if isinstance(out.get("cards"), list):
        out["cards"] = [CD.dump(c) if isinstance(c, CD.Card) else c for c in out["cards"]]
    return out


def summarize(name: str, result: dict[str, Any]) -> str:
    return str(result.get("summary") or result.get("error") or "ok")[:200]


# --------------------------------------------------------------------------------- helpers


def _no_scenario(world: "World") -> dict[str, Any] | None:
    if world.scenario is None:
        return {"error": "no scenario loaded", "summary": "no scenario loaded"}
    return None


def active_callsigns(world: "World") -> list[str]:
    return [a.callsign for a in world.sim.aircraft() if not a.is_intruder]


def find_callsign(world: "World", text: str | None) -> str | None:
    """A callsign as typed (ACA123), as spoken (air canada one two three), or a shortened one."""
    if not text:
        return None
    from tower.callsign import snap

    active = [a.callsign for a in world.sim.aircraft()]
    up = text.strip().upper()
    if up in active:
        return up
    s = snap(text, active)
    return s.best


def state_of(world: "World", callsign: str) -> AircraftState | None:
    return next((a for a in world.sim.aircraft() if a.callsign == callsign), None)


def path_of(world: "World", callsign: str) -> PlannedPath | None:
    if world.plan is None:
        return None
    return next((p for p in world.plan.paths if p.callsign == callsign), None)


def baseline_of(world: "World", callsign: str) -> PlannedPath | None:
    if world.baseline is None:
        return None
    return next((p for p in world.baseline.paths if p.callsign == callsign), None)


def card_of(world: "World", callsign: str) -> InstructionCard | None:
    """The card that explains what this flight is doing: pending first, then the newest issued."""
    mine = [c for c in world.cards.values() if c.callsign == callsign and not c.minor]
    if not mine:
        return None
    rank = {"pending": 0, "spoken": 1, "error": 1, "validated": 2, "verified": 3, "superseded": 4}
    return sorted(mine, key=lambda c: (rank.get(c.status, 5), -world.card_t.get(c.id, 0.0)))[0]


def risk_of(world: "World", callsign: str) -> float:
    return max((float(p.p_max) for p in world.risk.pairs if callsign in (p.a, p.b)), default=0.0)


def _card_result(world: "World", cs: str, card: InstructionCard | None, issue: str | None = None) -> dict[str, Any]:
    st = state_of(world, cs)
    if st is None:
        return {"error": f"{cs} is not in the sector", "summary": f"{cs} is not in the sector"}
    path, base = path_of(world, cs), baseline_of(world, cs)
    ac = CD.aircraft_card(st, path, card, base, issue=issue)
    out: dict[str, Any] = {
        "callsign": cs, "reason": card.reason if card else None, "cause": card.cause if card else None,
        "phrase": card.phrase if card else None, "status": card.status if card else None,
        "cost": ac.cost, "runner_up_cost": ac.runner_up_cost,
        "confidence": card.confidence if card else None, "risk_after": card.risk_after if card else None,
        "margin": ac.card.margin if ac.card else CD._margin(path), "changes": ac.changes, "extra_nm": ac.extra_nm,
        "risk_now": round(risk_of(world, cs), 3), "card": ac,
    }
    if card is not None:
        out["summary"] = (f"{cs}: {card.reason}; cost {ac.cost} vs runner-up {ac.runner_up_cost}, "
                          f"confidence {card.confidence}, risk after {card.risk_after}")
    else:
        out["summary"] = f"{cs}: no instruction card; on plan, {len(ac.changes)} change(s), cost {ac.cost}"
    return out


# ------------------------------------------------------------------------------------ world.*


@tool("world.set_speed", "Set the clock speed in sim seconds per real second (0.25 to 120).",
      {"speed": {"type": "number"}}, required=["speed"])
def _set_speed(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    prev = world.speed
    world.set_speed(float(a.get("speed", 1.0)))
    return {"speed": world.speed, "summary": f"speed {world.speed:g}x",
            "undo": {"tool": "world.set_speed", "args": {"speed": prev}}}


@tool("world.set_voice", "Voice on: the controller says each card and the pilot reads it back. Off: Tower sends every card by data link at once.",
      {"on": {"type": "boolean"}}, required=["on"])
def _set_voice(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    prev = not world.auto_speak
    on = bool(a.get("on", True))
    world.set_voice(on)
    return {"voice": on, "summary": f"voice {'on' if on else 'off'}",
            "undo": {"tool": "world.set_voice", "args": {"on": prev}}}


@tool("world.set_sliders", "Change the separation buffer (NM, on top of the 5 NM minimum), the pilot error rate (0 to 1) and the radio noise (0 to 1). Any subset.",
      {"buffer_nm": {"type": "number"}, "error_rate": {"type": "number"}, "noise": {"type": "number"}})
def _set_sliders(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    prev = {"buffer_nm": world.buffer_nm, "error_rate": world.error_rate, "noise": world.noise}
    world.set_sliders(a.get("buffer_nm"), a.get("error_rate"), a.get("noise"))
    now = {"buffer_nm": world.buffer_nm, "error_rate": world.error_rate, "noise": world.noise}
    return {**now, "summary": f"buffer {now['buffer_nm']:g} NM, error rate {now['error_rate']:g}, noise {now['noise']:g}",
            "undo": {"tool": "world.set_sliders", "args": prev}}


@tool("world.load", "Load a scenario by name (demo, intruder, dense, real/<region>_<date>_<hhmm>) at a traffic density.",
      {"name": {"type": "string"}, "density": {"type": "number", "description": "1 is as written; 2 doubles it"}},
      required=["name"])
def _load(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    from sim import scenarios as SC

    prev = world.scenario.name if world.scenario else None
    name = str(a.get("name") or "demo")
    if name not in SC.list_scenarios():
        close = [s for s in SC.list_scenarios() if name.lower() in s.lower()]
        if len(close) == 1:
            name = close[0]
        else:
            return {"error": f"unknown scenario {name}", "summary": f"unknown scenario {name}; have {', '.join(SC.list_scenarios())}"}
    world.load(name)
    density = float(a.get("density") or 1.0)
    if abs(density - 1.0) > 1e-6 and not name.startswith("real/"):
        world.tool_multiply_traffic(density)
    n = len(world.scenario.flights) if world.scenario else 0
    conflicts = world.plan.conflicts if world.plan else 0
    return {"scenario": name, "flights": n, "conflicts": conflicts,
            "summary": f"loaded {name}: {n} flights, {conflicts} conflicts",
            "undo": ({"tool": "world.load", "args": {"name": prev}} if prev else None)}


@tool("world.disrupt", "Drop a disruption: storm, closed, rocket, fighter, drone, balloon, unknown, emergency, or random. Give a target callsign to put it ahead of that flight, or x_nm/y_nm, or nothing for Tower's choice.",
      {"kind": {"type": "string"}, "target": {"type": "string", "description": "callsign"},
       "x_nm": {"type": "number"}, "y_nm": {"type": "number"}}, required=["kind"])
def _disrupt(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    if err := _no_scenario(world):
        return err
    target = find_callsign(world, a.get("target")) if a.get("target") else None
    if a.get("target") and target is None:
        return {"error": f"{a['target']} is not in the sector", "summary": f"{a['target']} is not in the sector"}
    x, y = a.get("x_nm"), a.get("y_nm")
    d = world.add_disruption(str(a.get("kind") or "random"), float(x) if x is not None else None,
                             float(y) if y is not None else None, target=target)
    if d is None:
        return {"error": f"could not add {a.get('kind')}", "summary": f"could not add {a.get('kind')}: see the notice"}
    changed = sorted({c.callsign for c in world.cards.values() if c.cause == d.id})
    return {"id": d.id, "kind": d.kind, "label": d.label, "x_nm": round(d.x_nm, 1), "y_nm": round(d.y_nm, 1),
            "rerouted": changed,
            "summary": f"{d.label} {d.id} at ({d.x_nm:.0f}, {d.y_nm:.0f}); {len(changed)} rerouted"
                       + (f": {', '.join(changed[:4])}" if changed else ""),
            "undo": {"tool": "world.remove_disruption", "args": {"id": d.id}}}


@tool("world.remove_disruption", "Take a disruption out by id. Flights that went around it are planned again without it.",
      {"id": {"type": "string"}}, required=["id"])
def _remove(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    did = str(a.get("id") or "")
    if not did and world.disruptions:
        did = list(world.disruptions)[-1]
    d = world.disruptions.get(did)
    if d is None:
        return {"error": f"no active disruption {did}", "summary": f"no active disruption {did or '(none)'}"}
    world.remove_disruption(did)
    return {"id": did, "summary": f"removed {d.label} {did}",
            "undo": {"tool": "world.disrupt", "args": {"kind": d.kind, "x_nm": d.x_nm, "y_nm": d.y_nm}}}


@tool("world.lifecycle", "Start, pause or reset the simulation.",
      {"action": {"type": "string", "enum": list(LIFECYCLE_ACTIONS)}}, required=["action"])
def _lifecycle(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    action = str(a.get("action") or "start")
    if action not in LIFECYCLE_ACTIONS:
        return {"error": f"unknown action {action}", "summary": f"unknown action {action}"}
    ok = {"start": world.start, "pause": world.pause, "reset": world.reset}[action]()
    if not ok:
        why = "load a scenario first" if world.scenario is None else f"cannot {action} while {world.lifecycle}"
        return {"error": why, "lifecycle": world.lifecycle, "summary": f"{action}: {why}"}
    return {"lifecycle": world.lifecycle, "summary": f"{action}: now {world.lifecycle}"}


@tool("world.multiply_traffic", "Scale the traffic by a factor: 2 doubles it. Rebuilds the world and its plan.",
      {"factor": {"type": "number"}}, required=["factor"])
def _multiply(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    if err := _no_scenario(world):
        return err
    f = float(a.get("factor") or 2.0)
    if not (0.1 <= f <= 10.0):
        return {"error": "factor must be between 0.1 and 10", "summary": "factor out of range"}
    world.tool_multiply_traffic(f)
    n = len(world.scenario.flights)  # type: ignore[union-attr]
    return {"factor": f, "flights": n, "conflicts": world.plan.conflicts if world.plan else 0,
            "summary": f"traffic x{f:g}: {n} flights, {world.plan.conflicts if world.plan else 0} conflicts",
            "undo": {"tool": "world.multiply_traffic", "args": {"factor": 1.0 / f}}}


@tool("world.spawn_flight", "Add one flight entering from a side of the sector.",
      {"airline": {"type": "string", "description": "ICAO prefix: ACA, WJA, DAL, UAL, AAL, DLH, BAW"},
       "from_side": {"type": "string", "enum": ["east", "west", "north", "south"]}, "alt_ft": {"type": "number"}})
def _spawn(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    if err := _no_scenario(world):
        return err
    text = world.tool_spawn_flight(str(a.get("airline") or "ACA"), str(a.get("from_side") or "east"),
                                   float(a.get("alt_ft") or 33000))
    cs = text.split()[1] if text.startswith("spawned") else None
    return {"callsign": cs, "summary": text}


@tool("world.nudge", "Ask the planner to re-plan ONE flight with a temporarily bigger buffer (+2 NM), for example to separate the closest pair. Produces an instruction card; never a clearance.",
      {"callsign": {"type": "string"}}, required=["callsign"], acts_on_traffic=True)
def _nudge(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    if err := _no_scenario(world):
        return err
    cs = find_callsign(world, a.get("callsign"))
    if cs is None:
        return {"error": f"{a.get('callsign')} is not in the sector", "summary": f"{a.get('callsign')} is not in the sector"}
    before = {c.id for c in world.cards.values()}
    saved = world.buffer_nm
    world.buffer_nm = saved + NUDGE_EXTRA_NM
    try:
        changed = world._replan(f"nudge {cs}", repin={cs})  # noqa: SLF001 - the planner's own repair path
    finally:
        world.buffer_nm = saved
    new = [c for c in world.cards.values() if c.id not in before and c.callsign == cs and not c.minor]
    card = new[-1] if new else None
    out = _card_result(world, cs, card)
    if card is None:
        out["summary"] = f"nudged {cs} with +{NUDGE_EXTRA_NM:g} NM: the planner kept its path" + (
            f" (replanned {', '.join(changed)})" if changed else "")
    else:
        out["summary"] = f"nudged {cs} with +{NUDGE_EXTRA_NM:g} NM: {card.phrase} ({card.reason})"
    out["changed"] = changed
    return out


# --------------------------------------------------------------------------------------- ui.*


def _ui(world: "World", command: str, args: dict[str, Any]) -> dict[str, Any]:
    world.emit(event("ui_command", {"command": command, "args": args}, t=world.sim.t))
    return {"ok": True, "command": command, "args": args}


@tool("ui.focus", "Fly the camera to one aircraft and select it.", {"callsign": {"type": "string"}}, required=["callsign"])
def _focus(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    cs = find_callsign(world, a.get("callsign"))
    if cs is None:
        return {"error": f"{a.get('callsign')} is not in the sector", "summary": f"{a.get('callsign')} is not in the sector"}
    return {**_ui(world, "focus", {"callsign": cs}), "summary": f"focused {cs}"}


@tool("ui.follow", "Keep the camera on one aircraft as it moves. Empty callsign stops following.",
      {"callsign": {"type": "string"}})
def _follow(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    if not a.get("callsign"):
        return {**_ui(world, "follow", {"callsign": None}), "summary": "stopped following"}
    cs = find_callsign(world, a.get("callsign"))
    if cs is None:
        return {"error": f"{a.get('callsign')} is not in the sector", "summary": f"{a.get('callsign')} is not in the sector"}
    return {**_ui(world, "follow", {"callsign": cs}), "summary": f"following {cs}"}


@tool("ui.camera", "Move the map camera: pitch (degrees, 0 is flat), bearing (degrees), vertical exaggeration, or top_down.",
      {"pitch": {"type": "number"}, "bearing": {"type": "number"}, "exaggeration": {"type": "number"},
       "top_down": {"type": "boolean"}})
def _camera(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    args = {k: a[k] for k in ("pitch", "bearing", "exaggeration", "top_down") if a.get(k) is not None}
    if a.get("top_down"):
        args["pitch"] = 0
    return {**_ui(world, "camera", args), "summary": "camera " + ", ".join(f"{k} {v}" for k, v in args.items())}


@tool("ui.line_view", "Which planned lines to draw: today (fixed routes), tower (the plan), both, or changed only.",
      {"view": {"type": "string", "enum": list(LINE_VIEWS)}}, required=["view"])
def _line_view(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    v = str(a.get("view") or "both")
    if v not in LINE_VIEWS:
        return {"error": f"unknown view {v}", "summary": f"unknown view {v}"}
    return {**_ui(world, "line_view", {"view": v}), "summary": f"lines: {v}"}


@tool("ui.panel", "Open a panel or drawer tab on the screen: scoreboard, cards, transcript, setup, alerts, risk, or none.",
      {"panel": {"type": "string"}}, required=["panel"])
def _panel(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    p = str(a.get("panel") or "none")
    return {**_ui(world, "panel", {"panel": p}), "summary": f"panel: {p}"}


@tool("ui.mode", "Switch the screen between normal (hand-laid-out panels) and agent (a stage squack composes).",
      {"mode": {"type": "string", "enum": list(UI_MODES)}}, required=["mode"])
def _mode(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    m = str(a.get("mode") or "normal")
    if m not in UI_MODES:
        return {"error": f"unknown mode {m}", "summary": f"unknown mode {m}"}
    world.set_ui_mode(m)
    return {**_ui(world, "mode", {"mode": m}), "summary": f"mode: {m}"}


# ------------------------------------------------------------------------------------ query.*

_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": lambda x, v: x == v, "ne": lambda x, v: x != v,
    "gt": lambda x, v: x is not None and x > v, "gte": lambda x, v: x is not None and x >= v,
    "lt": lambda x, v: x is not None and x < v, "lte": lambda x, v: x is not None and x <= v,
    "contains": lambda x, v: str(v).lower() in str(x).lower(),
}
_FIELDS = ["callsign", "alt_ft", "target_alt_ft", "hdg_deg", "gs_kt", "route", "actype", "is_intruder", "threat", "x_nm", "y_nm"]


def _heading_band(value: Any) -> tuple[float, float] | None:
    bands = {"north": (315, 45), "east": (45, 135), "south": (135, 225), "west": (225, 315)}
    return bands.get(str(value).lower())


def _match(row: dict[str, Any], f: dict[str, Any]) -> bool:
    field_, op, value = str(f.get("field", "")), str(f.get("op", "eq")), f.get("value")
    if field_ == "heading" and (band := _heading_band(value)):
        h = float(row.get("hdg_deg", 0.0)) % 360
        lo, hi = band
        return (lo <= h < hi) if lo < hi else (h >= lo or h < hi)
    if field_ in ("fl", "level"):
        field_, value = "alt_ft", float(value) * 100
    x = row.get(field_)
    if isinstance(x, list):
        x = " ".join(map(str, x))
    fn = _OPS.get(op)
    if fn is None:
        return False
    try:
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            value = float(value)
        return bool(fn(x, value))
    except (TypeError, ValueError):
        return False


@tool("query.aircraft", "List aircraft matching filters. Fields: callsign, alt_ft, hdg_deg, gs_kt, route, actype, is_intruder, threat; also `fl` (flight level) and `heading` with a compass value (north/east/south/west). Ops: eq, ne, gt, gte, lt, lte, contains.",
      {"filters": {"type": "array", "items": {"type": "object", "properties": {
          "field": {"type": "string"}, "op": {"type": "string"}, "value": {}}, "required": ["field", "op", "value"]}}})
def _q_aircraft(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    rows_all = [s.model_dump() for s in world.sim.aircraft()]
    filters = a.get("filters") or []
    rows = [r for r in rows_all if all(_match(r, f) for f in filters)]
    table = CD.TableCard(title="Aircraft" if not filters else f"Aircraft ({len(rows)} of {len(rows_all)})",
                         columns=["callsign", "FL", "hdg", "kt", "route"],
                         rows=[[r["callsign"], int(round(r["alt_ft"] / 100)), int(round(r["hdg_deg"])) % 360,
                                int(round(r["gs_kt"])), " ".join(r.get("route") or [])] for r in rows[:40]],
                         focus_column=0)
    return {"count": len(rows), "total": len(rows_all), "callsigns": [r["callsign"] for r in rows],
            "rows": [{k: r.get(k) for k in _FIELDS} for r in rows[:40]], "card": table,
            "summary": f"aircraft_list: {len(rows_all)} rows" + (f", filtered: {len(rows)}" if filters else "")}


def pairs_now(world: "World", max_nm: float | None = None, include_intruders: bool = True) -> list[dict[str, Any]]:
    """Every pair by distance right now, with the monitor's closest so far and the predicted risk."""
    states = [s for s in world.sim.aircraft() if include_intruders or not s.is_intruder]
    risk = {frozenset((p.a, p.b)): float(p.p_max) for p in world.risk.pairs}
    out = []
    for i in range(len(states)):
        for j in range(i + 1, len(states)):
            s, t = states[i], states[j]
            if s.is_intruder and t.is_intruder:
                continue
            nm = math.hypot(s.x_nm - t.x_nm, s.y_nm - t.y_nm)
            if max_nm is not None and nm > max_nm:
                continue
            key = (s.callsign, t.callsign) if s.callsign < t.callsign else (t.callsign, s.callsign)
            closest = world.monitor.closest.get(key)
            out.append({"a": key[0], "b": key[1], "nm": round(nm, 1), "ft": int(round(abs(s.alt_ft - t.alt_ft))),
                        "closest_so_far_nm": (round(closest[0], 1) if closest else None),
                        "p_los": round(risk.get(frozenset(key), 0.0), 3)})
    out.sort(key=lambda r: (r["nm"], -r["p_los"]))
    return out


@tool("query.pairs", "Pairs of aircraft within max_nm of each other right now, closest first, with vertical separation, the closest they have been, and the predicted probability of losing separation.",
      {"max_nm": {"type": "number", "default": PAIRS_DEFAULT_NM}})
def _q_pairs(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    max_nm = float(a.get("max_nm") or PAIRS_DEFAULT_NM)
    rows = pairs_now(world, max_nm)
    if not rows:  # nothing that close: still answer the closest pair there is
        rows = pairs_now(world, None)[:1]
        title = f"No pairs within {max_nm:g} NM; closest"
    else:
        title = f"Pairs within {max_nm:g} NM"
    table = CD.TableCard(title=title, columns=["a", "b", "NM", "ft", "closest NM", "p(LoS)"],
                         rows=[[r["a"], r["b"], r["nm"], r["ft"], r["closest_so_far_nm"], r["p_los"]] for r in rows[:20]],
                         focus_column=0)
    top = rows[0] if rows else None
    return {"count": len(rows), "pairs": rows[:20], "closest": top, "card": table,
            "summary": (f"{len(rows)} pair(s); closest {top['a']}/{top['b']} at {top['nm']} NM, {top['ft']} ft"
                        if top else "fewer than two aircraft")}


@tool("query.cards", "Instruction cards by status: pending, spoken, validated, verified, error, superseded, or all.",
      {"status": {"type": "string", "default": "pending"}})
def _q_cards(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    status = str(a.get("status") or "pending")
    cards = [c for c in world.cards.values() if not c.minor and (status == "all" or c.status == status)]
    cards.sort(key=lambda c: world.card_t.get(c.id, 0.0))
    rows = [[c.callsign, c.status, c.phrase, c.reason, c.confidence] for c in cards[:30]]
    table = CD.TableCard(title=f"Cards: {status} ({len(cards)})", columns=["callsign", "status", "phrase", "reason", "conf"],
                         rows=rows, focus_column=0)
    return {"count": len(cards), "cards": [{"id": c.id, "callsign": c.callsign, "status": c.status, "phrase": c.phrase,
                                            "reason": c.reason, "cause": c.cause, "confidence": c.confidence}
                                           for c in cards[:30]],
            "card": table, "summary": f"{len(cards)} card(s) {status}"}


@tool("query.log", "What was said to and by one aircraft: the last n exchanges on frequency. `text` ranks them by similarity when the memory is on Elasticsearch.",
      {"callsign": {"type": "string"}, "n": {"type": "integer", "default": 5}, "text": {"type": "string"}},
      required=["callsign"])
def _q_log(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    cs = find_callsign(world, a.get("callsign")) or str(a.get("callsign") or "").upper()
    n = int(a.get("n") or 5)
    source = world.memory.label
    rows = world.memory.history(cs, n, query=a.get("text") or None)
    if rows is None:
        source = "in-memory"
        rows = [{"speaker": ex.transmission.speaker, "text": ex.transmission.text_norm, "t": ex.transmission.t_end,
                 "clearance_id": ex.clearance_id} for ex in world.core.store.history(cs, n)]
    items = [CD.ListItem(t=r.get("t"), text=f"{r.get('speaker')}: {r.get('text')}", kind="transcript") for r in rows]
    return {"callsign": cs, "source": source, "exchanges": rows, "card": CD.ListCard(title=f"{cs} on frequency", items=items),
            "summary": f"{len(rows)} exchange(s) with {cs}" + (f" [{source}]" if source != "in-memory" else "")}


@tool("query.scoreboard", "The live scoreboard: miles saved, losses of separation, closest approach, errors caught, reaction time, conflicts predicted and resolved.")
def _q_scoreboard(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    sb = world.scoreboard().model_dump()
    return {**sb, "card": CD.scoreboard_card(sb),
            "summary": (f"miles saved {sb['miles_saved']}, LoS {sb['losses_of_separation']}, "
                        f"closest {sb['closest_approach_nm']} NM, caught {sb['errors_caught']}/{sb['errors_injected']}")}


def describe_event(ev: dict[str, Any]) -> str | None:
    """One line per event for the timeline. None: not worth a line."""
    typ, p = ev.get("type"), ev.get("payload") or {}
    if typ == "disruption":
        return f"{p.get('label') or p.get('kind')} {p.get('id')} {'appeared' if p.get('active', True) else 'ended'}"
    if typ == "plan_update":
        ch = p.get("changed") or []
        return f"replan ({p.get('trigger')}): {len(ch)} flight(s) {', '.join(ch[:5])}".rstrip(": ")
    if typ == "alert":
        return f"alert {p.get('callsign')}: {p.get('reason') or p.get('result')}"
    if typ == "alert_resolved":
        return f"alert on {p.get('callsign')} resolved in {p.get('seconds')} s"
    if typ == "instruction_card":
        return f"card {p.get('callsign')} {p.get('status')}: {p.get('phrase')}"
    if typ == "transcript":
        return f"{p.get('speaker')} {p.get('callsign') or ''}: {p.get('text_norm') or p.get('text_raw')}".replace("  ", " ")
    if typ == "said_check":
        return f"said-vs-card {p.get('callsign')}: {p.get('detail')}"
    if typ == "notice":
        return str(p.get("text"))
    if typ == "state":
        return f"lifecycle {p.get('lifecycle')}"
    if typ == "answer":
        return f"squack: {p.get('text')}"
    if typ == "agent_step":
        return f"squack {p.get('tool')}: {p.get('result_summary')}"
    if typ == "ui_command":
        return f"screen {p.get('command')} {p.get('args')}"
    if typ == "resolver_step":
        return f"resolver {p.get('tool')}: {p.get('result_summary')}"
    if typ == "clearance_opened":
        return f"clearance to {p.get('callsign')}"
    if typ == "sim_job":
        return f"sim job {p.get('kind')} {p.get('status')}"
    return None


@tool("query.timeline", "What happened: events in order over the last since_s sim seconds (disruptions, replans, alerts, cards, transcripts, squack's own actions). kinds filters by event type.",
      {"since_s": {"type": "number", "default": TIMELINE_DEFAULT_S},
       "kinds": {"type": "array", "items": {"type": "string"}}})
def _q_timeline(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    since = float(a.get("since_s") or TIMELINE_DEFAULT_S)
    kinds = set(a.get("kinds") or [])
    floor = world.sim.t - since
    items: list[CD.ListItem] = []
    last_state = None
    for ev in list(world.events):
        if ev.get("t", 0.0) < floor or (kinds and ev.get("type") not in kinds):
            continue
        if ev.get("type") == "state":  # only lifecycle changes, not every setting
            lc = (ev.get("payload") or {}).get("lifecycle")
            if lc == last_state:
                continue
            last_state = lc
        if ev.get("type") == "instruction_card" and (ev.get("payload") or {}).get("status") not in ("pending", "error"):
            continue
        line = describe_event(ev)
        if line:
            items.append(CD.ListItem(t=ev.get("t"), text=line, kind=ev.get("type")))
    items = items[-60:]
    counts: dict[str, int] = {}
    for it in items:
        counts[it.kind or "?"] = counts.get(it.kind or "?", 0) + 1
    return {"count": len(items), "counts": counts, "items": [i.model_dump() for i in items],
            "card": CD.ListCard(title=f"Last {since:g} s", items=items),
            "summary": f"{len(items)} events: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))}


# ---------------------------------------------------------------------------------- explain.*


@tool("explain.card", "Why this flight got its instruction: the card's reason and cause, the path's changes, cost against the runner-up, confidence and residual risk. All from the planner, nothing invented.",
      {"callsign": {"type": "string"}}, required=["callsign"])
def _e_card(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    if err := _no_scenario(world):
        return err
    cs = find_callsign(world, a.get("callsign"))
    if cs is None:
        return {"error": f"{a.get('callsign')} is not in the sector", "summary": f"{a.get('callsign')} is not in the sector"}
    return _card_result(world, cs, card_of(world, cs))


@tool("explain.flight", "One flight now: level, heading, speed, its planned path and any change from its fixed route, plus its current card if any.",
      {"callsign": {"type": "string"}}, required=["callsign"])
def _e_flight(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    if err := _no_scenario(world):
        return err
    cs = find_callsign(world, a.get("callsign"))
    if cs is None:
        return {"error": f"{a.get('callsign')} is not in the sector", "summary": f"{a.get('callsign')} is not in the sector"}
    out = _card_result(world, cs, card_of(world, cs))
    st = state_of(world, cs)
    if st is not None:
        out["summary"] = (f"{cs}: FL{int(round(st.alt_ft / 100)):03d} hdg {int(round(st.hdg_deg)) % 360:03d} "
                          f"{int(round(st.gs_kt))} kt, {len(out['changes'])} change(s), extra {out['extra_nm']} NM")
    return out


def disruption_effect(world: "World", did: str) -> list[dict[str, Any]]:
    """Flights whose current path exists to clear this disruption, with the miles it costs them."""
    rows = []
    for p in (world.plan.paths if world.plan else []):
        why = [c for c in p.changes if c.endswith(f"to clear {did}")]
        if not why:
            continue
        base = baseline_of(world, p.callsign)
        extra = round(p.distance_nm - base.distance_nm, 1) if base else None
        rows.append({"callsign": p.callsign, "change": why[0], "extra_nm": extra, "cost": round(p.cost, 1)})
    rows.sort(key=lambda r: -(r["extra_nm"] or 0.0))
    return rows


@tool("explain.disruption", "What a disruption cost: the flights rerouted to clear it, the extra miles each flew, and how fast the first one turned. No id means the latest.",
      {"id": {"type": "string"}})
def _e_disruption(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    if err := _no_scenario(world):
        return err
    did = str(a.get("id") or "")
    if not did:
        if not world.disruptions:
            return {"error": "no active disruption", "summary": "no active disruption"}
        did = list(world.disruptions)[-1]
    d = world.disruptions.get(did)
    if d is None:
        return {"error": f"no active disruption {did}", "summary": f"no active disruption {did}"}
    rows = disruption_effect(world, did)
    total = round(sum(r["extra_nm"] or 0.0 for r in rows), 1)
    comp = CD.ComparisonCard(title=f"{d.label} {d.id}: {len(rows)} rerouted, +{total} NM",
                             columns=["flight", "change", "extra NM"],
                             rows=[[r["callsign"], r["change"], r["extra_nm"]] for r in rows],
                             highlight_row=0 if rows else None)
    cards: list[CD.Card] = [comp]
    if rows:
        worst = rows[0]["callsign"]
        st = state_of(world, worst)
        if st is not None:
            cards.append(CD.aircraft_card(st, path_of(world, worst), card_of(world, worst), baseline_of(world, worst),
                                          issue=f"rerouted to clear {d.id}"))
    return {"id": did, "kind": d.kind, "label": d.label, "rerouted": [r["callsign"] for r in rows], "rows": rows,
            "extra_nm_total": total, "reaction_s": world.reaction_s, "card": comp, "cards": cards,
            "summary": f"{d.label} {d.id}: {len(rows)} rerouted, +{total} NM"
                       + (f", first turn in {world.reaction_s} s" if world.reaction_s is not None else "")}


@tool("explain.replan", "The last replan: what triggered it, which flights changed and why each did.")
def _e_replan(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    if err := _no_scenario(world):
        return err
    last = next((ev for ev in reversed(list(world.events)) if ev.get("type") == "plan_update"), None)
    trigger = (last or {}).get("payload", {}).get("trigger") or (world.plan.trigger if world.plan else "initial")
    changed = list((last or {}).get("payload", {}).get("changed") or [])
    items = []
    for cs in changed:
        c = card_of(world, cs)
        p = path_of(world, cs)
        why = c.reason if c else (p.changes[0] if p and p.changes else "on plan")
        items.append(CD.ListItem(t=(last or {}).get("t"), text=f"{cs}: {why}", kind="plan_update"))
    if not items:
        items.append(CD.ListItem(t=world.sim.t, text=f"{trigger}: no flight changed", kind="plan_update"))
    return {"trigger": trigger, "changed": changed, "t": (last or {}).get("t"),
            "conflicts": world.plan.conflicts if world.plan else None,
            "card": CD.ListCard(title=f"Replan: {trigger}", items=items),
            "summary": f"replan ({trigger}): {len(changed)} flight(s) changed" + (f": {', '.join(changed[:5])}" if changed else "")}


# -------------------------------------------------------------------------------------- sim.*
# A3's runner: tools/simjobs.py. A subprocess per job, one at a time, progress over a pipe. Its
# payloads go out as `sim_job` events from the callback below; when a job finishes the same
# callback also emits an `answer` (for: "event") carrying the chart or comparison card, so the bar
# shows the result without the agent polling.

try:
    from tools.simjobs import cancel_job, job_status, start_job
    SIM_JOBS = True
except Exception:  # noqa: BLE001 - pragma: no cover
    SIM_JOBS = False

    def start_job(kind: str, params: dict[str, Any] | None, on_progress: Callable[[dict[str, Any]], None]) -> str:  # type: ignore[misc]
        raise RuntimeError("sim jobs are not available: tools/simjobs.py is missing")

    def job_status(job_id: str) -> dict[str, Any]:  # type: ignore[misc]
        raise RuntimeError("sim jobs are not available: tools/simjobs.py is missing")

    def cancel_job(job_id: str) -> None:  # type: ignore[misc]
        raise RuntimeError("sim jobs are not available: tools/simjobs.py is missing")


def _progress_emitter(world: "World") -> Callable[[dict[str, Any]], None]:
    def on_progress(p: dict[str, Any]) -> None:
        world.emit_from_thread(event("sim_job", p, t=world.sim.t))
        if p.get("status") == "done" and p.get("result"):
            card = CD.sim_result_card(str(p.get("kind")), p["result"], p.get("params") or {})
            text = str(p["result"].get("caption") or f"{p.get('kind')} job {p.get('job_id')} finished")
            world.emit_from_thread(event("answer", {"turn_id": f"job-{p.get('job_id')}", "text": text,
                                                    "cards": [CD.dump(card)], "for": "event", "steps": []}, t=world.sim.t))
        elif p.get("status") in ("failed", "cancelled"):
            world.emit_from_thread(event("answer", {"turn_id": f"job-{p.get('job_id')}", "for": "event", "steps": [],
                                                    "text": f"{p.get('kind')} job {p.get('status')}"
                                                            + (f": {p.get('error')}" if p.get("error") else "."),
                                                    "cards": []}, t=world.sim.t))
    return on_progress


def _sim_params(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    return {"scenario": str(a.get("scenario") or (world.scenario.name if world.scenario else "demo")),
            "error_rate": float(a["error_rate"]) if a.get("error_rate") is not None else world.error_rate,
            "buffer_nm": float(a["buffer_nm"]) if a.get("buffer_nm") is not None else world.buffer_nm}


def _started(world: "World", job_id: str, what: str) -> dict[str, Any]:
    st = job_status(job_id)
    notice = st.get("notice")
    eta = st.get("eta_s")
    summary = (notice if notice and st.get("kind") != what.split()[0] else
               f"running {what} in the background (job {job_id})" + (f", about {eta:.0f} s" if eta else ""))
    return {**st, "summary": summary}


@tool("sim.montecarlo", "Run the Monte Carlo safety evaluation in the background (three arms: fixed routes, squack off, squack on). Returns the job id; sim_job events carry progress, and the result lands as a card. Default 8 runs, at most 20.",
      {"runs": {"type": "integer"}, "density": {"type": "number"}, "error_rate": {"type": "number"},
       "buffer_nm": {"type": "number"}, "scenario": {"type": "string"}})
def _s_montecarlo(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    params = {**_sim_params(world, a), "runs": int(min(MC_RUNS_MAX, max(1, int(a.get("runs") or MC_RUNS_DEFAULT)))),
              "density": float(a.get("density") or (world.scenario.traffic_multiplier if world.scenario else 1.0))}
    job_id = start_job("montecarlo", params, _progress_emitter(world))
    return _started(world, job_id, f"montecarlo {params['runs']} runs at {params['density']:g}x")


@tool("sim.sweep", "Sweep traffic density (and optionally the buffer) in the background: at most 4 densities. Returns the job id; the result lands as a chart card.",
      {"densities": {"type": "array", "items": {"type": "number"}}, "buffers": {"type": "array", "items": {"type": "number"}},
       "runs": {"type": "integer"}, "scenario": {"type": "string"}})
def _s_sweep(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    dens = [float(x) for x in (a.get("densities") or [1.0, 1.5, 2.0])][:SWEEP_POINTS_MAX]
    params = {**_sim_params(world, a), "densities": dens, "runs": int(a.get("runs") or 2)}
    if a.get("buffers"):
        params["buffers"] = [float(x) for x in a["buffers"]][:2]
    else:
        params.pop("buffer_nm", None)
    job_id = start_job("sweep", params, _progress_emitter(world))
    return _started(world, job_id, f"sweep of {len(dens)} densities x {params['runs']} runs")


@tool("sim.status", "Progress or result of a background sim job. No id means the current one.", {"job_id": {"type": "string"}})
def _s_status(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    jid = str(a.get("job_id") or "")
    if not jid:
        from tools.simjobs import current_job
        cur = current_job()
        if cur is None:
            return {"status": "idle", "summary": "no sim job running"}
        jid = cur["job_id"]
    st = job_status(jid)
    out: dict[str, Any] = {**st}
    if st.get("status") == "done" and st.get("result"):
        out["card"] = CD.sim_result_card(str(st.get("kind")), st["result"], st.get("params") or {})
        out["summary"] = str(st["result"].get("caption") or "done")
    else:
        pct = f"{float(st.get('progress') or 0.0):.0%}"
        out["summary"] = f"job {jid}: {st.get('status')} {pct}" + (f", {st.get('error')}" if st.get("error") else "")
    return out


@tool("sim.cancel", "Cancel a background sim job.", {"job_id": {"type": "string"}}, required=["job_id"])
def _s_cancel(world: "World", a: dict[str, Any]) -> dict[str, Any]:
    cancel_job(str(a.get("job_id") or ""))
    return {"job_id": a.get("job_id"), "summary": f"cancelling job {a.get('job_id')}"}


__all__ = ["REGISTRY", "Tool", "execute", "schemas", "names", "summarize", "resolve_name", "find_callsign",
           "pairs_now", "disruption_effect", "describe_event", "card_of", "state_of", "path_of", "baseline_of",
           "REPLAN_P", "SHOW_P", "SIM_JOBS"]
