"""Plain English from the controller, turned into the same items the rest of Tower already speaks.

The grammar parser knows standard phraseology: "turn left heading 270", "descend flight level 240".
A controller at a demo says "turn around", "go up another two thousand", "do a three sixty". None of
that is an instruction until it is resolved against the aircraft it is addressed to: "turn around"
is heading 198 for an aircraft on 018. This module does that resolving, with patterns and the radar
state and nothing else, so it costs no time. What it cannot place is left for the interpreter agent
(tower/interpreter.py).

`interpret` returns the items it found and the text with those words blanked out, so the grammar
parser can still read the rest of the sentence ("turn around and descend flight level 240").
Everything comes out as an absolute, standard item: the pilot reads back "turn left heading one nine
eight", the checker compares like with like, and the simulator needs no new idea of "relative".

Without a state (a pilot's readback) only the manoeuvres that need none are recognised.
"""
from __future__ import annotations

import re

from schemas import AircraftState, Item

DEFAULT_TURN_DEG = 30
SLIGHT_TURN_DEG = 15
HARD_TURN_DEG = 60
DEFAULT_SPEED_STEP_KT = 30
RELATIVE_BELOW_FT = 5000  # "climb 2000 feet" said to an aircraft at cruise is a change, not a level
CRUISE_FT = 15000

COMPASS = {"north": 360, "northeast": 45, "east": 90, "southeast": 135, "south": 180, "southwest": 225,
           "west": 270, "northwest": 315}

# Things an airliner will not do. The pilot says so, which is what a real one does.
UNABLE = re.compile(r"\b(barrel roll|loop(?: the loop)?|back ?flip|flip|upside down|invert(?:ed)?|hover|stop (?:in )?mid ?air|"
                    r"reverse|fly backwards?|go backwards?|land (?:on|in) (?:the )?(?:water|lake|highway|road)|"
                    r"touch and go|aileron roll|stall|nose ?dive|dive bomb|warp|teleport)\b")

_SIDE = r"(?P<side>left|right)"
# "360" is also a heading and a flight level. It is a circle only with a verb in front ("make a left
# 360") or a side after it ("360 to the left", which is how the pilot reads it back). A bare
# "left 360" stays what the grammar says it is: turn left heading 360.
_VERB = r"(?:mak(?:e|ing)|do(?:ing)?|fly(?:ing)?|execute|give me|perform(?:ing)?|complete)\s+(?:me\s+)?(?:a|an|one)?\s*"
RE_ORBIT = re.compile(
    rf"\b(?:{_VERB}(?:(?P<side1>left|right)\s+(?:hand\s+)?)?360(?:\s+degree)?(?:\s+turn)?(?:\s+(?:to the\s+)?(?P<side2>left|right)\b)?"
    rf"|(?:a\s+)?360(?:\s+degree)?(?:\s+turn)?\s+(?:to the\s+)?(?P<side3>left|right)\b"
    rf"|(?:{_VERB})?(?:(?P<side4>left|right)\s+(?:hand\s+)?)?(?:orbit|full circle|complete circle|circle)\b(?:\s+(?:to the\s+)?(?P<side5>left|right)\b)?)")
RE_HOLD = re.compile(
    rf"\b(?:hold(?:ing)?(?:\s+(?:at\s+)?(?:your\s+)?(?:present|current)?\s*position|\s+here|\s+there)|enter (?:a|the) hold|"
    rf"keep (?:circling|orbiting)|orbit until advised|circle until advised)\b(?:[\s,]+(?P<side>left|right)(?:\s+(?:hand\s+)?turns?)?)?")
RE_AROUND = re.compile(rf"\b(?:turn (?:back )?around|turn back|reverse course|(?:make|do)\s+(?:a|an)?\s*(?:180|u turn|uturn)|"
                       rf"go back the way you came)\b(?:\s+(?:and\s+)?(?:turn\s+|to the\s+)?{_SIDE}\b(?!\s+(?:heading|\d)))?")
RE_TURN_BY = re.compile(rf"\b(?:turn|go|come|veer|bank)?\s*{_SIDE}\s+(?:turn\s+)?(?:by\s+|about\s+)?(?P<deg>\d{{1,3}})\s+degrees?\b")
RE_TURN_BY2 = re.compile(rf"\b(?:turn|go|come|veer|bank)\s+(?:by\s+)?(?P<deg>\d{{1,3}})\s+degrees?\s+(?:to the\s+|to your\s+)?{_SIDE}\b")
RE_TURN_BARE = re.compile(rf"\b(?:turn|go|veer|bank|come|move)\s+(?P<how>slightly\s+|a (?:little|bit|touch)\s+|hard\s+|sharp(?:ly)?\s+)?"
                          rf"(?:to (?:the|your)\s+)?{_SIDE}\b(?!\s+(?:heading|turn|to|\d))(?P<how2>\s+a (?:little|bit|touch)|\s+slightly)?")
RE_COMPASS = re.compile(r"\b(?:fly|head|go|turn|proceed|steer|point)\s+(?:due\s+|to the\s+|towards?\s+)?"
                        r"(?P<pt>north ?east|north ?west|south ?east|south ?west|north|south|east|west)(?:bound)?\b")
_UP = r"climb|go up|come up|raise|increase|gain|move up|up|ascend|higher"
_DOWN = r"descend|go down|come down|drop|lower|decrease|lose|move down|down|sink"
RE_ALT_BY = re.compile(
    rf"\b(?P<verb>{_UP}|{_DOWN})(?:\s+(?:your|the))?(?:\s+(?:altitude|elevation|height|level))?"
    rf"(?:\s+(?P<rel>by|another|a further|an extra))?\s+(?P<ft>\d{{2,5}})\s*(?:feet|ft|foot)\b(?:\s+(?P<rel2>higher|lower|more|up|down))?")
RE_LEVEL_OFF = re.compile(r"\b(?:level off|level out|stop (?:your |the )?(?:climb|descent)|maintain (?:present|current) (?:altitude|level))\b")
RE_SPEED_BY = re.compile(r"\b(?P<verb>speed up|go faster|faster|accelerate|slow down|go slower|slower|slow|decelerate)\b"
                         r"(?:\s+(?:by\s+)?(?P<kt>\d{1,3})\s*(?:knots|kts?))?(?!\s+to\s+\d)")
RE_PRESENT_HDG = re.compile(r"\b(?:maintain|continue|fly|hold)\s+(?:your\s+)?(?:present|current)\s+heading\b|\bfly straight(?: ahead)?\b|\bstraight ahead\b")
RE_RESUME = re.compile(r"\b(?:resume (?:own |your own |normal )?navigation|back on course|(?:continue|proceed|carry on) (?:on course|"
                       r"to (?:your |the )?destination|as (?:planned|filed))|(?:go |head |proceed )?(?:direct |straight )?to (?:your |the )?"
                       r"(?:destination|exit)|on your way)\b")
RE_GO_TO = re.compile(r"\b(?:go|head|fly|navigate|proceed|route|straight|steer)(?:\s+(?:straight|directly|direct))?\s+(?:over\s+)?to(?:wards?)?\s+(?P<wpt>[A-Za-z]{3,6})\b")
RE_DISREGARD = re.compile(r"\b(?:disregard|cancel that|cancel (?:the |my )?(?:last|previous)(?: instruction| transmission)?|never ?mind|"
                          r"belay that|scratch that|ignore that|forget (?:that|it))\b")
RE_PILOT_UNABLE = re.compile(r"\bunable\b")


def _side(*found: str | None, default: str = "right") -> str:
    return next((f for f in found if f), default)


def _hdg(value: float) -> int:
    return int(round(value)) % 360 or 360


def _turn_item(state: AircraftState, side: str, deg: float) -> Item:
    base = state.target_hdg_deg if state.target_hdg_deg is not None else state.hdg_deg
    return Item(type="heading", value=_hdg(base + (deg if side == "right" else -deg)), unit="deg", action=f"turn_{side}")


def _altitude_item(ft: float, current_ft: float) -> Item:
    ft = max(1000.0, min(45000.0, ft))
    action = "climb" if ft > current_ft + 50 else "descend" if ft < current_ft - 50 else "maintain"
    if ft >= 18000:
        return Item(type="altitude", value=int(round(ft / 100.0)), unit="FL", action=action)
    return Item(type="altitude", value=int(round(ft / 100.0) * 100), unit="ft", action=action)


def interpret(text_norm: str, state: AircraftState | None, waypoints: list[str] | None = None) -> tuple[list[Item], str]:
    """(items, the text with what was used blanked out). Never raises on odd input."""
    text = text_norm
    low = text.lower()
    items: list[Item] = []
    taken: list[tuple[int, int]] = []

    def free(m: re.Match[str]) -> bool:
        return all(m.end() <= s or m.start() >= e for s, e in taken)

    def use(m: re.Match[str], item: Item | None) -> None:
        taken.append((m.start(), m.end()))
        if item is not None and not any(i.type == item.type and i.type != "manoeuvre" for i in items):
            items.append(item)

    for m in RE_DISREGARD.finditer(low):
        use(m, Item(type="manoeuvre", value="DISREGARD", action="disregard", mandatory=False))
    for m in UNABLE.finditer(low):
        use(m, Item(type="manoeuvre", value=f"UNABLE {m.group(1).upper()}", action="unable", mandatory=False))
    for m in RE_HOLD.finditer(low):
        if free(m):
            side = _side(m.groupdict().get("side"))
            use(m, Item(type="manoeuvre", value=f"HOLD {side.upper()}", action=f"hold_{side}"))
    for m in RE_ORBIT.finditer(low):
        if free(m):
            side = _side(*(m.group(f"side{k}") for k in range(1, 6)))
            use(m, Item(type="manoeuvre", value=f"360 {side.upper()}", action=f"orbit_{side}"))
    if state is None or state.is_intruder:
        return items, _blank(text, taken)

    for m in RE_AROUND.finditer(low):
        if free(m):
            side = _side(m.groupdict().get("side"))
            use(m, _turn_item(state, side, 180))
    for rx in (RE_TURN_BY, RE_TURN_BY2):
        for m in rx.finditer(low):
            deg = int(m.group("deg"))
            if free(m) and 1 <= deg <= 180 and "heading" not in m.group(0):
                use(m, _turn_item(state, m.group("side"), deg))
    for m in RE_COMPASS.finditer(low):
        if free(m):
            use(m, Item(type="heading", value=COMPASS[m.group("pt").replace(" ", "")], unit="deg", action="fly_heading"))
    for m in RE_TURN_BARE.finditer(low):
        if free(m):
            how = f"{m.group('how') or ''} {m.group('how2') or ''}"
            deg = SLIGHT_TURN_DEG if re.search(r"slight|little|bit|touch", how) else HARD_TURN_DEG if re.search(r"hard|sharp", how) else DEFAULT_TURN_DEG
            use(m, _turn_item(state, m.group("side"), deg))
    for m in RE_PRESENT_HDG.finditer(low):
        if free(m):
            use(m, Item(type="heading", value=_hdg(state.hdg_deg), unit="deg", action="fly_heading"))
    for m in RE_ALT_BY.finditer(low):
        if not free(m):
            continue
        ft = float(m.group("ft"))
        up = bool(re.fullmatch(_UP, m.group("verb")))
        base = state.target_alt_ft
        relative = bool(m.group("rel") or m.group("rel2")) or (up and ft < base) or (ft <= RELATIVE_BELOW_FT and base >= CRUISE_FT) \
            or bool(re.search(r"altitude|elevation|height", m.group(0)))
        if relative:
            use(m, _altitude_item(base + (ft if up else -ft), state.alt_ft))
    for m in RE_LEVEL_OFF.finditer(low):
        if free(m):
            use(m, _altitude_item(round(state.alt_ft / 100.0) * 100.0, state.alt_ft))
    for m in RE_SPEED_BY.finditer(low):
        if free(m):
            step = float(m.group("kt") or DEFAULT_SPEED_STEP_KT)
            faster = bool(re.match(r"speed up|go faster|faster|accelerate", m.group("verb")))
            kt = int(round(max(180.0, min(560.0, (state.target_gs_kt or state.gs_kt) + (step if faster else -step))) / 5.0) * 5)
            use(m, Item(type="speed", value=kt, unit="kt", action="increase" if faster else "reduce"))
    for m in RE_RESUME.finditer(low):
        if free(m) and state.route:
            use(m, Item(type="route", value=str(state.route[-1]).upper(), unit=None, action="direct"))
    known = {w.upper() for w in (waypoints or [])}
    for m in RE_GO_TO.finditer(text):
        if free(m) and m.group("wpt").upper() in known:
            use(m, Item(type="route", value=m.group("wpt").upper(), unit=None, action="direct"))
    return items, _blank(text, taken)


def _blank(text: str, taken: list[tuple[int, int]]) -> str:
    """The sentence without the words already understood, so the grammar does not read them again
    ("raise your elevation 300 feet" must not also become "300 feet")."""
    if not taken:
        return text
    chars = list(text)
    for s, e in taken:
        for i in range(s, min(e, len(chars))):
            if chars[i] != " ":
                chars[i] = " "
    return re.sub(r"\s+", " ", "".join(chars)).strip()


def valid(item: Item) -> bool:
    """Can this item be said, flown and checked? A language model once returned a heading of
    "around"; formatting it raised inside a background task and the transmission vanished."""
    try:
        if item.type == "heading":
            return 0 <= int(float(item.value)) <= 360  # 000 and 360 are both north
        if item.type == "altitude":
            v = float(item.value)
            return 10 <= v <= 600 if item.unit == "FL" else 500 <= v <= 60000
        if item.type == "speed":
            return 100 <= float(item.value) <= 700
        if item.type in ("route", "manoeuvre", "runway", "hold_short", "squawk"):
            return bool(str(item.value).strip())
        if item.type in ("frequency", "altimeter"):
            float(item.value)
        return True
    except (TypeError, ValueError):
        return False
