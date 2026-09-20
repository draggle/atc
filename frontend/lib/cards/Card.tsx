"use client";

/**
 * One card: title, body by kind, the live scoreboard strip when asked for, the actions row, and a
 * fade in its last seconds when it has a ttl. Everything squack shows on the stage or in the dock
 * goes through here.
 */
import { LiveScoreboard, renderBody } from "./registry";
import { useCardActions } from "./live";
import type { CardDescriptor } from "./types";

const FADE_MS = 4000;

export default function Card({
  card,
  expiresAt,
  now,
  className = "",
}: {
  card: CardDescriptor;
  /** ms wall clock when the card leaves; the fade starts a few seconds before */
  expiresAt?: number;
  /** the parent's clock, so every card on a stage fades on the same tick */
  now?: number;
  className?: string;
}) {
  const run = useCardActions();
  const left = expiresAt !== undefined && now !== undefined ? expiresAt - now : Infinity;
  const opacity = left < FADE_MS ? Math.max(0, left / FADE_MS) : 1;
  return (
    <section className={`panel px-3 py-2.5 transition-opacity duration-500 ${className}`} style={{ opacity }} data-card-kind={card.kind}>
      {card.title && (
        <div className="flex items-baseline justify-between gap-3 mb-1.5">
          <h2 className="text-[13px] font-semibold text-fg leading-tight">{card.title}</h2>
          {left !== Infinity && left < 30000 && <span className="text-[11px] text-muted tabular-nums">{Math.max(0, Math.ceil(left / 1000))} s</span>}
        </div>
      )}
      {renderBody(card)}
      {card.live?.scoreboard && <LiveScoreboard />}
      {card.actions && card.actions.length > 0 && (
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
          {card.actions.map((a, i) => (
            <button key={i} type="button" className="chip hover:text-fg hover:border-fg/40" onClick={() => run(a)}>
              {a.label}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
