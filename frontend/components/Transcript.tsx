"use client";

import { useEffect, useRef } from "react";
import { useTowerDispatch, useTowerState } from "@/lib/store";
import type { Transmission } from "@/lib/types";

const CALLSIGN_RE = /\b([A-Z]{2,3}\d{1,4}[A-Z]?)\b/;

const MONO = { fontFamily: "var(--font-mono)" } as const;

/** Parser's callsign when the backend gave one; regex over the text only as a fallback. */
function callsignOf(t: Transmission): string {
  if (t.callsign) return t.callsign;
  const m = t.text_norm.toUpperCase().match(CALLSIGN_RE);
  return m ? m[1] : "";
}

function Row({ t, showStock }: { t: Transmission; showStock: boolean }) {
  const conf = Math.max(0, Math.min(1, t.asr_confidence));
  const confCls = conf > 0.85 ? "bg-ok" : conf > 0.65 ? "bg-warn" : "bg-bad";
  const callsign = callsignOf(t);
  return (
    <div className="grid grid-cols-[4.5rem_5rem_1fr_3.5rem] gap-3 items-baseline py-1.5 border-b border-line/60 group" title={t.text_stock ? `stock: ${t.text_stock}` : undefined}>
      <span className="text-[11px] text-muted truncate">{t.speaker === "datalink" ? "data link" : t.speaker}</span>
      <span className="text-[13px] font-medium text-fg truncate">{callsign}</span>
      <div className="min-w-0">
        <span className="text-sm text-fg/90">{showStock && t.text_stock ? t.text_stock : t.text_norm}</span>
        {!showStock && t.text_stock && (
          <span className="hidden group-hover:inline ml-2 text-xs text-muted">stock: &ldquo;{t.text_stock}&rdquo;</span>
        )}
      </div>
      <div className="flex items-center gap-2">
        <div className="h-0.5 flex-1 rounded-full bg-line overflow-hidden">
          <div className={`h-full ${confCls}`} style={{ width: `${conf * 100}%` }} />
        </div>
        <span className="text-[11px] text-muted w-5 text-right tabular-nums" style={MONO}>{Math.round(conf * 100)}</span>
      </div>
    </div>
  );
}

export default function Transcript() {
  const { transcript, showStock, onAir } = useTowerState();
  const dispatch = useTowerDispatch();
  const endRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [transcript.length]);

  return (
    <section className="panel h-full flex flex-col p-3">
      <div className="flex items-baseline justify-between mb-1.5">
        <h2 className="text-[13px] font-semibold text-fg flex items-baseline gap-2">
          Frequency
          <span className="text-[11px] font-normal text-muted tabular-nums">{transcript.length}</span>
        </h2>
        <div className="flex items-center gap-3 text-[11px]">
          {onAir && (
            <span className="inline-flex items-center gap-1.5 text-ok">
              <span className="dot dot-ok" />
              {onAir.speaker === "pilot" ? `${onAir.callsign ?? "pilot"} transmitting`
                : onAir.speaker === "squack" ? "squack answering" : "squack transmitting"}
            </span>
          )}
          <button
            onClick={() => dispatch({ type: "toggle_stock" })}
            className={`underline decoration-dotted underline-offset-4 hover:text-fg ${showStock ? "text-warn" : "text-muted"}`}
            title="Show what stock Whisper heard instead of our fine-tuned model"
          >
            {showStock ? "Showing stock Whisper" : "Showing tuned Whisper"}
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
