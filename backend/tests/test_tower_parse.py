from schemas import Extraction, Item
from tower.callsign import similar_pairs, snap
from tower.normalize import normalize
from tower.parse import is_ack_only, needs_fallback, parse, parse_with_fallback

ACTIVE = ["ACA123", "ACA133", "DAL456", "BAW12A"]


def p(text, speaker="controller", active=ACTIVE):
    return parse(normalize(text), active, speaker, transmission_id="t")


def items(ext):
    return [(i.type, i.value, i.unit, i.action) for i in ext.items]


def test_controller_descend_flight_level():
    e = p("air canada one two three descend and maintain flight level two four zero")
    assert e.callsign == "ACA123"
    assert items(e) == [("altitude", 240, "FL", "descend")]
    assert e.unexplained_words == 0


def test_pilot_shortened_readback_down_to():
    e = p("down to flight level two four zero canada one two three", "pilot")
    assert e.callsign == "ACA123"
    assert items(e) == [("altitude", 240, "FL", "descend")]


def test_feet_and_heading_multi_part():
    e = p("air canada one two three turn left heading two seven zero descend four thousand")
    assert ("heading", 270, "deg", "turn_left") in items(e)
    assert ("altitude", 4000, "ft", "descend") in items(e)


def test_pilot_bare_left_heading():
    e = p("left two seven zero one two three", "pilot")
    assert e.callsign == "ACA123"
    assert items(e) == [("heading", 270, "deg", "turn_left")]


def test_unitless_low_number_is_flight_level():
    e = p("descend three one zero air canada one three three", "pilot")
    assert e.callsign == "ACA133"
    assert items(e) == [("altitude", 310, "FL", "descend")]


def test_frequency_with_and_without_decimal():
    c = p("delta four five six contact departure one two four decimal six five")
    assert items(c) == [("frequency", 124.65, "MHz", "contact")]
    r = p("one two four six five good day delta four five six", "pilot")
    assert r.callsign == "DAL456"
    assert items(r) == [("frequency", 124.65, "MHz", None)]
    assert r.unexplained_words == 0


def test_runway_actions():
    assert items(p("air canada one two three hold short runway two four left")) == [("hold_short", "24L", None, "hold_short")]
    assert items(p("air canada one two three runway two four left cleared to land")) == [("runway", "24L", None, "cleared_land")]
    assert items(p("cleared to land two four left air canada one two three", "pilot")) == [("runway", "24L", None, "cleared_land")]
    assert items(p("united five six seven cleared for takeoff runway zero five", active=None)) == [("runway", "05", None, "cleared_takeoff")]
    assert items(p("air canada one two three line up and wait runway two four left")) == [("runway", "24L", None, "line_up_wait")]


def test_squawk_altimeter_speed_direct():
    e = p("speedbird one two alpha squawk four five two one altimeter two niner niner two")
    assert ("squawk", "4521", None, "squawk") in items(e)
    assert ("altimeter", 29.92, "inHg", "altimeter") in items(e)
    assert items(p("air canada one two three qnh one zero one three")) == [("altimeter", 1013, "hPa", "altimeter")]
    e = p("air canada one two three reduce speed two five zero knots proceed direct bosox")
    assert ("speed", 250, "kt", "speed") in items(e)
    assert ("route", "BOSOX", None, "direct") in items(e)


def test_ack_only_and_unexplained():
    e = p("roger air canada one two three", "pilot")
    assert e.items == [] and e.callsign == "ACA123"
    assert is_ack_only("roger ACA123", e)
    traffic = p("air canada one two three traffic twelve oclock five miles a boeing seven three seven")
    assert traffic.items == []
    assert traffic.unexplained_words >= 5


def test_needs_fallback_on_keyword_without_value():
    text = normalize("air canada one two three descend and maintain uh flight level")
    e = parse(text, ACTIVE, "controller")
    assert needs_fallback(text, e)
    clean = normalize("air canada one two three descend flight level two four zero good day")
    assert not needs_fallback(clean, parse(clean, ACTIVE, "controller"))


def test_parse_with_fallback_only_calls_llm_when_needed():
    class SpyLLM:
        calls = 0

        def extract(self, text, active=None, transmission_id=""):
            self.calls += 1
            return Extraction(transmission_id=transmission_id, callsign="ACA123",
                              items=[Item(type="altitude", value=240, unit="FL", action="descend")], method="llm")

    spy = SpyLLM()
    clean = normalize("air canada one two three descend flight level two four zero")
    assert parse_with_fallback(clean, ACTIVE, "controller", spy).method == "grammar"
    assert spy.calls == 0
    messy = normalize("air canada one two three descend uh flight level")
    out = parse_with_fallback(messy, ACTIVE, "controller", spy)
    assert spy.calls == 1 and out.method == "llm" and out.items[0].value == 240


def test_snap_full_short_and_ambiguous():
    assert snap("ACA123", ACTIVE).best == "ACA123"
    assert snap("123", ACTIVE).best == "ACA123"
    assert snap("canada 123", ACTIVE).best == "ACA123"
    assert snap("XYZ9", ACTIVE).best is None
    assert snap("ACA1", ACTIVE).ambiguous  # 123 or 133: cannot tell
    s = snap("123", ["ACA123", "DAL123"])
    assert s.ambiguous and {s.best, s.runner_up} == {"ACA123", "DAL123"}
    assert snap("ACA12", ACTIVE).best in ("ACA123", "ACA123")


def test_similar_pairs():
    assert ("ACA123", "ACA133") in similar_pairs(["ACA123", "ACA133", "DAL456"])
    assert ("ACA123", "ACA132") in similar_pairs(["ACA123", "ACA132"])
    assert similar_pairs(["ACA123", "DAL456"]) == []
