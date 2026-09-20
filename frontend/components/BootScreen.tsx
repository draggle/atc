"use client";

import { useEffect, useState } from "react";
import { useTowerState } from "@/lib/store";

/**
 * The first thing on screen: "squack." rises in with a radar ping behind it.
 * Shows for at least one full ping, then keeps pulsing until the backend (or the mock) has
 * delivered its first state, then fades out and unmounts. Nothing underneath is blocked
 * once it is gone.
 */
const MIN_MS = 2600; // one full ping and the letters
const MAX_MS = 12000; // never hold a judge hostage: give up waiting and reveal the screen
const FADE_MS = 500;

export default function BootScreen() {
  const { sim, connection } = useTowerState();
  const [minDone, setMinDone] = useState(false);
  const [maxDone, setMaxDone] = useState(false);
  const [gone, setGone] = useState(false);
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    const a = setTimeout(() => setMinDone(true), MIN_MS);
    const b = setTimeout(() => setMaxDone(true), MAX_MS);
    return () => {
      clearTimeout(a);
      clearTimeout(b);
    };
  }, []);

  const loaded = sim !== null && connection !== "connecting";
  const ready = (minDone && loaded) || maxDone;

  useEffect(() => {
    if (!ready || leaving) return;
    setLeaving(true);
    const t = setTimeout(() => setGone(true), FADE_MS);
    return () => clearTimeout(t);
  }, [ready, leaving]);

  if (gone) return null;

  return (
    <div
      className={`boot ${leaving ? "boot-leave" : ""}`}
      role="status"
      aria-live="polite"
      aria-label="squack is loading"
    >
      <div className="boot-stage">
        <div className="boot-radar" aria-hidden="true">
          <i /><i /><i />
        </div>
        <div className="boot-word" aria-hidden="true">
          {"squack".split("").map((ch, i) => (
            <span key={i} style={{ animationDelay: `${0.1 + i * 0.06}s` }}>{ch}</span>
          ))}
          <span className="boot-dot">.</span>
        </div>
      </div>
    </div>
  );
}
