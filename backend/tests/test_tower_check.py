from schemas import Item, OpenClearance, Transmission
from tower.check import NullChecker, check, missing_verdict
from tower.commands import items_to_sim_command
from tower.normalize import normalize
from tower.parse import parse

ACTIVE = ["ACA123", "ACA133", "DAL456"]


def clearance(text, callsign=None, cid="c1"):
    ext = parse(normalize(text), ACTIVE, "controller")
    return OpenClearance(id=cid, callsign=callsign or ext.callsign, items=ext.items, issued_at=0.0)


def readback(text, n_best=(), conf=1.0, tid="t2"):
    norm = normalize(text)
    tx = Transmission(id=tid, t_start=5, t_end=7, audio_ref="", text_raw=text, text_norm=norm,
                      asr_confidence=conf, speaker="pilot", n_best=[normalize(h) for h in n_best])
    return parse(norm, ACTIVE, "pilot", tid), tx


def run(ctrl, pilot, model=None, **kw):
    c = clearance(ctrl)
    ext, tx = readback(pilot, **kw)
    return check(c, ext, tx, model=model, active=ACTIVE)


class FakeChecker:
    def __init__(self, label, conf):
        self.label, self.conf = label, conf

    def predict(self, controller_text, pilot_text):
        return self.label, self.conf


# --- the "what counts as a match" table -----------------------------------------------------------

def test_table_paraphrase_matches():
    v = run("air canada one two three descend and maintain flight level two four zero",
            "down to flight level two four zero air canada one two three")
    assert v.result == "match"


def test_table_wrong_value():
    v = run("air canada one two three descend flight level two four zero",
            "descend flight level two one zero air canada one two three")
    assert (v.result, v.error_type) == ("mismatch", "wrong_value")
    assert v.correction_phrase == "Air Canada one two three, negative, descend flight level two four zero"
    assert "240" in v.reason and "210" in v.reason


def test_table_partial_altitude_omitted():
    v = run("air canada one two three turn left heading two seven zero descend four thousand",
            "left two seven zero air canada one two three")
    assert (v.result, v.error_type) == ("partial", "omitted_item")
    assert "4000" in v.reason


def test_table_wrong_direction():
    v = run("air canada one two three climb flight level three one zero",
            "descend three one zero air canada one two three")
    assert (v.result, v.error_type) == ("mismatch", "wrong_direction")


def test_table_frequency_shortened_matches():
    v = run("delta four five six contact departure one two four decimal six five",
            "one two four six five good day delta four five six")
    assert v.result == "match"


def test_table_roger_to_hold_short_is_ack_only():
    v = run("air canada one two three hold short runway two four left", "roger")
    assert (v.result, v.error_type) == ("mismatch", "ack_only")
    assert v.correction_phrase.startswith("Air Canada one two three, read back hold short runway two four left")


def test_table_wrong_aircraft():
    v = run("air canada one two three descend flight level two four zero",
            "descend flight level two four zero air canada one three three")
    assert (v.result, v.error_type) == ("mismatch", "wrong_aircraft")


# --- rest of the taxonomy -------------------------------------------------------------------------

def test_wrong_runway_left_right():
    v = run("air canada one two three cleared to land runway two four left",
            "cleared to land runway two four right air canada one two three")
    assert (v.result, v.error_type) == ("mismatch", "wrong_runway")


def test_wrong_turn_direction():
    v = run("air canada one two three turn left heading two seven zero",
            "right two seven zero air canada one two three")
    assert (v.result, v.error_type) == ("mismatch", "wrong_direction")


def test_wrong_unit_flight_level_vs_feet():
    v = run("air canada one two three descend flight level one zero zero",
            "descend one zero thousand feet air canada one two three")
    assert (v.result, v.error_type) == ("mismatch", "wrong_unit")


def test_wrong_unit_heading_read_as_speed():
    v = run("air canada one two three fly heading two five zero",
            "speed two five zero air canada one two three")
    assert (v.result, v.error_type) == ("mismatch", "wrong_unit")


def test_missing_readback_verdict():
    c = clearance("air canada one two three descend flight level two four zero")
    v = missing_verdict(c)
    assert (v.result, v.error_type) == ("missing", "missing_readback")
    assert v.correction_phrase == "Air Canada one two three, confirm descend flight level two four zero"


def test_traffic_info_roger_is_fine():
    c = OpenClearance(id="c9", callsign="ACA123", issued_at=0.0,
                      items=[Item(type="other", value="traffic", mandatory=False)])
    ext, tx = readback("roger air canada one two three")
    assert check(c, ext, tx).result == "match"


# --- layers 2 and 3 -------------------------------------------------------------------------------

def test_n_best_rule_turns_mismatch_into_ambiguous():
    v = run("air canada one two three descend flight level two four zero",
            "descend flight level two one zero air canada one two three",
            n_best=["descend flight level two four zero air canada one two three"])
    assert v.result == "ambiguous"
    assert v.error_type == "wrong_value"  # candidate kept for the resolver
    assert v.correction_phrase is not None


def test_n_best_without_expected_stays_mismatch():
    v = run("air canada one two three descend flight level two four zero",
            "descend flight level two one zero air canada one two three",
            n_best=["descend flight level two two zero air canada one two three"])
    assert v.result == "mismatch"


def test_low_asr_confidence_is_ambiguous():
    v = run("air canada one two three descend flight level two four zero",
            "descend flight level two one zero air canada one two three", conf=0.4)
    assert v.result == "ambiguous"


def test_checker_model_disagreement_is_ambiguous_and_agreement_raises_confidence():
    base = run("air canada one two three descend flight level two four zero",
               "descend flight level two one zero air canada one two three", model=NullChecker())
    assert base.result == "mismatch"
    agree = run("air canada one two three descend flight level two four zero",
                "descend flight level two one zero air canada one two three",
                model=FakeChecker("wrong_value", 0.9))
    assert agree.result == "mismatch" and agree.confidence >= base.confidence
    disagree = run("air canada one two three descend flight level two four zero",
                   "descend flight level two one zero air canada one two three",
                   model=FakeChecker("correct", 0.8))
    assert disagree.result == "ambiguous"


def test_items_to_sim_command_from_readback():
    ext, _ = readback("descend flight level two one zero air canada one two three")
    cmd = items_to_sim_command(ext.items)
    assert (cmd.kind, cmd.value) == ("altitude", 21000.0)
    ext, _ = readback("left two seven zero air canada one two three")
    assert items_to_sim_command(ext.items).model_dump(exclude={"via"}) == {"kind": "heading", "value": 270.0}
    ext, _ = readback("direct bosox air canada one two three")
    assert items_to_sim_command(ext.items).value == "BOSOX"
    ext, _ = readback("one two four six five air canada one two three")
    assert items_to_sim_command(ext.items).kind == "none"


# --- a garbled fix name is not an omission (found live, Sept 19: "direct estir" heard as "direct to 6")


def _route_case(pilot_text, heard_items=None):
    from schemas import Extraction, Item, OpenClearance, Transmission
    from tower.check import check

    c = OpenClearance(id="c1", callsign="ACA123", issued_at=0.0,
                      items=[Item(type="route", value="ESTIR", action="direct")])
    tx = Transmission(id="t2", t_start=5, t_end=7, audio_ref="", text_raw=pilot_text, text_norm=pilot_text,
                      asr_confidence=0.76, speaker="pilot", n_best=[])
    ext = Extraction(transmission_id="t2", callsign="ACA123", items=heard_items or [])
    return check(c, ext, tx, active=["ACA123", "ACA133"])


def test_garbled_fix_name_is_ambiguous_not_an_alert():
    v = _route_case("direct to 6 ACA123")
    assert v.result == "ambiguous" and "not understood" in v.reason and "ESTIR" in v.reason


def test_direct_fused_with_the_fix_name_is_still_an_audible_routing():
    """Found live: "direct estir" came back from Whisper as one word, "Director". The pilot did read
    a routing back. That is "I could not hear the fix", never "No readback of route ESTIR"."""
    for heard in ("director ACA123", "directed ACA123", "directly ACA123", "direction ACA123"):
        v = _route_case(heard)
        assert v.result == "ambiguous" and "not understood" in v.reason, (heard, v.result, v.reason)


def test_no_routing_read_back_is_still_an_error():
    assert _route_case("roger ACA123").result == "mismatch"
    assert _route_case("climbing ACA123").result == "mismatch"


def test_a_different_real_fix_is_still_a_wrong_value():
    from schemas import Item

    v = _route_case("direct PIKAR ACA123", [Item(type="route", value="PIKAR", action="direct")])
    assert v.result == "mismatch" and v.error_type == "wrong_value"
