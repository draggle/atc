"""The squack agent: registry, keyword router, loop caps, wake policy, director, agent mode."""
import asyncio
import math

import pytest

from agent import registry as R
from agent.loop import NEEDS_MODEL, SquackAgent, route
from agent.wake import Director, Stage, WakePolicy
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


@pytest.fixture
def fake_jobs(monkeypatch):
    """No subprocess in the suite: the sim tools talk to a stand-in with the same interface."""
    jobs = {}

    def start_job(kind, params, on_progress):
        jid = f"job{len(jobs) + 1}"
        jobs[jid] = {"job_id": jid, "kind": kind, "status": "running", "progress": 0.0, "eta_s": None, "params": params}
        on_progress(dict(jobs[jid]))
        return jid

    def job_status(jid):
        return dict(jobs.get(jid) or {"job_id": jid, "kind": None, "status": "unknown", "progress": 0.0, "error": "no such job"})

    def cancel_job(jid):
        if jid in jobs:
            jobs[jid]["status"] = "cancelled"

    monkeypatch.setattr(R, "start_job", start_job)
    monkeypatch.setattr(R, "job_status", job_status)
    monkeypatch.setattr(R, "cancel_job", cancel_job)
    return jobs


# ----------------------------------------------------------------------------------- registry

DEFAULT_ARGS = {
    "world.set_speed": {"speed": 5}, "world.set_voice": {"on": False}, "world.set_sliders": {"buffer_nm": 4},
    "world.load": {"name": "demo"}, "world.disrupt": {"kind": "storm"}, "world.remove_disruption": {},
    "world.lifecycle": {"action": "start"}, "world.multiply_traffic": {"factor": 1.5},
    "world.spawn_flight": {"airline": "DAL", "from_side": "west"}, "world.nudge": {},
    "ui.focus": {}, "ui.follow": {}, "ui.camera": {"pitch": 45, "bearing": 30}, "ui.line_view": {"view": "changed"},
    "ui.panel": {"panel": "scoreboard"}, "ui.mode": {"mode": "normal"},
    "query.aircraft": {"filters": [{"field": "fl", "op": "gt", "value": 200}]}, "query.pairs": {"max_nm": 30},
    "query.cards": {"status": "all"}, "query.log": {}, "query.scoreboard": {}, "query.timeline": {"since_s": 600},
    "explain.card": {}, "explain.flight": {}, "explain.disruption": {}, "explain.replan": {},
    "sim.montecarlo": {"runs": 2}, "sim.sweep": {"densities": [1, 2]}, "sim.status": {"job_id": "job1"},
    "sim.cancel": {"job_id": "job1"},
}


def test_every_tool_runs_on_a_loaded_world_without_raising(world, fake_jobs):
    w, ev = world
    cs = w.sim.aircraft()[0].callsign
    assert set(DEFAULT_ARGS) == set(R.names()), "keep DEFAULT_ARGS in step with the registry"
    for name in R.names():
        args = dict(DEFAULT_ARGS[name])
        if name in ("world.nudge", "ui.focus", "ui.follow", "query.log", "explain.card", "explain.flight"):
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


def test_nudge_replans_one_flight_and_never_opens_a_clearance(world):
    w, ev = world
    cs = w.sim.aircraft()[0].callsign
    before, ev[:] = w.buffer_nm, []
    out = R.execute(w, "world.nudge", {"callsign": cs})
    assert "error" not in out and w.buffer_nm == before  # the +2 NM is temporary
    assert "clearance_opened" not in types(ev) or all(
        e["payload"]["callsign"] != cs or w.auto_speak for e in of(ev, "clearance_opened"))
    assert "plan_update" in types(ev)


def test_registry_refuses_tools_that_act_on_traffic_other_than_nudge(world, monkeypatch):
    w, _ = world
    monkeypatch.setitem(R.REGISTRY, "world.fly", R.Tool("world.fly", "x", {}, lambda w, a: {"ok": 1}, acts_on_traffic=True))
    assert "refused" in R.execute(w, "world.fly", {})["summary"]


def test_sim_tools_delegate_to_the_job_runner_and_emit_sim_job(world, fake_jobs):
    w, ev = world
    ev.clear()
    out = R.execute(w, "sim.montecarlo", {"runs": 50, "density": 2})
    assert out["job_id"] == "job1" and out["status"] == "running"
    assert "sim_job" in types(ev)
    assert R.execute(w, "sim.status", {"job_id": "job1"})["status"] == "running"
    R.execute(w, "sim.cancel", {"job_id": "job1"})
    assert fake_jobs["job1"]["status"] == "cancelled"


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
    ("who is above FL300 heading west", "query.aircraft"), ("separate {cs}", "world.nudge"),
    ("agent mode", "ui.mode"), ("what did that storm cost", "explain.disruption"),
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


def test_a_model_error_still_ends_in_an_answer(world):
    w, ev = world

    class Broken:
        is_mock = False

        def chat(self, *a, **k):
            raise RuntimeError("boom")

    ans = SquackAgent(w, Broken()).handle_message("hello")
    assert "failed" in ans.text and types(ev)[-1] == "answer"


def test_handle_events_composes_a_stage_answer_with_a_model(world):
    w, ev = world
    agent = SquackAgent(w, Chatty(["explain__replan"]))
    ans = agent.handle_events([event("plan_update", {"changed": ["X"], "trigger": "test"}, t=1.0)])
    assert ans is not None and ans.for_ == "event" and ans.cards
    assert SquackAgent(w, MockLLM()).handle_events([event("alert", {}, t=1.0)]) is None


# ---------------------------------------------------------------------------- wake and stage

def test_wake_policy_wakes_only_on_the_listed_events():
    wp = WakePolicy(clock=lambda: 0.0)
    assert not wp.observe(event("radar", {}))
    assert not wp.observe(event("plan_update", {"changed": [], "trigger": "periodic"}))
    assert wp.observe(event("plan_update", {"changed": ["ACA123"], "trigger": "periodic"}))
    assert wp.observe(event("alert", {"callsign": "ACA123"}))
    assert wp.observe(event("disruption", {"id": "STORM1", "active": True}))
    assert not wp.observe(event("state", {"lifecycle": "ready"}))  # the first state seen sets the baseline
    assert not wp.observe(event("state", {"lifecycle": "ready"}))
    assert wp.observe(event("state", {"lifecycle": "running"}))
    low = event("risk", {"pairs": [{"a": "A", "b": "B", "p_max": REPLAN_P - 0.01}]})
    high = event("risk", {"pairs": [{"a": "A", "b": "B", "p_max": REPLAN_P + 0.1}]})
    assert not wp.observe(low)
    assert wp.observe(high)
    assert not wp.observe(high)  # the same pair again is not a new crossing
    assert not wp.observe(event("risk", {"pairs": []}))
    assert wp.observe(high)  # it dropped out and came back: a new crossing


def test_wake_policy_debounces_and_batches():
    now = [0.0]
    wp = WakePolicy(clock=lambda: now[0])
    wp.observe(event("alert", {"callsign": "A"}))
    batch = wp.poll()
    assert batch and len(batch) == 1
    for t in (1.0, 2.0, 3.0):
        now[0] = t
        wp.observe(event("disruption", {"id": f"S{t}", "active": True}))
        assert wp.poll() is None
    now[0] = 5.0
    batch = wp.poll()
    assert batch and [e["payload"]["id"] for e in batch] == ["S1.0", "S2.0", "S3.0"]
    assert wp.poll() is None
    wp.staged(5.0, True)
    assert not wp.idle_due(30.0) and wp.idle_due(66.0) and not wp.idle_due(70.0)


def test_director_places_the_alert_card_first(world):
    w, _ = world
    cs = w.sim.aircraft()[0].callsign
    batch = [event("plan_update", {"changed": [cs], "trigger": "periodic"}, t=1.0),
             event("alert", {"callsign": cs, "reason": "read back FL250, cleared FL240", "correction_phrase": "negative"}, t=2.0)]
    stage = Director.stage_for(batch, w)
    assert stage.by == "director" and stage.ttl_s == 120.0
    assert stage.slots[0]["kind"] == "aircraft" and stage.slots[0]["callsign"] == cs
    assert "FL250" in stage.slots[0]["issue"] and "negative" in stage.slots[0]["issue"]
    assert stage.slots[1]["kind"] == "list" and cs in stage.text


def test_director_places_a_comparison_and_the_worst_flight_for_a_disruption(world):
    w, ev = world
    d = w.add_disruption("storm")
    stage = Director.stage_for([e for e in ev if e["type"] == "disruption"], w)
    assert stage.slots[0]["kind"] == "comparison" and d.id in stage.slots[0]["title"]
    rerouted = R.disruption_effect(w, d.id)
    if rerouted:
        assert stage.slots[1]["kind"] == "aircraft" and stage.slots[1]["callsign"] == rerouted[0]["callsign"]
        assert stage.slots[1]["live"] == {"aircraft": rerouted[0]["callsign"]}
    assert d.id in stage.text and len(stage.slots) <= 3


def test_director_places_the_change_list_for_a_replan(world):
    w, _ = world
    cs = [a.callsign for a in w.sim.aircraft()[:2]]
    stage = Director.stage_for([event("plan_update", {"changed": cs, "trigger": "risk A/B"}, t=3.0)], w)
    assert len(stage.slots) == 1 and stage.slots[0]["kind"] == "list"
    assert [it["text"].split(":")[0] for it in stage.slots[0]["items"]] == cs
    assert "risk A/B" in stage.slots[0]["title"]


def test_director_places_a_risk_pair(world):
    w, _ = world
    a, b = [x.callsign for x in w.sim.aircraft()[:2]]
    stage = Director.stage_for([event("risk", {"pairs": [{"a": a, "b": b, "p_max": 0.42, "eta_s": 71, "min_sep_nm_p5": 3.1}]})], w)
    assert stage.slots[0]["kind"] == "aircraft" and stage.slots[0]["callsign"] == a and "42%" in stage.slots[0]["issue"]


def test_normal_mode_talks_but_emits_no_stage(world):
    w, ev = world
    w.wake.clock = lambda: 100.0
    ev.clear()
    d = w.add_disruption("storm")
    asyncio.run(w.tick(1.0))
    assert "stage" not in types(ev) and w.wake.pending == [] and w._event_backlog == []
    said = [e for e in of(ev, "answer") if e["payload"]["for"] == "event"]
    assert len(said) == 1 and d.id in said[0]["payload"]["text"]
    assert said[0]["payload"]["cards"] and said[0]["payload"]["cards"][0]["kind"] == "comparison"


def test_event_answers_are_rate_limited_and_wait_for_the_users_turn(world):
    w, ev = world
    now = [100.0]
    w.wake.clock = lambda: now[0]
    w.add_disruption("storm"); asyncio.run(w.tick(1.0))
    assert len([e for e in of(ev, "answer") if e["payload"]["for"] == "event"]) == 1
    now[0] = 106.0  # past the 5 s wake debounce, inside the 8 s answer gap: it waits
    w.add_disruption("fighter"); asyncio.run(w.tick(1.0))
    assert len([e for e in of(ev, "answer") if e["payload"]["for"] == "event"]) == 1 and w._event_backlog
    now[0] = 109.0
    w._agent_busy = True  # the user is mid-turn: still waits
    asyncio.run(w.tick(1.0))
    assert len([e for e in of(ev, "answer") if e["payload"]["for"] == "event"]) == 1
    w._agent_busy = False
    asyncio.run(w.tick(1.0))
    said = [e for e in of(ev, "answer") if e["payload"]["for"] == "event"]
    assert len(said) == 2 and "VIPER" in said[-1]["payload"]["text"] and w._event_backlog == []


def test_agent_mode_emits_a_stage_and_clears_it_when_idle(world):
    w, ev = world
    w.wake.clock = lambda: 100.0
    w.add_disruption("storm")
    asyncio.run(w.tick(1.0))
    assert "stage" not in types(ev)
    w.set_ui_mode("agent")  # entering agent mode stages what is going on now
    assert types(ev).count("stage") == 1 and ev[-1]["payload"]["by"] == "director" and ev[-1]["payload"]["slots"]
    assert of(ev, "state")[-1]["payload"]["ui_mode"] == "agent"
    ev.clear()
    w.wake.clock = lambda: 200.0
    w.add_disruption("fighter")
    asyncio.run(w.tick(1.0))
    stages = of(ev, "stage")
    assert len(stages) == 1 and stages[0]["payload"]["by"] == "director"
    assert 0 < len(stages[0]["payload"]["slots"]) <= 3 and stages[0]["payload"]["ttl_s"] > 0
    assert [e for e in of(ev, "answer") if e["payload"]["for"] == "event"]  # and it says so
    w.wake.clock = lambda: 300.0  # idle for a minute: the stage is cleared once
    asyncio.run(w.tick(1.0)); asyncio.run(w.tick(1.0))
    assert [s["payload"]["slots"] for s in of(ev, "stage")][-1] == [] and len(of(ev, "stage")) == 2


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


def test_stage_model_defaults():
    assert Stage().payload() == {"slots": [], "ttl_s": 0.0, "by": "director", "text": ""}
