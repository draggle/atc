from schemas import AircraftState, Extraction, Item, OpenClearance, Transmission
from tower.conform import ConformanceMonitor
from tower.state import State, StateStore


def clr(cid, callsign, items=None, issued_at=0.0, timeout=25.0):
    return OpenClearance(id=cid, callsign=callsign, issued_at=issued_at, timeout_s=timeout,
                         items=items or [Item(type="altitude", value=240, unit="FL", action="descend")])


def tx(tid="t1", t=1.0, speaker="pilot"):
    return Transmission(id=tid, t_start=t, t_end=t + 1, audio_ref="", text_raw="", text_norm="", speaker=speaker)


def test_open_moves_to_expecting_and_resolve_moves_on():
    s = StateStore()
    assert s.state_of("ACA123") is State.UNKNOWN
    s.open(clr("c1", "ACA123"))
    assert s.state_of("ACA123") is State.EXPECTING_READBACK
    s.resolve("c1", "matched")
    assert s.state_of("ACA123") is State.READBACK_OK
    s.open(clr("c2", "ACA123"))
    s.resolve("c2", "mismatched")
    assert s.state_of("ACA123") is State.READBACK_ERROR


def test_missing_readback_after_timeout():
    s = StateStore(timeout_s=25.0)
    s.open(clr("c1", "ACA123", issued_at=10.0, timeout=25.0))
    assert s.tick(30.0) == []
    timed_out = s.tick(35.0)
    assert [c.id for c in timed_out] == ["c1"]
    assert timed_out[0].status == "missing"
    assert s.state_of("ACA123") is State.MISSING_READBACK
    assert s.tick(40.0) == []  # only fires once


def test_pilot_transmission_routes_to_own_clearance_or_pilot_reporting():
    s = StateStore()
    s.open(clr("c1", "ACA123"))
    ext = Extraction(transmission_id="t1", callsign="ACA123", items=[Item(type="altitude", value=240, unit="FL")])
    assert s.on_pilot_transmission(ext, tx()).id == "c1"
    idle = Extraction(transmission_id="t2", callsign="DAL456", items=[])
    assert s.on_pilot_transmission(idle, tx("t2")) is None
    assert s.state_of("DAL456") is State.PILOT_REPORTING
    assert len(s.history("ACA123")) == 1


def test_wrong_aircraft_readback_matches_other_clearance_by_items():
    s = StateStore()
    s.open(clr("c1", "ACA123"))
    other = Extraction(transmission_id="t1", callsign="ACA133",
                       items=[Item(type="altitude", value=240, unit="FL", action="descend")])
    m = s.find_clearance_for(other)
    assert m.clearance.id == "c1" and m.by_callsign is False


def test_similar_callsign_warning():
    s = StateStore()
    s.open(clr("c1", "ACA123"))
    s.open(clr("c2", "ACA133"))
    s.open(clr("c3", "DAL456"))
    assert s.similar_callsign_warnings() == [("ACA123", "ACA133")]


# --- radar verification ---------------------------------------------------------------------------

def state(callsign, alt, hdg=90.0, t=0.0, x=0.0, y=0.0):
    return AircraftState(callsign=callsign, x_nm=x, y_nm=y, alt_ft=alt, target_alt_ft=alt, hdg_deg=hdg, gs_kt=420, t=t)


def test_conformance_altitude_pass_through_alerts():
    m = ConformanceMonitor()
    c = clr("c1", "ACA123")  # descend FL240
    m.watch(c, now=0.0)
    assert m.tick([state("ACA123", 30000)], 0.0) == []
    assert m.tick([state("ACA123", 26000)], 30.0) == []
    v = m.tick([state("ACA123", 23500)], 60.0)
    assert len(v) == 1 and v[0].result == "mismatch" and v[0].error_type == "wrong_value"
    assert "Radar" in v[0].reason and v[0].decided_by == "rules"
    assert m.watches == []


def test_conformance_altitude_reached_closes_silently():
    m = ConformanceMonitor()
    m.watch(clr("c1", "ACA123"), now=0.0)
    m.tick([state("ACA123", 30000)], 0.0)
    m.tick([state("ACA123", 27000)], 30.0)
    assert m.tick([state("ACA123", 24100)], 60.0) == []
    assert m.watches == []


def test_conformance_wrong_way_for_20s_alerts():
    m = ConformanceMonitor()
    m.watch(clr("c1", "ACA123"), now=0.0)
    m.tick([state("ACA123", 30000)], 0.0)
    assert m.tick([state("ACA123", 30500)], 5.0) == []
    assert m.tick([state("ACA123", 31000)], 15.0) == []
    v = m.tick([state("ACA123", 32000)], 26.0)
    assert len(v) == 1 and "away from" in v[0].reason


def test_conformance_heading_converges_or_alerts():
    from tower.conform import TURN_MARGIN_S, TURN_RATE_DEG_S

    c = clr("c1", "ACA123", items=[Item(type="heading", value=270, unit="deg", action="turn_left")])
    # It never starts to turn: caught quickly, without waiting a whole turn's worth of time.
    m = ConformanceMonitor()
    m.watch(c, now=0.0)
    assert m.tick([state("ACA123", 30000, hdg=90)], 5.0) == []
    v = m.tick([state("ACA123", 30000, hdg=91)], 40.0)
    assert len(v) == 1 and "not turning" in v[0].reason
    # It turns, slowly, and is given the time a 180 degree turn needs. Then it is called out.
    m = ConformanceMonitor()
    m.watch(c, now=100.0)
    assert m.tick([state("ACA123", 30000, hdg=90)], 101.0) == []
    assert m.tick([state("ACA123", 30000, hdg=140)], 140.0) == []
    v = m.tick([state("ACA123", 30000, hdg=200)], 101.0 + 180 / TURN_RATE_DEG_S + TURN_MARGIN_S + 1)
    assert len(v) == 1 and "not converged" in v[0].reason
    # It gets there: the watch closes quietly.
    m = ConformanceMonitor()
    m.watch(c, now=400.0)
    assert m.tick([state("ACA123", 30000, hdg=265)], 430.0) == [] and m.watches == []


def test_conformance_direct_uses_bearing():
    m = ConformanceMonitor(waypoints={"BOSOX": (0.0, 50.0)})  # due north
    c = clr("c1", "ACA123", items=[Item(type="route", value="BOSOX", action="direct")])
    m.watch(c, now=0.0)
    assert m.tick([state("ACA123", 30000, hdg=5)], 10.0) == [] and m.watches == []
    m.watch(c, now=20.0)
    assert m.tick([state("ACA123", 30000, hdg=180)], 25.0) == []  # first look
    v = m.tick([state("ACA123", 30000, hdg=180)], 60.0)  # 35 s on and still flying away from it
    assert len(v) == 1 and "BOSOX" in v[0].reason
