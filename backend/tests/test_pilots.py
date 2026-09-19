import json
import random

import pytest

from pilots.errors import ALL_ERROR_TYPES, inject_error, sample_error_type
from pilots.pilot import AIPilot, PilotFleet, items_to_sim_commands
from pilots.readback import build_readback, roger, say_again, say_callsign
from schemas import Item, OpenClearance

ACTIVE = ["ACA123", "ACA133", "WJA456", "DLH253", "BAW9"]


def clearance(items, callsign="ACA123", cid="c1"):
    return OpenClearance(id=cid, callsign=callsign, items=items, issued_at=0.0)


def multi_items():
    return [
        Item(type="altitude", value=240, unit="FL", action="descend"),
        Item(type="heading", value=270, unit="deg", action="turn_left"),
        Item(type="speed", value=250, unit="kt", action="reduce"),
        Item(type="frequency", value=124.65, unit="MHz", action="contact departure"),
        Item(type="runway", value="24L", action="cleared_land"),
        Item(type="squawk", value="4521"),
    ]


# ---------------------------------------------------------------------------
# readback
# ---------------------------------------------------------------------------


def test_readback_dataset_convention():
    text = build_readback("ACA123", multi_items()[:1], None, shorten=False)
    assert text == "descend flight level two four zero, air canada one two three"
    assert text == text.lower()
    assert not any(ch.isdigit() for ch in text)


def test_readback_variants_cover_shortened_callsign():
    seen = set()
    for seed in range(60):
        seen.add(build_readback("ACA123", multi_items()[:1], random.Random(seed)))
    joined = " | ".join(seen)
    assert len(seen) > 5
    assert "canada one two three" in joined
    assert "air canada one two three, " in joined or ", air canada one two three" in joined


def test_frequency_and_runway_spelling():
    fr = build_readback("DLH253", [Item(type="frequency", value=124.65, unit="MHz", action="contact")], None, shorten=False)
    assert "one two four decimal six five" in fr and "lufthansa two five three" in fr
    rw = build_readback("BAW9", [Item(type="runway", value="24L", action="cleared_land")], None, shorten=False)
    assert "two four left" in rw and "speedbird nine" in rw
    from pilots.readback import ICAO_TO_TELEPHONY
    assert say_callsign("WJA456", "short") == f"{ICAO_TO_TELEPHONY['WJA']} four five six"
    assert say_callsign("ACA123", "short") == "canada one two three"
    assert say_again("ACA123").startswith("say again")
    assert roger("ACA123") == "roger, air canada one two three"


# ---------------------------------------------------------------------------
# error injection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("etype", ALL_ERROR_TYPES)
def test_every_error_type_is_producible(etype):
    rng = random.Random(1)
    items = multi_items()
    out, cs, got, desc = inject_error(items, "ACA123", ACTIVE, rng, error_type=etype)
    assert got == etype, desc
    assert desc
    before = build_readback("ACA123", items, None, shorten=False)
    after = build_readback(cs, out, None, shorten=False)
    if etype == "wrong_value":
        assert [o.value for o in out] != [i.value for i in items]
        assert [o.type for o in out] == [i.type for i in items]
    elif etype == "wrong_runway":
        rw_before = next(i for i in items if i.type == "runway").value
        rw_after = next(o for o in out if o.type == "runway").value
        assert rw_before != rw_after
    elif etype == "wrong_direction":
        acts_before = [i.action for i in items]
        acts_after = [o.action for o in out]
        assert acts_before != acts_after
        assert ("climb" in after) != ("climb" in before) or ("right" in after) != ("right" in before)
    elif etype == "wrong_unit":
        assert [(o.type, o.unit) for o in out] != [(i.type, i.unit) for i in items]
    elif etype == "omitted_item":
        assert len(out) == len(items) - 1
    elif etype == "ack_only":
        assert out == items and cs == "ACA123"
    elif etype == "wrong_aircraft":
        assert cs != "ACA123" and cs in ACTIVE
    elif etype == "missing_readback":
        assert out == items
    # original list untouched
    assert [i.value for i in items] == [i.value for i in multi_items()]


def test_wrong_value_examples_plausible():
    rng = random.Random(3)
    fl = [Item(type="altitude", value=240, unit="FL", action="descend")]
    for _ in range(20):
        out, _, et, _ = inject_error(fl, "ACA123", ACTIVE, rng, error_type="wrong_value")
        assert et == "wrong_value"
        assert out[0].value != 240 and 50 <= out[0].value <= 450 and out[0].value % 10 == 0
    hd = [Item(type="heading", value=270, unit="deg", action="turn_left")]
    out, _, _, _ = inject_error(hd, "ACA123", ACTIVE, rng, error_type="wrong_value")
    assert out[0].value != 270 and 1 <= out[0].value <= 360
    fq = [Item(type="frequency", value=124.65, unit="MHz", action="contact")]
    out, _, _, _ = inject_error(fq, "ACA123", ACTIVE, rng, error_type="wrong_value")
    assert out[0].value != 124.65 and 118.0 <= float(out[0].value) <= 137.0


def test_wrong_unit_heading_becomes_speed():
    rng = random.Random(5)
    hd = [Item(type="heading", value=250, unit="deg", action="turn_left")]
    out, _, et, desc = inject_error(hd, "ACA123", ACTIVE, rng, error_type="wrong_unit")
    assert et == "wrong_unit" and out[0].type == "speed" and out[0].value == 250
    text = build_readback("ACA123", out, None, shorten=False)
    assert "knots" in text and "heading" not in text


def test_inapplicable_type_falls_through():
    rng = random.Random(7)
    items = [Item(type="altitude", value=240, unit="FL", action="descend")]
    _, _, et, _ = inject_error(items, "ACA123", ACTIVE, rng, error_type="wrong_runway")
    assert et is not None and et != "wrong_runway"


def test_sample_error_type_respects_weights():
    rng = random.Random(0)
    kinds = {sample_error_type(rng, {k: 0 for k in ALL_ERROR_TYPES} | {"wrong_aircraft": 1}) for _ in range(20)}
    assert kinds == {"wrong_aircraft"}
    counts = {k: 0 for k in ALL_ERROR_TYPES}
    for _ in range(2000):
        counts[sample_error_type(rng)] += 1
    assert counts["wrong_value"] > counts["wrong_runway"]
    assert counts["wrong_aircraft"] > counts["missing_readback"]


def test_similar_callsign_preferred():
    rng = random.Random(11)
    picks = [inject_error(multi_items(), "ACA123", ACTIVE, rng, error_type="wrong_aircraft")[1] for _ in range(200)]
    assert picks.count("ACA133") > picks.count("BAW9")


# ---------------------------------------------------------------------------
# pilot and fleet
# ---------------------------------------------------------------------------


def test_sim_command_follows_spoken_value(tmp_path):
    p = AIPilot("ACA123", rng=random.Random(2), synthesize=False, data_dir=tmp_path)
    c = clearance([Item(type="altitude", value=240, unit="FL", action="descend")])
    r = p.respond(c, active_callsigns=ACTIVE, error_type="wrong_value")
    assert r.injected_error == "wrong_value"
    assert r.sim_command.kind == "altitude"
    assert r.sim_command.value == r.spoken_items[0].value * 100
    assert r.sim_command.value != 24000
    assert r.text and r.text == r.text.lower()
    lines = (tmp_path / "ground_truth.jsonl").read_text().strip().splitlines()
    gt = json.loads(lines[-1])
    assert gt["error_type"] == "wrong_value" and gt["spoken_text"] == r.text
    assert gt["true_items"][0]["value"] == 240


FIXES = ["WAKOL", "GALTO", "ESTIR", "PIKAR", "CENTA", "TULEK", "ZAMIR"]


def test_a_wrong_fix_can_be_read_back_when_the_pilot_knows_the_fixes():
    """The headline error: cleared direct ESTIR, reads back another real fix."""
    direct = [Item(type="route", value="ESTIR", action="direct")]
    seen = set()
    for seed in range(40):
        out, cs, et, desc = inject_error(direct, "ACA123", ACTIVE, random.Random(seed), error_type="wrong_value",
                                         waypoints=FIXES)
        assert et == "wrong_value" and cs == "ACA123"
        assert out[0].type == "route" and out[0].action == "direct"
        assert out[0].value in FIXES and out[0].value != "ESTIR", "another fix that exists, never the cleared one"
        assert "ESTIR" in desc and str(out[0].value) in desc
        seen.add(out[0].value)
    assert len(seen) > 1, "not always the same wrong fix"
    assert direct[0].value == "ESTIR", "the true clearance is never mutated"


def test_without_a_fix_list_a_direct_cannot_get_a_wrong_value():
    direct = [Item(type="route", value="ESTIR", action="direct")]
    for wps in (None, [], ["ESTIR"]):
        _, _, et, _ = inject_error(direct, "ACA123", ACTIVE, random.Random(1), error_type="wrong_value", waypoints=wps)
        assert et != "wrong_value"


def test_the_plane_flies_to_the_fix_the_pilot_read_back(tmp_path):
    p = AIPilot("ACA123", rng=random.Random(3), synthesize=False, data_dir=tmp_path)
    c = clearance([Item(type="route", value="ESTIR", action="direct")])
    r = p.respond(c, active_callsigns=ACTIVE, error_type="wrong_value", waypoints=FIXES)
    assert r.injected_error == "wrong_value"
    wrong = r.spoken_items[0].value
    assert wrong != "ESTIR" and wrong in FIXES
    assert r.sim_command.kind == "direct" and r.sim_command.value == wrong, "the plane obeys the readback"
    assert str(wrong).lower() in r.text


def test_ack_only_is_roger_and_flies_true_clearance(tmp_path):
    p = AIPilot("ACA123", rng=random.Random(2), synthesize=False, data_dir=tmp_path)
    c = clearance([Item(type="altitude", value=240, unit="FL", action="descend")])
    r = p.respond(c, active_callsigns=ACTIVE, error_type="ack_only")
    assert r.kind == "ack" and r.injected_error == "ack_only"
    assert r.text.split(",")[0] in ("roger", "wilco", "copied", "air canada one two three")
    assert "two four zero" not in r.text
    assert r.sim_command.kind == "altitude" and r.sim_command.value == 24000


def test_wrong_aircraft_other_plane_flies(tmp_path):
    p = AIPilot("ACA123", rng=random.Random(4), synthesize=False, data_dir=tmp_path)
    c = clearance([Item(type="heading", value=270, unit="deg", action="turn_left")])
    r = p.respond(c, active_callsigns=ACTIVE, error_type="wrong_aircraft")
    assert r.injected_error == "wrong_aircraft"
    assert r.acting_callsign != "ACA123" and r.acting_callsign in ACTIVE
    assert r.spoken_callsign == r.acting_callsign
    assert "one two three" not in r.text or r.acting_callsign.endswith("123")
    assert r.sim_command.kind == "heading" and r.sim_command.value == 270


def test_missing_readback_is_silent(tmp_path):
    p = AIPilot("ACA123", rng=random.Random(4), synthesize=False, data_dir=tmp_path)
    c = clearance([Item(type="heading", value=270, unit="deg", action="turn_left")])
    r = p.respond(c, active_callsigns=ACTIVE, error_type="missing_readback")
    assert r.kind == "silent" and r.text is None and r.audio_path is None
    assert r.sim_command.kind == "none"


def test_say_again_and_correction(tmp_path):
    p = AIPilot("ACA123", rng=random.Random(4), synthesize=False, data_dir=tmp_path)
    c = clearance(multi_items()[:2])
    r = p.respond(c, heard_ok=False, active_callsigns=ACTIVE)
    assert r.kind == "say_again" and "say again" in r.text and r.sim_command.kind == "none"
    r2 = p.respond_to_correction(c)
    assert r2.kind == "correction" and r2.injected_error is None
    assert "flight level two four zero" in r2.text and "two seven zero" in r2.text
    assert r2.text.endswith("air canada one two three")
    assert [cmd.kind for cmd in r2.sim_commands] == ["altitude", "heading"]


def test_items_to_sim_commands_mapping():
    cmds = items_to_sim_commands(multi_items() + [Item(type="route", value="BOSOX", action="direct")])
    assert [(c.kind, c.value) for c in cmds] == [
        ("altitude", 24000.0), ("heading", 270.0), ("speed", 250.0), ("direct", "BOSOX"),
    ]


def test_fleet_determinism_under_seed(tmp_path):
    def run(seed):
        fleet = PilotFleet(error_rate=0.5, seed=seed, synthesize=False, data_dir=tmp_path / str(seed))
        for cs in ACTIVE:
            fleet.get(cs)
        out = []
        for i in range(30):
            cs = ACTIVE[i % len(ACTIVE)]
            c = clearance(multi_items()[: 1 + i % 3], callsign=cs, cid=f"c{i}")
            r = fleet.respond(c)
            out.append((r.text, r.injected_error, r.acting_callsign, r.sim_command.model_dump()))
        return out

    a, b, c = run(1), run(1), run(2)
    assert a == b
    assert a != c
    assert any(e for _, e, _, _ in a), "error_rate 0.5 should inject at least one error in 30 tries"
    assert any(e is None for _, e, _, _ in a)


def test_fleet_error_rate_zero_never_errs(tmp_path):
    fleet = PilotFleet(error_rate=0.0, seed=9, synthesize=False, data_dir=tmp_path)
    for i in range(20):
        r = fleet.respond(clearance(multi_items()[:2], cid=f"c{i}"))
        assert r.injected_error is None and r.kind == "readback"
        assert r.sim_command.value == 24000


# --- regressions found when the real ElevenLabs key went in (Sept 19) -------------------------


def test_controller_voice_is_valid_for_the_active_backend(monkeypatch):
    from pilots.tts import ELEVEN_CONTROLLER_VOICE, TTS

    monkeypatch.delenv("ELEVENLABS_CONTROLLER_VOICE_ID", raising=False)
    eleven = TTS(backend="elevenlabs")
    assert eleven.controller_voice() == ELEVEN_CONTROLLER_VOICE
    assert eleven.controller_voice() not in eleven.voices  # never confused with a pilot
    assert eleven.controller_voice() != "Alex"  # a macOS name is a 404 on ElevenLabs
    monkeypatch.setenv("ELEVENLABS_CONTROLLER_VOICE_ID", "custom-id")
    assert TTS(backend="elevenlabs").controller_voice() == "custom-id"
    assert TTS(backend="silent").controller_voice() == "beep"


def test_default_eleven_voices_avoid_paid_library_ids():
    from pilots.tts import ELEVEN_VOICES

    paid_only = {"21m00Tcm4TlvDq8ikWAM", "ErXwobaYiN019PkySvjV", "TxGEqnHWrfWFTfGW9XjX",
                 "VR6AewLTigWG4xSOukaG", "AZnzlk1XvdvUeBnXmlld", "MF3mGyEYCl7XYWbV9V6O"}
    assert not paid_only & set(ELEVEN_VOICES)
    assert len(set(ELEVEN_VOICES)) == len(ELEVEN_VOICES) >= 6


def test_failed_synthesis_beeps_without_poisoning_the_cache(tmp_path, monkeypatch, caplog):
    from pilots.tts import TTS

    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)  # makes the real call fail, no network
    tts = TTS(backend="elevenlabs", cache_dir=tmp_path)
    with caplog.at_level("WARNING", logger="tower.tts"):
        out = tts.synthesize("descend flight level two four zero", tts.voices[0])
    assert out.name.endswith(".fallback.wav") and out.exists()
    assert not tts.cache_path("descend flight level two four zero", tts.voices[0]).exists()
    assert tts.last_error and any("beep instead" in r.message for r in caplog.records)
