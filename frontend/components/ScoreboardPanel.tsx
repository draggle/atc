"use client";

import { useTowerState } from "@/lib/store";

function Stat({ label, value, tone = "fg" }: { label: string; value: string; tone?: "fg" | "ok" | "bad" | "warn" }) {
  const cls = { fg: "text-fg", ok: "text-ok", bad: "text-bad", warn: "text-warn" }[tone];
  return (
    <div className="rounded-md bg-panel-2 border border-line px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`font-mono text-base tabular-nums ${cls}`}>{value}</div>
    </div>
  );
}

const fmt = (n: number | null | undefined, d = 1, suffix = "") => (n === null || n === undefined ? "—" : `${n.toFixed(d)}${suffix}`);

export default function ScoreboardPanel() {
  const { scoreboard: s, stats, sim } = useTowerState();
  // Live snapshot: the standard line is each flight's projected track, so there is nothing to save.
  const projected = sim?.meta?.live === true;
  return (
    <section className="panel p-2.5 shrink-0">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-xs uppercase tracking-wider text-muted">Scoreboard</h2>
        <span className="text-[10px] text-muted">measured this session</span>
      </div>
      {!s ? (
        <p className="text-xs text-muted text-center py-2">No numbers yet.</p>
      ) : (
        <div className="grid grid-cols-3 gap-1.5">
          {/* Reaction first: how fast the traffic turned, and that it stayed out. */}
          <Stat label="first turn" value={fmt(s.reaction_s, 0, " s")} tone={s.reaction_s != null && s.reaction_s <= 5 ? "ok" : "fg"} />
          <Stat label="rerouted" value={String(s.rerouted ?? 0)} />
          <Stat label="in a zone" value={`${s.in_zone_now ?? 0} now · ${s.zone_incursions ?? 0} ever`} tone={(s.zone_incursions ?? 0) > 0 ? "bad" : "ok"} />
          <Stat label="miles saved" value={projected ? "n/a" : fmt(s.miles_saved)} tone={projected ? "fg" : "ok"} />
          <Stat label="time saved" value={projected ? "n/a" : fmt(s.time_saved_s / 60, 1, " min")} tone={projected ? "fg" : "ok"} />
          <Stat label="loss of sep" value={String(s.losses_of_separation)} tone={s.losses_of_separation > 0 ? "bad" : "ok"} />
          <Stat label="closest" value={fmt(s.closest_approach_nm, 1, " NM")} tone={s.closest_approach_nm !== null && s.closest_approach_nm < 5 ? "bad" : "fg"} />
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
