"""The squack agent: one sentence in, tool calls over the world and the screen, one sentence and
cards out. PRD sections 3 and 4.

`SquackAgent(world, llm)` generalises `world_agent._llm_agent`: the same `LLM.chat` with tools on
the resolver model (Baseten, hard rule 5), the registry in `agent/registry.py` instead of six
hand-wired tools, and caps like the resolver's: 4 tool calls, 8 s, a short output. Every tool
call goes out as an `agent_step` and every turn ends in exactly one `answer` (never silent). The
agent never opens a clearance, never touches the planner and never starts a simulation: it talks,
it reads, and it changes the world and the screen the way the controls on screen do.

Without a Baseten key the same tools are reached by a keyword router, so the demo lines that
matter (focus, follow, why, scoreboard, closest pair, double traffic, load, storm, speed, voice)
work offline, and anything else says "I need a model for that".
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from agent import cards as CD
from agent import registry as R
from schemas import event
from tower.llm import MockLLM

if TYPE_CHECKING:
    from world import World

log = logging.getLogger("tower.agent")

MAX_TOOL_CALLS = 4
BUDGET_S = 8.0
MAX_TOKENS = 160  # one sentence, two at most: the prompt asks for it and this enforces it
HISTORY_TURNS = 10
MAX_CARDS = 3
MAX_TEXT_CHARS = 1500  # the token cap is the real one; this only guards the wire
NEEDS_MODEL = "I need a model for that."
MODEL_DOWN = "I can act on that, but I cannot talk it through right now."

SYSTEM = """You are squack, the controller's colleague on an air traffic control simulator, talking from the \
command bar. What you can do: change the world (load a scenario, add or remove a disruption, clock speed, \
voice on or off, the sliders, start/pause/reset, more traffic, spawn a flight); change the screen (focus, \
follow, camera, which lines are drawn, open a panel); read live state (aircraft, close pairs, instruction \
cards, the radio log, the scoreboard, the timeline); and explain the planner's own reasons (a card's reason \
and cause, cost against the runner-up, confidence, residual risk). What you cannot do: talk to aircraft or \
issue a clearance, re-plan or move a flight yourself, or run a simulation, Monte Carlo or sweep. Asked for \
one of those, say in one plain sentence that you cannot and what would do it instead; do not apologise and \
do not improvise numbers.

How you speak. One sentence. A second only when it says something the first cannot; never a third. Lead with \
the answer: no preamble, no "I checked", no repeating the question back, no offer of more help unless you were \
asked a yes/no question that needs one. Plain words a passenger would follow, not jargon: "Delta 789 is \
climbing to 36,000 feet", never "DAL789 FL360 assigned". Airline name and number as a person says it (Delta \
789, Air Canada 123), altitudes in thousands of feet, distances in miles. No abbreviations, no lists in the \
prose, no tables read aloud: the card carries the detail, your sentence carries the point. Never mention tools, \
steps or what you did to find out; the trace already shows that.

Examples of the register:
Q: "what's going on out there?" A: "Eleven flights, all on plan, and the closest pair is Delta 789 and \
WestJet 220 at 14 miles."
Q: "why did you turn Delta 789?" A: "It was going to come within 4 miles of WestJet 220, so I sent it 12 \
degrees right and it costs about 3 extra miles."
Q: "double the traffic" A: "Twenty-two flights now, and the plan is still clean."
Q: "who is above 35,000 feet?" A: "Three: Air Canada 123, Delta 789 and Lufthansa 470."

Rules: a clearance has to be said on the radio by the controller; "separate them" is the planner's job, not \
yours. Resolve "it", "that flight" and "this one" to the selected aircraft in the screen state. At most 4 tool calls, the fewest that answer; a plain question about the conversation needs \
none, but a question about the traffic, a flight, a card or the numbers always goes through a \
tool so that a card goes up beside your sentence. Hold the conversation across turns: earlier \
turns are in the history. Coordinates are nautical miles, \
x east, y north, sector centred on 0."""


@dataclass
class StepInfo:
    n: int
    tool: str
    args: dict[str, Any]
    summary: str
    ms: float
    ok: bool = True


@dataclass
class Answer:
    turn_id: str
    text: str
    cards: list[dict[str, Any]] = field(default_factory=list)
    for_: Literal["message"] = "message"  # kept on the wire; squack only ever replies to a message
    steps: list[StepInfo] = field(default_factory=list)

    @property
    def actions(self) -> list[str]:
        """The old agent_reply shape: "tool: summary" per call."""
        return [f"{s.tool}: {s.summary}" for s in self.steps]

    def payload(self) -> dict[str, Any]:
        return {"turn_id": self.turn_id, "text": self.text, "cards": self.cards, "for": self.for_,
                "steps": [{"n": s.n, "tool": s.tool, "summary": s.summary, "ms": round(s.ms, 1),
                           "status": "done" if s.ok else "error"} for s in self.steps]}


def picture(world: "World", ui_state: dict[str, Any] | None) -> dict[str, Any]:
    """What the model is told about the world and the screen. Small: tokens are latency."""
    sc = world.scenario
    ac = world.sim.aircraft() if sc else []
    return {
        "scenario": sc.name if sc else None, "t_s": round(world.sim.t), "lifecycle": world.lifecycle,
        "speed": world.speed, "voice": not world.auto_speak,
        "buffer_nm": world.buffer_nm, "error_rate": world.error_rate,
        "aircraft": [a.callsign for a in ac if not a.is_intruder][:40],
        "intruders": [a.callsign for a in ac if a.is_intruder],
        "disruptions": [f"{d.label} {d.id}" for d in world.disruptions.values()],
        "cards_pending": len([c for c in world.cards.values() if c.status == "pending" and not c.minor]),
        "risk_pairs": [[p.a, p.b, round(float(p.p_max), 2)] for p in world.risk.pairs[:5]],
        "screen": ui_state or {},
    }


class SquackAgent:
    """`handle_message(text, history, ui_state)`: the controller's turn, ending in one Answer.

    squack speaks only when spoken to. There is no event-driven entry point: nothing in the world
    makes it talk."""

    def __init__(self, world: "World", llm: Any, emit: Callable[[dict[str, Any]], None] | None = None,
                 clock: Callable[[], float] = time.monotonic, max_tool_calls: int = MAX_TOOL_CALLS,
                 budget_s: float = BUDGET_S) -> None:
        self.world = world
        self.llm = llm
        self._emit = emit or world.emit
        self.clock = clock
        self.max_tool_calls = max_tool_calls
        self.budget_s = budget_s

    @property
    def has_model(self) -> bool:
        return not (isinstance(self.llm, MockLLM) or getattr(self.llm, "is_mock", False))

    # -- entry points ------------------------------------------------------------------------------

    def handle_message(self, text: str, history: list[dict[str, Any]] | None = None,
                       ui_state: dict[str, Any] | None = None) -> Answer:
        turn = uuid.uuid4().hex[:8]
        text = (text or "").strip()
        if not text:
            return self._finish(Answer(turn, "Say something and I will do it."))
        if not self.has_model:
            ans = self._routed(turn, text, ui_state, "message")
        else:
            ans = self._loop(turn, [{"role": "user", "content": text}], history, ui_state, "message")
        if history is not None:
            history.append({"user": text, "squack": ans.text})
            del history[:-HISTORY_TURNS]
        return self._finish(ans)

    # -- the model loop ----------------------------------------------------------------------------

    def _loop(self, turn: str, user: list[dict[str, Any]], history: list[dict[str, Any]] | None,
              ui_state: dict[str, Any] | None, for_: Literal["message"] = "message") -> Answer:
        t0 = self.clock()
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM + "\n\nState now: "
                                           + json.dumps(picture(self.world, ui_state), default=str)}]
        for h in (history or [])[-HISTORY_TURNS:]:
            messages.append({"role": "user", "content": h.get("user", "")})
            messages.append({"role": "assistant", "content": h.get("squack", "")})
        messages += user
        ans = Answer(turn, "", for_=for_)
        cards: list[dict[str, Any]] = []
        text: str | None = None
        calls = 0
        while calls < self.max_tool_calls:
            if self.clock() - t0 > self.budget_s:
                text = text or "I ran out of time; here is what I managed."
                break
            try:
                resp = self.llm.chat(messages, tools=R.schemas(), tool_choice="auto", max_tokens=MAX_TOKENS)
            except Exception:  # noqa: BLE001 - never silent, never a traceback on the screen
                # Baseten unreachable or refusing: do the turn with the keyword router instead, so
                # "double the traffic" still doubles the traffic, and say so in one honest line.
                log.warning("agent model call failed; falling back to the keyword router", exc_info=True)
                return self._degraded(turn, user, ui_state, ans)
            if not resp.tool_calls:
                text = (resp.content or "").strip() or None
                break
            messages.append(resp.as_message())
            for tc in resp.tool_calls:
                if calls >= self.max_tool_calls:
                    messages.append({"role": "tool", "tool_call_id": tc.id, "name": tc.name,
                                     "content": json.dumps({"error": "tool budget spent"})})
                    continue
                calls += 1
                result, step = self._call(turn, calls, tc.name, tc.arguments)
                ans.steps.append(step)
                cards += _cards_of(result)
                brief = {k: v for k, v in result.items() if k not in ("card", "cards")}
                messages.append({"role": "tool", "tool_call_id": tc.id, "name": tc.name,
                                 "content": json.dumps(brief, default=str)[:4000]})
        if text is None:
            if self.clock() - t0 <= self.budget_s:
                try:
                    final = self.llm.chat(messages, max_tokens=MAX_TOKENS)
                    text = (final.content or "").strip() or None
                except Exception:  # noqa: BLE001
                    log.exception("agent final call failed")
            text = text or _sentence_from_steps(ans.steps)
        ans.text = _tidy(text)
        ans.cards = _dedupe(cards)[-MAX_CARDS:]
        return ans

    def _degraded(self, turn: str, user: list[dict[str, Any]], ui_state: dict[str, Any] | None,
                  ans: Answer) -> Answer:
        """The model is not answering. Anything already done stands; the rest goes to the router."""
        if ans.steps:
            ans.text = _tidy(_sentence_from_steps(ans.steps))
            ans.cards = _dedupe(ans.cards)[-MAX_CARDS:]
            return ans
        text = str(user[-1].get("content") or "") if user else ""
        routed = self._routed(turn, text, ui_state)
        if routed.text in (NEEDS_MODEL, "Done.") and not routed.steps:
            routed.text = MODEL_DOWN
        return routed

    # -- the keyword router ------------------------------------------------------------------------

    def _routed(self, turn: str, text: str, ui_state: dict[str, Any] | None,
                for_: Literal["message"] = "message") -> Answer:
        plan = route(self.world, text, ui_state)
        ans = Answer(turn, "", for_=for_)
        if plan is None:
            ans.text = NEEDS_MODEL
            return ans
        if isinstance(plan, str):  # a plain text reply from the router itself
            ans.text = plan
            return ans
        cards: list[dict[str, Any]] = []
        for n, (name, args) in enumerate(plan[:self.max_tool_calls], start=1):
            result, step = self._call(turn, n, name, args)
            ans.steps.append(step)
            cards += _cards_of(result)
        ans.text = _tidy(_sentence_from_steps(ans.steps))
        ans.cards = _dedupe(cards)[-MAX_CARDS:]
        return ans

    # -- shared -----------------------------------------------------------------------------------

    def _call(self, turn: str, n: int, name: str, args: dict[str, Any]) -> tuple[dict[str, Any], StepInfo]:
        t0 = self.clock()
        result = R.execute(self.world, name, args)
        ms = (self.clock() - t0) * 1000.0
        step = StepInfo(n, R.resolve_name(name), args, R.summarize(name, result), ms, ok="error" not in result)
        self._emit(event("agent_step", {"turn_id": turn, "step": n, "tool": step.tool, "args": args,
                                        "result_summary": step.summary, "elapsed_ms": round(ms, 1),
                                        "status": "done" if step.ok else "error"}, t=self.world.sim.t))
        return result, step

    def _finish(self, ans: Answer) -> Answer:
        self._emit(event("answer", ans.payload(), t=self.world.sim.t))
        return ans  # World speaks it (tower.voice.speak_reply) once the coroutine that asked has it


def _is_descriptor(c: Any) -> bool:
    """A card the screen can draw, not a tool's own row. `query.cards` returns instruction-card
    briefs under the same key, and those have no `kind`: without this guard they reached the
    registry as blank cards."""
    return isinstance(c, dict) and isinstance(c.get("kind"), str) and c["kind"] in CD.KINDS


def _cards_of(result: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    if isinstance(result.get("cards"), list):
        out += [c for c in result["cards"] if _is_descriptor(c)]
    if _is_descriptor(result.get("card")):
        out.append(result["card"])
    return out


def _dedupe(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for c in cards:
        key = json.dumps(c, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _sentence_from_steps(steps: list[StepInfo]) -> str:
    """The router's templated voice: one short sentence per tool, in order. It acts; it does not converse."""
    if not steps:
        return "Done."
    parts = []
    for st in steps:
        line = (st.summary or "done").strip().rstrip(".")
        parts.append(line[0].upper() + line[1:] + ".")
    return " ".join(parts)


def _tidy(text: str) -> str:
    """Whitespace and a hard length cap only: the reply is conversational, not one line."""
    out = " ".join((text or "Done.").split())
    return out[:MAX_TEXT_CHARS].rstrip() + ("…" if len(out) > MAX_TEXT_CHARS else "")


# ------------------------------------------------------------------------------ keyword router

AIRLINES = {"air canada": "ACA", "canada": "ACA", "westjet": "WJA", "west jet": "WJA", "porter": "POE",
            "jazz": "JZA", "delta": "DAL", "united": "UAL", "american": "AAL", "lufthansa": "DLH",
            "speedbird": "BAW", "british": "BAW"}
KINDS = ((r"\b(fighter|jet|intruder|bogey)\b", "fighter"), (r"\bdrone\b", "drone"), (r"\bballoon\b", "balloon"),
         (r"\b(mayday|emergency|engine failure)\b", "emergency"), (r"\b(unknown|unidentified|ufo)\b", "unknown"),
         (r"\b(storm|weather|thunder|cell)\b", "storm"), (r"\b(closed|restricted|military area|exercise)\b", "closed"),
         (r"\b(rocket|launch)\b", "rocket"), (r"\b(random|surprise|anything)\b", "random"))
WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "twenty": 20, "sixty": 60}
_CS = re.compile(r"\b([A-Z]{3}\d{1,4}[A-Z]?)\b")
Plan = list[tuple[str, dict[str, Any]]]


def _num(t: str, after: str) -> float | None:
    m = re.search(after + r"\D{0,12}?(\d+(?:\.\d+)?|" + "|".join(WORDS) + r")\b", t)
    if not m:
        return None
    v = m.group(1)
    return float(WORDS[v]) if v in WORDS else float(v)


def _callsign(world: "World", raw: str, ui_state: dict[str, Any] | None) -> str | None:
    """A callsign written (DAL789), spoken (delta seven eight nine), or "it" for the selected one."""
    m = _CS.search(raw)
    if m and R.find_callsign(world, m.group(1)):
        return R.find_callsign(world, m.group(1))
    low = raw.lower()
    sel = (ui_state or {}).get("selected")
    if sel and re.search(r"\b(it|that (one|flight|plane|aircraft)|this (one|flight|plane|aircraft)|him|her)\b", low):
        return R.find_callsign(world, str(sel)) or str(sel)
    # a spoken callsign: airline word and digits/words after it, or a bare digit group
    for word, code in AIRLINES.items():
        if word in low:
            tail = low.split(word, 1)[1]
            digits = re.findall(r"\d+|" + "|".join(WORDS), tail)
            spoken = " ".join(str(WORDS.get(d, d)) for d in digits[:4])
            cs = R.find_callsign(world, f"{code} {spoken}".strip()) or R.find_callsign(world, f"{word} {spoken}")
            if cs:
                return cs
    cs = R.find_callsign(world, raw)
    return cs


def _scenario_name(text: str) -> str | None:
    from sim import scenarios as SC

    names = SC.list_scenarios()
    low = text.lower()
    hours = {"four": "16", "three": "15", "five": "17", "two": "14", "one": "13", "noon": "12", "midnight": "00"}
    best, best_score = None, 0
    for n in names:
        toks = re.split(r"[/_\-.]", n.lower())
        score = sum(1 for tok in toks if len(tok) > 2 and tok.isalpha() and tok in low)
        for word, hh in hours.items():
            if re.search(rf"\b{word}\b", low) and any(tok.startswith(hh) and tok.isdigit() for tok in toks):
                score += 1
        if score > best_score:
            best, best_score = n, score
    return best


def route(world: "World", text: str, ui_state: dict[str, Any] | None = None) -> Plan | str | None:
    """Deterministic intent parser over the registry. Returns tool calls in order, a plain reply,
    or None when only a model could do it."""
    t = " ".join(text.lower().split())
    plan: Plan = []
    cs = _callsign(world, text, ui_state)
    sel = (ui_state or {}).get("selected")

    # world.* --------------------------------------------------------------------------------------
    if re.search(r"\b(load|open|switch to)\b", t) and (name := _scenario_name(t)):
        d = _num(t, r"\b(?:density|times|x)\b") or (2.0 if "double" in t else None)
        plan.append(("world.load", {"name": name, **({"density": d} if d else {})}))
    if re.search(r"\b(double|twice)\b", t) or re.search(r"\b(2x|x2)\b.*\b(traffic|flights)\b", t) \
            or re.search(r"\b(traffic|flights)\b.*\b(2x|x2)\b", t):
        if not any(n == "world.load" for n, _ in plan):
            plan.append(("world.multiply_traffic", {"factor": 2.0}))
    elif re.search(r"\b(triple|3x)\b", t):
        plan.append(("world.multiply_traffic", {"factor": 3.0}))
    elif re.search(r"\b(traffic|flights|density)\b", t) and (m := re.search(r"(?:times|x)\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*x\b", t)):
        plan.append(("world.multiply_traffic", {"factor": float(m.group(1) or m.group(2))}))
    elif re.search(r"\b(halve|half the traffic)\b", t):
        plan.append(("world.multiply_traffic", {"factor": 0.5}))
    if re.search(r"\b(remove|clear|delete|take out|take away)\b.*\b(storm|disruption|zone|intruder|fighter|drone|balloon|it)\b", t):
        did = next((i for i in world.disruptions if i.lower() in t), None)
        plan.append(("world.remove_disruption", {"id": did or (list(world.disruptions)[-1] if world.disruptions else "")}))
    else:
        for pattern, kind in KINDS:
            # a verb, or the kind word leading ("storm on DAL789"); never a question about one
            wanted = re.search(r"\b(put|drop|add|place|spawn|create|make|throw|send|launch|declare|give me|start a|bring)\b", t) \
                or re.match(r"(?:a |an |the )?(?:" + pattern.strip("\\b()") + r")\b", t)
            if wanted and re.search(pattern, t) and not re.search(r"\b(why|explain|cost|what|how|who|which|where)\b", t):
                args: dict[str, Any] = {"kind": kind}
                if cs and re.search(r"\b(on|ahead of|in front of|for|at|near|to)\b", t):
                    args["target"] = cs
                elif re.search(r"\b(ahead|in front)\b", t) and sel:
                    args["target"] = sel
                plan.append(("world.disrupt", args))
                break
    if (m := re.search(r"\bspeed\b\D{0,8}(\d+(?:\.\d+)?)", t)) or (m := re.search(r"\b(\d+(?:\.\d+)?)\s*x\b(?!.*\b(traffic|flights)\b)", t)) \
            or (m := re.search(r"\b(\d+)\s*times (?:faster|speed)", t)):
        if not any(n == "world.multiply_traffic" for n, _ in plan):
            plan.append(("world.set_speed", {"speed": float(m.group(1))}))
    elif re.search(r"\b(real ?time|normal speed|1x)\b", t):
        plan.append(("world.set_speed", {"speed": 1.0}))
    # "Action mode" on screen: manual is voice on, autonomous is voice off. The wire keeps set_voice.
    if m := re.search(r"\bvoice (on|off)\b|\b(mute|unmute)\b|\b(data ?link)\b|\b(manual|autonomous)\b", t):
        on = (m.group(1) == "on") if m.group(1) else (m.group(2) == "unmute") if m.group(2) \
            else (m.group(4) == "manual") if m.group(4) else False
        plan.append(("world.set_voice", {"on": on}))
    if re.search(r"\b(start|resume|unpause|play)\b", t) and not re.search(r"\b(monte carlo|sim|sweep)\b", t) and not plan:
        plan.append(("world.lifecycle", {"action": "start"}))
    elif re.search(r"\b(pause|hold on|freeze|stop the clock|stop)\b", t) and not re.search(r"\bfollow", t):
        plan.append(("world.lifecycle", {"action": "pause"}))
    elif re.search(r"\b(reset|restart|start over|from the top)\b", t):
        plan.append(("world.lifecycle", {"action": "reset"}))
    sliders: dict[str, Any] = {}
    if (v := _num(t, r"\bbuffer\b")) is not None:
        sliders["buffer_nm"] = v
    if (v := _num(t, r"\berror rate\b")) is not None:
        sliders["error_rate"] = v / 100 if v > 1 else v
    if (v := _num(t, r"\bnoise\b")) is not None:
        sliders["noise"] = v / 100 if v > 1 else v
    if sliders:
        plan.append(("world.set_sliders", sliders))
    if re.search(r"\b(spawn|add|bring in)\b.*\b(flight|plane|aircraft|arrival)\b", t):
        airline = next((code for word, code in AIRLINES.items() if word in t), "ACA")
        side = next((s for s in ("east", "west", "north", "south") if s in t), "east")
        alt = _num(t, r"\b(?:fl|flight level|at)\b")
        plan.append(("world.spawn_flight", {"airline": airline, "from_side": side,
                                            **({"alt_ft": alt * 100} if alt and 100 <= alt <= 450 else {})}))
    # ui.* -----------------------------------------------------------------------------------------
    if re.search(r"\b(focus|zoom|fly to|go to|look at|centre|center|select)\b", t) and cs:
        plan.append(("ui.focus", {"callsign": cs}))
    if re.search(r"\b(stop following|unfollow|let it go)\b", t):
        plan.append(("ui.follow", {"callsign": ""}))
    elif re.search(r"\bfollow\b", t) and (cs or sel):
        plan.append(("ui.follow", {"callsign": cs or sel}))
    cam: dict[str, Any] = {}
    if re.search(r"\b(top down|flat|2d|overhead|from above)\b", t):
        cam["top_down"] = True
    if (v := _num(t, r"\b(?:tilt|pitch)\b")) is not None:
        cam["pitch"] = v
    elif re.search(r"\b(tilt|3d|perspective)\b", t):
        cam["pitch"] = 55
    if (v := _num(t, r"\b(?:bearing|rotate|heading of the map)\b")) is not None:
        cam["bearing"] = v
    if (v := _num(t, r"\bexaggerat\w*\b")) is not None:
        cam["exaggeration"] = v
    if cam:
        plan.append(("ui.camera", cam))
    if m := re.search(r"\b(?:show|draw|only|just|lines?)\b.*\b(changed|tower|today|both)\b", t):
        if re.search(r"\b(lines?|paths?|routes?|changed|only|just)\b", t):
            plan.append(("ui.line_view", {"view": m.group(1)}))
    if m := re.search(r"\b(?:open|show|bring up)\b.*\b(scoreboard|cards|transcript|setup|alerts|risk|panel|drawer)\b", t):
        plan.append(("ui.panel", {"panel": m.group(1)}))

    # explain.* ------------------------------------------------------------------------------------
    if re.search(r"\b(why|explain|how (sure|confident)|reason)\b", t):
        if re.search(r"\b(replan|re-plan|changed|last plan)\b", t):
            plan.append(("explain.replan", {}))
        elif cs:
            plan.append(("explain.card", {"callsign": cs}))
        elif re.search(r"\b(storm|disruption|zone|fighter|drone|balloon|that)\b", t) and world.disruptions:
            plan.append(("explain.disruption", {}))
        elif sel:
            plan.append(("explain.card", {"callsign": sel}))
    elif re.search(r"\b(what did|how much did)\b.*\b(cost)\b", t):
        plan.append(("explain.disruption", {}) if not cs else ("explain.card", {"callsign": cs}))

    # query.* --------------------------------------------------------------------------------------
    if re.search(r"\b(scoreboard|score|how are we doing|miles saved|numbers)\b", t) and not any(n == "ui.panel" for n, _ in plan):
        plan.append(("query.scoreboard", {}))
    if re.search(r"\b(closest|nearest|within|near each other|close to each other|pairs?)\b", t) \
            and not re.search(r"\bseparate\b", t):
        nm = _num(t, r"\bwithin\b")
        plan.append(("query.pairs", {"max_nm": nm or R.PAIRS_DEFAULT_NM}))
    if re.search(r"\b(pending|cards|instructions)\b", t) and re.search(r"\b(list|show|what|which|how many|still)\b", t) \
            and not any(n in ("ui.panel", "query.scoreboard") for n, _ in plan):
        status = next((s for s in ("pending", "spoken", "validated", "verified", "error", "all") if s in t), "pending")
        plan.append(("query.cards", {"status": status}))
    if re.search(r"\b(read back|readback|say|said|log|history|transcript)\b", t) and cs \
            and not any(n == "explain.card" for n, _ in plan):
        plan.append(("query.log", {"callsign": cs, "n": 5}))
    if re.search(r"\b(what happened|timeline|recap|last (\d+|few|five|ten) (minutes?|seconds?)|so far)\b", t) \
            and not any(n == "query.log" for n, _ in plan):
        mins = _num(t, r"\blast\b")
        unit = 60.0 if "minute" in t else 1.0
        plan.append(("query.timeline", {"since_s": (mins * unit) if mins else R.TIMELINE_DEFAULT_S}))
    if re.search(r"\b(who is|who's|which flights?|which aircraft|list (the )?(aircraft|flights|planes)|everyone|everybody)\b", t) \
            and not any(n in ("query.pairs", "query.cards") for n, _ in plan):
        filters: list[dict[str, Any]] = []
        if (v := _num(t, r"\b(?:above|over|higher than)\b(?: fl| flight level)?")) is not None:
            filters.append({"field": "fl", "op": "gt", "value": v})
        if (v := _num(t, r"\b(?:below|under|lower than)\b(?: fl| flight level)?")) is not None:
            filters.append({"field": "fl", "op": "lt", "value": v})
        for d in ("north", "east", "south", "west"):
            if re.search(rf"\b(heading|going|flying|bound) {d}\b|\b{d}bound\b", t):
                filters.append({"field": "heading", "op": "eq", "value": d})
        for word, code in AIRLINES.items():
            if word in t:
                filters.append({"field": "callsign", "op": "contains", "value": code})
                break
        plan.append(("query.aircraft", {"filters": filters}))

    if not plan:
        if re.search(r"\b(describe|status|what is going on|what's going on|where are we|how many)\b", t):
            return world.tool_describe()
        if re.search(r"\b(hello|hi|hey|thanks|thank you)\b", t):
            return "Hello. Tell me what to do with the world or the screen, or ask about a flight."
        if re.search(r"\b(descend|climb|turn|heading|maintain|contact|squawk|cleared|direct)\b", t) and cs:
            return f"I cannot issue clearances; say that to {cs} on the radio."
        return None
    return plan
