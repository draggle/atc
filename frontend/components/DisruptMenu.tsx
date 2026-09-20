"use client";

import { useEffect, useRef, useState } from "react";
import { useTowerDispatch, useTowerState } from "@/lib/store";
import { useClient } from "./TowerApp";
import type { Disruption, DisruptionKindInfo, Lifecycle } from "@/lib/types";

/** Used until the backend sends its own menu in `state.sim.disruption_kinds` (and by the mock). */
export const KINDS: DisruptionKindInfo[] = [
  { kind: "fighter", label: "Fighter jet", blurb: "Fast, straight through, not talking to anyone.", shape: "point" },
  { kind: "drone", label: "Drone", blurb: "Slow and small, loitering at cruise level.", shape: "point", menu: false },
  { kind: "balloon", label: "Balloon", blurb: "Drifting with the wind.", shape: "point", menu: false },
  { kind: "emergency", label: "Emergency aircraft", blurb: "One of our flights declares a mayday and descends.", shape: "point" },
  { kind: "unknown", label: "Unknown target", blurb: "No height, no identity. Blocked at every level.", shape: "point", menu: false },
  { kind: "storm", label: "Storm cell", blurb: "Drifts and swells.", shape: "circle" },
  { kind: "closed", label: "Closed airspace", blurb: "A block of levels shut for a while.", shape: "circle", menu: false },
  { kind: "rocket", label: "Rocket launch", blurb: "A tall column, gone in minutes.", shape: "circle" },
];

/** Minutes left on a disruption, or "" when it never expires. Mirrors the map's zone labels. */
export const minutesLeft = (expires: number | null | undefined, t: number): string =>
  expires == null ? "" : `${Math.max(0, Math.ceil((expires - t) / 60))} min`;

/**
 * A burst: four short rays around a small circle. Chosen over a lightning bolt because at 14px a
 * bolt's zigzag closes up into a blob, while four separated rays stay four rays.
 */
const Burst = () => (
  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
  </svg>
);

/** An active disruption's id: red for a thing in the sky, amber for one of ours in trouble, white for a volume. */
const toneOf = (d: Disruption) => (d.kind === "emergency" ? "text-warn" : d.shape === "point" ? "text-bad" : "text-fg");

/**
 * Disrupt, in the top bar beside the clock. Opens a popover: one line of what this does, a
 * surprise-me row, the kinds squack knows, and whatever is already out there.
 */
export default function DisruptMenu() {
  const { sim, disruptions, dropMode, panel } = useTowerState();
  const dispatch = useTowerDispatch();
  const { send } = useClient();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  const lifecycle: Lifecycle = sim?.lifecycle ?? (sim?.scenario ? "running" : "idle");
  const kinds = (sim?.disruption_kinds?.length ? sim.disruption_kinds : KINDS).filter((k) => k.menu !== false);
  const active = Object.values(disruptions).filter((d) => d.active !== false);

  // squack can ask for this menu: `ui_command {command:"panel", args:{name:"disrupt"}}`. Consume
  // the request so closing it by hand does not fight the stored panel name.
  useEffect(() => {
    if (panel !== "disrupt") return;
    setOpen(true);
    dispatch({ type: "set_panel", panel: null });
  }, [panel, dispatch]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Escape cancels an armed kind, as the hint over the map promises.
  useEffect(() => {
    if (!dropMode) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") dispatch({ type: "set_drop_mode", kind: null });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dropMode, dispatch]);

  const arm = (kind: DisruptionKindInfo["kind"]) => {
    dispatch({ type: "set_drop_mode", kind });
    setOpen(false);
  };

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        disabled={lifecycle === "idle"}
        aria-expanded={open}
        aria-haspopup="dialog"
        className="btn btn-round"
        title="Disrupt the airspace"
        aria-label="Disrupt"
      >
        <Burst />
      </button>
      {open && (
        // The wrapper holds the position (Tailwind's -translate-x-1/2); the panel inside animates,
        // because the keyframes set `transform` and would otherwise undo that centring.
        <div className="absolute left-1/2 -translate-x-1/2 top-full mt-1.5 w-[380px] z-30">
        <div role="dialog" aria-label="Disrupt the airspace" className="panel pop-down w-full p-3 text-left">
          <p className="text-[12px] text-muted leading-snug">
            Drop something into the airspace that squack has to work around: weather, an intruder, a
            closed block, an emergency. The plan repairs itself and the new instructions appear.
          </p>

          <button
            className="card-pick mt-3 !py-2.5"
            onClick={() => { dispatch({ type: "set_drop_mode", kind: null }); setOpen(false); send({ type: "add_disruption", kind: "random" }); }}
          >
            <div className="text-[13px] font-semibold text-fg">Surprise me</div>
            <div className="text-[11px] text-muted mt-0.5">squack picks the kind and puts it where it will matter.</div>
          </button>

          <div className="mt-3 flex flex-col">
            {kinds.map((k) => (
              <button key={k.kind} className="text-left px-2 py-1.5 rounded-md hover:bg-panel-2" onClick={() => arm(k.kind)}>
                <div className="text-[13px] text-fg">{k.label}</div>
                <div className="text-[11px] text-muted leading-snug">{k.blurb}</div>
              </button>
            ))}
          </div>

          {active.length > 0 && (
            <div className="mt-3 border-t border-line pt-2.5">
              <div className="text-[11px] text-muted mb-1.5">Out there now</div>
              <div className="flex flex-wrap gap-1.5">
                {active.map((d) => (
                  <span key={d.id} className="chip font-mono">
                    <button className={toneOf(d)} title={d.label} onClick={() => { if (d.shape === "point") { dispatch({ type: "select", callsign: d.id }); setOpen(false); } }}>
                      {d.id}
                    </button>
                    <span className="text-muted">{minutesLeft(d.expires_t, sim?.t ?? 0) || d.label}</span>
                    {d.kind !== "emergency" && (
                      <button className="text-muted hover:text-fg" title="Remove it" onClick={() => send({ type: "remove_disruption", id: d.id })}>✕</button>
                    )}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
        </div>
      )}
      {dropMode && (
        // The armed hint, at the bottom edge above the command bar. Nothing floats in the middle
        // of the map, and nothing sits over the top bar.
        <div className="fixed left-1/2 -translate-x-1/2 bottom-[108px] z-20 pointer-events-none">
          <span className="hint">
            <span className="text-fg">
              {dropMode === "emergency" ? "Click near the flight that declares the emergency." : `Click the map to place the ${(kinds.find((k) => k.kind === dropMode)?.label ?? dropMode).toLowerCase()}.`}
            </span>
            <span>Escape to cancel.</span>
          </span>
        </div>
      )}
    </div>
  );
}
