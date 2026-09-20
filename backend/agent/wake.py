"""Agent mode: which events wake squack, and the director that fills the stage. PRD section 10.

`WakePolicy.observe` sees every emitted event (World._emit) and keeps the ones worth a turn: an
alert, a risk pair crossing REPLAN_P for the first time, a plan_update that changed a flight, a
disruption appearing or ending, a lifecycle change, a said-vs-card escalation. Everything else
never wakes the agent. `poll(now)` hands the batch over at most once per DEBOUNCE_S seconds:
the first event after a quiet spell goes at once, what arrives in the next five seconds waits for
the next turn. After IDLE_S with nothing to say the stage is cleared.

`Director.stage_for` is deterministic and needs no model: an alert places the alert card in slot
1, a disruption the comparison of rerouted flights and the worst flight's live card, a risk pair
the aircraft card with the cone numbers, a replan the list of changes. It runs first on every
wake, so the alert card is on screen within one tick; the agent's turn, when there is a key,
may then replace the stage with `by: "agent"`.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from agent import cards as CD
from agent import registry as R
from planner.risk import REPLAN_P, SHOW_P

if TYPE_CHECKING:
    from world import World

DEBOUNCE_S = 5.0
IDLE_S = 60.0
MAX_SLOTS = 3
WAKE_KINDS = ("alert", "disruption", "plan_update", "risk", "state", "said_check")
PRIORITY = {"alert": 0, "said_check": 0, "disruption": 1, "risk": 2, "plan_update": 3, "state": 4}
TTL_S = {"alert": 120.0, "said_check": 120.0, "disruption": 90.0, "risk": 60.0, "plan_update": 45.0, "state": 20.0}


class Stage(BaseModel):
    slots: list[dict[str, Any]] = Field(default_factory=list)
    ttl_s: float = 0.0
    by: Literal["director", "agent"] = "director"
    text: str = ""

    def payload(self) -> dict[str, Any]:
        return self.model_dump()


class WakePolicy:
    def __init__(self, clock: Callable[[], float] = time.monotonic, debounce_s: float = DEBOUNCE_S,
                 idle_s: float = IDLE_S) -> None:
        self.clock = clock
        self.debounce_s = debounce_s
        self.idle_s = idle_s
        self.pending: list[dict[str, Any]] = []
        self.last_turn = -1e18
        self.last_lifecycle: str | None = None
        self.over: set[frozenset[str]] = set()  # risk pairs at or above REPLAN_P right now
        self.last_staged: float | None = None  # when a non-empty stage last went up
        self.idle_cleared = True

    def reset(self) -> None:
        self.pending.clear()
        self.over.clear()

    def wants(self, ev: dict[str, Any]) -> bool:
        """Is this event one that wakes the agent? Updates the pair and lifecycle trackers."""
        typ, p = ev.get("type"), ev.get("payload") or {}
        if typ in ("alert", "said_check", "disruption"):
            return True
        if typ == "plan_update":
            return bool(p.get("changed"))
        if typ == "state":
            lc = p.get("lifecycle")
            changed = lc != self.last_lifecycle and self.last_lifecycle is not None
            self.last_lifecycle = lc
            return changed
        if typ == "risk":
            woke = False
            now_over: set[frozenset[str]] = set()
            for pair in p.get("pairs") or []:
                key = frozenset((str(pair.get("a")), str(pair.get("b"))))
                pm = float(pair.get("p_max") or 0.0)
                if pm >= REPLAN_P:
                    now_over.add(key)
                    if key not in self.over:
                        woke = True
                elif pm >= SHOW_P and key in self.over:
                    now_over.add(key)  # still over until it drops below the floor
            self.over = now_over
            return woke
        return False

    def observe(self, ev: dict[str, Any]) -> bool:
        if not self.wants(ev):
            return False
        if ev.get("type") == "risk":  # keep only the pairs that crossed, so the director has them
            ev = {**ev, "payload": {**(ev.get("payload") or {}),
                                    "pairs": [q for q in (ev.get("payload") or {}).get("pairs") or []
                                              if float(q.get("p_max") or 0.0) >= REPLAN_P]}}
        self.pending.append(ev)
        return True

    def poll(self, now: float | None = None) -> list[dict[str, Any]] | None:
        """The batch to act on, or None. At most one batch per debounce_s."""
        now = self.clock() if now is None else now
        if not self.pending or now - self.last_turn < self.debounce_s:
            return None
        self.last_turn = now
        batch, self.pending = self.pending, []
        return batch

    def staged(self, now: float | None, nonempty: bool) -> None:
        now = self.clock() if now is None else now
        if nonempty:
            self.last_staged = now
            self.idle_cleared = False

    def idle_due(self, now: float | None = None) -> bool:
        """True once, when the last stage has stood for idle_s with nothing new."""
        now = self.clock() if now is None else now
        if self.idle_cleared or self.last_staged is None or now - self.last_staged < self.idle_s:
            return False
        self.idle_cleared = True
        return True


class Director:
    """Deterministic stage from a batch. Needs no model; the planner's own reasons fill the cards."""

    @staticmethod
    def stage_for(events: list[dict[str, Any]], world: "World") -> Stage:
        ordered = sorted(events, key=lambda e: PRIORITY.get(str(e.get("type")), 9))
        slots: list[CD.Card] = []
        lines: list[str] = []
        ttl = 0.0
        seen_kinds: set[str] = set()
        for ev in ordered:
            if len(slots) >= MAX_SLOTS:
                break
            typ = str(ev.get("type"))
            cards, line = Director._cards(typ, ev.get("payload") or {}, world)
            for c in cards:
                if len(slots) < MAX_SLOTS and not any(CD.dump(c) == CD.dump(s) for s in slots):
                    slots.append(c)
            if line and typ not in seen_kinds:
                lines.append(line)
                seen_kinds.add(typ)
            if cards:
                ttl = max(ttl, TTL_S.get(typ, 30.0))
        return Stage(slots=[CD.dump(c) for c in slots], ttl_s=ttl, by="director", text=" ".join(lines[:2]))

    @staticmethod
    def _cards(typ: str, p: dict[str, Any], world: "World") -> tuple[list[CD.Card], str]:
        if typ == "alert":
            cs = p.get("callsign") or ""
            issue = p.get("reason") or f"readback {p.get('result')}"
            st = R.state_of(world, cs) if cs else None
            if st is None:
                return [CD.text(f"{cs}: {issue}", title="Alert")], f"Alert on {cs}: {issue}."
            fix = p.get("correction_phrase")
            card = CD.aircraft_card(st, R.path_of(world, cs), R.card_of(world, cs), R.baseline_of(world, cs),
                                    issue=issue + (f" Say: {fix}" if fix else ""), title=f"Alert: {cs}")
            return [card], f"{cs} read back wrong: {issue}."
        if typ == "said_check":
            cs = p.get("callsign") or ""
            st = R.state_of(world, cs) if cs else None
            issue = f"said-vs-card: {p.get('detail')}"
            if st is None:
                return [CD.text(issue, title=f"Check: {cs}")], f"{cs}: {p.get('detail')}."
            return [CD.aircraft_card(st, R.path_of(world, cs), R.card_of(world, cs), R.baseline_of(world, cs),
                                     issue=issue, title=f"Check: {cs}")], f"{cs}: what you said is not the card."
        if typ == "disruption":
            did, label = str(p.get("id")), str(p.get("label") or p.get("kind"))
            if not p.get("active", True):
                return [CD.text(f"{label} {did} is no longer a factor.", title=f"{label} cleared")], f"{label} {did} has cleared."
            rows = R.disruption_effect(world, did)
            total = round(sum(r["extra_nm"] or 0.0 for r in rows), 1)
            comp = CD.ComparisonCard(title=f"{label} {did}: {len(rows)} rerouted, +{total} NM",
                                     columns=["flight", "change", "extra NM"],
                                     rows=[[r["callsign"], r["change"], r["extra_nm"]] for r in rows[:8]],
                                     highlight_row=0 if rows else None)
            out: list[CD.Card] = [comp]
            if rows:
                worst = rows[0]["callsign"]
                st = R.state_of(world, worst)
                if st is not None:
                    out.append(CD.aircraft_card(st, R.path_of(world, worst), R.card_of(world, worst),
                                                R.baseline_of(world, worst), issue=f"rerouted to clear {did}"))
            line = f"{label} {did}: {len(rows)} flight{'s' if len(rows) != 1 else ''} rerouted, {total} NM extra."
            return out, line
        if typ == "risk":
            pairs = p.get("pairs") or []
            if not pairs:
                return [], ""
            q = pairs[0]
            a, b = str(q.get("a")), str(q.get("b"))
            pm, eta, sep = float(q.get("p_max") or 0.0), float(q.get("eta_s") or 0.0), q.get("min_sep_nm_p5")
            issue = f"LoS {pm:.0%} with {b} in {eta:.0f} s; p5 separation {sep} NM" if sep is not None else f"LoS {pm:.0%} with {b}"
            st = R.state_of(world, a)
            if st is None:
                return [CD.text(f"{a}/{b}: {issue}", title="Predicted conflict")], f"{a} and {b}: {issue}."
            return [CD.aircraft_card(st, R.path_of(world, a), R.card_of(world, a), R.baseline_of(world, a),
                                     issue=issue, title=f"Conflict: {a}/{b}")], f"{a} and {b} may lose separation ({pm:.0%} in {eta:.0f} s)."
        if typ == "plan_update":
            changed = list(p.get("changed") or [])
            trigger = str(p.get("trigger") or "replan")
            items = []
            for cs in changed[:8]:
                c = R.card_of(world, cs)
                path = R.path_of(world, cs)
                why = c.reason if c else (path.changes[0] if path and path.changes else "on plan")
                items.append(CD.ListItem(t=p.get("t"), text=f"{cs}: {why}", kind="plan_update"))
            if not items:
                return [], ""
            return [CD.ListCard(title=f"Replan: {trigger}", items=items)], f"Replanned {len(changed)} flight{'s' if len(changed) != 1 else ''} ({trigger})."
        if typ == "state":
            lc = str(p.get("lifecycle"))
            return [CD.text(f"Simulation {lc}.", title="Clock")], f"Simulation {lc}."
        return [], ""
