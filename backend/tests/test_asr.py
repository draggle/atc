import shutil

import pytest

from tower.asr import BasetenWhisper, dataset_normalize, logprob_to_confidence

PHRASE = "air canada one two three descend flight level two four zero"


def test_dataset_normalize():
    assert dataset_normalize("Air Canada 123, descend flight level 240.") == (
        "air canada one two three descend flight level two four zero"
    )
    assert dataset_normalize("Contact departure 124.65, good day!") == (
        "contact departure one two four decimal six five good day"
    )
    assert dataset_normalize("Turn left heading 270") == "turn left heading two seven zero"
    assert dataset_normalize("Runway 24L") == "runway two four l"


def test_logprob_to_confidence():
    assert logprob_to_confidence(0.0) == 1.0
    assert 0.3 < logprob_to_confidence(-1.0) < 0.4
    assert logprob_to_confidence(None) == 0.5


def test_baseten_extract_shapes():
    assert BasetenWhisper._extract({"text": " hello ", "avg_logprob": -0.2}) == ("hello", -0.2, [])
    text, lp, nb = BasetenWhisper._extract(
        {"model_output": {"segments": [{"text": "a", "avg_logprob": -0.4}, {"text": "b", "avg_logprob": -0.6}],
                          "n_best": ["a b", {"text": "a d"}]}}
    )
    assert text == "a b" and abs(lp + 0.5) < 1e-9 and nb == ["a b", "a d"]


@pytest.fixture(scope="module")
def spoken_clip(tmp_path_factory):
    if shutil.which("say") is None or shutil.which("ffmpeg") is None:
        pytest.skip("macOS say/ffmpeg not available")
    from pilots.tts import TTS

    tts = TTS(backend="say", cache_dir=tmp_path_factory.mktemp("tts"))
    return tts.synthesize(PHRASE, "Samantha")


@pytest.fixture(scope="module")
def local_whisper():
    try:
        from tower.asr import LocalWhisper

        return LocalWhisper("base.en")
    except Exception as exc:  # model download or CTranslate2 failure
        pytest.skip(f"LocalWhisper unavailable: {exc!r}")


def test_local_whisper_transcribes_say_clip(spoken_clip, local_whisper):
    res = local_whisper.transcribe(spoken_clip, prompt="air canada, westjet, flight level")
    norm = dataset_normalize(res.text)
    print("\nASR:", res.text, "| conf", round(res.confidence, 3), "| latency", round(res.latency_s, 2), "s")
    assert res.n_best and res.n_best[0] == res.text
    assert 0.0 <= res.confidence <= 1.0
    assert "two four zero" in norm or "two forty" in norm
    assert "canada" in norm
    assert res.latency_s < 30


def test_local_whisper_accepts_arrays_and_radio_audio(spoken_clip, local_whisper):
    from pilots.radio import radio_effect
    from tower.audio import read_wav

    samples, sr = read_wav(spoken_clip)
    noisy = radio_effect(samples, sr, noise_level=0.2, rng=0)
    res = local_whisper.transcribe(noisy, prompt=PHRASE)
    norm = dataset_normalize(res.text)
    print("\nASR(radio):", res.text)
    assert "canada" in norm or "two four zero" in norm


# --------------------------------------------------------------------------- remote with a local net

def test_fallback_hears_the_transmission_when_the_remote_fails():
    import numpy as np

    from tower.asr import ASRResult, WithFallback

    class Dead:
        calls = 0

        def transcribe(self, samples, prompt=None, **kw):
            Dead.calls += 1
            raise RuntimeError("network down")

    class Local:
        def transcribe(self, samples, prompt=None):
            return ASRResult(text="air canada one two three", confidence=0.9, n_best=["air canada one two three"],
                             latency_s=0.0, backend="local")

    asr = WithFallback(Dead(), Local, cooldown_s=60)
    a = asr.transcribe(np.zeros(1600, dtype=np.float32))
    b = asr.transcribe(np.zeros(1600, dtype=np.float32))
    assert a.backend == b.backend == "local" and a.text.startswith("air canada")
    assert Dead.calls == 1  # the second call did not wait on a dead network again


def test_fast_mode_is_one_beam_and_does_not_wait_for_the_comparison():
    """The controller's own voice: greedy decode, and the stock model's version arrives later."""
    import threading

    import numpy as np

    from tower.asr import ASRResult, BasetenWhisper, StockAndTuned

    sent: list[dict] = []
    remote = BasetenWhisper("https://example.invalid/predict", api_key="x", beam_size=3, n_best=3)
    remote._post = lambda body: (sent.append(body) or {"text": "air canada one two three turn left heading two seven zero"})
    remote.transcribe(np.zeros(1600, dtype=np.float32), "ACA123")
    remote.transcribe(np.zeros(1600, dtype=np.float32), "ACA123", fast=True)
    assert (sent[0]["beam_size"], sent[0]["n_best"]) == (3, 3)
    assert (sent[1]["beam_size"], sent[1]["n_best"]) == (1, 1)

    release = threading.Event()
    arrived = threading.Event()
    got: list[str] = []

    class SlowStock:
        def transcribe(self, samples, prompt=None):
            release.wait(5)
            return ASRResult(text="stock version", confidence=0.5, n_best=[], latency_s=0.0, backend="stock")

    both = StockAndTuned(remote, SlowStock())
    r = both.transcribe(np.zeros(1600, dtype=np.float32), "ACA123", fast=True,
                        on_stock=lambda t: (got.append(t), arrived.set()))
    assert r.text.startswith("air canada") and r.text_stock is None and not got  # answered without the comparison
    release.set()
    assert arrived.wait(5) and got == ["stock version"]
