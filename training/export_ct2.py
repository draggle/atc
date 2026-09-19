"""Convert a fine-tuned Whisper checkpoint to CTranslate2 for faster-whisper.

Wraps `ct2-transformers-converter --model <ckpt> --output_dir <dir> --quantization <q>`
and then loads the result with faster-whisper and transcribes one clip as a check.

  .venv/bin/python export_ct2.py --ckpt ../data/checkpoints/whisper-smoke/best \
      --out ../data/checkpoints/whisper-smoke-ct2 --check-clip ../data/asr/synthetic/synth_00000.wav

Quantization defaults to float16 (what the Baseten GPU Truss serves). On a CPU-only
machine, faster-whisper cannot run float16, so pass --quantization int8 or let
--auto pick int8 when no CUDA is present.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import torch


def convert(ckpt: str, out: str, quantization: str, force: bool) -> None:
    exe = shutil.which("ct2-transformers-converter") or str(Path(sys.executable).parent / "ct2-transformers-converter")
    cmd = [exe, "--model", ckpt, "--output_dir", out, "--quantization", quantization, "--copy_files", "tokenizer.json", "preprocessor_config.json"]
    if force:
        cmd.append("--force")
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)


def check(out: str, clip: str | None) -> None:
    from faster_whisper import WhisperModel

    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute = "float16" if device == "cuda" else "int8"
    model = WhisperModel(out, device=device, compute_type=compute)
    print(f"faster-whisper loaded {out} on {device} ({compute})")
    if clip:
        segs, info = model.transcribe(clip, language="en", beam_size=1)
        text = " ".join(s.text for s in segs).strip()
        print(f"transcribed {clip}: {text!r}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ckpt", required=True, help="transformers checkpoint dir (must contain tokenizer files)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--quantization", default=None, choices=[None, "float16", "int8", "int8_float16", "float32"])
    ap.add_argument("--check-clip", default=None, help="a wav to transcribe after loading")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-check", action="store_true")
    args = ap.parse_args()
    q = args.quantization or ("float16" if torch.cuda.is_available() else "int8")
    convert(args.ckpt, args.out, q, args.force)
    if not args.no_check:
        check(args.out, args.check_clip)


if __name__ == "__main__":
    main()
