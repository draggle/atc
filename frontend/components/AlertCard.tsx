"use client";

import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { callsignForClearance, useTowerDispatch, useTowerState, type ActiveAlert } from "@/lib/store";
import type { Item } from "@/lib/types";
import { HTTP_URL } from "@/lib/ws";

const MUTE_KEY = "tower.alertMute";
/** Only these verdicts play their clip unprompted. Ambiguous never does. */
const AUTOPLAY_RESULTS = new Set<string>(["mismatch", "partial", "missing"]);

const MONO = { fontFamily: "var(--font-mono)" } as const;

function readMute(): boolean {
  try {
    return window.localStorage.getItem(MUTE_KEY) === "1";
  } catch {
    return false;
  }
}

function writeMute(v: boolean) {
  try {
    window.localStorage.setItem(MUTE_KEY, v ? "1" : "0");
  } catch {
    /* private mode or blocked storage: the toggle still works for this page load */
  }
}

/** Radar verification alerts: the readback was right but the aircraft is not doing it. */
function isRadarAlert(a: ActiveAlert): boolean {
  return a.reason.startsWith("Radar:");
}

/**
 * What kind of thing went wrong, in the shape it deserves to be shown in.
 *
 * A wrong value really is two values to compare. An omission is not: the pilot read back
 * something else entirely and simply left an item out, so laying the two side by side as
 * "expected / heard" invents a comparison the checker never made (backend/tower/check.py pairs
 * items by concept, and an omitted item has no partner at all).
 */
export type AlertShape = "comparison" | "omission" | "silence" | "wrong_aircraft" | "radar" | "unclear";

export interface AlertDetail {
  shape: AlertShape;
  /** Cleared items the pilot read back as a different value: a genuine pair. */
  pairs: { expected: Item; heard: Item }[];
  /** Cleared items nothing in the readback answered. */
  omitted: Item[];
  /** What the pilot did say that was not about a cleared item (or, on a radar alert, what radar sees). */
  others: Item[];
}

const RUNWAYISH = new Set(["runway", "hold_short"]);
const sameConcept = (a: Item, b: Item) => a.type === b.type || (RUNWAYISH.has(a.type) && RUNWAYISH.has(b.type));

/** Loose equality, only to decide whether an item is worth showing as a disagreement. */
function sameValue(a: Item, b: Item): boolean {
  const na = Number(String(a.value).replace(/[^\d.-]/g, ""));
  const nb = Number(String(b.value).replace(/[^\d.-]/g, ""));
  if (Number.isFinite(na) && Number.isFinite(nb) && String(a.value).trim() !== "" && String(b.value).trim() !== "") {
    return na === nb && a.unit === b.unit;
  }
  return String(a.value).toUpperCase() === String(b.value).toUpperCase();
}

export function alertDetail(a: ActiveAlert): AlertDetail {
  const expected = Array.isArray(a.expected) ? a.expected : [];
  const heard = Array.isArray(a.heard) ? a.heard : [];
  const used = new Set<number>();
  const pairs: { expected: Item; heard: Item }[] = [];
  const omitted: Item[] = [];
  for (const e of expected) {
    const k = heard.findIndex((h, i) => !used.has(i) && sameConcept(e, h));
    if (k < 0) {
      omitted.push(e);
      continue;
    }
    used.add(k);
    if (!sameValue(e, heard[k])) pairs.push({ expected: e, heard: heard[k] });
  }
  const others = heard.filter((_, i) => !used.has(i));

  const radar = isRadarAlert(a);
  const shape: AlertShape = radar
    ? "radar"
    : a.error_type === "wrong_aircraft"
      ? "wrong_aircraft"
      : a.error_type === "missing_readback" || a.result === "missing" || a.error_type === "ack_only"
        ? "silence"
        : a.result === "ambiguous"
          ? "unclear"
          : pairs.length > 0
            ? "comparison"
            : omitted.length > 0
              ? "omission"
              : "unclear";
  return { shape, pairs, omitted, others };
}

/**
 * Title and tones for one alert. The flight strip uses the same ones, so the two can never disagree.
 * One flat card, a 2px left rule in the one colour that means something: red for wrong, amber for
 * "squack is not sure". `frame` is that rule; `soft` is the quiet box the correction phrase sits in.
 */
export function alertLook(a: ActiveAlert) {
  const radar = isRadarAlert(a);
  const severe = a.result === "mismatch" || a.result === "missing";
  const { shape, omitted } = alertDetail(a);
  const title = radar
    ? "Not flying the clearance"
    : a.error_type === "wrong_aircraft"
      ? "Another aircraft answered"
      : a.error_type === "ack_only"
        ? "Acknowledged only"
        : a.result === "missing" || a.error_type === "missing_readback"
          ? "No readback"
          : shape === "omission" && omitted.length > 0
            ? "Readback incomplete"
            : severe
              ? "Wrong readback"
              : a.result === "partial"
                ? "Partial readback"
                : "Unclear readback"; // squack could not tell, and says so. "Checking" is the card while it still is.
  const wrong = radar || severe;
  const frame = wrong ? "border-l-bad" : "border-l-warn";
  const pulse = "";
  const hover = "hover:bg-panel-2";
  const soft = "border-line bg-panel-2";
  const titleCls = wrong ? "text-bad" : "text-warn";
  return { radar, severe, title, frame, pulse, hover, soft, titleCls };
}

/** The value of one item in the words a controller uses: FL240, heading 270, 280 kt, direct ESTIR. */
function itemValue(i: Item): string {
  const v = String(i.value ?? "").trim();
  const n = Number(v.replace(/^FL\s*/i, "").replace(/,/g, ""));
  const num = Number.isFinite(n) && v !== "";
  switch (i.type) {
    case "altitude":
      if (!num) return `level ${v}`;
      return i.unit === "ft" && n >= 1000 ? `${Math.round(n).toLocaleString()} ft` : `FL${String(Math.round(n)).padStart(3, "0")}`;
    case "heading":
      return num ? `heading ${String(Math.round(n)).padStart(3, "0")}` : `heading ${v}`;
    case "speed":
      return num ? `speed ${Math.round(n)} kt` : `speed ${v}`;
    case "frequency":
      return `frequency ${v}`;
    case "squawk":
      return `squawk ${v}`;
    case "altimeter":
      return `altimeter ${v}`;
    case "runway":
      return `runway ${v}`;
    case "hold_short":
      return `hold short runway ${v}`;
    case "route":
      return `direct ${v.toUpperCase().replace(/^(DIRECT|DCT)\s+/, "")}`;
    default:
      return `${String(i.type ?? "item").replace(/_/g, " ")} ${v}${i.unit ? ` ${i.unit}` : ""}`.trim();
  }
}

/**
 * One item as a line of the card. The action is only prefixed when it says something the value does
 * not: `Item(type="speed", action="speed")` comes off the parser with the word twice, and printing
 * both gave "speed speed 525 kt" on the alert.
 */
export function fmtItem(i: Item): string {
  const body = itemValue(i);
  const act = String(i.action ?? "").replace(/_/g, " ").trim();
  if (!act) return body;
  const words = new Set(body.toLowerCase().split(/[^a-z]+/).filter(Boolean));
  const fresh = act
    .split(" ")
    .filter((w) => !words.has(w.toLowerCase()))
    .join(" ");
  return fresh ? `${fresh} ${body}` : body;
}

export function ItemList({ items, tone, size = "lg" }: { items: Item[]; tone: "expected" | "heard" | "plain"; size?: "lg" | "sm" }) {
  if (items.length === 0) return <span className="text-muted italic text-sm">nothing</span>;
  const ink = tone === "heard" ? "text-bad" : tone === "plain" ? "text-fg/70" : "text-fg";
  return (
    <ul className="space-y-0.5">
      {items.map((i, k) => (
        <li key={k} className={`tabular-nums leading-tight ${size === "lg" ? "text-lg" : "text-sm"} ${ink}`} style={MONO}>
          {fmtItem(i)}
        </li>
      ))}
    </ul>
  );
}

/** One labelled row of evidence: "Cleared", "Read back", "Not read back". */
function Row({ label, items, tone, note }: { label: string; items?: Item[]; tone: "expected" | "heard" | "plain"; note?: string }) {
  return (
    <div className="grid grid-cols-[5.25rem_1fr] gap-2 items-baseline">
      <span className="text-[11px] text-muted">{label}</span>
      {items ? <ItemList items={items} tone={tone} size="sm" /> : <span className={`text-sm ${tone === "heard" ? "text-bad" : "text-fg/70"}`} style={MONO}>{note}</span>}
    </div>
  );
}

/**
 * The proof behind the verdict, shaped by what actually happened. Secondary by design: the
 * correction is the instruction, this is what makes it believable.
 */
export function AlertEvidence({ a, dense = false }: { a: ActiveAlert; dense?: boolean }) {
  const { shape, pairs, omitted, others } = alertDetail(a);
  const rows: ReactNode[] = [];
  const key = (s: string) => `${a.clearance_id}-${s}`;
  if (shape === "comparison") {
    rows.push(<Row key={key("c")} label="Cleared" items={pairs.map((p) => p.expected)} tone="expected" />);
    rows.push(<Row key={key("r")} label="Read back" items={pairs.map((p) => p.heard)} tone="heard" />);
  } else if (shape === "omission") {
    rows.push(<Row key={key("m")} label="Missing" items={omitted} tone="heard" />);
    if (others.length > 0) rows.push(<Row key={key("s")} label="Did say" items={others} tone="plain" />);
  } else if (shape === "silence") {
    rows.push(<Row key={key("c")} label="Cleared" items={a.expected} tone="expected" />);
    rows.push(<Row key={key("h")} label="Heard" tone="heard" note={a.error_type === "ack_only" ? "acknowledgement only" : "nothing"} />);
  } else if (shape === "wrong_aircraft") {
    rows.push(<Row key={key("c")} label="Cleared" items={a.expected} tone="expected" />);
    rows.push(<Row key={key("h")} label="Answered by" tone="heard" note="another aircraft" />);
  } else if (shape === "radar") {
    rows.push(<Row key={key("c")} label="Cleared" items={a.expected} tone="expected" />);
    rows.push(<Row key={key("f")} label="Flying" items={a.heard} tone="heard" />);
  } else {
    rows.push(<Row key={key("c")} label="Cleared" items={a.expected} tone="expected" />);
    rows.push(<Row key={key("h")} label="Heard" items={a.heard} tone="plain" />);
  }
  if (shape === "comparison" && omitted.length > 0) {
    rows.push(<Row key={key("m2")} label="Missing" items={omitted} tone="heard" />);
  }
  return <div className={`space-y-1 ${dense ? "" : "mt-1.5"}`}>{rows}</div>;
}

/**
 * "Take me to it": a click or Enter on the card selects the aircraft, follows it, and flies the camera there.
 * The card's own controls and a text selection are left alone. Enter only: Space is push-to-talk everywhere.
 */
export function useShowOnMap(callsign: string) {
  const dispatch = useTowerDispatch();
  if (!callsign) return null;
  return {
    role: "button",
    tabIndex: 0,
    title: `Show ${callsign} on the map`,
    onClick: (e: MouseEvent<HTMLElement>) => {
      if ((e.target as HTMLElement).closest("button, audio, a, input")) return;
      const sel = window.getSelection();
      if (sel && !sel.isCollapsed) return;
      dispatch({ type: "focus", callsign });
    },
    onKeyDown: (e: KeyboardEvent<HTMLElement>) => {
      if (e.key !== "Enter" || e.target !== e.currentTarget) return;
      dispatch({ type: "focus", callsign });
    },
  };
}

export const SHOW_CLS = "group cursor-pointer transition-colors outline-none focus-visible:ring-1 focus-visible:ring-fg/60";

/** The callsign, reading as a link when the card will take you to it. */
export function CallsignLink({ callsign, live }: { callsign: string; live: boolean }) {
  if (!live) return <span className="text-sm font-medium text-fg">{callsign}</span>;
  return (
    <span className="text-sm font-medium text-fg">
      <span className="underline decoration-dotted decoration-muted underline-offset-4 group-hover:decoration-fg">{callsign}</span>
      <span className="ml-2 text-[11px] font-normal text-muted group-hover:text-fg">show on map ›</span>
    </span>
  );
}

/** A text link. Everything a card lets you do reads like this; nothing is a filled button. */
const LINK = "text-xs text-muted hover:text-fg underline decoration-dotted underline-offset-4";

function AgentTrace({ clearanceId, done }: { clearanceId: string; done: boolean }) {
  const { steps } = useTowerState();
  const list = steps[clearanceId] ?? [];
  const [open, setOpen] = useState(false);
  const pending = !done;
  return (
    <div className="mt-2 border-t border-line pt-2">
      <button onClick={() => setOpen(!open)} className="flex items-center gap-2 text-[11px] text-muted hover:text-fg">
        <span className="w-3 text-center">{open ? "▾" : "▸"}</span>
        <span>Agent trace</span>
        <span className="tabular-nums">· {list.length} step{list.length === 1 ? "" : "s"}</span>
        {pending && <span className="dot dot-warn animate-pulse" />}
      </button>
      {open && (
        <ol className="mt-2 space-y-1.5">
          {list.map((s) => (
            <li key={s.step} className="text-xs grid grid-cols-[1.25rem_1fr] gap-1">
              <span className="text-muted tabular-nums">{s.step}.</span>
              <div>
                <span className="text-muted" style={MONO}>{s.tool}</span>
                <span className="text-muted/60">({Object.entries(s.args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")})</span>
                <div className="text-fg/90 mt-0.5">{s.result_summary}</div>
              </div>
            </li>
          ))}
          {pending && list.length === 0 && <li className="text-xs text-muted">Waiting for the resolver…</li>}
        </ol>
      )}
    </div>
  );
}

/** The reason, without the machinery the controller does not need mid-transmission. */
function plainReason(a: ActiveAlert): string {
  const r = (a.reason ?? "").trim();
  if (!r) return "";
  // check.py appends its own workings after a semicolon ("…; but hypothesis 'x' contains…"):
  // the first clause is the English, the rest is evidence and lives under "Why squack is unsure".
  return r.replace(/^Radar:\s*/, "").split(";")[0].trim();
}

function extraReason(a: ActiveAlert): string {
  const r = (a.reason ?? "").trim();
  const i = r.indexOf(";");
  return i < 0 ? "" : r.slice(i + 1).trim();
}

/**
 * One alert, read top to bottom as: who and what kind, what to say, why, then the proof.
 * The correction phrase is the whole point of the card, so it is the only thing set large.
 */
function OneAlert({ a, newest }: { a: ActiveAlert; newest: boolean }) {
  const state = useTowerState();
  const dispatch = useTowerDispatch();
  const callsign = a.callsign ?? callsignForClearance(state, a.clearance_id) ?? "";
  const hasSteps = (state.steps[a.clearance_id] ?? []).length > 0 || a.decided_by === "resolver";
  const resolving = state.resolving.includes(a.clearance_id);
  const { radar, title, frame, hover, titleCls } = alertLook(a);
  const show = useShowOnMap(callsign);
  const [open, setOpen] = useState(newest);
  const why = plainReason(a);
  const more = extraReason(a);
  if (a.resolved) {
    // The controller said the correction and the pilot read it back right. Closed, and it says so.
    return (
      <div className="rounded-lg border border-line border-l-2 border-l-ok bg-panel-2 px-3 py-2.5">
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold text-ok">Corrected</span>
          <span className="text-xs text-muted">{callsign}</span>
        </div>
        <p className="mt-1 text-xs text-muted">
          Wrong readback caught, corrected and read back right in {Math.round(a.resolved.seconds)} s.
        </p>
      </div>
    );
  }

  return (
    <div {...show} className={`rounded-lg border border-line border-l-2 ${frame} bg-panel-2 px-3 py-2.5 ${show ? `${SHOW_CLS} ${hover}` : ""}`}>
      {/* What kind of thing this is, and who it is about. Small: the instruction below is the point. */}
      <div className="flex items-baseline justify-between gap-2">
        <div className="min-w-0 flex items-baseline gap-2">
          <span className={`text-[13px] font-semibold ${titleCls}`}>{title}</span>
          <CallsignLink callsign={callsign} live={!!show} />
        </div>
        <span className="shrink-0 text-[11px] text-muted tabular-nums" title={`Decided by ${a.decided_by.replace("_", " ")}`}>
          {(a.confidence * 100).toFixed(0)}%
        </span>
      </div>

      {/* The one thing to do about it. */}
      {a.correction_phrase ? (
        <p className="mt-2 text-[15px] font-medium leading-snug text-fg">
          <span className="text-[11px] font-normal text-muted mr-1.5 align-middle">Say</span>
          &ldquo;{a.correction_phrase}&rdquo;
        </p>
      ) : radar ? (
        <p className="mt-2 text-[15px] font-medium leading-snug text-fg">Check {callsign || "the aircraft"} on the radar.</p>
      ) : null}

      {/* Why, in the backend's own one line. */}
      {why && <p className="mt-1.5 text-xs text-muted leading-snug">{why}</p>}

      {/* The proof, secondary but always one click away. */}
      <button
        onClick={() => setOpen(!open)}
        className="mt-2 flex items-center gap-1.5 text-[11px] text-muted hover:text-fg"
        aria-expanded={open}
      >
        <span className="w-2.5 text-center">{open ? "▾" : "▸"}</span>
        <span>What was said</span>
      </button>
      {open && (
        <>
          <AlertEvidence a={a} />
          {more && <p className="mt-1.5 text-[11px] text-muted leading-snug">{more}</p>}
          <p className="mt-1.5 text-[11px] text-muted">
            {a.error_type?.replace(/_/g, " ") ?? a.result} · decided by {a.decided_by.replace("_", " ")}
          </p>
        </>
      )}

      <div className="mt-2 flex items-center gap-3">
        {a.audio_ref && (
          <audio controls preload="none" className="h-7 max-w-[180px]" src={`${HTTP_URL}/audio/${a.audio_ref}`}>
            <track kind="captions" />
          </audio>
        )}
        <button onClick={() => dispatch({ type: "dismiss_alert", clearance_id: a.clearance_id })} className={`ml-auto ${LINK}`}>
          Dismiss
        </button>
      </div>

      {(hasSteps || resolving) && <AgentTrace clearanceId={a.clearance_id} done={!resolving} />}
    </div>
  );
}

function Checking({ clearanceId }: { clearanceId: string }) {
  const state = useTowerState();
  const callsign = callsignForClearance(state, clearanceId) ?? "";
  const show = useShowOnMap(callsign);
  // The resolver's `watch` tool waits on the radar before it decides, a minute by default. Say so,
  // with a countdown on the simulator's clock, or a silent minute reads as a missed error.
  const watch = [...(state.steps[clearanceId] ?? [])].reverse().find((s) => s.tool === "watch");
  const watchFor = Number(watch?.args?.seconds) > 0 ? Number(watch?.args?.seconds) : 60;
  const simT = state.sim?.t ?? 0;
  const [watchT0, setWatchT0] = useState<number | null>(null);
  useEffect(() => {
    if (watch && watchT0 === null) setWatchT0(simT);
  }, [watch, watchT0, simT]);
  const left = watch ? Math.max(0, Math.ceil(watchFor - (simT - (watchT0 ?? simT)))) : null;
  // The watch ran out and the radar raised nothing: the aircraft did as it was told. Take the card down.
  const dispatch = useTowerDispatch();
  useEffect(() => {
    if (left !== 0) return;
    const t = setTimeout(() => dispatch({ type: "stop_resolving", clearance_id: clearanceId }), 4000);
    return () => clearTimeout(t);
  }, [left, clearanceId, dispatch]);
  return (
    <div {...show} className={`rounded-lg border border-line border-l-2 border-l-warn bg-panel-2 px-3 py-2.5 ${show ? `${SHOW_CLS} hover:bg-panel` : ""}`}>
      <div className="flex items-center gap-2">
        {/* The one animation left on an alert: a slow breathe while squack is still deciding. */}
        <span className="dot dot-warn animate-pulse" />
        <span className="text-[13px] font-semibold text-warn animate-pulse">{watch ? "Watching" : "Checking"}</span>
        <CallsignLink callsign={callsign} live={!!show} />
        {left !== null && <span className="ml-auto text-sm tabular-nums text-warn" style={MONO} title="Simulator seconds until squack decides">{left} s</span>}
      </div>
      <p className="mt-1.5 text-[15px] font-medium leading-snug text-fg">Nothing to say yet.</p>
      {watch ? (
        <p className="mt-1 text-xs text-muted leading-snug">
          The readback was unclear, so squack is watching what {callsign || "the aircraft"} actually flies before it decides. {left === 0 ? "Nothing wrong on the radar." : `Verdict in about ${left} s.`}
        </p>
      ) : (
        <p className="mt-1 text-xs text-muted leading-snug">Readback unclear. The resolver is gathering evidence before deciding whether to interrupt you.</p>
      )}
      <AgentTrace clearanceId={clearanceId} done={false} />
    </div>
  );
}

/** Plays the newest severe alert's clip once. Autoplay can be blocked by the browser; that is swallowed. */
function useAlertAutoplay(latest: ActiveAlert | undefined, muted: boolean) {
  const played = useRef(new Set<string>());
  useEffect(() => {
    if (!latest || muted) return;
    if (!latest.audio_ref || !AUTOPLAY_RESULTS.has(latest.result)) return;
    const key = `${latest.clearance_id}:${latest.audio_ref}`;
    if (played.current.has(key)) return;
    played.current.add(key);
    try {
      const el = new Audio(`${HTTP_URL}/audio/${latest.audio_ref}`);
      el.play().catch(() => {});
    } catch {
      /* no Audio in this environment */
    }
  }, [latest, muted]);
}

const CORRECTED_SHOWS_MS = 10000; // a corrected alert says so for this long, then leaves the panel

export default function AlertCard() {
  const { alerts: everyAlert, resolving } = useTowerState();
  const [, tick] = useState(0);
  const closing = everyAlert.some((a) => a.resolved);
  useEffect(() => {
    if (!closing) return;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [closing]);
  const alerts = everyAlert.filter((a) => !a.resolved || Date.now() - a.resolved.at < CORRECTED_SHOWS_MS);
  const [muted, setMuted] = useState(false);
  useEffect(() => setMuted(readMute()), []);
  const [latest, ...rest] = alerts;
  // Every transmission is now played on the frequency as it happens (lib/radio.ts), so the
  // alert no longer plays its clip a second time. The clip stays on the card to replay by hand.
  useAlertAutoplay(undefined, muted);
  const checking = resolving.filter((id) => !alerts.some((a) => a.clearance_id === id));
  if (!latest && checking.length === 0) return null;
  const toggleMute = () => {
    const v = !muted;
    setMuted(v);
    writeMute(v);
  };
  return (
    // Same backing as the Instructions list below: an alert is read over a zoomed-in, busy map.
    <section className="panel p-3 shrink-0 flex flex-col gap-2">
      <div className="flex items-baseline justify-between">
        <h2 className="text-[13px] font-semibold text-fg">Alerts</h2>
        <button
          onClick={toggleMute}
          title={muted ? "Alert clips are muted. Click to auto-play them." : "Alert clips auto-play once. Click to mute."}
          className={`text-[11px] hover:text-fg underline decoration-dotted underline-offset-4 ${muted ? "text-muted" : "text-ok"}`}
        >
          {muted ? "sound off" : "sound on"}
        </button>
      </div>
      {checking.map((id) => (
        <Checking key={id} clearanceId={id} />
      ))}
      {latest && <OneAlert key={latest.clearance_id} a={latest} newest />}
      {rest.length > 0 && <div className="text-[11px] text-muted text-right">{rest.length} earlier alert{rest.length === 1 ? "" : "s"} below</div>}
      {rest.map((a) => (
        <OneAlert key={a.clearance_id} a={a} newest={false} />
      ))}
    </section>
  );
}
