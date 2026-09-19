"use client";

import { useEffect, useRef } from "react";
import { useTowerDispatch, useTowerState } from "@/lib/store";
import type { Transmission } from "@/lib/types";

const CALLSIGN_RE = /\b([A-Z]{2,3}\d{1,4}[A-Z]?)\b/;

function guessCallsign(t: Transmission): string {
  const m = t.text_norm.toUpperCase().match(CALLSIGN_RE);
  return m ? m[1] : "";
}

function Row({ t, showStock }: { t: Transmission; showStock: boolean }) {
  const conf = Math.max(0, Math.min(1, t.asr_confidence));
  const confCls = conf > 0.85 ? "bg-ok" : conf > 0.65 ? "bg-warn" : "bg-bad";
  const isCtl = t.speaker === "controller";
  const callsign = guessCallsign(t);
  return (
    <div className="grid grid-cols-[4.5rem_5rem_1fr_4rem] gap-2 items-baseline py-1 border-b border-line/40 group" title={t.text_stock ? `stock: ${t.text_stock}` : undefined}>
      <span className={`text-[10px] uppercase tracking-wider font-semibold ${isCtl ? "text-accent" : t.speaker === "pilot" ? "text-ok" : "text-muted"}`}>{t.speaker}</span>
      <span className="font-mono text-xs text-muted truncate">{callsign}</span>
      <div className="min-w-0">
        <span className="text-sm">{showStock && t.text_stock ? t.text_stock : t.text_norm}</span>
        {!showStock && t.text_stock && (
          <span className="hidden group-hover:inline ml-2 text-xs text-muted">stock: &ldquo;{t.text_stock}&rdquo;</span>
        )}
      </div>
      <div className="flex items-center gap-1.5">
        <div className="h-1.5 flex-1 rounded bg-line overflow-hidden">
          <div className={`h-full ${confCls}`} style={{ width: `${conf * 100}%` }} />
        </div>
        <span className="font-mono text-[10px] text-muted w-6 text-right">{Math.round(conf * 100)}</span>
      </div>
    </div>
  );
}

export default function Transcript() {
  const { transcript, showStock } = useTowerState();
  const dispatch = useTowerDispatch();
  const endRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [transcript.length]);

  return (
    <section className="panel h-full flex flex-col p-2.5">
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-xs uppercase tracking-wider text-muted">Frequency</h2>
        <div className="flex items-center gap-3 text-[10px]">
          <span className="text-muted">{transcript.length} transmissions</span>
          <button
            onClick={() => dispatch({ type: "toggle_stock" })}
            className={`px-2 py-0.5 rounded border ${showStock ? "border-warn/50 text-warn bg-warn/10" : "border-line text-muted hover:text-fg"}`}
            title="Show what stock Whisper heard instead of our fine-tuned model"
          >
            {showStock ? "Showing: stock Whisper" : "Showing: tuned Whisper"}
          </button>
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto scroll-thin">
        {transcript.length === 0 ? (
          <p className="text-xs text-muted py-4 text-center">Frequency is quiet.</p>
        ) : (
          transcript.map((t) => <Row key={t.id} t={t} showStock={showStock} />)
        )}
        <div ref={endRef} />
      </div>
    </section>
  );
}
