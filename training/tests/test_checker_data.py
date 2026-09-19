import random
from collections import Counter

from gen_checker_data import LABELS, INJECTORS, asr_noise, generate
from phrases import controller_text, pilot_readback, random_clearance
from text_norm import normalize


def test_eight_classes():
    assert len(LABELS) == 8 and LABELS[0] == "correct"
    assert set(INJECTORS) == set(LABELS) - {"correct"}


def test_generate_is_balanced_and_deterministic():
    a = generate(800, seed=3, noise_frac=0.3)
    b = generate(800, seed=3, noise_frac=0.3)
    assert a == b
    counts = Counter(r["label"] for r in a)
    assert set(counts) == set(LABELS)
    assert max(counts.values()) - min(counts.values()) <= 1
    noisy = sum(r["meta"]["asr_noise"] for r in a) / len(a)
    assert 0.2 < noisy < 0.4


def test_error_rows_differ_from_correct_readback():
    rows = generate(400, seed=5, noise_frac=0.0)
    for r in rows:
        assert r["pilot"].strip()
        assert r["pilot"] != r["controller"]
        assert normalize(r["pilot"]) == r["pilot"]
        assert normalize(r["controller"]) == r["controller"]


def test_ack_only_has_no_values():
    rng = random.Random(0)
    for _ in range(50):
        c = random_clearance(rng)
        text = INJECTORS["ack_only"](c, rng)
        assert any(w in text for w in ("roger", "wilco", "copied", "okay", "copy"))
        assert "flight level" not in text and "heading" not in text


def test_asr_noise_keeps_length_reasonable():
    rng = random.Random(1)
    text = "lufthansa two five three descend flight level two four zero"
    for _ in range(50):
        out = asr_noise(text, rng, "correct")
        assert abs(len(out.split()) - len(text.split())) <= 1
        # digit words are never changed on correct rows (a swap would flip the label)
        # (garbles like five->fife are allowed; the normalizer folds them back)
        keep = ("two", "five", "three", "four", "zero")
        digits = [w for w in text.split() if w in keep]
        assert [w for w in normalize(out).split() if w in keep] == digits
