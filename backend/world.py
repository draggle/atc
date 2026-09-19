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
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

import numpy as np

from pilots.pilot import PilotFleet, PilotResponse
from pilots.radio import apply_to_file
from pilots.tts import TTS
import importlib

C = importlib.import_module("planner.cards")
PL = importlib.import_module("planner.plan")
from planner.conflicts import closest_approach
from schemas import (
    AircraftState, Disruption, FlightSpec, InstructionCard, OpenClearance, Plan, Scenario,
    Scoreboard, SimCommand, Transmission, Waypoint, event,
)
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
                            "source": "sim"})
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
        # Lifecycle: nothing moves until start(). idle -> ready -> running <-> paused -> ended.
        self.lifecycle: str = "idle"
        self.world_id = 0  # bumps on every load so the screen can drop the previous world's state
        self._base_scenario: Scenario | None = None  # what reset() returns to
        self._lock = asyncio.Lock()
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ setup

    def load(self, name: str) -> None:
        sc = SC.load(name)
        self._load_scenario(sc)

    def _load_scenario(self, sc: Scenario) -> None:
        """Build the world and its plan. Does not start the clock: lifecycle becomes "ready"."""
        self._base_scenario = sc.model_copy(deep=True)
        self.world_id += 1
        self.lifecycle = "ready"
        self.alert_latencies.clear()
        self.tier1_latencies.clear()
        self.transmissions = self.matches = self.alerts = self.false_alarms = 0
        self.errors_injected = self.errors_caught = 0
        self.scenario = sc
        self.sim = Simulator(sc)
        self.core = TowerCore(waypoints={w.name: (w.x_nm, w.y_nm) for w in sc.waypoints})
        self.fleet = PilotFleet(error_rate=self.error_rate, seed=sc.seed, tts=self.tts,
                                synthesize=self.synthesize)
        self.monitor = SeparationMonitor()
        self.cards.clear()
        self.card_by_clearance.clear()
        self.clearance_meta.clear()
        self.matched_at.clear()
        self.pending.clear()
        self.buffer_nm = sc.separation_buffer_nm
        self.noise = sc.noise_level
        self.baseline = PL.baseline(sc)
        self.plan = PL.plan(sc, sc.waypoints, sc.zones, self.buffer_nm, time_budget_s=1.0)
        # Planned savings are frozen at the initial plan: after a replan the plan holds remaining
        # distance while the baseline holds full routes, so a live difference would be wrong.
        self.planned_miles_saved = self.baseline.total_distance_nm - self.plan.total_distance_nm
        self.planned_time_saved_s = self.baseline.total_time_s - self.plan.total_time_s
        self.last_replan_t = 0.0
        self.emit_state()
        self.emit_plan(self.plan, trigger="initial")
        for card in C.cards_from_plan(self.plan, None, now_t=0.0, states=self.sim.aircraft()):
            self._add_card(card)
        self.emit(event("radar", {"aircraft": [a.model_dump() for a in self.sim.aircraft()], "t": 0.0}, t=0.0))
        self.emit_scoreboard()

    # ------------------------------------------------------------------ emitters

    def emit_state(self) -> None:
        sc = self.scenario
        self.emit(event("state", {
            "scenario": sc.name if sc else None,
            "tower_enabled": self.tower_enabled,
            "auto_speak": self.auto_speak,
            "t": self.sim.t,
            "waypoints": [w.model_dump() for w in (sc.waypoints if sc else [])],
            "zones": [z.model_dump() for z in self.sim.zones],
            "sector_nm": sc.sector_nm if sc else 200.0,
            "buffer_nm": self.buffer_nm, "error_rate": self.error_rate, "noise": self.noise,
            "speed": self.speed,
            "lifecycle": self.lifecycle,
            "world_id": self.world_id,
            "scenarios": scenario_catalog(),
            "watching": self.watching(),
        }, t=self.sim.t))

    def watching(self) -> list[str]:
        """Callsigns radar verification is currently watching."""
        return sorted({w.callsign for w in self.core.conformance.watches})

    def emit_plan(self, plan: Plan, trigger: str, changed: list[str] | None = None) -> None:
        payload = plan.model_dump()
        if self.baseline is not None:
            payload["baseline_paths"] = [p.model_dump() for p in self.baseline.paths]
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
        self.emit(event("instruction_card", card, t=self.sim.t))

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
        self.auto_speak = enabled
        self.emit_state()

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
                self.monitor.observe(list(self.sim.active.values()), now)
                states = self.sim.aircraft()
                self._emit_core_events(self.core.tick(now, states))
                for cid, t_match in list(self.matched_at.items()):
                    if now - t_match >= CARD_VERIFY_S and not self.core.conformance.watching(
                            self.core.store.get(cid).callsign if self.core.store.get(cid) else ""):
                        self._set_card_status(cid, "verified")
                        del self.matched_at[cid]
                if now - self.last_replan_t >= REPLAN_EVERY_S:
                    self._replan("periodic")
            now = self.sim.t
            states = self.sim.aircraft()
            self.emit(event("radar", {"aircraft": [a.model_dump() for a in states], "t": now,
                                      "watching": self.watching()}, t=now))
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

    # ------------------------------------------------------------------ planning

    def _replan(self, trigger: str, disruption: Disruption | None = None) -> None:
        if self.plan is None or self.scenario is None:
            return
        states = self.sim.aircraft()
        prev = self.plan
        self.plan = PL.replan(prev, states, self.scenario.waypoints, self.sim.zones, self.buffer_nm,
                              disruption=disruption, flights=self.scenario.flights, now_t=self.sim.t,
                              time_budget_s=0.5)
        self.plan.trigger = trigger
        self.last_replan_t = self.sim.t
        new_cards = C.cards_from_plan(self.plan, prev, now_t=self.sim.t, states=states)
        new_cards += C.followup_cards(self.plan, states, self.sim.t)
        changed = sorted({c.callsign for c in new_cards})
        self.emit_plan(self.plan, trigger=trigger, changed=changed)
        for card in new_cards:
            # drop a pending card for the same callsign superseded by this one
            for old in list(self.cards.values()):
                if old.callsign == card.callsign and old.status == "pending":
                    del self.cards[old.id]
            self._add_card(card)

    def add_disruption(self, kind: str, x_nm: float, y_nm: float) -> Disruption:
        if kind == "intruder":
            # aim it through the sector centre
            cx = cy = 0.0  # sector is centred on the origin
            hdg = float(np.degrees(np.arctan2(cx - x_nm, cy - y_nm)) % 360)
            d = Disruption(id=f"VIPER{len(self.sim.active) + 10}", kind="intruder", x_nm=x_nm, y_nm=y_nm,
                           hdg_deg=hdg, gs_kt=550)
            pred = PL.predict_intruder(x_nm, y_nm, hdg, 550, 30000, self.sim.t)
            d.predicted_path = [(float(r[0]), float(r[1]), float(r[2])) for r in pred[::6]]
        else:
            d = Disruption(id=f"STORM{len(self.sim.zones) + 1}", kind="storm", x_nm=x_nm, y_nm=y_nm,
                           radius_nm=15.0)
        self.sim.add_disruption(d)
        self.emit(event("disruption", d, t=self.sim.t))
        self.emit_state()
        self._replan(f"{kind} added", disruption=d)
        return d

    # ------------------------------------------------------------------ radio: controller side

    def _new_tx(self, text_raw: str, speaker: str, audio_ref: str = "", conf: float = 1.0,
                n_best: list[str] | None = None, text_stock: str | None = None,
                duration_s: float = 3.0) -> Transmission:
        wps = list(self.sim.waypoints)
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
        prompt = build_prompt([_spoken(cs) for cs in self.sim.active], list(self.sim.waypoints))
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
                self._link_card(card, c.id)
            self._schedule_pilot(c, heard_ok=(conf >= 0.5))

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
            if phrase:
                await self._auto_correct(c, phrase)

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
        n = self.scenario.sector_nm if self.scenario else 200.0
        d = self.add_disruption(kind, x_nm if x_nm is not None else -n * 0.15,
                                y_nm if y_nm is not None else -n * 0.35)
        return f"added {kind} {d.id} at ({d.x_nm:.0f}, {d.y_nm:.0f})"

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


def _route_of(world: "World", text_norm: str) -> list[str]:
    """Remaining route of the aircraft a transmission addresses, for the waypoint prior."""
    from tower.callsign import snap
    active = list(world.sim.active)
    if not active:
        return []
    s = snap(text_norm, active)
    a = world.sim.get(s.best) if s.best else None
    return list(a.route) if a else []


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
