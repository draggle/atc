"""Voice on: the controller says the card, the pilot reads it back, and Tower checks both ends."""
import asyncio
import importlib

from schemas import Item
from world import World

C = importlib.import_module("planner.cards")


def make(error_rate: float = 0.0):
    events: list[dict] = []
    w = World(events.append, synthesize=False, realtime=False)
    w.load("demo")
    w.error_rate = error_rate
    w.fleet.set_error_rate(error_rate)
    w.start()
    asyncio.run(w.tick(1.0))
    return w, events


def a_card(w: World):
    """A pending card for a flight on frequency, with one item we know how to get wrong."""
    for c in w.cards.values():
        if c.status == "pending" and c.callsign in w.sim.active and not c.minor and c.items:
            return c
    raise AssertionError("no card to work with")


def wrong_version(w: World, item: Item) -> Item:
    if item.type == "heading":
        return item.model_copy(update={"value": (int(item.value) + 90) % 360 or 360})
    if item.type == "altitude":
        return item.model_copy(update={"value": int(item.value) + 20})
    if item.type == "speed":
        return item.model_copy(update={"value": int(item.value) + 40})
    other = next(n for n, wp in w.sim.waypoints.items() if wp.kind != "hidden" and n != str(item.value))
    return item.model_copy(update={"value": other})


def run(w: World, seconds: int = 6):
    asyncio.run(w.tick(float(seconds)))


def of(events, typ):
    return [e["payload"] for e in events if e["type"] == typ]


def notices(events):
    return [p["text"] for p in of(events, "notice")]


# --------------------------------------------------------------------------- the switch

def test_voice_off_sends_what_was_waiting():
    w, events = make()
    w.set_voice(True)
    waiting = [c for c in w.cards.values() if c.status == "pending" and c.callsign in w.sim.active]
    assert waiting
    w.set_voice(False)
    assert not [c for c in w.cards.values() if c.status == "pending" and c.callsign in w.sim.active]
    assert all(c.via == "datalink" for c in waiting)


def test_voice_on_runs_fast_between_instructions_and_at_1x_while_there_is_something_to_say():
    w, events = make()
    w.speed = 20.0
    w.set_voice(True)
    assert w.speed == 20.0  # the chosen speed is kept: it is the speed between instructions
    for c in list(w.cards.values()):  # deal with the opening shortcuts so nothing is waiting
        if c.status == "pending" and c.callsign in w.sim.active:
            asyncio.run(w.controller_text(c.phrase))
    run(w, 40)
    assert not w.talking() and w.clock_speed() == 20.0
    w.add_disruption("storm")
    assert any(c.status == "pending" and c.cause for c in w.cards.values() if c.callsign in w.sim.active)
    assert w.talking() and w.clock_speed() == 1.0  # a reroute to say: real time
    w.set_ptt(True)
    assert w.clock_speed() == 1.0
    w.set_ptt(False)
    w.set_voice(False)
    assert w.clock_speed() == 20.0  # voice off never slows down


# --------------------------------------------------------------------------- what you said, against the card

def test_saying_the_card_goes_through_and_is_marked_said_by_you():
    w, events = make()
    card = a_card(w)
    asyncio.run(w.controller_text(card.phrase))
    assert card.status == "spoken" and card.via == "human" and card.heard_instead is None
    run(w)
    assert card.status == "validated"
    assert not of(events, "said_check")


def test_what_the_controller_says_wins_over_the_card():
    """The card is advice. Say something else and that is what happens: nothing is held, nobody
    is asked to confirm, the aircraft does it and Tower plans round it."""
    w, events = make()
    card = a_card(w)
    other = wrong_version(w, card.items[0])
    w.set_next_readback("correct")
    asyncio.run(w.controller_text(C.phrase_for(card.callsign, [other])))
    run(w, 4)
    assert not of(events, "said_check")  # no "did you mean the card?" any more
    assert [t for t in of(events, "transcript") if t["speaker"] == "pilot"]  # the pilot answered
    a = w.sim.active[card.callsign]
    if other.type == "route":
        assert list(a.route) == [str(other.value)]
    elif other.type == "heading":
        assert a.target_hdg == float(other.value) % 360
    assert any("doing what you said" in n for n in notices(events))
    assert card.clearance_id is None  # the card itself was never given: it was not what was said


def test_an_unsure_hearing_that_clashes_with_the_card_is_taken_as_the_card():
    """The simulated pilot acts on Tower's transcript of the controller, which no real pilot does.
    So when the speech model itself was unsure and the card says nearly the same, it was the card."""
    w, events = make()
    card = a_card(w)
    w.set_next_readback("correct")
    slip = C.phrase_for(card.callsign, [wrong_version(w, card.items[0])])
    asyncio.run(w._controller(slip, conf=0.4))
    run(w, 4)
    assert any("took the card" in n for n in notices(events))
    assert card.status in ("validated", "verified")


def test_nothing_understood_says_so():
    w, events = make()
    asyncio.run(w.controller_text("good morning everybody"))
    assert any("did not catch a callsign" in n for n in notices(events))


# --------------------------------------------------------------------------- scripted readbacks and corrections

def test_next_readback_can_be_scripted_once():
    w, events = make(error_rate=0.0)
    card = a_card(w)
    w.set_next_readback("wrong_value")
    asyncio.run(w.controller_text(card.phrase))
    run(w)
    assert of(events, "alert") and card.status == "error"
    assert w.next_readback == "random"  # one shot


def test_scripted_correct_beats_the_error_slider():
    w, events = make(error_rate=1.0)
    card = a_card(w)
    w.set_next_readback("correct")
    asyncio.run(w.controller_text(card.phrase))
    run(w)
    assert not of(events, "alert") and card.status == "validated"


def test_a_spoken_correction_closes_the_alert():
    w, events = make(error_rate=0.0)
    card = a_card(w)
    w.set_next_readback("wrong_value")
    asyncio.run(w.controller_text(card.phrase))
    run(w)
    assert card.status == "error"
    wrong = of(events, "alert")[-1]["clearance_id"]
    w.error_rate = 1.0
    w.fleet.set_error_rate(1.0)  # even so: a pilot who has just been corrected reads it back right
    asyncio.run(w.controller_text(card.phrase))
    run(w)
    resolved = of(events, "alert_resolved")
    assert len(resolved) == 1 and resolved[0]["clearance_id"] == wrong and resolved[0]["by"] == "correction"
    assert card.status == "validated"
    assert len(of(events, "alert")) == 1  # and the correction did not raise a second alert


def test_a_wrong_value_on_a_routing_is_a_different_fix():
    w, events = make(error_rate=0.0)
    card = next(c for c in w.cards.values() if c.status == "pending" and c.callsign in w.sim.active
                and [i.type for i in c.items] == ["route"])
    w.set_next_readback("wrong_value")
    asyncio.run(w.controller_text(card.phrase))
    run(w)
    alert = of(events, "alert")[-1]
    assert alert["error_type"] == "wrong_value"
    heard = [i["value"] for i in alert["heard"] if i["type"] == "route"]
    assert heard and heard[0] != card.items[0].value and heard[0] in w.sim.waypoints
    assert w.sim.active[card.callsign].route[-1] == heard[0]  # and the aircraft is flying to the wrong fix


# --------------------------------------------------------------------------- a spoken reroute is two cards

def _controller_says_everything(w: World, seconds: int, hold_followups_for: float = 0.0, delay: float = 5.0):
    """A controller who reads each card a few seconds after it appears. Returns what was said, per
    flight, and how deep anyone got into a zone."""
    import math
    seen: dict[str, float] = {}
    said: dict[str, list[tuple[str, str]]] = {}
    inside: dict[tuple[str, str], float] = {}

    async def go():
        for _ in range(seconds):
            await w.tick(1.0)
            now = w.sim.t
            for c in list(w.cards.values()):
                if c.status != "pending" or c.minor or c.callsign not in w.sim.active:
                    continue
                seen.setdefault(c.id, now)
                wait = delay + (hold_followups_for if c.origin == "followup" else 0.0)
                if now - seen[c.id] >= wait:
                    said.setdefault(c.callsign, []).append((c.origin, c.phrase.split(", ", 1)[1]))
                    await w.controller_text(c.phrase)
            for z in w.sim.zones:
                for a in w.sim.active.values():
                    depth = z.radius_nm - math.hypot(a.x - z.x_nm, a.y - z.y_nm)
                    if not a.is_intruder and depth > 0:
                        inside[(z.id, a.callsign)] = max(inside.get((z.id, a.callsign), 0.0), depth)
    asyncio.run(go())
    return said, inside


def test_a_spoken_reroute_is_a_heading_then_a_direct_and_nothing_else():
    w, _ = make()
    _controller_says_everything(w, 600)
    w.add_disruption("storm")
    said, inside = _controller_says_everything(w, 1200)
    assert inside == {}
    two_step = {cs: rows for cs, rows in said.items() if any("heading" in p for _, p in rows)}
    assert two_step, "the storm should have put at least one flight on a heading"
    finished = 0
    for cs, rows in two_step.items():
        phrases = [p for _, p in rows]
        directs = [p for p in phrases if p.startswith("proceed direct")]
        assert len(directs) == len(set(directs)), f"{cs} was told the same direct twice: {phrases}"
        if any(o == "followup" for o, _ in rows):
            finished += 1
            a = w.sim.active.get(cs)
            if a is not None:
                assert a.target_hdg is None and len(a.route) == 1  # back on its own navigation
    assert finished >= 1, "nobody was brought back on course"


def test_the_line_on_the_map_follows_the_aircraft_after_a_spoken_heading():
    import numpy as np
    w, _ = make()
    _controller_says_everything(w, 600)
    w.add_disruption("storm")
    _controller_says_everything(w, 60)
    on_heading = [a for a in w.sim.active.values() if a.target_hdg is not None and not a.is_intruder]
    assert on_heading
    _controller_says_everything(w, 120)
    for a in on_heading:
        path = next((p for p in w.plan.paths if p.callsign == a.callsign), None)
        if a.callsign not in w.sim.active or path is None:
            continue
        arr = np.asarray(path.samples, dtype=float).reshape(-1, 4)
        assert float(np.hypot(arr[:, 1] - a.x, arr[:, 2] - a.y).min()) < 2.0


def test_saying_the_second_card_late_is_still_safe():
    w, _ = make()
    _controller_says_everything(w, 600)
    w.add_disruption("storm")
    said, inside = _controller_says_everything(w, 1200, hold_followups_for=75.0)
    assert inside == {}  # a minute late back on course costs miles, never separation from the storm
    assert w.monitor.losses == 0


# --------------------------------------------------------------------------- one voice, a queue of cards

_QUEUE: dict = {}


def _one_controller_after_a_storm():
    """The demo as it is really worked: ONE controller, so one exchange at a time, 14 s each, top
    card first. Cards wait their turn, which is what the two tests above never exercise. Run once.

    Returns {callsign: [(t, origin, phrase)]} of what was said after the storm, how deep anyone got
    into it, new losses of separation, and for every back-on-course card whether going direct was
    clear of the storm, margin included, from where the aircraft was when the card went up.
    """
    if _QUEUE:
        return _QUEUE
    import math
    PL = importlib.import_module("planner.plan")
    w, _ = make()
    said: dict[str, list[tuple[float, str, str]]] = {}
    inside: dict[tuple[str, str], float] = {}
    offered: dict[str, bool] = {}
    state = {"free_at": 0.0, "record": False}

    def weight(c):
        return 0 if c.status == "error" else 1 if c.emergency else 2 if (c.cause or c.origin in ("followup", "release")) else 3

    async def go(seconds: int):
        for _ in range(seconds):
            await w.tick(1.0)
            now = w.sim.t
            todo = sorted([c for c in w.cards.values() if c.status == "pending" and not c.minor
                           and c.callsign in w.sim.active], key=lambda c: (weight(c), c.urgency_s))
            for c in todo:
                a = w.sim.active[c.callsign]
                if c.origin == "followup" and c.id not in offered:
                    gate = w.sim.waypoints[a.route[-1]]
                    here = PL.sample_path(PL.flyable([(a.x, a.y), (gate.x_nm, gate.y_nm)], a.hdg, a.gs),
                                          a.gs, now, a.alt, a.target_alt)
                    offered[c.id] = not PL.zone_mask(here, w.sim.zones, PL.ZONE_MARGIN_NM).any()
            if todo and now >= state["free_at"]:
                c = todo[0]
                if state["record"]:
                    said.setdefault(c.callsign, []).append((now, c.origin, c.phrase.split(", ", 1)[1]))
                await w.controller_text(c.phrase)
                state["free_at"] = now + 14.0
            for z in w.sim.zones:
                for a in w.sim.active.values():
                    depth = z.radius_nm - math.hypot(a.x - z.x_nm, a.y - z.y_nm)
                    if not a.is_intruder and depth > 0:
                        inside[(z.id, a.callsign)] = max(inside.get((z.id, a.callsign), 0.0), depth)

    asyncio.run(go(600))
    before = w.monitor.losses
    w.add_disruption("storm")
    state["record"] = True
    asyncio.run(go(500))
    _QUEUE.update(said=said, inside=inside, losses=w.monitor.losses - before, offered=offered)
    return _QUEUE


def test_a_queue_of_cards_is_worked_through_without_the_storm_or_each_other():
    q = _one_controller_after_a_storm()
    assert q["inside"] == {} and q["losses"] == 0
    assert sum(1 for rows in q["said"].values() if any("heading" in p for _, _, p in rows)) >= 3, \
        "the point of this run is several reroutes waiting for one voice"


def test_a_heading_that_was_read_back_is_not_followed_by_a_near_copy():
    """It used to be: say "heading 039", hear it back, and two seconds later be handed "heading 034".
    The detour was trimmed to just clear the margin for a turn at one exact moment, the real turn
    came a few seconds off, and the very heading just issued was judged unsafe by a hair."""
    for cs, rows in _one_controller_after_a_storm()["said"].items():
        headings = [(t, p) for t, _, p in rows if "heading" in p]
        for (t1, p1), (t2, p2) in zip(headings, headings[1:]):
            assert t2 - t1 > 60, f"{cs}: '{p1}' at {t1:.0f}s then '{p2}' at {t2:.0f}s"


def test_back_on_course_never_bounces_into_another_heading():
    """And: "proceed direct", then at once a new heading, then direct again, for as long as anyone
    kept reading. Direct was being offered for a point 25 s ahead and flown from short of it."""
    q = _one_controller_after_a_storm()
    for cs, rows in q["said"].items():
        origins = [o for _, o, _ in rows]
        if "followup" in origins:
            after = rows[origins.index("followup") + 1:]
            assert not [p for _, _, p in after if "heading" in p], f"{cs}: {rows}"
    assert q["offered"] and all(q["offered"].values()), "direct was offered before it was clear from where the aircraft was"


def test_a_heading_card_that_waits_is_still_the_one_to_say():
    """Nobody says anything for a minute and a half after the storm. Every heading still on the
    list must be the heading the planner wants now, from where that aircraft has got to."""
    w, _ = make()
    _controller_says_everything(w, 600)
    w.add_disruption("storm")
    run(w, 1)
    first = {c.callsign: float(i.value) for c in w.cards.values() if c.status == "pending"
             for i in c.items if i.type == "heading"}
    assert first
    for _ in range(90):
        run(w, 1)
    wanted = {p.callsign: float(ch.value) for p in w.plan.paths for ch in C._changes(p) if ch.kind == "heading"}
    shown = {c.callsign: float(i.value) for c in w.cards.values() if c.status == "pending" and c.callsign in w.sim.active
             for i in c.items if i.type == "heading"}
    assert shown
    for cs, hdg in shown.items():
        assert cs in wanted and abs((hdg - wanted[cs] + 180) % 360 - 180) <= C.SAME_HEADING_DEG, (cs, hdg, wanted.get(cs))
    # and saying one now does not bring a second heading straight behind it
    cs = next(iter(shown))
    card = next(c for c in w.cards.values() if c.callsign == cs and c.status == "pending")
    asyncio.run(w.controller_text(card.phrase))
    for _ in range(20):
        run(w, 1)
    again = [c for c in w.cards.values() if c.callsign == cs and c.status == "pending"
             and any(i.type == "heading" for i in c.items)]
    assert not again, [c.phrase for c in again]


# --------------------------------------------------------------------------- nothing may silence the screen

def test_every_event_of_a_spoken_exchange_can_be_sent():
    """One payload that was a model object, not a dict, killed the sender and froze every screen."""
    import json

    import app as A
    events: list[dict] = []
    w = World(events.append, synthesize=False, realtime=False)
    w.load("demo")
    w.start()
    two_part = None
    for _ in range(40):  # wait for a flight with a two-part card to check in
        asyncio.run(w.tick(10.0))
        two_part = next((c for c in w.cards.values() if len(c.items) == 2 and c.status == "pending"
                         and c.callsign in w.sim.active), None)
        if two_part is not None:
            break
    assert two_part is not None, "the demo scenario always has DAL789's direct-and-climb card"
    # Tower says it and hears its own items the other way round, as it did live
    swapped = C.phrase_for(two_part.callsign, list(reversed(two_part.items)))

    async def say():
        await w._controller(swapped, card=two_part)
        await w.tick(10.0)
    asyncio.run(say())
    for ev in events:
        json.dumps(ev, default=A._json_default)  # raises if anything cannot be sent
        assert isinstance(ev["payload"], dict), ev["type"]
    assert two_part.status in ("spoken", "validated")


def test_the_sender_survives_an_event_it_cannot_send():
    import app as A

    class Unsendable:
        pass

    async def go():
        hub = A.Hub()
        task = asyncio.create_task(hub.pump())
        hub.emit({"type": "notice", "payload": {"x": Unsendable()}, "t": 0})
        hub.emit({"type": "notice", "payload": {"text": "still alive"}, "t": 0})
        await asyncio.sleep(0.05)
        alive = not task.done()
        task.cancel()
        return alive
    assert asyncio.run(go())



# --------------------------------------------------------------------------- responsiveness

def test_the_aircraft_turns_when_the_pilot_keys_up_not_when_the_voice_is_ready():
    """Making the pilot's voice takes one to three seconds (a network call to the voice service).
    The aircraft used to wait for it. Now the reply is decided, flown, and only then spoken."""
    w, events = make()
    cs = next(iter(w.sim.active))
    pilot = w.fleet.get(cs)
    seen: dict = {}
    real = pilot.voice_it

    def voice_it(resp, clearance):
        seen["target_when_voice_starts"] = w.sim.active[cs].target_hdg
        seen["radar_frames_before_voice"] = sum(1 for e in events if e["type"] == "radar"
                                                and any(a["callsign"] == cs and a["target_hdg_deg"] == 270.0
                                                        for a in e["payload"]["aircraft"]))
        return real(resp, clearance)

    pilot.voice_it = voice_it
    w.set_next_readback("correct")
    asyncio.run(w.controller_text(f"{C.say_callsign(cs)}, turn left heading two seven zero"))
    run(w, 3)
    assert seen["target_when_voice_starts"] == 270.0  # already turning before a sound is made
    assert seen["radar_frames_before_voice"] >= 1  # and the screen was told at once, not on the next tick
