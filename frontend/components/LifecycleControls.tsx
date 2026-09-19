"use client";

import { useTowerDispatch, useTowerState } from "@/lib/store";
import { useClient } from "./TowerApp";
import type { Lifecycle } from "@/lib/types";

const SPEEDS = [1, 5, 20, 60] as const;

const LABEL: Record<Lifecycle, { text: string; cls: string }> = {
  idle: { text: "No world", cls: "text-muted border-line" },
  ready: { text: "Ready", cls: "text-accent border-accent/40 bg-accent/10" },
  running: { text: "Running", cls: "text-ok border-ok/40 bg-ok/10" },
  paused: { text: "Paused", cls: "text-warn border-warn/40 bg-warn/10" },
  ended: { text: "Ended", cls: "text-muted border-line bg-panel-2" },
};

/** Setup, Start or Pause, Reset, and the clock speed. Nothing moves until Start. */
export default function LifecycleControls() {
  const { sim } = useTowerState();
  const dispatch = useTowerDispatch();
  const { send } = useClient();
  const lifecycle: Lifecycle = sim?.lifecycle ?? (sim?.scenario ? "running" : "idle");
  const speed = sim?.speed ?? 1;
  const canStart = lifecycle === "ready" || lifecycle === "paused";
  const canReset = lifecycle !== "idle";
  const label = LABEL[lifecycle];

  const btn = "px-3 py-1 rounded-md border text-xs font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed";

  return (
    <div className="flex items-center gap-2">
      <button
        onClick={() => dispatch({ type: "set_setup_open", open: true })}
        className={`${btn} bg-panel-2 text-muted border-line hover:text-fg`}
        title="Choose the data source and scenario"
      >
        Setup
      </button>

      {lifecycle === "running" ? (
        <button onClick={() => send({ type: "pause" })} className={`${btn} bg-warn/15 text-warn border-warn/40 hover:bg-warn/25`}>
          Pause
        </button>
      ) : (
        <button
          onClick={() => send({ type: "start" })}
          disabled={!canStart}
          className={`${btn} bg-ok/20 text-ok border-ok/50 hover:bg-ok/30`}
          title={canStart ? "Start the clock" : "Load a scenario first"}
        >
          {lifecycle === "paused" ? "Resume" : "Start"}
        </button>
      )}

      <button
        onClick={() => send({ type: "reset" })}
        disabled={!canReset}
        className={`${btn} bg-panel-2 text-muted border-line hover:text-fg`}
        title="Back to the world as it was loaded"
      >
        Reset
      </button>

      <div className="flex rounded-md border border-line overflow-hidden text-xs" title="Clock speed. Voice only keeps up at 1x.">
        {SPEEDS.map((s) => (
          <button
            key={s}
            onClick={() => send({ type: "set_speed", speed: s })}
            className={`px-2 py-1 font-mono ${Math.abs(speed - s) < 0.01 ? "bg-accent/20 text-accent" : "bg-panel-2 text-muted hover:text-fg"}`}
          >
            {s}x
          </button>
        ))}
      </div>

      <span className={`px-2 py-0.5 rounded border text-[10px] font-mono tracking-wider uppercase ${label.cls}`}>{label.text}</span>
    </div>
  );
}
