"""Hand-rolled tool-calling loop. Max 4 tool calls, ~5 s, always ends in one terminal action."""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any, NamedTuple

from schemas import OpenClearance, ResolverStep, Transmission, Verdict
from tower.check import correction_phrase
from tower.resolver.tools import (
    TERMINAL_TOOLS,
    TOOL_SCHEMAS,
    ResolverTools,
    WatchRequest,
    summarize,
)

MAX_TOOL_CALLS = 4
BUDGET_S = 5.0

SYSTEM_PROMPT = """You are Tower's resolver: a second set of ears on an air traffic control frequency.
Tier 1 flagged a pilot readback as AMBIGUOUS. Decide whether to alert the controller.

Domain rules:
- A readback must match the clearance in MEANING, not words. "down to flight level two four zero"
  matches "descend and maintain flight level two four zero". "one two four six five" matches
  "contact departure one two four decimal six five".
- Mandatory readback items: altitudes and flight levels, headings, speeds, frequencies, squawk codes,
  altimeter settings, runway instructions (land, take off, hold short, line up, cross), route clearances.
  Traffic information and weather are NOT mandatory; "roger" is fine for those.
- Error kinds: wrong value; wrong runway (incl. left/right); wrong direction (climb vs descend, left vs
  right); wrong unit (flight level vs feet, heading read back as speed); omitted mandatory item;
  acknowledgement only ("roger", "wilco") where a full readback is required; wrong aircraft (another
  callsign read back the clearance); missing readback.
- Pilots shorten callsigns ("one two three", "canada one two three"). Similar callsigns on one
  frequency (ACA123 / ACA133) cause real errors: check active_aircraft before deciding wrong_aircraft.
- Speech recognition mishears digits. Never alert on a value if the expected value appears in any
  relisten hypothesis; prefer watch(callsign, seconds) so radar settles what the aircraft actually does.
- False alarms cost trust. Alert only when the evidence says the readback was wrong.

Budget: at most 4 tool calls total, about 5 seconds. Use relisten, active_aircraft,
frequency_history, aircraft_state, sanity_check to gather evidence, then finish with EXACTLY ONE
terminal action: raise_alert(reason), dismiss(reason), mark_uncertain(reason), or watch(callsign, seconds).
Reasons are one plain-English line for the controller's screen."""


class Resolution(NamedTuple):
    verdict: Verdict
    steps: list[ResolverStep]
    watch_request: WatchRequest | None


class Resolver:
    """`Resolver(llm, tools).resolve(clearance, verdict, transmission)` -> Resolution."""

    def __init__(self, llm: Any, tools: ResolverTools, max_tool_calls: int = MAX_TOOL_CALLS,
                 budget_s: float = BUDGET_S, clock: Callable[[], float] = time.monotonic) -> None:
        self.llm = llm
        self.tools = tools
        self.max_tool_calls = max_tool_calls
        self.budget_s = budget_s
        self.clock = clock

    # -- context -----------------------------------------------------------------------------------

    @staticmethod
    def _context(clearance: OpenClearance, verdict: Verdict, tx: Transmission,
                 extra: dict[str, Any] | None) -> str:
        ctx = {
            "clearance": clearance.model_dump(),
            "verdict": verdict.model_dump(),
            "transmission": {"id": tx.id, "text_norm": tx.text_norm, "text_raw": tx.text_raw,
                             "asr_confidence": tx.asr_confidence, "n_best": tx.n_best,
                             "speaker": tx.speaker},
        }
        if extra:
            ctx.update(extra)
        return json.dumps(ctx)

    # -- loop --------------------------------------------------------------------------------------

    def resolve(self, clearance: OpenClearance, verdict: Verdict, tx: Transmission,
                extra_context: dict[str, Any] | None = None) -> Resolution:
        t0 = self.clock()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._context(clearance, verdict, tx, extra_context)},
        ]
        steps: list[ResolverStep] = []

        for n in range(1, self.max_tool_calls + 1):
            last_slot = n == self.max_tool_calls
            out_of_time = self.clock() - t0 > self.budget_s
            if out_of_time:
                return self._finish(clearance, verdict, tx, steps, n, "mark_uncertain",
                                    {"reason": "resolver ran out of time"})
            try:
                resp = self.llm.chat(messages, tools=TOOL_SCHEMAS)
            except Exception as e:  # noqa: BLE001 - never silent
                return self._finish(clearance, verdict, tx, steps, n, "mark_uncertain",
                                    {"reason": f"resolver model error: {type(e).__name__}"})
            if not resp.tool_calls:
                reason = (resp.content or "").strip().splitlines()[0][:200] if resp.content else "model gave no action"
                return self._finish(clearance, verdict, tx, steps, n, "mark_uncertain", {"reason": reason})
            call = resp.tool_calls[0]
            if call.name in TERMINAL_TOOLS:
                return self._finish(clearance, verdict, tx, steps, n, call.name, call.arguments)
            if last_slot:
                return self._finish(clearance, verdict, tx, steps, n, "mark_uncertain",
                                    {"reason": f"out of tool budget after {call.name}"})
            try:
                result = self.tools.execute(call.name, call.arguments)
            except Exception as e:  # noqa: BLE001
                result = {"error": f"{type(e).__name__}: {e}"}
            steps.append(ResolverStep(clearance_id=clearance.id, step=n, tool=call.name,
                                      args=call.arguments, result_summary=summarize(call.name, result)))
            messages.append(resp.as_message())
            messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                             "content": json.dumps(result, default=str)})

        return self._finish(clearance, verdict, tx, steps, self.max_tool_calls + 1, "mark_uncertain",
                            {"reason": "out of tool budget"})

    # -- terminal actions --------------------------------------------------------------------------

    def _finish(self, clearance: OpenClearance, verdict: Verdict, tx: Transmission,
                steps: list[ResolverStep], step_no: int, action: str, args: dict[str, Any]) -> Resolution:
        reason = str(args.get("reason") or "").strip()
        final = verdict.model_copy(deep=True)
        final.decided_by = "resolver"
        final.readback_transmission_id = final.readback_transmission_id or tx.id
        watch: WatchRequest | None = None

        if action == "raise_alert":
            final.result = "mismatch" if final.error_type != "omitted_item" or not final.heard else "partial"
            final.confidence = 0.85
            final.reason = reason or final.reason
            final.correction_phrase = final.correction_phrase or correction_phrase(clearance, final)
            summary = f"ALERT: {final.reason}"
        elif action == "dismiss":
            final.result = "match"
            final.error_type = None
            final.confidence = 0.8
            final.reason = reason or "Resolver dismissed"
            final.correction_phrase = None
            summary = f"dismissed: {final.reason}"
        elif action == "watch":
            seconds = float(args.get("seconds") or 60)
            callsign = str(args.get("callsign") or clearance.callsign)
            watch = WatchRequest(clearance_id=clearance.id, callsign=callsign, seconds=seconds)
            final.result = "ambiguous"
            final.confidence = 0.5
            final.reason = reason or f"Watching {callsign} on radar for {int(seconds)} s"
            summary = f"watching {callsign} for {int(seconds)} s"
        else:
            action = "mark_uncertain"
            final.result = "ambiguous"
            final.confidence = 0.5
            final.reason = reason or "Resolver could not decide"
            summary = f"uncertain: {final.reason}"

        steps.append(ResolverStep(clearance_id=clearance.id, step=step_no, tool=action,
                                  args=args, result_summary=summary))
        return Resolution(final, steps, watch)
