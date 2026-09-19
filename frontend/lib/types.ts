/**
 * TypeScript mirror of backend/schemas.py. Change both together and tell the team.
 */

// ---------------------------------------------------------------------------
// Tower core (03-architecture.md)
// ---------------------------------------------------------------------------

export type Speaker = "controller" | "pilot" | "unknown";
export type ItemType =
  | "altitude"
  | "heading"
  | "speed"
  | "frequency"
  | "squawk"
  | "altimeter"
  | "runway"
  | "route"
  | "hold_short"
  | "other";
export type Unit = "FL" | "ft" | "deg" | "kt" | "MHz" | "hPa" | "inHg" | null;
export type ClearanceStatus = "open" | "matched" | "mismatched" | "partial" | "missing" | "uncertain";
export type VerdictResult = "match" | "mismatch" | "partial" | "missing" | "ambiguous";
export type DecidedBy = "rules" | "checker_model" | "resolver";
export type ErrorType =
  | "wrong_value"
  | "wrong_runway"
  | "wrong_direction"
  | "wrong_unit"
  | "omitted_item"
  | "ack_only"
  | "wrong_aircraft"
  | "missing_readback";

export interface Transmission {
  id: string;
  t_start: number;
  t_end: number;
  audio_ref: string;
  text_raw: string;
  text_norm: string;
  asr_confidence: number;
  speaker: Speaker;
  n_best: string[];
  text_stock: string | null;
  /** Parser's callsign for this transmission, null when none was recognised. */
  callsign: string | null;
}

export interface Item {
  type: ItemType;
  value: string | number;
  unit: Unit;
  action: string | null;
  mandatory: boolean;
}

export interface Extraction {
  transmission_id: string;
  callsign: string | null;
  items: Item[];
  unexplained_words: number;
  method: "grammar" | "llm";
}

export interface OpenClearance {
  id: string;
  callsign: string;
  items: Item[];
  issued_at: number;
  timeout_s: number;
  status: ClearanceStatus;
  source_transmission_id: string | null;
  card_id: string | null;
}

export interface Verdict {
  clearance_id: string;
  readback_transmission_id: string | null;
  result: VerdictResult;
  error_type: ErrorType | null;
  expected: Item[];
  heard: Item[];
  confidence: number;
  reason: string;
  decided_by: DecidedBy;
  correction_phrase: string | null;
}

/** `alert` payload: a Verdict plus the clip reference and (optionally) the callsign. */
export interface AlertPayload extends Verdict {
  audio_ref?: string;
  callsign?: string;
}

export interface ResolverStep {
  clearance_id: string;
  step: number;
  tool: string;
  args: Record<string, unknown>;
  result_summary: string;
}

// ---------------------------------------------------------------------------
// Simulator and planner (07-build-spec.md)
// ---------------------------------------------------------------------------

export interface AircraftState {
  callsign: string;
  x_nm: number;
  y_nm: number;
  alt_ft: number;
  target_alt_ft: number;
  hdg_deg: number;
  target_hdg_deg: number | null;
  gs_kt: number;
  target_gs_kt: number | null;
  route: string[];
  actype: string;
  is_intruder: boolean;
  t: number;
  /** Real-world position, degrees. Present from backends with a GeoFrame (phase 2 onward). */
  lat?: number;
  lon?: number;
}

export interface Waypoint {
  name: string;
  x_nm: number;
  y_nm: number;
  lat?: number;
  lon?: number;
}

/** Where on Earth the flat simulator plane sits. Azimuthal equidistant around (lat0, lon0). */
export interface GeoFrame {
  lat0: number;
  lon0: number;
  projection: "aeqd";
  name: string;
  /** half the sector width, NM */
  half_nm?: number;
  /** [[west, south], [east, north]] covering the sector, for fitting the map */
  bounds?: [[number, number], [number, number]];
}

/** [lon, lat, alt_ft, t]: GeoJSON order, simplified for drawing. */
export type LonLatAlt = [number, number, number, number];

export type ZoneKind = "storm" | "closed" | "intruder_buffer";

export interface Zone {
  id: string;
  x_nm: number;
  y_nm: number;
  radius_nm: number;
  kind: ZoneKind;
  lat?: number;
  lon?: number;
}

/** (t, x, y, alt) */
export type PathSample = [number, number, number, number];

export interface PlannedPath {
  callsign: string;
  samples: PathSample[];
  cost: number;
  changes: string[];
  distance_nm: number;
  time_s: number;
  /** The same path for the map: fewer points than samples, corners and level changes kept. */
  lonlat?: LonLatAlt[];
}

export interface Plan {
  paths: PlannedPath[];
  total_distance_nm: number;
  total_time_s: number;
  baseline_distance_nm: number;
  baseline_time_s: number;
  conflicts: number;
  trigger: string;
  /** Optional: fixed-route baseline paths, when the backend sends them. */
  baseline_paths?: PlannedPath[];
}

/** `plan_update`: a Plan whose `paths` are the flights that changed, plus the changed callsigns. */
export interface PlanUpdate extends Plan {
  changed?: string[];
}

export type CardStatus = "pending" | "spoken" | "validated" | "verified" | "error";

export interface InstructionCard {
  id: string;
  callsign: string;
  items: Item[];
  phrase: string;
  reason: string;
  urgency_s: number;
  status: CardStatus;
  clearance_id: string | null;
}

export type DisruptionKind = "intruder" | "storm" | "closed";

export interface Disruption {
  id: string;
  kind: DisruptionKind;
  x_nm: number;
  y_nm: number;
  radius_nm: number;
  hdg_deg: number | null;
  gs_kt: number | null;
  /** (t, x, y) */
  predicted_path: [number, number, number][];
  lat?: number;
  lon?: number;
  /** [lon, lat, t] */
  predicted_lonlat?: [number, number, number][];
}

export interface Scoreboard {
  miles_saved: number;
  time_saved_s: number;
  losses_of_separation: number;
  closest_approach_nm: number | null;
  errors_injected: number;
  errors_caught: number;
  false_alarms: number;
  mean_alert_latency_s: number | null;
  transmissions: number;
  tier1_latency_s: number | null;
}

export interface Stats {
  matches?: number;
  alerts?: number;
  tier1_latency_s?: number;
  [k: string]: unknown;
}

export interface AgentReply {
  text: string;
  actions: string[];
}

/** Nothing moves until "running". idle = no world loaded, ready = loaded and previewable. */
export type Lifecycle = "idle" | "ready" | "running" | "paused" | "ended";

export interface ScenarioInfo {
  name: string;
  description: string;
  flights: number;
  source: "sim" | "real";
}

export interface Notice {
  text: string;
  level: "info" | "warn" | "error";
}

export interface SimState {
  scenario: string | null;
  tower_enabled: boolean;
  auto_speak: boolean;
  lifecycle?: Lifecycle;
  /** sim seconds per real second */
  speed?: number;
  /** bumps on every load or reset, so the screen drops the previous world's state */
  world_id?: number;
  scenarios?: ScenarioInfo[];
  geo?: GeoFrame;
  t: number;
  waypoints: Waypoint[];
  zones: Zone[];
  sector_nm: number;
  /** Callsigns radar verification is watching after a matched readback. */
  watching?: string[];
}

// ---------------------------------------------------------------------------
// WebSocket envelope
// ---------------------------------------------------------------------------

export type EventMap = {
  transcript: Transmission;
  clearance_opened: OpenClearance;
  clearance_updated: OpenClearance;
  alert: AlertPayload;
  resolver_step: ResolverStep;
  stats: Stats;
  radar: AircraftState[] | { aircraft: AircraftState[]; t?: number; watching?: string[] };
  plan: Plan;
  plan_update: PlanUpdate;
  instruction_card: InstructionCard;
  disruption: Disruption;
  scoreboard: Scoreboard;
  agent_reply: AgentReply;
  state: SimState;
  notice: Notice;
};

export type EventType = keyof EventMap;

export type TowerEvent = {
  [K in EventType]: { type: K; payload: EventMap[K]; t: number };
}[EventType];

// ---------------------------------------------------------------------------
// Client -> server messages
// ---------------------------------------------------------------------------

export type PttChannel = "radio" | "agent";

export type ClientMessage =
  | { type: "ptt_start"; channel: PttChannel }
  | { type: "ptt_stop" }
  | { type: "agent_text"; text: string }
  | { type: "radio_text"; text: string }
  | { type: "load_scenario"; name: string }
  | { type: "configure"; source: "sim" | "real"; scenario: string; density?: number }
  | { type: "start" }
  | { type: "pause" }
  | { type: "reset" }
  | { type: "set_speed"; speed: number }
  | { type: "set_tower"; enabled: boolean }
  | { type: "set_auto_speak"; enabled: boolean }
  | { type: "add_disruption"; kind: "intruder" | "storm"; x_nm: number; y_nm: number }
  | { type: "speak_card"; id: string }
  | { type: "set_sliders"; buffer_nm: number; error_rate: number; noise: number };
