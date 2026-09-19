"""Deterministic phraseology normalizer.

Input is ASR text in the dataset convention (lowercase, numbers spelled out).
Output uses digits and ICAO codes so the parser and checker compare like with like.

Conventions of the normalized form (also accepted as input, so ``normalize`` is idempotent):

- Digit words collapse into one number token: ``two four zero`` -> ``240``. Leading zeros are kept
  (``zero niner zero`` -> ``090``) unless thousands/hundreds words were used.
- ``thousand`` / ``hundred`` are evaluated: ``four thousand five hundred`` -> ``4500``,
  ``one one thousand`` -> ``11000``.
- ``decimal`` / ``point`` inside a number -> ``.``: ``one two four decimal six five`` -> ``124.65``.
- Flight levels stay as the two tokens ``flight level`` followed by the number: ``flight level 240``.
  ``fl240`` / ``fl 240`` are rewritten to that form.
- Runway designators become ``24L`` / ``24R`` / ``24C`` when a 1-2 digit number is followed by
  left/right/centre (and not by a turn keyword).
- Phonetic letters become capitals, consecutive letters are joined: ``charlie golf alfa`` -> ``CGA``.
- Airline telephony + number (+ optional letters) becomes an ICAO callsign: ``ACA123``, ``BAW12A``.
- Everything else is lowercased; already-normalized ALL-CAPS tokens are preserved.
"""
from __future__ import annotations

import re

from schemas import Item

PHONETIC: dict[str, str] = {
    "alfa": "A", "alpha": "A", "bravo": "B", "charlie": "C", "delta": "D", "echo": "E",
    "foxtrot": "F", "golf": "G", "hotel": "H", "india": "I", "juliett": "J", "juliet": "J",
    "kilo": "K", "lima": "L", "mike": "M", "november": "N", "oscar": "O", "papa": "P",
    "quebec": "Q", "romeo": "R", "sierra": "S", "tango": "T", "uniform": "U", "victor": "V",
    "whiskey": "W", "xray": "X", "x-ray": "X", "yankee": "Y", "zulu": "Z",
}

DIGITS: dict[str, str] = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "tree": "3", "four": "4",
    "fower": "4", "five": "5", "fife": "5", "six": "6", "seven": "7", "eight": "8",
    "nine": "9", "niner": "9",
}
TEENS: dict[str, str] = {
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19",
}
TENS: dict[str, str] = {
    "twenty": "2", "thirty": "3", "forty": "4", "fifty": "5", "sixty": "6", "seventy": "7",
    "eighty": "8", "ninety": "9",
}
DECIMAL_WORDS = {"decimal", "point"}
SCALE_WORDS = {"thousand", "hundred"}

# Telephony designator -> ICAO three-letter code. Multi-word names are matched longest first.
TELEPHONY: dict[str, str] = {
    "air canada": "ACA", "westjet": "WJA", "west jet": "WJA", "jazz": "JZA", "porter": "POE",
    "delta": "DAL", "united": "UAL", "american": "AAL", "lufthansa": "DLH", "speedbird": "BAW",
    "air france": "AFR", "klm": "KLM", "ryanair": "RYR", "easy": "EZY", "easyjet": "EZY",
    "csa": "CSA",
}
ICAO_TO_TELEPHONY: dict[str, str] = {
    "ACA": "Air Canada", "WJA": "WestJet", "JZA": "Jazz", "POE": "Porter", "DAL": "Delta",
    "UAL": "United", "AAL": "American", "DLH": "Lufthansa", "BAW": "Speedbird",
    "AFR": "Air France", "KLM": "KLM", "RYR": "Ryanair", "EZY": "Easy", "CSA": "CSA",
}
_TELEPHONY_BY_LEN = sorted(TELEPHONY.items(), key=lambda kv: -len(kv[0].split()))
_MAX_TELEPHONY_WORDS = max(len(k.split()) for k in TELEPHONY)

RUNWAY_SIDES = {"left": "L", "right": "R", "centre": "C", "center": "C"}
_NO_RUNWAY_AFTER = {"heading", "turn"}

BREAK = "\x00"  # punctuation boundary: stops digit runs, dropped from output
# Max digits in a number that follows these keywords; a longer spoken run is a callsign glued on.
FIELD_WIDTH: dict[str, int] = {"level": 3, "heading": 3, "left": 3, "right": 3, "speed": 3,
                               "squawk": 4, "runway": 2}

_NUMBER_TOKEN = re.compile(r"^\d+(\.\d+)?$")
_INT_TOKEN = re.compile(r"^\d+$")
_PRESERVED = re.compile(r"^(?=.*[A-Z])[A-Z0-9][A-Z0-9.]*$")  # already-normalized ICAO / letter / runway tokens
_FL_COMPACT = re.compile(r"^fl(\d{2,3})$")
_CALLSIGN = re.compile(r"^[A-Z]{3}\d{1,4}[A-Z]{0,2}$")


class _Tok:
    """Working token with a kind: 'digit', 'letter', 'icao', 'word'."""

    __slots__ = ("kind", "text")

    def __init__(self, text: str, kind: str) -> None:
        self.text = text
        self.kind = kind

    def __repr__(self) -> str:  # pragma: no cover
        return f"{self.kind}:{self.text}"


def _pre_tokenize(text: str) -> list[str]:
    text = text.replace("-", " ").replace("/", " ")
    text = re.sub(r"(?<!\d)\.(?!\d)|[,;:!?\"()]", " , ", text)  # keep a boundary marker
    text = text.replace("'", "")
    out: list[str] = []
    for raw in text.split():
        raw = raw.strip(".")
        if not raw:
            continue
        if raw == ",":
            out.append(BREAK)
            continue
        if _PRESERVED.match(raw):
            out.append(raw)
        else:
            out.append(raw.lower())
    return out


def _apply_telephony(words: list[str]) -> list[_Tok]:
    """Replace telephony names followed by a number with an ICAO marker token."""
    toks: list[_Tok] = []
    i = 0
    while i < len(words):
        matched = False
        for name, icao in _TELEPHONY_BY_LEN:
            parts = name.split()
            n = len(parts)
            if words[i:i + n] == parts:
                nxt = words[i + n] if i + n < len(words) else None
                if nxt is not None and (nxt in DIGITS or nxt in TEENS or nxt in TENS
                                        or _INT_TOKEN.match(nxt)):
                    toks.append(_Tok(icao, "icao"))
                    i += n
                    matched = True
                break
        if not matched:
            w = words[i]
            m = _FL_COMPACT.match(w)
            if w == "fl":
                toks.extend([_Tok("flight", "word"), _Tok("level", "word")])
            elif m:
                toks.extend([_Tok("flight", "word"), _Tok("level", "word"), _Tok(m.group(1), "num")])
            else:
                toks.append(_Tok(w, "word"))
            i += 1
    return toks


def _is_digit_word(w: str) -> bool:
    return w in DIGITS or w in TEENS or w in TENS or bool(_INT_TOKEN.match(w))


def _collapse_number(words: list[str]) -> str:
    """Turn a run of number words into one numeric string."""
    total = 0
    scaled = False
    group = ""
    pending_tens = False
    decimals: str | None = None

    def flush_group(mult: int) -> None:
        nonlocal total, group, scaled
        total += int(group or ("1" if mult == 100 else "0")) * mult
        group = ""
        scaled = True

    for w in words:
        if w in DECIMAL_WORDS:
            if scaled:
                total += int(group or 0)
                group = str(total)
                scaled = False
            decimals = ""
            pending_tens = False
        elif decimals is not None:
            if w in DIGITS:
                decimals += DIGITS[w]
            elif _INT_TOKEN.match(w):
                decimals += w
        elif w == "thousand":
            if pending_tens:
                group += "0"
                pending_tens = False
            flush_group(1000)
        elif w == "hundred":
            if pending_tens:
                group += "0"
                pending_tens = False
            flush_group(100)
        elif w in TENS:
            if pending_tens:
                group += "0"
            group += TENS[w]
            pending_tens = True
        elif w in TEENS:
            if pending_tens:
                group += "0"
                pending_tens = False
            group += TEENS[w]
        elif w in DIGITS:
            group += DIGITS[w]
            pending_tens = False
        elif _INT_TOKEN.match(w):
            if pending_tens:
                group += "0"
                pending_tens = False
            group += w
    if pending_tens:
        group += "0"
    if scaled:
        total += int(group or 0)
        result = str(total)
    else:
        result = group
    if decimals is not None:
        result = f"{result or '0'}.{decimals}" if decimals else result
    return result


def _collapse(toks: list[_Tok]) -> list[_Tok]:
    """Collapse digit runs and letter runs; attach numbers and letters to ICAO markers."""
    out: list[_Tok] = []
    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        if t.kind == "num":
            out.append(_Tok(t.text, "digit"))
            i += 1
            continue
        w = t.text
        if t.kind == "word" and _NUMBER_TOKEN.match(w) and "." in w:
            out.append(_Tok(w, "digit"))
            i += 1
            continue
        if t.kind == "word" and _is_digit_word(w):
            run: list[str] = []
            j = i
            prev = toks[i - 1].text if i > 0 else ""
            width = FIELD_WIDTH.get(prev)
            while j < n and toks[j].kind == "word":
                if width is not None and sum(len(DIGITS.get(r, r)) for r in run if r in DIGITS or _INT_TOKEN.match(r)) >= width:
                    break
                wj = toks[j].text
                if run and _INT_TOKEN.match(wj) and run[-1] not in DECIMAL_WORDS:
                    break  # two already-numeric tokens stay separate ("4500 123")
                if run and _INT_TOKEN.match(run[0]) and wj not in SCALE_WORDS \
                        and wj not in DECIMAL_WORDS and run[-1] not in DECIMAL_WORDS:
                    break
                if _is_digit_word(wj) or wj in SCALE_WORDS or wj in DECIMAL_WORDS and j + 1 < n and toks[j + 1].kind == "word" \
                        and (toks[j + 1].text in DIGITS or _INT_TOKEN.match(toks[j + 1].text)):
                    run.append(wj)
                    j += 1
                else:
                    break
            # a leading "one" before "thousand" etc is fine; a run of only scale words is not a number
            if all(r in SCALE_WORDS for r in run):
                out.append(_Tok(w, "word"))
                i += 1
                continue
            out.append(_Tok(_collapse_number(run), "digit"))
            i = j
            continue
        if t.kind == "word" and w in PHONETIC:
            letters = ""
            j = i
            while j < n and toks[j].kind == "word" and toks[j].text in PHONETIC:
                letters += PHONETIC[toks[j].text]
                j += 1
            out.append(_Tok(letters, "letter"))
            i = j
            continue
        out.append(t)
        i += 1

    # Merge ICAO marker + number (+ letters) into one callsign token; also ICAO code already
    # separated from its digits ("ACA 123").
    merged: list[_Tok] = []
    i = 0
    while i < len(out):
        t = out[i]
        if t.kind == "icao" or (t.kind == "word" and t.text in ICAO_TO_TELEPHONY):
            if i + 1 < len(out) and out[i + 1].kind == "digit" and "." not in out[i + 1].text:
                cs = t.text + out[i + 1].text
                i += 2
                if i < len(out) and out[i].kind == "letter" and len(out[i].text) <= 2:
                    cs += out[i].text
                    i += 1
                merged.append(_Tok(cs, "callsign"))
                continue
        merged.append(t)
        i += 1
    return merged


def _runways(toks: list[_Tok]) -> list[_Tok]:
    out: list[_Tok] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if (t.kind == "digit" and _INT_TOKEN.match(t.text) and len(t.text) <= 2
                and i + 1 < len(toks) and toks[i + 1].text in RUNWAY_SIDES):
            after = toks[i + 2].text if i + 2 < len(toks) else ""
            if after not in _NO_RUNWAY_AFTER:
                out.append(_Tok(t.text.zfill(2) + RUNWAY_SIDES[toks[i + 1].text], "runway"))
                i += 2
                continue
        out.append(t)
        i += 1
    return out


def normalize(text: str) -> str:
    """Normalize dataset-convention ATC text to digits and ICAO codes. Idempotent."""
    if not text or not text.strip():
        return ""
    words = _pre_tokenize(text)
    toks = _apply_telephony(words)
    toks = _collapse(toks)
    toks = _runways(toks)
    return " ".join(t.text for t in toks if t.text != BREAK)


# ---------------------------------------------------------------------------
# Speech side: digits back to ICAO words, for TTS and correction phrases
# ---------------------------------------------------------------------------

_SPOKEN_DIGIT = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
    "6": "six", "7": "seven", "8": "eight", "9": "niner",
}
_SPOKEN_SIDE = {"L": "left", "R": "right", "C": "centre"}


def spell_digits(n: float | str) -> str:
    """Spell a value one digit at a time in ICAO words: 240 -> 'two four zero', 124.65 -> '... decimal ...'."""
    s = str(n)
    if isinstance(n, float) and s.endswith(".0"):
        s = s[:-2]
    words: list[str] = []
    for ch in s:
        if ch.isdigit():
            words.append(_SPOKEN_DIGIT[ch])
        elif ch == ".":
            words.append("decimal")
        elif ch.upper() in _SPOKEN_SIDE:
            words.append(_SPOKEN_SIDE[ch.upper()])
        elif ch.isalpha():
            words.append(_LETTER_WORD[ch.upper()])
    return " ".join(words)


_LETTER_WORD: dict[str, str] = {
    "A": "alfa", "B": "bravo", "C": "charlie", "D": "delta", "E": "echo", "F": "foxtrot",
    "G": "golf", "H": "hotel", "I": "india", "J": "juliett", "K": "kilo", "L": "lima",
    "M": "mike", "N": "november", "O": "oscar", "P": "papa", "Q": "quebec", "R": "romeo",
    "S": "sierra", "T": "tango", "U": "uniform", "V": "victor", "W": "whiskey", "X": "xray",
    "Y": "yankee", "Z": "zulu",
}


def spell_altitude_ft(ft: float) -> str:
    """4500 -> 'four thousand five hundred'; 11000 -> 'one one thousand' (ICAO style)."""
    ft = int(round(float(ft)))
    thousands, rem = divmod(ft, 1000)
    hundreds = rem // 100
    parts: list[str] = []
    if thousands:
        parts.append(f"{spell_digits(thousands)} thousand")
    if hundreds:
        parts.append(f"{spell_digits(hundreds)} hundred")
    return " ".join(parts) or "zero"


def spoken_callsign(callsign: str) -> str:
    """ACA123 -> 'Air Canada one two three'. Unknown prefixes are spelled phonetically."""
    m = re.match(r"^([A-Z]{3})(\d+)([A-Z]*)$", callsign)
    if m and m.group(1) in ICAO_TO_TELEPHONY:
        tail = spell_digits(m.group(2))
        if m.group(3):
            tail += " " + spell_digits(m.group(3))
        return f"{ICAO_TO_TELEPHONY[m.group(1)]} {tail}"
    return spell_digits(callsign)


def phrase_item(item: Item) -> str:
    """ICAO phraseology for one item, e.g. 'descend flight level two four zero'."""
    a = item.action or ""
    v = item.value
    if item.type == "altitude":
        level = f"flight level {spell_digits(int(v))}" if item.unit == "FL" else spell_altitude_ft(float(v))
        verb = {"climb": "climb", "descend": "descend", "maintain": "maintain"}.get(a, "maintain")
        return f"{verb} {level}"
    if item.type == "heading":
        hdg = spell_digits(str(int(v)).zfill(3))
        if a == "turn_left":
            return f"turn left heading {hdg}"
        if a == "turn_right":
            return f"turn right heading {hdg}"
        return f"fly heading {hdg}"
    if item.type == "speed":
        return f"speed {spell_digits(int(v))} knots"
    if item.type == "frequency":
        return f"contact {spell_digits(v)}"
    if item.type == "squawk":
        return f"squawk {spell_digits(str(v).zfill(4))}"
    if item.type == "altimeter":
        if item.unit == "hPa":
            return f"QNH {spell_digits(int(v))}"
        return f"altimeter {spell_digits(str(v).replace('.', ''))}"
    if item.type == "route":
        return f"proceed direct {str(v).upper()}"
    if item.type in ("runway", "hold_short"):
        rwy = f"runway {spell_digits(str(v))}"
        verb = {
            "cleared_land": "cleared to land", "cleared_takeoff": "cleared for takeoff",
            "hold_short": "hold short", "line_up_wait": "line up and wait",
        }.get(a, "runway")
        return f"{verb} {rwy}" if verb != "runway" else rwy
    return str(v)


def phrase_from_items(callsign: str, items: list[Item]) -> str:
    """Full transmission in ICAO phraseology: 'Air Canada one two three, descend flight level two four zero'."""
    parts = [phrase_item(i) for i in items]
    head = spoken_callsign(callsign) if callsign else ""
    body = ", ".join(parts)
    return f"{head}, {body}" if head and body else head or body
