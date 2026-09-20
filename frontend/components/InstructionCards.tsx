"use client";

import { useEffect, useState } from "react";
import { useTowerState } from "@/lib/store";
import type { CardStatus, InstructionCard } from "@/lib/types";
import { CallsignLink, SHOW_CLS, useShowOnMap } from "./AlertCard";
import { useClient } from "./TowerApp";

const VISIBLE_CAP = 4;

const MONO = { fontFamily: "var(--font-mono)" } as const;

/** A thin bar and "0.91": how sure squack is of this instruction (TRD 07). Muted: it informs, it does not shout. */
export function Confidence({ value, riskAfter, className = "" }: { value: number | null | undefined; riskAfter?: number | null; className?: string }) {
  if (typeof value !== "number") return null;
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <span className={`inline-flex items-center gap-2 whitespace-nowrap text-[11px] text-muted tabular-nums ${className}`} style={MONO} title="(1 - residual risk) x how clearly this beat the runner-up">
      <span className="inline-block h-0.5 w-10 rounded-full bg-line overflow-hidden align-middle">
        <span className="block h-full bg-fg/60" style={{ width: `${pct}%` }} />
      </span>
      {value.toFixed(2)}
      {typeof riskAfter === "number" && <span className="text-muted/70">risk after {riskAfter.toFixed(2)}</span>}
    </span>
  );
}

const STATUS: Record<CardStatus, { label: string; dot: string }> = {
  pending: { label: "pending", dot: "" },
  spoken: { label: "spoken, awaiting readback", dot: "bg-fg" },
  validated: { label: "readback OK", dot: "dot-ok" },
  verified: { label: "radar confirms", dot: "dot-ok" },
  error: { label: "wrong readback", dot: "dot-bad" },
  superseded: { label: "replaced", dot: "" }, // the store drops these; never drawn
};

/** `arrivedT` and `simT` are both server clock (sim seconds), so the countdown is immune to client lag. */
function Card({ card, arrivedT, simT, auto, onFrequency, running, held, tag, top }: { card: InstructionCard; arrivedT: number; simT: number; auto: boolean; onFrequency: boolean; running: boolean; held?: string; tag: { text: string; cls: string }; top?: boolean }) {
  const { send } = useClient();
  const st = STATUS[card.status];
  const remaining = Math.max(0, card.urgency_s - Math.max(0, simT - arrivedT));
  const urgent = remaining < 20 && card.status === "pending";
  const done = card.status === "verified";
  // The time to act has passed and nobody has said it. It stays, because the aircraft still needs it.
  const overdue = remaining <= 0 && card.status === "pending";
  // Same as an alert card: click (or Enter) to go to the aircraft. Only one that is on the radar.
  const show = useShowOnMap(onFrequency ? card.callsign : "");
  // squack takes a few seconds to speak a card, and the card stays "pending" until it has. Show that
  // the press landed, or it gets pressed again. (The backend ignores a second press too.)
  const [saying, setSaying] = useState(false);
  useEffect(() => {
    if (!saying) return;
    const t = setTimeout(() => setSaying(false), 8000);
    return () => clearTimeout(t);
  }, [saying]);
  const border = card.status === "error" ? "border-bad/60" : top ? "border-fg/40" : "border-line";
  return (
    <div {...show} className={`rounded-lg border ${border} bg-panel-2 px-3 py-2.5 transition-colors ${done ? "opacity-60" : ""} ${show ? `${SHOW_CLS} hover:bg-panel` : ""}`}>
      {/* Tag line: why the card exists, who it is for, and where it stands. */}
      <div className="flex items-center gap-2 min-w-0 text-[11px] text-muted">
        <span className={`dot ${st.dot}`} />
        <span className={tag.cls}>{tag.text}</span>
        <span className="text-muted/50">·</span>
        <CallsignLink callsign={card.callsign} live={!!show} />
        {card.status !== "pending" && <span className="truncate">· {st.label}</span>}
      </div>
      <p className="mt-1.5 text-[15px] leading-snug text-fg">&ldquo;{card.phrase}&rdquo;</p>
      <p className="mt-1 text-xs text-muted">{card.reason}</p>

      {card.status === "pending" && card.heard_instead && (
        <div className="mt-2 rounded-md bg-panel px-2.5 py-2 border border-line">
          {held ? (
            <>
              <p className="text-[11px] text-warn font-medium">squack heard something else. Nothing went to the pilot.</p>
              <p className="mt-0.5 text-xs text-fg/90">&ldquo;{card.heard_instead}&rdquo;</p>
              <div className="mt-1.5 flex items-center gap-2 text-[11px] text-muted">
                <span>Hold Space and say the card again, or</span>
                <button onClick={() => send({ type: "confirm_heard", clearance_id: held })} className="text-warn hover:underline underline-offset-4">
                  Send as heard
                </button>
              </div>
            </>
          ) : (
            <>
              {/* You are the authority: what you said is what the aircraft is doing. The card was advice. */}
              <p className="text-[11px] text-warn font-medium">You said something else, and the aircraft is doing it.</p>
              <p className="mt-0.5 text-xs text-fg/90">&ldquo;{card.heard_instead}&rdquo;</p>
              <p className="mt-1 text-[11px] text-muted">squack is planning round it. This card updates in a moment.</p>
            </>
          )}
        </div>
      )}

      {/* Bottom row: confidence, urgency, action. */}
      <div className="mt-2.5 flex items-center gap-3 text-[11px] text-muted">
        <Confidence value={card.confidence} className="shrink-0" />
        <span className={`tabular-nums whitespace-nowrap ${overdue ? "text-bad font-medium" : urgent ? "text-fg/80" : ""}`} style={MONO} title="How long until the aircraft has to be doing this">
          {card.status === "pending" ? (overdue ? "overdue" : `${Math.ceil(remaining)} s`) : card.status === "spoken" ? "awaiting readback" : ""}
        </span>
        {card.status === "pending" && !auto && !onFrequency && <span className="ml-auto">Not on frequency yet</span>}
        {card.status === "pending" && !auto && onFrequency && (
          <span className="ml-auto flex items-center gap-2 whitespace-nowrap">
            <span className="text-fg/90">Hold Space to say it</span>
            <span className="text-muted/50">or</span>
            <button
              disabled={saying || !running || !onFrequency}
              title={!running ? "Press Start first. The radio only works while the simulation is running." : !onFrequency ? `${card.callsign} is not on frequency yet.` : "squack says it for you in its own voice"}
              onClick={() => {
                setSaying(true);
                send({ type: "speak_card", id: card.id });
              }}
              className="text-muted hover:text-fg underline decoration-dotted underline-offset-4 disabled:opacity-50 disabled:cursor-default disabled:no-underline"
            >
              {saying ? "Saying it…" : "let squack say it"}
            </button>
          </span>
        )}
        {card.status === "pending" && auto && (
          <span className="ml-auto text-warn">{onFrequency ? "Going out by data link" : "Waits until the flight checks in"}</span>
        )}
        {card.via && card.status !== "pending" && (
          <span className="ml-auto">{card.via === "datalink" ? "sent by data link · accepted" : card.via === "voice" ? "said by squack" : "said by you"}</span>
        )}
      </div>
      {card.status === "pending" && !auto && onFrequency && !running && (
        <p className="mt-1 text-[11px] text-muted">Press Start first: the radio is closed.</p>
      )}
    </div>
  );
}

/** One word for why a card exists, so nobody has to work it out from the reason text. */
function tagOf(card: InstructionCard, disruptionIds: Set<string>): { text: string; cls: string } {
  // A backend from before cards carried `cause` still says it in the reason: "...crossing with STORM1, then...".
  const named = card.cause ?? (/(?:with|clear of|behind) ([A-Z][A-Z0-9]{2,})/.exec(card.reason)?.[1] ?? null);
  card = named && !card.cause ? { ...card, cause: named } : card;
  if (card.emergency || card.reason.startsWith("Immediate")) return { text: "Emergency", cls: "text-bad font-medium" };
  if (card.origin === "release") return { text: "All clear", cls: "text-ok" };
  if (card.origin === "followup") return { text: "Back on course", cls: "text-fg/80" };
  if (card.cause && disruptionIds.has(card.cause)) return { text: `Reroute · ${card.cause}`, cls: "text-warn" };
  if (card.cause) return { text: `Conflict · ${card.cause}`, cls: "text-fg/80" };
  if (card.origin === "initial") return { text: "Initial plan", cls: "text-muted" };
  return { text: "Shortcut", cls: "text-muted" };
}

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/** A line in the log of what squack has already sent (voice off), or what has been dealt with (voice on). */
function Sent({ card, tag }: { card: InstructionCard; tag: { text: string; cls: string } }) {
  const how = card.via === "datalink" ? "data link" : card.via === "voice" ? "squack's voice" : card.via === "human" ? "you" : "";
  return (
    <div className="rounded-md border border-line px-3 py-2 opacity-70">
      <div className="flex items-center gap-2 text-[11px] text-muted">
        <span className={`dot ${card.status === "error" ? "dot-bad" : card.status === "verified" ? "dot-ok" : "bg-fg"}`} />
        <span className="text-fg font-medium">{card.callsign}</span>
        <span className={tag.cls}>{tag.text}</span>
        <span className="ml-auto">
          {card.status === "error" ? "wrong readback" : card.status === "verified" ? "radar confirms" : card.status === "spoken" ? "awaiting readback" : "accepted"}
          {how ? ` · ${how}` : ""}
        </span>
      </div>
      <p className="mt-0.5 text-[12px] leading-snug text-fg/85">&ldquo;{card.phrase}&rdquo;</p>
    </div>
  );
}

export default function InstructionCards() {
  const { cards, cardT, sim, aircraft, held, disruptions } = useTowerState();
  const [showLater, setShowLater] = useState(false);
  const auto = sim?.auto_speak ?? false; // voice off: squack sends everything itself
  const simT = sim?.t ?? 0;
  const ids = new Set(Object.keys(disruptions));
  const newestFirst = (a: InstructionCard, b: InstructionCard) => (cardT[b.id] ?? 0) - (cardT[a.id] ?? 0);

  // Three piles. "now": needs a human (voice on) or is about to leave (voice off).
  // "later": for flights still to enter the sector. "done": already issued.
  const open = cards.filter((c) => c.status === "pending" || c.status === "error");
  // Wrong readbacks first, then anything that reacts to something (a reroute, a conflict, back on
  // course), then the opening shortcuts, which can wait. Within a group, the soonest first.
  const weight = (c: InstructionCard) => (c.status === "error" ? 0 : c.emergency ? 1 : c.cause || c.origin === "followup" || c.origin === "release" ? 2 : 3);
  const now = open.filter((c) => c.status === "error" || c.callsign in aircraft).sort((a, b) => weight(a) - weight(b) || a.urgency_s - b.urgency_s);
  const later = open.filter((c) => c.status !== "error" && !(c.callsign in aircraft)).sort((a, b) => a.urgency_s - b.urgency_s);
  const done = cards.filter((c) => c.status !== "pending" && c.status !== "error").sort(newestFirst);

  return (
    <section className="panel p-3 shrink-0 select-none">
      <div className="flex items-baseline justify-between mb-1">
        <h2 className="text-[13px] font-semibold text-fg">{auto ? "Sent by squack" : "Say these"}</h2>
        <span className="text-[11px] text-muted tabular-nums">
          {auto ? `${done.length} sent` : `${now.length} to say · ${done.length} done`}
        </span>
      </div>
      <p className="mb-2.5 text-[11px] leading-snug text-muted">
        {auto
          ? "Voice is off: squack sends each instruction by data link the instant the plan changes, and the aircraft turns. Nothing here needs you."
          : "Voice is on: hold Space and say the top card. The pilot reads it back, squack checks it, and only then does the aircraft turn."}
      </p>

      {/* Voice on: the to-do list, most urgent first. */}
      {!auto && (
        <div className="flex flex-col gap-2">
          {now.length === 0 && <p className="text-xs text-muted text-center py-2">Nothing to say. squack is quiet.</p>}
          {now.slice(0, VISIBLE_CAP).map((c, i) => (
            <Card key={c.id} card={c} arrivedT={cardT[c.id] ?? simT} simT={simT} auto={auto} onFrequency running={sim?.lifecycle === "running"} held={held[c.id]} tag={tagOf(c, ids)} top={i === 0} />
          ))}
          {now.length > VISIBLE_CAP && <p className="text-[11px] text-muted text-right">{now.length - VISIBLE_CAP} more after these</p>}
        </div>
      )}

      {/* Voice off: anything still open is only waiting for its moment; say so in a line. */}
      {auto && now.length > 0 && (
        <p className="mb-1.5 text-[11px] text-warn">{now.length} going out now</p>
      )}

      {/* Flights that have not entered the sector yet: one line, not a wall of cards. */}
      {later.length > 0 && (
        <button
          onClick={() => setShowLater((v) => !v)}
          className="mt-2 w-full flex items-center justify-between px-1 py-1.5 text-left text-[11px] text-muted hover:text-fg"
        >
          <span>
            <span className="text-fg/80 font-medium">{later.length}</span> ready for flights still to enter
            <span className="tabular-nums"> · next in {clock(Math.max(0, (cardT[later[0].id] ?? simT) + later[0].urgency_s - simT))}</span>
          </span>
          <span className="underline decoration-dotted underline-offset-4">{showLater ? "hide" : "show"}</span>
        </button>
      )}
      {showLater && (
        <div className="mt-1 flex flex-col gap-1.5">
          {later.slice(0, 8).map((c) => {
            const left = Math.max(0, (cardT[c.id] ?? simT) + c.urgency_s - simT);
            const tag = tagOf(c, ids);
            return (
              <div key={c.id} className="rounded-md border border-line px-3 py-2 opacity-70">
                <div className="flex items-center gap-2 text-[11px] text-muted">
                  <span className="text-fg font-medium">{c.callsign}</span>
                  <span className={tag.cls}>{tag.text}</span>
                  <span className="ml-auto tabular-nums" style={MONO}>enters in {clock(left)}</span>
                </div>
                <p className="mt-0.5 text-[12px] leading-snug text-fg/80">&ldquo;{c.phrase}&rdquo;</p>
              </div>
            );
          })}
        </div>
      )}

      {/* What has already gone out. In voice off this IS the panel: a log, newest first. */}
      {done.length > 0 && (
        <div className="mt-3 flex flex-col gap-1.5">
          {!auto && <div className="text-[11px] font-medium text-muted">Done</div>}
          {done.slice(0, auto ? 6 : 3).map((c) => (
            <Sent key={c.id} card={c} tag={tagOf(c, ids)} />
          ))}
          {done.length > (auto ? 6 : 3) && <p className="text-[11px] text-muted text-right">{done.length - (auto ? 6 : 3)} earlier</p>}
        </div>
      )}
      {cards.length === 0 && <p className="text-xs text-muted text-center py-3">Nothing to say. squack is quiet.</p>}
    </section>
  );
}
