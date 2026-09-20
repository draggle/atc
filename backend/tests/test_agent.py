"""The squack agent: registry, keyword router, loop caps, wake policy, director, agent mode."""
import asyncio
import math

import pytest

from agent import registry as R
from agent.loop import NEEDS_MODEL, SquackAgent, route
from planner.risk import REPLAN_P
from schemas import event
from tower.llm import ChatResponse, MockLLM, ToolCall
from world import World


def collect():
    events = []
    return events, events.append


@pytest.fixture
def world():
    events, emit = collect()
    w = World(emit, synthesize=False, realtime=False)
    w.load("demo")
    w.start()
    return w, events


def types(events):
    return [e["type"] for e in events]


def of(events, typ):
    return [e for e in events if e["type"] == typ]


# ----------------------------------------------------------------------------------- registry

DEFAULT_ARGS = {
    "world.set_speed": {"speed": 5}, "world.set_voice": {"on": False}, "world.set_sliders": {"buffer_nm": 4},
    "world.load": {"name": "demo"}, "world.disrupt": {"kind": "storm"}, "world.remove_disruption": {},
    "world.lifecycle": {"action": "start"}, "world.multiply_traffic": {"factor": 1.5},
    "world.spawn_flight": {"airline": "DAL", "from_side": "west"},
    "ui.focus": {}, "ui.follow": {}, "ui.camera": {"pitch": 45, "bearing": 30}, "ui.line_view": {"view": "changed"},
    "ui.panel": {"panel": "scoreboard"},
    "query.aircraft": {"filters": [{"field": "fl", "op": "gt", "value": 200}]}, "query.pairs": {"max_nm": 30},
    "query.cards": {"status": "all"}, "query.log": {}, "query.scoreboard": {}, "query.timeline": {"since_s": 600},
    "explain.card": {}, "explain.flight": {}, "explain.disruption": {}, "explain.replan": {},
}


def test_every_tool_runs_on_a_loaded_world_without_raising(world):
    w, ev = world
    cs = w.sim.aircraft()[0].callsign
    assert set(DEFAULT_ARGS) == set(R.names()), "keep DEFAULT_ARGS in step with the registry"
    for name in R.names():
        args = dict(DEFAULT_ARGS[name])
        if name in ("ui.focus", "ui.follow", "query.log", "explain.card", "explain.flight"):
            args["callsign"] = cs
        out = R.execute(w, name, args)
        assert isinstance(out, dict) and "summary" in out, name
        if name not in ("world.remove_disruption", "explain.disruption"):  # no disruption yet at that point
            assert "error" not in out, (name, out)
    assert R.execute(w, "no.such", {})["error"].startswith("unknown tool")


def test_schemas_use_wire_names_without_dots(world):
    for s in R.schemas():
        assert "." not in s["function"]["name"]
        assert R.resolve_name(s["function"]["name"]) in R.REGISTRY


def test_ui_tools_only_emit_a_ui_command(world):
    w, ev = world
    cs = w.sim.aircraft()[0].callsign
    ev.clear()
    out = R.execute(w, "ui.focus", {"callsign": cs.lower()})
    assert out["ok"] and types(ev) == ["ui_command"]
    assert ev[0]["payload"] == {"command": "focus", "args": {"callsign": cs}}
    assert R.execute(w, "ui.focus", {"callsign": "XYZ999"})["error"]


def test_query_pairs_returns_the_closest_pair(world):
    w, _ = world
    states = w.sim.aircraft()
    best = min(((math.hypot(a.x_nm - b.x_nm, a.y_nm - b.y_nm), a.callsign, b.callsign)
                for i, a in enumerate(states) for b in states[i + 1:]), key=lambda r: r[0])
    out = R.execute(w, "query.pairs", {"max_nm": 1000})
    top = out["closest"]
    assert {top["a"], top["b"]} == {best[1], best[2]} and abs(top["nm"] - best[0]) < 0.1
    assert out["card"]["kind"] == "table" and out["card"]["focus_column"] == 0
    assert out["card"]["rows"][0][2] == top["nm"]


def test_explain_card_reads_the_planners_reasons_for_a_real_card(world):
    w, _ = world
    d = w.add_disruption("storm")
    assert d is not None
    cards = [c for c in w.cards.values() if c.cause == d.id]
    assert cards, "a storm ahead of the traffic makes at least one card"
    cs = cards[0].callsign
    out = R.execute(w, "explain.card", {"callsign": cs})
    assert out["reason"] == cards[0].reason and out["cause"] == d.id
    assert isinstance(out["cost"], float) and out["cost"] >= 0
    assert "runner_up_cost" in out and "confidence" in out and out["confidence"] is not None
    assert out["risk_after"] is not None and out["margin"] is not None
    card = out["card"]
    assert card["kind"] == "aircraft" and card["callsign"] == cs and card["live"] == {"aircraft": cs}
    assert card["card"]["reason"] == cards[0].reason and any(d.id in ch for ch in card["changes"])


def test_explain_disruption_lists_the_rerouted_flights(world):
    w, _ = world
    d = w.add_disruption("storm")
    out = R.execute(w, "explain.disruption", {"id": d.id})
    assert out["id"] == d.id and out["card"]["kind"] == "comparison"
    assert set(out["rerouted"]) == {c.callsign for c in w.cards.values() if c.cause == d.id}
    if out["rerouted"]:
        assert out["cards"][1]["kind"] == "aircraft" and out["cards"][1]["callsign"] == out["rerouted"][0]


def test_query_timeline_reads_the_ring_buffer(world):
    w, _ = world
    d = w.add_disruption("storm")
    out = R.execute(w, "query.timeline", {"since_s": 600})
    assert out["counts"].get("disruption", 0) >= 1 and out["counts"].get("plan_update", 0) >= 1
    assert any(d.id in it["text"] for it in out["items"])
    only = R.execute(w, "query.timeline", {"since_s": 600, "kinds": ["disruption"]})
    assert set(only["counts"]) == {"disruption"}


def test_the_agent_can_neither_simulate_nor_touch_the_planner(world):
    """squack talks, reads, and changes the world and the screen. Nothing else."""
    w, _ = world
    assert not [n for n in R.names() if n.startswith("sim.")]
    assert "world.nudge" not in R.names()
    assert {n.split(".")[0] for n in R.names()} == {"world", "ui", "query", "explain"}
    assert "unknown tool" in R.execute(w, "sim.montecarlo", {})["summary"]


# ----------------------------------------------------------------------------- keyword router

@pytest.mark.parametrize("phrase, tool", [
    ("focus on {cs}", "ui.focus"), ("follow {cs}", "ui.follow"), ("why did you turn {cs}", "explain.card"),
    ("show the scoreboard", "ui.panel"), ("what is the closest pair right now", "query.pairs"),
    ("double the traffic", "world.multiply_traffic"), ("load dense", "world.load"),
    ("put a storm on {cs}", "world.disrupt"), ("speed 20", "world.set_speed"), ("20x", "world.set_speed"),
    ("voice off", "world.set_voice"), ("pause", "world.lifecycle"), ("buffer 3 miles", "world.set_sliders"),
    ("tilt the map", "ui.camera"), ("show only what changed", "ui.line_view"),
    ("list the cards still pending", "query.cards"), ("what did {cs} read back", "query.log"),
    ("what happened in the last five minutes", "query.timeline"),
    ("who is above FL300 heading west", "query.aircraft"),
    ("what did that storm cost", "explain.disruption"),
])
def test_keyword_router_covers_the_demo_phrases(world, phrase, tool):
    w, _ = world
    if tool == "explain.disruption":
        w.add_disruption("storm")
    cs = w.sim.aircraft()[0].callsign
    plan = route(w, phrase.format(cs=cs))
    assert isinstance(plan, list), (phrase, plan)
    assert tool in [n for n, _ in plan], (phrase, plan)


def test_keyword_router_resolves_it_to_the_selected_aircraft(world):
    w, _ = world
    cs = w.sim.aircraft()[1].callsign
    plan = route(w, "follow it", {"selected": cs})
    assert plan == [("ui.follow", {"callsign": cs})]
    plan = route(w, "why did you turn it", {"selected": cs})
    assert plan == [("explain.card", {"callsign": cs})]


def test_keyword_router_understands_a_spoken_callsign(world):
    w, _ = world
    cs = w.sim.aircraft()[0].callsign  # e.g. ACA123 -> "air canada one two three"
    digits = " ".join({"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six",
                       "7": "seven", "8": "eight", "9": "nine"}[d] for d in cs[3:] if d.isdigit())
    word = {"ACA": "air canada", "WJA": "westjet", "DAL": "delta", "UAL": "united", "AAL": "american", "JZA": "jazz",
            "POE": "porter"}[cs[:3]]
    plan = route(w, f"focus on {word} {digits}")
    assert plan and plan[0] == ("ui.focus", {"callsign": cs})


def test_keyword_router_says_it_needs_a_model_otherwise(world):
    w, ev = world
    agent = SquackAgent(w, MockLLM())
    ev.clear()
    ans = agent.handle_message("compose a haiku about the storm")
    assert ans.text == NEEDS_MODEL and ans.cards == [] and types(ev) == ["answer"]
    assert ev[0]["payload"]["for"] == "message" and ev[0]["payload"]["turn_id"] == ans.turn_id


def test_keyword_router_runs_the_tools_and_emits_steps_then_one_answer(world):
    w, ev = world
    cs = w.sim.aircraft()[0].callsign
    agent = SquackAgent(w, MockLLM())
    history = []
    ev.clear()
    ans = agent.handle_message(f"focus on {cs} and follow it", history, {"selected": cs})
    assert [s.tool for s in ans.steps] == ["ui.focus", "ui.follow"]
    t = types(ev)
    assert t.count("answer") == 1 and t[-1] == "answer" and t.count("agent_step") == 2 and t.count("ui_command") == 2
    step = of(ev, "agent_step")[0]["payload"]
    assert set(step) >= {"turn_id", "step", "tool", "args", "result_summary", "elapsed_ms"}
    assert history == [{"user": f"focus on {cs} and follow it", "squack": ans.text}]
    assert f"focused {cs}".lower() in ans.text.lower() and f"following {cs}".lower() in ans.text.lower()


def test_keyword_router_answers_with_cards(world):
    w, ev = world
    agent = SquackAgent(w, MockLLM())
    ans = agent.handle_message("what is the closest pair")
    assert ans.cards and ans.cards[0]["kind"] == "table"
    ans = agent.handle_message("show scoreboard numbers")
    assert any(c.get("live") == {"scoreboard": True} for c in ans.cards) or any(n == "ui.panel" for n in [s.tool for s in ans.steps])


def test_a_clearance_is_refused_without_touching_the_frequency(world):
    w, ev = world
    cs = w.sim.aircraft()[0].callsign
    agent = SquackAgent(w, MockLLM())
    ev.clear()
    ans = agent.handle_message(f"{cs} descend flight level two four zero")
    assert "radio" in ans.text and "clearance_opened" not in types(ev)


# --------------------------------------------------------------------------------- the loop

class Chatty:
    """A model that never stops calling tools: the loop must cut it off and still answer."""
    is_mock = False

    def __init__(self, calls=None):
        self.n = 0
        self.calls = calls

    def chat(self, messages, tools=None, tool_choice=None, max_tokens=300):
        self.n += 1
        if tools is None:
            return ChatResponse(content="Here is what I found.")
        name = (self.calls or ["query__scoreboard"])[(self.n - 1) % len(self.calls or [1])]
        return ChatResponse(content=None, tool_calls=[ToolCall(f"c{self.n}", name, {})])


def test_the_loop_caps_at_four_calls_and_always_answers(world):
    w, ev = world
    llm = Chatty(["query__scoreboard", "query__pairs", "explain__replan", "query__cards", "query__timeline"])
    agent = SquackAgent(w, llm)
    ev.clear()
    ans = agent.handle_message("do everything")
    assert len(ans.steps) == 4 and types(ev).count("agent_step") == 4
    assert types(ev).count("answer") == 1 and types(ev)[-1] == "answer"
    assert ans.text == "Here is what I found." and 1 <= len(ans.cards) <= 3
    assert [s.tool for s in ans.steps] == ["query.scoreboard", "query.pairs", "explain.replan", "query.cards"]


def test_the_loop_stops_on_the_time_budget(world):
    w, ev = world
    t = [0.0]

    def clock():
        t[0] += 3.0
        return t[0]

    agent = SquackAgent(w, Chatty(), clock=clock, budget_s=8.0)
    ans = agent.handle_message("keep going")
    assert len(ans.steps) < 4 and "answer" in types(ev)
    assert "ran out of time" in ans.text


class Broken:
    """A model that is not answering: Baseten down, a 403, a timeout."""

    is_mock = False

    def chat(self, *a, **k):
        raise RuntimeError("boom")


def test_a_model_error_falls_back_to_the_router_and_still_acts(world):
    """Baseten down: the turn still happens through the keyword router, and one answer goes out."""
    w, ev = world
    ans = SquackAgent(w, Broken()).handle_message("double the traffic")
    assert [s.tool for s in ans.steps] == ["world.multiply_traffic"] and types(ev)[-1] == "answer"
    assert "traceback" not in ans.text.lower()


def test_a_model_error_with_nothing_the_router_can_do_says_so_in_one_line(world):
    from agent.loop import MODEL_DOWN

    w, ev = world
    ans = SquackAgent(w, Broken()).handle_message("compose a haiku about the storm")
    assert ans.text == MODEL_DOWN and types(ev)[-1] == "answer" and ans.cards == []


def test_nothing_the_world_does_makes_squack_talk(world):
    """squack speaks only when spoken to: ticks, alerts, replans and disruptions emit no answer."""
    w, ev = world
    ev.clear()
    w.add_disruption("storm")
    w.emit(event("alert", {"callsign": w.sim.aircraft()[0].callsign, "result": "mismatch"}, t=w.sim.t))
    for _ in range(12):
        asyncio.run(w.tick(1.0))
    assert not of(ev, "answer") and not of(ev, "agent_step")
    assert not hasattr(w, "wake") and not hasattr(w, "ui_mode")


def test_replies_are_not_cut_to_one_line(world):
    w, _ = world

    class Talker:
        is_mock = False

        def chat(self, messages, tools=None, tool_choice=None, max_tokens=300):
            return ChatResponse(content="Nothing is close.\nThe nearest pair is 50 miles apart. I would leave it.")

    ans = SquackAgent(w, Talker()).handle_message("how are we doing")
    assert ans.text == "Nothing is close. The nearest pair is 50 miles apart. I would leave it." and ans.cards == []


def test_ring_buffer_skips_streams_and_keeps_the_rest(world):
    w, _ = world
    asyncio.run(w.tick(1.0))
    kinds = {e["type"] for e in w.events}
    assert "radar" not in kinds and "scoreboard" not in kinds and "state" in kinds and "plan" in kinds
    assert w.events.maxlen == 500


def test_agent_request_keeps_agent_reply_and_adds_answer(world):
    w, ev = world
    cs = w.sim.aircraft()[0].callsign
    ev.clear()
    reply = asyncio.run(w.agent_request(f"focus on {cs}", [], {"selected": None}))
    t = types(ev)
    assert "answer" in t and "agent_reply" in t and "ui_command" in t
    assert of(ev, "agent_reply")[0]["payload"]["actions"] == [f"ui.focus: focused {cs}"] and reply


def test_only_real_descriptors_reach_the_screen():
    """`query.cards` returns instruction-card briefs under a key called "cards". They are not
    things the registry can draw, and before the guard they arrived as blank cards."""
    from agent.loop import _cards_of

    brief = {"id": "c1", "callsign": "ACA123", "status": "pending", "phrase": "...", "reason": "..."}
    table = {"kind": "table", "columns": ["a"], "rows": [["b"]]}
    assert _cards_of({"cards": [brief, brief], "card": table}) == [table]
    assert _cards_of({"cards": [brief]}) == []
    assert _cards_of({"card": {"kind": "not-a-kind"}}) == []
    assert _cards_of({"card": table}) == [table]
