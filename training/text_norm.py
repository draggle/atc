"""Text normalizer applied identically to references and hypotheses before WER.

This is the *evaluation* normalizer: it maps everything to the dataset
convention (lowercase, digits spelled out one at a time, one spelling per
phonetic letter). It is deliberately not the backend normalizer, which goes the
other way (words to digits and ICAO codes).

Rules, in order:
  1. lowercase
  2. hyphens and slashes become spaces, remaining punctuation is dropped
  3. each digit character becomes its spoken word ("124.65" -> "one two four decimal six five")
  4. ATC variant spellings collapse: niner->nine, tree->three, fife->five,
     alpha->alfa, juliet->juliett, x-ray->xray, center->centre, oh->zero (whole tokens only)
  5. whitespace collapses
"""
from __future__ import annotations

import re

DIGIT_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]

# Variant token -> canonical token. Applied on whole tokens only.
TOKEN_MAP = {
    "niner": "nine",
    "tree": "three",
    "fife": "five",
    "fower": "four",
    "alpha": "alfa",
    "juliet": "juliett",
    "julliet": "juliett",
    "center": "centre",
    "ok": "okay",
    "o": "zero",
    "oh": "zero",
    "decimal": "decimal",
    "point": "decimal",
}

_PUNCT_TO_SPACE = re.compile(r"[-/–—_]")
# Keep letters, digits, '.' between digits (handled below) and whitespace. Drop the rest.
_DROP = re.compile(r"[^a-z0-9.\s]")
_DIGIT = re.compile(r"\d")
_DOT_BETWEEN_DIGITS = re.compile(r"(?<=\d)\.(?=\d)")
_WS = re.compile(r"\s+")


def _spell_digits(text: str) -> str:
    # A dot between two digits is a spoken "decimal" (frequencies like 124.65).
    text = _DOT_BETWEEN_DIGITS.sub(" decimal ", text)
    text = text.replace(".", " ")
    return _DIGIT.sub(lambda m: f" {DIGIT_WORDS[int(m.group())]} ", text)


def normalize(text: str) -> str:
    """Normalize one transcript string. Idempotent."""
    if not text:
        return ""
    t = text.lower()
    t = re.sub(r"\bx-ray\b", "xray", t)
    t = _PUNCT_TO_SPACE.sub(" ", t)
    t = _DROP.sub("", t)
    t = _spell_digits(t)
    tokens = [TOKEN_MAP.get(tok, tok) for tok in t.split()]
    return _WS.sub(" ", " ".join(tokens)).strip()


if __name__ == "__main__":
    import sys

    for line in sys.stdin:
        print(normalize(line))
