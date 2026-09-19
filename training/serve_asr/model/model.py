"""Our fine-tuned Whisper behind the request shape `backend/tower/asr.py::BasetenWhisper` sends.

Request JSON
    audio       base64 WAV, any rate, mono or stereo (resampled to 16 kHz mono here)
    prompt      optional text: active callsigns and fix names, already formatted by the client
    beam_size   optional, default 5
    n_best      optional, default 5: how many beam hypotheses to return
    language    ignored, always English

Response JSON
    text          best hypothesis
    avg_logprob   its length-normalised log probability (the client turns this into confidence)
    n_best        [{"text", "avg_logprob"}, ...] best first, duplicates removed
    model         what is actually loaded, so nobody mistakes the fallback for the tuned model
    decoding      "beam+prompt" normally; "beam" or "greedy" if something had to be dropped
    seconds       time spent in this call

The n-best list is real beam search output, which is what the checker's rule needs: never alert
on a mismatch if the expected value is among the top hypotheses.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import time
from pathlib import Path

import numpy as np

SR = 16000
MAX_SECONDS = 30.0  # one Whisper window. A radio transmission is a few seconds.
log = logging.getLogger("k7-asr")


def decode_audio(b64: str) -> np.ndarray:
    """base64 WAV -> float32 mono at 16 kHz."""
    import soundfile as sf

    audio, sr = sf.read(io.BytesIO(base64.b64decode(b64)), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if sr != SR and audio.size:
        n = int(round(audio.size * SR / sr))
        audio = np.interp(np.linspace(0, audio.size - 1, n), np.arange(audio.size), audio).astype(np.float32)
    return audio[: int(MAX_SECONDS * SR)]


def find_checkpoint(root: str | os.PathLike) -> Path | None:
    """The folder under `root` that holds a Hugging Face Whisper checkpoint, wherever Baseten put it."""
    base = Path(root)
    if not base.exists():
        return None
    hits = sorted(p.parent for p in base.rglob("config.json")
                  if any((p.parent / w).exists() for w in ("model.safetensors", "pytorch_model.bin")))
    best = [h for h in hits if h.name == "best"]
    return (best or hits or [None])[0]


class Model:
    def __init__(self, **kwargs) -> None:
        self._model = None
        self._processor = None
        self._device = "cpu"
        self._name = "not loaded"

    def load(self) -> None:
        import torch
        from transformers import WhisperForConditionalGeneration, WhisperProcessor

        root = os.environ.get("ASR_CHECKPOINT_ROOT", "/tmp/training_checkpoints")
        ckpt = find_checkpoint(root)
        if ckpt is None:
            source = os.environ.get("ASR_FALLBACK_MODEL", "openai/whisper-small")
            self._name = f"FALLBACK stock {source}"
            log.error("no checkpoint under %s, serving the stock model %s", root, source)
        else:
            source = str(ckpt)
            self._name = f"tuned {ckpt.parent.name}/{ckpt.name}"
            log.info("loading %s", source)
        import transformers

        log.info("transformers %s, torch %s", transformers.__version__, torch.__version__)
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._dtype = torch.float16 if self._device == "cuda" else torch.float32
        try:
            self._processor = WhisperProcessor.from_pretrained(source)
        except Exception:
            # Fine-tuning does not touch the tokenizer or the feature extractor, so the base
            # model's are identical. A tokenizer file this transformers cannot read must not
            # stop us serving our weights.
            base = os.environ.get("ASR_FALLBACK_MODEL", "openai/whisper-small")
            log.exception("could not read the saved processor, using the one from %s", base)
            self._processor = WhisperProcessor.from_pretrained(base)
        model = WhisperForConditionalGeneration.from_pretrained(source).to(self._device).eval()
        self._model = model.half() if self._device == "cuda" else model
        # One warm call so the first real transmission is not the slow one.
        self._transcribe(np.zeros(SR, dtype=np.float32), "", 5, 5)

    def _transcribe(self, audio: np.ndarray, prompt: str, beams: int, n_best: int) -> list[dict]:
        """Best first. Tries the full request, then drops what failed: the prompt, then beam search.

        The last rung is exactly the call the training job's own evaluation made, so the
        endpoint always answers even if a library change breaks one of the extras.
        """
        import torch

        feats = self._processor.feature_extractor(audio, sampling_rate=SR, return_tensors="pt").input_features
        feats = feats.to(self._device, dtype=self._dtype)
        base = dict(language="en", task="transcribe", max_new_tokens=128)
        beam = dict(num_beams=beams, num_return_sequences=min(n_best, beams),
                    return_dict_in_generate=True, output_scores=True)
        rungs: list[tuple[str, dict, int]] = []
        if prompt.strip():
            try:
                ids = self._processor.get_prompt_ids(prompt.strip(), return_tensors="pt").to(self._device)[-200:]
                rungs.append(("beam+prompt", {**base, **beam, "prompt_ids": ids}, int(ids.shape[-1])))
            except Exception:
                log.exception("could not build prompt ids, going without a prompt")
        rungs += [("beam", {**base, **beam}, 0), ("greedy", dict(base), 0)]

        last: Exception | None = None
        for name, kwargs, prompt_len in rungs:
            try:
                with torch.inference_mode():
                    out = self._model.generate(feats, **kwargs)
                seqs = out["sequences"] if hasattr(out, "keys") else out
                scores = out.get("sequences_scores") if hasattr(out, "keys") else None
                if prompt_len:
                    seqs = seqs[:, prompt_len:]
                texts = self._processor.batch_decode(seqs, skip_special_tokens=True)
                scores = scores.float().tolist() if scores is not None else [None] * len(texts)
                seen, hyps = set(), []
                for text, score in zip(texts, scores):
                    t = " ".join(text.split())
                    if t and t not in seen:
                        seen.add(t)
                        hyps.append({"text": t, "avg_logprob": score})
                if name != rungs[0][0]:
                    log.warning("transcribed with the '%s' rung", name)
                self._rung = name
                return hyps or [{"text": "", "avg_logprob": None}]
            except Exception as exc:
                last = exc
                log.exception("generate failed on the '%s' rung", name)
        raise RuntimeError(f"every decoding rung failed: {last!r}")

    def predict(self, request: dict) -> dict:
        t0 = time.perf_counter()
        if not isinstance(request, dict) or not request.get("audio"):
            return {"error": "send {'audio': <base64 wav>}", "text": "", "n_best": [], "model": self._name}
        audio = decode_audio(request["audio"])
        if audio.size < SR // 10:  # under a tenth of a second: nothing was said
            return {"text": "", "avg_logprob": None, "n_best": [], "model": self._name, "seconds": 0.0}
        beams = max(1, min(int(request.get("beam_size") or 5), 8))
        n_best = max(1, min(int(request.get("n_best") or 5), beams))
        hyps = self._transcribe(audio, str(request.get("prompt") or ""), beams, n_best)
        return {"text": hyps[0]["text"], "avg_logprob": hyps[0]["avg_logprob"], "n_best": hyps,
                "model": self._name, "decoding": getattr(self, "_rung", "?"),
                "seconds": round(time.perf_counter() - t0, 3)}
