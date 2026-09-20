"""The Pydantic contract between teammates.

Mirrors docs/03-architecture.md (Transmission, Item, Extraction, OpenClearance, Verdict)
and docs/07-build-spec.md (AircraftState, Sim protocol, extra WebSocket events).
Change the doc and this file together, and tell the team.
"""
from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Tower core (03-architecture.md)
# ---------------------------------------------------------------------------

Speaker = Literal["controller", "pilot", "unknown", "datalink"]  # datalink: sent as text, no voice
ItemType = Literal[
    "altitude", "heading", "speed", "frequency", "squawk",
    "altimeter", "runway", "route", "hold_short", "other",
    "manoeuvre",  # a 360, a hold, "disregard", "unable": see tower/freeform.py
]
Unit = Literal["FL", "ft", "deg", "kt", "MHz", "hPa", "inHg", None]
ClearanceStatus = Literal["open", "matched", "mismatched", "partial", "missing", "uncertain"]
VerdictResult = Literal["match", "mismatch", "partial", "missing", "ambiguous"]
DecidedBy = Literal["rules", "checker_model", "resolver"]

# Error taxonomy from 02-domain.md
ErrorType = Literal[
    "wrong_value", "wrong_runway", "wrong_direction", "wrong_unit",
    "omitted_item", "ack_only", "wrong_aircraft", "missing_readback",
]


class Transmission(BaseModel):
    id: str
    t_start: float  # seconds since session start
    t_end: float
    audio_ref: str  # path or key for the clip; "" if text-only
    text_raw: str  # ASR output
    text_norm: str  # after normalizer
    asr_confidence: float = 1.0  # 0 to 1
    speaker: Speaker = "unknown"
    n_best: list[str] = Field(default_factory=list)  # alternative hypotheses, normalized
    text_stock: str | None = None  # stock Whisper output when the toggle is on


class Item(BaseModel):
    type: ItemType
    value: str | float | int  # 240, 270, 124.65, "24L", "BOSOX"
    unit: Unit = None
    action: str | None = None  # "descend", "climb", "turn_left", "contact", "cleared_land"
    mandatory: bool = True  # must it be read back


class Extraction(BaseModel):
    transmission_id: str
    callsign: str | None  # ICAO form, e.g. "ACA123"
    items: list[Item] = Field(default_factory=list)
    unexplained_words: int = 0  # words the grammar parser could not account for
    # "freeform": plain English resolved against the aircraft by patterns (tower/freeform.py).
    # "agent": the interpreter agent worked it out (tower/interpreter.py). "llm": a model filled in
    # what was *heard*, which is a guess about the audio and is treated with suspicion.
    method: Literal["grammar", "llm", "freeform", "agent"] = "grammar"


class OpenClearance(BaseModel):
    id: str
    callsign: str
    items: list[Item]
    issued_at: float
    timeout_s: float = 25.0
    status: ClearanceStatus = "open"
    source_transmission_id: str | None = None
    card_id: str | None = None  # instruction card that produced it, if any


class Verdict(BaseModel):
    clearance_id: str
    readback_transmission_id: str | None
    result: VerdictResult
    error_type: ErrorType | None = None
    expected: list[Item] = Field(default_factory=list)
    heard: list[Item] = Field(default_factory=list)
    confidence: float = 1.0
    reason: str = ""  # one line, human readable
    decided_by: DecidedBy = "rules"
    correction_phrase: str | None = None


class ResolverStep(BaseModel):
    clearance_id: str
    step: int
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    result_summary: str = ""


# ---------------------------------------------------------------------------
# Simulator and planner (07-build-spec.md)
# ---------------------------------------------------------------------------


class AircraftState(BaseModel):
    callsign: str
    x_nm: float
    y_nm: float  # local flat projection, NM
    alt_ft: float
    target_alt_ft: float
    hdg_deg: float
    target_hdg_deg: float | None = None
    gs_kt: float
    target_gs_kt: float | None = None
    route: list[str] = Field(default_factory=list)  # remaining waypoint names
    actype: str = "A320"
    is_intruder: bool = False
    threat: str | None = None  # disruption kind when is_intruder: fighter, drone, balloon, emergency, unknown
    manoeuvre: str | None = None  # "360 left", "hold right": circling, not navigating. See SimCommand "orbit"
    t: float = 0.0  # sim seconds


class SimCommand(BaseModel):
    """What a clearance does to a plane. Frequency/squawk/altimeter have no motion effect."""
    kind: Literal["altitude", "heading", "direct", "speed", "route", "orbit", "none"]
    value: float | str | None = None  # ft, deg, waypoint name, kt; "left" or "right" for an orbit
    # "orbit": full circles at the standard rate. 1 is "make a three sixty", None is a hold: it
    # circles until it is told something else. Afterwards it picks up whatever it was doing.
    turns: float | None = 1.0
    # "route": fly these (x, y) points in order, then direct to the waypoint named in `value`.
    # Only a data link clearance can carry this. By voice a reroute is a heading, then a direct.
    via: list[tuple[float, float]] = Field(default_factory=list)


class Waypoint(BaseModel):
    name: str
    x_nm: float
    y_nm: float
    # "fix": an ordinary named waypoint. "gate": a named entry/exit of a real-traffic region.
    # "hidden": a vertex of a really-flown track; the sim flies it, nobody says or sees it.
    kind: Literal["fix", "gate", "hidden"] = "fix"


class GeoFrame(BaseModel):
    """Pins the flat simulator plane to the Earth. See sim/geoframe.py."""
    lat0: float = 43.6777  # default: Toronto Pearson
    lon0: float = -79.6248
    projection: Literal["aeqd"] = "aeqd"  # azimuthal equidistant, centred on (lat0, lon0)
    name: str = "Toronto Pearson (CYYZ)"
    shape: Literal["square", "circle"] = "square"  # the sector boundary drawn on the map


class FlightSpec(BaseModel):
    callsign: str
    actype: str = "A320"
    entry_time_s: float = 0.0
    route: list[str]  # waypoint names, first is entry, last is exit
    alt_ft: float = 30000
    gs_kt: float = 420
    is_intruder: bool = False
    threat: str | None = None  # disruption kind for an intruder, see disruptions.py
    x_nm: float | None = None  # overrides route[0] position if set
    y_nm: float | None = None
    hdg_deg: float | None = None


class Zone(BaseModel):
    """Blocked airspace: a circle in the flat plane, between two levels, that may drift, swell and end.

    `x_nm`, `y_nm` and `radius_nm` are true at time `t0`. The simulator moves the zone and keeps
    `t0` current; the planner extrapolates from it. The defaults are a fixed, full-height,
    permanent column, which is what a zone written into a scenario file is.
    """
    id: str
    x_nm: float
    y_nm: float
    radius_nm: float
    kind: Literal["storm", "closed", "rocket", "intruder_buffer"] = "storm"
    label: str = ""
    floor_ft: float = 0.0
    ceiling_ft: float = 99999.0
    hdg_deg: float = 0.0  # drift
    gs_kt: float = 0.0
    swell_nm_per_min: float = 0.0
    max_radius_nm: float | None = None
    t0: float = 0.0
    expires_t: float | None = None


class Scenario(BaseModel):
    name: str
    seed: int = 0
    sector_nm: float = 200.0
    waypoints: list[Waypoint]
    flights: list[FlightSpec]
    zones: list[Zone] = Field(default_factory=list)
    separation_buffer_nm: float = 3.0  # extra on top of 5 NM
    pilot_error_rate: float = 0.1
    noise_level: float = 0.2
    traffic_multiplier: float = 1.0
    description: str = ""
    geo: GeoFrame = Field(default_factory=GeoFrame)  # where on Earth this flat sector sits
    source: Literal["sim", "real"] = "sim"  # real: flights and their routes come from recorded traffic
    meta: dict[str, Any] = Field(default_factory=dict)  # region, date, window, attribution


class Sim(Protocol):
    def step(self, dt: float) -> None: ...
    def aircraft(self) -> list[AircraftState]: ...
    def apply(self, callsign: str, cmd: SimCommand) -> None: ...
    def spawn(self, scenario: Scenario) -> None: ...


class PlannedPath(BaseModel):
    callsign: str
    samples: list[tuple[float, float, float, float]]  # (t, x, y, alt)
    cost: float = 0.0
    changes: list[str] = Field(default_factory=list)  # human-readable deltas from ideal
    distance_nm: float = 0.0
    time_s: float = 0.0
    via: list[tuple[float, float]] = Field(default_factory=list)  # turn points of a reroute, before the exit


class Plan(BaseModel):
    paths: list[PlannedPath]
    total_distance_nm: float
    total_time_s: float
    baseline_distance_nm: float
    baseline_time_s: float
    conflicts: int = 0
    trigger: str = "initial"


class InstructionCard(BaseModel):
    id: str
    callsign: str
    items: list[Item]
    phrase: str  # exactly what to say on the radio
    reason: str  # one line, plain English
    urgency_s: float  # seconds until it must take effect
    status: Literal["pending", "spoken", "validated", "verified", "error", "superseded"] = "pending"
    clearance_id: str | None = None
    # Who issued it: the human on the mic, Tower's own voice (Auto), or Tower by data link (Auto,
    # when the voice channel cannot keep up). None while it is still pending.
    via: Literal["human", "voice", "datalink"] | None = None
    # A shortcut that saves too little to be worth a transmission. Never shown or spoken. In silent
    # Auto it still goes out by data link, which costs nobody anything, so the aircraft is on its line.
    minor: bool = False
    # Set when the controller said something that conflicts with this card. The pilot was not told.
    heard_instead: str | None = None
    # Why this card exists, so the screen can say so. origin: "initial" (the plan made when the
    # world loaded), "replan" (something changed), "followup" (dogleg done, go direct), "release"
    # (the disruption is gone). cause: what it clears: a disruption id or another callsign.
    origin: Literal["initial", "replan", "followup", "release"] = "replan"
    cause: str | None = None
    emergency: bool = False


DisruptionKind = Literal["fighter", "drone", "balloon", "emergency", "unknown", "storm", "closed", "rocket",
                         "intruder"]  # "intruder" is the old name for "fighter" and still accepted


class Disruption(BaseModel):
    """Anything unplanned that Tower has to work around. Kinds and their numbers: disruptions.py.

    shape "point": something flying (`hdg_deg`, `gs_kt`, `alt_ft`), planned around with a buffer
    that grows with look-ahead. shape "circle": blocked airspace (`radius_nm`, `floor_ft` to
    `ceiling_ft`), which may drift and swell. `expires_t` is when it ends by itself; None means
    it lasts until it leaves the sector.
    """
    id: str
    kind: DisruptionKind
    shape: Literal["point", "circle"] = "point"
    label: str = ""
    x_nm: float
    y_nm: float
    radius_nm: float = 0.0
    hdg_deg: float | None = None
    gs_kt: float | None = None
    alt_ft: float | None = None
    target_alt_ft: float | None = None
    floor_ft: float = 0.0
    ceiling_ft: float = 99999.0
    swell_nm_per_min: float = 0.0
    max_radius_nm: float | None = None
    t_start: float = 0.0
    expires_t: float | None = None
    active: bool = True
    predicted_path: list[tuple[float, float, float]] = Field(default_factory=list)  # (t,x,y)


class Scoreboard(BaseModel):
    miles_saved: float = 0.0
    time_saved_s: float = 0.0
    losses_of_separation: int = 0
    closest_approach_nm: float | None = None
    errors_injected: int = 0
    errors_caught: int = 0
    false_alarms: int = 0
    mean_alert_latency_s: float | None = None
    transmissions: int = 0
    tier1_latency_s: float | None = None
    # Reaction, measured live. rerouted: flights given a new path since the run started.
    # reaction_s: from the last disruption appearing to the first rerouted aircraft visibly turning.
    rerouted: int = 0
    reaction_s: float | None = None
    datalink_sent: int = 0
    in_zone_now: int = 0
    zone_incursions: int = 0


# ---------------------------------------------------------------------------
# WebSocket envelope
# ---------------------------------------------------------------------------

EventType = Literal[
    "transcript", "clearance_opened", "clearance_updated", "alert", "resolver_step", "stats",
    "radar", "plan", "plan_update", "instruction_card", "disruption", "scoreboard",
    "agent_reply", "state", "notice",
    "radio_audio",  # a clip is on the frequency right now: play it. Sent before it is transcribed
    "said_check",  # what the controller said does not match the card: nothing went to the pilot
    "alert_resolved",  # a wrong readback was corrected and read back right
    "aside",  # the controller said "disregard", or asked for something no airliner does: not a clearance
]


class Event(BaseModel):
    type: EventType
    payload: dict[str, Any]
    t: float = 0.0


def event(type_: EventType, payload: BaseModel | dict[str, Any], t: float = 0.0) -> dict[str, Any]:
    if isinstance(payload, BaseModel):
        payload = payload.model_dump()
    return Event(type=type_, payload=payload, t=t).model_dump()
