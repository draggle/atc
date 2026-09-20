"use client";

import { snapshotClock, useTowerDispatch, useTowerState } from "@/lib/store";
import { useClient } from "./TowerApp";
import LifecycleControls from "./LifecycleControls";
import VoiceToggle from "./VoiceToggle";
import UiModeToggle from "./UiModeToggle";

function fmtClock(t: number): string {
  const s = Math.max(0, Math.floor(t));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

export default function TopBar() {
  const { sim, plan, connection, scoreboard } = useTowerState();
  const dispatch = useTowerDispatch();
  const { send } = useClient();

  // The backend's figure is frozen at the first plan. After a replan the plan only holds what is
  // left to fly, so comparing it with the full baseline would invent thousands of miles.
  const milesSaved = scoreboard?.miles_saved ?? (plan ? Math.max(0, plan.baseline_distance_nm - plan.total_distance_nm) : 0);
  const conflicts = plan?.conflicts ?? 0;

  // A tiny dot and a word. Green only when the real backend is on the line.
  const conn: Record<typeof connection, { text: string; dot: string }> = {
    live: { text: "live", dot: "dot-ok" },
    mock: { text: "mock", dot: "" },
    connecting: { text: "connecting", dot: "" },
    closed: { text: "reconnecting", dot: "dot-bad" },
  };
  const status = conn[connection];

  // A live snapshot is still source "real": the flights are real, only the moment differs.
  const place = (sim?.meta?.label ?? sim?.meta?.region ?? "Real traffic").split(" (")[0];
  const live = sim?.source === "real" && sim.meta?.live === true;
  const snapshotAt = snapshotClock(sim?.meta?.snapshot_utc);
  // The mock plays the same scripted flights whatever it is asked for: never call those live.
  const snapshotTag = connection === "mock" ? "Mock snapshot" : sim?.meta?.fallback === "saved_snapshot" ? "Saved snapshot" : "Live snapshot";
  const liveTitle = connection === "mock"
    ? "Scripted mock traffic, not the real sky. Start the backend for a live snapshot."
    : `One snapshot of the real sky${snapshotAt ? `, taken ${snapshotAt}` : ""}. The simulator flies it from there.${
        sim?.meta?.fallback === "saved_snapshot" ? " The live feed was unavailable, so this is the saved snapshot from that time." : ""
      }`;

  const towerOn = sim?.tower_enabled ?? true;

  return (
    <header className="panel min-h-11 shrink-0 flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-1">
      <div className="flex items-baseline gap-3 min-w-0">
        <span className="text-[15px] font-semibold tracking-tight leading-none">squack.</span>
        <span className="text-xs text-muted truncate max-w-[280px]" title={live ? liveTitle : (sim?.scenario ?? undefined)}>
          {live
            ? `${snapshotTag} · ${place}${snapshotAt ? ` · ${snapshotAt}` : ""}`
            : sim?.source === "real" && sim.meta
              ? `${place} · ${sim.meta.date} ${String(sim.meta.hour_utc ?? 0).padStart(2, "0")}:00Z`
              : (sim?.scenario ?? "No scenario")}
        </span>
      </div>
      <span className="text-sm tabular-nums text-fg/90">{fmtClock(sim?.t ?? 0)}</span>

      <LifecycleControls />

      <div className="h-5 w-px bg-line" />

      <div className="flex items-center gap-2">
        <button
          onClick={() => {
            const v = !towerOn;
            dispatch({ type: "local_toggle", key: "tower_enabled", value: v });
            send({ type: "set_tower", enabled: v });
          }}
          aria-pressed={towerOn}
          className={`pill ${towerOn ? "pill-on" : ""}`}
          title={towerOn ? "squack is checking every readback" : "squack is off: readbacks are not being checked"}
        >
          squack {towerOn ? "on" : "off"}
        </button>

        <VoiceToggle />

        {/* A backend started before the current screen ignores its newer controls, which then look as if
            they work and snap back. Say so where it cannot be missed, for as long as it is true. */}
        {sim && sim.lifecycle !== undefined && sim.voice === undefined && (
          <span
            className="chip chip-warn"
            title="This backend process was started before the Voice switch and card tags were added. Stop it (Ctrl+C, or: lsof -ti:8000 | xargs kill) and start uvicorn again."
          >
            Backend out of date · restart it
          </span>
        )}

        {/* Voice on at a fast clock: it slows itself to 1x while there is something to say. Show which. */}
        {sim && (sim.voice ?? !sim.auto_speak) && (sim.speed ?? 1) > 1 && sim.lifecycle === "running" && (
          <span
            className={`chip ${(sim.clock_speed ?? 1) <= 1 ? "chip-ok" : ""}`}
            title="With voice on, the clock runs at your chosen speed between instructions and drops to 1x by itself whenever a card is waiting or somebody is talking."
          >
            {(sim.clock_speed ?? 1) <= 1 ? "1x: something to say" : `${sim.speed}x until the next instruction`}
          </span>
        )}
      </div>

      {/* Auto with squack's own voice is paused while we get the human side right: Auto is silent
          (data link) and the spoken loop is Manual. The backend still supports it: send
          {type: "set_auto_voice", enabled: true} to bring it back, and restore this switch. */}

      <div className="h-5 w-px bg-line" />

      <div className="flex items-center gap-4">
        {/* live snapshot: the standard line is the projected track, so there is nothing to save */}
        <span className="stat">miles saved <b className={live ? "!text-muted !font-medium" : ""}>{live ? "n/a" : milesSaved.toFixed(1)}</b></span>
        <span className="stat">conflicts <b className={conflicts > 0 ? "!text-bad" : ""}>{conflicts}</b></span>
        {scoreboard && (
          <>
            <span className="stat">LoS <b className={scoreboard.losses_of_separation > 0 ? "!text-bad" : ""}>{scoreboard.losses_of_separation}</b></span>
            <span className="stat" title="Conflicts the Monte Carlo look-ahead predicted">predicted <b>{scoreboard.conflicts_predicted ?? 0}</b></span>
          </>
        )}
      </div>

      <div className="ml-auto flex items-center gap-1.5 text-xs text-muted">
        <UiModeToggle />
        <div className="h-5 w-px bg-line mx-1.5" />
        <i className={`dot ${status.dot}`} />
        <span>{status.text}</span>
      </div>
    </header>
  );
}
