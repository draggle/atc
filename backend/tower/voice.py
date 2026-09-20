"""squack's voice: every answer it gives the user is also said on the frequency.

Same TTS and radio effect as the pilots (pilots/tts.py, pilots/radio.py), one dedicated voice so it
is never mistaken for a pilot or for Tower reading a card, and the clip goes out as a `radio_audio`
event with speaker "squack" that the screen plays like any other transmission.

Callers: `World.agent_request` today; the agent loop's `answer` event should call `speak_reply` too.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from pilots.radio import apply_to_file
from pilots.tts import SAY_VOICES, _all_say_voices
from schemas import event
from tower.audio import read_wav

if TYPE_CHECKING:
    from world import World

log = logging.getLogger("tower.voice")

MAX_SPEECH_CHARS = 240  # a long answer stays on the card; only its opening holds the frequency
NOISE_SCALE = 0.5  # squack's headset is cleaner than a cockpit: half the pilots' noise
# ElevenLabs premade voice for squack (Sarah, american female). Not in the pilot list nor the
# controller voice. Override with SQUACK_VOICE_ID.
ELEVEN_SQUACK_VOICE = "EXAVITQu4vr4xnSDxMaL"
# macOS voices in order of preference, none of them in the pilot pool (SAY_VOICES) when possible.
SAY_SQUACK_VOICES = ["Flo", "Eddy", "Shelley", "Samantha"]


def squack_voice(world: "World") -> str:
    """The voice squack speaks with, valid for whichever TTS backend the world runs."""
    tts = world.tts
    assert tts is not None
    forced = os.environ.get("SQUACK_VOICE_ID", "").strip()
    if tts.backend == "elevenlabs":
        return forced or ELEVEN_SQUACK_VOICE
    if tts.backend == "say":
        installed = _all_say_voices()
        if forced and forced in installed:
            return forced
        pilots = set(tts.voices)
        free = [v for v in SAY_SQUACK_VOICES if v in installed and v not in pilots]
        return free[0] if free else next((v for v in SAY_SQUACK_VOICES if v in installed), tts.voices[0])
    return tts.voices[0]


def speech_text(text: str, limit: int = MAX_SPEECH_CHARS) -> str:
    """The part of `text` that is said: the whole thing if short, else the first `limit` characters
    cut back to a sentence end (or a word end when there is no sentence end that early)."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if cut > 0:
        return head[: cut + 1]
    cut = head.rfind(" ")
    return (head[:cut] if cut > 0 else head).rstrip(",;:") + "."


def _synthesize(world: "World", said: str, voice_id: str) -> tuple[str, float]:
    """Thread body: TTS (cached by text in pilots/tts.py), radio effect, write into AUDIO_DIR."""
    from world import AUDIO_DIR  # local: world imports this module's caller

    assert world.tts is not None
    noise = max(0.05, world.noise * NOISE_SCALE)
    key = hashlib.sha1(f"{world.tts.backend}|{voice_id}|{noise:.3f}|{said}".encode()).hexdigest()[:12]
    ref = f"sq-{key}.wav"
    dst = AUDIO_DIR / ref
    if not dst.exists():
        clean = world.tts.synthesize(said, voice_id)
        apply_to_file(clean, dst, noise)
    samples, sr = read_wav(dst)
    return ref, len(samples) / sr


async def speak_reply(world: "World", text: str, *, voice_id: str | None = None) -> str | None:
    """Say `text` on the frequency as squack. Returns the audio_ref, or None when nothing was said
    (voice off, no TTS, empty text, or synthesis failed: logged, never raised)."""
    if not getattr(world, "speak_replies", True) or world.tts is None:
        return None
    said = speech_text(text or "")
    if not said:
        return None
    try:
        voice = voice_id or squack_voice(world)
        ref, dur = await asyncio.to_thread(_synthesize, world, said, voice)
    except Exception as exc:  # noqa: BLE001 - a lost voice must never lose the answer
        log.warning("squack could not speak its reply: %r", exc)
        return None
    world.emit(event("radio_audio", {"speaker": "squack", "callsign": None, "audio_ref": ref,
                                     "duration_s": round(dur, 2)}, t=world.sim.t))
    return ref


__all__ = ["speak_reply", "speech_text", "squack_voice", "MAX_SPEECH_CHARS"]
