# Training runs

## Summary, Sunday Sept 20 morning (human-written; auto entries below)

**Whisper fine-tune, Baseten H100, proving job (Sat Sept 19, job `w7rrk13`, team 13, project `k7-t13`).** `openai/whisper-small`, 300 steps only (about 0.85 epoch), warmup 50, batch 16 x grad accum 2, fp16, one H100. 322 s of training at 0.93 steps/s (29.8 samples/s). Whole job 8 min 20 s including install, dataset download and conversion, three evaluations, held-out comparison, and CTranslate2 export. Validation WER on 120 clips at steps 100, 200, 300: 0.28, 0.43, 0.35. That is noise from a small validation slice, and "best" was therefore step 100.

| Model | WER on 200 held-out real clips | Sub / Del / Ins | s per clip (H100) |
|---|---|---|---|
| stock whisper-small | 0.671 | 751 / 192 / 390 | 0.050 |
| **tuned whisper-small, 300 steps on Baseten** | **0.278** | 299 / 84 / 169 | 0.034 |

A rehearsal, not the result: say "five minutes of training" if this number is quoted. Stock errors are dominated by insertions (looping "zero two zero two", invented sentences such as "im going to turn the camera off"). Baseten lists the saved checkpoints with type `whisper`.

**Whisper fine-tune, Baseten H100, the full run (Sat Sept 19, job `q9jj663`, team 13, project `k7-t13`). This is the number we quote.** `openai/whisper-small`, 11,268 real ATC clips from jacktol/atc-dataset, 10 epochs = 3,530 steps, lr 1e-5, warmup 500, batch 16 x grad accum 2, fp16, decaying noise / pitch / stretch / clipping augmentation, one H100. Training took 3,641 s (61 min); the whole job 65 min, 13:52 to 14:57. Validation WER on all 593 validation clips every 500 steps: 0.207, 0.166, 0.149, 0.167, 0.146, **0.145** (step 3000, kept as best), 0.150. Early stopping (patience 3) never fired.

| Model | WER on 1,000 held-out real clips | Sub / Del / Ins | s per clip (H100) |
|---|---|---|---|
| stock whisper-small | 0.708 | 3933 / 1009 / 2138 | 0.043 |
| **tuned whisper-small, 61 min on Baseten** | **0.159** | 709 / 372 / 511 | 0.035 |

Same 1,000 clips for both, from the test split no run has trained on, greedy decoding, `text_norm` on both sides. Errors fall by 78 percent. Stock errors are mostly insertions (looping digits, invented sentences); the tuned model's insertions fall by three quarters. The job also wrote a CTranslate2 float16 export to `ct2-float16` beside the checkpoint, which is what faster-whisper loads. Say "whisper-small, one hour on one H100, 1,000 held-out clips" when quoting it. Validation loss rose after step 2000 while WER kept falling slowly, so more epochs on this data will not help: the next gain is more data (simulator audio for our own callsigns and fix names) or a bigger base model.

**Run 1 on our own simulator's audio (Sat Sept 19, measured against the deployed model, beam 3).** 299 clips from `gen_sim_audio.py`: the app's own phrases through the app's own radio effect, in a macOS voice (Tessa) that no run trains on. **WER 0.171** (549 errors in 3,204 words). Numbers, headings, levels and speeds are right. What is wrong is every name it has never heard: **a fix name came out perfectly in 1 of 126 clips** ("proceed direct rezex" -> "proceed direct to six", "direct nivat" -> "direct nibbit"), and the rarer airline names go the same way ("channex" -> "china is", "rednose" -> "read notes", "brickyard" -> "route you on"). This is the baseline run 2 has to beat.

**Run 2, the mixed run (Sat Sept 19, job `32ooy2w`, team 13, project `k7-t13`). Finished and good. Baseten lists it as FAILED: see the note below.** `openai/whisper-small`, the same real data as run 1 plus ATCOSIM (7,601 clips kept) plus 2,400 of our own simulator clips in eight macOS voices: 21,269 training clips, six epochs = 3,990 steps, lr 1e-5, warmup 500, batch 16 x grad accum 2, fp16, one H100. Training 4,251 s (71 min), whole job 79 min, 16:51 to 18:10. Validation WER on the same 593 clips as run 1, every 500 steps: 0.249, 0.185, 0.164, 0.150, **0.145** (step 2500, kept as best), 0.150, 0.146, 0.148.

| Test set | stock whisper-small | run 1 | **run 2** |
|---|---|---|---|
| 1,000 held-out real ATC clips (the same clips as run 1) | 0.708 | 0.159 | **0.155** |
| 299 clips of our own simulator audio, a voice no run trained on | 0.202 | 0.171 (measured against the deployment) | **0.035** |
| 300 clips of ATCOSIM's test split | 0.349 | not measured | **0.034** |

Run 2 is no worse on real radio (slightly better) and five times better on the audio our demo is made of. **Deployed 18:55 (deployment `qe9xon0`, primary; run 1 is `wn4r791`, scaled to zero, still there to roll back to).** Measured against the live endpoint on the 299 simulator clips: WER 0.029, and **a made-up fix name exactly right in 81 of 126 clips, against 1 of 126 for run 1.** The remaining misses are near-misses on the rarest names ("nivat" -> "nibbit", "rednose" -> "readnose") that waypoint and callsign snapping mostly absorb.

**The caveat is real, not theoretical.** The simulator clips are macOS voices; the demo voices are ElevenLabs. The one ElevenLabs clip we have with known text (Alice saying "direct estir, air canada one two three") still comes back as "direct to six" from run 2. One clip proves nothing about the rate, but it shows the gain does not fully carry across voices. **Measured on the demo's own voices, 19:10.** 20 fix-name phrases (815 characters of ElevenLabs credit, now cached) across all eight pilot voices, through the app's radio effect, saved in `data/asr/eleven_test/`. **Run 2: 13 of 20 exactly right. Run 1: 0 of 20.** So most of the gain does carry from the macOS training voices to ElevenLabs. Run 2's seven misses are all near-misses on the name itself ("voput" -> "to putt", "sovar" -> "sober", "bezux" -> "bezukes", "cargolux" -> "ergolux"), which is what waypoint and callsign snapping exist for. Run 1's misses were not near: "direct to golf golf golf", "leg and direct". A run 3 with a few hundred ElevenLabs clips in the mix would likely close most of the rest; it costs several thousand characters, so it needs the paid month.

**Why Baseten says FAILED when it did not fail.** Our script ran to its last line: best model saved, all three evaluations, the CTranslate2 export, "done. checkpoints in /mnt/ckpts". The job's status label had been stuck on "Deploying" since launch. At 18:10:26 the platform logged "Job already completed successfully, terminating job...", then caught up on its own stale events ("has started running", status RUNNING), killed the container, and recorded that kill as "exited with code 137, terminated unexpectedly". 137 is SIGKILL: the platform's own teardown, after our work was done. Checkpoint sync is COMPLETED and all 97 files are there, including `best/` (967 MB), `ct2-float16/` and `results/`. Worth telling the Baseten booth: a finished job shown as failed.

**Whisper fine-tune, laptop.** `openai/whisper-tiny` on the full jacktol/atc-dataset train split (11,268 clips), val 593 (120 used at each eval point), MacBook Air M4 16 GB, MPS. lr 5e-5, warmup 100, batch 8 x grad accum 2, 1,200 steps = 1.7 epochs, 62 minutes, decaying noise/pitch/stretch/clip augmentation. Train loss 12.3 to about 1.0. Val WER at steps 200 to 1200: 0.337, 0.375, 0.227, 0.221, 0.214 (best at 1200). Checkpoint `data/checkpoints/whisper-tiny-atc/best`, CTranslate2 int8 export at `data/checkpoints/whisper-tiny-atc-ct2` (loads in faster-whisper, transcribes a held-out clip correctly).

**Held-out comparison, same first 300 clips of the never-trained test split, greedy, text_norm on both sides:**

| Model | WER | Sub / Del / Ins | s per clip (MPS) |
|---|---|---|---|
| stock whisper-tiny | 1.184 | 1821 / 365 / 1313 | 0.05 |
| stock whisper-base | 1.110 | 1601 / 327 / 1351 | 0.12 |
| stock whisper-small | 0.694 | 1148 / 309 / 593 | 0.39 |
| **tuned whisper-tiny (ours, 1 h laptop)** | **0.217** | 360 / 85 / 195 | 0.04 |

The tuned tiny beats stock tiny, stock base, and stock small on the same real clips, and is the fastest of the four. Stock models' errors are dominated by insertions: looping digits and hallucinated phrases on short clips. This is a laptop run; the Baseten H100 run of whisper-small or medium.en (TRD 02) should land well below 0.2. Say "laptop, tiny, one hour" whenever this number is quoted.

**Checker cross-encoder, laptop.** distilroberta-base, 12k synthetic pairs, 1,200 steps, 9.7 min, MPS: accuracy 0.894, false alarm rate 0.085, detection 0.908 on 5,000 held-out synthetic pairs; wrong_aircraft is the weak class at 0.54. `data/checkpoints/checker-laptop/best`, `results/checker_eval.json`.

**Stock baselines, 100 clips (earlier, superseded by the 300-clip table):** tiny 1.041, base 0.919, small 0.672.

---

Appended automatically by `runlog.log_run`. Every entry is a run that actually executed. Labels say what it was for; SMOKE means a pipeline check, not a result to quote.

## 2026-09-19 04:31 EDT | whisper_finetune | SMOKE

- base model: `openai/whisper-tiny`
- data: `{"n_train": 40, "n_val": 6, "train": "../data/asr/manifests/synth_train.jsonl", "val": "../data/asr/manifests/synth_val.jsonl"}`
- hyperparameters: `{"augment": true, "batch": 4, "device": "mps", "epochs": 10, "fp16": false, "grad_accum": 1, "lr": 1e-05, "max_steps": 20, "patience": 3, "warmup": 2}`
- duration: 0.4 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"best_dir": "../data/checkpoints/whisper-smoke/best", "first_loss": 5.9152061462402346, "last_loss": 2.7117755889892576, "steps": 20, "val_wer": 0.2388}`

## 2026-09-19 04:32 EDT | wer_eval | SMOKE-synthetic-audio

- base model: `stock:openai/whisper-tiny`
- data: `{"manifest": "../data/asr/manifests/synth_test.jsonl", "n_clips": 12}`
- hyperparameters: `{"greedy": true, "normalizer": "text_norm.normalize"}`
- duration: 0.0 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"deletions": 5, "insertions": 18, "seconds_per_clip": 0.115, "substitutions": 37, "wer": 0.4878}`

## 2026-09-19 04:32 EDT | wer_eval | SMOKE-synthetic-audio

- base model: `tuned:../data/checkpoints/whisper-smoke/best`
- data: `{"manifest": "../data/asr/manifests/synth_test.jsonl", "n_clips": 12}`
- hyperparameters: `{"greedy": true, "normalizer": "text_norm.normalize"}`
- duration: 0.0 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"deletions": 2, "insertions": 7, "seconds_per_clip": 0.041, "substitutions": 26, "wer": 0.2846}`

## 2026-09-19 04:34 EDT | wer_eval | stock-baseline-real-heldout

- base model: `stock:openai/whisper-tiny`
- data: `{"manifest": "../data/asr/manifests/test.jsonl", "n_clips": 100}`
- hyperparameters: `{"greedy": true, "normalizer": "text_norm.normalize"}`
- duration: 0.1 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"deletions": 115, "insertions": 318, "seconds_per_clip": 0.053, "substitutions": 605, "wer": 1.0411}`

## 2026-09-19 04:34 EDT | wer_eval | stock-baseline-real-heldout

- base model: `stock-base`
- data: `{"manifest": "../data/asr/manifests/test.jsonl", "n_clips": 100}`
- hyperparameters: `{"greedy": true, "normalizer": "text_norm.normalize"}`
- duration: 0.2 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"deletions": 94, "insertions": 288, "seconds_per_clip": 0.102, "substitutions": 534, "wer": 0.9188}`

## 2026-09-19 04:34 EDT | wer_eval | stock-baseline-real-heldout

- base model: `stock-small`
- data: `{"manifest": "../data/asr/manifests/test.jsonl", "n_clips": 100}`
- hyperparameters: `{"greedy": true, "normalizer": "text_norm.normalize"}`
- duration: 0.5 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"deletions": 90, "insertions": 215, "seconds_per_clip": 0.3, "substitutions": 365, "wer": 0.672}`

## 2026-09-19 04:36 EDT | checker_finetune | SMOKE

- base model: `distilroberta-base`
- data: `{"n_train": 2000, "n_val": 1000, "train": "../data/checker/train.jsonl", "val": "../data/checker/heldout.jsonl"}`
- hyperparameters: `{"batch": 16, "device": "mps", "eval_steps": 100, "fp16": false, "lr": 2e-05, "max_len": 128, "max_steps": 200, "patience": 4, "warmup": 20}`
- duration: 1.2 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"best_dir": "../data/checkpoints/checker-smoke/best", "chance": 0.125, "first_loss": 2.068870391845703, "heldout_accuracy": 0.533, "heldout_false_alarm_rate": 0.9921, "last_loss": 1.2596484375, "steps": 200}`

## 2026-09-19 04:38 EDT | checker_eval | SMOKE

- base model: `../data/checkpoints/checker-smoke/best`
- data: `{"data": "../data/checker/heldout.jsonl", "n": 1000}`
- hyperparameters: `{"batch_size": 64}`
- duration: 0.1 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"accuracy": 0.534, "detection_rate": 0.9977, "false_alarm_rate": 0.9921, "latency_ms": {"batched_per_pair": 6.971, "single_pair": {"p50": 29.11, "p95": 205.32}}}`

## 2026-09-19 04:46 EDT | checker_finetune | laptop-distilroberta-12k-1200steps

- base model: `distilroberta-base`
- data: `{"n_train": 12000, "n_val": 1000, "train": "../data/checker/train.jsonl", "val": "../data/checker/heldout.jsonl"}`
- hyperparameters: `{"batch": 32, "device": "mps", "eval_steps": 300, "fp16": false, "lr": 2e-05, "max_len": 128, "max_steps": 1200, "patience": 4, "warmup": 100}`
- duration: 9.7 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"best_dir": "../data/checkpoints/checker-laptop/best", "chance": 0.125, "first_loss": 2.095775146484375, "heldout_accuracy": 0.89, "heldout_false_alarm_rate": 0.1181, "last_loss": 0.2772675514221191, "steps": 1200}`

## 2026-09-19 04:47 EDT | checker_eval | laptop-distilroberta-12k-1200steps

- base model: `../data/checkpoints/checker-laptop/best`
- data: `{"data": "../data/checker/heldout.jsonl", "n": 5000}`
- hyperparameters: `{"batch_size": 64}`
- duration: 0.3 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"accuracy": 0.8938, "detection_rate": 0.9079, "false_alarm_rate": 0.0854, "latency_ms": {"batched_per_pair": 3.564, "single_pair": {"p50": 7.07, "p95": 74.93}}}`

## 2026-09-19 06:04 EDT | whisper_finetune | laptop-tiny-real

- base model: `openai/whisper-tiny`
- data: `{"n_train": 11268, "n_val": 120, "train": "../data/asr/manifests/train.jsonl", "val": "../data/asr/manifests/val.jsonl"}`
- hyperparameters: `{"augment": true, "batch": 8, "device": "mps", "epochs": 10, "eval_steps": 200, "fp16": false, "grad_accum": 2, "grad_checkpoint": false, "lr": 5e-05, "max_steps": 1200, "patience": 3, "warmup": 100}`
- duration: 62.5 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"best_dir": "../data/checkpoints/whisper-tiny-atc/best", "first_loss": 12.317886962890626, "last_loss": 0.6886869049072266, "steps": 1200, "val_wer": 0.2141}`

## 2026-09-19 06:11 EDT | wer_eval | real-heldout-300

- base model: `stock:openai/whisper-tiny`
- data: `{"manifest": "../data/asr/manifests/test.jsonl", "n_clips": 300}`
- hyperparameters: `{"greedy": true, "normalizer": "text_norm.normalize"}`
- duration: 0.3 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"deletions": 365, "insertions": 1313, "seconds_per_clip": 0.052, "substitutions": 1821, "wer": 1.1841}`

## 2026-09-19 06:11 EDT | wer_eval | real-heldout-300

- base model: `tuned:../data/checkpoints/whisper-tiny-atc/best`
- data: `{"manifest": "../data/asr/manifests/test.jsonl", "n_clips": 300}`
- hyperparameters: `{"greedy": true, "normalizer": "text_norm.normalize"}`
- duration: 0.2 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"deletions": 85, "insertions": 195, "seconds_per_clip": 0.04, "substitutions": 360, "wer": 0.2166}`

## 2026-09-19 06:11 EDT | wer_eval | real-heldout-300

- base model: `stock-base`
- data: `{"manifest": "../data/asr/manifests/test.jsonl", "n_clips": 300}`
- hyperparameters: `{"greedy": true, "normalizer": "text_norm.normalize"}`
- duration: 0.6 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"deletions": 327, "insertions": 1351, "seconds_per_clip": 0.12, "substitutions": 1601, "wer": 1.1096}`

## 2026-09-19 06:11 EDT | wer_eval | real-heldout-300

- base model: `stock-small`
- data: `{"manifest": "../data/asr/manifests/test.jsonl", "n_clips": 300}`
- hyperparameters: `{"greedy": true, "normalizer": "text_norm.normalize"}`
- duration: 2.0 min
- hardware: mps: arm64 (Josephs-MacBook-Air.local)
- result: `{"deletions": 309, "insertions": 593, "seconds_per_clip": 0.394, "substitutions": 1148, "wer": 0.6937}`
