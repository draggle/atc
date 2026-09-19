"""AI pilots: one per simulated aircraft (07 §9).

Flow per clearance:
  true clearance (structured) + whether Tower heard the controller cleanly
    -> maybe inject one taxonomy error
    -> readback text in the dataset convention
    -> TTS + radio effect -> data/pilot_audio/<id>.wav  (fed to Tower's ingest, never the text)
    -> SimCommand for WHAT THE PILOT SAID (the plane obeys the readback, not the clearance)
    -> ground-truth line appended to data/ground_truth.jsonl

Who flies what:
  correct / wrong_value / wrong_direction / wrong_unit / wrong_runway  -> this plane flies the spoken items
  omitted_item        -> this plane flies only what it read back
  ack_only            -> pilot heard correctly; flies the true clearance
  wrong_aircraft      -> the OTHER plane (acting_callsign) flies the clearance; this plane does nothing
  missing_readback    -> nobody flies anything (the message never landed)
  say_again           -> nothing
"""
from __future__ import annotations

import json
import os
import random
import time
import zlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from schemas import ErrorType, Item, OpenClearance, SimCommand

from .errors import inject_error
from .radio import radio_effect
from .readback import build_readback, roger, say_again
from .tts import TTS

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = Path(os.environ.get("TOWER_DATA_DIR", REPO_ROOT / "data"))

ResponseKind = Literal["readback", "ack", "say_again", "silent", "correction"]


# ---------------------------------------------------------------------------
# Items -> simulator commands
# ---------------------------------------------------------------------------


def item_to_sim_command(item: Item) -> SimCommand:
    """07 §4 'Clearance to simulator command'. Frequency, squawk, altimeter, runway -> none."""
    if item.type == "altitude":
        ft = float(item.value) * (100.0 if item.unit == "FL" else 1.0)
        return SimCommand(kind="altitude", value=ft)
    if item.type == "heading":
        return SimCommand(kind="heading", value=float(item.value) % 360.0 or 360.0)
    if item.type == "route":
        return SimCommand(kind="direct", value=str(item.value).upper())
    if item.type == "speed":
        return SimCommand(kind="speed", value=float(item.value))
    return SimCommand(kind="none")


def items_to_sim_commands(items: list[Item]) -> list[SimCommand]:
    return [c for c in (item_to_sim_command(it) for it in items) if c.kind != "none"]


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class PilotResponse(BaseModel):
    id: str
    clearance_id: str
    callsign: str  # who the controller addressed (ICAO)
    acting_callsign: str  # who actually speaks and flies; differs only for wrong_aircraft
    kind: ResponseKind
    text: str | None  # dataset convention; None when there is no transmission
    spoken_callsign: str | None  # ICAO form of the callsign the pilot said, None if no transmission
    spoken_items: list[Item] = Field(default_factory=list)
    injected_error: ErrorType | None = None
    error_description: str = ""
    sim_command: SimCommand = Field(default_factory=lambda: SimCommand(kind="none"))
    sim_commands: list[SimCommand] = Field(default_factory=list)  # all motion commands, in order
    audio_path: str | None = None
    audio_duration_s: float | None = None
    voice: str | None = None
    noise_level: float = 0.2
    t: float = 0.0

    @property
    def transmits(self) -> bool:
        return self.text is not None


# ---------------------------------------------------------------------------
# Pilot
# ---------------------------------------------------------------------------


class AIPilot:
    def __init__(
        self,
        callsign: str,
        voice: str | None = None,
        error_rate: float = 0.1,
        rng: random.Random | None = None,
        tts: TTS | None = None,
        data_dir: str | Path | None = None,
        synthesize: bool = True,
        error_weights: dict[str, float] | None = None,
        shorten: bool = True,
    ):
        self.callsign = callsign.upper()
        self.error_rate = error_rate
        self.rng = rng or random.Random(zlib.crc32(self.callsign.encode()))
        self.tts = tts
        self.voice = voice or (tts.voice_for(self.callsign) if tts else None)
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self.synthesize = synthesize
        self.error_weights = error_weights
        self.shorten = shorten
        self._n = 0

    # -- public ------------------------------------------------------------

    def respond(
        self,
        clearance: OpenClearance,
        heard_ok: bool = True,
        active_callsigns: list[str] | None = None,
        noise_level: float = 0.2,
        error_type: ErrorType | None = None,
        force_error: bool | None = None,
    ) -> PilotResponse:
        """Respond to a clearance addressed to this pilot.

        error_type forces a specific taxonomy error; force_error=True/False overrides the
        error_rate coin flip. Both exist for tests and scripted demos.
        """
        active = [c.upper() for c in (active_callsigns or [])]
        rid = self._next_id(clearance)
        true_items = [it.model_copy() for it in clearance.items]

        if not heard_ok:
            resp = PilotResponse(
                id=rid, clearance_id=clearance.id, callsign=self.callsign, acting_callsign=self.callsign,
                kind="say_again", text=say_again(self.callsign, self.rng), spoken_callsign=self.callsign,
                noise_level=noise_level,
            )
            return self._finish(resp, clearance)

        make_error = force_error if force_error is not None else (
            error_type is not None or self.rng.random() < self.error_rate
        )
        spoken_items, spoken_cs, etype, desc = true_items, self.callsign, None, ""
        if make_error:
            spoken_items, spoken_cs, etype, desc = inject_error(
                true_items, self.callsign, active, self.rng, self.error_weights, error_type=error_type
            )

        if etype == "missing_readback":
            resp = PilotResponse(
                id=rid, clearance_id=clearance.id, callsign=self.callsign, acting_callsign=self.callsign,
                kind="silent", text=None, spoken_callsign=None, spoken_items=[],
                injected_error=etype, error_description=desc, noise_level=noise_level,
            )
            return self._finish(resp, clearance)

        if etype == "ack_only":
            text = roger(self.callsign, self.rng)
            cmds = items_to_sim_commands(true_items)  # heard it fine, flies it
            resp = PilotResponse(
                id=rid, clearance_id=clearance.id, callsign=self.callsign, acting_callsign=self.callsign,
                kind="ack", text=text, spoken_callsign=self.callsign, spoken_items=[],
                injected_error=etype, error_description=desc,
                sim_command=cmds[0] if cmds else SimCommand(kind="none"), sim_commands=cmds,
                noise_level=noise_level,
            )
            return self._finish(resp, clearance)

        text = build_readback(spoken_cs, spoken_items, self.rng, shorten=self.shorten)
        cmds = items_to_sim_commands(spoken_items)
        resp = PilotResponse(
            id=rid, clearance_id=clearance.id, callsign=self.callsign,
            acting_callsign=spoken_cs,  # for wrong_aircraft the other plane flies it
            kind="readback", text=text, spoken_callsign=spoken_cs, spoken_items=spoken_items,
            injected_error=etype, error_description=desc,
            sim_command=cmds[0] if cmds else SimCommand(kind="none"), sim_commands=cmds,
            noise_level=noise_level,
        )
        return self._finish(resp, clearance)

    def respond_to_correction(self, clearance: OpenClearance, noise_level: float = 0.2) -> PilotResponse:
        """After the controller corrects us: a full, correct readback with the full callsign."""
        rid = self._next_id(clearance)
        items = [it.model_copy() for it in clearance.items]
        text = build_readback(self.callsign, items, self.rng, shorten=False, callsign_style="full")
        cmds = items_to_sim_commands(items)
        resp = PilotResponse(
            id=rid, clearance_id=clearance.id, callsign=self.callsign, acting_callsign=self.callsign,
            kind="correction", text=text, spoken_callsign=self.callsign, spoken_items=items,
            sim_command=cmds[0] if cmds else SimCommand(kind="none"), sim_commands=cmds,
            noise_level=noise_level,
        )
        return self._finish(resp, clearance)

    # -- internals ---------------------------------------------------------

    def _next_id(self, clearance: OpenClearance) -> str:
        self._n += 1
        return f"{clearance.id}-{self.callsign}-{self._n}"

    def _finish(self, resp: PilotResponse, clearance: OpenClearance) -> PilotResponse:
        resp.t = time.time()
        resp.voice = self.voice
        if resp.text and self.synthesize and self.tts is not None:
            self._speak(resp)
        self._log(resp, clearance)
        return resp

    def _speak(self, resp: PilotResponse) -> None:
        from tower.audio import float_to_wav, read_wav

        clean = self.tts.synthesize(resp.text, self.voice)
        samples, sr = read_wav(clean)
        seed = zlib.crc32(resp.id.encode())
        noisy = radio_effect(samples, sr, noise_level=resp.noise_level, rng=seed)
        out = self.data_dir / "pilot_audio" / f"{resp.id}.wav"
        float_to_wav(out, noisy, sr)
        resp.audio_path = str(out)
        resp.audio_duration_s = round(len(noisy) / sr, 3)

    def _log(self, resp: PilotResponse, clearance: OpenClearance) -> None:
        line = {
            "id": resp.id,
            "t": resp.t,
            "clearance_id": clearance.id,
            "callsign": self.callsign,
            "acting_callsign": resp.acting_callsign,
            "true_items": [it.model_dump() for it in clearance.items],
            "true_text": build_readback(self.callsign, clearance.items, None, shorten=False),
            "kind": resp.kind,
            "spoken_text": resp.text,
            "spoken_callsign": resp.spoken_callsign,
            "spoken_items": [it.model_dump() for it in resp.spoken_items],
            "error_type": resp.injected_error,
            "error_description": resp.error_description,
            "sim_command": resp.sim_command.model_dump(),
            "audio_path": resp.audio_path,
            "voice": resp.voice,
            "noise_level": resp.noise_level,
        }
        path = self.data_dir / "ground_truth.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(line) + "\n")


# ---------------------------------------------------------------------------
# Fleet
# ---------------------------------------------------------------------------


class PilotFleet:
    """One AIPilot per callsign, sharing one seeded rng and one error-rate knob."""

    def __init__(
        self,
        error_rate: float = 0.1,
        seed: int = 0,
        tts: TTS | None = None,
        data_dir: str | Path | None = None,
        synthesize: bool = True,
        error_weights: dict[str, float] | None = None,
        shorten: bool = True,
    ):
        self.rng = random.Random(seed)
        self.error_rate = error_rate
        self.tts = tts if tts is not None else (TTS() if synthesize else None)
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self.synthesize = synthesize
        self.error_weights = error_weights
        self.shorten = shorten
        self.pilots: dict[str, AIPilot] = {}

    def get(self, callsign: str) -> AIPilot:
        cs = callsign.upper()
        if cs not in self.pilots:
            self.pilots[cs] = AIPilot(
                cs, error_rate=self.error_rate, rng=self.rng, tts=self.tts, data_dir=self.data_dir,
                synthesize=self.synthesize, error_weights=self.error_weights, shorten=self.shorten,
            )
        return self.pilots[cs]

    def set_error_rate(self, error_rate: float) -> None:
        self.error_rate = error_rate
        for p in self.pilots.values():
            p.error_rate = error_rate

    @property
    def active_callsigns(self) -> list[str]:
        return list(self.pilots)

    def respond(self, clearance: OpenClearance, heard_ok: bool = True, noise_level: float = 0.2, **kw) -> PilotResponse:
        return self.get(clearance.callsign).respond(
            clearance, heard_ok=heard_ok, active_callsigns=self.active_callsigns, noise_level=noise_level, **kw
        )


__all__ = ["AIPilot", "PilotFleet", "PilotResponse", "item_to_sim_command", "items_to_sim_commands"]
