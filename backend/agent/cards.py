"""Card descriptors: what the squack agent answers with. PRD section 5.

The agent never returns markup. Every answer is one sentence plus cards drawn from this fixed
registry; the screen maps `kind` to a component (`frontend/lib/cards/registry.tsx`). A card may
carry `live` bindings (`{aircraft: "DAL789"}`, `{scoreboard: true}`) so the screen keeps its
numbers current from the store without another agent turn. Every tool that answers builds one of
these here; the model never types card JSON.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from schemas import AircraftState, InstructionCard, PlannedPath

CardKind = Literal["text", "table", "list", "aircraft", "comparison", "steps"]


class Live(BaseModel):
    aircraft: str | None = None  # keep level, heading and speed current for this callsign
    scoreboard: bool | None = None  # keep the numbers current from the scoreboard event


class Card(BaseModel):
    kind: CardKind
    title: str = ""
    live: Live | None = None


class TextCard(Card):
    kind: Literal["text"] = "text"
    text: str


class TableCard(Card):
    kind: Literal["table"] = "table"
    columns: list[str]
    rows: list[list[Any]]
    focus_column: int | None = None  # cells in this column are callsign buttons that focus the map
    caption: str = ""  # a muted line under the table: run parameters, units, what was held fixed


class ListItem(BaseModel):
    t: float | None = None  # sim clock stamp
    text: str
    kind: str | None = None  # event type, for an icon


class ListCard(Card):
    kind: Literal["list"] = "list"
    items: list[ListItem]


class CardBrief(BaseModel):
    """The instruction card an aircraft card explains: the planner's own reasons, not a model's."""
    id: str
    phrase: str
    reason: str
    cause: str | None = None
    origin: str = "replan"
    status: str = "pending"
    confidence: float | None = None
    risk_after: float | None = None
    margin: float | None = None  # margin_factor(cost, runner_up_cost)


class AircraftCard(Card):
    kind: Literal["aircraft"] = "aircraft"
    callsign: str
    level_ft: float
    hdg: float
    gs_kt: float
    card: CardBrief | None = None
    changes: list[str] = Field(default_factory=list)
    cost: float | None = None
    runner_up_cost: float | None = None
    extra_nm: float | None = None  # planned distance minus the fixed-route baseline
    issue: str | None = None  # an alert or a risk pair: what is wrong, one line


class ComparisonCard(Card):
    kind: Literal["comparison"] = "comparison"
    columns: list[str]
    rows: list[list[Any]]
    highlight_row: int | None = None


class Step(BaseModel):
    n: int
    tool: str
    summary: str = ""
    ms: float = 0.0
    status: Literal["running", "done", "error"] = "done"


class StepsCard(Card):
    kind: Literal["steps"] = "steps"
    steps: list[Step]


AnyCard = TextCard | TableCard | ListCard | AircraftCard | ComparisonCard | StepsCard

#: Every kind the screen can draw. `loop._is_descriptor` checks against this.
KINDS = frozenset({"text", "table", "list", "aircraft", "comparison", "steps"})


def dump(card: Card) -> dict[str, Any]:
    return card.model_dump(exclude_none=True)


# ------------------------------------------------------------------------------------ builders


def text(t: str, title: str = "") -> TextCard:
    return TextCard(text=t, title=title)


def _margin(path: PlannedPath | None) -> float | None:
    if path is None:
        return None
    from planner.risk import margin_factor

    return round(float(margin_factor(path.cost, path.runner_up_cost)), 3)


def aircraft_card(state: AircraftState, path: PlannedPath | None = None, card: InstructionCard | None = None,
                  baseline: PlannedPath | None = None, issue: str | None = None, title: str = "") -> AircraftCard:
    """The flight card: state now, the instruction card's reasons, the path's cost and changes."""
    brief = None
    if card is not None:
        brief = CardBrief(id=card.id, phrase=card.phrase, reason=card.reason, cause=card.cause, origin=card.origin,
                          status=card.status, confidence=card.confidence, risk_after=card.risk_after,
                          margin=_margin(path))
    extra = None
    if path is not None and baseline is not None:
        extra = round(path.distance_nm - baseline.distance_nm, 1)
    return AircraftCard(
        title=title or state.callsign, callsign=state.callsign, level_ft=round(state.alt_ft), hdg=round(state.hdg_deg),
        gs_kt=round(state.gs_kt), card=brief, changes=list(path.changes) if path else [],
        cost=(round(path.cost, 1) if path else None),
        runner_up_cost=(round(path.runner_up_cost, 1) if path and path.runner_up_cost is not None else None),
        extra_nm=extra, issue=issue, live=Live(aircraft=state.callsign))


def scoreboard_card(sb: dict[str, Any]) -> TableCard:
    labels = [("miles_saved", "miles saved"), ("time_saved_s", "time saved (s)"),
              ("losses_of_separation", "losses of separation"), ("closest_approach_nm", "closest approach (NM)"),
              ("errors_injected", "errors injected"), ("errors_caught", "errors caught"),
              ("false_alarms", "false alarms"), ("mean_alert_latency_s", "alert latency (s)"),
              ("rerouted", "rerouted"), ("reaction_s", "reaction (s)"),
              ("conflicts_predicted", "conflicts predicted"), ("conflicts_resolved", "conflicts resolved"),
              ("futures_per_s", "futures / s"), ("cones_now", "cones now")]
    rows = [[label, sb.get(key)] for key, label in labels]
    return TableCard(title="Scoreboard", columns=["metric", "value"], rows=rows, live=Live(scoreboard=True))


def steps_card(steps: list[Step]) -> StepsCard:
    return StepsCard(title="Trace", steps=steps)


ARM_LABEL = {"fixed": "fixed routes", "tower_off": "squack off", "tower_on": "squack"}


def sim_result_card(kind: str, result: dict[str, Any], params: dict[str, Any]) -> Card:
    """A finished sim job as a table: one row per arm for a Monte Carlo, one row per density and
    arm for a sweep. Rows come from tools/simjobs.py. There is no chart card; the numbers read
    better as a table and the sentence carries the point."""
    rows = list(result.get("rows") or [])
    caption = str(result.get("caption") or "")
    if kind == "sweep" and rows:
        table = [[f"{float(r.get('density') or 0.0):g}x", ARM_LABEL.get(str(r.get("arm")), str(r.get("arm"))),
                  r.get("los_per_h"), r.get("closest_p5_nm")] for r in
                 sorted(rows, key=lambda r: (float(r.get("density") or 0.0), str(r.get("arm"))))]
        return TableCard(title="Losses of separation per flight hour by density",
                         columns=["density", "arm", "LoS / h", "closest p5 NM"], rows=table, caption=caption)
    cols = ["arm", "LoS / h", "closest p5 NM", "miles vs fixed %", "caught / injected"]
    table = [[ARM_LABEL.get(str(r.get("arm")), str(r.get("arm"))), r.get("los_per_h"), r.get("closest_p5_nm"),
              r.get("miles_vs_fixed_pct"), f"{r.get('errors_caught', 0)} / {r.get('errors_injected', 0)}"] for r in rows]
    runs, dens = params.get("runs"), params.get("density")
    title = "Monte Carlo" + (f": {runs} runs" if runs else "") + (f" at {dens:g}x" if isinstance(dens, (int, float)) else "")
    hi = next((i for i, r in enumerate(rows) if r.get("arm") == "tower_on"), None)
    return ComparisonCard(title=title, columns=cols, rows=table, highlight_row=hi)
