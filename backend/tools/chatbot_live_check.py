"""Live check: squack speaks only when spoken to, and answers when it is.

Drives a running backend over the websocket like the screen does. Configures the demo scenario,
starts the clock, then sits quiet for QUIET_S seconds and asserts that no `answer` arrived; then
sends the three questions on the command line and prints each reply verbatim.

    .venv/bin/python -m tools.chatbot_live_check --url ws://127.0.0.1:8001/ws
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

import websockets

QUIET_S = 30.0
ASK_TIMEOUT_S = 40.0
QUESTIONS = ["what's going on out there?", "why did you turn Delta 789?", "double the traffic"]


async def main(url: str, quiet_s: float) -> int:
    async with websockets.connect(url, max_size=None) as ws:
        async def send(msg: dict) -> None:
            await ws.send(json.dumps(msg))

        await send({"type": "configure", "source": "sim", "scenario": "demo"})
        await asyncio.sleep(2.0)
        await send({"type": "start"})

        # --- 1. quiet: nothing we do not ask for may produce an `answer` ---------------------
        seen: list[dict] = []
        loop = asyncio.get_running_loop()
        end = loop.time() + quiet_s
        while loop.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - loop.time()))
            except asyncio.TimeoutError:
                break
            ev = json.loads(raw)
            if ev.get("type") in ("answer", "agent_step", "stage"):
                seen.append(ev)
        kinds = sorted({e["type"] for e in seen})
        print(f"[quiet {quiet_s:.0f}s] unprompted answer/agent_step/stage events: {len(seen)} {kinds}")
        if seen:
            for e in seen[:5]:
                print("   ", json.dumps(e)[:300])
            print("FAIL: squack talked without being spoken to")
            return 1

        # --- 2. ask: each question must come back with one answer ---------------------------
        ok = True
        for q in QUESTIONS:
            await send({"type": "agent_text", "text": q, "ui_state": {"selected": None}})
            ans = None
            end = loop.time() + ASK_TIMEOUT_S
            while loop.time() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - loop.time()))
                except asyncio.TimeoutError:
                    break
                ev = json.loads(raw)
                if ev.get("type") == "answer":
                    ans = ev
                    break
            if ans is None:
                print(f"\nQ: {q}\nA: <no answer in {ASK_TIMEOUT_S:.0f}s>  FAIL")
                ok = False
                continue
            p = ans["payload"]
            print(f"\nQ: {q}\nA: {p['text']}\n   cards: {[c.get('kind') for c in p.get('cards') or []]}"
                  f"  steps: {[s.get('tool') for s in p.get('steps') or []]}  for: {p.get('for')}")
            if q == QUESTIONS[0] and not p.get("cards"):
                print("   FAIL: expected at least one card on the open question")
                ok = False
        return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8000/ws")
    ap.add_argument("--quiet-s", type=float, default=QUIET_S)
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.url, a.quiet_s)))
