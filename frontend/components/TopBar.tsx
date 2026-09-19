"use client";

import { useTowerDispatch, useTowerState } from "@/lib/store";
import { useClient } from "./TowerApp";
import LifecycleControls from "./LifecycleControls";

function fmtClock(t: number): string {
  const s = Math.max(0, Math.floor(t));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

function Toggle({
  on,
  label,
  onChange,
  activeClass = "bg-ok/20 text-ok border-ok/40",
  inactiveClass = "bg-panel-2 text-muted border-line hover:text-fg",
  title,
}: {
  on: boolean;
  label: string;
  onChange: (v: boolean) => void;
  activeClass?: string;
  inactiveClass?: string;
  title?: string;
}) {
  return (
    <button
      onClick={() => onChange(!on)}
      title={title}
      className={`px-3 py-1 rounded-md border text-xs font-medium transition-colors ${on ? activeClass : inactiveClass}`}
    >
      {label} <span className="font-mono">{on ? "ON" : "OFF"}</span>
    </button>
  );
}

export default function TopBar() {
  const { sim, plan, connection, planView, scoreboard } = useTowerState();
  const dispatch = useTowerDispatch();
  const { send } = useClient();

  // The backend's figure is frozen at the first plan. After a replan the plan only holds what is
  // left to fly, so comparing it with the full baseline would invent thousands of miles.
  const milesSaved = scoreboard?.miles_saved ?? (plan ? Math.max(0, plan.baseline_distance_nm - plan.total_distance_nm) : 0);
  const conflicts = plan?.conflicts ?? 0;

  const connBadge: Record<typeof connection, { text: string; cls: string }> = {
    live: { text: "LIVE", cls: "bg-ok/15 text-ok border-ok/40" },
    mock: { text: "MOCK", cls: "bg-warn/15 text-warn border-warn/40" },
    connecting: { text: "CONNECTING", cls: "bg-panel-2 text-muted border-line" },
    closed: { text: "RECONNECTING", cls: "bg-bad/15 text-bad border-bad/40" },
  };
  const badge = connBadge[connection];

  return (
    <header className="panel min-h-12 shrink-0 flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1">
      <div className="flex items-baseline gap-2 min-w-0">
        <span className="text-lg font-semibold tracking-tight">Tower</span>
        <span className="text-xs text-muted truncate max-w-[260px]" title={sim?.scenario ?? undefined}>
          {sim?.source === "real" && sim.meta
            ? `${(sim.meta.label ?? "Real traffic").split(" (")[0]} · ${sim.meta.date} ${String(sim.meta.hour_utc ?? 0).padStart(2, "0")}:00Z`
            : (sim?.scenario ?? "No scenario")}
        </span>
      </div>
      <span className="font-mono text-sm text-fg/90 tabular-nums">{fmtClock(sim?.t ?? 0)}</span>

      <LifecycleControls />

      <div className="h-6 w-px bg-line" />

      <Toggle
        on={sim?.tower_enabled ?? true}
        label="Tower"
        inactiveClass="bg-zinc-600/60 text-zinc-200 border-zinc-400 hover:bg-zinc-500/60"
        title={sim?.tower_enabled === false ? "Tower is off: readbacks are not being checked" : "Tower is checking every readback"}
        onChange={(v) => {
          dispatch({ type: "local_toggle", key: "tower_enabled", value: v });
          send({ type: "set_tower", enabled: v });
        }}
      />
      {/* Who issues the instructions. The switch is the controller's, at any moment. */}
      <div
        className="flex rounded-md border border-line overflow-hidden text-xs"
        title="Manual: Tower proposes each instruction and you say it. Auto: Tower says them itself, one at a time, and sends the rest by data link. Hold the mic in Auto and Tower waits for you."
      >
        {([["Manual", false], ["Auto", true]] as const).map(([label, auto]) => {
          const on = (sim?.auto_speak ?? false) === auto;
          return (
            <button
              key={label}
              onClick={() => {
                dispatch({ type: "local_toggle", key: "auto_speak", value: auto });
                send({ type: "set_auto_speak", enabled: auto });
              }}
              className={`px-3 py-1 font-medium transition-colors ${on ? (auto ? "bg-warn/20 text-warn" : "bg-accent/20 text-accent") : "bg-panel-2 text-muted hover:text-fg"}`}
            >
              {label}
            </button>
          );
        })}
      </div>

      <div className="h-6 w-px bg-line" />

      <div className="flex rounded-md border border-line overflow-hidden text-xs">
        {(["today", "tower"] as const).map((v) => (
          <button
            key={v}
            onClick={() => dispatch({ type: "set_plan_view", view: v })}
            className={`px-3 py-1 capitalize ${planView === v ? "bg-accent/20 text-accent" : "bg-panel-2 text-muted hover:text-fg"}`}
          >
            {v === "today" ? "Today" : "Tower plan"}
          </button>
        ))}
      </div>

      <div className="flex items-center gap-4 text-xs">
        <div>
          <span className="text-muted">miles saved </span>
          <span className="font-mono text-ok tabular-nums">{milesSaved.toFixed(1)}</span>
        </div>
        <div>
          <span className="text-muted">conflicts </span>
          <span className={`font-mono tabular-nums ${conflicts > 0 ? "text-bad" : "text-ok"}`}>{conflicts}</span>
        </div>
        {scoreboard && (
          <div>
            <span className="text-muted">LoS </span>
            <span className={`font-mono tabular-nums ${scoreboard.losses_of_separation > 0 ? "text-bad" : "text-ok"}`}>{scoreboard.losses_of_separation}</span>
          </div>
        )}
      </div>

      <div className="ml-auto flex items-center gap-2">
        <span className={`px-2 py-0.5 rounded border text-[10px] font-mono tracking-wider ${badge.cls}`}>{badge.text}</span>
      </div>
    </header>
  );
}
