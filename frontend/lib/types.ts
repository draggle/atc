/**
 * TypeScript mirror of backend/schemas.py. Change both together and tell the team.
 */

// ---------------------------------------------------------------------------
// Tower core (03-architecture.md)
// ---------------------------------------------------------------------------

/** "datalink": an instruction Tower sent as text in Auto mode. Nothing was spoken. */
export type Speaker = "controller" | "pilot" | "unknown" | "datalink";
export type ItemType =
  | "manoeuvre"
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
  /** What kind of disruption an intruder is: fighter, drone, balloon, emergency, unknown. */
  threat?: PointKind | null;
  /** "360 left", "hold right": circling on the controller's word instead of navigating */
  manoeuvre?: string | null;
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
  /** "gate": a named entry/exit of a real-traffic region. Hidden track vertices never reach the screen. */
  kind?: "fix" | "gate" | "hidden";
}

/** Where on Earth the flat simulator plane sits. Azimuthal equidistant around (lat0, lon0). */
export interface GeoFrame {
  lat0: number;
  lon0: number;
  projection: "aeqd";
  name: string;
  /** the sector boundary to draw */
  shape?: "square" | "circle";
  /** half the sector width, NM */
  half_nm?: number;
  /** [[west, south], [east, north]] covering the sector, for fitting the map */
  bounds?: [[number, number], [number, number]];
}

/** [lon, lat, alt_ft, t]: GeoJSON order, simplified for drawing. */
export type LonLatAlt = [number, number, number, number];

export type ZoneKind = "storm" | "closed" | "rocket" | "intruder_buffer";

/** Blocked airspace. It may drift and swell (the radar frame carries fresh zones when it does) and end. */
export interface Zone {
  id: string;
  x_nm: number;
  y_nm: number;
  radius_nm: number;
  kind: ZoneKind;
  label?: string;
  /** Blocked between these levels. 99999 means every level. */
  floor_ft?: number;
  ceiling_ft?: number;
  expires_t?: number | null;
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

/** "superseded": a newer plan replaced a card nobody had spoken. The store drops it. */
export type CardStatus = "pending" | "spoken" | "validated" | "verified" | "error" | "superseded";

export interface InstructionCard {
  id: string;
  callsign: string;
  items: Item[];
  phrase: string;
  reason: string;
  urgency_s: number;
  status: CardStatus;
  clearance_id: string | null;
  /** Who issued it: the human, Tower's voice (Auto), or Tower by data link (Auto). */
  via?: "human" | "voice" | "datalink" | null;
  /** What Tower heard you say instead, when it conflicts with this card. The pilot was not told. */
  heard_instead?: string | null;
  /** Why the card exists: the first plan, a change, a dogleg's "go direct", or a disruption ending. */
  origin?: "initial" | "replan" | "followup" | "release";
  /** What it clears: a disruption id (STORM1) or another flight's callsign. */
  cause?: string | null;
  emergency?: boolean;
  /** How sure Tower is of this instruction: (1 - residual risk) x how clearly it beat the runner-up. [0.05, 0.99]. */
  confidence?: number | null;
  /** Residual loss-of-separation probability on the pairs this card touches, re-scored after the replan. */
  risk_after?: number | null;
}

/** A clip is on the frequency right now. Sent before it has been transcribed. */
export interface RadioAudio {
  /** squack: its spoken answer to the user (tower/voice.py); callsign is null */
  speaker: "pilot" | "controller" | "squack";
  callsign: string | null;
  audio_ref: string;
  duration_s?: number;
}

/** What the controller said conflicts with the card. Nothing went to the pilot. */
export interface SaidCheck {
  clearance_id: string;
  card_id: string;
  callsign: string;
  heard: string;
  expected: string;
  detail: string;
}

export interface AlertResolved {
  clearance_id: string;
  callsign: string | null;
  by: "correction";
  seconds: number;
}

export type NextReadback = "random" | "correct" | "wrong_value" | "wrong_aircraft" | "omitted_item" | "missing_readback";

export type PointKind = "fighter" | "drone" | "balloon" | "emergency" | "unknown";
export type CircleKind = "storm" | "closed" | "rocket";
export type DisruptionKind = PointKind | CircleKind;

/** One entry of the Disrupt menu, sent by the backend in `state.disruption_kinds`. */
export interface DisruptionKindInfo {
  kind: DisruptionKind;
  label: string;
  blurb: string;
  shape: "point" | "circle";
  /** false: kept off the menu (the headset agent can still ask for it). Older backends send nothing: shown. */
  menu?: boolean;
}

export interface Disruption {
  id: string;
  kind: DisruptionKind;
  shape?: "point" | "circle";
  label?: string;
  alt_ft?: number | null;
  floor_ft?: number;
  ceiling_ft?: number;
  expires_t?: number | null;
  /** false when it has expired, left the sector or been removed: the screen drops it. */
  active?: boolean;
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
  /** Reaction, measured live by the backend. */
  rerouted?: number;
  /** Sim seconds from the last disruption appearing to the first rerouted aircraft visibly turning. */
  reaction_s?: number | null;
  datalink_sent?: number;
  in_zone_now?: number;
  zone_incursions?: number;
  /** Monte Carlo (TRD 07): pairs that crossed the replan threshold, and those that then cleared without a loss of separation. */
  conflicts_predicted?: number;
  conflicts_resolved?: number;
  /** n_rollouts x aircraft / elapsed, measured on the last prediction. Honest number, never a slogan. */
  futures_per_s?: number | null;
  /** pairs currently drawn as cones */
  cones_now?: number;
}

/** One pair of aircraft that may lose separation inside the horizon, from a few hundred noisy rollouts. */
export interface RiskPair {
  a: string;
  b: string;
  /** peak probability of loss of separation over the horizon */
  p_max: number;
  /** first sample where p >= 0.05, null when never */
  t_first_s: number | null;
  /** sim seconds from the report to the sample of maximum p */
  eta_s: number;
  /** 5th percentile of the minimum separation across rollouts, NM */
  min_sep_nm_p5: number;
  /** [[t, p], ...] */
  curve: [number, number][];
  /** mean closest-approach midpoint, sector NM */
  cpa_xy: [number, number];
  /** lateral p5..p95 spread of each aircraft's rollouts at eta, NM */
  spread_a_nm: number;
  spread_b_nm: number;
}

/** `risk` event: at most once per sim second, only pairs with p_max >= 0.05. */
export interface RiskReport {
  pairs: RiskPair[];
  horizon_s: number;
  n_rollouts: number;
  elapsed_ms: number;
  futures_per_s: number;
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

/** What the mic is hearing while push-to-talk is held. Partials replace each other; the final is the last one for its channel. */
export interface Dictation {
  channel: PttChannel;
  text: string;
  final: boolean;
  /** seconds of audio this text covers */
  t_audio_s: number;
}

/** Nothing moves until "running". idle = no world loaded, ready = loaded and previewable. */
export type Lifecycle = "idle" | "ready" | "running" | "paused" | "ended";

/** What a real-traffic scenario was built from. */
export interface ScenarioMeta {
  region?: string;
  label?: string;
  date?: string;
  hour_utc?: number;
  gates?: number;
  attribution?: string;
  caveats?: string;
  flights_available?: number;
  max_flights?: number;
  /** Live sky: one snapshot of the traffic over the region, taken when it was loaded. `source` stays "real". */
  live?: boolean;
  /** when the snapshot was taken, ISO-8601 */
  snapshot_utc?: string;
  /** the live feed was down: "saved_snapshot" is the last snapshot the backend saved (still `live`), "replay" is a recorded hour */
  fallback?: "saved_snapshot" | "replay";
}

/** A region live mode can take a snapshot of. */
export interface LiveRegion {
  key: string;
  label: string;
}

export interface ScenarioInfo {
  name: string;
  description: string;
  flights: number;
  source: "sim" | "real";
  meta?: ScenarioMeta;
}

export interface Notice {
  text: string;
  level: "info" | "warn" | "error";
}

export interface SimState {
  scenario: string | null;
  tower_enabled: boolean;
  auto_speak: boolean;
  /** In Auto: Tower also speaks, one exchange at a time. Off means every instruction goes by data link. */
  auto_voice?: boolean;
  /** The one switch. On: you say the cards. Off: Tower sends them by data link. (voice === !auto_speak) */
  voice?: boolean;
  /** squack says its answers on the frequency as well as writing them. */
  speak_replies?: boolean;
  /** How fast the clock is really running. With voice on it drops to 1 whenever there is something to say. */
  clock_speed?: number;
  /** How the next pilot will answer. One shot, then back to "random". */
  next_readback?: NextReadback;
  lifecycle?: Lifecycle;
  /** sim seconds per real second */
  speed?: number;
  /** bumps on every load or reset, so the screen drops the previous world's state */
  world_id?: number;
  scenarios?: ScenarioInfo[];
  /** absent from backends without live mode */
  live_regions?: LiveRegion[];
  geo?: GeoFrame;
  /** real: flights and the routes they flew come from recorded traffic */
  source?: "sim" | "real";
  meta?: ScenarioMeta;
  t: number;
  waypoints: Waypoint[];
  zones: Zone[];
  sector_nm: number;
  /** Callsigns radar verification is watching after a matched readback. */
  watching?: string[];
  /** Disruptions still active, so a reconnect restores them. */
  disruptions?: Disruption[];
  disruption_kinds?: DisruptionKindInfo[];
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
  /** `zones` is present while any zone is drifting or swelling: it replaces `state.zones`. */
  radar: AircraftState[] | { aircraft: AircraftState[]; t?: number; watching?: string[]; zones?: Zone[]; clock_speed?: number };
  plan: Plan;
  plan_update: PlanUpdate;
  instruction_card: InstructionCard;
  disruption: Disruption;
  scoreboard: Scoreboard;
  agent_reply: AgentReply;
  state: SimState;
  notice: Notice;
  radio_audio: RadioAudio;
  said_check: SaidCheck;
  alert_resolved: AlertResolved;
  risk: RiskReport;
  dictation: Dictation;
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
  | { type: "configure"; source: "sim" | "real"; scenario: string; density?: number; max_flights?: number }
  | { type: "configure"; source: "live"; region: string; max_flights?: number }
  | { type: "start" }
  | { type: "pause" }
  | { type: "reset" }
  | { type: "set_speed"; speed: number }
  | { type: "set_tower"; enabled: boolean }
  /** Auto: Tower issues the instructions itself. Manual: Tower proposes, the human says it. */
  | { type: "set_auto_speak"; enabled: boolean }
  /** Auto with Tower's voice (one exchange at a time), or silent: everything by data link, instantly. */
  | { type: "set_auto_voice"; enabled: boolean }
  | { type: "set_voice"; enabled: boolean }
  /** squack speaks its answers on the frequency (state.speak_replies). */
  | { type: "set_speak_replies"; enabled: boolean }
  | { type: "set_next_readback"; mode: NextReadback }
  | { type: "confirm_heard"; clearance_id: string }
  /** No position, or kind "random": Tower puts it where it will matter. Seeded, so it repeats. */
  // target: a callsign, "disrupt this flight". Tower puts it on that flight's own path, ahead of it.
  | { type: "add_disruption"; kind: DisruptionKind | "random"; x_nm?: number; y_nm?: number; target?: string }
  | { type: "remove_disruption"; id: string }
  | { type: "speak_card"; id: string }
  | { type: "set_sliders"; buffer_nm: number; error_rate: number; noise: number };
