"use client";

import { snapshotClock, useTowerDispatch, useTowerState } from "@/lib/store";
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
  const { sim, plan, connection, scoreboard } = useTowerState();
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

  // A live snapshot is still source "real": the flights are real, only the moment differs.
  const place = (sim?.meta?.label ?? sim?.meta?.region ?? "Real traffic").split(" (")[0];
  const live = sim?.source === "real" && sim.meta?.live === true;
  const snapshotAt = snapshotClock(sim?.meta?.snapshot_utc);
  // The mock plays the same scripted flights whatever it is asked for: never call those live.
  const snapshotTag = connection === "mock" ? "MOCK SNAPSHOT" : sim?.meta?.fallback === "saved_snapshot" ? "SAVED SNAPSHOT" : "LIVE SNAPSHOT";
  const liveTitle = connection === "mock"
    ? "Scripted mock traffic, not the real sky. Start the backend for a live snapshot."
    : `One snapshot of the real sky${snapshotAt ? `, taken ${snapshotAt}` : ""}. The simulator flies it from there.${
        sim?.meta?.fallback === "saved_snapshot" ? " The live feed was unavailable, so this is the saved snapshot from that time." : ""
      }`;

  return (
    <header className="panel min-h-12 shrink-0 flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1">
      <div className="flex items-baseline gap-2 min-w-0">
        <span className="text-lg font-semibold tracking-tight">Tower</span>
        <span className="text-xs text-muted truncate max-w-[300px]" title={live ? liveTitle : (sim?.scenario ?? undefined)}>
          {live ? (
            <>
              <span className="font-mono text-[10px] tracking-wider text-accent">{snapshotTag}</span>
              {` · ${place}${snapshotAt ? ` · ${snapshotAt}` : ""}`}
            </>
          ) : sim?.source === "real" && sim.meta ? (
            `${place} · ${sim.meta.date} ${String(sim.meta.hour_utc ?? 0).padStart(2, "0")}:00Z`
          ) : (
            (sim?.scenario ?? "No scenario")
          )}
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
      {/* The one switch, and it is the controller's at any moment.
          Off: Tower sends every instruction by data link, instantly. The path demo.
          On: the real loop. You say each card, the pilot reads it back, Tower checks both. */}
      <div
        className="flex items-center rounded-md border border-line overflow-hidden text-xs"
        title="Voice off: Tower sends every instruction by data link the instant the plan changes. Voice on: you say each instruction, the pilot reads it back, and our Whisper model checks both. Voice runs at 1x."
      >
        <span className="px-2 py-1 text-muted bg-panel-2 border-r border-line">Voice</span>
        {([["Off", false], ["On", true]] as const).map(([label, on]) => {
          const active = (sim?.voice ?? !(sim?.auto_speak ?? false)) === on;
          return (
            <button
              key={label}
              onClick={() => {
                dispatch({ type: "local_toggle", key: "auto_speak", value: !on });
                send({ type: "set_voice", enabled: on });
              }}
              className={`px-3 py-1 font-medium transition-colors ${active ? (on ? "bg-ok/20 text-ok" : "bg-warn/20 text-warn") : "bg-panel-2 text-muted hover:text-fg"}`}
            >
              {label}
            </button>
          );
        })}
      </div>

      {/* Auto with Tower's own voice is paused while we get the human side right: Auto is silent
          (data link) and the spoken loop is Manual. The backend still supports it: send
          {type: "set_auto_voice", enabled: true} to bring it back, and restore this switch. */}

      <div className="h-6 w-px bg-line" />


      <div className="flex items-center gap-4 text-xs">
        <div>
          <span className="text-muted">miles saved </span>
          {/* live snapshot: the standard line is the projected track, so there is nothing to save */}
          <span className={`font-mono tabular-nums ${live ? "text-muted" : "text-ok"}`}>{live ? "n/a" : milesSaved.toFixed(1)}</span>
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
