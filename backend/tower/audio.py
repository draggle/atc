"""Audio ingest: PCM helpers, WAV I/O, voice activity detection, utterance chunking.

Everything is 16 kHz mono float32 in [-1, 1] internally. The browser and the AI pilots
both feed PCM16 frames into `UtteranceChunker.push`, which yields complete utterances
once ~300 ms of silence follows speech and drops anything shorter than 0.5 s (07 §7.1).

VAD backends:
  silero  the silero-vad ONNX model (pip `silero-vad`, pulls onnxruntime + torch). Default
          when importable. Works on 512-sample frames at 16 kHz.
  energy  RMS threshold with a noise-floor tracker. Fallback, and the right choice for
          tests that use synthetic tones, which silero (speech-trained) ignores.
"""
from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import numpy as np
import soundfile as sf

SR = 16000
SILERO_FRAME = 512  # samples per silero frame at 16 kHz (32 ms)

# ---------------------------------------------------------------------------
# PCM and WAV helpers
# ---------------------------------------------------------------------------


def pcm16_to_float(data: bytes) -> np.ndarray:
    if len(data) % 2:
        data = data[:-1]
    return (np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0)


def float_to_pcm16(samples: np.ndarray) -> bytes:
    x = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    return (x * 32767.0).astype("<i2").tobytes()


def float_to_wav(path: str | Path, samples: np.ndarray, sr: int = SR) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    x = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    sf.write(str(path), x, sr, subtype="PCM_16")
    return path


def resample(samples: np.ndarray, sr_in: int, sr_out: int = SR) -> np.ndarray:
    if sr_in == sr_out:
        return samples.astype(np.float32)
    from scipy.signal import resample_poly

    g = math.gcd(sr_in, sr_out)
    return resample_poly(samples, sr_out // g, sr_in // g).astype(np.float32)


def read_wav(path: str | Path, sr: int = SR) -> tuple[np.ndarray, int]:
    """Read any soundfile-supported file as float32 mono at `sr` (default 16 kHz)."""
    data, sr_in = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    return resample(mono, sr_in, sr), sr


def wav_bytes(samples: np.ndarray, sr: int = SR) -> bytes:
    """Encode to an in-memory PCM16 WAV, for base64 upload to Baseten."""
    import io

    buf = io.BytesIO()
    sf.write(buf, np.clip(samples, -1, 1).astype(np.float32), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def synth_test_tone(duration_s: float = 1.0, freq_hz: float = 440.0, sr: int = SR, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(sr * duration_s)) / sr
    return (amp * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)


def silence(duration_s: float, sr: int = SR) -> np.ndarray:
    return np.zeros(int(sr * duration_s), dtype=np.float32)


# ---------------------------------------------------------------------------
# VAD
# ---------------------------------------------------------------------------

VADBackend = Literal["silero", "energy", "auto"]


class VAD:
    """Frame-level speech probability. Call `prob(frame)` with 512 float32 samples at 16 kHz."""

    def __init__(self, backend: VADBackend = "auto", threshold: float = 0.5, energy_threshold: float = 0.02):
        self.threshold = threshold
        self.energy_threshold = energy_threshold
        self._model = None
        self._noise_floor = 1e-3
        if backend in ("auto", "silero"):
            try:
                import silero_vad

                self._model = silero_vad.load_silero_vad(onnx=True)
                self.backend = "silero"
            except Exception:
                if backend == "silero":
                    raise
                self.backend = "energy"
        else:
            self.backend = "energy"

    def reset(self) -> None:
        if self._model is not None:
            self._model.reset_states()
        self._noise_floor = 1e-3

    def prob(self, frame: np.ndarray, sr: int = SR) -> float:
        frame = np.asarray(frame, dtype=np.float32)
        if self.backend == "silero":
            import torch

            if len(frame) != SILERO_FRAME:
                frame = np.pad(frame, (0, max(0, SILERO_FRAME - len(frame))))[:SILERO_FRAME]
            return float(self._model(torch.from_numpy(frame), sr).item())
        rms = float(np.sqrt(np.mean(frame**2))) if len(frame) else 0.0
        # Slowly track the floor down, quickly up when quiet.
        if rms < self._noise_floor * 2:
            self._noise_floor = 0.9 * self._noise_floor + 0.1 * max(rms, 1e-5)
        thresh = max(self.energy_threshold, self._noise_floor * 4)
        return 1.0 if rms > thresh else 0.0

    def is_speech(self, frame: np.ndarray, sr: int = SR) -> bool:
        return self.prob(frame, sr) >= self.threshold


# ---------------------------------------------------------------------------
# Utterance chunker
# ---------------------------------------------------------------------------


class UtteranceChunker:
    """Turn a stream of PCM16 frames into complete utterances.

    push(bytes) -> list of float32 arrays completed by this frame (usually empty or one).
    flush()     -> any in-progress utterance, e.g. when push-to-talk releases.
    """

    def __init__(
        self,
        vad: VAD | None = None,
        sr: int = SR,
        frame_samples: int = SILERO_FRAME,
        silence_ms: float = 300.0,
        min_utterance_s: float = 0.5,
        max_utterance_s: float = 20.0,
        pre_roll_ms: float = 200.0,
    ):
        self.vad = vad or VAD()
        self.sr = sr
        self.frame_samples = frame_samples
        self.silence_frames = max(1, int(round(silence_ms / 1000.0 * sr / frame_samples)))
        self.min_samples = int(min_utterance_s * sr)
        self.max_samples = int(max_utterance_s * sr)
        self.pre_roll = deque(maxlen=max(1, int(round(pre_roll_ms / 1000.0 * sr / frame_samples))))
        self._pending = b""
        self._active: list[np.ndarray] = []
        self._active_len = 0
        self._preroll_len = 0
        self._silence_run = 0
        self.dropped = 0
        self.emitted = 0

    # -- frame iteration ---------------------------------------------------

    def _frames(self, data: bytes) -> Iterator[np.ndarray]:
        buf = self._pending + data
        step = self.frame_samples * 2
        n_full = len(buf) // step
        for i in range(n_full):
            yield pcm16_to_float(buf[i * step : (i + 1) * step])
        self._pending = buf[n_full * step :]

    def push(self, data: bytes) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        for frame in self._frames(data):
            utt = self._push_frame(frame)
            if utt is not None:
                out.append(utt)
        return out

    def push_samples(self, samples: np.ndarray) -> list[np.ndarray]:
        return self.push(float_to_pcm16(samples))

    def _push_frame(self, frame: np.ndarray) -> np.ndarray | None:
        speech = self.vad.is_speech(frame, self.sr)
        if not self._active:
            if speech:
                self._active = list(self.pre_roll) + [frame]
                self._preroll_len = sum(len(f) for f in self.pre_roll)
                self._active_len = self._preroll_len + len(frame)
                self._silence_run = 0
            else:
                self.pre_roll.append(frame)
            return None

        self._active.append(frame)
        self._active_len += len(frame)
        self._silence_run = 0 if speech else self._silence_run + 1
        if self._silence_run >= self.silence_frames or self._active_len >= self.max_samples:
            return self._finish()
        return None

    def _finish(self) -> np.ndarray | None:
        if not self._active:
            return None
        utt = np.concatenate(self._active)
        # Only speech counts toward the minimum, not the pre-roll or the closing silence.
        speech_samples = self._active_len - self._preroll_len - self._silence_run * self.frame_samples
        self._active = []
        self._active_len = 0
        self._preroll_len = 0
        self._silence_run = 0
        self.pre_roll.clear()
        if speech_samples < self.min_samples:
            self.dropped += 1
            return None
        self.emitted += 1
        return utt

    def flush(self) -> list[np.ndarray]:
        if self._pending:
            tail = pcm16_to_float(self._pending)
            self._pending = b""
            if self._active:
                self._active.append(tail)
                self._active_len += len(tail)
        utt = self._finish()
        return [utt] if utt is not None else []


__all__ = [
    "SR",
    "VAD",
    "UtteranceChunker",
    "float_to_pcm16",
    "float_to_wav",
    "pcm16_to_float",
    "read_wav",
    "resample",
    "silence",
    "synth_test_tone",
    "wav_bytes",
]
