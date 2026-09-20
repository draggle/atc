"use client";

import { alertFor, riskFor, useTowerDispatch, useTowerState, type ActiveAlert } from "@/lib/store";
import { ItemList, alertLook } from "./AlertCard";
import { Confidence } from "./InstructionCards";
import { useClient } from "./TowerApp";

const THREAT: Record<string, string> = {
  fighter: "Fighter jet, not talking to us",
  drone: "Drone",
  balloon: "Balloon, drifting",
  unknown: "Unknown target, no height",
  emergency: "Emergency, mayday declared",
};

const STATUS_CLS: Record<string, string> = {
  pending: "text-muted",
  spoken: "text-accent",
  validated: "text-ok",
  verified: "text-ok",
  error: "text-bad",
};

function Field({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="min-w-0">
      <div className="eyebrow">{label}</div>
      <div className={`font-mono text-sm tabular-nums truncate ${tone ?? "text-fg"}`}>{value}</div>
    </div>
  );
}

/** What is wrong with this aircraft, in the alert card's own words and tones. The card owns the actions. */
function Issue({ alert }: { alert: ActiveAlert }) {
  const { radar, title, frame, titleCls } = alertLook(alert);
  return (
    <div className={`rounded-md border-2 px-2.5 py-2 ${frame}`}>
      {radar && <div className="text-[10px] uppercase tracking-wider font-semibold text-cyan-200">Read back right, flying wrong</div>}
      <div className={`text-sm font-bold tracking-wide ${titleCls}`}>{title}</div>
      <div className="mt-1 grid grid-cols-2 gap-3 text-xs">
        <div className="min-w-0">
          <div className="eyebrow mb-0.5">Expected</div>
          <ItemList items={alert.expected} tone="expected" />
        </div>
        <div className="min-w-0">
          <div className="eyebrow mb-0.5">Heard</div>
          <ItemList items={alert.heard} tone="heard" />
        </div>
      </div>
      {alert.reason && <p className="mt-1.5 text-[11px] leading-snug text-fg/80">{alert.reason}</p>}
    </div>
  );
}

/** Everything Tower knows about one aircraft: where it is, what it was told, and what it did. */
export default function FlightStrip() {
  const state = useTowerState();
  const { selected, follow, aircraft, plan, cards, clearances, watching } = state;
  const dispatch = useTowerDispatch();
  const { send } = useClient();
  if (!selected) return null;
  const a = aircraft[selected];
  const alert = alertFor(state, selected);
  const risk = riskFor(state, selected, performance.now());
  const path = plan?.paths.find((p) => p.callsign === selected);
  const history = cards.filter((c) => c.callsign === selected).slice(-4).reverse();
  const open = Object.values(clearances).filter((c) => c.callsign === selected && c.status === "open");
  const climbing = a ? a.target_alt_ft - a.alt_ft : 0;
  const trend = Math.abs(climbing) < 150 ? "level" : climbing > 0 ? "climbing" : "descending";

  return (
    <div className="glass pointer-events-auto p-3 flex flex-col gap-3">
      {alert && <Issue alert={alert} />}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="eyebrow">{a?.is_intruder ? (THREAT[a.threat ?? ""] ?? "Uncooperative traffic") : "Flight"}</div>
          <div className={`font-mono text-xl font-bold tracking-wide ${a?.threat === "emergency" ? "text-warn" : a?.is_intruder ? "text-bad" : "text-fg"}`}>{selected}</div>
        </div>
        <div className="flex items-center gap-1.5">
          {a?.is_intruder && a.threat !== "emergency" && (
            <button
              onClick={() => send({ type: "remove_disruption", id: selected })}
              className="px-2 py-1 rounded-md border border-bad/40 bg-bad/10 text-[11px] font-medium text-bad hover:bg-bad/20"
            >
              Remove
            </button>
          )}
          <button
            onClick={() => dispatch({ type: "set_follow", on: !follow })}
            className={`px-2 py-1 rounded-md border text-[11px] font-medium ${follow ? "bg-accent/20 text-accent border-accent/50" : "bg-panel-2/70 text-muted border-line hover:text-fg"}`}
          >
            {follow ? "Following" : "Follow"}
          </button>
          <button
            onClick={() => dispatch({ type: "select", callsign: null })}
            className="px-2 py-1 rounded-md border border-line bg-panel-2/70 text-[11px] text-muted hover:text-fg"
            aria-label="Close flight strip"
          >
            Close
          </button>
        </div>
      </div>

      {a ? (
        <div className="grid grid-cols-4 gap-x-3 gap-y-2">
          <Field label="Type" value={a.actype} />
          <Field label="Level" value={`FL${String(Math.round(a.alt_ft / 100)).padStart(3, "0")}`} />
          <Field label="Speed" value={`${Math.round(a.gs_kt)} kt`} />
          <Field label="Heading" value={`${String(Math.round(a.hdg_deg)).padStart(3, "0")}`} />
          <div className="col-span-2">
            <Field label="Cleared level" value={`FL${String(Math.round(a.target_alt_ft / 100)).padStart(3, "0")}  ${trend}`} tone={trend === "level" ? "text-fg" : "text-warn"} />
          </div>
          <div className="col-span-2">
            <Field label="Route ahead" value={a.route.length ? a.route.join(" ") : "direct"} />
          </div>
        </div>
      ) : (
        <p className="text-xs text-muted">{selected} has left the sector.</p>
      )}

      {risk && (
        <div className="text-[11px] text-bad border border-bad/40 bg-bad/10 rounded-md px-2 py-1">
          Predicted conflict with <span className="font-mono font-semibold">{risk.other}</span>: {Math.round(risk.pair.p_max * 100)}% in {Math.round(risk.pair.t_first_s ?? risk.pair.eta_s)} s
          <span className="text-bad/70"> · min sep {risk.pair.min_sep_nm_p5.toFixed(1)} NM</span>
        </div>
      )}
      {watching.includes(selected) && (
        <div className="text-[11px] text-cyan-300 border border-cyan-400/30 bg-cyan-400/10 rounded-md px-2 py-1">
          Tower is watching this aircraft on radar to confirm it complies.
        </div>
      )}
      {open.length > 0 && (
        <div className="text-[11px] text-accent border border-accent/30 bg-accent/10 rounded-md px-2 py-1">
          Waiting for a readback.
        </div>
      )}

      {path && path.changes.length > 0 && (
        <div>
          <div className="eyebrow mb-1">Tower&apos;s plan for this flight</div>
          <ul className="text-[11px] text-fg/85 flex flex-col gap-0.5">
            {path.changes.slice(0, 3).map((c, i) => (
              <li key={i} className="leading-snug">{c}</li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <div className="eyebrow mb-1">Instructions</div>
        {history.length === 0 ? (
          <p className="text-[11px] text-muted">Nothing issued yet.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {history.map((c, i) => (
              <li key={c.id} className="text-[11px] leading-snug flex gap-2">
                <span className={`font-mono uppercase shrink-0 w-[62px] ${STATUS_CLS[c.status] ?? "text-muted"}`}>{c.status}</span>
                <span className="min-w-0">
                  <span className="text-fg/85">{c.phrase}</span>
                  {i === 0 && <Confidence value={c.confidence} riskAfter={c.risk_after} className="block mt-0.5" />}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
