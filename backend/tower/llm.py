"""Baseten LLM access through the OpenAI SDK, plus a deterministic MockLLM for keyless runs.

Environment: BASETEN_API_KEY, EXTRACTOR_MODEL, RESOLVER_MODEL. `get_llm()` returns the real
client when a key is set, otherwise `LLM.mock()`.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from schemas import Extraction, Item
from tower import parse as P
from tower.normalize import phrase_from_items

BASETEN_BASE_URL = "https://inference.baseten.co/v1"
DEFAULT_EXTRACTOR_MODEL = "meta-llama/Llama-3.3-70B-Instruct"
DEFAULT_RESOLVER_MODEL = "meta-llama/Llama-3.3-70B-Instruct"
MAX_RETRIES = 3

EXTRACT_SYSTEM = """You extract structured ATC clearances from one normalized radio transmission.
Return JSON only: {"callsign": "ICAO like ACA123 or null", "items": [{"type": one of
altitude|heading|speed|frequency|squawk|altimeter|runway|route|hold_short|other, "value": number or
string, "unit": one of FL|ft|deg|kt|MHz|hPa|inHg|null, "action": one of descend|climb|maintain|
turn_left|turn_right|fly_heading|direct|speed|contact|squawk|altimeter|cleared_land|cleared_takeoff|
hold_short|line_up_wait|null, "mandatory": bool}]}.
Altitudes under 1000 without a unit are flight levels. Traffic and weather are not mandatory.
Only include what was actually said."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None

    def as_message(self) -> dict[str, Any]:
        """OpenAI-format assistant message to append to the conversation."""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                for tc in self.tool_calls
            ]
        return msg


def _items_from_json(data: Any) -> list[Item]:
    items: list[Item] = []
    for raw in (data or []):
        try:
            items.append(Item.model_validate(raw))
        except Exception:  # noqa: BLE001 - drop malformed items rather than fail the call
            continue
    return items


class LLM:
    """Thin wrapper: extract() with JSON mode, chat() with tools, retries on 429."""

    def __init__(self, api_key: str | None = None, base_url: str = BASETEN_BASE_URL,
                 extractor_model: str | None = None, resolver_model: str | None = None,
                 sleep: Callable[[float], None] = time.sleep, client: Any = None) -> None:
        self.api_key = api_key or os.environ.get("BASETEN_API_KEY")
        self.extractor_model = extractor_model or os.environ.get("EXTRACTOR_MODEL", DEFAULT_EXTRACTOR_MODEL)
        self.resolver_model = resolver_model or os.environ.get("RESOLVER_MODEL", DEFAULT_RESOLVER_MODEL)
        self._sleep = sleep
        if client is not None:
            self.client = client
        else:
            from openai import OpenAI

            self.client = OpenAI(api_key=self.api_key, base_url=base_url)
        self.is_mock = False

    @staticmethod
    def mock() -> MockLLM:
        return MockLLM()

    def _with_retry(self, fn: Callable[[], Any]) -> Any:
        from openai import APIConnectionError, APITimeoutError, RateLimitError

        delay = 1.0
        for attempt in range(MAX_RETRIES + 1):
            try:
                return fn()
            except (RateLimitError, APIConnectionError, APITimeoutError):
                if attempt == MAX_RETRIES:
                    raise
                self._sleep(delay)
                delay *= 2

    def extract(self, text: str, active: list[str] | None = None, transmission_id: str = "") -> Extraction:
        user = text if not active else f"Active callsigns: {', '.join(active)}\nTransmission: {text}"
        resp = self._with_retry(lambda: self.client.chat.completions.create(
            model=self.extractor_model, temperature=0.0, max_tokens=400,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": EXTRACT_SYSTEM}, {"role": "user", "content": user}],
        ))
        content = resp.choices[0].message.content or "{}"
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            data = {}
        return Extraction(transmission_id=transmission_id, callsign=data.get("callsign") or None,
                          items=_items_from_json(data.get("items")), unexplained_words=0, method="llm")

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
             tool_choice: str | None = None, max_tokens: int = 300) -> ChatResponse:
        kwargs: dict[str, Any] = dict(model=self.resolver_model, messages=messages,
                                      temperature=0.0, max_tokens=max_tokens)
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "required"
        resp = self._with_retry(lambda: self.client.chat.completions.create(**kwargs))
        msg = resp.choices[0].message
        calls: list[ToolCall] = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        return ChatResponse(content=msg.content, tool_calls=calls, raw=resp)

    def phrase(self, callsign: str, items: list[Item]) -> str:
        """Deterministic phraseology; kept on LLM for interface symmetry with future variety."""
        return phrase_from_items(callsign, items)


class MockLLM:
    """Keyless stand-in. extract() is the grammar parser; chat() follows a scripted resolver policy.

    The policy reads the resolver's JSON context (first user message) and the tool results so far:
    relisten first when speech confidence is low, active_aircraft when the callsign is in doubt,
    frequency_history for a partial readback, watch when the altitude is still ambiguous, then a
    terminal action. It never needs more than 4 calls.
    """

    is_mock = True
    extractor_model = "mock"
    resolver_model = "mock"

    def extract(self, text: str, active: list[str] | None = None, transmission_id: str = "") -> Extraction:
        ext = P.parse(text, active, "unknown", transmission_id)
        ext.method = "llm"
        return ext

    def phrase(self, callsign: str, items: list[Item]) -> str:
        return phrase_from_items(callsign, items)

    # -- scripted resolver policy ----------------------------------------------------------------

    @staticmethod
    def _context(messages: list[dict[str, Any]]) -> dict[str, Any]:
        for m in messages:
            if m.get("role") == "user":
                try:
                    return json.loads(m.get("content") or "{}")
                except json.JSONDecodeError:
                    return {}
        return {}

    @staticmethod
    def _tool_results(messages: list[dict[str, Any]]) -> dict[str, Any]:
        """name -> parsed result for every tool message so far."""
        names: dict[str, str] = {}
        for m in messages:
            if m.get("role") == "assistant":
                for tc in m.get("tool_calls") or []:
                    names[tc["id"]] = tc["function"]["name"]
        out: dict[str, Any] = {}
        for m in messages:
            if m.get("role") == "tool":
                name = names.get(m.get("tool_call_id", ""), m.get("name", ""))
                try:
                    out[name] = json.loads(m.get("content") or "null")
                except json.JSONDecodeError:
                    out[name] = m.get("content")
        return out

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
             tool_choice: str | None = None, max_tokens: int = 300) -> ChatResponse:
        ctx = self._context(messages)
        done = self._tool_results(messages)
        n_calls = len(done)
        call_id = f"mock-{n_calls + 1}"

        def call(name: str, **args: Any) -> ChatResponse:
            return ChatResponse(content=None, tool_calls=[ToolCall(call_id, name, args)])

        verdict = ctx.get("verdict") or {}
        tx = ctx.get("transmission") or {}
        clearance = ctx.get("clearance") or {}
        error = verdict.get("error_type")
        callsign = clearance.get("callsign", "")
        expected = verdict.get("expected") or clearance.get("items") or []
        exp_values = {str(i.get("value")) for i in expected}
        heard_values = {str(i.get("value")) for i in verdict.get("heard") or []}
        budget_left = 4 - n_calls

        # 1. Investigate, cheapest evidence first, while at least one call is left for the verdict.
        if budget_left > 1:
            if "relisten" not in done and (tx.get("asr_confidence", 1.0) < 0.8 or tx.get("n_best")):
                return call("relisten", transmission_id=tx.get("id", ""))
            if "active_aircraft" not in done and (error == "wrong_aircraft" or ctx.get("similar_callsigns")):
                return call("active_aircraft")
            if "frequency_history" not in done and verdict.get("result") == "partial" or \
                    "frequency_history" not in done and error in ("omitted_item", "ack_only") and budget_left > 2:
                return call("frequency_history", callsign=callsign, n=3)

        # 2. Decide from the evidence.
        hyps: list[str] = list(done.get("relisten") or [])
        hyps_text = " ".join(hyps)
        expected_seen = any(v in hyps_text.split() or v in hyps_text for v in exp_values)
        heard_seen = any(v in hyps_text for v in heard_values)

        if error == "wrong_aircraft":
            active = done.get("active_aircraft")
            heard_cs = (ctx.get("readback_callsign") or "")
            if isinstance(active, list):
                active_names = {a.get("callsign") for a in active}
                if heard_cs and heard_cs not in active_names:
                    return call("dismiss", reason=f"{heard_cs} is not on frequency; likely a mishearing of {callsign}")
                return call("raise_alert", reason=f"{heard_cs or 'another aircraft'} read back a clearance issued to {callsign}")
            return call("raise_alert", reason=f"another aircraft read back a clearance issued to {callsign}")

        if verdict.get("result") == "partial" or error in ("omitted_item", "ack_only"):
            history = done.get("frequency_history")
            if isinstance(history, list) and any(
                    any(str(v) in json.dumps(h) for v in exp_values) for h in history if h.get("speaker") == "pilot"):
                return call("dismiss", reason="omitted item was read back in an earlier exchange")
            if expected_seen:
                return call("dismiss", reason="re-listen shows the full readback")
            return call("raise_alert", reason=verdict.get("reason") or "mandatory item missing from readback")

        if hyps:
            if expected_seen and heard_seen:
                watchable = any(i.get("type") in ("altitude", "heading", "route") for i in expected)
                if watchable and "watch" not in done:
                    return call("watch", callsign=callsign, seconds=60)
                return call("mark_uncertain", reason="hypotheses split between expected and heard values")
            if expected_seen:
                return call("dismiss", reason="re-listen supports the expected value; original hearing was noise")
            return call("raise_alert", reason=f"re-listen confirms the readback differs: {verdict.get('reason', '')}".strip())

        if tx.get("asr_confidence", 1.0) < 0.5:
            return call("mark_uncertain", reason="speech too unclear to judge and no alternatives available")
        return call("raise_alert", reason=verdict.get("reason") or "readback does not match")


def get_llm() -> LLM | MockLLM:
    """Real Baseten client when BASETEN_API_KEY is set, else the deterministic mock."""
    if os.environ.get("BASETEN_API_KEY"):
        return LLM()
    return LLM.mock()
