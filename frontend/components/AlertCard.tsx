"use client";

import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { callsignForClearance, useTowerDispatch, useTowerState, type ActiveAlert } from "@/lib/store";
import type { Item } from "@/lib/types";
import { HTTP_URL } from "@/lib/ws";

const MUTE_KEY = "tower.alertMute";
/** Only these verdicts play their clip unprompted. Ambiguous never does. */
const AUTOPLAY_RESULTS = new Set<string>(["mismatch", "partial", "missing"]);

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

/** Title and tones for one alert. The flight strip uses the same ones, so the two can never disagree. */
export function alertLook(a: ActiveAlert) {
  const radar = isRadarAlert(a);
  const severe = a.result === "mismatch" || a.result === "missing";
  const title = radar
    ? "NOT FLYING THE CLEARANCE"
    : severe
      ? a.result === "missing"
        ? "NO READBACK"
        : "WRONG READBACK"
      : a.result === "partial"
        ? "PARTIAL READBACK"
        : "CHECKING";
  const frame = radar ? "border-cyan-400 bg-cyan-400/10" : severe ? "border-bad bg-bad/10" : "border-warn bg-warn/10";
  const pulse = radar ? "alert-pulse-cyan" : severe ? "alert-pulse" : "";
  const hover = radar ? "hover:bg-cyan-400/15" : severe ? "hover:bg-bad/15" : "hover:bg-warn/15";
  const soft = radar ? "border-cyan-400/50 bg-cyan-400/10" : severe ? "border-bad/50 bg-bad/10" : "border-warn/50 bg-warn/10";
  const titleCls = radar ? "text-cyan-300" : severe ? "text-bad" : "text-warn";
  return { radar, severe, title, frame, pulse, hover, soft, titleCls };
}

export function fmtItem(i: Item): string {
  const unit = i.unit ? ` ${i.unit}` : "";
  const act = i.action ? `${i.action.replace("_", " ")} ` : "";
  return `${act}${i.type} ${i.value}${unit}`;
}

export function ItemList({ items, tone }: { items: Item[]; tone: "expected" | "heard" }) {
  if (items.length === 0) return <span className="text-muted italic">nothing</span>;
  return (
    <ul className="space-y-0.5">
      {items.map((i, k) => (
        <li key={k} className={`font-mono ${tone === "expected" ? "text-fg" : "text-bad"}`}>
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
function useShowOnMap(callsign: string) {
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

const SHOW_CLS = "group cursor-pointer transition-colors outline-none focus-visible:ring-2 focus-visible:ring-accent/60";

/** The callsign, reading as a link when the card will take you to it. */
function CallsignLink({ callsign, live }: { callsign: string; live: boolean }) {
  if (!live) return <span className="font-mono text-sm">{callsign}</span>;
  return (
    <span className="font-mono text-sm">
      <span className="underline decoration-dotted decoration-muted underline-offset-4 group-hover:decoration-fg">{callsign}</span>
      <span className="ml-2 text-[10px] text-muted group-hover:text-fg">show on map ›</span>
    </span>
  );
}

function AgentTrace({ clearanceId, done }: { clearanceId: string; done: boolean }) {
  const { steps } = useTowerState();
  const list = steps[clearanceId] ?? [];
  const [open, setOpen] = useState(true);
  const pending = !done;
  return (
    <div className="mt-2 border-t border-line/60 pt-2">
      <button onClick={() => setOpen(!open)} className="flex items-center gap-2 text-xs text-muted hover:text-fg">
        <span>{open ? "▾" : "▸"}</span>
        <span>Agent trace</span>
        <span className="font-mono">({list.length} step{list.length === 1 ? "" : "s"})</span>
        {pending && <span className="spinner" />}
      </button>
      {open && (
        <ol className="mt-1.5 space-y-1.5">
          {list.map((s) => (
            <li key={s.step} className="text-xs grid grid-cols-[1.25rem_1fr] gap-1">
              <span className="font-mono text-muted">{s.step}.</span>
              <div>
                <span className="font-mono text-warn">{s.tool}</span>
                <span className="text-muted">({Object.entries(s.args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")})</span>
                <div className="text-fg/90">{s.result_summary}</div>
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
  const { radar, title, frame, pulse, hover, soft, titleCls } = alertLook(a);
  const show = useShowOnMap(callsign);

  return (
    <div {...show} className={`rounded-lg border-2 p-3 ${frame} ${pulse} ${show ? `${SHOW_CLS} ${hover}` : ""}`}>
      <div className="flex items-start justify-between gap-2">
        <div>
          {radar && (
            <div className="inline-block mb-1 px-1.5 py-0.5 rounded bg-cyan-400/20 text-cyan-200 text-[10px] uppercase tracking-wider font-semibold">
              Read back right, flying wrong
            </div>
          )}
          <div className={`text-lg font-bold tracking-wide ${titleCls}`}>{title}</div>
          <div>
            <CallsignLink callsign={callsign} live={!!show} />
          </div>
        </div>
        <div className="text-right">
          <div className="text-[10px] uppercase text-muted">{a.error_type?.replace("_", " ") ?? a.result}</div>
          <div className="font-mono text-xs text-muted">
            conf {(a.confidence * 100).toFixed(0)}% · {a.decided_by.replace("_", " ")}
          </div>
        </div>
      </div>

      <div className="mt-2 grid grid-cols-2 gap-3 text-sm">
        <div>
          <div className="text-[10px] uppercase text-muted mb-0.5">Expected</div>
          <ItemList items={a.expected} tone="expected" />
        </div>
        <div>
          <div className="text-[10px] uppercase text-muted mb-0.5">Heard</div>
          <ItemList items={a.heard} tone="heard" />
        </div>
      </div>

      {a.reason && <p className="mt-2 text-xs text-fg/80">{a.reason}</p>}

      <div className="mt-2 flex items-center gap-2">
        {a.audio_ref && (
          <audio controls preload="none" className="h-7 max-w-[180px]" src={`${HTTP_URL}/audio/${a.audio_ref}`}>
            <track kind="captions" />
          </audio>
        )}
        <button onClick={() => dispatch({ type: "dismiss_alert", clearance_id: a.clearance_id })} className="ml-auto text-xs text-muted hover:text-fg px-2 py-1 rounded border border-line">
          Dismiss
        </button>
      </div>

      {a.correction_phrase && (
        <div className={`mt-2 rounded-md border px-2.5 py-2 ${soft}`}>
          <div className="text-[10px] uppercase text-muted">Say now</div>
          <div className="text-[15px] leading-snug">&ldquo;{a.correction_phrase}&rdquo;</div>
        </div>
      )}

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
  return (
    <div {...show} className={`rounded-lg border-2 border-warn bg-warn/10 p-3 ${show ? `${SHOW_CLS} hover:bg-warn/15` : ""}`}>
      <div className="flex items-center gap-2">
        <span className="spinner" />
        <span className="text-lg font-bold tracking-wide text-warn">{watch ? "WATCHING" : "CHECKING"}</span>
        <CallsignLink callsign={callsign} live={!!show} />
        {left !== null && <span className="ml-auto font-mono text-sm tabular-nums text-warn" title="Simulator seconds until Tower decides">{left}s</span>}
      </div>
      {watch ? (
        <p className="mt-1 text-xs text-fg/80">
          The readback was unclear, so Tower is watching what {callsign || "the aircraft"} actually flies before it decides. {left === 0 ? "Deciding now." : `Verdict in about ${left} s.`}
        </p>
      ) : (
        <p className="mt-1 text-xs text-fg/80">Readback unclear. The resolver is gathering evidence before deciding whether to interrupt you.</p>
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

export default function AlertCard() {
  const { alerts, resolving } = useTowerState();
  const [muted, setMuted] = useState(false);
  useEffect(() => setMuted(readMute()), []);
  const [latest, ...rest] = alerts;
  useAlertAutoplay(latest, muted);
  const checking = resolving.filter((id) => !alerts.some((a) => a.clearance_id === id));
  if (!latest && checking.length === 0) return null;
  const toggleMute = () => {
    const v = !muted;
    setMuted(v);
    writeMute(v);
  };
  return (
    // Same backing as the Instructions list below: an alert is read over a zoomed-in, busy map.
    <section className="panel p-2.5 shrink-0 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <h2 className="text-xs uppercase tracking-wider text-muted">Alerts</h2>
        <button
          onClick={toggleMute}
          title={muted ? "Alert clips are muted. Click to auto-play them." : "Alert clips auto-play once. Click to mute."}
          className={`text-[10px] px-2 py-0.5 rounded border ${muted ? "border-line text-muted" : "border-accent/40 text-accent bg-accent/10"}`}
        >
          {muted ? "sound off" : "sound on"}
        </button>
      </div>
      {checking.map((id) => (
        <Checking key={id} clearanceId={id} />
      ))}
      {latest && <OneAlert a={latest} />}
      {rest.length > 0 && <div className="text-[10px] text-muted text-right">{rest.length} earlier alert{rest.length === 1 ? "" : "s"} below</div>}
      {rest.map((a) => (
        <OneAlert key={a.clearance_id} a={a} />
      ))}
    </section>
  );
}
