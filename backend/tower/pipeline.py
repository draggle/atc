"""TowerCore: the synchronous façade the integrator drives.

on_transmission(tx, active_callsigns, states) -> events
tick(now, states) -> events (timeouts and radar conformance)
sim_command_for_readback(extraction) -> SimCommand
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from schemas import (
    AircraftState,
    Extraction,
    OpenClearance,
    SimCommand,
    Speaker,
    Transmission,
    Verdict,
    event,
)
from tower import callsign as CS
from tower import check as CK
from tower import parse as P
from tower.commands import items_to_sim_command
from tower.conform import ConformanceMonitor
from tower.llm import LLM, MockLLM, get_llm
from tower.normalize import normalize
from tower.resolver.agent import Resolver
from tower.resolver.tools import ResolverTools, clearance_brief
from tower.state import State, StateStore

_RESULT_TO_STATUS = {"match": "matched", "mismatch": "mismatched", "partial": "partial",
                     "missing": "missing", "ambiguous": "uncertain"}


class TowerCore:
    def __init__(self, llm: LLM | MockLLM | None = None, checker_model: CK.CheckerModel | None = None,
                 timeout_s: float = 25.0, waypoints: dict[str, tuple[float, float]] | None = None,
                 relisten: Callable[[str], list[str]] | None = None,
                 use_llm_fallback: bool = True) -> None:
        self.llm = llm if llm is not None else get_llm()
        self.checker_model = checker_model if checker_model is not None else CK.checker_from_env()
        self.store = StateStore(timeout_s=timeout_s)
        self.conformance = ConformanceMonitor(waypoints)
        self.use_llm_fallback = use_llm_fallback
        self._transmissions: dict[str, Transmission] = {}
        self._states: dict[str, AircraftState] = {}
        self._active: list[str] = []
        self.last_extraction: Extraction | None = None
        self.verdicts: list[Verdict] = []
        tools = ResolverTools(
            relisten=relisten or self._relisten_from_nbest,
            active_aircraft=self._active_aircraft,
            frequency_history=self._frequency_history,
            aircraft_state=lambda cs: self._states.get(cs),
            waypoints={w.upper() for w in (waypoints or {})},
        )
        self.resolver = Resolver(self.llm, tools)

    # -- tool backends ---------------------------------------------------------------------------

    def _relisten_from_nbest(self, transmission_id: str) -> list[str]:
        tx = self._transmissions.get(transmission_id)
        if tx is None:
            return []
        hyps = [tx.text_norm] + [h for h in tx.n_best if h != tx.text_norm]
        return hyps[:5]

    def _active_aircraft(self) -> list[dict[str, Any]]:
        names = sorted(set(self._active) | set(self.store.active_callsigns()))
        return [{"callsign": cs, "open_clearances": [clearance_brief(c) for c in self.store.open_clearances(cs)],
                 "state": self.store.state_of(cs).value} for cs in names]

    def _frequency_history(self, callsign: str, n: int) -> list[dict[str, Any]]:
        return [{"speaker": ex.transmission.speaker, "text": ex.transmission.text_norm,
                 "items": [i.model_dump() for i in ex.extraction.items], "clearance_id": ex.clearance_id}
                for ex in self.store.history(callsign, n)]

    # -- public API ------------------------------------------------------------------------------

    def sim_command_for_readback(self, extraction: Extraction) -> SimCommand:
        return items_to_sim_command(extraction.items)

    def on_transmission(self, tx: Transmission, active_callsigns: list[str] | None = None,
                        states: list[AircraftState] | None = None) -> list[dict[str, Any]]:
        if active_callsigns is not None:
            self._active = list(active_callsigns)
        if states:
            self._states = {s.callsign: s for s in states}
        if not tx.text_norm:
            tx.text_norm = normalize(tx.text_raw)
        tx.n_best = [normalize(h) for h in tx.n_best]
        self._transmissions[tx.id] = tx
        active = self._active or None

        speaker: Speaker = tx.speaker
        if speaker == "unknown":
            speaker = self._infer_speaker(tx.text_norm, active)
            tx.speaker = speaker

        llm = self.llm if self.use_llm_fallback else None
        ext = P.parse_with_fallback(tx.text_norm, active, speaker, llm, transmission_id=tx.id)
        self.last_extraction = ext

        if speaker == "controller":
            return self._on_controller(tx, ext)
        return self._on_pilot(tx, ext, active)

    def tick(self, now: float, states: list[AircraftState] | None = None) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for c in self.store.tick(now):
            v = CK.missing_verdict(c)
            self.verdicts.append(v)
            events.append(event("alert", v, t=now))
            events.append(event("clearance_updated", c, t=now))
        if states:
            self._states = {s.callsign: s for s in states}
            for v in self.conformance.tick(states, now):
                c = self.store.get(v.clearance_id)
                if c is not None:
                    v.correction_phrase = CK.correction_phrase(c, v)
                    self.store.resolve(c.id, "mismatched")
                    events.append(event("clearance_updated", c, t=now))
                self.verdicts.append(v)
                events.append(event("alert", v, t=now))
        return events

    # -- internals -------------------------------------------------------------------------------

    def _infer_speaker(self, text_norm: str, active: list[str] | None) -> Speaker:
        toks = text_norm.split()
        if not toks:
            return "unknown"
        if CS.is_callsign_token(toks[0]):
            return "controller"
        if CS.is_callsign_token(toks[-1]) or toks[-1].isdigit():
            return "pilot"
        return "pilot" if self.store.all_open() else "controller"

    def _on_controller(self, tx: Transmission, ext: Extraction) -> list[dict[str, Any]]:
        callsign = ext.callsign
        if callsign is None and len(self._active) == 1:
            callsign = self._active[0]
        if callsign is None or not ext.items:
            self.store.record(callsign, tx, ext)
            return []
        c = OpenClearance(id=self.store.next_id(), callsign=callsign, items=ext.items, issued_at=tx.t_end,
                          timeout_s=self.store.timeout_s, source_transmission_id=tx.id)
        self.store.open(c)
        self.store.record(callsign, tx, ext, c.id)
        events = [event("clearance_opened", c, t=tx.t_end)]
        return events

    def _on_pilot(self, tx: Transmission, ext: Extraction, active: list[str] | None) -> list[dict[str, Any]]:
        similar_flag = False
        if active and ext.callsign:
            s = P.callsign_is_ambiguous(tx.text_norm, active, "pilot")
            if s.ambiguous and s.runner_up:
                # prefer whichever of the pair is actually expecting a readback
                best_open = bool(self.store.open_clearances(s.best or ""))
                runner_open = bool(self.store.open_clearances(s.runner_up))
                if runner_open and not best_open:
                    ext.callsign = s.runner_up
                similar_flag = best_open and runner_open

        clearance = self.store.on_pilot_transmission(ext, tx)
        if clearance is None:
            return []

        v = CK.check(clearance, ext, tx, model=self.checker_model, active=active)
        if similar_flag and v.result != "ambiguous":
            v.result = "ambiguous"
            v.confidence = 0.5
            v.reason = f"{v.reason}; similar callsigns on frequency"

        events: list[dict[str, Any]] = []
        if v.result == "ambiguous":
            extra = {"readback_callsign": ext.callsign,
                     "similar_callsigns": self.store.similar_callsign_warnings(active)}
            res = self.resolver.resolve(clearance, v, tx, extra_context=extra)
            for step in res.steps:
                events.append(event("resolver_step", step, t=tx.t_end))
            v = res.verdict
            if res.watch_request is not None:
                self.conformance.watch(clearance, ext.items or clearance.items, now=tx.t_end)

        self.verdicts.append(v)
        status = _RESULT_TO_STATUS[v.result]
        self.store.resolve(clearance.id, status)  # type: ignore[arg-type]
        if v.result in ("mismatch", "partial"):
            events.append(event("alert", {**v.model_dump(), "audio_ref": tx.audio_ref}, t=tx.t_end))
        elif v.result == "match":
            # correct readback: verify on radar that the aircraft actually does it
            self.conformance.watch(clearance, ext.items or clearance.items, now=tx.t_end)
        events.append(event("clearance_updated", clearance, t=tx.t_end))
        return events

    # -- convenience for the integrator and tests ---------------------------------------------------

    def state_of(self, callsign: str) -> State:
        return self.store.state_of(callsign)
