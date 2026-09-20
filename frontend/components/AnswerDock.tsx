"use client";

/**
 * squack's latest answer, docked above the command bar: the one sentence, its cards,
 * and a steps card that fills in while the agent is still working on the same turn. Dismiss closes
 * it; the next answer replaces it.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useTowerDispatch, useTowerState } from "@/lib/store";
import Card from "@/lib/cards/Card";
import { StepsCard } from "@/lib/cards/registry";
import type { StepsCard as StepsCardT } from "@/lib/cards/types";
import { DOCK_BOTTOM, ROW_TOP, STREAM_CPS } from "@/lib/layout";

const reducedMotion = () =>
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/**
 * Reveal the answer at about the pace squack says it: one rAF loop with a time budget, slicing the
 * string (never animating per character). Reduced motion gets the whole sentence at once.
 */
function useStreamedText(text: string, key: string): { shown: string; done: boolean } {
  const [n, setN] = useState(0);
  const raf = useRef(0);
  useEffect(() => {
    if (!text) {
      setN(0);
      return;
    }
    if (reducedMotion()) {
      setN(text.length);
      return;
    }
    setN(0);
    let last = performance.now();
    let chars = 0;
    const frame = (t: number) => {
      chars += ((t - last) / 1000) * STREAM_CPS;
      last = t;
      const next = Math.min(text.length, Math.floor(chars));
      setN(next);
      if (next < text.length) raf.current = requestAnimationFrame(frame);
    };
    raf.current = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf.current);
  }, [text, key]);
  return { shown: text.slice(0, n), done: n >= text.length };
}

export default function AnswerDock() {
  const { answers, agentSteps, turn } = useTowerState();
  const dispatch = useTowerDispatch();

  const latest = useMemo(() => [...answers].reverse().find((a) => !a.dismissed) ?? null, [answers]);
  const stream = useStreamedText(latest?.text ?? "", latest?.turn_id ?? "");
  // A turn with steps streaming and no answer yet: show the trace on its own, so a slow tool call is
  // never a silent second.
  const working = turn && !turn.done && !answers.some((a) => a.turn_id === turn.id) ? turn : null;
  const shownTurn = working?.id ?? latest?.turn_id ?? null;
  const steps = shownTurn ? agentSteps[shownTurn] ?? [] : [];

  if (!latest && !working) return null;

  const stepsCard: StepsCardT | null = steps.length > 0 || working
    ? { kind: "steps", steps: steps.map((s) => ({ n: s.step, tool: s.tool, summary: s.result_summary, done: true })) }
    : null;
  const done = !working;

  return (
    <div
      className="pointer-events-none absolute left-1/2 z-30 -translate-x-1/2 w-[min(680px,calc(100vw-32px))] flex flex-col gap-1.5 overflow-y-auto scroll-thin"
      style={{ bottom: DOCK_BOTTOM, maxHeight: ROW_TOP - DOCK_BOTTOM }}
      data-testid="answer-dock"
    >
      {(latest || working) && (
        <div className="pointer-events-auto panel px-4 py-3 text-[13px] leading-snug text-fg/95 card-in">
          <div className="flex items-start gap-3">
            <span className="text-muted shrink-0 text-[11px] mt-0.5">squack</span>
            <div className="min-w-0 flex-1">
              {latest && !working ? <div>{stream.shown}</div> : <div className="text-muted">Working on it…</div>}
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

      {latest && !working && stream.done && latest.cards.map((c, i) => (
        <div key={`${latest.turn_id}-${i}`} className="pointer-events-auto card-in">
          <Card card={c} />
        </div>
      ))}
    </div>
  );
}
