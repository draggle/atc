"""Text to speech for the AI pilots. Always yields 16 kHz mono PCM16 WAV.

Backends, picked by `pick_backend()`:
  elevenlabs  when ELEVENLABS_API_KEY is set. POST /v1/text-to-speech/{voice}?output_format=pcm_16000
  say         macOS `say -v <voice> -o x.aiff` then ffmpeg to 16 kHz mono wav
  silent      a short beep so the pipeline runs on a machine with neither

One voice per callsign, chosen deterministically by CRC32 of the callsign, so ACA123
sounds the same across runs and machines with the same backend. Results are cached under
data/tts_cache/<sha1(backend, voice, text)>.wav, which is gitignored.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import zlib
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "tts_cache"

# English macOS voices that stock Whisper base.en still understands after the radio effect at
# noise 0.3 (measured Sept 19). Kathy, Reed, Sandy and Fred were dropped: garbled even clean.
SAY_VOICES = ["Daniel", "Karen", "Moira", "Tessa", "Rishi", "Samantha"]

# ElevenLabs premade voice ids. Override with ELEVENLABS_VOICE_IDS=id1,id2,...
ELEVEN_VOICES = [
    "21m00Tcm4TlvDq8ikWAM",  # Rachel
    "pNInz6obpgDQGcFmaJgB",  # Adam
    "ErXwobaYiN019PkySvjV",  # Antoni
    "EXAVITQu4vr4xnSDxMaL",  # Bella
    "TxGEqnHWrfWFTfGW9XjX",  # Josh
    "VR6AewLTigWG4xSOukaG",  # Arnold
    "AZnzlk1XvdvUeBnXmlld",  # Domi
    "MF3mGyEYCl7XYWbV9V6O",  # Elli
]


def _installed_say_voices() -> list[str]:
    if shutil.which("say") is None:
        return []
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    names = {line.split()[0] for line in out.splitlines() if line.strip()}
    return [v for v in SAY_VOICES if v in names]


def pick_backend() -> str:
    forced = os.environ.get("TTS_BACKEND")
    if forced:
        return forced
    if os.environ.get("ELEVENLABS_API_KEY"):
        return "elevenlabs"
    if shutil.which("say") and shutil.which("ffmpeg"):
        return "say"
    return "silent"


def _ffmpeg_to_wav16k(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", "16000",
         "-sample_fmt", "s16", str(dst)],
        check=True,
        capture_output=True,
    )


class TTS:
    def __init__(
        self,
        backend: str | None = None,
        cache_dir: str | Path | None = None,
        rate_wpm: int = 200,
        voices: list[str] | None = None,
    ):
        self.backend = backend or pick_backend()
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self.rate_wpm = rate_wpm  # pilots talk fast; macOS default is ~175
        self.last_error: str | None = None
        if voices is not None:
            self.voices = voices
        elif self.backend == "say":
            self.voices = _installed_say_voices() or ["Samantha"]
        elif self.backend == "elevenlabs":
            env = os.environ.get("ELEVENLABS_VOICE_IDS", "")
            self.voices = [v for v in env.split(",") if v.strip()] or ELEVEN_VOICES
        else:
            self.voices = ["beep"]

    # -- voice assignment -------------------------------------------------

    def voice_for(self, callsign: str) -> str:
        idx = zlib.crc32(callsign.upper().encode()) % len(self.voices)
        return self.voices[idx]

    # -- synthesis --------------------------------------------------------

    def cache_path(self, text: str, voice_id: str) -> Path:
        key = hashlib.sha1(f"{self.backend}|{voice_id}|{self.rate_wpm}|{text}".encode()).hexdigest()
        return self.cache_dir / f"{key}.wav"

    def synthesize(self, text: str, voice_id: str | None = None, out_path: str | Path | None = None) -> Path:
        """Synthesize `text` to a 16 kHz mono WAV. Returns the written path.

        If `out_path` is None the cache path is returned directly. Otherwise the cached
        file is copied to `out_path`.
        """
        voice_id = voice_id or self.voices[0]
        cached = self.cache_path(text, voice_id)
        if not cached.exists():
            cached.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._synthesize_uncached(text, voice_id, cached)
            except Exception as exc:  # fall back rather than kill the sim loop
                if self.backend == "silent":
                    raise
                SilentTTS(cache_dir=self.cache_dir)._synthesize_uncached(text, voice_id, cached)
                self.last_error = repr(exc)
        if out_path is None:
            return cached
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cached, out_path)
        return out_path

    def _synthesize_uncached(self, text: str, voice_id: str, dst: Path) -> None:
        if self.backend == "elevenlabs":
            self._eleven(text, voice_id, dst)
        elif self.backend == "say":
            self._say(text, voice_id, dst)
        else:
            SilentTTS(cache_dir=self.cache_dir)._synthesize_uncached(text, voice_id, dst)

    def _say(self, text: str, voice: str, dst: Path) -> None:
        with tempfile.TemporaryDirectory() as td:
            aiff = Path(td) / "tts.aiff"
            subprocess.run(
                ["say", "-v", voice, "-r", str(self.rate_wpm), "-o", str(aiff), text],
                check=True,
                capture_output=True,
                timeout=60,
            )
            _ffmpeg_to_wav16k(aiff, dst)

    def _eleven(self, text: str, voice_id: str, dst: Path) -> None:
        import httpx

        from tower.audio import float_to_wav, pcm16_to_float

        api_key = os.environ["ELEVENLABS_API_KEY"]
        model_id = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        body = {
            "text": text,
            "model_id": model_id,
            "voice_settings": {"stability": 0.4, "similarity_boost": 0.7, "speed": 1.1},
        }
        headers = {"xi-api-key": api_key, "accept": "audio/*", "content-type": "application/json"}
        with httpx.Client(timeout=60) as client:
            r = client.post(url, params={"output_format": "pcm_16000"}, json=body, headers=headers)
            r.raise_for_status()
            content = r.content
        ctype = r.headers.get("content-type", "")
        if "mpeg" in ctype or content[:3] == b"ID3" or content[:2] == b"\xff\xfb":
            with tempfile.TemporaryDirectory() as td:
                mp3 = Path(td) / "tts.mp3"
                mp3.write_bytes(content)
                _ffmpeg_to_wav16k(mp3, dst)
            return
        float_to_wav(dst, pcm16_to_float(content), 16000)


class SilentTTS(TTS):
    """Writes a short two-tone beep of a length proportional to the text. Keeps pipelines alive."""

    def __init__(self, cache_dir: str | Path | None = None):
        super().__init__(backend="silent", cache_dir=cache_dir, voices=["beep"])

    def _synthesize_uncached(self, text: str, voice_id: str, dst: Path) -> None:
        from tower.audio import float_to_wav

        sr = 16000
        dur = min(6.0, 0.25 + 0.06 * len(text.split()) * 5)
        t = np.arange(int(sr * dur)) / sr
        tone = 0.3 * np.sin(2 * np.pi * 700 * t) * (0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 4 * t)))
        float_to_wav(dst, tone.astype(np.float32), sr)


__all__ = ["TTS", "SilentTTS", "pick_backend", "SAY_VOICES", "ELEVEN_VOICES", "DEFAULT_CACHE_DIR"]
