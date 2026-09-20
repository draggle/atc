import math

from schemas import Disruption, FlightSpec, Scenario, SimCommand, Waypoint
from sim.engine import CLIMB_FPM, TURN_RATE_DEG_S, Simulator
from sim.scenarios import generate, list_scenarios, load, multiply


def _line_scenario(**kw) -> Scenario:
    wps = [Waypoint(name="A", x_nm=-50, y_nm=0), Waypoint(name="B", x_nm=0, y_nm=0), Waypoint(name="C", x_nm=50, y_nm=0)]
    f = FlightSpec(callsign="ACA123", route=["A", "B", "C"], alt_ft=30000, gs_kt=360, **kw)
    return Scenario(name="t", waypoints=wps, flights=[f])


def test_turn_rate_and_climb_rate():
    sim = Simulator(_line_scenario())
    a = sim.get("ACA123")
    assert a is not None and abs(a.hdg - 90) < 1e-6
    sim.apply("ACA123", SimCommand(kind="heading", value=180))
    sim.apply("ACA123", SimCommand(kind="altitude", value=33000))
    sim.step(10.0)
    assert math.isclose(a.hdg, 90 + TURN_RATE_DEG_S * 10, abs_tol=1e-6)
    assert math.isclose(a.alt, 30000 + CLIMB_FPM / 60 * 10, abs_tol=1e-6)
    for _ in range(int(90 / TURN_RATE_DEG_S)):
        sim.step(1.0)
    assert math.isclose(a.hdg, 180.0, abs_tol=1e-6)
    assert a.target_hdg == 180.0  # route following suspended


def test_speed_and_position_advance():
    sim = Simulator(_line_scenario())
    a = sim.get("ACA123")
    sim.step(10.0)
    assert math.isclose(a.x, -50 + 360 / 3600 * 10, abs_tol=1e-6)
    sim.apply("ACA123", SimCommand(kind="speed", value=380))
    sim.step(5.0)
    assert math.isclose(a.gs, 365.0)


def test_waypoint_following_and_exit():
    sim = Simulator(_line_scenario())
    a = sim.get("ACA123")
    assert a.route == ["B", "C"]
    while sim.get("ACA123") is not None and sim.t < 4000:
        sim.step(1.0)
        if sim.get("ACA123") and sim.get("ACA123").x > 1.5:
            assert sim.get("ACA123").route == ["C"]
    assert "ACA123" in sim.removed and sim.done()
    assert 980 < sim.t < 1030  # 100 NM at 360 kt is 1000 s, minus the 1 NM capture radius


def test_direct_drops_route_prefix():
    sim = Simulator(_line_scenario())
    sim.apply("ACA123", SimCommand(kind="direct", value="C"))
    a = sim.get("ACA123")
    assert a.route == ["C"] and a.target_hdg is None


def test_spawn_at_entry_time_and_state_contract():
    sim = Simulator(_line_scenario(entry_time_s=30))
    assert sim.aircraft() == []
    sim.step(29.0)
    assert sim.aircraft() == []
    sim.step(1.0)
    st = sim.aircraft()
    assert len(st) == 1 and st[0].callsign == "ACA123" and st[0].t == 30.0


def test_intruder_spawn_and_zone():
    sim = Simulator(_line_scenario())
    sim.add_disruption(Disruption(id="VIPER11", kind="intruder", x_nm=0, y_nm=-60, hdg_deg=0, gs_kt=600))
    v = sim.get("VIPER11")
    assert v is not None and v.is_intruder
    sim.step(60.0)
    assert math.isclose(v.y, -50, abs_tol=1e-6) and math.isclose(v.x, 0, abs_tol=1e-6)
    sim.add_disruption(Disruption(id="storm1", kind="storm", x_nm=10, y_nm=10, radius_nm=15))
    assert sim.zones[-1].id == "storm1" and sim.zones[-1].kind == "storm"
    assert sim.aircraft()[-1].is_intruder in (True, False)


def test_scenarios_load_and_generate():
    assert {"demo", "dense", "intruder"} <= set(list_scenarios())
    d = load("demo")
    assert 15 <= len(d.waypoints) <= 25 and len(d.flights) == 8
    assert {"ACA123", "ACA133"} <= {f.callsign for f in d.flights}
    assert len(load("dense").flights) >= 20
    i = load("intruder")
    v = [f for f in i.flights if f.is_intruder][0]
    assert v.entry_time_s == 300
    g = generate(3, 12)
    assert len(g.flights) == 12 and len({f.callsign for f in g.flights}) == 12
    assert len(multiply(d, 2.0).flights) == 16 and len(multiply(d, 0.5).flights) == 4
    sim = Simulator(d)
    for _ in range(60):
        sim.step(60.0)
    assert sim.done()


# --------------------------------------------------------------------------- the setup panel's custom scenario

def test_a_custom_scenario_is_its_name():
    """Everything about it is in the name, so Reset and a reload give the same sky back."""
    from sim import scenarios as SC
    name = SC.custom_name(24, "busy", 7)
    assert name == "custom/24/busy/7" and SC.is_known(name)
    a, b = SC.load(name), SC.load(name)
    assert [(f.callsign, f.entry_time_s, f.route) for f in a.flights] == [(f.callsign, f.entry_time_s, f.route) for f in b.flights]
    assert len(a.flights) == 24 and len({f.callsign for f in a.flights}) == 24
    assert sum(1 for f in a.flights if f.entry_time_s == 0) == 3  # traffic on the screen the moment Start is pressed
    assert [f.callsign for f in SC.load(SC.custom_name(24, "busy", 8)).flights] != [f.callsign for f in a.flights]
    # out of range is pulled in, nonsense is refused
    assert SC.custom_name(999, "frantic", -4) == "custom/80/normal/0"
    assert not SC.is_known("custom/12/frantic/1") and not SC.is_known("custom/x/normal/1") and not SC.is_known("custom/500/calm/1")


def test_a_custom_scenario_runs_and_resets_like_any_other():
    import asyncio

    from world import World

    w = World(lambda e: None, synthesize=False, realtime=False)
    w.load("custom/14/busy/2")
    w.set_auto_speak(True)
    w.start()
    asyncio.run(w.tick(1.0))
    first = sorted(w.sim.active)
    assert len(first) == 3 and w.plan is not None
    for _ in range(900):
        asyncio.run(w.tick(1.0))
    assert w.monitor.losses == 0
    assert w.reset() and w.scenario.name == "custom/14/busy/2"
    w.start()
    asyncio.run(w.tick(1.0))
    assert sorted(w.sim.active) == first  # the same sky again
