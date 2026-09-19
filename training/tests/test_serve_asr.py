"""The serving package's plain-Python parts. The model itself only runs on Baseten."""
import base64
import importlib.util
import io
from pathlib import Path

import numpy as np
import soundfile as sf

spec = importlib.util.spec_from_file_location(
    "serve_asr_model", Path(__file__).resolve().parents[1] / "serve_asr" / "model" / "model.py")
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


def wav_b64(samples: np.ndarray, sr: int) -> str:
    buf = io.BytesIO()
    sf.write(buf, samples, sr, format="WAV", subtype="PCM_16")
    return base64.b64encode(buf.getvalue()).decode()


def test_decode_resamples_and_mixes_down():
    tone = np.sin(np.linspace(0, 200, 44100)).astype(np.float32)
    stereo = np.stack([tone, tone], axis=1)
    out = M.decode_audio(wav_b64(stereo, 44100))
    assert out.dtype == np.float32 and out.ndim == 1
    assert abs(out.size - 16000) <= 1


def test_decode_caps_at_one_window():
    out = M.decode_audio(wav_b64(np.zeros(16000 * 45, dtype=np.float32), 16000))
    assert out.size == 16000 * 30


def test_find_checkpoint_prefers_best(tmp_path):
    for name in ("checkpoint-500", "best"):
        d = tmp_path / "q9jj663" / "rank-0" / name
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}")
        (d / "model.safetensors").write_bytes(b"")
    assert M.find_checkpoint(tmp_path).name == "best"
    assert M.find_checkpoint(tmp_path / "nothing") is None


def test_predict_shape_matches_the_client(monkeypatch):
    m = M.Model()
    m._name = "tuned test"
    monkeypatch.setattr(m, "_transcribe", lambda audio, prompt, beams, n: [
        {"text": "air canada one two three descend flight level two four zero", "avg_logprob": -0.12},
        {"text": "air canada one two three descend flight level two five zero", "avg_logprob": -0.31}][:n])
    out = m.predict({"audio": wav_b64(np.zeros(16000, dtype=np.float32), 16000), "prompt": "ACA123", "beam_size": 5})
    assert out["text"].startswith("air canada") and out["avg_logprob"] == -0.12
    assert [h["text"] for h in out["n_best"]][1].endswith("two five zero")
    assert out["model"] == "tuned test"
    # what backend/tower/asr.py::BasetenWhisper._extract reads
    assert {"text", "avg_logprob", "n_best"} <= set(out)


def test_predict_handles_silence_and_bad_input():
    m = M.Model()
    assert m.predict({})["text"] == "" and "error" in m.predict({})
    assert m.predict({"audio": wav_b64(np.zeros(100, dtype=np.float32), 16000)})["n_best"] == []
