"use client";

import { useTowerDispatch, useTowerState } from "@/lib/store";
import { useClient } from "./TowerApp";
import type { Lifecycle } from "@/lib/types";

const SPEEDS = [1, 5, 20, 60] as const;

// Colour only where it means something: green while the clock runs, amber while it is held.
const LABEL: Record<Lifecycle, { text: string; dot: string }> = {
  idle: { text: "No world", dot: "" },
  ready: { text: "Ready", dot: "" },
  running: { text: "Running", dot: "dot-ok" },
  paused: { text: "Paused", dot: "dot-warn" },
  ended: { text: "Ended", dot: "" },
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

  return (
    <div className="flex items-center gap-2">
      <button
        onClick={() => dispatch({ type: "set_setup_open", open: true })}
        className="btn"
        title="Choose the data source and scenario"
      >
        Setup
      </button>

      {lifecycle === "running" ? (
        <button onClick={() => send({ type: "pause" })} className="btn">
          Pause
        </button>
      ) : (
        // The one filled button on the screen once a world is loaded.
        <button
          onClick={() => send({ type: "start" })}
          disabled={!canStart}
          className={`btn ${canStart ? "btn-primary" : ""}`}
          title={canStart ? "Start the clock" : "Load a scenario first"}
        >
          {lifecycle === "paused" ? "Resume" : "Start"}
        </button>
      )}

      <button
        onClick={() => send({ type: "reset" })}
        disabled={!canReset}
        className="btn"
        title="Back to the world as it was loaded"
      >
        Reset
      </button>

      <div className="seg" title="Clock speed. Voice only keeps up at 1x.">
        {SPEEDS.map((s) => (
          <button
            key={s}
            onClick={() => send({ type: "set_speed", speed: s })}
            aria-pressed={Math.abs(speed - s) < 0.01}
            className="tabular-nums"
          >
            {s}x
          </button>
        ))}
      </div>

      <span className="flex items-center gap-1.5 text-xs text-muted whitespace-nowrap">
        <i className={`dot ${label.dot}`} />
        {label.text}
      </span>
    </div>
  );
}
