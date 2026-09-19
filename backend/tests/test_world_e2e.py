"""End-to-end without audio: a typed clearance, a scripted wrong readback, an alert."""
import asyncio

import pytest

from world import World


def collect():
    events = []
    return events, events.append


@pytest.fixture
def world():
    events, emit = collect()
    w = World(emit, synthesize=False, realtime=False)
    w.load("demo")
    w.start()  # nothing moves, and the radio is closed, until the world is started
    return w, events


def types(events):
    return [e["type"] for e in events]


ALL = ["wrong_value", "wrong_runway", "wrong_direction", "wrong_unit", "omitted_item",
       "ack_only", "wrong_aircraft", "missing_readback"]


def only(kind):
    return {k: (1.0 if k == kind else 0.0) for k in ALL}


def test_load_emits_state_plan_cards_radar(world):
    w, ev = world
    t = types(ev)
    assert "state" in t and "plan" in t and "radar" in t and "scoreboard" in t
    assert w.plan is not None and w.plan.conflicts == 0


def test_correct_readback_matches_and_plane_moves(world):
    w, ev = world
    w.fleet.set_error_rate(0.0)
    cs = w.sim.aircraft()[0].callsign
    ev.clear()
    asyncio.run(w.controller_text(f"{cs} descend and maintain flight level two four zero"))
    assert "clearance_opened" in types(ev)
    # pilot is scheduled 1.5 s later
    asyncio.run(w.tick(1.0)); asyncio.run(w.tick(1.0))
    t = types(ev)
    assert "alert" not in t
    upd = [e for e in ev if e["type"] == "clearance_updated"]
    assert upd and upd[-1]["payload"]["status"] == "matched"
    assert w.sim.get(cs).target_alt == 24000


def test_wrong_readback_alerts_and_plane_obeys_readback(world):
    w, ev = world
    cs = w.sim.aircraft()[0].callsign
    pilot = w.fleet.get(cs)
    pilot.error_rate = 1.0
    pilot.error_weights = only("wrong_value")
    ev.clear()
    asyncio.run(w.controller_text(f"{cs} descend and maintain flight level two four zero"))
    asyncio.run(w.tick(1.0)); asyncio.run(w.tick(1.0))
    alerts = [e for e in ev if e["type"] == "alert"]
    assert alerts, types(ev)
    a = alerts[0]["payload"]
    assert a["result"] in ("mismatch", "partial", "ambiguous") and a["callsign"] == cs
    assert a.get("correction_phrase")
    # the plane flew what the pilot said, not the clearance
    assert w.sim.get(cs).target_alt != 24000 or a["result"] == "ambiguous"
    assert w.errors_injected == 1 and w.errors_caught == 1


def test_tower_off_suppresses_alerts(world):
    w, ev = world
    w.set_tower(False)
    cs = w.sim.aircraft()[0].callsign
    pilot = w.fleet.get(cs)
    pilot.error_rate = 1.0
    pilot.error_weights = only("wrong_value")
    ev.clear()
    asyncio.run(w.controller_text(f"{cs} descend and maintain flight level two four zero"))
    asyncio.run(w.tick(1.0)); asyncio.run(w.tick(1.0))
    assert "alert" not in types(ev)
    assert w.sim.get(cs).target_alt != 24000


def test_missing_readback_times_out(world):
    w, ev = world
    cs = w.sim.aircraft()[0].callsign
    pilot = w.fleet.get(cs)
    pilot.error_rate = 1.0
    pilot.error_weights = only("missing_readback")
    ev.clear()
    asyncio.run(w.controller_text(f"{cs} descend and maintain flight level two four zero"))
    for _ in range(30):
        asyncio.run(w.tick(1.0))
    alerts = [e for e in ev if e["type"] == "alert"]
    assert alerts and alerts[0]["payload"]["error_type"] == "missing_readback"


def test_agent_builds_world(world):
    w, ev = world
    n0 = len(w.scenario.flights)
    reply = asyncio.run(w.agent_request("add a porter flight from the east and put a fighter jet through the middle"))
    assert "POE" in reply or "spawned" in reply
    assert len(w.scenario.flights) == n0 + 1
    assert any(a.is_intruder for a in w.sim.aircraft())
    assert "plan_update" in types(ev) and "disruption" in types(ev)


def test_auto_correct_after_wrong_readback(world):
    w, ev = world
    w.set_auto_speak(True)
    cs = w.sim.aircraft()[0].callsign
    pilot = w.fleet.get(cs)
    pilot.error_rate = 1.0
    pilot.error_weights = only("wrong_value")
    asyncio.run(w.controller_text(f"{cs} descend and maintain flight level two four zero"))
    asyncio.run(w.tick(1.0)); asyncio.run(w.tick(1.0))
    # after the correction the pilot reads back correctly and the plane flies the clearance
    assert w.sim.get(cs).target_alt == 24000


def test_snap_waypoints_uses_route_prior():
    from world import snap_waypoints
    wps = ["WAKOL", "GALTO", "ESTIR", "PIKAR", "CENTA"]
    assert snap_waypoints("ACA123 proceed direct at better", wps, ["ESTIR", "CENTA"]) == "ACA123 proceed direct ESTIR"
    assert snap_waypoints("direct pick are UAL210", wps) == "direct PIKAR UAL210"
    assert snap_waypoints("climb flight level 350", wps) == "climb flight level 350"


# --- lifecycle: nothing runs until Start ------------------------------------------------------


def fresh():
    events = []
    return World(events.append, synthesize=False, realtime=False), events


def last_state(events):
    return [e for e in events if e["type"] == "state"][-1]["payload"]


def test_new_world_is_idle_and_does_not_tick():
    w, ev = fresh()
    assert w.lifecycle == "idle"
    asyncio.run(w.tick(1.0))
    assert w.sim.t == 0 and not ev
    assert not w.start() and not w.reset()


def test_load_is_ready_with_plan_and_aircraft_but_clock_stays_at_zero():
    w, ev = fresh()
    w.load("demo")
    st = last_state(ev)
    assert st["lifecycle"] == "ready" and st["world_id"] == 1
    assert {s["name"] for s in st["scenarios"]} >= {"demo", "dense", "intruder"}
    assert w.plan is not None and w.cards
    radar = [e for e in ev if e["type"] == "radar"][-1]["payload"]
    assert radar["aircraft"], "aircraft due at t=0 must be visible before Start"
    for _ in range(5):
        asyncio.run(w.tick(1.0))
    assert w.sim.t == 0


def test_start_pause_resume_reset():
    w, ev = fresh()
    w.load("demo")
    assert w.start() and last_state(ev)["lifecycle"] == "running"
    asyncio.run(w.tick(1.0)); asyncio.run(w.tick(1.0))
    assert w.sim.t == 2
    assert w.pause() and last_state(ev)["lifecycle"] == "paused"
    asyncio.run(w.tick(1.0))
    assert w.sim.t == 2
    assert w.start()
    asyncio.run(w.tick(1.0))
    assert w.sim.t == 3
    assert w.reset()
    st = last_state(ev)
    assert st["lifecycle"] == "ready" and st["world_id"] == 2 and w.sim.t == 0
    assert all(c.status == "pending" for c in w.cards.values())


def test_fast_clock_substeps_and_sends_one_radar_frame():
    w, ev = fresh()
    w.load("demo"); w.start(); w.set_speed(20)
    assert last_state(ev)["speed"] == 20
    ev.clear()
    asyncio.run(w.tick(5.0))
    assert w.sim.t == 5
    assert len([e for e in ev if e["type"] == "radar"]) == 1
    w.set_speed(100000)
    assert w.speed == 120.0


def test_radio_is_closed_until_running():
    w, ev = fresh()
    w.load("demo")
    cs = w.sim.aircraft()[0].callsign
    ev.clear()
    asyncio.run(w.controller_text(f"{cs} descend and maintain flight level two four zero"))
    assert "clearance_opened" not in types(ev)
    assert any(e["type"] == "notice" and "Start" in e["payload"]["text"] for e in ev)


def test_world_ends_when_every_flight_has_left():
    w, ev = fresh()
    w.load("demo"); w.start()
    for _ in range(400):
        asyncio.run(w.tick(30.0))
        if w.lifecycle == "ended":
            break
    assert w.lifecycle == "ended" and last_state(ev)["lifecycle"] == "ended"
    t_end = w.sim.t
    asyncio.run(w.tick(30.0))
    assert w.sim.t == t_end
