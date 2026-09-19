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


def test_tower_trusts_its_own_card_over_its_own_ears(world):
    """Tower says "direct ESTIR" and hears itself say "direct JIGOR": the card is what was said."""
    from schemas import InstructionCard, Item, OpenClearance
    w, ev = world
    cs = w.sim.aircraft()[0].callsign
    card = InstructionCard(id="card-x", callsign=cs, items=[Item(type="route", value="ESTIR", action="direct")],
                           phrase=f"{cs} proceed direct ESTIR", reason="test", urgency_s=60.0)
    w.cards[card.id] = card
    asyncio.run(w._controller(f"{cs} proceed direct JIGOR", card=card))
    opened = [OpenClearance.model_validate(e["payload"]) for e in ev if e["type"] == "clearance_opened"]
    assert opened, "a clearance opens"
    assert all([i.value for i in c.items] == ["ESTIR"] for c in opened), "the screen never sees the misheard fix"
    assert all(w.core.store.get(c.id).items[0].value == "ESTIR" for c in opened), "nor does the checker"


def test_a_correction_is_never_corrected_again(world, monkeypatch):
    """If the corrected readback still alerts, Tower hands it to the human. It must not loop."""
    w, ev = world
    w.set_auto_speak(True)
    cs = w.sim.aircraft()[0].callsign
    calls = []
    real = w._auto_correct

    async def counted(c, phrase):
        calls.append(c.id)
        assert len(calls) < 5, "auto-correct is looping"
        await real(c, phrase)

    monkeypatch.setattr(w, "_auto_correct", counted)
    # every readback, including the one after a correction, raises an alert
    monkeypatch.setattr(w.core, "on_transmission", lambda tx, active, states: (
        [{"type": "alert", "payload": {"clearance_id": None, "correction_phrase": f"{cs} negative"}}]
        if tx.speaker == "pilot" else w.core.__class__.on_transmission(w.core, tx, active, states)))
    asyncio.run(w.controller_text(f"{cs} descend and maintain flight level two four zero"))
    asyncio.run(w.tick(1.0)); asyncio.run(w.tick(1.0)); asyncio.run(w.tick(1.0))
    # Auto mode may issue cards of its own meanwhile: each clearance is corrected once, a fix never
    assert calls and len(calls) == len(set(calls)) and not any(c.endswith("-fix") for c in calls), calls
    assert any(e["type"] == "notice" and "still" in e["payload"]["text"].lower() for e in ev)


def test_a_pilots_clear_wrong_fix_is_not_snapped_back_to_the_expected_one():
    """The route hint rescues a garbled fix. It must not overrule a pilot who said another real fix."""
    from world import snap_waypoints
    wps = ["WAKOL", "GALTO", "ESTIR", "PIKAR", "CENTA", "TULEK", "ZAMIR"]
    # the controller's voice: the hint wins, as before
    assert snap_waypoints("direct tulick WJA456", wps, ["PIKAR"]) == "direct PIKAR WJA456"
    # a pilot's readback: a clear match to another real fix is what was said
    assert snap_waypoints("direct tulick WJA456", wps, ["PIKAR"], trust_hint=False) == "direct TULEK WJA456"
    assert snap_waypoints("direct zamir UAL210", wps, ["ESTIR"], trust_hint=False) == "direct ZAMIR UAL210"
    # a pilot's garbled CORRECT readback is still rescued by the hint
    assert snap_waypoints("ACA123 direct at better", wps, ["ESTIR", "CENTA"], trust_hint=False) == "ACA123 direct ESTIR"
    assert snap_waypoints("direct jigor ENY3812", ["GEGOR", "MANUM", "KITOR"], ["GEGOR"], trust_hint=False) == "direct GEGOR ENY3812"


def test_snap_waypoints_reads_the_fix_before_direct_form():
    from world import snap_waypoints
    wps = ["WAKOL", "GALTO", "ESTIR", "PIKAR", "CENTA", "TULEK", "ZAMIR"]
    assert snap_waypoints("estir direct ACA123", wps, ["ESTIR"], trust_hint=False) == "direct ESTIR ACA123"
    # filler words are not part of a fix name and stay where they were
    assert snap_waypoints("ACA123 roger at better direct", wps, ["ESTIR"], trust_hint=False) == "ACA123 roger at direct ESTIR"
    # a wrong fix said this way is still a wrong fix
    assert snap_waypoints("centa direct UAL210", wps, ["ESTIR"], trust_hint=False) == "direct CENTA UAL210"
    # a refusal or a question is never rescued into a readback of the expected fix
    for said in ("say again direct ACA123", "unable direct ACA123", "negative direct ACA123", "request direct ACA123"):
        assert snap_waypoints(said, wps, ["ESTIR"], trust_hint=False) == said
    # the ordinary order is left exactly as it was
    assert snap_waypoints("ACA123 proceed direct estir", wps, ["ESTIR"]) == "ACA123 proceed direct ESTIR"
    assert snap_waypoints("cleared direct estir ACA123", wps, ["ESTIR"], trust_hint=False) == "cleared direct ESTIR ACA123"


@pytest.mark.parametrize("fmt", ["direct {f}, {tel}", "cleared direct {f}, {tel}", "{f} direct, {tel}", "{tel}, {f} direct"])
def test_every_way_our_pilots_say_a_direct_matches_when_it_is_right(fmt):
    """An alert must never fire for a correct readback."""
    from world import _spoken
    ev = []
    w = World(ev.append, synthesize=False, realtime=False)
    w.load("demo"); w.start(); w.fleet.set_error_rate(0.0)
    a = next(x for x in w.sim.aircraft() if len(w.sim.get(x.callsign).route) >= 2)
    cs, fix = a.callsign, w.sim.get(a.callsign).route[-1]
    asyncio.run(w.controller_text(f"{cs} proceed direct {fix}"))
    tx = w._new_tx(fmt.format(f=fix.lower(), tel=_spoken(cs)), "pilot")
    events = w.core.on_transmission(tx, list(w.sim.active), w.sim.aircraft())
    assert not [e for e in events if e["type"] == "alert"], (tx.text_norm, events)
    upd = [e["payload"] for e in events if e["type"] == "clearance_updated"]
    assert upd and (upd[-1]["status"] if isinstance(upd[-1], dict) else upd[-1].status) == "matched"


def test_wrong_fix_readback_alerts_and_the_plane_goes_to_the_wrong_fix(world):
    w, ev = world
    a = next(x for x in w.sim.aircraft() if len(w.sim.get(x.callsign).route) >= 2)
    cs = a.callsign
    fix = w.sim.get(cs).route[-1]
    pilot = w.fleet.get(cs)
    pilot.error_rate = 1.0
    pilot.error_weights = only("wrong_value")
    ev.clear()
    asyncio.run(w.controller_text(f"{cs} proceed direct {fix}"))
    asyncio.run(w.tick(1.0)); asyncio.run(w.tick(1.0))
    alerts = [e["payload"] for e in ev if e["type"] == "alert"]
    assert alerts, types(ev)
    al = alerts[0]
    assert al["error_type"] == "wrong_value" and al["callsign"] == cs
    heard = [i["value"] if isinstance(i, dict) else i.value for i in al["heard"]]
    assert heard and heard[0] != fix and heard[0] in w.spoken_waypoints(), f"heard {heard}, cleared {fix}"
    assert w.sim.get(cs).route[:1] == [heard[0]], "the plane obeys the readback, not the clearance"


def _opened(ev):
    return [e for e in ev if e["type"] == "clearance_opened"]


def test_saying_the_same_instruction_twice_is_one_instruction(world):
    """A double click on "Say it", or a controller repeating themselves, is not two clearances.

    Two open clearances for one instruction mean one readback closes one of them and the other
    times out as "NO READBACK, heard nothing" about a pilot who answered correctly."""
    w, ev = world
    w.fleet.set_error_rate(0.0)
    cs = w.sim.aircraft()[0].callsign
    ev.clear()
    asyncio.run(w.controller_text(f"{cs} descend and maintain flight level two four zero"))
    asyncio.run(w.controller_text(f"{cs} descend and maintain flight level two four zero"))
    assert len(_opened(ev)) == 1, "the repeat refreshes the open clearance, it does not open another"
    assert len(w.core.store.open_clearances(cs)) == 1
    for _ in range(40):
        asyncio.run(w.tick(1.0))
    assert not [e for e in ev if e["type"] == "alert"], [e["payload"].get("reason") for e in ev if e["type"] == "alert"]
    assert w.sim.get(cs).target_alt == 24000


def test_a_different_instruction_to_the_same_aircraft_is_still_a_new_clearance(world):
    w, ev = world
    cs = w.sim.aircraft()[0].callsign
    ev.clear()
    asyncio.run(w.controller_text(f"{cs} descend and maintain flight level two four zero"))
    asyncio.run(w.controller_text(f"{cs} turn left heading two seven zero"))
    assert len(_opened(ev)) == 2


def test_say_it_pressed_twice_speaks_the_card_once(world):
    from schemas import InstructionCard, Item
    w, ev = world
    cs = w.sim.aircraft()[0].callsign
    card = InstructionCard(id="card-dbl", callsign=cs, items=[Item(type="altitude", value=240, unit="FL", action="descend")],
                           phrase=f"{cs} descend and maintain flight level two four zero", reason="test", urgency_s=60.0)
    w.cards[card.id] = card
    ev.clear()

    async def both():
        await asyncio.gather(w.speak_card(card.id), w.speak_card(card.id))
        await w.speak_card(card.id)  # and a third, late press

    asyncio.run(both())
    assert len(_opened(ev)) == 1


def test_an_uncertain_verdict_reaches_the_screen_with_something_to_say(world, monkeypatch):
    """The resolver is never silent: "uncertain" must end in a card that tells the controller what to do."""
    from tower.resolver.agent import Resolution
    w, ev = world
    w.fleet.set_error_rate(0.0)
    cs = w.sim.aircraft()[0].callsign
    asyncio.run(w.controller_text(f"{cs} descend and maintain flight level two four zero"))

    def gave_up(clearance, verdict, tx, extra_context=None):
        v = verdict.model_copy(update={"result": "ambiguous", "decided_by": "resolver", "confidence": 0.5,
                                       "reason": "uncertain: resolver ran out of time"})
        return Resolution(v, [], None)

    monkeypatch.setattr(w.core.resolver, "resolve", gave_up)
    ev.clear()
    tx = w._new_tx(f"descend flight level two one zero {cs}", "pilot", conf=0.4)  # unclear, and low confidence
    events = w.core.on_transmission(tx, list(w.sim.active), w.sim.aircraft())
    alerts = [e["payload"] for e in events if e["type"] == "alert"]
    assert alerts, [e["type"] for e in events]
    a = alerts[0]
    assert a["result"] == "ambiguous" and a["decided_by"] == "resolver"
    assert "confirm" in (a.get("correction_phrase") or "").lower()
    assert [e for e in events if e["type"] == "clearance_updated"][-1]["payload"]["status"] == "uncertain"


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
