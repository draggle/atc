"use client";

import { useEffect } from "react";
import { useTowerDispatch, useTowerState } from "@/lib/store";

const LIFE_MS = 6000;

/**
 * Only failures reach the screen, and only in the dock above the command bar. Nothing floats in
 * the middle of the map: info and warn notices stay in the store (the stale-backend warning reads
 * them) but are never drawn.
 */
export default function Notices() {
  const { notices } = useTowerState();
  const dispatch = useTowerDispatch();
  const shown = notices.filter((n) => n.level === "error");

  useEffect(() => {
    if (notices.length === 0) return;
    const oldest = notices[0];
    const wait = Math.max(0, LIFE_MS - (Date.now() - oldest.at));
    const h = setTimeout(() => dispatch({ type: "dismiss_notice", id: oldest.id }), wait);
    return () => clearTimeout(h);
  }, [notices, dispatch]);

  if (shown.length === 0) return null;
  return (
    <div className="absolute left-1/2 bottom-[108px] -translate-x-1/2 z-40 flex flex-col gap-2 w-[min(680px,calc(100vw-32px))]" role="status" aria-live="polite">
      {shown.map((n) => (
        <button
          key={n.id}
          onClick={() => dispatch({ type: "dismiss_notice", id: n.id })}
          className="text-left text-sm text-fg bg-panel border border-line rounded-[var(--radius)] px-3 py-2 border-l-2 border-l-bad"
        >
          {n.text}
        </button>
      ))}
    </div>
  );
}
