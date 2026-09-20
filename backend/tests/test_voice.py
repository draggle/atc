"""squack's voice (tower/voice.py): its answers go on the frequency as radio_audio clips."""
import asyncio
from pathlib import Path

import pytest

from tower.audio import read_wav
from tower.voice import MAX_SPEECH_CHARS, speak_reply, speech_text
from world import AUDIO_DIR, World


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setenv("TTS_BACKEND", "silent")  # runs anywhere: no `say`, no key
    events: list[dict] = []
    w = World(events.append, synthesize=True, realtime=False)
    w.tts.cache_dir = tmp_path
    w.load("demo")
    return w, events


def _clips(events):
    return [e["payload"] for e in events if e["type"] == "radio_audio"]


def test_reply_goes_on_the_air_as_squack(world):
    w, events = world
    ref = asyncio.run(speak_reply(w, "Traffic doubled. Twelve flights now."))
    assert ref and ref.startswith("sq-") and ref.endswith(".wav")
    clips = _clips(events)
    assert len(clips) == 1
    clip = clips[0]
    assert clip["speaker"] == "squack" and clip["callsign"] is None and clip["audio_ref"] == ref
    path = AUDIO_DIR / ref
    assert path.exists()
    samples, sr = read_wav(path)
    assert sr == 16000 and len(samples) > 1600
    assert abs(clip["duration_s"] - len(samples) / sr) < 0.02
    # said again: same text, same clip, no second synthesis needed
    assert asyncio.run(speak_reply(w, "Traffic doubled. Twelve flights now.")) == ref


def test_long_answers_are_cut_at_a_sentence():
    text = "First sentence here. " * 20  # 420 chars
    said = speech_text(text)
    assert len(said) <= MAX_SPEECH_CHARS and said.endswith("First sentence here.")
    assert len(said) > MAX_SPEECH_CHARS - len("First sentence here. ") - 1
    # no sentence end early enough: cut at a word and close it
    words = "word " * 100
    said = speech_text(words)
    assert len(said) <= MAX_SPEECH_CHARS and said.endswith("word.") and "  " not in said
    assert speech_text("short") == "short"


def test_agent_request_speaks_the_reply(world):
    w, events = world
    reply = asyncio.run(w.agent_request("double the traffic"))
    assert reply
    clips = _clips(events)
    assert len(clips) == 1 and clips[0]["speaker"] == "squack"
    # the full answer is on the card, the clip is capped
    assert [e for e in events if e["type"] == "agent_reply"][-1]["payload"]["text"] == reply


def test_disabled_says_nothing(world):
    w, events = world
    w.set_speak_replies(False)
    assert [e for e in events if e["type"] == "state"][-1]["payload"]["speak_replies"] is False
    assert asyncio.run(speak_reply(w, "Quiet please.")) is None
    assert _clips(events) == []
    assert asyncio.run(speak_reply(w, "   ")) is None  # nothing to say


def test_env_switch_starts_it_off(monkeypatch):
    monkeypatch.setenv("SQUACK_SPEAK", "0")
    w = World(lambda e: None, synthesize=False, realtime=False)
    assert w.speak_replies is False


def test_failure_returns_none(world, monkeypatch):
    w, events = world

    def boom(*a, **k):
        raise RuntimeError("no voice today")

    monkeypatch.setattr(w.tts, "synthesize", boom)
    assert asyncio.run(speak_reply(w, "This one fails to synthesize.")) is None
    assert _clips(events) == []


def test_no_tts_means_no_voice():
    events: list[dict] = []
    w = World(events.append, synthesize=False, realtime=False)
    assert asyncio.run(speak_reply(w, "Hello.")) is None
    assert _clips(events) == []
