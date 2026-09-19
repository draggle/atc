"use client";

import { createContext, useContext, useReducer, type Dispatch, type ReactNode } from "react";
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
  Scoreboard,
  SimState,
  Stats,
  TowerEvent,
  Transmission,
} from "./types";

export type Connection = "connecting" | "live" | "mock" | "closed";

export interface ActiveAlert extends AlertPayload {
  received_at: number; // ms wall clock
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
  planView: "today" | "tower";
  showStock: boolean;
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
  planView: "tower",
  showStock: false,
};

export type Action =
  | { type: "event"; event: TowerEvent }
  | { type: "connection"; connection: Connection }
  | { type: "dismiss_alert"; clearance_id: string }
  | { type: "user_chat"; text: string }
  | { type: "set_sliders"; sliders: Sliders }
  | { type: "set_plan_view"; view: "today" | "tower" }
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
const FLASH_MS = 4000;

let noticeSeq = 0;

/** Drop everything that belonged to the previous world. Settings and chat survive. */
function clearWorld(state: TowerState): TowerState {
  return {
    ...state,
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
    selected: null,
    follow: false,
  };
}

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
      // The backend lists the disruptions still active, so a reconnect or a reload restores them.
      const disruptions = ev.payload.disruptions
        ? Object.fromEntries(ev.payload.disruptions.map((d) => [d.id, d]))
        : base.disruptions;
      return { ...base, sim: ev.payload, watching: ev.payload.watching ?? base.watching, setupOpen, disruptions };
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
      const zones = Array.isArray(ev.payload) ? undefined : ev.payload.zones; // drifting storms
      const sim = state.sim ? { ...state.sim, t, ...(zones ? { zones } : {}) } : state.sim;
      const watching = Array.isArray(ev.payload) ? state.watching : (ev.payload.watching ?? state.watching);
      return { ...state, aircraft, tracks, sim, watching };
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
      const byCs = new Map(base.paths.map((p) => [p.callsign, p]));
      for (const p of ev.payload.paths) byCs.set(p.callsign, p);
      const plan: Plan = {
        ...base,
        ...ev.payload,
        paths: Array.from(byCs.values()),
        baseline_paths: ev.payload.baseline_paths ?? base.baseline_paths,
      };
      return { ...state, plan, flashUntil };
    }

    case "instruction_card": {
      if (ev.payload.status === "superseded") {
        return { ...state, cards: state.cards.filter((c) => c.id !== ev.payload.id) };
      }
      const idx = state.cards.findIndex((c) => c.id === ev.payload.id);
      const cards = idx >= 0 ? state.cards.map((c, i) => (i === idx ? ev.payload : c)) : [...state.cards, ev.payload];
      const cardT = ev.payload.id in state.cardT ? state.cardT : { ...state.cardT, [ev.payload.id]: ev.t };
      return { ...state, cards, cardT };
    }

    case "clearance_opened":
    case "clearance_updated":
      return upsertClearance(state, ev.payload);

    case "transcript": {
      const transcript = [...state.transcript, ev.payload].slice(-TRANSCRIPT_CAP);
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

    case "disruption": {
      if (ev.payload.active === false) {
        const { [ev.payload.id]: _gone, ...rest } = state.disruptions;
        return { ...state, disruptions: rest, selected: state.selected === ev.payload.id && ev.payload.kind !== "emergency" ? null : state.selected };
      }
      return { ...state, disruptions: { ...state.disruptions, [ev.payload.id]: ev.payload } };
    }

    case "scoreboard":
      return { ...state, scoreboard: ev.payload };

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
    case "dismiss_alert":
      return { ...state, alerts: state.alerts.filter((a) => a.clearance_id !== action.clearance_id) };
    case "user_chat":
      return { ...state, chat: [...state.chat, { role: "user" as const, text: action.text, at: Date.now() }].slice(-30) };
    case "set_sliders":
      return { ...state, sliders: action.sliders };
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

/** callsign -> "alert" | "resolving", for radar highlighting */
export function highlightMap(state: TowerState): Record<string, "alert" | "resolving"> {
  const out: Record<string, "alert" | "resolving"> = {};
  for (const id of state.resolving) {
    const cs = callsignForClearance(state, id);
    if (cs) out[cs] = "resolving";
  }
  for (const a of state.alerts) {
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
