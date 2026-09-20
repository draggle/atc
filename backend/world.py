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
import threading
import time
import uuid
from collections import deque
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
RISK = importlib.import_module("planner.risk")
from planner.conflicts import closest_approach
from schemas import (
    AircraftState,
    Disruption,
    FlightSpec,
    GeoFrame,
    InstructionCard,
    Item,
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
from tower.memory import Memory, memory_from_env
from tower.normalize import ICAO_TO_TELEPHONY, normalize
from tower.pipeline import TowerCore
from tower.voice import speak_reply

log = logging.getLogger("tower.world")

DATA_DIR = Path(os.environ.get("TOWER_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
AUDIO_DIR = DATA_DIR / "audio"
PILOT_DELAY_S = 1.5  # sim seconds between a clearance and the pilot keying up (headless runs and tests)
PILOT_KEY_UP_S = 0.15  # real seconds, with a screen attached: the wait that is felt, so it is short
MIN_MIC_S = 0.5  # shorter than this from the mic is a stray key press
GUESS_MIN_CONF = 0.6  # below this, an instruction only the language model could find is noise
REPLAN_EVERY_S = 60.0
# Auto mode. One voice exchange at a time; whatever the voice cannot get to in time goes by data link.
AUTO_VOICE_MAX_SPEED = 1.5  # faster than this and speech, which takes real seconds, cannot keep up
AUTO_VOICE_QUEUE_MAX = 0  # cards allowed to wait for the voice channel. None: reaction comes first,
#                           so one aircraft is talked round and every other reroute goes out at once
# How far ahead a new path may start. The aircraft cannot change what it does before the instruction
# reaches it: a minute for a human to say it and hear it back, seconds for Tower in Auto.
FROZEN_MANUAL_S = 25.0  # voice on: long enough to say a card and hear it back, no longer
FROZEN_AUTO_S = 10.0  # Auto with Tower's voice on
FROZEN_LINK_S = 0.0  # Auto, silent: the reroute is on the flight deck in the same second
REPLAN_ACTIVE_S = 15.0  # how often every path is re-checked while a disruption is in the sector
INTERPRET_TIMEOUT_S = 6.0  # the interpreter agent gets this long; after that the controller is told to say it again
UNSURE_CONF = 0.6  # below this Tower doubts its own ears, and a clash with the card is settled in the card's favour
TURN_BACK_RETRY_S = 5.0  # a flight at its expected turn-back point is looked at this often until it can go direct
ROUTE_MIN_OFFSET_NM = 1.0  # a planned path this close to a straight line is just "direct"
AUTO_EXCHANGE_S = 12.0  # a spoken instruction and its readback, roughly
AUTO_EXCHANGE_TIMEOUT_S = 30.0  # stop waiting for a readback that never came
CARD_VERIFY_S = 30.0  # a matched clearance with no radar alert for this long is "verified"
# Monte Carlo conflict prediction (TRD 07). Thresholds live in planner/risk.py.
RISK_REPLAN_EVERY_S = 20.0  # one risk replan per pair per this long
RISK_FAST_CADENCE_S = 2.0  # above 1x the prediction runs every this many sim seconds, not every step
RISK_EMIT_EVERY_S = 1.0  # the risk event goes out at most this often
RISK_N_MAX = 256
RISK_N_MIN = 32
# --- squack agent (backend/agent/, docs/trd/08-squack-agent-prd.md) ---
EVENT_RING = 500  # emitted events kept in World.events for query.timeline
RING_SKIP = {"radar", "scoreboard", "stats", "risk"}  # streams: nothing a timeline would show
EVENT_ANSWER_GAP_S = 8.0  # squack talks unprompted at most this often

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
                 realtime: bool = True, memory: Memory | None = None) -> None:
        # Every event passes through the searchable memory (Elasticsearch when configured) on its
        # way out, so the resolver can search what the screen has seen. See tower/memory.py.
        self.memory: Memory = memory if memory is not None else memory_from_env()
        self._emit_out = emit
        self.emit = self._emit
        self.synthesize = synthesize
        self.realtime = realtime
        self.asr: ASR | None = asr
        self.tts = TTS() if synthesize else None
        self.scenario: Scenario | None = None
        self.sim = Simulator()
        self.core = TowerCore(memory=self.memory)
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
        self._turn_back_t: dict[str, float] = {}  # callsign -> when its turn back was last looked at
        self._tasks: set[asyncio.Task[Any]] = set()  # pilot replies in flight: kept so they are not collected
        self._undo: dict[str, dict[str, Any]] = {}  # callsign -> what it was cleared to do before the last instruction
        self._voice_since = 0.0
        self.human_on_mic = False  # the controller is holding the mic: Tower keeps quiet
        self.auto_voice = False  # Auto speaks one exchange at a time. Off: every instruction by data link
        self.speak_replies = os.environ.get("SQUACK_SPEAK", "1") != "0"  # squack says its answers on frequency
        self.next_readback = "random"  # or correct, wrong_value, wrong_aircraft, omitted_item, missing_readback
        self.held: dict[str, tuple[OpenClearance, str | None]] = {}  # said-vs-card conflicts awaiting "send as heard"
        self.alerted: dict[str, tuple[str, float]] = {}  # callsign -> (clearance id of the wrong readback, sim time)
        self.correcting: dict[str, str] = {}  # correction clearance id -> the clearance it corrects
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
        self._speaking: set[str] = set()  # cards Tower is saying right now, see speak_card
        # Monte Carlo conflict prediction (TRD 07). risk_predict is an attribute so tests and the
        # integrator can inject a stub while planner/risk.py is being written.
        self.risk_predict: Callable[..., Any] = RISK.predict
        self.risk: Any = RISK.RiskReport()
        self._risk_n = RISK_N_MAX  # rollouts per call, adapted to the measured cost
        self._risk_seen: dict[frozenset[str], dict[str, Any]] = {}  # pair -> over, replanned_t, los_at
        self._risk_t = -1e9  # sim time of the last prediction
        self._risk_emit_t = -1e9  # sim time of the last risk event
        self._risk_prev_empty = True  # the previous report had no pairs: nothing to clear on screen
        self.conflicts_predicted = 0
        self.conflicts_resolved = 0
        # --- squack agent (backend/agent/): ui mode, event ring buffer, wake policy, the agent itself ---
        from agent.wake import WakePolicy
        self.ui_mode: str = "normal"  # "normal" | "agent": set_ui_mode
        self.events: deque[dict[str, Any]] = deque(maxlen=EVENT_RING)  # the last emitted events, minus streams
        self.wake = WakePolicy()
        self._agent: Any = None  # SquackAgent, built on first use so the LLM stays optional
        self._loop: asyncio.AbstractEventLoop | None = None  # for emits from the agent's thread
        self._loop_thread: threading.Thread | None = None
        self._agent_busy = False  # a user turn is in flight: event answers wait
        self._event_backlog: list[dict[str, Any]] = []  # wake batches not yet spoken about
        self._event_answer_t = -1e18  # real time of the last unprompted answer
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
        self.memory.new_session(sc.name)
        self.core = TowerCore(waypoints={w.name: (w.x_nm, w.y_nm) for w in sc.waypoints if w.kind != "hidden"},
                              memory=self.memory)
        if self.memory.enabled:
            self.memory.index_waypoints(self._with_latlon([w.model_dump() for w in sc.waypoints
                                                           if w.kind != "hidden"]))
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
        self._turn_back_t.clear(); self._undo.clear()
        self.held.clear(); self.alerted.clear(); self.correcting.clear()
        self.next_readback = "random"
        self.rerouted, self.reaction_s, self._react, self.in_zone_now = set(), None, None, 0
        self.zone_incursions = set()
        self.disruptions.clear()
        self.disruption_count = 0
        self._risk_seen.clear()
        self._risk_t = self._risk_emit_t = -1e9
        self._risk_prev_empty = True
        self.conflicts_predicted = self.conflicts_resolved = 0
        self.buffer_nm = sc.separation_buffer_nm
        self.noise = sc.noise_level
        self.baseline = baseline
        self.plan = plan
        # Planned savings are frozen at the initial plan: after a replan the plan holds remaining
        # distance while the baseline holds full routes, so a live difference would be wrong.
        self.planned_miles_saved = self.baseline.total_distance_nm - self.plan.total_distance_nm
        self.planned_time_saved_s = self.baseline.total_time_s - self.plan.total_time_s
        self.last_replan_t = 0.0
        # Score the initial plan once so its cards carry a confidence. No trigger here: nothing moves
        # until start(), and a risk replan of a plan nobody has seen would be a plan nobody can follow.
        self.risk = self._run_predict(self.sim.aircraft(), adapt=False)
        self.emit_state()
        self.emit_plan(self.plan, trigger="initial")
        for card in C.cards_from_plan(self.plan, None, now_t=0.0, states=self.sim.aircraft()):
            self._add_card(card)
        self.emit(event("radar", self.radar_payload(), t=0.0))
        self.emit_scoreboard()

    def _emit(self, ev: dict[str, Any]) -> None:
        try:
            self.memory.observe(ev)
        except Exception:  # noqa: BLE001 - memory never breaks the event path
            log.exception("memory.observe failed")
        # --- squack agent: the ring buffer query.timeline reads, and the wake policy ---
        if ev.get("type") not in RING_SKIP:
            self.events.append(ev)
        try:
            self.wake.observe(ev)
        except Exception:  # noqa: BLE001 - the agent never breaks the event path
            log.exception("wake.observe failed")
        self._emit_out(ev)

    def emit_from_thread(self, ev: dict[str, Any]) -> None:
        """Emit from the agent's worker thread: hand the event to the socket loop if there is one."""
        loop, owner = self._loop, self._loop_thread
        if loop is not None and owner is not None and threading.current_thread() is not owner and loop.is_running():
            loop.call_soon_threadsafe(self._emit, ev)
        else:
            self._emit(ev)

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
               "watching": self.watching(), "clock_speed": self.clock_speed()}
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
            "voice": not self.auto_speak,
            "speak_replies": self.speak_replies,
            "clock_speed": self.clock_speed(),
            "next_readback": self.next_readback,
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
            "ui_mode": self.ui_mode,  # squack agent: "normal" | "agent"
            "world_id": self.world_id,
            "memory": self.memory.label if self.memory.enabled else None,
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
            conflicts_predicted=self.conflicts_predicted, conflicts_resolved=self.conflicts_resolved,
            futures_per_s=(round(float(self.risk.futures_per_s)) if self.risk.n_rollouts else None),
            cones_now=len(self.risk.pairs),
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
                if p.get("result") != "ambiguous":  # "unclear" asks for a confirmation, it accuses nobody
                    self._set_card_status(cid, "error")
                    if c is not None and cid not in self.correcting:
                        self.alerted[c.callsign] = (cid, self.sim.t)
            elif typ == "resolver_step" and not self.tower_enabled:
                continue
            elif typ == "clearance_updated":
                status = p.get("status")
                cid = p.get("id")
                if status == "matched":
                    self.matches += 1
                    self.matched_at[cid] = self.sim.t
                    self._set_card_status(cid, "validated")
                    matched = self.core.store.get(cid)
                    if matched is not None:
                        self._after_readback(matched)
                    original = self.correcting.pop(cid, None)
                    if original is not None:  # the corrected readback came back right: close the alert
                        cs = p.get("callsign")
                        t_alert = self.alerted.pop(cs, (original, self.sim.t))[1]
                        self.emit(event("alert_resolved", {"clearance_id": original, "callsign": cs, "by": "correction",
                                                           "seconds": round(self.sim.t - t_alert, 1)}, t=self.sim.t))
            self.emit(ev)

    # ------------------------------------------------------------------ cards

    def _add_card(self, card: InstructionCard) -> None:
        if card.confidence is None:
            self._score_card(card)
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

    def set_voice(self, on: bool) -> None:
        """The one switch, and it is the controller's at any moment.

        Off: Tower sends every instruction itself by data link the instant the plan changes. That
        is the path demo: nothing is spoken, so nothing waits and nothing is misheard.
        On: the real loop. Tower proposes each instruction on a card, the controller says it, the
        pilot reads it back, and both are heard by our Whisper model and checked.
        """
        self.auto_speak = not on
        self._voice_card = None
        self.emit_state()
        if self.scenario is not None:
            self.notice(("Voice on: say each instruction and the pilot reads it back."
                         + (f" The clock runs at {self.speed:g}x and slows to 1x by itself whenever there is "
                            "something to say." if self.speed > 1.0 else ""))
                        if on else "Voice off: Tower sends every instruction by data link, instantly.", "info")
        if not on:
            self._auto_links()  # whatever was waiting to be said goes out now

    def set_auto_speak(self, enabled: bool) -> None:
        """Older name for the same switch: auto_speak on is voice off."""
        self.set_voice(not enabled)

    def clock_speed(self) -> float:
        """How fast the clock really runs right now.

        Voice off: whatever speed was chosen. Voice on: speech takes real seconds, so the clock
        drops to 1x by itself whenever there is something to say or somebody is talking, and
        runs at the chosen speed in between. A spoken reroute is two instructions several
        minutes of flying apart: without this a short demo is mostly waiting.
        """
        if self.auto_speak or self.speed <= 1.0:
            return self.speed
        return 1.0 if self.talking() else self.speed

    def talking(self) -> bool:
        """Voice on: is the frequency in use, or is an instruction waiting to be said?"""
        if self.human_on_mic or self._speaking or self.held:
            return True
        now = self.sim.t
        if any(c.status == "open" and now - c.issued_at < c.timeout_s for c in self.core.store.all_open()):
            return True  # an exchange in progress: instruction issued, readback not yet in
        if any(t <= now + 3.0 for t, _ in self.pending):
            return True  # a pilot is about to key up
        return any(c.status in ("pending", "error") and not c.minor and c.callsign in self.sim.active
                   and (c.origin != "initial" or c.cause or c.emergency) for c in self.cards.values())

    def set_next_readback(self, mode: str) -> None:
        """Script the next pilot reply so a catch can be shown on cue. One shot, then back to random."""
        allowed = ("random", "correct", "wrong_value", "wrong_aircraft", "omitted_item", "missing_readback")
        self.next_readback = mode if mode in allowed else "random"
        self.emit_state()

    def set_ptt(self, down: bool) -> None:
        self.human_on_mic = bool(down)
        if down and self.asr is not None and hasattr(self.asr, "warm"):
            # The key is down: in a few seconds there will be audio to send. Get the line to the
            # speech model open now, so the transmission does not pay for it.
            try:
                asyncio.get_running_loop().run_in_executor(None, self.asr.warm)
            except RuntimeError:  # no loop: a test calling this directly
                pass

    def set_auto_voice(self, enabled: bool) -> None:
        """Auto with or without Tower's voice. Silent is instant: nothing waits for a radio exchange."""
        self.auto_voice = bool(enabled)
        if not enabled:
            self._voice_card = None
        self.emit_state()

    def set_speak_replies(self, enabled: bool) -> None:
        """Whether squack also says its answers on the frequency (tower/voice.py)."""
        self.speak_replies = bool(enabled)
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
        await self._agent_pulse()  # squack agent: wake batches and the stage, in any lifecycle
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
                # Predicted risk first: a risk replan resets last_replan_t, so the periodic check
                # below does not plan the same second twice.
                self._risk_step(now, states)
                # Re-check often while anything unusual is going on, in either mode. Never while the
                # controller is mid-sentence: a card must not change under the words being read.
                busy = bool(self.disruptions) or any(a.target_hdg is not None and not a.is_intruder
                                                     for a in self.sim.active.values())
                every = REPLAN_ACTIVE_S if busy else REPLAN_EVERY_S
                if now - self.last_replan_t >= every and not self.human_on_mic:
                    self._replan("periodic")
                if not self.auto_speak:
                    self._back_on_course()
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
        sent_path: list[tuple[float, float]] | None = None
        if reroute is not None:
            # By voice a reroute is a heading now and a "direct" later. By data link it is the planned
            # path itself, so the aircraft flies the line drawn on the map, turn for turn.
            via, exit_name, hdg, leg_nm = reroute
            commands = [c for c in commands if c.kind not in ("heading", "direct")]
            commands.append(SimCommand(kind="route", value=exit_name, via=via))
            # What radar will hold it to: the line it was sent, with the curves it will really fly.
            gate = self.sim.waypoints.get(exit_name)
            if gate is not None:
                from planner.trajectory import flyable
                sent_path = flyable([(a.x, a.y), *via, (gate.x_nm, gate.y_nm)], a.hdg, a.gs)
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
        self.core.conformance.watch(c, watched, now=now, path=sent_path)
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
                release: set[str] | None = None, why: str = "", repin: set[str] | None = None,
                rescore: bool = False) -> list[str]:
        """Repair the plan and issue the cards. Returns the callsigns that got a new instruction.

        rescore: run the risk prediction again on the new plan before the cards are made, so their
        confidence reflects the risk left after this replan rather than the risk that triggered it.
        """
        if self.plan is None or self.scenario is None:
            return []
        states = self.sim.aircraft()
        prev = self.plan
        issued = self._unsaid_headings()
        self.plan = PL.replan(prev, states, self.scenario.waypoints, self.sim.zones, self.buffer_nm,
                              disruption=disruption, flights=self.scenario.flights, now_t=self.sim.t,
                              time_budget_s=0.5, release=release, repin=repin, unsaid=set(issued),
                              as_flown=not self.auto_speak,
                              frozen_s=(FROZEN_MANUAL_S if not self.auto_speak else
                                        FROZEN_LINK_S if self._links_only() else FROZEN_AUTO_S))
        self.plan.trigger = trigger
        self.last_replan_t = self.sim.t
        if rescore:
            self.risk = self._run_predict(states, adapt=False)
        new_cards = C.cards_from_plan(self.plan, prev, now_t=self.sim.t, states=states, issued=issued)
        new_cards += self._new_followups(states)
        if release:
            ended = tuple(f" to clear {name}" for name in release)
            freed = {p.callsign for p in prev.paths if any(c.endswith(ended) for c in p.changes)}
            have = {c.callsign for c in new_cards}
            new_cards += [c for c in C.release_cards(self.plan, states, freed, self.sim.t, why)
                          if c.callsign not in have]
        circling = {a.callsign for a in self.sim.active.values() if a.orbit_dir}
        new_cards = [c for c in new_cards if c.callsign not in circling]  # it was told to circle: no advice until it is done
        changed = sorted({c.callsign for c in new_cards})
        self.emit_plan(self.plan, trigger=trigger, changed=changed)
        for card in new_cards:
            self._drop_pending_cards(card.callsign)  # superseded by this one
            self._add_card(card)
        # A heading nobody has said yet, for a turn the plan no longer wants (the storm moved on, or
        # the flight is past it): take the card down rather than leave a stale turn on the list.
        still = C.turning(self.plan)
        for cs in issued:
            if cs not in still and cs not in changed:
                for c in list(self.cards.values()):
                    if c.callsign == cs and c.status == "pending" and all(i.type == "heading" for i in c.items):
                        c.status = "superseded"
                        self.emit(event("instruction_card", c, t=self.sim.t))
                        del self.cards[c.id]
        self._auto_links()  # in Auto the reroutes leave now, not on the next tick of the clock
        return changed

    # ------------------------------------------------------------------ predicted risk (TRD 07)

    def _run_predict(self, states: list[AircraftState], *, adapt: bool) -> Any:
        """One call of the risk module on the current plan. adapt: size the next call to this one's cost."""
        if self.plan is None:
            return RISK.RiskReport()
        paths = {p.callsign: p for p in self.plan.paths}
        seed = (self.scenario.seed if self.scenario is not None else 0) + int(self.sim.t)
        t0 = time.perf_counter()
        try:
            report = self.risk_predict(states, paths, self.sim.zones, self.sim.t, n=self._risk_n, seed=seed)
        except Exception:  # noqa: BLE001 - a prediction that fails must not stop the clock
            log.exception("risk.predict failed")
            return RISK.RiskReport(n_rollouts=0)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if adapt:
            budget_ms = 25.0 if len(states) <= 15 else 150.0
            if elapsed_ms > budget_ms:
                self._risk_n = max(RISK_N_MIN, self._risk_n // 2)
            elif elapsed_ms < budget_ms / 2:
                self._risk_n = min(RISK_N_MAX, self._risk_n * 2)
        return report

    def _risk_step(self, now: float, states: list[AircraftState]) -> None:
        """Every step at 1x, every RISK_FAST_CADENCE_S above: predict, count, replan, emit."""
        cadence = 0.0 if self.clock_speed() <= 1.0 else RISK_FAST_CADENCE_S
        if now - self._risk_t + 1e-9 < cadence:
            return
        self._risk_t = now
        self.risk = self._run_predict(states, adapt=True)
        due: list[tuple[str, str]] = []
        p_now: dict[frozenset[str], float] = {}
        for p in self.risk.pairs:
            key = frozenset((p.a, p.b))
            p_now[key] = float(p.p_max)
            st = self._risk_seen.setdefault(key, {"over": False, "replanned_t": -1e9, "los_at": 0})
            if p.p_max >= RISK.REPLAN_P:
                if not st["over"]:
                    st["over"] = True
                    st["los_at"] = self.monitor.losses
                    self.conflicts_predicted += 1
                if now - st["replanned_t"] >= RISK_REPLAN_EVERY_S and not self.human_on_mic:
                    due.append((p.a, p.b))
        for key, st in self._risk_seen.items():
            if st["over"] and p_now.get(key, 0.0) < RISK.SHOW_P:
                st["over"] = False
                # "No loss of separation for this pair meanwhile" is approximated as no new loss of
                # separation anywhere since the pair crossed the threshold: the monitor counts
                # losses in total, and a second pair losing separation in the same window is rare.
                if self.monitor.losses == st["los_at"]:
                    self.conflicts_resolved += 1
        if due:
            for a, b in due:
                self._risk_seen[frozenset((a, b))]["replanned_t"] = now
            trigger = f"risk {due[0][0]}/{due[0][1]}" if len(due) == 1 else f"risk {len(due)} pairs"
            self._replan(trigger, repin={cs for pair in due for cs in pair}, rescore=True)
        empty = not self.risk.pairs
        if now - self._risk_emit_t >= RISK_EMIT_EVERY_S and not (empty and self._risk_prev_empty):
            self._risk_emit_t = now
            self.emit(event("risk", self._risk_payload(self.risk), t=now))
        self._risk_prev_empty = empty

    @staticmethod
    def _risk_payload(report: Any) -> dict[str, Any]:
        """Plain JSON only: the report may hold numpy scalars, and the sender must not depend on a default hook."""
        pairs = []
        for p in report.pairs:
            pairs.append({
                "a": str(p.a), "b": str(p.b), "p_max": float(p.p_max),
                "t_first_s": (None if p.t_first_s is None else float(p.t_first_s)),
                "eta_s": float(p.eta_s), "min_sep_nm_p5": float(p.min_sep_nm_p5),
                "curve": [[float(t), float(v)] for t, v in p.curve],
                "cpa_xy": [float(p.cpa_xy[0]), float(p.cpa_xy[1])],
                "spread_a_nm": float(p.spread_a_nm), "spread_b_nm": float(p.spread_b_nm),
            })
        return {"pairs": pairs, "horizon_s": float(report.horizon_s), "n_rollouts": int(report.n_rollouts),
                "elapsed_ms": round(float(report.elapsed_ms), 2), "futures_per_s": float(report.futures_per_s)}

    def _score_card(self, card: InstructionCard) -> None:
        """confidence = (1 - risk_after) x margin_factor, from the current report and the card's path."""
        risk_after = 0.0
        for p in self.risk.pairs:
            if card.callsign in (p.a, p.b):
                risk_after = max(risk_after, float(p.p_max))
        path = next((p for p in self.plan.paths if p.callsign == card.callsign), None) if self.plan else None
        margin = RISK.margin_factor(path.cost, path.runner_up_cost) if path is not None else 1.0
        card.risk_after = round(risk_after, 3)
        card.confidence = round(min(0.99, max(0.05, (1.0 - risk_after) * margin)), 3)

    def _unsaid_headings(self) -> dict[str, float]:
        """Voice on: callsign -> the heading on its card that nobody has said yet.

        A heading is worked out for one place and one moment. While its card waits in the list the
        aircraft flies on, so the planner works these flights out again on every replan, and the
        card is replaced once the heading on it is no longer the one to say. Not the card being
        said at this moment: that one must not change under the controller's words.
        """
        if self.auto_speak:
            return {}
        out: dict[str, float] = {}
        for c in self.cards.values():
            if c.status != "pending" or c.id == self._voice_card or c.callsign not in self.sim.active:
                continue
            for i in c.items:
                if i.type == "heading":
                    try:
                        out[c.callsign] = float(i.value)
                    except (TypeError, ValueError):
                        pass
        return out

    def _new_followups(self, states: list[AircraftState]) -> list[InstructionCard]:
        """"Proceed direct" cards that are due and not already on the list."""
        if self.plan is None:
            return []
        waiting = {c.callsign for c in self.cards.values()
                   if c.origin == "followup" and c.status in ("pending", "spoken")}
        causes = {c.callsign: c.cause for c in self.cards.values()  # cards are kept in the order they were issued
                  if c.cause and any(i.type == "heading" for i in c.items)}
        return [c for c in C.followup_cards(self.plan, states, self.sim.t, causes) if c.callsign not in waiting]

    def _back_on_course(self) -> None:
        """Voice on, every tick: offer the second card of a reroute the moment it is safe.

        The planner says when: a flight on an assigned heading whose plan is direct again can be
        told to go direct from where it is. Every flight on a heading is looked at again on each
        replan, and here as well when it reaches the point where the planner expected the turn
        back to become possible, so the card is not up to REPLAN_ACTIVE_S late.
        """
        if self.plan is not None and not self.human_on_mic:
            due = set()
            via = {p.callsign: p.via[0] for p in self.plan.paths if p.via}
            for a in self.sim.active.values():
                if a.is_intruder or a.target_hdg is None or a.callsign not in via:
                    continue
                if self.sim.t - self._turn_back_t.get(a.callsign, -1e9) < TURN_BACK_RETRY_S:
                    continue
                r = math.radians(a.target_hdg)
                ahead = (via[a.callsign][0] - a.x) * math.sin(r) + (via[a.callsign][1] - a.y) * math.cos(r)
                if ahead <= 1.0:  # NM still to run to the expected turn-back point
                    due.add(a.callsign)
                    self._turn_back_t[a.callsign] = self.sim.t
            if due:
                self._replan("turn back", repin=due)
        for card in self._new_followups(self.sim.aircraft()):
            self._drop_pending_cards(card.callsign)
            self._add_card(card)

    def _after_readback(self, c: OpenClearance) -> None:
        """A heading or a routing has just been accepted: plan that flight again from where it is.

        Until now the line on the map was the plan made before the controller spoke. The aircraft
        turns when its pilot reads back, not when the plan assumed, so the line and the aircraft
        parted company and a minute later the planner called it a deviation. Now the line is
        redrawn at once: along the heading for the shortest safe distance, then direct.
        """
        if self.auto_speak or not any(i.type in ("heading", "route") for i in c.items):
            return
        if self.clearance_meta.get(c.id, {}).get("replanned"):
            return  # already done the moment the aircraft acted, which is sooner
        if c.callsign in self.sim.active and self.plan is not None:
            self._replan("readback", repin={c.callsign})

    # ------------------------------------------------------------------ disruptions

    def add_disruption(self, kind: str, x_nm: float | None = None, y_nm: float | None = None,
                       target: str | None = None) -> Disruption | None:
        """Drop a disruption into the world and replan around it.

        `kind` is any key of disruptions.PROFILES, or "random". With no position, or for
        "random", it is put where it will matter: on the path of a flight a few minutes ahead.
        Seeded by the scenario and the count so far, so the same presses give the same result.

        `target` is a callsign: "disrupt this flight". The disruption goes on that flight's own
        planned path, far enough ahead to be avoided and near enough to matter, so the button
        always does something and always to the aircraft the room is looking at. Placing one by
        hand meant guessing where a path really runs under a tilted, height-exaggerated map.
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
        spot = None
        if target is not None:
            a = self.sim.active.get(target)
            if a is None or a.is_intruder:
                self.notice(f"{target} is not in the sector.", "warn")
                return None
            if kind == "emergency":
                x_nm, y_nm = a.x, a.y  # the flight itself: _make_disruption takes the nearest aircraft
            else:
                spot = self._ahead_of(target, DZ.PROFILES[kind])
                if spot is None:
                    self.notice(f"{target} is too close to the edge of the sector to put that ahead of it. Pick a flight with more of its path left.", "warn")
                    return None
                x_nm = y_nm = None
        d = self._make_disruption(kind, x_nm, y_nm, rng, spot=spot)
        self.disruptions[d.id] = d
        if kind == "emergency":
            self._drop_pending_cards(d.id)
        self.sim.add_disruption(d)
        self.emit(event("disruption", self._disruption_payload(d), t=self.sim.t))
        self.emit_state()
        before = self._unresolved()
        was = {a.callsign: (a.hdg_deg, a.target_alt_ft, a.target_gs_kt) for a in self.sim.aircraft()}
        changed = self._replan(f"{d.label} {d.id}", disruption=d)
        self.rerouted.update(changed)
        self._react = (self.sim.t, {cs: was[cs] for cs in changed if cs in was})
        self.reaction_s = None
        self._measure_reaction()  # by data link the new level or speed is already set: that is a reaction too
        self._disruption_notice(d, changed, before)
        if kind == "emergency" and self.lifecycle == "running":
            self._mayday(d)
        return d

    def remove_disruption(self, disruption_id: str) -> None:
        if self.sim.remove_disruption(disruption_id):
            self._disruptions_ended([disruption_id], by_hand=True)

    def _ahead_of(self, callsign: str, prof: "DZ.Profile") -> tuple[float, float, float, float, float | None] | None:
        """(x, y, level, seconds ahead, radius) on a flight's planned path, for "disrupt this flight".

        A zone is centred where the flight will be once it has its radius, the planner's margin
        and room to turn in front of it: near enough that the detour starts now, far enough that
        it is a detour and not a flight through the middle. It takes the small end of the kind's
        size, which keeps the way round short. An intruder is timed to meet the flight four
        minutes on. None if the path leaves the middle of the sector before that.
        """
        a = self.sim.active[callsign]
        now = self.sim.t
        half = (self.scenario.sector_nm if self.scenario else 200.0) / 2.0
        circle = self.frame.shape == "circle"
        path = next((p for p in (self.plan.paths if self.plan else []) if p.callsign == callsign and p.samples), None)
        if path is not None:
            arr = np.asarray(path.samples, dtype=float).reshape(-1, 4)
            arr = arr[arr[:, 0] >= now - 1e-6]
        if path is None or arr.shape[0] < 2:  # no plan: straight on
            r = math.radians(a.hdg)
            ts = np.arange(0.0, 900.0, 10.0)
            arr = np.column_stack([now + ts, a.x + math.sin(r) * a.gs * ts / 3600.0, a.y + math.cos(r) * a.gs * ts / 3600.0,
                                   np.full_like(ts, a.alt)])
        run = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(arr[:, 1]), np.diff(arr[:, 2])))])  # NM along the path
        others = [(b.x, b.y) for b in self.sim.active.values() if b.callsign != callsign and not b.is_intruder]
        if prof.shape == "circle":
            radius = float(prof.radius_nm[0])
            wanted = [radius + room for room in (22.0, 17.0, 13.0)]  # NM from the aircraft to the centre
        else:
            radius = None
            wanted = [a.gs * lead / 3600.0 for lead in (240.0, 200.0, 160.0)]
        best = None
        for d in wanted:
            k = int(np.searchsorted(run, d))
            if k >= arr.shape[0]:
                continue
            x, y = float(arr[k, 1]), float(arr[k, 2])
            if not DZ.inside_sector(x, y, half * 0.85, circle):
                continue
            room = min((math.hypot(x - ox, y - oy) for ox, oy in others), default=999.0)
            spot = (x, y, float(a.target_alt), max(60.0, float(arr[k, 0]) - now), radius)
            if room >= (radius or 0.0) + 8.0:
                return spot  # nobody else is under it
            if best is None or room > best[0]:
                best = (room, spot)
        return best[1] if best else None

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
                         rng: np.random.Generator,
                         spot: tuple[float, float, float, float, float | None] | None = None) -> Disruption:
        """`spot` (from _ahead_of) replaces Tower's own choice of where it will matter."""
        prof = DZ.PROFILES[kind]
        now = self.sim.t
        half = (self.scenario.sector_nm if self.scenario else 200.0) / 2.0
        circle = self.frame.shape == "circle"
        placed = x_nm is not None and y_nm is not None
        aimed_radius = None
        if spot is not None:
            tx, ty, level, lead, aimed_radius = spot
        else:
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
            r = aimed_radius if aimed_radius is not None else DZ.uniform(rng, prof.radius_nm)
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
            t0, was = self._react
            for cs, (hdg0, alt0, gs0) in was.items():
                a = self.sim.active.get(cs)
                if a is None:
                    continue
                turning = abs((a.hdg - hdg0 + 180) % 360 - 180) >= 2.0
                if turning or a.target_alt != alt0 or (gs0 is not None and a.target_gs != gs0):
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
        # A pilot's readback is the thing being checked: the hint may rescue a garbled fix, but it
        # must not turn a clearly different fix into the expected one.
        hint_first = speaker != "pilot"
        norm = snap_waypoints(norm0, wps, pref, trust_hint=hint_first)
        return Transmission(id=f"tx-{uuid.uuid4().hex[:8]}", t_start=self.sim.t - duration_s,
                            t_end=self.sim.t, audio_ref=audio_ref, text_raw=text_raw,
                            text_norm=norm, asr_confidence=conf, speaker=speaker,
                            n_best=[snap_waypoints(normalize(h), wps, pref, trust_hint=hint_first)
                                    for h in (n_best or [])],
                            text_stock=text_stock)  # type: ignore[arg-type]

    async def _transcribe(self, samples: np.ndarray, fast: bool = False,
                          on_stock=None) -> tuple[str, float, list[str], str | None, float]:
        """`fast` is the controller's own voice: one beam, and the stock comparison is not waited
        for (it goes to `on_stock` when it turns up). See tower/asr.py."""
        if self.asr is None:
            self.asr = await asyncio.to_thread(get_asr)
        prompt = build_prompt([_spoken(cs) for cs in self.sim.active], self.spoken_waypoints())
        t0 = time.perf_counter()
        kw: dict[str, Any] = {}
        if fast:
            kw["fast"] = True
            if on_stock is not None and type(self.asr).__name__ == "StockAndTuned":
                kw["on_stock"] = on_stock
        try:
            r = await asyncio.to_thread(self.asr.transcribe, samples, prompt, **kw)
        except TypeError:  # a stand-in model that knows nothing of `fast`
            r = await asyncio.to_thread(self.asr.transcribe, samples, prompt)
        return r.text, r.confidence, list(r.n_best), r.text_stock, time.perf_counter() - t0

    async def controller_audio(self, samples: np.ndarray, sr: int = 16000,
                               on_text: Callable[[str], None] | None = None) -> None:
        """A controller utterance from the mic: transcribe, then treat as controller text.

        `on_text` hears the transcript the moment it exists, before anything is done with it: the
        command bar's dictation final (app.py) goes out ahead of the transcript line and the pilots."""
        if not self._radio_open():
            return
        if len(samples) / sr < MIN_MIC_S:
            return  # a stray tap of Space, not a transmission (07 section 7.1: drop under 0.5 s)
        ref = f"ctl-{uuid.uuid4().hex[:8]}.wav"
        float_to_wav(AUDIO_DIR / ref, samples, sr)
        loop = asyncio.get_running_loop()
        late: dict[str, Any] = {}

        def stock_heard(text: str) -> None:  # called from the comparison's thread, a moment later
            loop.call_soon_threadsafe(self._stock_arrived, late, text)

        t0 = time.perf_counter()
        text, conf, n_best, stock, lat = await self._transcribe(samples, fast=True, on_stock=stock_heard)
        if on_text is not None:
            on_text(text)
        await self._controller(text, audio_ref=ref, conf=conf, n_best=n_best, text_stock=stock,
                               duration_s=len(samples) / sr, asr_latency=lat, late=late)
        log.info("controller: heard in %.2f s, understood %.2f s after the key was released: %r",
                 lat, time.perf_counter() - t0, text)

    def _stock_arrived(self, late: dict[str, Any], text: str) -> None:
        """The stock model's version of the controller's words, for the side-by-side. It comes after
        the transcript line went out, so the line is sent again with it filled in (same id)."""
        late["stock"] = text
        p = late.get("payload")
        if p is not None and not p.get("text_stock"):
            p["text_stock"] = text
            self.emit(event("transcript", p, t=self.sim.t))

    async def controller_text(self, text: str) -> None:
        if not self._radio_open():
            return
        await self._controller(text)

    async def _controller(self, text: str, *, audio_ref: str = "", conf: float = 1.0,
                          n_best: list[str] | None = None, text_stock: str | None = None,
                          duration_s: float = 3.0, asr_latency: float = 0.0,
                          card: InstructionCard | None = None, late: dict[str, Any] | None = None) -> None:
        if not text.strip():
            return
        tx = self._new_tx(text, "controller", audio_ref, conf, n_best, text_stock, duration_s)
        t0 = time.perf_counter()
        async with self._lock:
            states = self.sim.aircraft()
            events = self.core.on_transmission(tx, list(self.sim.active), states)
        self.transmissions += 1
        self.tier1_latencies.append(asr_latency + time.perf_counter() - t0)
        payload = self._tx_payload(tx)
        if late is not None:
            late["payload"] = payload
            if late.get("stock") and not payload.get("text_stock"):
                payload["text_stock"] = late["stock"]
        self.emit(event("transcript", payload, t=self.sim.t))
        if card is not None and self._trust_the_card(card, events):
            conf = min(conf, 0.5)
        self._emit_core_events(events)
        for e in events:
            if e["type"] == "aside":
                await self._aside(e["payload"])
                return
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
        # Tower only guessed this instruction (the language model filled it in). Decided before any
        # card is linked below: a card Tower spoke itself is the truth, a card merely pending for the
        # same aircraft is not.
        ext = self.core.last_extraction
        guessed = ext is not None and ext.transmission_id == tx.id and ext.method == "llm" and card is None
        human = card is None
        if human and not opened:
            opened = await self._interpret(tx)
            if opened is None:  # an aside ("unable"), already dealt with
                return
            ext = self.core.last_extraction  # the agent's reading, if it had one
            if not opened:
                self._explain_unheard(tx)
        for c in opened:
            self.clearance_meta[c.id] = {"issued_real": time.monotonic(), "issued_sim": self.sim.t}
            if guessed and conf < GUESS_MIN_CONF:
                # Speaker bleed, a cough, half a word: the grammar found nothing, the model found an
                # "instruction", and the speech model itself was unsure. Issue nothing at all.
                async with self._lock:
                    closed = self.core.store.resolve(c.id, "uncertain")
                if closed is not None:
                    self.emit(event("clearance_updated", closed, t=self.sim.t))
                self.notice("Tower could not make an instruction out of that transmission, so nothing was issued.", "info")
                continue
            this_card = card
            if human:
                # Is this the correction of a readback Tower just flagged? Then it belongs to that
                # exchange: the pilot gets it right this time, and a good readback closes the alert.
                flagged = self.alerted.get(c.callsign)
                wrong = self.core.store.get(flagged[0]) if flagged else None
                if wrong is not None and not guessed and _said_vs_card(c.items, wrong.items)[0] == "same":
                    self.correcting[c.id] = wrong.id
                    self.card_by_clearance[c.id] = self.card_by_clearance.get(wrong.id, "")
                    self._schedule_pilot(c, heard_ok=True, correction=True)
                    continue
                this_card = self._match_card(c)
                if this_card is not None:
                    verdict, detail = _said_vs_card(c.items, this_card.items)
                    standard = ext is not None and ext.transmission_id == tx.id and ext.method == "grammar"
                    if verdict == "conflict" and conf < UNSURE_CONF and standard:
                        # A standard phrase, Tower doubts its own ears, and the card says something
                        # close: it was almost certainly the card. Never for plain English: "turn
                        # around" scores low with a model tuned on phraseology, and it is not the card. (The simulated pilot acts on Tower's transcript,
                        # which a real pilot does not, so a mishearing here would move an aircraft.)
                        c.items = [i.model_copy() for i in this_card.items]
                        stored = self.core.store.get(c.id)
                        if stored is not None:  # the copy the readback will be checked against
                            stored.items = [i.model_copy() for i in this_card.items]
                            self.emit(event("clearance_updated", stored, t=self.sim.t))
                        self.notice(f"Tower was not sure what it heard for {c.callsign} and took the card: {this_card.phrase}.", "info")
                        verdict, conf = "same", max(conf, 0.5)  # the pilot hears the card, which is clear enough
                    if verdict == "conflict":
                        # The controller is the authority. What was said is what happens; the card
                        # was advice. It stays up, marked, until the planner catches up a moment
                        # later and replaces it with what is right for the aircraft's new course.
                        this_card.heard_instead = C.phrase_for(c.callsign, c.items)
                        self.emit(event("instruction_card", this_card, t=self.sim.t))
                        self.notice(f"{c.callsign} is doing what you said ({detail} was on the card). Tower is planning round it.", "info")
                        this_card = None
                    if verdict == "partial":
                        if {i.type for i in c.items} & {i.type for i in this_card.items}:
                            self.notice(f"That was part of the card for {c.callsign}. Still to say: {detail}.", "info")
                        # else: nothing to do with the card at all (a heading, and the card is a
                        # routing). The controller's own instruction; the card is still advice.
                        this_card = None  # the card stays open for the rest
            if this_card is not None:
                this_card.via = this_card.via or "human"
                this_card.heard_instead = None
                self._link_card(this_card, c.id)
            # A pilot who heard the same garble asks for it again. Flying a guess put an aircraft on
            # heading 021.
            self._schedule_pilot(c, heard_ok=(conf >= 0.5 and not guessed))

    async def _interpret(self, tx: Transmission) -> list[OpenClearance] | None:
        """Nothing the grammar or the patterns know, but it was addressed to an aircraft: ask the
        interpreter agent what was meant. Off the clock, in a thread, capped, so the radar never
        waits for it. Returns the clearances it opened, [] for none, None if it ended in an aside."""
        ext = self.core.last_extraction
        agent = self.core.interpreter
        cs = ext.callsign if ext is not None and ext.transmission_id == tx.id else None
        if cs is None or cs not in self.sim.active or not agent.available or len(tx.text_norm.split()) < 3:
            return []
        self.notice(f"{cs}: working out what you meant…", "info")
        states = self.sim.aircraft()
        me = next(s for s in states if s.callsign == cs)
        fixes = {n: (w.x_nm, w.y_nm) for n, w in self.sim.waypoints.items() if w.kind != "hidden"}
        try:
            items, why = await asyncio.wait_for(
                asyncio.to_thread(agent.interpret, tx.text_norm, me, fixes, list(self.sim.zones), states), INTERPRET_TIMEOUT_S)
        except asyncio.TimeoutError:
            self.notice(f"{cs}: Tower could not work that out in time. Say it again, or use standard phraseology.", "warn")
            return []
        if not items:
            if why:
                self.notice(f"{cs}: {why}", "warn")
            return []
        async with self._lock:
            events = self.core.open_interpreted(tx, cs, items)
        for e in events:
            if e["type"] == "aside":
                await self._aside(e["payload"])
                return None
        self._emit_core_events(events)
        self.emit(event("transcript", self._tx_payload(tx), t=self.sim.t))  # the line again, now with its callsign and reading
        return [OpenClearance.model_validate(e["payload"]) for e in events if e["type"] == "clearance_opened"]

    async def _aside(self, p: dict[str, Any]) -> None:
        """Two things a controller says that are not clearances: "disregard" and the impossible."""
        cs = str(p.get("callsign") or "")
        a = self.sim.active.get(cs)
        if a is None:
            return
        pilot = self.fleet.get(cs)
        from pilots.readback import say_callsign as pilot_callsign
        if p.get("action") == "disregard":
            before = self._undo.pop(cs, None)
            for c in self.core.store.open_clearances(cs):  # nothing is owed on an instruction that was withdrawn
                async with self._lock:
                    closed = self.core.store.resolve(c.id, "uncertain")
                if closed is not None:
                    self.emit(event("clearance_updated", closed, t=self.sim.t))
            if before is None:
                self.notice(f"{cs}: nothing to disregard.", "info")
                return
            a.target_hdg, a.target_alt, a.target_gs = before["target_hdg"], before["target_alt"], before["target_gs"]
            a.route, a.via = list(before["route"]), list(before["via"])
            a.orbit_dir, a.orbit_left_deg = before["orbit_dir"], before["orbit_left_deg"]
            a._last_wp_dist = None
            self.emit(event("radar", self.radar_payload(), t=self.sim.t))
            self.notice(f"{cs}: last instruction withdrawn. It is back to what it was cleared to do before.", "info")
            if not self.auto_speak and self.plan is not None:
                self._replan("instruction", repin={cs})
            text = f"disregarding, {pilot_callsign(cs)}"
        else:
            what = str(p.get("what") or "").replace("UNABLE", "").strip().lower()
            self.notice(f"{cs}: unable{' (' + what + ')' if what else ''}. Nothing changed.", "warn")
            text = f"unable, {pilot_callsign(cs)}"
        resp = await asyncio.to_thread(pilot.announce, text, self.noise)
        tx = await self._hear_pilot(resp)
        self.emit(event("transcript", {**tx.model_dump(), "callsign": cs}, t=self.sim.t))

    def _explain_unheard(self, tx: Transmission) -> None:
        """The controller keyed the mic and nothing came of it. Say why, or it looks like a dead radio."""
        ext = self.core.last_extraction
        ext = ext if ext is not None and ext.transmission_id == tx.id else None
        if ext is None or ext.callsign is None:
            self.notice("Tower did not catch a callsign in that. Start with it: “Air Canada one two three, turn left…”",
                        "warn")
        elif not ext.items:
            self.notice(f"Tower heard {ext.callsign} but no instruction it knows. Say it again.", "warn")

    async def _hold_for_the_controller(self, c: OpenClearance, card: InstructionCard, detail: str) -> None:
        """What was said conflicts with the card. Either the controller slipped or Whisper misheard:
        Tower cannot tell which, so nothing goes to the pilot until the controller decides."""
        async with self._lock:
            self.core.store.resolve(c.id, "uncertain")
        self.emit(event("clearance_updated", self.core.store.get(c.id) or c, t=self.sim.t))
        heard = C.phrase_for(c.callsign, c.items)
        self.held[c.id] = (c, card.id)
        card.heard_instead = heard
        self.emit(event("instruction_card", card, t=self.sim.t))
        self.emit(event("said_check", {"clearance_id": c.id, "card_id": card.id, "callsign": c.callsign,
                                       "heard": heard, "expected": card.phrase, "detail": detail}, t=self.sim.t))
        self.notice(f"Tower heard “{heard}”. The card says {detail}. Nothing went to {c.callsign}: "
                    "say it again, or press Send as heard.", "warn")

    def confirm_heard(self, clearance_id: str) -> None:
        """The controller meant what Tower heard. Issue it as heard; the card stays open."""
        held = self.held.pop(clearance_id, None)
        if held is None or not self._radio_open():
            return
        old, card_id = held
        card = self.cards.get(card_id or "")
        if card is not None:
            card.heard_instead = None
            self.emit(event("instruction_card", card, t=self.sim.t))
        c = OpenClearance(id=self.core.store.next_id(), callsign=old.callsign, items=old.items,
                          issued_at=self.sim.t, source_transmission_id=old.source_transmission_id)
        self.core.store.open(c)
        self.emit(event("clearance_opened", c, t=self.sim.t))
        self.clearance_meta[c.id] = {"issued_real": time.monotonic(), "issued_sim": self.sim.t}
        self._schedule_pilot(c, heard_ok=True)

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
            # The same items in another order ("maintain flight level 360, direct TULEK") is not a
            # mishearing. Compared as what they ask for, so FL360 and 36,000 ft are the same too.
            if sorted(map(str, map(_item_key, heard.items))) == sorted(map(str, map(_item_key, card.items))):
                continue
            stored = self.core.store.get(heard.id)
            if stored is None:
                continue
            log.warning("Tower misheard its own card for %s: heard %s, said %s", card.callsign,
                        [i.value for i in heard.items], [i.value for i in card.items])
            stored.items = [it.model_copy() for it in card.items]
            # A dict, like every other event payload. The model object here could not be turned into
            # JSON, and that one failure killed the task that sends every event to every screen.
            e["payload"] = stored.model_dump()
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
        # Speaking takes a few real seconds and the card stays "pending" throughout, so a second
        # press of "Say it" used to transmit the same instruction again.
        if card_id in self._speaking or card.status != "pending":
            return
        self._speaking.add(card_id)
        card.via = card.via or "voice"  # Tower's own voice, whoever pressed the button
        try:
            await self._speak_card(card)
        finally:
            self._speaking.discard(card_id)

    async def _speak_card(self, card: InstructionCard) -> None:
        if self.tts is None or self.asr is None and not self.synthesize:
            await self._controller(card.phrase, card=card)
            return
        try:
            path = await asyncio.to_thread(self.tts.synthesize, card.phrase, self.tts.controller_voice())
            ref = f"ctl-{uuid.uuid4().hex[:8]}.wav"
            await asyncio.to_thread(apply_to_file, path, AUDIO_DIR / ref, max(0.05, self.noise * 0.5))
            samples, sr = read_wav(AUDIO_DIR / ref)
            self.emit(event("radio_audio", {"speaker": "controller", "callsign": card.callsign, "audio_ref": ref,
                                            "duration_s": round(len(samples) / sr, 2)}, t=self.sim.t))
            text, conf, n_best, stock, lat = await self._transcribe(samples)
            await self._controller(text, audio_ref=ref, conf=conf, n_best=n_best, text_stock=stock,
                                   duration_s=len(samples) / sr, asr_latency=lat, card=card)
        except Exception as exc:  # TTS or ASR failure must never stall the demo
            log.warning("speak_card fell back to text: %s", exc)
            await self._controller(card.phrase, card=card)

    # ------------------------------------------------------------------ radio: pilot side

    def _schedule_pilot(self, c: OpenClearance, heard_ok: bool = True, correction: bool = False) -> None:
        async def go() -> None:
            await self._pilot_responds(c, heard_ok, correction=correction)
        if not self.realtime:
            self.pending.append((self.sim.t + PILOT_DELAY_S, go))
            return

        # With a screen attached the reply does not wait for the clock. The queue above is served
        # once a tick, so a 1.5 s delay was really 1.5 to 2.5 s of nothing happening after the
        # controller let go of the key, and that gap is what made the radio feel dead.
        async def soon() -> None:
            await asyncio.sleep(PILOT_KEY_UP_S)
            try:
                await go()
            except Exception:
                log.exception("pilot reply failed for %s", c.callsign)
        task = asyncio.create_task(soon())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _pilot_responds(self, c: OpenClearance, heard_ok: bool = True,
                              correction: bool = False) -> None:
        if c.callsign not in self.sim.active and not correction:
            return
        pilot = self.fleet.get(c.callsign)
        # the fix names let a pilot read a direct back to the wrong one
        kw: dict[str, Any] = {"noise_level": self.noise, "waypoints": self.spoken_waypoints()}
        if not correction and self.next_readback != "random":
            if self.next_readback == "correct":
                kw["force_error"] = False
            else:
                kw["error_type"] = self.next_readback
            self.next_readback = "random"  # one shot
            self.emit_state()
        # Decide first, which is instant, and act on it. The voice is made afterwards: it takes one
        # to three seconds, and the aircraft used to sit still for all of them.
        if correction:
            resp: PilotResponse = pilot.respond_to_correction(c, self.noise, speak=False)
        else:
            resp = pilot.respond(c, heard_ok, list(self.sim.active), speak=False, **kw)
        meta = self.clearance_meta.setdefault(c.id, {})
        if resp.injected_error:
            meta["injected_error"] = resp.injected_error
            self.errors_injected += 1
        # The plane obeys what the pilot said, whoever the pilot was.
        actor = resp.acting_callsign
        moved = False
        cmds = [cmd for cmd in (resp.sim_commands or [resp.sim_command]) if cmd.kind != "none"]
        if cmds and actor in self.sim.active:
            a = self.sim.active[actor]
            self._undo[actor] = {"target_hdg": a.target_hdg, "target_alt": a.target_alt, "target_gs": a.target_gs,
                                 "route": list(a.route), "via": list(a.via), "orbit_dir": a.orbit_dir,
                                 "orbit_left_deg": a.orbit_left_deg}
        for cmd in cmds:
            if actor in self.sim.active:
                self.sim.apply(actor, cmd)
                moved = True
        if moved:
            meta["acted_real"] = time.monotonic()
            if not self.auto_speak and actor in self.sim.active and self.plan is not None:
                # Plan round what it is now really doing, at once: its own line is redrawn from
                # where it is, and everyone else is kept clear of it. If the instruction itself
                # is the problem (a heading into a storm), the card that comes out of this says so.
                meta["replanned"] = True
                self._replan("instruction", repin={actor})
            # Show it now, not on the next tick of the clock: the cleared heading and level are in
            # the radar frame, and the screen draws them the moment they change.
            self.emit(event("radar", self.radar_payload(), t=self.sim.t))
        await asyncio.to_thread(pilot.voice_it, resp, c)
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
            # The pilot did not get it and said so. Nothing was read back, so nothing is cleared and
            # nothing is owed: left open, this timed out 25 s later as "no readback, heard nothing".
            async with self._lock:
                closed = self.core.store.resolve(c.id, "uncertain")
            if closed is not None:
                self.emit(event("clearance_updated", closed, t=self.sim.t))
            self._set_card_status(c.id, "pending")  # the card is there to be said again
            self.notice(f"{c.callsign} asked you to say again. Nothing was read back, so say it again.", "warn")
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
                # On the air now: the screen plays it while Tower is still working out what was said.
                self.emit(event("radio_audio", {"speaker": "pilot", "callsign": resp.acting_callsign, "audio_ref": ref,
                                                "duration_s": round(len(samples) / sr, 2)}, t=self.sim.t))
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

    async def agent_audio(self, samples: np.ndarray, on_text: Callable[[str], None] | None = None) -> str:
        text, *_ = await self._transcribe(samples)
        if on_text is not None:
            on_text(text)  # the dictation final, before the agent answers
        return await self.agent_request(text)

    async def agent_request(self, text: str, history: list[dict[str, Any]] | None = None,
                            ui_state: dict[str, Any] | None = None) -> str:
        from world_agent import handle  # local import: keeps the LLM optional
        self._agent_busy = True
        try:
            reply, actions = await handle(self, text, history, ui_state)
        finally:
            self._agent_busy = False
        # agent_reply stays for the headset path; the bar reads the `answer` the loop emitted.
        self.emit(event("agent_reply", {"text": reply, "actions": actions}, t=self.sim.t))
        await speak_reply(self, reply)  # respects self.speak_replies; never raises
        return reply

    # --- squack agent (backend/agent/, docs/trd/08-squack-agent-prd.md) ---------------------------

    def agent(self) -> Any:
        """The SquackAgent, built once. Its LLM is Baseten with a key, the keyword router without."""
        if self._agent is None:
            from agent.loop import SquackAgent
            from tower.llm import get_llm
            self._agent = SquackAgent(self, get_llm(), emit=self.emit_from_thread)
        return self._agent

    def _remember_loop(self) -> None:
        try:
            self._loop = asyncio.get_running_loop()
            self._loop_thread = threading.current_thread()
        except RuntimeError:
            self._loop = self._loop_thread = None

    def set_ui_mode(self, mode: str) -> None:
        """normal: the hand-laid-out panels. agent: a stage of at most three cards squack fills."""
        mode = mode if mode in ("normal", "agent") else "normal"
        was, self.ui_mode = self.ui_mode, mode
        self.emit_state()
        if mode == "agent" and was != "agent":
            # Do not open on a blank stage: what is going on now, as if it had just happened.
            batch = [event("disruption", self._disruption_payload(d), t=self.sim.t) for d in self.disruptions.values()]
            if self.risk.pairs:
                batch.append(event("risk", self._risk_payload(self.risk), t=self.sim.t))
            self.wake.pending.clear()
            self._stage(batch)

    def _stage(self, batch: list[dict[str, Any]]) -> None:
        from agent.wake import Director
        stage = Director.stage_for(batch, self)
        self.emit(event("stage", stage.payload(), t=self.sim.t))
        self.wake.staged(None, bool(stage.slots))

    async def _speak_reply(self, text: str) -> None:
        """Say the reply aloud when tower/voice.py (other branch) is present; silent otherwise."""
        try:
            from tower.voice import speak_reply  # type: ignore[import-not-found]
        except ImportError:
            return
        try:
            await speak_reply(self, text)
        except Exception:  # noqa: BLE001 - the voice never breaks the answer
            log.exception("speak_reply failed")

    async def _agent_pulse(self) -> None:
        """Once per tick. A wake batch (agent/wake.py) does two things: in agent mode the director
        puts the stage up at once; in both modes squack says what happened, an `answer` with
        for: "event", at most one per EVENT_ANSWER_GAP_S and never while a user turn is in flight
        (the batch waits in a backlog). With a model the agent's turn writes that answer and, in
        agent mode, may replace the stage; without one it is the director's own text and cards."""
        from agent.wake import Director, Stage

        batch = self.wake.poll()
        if batch is not None:
            if self.ui_mode == "agent":
                self._stage(batch)
            self._event_backlog += batch
        elif self.ui_mode == "agent" and self.wake.idle_due():
            self.emit(event("stage", Stage().payload(), t=self.sim.t))
        if not self._event_backlog or self._agent_busy:
            return
        now = self.wake.clock()
        if now - self._event_answer_t < EVENT_ANSWER_GAP_S:
            return
        self._event_answer_t = now
        batch, self._event_backlog = self._event_backlog, []
        agent = self.agent()
        if not agent.has_model:
            stage = Director.stage_for(batch, self)
            self.emit(event("answer", {"turn_id": f"ev-{int(now * 1000) & 0xFFFFFF:06x}", "text": stage.text or "Something changed.",
                                       "cards": stage.slots, "for": "event", "steps": []}, t=self.sim.t))
            await self._speak_reply(stage.text)
            return
        self._remember_loop()

        async def turn() -> None:
            self._agent_busy = True
            try:
                ans = await asyncio.to_thread(agent.handle_events, batch)
            finally:
                self._agent_busy = False
            if ans is None:
                return
            if self.ui_mode == "agent" and ans.cards:
                self.emit(event("stage", Stage(slots=ans.cards, ttl_s=60.0, by="agent", text=ans.text).payload(),
                                t=self.sim.t))
                self.wake.staged(None, True)
            await self._speak_reply(ans.text)

        if self.realtime:
            task = asyncio.create_task(turn())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        else:
            await turn()

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
# "estir direct": the shortened readback. Only when no fix follows "direct".
_DIRECT_POST_RE = re.compile(r"\b((?:[a-z]+\s+){1,3})direct\b(?!\s+(?:to\s+)?[A-Za-z]{3,6}\b(?!\s+\d))")


SURE_FIX = 88.0  # at or above this similarity the heard word is that fix, whatever the route says


def snap_waypoints(text_norm: str, waypoints: list[str], preferred: list[str] | None = None,
                   trust_hint: bool = True) -> str:
    """Stock Whisper never gets made-up fix names right ("ESTIR" -> "at better").

    Replace the lowercase words after "direct" with the closest known waypoint. Waypoints on the
    addressed aircraft's own route are preferred with a lower bar, the way a controller would
    assume. Deterministic, so it lives in tier 1. See docs/02-domain.md, waypoints.

    `trust_hint=False` is for a pilot's readback. There the route is what we EXPECT to hear, so
    trying it first at a bar of 30 turns a wrong fix into the right one ("tulick", cleared PIKAR,
    became PIKAR) and hides the very error Tower exists to catch. A clear match to any real fix
    is taken first, and the hint only rescues what matches nothing.
    """
    if not waypoints or "direct" not in text_norm:
        return text_norm
    from rapidfuzz import fuzz
    names = [w.upper() for w in waypoints]
    pref = [w.upper() for w in (preferred or []) if w.upper() in names]

    def score(heard: str, name: str) -> float:
        cands = [heard.replace(" ", "").upper()] + [w.upper() for w in heard.split()]
        return max(fuzz.ratio(c, name) for c in cands)

    def closest(heard: str) -> str | None:
        everywhere = sorted(((score(heard, n), n) for n in names), reverse=True)
        # A word that IS a known fix stays that fix, controller or pilot. A clearly spoken "ESTIR"
        # was being rewritten to whatever fix the aircraft happened to be routed to.
        if everywhere and everywhere[0][0] >= SURE_FIX:
            return everywhere[0][1]
        pools = ((pref, 30.0), (names, 60.0)) if trust_hint else ((names, 60.0), (pref, 30.0))
        for pool, bar in pools:
            if not pool:
                continue
            ranked = sorted(((score(heard, n), n) for n in pool), reverse=True)
            best, runner = ranked[0], (ranked[1] if len(ranked) > 1 else (0.0, ""))
            if best[0] >= bar and (best[0] - runner[0] >= 5 or len(pool) == 1):
                return best[1]
        return None

    def fix(m: "re.Match[str]") -> str:
        name = closest(m.group(2).strip())
        if name is None:
            return m.group(0)
        tail = " " if m.group(2).endswith(" ") else ""
        return f"{m.group(1)} {name}{tail}"

    def fix_post(m: "re.Match[str]") -> str:
        """ "roger estir direct" -> "roger direct ESTIR", the order the parser and checker know."""
        from tower.parse import COMMAND_KEYWORDS, FILLER, NOT_A_FIX
        skip = FILLER | set(COMMAND_KEYWORDS) | NOT_A_FIX
        words = m.group(1).split()
        kept: list[str] = []
        while len(words) > 1 and words[0] in skip:
            kept.append(words.pop(0))  # filler is not part of a fix name: leave it where it was
        if any(w in skip for w in words):
            return m.group(0)  # "say again direct", "unable direct": not a fix at all
        name = closest(" ".join(words))
        if name is None:
            return m.group(0)
        return " ".join([*kept, "direct", name])

    return _DIRECT_POST_RE.sub(fix_post, _DIRECT_RE.sub(fix, text_norm))


def _item_key(i: Item) -> tuple[str, Any]:
    """An item reduced to what it asks for, so "flight level three eight zero" equals 38,000 ft."""
    if i.type == "altitude":
        return ("altitude", round(float(i.value) * (100.0 if (i.unit or "").upper() == "FL" else 1.0)))
    if i.type == "route":
        return ("route", str(i.value).upper())
    try:
        return (i.type, round(float(i.value)))
    except (TypeError, ValueError):
        return (i.type, str(i.value).upper())


def _said_vs_card(said: list[Item], card: list[Item]) -> tuple[str, str]:
    """("same" | "partial" | "conflict", words for the screen).

    conflict: the same kind of instruction with a different value, a heading of 210 where the card
    says 120. partial: nothing wrong, but something on the card was not said.
    """
    from pilots.readback import say_item

    s = {k[0]: (k, i) for i in said for k in [_item_key(i)]}
    c = {k[0]: (k, i) for i in card for k in [_item_key(i)]}
    clash = [t for t in s if t in c and s[t][0] != c[t][0]]
    if clash:
        return "conflict", ", ".join(say_item(c[t][1]) for t in clash)
    missing = [t for t in c if t not in s]
    if missing:
        return "partial", ", ".join(say_item(c[t][1]) for t in missing)
    return "same", ""


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
