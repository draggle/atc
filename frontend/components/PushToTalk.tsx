"use client";

import { useEffect, useState } from "react";
import { useTowerState } from "@/lib/store";
import { radio } from "@/lib/radio";
import { useClient } from "./TowerApp";

/** A quiet text link and a small outline chip, the same two shapes the other cards use. */
const CHIP = "chip select-none";

export default function PushToTalk() {
  const { send } = useClient();
  const { sim } = useTowerState();
  const nextReadback = sim?.next_readback ?? "random";
  const [radioMuted, setRadioMuted] = useState(false);
  useEffect(() => setRadioMuted(radio?.muted ?? false), []);

  return (
    <section className="panel p-3 shrink-0">
      <div className="flex items-baseline justify-between mb-2.5">
        <h2 className="text-[13px] font-semibold text-fg">Demo</h2>
        <button
          onClick={() => { const m = !radioMuted; setRadioMuted(m); radio?.setMuted(m); }}
          title={radioMuted ? "The frequency is muted. Click to hear every transmission." : "Every transmission is played as it happens. Click to mute."}
          className={`text-[11px] underline decoration-dotted underline-offset-4 hover:text-fg ${radioMuted ? "text-muted" : "text-ok"}`}
        >
          {radioMuted ? "frequency muted" : "frequency on"}
        </button>
      </div>
      <div className="text-[11px] text-muted mb-2">Talk from the bar at the bottom: hold Space for the radio, Shift+Space for squack.</div>

      {/* Script the next pilot reply, so a catch happens on cue instead of by chance. One shot. */}
      <div>
        <div className="text-[11px] text-muted mb-1.5">Next readback</div>
        <div className="flex flex-wrap gap-1.5">
          {([["random", "By chance"], ["correct", "Correct"], ["wrong_value", "Wrong value"], ["wrong_aircraft", "Wrong plane"], ["missing_readback", "No reply"]] as const).map(([mode, label]) => {
            const on = nextReadback === mode;
            const benign = mode === "random" || mode === "correct";
            return (
              <button
                key={mode}
                onClick={() => send({ type: "set_next_readback", mode })}
                title={mode === "random" ? "Use the pilot error slider" : "Applies to the next instruction only, then goes back to chance"}
                className={`${CHIP} ${on ? (benign ? "border-fg/60 text-fg" : "chip-bad") : "hover:text-fg hover:border-fg/40"}`}
              >
                {label}
              </button>
            );
          })}
        </div>
      </div>
    </section>
  );
}
