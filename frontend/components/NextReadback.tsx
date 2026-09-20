"use client";

import { useTowerState } from "@/lib/store";
import { useClient } from "./TowerApp";

/**
 * Script the next pilot reply, so a catch happens on cue instead of by chance. One shot: after the
 * next instruction it goes back to chance by itself. It sits in the right rail under the cards,
 * because it is pressed in the same breath as "say this card"; it used to be one more row at the
 * bottom of the settings sheet, which is two clicks and a scroll away in the middle of a demo.
 */
const READBACKS = [
  ["random", "By chance", "Use the pilot error rate from Settings"],
  ["correct", "Correct", "The next readback is right, whatever the error rate"],
  ["wrong_value", "Wrong value", "The next readback has a wrong number or a wrong fix in it"],
  ["wrong_aircraft", "Wrong plane", "Another aircraft answers the next instruction"],
  ["missing_readback", "No reply", "Nobody answers the next instruction"],
] as const;

export default function NextReadback() {
  const { sim } = useTowerState();
  const { send } = useClient();
  const next = sim?.next_readback ?? "random";
  const voiceOn = sim ? (sim.voice ?? !sim.auto_speak) : true;
  const armed = next !== "random";
  return (
    <section className="panel px-3 py-2.5 shrink-0 select-none" aria-label="Next readback">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-[13px] font-semibold text-fg">Next readback</h2>
        <span className={`text-[11px] ${armed ? "text-warn" : "text-muted"}`}>
          {!voiceOn ? "nothing is read back in Autonomous" : armed ? "set for the next instruction only" : "as the error rate decides"}
        </span>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5" role="group" aria-label="What the next pilot reply will be">
        {READBACKS.map(([mode, label, hint]) => {
          const on = next === mode;
          const wrong = mode !== "random" && mode !== "correct";
          return (
            <button
              key={mode}
              type="button"
              disabled={!voiceOn}
              onClick={() => send({ type: "set_next_readback", mode })}
              aria-pressed={on}
              title={hint}
              className={`pill cursor-pointer disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-fg/70 ${
                on ? (wrong ? "!bg-bad !text-bg !border-bad font-semibold" : "pill-on") : ""
              }`}
            >
              {label}
            </button>
          );
        })}
      </div>
    </section>
  );
}
