"use client";

import { createContext, useContext, useReducer, type Dispatch, type ReactNode } from "react";
import { radio } from "./radio";
import type {
  AgentReply,
  AircraftState,
  AlertPayload,
  Disruption,
  InstructionCard,
  Notice,
  OpenClearance,
  Plan,
  ResolverStep,
  RiskPair,
  RiskReport,
  Scoreboard,
  SimState,
  Stats,
  TowerEvent,
  Transmission,
  LonLatAlt,
} from "./types";

export type Connection = "connecting" | "live" | "mock" | "closed";

export interface ActiveAlert extends AlertPayload {
  received_at: number; // ms wall clock
  /** Set when the controller's correction was read back right: the alert is closed. */
  resolved?: { by: "correction"; seconds: number; at: number };
}

export interface ChatLine {
  role: "user" | "agent";
  text: string;
  actions?: string[];
  at: number;
}

/** Last two radar states for one aircraft, with the wall-clock ms each arrived, for interpolation. */
export interface Track {
  cur: AircraftState;
  curAt: number;
  prev: AircraftState | null;
  prevAt: number;
}

export interface ActiveNotice extends Notice {
  id: number;
  at: number; // ms wall clock
}

/** One pair the map is drawing, or has just stopped drawing (performance.now ms). */
export interface SeenRisk {
  pair: RiskPair;
  /** when it first appeared, for the fade-in */
  first: number;
  /** when a report first arrived without it (or with it under the keep floor); null while it is live */
  gone: number | null;
}

export interface Sliders {
  buffer_nm: number;
  error_rate: number;
  noise: number;
}

export interface TowerState {
  connection: Connection;
  sim: SimState | null;
  aircraft: Record<string, AircraftState>;
  /** callsign -> last two radar states, for smooth motion between ticks */
  tracks: Record<string, Track>;
  /** callsigns radar verification is watching after a matched readback */
  watching: string[];
  plan: Plan | null;
  /** callsign -> wall-clock ms when the flash should end */
  flashUntil: Record<string, number>;
  cards: InstructionCard[]; // arrival order
  /** card id -> server clock (Event envelope t) when the card first arrived */
  cardT: Record<string, number>;
  clearances: Record<string, OpenClearance>;
  alerts: ActiveAlert[]; // newest first
  steps: Record<string, ResolverStep[]>;
  /** clearance ids with resolver steps but no verdict yet */
  resolving: string[];
  transcript: Transmission[];
  scoreboard: Scoreboard | null;
  stats: Stats | null;
  disruptions: Record<string, Disruption>;
  chat: ChatLine[];
  notices: ActiveNotice[];
  /** the setup panel is open */
  setupOpen: boolean;
  /** callsign with its flight strip open */
  selected: string | null;
  /** the camera follows the selected aircraft */
  follow: boolean;
  /** bumps on every "focus": the map flies to the selected aircraft when it changes */
  focusSeq: number;
  sliders: Sliders;
  /** Which lines the map draws: the original routes, Tower's, both, or only flights Tower moved. */
  planView: PlanView;
  /** The path a flight was on before its last reroute, kept for a while so the change can be seen. */
  ghosts: Record<string, Ghost>;
  /** Last radar frame's sim time and when it arrived, so anything can be placed at "sim now". */
  simClock: { t: number; at: number };
  /** card id -> the held clearance, when what you said conflicts with that card */
  held: Record<string, string>;
  /** Who is on the frequency right now (the clip being played), for the pulse on the map. */
  onAir: { speaker: "pilot" | "controller"; callsign: string | null } | null;
  /** callsign -> what Tower just understood for it ("H270 ↑FL360"), shown on the aircraft for a few seconds */
  acks: Record<string, { text: string; at: number }>;
  showStock: boolean;
  /** The latest Monte Carlo report (TRD 07): every pair with p_max >= 0.05. */
  risk: RiskReport | null;
  /** "A|B" -> that pair's latest numbers and timing, so a cone fades in and outlasts a one-report dip. */
  riskPairs: Record<string, SeenRisk>;
}

/** The short form of an instruction, as a radar data block would show it. */
export function ackText(items: { type: string; value: string | number; unit: string | null; action: string | null }[]): string {
  const parts: string[] = [];
  for (const i of items) {
    if (i.type === "heading") parts.push(`H${String(Math.round(Number(i.value)) % 360 || 360).padStart(3, "0")}`);
    else if (i.type === "altitude") {
      const arrow = i.action === "climb" ? "↑" : i.action === "descend" ? "↓" : "=";
      parts.push(i.unit === "FL" ? `${arrow}FL${String(i.value).padStart(3, "0")}` : `${arrow}${i.value}ft`);
    } else if (i.type === "speed") parts.push(`${i.value}kt`);
    else if (i.type === "route") parts.push(`→${i.value}`);
    else if (i.type === "manoeuvre") parts.push(String(i.value));
  }
  return parts.join(" ");
}

export const initialState: TowerState = {
  connection: "connecting",
  sim: null,
  aircraft: {},
  tracks: {},
  watching: [],
  plan: null,
  flashUntil: {},
  cards: [],
  cardT: {},
  clearances: {},
  alerts: [],
  steps: {},
  resolving: [],
  transcript: [],
  scoreboard: null,
  stats: null,
  disruptions: {},
  chat: [],
  notices: [],
  setupOpen: false,
  selected: null,
  follow: false,
  focusSeq: 0,
  sliders: { buffer_nm: 3, error_rate: 0.1, noise: 0.2 },
  planView: "both",
  ghosts: {},
  simClock: { t: 0, at: 0 },
  held: {},
  onAir: null,
  acks: {},
  showStock: false,
  risk: null,
  riskPairs: {},
};

export type Action =
  | { type: "event"; event: TowerEvent }
  | { type: "connection"; connection: Connection }
  | { type: "dismiss_alert"; clearance_id: string }
  /** a radar watch ran its course with nothing to report: take its card down */
  | { type: "stop_resolving"; clearance_id: string }
  | { type: "user_chat"; text: string }
  | { type: "set_sliders"; sliders: Sliders }
  | { type: "set_plan_view"; view: PlanView }
  | { type: "on_air"; clip: { speaker: "pilot" | "controller"; callsign: string | null } | null }
  | { type: "toggle_stock" }
  | { type: "local_toggle"; key: "tower_enabled" | "auto_speak"; value: boolean }
  | { type: "dismiss_notice"; id: number }
  | { type: "set_setup_open"; open: boolean }
  | { type: "select"; callsign: string | null }
  /** "take me to it": select, follow, and fly the camera there. An alert card does this. */
  | { type: "focus"; callsign: string }
  | { type: "set_follow"; on: boolean }
  | { type: "reset" };

const TRANSCRIPT_CAP = 200;
const GHOST_MS = 30000; // how long the old path stays on screen after a reroute

export type PlanView = "today" | "tower" | "both" | "changed";

export interface Ghost {
  callsign: string;
  /** [lon, lat, alt_ft, t] */
  path: LonLatAlt[];
  born: number;
  until: number;
}

/** Is the new path a different line, or the same one a few seconds further along? */
function differs(a: LonLatAlt[], b: LonLatAlt[]): boolean {
  const mid = (p: LonLatAlt[]) => p[Math.floor(p.length / 2)];
  const [ma, mb] = [mid(a), mid(b)];
  return a.length !== b.length || Math.abs(ma[0] - mb[0]) > 0.03 || Math.abs(ma[1] - mb[1]) > 0.02;
}

const FLASH_MS = 4000;
/** Cone hysteresis: shows at 0.05, kept while p_max stays above 0.02, and for this long after it leaves the report. */
const RISK_SHOW_P = 0.05;
const RISK_KEEP_P = 0.02;
export const RISK_HOLD_MS = 1000;
/** A cone reaches full opacity this long after first appearing. */
export const RISK_FADE_IN_MS = 600;

let noticeSeq = 0;
let staleBackendWarned = false;

/** Drop everything that belonged to the previous world. Settings and chat survive. */
function clearWorld(state: TowerState): TowerState {
  return {
    ...state,
    aircraft: {},
    tracks: {},
    watching: [],
    plan: null,
    flashUntil: {},
    ghosts: {},
    held: {},
    onAir: null,
    acks: {},
    cards: [],
    cardT: {},
    clearances: {},
    alerts: [],
    steps: {},
    resolving: [],
    transcript: [],
    scoreboard: null,
    stats: null,
    disruptions: {},
    selected: null,
    follow: false,
    risk: null,
    riskPairs: {},
  };
}

export const riskKey = (a: string, b: string) => (a < b ? `${a}|${b}` : `${b}|${a}`);

function upsertClearance(state: TowerState, c: OpenClearance): TowerState {
  return { ...state, clearances: { ...state.clearances, [c.id]: c } };
}

function applyEvent(state: TowerState, ev: TowerEvent): TowerState {
  switch (ev.type) {
    case "state": {
      const prevWorld = state.sim?.world_id;
      const nextWorld = ev.payload.world_id;
      const base = prevWorld !== undefined && nextWorld !== undefined && prevWorld !== nextWorld ? clearWorld(state) : state;
      // A freshly loaded world closes the setup panel; an idle backend opens it.
      const setupOpen = ev.payload.lifecycle === "idle" ? true : nextWorld !== prevWorld ? false : base.setupOpen;
      // A backend started before the Voice switch existed ignores it: the switch then looks as if it
      // works and snaps back on the next state message. Say so once, instead of leaving it a mystery.
      let notices = base.notices;
      if (ev.payload.voice === undefined && ev.payload.lifecycle !== undefined && !staleBackendWarned) {
        staleBackendWarned = true;
        noticeSeq += 1;
        notices = [...notices, { id: noticeSeq, at: Date.now(), level: "warn" as const,
          text: "The backend is older than this screen, so the Voice switch and other new controls will not stick. Restart it: Ctrl+C in its terminal, then run uvicorn again." }].slice(-4);
      }
      // The backend lists the disruptions still active, so a reconnect or a reload restores them.
      const disruptions = ev.payload.disruptions
        ? Object.fromEntries(ev.payload.disruptions.map((d) => [d.id, d]))
        : base.disruptions;
      return { ...base, sim: ev.payload, watching: ev.payload.watching ?? base.watching, setupOpen, disruptions, notices };
    }

    case "notice": {
      noticeSeq += 1;
      const notices = [...state.notices, { ...ev.payload, id: noticeSeq, at: Date.now() }].slice(-4);
      return { ...state, notices };
    }

    case "radar": {
      const list = Array.isArray(ev.payload) ? ev.payload : ev.payload.aircraft;
      const now = performance.now();
      const aircraft: Record<string, AircraftState> = {};
      const tracks: Record<string, Track> = {};
      for (const a of list) {
        aircraft[a.callsign] = a;
        const old = state.tracks[a.callsign];
        tracks[a.callsign] = old
          ? { cur: a, curAt: now, prev: old.cur, prevAt: old.curAt }
          : { cur: a, curAt: now, prev: null, prevAt: now };
      }
      const t = Array.isArray(ev.payload) ? (list[0]?.t ?? state.sim?.t ?? 0) : (ev.payload.t ?? state.sim?.t ?? 0);
      const simClock = { t, at: now };
      const zones = Array.isArray(ev.payload) ? undefined : ev.payload.zones; // drifting storms
      const clock = Array.isArray(ev.payload) ? undefined : ev.payload.clock_speed;
      const why = Array.isArray(ev.payload) ? undefined : { clock_why: ev.payload.clock_why ?? "", clock_hold_s: ev.payload.clock_hold_s ?? 0 };
      const sim = state.sim ? { ...state.sim, t, ...(zones ? { zones } : {}), ...(clock !== undefined ? { clock_speed: clock, ...why } : {}) } : state.sim;
      const watching = Array.isArray(ev.payload) ? state.watching : (ev.payload.watching ?? state.watching);
      return { ...state, aircraft, tracks, sim, watching, simClock };
    }

    case "plan":
      return { ...state, plan: ev.payload };

    case "plan_update": {
      const base = state.plan;
      const changed = ev.payload.changed ?? ev.payload.paths.map((p) => p.callsign);
      const now = Date.now();
      const flashUntil = { ...state.flashUntil };
      for (const cs of changed) flashUntil[cs] = now + FLASH_MS;
      if (!base) return { ...state, plan: ev.payload, flashUntil };
      // Keep what each rerouted flight WAS going to fly: a ghost line, and a ghost aircraft on it.
      const ghosts: Record<string, Ghost> = {};
      for (const [cs, g] of Object.entries(state.ghosts)) if (g.until > now) ghosts[cs] = g;
      for (const cs of changed) {
        const old = base.paths.find((p) => p.callsign === cs);
        const next = ev.payload.paths.find((p) => p.callsign === cs);
        if (old?.lonlat && old.lonlat.length > 1 && next?.lonlat && differs(old.lonlat, next.lonlat) && !ghosts[cs]) {
          ghosts[cs] = { callsign: cs, path: old.lonlat, born: now, until: now + GHOST_MS };
        }
      }
      const byCs = new Map(base.paths.map((p) => [p.callsign, p]));
      for (const p of ev.payload.paths) byCs.set(p.callsign, p);
      const plan: Plan = {
        ...base,
        ...ev.payload,
        paths: Array.from(byCs.values()),
        baseline_paths: ev.payload.baseline_paths ?? base.baseline_paths,
      };
      return { ...state, plan, flashUntil, ghosts };
    }

    case "instruction_card": {
      if (ev.payload.status === "superseded") {
        return { ...state, cards: state.cards.filter((c) => c.id !== ev.payload.id) };
      }
      if (!ev.payload.heard_instead && state.held[ev.payload.id]) {
        const { [ev.payload.id]: _cleared, ...held } = state.held; // said again properly, or sent as heard
        state = { ...state, held };
      }
      const idx = state.cards.findIndex((c) => c.id === ev.payload.id);
      const cards = idx >= 0 ? state.cards.map((c, i) => (i === idx ? ev.payload : c)) : [...state.cards, ev.payload];
      const cardT = ev.payload.id in state.cardT ? state.cardT : { ...state.cardT, [ev.payload.id]: ev.t };
      return { ...state, cards, cardT };
    }

    case "clearance_opened":
    case "clearance_updated": {
      // The resolver is done with a clearance once it leaves "open". Without an alert (it dismissed
      // the doubt, or the readback matched after all) nothing else ends the CHECKING card, and it
      // span for ever. A radar watch is the exception: that card counts itself down.
      let next = upsertClearance(state, ev.payload);
      const id = ev.payload.id;
      // Spoken, and just understood: put it on the aircraft at once. This is the first thing the
      // controller sees after letting go of the key, before the pilot has said a word.
      if (ev.type === "clearance_opened" && ev.payload.status === "open") {
        const text = ackText(ev.payload.items ?? []);
        if (text) next = { ...next, acks: { ...next.acks, [ev.payload.callsign]: { text, at: performance.now() } } };
      }
      // The controller said the correction and the pilot read it back right: that settles every
      // standing alert about the same instruction to the same aircraft. It used to stay until
      // dismissed by hand, which read as "Tower did not hear my correction".
      if (ev.payload.status === "matched" && next.alerts.length > 0) {
        const settled = new Set((ev.payload.items ?? []).map((i) => `${i.type}:${String(i.value).toUpperCase()}`));
        const alerts = next.alerts.filter((a) => {
          if (a.resolved) return true; // already closed as "corrected": it shows that for a moment, then goes
          if (a.clearance_id === id) return false;
          const cs = a.callsign ?? callsignForClearance(next, a.clearance_id);
          if (cs !== ev.payload.callsign || a.expected.length === 0) return true;
          return !a.expected.every((i) => settled.has(`${i.type}:${String(i.value).toUpperCase()}`));
        });
        if (alerts.length !== next.alerts.length) next = { ...next, alerts };
      }
      const watching = (state.steps[id] ?? []).at(-1)?.tool === "watch";
      if (ev.payload.status === "open" || watching || !next.resolving.includes(id)) return next;
      return { ...next, resolving: next.resolving.filter((r) => r !== id) };
    }

    case "transcript": {
      // Same id again: the line is being filled in (the stock model's version arrives a moment
      // after the tuned one, which nothing waits for any more).
      const at = state.transcript.findIndex((t) => t.id === ev.payload.id);
      const transcript = at >= 0
        ? state.transcript.map((t, i) => (i === at ? { ...t, ...ev.payload } : t))
        : [...state.transcript, ev.payload].slice(-TRANSCRIPT_CAP);
      return { ...state, transcript };
    }

    case "resolver_step": {
      const id = ev.payload.clearance_id;
      const prev = state.steps[id] ?? [];
      const steps = { ...state.steps, [id]: [...prev, ev.payload].sort((a, b) => a.step - b.step) };
      const resolving = state.resolving.includes(id) ? state.resolving : [...state.resolving, id];
      return { ...state, steps, resolving };
    }

    case "alert": {
      const id = ev.payload.clearance_id;
      const resolving = state.resolving.filter((r) => r !== id);
      // Never show an alert for a correct readback.
      if (ev.payload.result === "match") return { ...state, resolving };
      const alerts = [
        { ...ev.payload, received_at: Date.now() },
        ...state.alerts.filter((a) => a.clearance_id !== id),
      ].slice(0, 6);
      return { ...state, alerts, resolving };
    }

    case "radio_audio":
      radio?.play(ev.payload); // the clip goes on the air now; its transcript follows a moment later
      return state;

    case "said_check":
      return { ...state, held: { ...state.held, [ev.payload.card_id]: ev.payload.clearance_id } };

    case "alert_resolved": {
      const alerts = state.alerts.map((a) =>
        a.clearance_id === ev.payload.clearance_id ? { ...a, resolved: { by: ev.payload.by, seconds: ev.payload.seconds, at: Date.now() } } : a);
      return { ...state, alerts };
    }

    case "disruption": {
      if (ev.payload.active === false) {
        const { [ev.payload.id]: _gone, ...rest } = state.disruptions;
        return { ...state, disruptions: rest, selected: state.selected === ev.payload.id && ev.payload.kind !== "emergency" ? null : state.selected };
      }
      return { ...state, disruptions: { ...state.disruptions, [ev.payload.id]: ev.payload } };
    }

    case "scoreboard":
      return { ...state, scoreboard: ev.payload };

    case "risk": {
      const now = performance.now();
      const riskPairs: Record<string, SeenRisk> = {};
      // Pairs already drawn stay while they are above the keep floor; new ones need the show floor.
      // Reports come at most once a second, so the fade-out keys off the report that dropped the
      // pair, never off time since the last report: a live cone must not pulse between reports.
      for (const pair of ev.payload.pairs) {
        const key = riskKey(pair.a, pair.b);
        const seen = state.riskPairs[key];
        const live = seen && seen.gone === null ? pair.p_max >= RISK_KEEP_P : pair.p_max >= RISK_SHOW_P;
        if (live) riskPairs[key] = { pair, first: seen && seen.gone === null ? seen.first : now, gone: null };
      }
      for (const [key, seen] of Object.entries(state.riskPairs)) {
        if (riskPairs[key]) continue;
        const gone = seen.gone ?? now;
        if (now - gone < RISK_HOLD_MS) riskPairs[key] = { ...seen, gone };
      }
      return { ...state, risk: ev.payload, riskPairs };
    }

    case "stats":
      return { ...state, stats: ev.payload };

    case "agent_reply": {
      const r: AgentReply = ev.payload;
      return { ...state, chat: [...state.chat, { role: "agent" as const, text: r.text, actions: r.actions, at: Date.now() }].slice(-30) };
    }

    default:
      return state;
  }
}

export function reducer(state: TowerState, action: Action): TowerState {
  switch (action.type) {
    case "event":
      return applyEvent(state, action.event);
    case "connection":
      return { ...state, connection: action.connection };
    case "stop_resolving":
      return { ...state, resolving: state.resolving.filter((r) => r !== action.clearance_id) };
    case "dismiss_alert":
      return { ...state, alerts: state.alerts.filter((a) => a.clearance_id !== action.clearance_id) };
    case "user_chat":
      return { ...state, chat: [...state.chat, { role: "user" as const, text: action.text, at: Date.now() }].slice(-30) };
    case "set_sliders":
      return { ...state, sliders: action.sliders };
    case "on_air":
      return { ...state, onAir: action.clip };
    case "set_plan_view":
      return { ...state, planView: action.view };
    case "toggle_stock":
      return { ...state, showStock: !state.showStock };
    case "local_toggle":
      return state.sim ? { ...state, sim: { ...state.sim, [action.key]: action.value } } : state;
    case "dismiss_notice":
      return { ...state, notices: state.notices.filter((n) => n.id !== action.id) };
    case "set_setup_open":
      return { ...state, setupOpen: action.open };
    case "select":
      return { ...state, selected: action.callsign, follow: action.callsign ? state.follow : false };
    case "focus":
      return { ...state, selected: action.callsign, follow: true, focusSeq: state.focusSeq + 1 };
    case "set_follow":
      return { ...state, follow: action.on };
    case "reset":
      return { ...initialState, connection: state.connection };
  }
}

// ---------------------------------------------------------------------------
// Derived helpers
// ---------------------------------------------------------------------------

/** The cones to draw right now: each pair still inside its hold time, with its opacity factor (fade-in, then fade-out over the hold). */
export function visibleRisk(state: TowerState, now: number): { key: string; pair: RiskPair; fade: number }[] {
  const out: { key: string; pair: RiskPair; fade: number }[] = [];
  for (const [key, seen] of Object.entries(state.riskPairs)) {
    const gone = seen.gone === null ? 0 : now - seen.gone;
    if (gone >= RISK_HOLD_MS) continue;
    const fadeIn = Math.min(1, (now - seen.first) / RISK_FADE_IN_MS);
    const fadeOut = 1 - gone / RISK_HOLD_MS;
    out.push({ key, pair: seen.pair, fade: Math.max(0, Math.min(fadeIn, fadeOut)) });
  }
  return out.sort((x, y) => y.pair.p_max - x.pair.p_max);
}

/** The most likely predicted conflict involving this aircraft, if any is on screen. */
export function riskFor(state: TowerState, callsign: string | null, now: number): { other: string; pair: RiskPair } | null {
  if (!callsign) return null;
  for (const { pair } of visibleRisk(state, now)) {
    if (pair.a === callsign) return { other: pair.b, pair };
    if (pair.b === callsign) return { other: pair.a, pair };
  }
  return null;
}

/** callsign -> "alert" | "resolving", for radar highlighting */
export function highlightMap(state: TowerState): Record<string, "alert" | "resolving"> {
  const out: Record<string, "alert" | "resolving"> = {};
  for (const id of state.resolving) {
    const cs = callsignForClearance(state, id);
    if (cs) out[cs] = "resolving";
  }
  for (const a of state.alerts) {
    if (a.resolved) continue; // corrected and read back right: the red ring comes off the aircraft
    const cs = a.callsign ?? callsignForClearance(state, a.clearance_id);
    if (cs) out[cs] = "alert";
  }
  return out;
}

/** The newest alert standing against this aircraft, if any. `alerts` is newest first. */
export function alertFor(state: TowerState, callsign: string | null): ActiveAlert | undefined {
  if (!callsign) return undefined;
  return state.alerts.find((a) => (a.callsign ?? callsignForClearance(state, a.clearance_id)) === callsign);
}

export function callsignForClearance(state: TowerState, clearanceId: string): string | null {
  const c = state.clearances[clearanceId];
  if (c) return c.callsign;
  const card = state.cards.find((k) => k.clearance_id === clearanceId);
  return card?.callsign ?? null;
}

/** "14:32 UTC" from a live snapshot's ISO-8601 time, or "" when it is missing or not a date. */
export function snapshotClock(iso: unknown): string {
  if (typeof iso !== "string" || !iso) return "";
  // No zone on the string means UTC, not the laptop's time zone.
  const d = new Date(/(z|[+-]\d{2}:?\d{2})$/i.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return "";
  return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")} UTC`;
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const StateCtx = createContext<TowerState>(initialState);
const DispatchCtx = createContext<Dispatch<Action>>(() => {});

export function TowerStoreProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  return (
    <StateCtx.Provider value={state}>
      <DispatchCtx.Provider value={dispatch}>{children}</DispatchCtx.Provider>
    </StateCtx.Provider>
  );
}

export const useTowerState = () => useContext(StateCtx);
export const useTowerDispatch = () => useContext(DispatchCtx);
