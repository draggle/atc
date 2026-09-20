"use client";

import { alertFor, riskFor, useTowerDispatch, useTowerState, type ActiveAlert } from "@/lib/store";
import { AlertEvidence, alertLook } from "./AlertCard";
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
  spoken: "text-fg/80",
  validated: "text-ok",
  verified: "text-ok",
  error: "text-bad",
};

const MONO = { fontFamily: "var(--font-mono)" } as const;

/** A quiet text link, the same as on the alert card. */
const LINK = "text-[11px] text-muted hover:text-fg underline decoration-dotted underline-offset-4";
/** A small outline chip: hairline, sentence case, no fill. */
const CHIP = "chip hover:text-fg hover:border-fg/40 disabled:opacity-40 disabled:hover:text-muted disabled:hover:border-line";

function Field({ label, value, tone, wide }: { label: string; value: string; tone?: string; wide?: boolean }) {
  return (
    <div className={`min-w-0 flex items-baseline justify-between gap-3 py-1 border-b border-line/60 ${wide ? "col-span-2" : ""}`}>
      <span className="text-[11px] text-muted shrink-0">{label}</span>
      <span className={`text-[13px] tabular-nums truncate ${tone ?? "text-fg"}`} style={MONO}>{value}</span>
    </div>
  );
}

/** What is wrong with this aircraft, in the alert card's own words and tones. The card owns the actions. */
function Issue({ alert }: { alert: ActiveAlert }) {
  const { radar, title, frame, titleCls } = alertLook(alert);
  return (
    <div className={`rounded-md border border-line border-l-2 ${frame} bg-panel-2 px-3 py-2`}>
      <div className={`text-sm font-semibold ${titleCls}`}>{title}</div>
      {radar && <div className="text-[11px] text-muted">Read back right, flying wrong</div>}
      {/* The same shaped evidence the alert shows: an omission never reads as a wrong value. */}
      <div className="mt-2">
        <AlertEvidence a={alert} dense />
      </div>
      {alert.reason && <p className="mt-1.5 text-[11px] leading-snug text-muted">{alert.reason}</p>}
    </div>
  );
}

/** Everything squack knows about one aircraft: where it is, what it was told, and what it did. */
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
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[11px] text-muted">{a?.is_intruder ? (THREAT[a.threat ?? ""] ?? "Uncooperative traffic") : "Flight"}</div>
          <div className={`text-xl font-semibold tracking-tight ${a?.threat === "emergency" ? "text-warn" : a?.is_intruder ? "text-bad" : "text-fg"}`}>{selected}</div>
        </div>
        <div className="flex items-center gap-3 pt-1">
          {a?.is_intruder && a.threat !== "emergency" && (
            <button onClick={() => send({ type: "remove_disruption", id: selected })} className="text-[11px] text-bad hover:underline underline-offset-4">
              Remove
            </button>
          )}
          <button
            onClick={() => dispatch({ type: "set_follow", on: !follow })}
            className={`text-[11px] underline decoration-dotted underline-offset-4 ${follow ? "text-ok" : "text-muted hover:text-fg"}`}
          >
            {follow ? "Following" : "Follow"}
          </button>
          <button onClick={() => dispatch({ type: "select", callsign: null })} className={LINK} aria-label="Close flight strip">
            Close
          </button>
        </div>
      </div>

      {a ? (
        <div className="grid grid-cols-2 gap-x-4">
          <Field label="Type" value={a.actype} />
          <Field label="Level" value={`FL${String(Math.round(a.alt_ft / 100)).padStart(3, "0")}`} />
          <Field label="Speed" value={`${Math.round(a.gs_kt)} kt`} />
          <Field label="Heading" value={`${String(Math.round(a.hdg_deg)).padStart(3, "0")}`} />
          <Field label="Cleared level" value={`FL${String(Math.round(a.target_alt_ft / 100)).padStart(3, "0")} ${trend}`} tone={trend === "level" ? "text-fg" : "text-warn"} wide />
          <Field label="Route ahead" value={a.route.length ? a.route.join(" ") : "direct"} wide />
        </div>
      ) : (
        <p className="text-xs text-muted">{selected} has left the sector.</p>
      )}

      {risk && (
        <p className="text-[11px] text-bad leading-snug">
          Predicted conflict with <span className="font-medium" style={MONO}>{risk.other}</span>: {Math.round(risk.pair.p_max * 100)}% in {Math.round(risk.pair.t_first_s ?? risk.pair.eta_s)} s
          <span className="text-bad/70"> · min sep {risk.pair.min_sep_nm_p5.toFixed(1)} NM</span>
        </p>
      )}

      {a && !a.is_intruder && (
        // Disrupt this flight: squack puts it on this aircraft's own path, far enough ahead to be
        // avoided and near enough to matter. No aiming at a tilted map.
        <div>
          <div className="text-[11px] text-muted mb-1.5">Disrupt this flight</div>
          <div className="flex flex-wrap gap-1.5">
            {([["storm", "Storm ahead"], ["rocket", "Launch ahead"], ["fighter", "Fighter"], ["emergency", "Mayday"]] as const).map(([kind, label]) => (
              <button
                key={kind}
                disabled={state.sim?.lifecycle !== "running"}
                onClick={() => send({ type: "add_disruption", kind, target: selected })}
                title={kind === "emergency" ? "This flight declares an emergency" : kind === "fighter" ? "An intruder timed to meet this flight" : "On this flight's path, a few minutes ahead"}
                className={CHIP}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      )}

      {watching.includes(selected) && (
        <p className="text-[11px] text-warn">squack is watching this aircraft on radar to confirm it complies.</p>
      )}
      {open.length > 0 && (
        <p className="text-[11px] text-muted">Waiting for a readback.</p>
      )}

      {path && path.changes.length > 0 && (
        <div>
          <div className="text-[11px] text-muted mb-1">squack&apos;s plan for this flight</div>
          <ul className="text-[11px] text-fg/85 flex flex-col gap-0.5">
            {path.changes.slice(0, 3).map((c, i) => (
              <li key={i} className="leading-snug">{c}</li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <div className="text-[11px] text-muted mb-1">Instructions</div>
        {history.length === 0 ? (
          <p className="text-[11px] text-muted">Nothing issued yet.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {history.map((c, i) => (
              <li key={c.id} className="text-[11px] leading-snug flex gap-2">
                <span className={`shrink-0 w-[62px] ${STATUS_CLS[c.status] ?? "text-muted"}`}>{c.status}</span>
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
