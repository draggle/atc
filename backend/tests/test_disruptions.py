"""Phase 5: one Disruption type with many kinds, seeded Random, altitude bands, expiry, release."""
import asyncio
import importlib
import math

import numpy as np
import pytest

import disruptions as DZ
from planner.conflicts import zone_mask
from planner.trajectory import samples_array
from schemas import AircraftState, FlightSpec, Scenario, Waypoint, Zone
from world import World

PL = importlib.import_module("planner.plan")  # `planner.plan` the name is the function, not the module


def make_world(name: str = "demo", warm_s: float = 600.0):
    events: list[dict] = []
    w = World(events.append, synthesize=False, realtime=False)
    w.load(name)
    w.error_rate = 0.0
    w.fleet.set_error_rate(0.0)
    w.start()
    asyncio.run(fly(w, warm_s))
    events.clear()
    return w, events


async def fly(w: World, seconds: float) -> None:
    """Advance the clock with a controller who says every card as soon as the flight is there."""
    for _ in range(int(seconds // 10)):
        for c in sorted([c for c in w.cards.values() if c.status == "pending" and c.callsign in w.sim.active],
                        key=lambda c: c.urgency_s):
            await w.speak_card(c.id)
        await w.tick(10)


def notices(events: list[dict]) -> list[str]:
    return [e["payload"]["text"] for e in events if e["type"] == "notice"]


# --------------------------------------------------------------------------- every kind

@pytest.mark.parametrize("kind", list(DZ.PROFILES))
def test_every_kind_lands_and_gets_a_plan(kind):
    w, events = make_world()
    d = w.add_disruption(kind)
    assert d is not None and d.kind == kind and d.shape == DZ.PROFILES[kind].shape
    assert d.id in w.disruptions
    sent = [e for e in events if e["type"] == "disruption"]
    assert sent and sent[-1]["payload"]["id"] == d.id
    assert "lat" in sent[-1]["payload"] and sent[-1]["payload"]["active"] is True
    assert any(d.id in n or d.label in n for n in notices(events))
    assert w.plan is not None and w.plan.conflicts == 0
    if d.shape == "point":
        a = w.sim.active[d.id]
        assert a.is_intruder and a.threat == kind
        assert all(p.callsign != d.id for p in w.plan.paths)  # nobody plans an intruder's path for it
    else:
        z = next(z for z in w.sim.zones if z.id == d.id)
        assert z.floor_ft == d.floor_ft and z.ceiling_ft == d.ceiling_ft


def test_old_names_still_work():
    w, _ = make_world()
    d = w.add_disruption("intruder", -60.0, -80.0)
    assert d.kind == "fighter" and w.sim.active[d.id].is_intruder
    s = w.add_disruption("storm", 30.0, 20.0)
    assert s.shape == "circle" and (s.x_nm, s.y_nm) == (30.0, 20.0)


def test_unknown_kind_is_refused_with_a_notice():
    w, events = make_world()
    assert w.add_disruption("kraken") is None
    assert any("kraken" in n for n in notices(events))


def test_placing_outside_the_sector_is_refused():
    w, events = make_world()
    assert w.add_disruption("balloon", 900.0, 900.0) is None
    assert not w.disruptions and any("outside the sector" in n for n in notices(events))


def test_state_carries_the_menu_and_the_active_disruptions():
    w, events = make_world()
    d = w.add_disruption("storm")
    st = [e for e in events if e["type"] == "state"][-1]["payload"]
    assert {k["kind"] for k in st["disruption_kinds"]} == set(DZ.PROFILES)
    assert [x["id"] for x in st["disruptions"]] == [d.id]


# --------------------------------------------------------------------------- random

def test_random_is_repeatable():
    def presses():
        w, _ = make_world()
        out = []
        for _ in range(4):
            d = w.add_disruption("random")
            out.append((d.kind, d.id, round(d.x_nm, 3), round(d.y_nm, 3), d.hdg_deg and round(d.hdg_deg, 3)))
        return out
    assert presses() == presses()


def test_random_matters_and_stays_safe():
    w, events = make_world("dense", warm_s=900)
    before = w.monitor.losses
    rerouted = 0
    for _ in range(4):
        d = w.add_disruption("random")
        assert d is not None
        rerouted += sum(1 for e in events if e["type"] == "instruction_card" and e["payload"]["status"] == "pending")
        events.clear()
        asyncio.run(fly(w, 240))
    assert rerouted > 0  # it lands where the traffic is
    assert w.monitor.losses == before  # and with the cards flown, nobody gets close


def test_a_random_zone_does_not_land_on_a_plane():
    w, _ = make_world("dense", warm_s=900)
    for kind in ("storm", "closed", "rocket"):
        d = w.add_disruption(kind)
        for a in w.sim.aircraft():
            if not a.is_intruder:
                assert math.hypot(a.x_nm - d.x_nm, a.y_nm - d.y_nm) > d.radius_nm


# --------------------------------------------------------------------------- zones in time and height

def test_zone_mask_respects_band_expiry_and_drift():
    z = Zone(id="AREA1", x_nm=0, y_nm=0, radius_nm=10, floor_ft=30000, ceiling_ft=34000, t0=0, expires_t=600)
    row = lambda t, x, alt: np.array([[t, x, 0.0, alt]])
    assert zone_mask(row(100, 0, 32000), [z]).all()
    assert not zone_mask(row(100, 0, 36000), [z]).any()  # 2,000 ft above the ceiling
    assert zone_mask(row(100, 0, 34500), [z]).all()  # inside the 1,000 ft margin
    assert not zone_mask(row(100, 0, 28000), [z]).any()
    assert not zone_mask(row(1000, 0, 32000), [z]).any()  # gone by then
    drifting = Zone(id="STORM1", x_nm=0, y_nm=0, radius_nm=10, hdg_deg=90, gs_kt=36, t0=0)  # 0.6 NM a minute east
    assert zone_mask(row(0, 0, 32000), [drifting]).all()
    assert not zone_mask(row(3600, 0, 32000), [drifting]).any()  # it has moved 36 NM east
    assert zone_mask(row(3600, 36, 32000), [drifting]).all()


def test_closed_airspace_lets_a_flight_go_over():
    wps = [Waypoint(name="WEST", x_nm=-90, y_nm=0), Waypoint(name="EAST", x_nm=90, y_nm=0)]
    sc = Scenario(name="t", waypoints=wps, flights=[FlightSpec(callsign="ACA1", route=["WEST", "EAST"], alt_ft=33000)])
    wall = Zone(id="AREA1", x_nm=0, y_nm=0, radius_nm=60, kind="closed", floor_ft=31000, ceiling_ft=34000)
    p = PL.plan(sc, wps, [wall], 3.0)
    path = p.paths[0]
    assert not any(c.startswith("unresolved") for c in path.changes)
    assert any(c.startswith("altitude") and c.endswith("to clear AREA1") for c in path.changes)
    assert not zone_mask(samples_array(path), [wall]).any()


def test_a_zone_on_top_of_a_flight_gets_it_out_and_keeps_it_out():
    wps = [Waypoint(name="WEST", x_nm=-90, y_nm=0), Waypoint(name="EAST", x_nm=90, y_nm=0)]
    spec = FlightSpec(callsign="ACA1", route=["WEST", "EAST"], alt_ft=33000, gs_kt=450)
    sc = Scenario(name="t", waypoints=wps, flights=[spec])
    first = PL.plan(sc, wps, [], 3.0)
    now = 600.0  # 75 NM along: 15 NM west of the centre
    state = AircraftState(callsign="ACA1", x_nm=-15, y_nm=2, alt_ft=33000, target_alt_ft=33000, hdg_deg=90,
                          gs_kt=450, route=["EAST"], t=now)
    storm = Zone(id="STORM1", x_nm=0, y_nm=0, radius_nm=25, t0=now)
    p = PL.replan(first, [state], wps, [storm], 3.0, flights=[spec], now_t=now, frozen_s=0.0)
    arr = samples_array(p.paths[0])
    inside = zone_mask(arr, [storm])
    assert inside[0] and not inside[-1]
    out = int(np.argmin(inside))
    assert not inside[out:].any()  # once out, never back in
    direct_s = (25 + 15) / 450 * 3600  # straight on through the middle
    assert out * 10 < direct_s  # it took a shorter way out than that


# --------------------------------------------------------------------------- the bug this phase found

def test_a_periodic_replan_still_sees_the_intruder():
    """replan() used to know about an intruder only on the call that introduced it."""
    wps = [Waypoint(name="WEST", x_nm=-90, y_nm=0), Waypoint(name="EAST", x_nm=90, y_nm=0)]
    spec = FlightSpec(callsign="ACA1", route=["WEST", "EAST"], alt_ft=35000, gs_kt=450)
    sc = Scenario(name="t", waypoints=wps, flights=[spec])
    first = PL.plan(sc, wps, [], 3.0)
    now = 120.0
    me = AircraftState(callsign="ACA1", x_nm=-75, y_nm=0, alt_ft=35000, target_alt_ft=35000, hdg_deg=90,
                       gs_kt=450, route=["EAST"], t=now)
    # 75 NM south of the point ACA1 reaches in ten minutes, flying north at the same speed.
    jet = AircraftState(callsign="VIPER1", x_nm=0, y_nm=-75, alt_ft=35000, target_alt_ft=35000, hdg_deg=0,
                        target_hdg_deg=0, gs_kt=450, is_intruder=True, threat="fighter", t=now)
    p = PL.replan(first, [me, jet], wps, [], 3.0, flights=[spec], now_t=now)  # no `disruption=`: a periodic repair
    mine = next(x for x in p.paths if x.callsign == "ACA1")
    assert any(c.endswith("to clear VIPER1") for c in mine.changes)
    assert p.conflicts == 0


# --------------------------------------------------------------------------- ending

def test_expiry_tells_the_screen_and_frees_the_flights():
    w, events = make_world()
    d = w.add_disruption("rocket")
    assert d.expires_t is not None
    asyncio.run(fly(w, d.expires_t - w.sim.t + 20))
    assert d.id not in w.disruptions and all(z.id != d.id for z in w.sim.zones)
    ended = [e for e in events if e["type"] == "disruption" and e["payload"]["id"] == d.id and not e["payload"]["active"]]
    assert ended
    assert any("has cleared" in n for n in notices(events))
    assert not any(c.endswith(f"to clear {d.id}") for p in w.plan.paths for c in p.changes)


def test_remove_by_hand():
    w, events = make_world()
    jet = w.add_disruption("fighter")
    storm = w.add_disruption("storm")
    w.remove_disruption(jet.id)
    w.remove_disruption(storm.id)
    assert jet.id not in w.sim.active and all(z.id != storm.id for z in w.sim.zones)
    assert not w.disruptions
    assert sum(1 for n in notices(events) if "removed" in n) == 2


def test_a_lingering_balloon_does_not_keep_the_run_alive():
    w, _ = make_world()
    w.add_disruption("balloon")
    for a in list(w.sim.active.values()):
        if not a.is_intruder:
            w.sim.active.pop(a.callsign)
    w.sim.pending.clear()
    assert w.sim.done()


# --------------------------------------------------------------------------- emergency

def test_emergency_takes_over_a_real_flight():
    w, events = make_world()
    before = {a.callsign for a in w.sim.aircraft()}
    d = w.add_disruption("emergency")
    assert d.id in before  # one of ours, not a new aircraft
    a = w.sim.active[d.id]
    assert a.is_intruder and a.threat == "emergency" and a.target_alt == 10000 and a.route == []
    assert all(p.callsign != d.id for p in w.plan.paths)
    assert not any(c.callsign == d.id and c.status == "pending" for c in w.cards.values())
    alt0 = a.alt
    asyncio.run(fly(w, 60))
    assert d.id not in w.sim.active or w.sim.active[d.id].alt < alt0 - 2500  # an emergency descent, not 1,500 fpm
    said = [e["payload"] for e in events if e["type"] == "transcript" and "mayday" in (e["payload"].get("text_raw") or "").lower()]
    assert said  # it says so on frequency


def test_emergency_needs_a_flight():
    events: list[dict] = []
    w = World(events.append, synthesize=False, realtime=False)
    w.load("demo")
    while w.sim.active:
        w.sim.active.popitem()
    assert w.add_disruption("emergency") is None
    assert any("emergency" in n.lower() for n in notices(events))


# --------------------------------------------------------------------------- cards

def test_superseded_cards_are_withdrawn_on_the_screen():
    w, events = make_world()
    w.add_disruption("storm")
    w.add_disruption("fighter")
    gone = [e["payload"]["id"] for e in events if e["type"] == "instruction_card" and e["payload"]["status"] == "superseded"]
    assert all(i not in w.cards for i in gone)
    pending = [c.callsign for c in w.cards.values() if c.status == "pending"]
    assert len(pending) == len(set(pending))  # at most one live instruction per flight


def test_a_card_for_a_flight_not_yet_in_the_sector_waits():
    events: list[dict] = []
    w = World(events.append, synthesize=False, realtime=False)
    w.load("demo")
    w.start()
    later = next((c for c in w.cards.values() if c.callsign not in w.sim.active), None)
    if later is None:
        pytest.skip("every card in this scenario is for a flight already in the sector")
    asyncio.run(w.speak_card(later.id))
    assert w.cards[later.id].status == "pending"
    assert any("not in the sector yet" in n for n in notices(events))
