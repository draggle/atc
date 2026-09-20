"""The suite never talks to a paid service.

Importing `app` loads the repo's `.env`, so on a laptop with real keys every later test would call
Baseten and ElevenLabs: slow, billed, and flaky, because a model's wording changes between runs.
Without keys the code takes its deterministic local paths, which is what the tests pin.
"""
import pytest

PAID = ("BASETEN_API_KEY", "ELEVENLABS_API_KEY", "ASR_MODEL_URL", "ASR_STOCK_MODEL_URL", "CHECKER_MODEL_URL",
        # Not billed, but shared: with these set every World() writes into the team's live
        # Elasticsearch cluster, the memory the resolver searches during a demo.
        "ELASTIC_URL", "ELASTIC_API_KEY",
        "OPENAI_API_KEY")


@pytest.fixture(autouse=True)
def _no_paid_services(monkeypatch):
    # Set to empty, not deleted. Several tests `import app`, and app.py calls load_dotenv() as it is
    # imported: a deleted variable is put straight back from `.env`, and the app's global World is
    # built with it. dotenv never overrides a variable that exists, even an empty one, and every
    # reader in the backend treats empty as "not configured".
    for name in PAID:
        monkeypatch.setenv(name, "")
    # Belt and braces for the shared one: whatever the environment says, no World built in a test
    # gets the team's live Elasticsearch memory. (Deleting the variables was not enough: one run
    # wrote 249 records into the live cluster through the app's global World.)
    from tower import memory
    monkeypatch.setattr(memory.ElasticMemory, "from_env", classmethod(lambda cls: None))
