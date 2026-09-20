"use client";

/**
 * squack's latest answer, docked above the command bar in both modes: the one sentence, its cards,
 * and a steps card that fills in while the agent is still working on the same turn. Background
 * simulations show their progress here too. Dismiss closes it; the next answer replaces it.
 */
import { useMemo } from "react";
import { useTowerDispatch, useTowerState } from "@/lib/store";
import Card from "@/lib/cards/Card";
import { StepsCard } from "@/lib/cards/registry";
import type { SimJob, StepsCard as StepsCardT } from "@/lib/cards/types";

function JobLine({ job }: { job: SimJob }) {
  const pct = Math.round(Math.max(0, Math.min(1, job.progress)) * 100);
  const label = job.kind.replace(/^sim\./, "").replace("_", " ");
  return (
    <div className="panel px-3 py-2 text-[12px]">
      <div className="flex items-center gap-2">
        <span className="dot dot-warn animate-pulse" />
        <span className="text-fg/90">Running {label}</span>
        <span className="ml-auto text-muted tabular-nums">
          {pct}%{job.eta_s != null && job.eta_s > 0 ? ` · about ${Math.ceil(job.eta_s)} s` : ""}
        </span>
      </div>
      <div className="mt-1.5 h-1 rounded-sm bg-line/60 overflow-hidden">
        <div className="h-full bg-fg/85 transition-[width] duration-500" style={{ width: `${pct}%` }} />
      </div>
      {Object.keys(job.params ?? {}).length > 0 && (
        <div className="mt-1 text-[11px] text-muted" style={{ fontFamily: "var(--font-mono)" }}>
          {Object.entries(job.params).map(([k, v]) => `${k}=${String(v)}`).join(" · ")}
        </div>
      )}
    </div>
  );
}

export default function AnswerDock() {
  const { answers, agentSteps, turn, simJobs } = useTowerState();
  const dispatch = useTowerDispatch();

  const latest = useMemo(() => [...answers].reverse().find((a) => !a.dismissed) ?? null, [answers]);
  // A turn with steps streaming and no answer yet: show the trace on its own, so a slow tool call is
  // never a silent second.
  const working = turn && !turn.done && !answers.some((a) => a.turn_id === turn.id) ? turn : null;
  const shownTurn = working?.id ?? latest?.turn_id ?? null;
  const steps = shownTurn ? agentSteps[shownTurn] ?? [] : [];
  const running = Object.values(simJobs).filter((j) => j.status === "running");

  if (!latest && !working && running.length === 0) return null;

  const stepsCard: StepsCardT | null = steps.length > 0 || working
    ? { kind: "steps", steps: steps.map((s) => ({ n: s.step, tool: s.tool, summary: s.result_summary, done: true })) }
    : null;
  const done = !working;

  return (
    <div
      className="pointer-events-none absolute left-1/2 bottom-[108px] z-30 -translate-x-1/2 w-[min(680px,calc(100vw-32px))] max-h-[min(52vh,560px)] flex flex-col gap-1.5 overflow-y-auto scroll-thin"
      data-testid="answer-dock"
    >
      {running.map((j) => (
        <div key={j.job_id} className="pointer-events-auto"><JobLine job={j} /></div>
      ))}

      {(latest || working) && (
        <div className="pointer-events-auto panel px-4 py-3 text-[13px] leading-snug text-fg/95 card-in">
          <div className="flex items-start gap-3">
            <span className="text-muted shrink-0 text-[11px] mt-0.5">{latest?.for === "event" ? "squack noticed" : "squack"}</span>
            <div className="min-w-0 flex-1">
              {latest && !working ? <div>{latest.text}</div> : <div className="text-muted">Working on it…</div>}
              {stepsCard && (
                <div className="mt-2 border-t border-line pt-2">
                  <StepsCard card={stepsCard} open={!done} />
                </div>
              )}
            </div>
            {latest && (
              <button
                type="button"
                onClick={() => dispatch({ type: "dismiss_answer", turn_id: latest.turn_id })}
                aria-label="Dismiss"
                className="text-muted hover:text-fg text-[13px] leading-none"
              >
                ×
              </button>
            )}
          </div>
        </div>
      )}

      {latest && !working && latest.cards.map((c, i) => (
        <div key={`${latest.turn_id}-${i}`} className="pointer-events-auto card-in">
          <Card card={c} />
        </div>
      ))}
    </div>
  );
}
