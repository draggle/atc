"""Monte Carlo robustness runs. docs/07-build-spec.md section 6.

Three arms, common random numbers per run so the arms differ only in what we claim they do:
  fixed     the flights fly their fixed waypoint routes, no instructions
  tower_off the planner's instructions are issued; a wrong readback is flown uncorrected
  tower_on  same, but a wrong readback is corrected 3 s after the alert

Disturbances per run: speed jitter +-3%, entry shift +-60 s, instruction delay 5 to 30 s,
readback errors injected at `error_rate` per instruction. Error injection happens at the
sim-command level, so this stays independent of the audio pipeline (Stream B).

The tower_on arm also runs `planner.risk.predict` every RISK_EVERY_S of sim time (TRD 07) and
counts pairs that first crossed REPLAN_P and pairs that later dropped below SHOW_P with no loss
of separation, so the batch report and the live scoreboard count the same thing.
"""
from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from planner import risk
from planner.cards import cards_from_plan, followup_cards, item_to_sim_command, parse_change
from planner.plan import plan as plan_fn
from planner.plan import replan
from schemas import InstructionCard, Plan, Scenario, Scoreboard, SimCommand
from sim.engine import Simulator
from sim.monitor import SeparationMonitor
from sim.scenarios import multiply

ARMS = ("fixed", "tower_off", "tower_on")
ALERT_LATENCY_S = 3.0
STEP_S = 2.0
MAX_SIM_S = 3 * 3600.0
RISK_EVERY_S = 10.0
RISK_N = 64  # rollouts per prediction in the eval; small to keep a 20-run batch bounded


@dataclass
class _Pending:
    t: float
    callsign: str
    cmds: list[SimCommand]


@dataclass
class RunStats:
    los: int = 0
    flight_hours: float = 0.0
    miles: float = 0.0
    time_s: float = 0.0
    closest: list[float] = field(default_factory=list)
    errors_injected: int = 0
    errors_caught: int = 0
    instructions: int = 0
    replans: int = 0
    conflicts_predicted: int = 0  # pairs that first reached risk.REPLAN_P in a prediction
    conflicts_resolved: int = 0   # of those, pairs that later fell below risk.SHOW_P with no LoS


def _corrupt(cmd: SimCommand, rng: random.Random, waypoints: list[str]) -> SimCommand:
    """A plausible wrong readback: off by a level, a heading, a speed, or the wrong fix."""
    if cmd.kind == "altitude":
        return SimCommand(kind="altitude", value=float(cmd.value) + rng.choice([-2000, -1000, 1000, 2000]))
    if cmd.kind == "heading":
        return SimCommand(kind="heading", value=(float(cmd.value) + rng.choice([-30, -20, -10, 10, 20, 30])) % 360)
    if cmd.kind == "speed":
        return SimCommand(kind="speed", value=float(cmd.value) + rng.choice([-30, -20, 20, 30]))
    if cmd.kind == "direct":
        others = [w for w in waypoints if w != cmd.value]
        return SimCommand(kind="direct", value=rng.choice(others) if others else cmd.value)
    return cmd


def _entry_delays(plan: Plan) -> dict[str, float]:
    out = {}
    for path in plan.paths:
        for text in path.changes:
            ch = parse_change(text)
            if ch is not None and ch.kind == "delay":
                out[path.callsign] = float(ch.value)
    return out


def _disturb(scenario: Scenario, rng: random.Random, delays: dict[str, float]) -> Scenario:
    sc = scenario.model_copy(deep=True)
    for f in sc.flights:
        if f.is_intruder:
            continue
        f.gs_kt = f.gs_kt * (1 + rng.uniform(-0.03, 0.03))
        f.entry_time_s = max(0.0, f.entry_time_s + delays.get(f.callsign, 0.0) + rng.uniform(-60, 60))
    return sc


def _had_los(mon: SeparationMonitor, key: tuple[str, str]) -> bool:
    return key in mon._open or any((e[0], e[1]) == key for e in mon.events)


def _simulate(scenario: Scenario, nominal: Scenario, plan0: Plan, arm: str, rng: random.Random,
              error_rate: float, buffer_nm: float, replan_s: float) -> RunStats:
    """Fly one disturbed run. Planned arms are card-driven: cards from the plan, replanned every replan_s."""
    sim = Simulator(scenario)
    mon = SeparationMonitor()
    stats = RunStats()
    wp_names = sorted(sim.waypoints)
    queue: list[_Pending] = []  # instructions waiting to be spoken (controller delay)
    corrections: list[_Pending] = []  # tower_on: correct command re-issued after the alert
    unspoken: dict[str, list[InstructionCard]] = {}  # cards for flights not yet on frequency
    followed: set[tuple[str, str]] = set()
    current = plan0
    next_replan = replan_s
    next_risk = 0.0
    predicted: dict[tuple[str, str], bool] = {}  # pair -> resolved yet

    def schedule(cards: list[InstructionCard], t: float) -> None:
        for c in cards:
            cmds = [item_to_sim_command(i) for i in c.items]
            cmds = [x for x in cmds if x.kind != "none"]
            if not cmds:
                continue
            # Spoken with a controller delay, but no earlier than the plan says it should take effect
            # (the frozen window), and right away for an emergency card.
            delay = rng.uniform(2, 6) if c.urgency_s == 0 else max(rng.uniform(5, 30), c.urgency_s - 5)
            if sim.get(c.callsign) is None:
                unspoken.setdefault(c.callsign, []).append(c)
            else:
                queue.append(_Pending(t + delay, c.callsign, cmds))

    def speak(p: _Pending, t: float) -> None:
        stats.instructions += 1
        if rng.random() < error_rate:
            stats.errors_injected += 1
            bad = rng.randrange(len(p.cmds))
            for i, cmd in enumerate(p.cmds):
                sim.apply(p.callsign, _corrupt(cmd, rng, wp_names) if i == bad else cmd)
            if arm == "tower_on":
                stats.errors_caught += 1
                corrections.append(_Pending(t + ALERT_LATENCY_S, p.callsign, [p.cmds[bad]]))
            return
        for cmd in p.cmds:
            sim.apply(p.callsign, cmd)

    if arm != "fixed":
        schedule(cards_from_plan(current, None, 0.0), 0.0)

    while not sim.done() and sim.t < MAX_SIM_S:
        sim.step(STEP_S)
        t = sim.t
        if arm != "fixed":
            for cs in [cs for cs in unspoken if sim.get(cs) is not None]:
                for c in unspoken.pop(cs):
                    queue.append(_Pending(t + rng.uniform(5, 30), cs, [item_to_sim_command(i) for i in c.items]))
            for p in [p for p in queue if p.t <= t]:
                queue.remove(p)
                if sim.get(p.callsign) is not None:
                    speak(p, t)
            for p in [p for p in corrections if p.t <= t]:
                corrections.remove(p)
                for cmd in p.cmds:
                    sim.apply(p.callsign, cmd)
            states = sim.aircraft()
            for c in followup_cards(current, states, t):
                key = (c.callsign, str(c.items[0].value))
                if key not in followed:
                    followed.add(key)
                    queue.append(_Pending(t + rng.uniform(2, 6), c.callsign, [item_to_sim_command(c.items[0])]))
            if arm == "tower_on" and t >= next_risk and states:
                next_risk += RISK_EVERY_S
                report = risk.predict(states, {p.callsign: p for p in current.paths}, nominal.zones, t,
                                      n=RISK_N, seed=int(t))
                above = {(min(p.a, p.b), max(p.a, p.b)): p.p_max for p in report.pairs}
                for key, p_max in above.items():
                    if p_max >= risk.REPLAN_P and key not in predicted:
                        predicted[key] = False
                        stats.conflicts_predicted += 1
                for key, done in predicted.items():
                    if not done and above.get(key, 0.0) < risk.SHOW_P and not _had_los(mon, key):
                        predicted[key] = True
                        stats.conflicts_resolved += 1
            if t >= next_replan and states:
                next_replan += replan_s
                new = replan(current, states, nominal.waypoints, nominal.zones, buffer_nm, None, frozen_s=60,
                             flights=nominal.flights, now_t=t, time_budget_s=0.2)
                stats.replans += 1
                schedule(cards_from_plan(new, current, t, states), t)
                current = new
        mon.observe(list(sim.active.values()), t)
    mon.finish()
    stats.los = mon.losses
    acs = list(sim.removed.values()) + list(sim.active.values())
    reg = [a for a in acs if not a.is_intruder]
    stats.flight_hours = sum(a.airborne_s for a in reg) / 3600.0
    stats.miles = sum(a.distance_nm for a in reg)
    stats.time_s = sum(a.airborne_s for a in reg)
    stats.closest = [v[0] for v in mon.closest.values()]
    return stats


def run(scenario: Scenario, n_runs: int = 20, seed: int = 0, arms: tuple[str, ...] = ARMS,
        error_rate: float = 0.02, density: float = 1.0, buffer_nm: float | None = None,
        plan_budget_s: float = 0.3, replan_s: float = 60.0,
        on_run: Callable[[int, int], None] | None = None) -> dict:
    """Monte Carlo over `n_runs` disturbed copies of `scenario`. Returns a JSON-friendly dict.

    The planned arms replan every `replan_s` seconds of sim time from the live radar picture,
    so late flights and readback deviations get repaired the way the live app would.
    `on_run(done, total)` is called after every run (all arms flown), for progress reporting.
    """
    sc = multiply(scenario, density) if density != 1.0 else scenario
    buf = sc.separation_buffer_nm if buffer_nm is None else buffer_nm
    plan = plan_fn(sc, sc.waypoints, sc.zones, buf, time_budget_s=plan_budget_s)
    delays = _entry_delays(plan)
    per_arm: dict[str, list[RunStats]] = {a: [] for a in arms}
    for k in range(n_runs):
        base_seed = seed * 100003 + k
        for arm in arms:
            rng = random.Random(base_seed)  # common random numbers across arms
            disturbed = _disturb(sc, rng, delays if arm != "fixed" else {})
            per_arm[arm].append(_simulate(disturbed, sc, plan, arm, random.Random(base_seed + 7), error_rate, buf, replan_s))
        if on_run is not None:
            on_run(k + 1, n_runs)
    fixed_miles = float(np.mean([s.miles for s in per_arm.get("fixed", [])])) if "fixed" in per_arm else None
    fixed_time = float(np.mean([s.time_s for s in per_arm.get("fixed", [])])) if "fixed" in per_arm else None
    out = {
        "scenario": sc.name, "n_runs": n_runs, "seed": seed, "error_rate": error_rate, "density": density,
        "buffer_nm": buf, "plan_conflicts": plan.conflicts, "replan_s": replan_s, "arms": {},
    }
    for arm, runs in per_arm.items():
        los = sum(s.los for s in runs)
        fh = sum(s.flight_hours for s in runs)
        closest = np.array([c for s in runs for c in s.closest]) if any(s.closest for s in runs) else np.array([])
        miles = float(np.mean([s.miles for s in runs]))
        tsec = float(np.mean([s.time_s for s in runs]))
        inj = sum(s.errors_injected for s in runs)
        caught = sum(s.errors_caught for s in runs)
        sb = Scoreboard(
            miles_saved=(fixed_miles - miles) if fixed_miles is not None else 0.0,
            time_saved_s=(fixed_time - tsec) if fixed_time is not None else 0.0,
            losses_of_separation=los, closest_approach_nm=float(closest.min()) if closest.size else None,
            errors_injected=inj, errors_caught=caught, false_alarms=0,
            mean_alert_latency_s=ALERT_LATENCY_S if arm == "tower_on" and caught else None,
            transmissions=sum(s.instructions for s in runs),
            conflicts_predicted=sum(s.conflicts_predicted for s in runs),
            conflicts_resolved=sum(s.conflicts_resolved for s in runs),
        )
        out["arms"][arm] = {
            "los_total": los, "flight_hours": round(fh, 3),
            "los_per_flight_hour": (los / fh) if fh else 0.0,
            "closest_min_nm": float(closest.min()) if closest.size else None,
            "closest_p5_nm": float(np.percentile(closest, 5)) if closest.size else None,
            "closest_p50_nm": float(np.percentile(closest, 50)) if closest.size else None,
            "miles_mean": miles, "time_mean_s": tsec,
            "miles_vs_baseline": (miles - fixed_miles) if fixed_miles is not None else None,
            "miles_vs_baseline_pct": ((miles - fixed_miles) / fixed_miles * 100) if fixed_miles else None,
            "errors_injected": inj, "errors_caught": caught,
            "replans": sum(s.replans for s in runs),
            "conflicts_predicted": sum(s.conflicts_predicted for s in runs),
            "conflicts_resolved": sum(s.conflicts_resolved for s in runs),
            "scoreboard": sb.model_dump(),
        }
    return out
