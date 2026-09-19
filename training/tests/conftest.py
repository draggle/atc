import sys
from pathlib import Path

# Make `training/` importable as top-level modules (text_norm, phrases, ...).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
