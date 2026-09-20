"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { useTowerState } from "@/lib/store";

/** A single ⌄, turned over when the panel is open. */
function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden
      className="transition-transform duration-[220ms] motion-reduce:transition-none"
      style={{ transform: open ? "rotate(180deg)" : "none" }}
    >
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

const OPEN_MS = 220;
const CLOSE_MS = 200;
const EASE = "cubic-bezier(.22,1,.36,1)";
const snap = () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/**
 * Height animation over unknown content: measure the body, drive `max-height` to it, then clear the
 * cap once open so the body scrolls normally afterwards. Reduced motion snaps.
 */
function useReveal(open: boolean) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [style, setStyle] = useState<React.CSSProperties>({ maxHeight: 0, opacity: 0, overflow: "hidden" });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (snap()) {
      setStyle(open ? { opacity: 1 } : { maxHeight: 0, opacity: 0, overflow: "hidden" });
      return;
    }
    const ms = open ? OPEN_MS : CLOSE_MS;
    setStyle({
      maxHeight: open ? el.scrollHeight + 8 : 0,
      opacity: open ? 1 : 0,
      overflow: "hidden",
      transition: `max-height ${ms}ms ${EASE}, opacity ${ms}ms ${EASE}`,
    });
    if (!open) return;
    const t = setTimeout(() => setStyle({ opacity: 1 }), ms);
    return () => clearTimeout(t);
  }, [open]);
  return { ref, style };
}

/** The command bar owns ⌘K; there is no store action for "focus the bar", so the row reaches for
 *  the input the bar renders. One DOM query beats a new reducer case for a hint. */
const focusCommandBar = () =>
  document.querySelector<HTMLInputElement>('input[aria-label*="squack"]')?.focus();

/** One row of the list: a muted label, a white number. Colour only when the number means something. */
function Stat({ label, value, tone = "fg" }: { label: string; value: string; tone?: "fg" | "ok" | "bad" | "warn" }) {
  const cls = { fg: "text-fg", ok: "text-ok", bad: "text-bad", warn: "text-warn" }[tone];
  return (
    <div className="border-t border-line py-1.5 first:border-t-0 [&:nth-child(2)]:border-t-0">
      <div className="text-xs text-muted leading-tight">{label}</div>
      <div className={`text-xl font-medium leading-tight tabular-nums whitespace-nowrap ${cls}`}>{value}</div>
    </div>
  );
}

const fmt = (n: number | null | undefined, d = 1, suffix = "") => (n === null || n === undefined ? "—" : `${n.toFixed(d)}${suffix}`);
/** 850, 3.2k, 312k: a rate that is read at a glance and never rounds a hundred up to a thousand. */
const fmtRate = (n: number | null | undefined) => (n === null || n === undefined ? "—" : n >= 10000 ? `${Math.round(n / 1000)}k` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(Math.round(n)));

/** ⌘K on a Mac, Ctrl K elsewhere. Decided after mount: reading navigator during render would not
 *  match what the server wrote and React would throw a hydration mismatch. */
function useAskKey(): string {
  const [key, setKey] = useState("\u2318K");
  useEffect(() => {
    if (!/Mac/i.test(navigator.userAgent)) setKey("Ctrl K");
  }, []);
  return key;
}

export default function ScoreboardPanel() {
  const { scoreboard: s, stats, sim } = useTowerState();
  const [open, setOpen] = useState(false);
  const askKey = useAskKey();
  const bodyId = useId();
  const reveal = useReveal(open);
  // Live snapshot: the standard line is each flight's projected track, so there is nothing to save.
  const projected = sim?.meta?.live === true;
  const losses = s?.losses_of_separation ?? 0;
  const predicted = s?.conflicts_predicted ?? 0;

  return (
    <section className="panel px-3 py-2.5 shrink-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={bodyId}
        className="w-full flex items-baseline gap-3 text-left"
      >
        <h2 className="text-sm font-semibold text-fg">Analytics</h2>
        <span className="ml-auto text-xs text-muted tabular-nums whitespace-nowrap">
          <span className={losses > 0 ? "text-bad" : "text-ok"}>{losses} {losses === 1 ? "loss" : "losses"}</span>
          {" · "}
          <span className={predicted > (s?.conflicts_resolved ?? 0) ? "text-warn" : ""}>{predicted} predicted</span>
        </span>
        <span className="text-muted shrink-0 self-center"><Chevron open={open} /></span>
      </button>
      <div id={bodyId} ref={reveal.ref} style={reveal.style} className="mt-1 scroll-thin">
      {!s ? (
        <p className="text-xs text-muted py-2">No numbers yet.</p>
      ) : (
        <div className="grid grid-cols-2 gap-x-4">
          {/* Reaction first: how fast the traffic turned, and that it stayed out. */}
          <Stat label="first turn" value={fmt(s.reaction_s, 0, " s")} tone={s.reaction_s != null && s.reaction_s <= 5 ? "ok" : "fg"} />
          <Stat label="rerouted" value={String(s.rerouted ?? 0)} />
          <Stat label="in a zone" value={`${s.in_zone_now ?? 0} now · ${s.zone_incursions ?? 0} ever`} tone={(s.zone_incursions ?? 0) > 0 ? "bad" : "fg"} />
          <Stat label="miles saved" value={projected ? "n/a" : fmt(s.miles_saved)} />
          <Stat label="time saved" value={projected ? "n/a" : fmt(s.time_saved_s / 60, 1, " min")} />
          <Stat label="losses of separation" value={String(s.losses_of_separation)} tone={s.losses_of_separation > 0 ? "bad" : "ok"} />
          <Stat label="closest" value={fmt(s.closest_approach_nm, 1, " NM")} tone={s.closest_approach_nm !== null && s.closest_approach_nm < 5 ? "bad" : "fg"} />
          {/* Monte Carlo (TRD 07): what the rollouts saw coming, what cleared before it happened, and the measured rate. */}
          <Stat label="conflicts predicted" value={String(s.conflicts_predicted ?? 0)} tone={(s.conflicts_predicted ?? 0) > (s.conflicts_resolved ?? 0) ? "warn" : "fg"} />
          <Stat label="resolved early" value={String(s.conflicts_resolved ?? 0)} />
          <Stat label="futures / s" value={fmtRate(s.futures_per_s)} />
          <Stat label="errors caught" value={`${s.errors_caught} / ${s.errors_injected}`} tone={s.errors_caught < s.errors_injected ? "warn" : "fg"} />
          <Stat label="false alarms" value={String(s.false_alarms)} tone={s.false_alarms > 0 ? "warn" : "fg"} />
          <Stat label="alert latency" value={fmt(s.mean_alert_latency_s, 1, " s")} />
          <Stat label="tier 1" value={fmt(s.tier1_latency_s ?? (typeof stats?.tier1_latency_s === "number" ? stats.tier1_latency_s : null), 1, " s")} />
          <Stat label="transmissions" value={String(s.transmissions)} />
          <Stat label="by data link" value={String(s.datalink_sent ?? 0)} />
        </div>
      )}
      <button
        type="button"
        onClick={focusCommandBar}
        className="mt-2 w-full border-t border-line pt-2 flex items-center gap-2 text-left text-[11px] text-muted hover:text-fg"
      >
        <span style={{ fontFamily: "var(--font-mono)" }}>{askKey}</span>
        <span>to ask squack about any of this.</span>
      </button>
      </div>
    </section>
  );
}
