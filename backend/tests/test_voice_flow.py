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

def test_voice_on_slows_the_clock_and_voice_off_sends_what_was_waiting():
    w, events = make()
    w.speed = 20.0
    w.set_voice(True)
    assert w.speed == 1.0 and not w.auto_speak
    assert any("1x" in n for n in notices(events))
    waiting = [c for c in w.cards.values() if c.status == "pending" and c.callsign in w.sim.active]
    assert waiting
    w.set_voice(False)
    assert not [c for c in w.cards.values() if c.status == "pending" and c.callsign in w.sim.active]
    assert all(c.via == "datalink" for c in waiting)


# --------------------------------------------------------------------------- what you said, against the card

def test_saying_the_card_goes_through_and_is_marked_said_by_you():
    w, events = make()
    card = a_card(w)
    asyncio.run(w.controller_text(card.phrase))
    assert card.status == "spoken" and card.via == "human" and card.heard_instead is None
    run(w)
    assert card.status == "validated"
    assert not of(events, "said_check")


def test_a_slip_is_stopped_before_the_pilot_acts():
    w, events = make()
    card = a_card(w)
    slip = C.phrase_for(card.callsign, [wrong_version(w, card.items[0])])
    before = w.sim.active[card.callsign].target_hdg, w.sim.active[card.callsign].target_alt, list(w.sim.active[card.callsign].route)
    asyncio.run(w.controller_text(slip))
    run(w, 8)
    check = of(events, "said_check")
    assert len(check) == 1 and check[0]["callsign"] == card.callsign and check[0]["card_id"] == card.id
    assert card.status == "pending" and card.heard_instead  # still to be said, and flagged
    a = w.sim.active[card.callsign]
    assert (a.target_hdg, a.target_alt, list(a.route)) == before  # the aircraft did nothing
    assert not [t for t in of(events, "transcript") if t["speaker"] == "pilot"]  # and nobody read it back
    assert any("Tower heard" in n for n in notices(events))
    # Said properly the second time: through, and the flag clears.
    asyncio.run(w.controller_text(card.phrase))
    run(w)
    assert card.status == "validated" and card.heard_instead is None


def test_send_as_heard_issues_what_was_said():
    w, events = make()
    card = a_card(w)
    slip_item = wrong_version(w, card.items[0])
    asyncio.run(w.controller_text(C.phrase_for(card.callsign, [slip_item])))
    held = of(events, "said_check")[0]["clearance_id"]
    w.confirm_heard(held)
    run(w)
    assert [t for t in of(events, "transcript") if t["speaker"] == "pilot"]  # now the pilot answers
    assert card.status == "pending" and card.heard_instead is None  # the card itself was never given


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

