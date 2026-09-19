"use client";

import { useTowerState } from "@/lib/store";
import type { CardStatus, InstructionCard } from "@/lib/types";
import { CallsignLink, SHOW_CLS, useShowOnMap } from "./AlertCard";
import { useClient } from "./TowerApp";

const VISIBLE_CAP = 4;

const STATUS: Record<CardStatus, { label: string; cls: string; bar: string }> = {
  pending: { label: "pending", cls: "border-line", bar: "bg-muted" },
  spoken: { label: "spoken, awaiting readback", cls: "border-accent/50", bar: "bg-accent" },
  validated: { label: "readback OK", cls: "border-ok/50", bar: "bg-ok" },
  verified: { label: "radar confirms", cls: "border-ok", bar: "bg-ok" },
  error: { label: "wrong readback", cls: "border-bad", bar: "bg-bad" },
  superseded: { label: "replaced", cls: "border-line", bar: "bg-muted" }, // the store drops these; never drawn
};

/** `arrivedT` and `simT` are both server clock (sim seconds), so the countdown is immune to client lag. */
function Card({ card, arrivedT, simT, auto, onFrequency }: { card: InstructionCard; arrivedT: number; simT: number; auto: boolean; onFrequency: boolean }) {
  const { send } = useClient();
  const st = STATUS[card.status];
  const remaining = Math.max(0, card.urgency_s - Math.max(0, simT - arrivedT));
  const frac = card.urgency_s > 0 ? remaining / card.urgency_s : 0;
  const urgent = remaining < 20 && card.status === "pending";
  const done = card.status === "verified";
  // The time to act has passed and nobody has said it. It stays, because the aircraft still needs it.
  const overdue = remaining <= 0 && card.status === "pending";
  // Same as an alert card: click (or Enter) to go to the aircraft. Only one that is on the radar.
  const show = useShowOnMap(onFrequency ? card.callsign : "");
  return (
    <div {...show} className={`rounded-lg border ${st.cls} bg-panel-2 p-2.5 transition-colors ${done ? "opacity-60" : ""} ${show ? `${SHOW_CLS} hover:bg-panel-2/70` : ""}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`inline-block w-2 h-2 rounded-full ${st.bar}`} />
          <CallsignLink callsign={card.callsign} live={!!show} />
          <span className="text-[10px] text-muted truncate">{st.label}</span>
        </div>
        <span className={`font-mono text-xs tabular-nums ${urgent ? "text-bad" : "text-muted"}`}>
          {overdue ? "overdue" : card.status === "pending" || card.status === "spoken" ? `${Math.ceil(remaining)}s` : ""}
        </span>
      </div>
      <p className="mt-1.5 text-[15px] leading-snug">&ldquo;{card.phrase}&rdquo;</p>
      <p className="mt-1 text-xs text-muted">{card.reason}</p>
      {(card.status === "pending" || card.status === "spoken") && (
        <div className="mt-2 h-0.5 rounded bg-line overflow-hidden">
          <div className={`h-full ${urgent ? "bg-bad" : "bg-accent"}`} style={{ width: `${frac * 100}%`, transition: "width 1s linear" }} />
        </div>
      )}
      {card.status === "pending" && !auto && (
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
      {card.status === "pending" && auto && (
        <p className="mt-2 text-[10px] text-warn">
          {onFrequency ? "Tower has it queued. Hold Space to say it yourself." : "Waits until the flight checks in."}
        </p>
      )}
      {card.via && card.status !== "pending" && (
        <p className="mt-1.5 text-[10px] uppercase tracking-wider text-muted">
          {card.via === "datalink" ? "sent by data link · accepted" : card.via === "voice" ? "said by Tower" : "said by you"}
        </p>
      )}
    </div>
  );
}

export default function InstructionCards() {
  const { cards, cardT, sim, aircraft } = useTowerState();
  const auto = sim?.auto_speak ?? false;
  const simT = sim?.t ?? 0;

  // Active first (pending/spoken/error), then validated, verified last; within a group, most urgent first.
  const rank: Record<CardStatus, number> = { error: 0, pending: 1, spoken: 2, validated: 3, verified: 4, superseded: 5 };
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
            <Card key={c.id} card={c} arrivedT={cardT[c.id] ?? simT} simT={simT} auto={auto} onFrequency={c.callsign in aircraft} />
          ))}
        </div>
      )}
    </section>
  );
}
