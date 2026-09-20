"""The squack agent: one sentence in, tool calls over the world and the screen, one sentence and
cards out. PRD sections 3, 4 and 10.

`SquackAgent(world, llm)` generalises `world_agent._llm_agent`: the same `LLM.chat` with tools on
the resolver model (Baseten, hard rule 5), the registry in `agent/registry.py` instead of six
hand-wired tools, and caps like the resolver's: 4 tool calls, 8 s, 600 output tokens. Every tool
call goes out as an `agent_step` and every turn ends in exactly one `answer` (never silent). The
agent never opens a clearance: no tool here touches the frequency, and the registry refuses
anything marked `acts_on_traffic` except `world.nudge`.

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
MAX_TOKENS = 600
HISTORY_TURNS = 10
MAX_CARDS = 3
MAX_TEXT_CHARS = 1500  # 600 tokens is the real cap; this only guards the wire
NEEDS_MODEL = "I need a model for that."

SYSTEM = """You are squack, the controller's colleague on an air traffic control simulator, talking from the \
command bar. Speak in the first person, calm and specific, no filler and no apologies. Use callsigns the way the \
radio does (Delta 789, Air Canada 123). You can act and you can talk: turn what the controller says into tool \
calls over the world (scenarios, disruptions, speed, voice, sliders), the screen (focus, follow, camera, panels), \
live questions (aircraft, pairs, cards, log, scoreboard, timeline), explanations (the planner's own reasons: the \
card's reason and cause, cost against the runner-up, confidence, residual risk) and background simulations; then \
answer in two or three sentences that say what you did and what you saw. Go longer only for an open question \
("what's going on", "explain the plan", "how are we doing"). The cards the tools produced carry the data, so do \
not read a table back; give the one number that matters. Name the tool you used only when it matters ("I \
re-planned Delta 789 with 2 more miles of buffer"). Hold the conversation across turns: earlier turns are in \
the history.

Rules: you cannot issue clearances or talk to aircraft; if asked, say it has to be said on the radio. "Separate \
them" or "move X away" means world.nudge(X). Resolve "it", "that flight" and "this one" to the selected aircraft \
in the screen state. At most 4 tool calls, the fewest that answer; a plain question about the conversation needs \
none. When an event batch is given instead of a message, say what just happened and why in a sentence or two, and \
call the explain or query tools that put the right cards up (at most 3). Coordinates are nautical miles, x east, \
y north, sector centred on 0."""


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
    for_: Literal["message", "event"] = "message"
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
        "speed": world.speed, "voice": not world.auto_speak, "ui_mode": world.ui_mode,
        "buffer_nm": world.buffer_nm, "error_rate": world.error_rate,
        "aircraft": [a.callsign for a in ac if not a.is_intruder][:40],
        "intruders": [a.callsign for a in ac if a.is_intruder],
        "disruptions": [f"{d.label} {d.id}" for d in world.disruptions.values()],
        "cards_pending": len([c for c in world.cards.values() if c.status == "pending" and not c.minor]),
        "risk_pairs": [[p.a, p.b, round(float(p.p_max), 2)] for p in world.risk.pairs[:5]],
        "screen": ui_state or {},
        "sim_jobs": "available" if R.SIM_JOBS else "not available",
    }


class SquackAgent:
    """`handle_message(text, history, ui_state)` and `handle_events(events)`; both end in an Answer."""

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

    def handle_events(self, events: list[dict[str, Any]], ui_state: dict[str, Any] | None = None) -> Answer | None:
        """An environment batch from the wake policy. None without a model: the director already
        put the stage up, and a keyword router has nothing to say about an event."""
        if not self.has_model or not events:
            return None
        lines = [f"[{ev.get('t', 0):.0f}s] {ev.get('type')}: {R.describe_event(ev) or ''}" for ev in events]
        content = "Environment events since your last turn:\n" + "\n".join(lines) + \
            "\nTell the controller what just happened and why, in a sentence or two, with the cards that show it."
        turn = uuid.uuid4().hex[:8]
        return self._finish(self._loop(turn, [{"role": "user", "content": content}], None, ui_state, "event"))

    # -- the model loop ----------------------------------------------------------------------------

    def _loop(self, turn: str, user: list[dict[str, Any]], history: list[dict[str, Any]] | None,
              ui_state: dict[str, Any] | None, for_: Literal["message", "event"]) -> Answer:
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
            except Exception as exc:  # noqa: BLE001 - never silent
                log.exception("agent model call failed")
                text = f"The model call failed ({type(exc).__name__}); nothing was changed."
                break
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

    # -- the keyword router ------------------------------------------------------------------------

    def _routed(self, turn: str, text: str, ui_state: dict[str, Any] | None,
                for_: Literal["message", "event"]) -> Answer:
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
    if m := re.search(r"\bvoice (on|off)\b|\b(mute|unmute)\b|\b(data ?link)\b", t):
        on = (m.group(1) == "on") if m.group(1) else (m.group(2) == "unmute") if m.group(2) else False
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
    if re.search(r"\b(separate|nudge|move .* (away|apart|off)|give .* room)\b", t):
        who = cs
        if who is None and re.search(r"\bthem\b|closest pair", t):
            top = R.pairs_now(world, None)[:1]
            who = top[0]["a"] if top else None
        if who:
            plan.append(("world.nudge", {"callsign": who}))

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
    if re.search(r"\b(agent mode|squack decides|you decide|take the screen)\b", t):
        plan.append(("ui.mode", {"mode": "agent"}))
    elif re.search(r"\b(normal mode|give me the screen|manual screen)\b", t):
        plan.append(("ui.mode", {"mode": "normal"}))

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
            and not re.search(r"\b(separate|nudge)\b", t):
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

    # sim.* ----------------------------------------------------------------------------------------
    if re.search(r"\bmonte ?carlo\b|\brun the (sim|eval|evaluation)\b|\b(does|will) it (still )?hold\b", t):
        runs = _num(t, r"\b(?:runs?)\b") or _num(t, r"\b(\d+)\s*runs?\b")
        args = {"runs": int(runs)} if runs else {}
        if re.search(r"\bdouble\b", t) and not any(n == "world.multiply_traffic" for n, _ in plan):
            args["density"] = 2.0
        plan.append(("sim.montecarlo", args))
    elif re.search(r"\bsweep\b", t):
        nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", t)]
        plan.append(("sim.sweep", {"densities": nums[:2] or [1.0, 2.0]}))

    if not plan:
        if re.search(r"\b(describe|status|what is going on|what's going on|where are we|how many)\b", t):
            return world.tool_describe()
        if re.search(r"\b(hello|hi|hey|thanks|thank you)\b", t):
            return "Hello. Tell me what to do with the world or the screen, or ask about a flight."
        if re.search(r"\b(descend|climb|turn|heading|maintain|contact|squawk|cleared|direct)\b", t) and cs:
            return f"I cannot issue clearances; say that to {cs} on the radio."
        return None
    return plan
