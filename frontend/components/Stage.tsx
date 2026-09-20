"use client";

/**
 * Agent mode's panel layer. Where the rail is in normal mode, up to three slots that squack (or the
 * director, without a key) fills with cards from the registry. A card enters with a short fade and
 * leaves when it is replaced or when its ttl runs out. Nothing else is on screen until squack puts
 * it there (TRD 08, rung j).
 */
import { useEffect, useState } from "react";
import { useTowerState } from "@/lib/store";
import Card from "@/lib/cards/Card";

export default function Stage() {
  const { stage, sim } = useTowerState();
  const [now, setNow] = useState(() => Date.now());

  // One clock for every slot, ticking only while something on the stage can expire.
  const hasTtl = !!stage && stage.slots.some((c) => (c.ttl_s ?? stage.ttl_s) > 0);
  useEffect(() => {
    if (!hasTtl) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [hasTtl, stage?.at]);

  const slots = (stage?.slots ?? [])
    .map((card, i) => {
      const ttl = card.ttl_s ?? stage!.ttl_s;
      return { card, i, expiresAt: ttl > 0 ? stage!.at + ttl * 1000 : undefined };
    })
    .filter((s) => s.expiresAt === undefined || s.expiresAt > now);

  return (
    <div className="absolute top-[60px] right-2 bottom-2 z-10 w-[380px] flex flex-col gap-2 overflow-y-auto scroll-thin pr-0.5" data-testid="stage">
      {slots.length === 0 ? (
        <div className="text-[11px] text-muted text-right px-1 pt-1">
          {sim?.lifecycle === "running" ? "squack puts a card here when something matters." : "squack decides what goes here once the world is running."}
        </div>
      ) : (
        <>
          {slots.map(({ card, i, expiresAt }) => (
            <div key={`${stage!.at}-${i}`} className="card-in shrink-0">
              <Card card={card} expiresAt={expiresAt} now={now} />
            </div>
          ))}
          <div className="text-[11px] text-muted text-right px-1">{stage!.by === "agent" ? "placed by squack" : "placed by the director"}</div>
        </>
      )}
    </div>
  );
}
