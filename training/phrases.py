"""Templated ATC phrase generator shared by prep_data.py (synthetic audio) and
gen_checker_data.py (clearance / readback pairs).

Everything is emitted in dataset convention: lowercase, digits spelled out one
at a time, no punctuation. See docs/02-domain.md for the phraseology.

A Clearance is a callsign plus an ordered list of ClearanceItems. Each item has
a type, a spoken value, and enough structure for gen_checker_data.py to inject a
specific error kind (wrong value, wrong direction, wrong unit, ...).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field, replace

DIGIT_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]

# Telephony names from docs/02-domain.md plus a few common European ones from the datasets.
AIRLINES = [
    "air canada", "westjet", "jazz", "porter", "delta", "united", "american",
    "lufthansa", "speedbird", "air france", "klm", "ryanair", "easy", "csa",
    "austrian", "swiss", "wizz air", "eurowings", "finnair", "sas",
]

WAYPOINTS = [
    "bosox", "kinky", "lomsi", "ogruk", "dedki", "rilax", "vebit", "abtal",
    "nulpo", "kerax", "barud", "tepso", "lasat", "domid", "ravel", "gilon",
]

ITEM_TYPES = ["altitude", "heading", "speed", "frequency", "squawk", "runway", "direct"]


def say_digits(s: str) -> str:
    """'240' -> 'two four zero'. Non-digits pass through unchanged."""
    out = []
    for ch in s:
        out.append(DIGIT_WORDS[int(ch)] if ch.isdigit() else ch)
    return " ".join(out)


@dataclass
class ClearanceItem:
    type: str            # one of ITEM_TYPES
    action: str          # climb, descend, turn_left, turn_right, fly, reduce, increase, contact, squawk, cleared_land, hold_short, direct
    value: str           # canonical digits / code, e.g. "240", "270", "124.65", "4521", "24L", "bosox"
    unit: str = ""       # "FL" or "ft" for altitude, "" otherwise
    spoken: str = ""     # controller spoken form, filled by speak()


@dataclass
class Clearance:
    callsign: str        # spoken callsign, e.g. "lufthansa two five three"
    items: list[ClearanceItem] = field(default_factory=list)
    airline: str = ""
    number: str = ""     # digits of the callsign, e.g. "253"

    def clone(self) -> "Clearance":
        return replace(self, items=[replace(i) for i in self.items])


# ---------------------------------------------------------------------------
# Callsigns
# ---------------------------------------------------------------------------

def random_callsign(rng: random.Random) -> tuple[str, str, str]:
    airline = rng.choice(AIRLINES)
    n_digits = rng.choice([2, 3, 3, 4])
    number = "".join(str(rng.randint(0, 9)) for _ in range(n_digits))
    if number[0] == "0":
        number = str(rng.randint(1, 9)) + number[1:]
    return f"{airline} {say_digits(number)}", airline, number


def similar_callsign(rng: random.Random, airline: str, number: str) -> str:
    """A confusable callsign: same airline, one digit changed or two digits swapped."""
    digits = list(number)
    if len(digits) >= 2 and rng.random() < 0.4:
        i = rng.randrange(len(digits) - 1)
        digits[i], digits[i + 1] = digits[i + 1], digits[i]
        if "".join(digits) == number:
            digits[i] = str((int(digits[i]) + 1) % 10)
    else:
        i = rng.randrange(len(digits))
        digits[i] = str((int(digits[i]) + rng.randint(1, 9)) % 10)
    return f"{airline} {say_digits(''.join(digits))}"


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

def _altitude(rng: random.Random) -> ClearanceItem:
    action = rng.choice(["climb", "descend", "descend", "maintain"])
    if rng.random() < 0.65:
        fl = rng.choice([60, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160, 180, 200, 210,
                         220, 230, 240, 250, 260, 270, 280, 290, 300, 310, 320, 330, 340, 350, 360, 370, 380, 390])
        return ClearanceItem("altitude", action, str(fl), "FL")
    ft = rng.choice([2000, 2500, 3000, 3500, 4000, 4500, 5000, 6000, 7000, 8000, 9000, 10000, 11000, 12000])
    return ClearanceItem("altitude", action, str(ft), "ft")


def _heading(rng: random.Random) -> ClearanceItem:
    action = rng.choice(["turn_left", "turn_right", "fly"])
    hdg = rng.randrange(0, 360, 5)
    if hdg == 0:
        hdg = 360
    return ClearanceItem("heading", action, f"{hdg:03d}")


def _speed(rng: random.Random) -> ClearanceItem:
    action = rng.choice(["reduce", "increase", "maintain"])
    spd = rng.randrange(160, 320, 10)
    return ClearanceItem("speed", action, str(spd))


def _frequency(rng: random.Random) -> ClearanceItem:
    station = rng.choice(["tower", "ground", "departure", "approach", "radar", "control", "centre"])
    whole = rng.randint(118, 135)
    frac = rng.choice(["05", "1", "125", "15", "2", "25", "3", "35", "4", "45", "5", "55", "6", "65", "7", "75", "8", "85", "9", "95"])
    return ClearanceItem("frequency", f"contact_{station}", f"{whole}.{frac}")


def _squawk(rng: random.Random) -> ClearanceItem:
    code = "".join(str(rng.randint(0, 7)) for _ in range(4))
    if code in {"7500", "7600", "7700"}:
        code = "4521"
    return ClearanceItem("squawk", "squawk", code)


def _runway(rng: random.Random) -> ClearanceItem:
    action = rng.choice(["cleared_land", "cleared_takeoff", "hold_short", "line_up", "cross"])
    num = rng.randint(1, 36)
    side = rng.choice(["", "", "L", "R", "C"])
    return ClearanceItem("runway", action, f"{num:02d}{side}")


def _direct(rng: random.Random) -> ClearanceItem:
    return ClearanceItem("direct", "direct", rng.choice(WAYPOINTS))


_MAKERS = {
    "altitude": _altitude,
    "heading": _heading,
    "speed": _speed,
    "frequency": _frequency,
    "squawk": _squawk,
    "runway": _runway,
    "direct": _direct,
}


def make_item(rng: random.Random, item_type: str) -> ClearanceItem:
    item = _MAKERS[item_type](rng)
    item.spoken = speak_item(item, rng, controller=True)
    return item


# ---------------------------------------------------------------------------
# Spoken forms
# ---------------------------------------------------------------------------

def spoken_altitude(value: str, unit: str) -> str:
    if unit == "FL":
        return f"flight level {say_digits(value)}"
    n = int(value)
    thousands, hundreds = divmod(n, 1000)
    words = []
    if thousands:
        words.append(f"{say_digits(str(thousands))} thousand")
    if hundreds:
        words.append(f"{DIGIT_WORDS[hundreds // 100]} hundred")
    return " ".join(words)


def spoken_frequency(value: str) -> str:
    whole, frac = value.split(".")
    return f"{say_digits(whole)} decimal {say_digits(frac)}"


def spoken_runway(value: str) -> str:
    num, side = value[:2], value[2:]
    side_word = {"L": " left", "R": " right", "C": " centre", "": ""}[side]
    return f"runway {say_digits(num)}{side_word}"


_STATION = {
    "contact_tower": "tower", "contact_ground": "ground", "contact_departure": "departure",
    "contact_approach": "approach", "contact_radar": "radar", "contact_control": "control",
    "contact_centre": "centre",
}


def speak_item(item: ClearanceItem, rng: random.Random, controller: bool) -> str:
    """Spoken form of one item. Controller phrasing is fuller; pilot phrasing
    picks among several accepted shortened paraphrases."""
    t, a, v = item.type, item.action, item.value
    if t == "altitude":
        alt = spoken_altitude(v, item.unit)
        if controller:
            verb = {"climb": rng.choice(["climb", "climb and maintain", "climb to"]),
                    "descend": rng.choice(["descend", "descend and maintain", "descend to"]),
                    "maintain": "maintain"}[a]
            return f"{verb} {alt}"
        verb = {"climb": rng.choice(["climb", "climbing", "up to", "climb to"]),
                "descend": rng.choice(["descend", "descending", "down to", "descend to"]),
                "maintain": rng.choice(["maintain", "maintaining"])}[a]
        return f"{verb} {alt}"
    if t == "heading":
        hdg = say_digits(v)
        if controller:
            return {"turn_left": rng.choice([f"turn left heading {hdg}", f"left heading {hdg}"]),
                    "turn_right": rng.choice([f"turn right heading {hdg}", f"right heading {hdg}"]),
                    "fly": f"fly heading {hdg}"}[a]
        return {"turn_left": rng.choice([f"left heading {hdg}", f"left {hdg}", f"turning left {hdg}", f"left turn heading {hdg}"]),
                "turn_right": rng.choice([f"right heading {hdg}", f"right {hdg}", f"turning right {hdg}", f"right turn heading {hdg}"]),
                "fly": rng.choice([f"heading {hdg}", f"fly heading {hdg}"])}[a]
    if t == "speed":
        spd = say_digits(v)
        if controller:
            return {"reduce": f"reduce speed {spd} knots", "increase": f"increase speed {spd} knots",
                    "maintain": rng.choice([f"maintain {spd} knots", f"speed {spd} knots"])}[a]
        return rng.choice([f"speed {spd}", f"{spd} knots", f"reducing {spd}" if a == "reduce" else f"speed {spd} knots", f"{spd} on the speed"])
    if t == "frequency":
        f = spoken_frequency(v)
        st = _STATION[a]
        if controller:
            return f"contact {st} {f}" if rng.random() < 0.8 else f"contact {st} on {f}"
        return rng.choice([f"{f}", f"{st} {f}", f"{st} on {f}", f"contact {st} {f}", f"over to {st} {f}"])
    if t == "squawk":
        code = say_digits(v)
        if controller:
            return f"squawk {code}"
        return rng.choice([f"squawk {code}", f"squawking {code}", f"{code} on the squawk", f"{code}"])
    if t == "runway":
        rw = spoken_runway(v)
        if controller:
            return {"cleared_land": f"cleared to land {rw}", "cleared_takeoff": f"cleared for takeoff {rw}",
                    "hold_short": f"hold short {rw}", "line_up": f"line up and wait {rw}",
                    "cross": f"cross {rw}"}[a]
        return {"cleared_land": rng.choice([f"cleared to land {rw}", f"cleared land {rw}"]),
                "cleared_takeoff": rng.choice([f"cleared for takeoff {rw}", f"cleared takeoff {rw}"]),
                "hold_short": rng.choice([f"hold short {rw}", f"holding short {rw}"]),
                "line_up": rng.choice([f"line up and wait {rw}", f"lining up {rw}"]),
                "cross": rng.choice([f"cross {rw}", f"crossing {rw}"])}[a]
    if t == "direct":
        return f"direct {v}" if controller else rng.choice([f"direct {v}", f"direct to {v}", f"proceeding direct {v}"])
    raise ValueError(t)


# ---------------------------------------------------------------------------
# Clearances and transmissions
# ---------------------------------------------------------------------------

def random_clearance(rng: random.Random, n_items: int | None = None) -> Clearance:
    callsign, airline, number = random_callsign(rng)
    if n_items is None:
        n_items = rng.choices([1, 2, 3], weights=[0.5, 0.35, 0.15])[0]
    # Weighted toward frequency changes and heading/speed per HAAWAII.
    weights = {"altitude": 0.22, "heading": 0.2, "speed": 0.13, "frequency": 0.22,
               "squawk": 0.07, "runway": 0.1, "direct": 0.06}
    types: list[str] = []
    while len(types) < n_items:
        t = rng.choices(list(weights), weights=list(weights.values()))[0]
        if t not in types and not (t == "runway" and types):
            types.append(t)
    if "runway" in types and len(types) > 1:
        types = ["runway"]
    items = [make_item(rng, t) for t in types]
    return Clearance(callsign, items, airline, number)


def controller_text(c: Clearance, rng: random.Random) -> str:
    return f"{c.callsign} {' '.join(i.spoken for i in c.items)}"


def pilot_readback(c: Clearance, rng: random.Random, callsign_first: bool | None = None) -> str:
    """A correct readback: items in a possibly different order, paraphrased,
    callsign at the start or end, sometimes shortened."""
    items = list(c.items)
    if len(items) > 1 and rng.random() < 0.3:
        rng.shuffle(items)
    parts = [speak_item(i, rng, controller=False) for i in items]
    cs = c.callsign
    r = rng.random()
    if r < 0.25:
        cs = say_digits(c.number)  # shortened to the number only
    elif r < 0.35:
        cs = f"{c.airline} {say_digits(c.number[-2:])}"
    body = " ".join(parts)
    if callsign_first is None:
        callsign_first = rng.random() < 0.3
    if rng.random() < 0.15:
        body = rng.choice(["roger", "wilco", "copied"]) + " " + body
    return f"{cs} {body}" if callsign_first else f"{body} {cs}"


def random_controller_line(rng: random.Random) -> str:
    return controller_text(random_clearance(rng), rng)


def random_pilot_line(rng: random.Random) -> str:
    return pilot_readback(random_clearance(rng), rng)


def random_line(rng: random.Random) -> str:
    """A controller or pilot line for synthetic audio. Includes some non-clearance chatter."""
    r = rng.random()
    if r < 0.45:
        return random_controller_line(rng)
    if r < 0.9:
        return random_pilot_line(rng)
    cs, _, _ = random_callsign(rng)
    return rng.choice([
        f"{cs} good day radar contact",
        f"{cs} report established",
        f"{cs} say again",
        f"{cs} request descent",
        f"{cs} traffic in sight",
        f"good morning {cs} with you passing flight level {say_digits(str(rng.randrange(80, 360, 10)))}",
    ])


if __name__ == "__main__":
    rng = random.Random(0)
    for _ in range(8):
        c = random_clearance(rng)
        print("CTL:", controller_text(c, rng))
        print("PLT:", pilot_readback(c, rng))
        print()
