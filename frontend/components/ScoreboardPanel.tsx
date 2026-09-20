"use client";

import { useTowerState } from "@/lib/store";

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

export default function ScoreboardPanel() {
  const { scoreboard: s, stats, sim } = useTowerState();
  // Live snapshot: the standard line is each flight's projected track, so there is nothing to save.
  const projected = sim?.meta?.live === true;
  return (
    <section className="panel px-3 py-2.5 shrink-0">
      <div className="flex items-baseline justify-between mb-1">
        <h2 className="text-sm font-semibold text-fg">Scoreboard</h2>
        <span className="text-xs text-muted">measured this session</span>
      </div>
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
    </section>
  );
}
