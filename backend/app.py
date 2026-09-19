"""Tower backend: FastAPI app, WebSocket hub, and the 1 Hz clock.

Run:  cd backend && .venv/bin/uvicorn app:app --reload --port 8000
Protocol: docs/08-ws-protocol.md
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sim import scenarios as SC  # noqa: E402
from tower.audio import pcm16_to_float  # noqa: E402
from world import AUDIO_DIR, World  # noqa: E402

logging.basicConfig(level=os.environ.get("TOWER_LOG", "INFO"))
log = logging.getLogger("tower.app")

SPEED = float(os.environ.get("TOWER_SIM_SPEED", "1.0"))  # sim seconds per real second
SYNTH = os.environ.get("TOWER_SYNTHESIZE", "1") != "0"


class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.last_state: dict[str, Any] | None = None
        self.last_plan: dict[str, Any] | None = None

    def emit(self, ev: dict[str, Any]) -> None:
        if ev["type"] == "state":
            self.last_state = ev
        elif ev["type"] == "plan":
            self.last_plan = ev
        self.queue.put_nowait(ev)

    async def pump(self) -> None:
        while True:
            ev = await self.queue.get()
            data = json.dumps(ev, default=_json_default)
            dead = []
            for ws in list(self.clients):
                try:
                    await ws.send_text(data)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.clients.discard(ws)


def _json_default(o: Any) -> Any:
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(str(type(o)))


hub = Hub()
world = World(hub.emit, synthesize=SYNTH)


async def clock() -> None:
    """Drive the world. Speed is world.speed (sim seconds per real second), set from the screen.

    At 1x and below: one 1 s tick per period, as before. Above 1x: four ticks a second, each
    advancing speed/4 sim seconds, so the radar stays smooth without flooding the socket.
    """
    loop = asyncio.get_event_loop()
    while True:
        t0 = loop.time()
        speed = max(0.05, world.speed)
        if speed <= 1.0:
            period, dt = 1.0 / speed, 1.0
        else:
            period, dt = 0.25, speed * 0.25
        try:
            await world.tick(dt)
        except Exception:
            log.exception("tick failed")
        await asyncio.sleep(max(0.02, period - (loop.time() - t0)))


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Nothing runs until the screen sends "start". TOWER_SCENARIO only preloads a world (ready,
    # not running); TOWER_AUTOSTART=1 restores the old behaviour for headless runs.
    world.speed = SPEED
    preload = os.environ.get("TOWER_SCENARIO")
    if preload:
        world.load(preload)
        if os.environ.get("TOWER_AUTOSTART") == "1":
            world.start()
    else:
        world.emit_state()
    tasks = [asyncio.create_task(hub.pump()), asyncio.create_task(clock())]
    if SYNTH:
        asyncio.create_task(asyncio.to_thread(_warm_asr))
    yield
    for t in tasks:
        t.cancel()


def _warm_asr() -> None:
    try:
        from tower.asr import get_asr
        world.asr = get_asr()
        log.info("ASR ready: %s", type(world.asr).__name__)
    except Exception:
        log.exception("ASR warmup failed; the radio will fall back to typed text")


app = FastAPI(title="Tower", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "scenario": world.scenario.name if world.scenario else None, "t": world.sim.t,
            "lifecycle": world.lifecycle, "speed": world.speed,
            "asr": type(world.asr).__name__ if world.asr else None, "clients": len(hub.clients)}


@app.get("/scenarios")
async def scenarios() -> list[str]:
    return SC.list_scenarios()


@app.get("/scoreboard")
async def scoreboard() -> dict[str, Any]:
    return world.scoreboard().model_dump()


@app.get("/audio/{ref}")
async def audio(ref: str):
    path = AUDIO_DIR / Path(ref).name
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, media_type="audio/wav")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    hub.clients.add(ws)
    if hub.last_state:
        await ws.send_text(json.dumps(hub.last_state, default=_json_default))
    if hub.last_plan:
        await ws.send_text(json.dumps(hub.last_plan, default=_json_default))
    for card in world.cards.values():
        await ws.send_text(json.dumps({"type": "instruction_card", "payload": card.model_dump(), "t": world.sim.t}))
    if world.scenario is not None:
        # A screen that connects to a loaded-but-not-started world still needs to see the aircraft.
        await ws.send_text(json.dumps({"type": "radar", "t": world.sim.t, "payload": {
            "aircraft": [a.model_dump() for a in world.sim.aircraft()], "t": world.sim.t,
            "watching": world.watching()}}, default=_json_default))
        await ws.send_text(json.dumps({"type": "scoreboard", "t": world.sim.t,
                                       "payload": world.scoreboard().model_dump()}, default=_json_default))
    ptt_channel: str | None = None
    buf: list[bytes] = []
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                if ptt_channel:
                    buf.append(msg["bytes"])
                continue
            if msg.get("text") is None:
                continue
            try:
                data = json.loads(msg["text"])
            except json.JSONDecodeError:
                continue
            typ = data.get("type")
            if typ == "ptt_start":
                ptt_channel = data.get("channel", "radio")
                buf = []
            elif typ == "ptt_stop":
                channel, ptt_channel = ptt_channel, None
                if channel and buf:
                    samples = pcm16_to_float(b"".join(buf))
                    buf = []
                    if len(samples) < 16000 * 0.4:
                        continue
                    if channel == "agent":
                        asyncio.create_task(world.agent_audio(samples))
                    else:
                        asyncio.create_task(world.controller_audio(samples))
            elif typ == "agent_text":
                asyncio.create_task(world.agent_request(str(data.get("text", ""))))
            elif typ == "radio_text":
                asyncio.create_task(world.controller_text(str(data.get("text", ""))))
            elif typ in ("load_scenario", "configure"):
                # configure: {source: "sim", scenario, density}. Real traffic arrives in phase 4.
                name = str(data.get("scenario") or data.get("name") or "demo")
                density = float(data.get("density") or 1.0)
                try:
                    if name not in SC.list_scenarios():
                        raise ValueError(f"unknown scenario {name}")
                    world.load(name)
                    if abs(density - 1.0) > 1e-6:
                        world.tool_multiply_traffic(density)
                except Exception as exc:  # noqa: BLE001 - tell the screen, keep the socket
                    log.exception("configure failed")
                    world.notice(f"Could not load {name}: {exc}", "error")
            elif typ == "start":
                if not world.start():
                    world.notice("Nothing to start. Load a scenario first.", "warn")
            elif typ == "pause":
                world.pause()
            elif typ == "reset":
                if not world.reset():
                    world.notice("Nothing to reset. Load a scenario first.", "warn")
            elif typ == "set_tower":
                world.set_tower(bool(data.get("enabled", True)))
            elif typ == "set_auto_speak":
                world.set_auto_speak(bool(data.get("enabled", False)))
            elif typ == "add_disruption":
                world.add_disruption(str(data.get("kind", "intruder")), float(data.get("x_nm", -30)),
                                     float(data.get("y_nm", -70)))
            elif typ == "speak_card":
                asyncio.create_task(world.speak_card(str(data.get("id", ""))))
            elif typ == "set_sliders":
                world.set_sliders(data.get("buffer_nm"), data.get("error_rate"), data.get("noise"))
            elif typ == "set_speed":
                world.set_speed(float(data.get("speed", 1.0)))
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(ws)
