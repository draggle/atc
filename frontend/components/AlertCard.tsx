"use client";

import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
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
 * Title and tones for one alert. The flight strip uses the same ones, so the two can never disagree.
 * One flat card, a 2px left rule in the one colour that means something: red for wrong, amber for
 * "squack is not sure". `frame` is that rule; `soft` is the quiet box the correction phrase sits in.
 */
export function alertLook(a: ActiveAlert) {
  const radar = isRadarAlert(a);
  const severe = a.result === "mismatch" || a.result === "missing";
  const title = radar
    ? "Not flying the clearance"
    : severe
      ? a.result === "missing"
        ? "No readback"
        : "Wrong readback"
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

export function fmtItem(i: Item): string {
  const unit = i.unit ? ` ${i.unit}` : "";
  const act = i.action ? `${i.action.replace("_", " ")} ` : "";
  return `${act}${i.type} ${i.value}${unit}`;
}

export function ItemList({ items, tone, size = "lg" }: { items: Item[]; tone: "expected" | "heard"; size?: "lg" | "sm" }) {
  if (items.length === 0) return <span className="text-muted italic text-sm">nothing</span>;
  return (
    <ul className="space-y-0.5">
      {items.map((i, k) => (
        <li key={k} className={`tabular-nums leading-tight ${size === "lg" ? "text-lg" : "text-sm"} ${tone === "expected" ? "text-fg" : "text-bad"}`} style={MONO}>
          {fmtItem(i)}
        </li>
      ))}
    </ul>
  );
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
  const [open, setOpen] = useState(true);
  const pending = !done;
  return (
    <div className="mt-3 border-t border-line pt-2">
      <button onClick={() => setOpen(!open)} className="flex items-center gap-2 text-xs text-muted hover:text-fg">
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

function OneAlert({ a }: { a: ActiveAlert }) {
  const state = useTowerState();
  const dispatch = useTowerDispatch();
  const callsign = a.callsign ?? callsignForClearance(state, a.clearance_id) ?? "";
  const hasSteps = (state.steps[a.clearance_id] ?? []).length > 0 || a.decided_by === "resolver";
  const resolving = state.resolving.includes(a.clearance_id);
  const { radar, title, frame, hover, soft, titleCls } = alertLook(a);
  const show = useShowOnMap(callsign);
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
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className={`text-base font-semibold leading-tight ${titleCls}`}>{title}</div>
          {radar && <div className="mt-0.5 text-[11px] text-muted">Read back right, flying wrong</div>}
          <div className="mt-1">
            <CallsignLink callsign={callsign} live={!!show} />
          </div>
        </div>
        <div className="text-right text-[11px] text-muted shrink-0 leading-relaxed">
          <div>{a.error_type?.replace("_", " ") ?? a.result}</div>
          <div className="tabular-nums">
            {(a.confidence * 100).toFixed(0)}% · {a.decided_by.replace("_", " ")}
          </div>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3">
        <div className="min-w-0">
          <div className="text-[11px] text-muted mb-1">Expected</div>
          <ItemList items={a.expected} tone="expected" />
        </div>
        <div className="min-w-0">
          <div className="text-[11px] text-muted mb-1">Heard</div>
          <ItemList items={a.heard} tone="heard" />
        </div>
      </div>

      {a.reason && <p className="mt-2.5 text-xs text-muted leading-snug">{a.reason}</p>}

      {a.correction_phrase && (
        <div className={`mt-3 rounded-md border px-3 py-2 ${soft}`}>
          <div className="text-[11px] text-muted">Say now</div>
          <div className="mt-0.5 text-[15px] leading-snug text-fg">&ldquo;{a.correction_phrase}&rdquo;</div>
        </div>
      )}

      <div className="mt-2.5 flex items-center gap-3">
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
        <span className="text-base font-semibold text-warn animate-pulse">{watch ? "Watching" : "Checking"}</span>
        <CallsignLink callsign={callsign} live={!!show} />
        {left !== null && <span className="ml-auto text-sm tabular-nums text-warn" style={MONO} title="Simulator seconds until squack decides">{left} s</span>}
      </div>
      {watch ? (
        <p className="mt-1.5 text-xs text-muted">
          The readback was unclear, so squack is watching what {callsign || "the aircraft"} actually flies before it decides. {left === 0 ? "Nothing wrong on the radar." : `Verdict in about ${left} s.`}
        </p>
      ) : (
        <p className="mt-1.5 text-xs text-muted">Readback unclear. The resolver is gathering evidence before deciding whether to interrupt you.</p>
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
      {latest && <OneAlert a={latest} />}
      {rest.length > 0 && <div className="text-[11px] text-muted text-right">{rest.length} earlier alert{rest.length === 1 ? "" : "s"} below</div>}
      {rest.map((a) => (
        <OneAlert key={a.clearance_id} a={a} />
      ))}
    </section>
  );
}
