"use client";

import { useEffect } from "react";
import { useTowerDispatch, useTowerState } from "@/lib/store";

const LIFE_MS = 6000;

const CLS = {
  info: "border-accent/40 bg-accent/10 text-fg",
  warn: "border-warn/50 bg-warn/15 text-fg",
  error: "border-bad/50 bg-bad/15 text-fg",
} as const;

/** Short messages from the backend: why an action was refused, or what failed. */
export default function Notices() {
  const { notices } = useTowerState();
  const dispatch = useTowerDispatch();

  useEffect(() => {
    if (notices.length === 0) return;
    const oldest = notices[0];
    const wait = Math.max(0, LIFE_MS - (Date.now() - oldest.at));
    const h = setTimeout(() => dispatch({ type: "dismiss_notice", id: oldest.id }), wait);
    return () => clearTimeout(h);
  }, [notices, dispatch]);

  if (notices.length === 0) return null;
  return (
    <div className="absolute top-16 left-1/2 -translate-x-1/2 z-50 flex flex-col gap-2 w-[min(90vw,32rem)]" role="status" aria-live="polite">
      {notices.map((n) => (
        <button
          key={n.id}
          onClick={() => dispatch({ type: "dismiss_notice", id: n.id })}
          className={`text-left text-sm rounded-md border px-3 py-2 shadow-lg ${CLS[n.level] ?? CLS.info}`}
        >
          {n.text}
        </button>
      ))}
    </div>
  );
}
