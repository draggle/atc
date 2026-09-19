"use client";

import { useEffect, useState } from "react";
import { useTowerState } from "@/lib/store";
import type { CardStatus, InstructionCard } from "@/lib/types";
import { useClient } from "./TowerApp";

const VISIBLE_CAP = 4;

const STATUS: Record<CardStatus, { label: string; cls: string; bar: string }> = {
  pending: { label: "pending", cls: "border-line", bar: "bg-muted" },
  spoken: { label: "spoken, awaiting readback", cls: "border-accent/50", bar: "bg-accent" },
  validated: { label: "readback OK", cls: "border-ok/50", bar: "bg-ok" },
  verified: { label: "radar confirms", cls: "border-ok", bar: "bg-ok" },
  error: { label: "wrong readback", cls: "border-bad", bar: "bg-bad" },
};

function useNow(intervalMs: number) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const h = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(h);
  }, [intervalMs]);
  return now;
}

function Card({ card, receivedAt, now }: { card: InstructionCard; receivedAt: number; now: number }) {
  const { send } = useClient();
  const st = STATUS[card.status];
  const remaining = Math.max(0, card.urgency_s - (now - receivedAt) / 1000);
  const frac = card.urgency_s > 0 ? remaining / card.urgency_s : 0;
  const urgent = remaining < 20 && card.status === "pending";
  const done = card.status === "verified";
  return (
    <div className={`rounded-lg border ${st.cls} bg-panel-2 p-2.5 transition-colors ${done ? "opacity-60" : ""}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`inline-block w-2 h-2 rounded-full ${st.bar}`} />
          <span className="font-mono text-sm">{card.callsign}</span>
          <span className="text-[10px] text-muted truncate">{st.label}</span>
        </div>
        <span className={`font-mono text-xs tabular-nums ${urgent ? "text-bad" : "text-muted"}`}>
          {card.status === "pending" || card.status === "spoken" ? `${Math.ceil(remaining)}s` : ""}
        </span>
      </div>
      <p className="mt-1.5 text-[15px] leading-snug">&ldquo;{card.phrase}&rdquo;</p>
      <p className="mt-1 text-xs text-muted">{card.reason}</p>
      {(card.status === "pending" || card.status === "spoken") && (
        <div className="mt-2 h-0.5 rounded bg-line overflow-hidden">
          <div className={`h-full ${urgent ? "bg-bad" : "bg-accent"}`} style={{ width: `${frac * 100}%`, transition: "width 1s linear" }} />
        </div>
      )}
      {card.status === "pending" && (
        <div className="mt-2 flex items-center gap-2">
          <button
            onClick={() => send({ type: "speak_card", id: card.id })}
            className="px-2.5 py-1 rounded-md bg-accent/20 text-accent border border-accent/40 text-xs font-medium hover:bg-accent/30"
          >
            Say it
          </button>
          <span className="text-[10px] text-muted">or hold Space and read it on the radio</span>
        </div>
      )}
    </div>
  );
}

export default function InstructionCards() {
  const { cards } = useTowerState();
  const now = useNow(1000);
  const [seen] = useState(() => new Map<string, number>());
  for (const c of cards) if (!seen.has(c.id)) seen.set(c.id, Date.now());

  // Active first (pending/spoken/error), then validated, verified last; within a group, most urgent first.
  const rank: Record<CardStatus, number> = { error: 0, pending: 1, spoken: 2, validated: 3, verified: 4 };
  const sorted = [...cards].sort((a, b) => rank[a.status] - rank[b.status] || a.urgency_s - b.urgency_s);
  const visible = sorted.slice(0, VISIBLE_CAP);
  const hidden = sorted.length - visible.length;

  return (
    <section className="panel p-2.5 shrink-0">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-xs uppercase tracking-wider text-muted">Instructions</h2>
        <span className="text-[10px] text-muted font-mono">
          {cards.filter((c) => c.status === "pending").length} pending{hidden > 0 ? ` · ${hidden} more` : ""}
        </span>
      </div>
      {visible.length === 0 ? (
        <p className="text-xs text-muted py-3 text-center">Nothing to say. Tower is quiet.</p>
      ) : (
        <div className="flex flex-col gap-2">
          {visible.map((c) => (
            <Card key={c.id} card={c} receivedAt={seen.get(c.id) ?? now} now={now} />
          ))}
        </div>
      )}
    </section>
  );
}
