import pytest

from text_norm import normalize


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Lufthansa 253, descend flight level 240.", "lufthansa two five three descend flight level two four zero"),
        ("LUFTHANSA TWO FIVE THREE", "lufthansa two five three"),
        ("contact departure 124.65", "contact departure one two four decimal six five"),
        ("one two four point six five", "one two four decimal six five"),
        ("niner tree fife", "nine three five"),
        ("alpha bravo juliet", "alfa bravo juliett"),
        ("alfa juliett", "alfa juliett"),
        ("x-ray", "xray"),
        ("runway 24L", "runway two four l"),
        ("  climb   flight  level\t100 ", "climb flight level one zero zero"),
        ("squawk 4521", "squawk four five two one"),
        ("", ""),
        ("Roger, wilco!", "roger wilco"),
        ("hold-short runway 06", "hold short runway zero six"),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_idempotent():
    s = "Lufthansa 253, descend FL240 niner alpha x-ray"
    once = normalize(s)
    assert normalize(once) == once


def test_reference_and_hypothesis_agree_after_normalization():
    ref = "speedbird one two tree descend flight level niner zero"
    hyp = "Speedbird 123 descend flight level 90"
    assert normalize(ref) == normalize(hyp)


def test_phrases_are_already_normalized():
    """The synthetic generator must emit text the normalizer leaves untouched."""
    import random
    from phrases import random_line

    rng = random.Random(1)
    for _ in range(200):
        line = random_line(rng)
        assert normalize(line) == line, line
