"""The suite never talks to a paid service.

Importing `app` loads the repo's `.env`, so on a laptop with real keys every later test would call
Baseten and ElevenLabs: slow, billed, and flaky, because a model's wording changes between runs.
Without keys the code takes its deterministic local paths, which is what the tests pin.
"""
import pytest

PAID = ("BASETEN_API_KEY", "ELEVENLABS_API_KEY", "ASR_MODEL_URL", "ASR_STOCK_MODEL_URL", "CHECKER_MODEL_URL")


@pytest.fixture(autouse=True)
def _no_paid_services(monkeypatch):
    for name in PAID:
        monkeypatch.delenv(name, raising=False)
