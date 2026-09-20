"use client";

import { useEffect, useState } from "react";
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
function Card({ card, arrivedT, simT, auto, onFrequency, running, held, tag }: { card: InstructionCard; arrivedT: number; simT: number; auto: boolean; onFrequency: boolean; running: boolean; held?: string; tag: { text: string; cls: string } }) {
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
  // Tower takes a few seconds to speak a card, and the card stays "pending" until it has. Show that
  // the press landed, or it gets pressed again. (The backend ignores a second press too.)
  const [saying, setSaying] = useState(false);
  useEffect(() => {
    if (!saying) return;
    const t = setTimeout(() => setSaying(false), 8000);
    return () => clearTimeout(t);
  }, [saying]);
  return (
    <div {...show} className={`rounded-lg border ${st.cls} bg-panel-2 p-2.5 transition-colors ${done ? "opacity-60" : ""} ${show ? `${SHOW_CLS} hover:bg-panel-2/70` : ""}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`inline-block w-2 h-2 rounded-full ${st.bar}`} />
          <CallsignLink callsign={card.callsign} live={!!show} />
          <span className={`rounded border px-1.5 py-px text-[9px] font-semibold uppercase tracking-wider ${tag.cls}`}>{tag.text}</span>
          {card.status !== "pending" && <span className="text-[10px] text-muted truncate">{st.label}</span>}
        </div>
        <span className={`font-mono text-[11px] tabular-nums whitespace-nowrap ${urgent ? "text-bad" : "text-muted"}`} title="How long until the aircraft has to be doing this">
          {card.status === "pending" ? (overdue ? "overdue: say it now" : `takes effect in ${Math.ceil(remaining)} s`) : card.status === "spoken" ? "awaiting readback" : ""}
        </span>
      </div>
      <p className="mt-1.5 text-[15px] leading-snug">&ldquo;{card.phrase}&rdquo;</p>
      <p className="mt-1 text-xs text-muted">{card.reason}</p>
      {(card.status === "pending" || card.status === "spoken") && (
        <div className="mt-2 h-0.5 rounded bg-line overflow-hidden">
          <div className={`h-full ${urgent ? "bg-bad" : "bg-accent"}`} style={{ width: `${frac * 100}%`, transition: "width 1s linear" }} />
        </div>
      )}
      {card.status === "pending" && card.heard_instead && (
        <div className="mt-2 rounded-md border border-warn/50 bg-warn/10 px-2 py-1.5">
          <p className="text-[11px] text-warn font-medium">Tower heard something else. Nothing went to the pilot.</p>
          <p className="mt-0.5 text-xs text-fg/90">&ldquo;{card.heard_instead}&rdquo;</p>
          <div className="mt-1.5 flex items-center gap-2">
            <span className="text-[10px] text-muted">Hold Space and say the card again, or</span>
            {held && (
              <button
                onClick={() => send({ type: "confirm_heard", clearance_id: held })}
                className="px-2 py-0.5 rounded border border-warn/50 text-warn text-[11px] hover:bg-warn/15"
              >
                Send as heard
              </button>
            )}
          </div>
        </div>
      )}
      {card.status === "pending" && !auto && !onFrequency && (
        <p className="mt-2 text-[10px] text-muted">Not on frequency yet. Say it when the flight checks in.</p>
      )}
      {card.status === "pending" && !auto && onFrequency && (
        <div className="mt-2 flex items-center gap-2">
          <span className="text-[11px] text-ok font-medium">Hold Space and say it</span>
          <button
            disabled={saying || !running || !onFrequency}
            title={!running ? "Press Start first. The radio only works while the simulation is running." : !onFrequency ? `${card.callsign} is not on frequency yet.` : "Tower says it for you in its own voice"}
            onClick={() => {
              setSaying(true);
              send({ type: "speak_card", id: card.id });
            }}
            className="ml-auto px-2 py-0.5 rounded-md border border-line text-[11px] text-muted hover:text-fg disabled:opacity-60 disabled:cursor-default"
          >
            {saying ? "Saying it…" : "or let Tower say it"}
          </button>
          {!running && <span className="text-[10px] text-muted">press Start first: the radio is closed</span>}
        </div>
      )}
      {card.status === "pending" && auto && (
        <p className="mt-2 text-[10px] text-warn">
          {onFrequency ? "Going out by data link." : "Waits until the flight checks in."}
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

/** One word for why a card exists, so nobody has to work it out from the reason text. */
function tagOf(card: InstructionCard, disruptionIds: Set<string>): { text: string; cls: string } {
  // A backend from before cards carried `cause` still says it in the reason: "...crossing with STORM1, then...".
  const named = card.cause ?? (/(?:with|clear of|behind) ([A-Z][A-Z0-9]{2,})/.exec(card.reason)?.[1] ?? null);
  card = named && !card.cause ? { ...card, cause: named } : card;
  if (card.emergency || card.reason.startsWith("Immediate")) return { text: "Emergency", cls: "border-bad/60 text-bad bg-bad/10" };
  if (card.origin === "release") return { text: "All clear", cls: "border-ok/50 text-ok bg-ok/10" };
  if (card.origin === "followup") return { text: "Back on course", cls: "border-accent/40 text-accent bg-accent/10" };
  if (card.cause && disruptionIds.has(card.cause)) return { text: `Reroute · ${card.cause}`, cls: "border-warn/60 text-warn bg-warn/10" };
  if (card.cause) return { text: `Conflict · ${card.cause}`, cls: "border-accent/40 text-accent bg-accent/10" };
  if (card.origin === "initial") return { text: "Initial plan", cls: "border-line text-muted bg-panel-2" };
  return { text: "Shortcut", cls: "border-line text-muted bg-panel-2" };
}

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/** A line in the log of what Tower has already sent (voice off), or what has been dealt with (voice on). */
function Sent({ card, tag }: { card: InstructionCard; tag: { text: string; cls: string } }) {
  const how = card.via === "datalink" ? "data link" : card.via === "voice" ? "Tower's voice" : card.via === "human" ? "you" : "";
  return (
    <div className="rounded-md border border-line/70 bg-panel-2/60 px-2.5 py-1.5">
      <div className="flex items-center gap-2">
        <span className={`h-1.5 w-1.5 rounded-full ${card.status === "error" ? "bg-bad" : card.status === "verified" ? "bg-ok" : "bg-accent"}`} />
        <span className="font-mono text-xs font-semibold">{card.callsign}</span>
        <span className={`rounded border px-1.5 py-px text-[9px] font-semibold uppercase tracking-wider ${tag.cls}`}>{tag.text}</span>
        <span className="ml-auto text-[10px] text-muted">
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
  const auto = sim?.auto_speak ?? false; // voice off: Tower sends everything itself
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
    <section className="panel p-2.5 shrink-0 select-none">
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-xs uppercase tracking-wider text-muted">{auto ? "Sent by Tower" : "Say these"}</h2>
        <span className="text-[10px] text-muted font-mono">
          {auto ? `${done.length} sent` : `${now.length} to say · ${done.length} done`}
        </span>
      </div>
      <p className="mb-2 text-[10px] leading-snug text-muted">
        {auto
          ? "Voice is off: Tower sends each instruction by data link the instant the plan changes, and the aircraft turns. Nothing here needs you."
          : "Voice is on: hold Space and say the top card. The pilot reads it back, Tower checks it, and only then does the aircraft turn."}
      </p>

      {/* Voice on: the to-do list, most urgent first. */}
      {!auto && (
        <div className="flex flex-col gap-2">
          {now.length === 0 && <p className="text-xs text-muted text-center py-2">Nothing to say. Tower is quiet.</p>}
          {now.slice(0, VISIBLE_CAP).map((c, i) => (
            <div key={c.id} className={i === 0 ? "ring-1 ring-ok/40 rounded-lg" : ""}>
              <Card card={c} arrivedT={cardT[c.id] ?? simT} simT={simT} auto={auto} onFrequency running={sim?.lifecycle === "running"} held={held[c.id]} tag={tagOf(c, ids)} />
            </div>
          ))}
          {now.length > VISIBLE_CAP && <p className="text-[10px] text-muted text-right">{now.length - VISIBLE_CAP} more after these</p>}
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
          className="mt-2 w-full flex items-center justify-between rounded-md border border-line bg-panel-2/60 px-2.5 py-1.5 text-left text-[11px] text-muted hover:text-fg"
        >
          <span>
            <span className="text-fg/90 font-medium">{later.length}</span> ready for flights still to enter
            <span className="text-muted"> · next in {clock(Math.max(0, (cardT[later[0].id] ?? simT) + later[0].urgency_s - simT))}</span>
          </span>
          <span className="font-mono">{showLater ? "hide" : "show"}</span>
        </button>
      )}
      {showLater && (
        <div className="mt-1.5 flex flex-col gap-1.5">
          {later.slice(0, 8).map((c) => {
            const left = Math.max(0, (cardT[c.id] ?? simT) + c.urgency_s - simT);
            const tag = tagOf(c, ids);
            return (
              <div key={c.id} className="rounded-md border border-line/60 px-2.5 py-1.5">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs font-semibold text-fg/80">{c.callsign}</span>
                  <span className={`rounded border px-1.5 py-px text-[9px] font-semibold uppercase tracking-wider ${tag.cls}`}>{tag.text}</span>
                  <span className="ml-auto text-[10px] font-mono text-muted">enters in {clock(left)}</span>
                </div>
                <p className="mt-0.5 text-[12px] leading-snug text-fg/70">&ldquo;{c.phrase}&rdquo;</p>
              </div>
            );
          })}
        </div>
      )}

      {/* What has already gone out. In voice off this IS the panel: a log, newest first. */}
      {done.length > 0 && (
        <div className="mt-2 flex flex-col gap-1.5">
          {!auto && <div className="text-[10px] uppercase tracking-wider text-muted">Done</div>}
          {done.slice(0, auto ? 6 : 3).map((c) => (
            <Sent key={c.id} card={c} tag={tagOf(c, ids)} />
          ))}
          {done.length > (auto ? 6 : 3) && <p className="text-[10px] text-muted text-right">{done.length - (auto ? 6 : 3)} earlier</p>}
        </div>
      )}
      {cards.length === 0 && <p className="text-xs text-muted text-center py-3">Nothing to say. Tower is quiet.</p>}
    </section>
  );
}
