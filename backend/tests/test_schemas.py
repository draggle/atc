from schemas import AircraftState, Item, OpenClearance, Verdict, event


def test_roundtrip():
    c = OpenClearance(id="c1", callsign="ACA123", items=[Item(type="altitude", value=240, unit="FL", action="descend")], issued_at=0.0)
    assert OpenClearance.model_validate(c.model_dump()) == c
    v = Verdict(clearance_id="c1", readback_transmission_id="t2", result="mismatch", error_type="wrong_value")
    e = event("alert", v)
    assert e["type"] == "alert" and e["payload"]["result"] == "mismatch"
    a = AircraftState(callsign="X", x_nm=0, y_nm=0, alt_ft=1, target_alt_ft=1, hdg_deg=0, gs_kt=1)
    assert a.route == []
