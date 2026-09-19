"""Items -> SimCommand. Call this on the pilot's readback so the plane obeys what was said."""
from __future__ import annotations

from schemas import Item, SimCommand

_PRIORITY = ("altitude", "heading", "route", "speed")


def item_to_sim_command(item: Item) -> SimCommand:
    if item.type == "altitude":
        ft = float(item.value) * 100 if item.unit == "FL" else float(item.value)
        return SimCommand(kind="altitude", value=ft)
    if item.type == "heading":
        return SimCommand(kind="heading", value=float(item.value) % 360)
    if item.type == "route":
        return SimCommand(kind="direct", value=str(item.value).upper())
    if item.type == "speed":
        return SimCommand(kind="speed", value=float(item.value))
    return SimCommand(kind="none")


def items_to_sim_command(items: list[Item]) -> SimCommand:
    """First motion-affecting item wins (altitude, heading, direct, speed); otherwise none."""
    for kind in _PRIORITY:
        for item in items:
            if item.type == kind:
                return item_to_sim_command(item)
    return SimCommand(kind="none")


def items_to_sim_commands(items: list[Item]) -> list[SimCommand]:
    """Every motion-affecting command, in the order spoken. For multi-part clearances."""
    out = [item_to_sim_command(i) for i in items]
    return [c for c in out if c.kind != "none"]
