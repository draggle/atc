"use client";

import { useEffect, useRef, useState } from "react";
import { snapshotClock, useTowerDispatch, useTowerState, type Connection } from "@/lib/store";
import { useClient } from "./TowerApp";
import type { Lifecycle, SimState } from "@/lib/types";
import DisruptMenu from "./DisruptMenu";

const SPEEDS = [1, 5, 20, 60] as const;

function fmtClock(t: number): string {
  const s = Math.max(0, Math.floor(t));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

/** "Live snapshot · Europe · 14:32 UTC", "Europe · 2025-09-18 14:00Z" or the scenario's name. */
export function scenarioLabel(sim: SimState | null, connection: Connection): string {
  // A live snapshot is still source "real": the flights are real, only the moment differs.
  const place = (sim?.meta?.label ?? sim?.meta?.region ?? "Real traffic").split(" (")[0];
  const live = sim?.source === "real" && sim.meta?.live === true;
  const snapshotAt = snapshotClock(sim?.meta?.snapshot_utc);
  // The mock plays the same scripted flights whatever it is asked for: never call those live.
  const snapshotTag = connection === "mock" ? "Mock snapshot" : sim?.meta?.fallback === "saved_snapshot" ? "Saved snapshot" : "Live snapshot";
  if (live) return `${snapshotTag} · ${place}${snapshotAt ? ` · ${snapshotAt}` : ""}`;
  if (sim?.source === "real" && sim.meta) return `${place} · ${sim.meta.date} ${String(sim.meta.hour_utc ?? 0).padStart(2, "0")}:00Z`;
  return sim?.scenario ?? "No scenario";
}

const Play = () => <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden><path d="M4 2.5v11l9-5.5z" /></svg>;
const Pause = () => <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden><path d="M3.5 2.5h3v11h-3zM9.5 2.5h3v11h-3z" /></svg>;
/** A circular arrow, anticlockwise: restart. Drawn here, no icon library. */
const Restart = () => (
  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M3 12a9 9 0 1 0 2.64-6.36L3 8" />
    <path d="M3 3v5h5" />
  </svg>
);
const Menu = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
    <path d="M4 7h16M4 12h16M4 17h16" />
  </svg>
);

/** Three groups over the map: the sky on the left, the clock in the middle, status and settings on the right. */
export default function TopBar() {
  const { sim, plan, connection, aircraft, alerts, settingsOpen } = useTowerState();
  const dispatch = useTowerDispatch();
  const { send } = useClient();

  const lifecycle: Lifecycle = sim?.lifecycle ?? (sim?.scenario ? "running" : "idle");
  const speed = sim?.speed ?? 1;
  const canStart = lifecycle === "ready" || lifecycle === "paused";
  const voiceOn = sim?.voice ?? !(sim?.auto_speak ?? false);
  // Voice on at a fast clock: it slows itself to 1x while there is something to say.
  const slowedForVoice = voiceOn && speed > 1 && lifecycle === "running" && (sim?.clock_speed ?? 1) <= 1;

  const nAircraft = Object.values(aircraft).filter((a) => !a.is_intruder).length;
  const conflicts = plan?.conflicts ?? 0;
  const wrong = alerts.filter((a) => !a.resolved).length;
  const live = sim?.source === "real" && sim.meta?.live === true;
  const liveTitle = connection === "mock"
    ? "Scripted mock traffic, not the real sky. Start the backend for a live snapshot."
    : live
      ? `One snapshot of the real sky. The simulator flies it from there.${sim?.meta?.fallback === "saved_snapshot" ? " The live feed was unavailable, so this is the saved snapshot from that time." : ""}`
      : sim?.scenario ?? undefined;

  // The speed menu: a muted "1x ▾" that opens a short list. Closes on a pick, a click elsewhere, or Escape.
  const [speedOpen, setSpeedOpen] = useState(false);
  const speedRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!speedOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!speedRef.current?.contains(e.target as Node)) setSpeedOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSpeedOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [speedOpen]);

  return (
    <header className="h-11 grid grid-cols-[1fr_auto_1fr] items-center gap-4 px-3">
      {/* left: the sky */}
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-[15px] font-semibold tracking-tight leading-none">squack.</span>
        <button
          onClick={() => dispatch({ type: "set_setup_open", open: true })}
          className="btn !border-transparent max-w-[320px]"
          title={liveTitle ? `${liveTitle} Click to choose another sky.` : "Choose the data source and scenario"}
        >
          <span className="truncate">{scenarioLabel(sim, connection)}</span>
          <span className="opacity-60">▾</span>
        </button>
      </div>

      {/* centre: the clock */}
      <div className="flex items-center gap-3">
        <button
          onClick={() => send({ type: "reset" })}
          disabled={lifecycle === "idle"}
          className="btn btn-round"
          title="Restart the scenario"
          aria-label="Restart"
        >
          <Restart />
        </button>
        {lifecycle === "running" ? (
          <button onClick={() => send({ type: "pause" })} className="btn btn-round" title="Pause the clock" aria-label="Pause">
            <Pause />
          </button>
        ) : canStart ? (
          // The one filled button on the screen once a world is loaded.
          <button onClick={() => send({ type: "start" })} className="btn btn-primary" title="Start the clock">
            <Play />
            {lifecycle === "paused" ? "Resume" : "Start"}
          </button>
        ) : (
          <button disabled className="btn btn-round" title={lifecycle === "ended" ? "Every flight has left. Press restart." : "Load a sky first"} aria-label="Start">
            <Play />
          </button>
        )}
        <span className="text-sm font-mono tabular-nums text-fg/90">{fmtClock(sim?.t ?? 0)}</span>
        <div className="relative" ref={speedRef}>
          <button
            onClick={() => setSpeedOpen((o) => !o)}
            aria-expanded={speedOpen}
            aria-haspopup="menu"
            className="btn !border-transparent tabular-nums"
            title={slowedForVoice ? "Manual: the clock is at 1x while there is something to say, and back to your speed between instructions." : "Clock speed. Manual only keeps up at 1x."}
          >
            {speed}x{slowedForVoice && <span className="opacity-60">· 1x now</span>}
            <span className="opacity-60">▾</span>
          </button>
          {speedOpen && (
            <div role="menu" className="panel absolute left-1/2 -translate-x-1/2 top-full mt-1 py-1 min-w-[72px] flex flex-col">
              {SPEEDS.map((s) => (
                <button
                  key={s}
                  role="menuitemradio"
                  aria-checked={Math.abs(speed - s) < 0.01}
                  onClick={() => { send({ type: "set_speed", speed: s }); setSpeedOpen(false); }}
                  className={`px-3 h-7 text-left text-xs tabular-nums hover:bg-panel-2 ${Math.abs(speed - s) < 0.01 ? "text-fg font-semibold" : "text-muted"}`}
                >
                  {s}x
                </button>
              ))}
            </div>
          )}
        </div>
        <DisruptMenu />
      </div>

      {/* right: one status line, then the menu */}
      <div className="flex items-center justify-end gap-3 min-w-0">
        <span className="text-xs text-muted whitespace-nowrap truncate">
          <span className="text-fg font-semibold">{nAircraft}</span> aircraft
          {" · "}
          <span title="Pairs of flights the plan could not keep apart. Zero means every flight has a clear path.">
            <span className={conflicts > 0 ? "text-bad font-semibold" : "text-fg font-semibold"}>{conflicts}</span>
            <span className={conflicts > 0 ? "text-bad" : ""}> {conflicts === 1 ? "conflict" : "conflicts"}</span>
          </span>
          {wrong > 0 && <> · <span className="text-bad">{wrong} wrong {wrong === 1 ? "readback" : "readbacks"}</span></>}
          {connection !== "live" && ` · ${connection === "closed" ? "reconnecting" : connection}`}
        </span>
        <button
          onClick={() => dispatch({ type: "set_settings_open", open: true })}
          aria-expanded={settingsOpen}
          aria-controls="settings-sheet"
          className="btn btn-round"
          title="Settings"
          aria-label="Settings"
        >
          <Menu />
        </button>
      </div>
    </header>
  );
}
