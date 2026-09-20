"""Drive a running backend the way the screen does and print every resolver step, so you can see
the [Elasticsearch] searches without clicking around. Start the backend first, then:

    cd backend && .venv/bin/python tools/elastic_demo.py            # about two minutes
    cd backend && .venv/bin/python tools/elastic_demo.py --rounds 6  # keep going longer

It loads the demo scenario, starts the clock, sets pilot error rate and radio noise to the
maximum, then sends altitude and routing instructions to the aircraft that are in the sector,
one every few seconds. The AI pilots answer by voice through Whisper, exactly as on stage.
Every alert and every resolver step is printed as it arrives. Exit code 0 when at least one
resolver step searched Elasticsearch.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

try:
    import websockets
except ImportError:  # pragma: no cover
    print("pip install websockets  (it is in the backend's dependencies)")
    sys.exit(2)

URL = "ws://127.0.0.1:8000/ws"

CLEARANCES = [  # (callsign as spoken, instruction). Only sent if the aircraft is on radar.
    ("ACA123", "air canada one two three descend and maintain flight level two four zero"),
    ("UAL210", "united two one zero climb and maintain flight level three seven zero"),
    ("DAL789", "delta seven eight nine descend and maintain flight level three three zero"),
    ("WJA456", "westjet four five six turn left heading two seven zero"),
    ("ACA123", "air canada one two three proceed direct estir"),
    ("ACA133", "air canada one three three descend and maintain flight level two five zero"),
    ("UAL210", "united two one zero proceed direct pikar"),
    ("DAL789", "delta seven eight nine turn right heading zero nine zero"),
]


async def main(rounds: int, gap_s: float) -> int:
    print(f"connecting to {URL} ...")
    try:
        ws = await websockets.connect(URL, max_size=None)
    except OSError as e:
        print(f"cannot connect: {e}. Is the backend running on port 8000?")
        return 2

    state: dict[str, Any] = {}
    on_radar: set[str] = set()
    elastic_steps = 0
    steps = 0
    alerts = 0

    async def send(msg: dict[str, Any]) -> None:
        await ws.send(json.dumps(msg))

    async def pump(until: float) -> None:
        """Read events until `until` (monotonic seconds), printing the interesting ones."""
        nonlocal elastic_steps, steps, alerts
        while time.monotonic() < until:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.05, until - time.monotonic()))
            except TimeoutError:
                return
            ev = json.loads(raw)
            typ, p = ev.get("type"), ev.get("payload") or {}
            if typ == "state":
                state.update(p)
            elif typ == "radar":
                on_radar.clear()
                on_radar.update(a["callsign"] for a in p.get("aircraft", []) if not a.get("is_intruder"))
            elif typ == "transcript":
                who = (p.get("speaker") or "?").upper()
                conf = p.get("asr_confidence")
                print(f"  {who:<10} {p.get('callsign') or '':<7} {p.get('text_norm')!r}"
                      + (f"  conf {conf:.2f}" if isinstance(conf, (int, float)) else ""))
            elif typ == "resolver_step":
                steps += 1
                summary = p.get("result_summary", "")
                mark = ">>" if summary.startswith("[Elasticsearch]") else "  "
                if summary.startswith("[Elasticsearch]"):
                    elastic_steps += 1
                print(f"{mark} RESOLVER step {p.get('step')} {p.get('tool')}({json.dumps(p.get('args'))}): {summary}")
            elif typ == "alert":
                alerts += 1
                print(f"  ALERT {p.get('result')} {p.get('callsign')}: {p.get('reason')}  [decided by {p.get('decided_by')}]")
            elif typ == "notice":
                print(f"  notice: {p.get('text') or p.get('message') or p}")

    await pump(time.monotonic() + 1.0)
    print(f"backend memory: {state.get('memory') or 'in-memory (ELASTIC_URL not set on the backend)'}")
    if not state.get("memory"):
        print("the resolver will still run, but no step can say [Elasticsearch]. Set ELASTIC_URL and restart the backend.")

    print("loading demo, starting, sliders to max ...")
    await send({"type": "configure", "source": "sim", "scenario": "demo", "density": 1.0})
    await pump(time.monotonic() + 2.0)
    await send({"type": "start"})
    await send({"type": "set_sliders", "error_rate": 0.5, "noise": 1.0})
    await pump(time.monotonic() + 2.0)

    for r in range(rounds):
        for cs, phrase in CLEARANCES:
            if cs not in on_radar:
                continue
            print(f"\n[{r + 1}/{rounds}] TX: {phrase}")
            await send({"type": "radio_text", "text": phrase})
            await pump(time.monotonic() + gap_s)

    print(f"\n{steps} resolver steps, {elastic_steps} searched Elasticsearch, {alerts} alerts")
    await ws.close()
    if elastic_steps:
        print("the trace shows Elasticsearch searches: this is what the amber card's agent trace displays on screen")
        return 0
    print("no Elasticsearch step this run. Every readback was either clearly right or clearly wrong, so the agent "
          "never woke. Run again, or add --rounds 6.")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3, help="how many passes over the instruction list")
    ap.add_argument("--gap", type=float, default=9.0, help="seconds to wait after each instruction")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.rounds, a.gap)))
