"""The World: one object that owns the simulator, planner, AI pilots, Tower core, and speech.

The FastAPI app (app.py) drives it: `tick()` once per second of sim time, and the WebSocket
handlers call `controller_text`, `controller_audio`, `agent_request`, `speak_card`, etc.
Every side effect becomes an Event dict pushed to `self.emit`.

Rules honoured here (see CLAUDE.md):
- The plane obeys the pilot's readback, not the clearance.
- Tower must hear the pilots: pilot audio goes through ASR, never their text.
- Tower off means readbacks are not checked and nothing is corrected.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

import numpy as np

import disruptions as DZ
from pilots.pilot import PilotFleet, PilotResponse
from pilots.radio import apply_to_file
from pilots.tts import TTS
import importlib

C = importlib.import_module("planner.cards")
PL = importlib.import_module("planner.plan")
from planner.conflicts import closest_approach
from schemas import (
    AircraftState,
    Disruption,
    FlightSpec,
    GeoFrame,
    InstructionCard,
    OpenClearance,
    Plan,
    Scenario,
    Scoreboard,
    SimCommand,
    Transmission,
    Waypoint,
    event,
)
from sim import geoframe as GEO
from sim import regions as REGIONS
from sim import scenarios as SC
from sim.engine import Simulator
from sim.monitor import SeparationMonitor
from tower.asr import ASR, build_prompt, dataset_normalize, get_asr
from tower.audio import float_to_wav, read_wav
from tower.normalize import ICAO_TO_TELEPHONY, normalize
from tower.pipeline import TowerCore

log = logging.getLogger("tower.world")

DATA_DIR = Path(os.environ.get("TOWER_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
AUDIO_DIR = DATA_DIR / "audio"
PILOT_DELAY_S = 1.5  # seconds between a clearance and the pilot keying up
REPLAN_EVERY_S = 60.0
# Auto mode. One voice exchange at a time; whatever the voice cannot get to in time goes by data link.
AUTO_VOICE_MAX_SPEED = 1.5  # faster than this and speech, which takes real seconds, cannot keep up
AUTO_VOICE_QUEUE_MAX = 0  # cards allowed to wait for the voice channel. None: reaction comes first,
#                           so one aircraft is talked round and every other reroute goes out at once
# How far ahead a new path may start. The aircraft cannot change what it does before the instruction
# reaches it: a minute for a human to say it and hear it back, seconds for Tower in Auto.
FROZEN_MANUAL_S = 60.0
FROZEN_AUTO_S = 10.0  # Auto with Tower's voice on
FROZEN_LINK_S = 0.0  # Auto, silent: the reroute is on the flight deck in the same second
REPLAN_ACTIVE_S = 15.0  # how often every path is re-checked while a disruption is in the sector
ROUTE_MIN_OFFSET_NM = 1.0  # a planned path this close to a straight line is just "direct"
AUTO_EXCHANGE_S = 12.0  # a spoken instruction and its readback, roughly
AUTO_EXCHANGE_TIMEOUT_S = 30.0  # stop waiting for a readback that never came
CARD_VERIFY_S = 30.0  # a matched clearance with no radar alert for this long is "verified"

Emit = Callable[[dict[str, Any]], None]


_CATALOG: list[dict[str, Any]] | None = None


def scenario_catalog() -> list[dict[str, Any]]:
    """Name, description and size of every built-in scenario, for the setup panel. Cached."""
    global _CATALOG
    if _CATALOG is None:
        out = []
        for name in SC.list_scenarios():
            try:
                sc = SC.load(name)
                out.append({"name": name, "description": sc.description, "flights": len(sc.flights),
                            "source": sc.source, "meta": {k: sc.meta.get(k) for k in
                                                          ("region", "label", "date", "hour_utc", "gates")
                                                          if k in sc.meta}})
            except Exception:  # a broken file must not take the screen down
                log.exception("scenario %s failed to load", name)
        _CATALOG = out
    return _CATALOG


class World:
    def __init__(self, emit: Emit, *, synthesize: bool = True, asr: ASR | None = None,
                 realtime: bool = True) -> None:
        self.emit = emit
        self.synthesize = synthesize
        self.realtime = realtime
        self.asr: ASR | None = asr
        self.tts = TTS() if synthesize else None
        self.scenario: Scenario | None = None
        self.sim = Simulator()
        self.core = TowerCore()
        self.fleet = PilotFleet(error_rate=0.1, seed=0, tts=self.tts, synthesize=synthesize)
        self.monitor = SeparationMonitor()
        self.plan: Plan | None = None
        self.baseline: Plan | None = None
        self.cards: dict[str, InstructionCard] = {}
        self.card_by_clearance: dict[str, str] = {}
        self.tower_enabled = True
        self.auto_speak = False
        self.buffer_nm = 3.0
        self.noise = 0.2
        self.error_rate = 0.1
        self.last_replan_t = 0.0
        self.pending: list[tuple[float, Callable[[], Awaitable[None]]]] = []  # (due_t, coroutine factory)
        self.clearance_meta: dict[str, dict[str, Any]] = {}  # clearance_id -> ground truth for scoring
        self.matched_at: dict[str, float] = {}
        self.alert_latencies: list[float] = []
        self.tier1_latencies: list[float] = []
        self.transmissions = 0
        self.matches = 0
        self.alerts = 0
        self.false_alarms = 0
        self.errors_injected = 0
        self.errors_caught = 0
        self.speed = 1.0
        self.card_t: dict[str, float] = {}  # card id -> sim time it was issued, for "due at"
        self._voice_card: str | None = None  # the card Tower is saying right now, in Auto
        self._voice_since = 0.0
        self.human_on_mic = False  # the controller is holding the mic: Tower keeps quiet
        self.auto_voice = False  # Auto speaks one exchange at a time. Off: every instruction by data link
        self.datalink_sent = 0
        self.rerouted: set[str] = set()
        self.reaction_s: float | None = None
        self._react: tuple[float, dict[str, float]] | None = None  # (disruption time, heading of each rerouted flight then)
        self.zone_incursions: set[tuple[str, str]] = set()
        self.in_zone_now = 0
        self.disruptions: dict[str, Disruption] = {}  # the ones still active
        self.disruption_count = 0  # seeds Random, so a rehearsed demo repeats
        # Lifecycle: nothing moves until start(). idle -> ready -> running <-> paused -> ended.
        self.lifecycle: str = "idle"
        self.world_id = 0  # bumps on every load so the screen can drop the previous world's state
        self._base_scenario: Scenario | None = None  # what reset() returns to
        self.live_loading = False  # a live snapshot is being fetched (sim/live.py); a second request is ignored
        self._lock = asyncio.Lock()
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ setup

    def load(self, name: str, max_flights: int | None = None) -> None:
        sc = SC.thin(SC.load(name), max_flights)
        self._load_scenario(sc)

    def load_scenario(self, sc: Scenario) -> None:
        """Load a scenario built elsewhere, such as a live snapshot (sim/live.py). reset() returns to it."""
        self._load_scenario(sc)

    def _load_scenario(self, sc: Scenario) -> None:
        """Build the world and its plan. Does not start the clock: lifecycle becomes "ready"."""
        # Plan first: if the planner raises on a scenario, the world it was replacing stays whole.
        baseline = PL.baseline(sc)
        plan = PL.plan(sc, sc.waypoints, sc.zones, sc.separation_buffer_nm, time_budget_s=1.0)
        self._base_scenario = sc.model_copy(deep=True)
        self.world_id += 1
        self.lifecycle = "ready"
        self.alert_latencies.clear()
        self.tier1_latencies.clear()
        self.transmissions = self.matches = self.alerts = self.false_alarms = 0
        self.errors_injected = self.errors_caught = 0
        self.scenario = sc
        self.sim = Simulator(sc)
        self.core = TowerCore(waypoints={w.name: (w.x_nm, w.y_nm) for w in sc.waypoints if w.kind != "hidden"})
        self.fleet = PilotFleet(error_rate=self.error_rate, seed=sc.seed, tts=self.tts,
                                synthesize=self.synthesize)
        self.monitor = SeparationMonitor()
        self.cards.clear()
        self.card_by_clearance.clear()
        self.clearance_meta.clear()
        self.matched_at.clear()
        self.pending.clear()
        self.card_t.clear()
        self._voice_card, self.human_on_mic, self.datalink_sent = None, False, 0
        self.rerouted, self.reaction_s, self._react, self.in_zone_now = set(), None, None, 0
        self.zone_incursions = set()
        self.disruptions.clear()
        self.disruption_count = 0
        self.buffer_nm = sc.separation_buffer_nm
        self.noise = sc.noise_level
        self.baseline = baseline
        self.plan = plan
        # Planned savings are frozen at the initial plan: after a replan the plan holds remaining
        # distance while the baseline holds full routes, so a live difference would be wrong.
        self.planned_miles_saved = self.baseline.total_distance_nm - self.plan.total_distance_nm
        self.planned_time_saved_s = self.baseline.total_time_s - self.plan.total_time_s
        self.last_replan_t = 0.0
        self.emit_state()
        self.emit_plan(self.plan, trigger="initial")
        for card in C.cards_from_plan(self.plan, None, now_t=0.0, states=self.sim.aircraft()):
            self._add_card(card)
        self.emit(event("radar", self.radar_payload(), t=0.0))
        self.emit_scoreboard()

    # ------------------------------------------------------------------ geography

    @property
    def frame(self) -> GeoFrame:
        """Where on Earth the flat sector sits. See sim/geoframe.py."""
        return self.scenario.geo if self.scenario is not None else GEO.DEFAULT_FRAME

    def _with_latlon(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add lat and lon to dicts that carry x_nm and y_nm. The flat values stay."""
        if not items:
            return items
        lat, lon = GEO.to_latlon(self.frame, [i["x_nm"] for i in items], [i["y_nm"] for i in items])
        for item, la, lo in zip(items, np.atleast_1d(lat), np.atleast_1d(lon)):
            item["lat"], item["lon"] = round(float(la), 5), round(float(lo), 5)
        return items

    def radar_payload(self, states: list[AircraftState] | None = None) -> dict[str, Any]:
        states = self.sim.aircraft() if states is None else states
        out = {"aircraft": self._with_latlon([a.model_dump() for a in states]), "t": self.sim.t,
               "watching": self.watching()}
        if any(z.gs_kt or z.swell_nm_per_min for z in self.sim.zones):
            out["zones"] = self._with_latlon([z.model_dump() for z in self.sim.zones])  # they move
        return out

    def spoken_waypoints(self) -> list[str]:
        """Fix names a human could say or hear. Hidden track vertices are never spoken."""
        return [n for n, w in self.sim.waypoints.items() if w.kind != "hidden"]

    def geo_payload(self) -> dict[str, Any]:
        half = (self.scenario.sector_nm if self.scenario else 200.0) / 2.0
        return {**self.frame.model_dump(), "half_nm": half, "bounds": GEO.bounds(self.frame, half)}

    def _disruption_payload(self, d: Disruption) -> dict[str, Any]:
        out = self._with_latlon([d.model_dump()])[0]
        if d.predicted_path:
            arr = np.asarray(d.predicted_path, dtype=float)
            lat, lon = GEO.to_latlon(self.frame, arr[:, 1], arr[:, 2])
            out["predicted_lonlat"] = [[round(float(lo), 5), round(float(la), 5), round(float(t), 1)]
                                       for lo, la, t in zip(np.atleast_1d(lon), np.atleast_1d(lat), arr[:, 0])]
        return out

    # ------------------------------------------------------------------ emitters

    def emit_state(self) -> None:
        sc = self.scenario
        self.emit(event("state", {
            "scenario": sc.name if sc else None,
            "tower_enabled": self.tower_enabled,
            "auto_speak": self.auto_speak,
            "mode": "auto" if self.auto_speak else "manual",
            "auto_voice": self.auto_voice,
            "t": self.sim.t,
            "waypoints": self._with_latlon([w.model_dump() for w in (sc.waypoints if sc else [])
                                            if w.kind != "hidden"]),
            "source": sc.source if sc else "sim",
            "meta": sc.meta if sc else {},
            "zones": self._with_latlon([z.model_dump() for z in self.sim.zones]),
            "geo": self.geo_payload(),
            "sector_nm": sc.sector_nm if sc else 200.0,
            "buffer_nm": self.buffer_nm, "error_rate": self.error_rate, "noise": self.noise,
            "speed": self.speed,
            "lifecycle": self.lifecycle,
            "world_id": self.world_id,
            "scenarios": scenario_catalog(),
            "live_regions": REGIONS.catalog(),  # live mode needs no files, so it is offered even with no replays
            "watching": self.watching(),
            "disruptions": [self._disruption_payload(d) for d in self.disruptions.values()],
            "disruption_kinds": DZ.catalog(),
        }, t=self.sim.t))

    def watching(self) -> list[str]:
        """Callsigns radar verification is currently watching."""
        return sorted({w.callsign for w in self.core.conformance.watches})

    def emit_plan(self, plan: Plan, trigger: str, changed: list[str] | None = None) -> None:
        payload = plan.model_dump()
        if self.baseline is not None:
            payload["baseline_paths"] = [p.model_dump() for p in self.baseline.paths]
        frame = self.frame
        for path in payload["paths"] + payload.get("baseline_paths", []):
            path["lonlat"] = GEO.path_lonlat(frame, path["samples"])
            # The screen draws from lonlat. The raw 10 s samples stay in the backend: 55 flights of
            # them made this message 1.3 MB, and real traffic would be several MB on every replan.
            # Endpoints are kept so a consumer can still tell where and when a path starts and ends.
            if len(path["samples"]) > 2:
                path["samples"] = [path["samples"][0], path["samples"][-1]]
        payload["trigger"] = trigger
        if changed is None:
            self.emit(event("plan", payload, t=self.sim.t))
        else:
            payload["changed"] = changed
            self.emit(event("plan_update", payload, t=self.sim.t))

    def emit_scoreboard(self) -> None:
        self.emit(event("scoreboard", self.scoreboard(), t=self.sim.t))
        self.emit(event("stats", {
            "tier1_latency_s": float(np.mean(self.tier1_latencies)) if self.tier1_latencies else None,
            "transmissions": self.transmissions, "matches": self.matches, "alerts": self.alerts,
        }, t=self.sim.t))

    def scoreboard(self) -> Scoreboard:
        miles_saved = getattr(self, "planned_miles_saved", 0.0)
        time_saved = getattr(self, "planned_time_saved_s", 0.0)
        return Scoreboard(
            miles_saved=round(miles_saved, 1), time_saved_s=round(time_saved),
            losses_of_separation=int(self.monitor.losses),
            closest_approach_nm=(round(self.monitor.closest_nm(), 2) if self.monitor.closest else None),
            errors_injected=self.errors_injected, errors_caught=self.errors_caught,
            false_alarms=self.false_alarms,
            mean_alert_latency_s=(round(float(np.mean(self.alert_latencies)), 2) if self.alert_latencies else None),
            transmissions=self.transmissions,
            tier1_latency_s=(round(float(np.mean(self.tier1_latencies)), 2) if self.tier1_latencies else None),
            rerouted=len(self.rerouted), reaction_s=self.reaction_s, datalink_sent=self.datalink_sent,
            in_zone_now=self.in_zone_now, zone_incursions=len(self.zone_incursions),
        )

    def _emit_core_events(self, events: list[dict[str, Any]]) -> None:
        """Forward Tower core events, enriching alerts and updating cards and counters."""
        for ev in events:
            typ = ev["type"]
            p = ev["payload"]
            if typ == "alert":
                if not self.tower_enabled:
                    continue
                cid = p.get("clearance_id")
                c = self.core.store.get(cid) if cid else None
                p.setdefault("callsign", c.callsign if c else None)
                meta = self.clearance_meta.get(cid, {})
                self.alerts += 1
                if meta.get("injected_error"):
                    self.errors_caught += 1
                    if "issued_real" in meta:
                        self.alert_latencies.append(time.monotonic() - meta["issued_real"])
                elif p.get("result") in ("mismatch", "partial", "missing"):
                    self.false_alarms += 1
                self._set_card_status(cid, "error")
            elif typ == "resolver_step" and not self.tower_enabled:
                continue
            elif typ == "clearance_updated":
                status = p.get("status")
                cid = p.get("id")
                if status == "matched":
                    self.matches += 1
                    self.matched_at[cid] = self.sim.t
                    self._set_card_status(cid, "validated")
            self.emit(ev)

    # ------------------------------------------------------------------ cards

    def _add_card(self, card: InstructionCard) -> None:
        self.cards[card.id] = card
        self.card_t[card.id] = self.sim.t
        if not card.minor:  # a minor shortcut is never shown: see InstructionCard.minor
            self.emit(event("instruction_card", card, t=self.sim.t))

    def _drop_pending_cards(self, callsign: str) -> None:
        """A newer plan, or an emergency, makes a flight's unspoken cards wrong. Tell the screen."""
        for old in list(self.cards.values()):
            if old.callsign == callsign and old.status == "pending":
                old.status = "superseded"
                if not old.minor:
                    self.emit(event("instruction_card", old, t=self.sim.t))
                del self.cards[old.id]

    def _set_card_status(self, clearance_id: str | None, status: str) -> None:
        if not clearance_id:
            return
        card_id = self.card_by_clearance.get(clearance_id)
        card = self.cards.get(card_id or "")
        if card and card.status != status:
            card.status = status  # type: ignore[assignment]
            self.emit(event("instruction_card", card, t=self.sim.t))

    def _link_card(self, card: InstructionCard, clearance_id: str) -> None:
        card.clearance_id = clearance_id
        card.status = "spoken"
        self.card_by_clearance[clearance_id] = card.id
        self.emit(event("instruction_card", card, t=self.sim.t))

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> bool:
        """ready or paused -> running. Returns False if there is nothing to run."""
        if self.scenario is None or self.lifecycle not in ("ready", "paused"):
            return False
        self.lifecycle = "running"
        self.emit_state()
        return True

    def pause(self) -> bool:
        if self.lifecycle != "running":
            return False
        self.lifecycle = "paused"
        self.emit_state()
        return True

    def reset(self) -> bool:
        """Back to the world as it was loaded: clock at zero, nothing issued, nothing moving."""
        if self._base_scenario is None:
            return False
        self._load_scenario(self._base_scenario.model_copy(deep=True))
        return True

    def set_speed(self, speed: float) -> None:
        self.speed = float(min(120.0, max(0.25, speed)))
        self.emit_state()

    def notice(self, text: str, level: str = "info") -> None:
        self.emit(event("notice", {"text": text, "level": level}, t=self.sim.t))

    def _radio_open(self) -> bool:
        """The frequency only works while the clock runs; otherwise pilots could never answer."""
        if self.lifecycle == "running":
            return True
        self.notice("Press Start first. The radio only works while the simulation is running."
                    if self.lifecycle in ("ready", "paused") else "Load a scenario first.", "warn")
        return False

    # ------------------------------------------------------------------ settings

    def set_tower(self, enabled: bool) -> None:
        self.tower_enabled = enabled
        self.emit_state()

    def set_auto_speak(self, enabled: bool) -> None:
        """Manual or Auto. The switch belongs to the controller and works at any moment.

        Manual: Tower proposes, the human says it. Auto: Tower issues every instruction itself,
        by voice one at a time, by data link when the voice cannot keep up, and says its own
        corrections. The human can still key the mic in Auto; Tower waits.
        """
        self.auto_speak = enabled
        if not enabled:
            self._voice_card = None
        self.emit_state()
        if self.scenario is not None:
            self.notice("Auto: Tower issues the instructions. Hold the mic to take over at any time." if enabled
                        else "Manual: Tower proposes, you say it.", "info")

    def set_ptt(self, down: bool) -> None:
        self.human_on_mic = bool(down)

    def set_auto_voice(self, enabled: bool) -> None:
        """Auto with or without Tower's voice. Silent is instant: nothing waits for a radio exchange."""
        self.auto_voice = bool(enabled)
        if not enabled:
            self._voice_card = None
        self.emit_state()

    def _links_only(self) -> bool:
        return not self.auto_voice or self.speed > AUTO_VOICE_MAX_SPEED

    def set_sliders(self, buffer_nm: float | None = None, error_rate: float | None = None,
                    noise: float | None = None) -> None:
        if buffer_nm is not None:
            self.buffer_nm = max(0.0, float(buffer_nm))
        if error_rate is not None:
            self.error_rate = min(1.0, max(0.0, float(error_rate)))
            self.fleet.set_error_rate(self.error_rate)
        if noise is not None:
            self.noise = min(1.0, max(0.0, float(noise)))
        self.emit_state()

    # ------------------------------------------------------------------ the clock

    async def tick(self, dt: float = 1.0) -> None:
        """Advance the world by dt sim seconds. A no-op unless the lifecycle is "running".

        dt may be large when the clock runs fast: the sim still steps at most 1 s at a time so
        separation monitoring and the core's timeouts never skip, and one radar frame is sent
        at the end.
        """
        if self.scenario is None or self.lifecycle != "running":
            return
        async with self._lock:
            remaining = float(dt)
            while remaining > 1e-9:
                step = min(1.0, remaining)
                remaining -= step
                self.sim.step(step)
                now = self.sim.t
                ended = self.sim.pop_ended()
                if ended:
                    self._disruptions_ended(ended)
                self._measure_reaction()
                self.monitor.observe(list(self.sim.active.values()), now)
                states = self.sim.aircraft()
                self._emit_core_events(self.core.tick(now, states))
                for cid, t_match in list(self.matched_at.items()):
                    if now - t_match >= CARD_VERIFY_S and not self.core.conformance.watching(
                            self.core.store.get(cid).callsign if self.core.store.get(cid) else ""):
                        self._set_card_status(cid, "verified")
                        del self.matched_at[cid]
                every = REPLAN_ACTIVE_S if (self.disruptions and self.auto_speak) else REPLAN_EVERY_S
                if now - self.last_replan_t >= every:
                    self._replan("periodic")
            now = self.sim.t
            states = self.sim.aircraft()
            self.emit(event("radar", self.radar_payload(states), t=now))
            if int(now) % 5 == 0 or dt > 1.0:
                self.emit_scoreboard()
            if self.sim.done() and not self.pending:
                self.lifecycle = "ended"
                self.emit_scoreboard()
                self.emit_state()
        # run due pilot responses outside the lock: they do TTS and ASR
        due = [f for (t, f) in self.pending if t <= now]
        self.pending = [(t, f) for (t, f) in self.pending if t > now]
        if self.realtime:
            for f in due:
                asyncio.create_task(f())  # TTS and ASR must not stall the clock
        else:
            for f in due:
                await f()
        if self.auto_speak and self.tower_enabled and self.lifecycle == "running":
            await self._auto_dispatch()

    # ------------------------------------------------------------------ Auto mode

    def _due(self, card: InstructionCard) -> float:
        return self.card_t.get(card.id, self.sim.t) + card.urgency_s

    def _auto_ready(self) -> list[InstructionCard]:
        """Pending cards Auto may issue now, most urgent first: only flights already on frequency."""
        minor_too = self._links_only()  # a small shortcut costs nothing by data link, and a transmission by voice
        return sorted((c for c in self.cards.values()
                       if c.status == "pending" and c.id != self._voice_card and c.callsign in self.sim.active
                       and (minor_too or not c.minor)
                       and not self.sim.active[c.callsign].is_intruder), key=self._due)

    def _auto_links(self) -> None:
        """Send by data link everything Auto will not say. Synchronous, so a reroute is instant.

        Silent Auto, or a clock too fast for speech: everything. With Tower's voice on: every card
        except the one the voice channel is free to take next.
        """
        if not (self.auto_speak and self.tower_enabled and self.lifecycle == "running"):
            return
        ready = self._auto_ready()
        if not self._links_only() and self._voice_card is None and not self.human_on_mic:
            ready = ready[1:]  # the most urgent one is for the voice, on the next tick
        if not self._links_only():
            now = self.sim.t
            ready = [c for i, c in enumerate(ready)
                     if i >= AUTO_VOICE_QUEUE_MAX or self._due(c) - now < (i + 1) * AUTO_EXCHANGE_S]
        for card in ready:
            self._send_by_datalink(card)

    async def _auto_dispatch(self) -> None:
        """Issue pending cards without a human. Runs on every tick of the clock.

        Data link carries whatever the voice will not (`_auto_links`). With Tower's voice on, one
        exchange runs at a time, most urgent first, and the channel stays busy until the readback
        is validated or the wait times out.
        """
        now = self.sim.t
        if self._voice_card is not None:
            vc = self.cards.get(self._voice_card)
            done = vc is None or vc.status in ("validated", "verified", "superseded")
            if done or now - self._voice_since > AUTO_EXCHANGE_TIMEOUT_S:
                self._voice_card = None
        if not self._links_only() and self._voice_card is None and not self.human_on_mic:
            ready = self._auto_ready()
            if ready:
                first = ready[0]
                self._voice_card, self._voice_since = first.id, now
                first.via = "voice"
                if self.realtime:
                    asyncio.create_task(self.speak_card(first.id))  # TTS and ASR must not stall the clock
                else:
                    await self.speak_card(first.id)
        self._auto_links()

    def _planned_route(self, callsign: str) -> tuple[list[tuple[float, float]], str, float, float] | None:
        """(turn points, exit fix, first heading, first leg NM) of the flight's planned path from here.

        None when the path is as good as straight, or the flight has no exit to go direct to.
        """
        a = self.sim.active.get(callsign)
        path = next((p for p in (self.plan.paths if self.plan else []) if p.callsign == callsign), None)
        if a is None or path is None or not a.route:
            return None
        if path.via:  # the planner's own turn point
            turn = (float(path.via[0][0]), float(path.via[0][1]))
        else:
            arr = np.asarray(path.samples, dtype=float).reshape(-1, 4)
            arr = arr[arr[:, 0] >= self.sim.t - 1e-6]
            if arr.shape[0] < 3:
                return None
            end = arr[-1, 1:3]
            u = end - np.array([a.x, a.y])
            length = float(np.hypot(*u)) or 1.0
            off = np.abs((arr[:, 1] - a.x) * u[1] - (arr[:, 2] - a.y) * u[0]) / length
            k = int(np.argmax(off))
            if off[k] < ROUTE_MIN_OFFSET_NM:
                return None
            turn = (float(arr[k, 1]), float(arr[k, 2]))
        hdg = float(np.degrees(np.arctan2(turn[0] - a.x, turn[1] - a.y)) % 360)
        return [turn], a.route[-1], hdg, float(math.hypot(turn[0] - a.x, turn[1] - a.y))

    def _send_by_datalink(self, card: InstructionCard) -> None:
        """Controller-pilot data link: the instruction arrives as text and the crew accepts it.

        Nothing is spoken, so nothing can be misheard, and the aircraft does exactly what the
        card says. Radar verification still watches it like any other clearance.
        """
        from pilots.pilot import items_to_sim_commands

        a = self.sim.active.get(card.callsign)
        if a is None or a.is_intruder:
            return
        now = self.sim.t
        if card.minor:  # applied quietly: no card, no transcript line, nothing to watch
            for cmd in items_to_sim_commands(card.items):
                self.sim.apply(card.callsign, cmd)
            card.via, card.status = "datalink", "validated"
            return
        commands = items_to_sim_commands(card.items)
        watched = list(card.items)
        text = card.phrase
        reroute = self._planned_route(card.callsign) if any(i.type == "heading" for i in card.items) else None
        if reroute is not None:
            # By voice a reroute is a heading now and a "direct" later. By data link it is the planned
            # path itself, so the aircraft flies the line drawn on the map, turn for turn.
            via, exit_name, hdg, leg_nm = reroute
            commands = [c for c in commands if c.kind not in ("heading", "direct")]
            commands.append(SimCommand(kind="route", value=exit_name, via=via))
            watched = [i if i.type != "heading" else i.model_copy(update={"value": int(round(hdg)) % 360 or 360})
                       for i in card.items if i.type != "route"]
            from pilots.readback import say_callsign, say_digits
            rest = [p for p in card.phrase.split(", ")[1:] if "heading" not in p and "direct" not in p]
            text = ", ".join([say_callsign(card.callsign),
                              f"reroute heading {say_digits(f'{int(round(hdg)) % 360:03d}')} for {leg_nm:.0f} miles then direct {exit_name}",
                              *rest])
        c = OpenClearance(id=self.core.store.next_id(), callsign=card.callsign, items=card.items,
                          issued_at=now, card_id=card.id)
        self.core.store.open(c)
        self.core.store.resolve(c.id, "matched")
        self.core.conformance.watch(c, watched, now=now)
        for cmd in commands:
            self.sim.apply(card.callsign, cmd)
        card.via = "datalink"
        self._link_card(card, c.id)
        self._set_card_status(c.id, "validated")
        self.matched_at[c.id] = now
        self.clearance_meta[c.id] = {"issued_real": time.monotonic(), "issued_sim": now, "via": "datalink"}
        self.datalink_sent += 1
        self.emit(event("clearance_opened", c, t=now))
        tx = Transmission(id=f"tx-{uuid.uuid4().hex[:8]}", t_start=now, t_end=now, audio_ref="",
                          text_raw=f"{text}  ·  WILCO", text_norm=text, asr_confidence=1.0,
                          speaker="datalink")
        p = tx.model_dump()
        p["callsign"] = card.callsign
        self.emit(event("transcript", p, t=now))

    # ------------------------------------------------------------------ planning

    def _replan(self, trigger: str, disruption: Disruption | None = None,
                release: set[str] | None = None, why: str = "") -> list[str]:
        """Repair the plan and issue the cards. Returns the callsigns that got a new instruction."""
        if self.plan is None or self.scenario is None:
            return []
        states = self.sim.aircraft()
        prev = self.plan
        self.plan = PL.replan(prev, states, self.scenario.waypoints, self.sim.zones, self.buffer_nm,
                              disruption=disruption, flights=self.scenario.flights, now_t=self.sim.t,
                              time_budget_s=0.5, release=release,
                              frozen_s=(FROZEN_MANUAL_S if not self.auto_speak else
                                        FROZEN_LINK_S if self._links_only() else FROZEN_AUTO_S))
        self.plan.trigger = trigger
        self.last_replan_t = self.sim.t
        new_cards = C.cards_from_plan(self.plan, prev, now_t=self.sim.t, states=states)
        new_cards += C.followup_cards(self.plan, states, self.sim.t)
        if release:
            ended = tuple(f" to clear {name}" for name in release)
            freed = {p.callsign for p in prev.paths if any(c.endswith(ended) for c in p.changes)}
            have = {c.callsign for c in new_cards}
            new_cards += [c for c in C.release_cards(self.plan, states, freed, self.sim.t, why)
                          if c.callsign not in have]
        changed = sorted({c.callsign for c in new_cards})
        self.emit_plan(self.plan, trigger=trigger, changed=changed)
        for card in new_cards:
            self._drop_pending_cards(card.callsign)  # superseded by this one
            self._add_card(card)
        self._auto_links()  # in Auto the reroutes leave now, not on the next tick of the clock
        return changed

    # ------------------------------------------------------------------ disruptions

    def add_disruption(self, kind: str, x_nm: float | None = None, y_nm: float | None = None) -> Disruption | None:
        """Drop a disruption into the world and replan around it.

        `kind` is any key of disruptions.PROFILES, or "random". With no position, or for
        "random", it is put where it will matter: on the path of a flight a few minutes ahead.
        Seeded by the scenario and the count so far, so the same presses give the same result.
        """
        if self.scenario is None:
            self.notice("Load a scenario first.", "warn")
            return None
        try:
            kind = DZ.resolve(kind)
        except ValueError as exc:
            self.notice(str(exc), "warn")
            return None
        if x_nm is not None and y_nm is not None and kind != "random" and not DZ.inside_sector(
                x_nm, y_nm, self.scenario.sector_nm / 2.0, self.frame.shape == "circle", margin_nm=5.0):
            self.notice("That is outside the sector. Click inside the boundary to place it.", "warn")
            return None
        rng = np.random.default_rng([self.scenario.seed, self.disruption_count, 5])
        self.disruption_count += 1
        regular = [a for a in self.sim.aircraft() if not a.is_intruder]
        if kind == "random":
            kind, x_nm, y_nm = DZ.pick_kind(rng, allow_emergency=bool(regular)), None, None
        if kind == "emergency" and not regular:
            self.notice("An emergency needs a flight that is already in the sector. Press Start first.", "warn")
            return None
        d = self._make_disruption(kind, x_nm, y_nm, rng)
        self.disruptions[d.id] = d
        if kind == "emergency":
            self._drop_pending_cards(d.id)
        self.sim.add_disruption(d)
        self.emit(event("disruption", self._disruption_payload(d), t=self.sim.t))
        self.emit_state()
        before = self._unresolved()
        headings = {a.callsign: a.hdg_deg for a in self.sim.aircraft()}
        changed = self._replan(f"{d.label} {d.id}", disruption=d)
        self.rerouted.update(changed)
        self._react = (self.sim.t, {cs: headings[cs] for cs in changed if cs in headings})
        self.reaction_s = None
        self._disruption_notice(d, changed, before)
        if kind == "emergency" and self.lifecycle == "running":
            self._mayday(d)
        return d

    def remove_disruption(self, disruption_id: str) -> None:
        if self.sim.remove_disruption(disruption_id):
            self._disruptions_ended([disruption_id], by_hand=True)

    def _busy_spot(self, rng: np.random.Generator, clear_nm: float = 0.0) -> tuple[float, float, float, float]:
        """(x, y, level, seconds ahead): where some flight will be in four to seven minutes.

        With `clear_nm`, a spot that has nobody within that distance right now is preferred, so
        a random zone lands ahead of the traffic, not on top of it.
        """
        now = self.sim.t
        half = (self.scenario.sector_nm if self.scenario else 200.0) / 2.0
        circle = self.frame.shape == "circle"
        airborne = {a.callsign for a in self.sim.aircraft() if not a.is_intruder}
        paths = [p for p in (self.plan.paths if self.plan else []) if p.samples]
        pool = [p for p in paths if p.callsign in airborne] or \
               [p for p in paths if now <= p.samples[0][0] <= now + 600] or paths
        now_at = [(a.x_nm, a.y_nm) for a in self.sim.aircraft() if not a.is_intruder]
        best: tuple[float, tuple[float, float, float, float]] | None = None
        for _ in range(16):
            if not pool:
                break
            p = pool[int(rng.integers(len(pool)))]
            lead = float(rng.uniform(240, 420))
            arr = np.asarray(p.samples, dtype=float)
            t = max(now, float(arr[0, 0])) + lead
            k = int(np.argmin(np.abs(arr[:, 0] - t)))
            x, y = float(arr[k, 1]), float(arr[k, 2])
            if not DZ.inside_sector(x, y, half * (0.8 if clear_nm <= 0 else 0.6), circle):
                continue  # a zone goes mid-sector: on an exit gate nobody can route around it
            room = min((math.hypot(x - ax, y - ay) for ax, ay in now_at), default=999.0)
            spot = (x, y, float(arr[k, 3]), max(60.0, float(arr[k, 0]) - now))
            if room >= clear_nm:
                return spot
            if best is None or room > best[0]:
                best = (room, spot)
        if best is not None:
            return best[1]
        return float(rng.uniform(-0.3, 0.3) * half), float(rng.uniform(-0.3, 0.3) * half), 33000.0, 300.0

    def _level_near(self, x: float, y: float, default: float = 33000.0) -> float:
        regular = [a for a in self.sim.aircraft() if not a.is_intruder]
        if not regular:
            return default
        return min(regular, key=lambda a: (a.x_nm - x) ** 2 + (a.y_nm - y) ** 2).target_alt_ft

    def _make_disruption(self, kind: str, x_nm: float | None, y_nm: float | None,
                         rng: np.random.Generator) -> Disruption:
        prof = DZ.PROFILES[kind]
        now = self.sim.t
        half = (self.scenario.sector_nm if self.scenario else 200.0) / 2.0
        circle = self.frame.shape == "circle"
        placed = x_nm is not None and y_nm is not None
        # A random zone is dropped ahead of the traffic with room to react, never on top of a plane.
        tx, ty, level, lead = self._busy_spot(rng, clear_nm=prof.radius_nm[1] + 12.0 if prof.shape == "circle" else 0.0)
        expires = now + DZ.uniform(rng, prof.duration_s) if prof.duration_s else None
        n = self.disruption_count

        if kind == "emergency":
            regular = [a for a in self.sim.aircraft() if not a.is_intruder]
            def company(a: AircraftState) -> int:
                return sum(1 for b in regular if b is not a and math.hypot(a.x_nm - b.x_nm, a.y_nm - b.y_nm) < 50)
            inside = [a for a in regular if DZ.inside_sector(a.x_nm, a.y_nm, half * 0.75, circle)] or regular
            busiest = sorted(inside, key=company, reverse=True)[:3]  # one of the three with most traffic around
            a = (min(regular, key=lambda a: (a.x_nm - x_nm) ** 2 + (a.y_nm - y_nm) ** 2) if placed
                 else busiest[int(rng.integers(len(busiest)))])
            # Divert toward the nearest edge it can reach without turning right round.
            options = [(a.hdg_deg + off) % 360 for off in range(-100, 101, 25)]
            hdg = min(options, key=lambda h: DZ.edge_distance(a.x_nm, a.y_nm, h, half, circle))
            d = Disruption(id=a.callsign, kind="emergency", shape="point", label=prof.label, x_nm=a.x_nm, y_nm=a.y_nm,
                           hdg_deg=hdg, gs_kt=a.gs_kt, alt_ft=a.alt_ft, target_alt_ft=10000.0, t_start=now)
        elif prof.shape == "point":
            gs = DZ.uniform(rng, prof.gs_kt)
            alt = DZ.round_level(level if not placed else self._level_near(x_nm, y_nm, level))
            if kind == "balloon":
                hdg = float(rng.uniform(60, 120))  # with the westerlies
                x, y = (x_nm, y_nm) if placed else (tx - math.sin(math.radians(hdg)) * gs * lead / 3600,
                                                    ty - math.cos(math.radians(hdg)) * gs * lead / 3600)
            elif placed:
                x, y = float(x_nm), float(y_nm)
                hdg = self._aim(x, y, gs)
            else:
                # Arrive where the chosen flight will be, when it will be there. Do not appear on
                # top of anyone: of a few approach directions, take the one with the most room.
                run = gs * lead / 3600.0
                now_at = [(a.x_nm, a.y_nm) for a in self.sim.aircraft() if not a.is_intruder]
                best = None
                for _ in range(10):
                    h = float(rng.uniform(0, 360))
                    r = math.radians(h)
                    cx, cy = tx - math.sin(r) * run, ty - math.cos(r) * run
                    if not DZ.inside_sector(cx, cy, half, circle, margin_nm=10.0):
                        continue
                    room = min((math.hypot(cx - ax, cy - ay) for ax, ay in now_at), default=999.0)
                    if best is None or room > best[0]:
                        best = (room, cx, cy, h)
                    if room > prof.base_nm + 12.0:
                        break
                if best is None:
                    h = float(rng.uniform(0, 360))
                    best = (0.0, tx - math.sin(math.radians(h)) * run, ty - math.cos(math.radians(h)) * run, h)
                _, x, y, hdg = best
            d = Disruption(id=f"{prof.prefix}{n}", kind=kind, shape="point", label=prof.label, x_nm=x, y_nm=y,
                           hdg_deg=hdg, gs_kt=gs, alt_ft=alt, t_start=now, expires_t=expires)
        else:
            x, y = (float(x_nm), float(y_nm)) if placed else (tx, ty)
            r = DZ.uniform(rng, prof.radius_nm)
            floor, ceiling = 0.0, DZ.ALL_LEVELS_FT
            if prof.band_ft is not None:
                lvl = DZ.round_level(self._level_near(x, y, level))
                floor, ceiling = lvl - prof.band_ft[0], lvl + prof.band_ft[1]
            drift = DZ.uniform(rng, prof.drift_kt)
            d = Disruption(id=f"{prof.prefix}{n}", kind=kind, shape="circle", label=prof.label, x_nm=x, y_nm=y,
                           radius_nm=r, hdg_deg=float(rng.uniform(40, 130)) if drift else None, gs_kt=drift or None,
                           floor_ft=floor, ceiling_ft=ceiling, swell_nm_per_min=prof.swell_nm_per_min,
                           max_radius_nm=r + 8.0 if prof.swell_nm_per_min else None, t_start=now, expires_t=expires)
        if d.shape == "point" and d.hdg_deg is not None:
            pred = PL.predict_intruder(d.x_nm, d.y_nm, d.hdg_deg, d.gs_kt or 0.0, d.alt_ft or 30000.0, now)
            if expires is not None:
                pred = pred[pred[:, 0] <= expires]
            d.predicted_path = [(float(r[0]), float(r[1]), float(r[2])) for r in pred[::6]]
        return d

    def _aim(self, x: float, y: float, gs_kt: float) -> float:
        """Heading from (x, y) that meets the nearest flight, or the sector centre if there is none."""
        regular = [a for a in self.sim.aircraft() if not a.is_intruder]
        if not regular or gs_kt <= 0:
            return float(np.degrees(np.arctan2(-x, -y)) % 360)
        a = min(regular, key=lambda a: (a.x_nm - x) ** 2 + (a.y_nm - y) ** 2)
        lead_s = math.hypot(a.x_nm - x, a.y_nm - y) / gs_kt * 3600.0
        r = math.radians(a.hdg_deg)
        px = a.x_nm + math.sin(r) * a.gs_kt * lead_s / 3600.0
        py = a.y_nm + math.cos(r) * a.gs_kt * lead_s / 3600.0
        return float(np.degrees(np.arctan2(px - x, py - y)) % 360)

    def _measure_reaction(self) -> None:
        """Live proof of the two things that matter: how fast the traffic turns, and that it stays out."""
        if self._react is not None and self.reaction_s is None:
            t0, headings = self._react
            for cs, h0 in headings.items():
                a = self.sim.active.get(cs)
                if a is not None and abs((a.hdg - h0 + 180) % 360 - 180) >= 2.0:
                    self.reaction_s = round(self.sim.t - t0, 1)
                    break
        inside = 0
        for z in self.sim.zones:
            for a in self.sim.active.values():
                if (not a.is_intruder and z.floor_ft - 1000 < a.alt < z.ceiling_ft + 1000
                        and (a.x - z.x_nm) ** 2 + (a.y - z.y_nm) ** 2 < z.radius_nm ** 2):
                    inside += 1
                    self.zone_incursions.add((z.id, a.callsign))
        self.in_zone_now = inside

    def _unresolved(self) -> set[str]:
        return {p.callsign for p in (self.plan.paths if self.plan else [])
                if any(c.startswith("unresolved") for c in p.changes)}

    def _disruption_notice(self, d: Disruption, changed: list[str], before: set[str]) -> None:
        if d.shape == "circle":
            levels = ("every level" if d.ceiling_ft >= DZ.ALL_LEVELS_FT
                      else f"FL{d.floor_ft / 100:03.0f} to FL{d.ceiling_ft / 100:03.0f}")
            what = f"{d.label} {d.id}, {d.radius_nm:.0f} NM across {levels}"
        elif d.kind == "emergency":
            what = f"{d.id} has declared an emergency and is descending"
        elif d.kind == "unknown":
            what = f"{d.label} {d.id}, no height, {d.gs_kt or 0:.0f} kt"
        else:
            what = f"{d.label} {d.id} at FL{(d.alt_ft or 0) / 100:03.0f}, {d.gs_kt or 0:.0f} kt"
        n = len(changed)
        moved = "No flight needs to move." if n == 0 else f"{n} flight{'s' if n != 1 else ''} rerouted."
        paths = self.plan.paths if self.plan else []
        paths = [p for p in paths if p.callsign not in before]  # only what this disruption caused
        clipped = [p.callsign for p in paths if any(c.startswith("unresolved: crosses") for c in p.changes)]
        stuck = [p.callsign for p in paths if any(c.startswith("unresolved conflict") for c in p.changes)]
        text = f"{what}. {moved}"
        if clipped:
            text += f" Too close to avoid it: {_some(clipped)} will take the shortest way through."
        if stuck:
            text += f" No conflict-free route yet for {_some(stuck)}: the emergency layer turns it if it gets close."
        self.notice(text, "warn" if (n or clipped or stuck) else "info")

    def _disruptions_ended(self, ids: list[str], by_hand: bool = False) -> None:
        """Expired, flown out of the sector, or removed. Flights that went around them go back."""
        gone = [self.disruptions.pop(i) for i in ids if i in self.disruptions]
        if not gone:
            return
        for d in gone:
            d.active = False
            self.emit(event("disruption", self._disruption_payload(d), t=self.sim.t))
        self.emit_state()
        names = ", ".join(f"{d.label} {d.id}" if d.kind != "emergency" else d.id for d in gone)
        why = f"{names} is no longer a factor: resume direct routing."
        changed = self._replan(f"{gone[0].id} cleared", release={d.id for d in gone}, why=why)
        verb = "removed" if by_hand else ("has left the sector" if gone[0].shape == "point" and gone[0].expires_t is None
                                          else "has cleared")
        back = f" {len(changed)} flight{'s' if len(changed) != 1 else ''} planned again without it." if changed else ""
        self.notice(f"{names} {verb}.{back}", "info")

    def _mayday(self, d: Disruption) -> None:
        """The emergency aircraft says so on frequency, in its own voice, and Tower hears it."""
        from pilots.readback import say_callsign, say_feet

        text = (f"mayday mayday mayday {say_callsign(d.id)} engine failure "
                f"descending {say_feet(d.target_alt_ft or 10000)} heading {' '.join(f'{int(d.hdg_deg or 0):03d}')}")

        async def go() -> None:
            resp = await asyncio.to_thread(self.fleet.get(d.id).announce, text, self.noise)
            tx = await self._hear_pilot(resp)
            self.transmissions += 1
            self.emit(event("transcript", self._tx_payload(tx), t=self.sim.t))
        self.pending.append((self.sim.t + 1.0, go))

    # ------------------------------------------------------------------ radio: controller side

    def _new_tx(self, text_raw: str, speaker: str, audio_ref: str = "", conf: float = 1.0,
                n_best: list[str] | None = None, text_stock: str | None = None,
                duration_s: float = 3.0) -> Transmission:
        wps = self.spoken_waypoints()
        norm0 = normalize(dataset_normalize(text_raw))
        pref = _route_of(self, norm0)
        norm = snap_waypoints(norm0, wps, pref)
        return Transmission(id=f"tx-{uuid.uuid4().hex[:8]}", t_start=self.sim.t - duration_s,
                            t_end=self.sim.t, audio_ref=audio_ref, text_raw=text_raw,
                            text_norm=norm, asr_confidence=conf, speaker=speaker,
                            n_best=[snap_waypoints(normalize(h), wps, pref) for h in (n_best or [])],
                            text_stock=text_stock)  # type: ignore[arg-type]

    async def _transcribe(self, samples: np.ndarray) -> tuple[str, float, list[str], str | None, float]:
        if self.asr is None:
            self.asr = await asyncio.to_thread(get_asr)
        prompt = build_prompt([_spoken(cs) for cs in self.sim.active], self.spoken_waypoints())
        t0 = time.perf_counter()
        r = await asyncio.to_thread(self.asr.transcribe, samples, prompt)
        return r.text, r.confidence, list(r.n_best), r.text_stock, time.perf_counter() - t0

    async def controller_audio(self, samples: np.ndarray, sr: int = 16000) -> None:
        """A controller utterance from the mic: transcribe, then treat as controller text."""
        if not self._radio_open():
            return
        ref = f"ctl-{uuid.uuid4().hex[:8]}.wav"
        float_to_wav(AUDIO_DIR / ref, samples, sr)
        text, conf, n_best, stock, lat = await self._transcribe(samples)
        await self._controller(text, audio_ref=ref, conf=conf, n_best=n_best, text_stock=stock,
                               duration_s=len(samples) / sr, asr_latency=lat)

    async def controller_text(self, text: str) -> None:
        if not self._radio_open():
            return
        await self._controller(text)

    async def _controller(self, text: str, *, audio_ref: str = "", conf: float = 1.0,
                          n_best: list[str] | None = None, text_stock: str | None = None,
                          duration_s: float = 3.0, asr_latency: float = 0.0,
                          card: InstructionCard | None = None) -> None:
        if not text.strip():
            return
        tx = self._new_tx(text, "controller", audio_ref, conf, n_best, text_stock, duration_s)
        t0 = time.perf_counter()
        async with self._lock:
            states = self.sim.aircraft()
            events = self.core.on_transmission(tx, list(self.sim.active), states)
        self.transmissions += 1
        self.tier1_latencies.append(asr_latency + time.perf_counter() - t0)
        self.emit(event("transcript", self._tx_payload(tx), t=self.sim.t))
        if card is not None and self._trust_the_card(card, events):
            conf = min(conf, 0.5)
        self._emit_core_events(events)
        opened = [OpenClearance.model_validate(e["payload"]) for e in events if e["type"] == "clearance_opened"]
        if card is not None and not opened:
            # Tower's own ears missed part of the card. The pilot still heard the real instruction,
            # so open the clearance from the card and flag the transcript as low confidence.
            c = OpenClearance(id=self.core.store.next_id(), callsign=card.callsign, items=card.items,
                              issued_at=self.sim.t, card_id=card.id, source_transmission_id=tx.id)
            async with self._lock:
                self.core.store.open(c)
            self.emit(event("clearance_opened", c, t=self.sim.t))
            opened = [c]
            conf = min(conf, 0.5)
        for c in opened:
            self.clearance_meta[c.id] = {"issued_real": time.monotonic(), "issued_sim": self.sim.t}
            if card is None:
                card = self._match_card(c)
            if card is not None:
                card.via = card.via or "human"
                self._link_card(card, c.id)
            self._schedule_pilot(c, heard_ok=(conf >= 0.5))

    def _trust_the_card(self, card: InstructionCard, events: list[dict[str, Any]]) -> bool:
        """Tower spoke this card itself, so the card is what was said, whatever its own ears heard.

        Whisper turns a made-up fix into another word ("GEGOR" -> "jigor"), and the clearance then
        expects a fix nobody was given: the pilot's correct readback alerts and the correction asks
        for the wrong fix. A human on the mic gets no such benefit of the doubt: they may have misspoken.
        Returns True if what Tower heard had to be overruled.
        """
        overruled = False
        for e in events:
            if e["type"] != "clearance_opened":
                continue
            heard = OpenClearance.model_validate(e["payload"])
            if heard.callsign != card.callsign:
                continue
            if [(i.type, i.value) for i in heard.items] == [(i.type, i.value) for i in card.items]:
                continue
            stored = self.core.store.get(heard.id)
            if stored is None:
                continue
            log.warning("Tower misheard its own card for %s: heard %s, said %s", card.callsign,
                        [i.value for i in heard.items], [i.value for i in card.items])
            stored.items = [it.model_copy() for it in card.items]
            e["payload"] = stored
            overruled = True
        return overruled

    def _tx_payload(self, tx: Transmission) -> dict[str, Any]:
        """Transmission plus the callsign the parser attached, for the transcript column."""
        p = tx.model_dump()
        ext = self.core.last_extraction
        p["callsign"] = ext.callsign if ext and ext.transmission_id == tx.id else None
        return p

    def _match_card(self, c: OpenClearance) -> InstructionCard | None:
        for card in self.cards.values():
            if card.callsign == c.callsign and card.status == "pending":
                return card
        return None

    async def speak_card(self, card_id: str) -> None:
        """Tower speaks the card itself (auto-speak) through TTS and its own ears."""
        card = self.cards.get(card_id)
        if card is None or not self._radio_open():
            return
        if card.callsign not in self.sim.active:
            self.notice(f"{card.callsign} is not in the sector yet. Its instruction waits until it checks in.", "info")
            return
        if self.tts is None or self.asr is None and not self.synthesize:
            await self._controller(card.phrase, card=card)
            return
        try:
            path = await asyncio.to_thread(self.tts.synthesize, card.phrase, self.tts.controller_voice())
            ref = f"ctl-{uuid.uuid4().hex[:8]}.wav"
            await asyncio.to_thread(apply_to_file, path, AUDIO_DIR / ref, max(0.05, self.noise * 0.5))
            samples, sr = read_wav(AUDIO_DIR / ref)
            text, conf, n_best, stock, lat = await self._transcribe(samples)
            await self._controller(text, audio_ref=ref, conf=conf, n_best=n_best, text_stock=stock,
                                   duration_s=len(samples) / sr, asr_latency=lat, card=card)
        except Exception as exc:  # TTS or ASR failure must never stall the demo
            log.warning("speak_card fell back to text: %s", exc)
            await self._controller(card.phrase, card=card)

    # ------------------------------------------------------------------ radio: pilot side

    def _schedule_pilot(self, c: OpenClearance, heard_ok: bool = True) -> None:
        async def go() -> None:
            await self._pilot_responds(c, heard_ok)
        self.pending.append((self.sim.t + PILOT_DELAY_S, go))

    async def _pilot_responds(self, c: OpenClearance, heard_ok: bool = True,
                              correction: bool = False) -> None:
        if c.callsign not in self.sim.active and not correction:
            return
        pilot = self.fleet.get(c.callsign)
        kw: dict[str, Any] = {"noise_level": self.noise}
        if correction:
            resp: PilotResponse = await asyncio.to_thread(pilot.respond_to_correction, c, self.noise)
        else:
            resp = await asyncio.to_thread(pilot.respond, c, heard_ok, list(self.sim.active), **kw)
        meta = self.clearance_meta.setdefault(c.id, {})
        if resp.injected_error:
            meta["injected_error"] = resp.injected_error
            self.errors_injected += 1
        # The plane obeys what the pilot said, whoever the pilot was.
        actor = resp.acting_callsign
        for cmd in (resp.sim_commands or [resp.sim_command]):
            if cmd.kind != "none" and actor in self.sim.active:
                self.sim.apply(actor, cmd)
        if not resp.transmits:
            return  # missing readback: the timeout in core.tick will raise it
        # Tower hears the pilot through the radio, never reads the text.
        tx = await self._hear_pilot(resp)
        t0 = time.perf_counter()
        async with self._lock:
            events = self.core.on_transmission(tx, list(self.sim.active), self.sim.aircraft())
        self.transmissions += 1
        self.tier1_latencies.append(time.perf_counter() - t0)
        self.emit(event("transcript", self._tx_payload(tx), t=self.sim.t))
        if resp.kind == "say_again":
            return
        self._emit_core_events(events)
        alert = next((e for e in events if e["type"] == "alert"), None)
        if alert and self.tower_enabled and self.auto_speak:
            phrase = alert["payload"].get("correction_phrase")
            if phrase and not correction:
                await self._auto_correct(c, phrase)
            elif phrase:
                # One correction, then the human decides. Correcting a correction can loop for ever.
                self.notice(f"{c.callsign} still reads back wrong after Tower's correction. It is yours to sort out.",
                            "warn")

    async def _hear_pilot(self, resp: PilotResponse) -> Transmission:
        ref = ""
        if resp.audio_path and Path(resp.audio_path).exists():
            ref = Path(resp.audio_path).name
            dst = AUDIO_DIR / ref
            if not dst.exists():
                dst.write_bytes(Path(resp.audio_path).read_bytes())
            try:
                samples, sr = read_wav(resp.audio_path)
                text, conf, n_best, stock, lat = await self._transcribe(samples)
                self.tier1_latencies.append(lat)
                return self._new_tx(text, "pilot", ref, conf, n_best, stock, len(samples) / sr)
            except Exception as exc:
                log.warning("pilot ASR failed, using text: %s", exc)
        return self._new_tx(resp.text or "", "pilot", ref, 1.0, [], None, resp.audio_duration_s or 3.0)

    async def _auto_correct(self, c: OpenClearance, phrase: str) -> None:
        """Tower itself says the correction on frequency and the pilot reads back correctly."""
        tx = self._new_tx(phrase, "controller")
        self.emit(event("transcript", tx, t=self.sim.t))
        fixed = OpenClearance(id=f"{c.id}-fix", callsign=c.callsign, items=c.items, issued_at=self.sim.t,
                              status="open", card_id=c.card_id)
        self.core.store.open(fixed)
        self.emit(event("clearance_opened", fixed, t=self.sim.t))
        self.card_by_clearance[fixed.id] = self.card_by_clearance.get(c.id, "")
        await self._pilot_responds(fixed, True, correction=True)

    # ------------------------------------------------------------------ world builder agent

    async def agent_audio(self, samples: np.ndarray) -> str:
        text, *_ = await self._transcribe(samples)
        return await self.agent_request(text)

    async def agent_request(self, text: str) -> str:
        from world_agent import handle  # local import: keeps the LLM optional
        reply, actions = await handle(self, text)
        self.emit(event("agent_reply", {"text": reply, "actions": actions}, t=self.sim.t))
        return reply

    # tools the agent can call --------------------------------------------------------------

    def tool_load_scenario(self, name: str) -> str:
        if name not in SC.list_scenarios():
            return f"unknown scenario {name}; have {SC.list_scenarios()}"
        self.load(name)
        return f"loaded {name} with {len(self.scenario.flights)} flights"  # type: ignore[union-attr]

    def tool_multiply_traffic(self, factor: float) -> str:
        if self.scenario is None:
            return "no scenario loaded"
        self._load_scenario(SC.multiply(self.scenario, factor))
        return f"traffic x{factor}: {len(self.scenario.flights)} flights"

    def tool_spawn_flight(self, airline: str = "ACA", from_side: str = "east", alt_ft: float = 33000,
                          callsign: str | None = None) -> str:
        if self.scenario is None:
            return "no scenario loaded"
        routes = SC.routes_of(self.scenario)
        wps = {w.name: w for w in self.scenario.waypoints}
        side = from_side.lower()
        def score(r: list[str]) -> float:
            w = wps[r[0]]
            return {"east": w.x_nm, "west": -w.x_nm, "north": w.y_nm, "south": -w.y_nm}.get(side, 0.0)
        route = max(routes.values(), key=score) if routes else [self.scenario.waypoints[0].name]
        cs = callsign or f"{airline.upper()}{self.sim.rng.integers(100, 999)}"
        spec = FlightSpec(callsign=cs, route=list(route), alt_ft=alt_ft, entry_time_s=self.sim.t)
        self.scenario.flights.append(spec)
        self.sim.pending.append(spec)
        self.sim.step(0.0)
        self._replan(f"{cs} added")
        return f"spawned {cs} from the {side} at {int(alt_ft)} ft on {' '.join(route)}"

    def tool_add_disruption(self, kind: str, x_nm: float | None = None, y_nm: float | None = None) -> str:
        d = self.add_disruption(kind, x_nm, y_nm)
        if d is None:
            return f"could not add {kind}"
        return f"added {d.label} {d.id} at ({d.x_nm:.0f}, {d.y_nm:.0f})"

    def tool_set(self, tower: bool | None = None, auto_speak: bool | None = None,
                 error_rate: float | None = None, noise: float | None = None,
                 buffer_nm: float | None = None) -> str:
        if tower is not None:
            self.set_tower(tower)
        if auto_speak is not None:
            self.set_auto_speak(auto_speak)
        self.set_sliders(buffer_nm, error_rate, noise)
        return (f"tower={'on' if self.tower_enabled else 'off'} auto_speak={self.auto_speak} "
                f"error_rate={self.error_rate} noise={self.noise} buffer={self.buffer_nm}")

    def tool_describe(self) -> str:
        if self.scenario is None:
            return "No scenario loaded. Scenarios: " + ", ".join(SC.list_scenarios())
        ac = self.sim.aircraft()
        ca = closest_approach(self.plan.paths) if self.plan else []
        nearest = min(ca, key=lambda r: r[2]) if ca else None
        s = self.scoreboard()
        return (f"{self.scenario.name}: t={self.sim.t:.0f}s, {len(ac)} aircraft airborne "
                f"({', '.join(a.callsign for a in ac)}), {len([c for c in self.cards.values() if c.status=='pending'])} "
                f"pending instructions, miles saved {s.miles_saved}, losses of separation {s.losses_of_separation}"
                + (f", closest planned pair {nearest[0]}/{nearest[1]} at {nearest[2]:.1f} NM" if nearest else ""))


_DIRECT_RE = re.compile(r"\b(direct(?:\s+to)?)\s+((?:[a-z]+\s?){1,3})")


def snap_waypoints(text_norm: str, waypoints: list[str], preferred: list[str] | None = None) -> str:
    """Stock Whisper never gets made-up fix names right ("ESTIR" -> "at better").

    Replace the lowercase words after "direct" with the closest known waypoint. Waypoints on the
    addressed aircraft's own route are preferred with a lower bar, the way a controller would
    assume. Deterministic, so it lives in tier 1. See docs/02-domain.md, waypoints.
    """
    if not waypoints or "direct" not in text_norm:
        return text_norm
    from rapidfuzz import fuzz
    names = [w.upper() for w in waypoints]
    pref = [w.upper() for w in (preferred or []) if w.upper() in names]

    def score(heard: str, name: str) -> float:
        cands = [heard.replace(" ", "").upper()] + [w.upper() for w in heard.split()]
        return max(fuzz.ratio(c, name) for c in cands)

    def fix(m: "re.Match[str]") -> str:
        heard = m.group(2).strip()
        for pool, bar in ((pref, 30.0), (names, 60.0)):
            if not pool:
                continue
            ranked = sorted(((score(heard, n), n) for n in pool), reverse=True)
            best, runner = ranked[0], (ranked[1] if len(ranked) > 1 else (0.0, ""))
            if best[0] >= bar and (best[0] - runner[0] >= 5 or len(pool) == 1):
                tail = " " if m.group(2).endswith(" ") else ""
                return f"{m.group(1)} {best[1]}{tail}"
        return m.group(0)

    return _DIRECT_RE.sub(fix, text_norm)


def _some(names: list[str], limit: int = 4) -> str:
    return ", ".join(names[:limit]) + (f" and {len(names) - limit} more" if len(names) > limit else "")


def _route_of(world: "World", text_norm: str) -> list[str]:
    """Remaining route of the aircraft a transmission addresses, for the waypoint prior."""
    from tower.callsign import snap
    active = list(world.sim.active)
    if not active:
        return []
    s = snap(text_norm, active)
    a = world.sim.get(s.best) if s.best else None
    if a is None:
        return []
    sayable = set(world.spoken_waypoints())
    return [n for n in a.route if n in sayable]


def _spoken(callsign: str) -> str:
    """ACA123 -> 'air canada one two three' for the ASR prompt."""
    import re
    m = re.match(r"([A-Z]+)(\d+.*)", callsign)
    if not m:
        return callsign.lower()
    name = ICAO_TO_TELEPHONY.get(m.group(1), m.group(1)).lower()
    digits = " ".join("zero one two three four five six seven eight nine".split()[int(d)] if d.isdigit() else d.lower()
                      for d in m.group(2))
    return f"{name} {digits}"
