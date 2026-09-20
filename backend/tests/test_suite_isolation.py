"""The suite must never reach a service that is shared or billed.

Several tests `import app`, and app.py calls load_dotenv() as it is imported. With real settings in
the repo's `.env`, that put the Elasticsearch settings straight back after the fixture had removed
them, the app's global World was built with the live memory, and one run of the suite wrote 249
test records into the team's cluster, the one the resolver searches during a demo.
"""
import os

from dotenv import load_dotenv

SHARED_OR_BILLED = ("ELASTIC_URL", "ELASTIC_API_KEY", "OPENAI_API_KEY", "BASETEN_API_KEY", "ELEVENLABS_API_KEY",
                    "ASR_MODEL_URL")


def _a_real_looking_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("".join(f"{name}=https://real.invalid/{name.lower()}\n" for name in SHARED_OR_BILLED))
    return env


def test_loading_the_env_file_inside_a_test_changes_nothing(tmp_path):
    load_dotenv(_a_real_looking_env_file(tmp_path))  # exactly what `import app` does
    for name in SHARED_OR_BILLED:
        assert not os.environ.get(name), f"{name} reached a test through load_dotenv"


def test_no_world_gets_the_shared_memory_even_after_the_env_file_is_loaded(tmp_path):
    from tower.memory import NullMemory
    from world import World

    load_dotenv(_a_real_looking_env_file(tmp_path))
    w = World(lambda e: None, synthesize=False, realtime=False)
    assert isinstance(w.memory, NullMemory), type(w.memory).__name__


def test_the_apps_own_world_has_no_shared_memory():
    """The hole itself: the World that app.py builds as it is imported."""
    import sys

    from tower.memory import NullMemory
    sys.modules.pop("app", None)
    import app as A
    assert isinstance(A.world.memory, NullMemory), type(A.world.memory).__name__
