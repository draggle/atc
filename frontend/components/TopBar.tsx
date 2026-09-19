"use client";

import { useTowerDispatch, useTowerState } from "@/lib/store";
import { useClient } from "./TowerApp";

function fmtClock(t: number): string {
  const s = Math.max(0, Math.floor(t));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

function Toggle({ on, label, onChange, activeClass = "bg-ok/20 text-ok border-ok/40" }: { on: boolean; label: string; onChange: (v: boolean) => void; activeClass?: string }) {
  return (
    <button
      onClick={() => onChange(!on)}
      className={`px-3 py-1 rounded-md border text-xs font-medium transition-colors ${on ? activeClass : "bg-panel-2 text-muted border-line hover:text-fg"}`}
    >
      {label} <span className="font-mono">{on ? "ON" : "OFF"}</span>
    </button>
  );
}

export default function TopBar() {
  const { sim, plan, connection, planView, scoreboard } = useTowerState();
  const dispatch = useTowerDispatch();
  const { send } = useClient();

  const milesSaved = plan ? Math.max(0, plan.baseline_distance_nm - plan.total_distance_nm) : (scoreboard?.miles_saved ?? 0);
  const conflicts = plan?.conflicts ?? 0;

  const connBadge: Record<typeof connection, { text: string; cls: string }> = {
    live: { text: "LIVE", cls: "bg-ok/15 text-ok border-ok/40" },
    mock: { text: "MOCK", cls: "bg-warn/15 text-warn border-warn/40" },
    connecting: { text: "CONNECTING", cls: "bg-panel-2 text-muted border-line" },
    closed: { text: "RECONNECTING", cls: "bg-bad/15 text-bad border-bad/40" },
  };
  const badge = connBadge[connection];

  return (
    <header className="panel h-12 shrink-0 flex items-center gap-3 px-3">
      <div className="flex items-baseline gap-2 min-w-0">
        <span className="text-lg font-semibold tracking-tight">Tower</span>
        <span className="text-xs text-muted truncate">{sim?.scenario ?? "No scenario"}</span>
      </div>
      <span className="font-mono text-sm text-fg/90 tabular-nums">{fmtClock(sim?.t ?? 0)}</span>

      <div className="h-6 w-px bg-line" />

      <Toggle
        on={sim?.tower_enabled ?? true}
        label="Tower"
        onChange={(v) => {
          dispatch({ type: "local_toggle", key: "tower_enabled", value: v });
          send({ type: "set_tower", enabled: v });
        }}
      />
      <Toggle
        on={sim?.auto_speak ?? false}
        label="Auto-speak"
        activeClass="bg-accent/15 text-accent border-accent/40"
        onChange={(v) => {
          dispatch({ type: "local_toggle", key: "auto_speak", value: v });
          send({ type: "set_auto_speak", enabled: v });
        }}
      />

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
