"""Baseten training job for the RoBERTa-base readback checker (docs/07 section 11).

    truss train push config.py

Minutes on one H100. run.sh generates 50k synthetic pairs, trains, evaluates,
and leaves the best model under $BT_CHECKPOINT_DIR/best.
"""
from truss.base.truss_config import AcceleratorSpec
from truss_train import (CacheConfig, CheckpointingConfig, Compute, Image, Runtime, TrainingJob,
                         TrainingProject)

runtime = Runtime(
    start_commands=["chmod +x ./run.sh && ./run.sh"],
    cache_config=CacheConfig(enabled=True),
    checkpointing_config=CheckpointingConfig(enabled=True),
    environment_variables={
        "CHECKER_BASE": "roberta-base",
        "RUN_LABEL": "baseten-h100-roberta-base-50k",
        "N_PAIRS": "50000",
        "MAX_STEPS": "20000",
    },
)
job = TrainingJob(
    image=Image(base_image="pytorch/pytorch:2.7.0-cuda12.8-cudnn9-runtime"),
    compute=Compute(accelerator=AcceleratorSpec(accelerator="H100", count=1)),
    runtime=runtime,
)
project = TrainingProject(name="tower-readback-checker", job=job)
