"""Speech recognition clients (07 §7.2).

  LocalWhisper    faster-whisper on CPU (CTranslate2). Default when ASR_MODEL_URL is unset.
  BasetenWhisper  our fine-tuned Whisper served from a faster-whisper Truss on Baseten.
  StockAndTuned   runs a tuned and a stock model on the same clip so the transcript event
                  can carry `text_stock` for the side-by-side toggle.

Baseten request shape (matches the faster-whisper Truss example; adjust `_extract` if the
deployed model differs):

    POST {ASR_MODEL_URL}   Authorization: Api-Key {BASETEN_API_KEY}
    {"audio": "<base64 PCM16 WAV>", "prompt": "air canada one two three ...",
     "beam_size": 5, "language": "en"}

    -> {"text": "...", "avg_logprob": -0.31, "segments": [...], "n_best": ["...", ...]}
       (avg_logprob and n_best optional; we also accept "language_probability"/"confidence")

`dataset_normalize` turns raw ASR text into the dataset convention (lowercase, no
punctuation, digits spoken one at a time) so stock Whisper output and our fine-tuned output
enter the normalizer looking the same.

Prompt gotcha, measured on this machine with base.en on radio-filtered `say` audio:
a bare callsign list as `initial_prompt` ("air canada") makes Whisper treat those words as
already spoken and it drops everything up to them ("one two three"). A phraseology sentence
(ATC_PROMPT_PREFIX) roughly halves the loss on every voice tried. So every client here
prefixes the caller's prompt with ATC_PROMPT_PREFIX; pass `raw_prompt=True` to bypass. Use
`build_prompt(callsigns, waypoints)` to format the active list.
"""
from __future__ import annotations

import base64
import math
import os
import re
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np
from pydantic import BaseModel, Field

from .audio import SR, read_wav, wav_bytes

DIGIT_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}


ATC_PROMPT_PREFIX = (
    "Air traffic control radio. descend flight level, climb, turn left heading, reduce speed, "
    "contact departure, squawk, cleared to land runway."
)


def build_prompt(callsigns: list[str] | tuple[str, ...] = (), waypoints: list[str] | tuple[str, ...] = ()) -> str:
    """Prompt text from the active callsigns (spoken form preferred) and nearby waypoints."""
    parts = [ATC_PROMPT_PREFIX]
    if callsigns:
        parts.append("Callsigns: " + ", ".join(callsigns) + ".")
    if waypoints:
        parts.append("Waypoints: " + ", ".join(w.lower() for w in waypoints) + ".")
    return " ".join(parts)


def full_prompt(prompt: str | None, raw: bool = False) -> str | None:
    if raw:
        return prompt or None
    if not prompt:
        return ATC_PROMPT_PREFIX
    if prompt.startswith(ATC_PROMPT_PREFIX):
        return prompt
    return f"{ATC_PROMPT_PREFIX} Callsigns: {prompt.rstrip('.')}."


log = logging.getLogger("tower.asr")

class ASRResult(BaseModel):
    text: str
    confidence: float = 1.0  # 0..1
    n_best: list[str] = Field(default_factory=list)  # includes text at index 0
    latency_s: float = 0.0
    text_stock: str | None = None
    backend: str = ""


@runtime_checkable
class ASR(Protocol):
    def transcribe(self, samples_or_path: np.ndarray | str | Path, prompt: str | None = None) -> ASRResult: ...


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _spell_number_token(tok: str) -> str:
    out: list[str] = []
    for ch in tok:
        if ch.isdigit():
            out.append(DIGIT_WORDS[ch])
        elif ch in ".,":
            out.append("decimal")
    return " ".join(out)


def dataset_normalize(text: str) -> str:
    """Lowercase, strip punctuation, spell digits one at a time. '124.65' -> 'one two four decimal six five'."""
    t = text.lower().replace("-", " ").replace("/", " ")
    # numbers with optional decimal: 124.65, 240, 29.92
    t = re.sub(r"\d+(?:[.,]\d+)?", lambda m: " " + _spell_number_token(m.group(0)) + " ", t)
    t = re.sub(r"[^\w\s]", " ", t)  # punctuation (after numbers so '.' inside them survived)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def logprob_to_confidence(avg_logprob: float | None) -> float:
    """Whisper avg_logprob is ~-0.1 (sure) to ~-1.5 (guessing). exp() maps that to ~0.9..0.2."""
    if avg_logprob is None or not math.isfinite(avg_logprob):
        return 0.5
    return float(min(1.0, max(0.0, math.exp(avg_logprob))))


def _load_samples(samples_or_path: np.ndarray | str | Path) -> np.ndarray:
    if isinstance(samples_or_path, (str, Path)):
        samples, _ = read_wav(samples_or_path, SR)
        return samples
    return np.asarray(samples_or_path, dtype=np.float32)


# ---------------------------------------------------------------------------
# Local faster-whisper
# ---------------------------------------------------------------------------


class LocalWhisper:
    """faster-whisper on CPU. First use downloads the model (~150 MB for base.en) into the HF cache.

    n_best: faster-whisper does not expose beam alternatives, so extra hypotheses come from
    re-decoding at the sampling temperatures in `n_best_temperatures` (each costs one more
    decode). With the default empty tuple, n_best == [text]. The resolver's re-listen tool
    can pass temperatures explicitly when it needs the top-5 rule.
    """

    def __init__(
        self,
        model_size: str = "base.en",
        beam_size: int = 5,
        n_best: int = 5,
        n_best_temperatures: tuple[float, ...] = (),
        compute_type: str = "int8",
        device: str = "cpu",
    ):
        from faster_whisper import WhisperModel

        self.model_size = model_size
        self.beam_size = beam_size
        self.n_best = n_best
        self.n_best_temperatures = n_best_temperatures
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)

    def _decode(self, samples: np.ndarray, prompt: str | None, temperature: float) -> tuple[str, float | None]:
        segments, _info = self.model.transcribe(
            samples,
            language="en",
            beam_size=self.beam_size if temperature == 0 else 1,
            best_of=1 if temperature == 0 else 5,
            temperature=temperature,
            initial_prompt=prompt,
            condition_on_previous_text=False,
            vad_filter=False,
            without_timestamps=True,
        )
        texts, lps = [], []
        for seg in segments:
            texts.append(seg.text.strip())
            lps.append(seg.avg_logprob)
        text = " ".join(t for t in texts if t)
        lp = float(np.mean(lps)) if lps else None
        return text, lp

    def transcribe(
        self, samples_or_path, prompt: str | None = None, extra_hypotheses: bool | None = None, raw_prompt: bool = False
    ) -> ASRResult:
        t0 = time.perf_counter()
        samples = _load_samples(samples_or_path)
        prompt = full_prompt(prompt, raw_prompt)
        text, lp = self._decode(samples, prompt, 0.0)
        n_best = [text]
        temps = self.n_best_temperatures if extra_hypotheses is None else (
            (0.2, 0.4, 0.6, 0.8) if extra_hypotheses else ()
        )
        for temp in temps:
            if len(n_best) >= self.n_best:
                break
            alt, _ = self._decode(samples, prompt, temp)
            if alt and alt not in n_best:
                n_best.append(alt)
        return ASRResult(
            text=text,
            confidence=logprob_to_confidence(lp),
            n_best=n_best,
            latency_s=time.perf_counter() - t0,
            backend=f"local:{self.model_size}",
        )


# ---------------------------------------------------------------------------
# Baseten
# ---------------------------------------------------------------------------


class BasetenWhisper:
    def __init__(
        self,
        url: str,
        api_key: str | None = None,
        beam_size: int = 5,
        n_best: int = 5,
        timeout_s: float = 8.0,
        max_retries: int = 2,
        name: str = "baseten",
    ):
        # Short timeout, few retries: a transmission that waits half a minute for the network is
        # worse than one heard by the local model. WithFallback takes over when this gives up.
        import httpx

        self.url = url
        self.api_key = api_key or os.environ.get("BASETEN_API_KEY", "")
        self.beam_size = beam_size
        self.n_best = n_best
        self.max_retries = max_retries
        self.name = name
        self._client = httpx.Client(timeout=timeout_s)

    def _post(self, body: dict) -> dict:
        import httpx

        headers = {"Authorization": f"Api-Key {self.api_key}"} if self.api_key else {}
        delay = 0.5
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                r = self._client.post(self.url, json=body, headers=headers)
                if r.status_code == 429 or 500 <= r.status_code < 600:
                    retry_after = r.headers.get("retry-after")
                    time.sleep(float(retry_after) if retry_after else delay)
                    delay = min(delay * 2, 8.0)
                    continue
                r.raise_for_status()
                return r.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                time.sleep(delay)
                delay = min(delay * 2, 8.0)
        raise RuntimeError(f"Baseten ASR failed after {self.max_retries} attempts: {last_exc!r}")

    @staticmethod
    def _extract(data: dict) -> tuple[str, float | None, list[str]]:
        if isinstance(data, dict) and "model_output" in data:  # some Truss wrappers nest it
            data = data["model_output"]
        text = ""
        lp = None
        n_best: list[str] = []
        if isinstance(data, str):
            return data.strip(), None, []
        if "text" in data:
            text = str(data["text"]).strip()
        elif "segments" in data:
            text = " ".join(str(s.get("text", "")).strip() for s in data["segments"]).strip()
        if "avg_logprob" in data:
            lp = data["avg_logprob"]
        elif data.get("segments"):
            lps = [s.get("avg_logprob") for s in data["segments"] if s.get("avg_logprob") is not None]
            lp = float(np.mean(lps)) if lps else None
        if isinstance(data.get("n_best"), list):
            n_best = [str(x if isinstance(x, str) else x.get("text", "")).strip() for x in data["n_best"]]
        return text, lp, n_best

    def transcribe(self, samples_or_path, prompt: str | None = None, raw_prompt: bool = False) -> ASRResult:
        t0 = time.perf_counter()
        samples = _load_samples(samples_or_path)
        body = {
            "audio": base64.b64encode(wav_bytes(samples, SR)).decode(),
            "prompt": full_prompt(prompt, raw_prompt) or "",
            "beam_size": self.beam_size,
            "n_best": self.n_best,  # training/serve_asr returns real beam alternatives with scores
            "language": "en",
        }
        data = self._post(body)
        text, lp, n_best = self._extract(data)
        conf = data.get("confidence") if isinstance(data, dict) else None
        if not n_best or n_best[0] != text:
            n_best = [text] + [h for h in n_best if h != text]
        return ASRResult(
            text=text,
            confidence=float(conf) if conf is not None else logprob_to_confidence(lp),
            n_best=n_best[: self.n_best],
            latency_s=time.perf_counter() - t0,
            backend=self.name,
        )


# ---------------------------------------------------------------------------
# Remote first, local if the network lets us down
# ---------------------------------------------------------------------------


class WithFallback:
    """Use the Baseten model; if a call fails, hear this transmission with the local model instead.

    After a failure the remote is skipped for `cooldown_s`, so a dead network costs one slow
    transmission, not every one. `backend` on the result says which model actually heard it.
    """

    def __init__(self, primary: ASR, make_fallback, cooldown_s: float = 45.0):
        self.primary = primary
        self._make_fallback = make_fallback
        self._fallback: ASR | None = None
        self.cooldown_s = cooldown_s
        self._skip_until = 0.0
        self.failures = 0

    def _local(self) -> ASR:
        if self._fallback is None:
            self._fallback = self._make_fallback()
        return self._fallback

    def transcribe(self, samples_or_path, prompt: str | None = None, **kw) -> ASRResult:
        samples = _load_samples(samples_or_path)
        if time.monotonic() >= self._skip_until:
            try:
                return self.primary.transcribe(samples, prompt, **kw)
            except Exception as exc:
                self.failures += 1
                self._skip_until = time.monotonic() + self.cooldown_s
                log.warning("Baseten ASR failed (%s). Local model for the next %.0f s.", exc, self.cooldown_s)
        return self._local().transcribe(samples, prompt)


# ---------------------------------------------------------------------------
# Stock vs tuned
# ---------------------------------------------------------------------------


STOCK_WAIT_S = 4.0  # how long the comparison may hold up a transmission after the tuned model answered
_STOCK_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="asr-stock")


class StockAndTuned:
    """Transcribe with the tuned model; also run the stock model and attach `text_stock`."""

    def __init__(self, tuned: ASR, stock: ASR | None):
        self.tuned = tuned
        self.stock = stock

    def transcribe(self, samples_or_path, prompt: str | None = None) -> ASRResult:
        """Both models hear the clip at the same time, so the comparison costs no extra wait."""
        samples = _load_samples(samples_or_path)
        if self.stock is None:
            return self.tuned.transcribe(samples, prompt)
        pending = _STOCK_POOL.submit(self.stock.transcribe, samples, prompt)
        res = self.tuned.transcribe(samples, prompt)
        try:
            res.text_stock = pending.result(timeout=STOCK_WAIT_S).text
        except Exception as exc:  # never let the toggle break or slow the main path
            res.text_stock = f"<stock failed: {type(exc).__name__}>"
        return res


_ASR_SINGLETON: ASR | None = None


def get_asr(force_new: bool = False) -> ASR:
    """BasetenWhisper if ASR_MODEL_URL is set, else LocalWhisper.

    Env: ASR_MODEL_URL, ASR_STOCK_MODEL_URL, BASETEN_API_KEY, ASR_LOCAL_MODEL (default base.en),
    ASR_STOCK_LOCAL=1 to run a local stock model alongside a Baseten tuned model.
    """
    global _ASR_SINGLETON
    if _ASR_SINGLETON is not None and not force_new:
        return _ASR_SINGLETON
    tuned_url = os.environ.get("ASR_MODEL_URL")
    stock_url = os.environ.get("ASR_STOCK_MODEL_URL")
    local_size = os.environ.get("ASR_LOCAL_MODEL", "base.en")

    # ASR_LOCAL_MODEL may be a size ("base.en") or a folder holding a CTranslate2 export of our
    # tuned model, which makes the local fallback as good as the remote one.
    # Beam width is the speed dial for the deployed model. Measured on the T4, round trip:
    # 1 = 0.3 s, 3 = 0.8 s, 5 = 1.7 s. Three keeps a score and alternatives inside the 2 s budget.
    beams = int(os.environ.get("ASR_BEAM_SIZE", "3"))
    tuned: ASR = (WithFallback(BasetenWhisper(tuned_url, beam_size=beams, n_best=beams, name="baseten:tuned"),
                               lambda: LocalWhisper(local_size))
                  if tuned_url else LocalWhisper(local_size))
    stock: ASR | None = None
    if stock_url:
        stock = BasetenWhisper(stock_url, name="baseten:stock")
    elif tuned_url and os.environ.get("ASR_STOCK_LOCAL"):
        stock = LocalWhisper(local_size)
    _ASR_SINGLETON = StockAndTuned(tuned, stock) if stock else tuned
    return _ASR_SINGLETON


__all__ = [
    "ASR",
    "ASRResult",
    "ATC_PROMPT_PREFIX",
    "BasetenWhisper",
    "build_prompt",
    "LocalWhisper",
    "StockAndTuned",
    "dataset_normalize",
    "get_asr",
    "logprob_to_confidence",
]
