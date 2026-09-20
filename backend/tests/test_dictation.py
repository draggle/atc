"""Live dictation over the websocket: partials while the key is down, the final before the transcript.

Drives the real FastAPI app with the TestClient: ptt_start, a spoken clip in 100 ms frames paced at
real time (a partial only exists if audio is still arriving while the last one decodes), ptt_stop.
Needs macOS `say` for the clip and the local Whisper model; skipped without either.
"""
from __future__ import annotations

import json
import shutil
import time

import numpy as np
import pytest

from tower.audio import float_to_pcm16, read_wav

PHRASE = "Air Canada one two three, descend flight level two four zero, reduce speed two five zero knots"
FRAME_S = 0.1


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    if shutil.which("say") is None or shutil.which("ffmpeg") is None:
        pytest.skip("macOS say/ffmpeg not available")
    from pilots.tts import TTS

    path = TTS(backend="say", cache_dir=tmp_path_factory.mktemp("tts")).synthesize(PHRASE, "Samantha")
    samples, _sr = read_wav(path)
    return samples


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    try:
        from tower.asr import get_asr

        get_asr().transcribe(np.zeros(16000, dtype=np.float32))  # load the model before the clock runs
    except Exception as exc:  # noqa: BLE001 - model download or CTranslate2 failure
        pytest.skip(f"local Whisper unavailable: {exc!r}")
    import app as APP

    with TestClient(APP.app) as c:
        yield c


def _drain(ws, until, limit: int = 600, wall_s: float = 60.0) -> list[dict]:
    seen: list[dict] = []
    t0 = time.monotonic()
    for _ in range(limit):
        ev = ws.receive_json()
        seen.append(ev)
        if until(ev) or time.monotonic() - t0 > wall_s:
            break
    return seen


def test_partials_then_final_then_transcript(client, clip):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "load_scenario", "name": "demo"})
        ws.send_json({"type": "start"})
        _drain(ws, lambda e: e["type"] == "state" and e["payload"].get("lifecycle") == "running")

        ws.send_json({"type": "ptt_start", "channel": "radio"})
        pcm = float_to_pcm16(clip)
        step = int(16000 * FRAME_S) * 2
        for i in range(0, len(pcm), step):
            ws.send_bytes(pcm[i:i + step])
            time.sleep(FRAME_S)
        ws.send_json({"type": "ptt_stop"})

        seen = _drain(ws, lambda e: e["type"] == "transcript" and e["payload"].get("speaker") == "controller")

    kinds = [(e["type"], e["payload"].get("final")) for e in seen if e["type"] in ("dictation", "transcript")]
    dictation = [e["payload"] for e in seen if e["type"] == "dictation"]
    assert dictation, f"no dictation events among {sorted({e['type'] for e in seen})}"
    for p in dictation:
        assert p["channel"] == "radio" and isinstance(p["text"], str) and p["t_audio_s"] > 0
    partials = [p for p in dictation if not p["final"]]
    finals = [p for p in dictation if p["final"]]
    assert partials, f"no partial before the final: {kinds}"
    assert len(finals) == 1, kinds
    assert dictation[-1]["final"], "the final must be the last dictation event"
    assert any(w in finals[0]["text"].lower() for w in ("canada", "two four zero", "240")), finals[0]
    assert kinds[-1][0] == "transcript" and kinds.index(("dictation", True)) < len(kinds) - 1, kinds
    # every partial saw more audio than the one before
    lengths = [p["t_audio_s"] for p in partials]
    assert lengths == sorted(lengths) and lengths[0] >= 1.2, lengths


def test_partial_after_release_is_dropped():
    """A partial that returns after ptt_stop never overtakes the final."""
    import asyncio

    import app as APP

    async def run():
        out: list[dict] = []
        emit, APP.world.emit = APP.world.emit, out.append
        try:
            d = APP.Dictation("agent")
            d.closed = True
            await d._partial(np.zeros(16000, dtype=np.float32), 1.0)  # a decode that lands late
        finally:
            APP.world.emit = emit
        return [e for e in out if e["type"] == "dictation"]  # the world's own clock may emit radar meanwhile

    assert asyncio.run(run()) == []


def test_short_tap_is_not_routed():
    import asyncio

    import app as APP

    async def run():
        out: list[dict] = []
        emit, APP.world.emit = APP.world.emit, out.append
        try:
            d = APP.Dictation("radio")
            d.feed(b"\x00" * 3200)  # 0.1 s: a stray key press, below the 0.4 s floor
            await d.finish()
        finally:
            APP.world.emit = emit
        return [e for e in out if e["type"] == "dictation"], d.task

    out, task = asyncio.run(run())
    assert out == [] and task is None
