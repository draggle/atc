"""Pilot readback templates.

Output follows the ASR dataset convention: lowercase, digits spoken one at a time,
"flight level two four zero", "one two four decimal six five", telephony names for
airlines. Digits and ICAO codes only appear after the normalizer, never here.

`tower.normalize` (owned by another stream) is the source of truth for the telephony
table once it lands; we fall back to a local copy so this module stands alone.
"""
from __future__ import annotations

import random
import re

from schemas import Item

from airlines import ICAO_TO_TELEPHONY as _SHARED

# Pilots say the name in lower case. One shared table: backend/airlines.py.
ICAO_TO_TELEPHONY: dict[str, str] = {code: name.lower() for code, name in _SHARED.items()}

DIGIT_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}

PHONETIC = {
    "a": "alfa", "b": "bravo", "c": "charlie", "d": "delta", "e": "echo", "f": "foxtrot",
    "g": "golf", "h": "hotel", "i": "india", "j": "juliett", "k": "kilo", "l": "lima",
    "m": "mike", "n": "november", "o": "oscar", "p": "papa", "q": "quebec", "r": "romeo",
    "s": "sierra", "t": "tango", "u": "uniform", "v": "victor", "w": "whiskey", "x": "xray",
    "y": "yankee", "z": "zulu",
}

RUNWAY_SUFFIX = {"L": "left", "R": "right", "C": "centre"}


# ---------------------------------------------------------------------------
# Number and callsign speech
# ---------------------------------------------------------------------------


def say_digits(value: int | float | str) -> str:
    """'240' -> 'two four zero'. Decimal point becomes 'decimal'. Letters go phonetic."""
    s = str(value)
    out: list[str] = []
    for ch in s:
        if ch.isdigit():
            out.append(DIGIT_WORDS[ch])
        elif ch == ".":
            out.append("decimal")
        elif ch.isalpha():
            out.append(PHONETIC[ch.lower()])
    return " ".join(out)


def say_feet(value: int | float) -> str:
    """4500 -> 'four thousand five hundred'; 11000 -> 'one one thousand'; 10000 -> 'one zero thousand'."""
    v = int(round(float(value)))
    thousands, rem = divmod(v, 1000)
    hundreds = rem // 100
    parts: list[str] = []
    if thousands:
        parts.append(say_digits(thousands) + " thousand")
    if hundreds:
        parts.append(DIGIT_WORDS[str(hundreds)] + " hundred")
    return " ".join(parts) if parts else "zero"


def say_frequency(value: float | str) -> str:
    """124.65 -> 'one two four decimal six five'. Preserve trailing zeros: 118.1 -> '... decimal one'."""
    s = str(value)
    if "." not in s:
        return say_digits(s)
    whole, frac = s.split(".", 1)
    frac = frac.rstrip("0") or "0"
    return f"{say_digits(whole)} decimal {say_digits(frac)}"


def say_runway(value: str) -> str:
    """'24L' -> 'two four left'."""
    m = re.fullmatch(r"(\d{1,2})([LRC]?)", str(value).upper())
    if not m:
        return say_digits(value)
    digits, suffix = m.groups()
    out = say_digits(digits)
    if suffix:
        out += " " + RUNWAY_SUFFIX[suffix]
    return out


def split_callsign(callsign_icao: str) -> tuple[str, str]:
    """'ACA123' -> ('ACA', '123'). Registration-style callsigns return ('', whole)."""
    m = re.fullmatch(r"([A-Z]{3})(\d[0-9A-Z]*)", callsign_icao.upper())
    if m and m.group(1) in ICAO_TO_TELEPHONY:
        return m.group(1), m.group(2)
    return "", callsign_icao.upper()


def say_callsign(callsign_icao: str, style: str = "full") -> str:
    """Speak an ICAO callsign.

    style: 'full' -> 'air canada one two three'
           'short' -> 'canada one two three' (drops a leading 'air'; other names stay whole)
           'digits' -> 'one two three'
    """
    prefix, tail = split_callsign(callsign_icao)
    if not prefix:
        return say_digits(tail)
    name = ICAO_TO_TELEPHONY[prefix]
    if style == "digits":
        return say_digits(tail)
    if style == "short" and name.startswith("air ") and len(name.split()) > 1:
        name = name.split(None, 1)[1]
    return f"{name} {say_digits(tail)}"


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


def _altitude_phrases(item: Item, shorten: bool) -> list[str]:
    action = (item.action or "").lower()
    if item.unit == "FL":
        num = say_digits(int(item.value))
        level = f"flight level {num}"
    else:
        num = say_feet(item.value)
        level = num
    verb = "descend" if action.startswith("desc") else "climb" if action.startswith("climb") else "maintain"
    full = [f"{verb} {level}", f"{verb} and maintain {level}"]
    if not shorten:
        return full
    if verb == "descend":
        return full + [f"down to {level}", f"descending {level}", f"down to {num}", f"descend {num}"]
    if verb == "climb":
        return full + [f"up to {level}", f"climbing {level}", f"up to {num}", f"climb {num}"]
    return full + [level]


def _heading_phrases(item: Item, shorten: bool) -> list[str]:
    action = (item.action or "").lower()
    num = say_digits(f"{int(item.value):03d}")
    direction = "left" if "left" in action else "right" if "right" in action else None
    if direction:
        full = [f"turn {direction} heading {num}", f"{direction} heading {num}"]
        short = [f"{direction} {num}", f"{direction} turn {num}", f"heading {num}"]
    else:
        full = [f"fly heading {num}", f"heading {num}"]
        short = [f"heading {num}", num]
    return full + short if shorten else full


def _speed_phrases(item: Item, shorten: bool) -> list[str]:
    action = (item.action or "").lower()
    num = say_digits(int(item.value))
    if action.startswith("reduce"):
        full = [f"reduce speed {num} knots", f"reduce speed to {num} knots"]
    elif action.startswith("increase"):
        full = [f"increase speed {num} knots", f"increase speed to {num} knots"]
    else:
        full = [f"speed {num} knots", f"maintain {num} knots"]
    short = [f"speed {num}", f"{num} knots", f"{num} on the speed"]
    return full + short if shorten else full


def _frequency_phrases(item: Item, shorten: bool) -> list[str]:
    freq = say_frequency(item.value)
    station = _station_from_action(item.action)
    full = [f"contact {station} {freq}".replace("  ", " "), f"{station} {freq}".strip(), freq]
    short = [f"over to {freq}", f"{freq} good day", freq.replace(" decimal", "")]
    return full + short if shorten else full[:2]


def _station_from_action(action: str | None) -> str:
    if not action:
        return ""
    a = action.lower()
    for key in ("departure", "approach", "tower", "ground", "centre", "center", "arrival"):
        if key in a:
            return key
    return ""


def _squawk_phrases(item: Item, shorten: bool) -> list[str]:
    num = say_digits(str(item.value).zfill(4))
    full = [f"squawk {num}"]
    short = [f"squawking {num}", num]
    return full + short if shorten else full


def _altimeter_phrases(item: Item, shorten: bool) -> list[str]:
    if item.unit == "hPa":
        num = say_digits(int(item.value))
        full = [f"qnh {num}", f"altimeter {num}"]
    else:
        num = say_digits(str(item.value).replace(".", ""))
        full = [f"altimeter {num}"]
    short = [num]
    return full + short if shorten else full


def _runway_phrases(item: Item, shorten: bool) -> list[str]:
    action = (item.action or "").lower()
    rwy = say_runway(str(item.value))
    if "land" in action:
        full = [f"cleared to land runway {rwy}", f"cleared to land {rwy}"]
    elif "takeoff" in action or "take_off" in action:
        full = [f"cleared for takeoff runway {rwy}", f"cleared for takeoff {rwy}"]
    elif "line" in action:
        full = [f"line up and wait runway {rwy}", f"line up and wait {rwy}"]
    elif "cross" in action:
        full = [f"cross runway {rwy}", f"crossing {rwy}"]
    elif "hold" in action:
        full = [f"hold short runway {rwy}", f"holding short {rwy}"]
    else:
        full = [f"runway {rwy}"]
    short = [rwy]
    return full + short if shorten else full


def _hold_short_phrases(item: Item, shorten: bool) -> list[str]:
    rwy = say_runway(str(item.value))
    full = [f"hold short runway {rwy}", f"hold short of runway {rwy}"]
    short = [f"holding short {rwy}", f"hold short {rwy}"]
    return full + short if shorten else full


def _route_phrases(item: Item, shorten: bool) -> list[str]:
    wp = str(item.value).lower()
    full = [f"direct {wp}", f"direct to {wp}", f"cleared direct {wp}"]
    short = [f"{wp} direct"]
    return full + short if shorten else full


_PHRASERS = {
    "altitude": _altitude_phrases,
    "heading": _heading_phrases,
    "speed": _speed_phrases,
    "frequency": _frequency_phrases,
    "squawk": _squawk_phrases,
    "altimeter": _altimeter_phrases,
    "runway": _runway_phrases,
    "hold_short": _hold_short_phrases,
    "route": _route_phrases,
}


def _manoeuvre_phrases(item: Item, shorten: bool) -> list[str]:
    """A circle is read back as "three sixty to the left": "left three sixty" is also how a pilot
    shortens "turn left heading three six zero", and the checker could not tell them apart."""
    side = "left" if str(item.action or "").endswith("left") else "right"
    if str(item.action or "").startswith("orbit_"):
        # Never starts with a number: after a leading callsign the digits would run together
        # ("air canada one two three three sixty" normalizes to ACA123360).
        return [f"making a three sixty to the {side}", f"a three sixty to the {side}"]
    if str(item.action or "").startswith("hold_"):
        return [f"holding present position {side} turns", f"hold present position {side} turns"]
    return [str(item.value).lower()]


_PHRASERS["manoeuvre"] = _manoeuvre_phrases


def item_phrases(item: Item, shorten: bool = True) -> list[str]:
    """All acceptable spoken forms for one item. Index 0 is the most formal."""
    fn = _PHRASERS.get(item.type)
    if fn is None:
        return [str(item.value).lower()]
    return fn(item, shorten)


def say_item(item: Item, rng: random.Random | None = None, shorten: bool = True) -> str:
    phrases = item_phrases(item, shorten)
    if rng is None:
        return phrases[0]
    # Bias toward the formal forms: half the mass on index 0/1, the rest spread.
    if len(phrases) > 2 and rng.random() < 0.5:
        return rng.choice(phrases[:2])
    return rng.choice(phrases)


# ---------------------------------------------------------------------------
# Whole readbacks
# ---------------------------------------------------------------------------


def build_readback(
    callsign_icao: str,
    items: list[Item],
    style_rng: random.Random | None = None,
    shorten: bool = True,
    callsign_style: str | None = None,
) -> str:
    """Compose a pilot readback in the dataset text convention.

    Variation: item phrasing, trailing (usual) vs leading callsign, shortened callsign.
    `callsign_style` forces 'full' | 'short' | 'digits' if given.
    """
    rng = style_rng or random.Random(0)
    if callsign_style is None:
        if not shorten:
            callsign_style = "full"
        else:
            r = rng.random()
            callsign_style = "full" if r < 0.6 else "short" if r < 0.8 else "digits"
    cs = say_callsign(callsign_icao, callsign_style)
    body = ", ".join(say_item(it, rng if style_rng else None, shorten) for it in items)
    if not body:
        return f"roger, {cs}"
    if shorten and rng.random() < 0.2:
        return f"{cs}, {body}"
    return f"{body}, {cs}"


def say_again(callsign_icao: str, rng: random.Random | None = None) -> str:
    cs = say_callsign(callsign_icao, "full")
    options = [f"say again, {cs}", f"{cs}, say again", f"say again for {cs}"]
    return options[0] if rng is None else rng.choice(options)


def roger(callsign_icao: str, rng: random.Random | None = None) -> str:
    cs = say_callsign(callsign_icao, "full")
    options = [f"roger, {cs}", f"wilco, {cs}", f"{cs}, roger", f"copied, {cs}"]
    return options[0] if rng is None else rng.choice(options)
