"""Tower backend: FastAPI app, WebSocket hub, and the 1 Hz clock.

Run:  cd backend && .venv/bin/uvicorn app:app --reload --port 8000
Protocol: docs/08-ws-protocol.md
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sim import live as LIVE  # noqa: E402
from schemas import event  # noqa: E402
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
            try:
                data = json.dumps(ev, default=_json_default)
            except Exception:
                # One event that cannot be sent must never stop all the others. This task dying is
                # silent: the backend keeps running and every screen freezes on its last frame.
                log.exception("dropped an event that could not be turned into JSON: %s", ev.get("type"))
                continue
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
    if hasattr(o, "model_dump"):  # a pydantic model that was put in a payload as it was
        return o.model_dump()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(str(type(o)))


hub = Hub()
world = World(hub.emit, synthesize=SYNTH)
_background: set[asyncio.Task[Any]] = set()  # keeps fire-and-forget tasks alive until they finish


def _spawn(coro, what: str) -> None:
    """Run something off the socket loop, and never let it fail without a word. A transmission whose
    task raised used to vanish: no transcript, no notice, no pilot, and nothing in the log either."""
    async def guarded() -> None:
        try:
            await coro
        except Exception:
            log.exception("%s failed", what)
            world.notice(f"Tower hit an error handling that {what}. Nothing was issued: say it again.", "error")
    task = asyncio.create_task(guarded())
    _background.add(task)
    task.add_done_callback(_background.discard)


class Dictation:
    """What the controller is saying, while the key is still down.

    Frames pile up in `chunks`. Every PARTIAL_EVERY_S of new audio (and at least PARTIAL_GAP_S after
    the last one was launched) the whole buffer so far is transcribed on the fast path (one beam, no
    alternatives) in a thread and goes out as `dictation {final: false}`. One partial in flight at a
    time: frames that land while it runs wait for the next. Buffers past MAX_S stop getting partials
    (a decode that long would fall behind the speech). On release `finish` hands the audio to the
    existing routing and the transcript that routing computes anyway goes out first as the final, so
    nothing is decoded twice. A partial that returns after the release is dropped: the final is
    always the last dictation event for its channel. A partial that fails is logged, never fatal.
    """

    PARTIAL_EVERY_S = 1.2
    PARTIAL_GAP_S = 0.6
    MAX_S = 20.0
    BYTES_PER_S = 16000 * 2  # PCM16 mono 16 kHz

    def __init__(self, channel: str) -> None:
        self.channel = channel
        self.chunks: list[bytes] = []
        self.n_bytes = 0
        self.launched_s = 0.0  # audio length when the last partial was launched
        self.launched_at = 0.0  # wall clock of that launch
        self.task: asyncio.Task[Any] | None = None
        self.closed = False

    @property
    def seconds(self) -> float:
        return self.n_bytes / self.BYTES_PER_S

    def feed(self, frame: bytes) -> None:
        self.chunks.append(frame)
        self.n_bytes += len(frame)
        s = self.seconds
        if self.task is not None and not self.task.done():
            return
        if s > self.MAX_S or s - self.launched_s < self.PARTIAL_EVERY_S:
            return
        if time.monotonic() - self.launched_at < self.PARTIAL_GAP_S:
            return
        self.launched_s, self.launched_at = s, time.monotonic()
        self.task = asyncio.create_task(self._partial(pcm16_to_float(b"".join(self.chunks)), s))
        _background.add(self.task)
        self.task.add_done_callback(_background.discard)

    async def _partial(self, samples: np.ndarray, t_audio_s: float) -> None:
        try:
            text, *_ = await world._transcribe(samples, fast=True)
        except Exception:  # noqa: BLE001 - a lost partial costs nothing; the final still comes
            log.exception("dictation partial failed")
            return
        if self.closed:
            return
        self.emit(text, final=False, t_audio_s=t_audio_s)

    def emit(self, text: str, *, final: bool, t_audio_s: float) -> None:
        world.emit(event("dictation", {"channel": self.channel, "text": text, "final": final,
                                       "t_audio_s": round(t_audio_s, 2)}, t=world.sim.t))

    async def finish(self) -> None:
        """The key came up: route the whole clip as before, with the final dictation ahead of it."""
        self.closed = True
        samples = pcm16_to_float(b"".join(self.chunks))
        self.chunks = []
        if len(samples) < 16000 * 0.4:
            return
        seconds = len(samples) / 16000

        def final(text: str) -> None:
            try:
                self.emit(text, final=True, t_audio_s=seconds)
            except Exception:  # noqa: BLE001 - never between the controller and the aircraft
                log.exception("dictation final failed")

        if self.channel == "agent":
            await world.agent_audio(samples, on_text=final)
        else:
            await world.controller_audio(samples, on_text=final)


def _configure_live(data: dict[str, Any]) -> None:
    """configure with source "live": one snapshot of the real sky. See sim/live.py.

    The fetch can take seconds, so it runs as a task (and in a thread inside that): the socket keeps
    reading and the clock keeps ticking. load_into_async never raises and reports through notices.
    """
    try:
        cap = int(data.get("max_flights") or 0) or None
    except (TypeError, ValueError, OverflowError):  # OverflowError: JSON 1e999 parses to inf
        cap = None
    task =asyncio.create_task(LIVE.load_into_async(world, str(data.get("region") or ""), cap))
    _background.add(task)
    task.add_done_callback(_background.discard)


async def clock() -> None:
    """Drive the world. Speed is world.speed (sim seconds per real second), set from the screen.

    At 1x and below: one 1 s tick per period, as before. Above 1x: four ticks a second, each
    advancing speed/4 sim seconds, so the radar stays smooth without flooding the socket.
    """
    loop = asyncio.get_event_loop()
    while True:
        t0 = loop.time()
        period = 1.0
        try:  # everything inside: an exception out here would end the clock without a word
            speed = max(0.05, world.clock_speed())  # voice on: 1x while anyone is talking, the chosen speed otherwise
            if speed <= 1.0:
                period, dt = 1.0 / speed, 1.0
            else:
                period, dt = 0.25, speed * 0.25
            await world.tick(dt)
        except Exception:
            log.exception("tick failed")
        await asyncio.sleep(max(0.02, period - (loop.time() - t0)))


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Nothing runs until the screen sends "start". TOWER_SCENARIO only preloads a world (ready,
    # not running); TOWER_AUTOSTART=1 restores the old behaviour for headless runs.
    world.speed = SPEED
    # The screen opens on the path demo: voice off, instructions by data link. TOWER_VOICE=on starts
    # in the spoken loop instead. (World itself defaults to voice on, which is what the tests drive.)
    world.auto_speak = os.environ.get("TOWER_VOICE", "off").lower() != "on"
    preload = os.environ.get("TOWER_SCENARIO")
    if preload:
        world.load(preload)
        if os.environ.get("TOWER_AUTOSTART") == "1":
            world.start()
    else:
        world.emit_state()
    tasks = [asyncio.create_task(hub.pump()), asyncio.create_task(clock()), asyncio.create_task(keep_warm())]
    if SYNTH:
        asyncio.create_task(asyncio.to_thread(_warm_asr))
    yield
    for t in tasks:
        t.cancel()


KEEP_WARM_S = float(os.environ.get("ASR_KEEP_WARM_S", "240"))  # 0 turns it off


def _warm_asr() -> None:
    try:
        from tower.asr import get_asr
        world.asr = get_asr()
        log.info("ASR ready: %s", type(world.asr).__name__)
        _wake_asr("startup")
    except Exception:
        log.exception("ASR warmup failed; the radio will fall back to typed text")


def _wake_asr(why: str) -> None:
    """The deployed model scales to zero when idle and takes about a minute to return. Met cold on
    the air that was a 20 s transmission. So it is woken when the backend starts, and kept awake
    (one half-second clip every KEEP_WARM_S) while voice is on and a screen is connected."""
    asr = world.asr
    if asr is None or not hasattr(asr, "wake"):
        return
    took = asr.wake()
    if took is None:
        log.warning("speech model did not wake (%s); the local model will hear transmissions until it does", why)
    elif took > 5.0:
        log.info("speech model was asleep: woke in %.0f s (%s)", took, why)


async def keep_warm() -> None:
    while KEEP_WARM_S > 0:
        await asyncio.sleep(KEEP_WARM_S)
        if hub.clients and not world.auto_speak and world.lifecycle in ("running", "ready", "paused"):
            await asyncio.to_thread(_wake_asr, "keep warm")


app = FastAPI(title="Tower", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "scenario": world.scenario.name if world.scenario else None, "t": world.sim.t,
            "lifecycle": world.lifecycle, "speed": world.speed,
            "asr": type(world.asr).__name__ if world.asr else None, "clients": len(hub.clients),
            "memory": world.memory.status()}


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
    for card in [c for c in world.cards.values() if not c.minor]:  # minor shortcuts are never shown
        await ws.send_text(json.dumps({"type": "instruction_card", "payload": card.model_dump(), "t": world.sim.t}))
    if world.scenario is not None:
        # A screen that connects to a loaded-but-not-started world still needs to see the aircraft.
        await ws.send_text(json.dumps({"type": "radar", "t": world.sim.t, "payload": world.radar_payload()},
                                      default=_json_default))
        await ws.send_text(json.dumps({"type": "scoreboard", "t": world.sim.t,
                                       "payload": world.scoreboard().model_dump()}, default=_json_default))
    dictation: Dictation | None = None  # open while a push-to-talk channel is held
    agent_history: list[dict[str, Any]] = []  # squack agent: this connection's last turns
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                if dictation is not None:
                    dictation.feed(msg["bytes"])
                continue
            if msg.get("text") is None:
                continue
            try:
                data = json.loads(msg["text"])
            except json.JSONDecodeError:
                continue
            typ = data.get("type")
            if typ == "ptt_start":
                if dictation is not None:
                    dictation.closed = True  # a start without a stop: the old clip is dropped, as before
                dictation = Dictation(str(data.get("channel", "radio")))
                if dictation.channel == "radio":
                    world.set_ptt(True)  # in Auto, Tower keeps quiet while the human has the mic
            elif typ == "ptt_stop":
                world.set_ptt(False)
                held, dictation = dictation, None
                if held is not None:
                    _spawn(held.finish(), "headset request" if held.channel == "agent" else "transmission")
            elif typ == "agent_text":
                # squack agent: ui_state {selected, planView, speed, voice} lets "follow it" resolve;
                # agent_history is this connection's last 10 turns (backend/agent/loop.py)
                _spawn(world.agent_request(str(data.get("text", "")), agent_history,
                                           data.get("ui_state") if isinstance(data.get("ui_state"), dict) else None),
                       "headset request")
            elif typ == "set_ui_mode":  # squack agent: {"mode": "normal" | "agent"}. set_mode is manual/auto
                world.set_ui_mode(str(data.get("mode", "normal")))
            elif typ == "radio_text":
                _spawn(world.controller_text(str(data.get("text", ""))), "transmission")
            elif typ == "configure" and data.get("source") == "live":
                _configure_live(data)
            elif typ in ("load_scenario", "configure"):
                # configure: {source: "sim" | "real", scenario, density, max_flights}.
                name = str(data.get("scenario") or data.get("name") or "demo")
                density = float(data.get("density") or 1.0)
                cap = data.get("max_flights")
                try:
                    if name not in SC.list_scenarios():
                        raise ValueError(f"unknown scenario {name}")
                    world.load(name, max_flights=int(cap) if cap else None)
                    if abs(density - 1.0) > 1e-6 and not name.startswith("real/"):
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
            elif typ == "set_voice":  # the one switch: on = you say the cards, off = Tower sends them by data link
                world.set_voice(bool(data.get("enabled", False)))
                if data.get("enabled") and SYNTH:
                    asyncio.get_running_loop().run_in_executor(None, _wake_asr, "voice on")
            elif typ == "set_next_readback":  # script the next pilot reply: correct, wrong_value, wrong_aircraft, ...
                world.set_next_readback(str(data.get("mode", "random")))
            elif typ == "confirm_heard":  # said-vs-card conflict: the controller meant what Tower heard
                world.confirm_heard(str(data.get("clearance_id", "")))
            elif typ == "set_auto_voice":  # Auto with Tower's voice (one exchange at a time) or silent and instant
                world.set_auto_voice(bool(data.get("enabled", False)))
            elif typ == "set_speak_replies":  # squack says its answers on the frequency (tower/voice.py)
                world.set_speak_replies(bool(data.get("enabled", True)))
            elif typ == "set_mode":  # {"mode": "manual" | "auto"}: the same switch, by its real name
                world.set_auto_speak(str(data.get("mode", "manual")) == "auto")
            elif typ == "add_disruption":
                # kind: any of disruptions.PROFILES, or "random". No position means Tower's choice.
                x, y = data.get("x_nm"), data.get("y_nm")
                # target: a callsign, "disrupt this flight". Tower puts it on that flight's path ahead.
                world.add_disruption(str(data.get("kind", "random")),
                                     float(x) if x is not None else None, float(y) if y is not None else None,
                                     target=str(data["target"]) if data.get("target") else None)
            elif typ == "remove_disruption":
                world.remove_disruption(str(data.get("id", "")))
            elif typ == "speak_card":
                _spawn(world.speak_card(str(data.get("id", ""))), "card")
            elif typ == "set_sliders":
                world.set_sliders(data.get("buffer_nm"), data.get("error_rate"), data.get("noise"))
            elif typ == "set_speed":
                world.set_speed(float(data.get("speed", 1.0)))
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(ws)
