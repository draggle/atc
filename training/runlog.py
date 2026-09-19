"""Append a run record to training/RUNS.md. Every script that trains or measures
something calls log_run() so the log is written by the code, not by memory."""
from __future__ import annotations

import datetime as _dt
import json
import platform
from pathlib import Path

RUNS_MD = Path(__file__).resolve().parent / "RUNS.md"


def hardware_string() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return f"cuda: {torch.cuda.get_device_name(0)}"
        if torch.backends.mps.is_available():
            return f"mps: {platform.machine()} ({platform.node()})"
    except Exception:  # noqa: BLE001
        pass
    return f"cpu: {platform.machine()} ({platform.node()})"


def log_run(kind: str, label: str, *, base_model: str, data: dict, hyperparams: dict,
            duration_s: float, result: dict, hardware: str | None = None, notes: str = "") -> None:
    """kind: whisper_finetune | checker_finetune | wer_eval | checker_eval.
    label: a short human tag, e.g. 'SMOKE' or 'full'. Be honest in it."""
    hardware = hardware or hardware_string()
    ts = _dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    lines = [
        "",
        f"## {ts} | {kind} | {label}",
        "",
        f"- base model: `{base_model}`",
        f"- data: `{json.dumps(data, sort_keys=True)}`",
        f"- hyperparameters: `{json.dumps(hyperparams, sort_keys=True)}`",
        f"- duration: {duration_s / 60:.1f} min",
        f"- hardware: {hardware}",
        f"- result: `{json.dumps(result, sort_keys=True)}`",
    ]
    if notes:
        lines.append(f"- notes: {notes}")
    lines.append("")
    if not RUNS_MD.exists():
        RUNS_MD.write_text("# Training runs\n\nAppended automatically by `runlog.log_run`. Every entry is a run that actually executed. Labels say what it was for; SMOKE means a pipeline check, not a result to quote.\n")
    with open(RUNS_MD, "a") as fh:
        fh.write("\n".join(lines))
    print(f"logged to {RUNS_MD}")
