"""Second Whisper run: the same real ATC data, plus ATCOSIM, plus our own simulator's audio.

Why: run 1 (job q9jj663, WER 0.708 -> 0.159 on real held-out clips) had never heard a synthetic
voice through our radio effect or a fix name we invented, and hears "direct ESTIR" as
"direct to six". This run adds
  - Jzuluaga/atcosim_corpus: 7,640 clips of controllers in en-route simulations (downloaded in the job)
  - ./sim: 2,400 clips from gen_sim_audio.py, the app's own phrases through the app's own radio
    effect (staged beside this file; see BASETEN.md, "The mixed run")
Validation and the 1,000 held-out real clips are the same clips as run 1, so the headline number
is comparable. Two more numbers come out: WER on our simulator audio with a voice never used in
training, and WER on ATCOSIM's own test split.

Stage and submit (from training/):
    stage_mix   # see BASETEN.md
    cd /tmp/tower-whisper-mix && <repo>/training/.venv/bin/truss train push config.py --team "13"
"""
from truss.base.truss_config import AcceleratorSpec
from truss_train import (CacheConfig, CheckpointingConfig, Compute, Image, Runtime, TrainingJob,
                         TrainingProject)

runtime = Runtime(
    start_commands=["chmod +x ./run.sh && ./run.sh"],
    cache_config=CacheConfig(enabled=True),
    checkpointing_config=CheckpointingConfig(enabled=True),
    environment_variables={
        "WHISPER_BASE": "openai/whisper-small",
        "RUN_LABEL": "k7-small-mix",
        "EXTRA_DATA": "1",
        # 21,300 training clips = 666 steps an epoch. Run 1 had flattened by its epoch 4 to 8;
        # six epochs here is about 4,000 steps, roughly 70 minutes on one H100.
        "EPOCHS": "6",
        "MAX_STEPS": "",
        "EVAL_STEPS": "500",
        "EVAL_LIMIT": "1000",
    },
)
job = TrainingJob(
    image=Image(base_image="pytorch/pytorch:2.7.0-cuda12.8-cudnn9-runtime"),
    compute=Compute(accelerator=AcceleratorSpec(accelerator="H100", count=1)),
    runtime=runtime,
)
project = TrainingProject(name="k7-t13", job=job)
