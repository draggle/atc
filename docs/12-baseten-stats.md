# Squawk on Baseten: the numbers

One page for the Baseten sponsor track. Every figure is measured by us and recorded in `training/RUNS.md`, `README.md` or `docs/10-roadmap.md`. Say the caveat next to each number when you quote it.

## Whisper fine-tune, trained on a Baseten H100

| | Stock whisper-small | Ours, tuned on Baseten |
|---|---|---|
| Word error rate, 1,000 held-out real ATC clips | 0.708 | **0.155** |
| Errors on those clips (sub / del / ins) | 3,933 / 1,009 / 2,138 | 709 / 372 / 511 |
| Word error rate, 299 clips of our own simulator audio | 0.202 | **0.035** |
| Word error rate, 300 ATCOSIM test clips | 0.349 | **0.034** |
| Made-up fix names heard exactly right (126 clips) | 1 of 126 (run 1) | **81 of 126** (run 2) |
| Fix names right on the demo's ElevenLabs voices (20 clips) | 0 of 20 (run 1) | **13 of 20** (run 2) |

- Errors on real radio fall by 78 percent. Stock Whisper's errors are mostly insertions: looping digits and invented sentences on short clips.
- Training data: 21,269 clips. 11,268 real ATC clips (jacktol/atc-dataset), 7,601 from ATCOSIM, 2,400 of our own simulator audio in eight voices.
- One H100, 71 minutes of training, 79 minutes for the whole job including download, three evaluations and the CTranslate2 export. Six epochs, 3,990 steps, batch 16 x 2, fp16.
- Two proving runs first: a 5-minute job (300 steps) already cut WER from 0.671 to 0.278 on 200 clips, so the full hour was spent knowing the pipeline worked.
- For scale: the best we could do on a 16 GB laptop was whisper-tiny, one hour, WER 0.217 on 300 clips. whisper-base would not even fit in memory.

## Serving on Baseten

| | |
|---|---|
| Tuned Whisper endpoint | Baseten T4, beam 3, **0.8 s per transmission**, n-best hypotheses returned with scores |
| Beam 1 for the controller's own voice | 0.3 s |
| Fallback | Local faster-whisper takes over per transmission if the endpoint cannot be reached |
| Resolver agent | GLM-5.3-Fast on Baseten Model APIs, tool calling, at most 4 tool calls, about 3 s to a verdict |
| Interpreter agent (plain English like "take it round the north side of the storm") | Same model, one tool call, measured 0.5 to 3.4 s |
| Whole tier-1 pipeline after speech recognition | about 0.8 s |

Stock and tuned models run side by side on the same audio, so the toggle on screen is a live comparison, not a recording.

## The readback checker

| | |
|---|---|
| Model | distilroberta-base cross-encoder, 12,000 synthetic instruction/readback pairs |
| Accuracy on 5,000 held-out pairs | 0.894 |
| Detection rate | 0.908 |
| False alarm rate | 0.085 |
| Latency | 7 ms per pair batched, p50 7 ms single |

Caveat to say out loud: trained on the laptop in 10 minutes and evaluated on synthetic pairs. The Baseten job config exists, and it was not run before judging.

## What it adds up to, Monte Carlo on the simulator

| Arm | Losses of separation per flight hour | Closest approach |
|---|---|---|
| Fixed routes | 0.34 | 0.04 NM |
| Tower plan | 0 | 9.4 NM |

Demo scenario, 20 runs, 2 percent readback errors. Dense scenario at 5 percent errors: 154 losses of separation on fixed routes, 1 with the plan alone, 0 with the plan plus readback validation. 6 to 8 percent fewer miles flown at every traffic density.

## Two things worth telling Baseten

- A job that finished (best model saved, evaluations done, checkpoint sync complete, 97 files) was recorded as FAILED with exit code 137. The platform's own teardown killed the container after our script had ended. Job `32ooy2w`, project `k7-t13`.
- Scale-to-zero cost us a 16 second stall on the first transmission after a quiet half hour. We keep the model warm from the app while voice is on.
