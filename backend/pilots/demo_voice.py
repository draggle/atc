"""Synthesize one wrong readback through the radio effect and print its ground-truth line.

    cd backend && .venv/bin/python -m pilots.demo_voice [error_type] [noise_level]
    afplay data/pilot_audio/<id>.wav

Useful for the integrator to hear what Tower will hear.
"""
from __future__ import annotations

import json
import random
import sys
import time

from pilots.pilot import AIPilot
from pilots.tts import TTS, pick_backend
from schemas import Item, OpenClearance


def main() -> None:
    error_type = sys.argv[1] if len(sys.argv) > 1 else "wrong_value"
    noise = float(sys.argv[2]) if len(sys.argv) > 2 else 0.3
    tts = TTS()
    print(f"TTS backend: {pick_backend()}  voices: {tts.voices}")

    clearance = OpenClearance(
        id=f"demo-{int(time.time())}",
        callsign="ACA123",
        items=[
            Item(type="altitude", value=240, unit="FL", action="descend"),
            Item(type="frequency", value=124.65, unit="MHz", action="contact departure"),
        ],
        issued_at=0.0,
    )
    pilot = AIPilot("ACA123", rng=random.Random(int(time.time())), tts=tts)
    t0 = time.perf_counter()
    r = pilot.respond(
        clearance,
        active_callsigns=["ACA123", "ACA133", "WJA456", "DLH253"],
        noise_level=noise,
        error_type=None if error_type == "none" else error_type,  # type: ignore[arg-type]
        force_error=error_type != "none",
    )
    dt = time.perf_counter() - t0
    print(f"controller: descend flight level two four zero, contact departure one two four decimal six five")
    print(f"pilot     : {r.text}")
    print(f"error     : {r.injected_error} ({r.error_description})")
    print(f"sim       : {r.acting_callsign} -> {r.sim_command.model_dump()}")
    print(f"audio     : {r.audio_path}  ({r.audio_duration_s}s, voice {r.voice}, synth+fx {dt:.2f}s)")
    if tts.last_error:
        print(f"tts fell back to silent: {tts.last_error}")
    gt_path = pilot.data_dir / "ground_truth.jsonl"
    last = gt_path.read_text().strip().splitlines()[-1]
    print("ground truth:", json.dumps(json.loads(last), indent=1))
    if r.audio_path:
        print(f"\nplay:  afplay {r.audio_path}")


if __name__ == "__main__":
    main()
