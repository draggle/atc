"""The world-builder agent: the supervisor talks to it, it changes the world.

It can load scenarios, add flights, drop intruders and storms, multiply traffic, and change
settings. It can never issue a clearance: anything a plane does goes over the radio.

With BASETEN_API_KEY set it is a real tool-calling loop (max 3 calls). Without a key it is a
deterministic keyword parser over the same tool set, so the demo works offline.

Since the squack agent (backend/agent/, TRD 08) `handle` is a thin wrapper over that loop and
its registry. The original `_llm_agent` and `_keyword_agent` are kept below for reference and
for tests that drive them directly; nothing in the app calls them any more.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any

from tower.llm import LLM, MockLLM, get_llm

if TYPE_CHECKING:
    from world import World

SYSTEM = """You are Tower's world builder for an air traffic control simulator. The user is a
supervisor setting up or changing the airspace by voice. Use the tools to do what they ask, then
answer in one short plain-English sentence. You cannot issue instructions to aircraft; those go
over the radio. Coordinates are nautical miles, x east and y north, from -100 to +100 with the sector centred on 0."""

TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {"name": "load_scenario", "description": "Load a named scenario (demo, intruder, dense).",
     "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "spawn_flight", "description": "Add one flight entering from a side of the sector.",
     "parameters": {"type": "object", "properties": {"airline": {"type": "string", "description": "ICAO prefix, e.g. ACA, WJA, POE, DAL, UAL, AAL, JZA"},
                                                     "from_side": {"type": "string", "enum": ["east", "west", "north", "south"]},
                                                     "alt_ft": {"type": "number"}}, "required": ["airline", "from_side"]}}},
    {"type": "function", "function": {"name": "add_disruption",
     "description": "Drop a disruption. Leave x_nm and y_nm out to put it where it will matter. 'random' picks the kind too.",
     "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": [
         "fighter", "drone", "balloon", "emergency", "unknown", "storm", "closed", "rocket", "random"]},
                                                     "x_nm": {"type": "number"}, "y_nm": {"type": "number"}}, "required": ["kind"]}}},
    {"type": "function", "function": {"name": "multiply_traffic", "description": "Scale the traffic by a factor, e.g. 2 doubles it.",
     "parameters": {"type": "object", "properties": {"factor": {"type": "number"}}, "required": ["factor"]}}},
    {"type": "function", "function": {"name": "set", "description": "Change settings.",
     "parameters": {"type": "object", "properties": {"tower": {"type": "boolean"}, "auto_speak": {"type": "boolean"},
                                                     "error_rate": {"type": "number"}, "noise": {"type": "number"},
                                                     "buffer_nm": {"type": "number"}}}}},
    {"type": "function", "function": {"name": "describe", "description": "Describe the current airspace and scoreboard.",
     "parameters": {"type": "object", "properties": {}}}},
]

AIRLINES = {"air canada": "ACA", "canada": "ACA", "westjet": "WJA", "west jet": "WJA", "porter": "POE",
            "jazz": "JZA", "delta": "DAL", "united": "UAL", "american": "AAL", "lufthansa": "DLH",
            "speedbird": "BAW", "british": "BAW"}


async def handle(world: "World", text: str, history: list[dict[str, Any]] | None = None,
                 ui_state: dict[str, Any] | None = None) -> tuple[str, list[str]]:
    """The headset and the command bar share one brain: backend/agent/loop.py. Returns the old
    (reply, actions) pair; the loop itself emits `agent_step` and `answer`."""
    world._remember_loop()  # noqa: SLF001 - so the loop's events reach the socket from the worker thread
    agent = world.agent()
    ans = await asyncio.to_thread(agent.handle_message, text, history, ui_state)
    return ans.text, ans.actions


def _run_tool(world: "World", name: str, args: dict[str, Any]) -> str:
    if name == "load_scenario":
        return world.tool_load_scenario(str(args.get("name", "demo")))
    if name == "spawn_flight":
        return world.tool_spawn_flight(str(args.get("airline", "ACA")), str(args.get("from_side", "east")),
                                       float(args.get("alt_ft", 33000)))
    if name == "add_disruption":
        return world.tool_add_disruption(str(args.get("kind", "intruder")), args.get("x_nm"), args.get("y_nm"))
    if name == "multiply_traffic":
        return world.tool_multiply_traffic(float(args.get("factor", 2)))
    if name == "set":
        return world.tool_set(args.get("tower"), args.get("auto_speak"), args.get("error_rate"),
                              args.get("noise"), args.get("buffer_nm"))
    if name == "describe":
        return world.tool_describe()
    return f"unknown tool {name}"


def _llm_agent(world: "World", llm: LLM, text: str) -> tuple[str, list[str]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}]
    actions: list[str] = []
    for _ in range(3):
        resp = llm.chat(messages, tools=TOOLS, tool_choice="auto")
        if not resp.tool_calls:
            return (resp.content or "Done."), actions
        messages.append(resp.as_message())
        for tc in resp.tool_calls:
            result = _run_tool(world, tc.name, tc.arguments)
            actions.append(f"{tc.name}: {result}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps({"result": result})})
    final = llm.chat(messages)
    return (final.content or "; ".join(actions) or "Done."), actions


def _keyword_agent(world: "World", text: str) -> tuple[str, list[str]]:
    """Deterministic intent parser over the same tools. Good enough for the demo phrases."""
    t = text.lower().strip()
    actions: list[str] = []
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", t)]

    def do(name: str, **args: Any) -> None:
        actions.append(f"{name}: {_run_tool(world, name, args)}")

    if re.search(r"\b(load|open|start|reset)\b", t) or "scenario" in t:
        name = next((s for s in ("intruder", "dense", "demo") if s in t), "demo")
        do("load_scenario", name=name)
    if re.search(r"\b(double|twice|times two|x2|2x)\b", t):
        do("multiply_traffic", factor=2.0)
    elif re.search(r"\b(triple|times three|3x)\b", t):
        do("multiply_traffic", factor=3.0)
    elif m := re.search(r"(?:times|x)\s*(\d+(?:\.\d+)?)", t):
        do("multiply_traffic", factor=float(m.group(1)))
    for pattern, kind in ((r"\b(fighter|jet|intruder|bogey)\b", "fighter"), (r"\bdrone\b", "drone"),
                          (r"\bballoon\b", "balloon"), (r"\b(mayday|emergency|engine failure)\b", "emergency"),
                          (r"\b(unknown|unidentified|ufo)\b", "unknown"), (r"\b(storm|weather|thunder|cell)\b", "storm"),
                          (r"\b(closed|restricted|military area|exercise)\b", "closed"), (r"\b(rocket|launch)\b", "rocket"),
                          (r"\b(random|surprise|anything)\b", "random")):
        if re.search(pattern, t):
            do("add_disruption", kind=kind)
    airline = next((code for word, code in AIRLINES.items() if word in t), None)
    if airline or re.search(r"\b(add|bring|place|put|spawn)\b.*\b(flight|plane|aircraft|arrival)\b", t):
        side = next((s for s in ("east", "west", "north", "south") if s in t), "east")
        alt = next((n * 100 for n in nums if 100 <= n <= 450), 33000.0)
        do("spawn_flight", airline=airline or "ACA", from_side=side, alt_ft=alt)
    if re.search(r"\btower (off|disable)|turn (off|tower off)|disable tower\b", t):
        do("set", tower=False)
    elif re.search(r"\btower (on|enable)|turn (on|tower on)|enable tower\b", t):
        do("set", tower=True)
    if re.search(r"\b(auto[- ]?speak|speak (the )?cards|you (speak|talk)|handle the radio)\b", t):
        do("set", auto_speak=not re.search(r"\b(off|stop|disable)\b", t))
    if m := re.search(r"error rate\D*(\d+(?:\.\d+)?)", t):
        v = float(m.group(1))
        do("set", error_rate=v / 100 if v > 1 else v)
    if m := re.search(r"noise\D*(\d+(?:\.\d+)?)", t):
        v = float(m.group(1))
        do("set", noise=v / 100 if v > 1 else v)
    if m := re.search(r"buffer\D*(\d+(?:\.\d+)?)", t):
        do("set", buffer_nm=float(m.group(1)))
    if not actions or re.search(r"\b(what|describe|status|how many|where)\b", t):
        do("describe")
    reply = " ".join(a.split(": ", 1)[1] for a in actions)
    return (reply[0].upper() + reply[1:] if reply else "Done."), actions
