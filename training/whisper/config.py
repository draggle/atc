"""Baseten training job for the Whisper ATC fine-tune. Matches docs/07-build-spec.md section 11.

Submit from this directory (it uploads the whole directory as the job workspace,
so it copies ../*.py into a staging folder first; see ../BASETEN.md):

    truss train push config.py            # or: baseten train push --config config.py

The job runs run.sh, which installs requirements, prepares data, and launches
finetune_whisper.py. Checkpoints must be written under $BT_CHECKPOINT_DIR.
"""
from truss.base.truss_config import AcceleratorSpec
from truss_train import (CacheConfig, CheckpointingConfig, Compute, Image, Runtime, TrainingJob,
                         TrainingProject)

runtime = Runtime(
    start_commands=["chmod +x ./run.sh && ./run.sh"],
    cache_config=CacheConfig(enabled=True),
    checkpointing_config=CheckpointingConfig(enabled=True),
    environment_variables={
        # Which base model and how long. Override here before pushing.
        "WHISPER_BASE": "openai/whisper-small",
        "RUN_LABEL": "k7-small",
        # Optional: cap steps for a first end-to-end job, e.g. "300". Empty means full epochs.
        "MAX_STEPS": "",
    },
)
job = TrainingJob(
    image=Image(base_image="pytorch/pytorch:2.7.0-cuda12.8-cudnn9-runtime"),
    compute=Compute(accelerator=AcceleratorSpec(accelerator="H100", count=1)),
    runtime=runtime,
)
project = TrainingProject(name="k7-t13", job=job)
