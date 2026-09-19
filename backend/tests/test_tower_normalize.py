import pytest

from schemas import Item
from tower.normalize import normalize, phrase_from_items, spell_altitude_ft, spell_digits


@pytest.mark.parametrize("raw, expected", [
    ("air canada one two three descend and maintain flight level two four zero",
     "ACA123 descend and maintain flight level 240"),
    ("lufthansa two five three descend flight level two four zero", "DLH253 descend flight level 240"),
    ("contact departure one two four decimal six five", "contact departure 124.65"),
    ("one one niner point one", "119.1"),
    ("descend four thousand five hundred", "descend 4500"),
    ("climb one one thousand", "climb 11000"),
    ("hold short runway two four left", "hold short runway 24L"),
    ("runway two four right cleared to land", "runway 24R cleared to land"),
    ("turn left heading two seven zero", "turn left heading 270"),
    ("fly heading zero niner zero", "fly heading 090"),
    ("squawk four five two one", "squawk 4521"),
    ("altimeter two niner niner two", "altimeter 2992"),
    ("speedbird one two alpha climb flight level tree one zero", "BAW12A climb flight level 310"),
    ("charlie golf alfa bravo charlie", "CGABC"),
    ("juliet uniform juliett", "JUJ"),
    ("delta four five six turn right heading zero niner zero", "DAL456 turn right heading 090"),
    ("westjet fife fife zero reduce speed two five zero knots", "WJA550 reduce speed 250 knots"),
    ("fl240", "flight level 240"),
    ("descend flight level one hundred", "descend flight level 100"),
    ("twenty five hundred", "2500"),
    ("descend flight level two one zero one two three", "descend flight level 210 123"),
    ("Air Canada 123, descend flight level 240.", "ACA123 descend flight level 240"),
    ("", ""),
])
def test_normalize_examples(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize("raw", [
    "air canada one two three descend and maintain flight level two four zero",
    "hold short runway two four left",
    "contact departure one two four decimal six five",
    "descend four thousand five hundred, one two three",
    "proceed direct bosox",
    "speedbird one two alpha squawk four five two one",
])
def test_normalize_idempotent(raw):
    once = normalize(raw)
    assert normalize(once) == once


def test_telephony_only_becomes_callsign_with_digits():
    # "delta" is a phonetic letter unless followed by a number
    assert normalize("delta one two three") == "DAL123"
    assert normalize("taxi via delta") == "taxi via D"


def test_spell_digits():
    assert spell_digits(240) == "two four zero"
    assert spell_digits("124.65") == "one two four decimal six five"
    assert spell_digits("24L") == "two four left"
    assert spell_digits("4521") == "four five two one"
    assert spell_digits(9) == "niner"


def test_spell_altitude_ft():
    assert spell_altitude_ft(4500) == "four thousand five hundred"
    assert spell_altitude_ft(11000) == "one one thousand"


def test_phrase_from_items_round_trips_through_normalizer():
    items = [Item(type="altitude", value=240, unit="FL", action="descend"),
             Item(type="heading", value=270, unit="deg", action="turn_left"),
             Item(type="frequency", value=124.65, unit="MHz", action="contact")]
    phrase = phrase_from_items("ACA123", items)
    assert phrase == ("Air Canada one two three, descend flight level two four zero, "
                      "turn left heading two seven zero, contact one two four decimal six five")
    assert normalize(phrase) == "ACA123 descend flight level 240 turn left heading 270 contact 124.65"
