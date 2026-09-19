"""Radio effect: make clean TTS sound like a narrow-band AM aircraft radio.

Chain: band-pass 300 to 3400 Hz -> light soft clipping -> band-limited noise at
`noise_level` -> squelch clicks at both ends -> optional dropouts at high noise.
The chaos slider in the UI maps straight to `noise_level` (0 clean, 1 barely readable).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

BAND_LO_HZ = 300.0
BAND_HI_HZ = 3400.0


def _bandpass_sos(sr: int, lo: float = BAND_LO_HZ, hi: float = BAND_HI_HZ, order: int = 4):
    nyq = sr / 2.0
    hi = min(hi, nyq * 0.95)
    return signal.butter(order, [lo / nyq, hi / nyq], btype="band", output="sos")


def bandpass(samples: np.ndarray, sr: int = 16000) -> np.ndarray:
    sos = _bandpass_sos(sr)
    return signal.sosfiltfilt(sos, samples).astype(np.float32)


def soft_clip(samples: np.ndarray, drive: float = 1.6) -> np.ndarray:
    """tanh limiter. drive 1 is nearly transparent, 2.5 is crunchy (strong odd harmonics)."""
    return (np.tanh(samples * drive) / np.tanh(drive)).astype(np.float32)


def _squelch_click(sr: int, rng: np.random.Generator, ms: float = 12.0, amp: float = 0.35) -> np.ndarray:
    n = int(sr * ms / 1000.0)
    t = np.arange(n) / sr
    burst = rng.standard_normal(n) * np.exp(-t * 400.0)
    tone = np.sin(2 * np.pi * 1800.0 * t) * np.exp(-t * 600.0)
    return (amp * (0.6 * burst + 0.8 * tone)).astype(np.float32)


def radio_effect(
    samples: np.ndarray,
    sr: int = 16000,
    noise_level: float = 0.2,
    rng: np.random.Generator | int | None = None,
    clip_drive: float = 1.6,
    clicks: bool = True,
) -> np.ndarray:
    """Return a new float32 array the same length as `samples`.

    noise_level scales additive band-limited noise relative to the speech RMS:
    0.2 is roughly 14 dB SNR, 1.0 is 0 dB. Above 0.5, random 50 to 150 ms dropouts appear.
    """
    if not isinstance(rng, np.random.Generator):
        rng = np.random.default_rng(rng)
    x = np.asarray(samples, dtype=np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    n = len(x)
    if n == 0:
        return x

    y = bandpass(x, sr)
    peak = float(np.max(np.abs(y))) or 1.0
    y = y / peak * 0.8
    y = soft_clip(y, clip_drive)

    noise_level = float(max(0.0, noise_level))
    if noise_level > 0:
        speech_rms = float(np.sqrt(np.mean(y**2))) or 1e-3
        noise = rng.standard_normal(n).astype(np.float32)
        noise = bandpass(noise, sr)
        noise_rms = float(np.sqrt(np.mean(noise**2))) or 1e-6
        noise *= (speech_rms * noise_level) / noise_rms
        y = y + noise

    if noise_level > 0.5:
        # Dropouts: a few 50 to 150 ms holes, more of them as noise rises.
        n_drop = int(rng.integers(1, 2 + int(4 * (noise_level - 0.5))))
        for _ in range(n_drop):
            hole = int(sr * rng.uniform(0.05, 0.15))
            if hole >= n:
                break
            start = int(rng.integers(0, n - hole))
            y[start : start + hole] *= 0.05

    if clicks:
        click = _squelch_click(sr, rng)
        k = min(len(click), n)
        y[:k] += click[:k]
        y[n - k :] += click[:k][::-1]

    peak = float(np.max(np.abs(y))) or 1.0
    if peak > 0.95:
        y = y / peak * 0.95
    return y.astype(np.float32)


def apply_to_file(in_path: str | Path, out_path: str | Path, noise_level: float = 0.2, seed: int | None = None) -> Path:
    """Read any wav, apply the effect, write 16 kHz mono PCM16 wav."""
    from tower.audio import float_to_wav, read_wav  # local import: tower.audio depends on nothing here

    samples, sr = read_wav(in_path)
    out = radio_effect(samples, sr, noise_level=noise_level, rng=seed)
    return float_to_wav(out_path, out, sr)
