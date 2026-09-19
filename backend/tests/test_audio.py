import numpy as np
from scipy.signal import welch

from pilots.radio import radio_effect
from tower.audio import (
    UtteranceChunker,
    VAD,
    float_to_pcm16,
    float_to_wav,
    pcm16_to_float,
    read_wav,
    silence,
    synth_test_tone,
)


def test_pcm_roundtrip():
    x = synth_test_tone(0.1, amp=0.5)
    y = pcm16_to_float(float_to_pcm16(x))
    assert y.shape == x.shape
    assert np.max(np.abs(x - y)) < 1e-3


def test_wav_roundtrip_and_resample(tmp_path):
    x = synth_test_tone(0.5, sr=44100)
    p = float_to_wav(tmp_path / "a.wav", x, 44100)
    y, sr = read_wav(p)
    assert sr == 16000
    assert abs(len(y) - 8000) <= 2


def test_chunker_splits_tone_silence_tone_and_drops_blip():
    sr = 16000
    buf = np.concatenate([
        silence(0.5), synth_test_tone(1.0), silence(0.6), synth_test_tone(0.8),
        silence(0.6), synth_test_tone(0.2), silence(0.6),
    ])
    ch = UtteranceChunker(VAD(backend="energy"), sr=sr)
    utts = []
    pcm = float_to_pcm16(buf)
    step = 3210  # deliberately odd byte count so frames straddle pushes
    for i in range(0, len(pcm), step):
        utts += ch.push(pcm[i : i + step])
    utts += ch.flush()
    assert len(utts) == 2, [len(u) / sr for u in utts]
    assert 0.9 <= len(utts[0]) / sr <= 1.6
    assert 0.7 <= len(utts[1]) / sr <= 1.4
    assert ch.dropped == 1
    assert all(u.dtype == np.float32 for u in utts)


def test_chunker_flush_returns_in_progress_utterance():
    ch = UtteranceChunker(VAD(backend="energy"))
    assert ch.push(float_to_pcm16(synth_test_tone(1.0))) == []
    out = ch.flush()
    assert len(out) == 1 and len(out[0]) >= 16000 * 0.9


def test_silero_vad_prefers_speech_shaped_signal_if_available():
    try:
        vad = VAD(backend="silero")
    except Exception:
        return  # not installed here; energy fallback covered elsewhere
    assert vad.backend == "silero"
    p_silence = vad.prob(np.zeros(512, dtype=np.float32))
    assert p_silence < 0.5


def _band_energy(x, sr, lo, hi):
    f, pxx = welch(x, fs=sr, nperseg=2048)
    return float(pxx[(f >= lo) & (f < hi)].sum())


def test_radio_effect_length_and_band():
    sr = 16000
    t = np.arange(sr * 2) / sr
    x = (0.3 * np.sin(2 * np.pi * 100 * t) + 0.3 * np.sin(2 * np.pi * 1000 * t)
         + 0.3 * np.sin(2 * np.pi * 6000 * t)).astype(np.float32)
    y = radio_effect(x, sr, noise_level=0.1, rng=0)
    assert y.shape == x.shape and y.dtype == np.float32
    assert np.max(np.abs(y)) <= 1.0
    in_before = _band_energy(x, sr, 300, 3400)
    out_before = _band_energy(x, sr, 0, 250) + _band_energy(x, sr, 4000, 8000)
    in_after = _band_energy(y, sr, 300, 3400)
    out_after = _band_energy(y, sr, 0, 250) + _band_energy(y, sr, 4000, 8000)
    assert out_after / in_after < 0.05 * (out_before / in_before)


def test_radio_effect_noise_scales():
    x = synth_test_tone(1.0, freq_hz=1000)
    quiet = radio_effect(x, noise_level=0.0, rng=1, clicks=False)
    loud = radio_effect(x, noise_level=1.0, rng=1, clicks=False)
    # noise shows up as off-tone energy relative to the tone line (absolute levels shift with peak normalization)
    def off_ratio(y):
        return _band_energy(y, 16000, 1500, 3400) / _band_energy(y, 16000, 950, 1050)
    assert off_ratio(loud) > 20 * off_ratio(quiet)


def test_radio_effect_deterministic_under_seed():
    x = synth_test_tone(1.0)
    a = radio_effect(x, noise_level=0.7, rng=42)
    b = radio_effect(x, noise_level=0.7, rng=42)
    assert np.array_equal(a, b)
