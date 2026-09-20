/**
 * Scripted mock backend. Emits the same Event envelopes the real server would,
 * with payloads shaped exactly like backend/schemas.py, and reacts to client messages
 * so every view can be exercised without a backend.
 */
import { DEFAULT_FRAME, nmToLatLon } from "./geo";
import type {
  AircraftState,
  AlertPayload,
  ClientMessage,
  Disruption,
  DisruptionKind,
  InstructionCard,
  Item,
  Lifecycle,
  NextReadback,
  LonLatAlt,
  OpenClearance,
  PathSample,
  Plan,
  PlannedPath,
  ResolverStep,
  LiveRegion,
  ScenarioInfo,
  ScenarioMeta,
  Scoreboard,
  SimState,
  TowerEvent,
  Transmission,
  Waypoint,
  PointKind,
  RiskPair,
  Zone,
} from "./types";
import type { CardDescriptor, SimJob } from "./cards/types";

export interface MockHandle {
  send(msg: ClientMessage): void;
  stop(): void;
}

type Emit = (ev: TowerEvent) => void;

const SECTOR = 200;
let MOCK_WORLD_ID = 0;
const MOCK_SCENARIOS: ScenarioInfo[] = [
  { name: "demo", description: "Scripted mock traffic. Start the backend for the real simulator.", flights: 8, source: "sim" },
];
const MOCK_LIVE_REGIONS: LiveRegion[] = [
  { key: "europe-core", label: "Western Europe core (Maastricht, Rhine, Benelux)" },
  { key: "uk", label: "United Kingdom" },
  { key: "us-northeast", label: "US Northeast" },
  { key: "toronto", label: "Toronto" },
];
const TICK_MS = 1000; // 1 Hz like the backend, so client-side interpolation is exercised
const DT_S = 4; // sim seconds per tick, so motion is visible

const WAYPOINTS: Waypoint[] = [
  { name: "BOSOX", x_nm: -80, y_nm: -70 },
  { name: "LINNG", x_nm: -40, y_nm: 50 },
  { name: "TULEG", x_nm: 0, y_nm: 0 },
  { name: "DUNKS", x_nm: 50, y_nm: -60 },
  { name: "PEKOE", x_nm: 75, y_nm: 70 },
  { name: "ANCOL", x_nm: -70, y_nm: 80 },
  { name: "WAKOL", x_nm: 85, y_nm: 5 },
  { name: "SIMCO", x_nm: 10, y_nm: -85 },
];
const WP = Object.fromEntries(WAYPOINTS.map((w) => [w.name, w]));

interface Flight {
  callsign: string;
  actype: string;
  route: string[]; // full route; remaining computed from idx
  idx: number; // index of next waypoint
  x: number;
  y: number;
  alt: number;
  targetAlt: number;
  hdg: number;
  gs: number;
  isIntruder: boolean;
  threat?: PointKind;
}

const FLIGHTS: Omit<Flight, "idx" | "x" | "y" | "hdg">[] = [
  { callsign: "ACA123", actype: "A320", route: ["BOSOX", "TULEG", "WAKOL"], alt: 33000, targetAlt: 33000, gs: 440, isIntruder: false },
  { callsign: "WJA456", actype: "B738", route: ["ANCOL", "LINNG", "TULEG", "DUNKS"], alt: 29000, targetAlt: 29000, gs: 430, isIntruder: false },
  { callsign: "JZA221", actype: "CRJ9", route: ["PEKOE", "TULEG", "BOSOX"], alt: 27000, targetAlt: 27000, gs: 400, isIntruder: false },
  { callsign: "POE331", actype: "DH8D", route: ["WAKOL", "DUNKS", "SIMCO"], alt: 21000, targetAlt: 21000, gs: 330, isIntruder: false },
  { callsign: "DAL88", actype: "B763", route: ["SIMCO", "TULEG", "ANCOL"], alt: 35000, targetAlt: 35000, gs: 470, isIntruder: false },
  { callsign: "UAL1592", actype: "A321", route: ["DUNKS", "TULEG", "LINNG"], alt: 31000, targetAlt: 31000, gs: 450, isIntruder: false },
  { callsign: "ACA133", actype: "A333", route: ["LINNG", "PEKOE"], alt: 37000, targetAlt: 37000, gs: 480, isIntruder: false },
  { callsign: "FLE702", actype: "B38M", route: ["BOSOX", "SIMCO", "WAKOL"], alt: 25000, targetAlt: 25000, gs: 420, isIntruder: false },
];

const hdgTo = (x: number, y: number, tx: number, ty: number) => (Math.atan2(tx - x, ty - y) * 180) / Math.PI + 360;

function item(type: Item["type"], value: string | number, unit: Item["unit"], action: string | null): Item {
  return { type, value, unit, action, mandatory: true };
}

/** `liveRegion`: pretend the same scripted world is a live snapshot of that region, labelled as the backend would. */
export function startMock(emit: Emit, scenarioName?: string, liveRegion?: string): MockHandle {
  MOCK_WORLD_ID += 1;
  const timers = new Set<ReturnType<typeof setTimeout>>();
  let stopped = false;
  let simT = 0;
  // Same lifecycle as the backend: the mock loads "ready" and nothing moves until start.
  let lifecycle: Lifecycle = "ready";
  let speed = 1;
  // Echoed back so the settings sheet reflects what was picked; the mock's pilots do not use them.
  let speakReplies = true;
  let nextReadback: NextReadback = "random";
  let scriptStarted = false;
  let towerEnabled = true;
  let autoSpeak = false;
  let scenario = liveRegion ? `live/${liveRegion}` : (scenarioName ?? "Toronto FIR, 16:00 local");
  const liveMeta: ScenarioMeta | undefined = liveRegion
    ? {
        region: liveRegion,
        label: MOCK_LIVE_REGIONS.find((r) => r.key === liveRegion)?.label ?? liveRegion,
        live: true,
        snapshot_utc: new Date().toISOString(),
        attribution: "Flight data: adsb.lol, ODbL 1.0 and CC0. Gate names are ours.",
        caveats: "Mock snapshot: scripted traffic, not the real sky. Start the backend for a live one.",
      }
    : undefined;
  const zones: Zone[] = [{ id: "storm-1", x_nm: 40, y_nm: 30, radius_nm: 14, kind: "storm" }];
  const flights: Flight[] = FLIGHTS.map((f) => {
    const a = WP[f.route[0]];
    const b = WP[f.route[1]];
    return { ...f, idx: 1, x: a.x_nm, y: a.y_nm, hdg: hdgTo(a.x_nm, a.y_nm, b.x_nm, b.y_nm) % 360 };
  });
  let intruderCount = 0;
  let lastDisruptionId = "";
  let score: Scoreboard = {
    miles_saved: 0,
    time_saved_s: 0,
    losses_of_separation: 0,
    closest_approach_nm: 9.8,
    errors_injected: 0,
    errors_caught: 0,
    false_alarms: 0,
    mean_alert_latency_s: null,
    transmissions: 0,
    tier1_latency_s: 1.3,
    conflicts_predicted: 0,
    conflicts_resolved: 0,
    futures_per_s: null,
    cones_now: 0,
  };
  const cards = new Map<string, InstructionCard>();
  /** Callsigns radar verification is watching after a matched readback. */
  const watching = new Set<string>();

  const after = (ms: number, fn: () => void) => {
    const h = setTimeout(() => {
      timers.delete(h);
      if (stopped) return;
      if (lifecycle === "paused") {
        after(300, fn); // the scripted story waits while the clock is paused
        return;
      }
      fn();
    }, ms);
    timers.add(h);
  };
  const send = <E extends TowerEvent>(ev: E) => {
    if (!stopped) emit(ev);
  };

  // ------------------------------------------------------------------ geography
  // The mock sits where the backend's default scenarios sit, so the map can be built against it.
  const ll = (x: number, y: number) => {
    const [lat, lon] = nmToLatLon(DEFAULT_FRAME, x, y);
    return { lat: Math.round(lat * 1e5) / 1e5, lon: Math.round(lon * 1e5) / 1e5 };
  };
  const half = SECTOR / 2;
  const corners = [ll(-half, -half), ll(half, half), ll(-half, half), ll(half, -half)];
  const geo = {
    ...DEFAULT_FRAME,
    half_nm: half,
    bounds: [
      [Math.min(...corners.map((c) => c.lon)), Math.min(...corners.map((c) => c.lat))],
      [Math.max(...corners.map((c) => c.lon)), Math.max(...corners.map((c) => c.lat))],
    ] as [[number, number], [number, number]],
  };

  // ------------------------------------------------------------------ state
  const stateEvent = (): SimState => ({
    scenario,
    tower_enabled: towerEnabled,
    auto_speak: autoSpeak,
    voice: !autoSpeak,
    t: simT,
    waypoints: WAYPOINTS.map((w) => ({ ...w, ...ll(w.x_nm, w.y_nm) })),
    zones: zones.map((z) => ({ ...z, ...ll(z.x_nm, z.y_nm) })),
    geo,
    sector_nm: SECTOR,
    watching: Array.from(watching),
    lifecycle,
    speed,
    speak_replies: speakReplies,
    next_readback: nextReadback,
    world_id: MOCK_WORLD_ID,
    scenarios: MOCK_SCENARIOS,
    live_regions: MOCK_LIVE_REGIONS,
    ...(liveMeta ? { source: "real" as const, meta: liveMeta } : {}),
  });

  // ------------------------------------------------------------------ plan
  const pathFor = (f: Flight, direct: boolean): PlannedPath => {
    const pts: { x: number; y: number }[] = [{ x: f.x, y: f.y }];
    const remaining = f.route.slice(f.idx);
    if (direct && remaining.length > 1) {
      pts.push({ x: WP[remaining[remaining.length - 1]].x_nm, y: WP[remaining[remaining.length - 1]].y_nm });
    } else {
      for (const n of remaining) pts.push({ x: WP[n].x_nm, y: WP[n].y_nm });
    }
    let dist = 0;
    const samples: PathSample[] = [];
    let t = simT;
    for (let i = 0; i < pts.length; i++) {
      if (i > 0) {
        const d = Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
        dist += d;
        t += (d / f.gs) * 3600;
      }
      samples.push([t, pts[i].x, pts[i].y, f.alt]);
    }
    const lonlat: LonLatAlt[] = samples.map(([st, sx, sy, salt]) => {
      const p = ll(sx, sy);
      return [p.lon, p.lat, salt, st];
    });
    return { callsign: f.callsign, samples, cost: dist, changes: [], distance_nm: dist, time_s: t - simT, lonlat };
  };
  const buildPlan = (trigger: string, direct: Set<string>): Plan => {
    const paths = flights.filter((f) => !f.isIntruder).map((f) => pathFor(f, direct.has(f.callsign)));
    const baseline_paths = flights.filter((f) => !f.isIntruder).map((f) => pathFor(f, false));
    const total = (ps: PlannedPath[], k: "distance_nm" | "time_s") => ps.reduce((s, p) => s + p[k], 0);
    return {
      paths,
      baseline_paths,
      total_distance_nm: total(paths, "distance_nm"),
      total_time_s: total(paths, "time_s"),
      baseline_distance_nm: total(baseline_paths, "distance_nm"),
      baseline_time_s: total(baseline_paths, "time_s"),
      conflicts: 0,
      trigger,
    };
  };
  const directSet = new Set<string>(["ACA123", "DAL88", "JZA221", "UAL1592"]);

  // ------------------------------------------------------------------ radar
  const tick = (force = false) => {
    // Only a running world moves. `force` sends one frame so a ready world is visible.
    const dt = lifecycle === "running" ? DT_S * speed : 0;
    if (dt === 0 && !force) return;
    simT += dt;
    for (const f of flights) {
      if (!f.isIntruder && f.idx < f.route.length) {
        const w = WP[f.route[f.idx]];
        const d = Math.hypot(w.x_nm - f.x, w.y_nm - f.y);
        if (d < 1.5) {
          f.idx += 1;
          if (f.idx >= f.route.length) {
            // Re-enter from the start to keep the picture alive.
            f.idx = 1;
            f.x = WP[f.route[0]].x_nm;
            f.y = WP[f.route[0]].y_nm;
          }
        }
        const nw = WP[f.route[Math.min(f.idx, f.route.length - 1)]];
        f.hdg = hdgTo(f.x, f.y, nw.x_nm, nw.y_nm) % 360;
      }
      const step = (f.gs / 3600) * dt;
      f.x += Math.sin((f.hdg * Math.PI) / 180) * step;
      f.y += Math.cos((f.hdg * Math.PI) / 180) * step;
      if (f.alt !== f.targetAlt) {
        const da = Math.sign(f.targetAlt - f.alt) * Math.min(Math.abs(f.targetAlt - f.alt), 30 * dt);
        f.alt += da;
      }
      if (f.isIntruder && (Math.abs(f.x) > SECTOR / 2 + 10 || Math.abs(f.y) > SECTOR / 2 + 10)) {
        f.isIntruder = false;
        f.gs = 0;
      }
    }
    for (let i = flights.length - 1; i >= 0; i--) if (flights[i].gs === 0) flights.splice(i, 1);
    const list: AircraftState[] = flights.map((f) => ({
      callsign: f.callsign,
      x_nm: f.x,
      y_nm: f.y,
      alt_ft: f.alt,
      target_alt_ft: f.targetAlt,
      hdg_deg: f.hdg,
      target_hdg_deg: null,
      gs_kt: f.gs,
      target_gs_kt: null,
      route: f.route.slice(f.idx),
      actype: f.actype,
      is_intruder: f.isIntruder,
      threat: f.threat ?? null,
      t: simT,
      ...ll(f.x, f.y),
    }));
    send({ type: "radar", payload: { aircraft: list, t: simT, watching: Array.from(watching) }, t: simT });
  };
  const interval = setInterval(() => tick(), TICK_MS);

  // ------------------------------------------------------------------ helpers
  let txCounter = 0;
  const transmission = (speaker: Transmission["speaker"], text: string, conf: number, stock?: string, callsign: string | null = null): Transmission => {
    txCounter += 1;
    score = { ...score, transmissions: score.transmissions + 1 };
    return {
      id: `tx-${txCounter}`,
      t_start: simT,
      t_end: simT + 3,
      audio_ref: `mock/tx-${txCounter}.wav`,
      text_raw: text,
      text_norm: text,
      asr_confidence: conf,
      speaker,
      n_best: [text],
      text_stock: stock ?? null,
      callsign,
    };
  };
  const clearance = (id: string, callsign: string, items: Item[], status: OpenClearance["status"], cardId: string | null): OpenClearance => ({
    id,
    callsign,
    items,
    issued_at: simT,
    timeout_s: 25,
    status,
    source_transmission_id: `tx-${txCounter}`,
    card_id: cardId,
  });
  const card = (c: InstructionCard) => {
    cards.set(c.id, c);
    send({ type: "instruction_card", payload: c, t: simT });
  };
  const setCard = (id: string, status: InstructionCard["status"], clearanceId?: string) => {
    const c = cards.get(id);
    if (!c) return;
    card({ ...c, status, clearance_id: clearanceId ?? c.clearance_id });
  };
  const scoreboard = () => send({ type: "scoreboard", payload: score, t: simT });

  // ---------------------------------------------------------------- the squack agent (TRD 08)
  // The director places cards on the stage in both modes; the screen shows them only in agent mode.
  let turnSeq = 0;
  let uiMode: "normal" | "agent" = "normal";
  const stage = (slots: CardDescriptor[], by: "director" | "agent" = "director", ttl_s = 90) =>
    send({ type: "stage", payload: { slots: slots.slice(0, 3), ttl_s, by }, t: simT });
  const step = (turn_id: string, n: number, tool: string, args: Record<string, unknown>, result_summary: string, elapsed_ms: number) =>
    send({ type: "agent_step", payload: { turn_id, step: n, tool, args, result_summary, elapsed_ms }, t: simT });
  const answer = (turn_id: string, text: string, cards: CardDescriptor[] = [], why: "message" | "event" = "message") =>
    send({ type: "answer", payload: { turn_id, text, cards, for: why }, t: simT });
  const fl = (ft: number) => `FL${String(Math.round(ft / 100)).padStart(3, "0")}`;
  const aircraftRows = (pred: (f: Flight) => boolean) =>
    flights.filter((f) => !f.isIntruder && pred(f)).map((f) => [f.callsign, fl(f.alt), Math.round(f.hdg), Math.round(f.gs), f.route[f.route.length - 1] ?? ""] as (string | number)[]);

  // ---------------------------------------------------------------- predicted conflicts (TRD 07)
  /** One pair's risk report at probability p. The CPA is the midpoint of both flights 90 s ahead, so the wedges track them. */
  const risk = (a: string, b: string, p: number) => {
    const fa = flights.find((f) => f.callsign === a);
    const fb = flights.find((f) => f.callsign === b);
    if (!fa || !fb) return;
    const ahead = (f: Flight, s: number) => { const d = (f.gs / 3600) * s; return [f.x + Math.sin((f.hdg * Math.PI) / 180) * d, f.y + Math.cos((f.hdg * Math.PI) / 180) * d] as [number, number]; };
    const [ax, ay] = ahead(fa, 90);
    const [bx, by] = ahead(fb, 90);
    const eta = 90;
    const curve: [number, number][] = [];
    for (let t = 0; t <= 120; t += 5) curve.push([t, Math.round(p * Math.max(0, 1 - Math.abs(t - eta) / 45) * 100) / 100]);
    const pairs: RiskPair[] = p >= 0.05 ? [{
      a, b, p_max: p, t_first_s: p >= 0.3 ? eta - 30 : p >= 0.15 ? eta - 15 : eta, eta_s: eta,
      min_sep_nm_p5: Math.round((6.5 - 4 * p) * 10) / 10, curve, cpa_xy: [(ax + bx) / 2, (ay + by) / 2],
      spread_a_nm: 1.5 + 2.5 * p, spread_b_nm: 1.2 + 2 * p,
    }] : [];
    const n = 256;
    const elapsed = 18 + flights.length * 0.9;
    send({ type: "risk", payload: { pairs, horizon_s: 120, n_rollouts: n, elapsed_ms: elapsed, futures_per_s: Math.round((n * flights.length) / (elapsed / 1000)) }, t: simT });
    score = { ...score, futures_per_s: Math.round((n * flights.length) / (elapsed / 1000)), cones_now: pairs.length };
  };

  // ------------------------------------------------------------------ scripted stories
  let gen = 0;

  /** Happy path: card spoken, correct readback, validated then verified. */
  const happyPath = (cardId: string, delay: number) => {
    const clId = `cl-${cardId}`;
    let c: InstructionCard | undefined;
    after(delay, () => {
      c = cards.get(cardId);
      if (!c) return;
      setCard(cardId, "spoken", clId);
      send({ type: "transcript", payload: transmission("controller", c.phrase.toLowerCase(), 0.97, undefined, c.callsign), t: simT });
      send({ type: "clearance_opened", payload: clearance(clId, c.callsign, c.items, "open", cardId), t: simT });
    });
    after(delay + 2200, () => {
      if (!c) return;
      const cur = c;
      const rb = `${c.phrase.toLowerCase().replace(/^\S+\s?\S*\s/, "")} ${c.callsign.toLowerCase()}`;
      send({ type: "transcript", payload: transmission("pilot", rb, 0.91, rb.replace("two four zero", "two four"), c.callsign), t: simT });
      send({ type: "clearance_updated", payload: clearance(clId, c.callsign, c.items, "matched", cardId), t: simT });
      // A match verdict is sent by the backend but must never render as an alert.
      const v: AlertPayload = {
        clearance_id: clId,
        readback_transmission_id: `tx-${txCounter}`,
        result: "match",
        error_type: null,
        expected: c.items,
        heard: c.items,
        confidence: 0.96,
        reason: "All mandatory items read back.",
        decided_by: "rules",
        correction_phrase: null,
        callsign: c.callsign,
      };
      send({ type: "alert", payload: v, t: simT });
      setCard(cardId, "validated", clId);
      watching.add(cur.callsign);
      const f = flights.find((x) => x.callsign === cur.callsign);
      const alt = c.items.find((i) => i.type === "altitude");
      if (f && alt && typeof alt.value === "number") f.targetAlt = alt.unit === "FL" ? alt.value * 100 : alt.value;
    });
    after(delay + 6500, () => {
      if (c) watching.delete(c.callsign);
      setCard(cardId, "verified", clId);
    });
  };

  const runScript = () => {
    gen += 1;
    const g = gen;
    const fl240 = [item("altitude", 240, "FL", "descend")];
    const hdg270 = [item("heading", 270, "deg", "turn_left")];
    const fl240b = [item("altitude", 240, "FL", "descend")];
    const spd = [item("speed", 280, "kt", "reduce")];

    after(1500, () =>
      card({
        id: `c${g}-1`,
        callsign: "ACA123",
        items: fl240,
        phrase: "Air Canada one two three, descend flight level two four zero",
        reason: "Crossing traffic DAL88 at FL350 in 4 minutes",
        urgency_s: 90,
        status: "pending",
        clearance_id: null,
        cause: "DAL88",
        confidence: 0.91,
        risk_after: 0.03,
      }),
    );
    // The rollouts see ACA123 and DAL88 closing: the cone grows until the descent is read back, then clears.
    const conflict: [number, number][] = [[1500, 0.1], [2500, 0.22], [3500, 0.38], [4500, 0.52], [5500, 0.6], [6600, 0.58]];
    for (const [ms, p] of conflict) after(ms, () => risk("ACA123", "DAL88", p));
    after(3500, () => { score = { ...score, conflicts_predicted: (score.conflicts_predicted ?? 0) + 1 }; scoreboard(); });
    // Director: a predicted conflict puts the flight squack is about to move on the stage, with what the card buys.
    after(3700, () => stage([
      {
        kind: "aircraft", title: "Conflict predicted · DAL88", callsign: "ACA123", live: { aircraft: "ACA123" },
        fields: [{ label: "with", value: "DAL88 at FL350" }, { label: "p(loss of separation)", value: 0.38 }, { label: "closest in", value: "90 s" }, { label: "card", value: "descend FL240" }, { label: "confidence", value: "0.91 · risk after 0.03" }],
        actions: [{ label: "Why this card", command: "explain", args: { text: "why did you turn ACA123" } }],
      },
      {
        kind: "comparison", title: "Descend ACA123 to FL240",
        rows: [{ label: "closest approach", before: 4.1, after: 9.8, unit: "NM" }, { label: "p(loss of separation)", before: 0.38, after: 0.03 }, { label: "miles added", before: 0, after: 1.2, unit: "NM" }, { label: "minutes added", before: 0, after: 0.3 }],
        live: { scoreboard: true },
      },
    ], "director", 40));
    after(7600, () => risk("ACA123", "DAL88", 0.12));
    after(8600, () => { risk("ACA123", "DAL88", 0); score = { ...score, conflicts_resolved: (score.conflicts_resolved ?? 0) + 1 }; scoreboard(); });
    after(3000, () =>
      card({
        id: `c${g}-2`,
        callsign: "POE331",
        items: spd,
        phrase: "Porter three three one, reduce speed two eight zero knots",
        reason: "Sequence behind FLE702 into SIMCO",
        urgency_s: 150,
        status: "pending",
        clearance_id: null,
        confidence: 0.88,
        risk_after: 0.01,
      }),
    );
    happyPath(`c${g}-1`, 4500);

    // Wrong readback: 270 issued, 250 heard.
    after(12000, () =>
      card({
        id: `c${g}-3`,
        callsign: "WJA456",
        items: hdg270,
        phrase: "WestJet four five six, turn left heading two seven zero",
        reason: "Route around storm cell near PEKOE",
        urgency_s: 45,
        status: "pending",
        clearance_id: null,
        confidence: 0.76,
        risk_after: 0.08,
      }),
    );
    after(13500, () => {
      setCard(`c${g}-3`, "spoken", `cl-c${g}-3`);
      send({ type: "transcript", payload: transmission("controller", "westjet four five six turn left heading two seven zero", 0.96, undefined, "WJA456"), t: simT });
      send({ type: "clearance_opened", payload: clearance(`cl-c${g}-3`, "WJA456", hdg270, "open", `c${g}-3`), t: simT });
    });
    after(15800, () => {
      send({ type: "transcript", payload: transmission("pilot", "left heading two five zero westjet four five six", 0.88, "left heading to five zero west jet for five six", "WJA456"), t: simT });
      send({ type: "clearance_updated", payload: clearance(`cl-c${g}-3`, "WJA456", hdg270, "mismatched", `c${g}-3`), t: simT });
      score = { ...score, errors_injected: score.errors_injected + 1, errors_caught: score.errors_caught + 1, mean_alert_latency_s: 1.4 };
      const v: AlertPayload = {
        clearance_id: `cl-c${g}-3`,
        readback_transmission_id: `tx-${txCounter}`,
        result: "mismatch",
        error_type: "wrong_value",
        expected: hdg270,
        heard: [item("heading", 250, "deg", "turn_left")],
        confidence: 0.93,
        reason: "Heading read back as 250, cleared 270.",
        decided_by: "checker_model",
        correction_phrase: "WestJet four five six, negative, turn left heading two seven zero",
        audio_ref: `mock/tx-${txCounter}.wav`,
        callsign: "WJA456",
      };
      send({ type: "alert", payload: v, t: simT });
      setCard(`c${g}-3`, "error", `cl-c${g}-3`);
      scoreboard();
      // Director: the alert card goes on the stage within a tick, with the correction to say.
      stage([
        {
          kind: "aircraft", title: "Wrong readback", callsign: "WJA456", live: { aircraft: "WJA456" },
          issue: { title: "Wrong readback", expected: hdg270, heard: [item("heading", 250, "deg", "turn_left")], reason: "Heading read back as 250, cleared 270. The aircraft will fly what it read back." },
          fields: [{ label: "say now", value: "WestJet four five six, negative, turn left heading two seven zero" }, { label: "decided by", value: "checker model · 93%" }],
          actions: [{ label: "Focus", command: "focus", args: { callsign: "WJA456" } }],
        },
        {
          kind: "list", title: "Last minute",
          items: [
            { title: "WJA456 read back heading 250 for 270", detail: "checker model, 1.4 s after the readback", callsign: "WJA456", tone: "bad" },
            { title: "ACA123 descending FL240, verified on radar", callsign: "ACA123", tone: "ok" },
            { title: "Predicted conflict ACA123 / DAL88 cleared", detail: "p 0.6 to 0.0 in 80 s" },
          ],
        },
      ], "director", 60);
    });

    // Ambiguous: garbled readback, resolver wakes, three steps, then a verdict.
    after(21000, () =>
      card({
        id: `c${g}-4`,
        callsign: "JZA221",
        items: fl240b,
        phrase: "Jazz two two one, descend flight level two four zero",
        reason: "Vertical separation from ACA133 at FL370 near TULEG",
        urgency_s: 70,
        status: "pending",
        clearance_id: null,
        confidence: 0.83,
        risk_after: 0.05,
      }),
    );
    after(22500, () => {
      setCard(`c${g}-4`, "spoken", `cl-c${g}-4`);
      send({ type: "transcript", payload: transmission("controller", "jazz two two one descend flight level two four zero", 0.95, undefined, "JZA221"), t: simT });
      send({ type: "clearance_opened", payload: clearance(`cl-c${g}-4`, "JZA221", fl240b, "open", `c${g}-4`), t: simT });
    });
    after(24800, () => {
      send({ type: "transcript", payload: transmission("pilot", "descend two [static] zero jazz two two one", 0.54, "descent to zero just to to one", null), t: simT });
      send({ type: "clearance_updated", payload: clearance(`cl-c${g}-4`, "JZA221", fl240b, "uncertain", `c${g}-4`), t: simT });
    });
    const steps: ResolverStep[] = [
      { clearance_id: `cl-c${g}-4`, step: 1, tool: "relisten", args: { transmission_id: "last", n_best: 3 }, result_summary: "240 likely (0.55), 210 possible (0.40)" },
      { clearance_id: `cl-c${g}-4`, step: 2, tool: "active_aircraft", args: { prefix: "JZA" }, result_summary: "Only JZA221 on frequency; no callsign confusion" },
      { clearance_id: `cl-c${g}-4`, step: 3, tool: "aircraft_state", args: { callsign: "JZA221" }, result_summary: "Descending through FL262, target FL240, consistent with clearance" },
    ];
    after(25600, () => send({ type: "resolver_step", payload: steps[0], t: simT }));
    after(26900, () => send({ type: "resolver_step", payload: steps[1], t: simT }));
    after(28300, () => send({ type: "resolver_step", payload: steps[2], t: simT }));
    after(29800, () => {
      const v: AlertPayload = {
        clearance_id: `cl-c${g}-4`,
        readback_transmission_id: `tx-${txCounter}`,
        result: "ambiguous",
        error_type: null,
        expected: fl240b,
        heard: [item("altitude", "2?0", "FL", "descend")],
        confidence: 0.61,
        reason: "Readback garbled. Radar shows descent toward FL240; watching 20 s before clearing.",
        decided_by: "resolver",
        correction_phrase: "Jazz two two one, confirm descending flight level two four zero",
        audio_ref: `mock/tx-${txCounter}.wav`,
        callsign: "JZA221",
      };
      send({ type: "alert", payload: v, t: simT });
      const f = flights.find((x) => x.callsign === "JZA221");
      if (f) f.targetAlt = 24000;
      watching.add("JZA221");
      setCard(`c${g}-4`, "validated", `cl-c${g}-4`);
    });
    after(36000, () => {
      watching.delete("JZA221");
      setCard(`c${g}-4`, "verified", `cl-c${g}-4`);
    });

    // Disruption: intruder through the middle, replan, new cards.
    after(33000, () => addDisruption("fighter", 100, 195, 180));
    after(40000, () => {
      score = { ...score, miles_saved: score.miles_saved + 8.4, time_saved_s: score.time_saved_s + 71 };
      scoreboard();
    });
    // Director: a disruption puts the cost of going round it on the stage.
    after(34500, () => stage([
      {
        kind: "comparison", title: "Fighter through the middle · VIPER",
        rows: [{ label: "flights rerouted", before: 0, after: 2 }, { label: "extra miles", before: 0, after: 6.4, unit: "NM" }, { label: "first turn", before: "—", after: 3, unit: "s" }, { label: "closest to intruder", before: 2.1, after: 8.3, unit: "NM" }],
      },
      { kind: "list", title: "Rerouted", items: flights.filter((f) => !f.isIntruder).slice(0, 2).map((f) => ({ title: `${f.callsign} turned ${f.callsign === flights[0].callsign ? "left" : "right"} 30°`, detail: "clear of the fighter by 8 NM", callsign: f.callsign, tone: "warn" as const })) },
    ], "director", 60));
    after(48000, runScript);
  };

  // The mock knows two behaviours: a point that flies a straight line, and a fixed circle. Every
  // kind the real backend offers maps onto one of them, so the Disrupt menu works without a backend.
  const POINTS: Record<string, { prefix: string; gs: number; actype: string }> = {
    fighter: { prefix: "VIPER", gs: 520, actype: "F18" }, drone: { prefix: "DRONE", gs: 130, actype: "UAV" },
    balloon: { prefix: "BALLOON", gs: 30, actype: "BALL" }, unknown: { prefix: "UNKNOWN", gs: 280, actype: "ZZZZ" },
    emergency: { prefix: "MAYDAY", gs: 420, actype: "A320" },
  };
  const CIRCLES: Record<string, { prefix: string; r: number; floor: number; ceiling: number }> = {
    storm: { prefix: "STORM", r: 14, floor: 0, ceiling: 99999 }, closed: { prefix: "AREA", r: 18, floor: 31000, ceiling: 34000 },
    rocket: { prefix: "LAUNCH", r: 20, floor: 0, ceiling: 99999 },
  };
  const addDisruption = (asked: DisruptionKind | "random", px?: number, py?: number, hdg = 200) => {
    intruderCount += 1;
    const all = [...Object.keys(POINTS), ...Object.keys(CIRCLES)] as DisruptionKind[];
    const kind: DisruptionKind = asked === "random" ? all[intruderCount % all.length] : asked;
    const x = px ?? ((intruderCount * 37) % 120) - 60;
    const y = py ?? ((intruderCount * 53) % 120) - 60;
    if (kind in POINTS) {
      const { prefix, gs, actype } = POINTS[kind];
      const id = `${prefix}${intruderCount}`;
      flights.push({ callsign: id, actype, route: [], idx: 0, x, y, alt: 33000, targetAlt: 33000, hdg, gs, isIntruder: true, threat: kind as PointKind });
      const predicted: [number, number, number][] = [];
      for (let s = 0; s <= 600; s += 60) {
        const d = (gs / 3600) * s;
        predicted.push([simT + s, x + Math.sin((hdg * Math.PI) / 180) * d, y + Math.cos((hdg * Math.PI) / 180) * d]);
      }
      const d: Disruption = {
        id, kind, shape: "point", label: kind, alt_ft: 33000, active: true,
        x_nm: x, y_nm: y, radius_nm: 8, hdg_deg: hdg, gs_kt: gs, predicted_path: predicted, ...ll(x, y),
        predicted_lonlat: predicted.map(([pt, px, py]) => { const p = ll(px, py); return [p.lon, p.lat, pt] as [number, number, number]; }),
      };
      send({ type: "disruption", payload: d, t: simT });
      lastDisruptionId = id;
    } else {
      const { prefix, r, floor, ceiling } = CIRCLES[kind];
      const id = `${prefix}${intruderCount}`;
      const z: Zone = { id, x_nm: x, y_nm: y, radius_nm: r, kind: kind as Zone["kind"], label: kind, floor_ft: floor, ceiling_ft: ceiling, ...ll(x, y) };
      zones.push(z);
      send({ type: "state", payload: stateEvent(), t: simT });
      const d: Disruption = { id, kind, shape: "circle", label: kind, floor_ft: floor, ceiling_ft: ceiling, active: true, x_nm: x, y_nm: y, radius_nm: r, hdg_deg: null, gs_kt: null, predicted_path: [], ...ll(x, y) };
      send({ type: "disruption", payload: d, t: simT });
      lastDisruptionId = id;
    }
    const id = lastDisruptionId;
    after(600, () => {
      // Replan the two nearest flights: toggle them to a direct/offset path so the line visibly changes.
      const near = flights
        .filter((f) => !f.isIntruder)
        .sort((a, b) => Math.hypot(a.x - x, a.y - y) - Math.hypot(b.x - x, b.y - y))
        .slice(0, 2);
      for (const f of near) {
        if (directSet.has(f.callsign)) directSet.delete(f.callsign);
        else directSet.add(f.callsign);
      }
      const full = buildPlan(`disruption:${id}`, directSet);
      const changed = new Set(near.map((f) => f.callsign));
      const paths = full.paths.map((p) => (changed.has(p.callsign) ? { ...p, changes: [`avoid ${id}`] } : p)).filter((p) => changed.has(p.callsign));
      send({ type: "plan_update", payload: { ...full, paths, changed: Array.from(changed), conflicts: 0 }, t: simT });
      near.forEach((f, i) => {
        const h = Math.round(((f.hdg + (i === 0 ? -30 : 30) + 360) % 360) / 10) * 10;
        const items = [item("heading", h, "deg", i === 0 ? "turn_left" : "turn_right")];
        const spoken = String(h).padStart(3, "0").split("").map((d) => ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "niner"][Number(d)]).join(" ");
        card({
          id: `d-${id}-${f.callsign}`,
          callsign: f.callsign,
          items,
          phrase: `${f.callsign}, turn ${i === 0 ? "left" : "right"} heading ${spoken}`,
          reason: `Clear of ${kind} ${id} by ${5 + 3} NM`,
          urgency_s: 30 + i * 20,
          status: "pending",
          clearance_id: null,
          confidence: i === 0 ? 0.79 : 0.72,
          risk_after: i === 0 ? 0.06 : 0.09,
        });
      });
    });
  };

  // ------------------------------------------------------------------ kickoff
  after(50, () => send({ type: "state", payload: stateEvent(), t: simT }));
  after(150, () => send({ type: "plan", payload: buildPlan("initial", directSet), t: simT }));
  after(300, scoreboard);
  after(400, () => tick(true));

  // ------------------------------------------------------------------ client messages
  const handle = (msg: ClientMessage) => {
    switch (msg.type) {
      case "set_tower":
        towerEnabled = msg.enabled;
        send({ type: "state", payload: stateEvent(), t: simT });
        return;
      case "set_auto_voice":
      case "confirm_heard":
        return;
      case "set_next_readback":
        nextReadback = msg.mode;
        send({ type: "state", payload: stateEvent(), t: simT });
        return;
      case "set_speak_replies":
        speakReplies = msg.enabled;
        send({ type: "state", payload: stateEvent(), t: simT });
        return;
      case "set_voice":
        autoSpeak = !msg.enabled;
        send({ type: "state", payload: stateEvent(), t: simT });
        return;
      case "set_auto_speak":
        autoSpeak = msg.enabled;
        send({ type: "state", payload: stateEvent(), t: simT });
        return;
      case "load_scenario":
        scenario = msg.name;
        send({ type: "state", payload: stateEvent(), t: simT });
        after(600, () => send({ type: "agent_reply", payload: { text: `Loaded ${msg.name}.`, actions: ["load_scenario"] }, t: simT }));
        return;
      case "add_disruption":
        addDisruption(msg.kind, msg.x_nm, msg.y_nm);
        return;
      case "remove_disruption": {
        const zi = zones.findIndex((z) => z.id === msg.id);
        if (zi >= 0) zones.splice(zi, 1);
        const fi = flights.findIndex((f) => f.callsign === msg.id && f.isIntruder);
        if (fi >= 0) flights.splice(fi, 1);
        send({ type: "state", payload: stateEvent(), t: simT });
        send({ type: "disruption", payload: { id: msg.id, kind: "storm", active: false, x_nm: 0, y_nm: 0, radius_nm: 0, hdg_deg: null, gs_kt: null, predicted_path: [] }, t: simT });
        return;
      }
      case "speak_card":
        if (cards.get(msg.id)?.status === "pending") happyPath(msg.id, 200);
        return;
      case "set_ui_mode":
        uiMode = msg.mode;
        return;
      case "agent_text": {
        // Scripted answers, so the bar and the dock can be built without a backend. The real loop
        // (backend/agent) answers with the same events.
        turnSeq += 1;
        const turn = `mock-t${turnSeq}`;
        const q = msg.text.toLowerCase();
        const csIn = flights.find((f) => q.includes(f.callsign.toLowerCase()))?.callsign;
        if (/\b(who|which|list|flights?)\b/.test(q) && /\b(above|over|below|under)\b/.test(q)) {
          const m = q.match(/(\d{3})/);
          const level = m ? Number(m[1]) * 100 : 35000;
          const above = !/\b(below|under)\b/.test(q);
          const rows = aircraftRows((f) => (above ? f.alt >= level : f.alt < level));
          after(500, () => step(turn, 1, "query.aircraft", { filter: { field: "alt_ft", op: above ? ">=" : "<", value: level } }, `${rows.length} of ${flights.filter((f) => !f.isIntruder).length} aircraft`, 12));
          after(1100, () => answer(turn, `${rows.length} flight${rows.length === 1 ? " is" : "s are"} ${above ? "at or above" : "below"} ${fl(level)}.`, [
            { kind: "table", title: `${above ? "At or above" : "Below"} ${fl(level)}`, columns: ["callsign", "level", "hdg", "kt", "to"], rows, focus_col: 0 },
          ]));
          return;
        }
        // Data viz: the three plot kinds the registry renders, so the dock can be seen without a backend.
        if (/\b(separation|closest|over time|changed|trend|history)\b/.test(q)) {
          const pts: [number, number][] = [];
          for (let i = 0; i <= 12; i++) pts.push([i * 30, 14 - Math.sin(i / 2.2) * 5 - i * 0.18]);
          after(500, () => step(turn, 1, "viz.timeline", { metric: "closest_nm", since_s: 360 }, "13 samples over 6 minutes", 8));
          after(1000, () => answer(turn, "Closest approach has tightened from about 14 NM to 9 NM over the last six minutes, mostly as WJA456 and DAL88 converge on SIMCO. Still twice the minimum.", [
            { kind: "chart", chart: "line", title: "Closest approach", x_label: "seconds", y_label: "NM", series: [{ label: "closest pair", points: pts }], caption: "Sampled every 30 s. 5 NM is the floor." },
          ]));
          return;
        }
        if (/\b(levels?|altitudes?|how high|distribution)\b/.test(q)) {
          const bins: [number, number][] = [[200, 1], [240, 2], [280, 1], [320, 3], [360, 2], [400, 1]];
          after(500, () => step(turn, 1, "viz.levels", {}, "10 aircraft in 6 bands", 5));
          after(1000, () => answer(turn, "Most of the traffic is stacked between FL320 and FL400, with three aircraft sharing FL320. That band is where the next conflict will come from.", [
            { kind: "chart", chart: "hist", title: "Aircraft by flight level", x_label: "flight level", y_label: "aircraft", series: [{ label: "aircraft", points: bins }] },
          ]));
          return;
        }
        if (/\bpairs?\b/.test(q)) {
          after(500, () => step(turn, 1, "viz.pairs", { max_nm: 60 }, "9 pairs within 60 NM", 11));
          after(1000, () => answer(turn, "Nine pairs are within 60 NM. One sits close on both axes: ACA123 and DAL88, 9.8 NM apart and only 500 ft vertically, which is the pair I am watching.", [
            { kind: "chart", chart: "scatter", title: "Nearby pairs", x_label: "horizontal NM", y_label: "vertical ft", series: [
              { label: "clear", points: [[42, 4000], [38, 3000], [55, 2000], [31, 5000], [48, 1500], [26, 6000]] },
              { label: "watching", tone: "bad", points: [[9.8, 500], [14, 900], [17, 1200]] },
            ], caption: "Under 5 NM and 1,000 ft at once is a loss of separation." },
          ]));
          return;
        }
        if (/\bwhy\b/.test(q)) {
          const cs = csIn ?? "ACA123";
          after(600, () => step(turn, 1, "explain.card", { callsign: cs }, `descend FL240 · reason "Crossing traffic DAL88 at FL350 in 4 minutes" · confidence 0.91`, 9));
          after(1500, () => step(turn, 2, "query.pairs", { max_nm: 10 }, `${cs}/DAL88 closest 4.1 NM before the card, 9.8 NM after`, 21));
          after(2400, () => step(turn, 3, "explain.replan", { last: true }, "cost 61.2 vs runner-up 63.9 (climb FL370); +1.2 NM, +0.3 min", 7));
          after(3100, () => answer(turn, `${cs} descended to FL240 to stay clear of DAL88 at FL350; the runner-up, a climb to FL370, cost 2.7 more.`, [
            {
              kind: "aircraft", title: "Why this card", callsign: cs, live: { aircraft: cs },
              fields: [{ label: "reason", value: "Crossing traffic DAL88 at FL350 in 4 minutes" }, { label: "cause", value: "DAL88" }, { label: "cost · runner-up", value: "61.2 · 63.9" }, { label: "confidence", value: "0.91 (risk after 0.03, margin 1.0)" }, { label: "miles added", value: 1.2 }],
              actions: [{ label: "Focus", command: "focus", args: { callsign: cs } }, { label: "Follow", command: "follow", args: { callsign: cs } }],
            },
          ]));
          return;
        }
        if (/monte|carlo|simulat|\bsim\b|sweep|does it (still )?hold|how safe/.test(q)) {
          const job_id = `job-${turnSeq}`;
          const params = { scenario: "demo", runs: 8, density: /double/.test(q) ? 2 : 1, error_rate: 0.1 };
          const jobAt = (progress: number, status: SimJob["status"], result?: SimJob["result"]) =>
            send({ type: "sim_job", payload: { job_id, kind: "sim.montecarlo", status, progress, eta_s: status === "running" ? Math.round((1 - progress) * 7) : 0, params, ...(result ? { result } : {}) }, t: simT });
          after(300, () => step(turn, 1, "sim.montecarlo", params, `job ${job_id} started in the background`, 4));
          after(600, () => answer(turn, `Running ${params.runs} runs at ${params.density}x density in the background, about 7 s. The clock keeps ticking.`));
          for (let i = 0; i <= 6; i++) after(700 + i * 1000, () => jobAt(Math.min(0.95, i / 6), "running"));
          const rows: (string | number)[][] = [["fixed routes", 0.42, 3.8, 0], ["squack, no errors", 0.06, 6.1, -8.4], ["squack + readback errors", 0.09, 5.7, -8.1]];
          after(7900, () => jobAt(1, "done", { columns: ["arm", "LoS / flight hour", "closest p5 NM", "miles vs fixed"], rows, caption: `${params.runs} runs · density ${params.density}x · error rate ${params.error_rate} · buffer 3 NM` }));
          after(8100, () => answer(`${turn}-result`, `It holds: ${params.runs} runs, losses of separation per flight hour 0.42 on fixed routes against 0.06 with squack, 0.09 with readback errors injected.`, [
            { kind: "chart", title: "Losses of separation per flight hour", unit: "/h", series: [{ label: "fixed routes", value: 0.42, tone: "bad" }, { label: "squack, no errors", value: 0.06, tone: "ok" }, { label: "squack + readback errors", value: 0.09 }], caption: `${params.runs} runs · density ${params.density}x · error rate ${params.error_rate} · buffer 3 NM · mock numbers` },
            { kind: "table", title: "By arm", columns: ["arm", "LoS / h", "closest p5 NM", "miles vs fixed"], rows },
          ], "event"));
          return;
        }
        if (/\b(focus|follow|show me|where is)\b/.test(q) && csIn) {
          const follow = /follow/.test(q);
          after(300, () => send({ type: "ui_command", payload: { command: follow ? "follow" : "focus", args: { callsign: csIn } }, t: simT }));
          after(500, () => answer(turn, `${follow ? "Following" : "Focused"} ${csIn}.`));
          return;
        }
        if (/tilt|top down|top-down|flat|3d|exaggerat/.test(q)) {
          const top = /top|flat/.test(q);
          after(300, () => send({ type: "ui_command", payload: { command: "camera", args: top ? { top_down: true } : { pitch: 60, bearing: -20 } }, t: simT }));
          after(500, () => answer(turn, top ? "Top down." : "Tilted."));
          return;
        }
        if (/\b(only|show) (what )?changed\b|original|both lines/.test(q)) {
          const view = /changed/.test(q) ? "changed" : /original/.test(q) ? "today" : "both";
          after(300, () => send({ type: "ui_command", payload: { command: "line_view", args: { view } }, t: simT }));
          after(500, () => answer(turn, `Lines: ${view}.`));
          return;
        }
        if (/scoreboard|numbers so far|how are we doing/.test(q)) {
          after(400, () => answer(turn, "The numbers measured this session.", [{ kind: "text", title: "Scoreboard", text: "Only what was measured on this laptop.", live: { scoreboard: true } }]));
          return;
        }
        if (/agent mode|squack decides|normal mode/.test(q)) {
          const mode = /normal/.test(q) ? "normal" : "agent";
          after(300, () => send({ type: "ui_command", payload: { command: "mode", args: { mode } }, t: simT }));
          after(500, () => answer(turn, mode === "agent" ? "squack decides what is on screen now." : "Back to the usual panels."));
          return;
        }
        after(900, () => answer(turn, `Mock: no backend is connected, so "${msg.text}" changed nothing. Try "who is above 350", "why did you turn ACA123" or "run the monte carlo".`, [], uiMode === "agent" ? "message" : "message"));
        return;
      }
      case "radio_text":
        send({ type: "transcript", payload: transmission("controller", msg.text.toLowerCase(), 1.0), t: simT });
        return;
      case "start":
        if (lifecycle === "ready" || lifecycle === "paused") {
          lifecycle = "running";
          send({ type: "state", payload: stateEvent(), t: simT });
          if (!scriptStarted) {
            scriptStarted = true;
            runScript();
          }
        }
        return;
      case "pause":
        if (lifecycle === "running") {
          lifecycle = "paused";
          send({ type: "state", payload: stateEvent(), t: simT });
        }
        return;
      case "set_speed":
        speed = Math.min(120, Math.max(0.25, msg.speed));
        send({ type: "state", payload: stateEvent(), t: simT });
        return;
      case "reset":
      case "configure":
        return; // handled in ws.ts by restarting the mock
      case "ptt_start":
      case "ptt_stop":
      case "set_sliders":
        return;
    }
  };

  return {
    send: handle,
    stop() {
      stopped = true;
      clearInterval(interval);
      for (const h of timers) clearTimeout(h);
      timers.clear();
    },
  };
}

