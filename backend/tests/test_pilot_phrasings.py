"""An alert must never fire for a correct readback.

Every phrasing `pilots/readback.py` can produce, for every kind of instruction, in both callsign
positions, goes through the parser and the rule checker. Found live: "maintain five four five
knots" was parsed as flight level 545 and alerted as a wrong unit. One in ten phrasings did not match.
"""
import pytest

from pilots.readback import item_phrases, say_callsign
from schemas import Item, OpenClearance, Transmission
from tower.check import check
from tower.normalize import normalize, spoken_callsign, spell_digits
from tower.parse import parse

ACTIVE = ["ACA123", "ACA133", "NRL614", "DAL456"]
ITEMS = [
    Item(type="altitude", value=240, unit="FL", action="descend"),
    Item(type="altitude", value=350, unit="FL", action="climb"),
    Item(type="altitude", value=360, unit="FL", action="maintain"),
    Item(type="altitude", value=5000, unit="ft", action="descend"),
    Item(type="heading", value=270, unit="deg", action="turn_left"),
    Item(type="heading", value=90, unit="deg", action="turn_right"),
    Item(type="heading", value=180, unit="deg", action="fly_heading"),
    Item(type="speed", value=250, unit="kt", action="reduce"),
    Item(type="speed", value=545, unit="kt", action="increase"),
    Item(type="speed", value=280, unit="kt", action="speed"),
    Item(type="route", value="ESTIR", action="direct"),
    Item(type="frequency", value=124.65, unit="MHz", action="contact departure"),
    Item(type="squawk", value="4521"),
]


def _verdict(callsign: str, item: Item, text: str):
    c = OpenClearance(id="c1", callsign=callsign, items=[item], issued_at=0.0)
    n = normalize(text)
    ext = parse(n, ACTIVE, "pilot", "t")
    tx = Transmission(id="t", t_start=0, t_end=3, audio_ref="", text_raw=text, text_norm=n,
                      asr_confidence=1.0, speaker="pilot", n_best=[])
    return check(c, ext, tx, active=ACTIVE), ext


@pytest.mark.parametrize("callsign", ["ACA123", "NRL614"])  # a known airline, and one spelled out letter by letter
@pytest.mark.parametrize("item", ITEMS, ids=lambda i: f"{i.type}-{i.value}-{i.action}")
def test_every_correct_phrasing_matches(callsign, item):
    wrong = []
    for shorten in (True, False):
        for phrase in item_phrases(item, shorten):
            for order in ("{p}, {cs}", "{cs}, {p}"):
                text = order.format(p=phrase, cs=say_callsign(callsign))
                v, ext = _verdict(callsign, item, text)
                if v.result != "match":
                    wrong.append(f"{text!r} -> {v.result}/{v.error_type}, heard {[(i.type, i.value, i.unit) for i in ext.items]}")
    assert not wrong, "\n".join(wrong)


def test_a_bare_number_that_is_not_the_cleared_value_is_not_a_readback():
    v, _ = _verdict("ACA123", Item(type="heading", value=180, unit="deg", action="fly_heading"),
                    "two one zero, air canada one two three")
    assert v.result != "match"
    v, _ = _verdict("ACA123", Item(type="squawk", value="4521"), "four five one two, air canada one two three")
    assert v.result != "match"


def test_a_real_wrong_unit_is_still_caught():
    v, _ = _verdict("ACA123", Item(type="speed", value=250, unit="kt", action="reduce"),
                    "descend flight level two five zero, air canada one two three")
    assert (v.result, v.error_type) == ("mismatch", "wrong_unit")
    v, _ = _verdict("ACA123", Item(type="heading", value=250, unit="deg", action="fly_heading"),
                    "speed two five zero, air canada one two three")
    assert (v.result, v.error_type) == ("mismatch", "wrong_unit")


def test_callsign_letters_are_never_runway_sides():
    """Found live: the say-now line read "november right left six one four" for NRL614. Said aloud,
    Tower heard no callsign and a heading of 614."""
    assert spoken_callsign("NRL614") == "november romeo lima six one four"
    assert spoken_callsign("RCL22") == "romeo charlie lima two two"
    assert spoken_callsign("BAW27L").endswith("two seven lima")
    assert spoken_callsign("ACA123") == "Air Canada one two three"
    assert spell_digits("24L") == "two four left", "a runway keeps its side"
    # and what Tower tells the controller to say is something Tower can understand
    for cs in ("NRL614", "BAW27L", "ACA123"):
        assert parse(normalize(f"{spoken_callsign(cs)} descend flight level two four zero"), [cs, "DAL456"], "controller", "t").callsign == cs


def test_a_bare_number_from_an_unidentified_aircraft_is_not_a_readback():
    """The bare-number rule is for the aircraft that owes the readback. If Tower cannot tell who
    spoke, a matching number proves nothing: it may be another aircraft taking the instruction."""
    v, ext = _verdict("NRL614", Item(type="squawk", value="4521"), "canada one three three, four five two one")
    assert ext.callsign != "NRL614" and v.result != "match"


def test_a_shortened_airline_name_after_direct_is_not_a_fix():
    v, ext = _verdict("ACA123", Item(type="route", value="ESTIR", action="direct"), "estir direct, canada one two three")
    assert [(i.type, i.value) for i in ext.items] == [("route", "ESTIR")] and v.result == "match"


def test_random_phrasings_never_false_alarm_and_errors_are_still_caught():
    """The detection side of the same bargain, over the pilots' own generator."""
    import random

    from pilots.errors import inject_error
    from pilots.readback import build_readback, roger
    fixes = ["WAKOL", "GALTO", "ESTIR", "PIKAR", "CENTA", "TULEK", "ZAMIR"]
    false_alarms, missed, n_err = [], [], 0
    for seed in range(25):
        for callsign in ("ACA123", "NRL614"):
            for k, item in enumerate(ITEMS):
                rng = random.Random(seed * 100 + k)
                text = build_readback(callsign, [item.model_copy()], rng)
                if _verdict(callsign, item, text)[0].result != "match":
                    false_alarms.append(text)
                # errors whose spoken form really differs from a correct readback
                for et in ("wrong_value", "ack_only"):
                    spoken, scs, got, _ = inject_error([item.model_copy()], callsign, ACTIVE, rng, error_type=et, waypoints=fixes)
                    if got != et:
                        continue
                    said = roger(scs, rng) if et == "ack_only" else build_readback(scs, spoken, rng)
                    n_err += 1
                    if _verdict(callsign, item, said)[0].result == "match":
                        missed.append((et, said))
    assert not false_alarms, false_alarms[:5]
    assert n_err > 500 and not missed, missed[:5]
