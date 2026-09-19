"use client";

import { useState } from "react";
import { callsignForClearance, useTowerDispatch, useTowerState, type ActiveAlert } from "@/lib/store";
import type { Item } from "@/lib/types";
import { HTTP_URL } from "@/lib/ws";

function fmtItem(i: Item): string {
  const unit = i.unit ? ` ${i.unit}` : "";
  const act = i.action ? `${i.action.replace("_", " ")} ` : "";
  return `${act}${i.type} ${i.value}${unit}`;
}

function ItemList({ items, tone }: { items: Item[]; tone: "expected" | "heard" }) {
  if (items.length === 0) return <span className="text-muted italic">nothing</span>;
  return (
    <ul className="space-y-0.5">
      {items.map((i, k) => (
        <li key={k} className={`font-mono ${tone === "expected" ? "text-fg" : "text-bad"}`}>
          {fmtItem(i)}
        </li>
      ))}
    </ul>
  );
}

function AgentTrace({ clearanceId, done }: { clearanceId: string; done: boolean }) {
  const { steps } = useTowerState();
  const list = steps[clearanceId] ?? [];
  const [open, setOpen] = useState(true);
  const pending = !done;
  return (
    <div className="mt-2 border-t border-line/60 pt-2">
      <button onClick={() => setOpen(!open)} className="flex items-center gap-2 text-xs text-muted hover:text-fg">
        <span>{open ? "▾" : "▸"}</span>
        <span>Agent trace</span>
        <span className="font-mono">({list.length} step{list.length === 1 ? "" : "s"})</span>
        {pending && <span className="spinner" />}
      </button>
      {open && (
        <ol className="mt-1.5 space-y-1.5">
          {list.map((s) => (
            <li key={s.step} className="text-xs grid grid-cols-[1.25rem_1fr] gap-1">
              <span className="font-mono text-muted">{s.step}.</span>
              <div>
                <span className="font-mono text-warn">{s.tool}</span>
                <span className="text-muted">({Object.entries(s.args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")})</span>
                <div className="text-fg/90">{s.result_summary}</div>
              </div>
            </li>
          ))}
          {pending && list.length === 0 && <li className="text-xs text-muted">Waiting for the resolver…</li>}
        </ol>
      )}
    </div>
  );
}

function OneAlert({ a }: { a: ActiveAlert }) {
  const state = useTowerState();
  const dispatch = useTowerDispatch();
  const severe = a.result === "mismatch" || a.result === "missing";
  const callsign = a.callsign ?? callsignForClearance(state, a.clearance_id) ?? "";
  const hasSteps = (state.steps[a.clearance_id] ?? []).length > 0 || a.decided_by === "resolver";
  const resolving = state.resolving.includes(a.clearance_id);
  const title = severe ? (a.result === "missing" ? "NO READBACK" : "WRONG READBACK") : a.result === "partial" ? "PARTIAL READBACK" : "CHECKING";

  return (
    <div className={`rounded-lg border-2 p-3 ${severe ? "border-bad bg-bad/10 alert-pulse" : "border-warn bg-warn/10"}`}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className={`text-lg font-bold tracking-wide ${severe ? "text-bad" : "text-warn"}`}>{title}</div>
          <div className="font-mono text-sm">{callsign}</div>
        </div>
        <div className="text-right">
          <div className="text-[10px] uppercase text-muted">{a.error_type?.replace("_", " ") ?? a.result}</div>
          <div className="font-mono text-xs text-muted">
            conf {(a.confidence * 100).toFixed(0)}% · {a.decided_by.replace("_", " ")}
          </div>
        </div>
      </div>

      <div className="mt-2 grid grid-cols-2 gap-3 text-sm">
        <div>
          <div className="text-[10px] uppercase text-muted mb-0.5">Expected</div>
          <ItemList items={a.expected} tone="expected" />
        </div>
        <div>
          <div className="text-[10px] uppercase text-muted mb-0.5">Heard</div>
          <ItemList items={a.heard} tone="heard" />
        </div>
      </div>

      {a.reason && <p className="mt-2 text-xs text-fg/80">{a.reason}</p>}

      <div className="mt-2 flex items-center gap-2">
        {a.audio_ref && (
          <audio controls preload="none" className="h-7 max-w-[180px]" src={`${HTTP_URL}/audio/${a.audio_ref}`}>
            <track kind="captions" />
          </audio>
        )}
        <button onClick={() => dispatch({ type: "dismiss_alert", clearance_id: a.clearance_id })} className="ml-auto text-xs text-muted hover:text-fg px-2 py-1 rounded border border-line">
          Dismiss
        </button>
      </div>

      {a.correction_phrase && (
        <div className={`mt-2 rounded-md border px-2.5 py-2 ${severe ? "border-bad/50 bg-bad/10" : "border-warn/50 bg-warn/10"}`}>
          <div className="text-[10px] uppercase text-muted">Say now</div>
          <div className="text-[15px] leading-snug">&ldquo;{a.correction_phrase}&rdquo;</div>
        </div>
      )}

      {(hasSteps || resolving) && <AgentTrace clearanceId={a.clearance_id} done={!resolving} />}
    </div>
  );
}

function Checking({ clearanceId }: { clearanceId: string }) {
  const state = useTowerState();
  const callsign = callsignForClearance(state, clearanceId) ?? "";
  return (
    <div className="rounded-lg border-2 border-warn bg-warn/10 p-3">
      <div className="flex items-center gap-2">
        <span className="spinner" />
        <span className="text-lg font-bold tracking-wide text-warn">CHECKING</span>
        <span className="font-mono text-sm">{callsign}</span>
      </div>
      <p className="mt-1 text-xs text-fg/80">Readback unclear. The resolver is gathering evidence before deciding whether to interrupt you.</p>
      <AgentTrace clearanceId={clearanceId} done={false} />
    </div>
  );
}

export default function AlertCard() {
  const { alerts, resolving } = useTowerState();
  const [latest, ...rest] = alerts;
  const checking = resolving.filter((id) => !alerts.some((a) => a.clearance_id === id));
  if (!latest && checking.length === 0) return null;
  return (
    <section className="shrink-0 flex flex-col gap-2">
      {checking.map((id) => (
        <Checking key={id} clearanceId={id} />
      ))}
      {latest && <OneAlert a={latest} />}
      {rest.length > 0 && <div className="text-[10px] text-muted text-right">{rest.length} earlier alert{rest.length === 1 ? "" : "s"} below</div>}
      {rest.map((a) => (
        <OneAlert key={a.clearance_id} a={a} />
      ))}
    </section>
  );
}
