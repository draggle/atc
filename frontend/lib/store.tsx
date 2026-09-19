"use client";

import { createContext, useContext, useReducer, type Dispatch, type ReactNode } from "react";
import type {
  AgentReply,
  AircraftState,
  AlertPayload,
  Disruption,
  InstructionCard,
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

export interface Sliders {
  buffer_nm: number;
  error_rate: number;
  noise: number;
}

export interface TowerState {
  connection: Connection;
  sim: SimState | null;
  aircraft: Record<string, AircraftState>;
  plan: Plan | null;
  /** callsign -> wall-clock ms when the flash should end */
  flashUntil: Record<string, number>;
  cards: InstructionCard[]; // arrival order
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
  sliders: Sliders;
  planView: "today" | "tower";
  showStock: boolean;
}

export const initialState: TowerState = {
  connection: "connecting",
  sim: null,
  aircraft: {},
  plan: null,
  flashUntil: {},
  cards: [],
  clearances: {},
  alerts: [],
  steps: {},
  resolving: [],
  transcript: [],
  scoreboard: null,
  stats: null,
  disruptions: {},
  chat: [],
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
  | { type: "reset" };

const TRANSCRIPT_CAP = 200;
const FLASH_MS = 4000;

function upsertClearance(state: TowerState, c: OpenClearance): TowerState {
  return { ...state, clearances: { ...state.clearances, [c.id]: c } };
}

function applyEvent(state: TowerState, ev: TowerEvent): TowerState {
  switch (ev.type) {
    case "state":
      return { ...state, sim: ev.payload };

    case "radar": {
      const list = Array.isArray(ev.payload) ? ev.payload : ev.payload.aircraft;
      const aircraft: Record<string, AircraftState> = {};
      for (const a of list) aircraft[a.callsign] = a;
      const t = Array.isArray(ev.payload) ? (list[0]?.t ?? state.sim?.t ?? 0) : (ev.payload.t ?? state.sim?.t ?? 0);
      const sim = state.sim ? { ...state.sim, t } : state.sim;
      return { ...state, aircraft, sim };
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
      const idx = state.cards.findIndex((c) => c.id === ev.payload.id);
      const cards = idx >= 0 ? state.cards.map((c, i) => (i === idx ? ev.payload : c)) : [...state.cards, ev.payload];
      return { ...state, cards };
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

    case "disruption":
      return { ...state, disruptions: { ...state.disruptions, [ev.payload.id]: ev.payload } };

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

export function callsignForClearance(state: TowerState, clearanceId: string): string | null {
  const c = state.clearances[clearanceId];
  if (c) return c.callsign;
  const card = state.cards.find((k) => k.clearance_id === clearanceId);
  return card?.callsign ?? null;
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
