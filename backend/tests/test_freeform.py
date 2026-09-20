"""Plain English on the radio: resolved against the aircraft, flown, read back and checked."""
import asyncio
import importlib

import pytest

from schemas import AircraftState
from tower.freeform import interpret, valid
from world import World

C = importlib.import_module("planner.cards")


def state(**kw):
    base = dict(callsign="DAL789", x_nm=0, y_nm=0, alt_ft=35000, target_alt_ft=35000, hdg_deg=18, target_hdg_deg=None,
                gs_kt=450, target_gs_kt=450, route=["TULEK"], actype="B739", t=0)
    return AircraftState(**{**base, **kw})


def got(text, st=None, fixes=("ESTIR", "TULEK")):
    items, rest = interpret(text, st if st is not None else state(), list(fixes))
    return [(i.type, i.action, i.value, i.unit) for i in items], rest


@pytest.mark.parametrize("text,want", [
    ("DAL789 turn around", [("heading", "turn_right", 198, "deg")]),
    ("DAL789 turn around and turn left", [("heading", "turn_left", 198, "deg")]),
    ("DAL789 do a 180", [("heading", "turn_right", 198, "deg")]),
    ("DAL789 turn right 30 degrees", [("heading", "turn_right", 48, "deg")]),
    ("DAL789 turn left by 20 degrees", [("heading", "turn_left", 358, "deg")]),
    ("DAL789 turn left", [("heading", "turn_left", 348, "deg")]),
    ("DAL789 fly north", [("heading", "fly_heading", 360, "deg")]),
    ("DAL789 head southwest", [("heading", "fly_heading", 225, "deg")]),
    ("DAL789 climb 2000 feet", [("altitude", "climb", 370, "FL")]),
    ("DAL789 go up another 1500 feet", [("altitude", "climb", 365, "FL")]),
    ("DAL789 raise your elevation 300 feet", [("altitude", "climb", 353, "FL")]),
    ("DAL789 descend 2000 feet", [("altitude", "descend", 330, "FL")]),
    ("DAL789 level off", [("altitude", "maintain", 350, "FL")]),
    ("DAL789 speed up by 20 knots", [("speed", "increase", 470, "kt")]),
    ("DAL789 slow down", [("speed", "reduce", 420, "kt")]),
    ("DAL789 resume own navigation", [("route", "direct", "TULEK", None)]),
    ("DAL789 go straight to estir", [("route", "direct", "ESTIR", None)]),
    ("DAL789 make a left 360", [("manoeuvre", "orbit_left", "360 LEFT", None)]),
    ("DAL789 do a 360", [("manoeuvre", "orbit_right", "360 RIGHT", None)]),
    ("DAL789 hold present position", [("manoeuvre", "hold_right", "HOLD RIGHT", None)]),
    ("DAL789 disregard", [("manoeuvre", "disregard", "DISREGARD", None)]),
    ("DAL789 do a barrel roll", [("manoeuvre", "unable", "UNABLE BARREL ROLL", None)]),
])
def test_plain_english_becomes_standard_items(text, want):
    assert got(text)[0] == want


@pytest.mark.parametrize("text", [
    "DAL789 turn left heading 360", "DAL789 turn right heading 090", "DAL789 descend flight level 240",
    "DAL789 climb and maintain flight level 360", "DAL789 proceed direct TULEK", "DAL789 reduce speed to 250 knots",
    "left 360 DAL789", "DAL789 descend and maintain 8000 feet",
])
def test_standard_phraseology_is_left_to_the_grammar(text):
    items, rest = got(text, state(alt_ft=12000, target_alt_ft=12000) if "8000" in text else None)
    assert items == [] and rest == text


def test_the_rest_of_the_sentence_still_reaches_the_grammar():
    items, rest = got("DAL789 turn around and descend flight level 240")
    assert items == [("heading", "turn_right", 198, "deg")] and "descend flight level 240" in rest and "around" not in rest
    # and the number inside a plain-English phrase is not read a second time as a level
    items, rest = got("DAL789 raise your elevation 300 feet")
    assert "300" not in rest


def test_a_pilot_reading_back_a_circle_needs_no_radar_state():
    items, _ = interpret("360 to the left DAL789", None, [])
    assert [(i.action, i.value) for i in items] == [("orbit_left", "360 LEFT")]
    assert interpret("DAL789 turn around", None, [])[0] == []  # that one needs to know the heading


def test_an_item_nobody_can_say_is_not_an_item():
    from schemas import Item
    assert not valid(Item(type="heading", value="around", unit="deg"))
    assert not valid(Item(type="altitude", value="flight level", unit="FL"))
    assert valid(Item(type="heading", value=0, unit="deg")) and valid(Item(type="heading", value=360, unit="deg"))


# --------------------------------------------------------------------------- through the whole loop

def make():
    events: list[dict] = []
    w = World(events.append, synthesize=False, realtime=False)
    w.load("demo")
    w.error_rate = 0.0
    w.fleet.set_error_rate(0.0)
    w.start()
    asyncio.run(w.tick(1.0))
    return w, events


def say(w, text, seconds=4):
    asyncio.run(w.controller_text(text))
    asyncio.run(w.tick(float(seconds)))


def test_the_sentence_that_used_to_vanish():
    """Said on Saturday night, heard perfectly, and nothing happened: the language model answered
    "heading: around" and formatting that raised inside a background task."""
    w, events = make()
    cs = next(iter(w.sim.active))
    a = w.sim.active[cs]
    hdg, alt = a.hdg, a.target_alt
    say(w, f"{C.say_callsign(cs)}, turn around and turn left and raise your elevation three hundred feet")
    assert a.target_hdg == pytest.approx((hdg + 180) % 360, abs=1.0)
    assert a.target_alt == pytest.approx(alt + 300, abs=50)
    pilot = [t for t in events if t["type"] == "transcript" and t["payload"]["speaker"] == "pilot"]
    assert pilot and "heading" in pilot[-1]["payload"]["text_norm"]  # read back in standard phraseology
    assert not [e for e in events if e["type"] == "alert"]  # and the readback matched


def test_a_three_sixty_is_flown_read_back_and_ends_where_it_began():
    w, events = make()
    cs = next(iter(w.sim.active))
    a = w.sim.active[cs]
    route = list(a.route)
    say(w, f"{C.say_callsign(cs)}, make a left three sixty")
    assert a.orbit_dir == -1 and w.sim.state_of(cs).manoeuvre == "360 left"
    assert not [e for e in events if e["type"] == "alert"]  # "three sixty to the left" checked as a match
    seen = set()
    for _ in range(260):  # a full circle at 1.5 degrees a second is four minutes
        asyncio.run(w.tick(1.0))
        seen.add(int(a.hdg // 45))
    assert len(seen) == 8 and a.orbit_dir == 0  # pointed every way, and stopped circling
    assert list(a.route) == route and a.target_hdg is None  # back to its own navigation
    assert not [c for c in w.cards.values() if c.callsign == cs and c.status == "pending" and c.origin == "replan"]


def test_a_hold_lasts_until_the_next_instruction():
    w, _ = make()
    cs = next(iter(w.sim.active))
    a = w.sim.active[cs]
    say(w, f"{C.say_callsign(cs)}, hold present position")
    for _ in range(300):
        asyncio.run(w.tick(1.0))
    assert a.orbit_dir == 1  # still going round
    say(w, f"{C.say_callsign(cs)}, resume own navigation")
    assert a.orbit_dir == 0 and a.target_hdg is None and len(a.route) == 1


def test_unable_changes_nothing_and_disregard_puts_it_back():
    w, events = make()
    cs = next(iter(w.sim.active))
    a = w.sim.active[cs]
    before = (a.target_hdg, a.target_alt, list(a.route))
    say(w, f"{C.say_callsign(cs)}, do a barrel roll")
    assert (a.target_hdg, a.target_alt, list(a.route)) == before
    assert any("unable" in e["payload"].get("text_norm", "").lower() for e in events if e["type"] == "transcript")
    say(w, f"{C.say_callsign(cs)}, turn right heading zero nine zero")
    assert a.target_hdg == 90.0
    say(w, f"{C.say_callsign(cs)}, disregard")
    assert (a.target_hdg, a.target_alt, list(a.route)) == before
    assert not w.core.store.open_clearances(cs)  # and no readback is owed on a withdrawn instruction
