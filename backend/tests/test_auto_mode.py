"""Auto mode: Tower issues its own instructions, so the aircraft fly the replanned routes."""
import asyncio
import math

from world import AUTO_VOICE_MAX_SPEED, World


def make(auto: bool, speed: float = 1.0, name: str = "demo", voice: bool = True):
    events: list[dict] = []
    w = World(events.append, synthesize=False, realtime=False)
    w.auto_voice = voice
    w.load(name)
    w.error_rate = 0.0
    w.fleet.set_error_rate(0.0)
    w.set_auto_speak(auto)
    w.speed = speed
    w.start()
    return w, events


def run(w: World, seconds: int) -> dict[tuple[str, str], float]:
    """Tick one second at a time with nobody at the controls. Returns how deep anyone went into a zone."""
    inside: dict[tuple[str, str], float] = {}

    async def go():
        for _ in range(seconds):
            await w.tick(1.0)
            for z in w.sim.zones:
                for a in w.sim.active.values():
                    depth = z.radius_nm - math.hypot(a.x - z.x_nm, a.y - z.y_nm)
                    if not a.is_intruder and depth > 0 and z.floor_ft - 1000 < a.alt < z.ceiling_ft + 1000:
                        inside[(z.id, a.callsign)] = max(inside.get((z.id, a.callsign), 0.0), depth)
    asyncio.run(go())
    return inside


def test_in_auto_the_planes_fly_the_new_routes():
    w, _ = make(auto=True)
    run(w, 600)
    before = w.monitor.losses
    w.add_disruption("storm")
    w.add_disruption("fighter")
    inside = run(w, 900)
    assert inside == {}  # nobody flew through the storm
    assert w.monitor.losses == before
    on_frequency = [c for c in w.cards.values() if c.status == "pending" and c.callsign in w.sim.active]
    assert on_frequency == []  # every instruction that could be issued was
    assert any(c.via == "voice" for c in w.cards.values())


def test_in_manual_nothing_is_issued_for_you():
    w, _ = make(auto=False)
    run(w, 600)
    w.add_disruption("storm")
    run(w, 300)
    assert all(c.via is None for c in w.cards.values())
    assert w.datalink_sent == 0


def test_fast_clock_goes_by_data_link_and_says_so():
    w, events = make(auto=True, speed=AUTO_VOICE_MAX_SPEED * 10)
    run(w, 600)
    w.add_disruption("storm")
    run(w, 300)
    issued = [c for c in w.cards.values() if c.via]
    assert issued and all(c.via == "datalink" for c in issued)
    issued = [c for c in issued if not c.minor]  # small shortcuts go out quietly: no line, no counter
    assert w.datalink_sent == len(issued)
    lines = [e["payload"] for e in events if e["type"] == "transcript" and e["payload"]["speaker"] == "datalink"]
    assert len(lines) == len(issued) and all("WILCO" in p["text_raw"] and p["callsign"] for p in lines)
    # radar verification still watches a data link clearance
    assert all(c.status in ("validated", "verified") for c in issued)


def test_one_voice_exchange_at_a_time(monkeypatch):
    import world as world_module

    monkeypatch.setattr(world_module, "AUTO_VOICE_QUEUE_MAX", 0)  # nobody may wait for the voice channel
    w, _ = make(auto=True, name="dense")
    most = 0

    async def go(seconds: int):
        nonlocal most
        for _ in range(seconds):
            await w.tick(1.0)
            speaking = [c for c in w.cards.values() if c.via == "voice" and c.status == "spoken"]
            most = max(most, len(speaking))
    asyncio.run(go(900))
    for kind in ("storm", "closed", "fighter"):  # a burst of reroutes, more than one voice can handle
        w.add_disruption(kind)
    asyncio.run(go(300))
    assert most <= 1
    assert w.datalink_sent > 0  # the overflow goes by data link instead of queueing past its deadline


def test_tower_waits_while_the_human_holds_the_mic():
    """Tower's VOICE waits. Reroutes still go out by data link, which does not need the frequency."""
    w, _ = make(auto=True)
    run(w, 600)
    said_before = sum(1 for c in w.cards.values() if c.via == "voice")
    w.set_ptt(True)
    w.add_disruption("storm")
    run(w, 10)
    assert sum(1 for c in w.cards.values() if c.via == "voice") == said_before  # Tower kept quiet
    assert not [c for c in w.cards.values() if c.status == "pending" and c.callsign in w.sim.active]
    w.set_ptt(False)
    w.add_disruption("closed")
    w.add_disruption("fighter")
    run(w, 30)
    assert sum(1 for c in w.cards.values() if c.via == "voice") > said_before


def test_a_data_link_reroute_flies_the_line_on_the_map():
    """The aircraft starts turning within seconds and then stays on the planned path."""
    import numpy as np

    w, _ = make(auto=True, speed=AUTO_VOICE_MAX_SPEED * 10)  # fast clock: everything by data link
    run(w, 600)
    before = {a.callsign: a.hdg for a in w.sim.active.values()}
    for kind in ("storm", "closed", "rocket"):  # whichever of these lands across somebody's track
        w.add_disruption(kind)
    run(w, 2)  # the dispatcher runs on the clock: the instructions go out on the next tick
    moved = [c.callsign for c in w.cards.values() if c.via == "datalink" and any(i.type == "heading" for i in c.items)]
    assert moved, "three zones on the traffic should force at least one reroute"
    run(w, 6)
    turning = [cs for cs in moved if cs in w.sim.active and abs((w.sim.active[cs].hdg - before[cs] + 180) % 360 - 180) > 3]
    assert turning, "nobody had started to turn eight seconds after the storm appeared"
    worst = 0.0
    for _ in range(40):  # ten more minutes, checking the distance from the planned line
        run(w, 15)
        for cs in moved:
            a = w.sim.active.get(cs)
            path = next((p for p in w.plan.paths if p.callsign == cs), None)
            if a is None or path is None or a.via == [] and a.target_hdg is not None:
                continue
            arr = np.asarray(path.samples, dtype=float).reshape(-1, 4)
            worst = max(worst, float(np.hypot(arr[:, 1] - a.x, arr[:, 2] - a.y).min()))
    assert worst < 2.0, f"an aircraft drifted {worst:.1f} NM from its planned path"


def test_auto_never_talks_to_a_flight_that_has_not_checked_in():
    w, _ = make(auto=True)
    run(w, 30)
    for c in w.cards.values():
        if c.callsign not in w.sim.active and c.callsign not in w.sim.removed:
            assert c.status == "pending" and c.via is None


def test_silent_auto_is_instant_and_never_speaks():
    w, _ = make(auto=True, voice=False)
    run(w, 600)
    for kind in ("storm", "closed", "rocket"):
        w.add_disruption(kind)
    # No tick has run since: the reroutes must already be on the flight decks.
    assert not [c for c in w.cards.values() if c.status == "pending" and c.callsign in w.sim.active]
    issued = [c for c in w.cards.values() if c.via]
    assert issued and all(c.via == "datalink" for c in issued)
    inside = run(w, 900)
    assert inside == {}

