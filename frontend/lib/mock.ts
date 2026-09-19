/**
 * Scripted mock backend. Emits the same Event envelopes the real server would,
 * with payloads shaped exactly like backend/schemas.py, and reacts to client messages
 * so every view can be exercised without a backend.
 */
import type {
  AircraftState,
  AlertPayload,
  ClientMessage,
  Disruption,
  InstructionCard,
  Item,
  OpenClearance,
  PathSample,
  Plan,
  PlannedPath,
  ResolverStep,
  Scoreboard,
  SimState,
  TowerEvent,
  Transmission,
  Waypoint,
  Zone,
} from "./types";

export interface MockHandle {
  send(msg: ClientMessage): void;
  stop(): void;
}

type Emit = (ev: TowerEvent) => void;

const SECTOR = 200;
const TICK_MS = 1000; // 1 Hz like the backend, so client-side interpolation is exercised
const DT_S = 4; // sim seconds per tick, so motion is visible

const WAYPOINTS: Waypoint[] = [
  { name: "BOSOX", x_nm: 20, y_nm: 30 },
  { name: "LINNG", x_nm: 60, y_nm: 150 },
  { name: "TULEG", x_nm: 100, y_nm: 100 },
  { name: "DUNKS", x_nm: 150, y_nm: 40 },
  { name: "PEKOE", x_nm: 175, y_nm: 170 },
  { name: "ANCOL", x_nm: 30, y_nm: 180 },
  { name: "WAKOL", x_nm: 185, y_nm: 105 },
  { name: "SIMCO", x_nm: 110, y_nm: 15 },
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

export function startMock(emit: Emit): MockHandle {
  const timers = new Set<ReturnType<typeof setTimeout>>();
  let stopped = false;
  let simT = 0;
  let towerEnabled = true;
  let autoSpeak = false;
  let scenario = "Toronto FIR, 16:00 local";
  const zones: Zone[] = [{ id: "storm-1", x_nm: 140, y_nm: 130, radius_nm: 14, kind: "storm" }];
  const flights: Flight[] = FLIGHTS.map((f) => {
    const a = WP[f.route[0]];
    const b = WP[f.route[1]];
    return { ...f, idx: 1, x: a.x_nm, y: a.y_nm, hdg: hdgTo(a.x_nm, a.y_nm, b.x_nm, b.y_nm) % 360 };
  });
  let intruderCount = 0;
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
  };
  const cards = new Map<string, InstructionCard>();
  /** Callsigns radar verification is watching after a matched readback. */
  const watching = new Set<string>();

  const after = (ms: number, fn: () => void) => {
    const h = setTimeout(() => {
      timers.delete(h);
      if (!stopped) fn();
    }, ms);
    timers.add(h);
  };
  const send = <E extends TowerEvent>(ev: E) => {
    if (!stopped) emit(ev);
  };

  // ------------------------------------------------------------------ state
  const stateEvent = (): SimState => ({
    scenario,
    tower_enabled: towerEnabled,
    auto_speak: autoSpeak,
    t: simT,
    waypoints: WAYPOINTS,
    zones,
    sector_nm: SECTOR,
    watching: Array.from(watching),
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
    return { callsign: f.callsign, samples, cost: dist, changes: [], distance_nm: dist, time_s: t - simT };
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
  const tick = () => {
    simT += DT_S;
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
      const step = (f.gs / 3600) * DT_S;
      f.x += Math.sin((f.hdg * Math.PI) / 180) * step;
      f.y += Math.cos((f.hdg * Math.PI) / 180) * step;
      if (f.alt !== f.targetAlt) {
        const da = Math.sign(f.targetAlt - f.alt) * Math.min(Math.abs(f.targetAlt - f.alt), 30 * DT_S);
        f.alt += da;
      }
      if (f.isIntruder && (f.x < -10 || f.x > SECTOR + 10 || f.y < -10 || f.y > SECTOR + 10)) {
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
      t: simT,
    }));
    send({ type: "radar", payload: { aircraft: list, t: simT, watching: Array.from(watching) }, t: simT });
  };
  const interval = setInterval(tick, TICK_MS);

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
      }),
    );
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
    after(33000, () => addDisruption("intruder", 100, 195, 180));
    after(40000, () => {
      score = { ...score, miles_saved: score.miles_saved + 8.4, time_saved_s: score.time_saved_s + 71 };
      scoreboard();
    });
    after(48000, runScript);
  };

  const addDisruption = (kind: "intruder" | "storm", x: number, y: number, hdg = 200) => {
    intruderCount += 1;
    const id = `${kind}-${intruderCount}`;
    if (kind === "intruder") {
      const gs = 520;
      flights.push({ callsign: `HAWK${intruderCount}`, actype: "F18", route: [], idx: 0, x, y, alt: 28000, targetAlt: 28000, hdg, gs, isIntruder: true });
      const predicted: [number, number, number][] = [];
      for (let s = 0; s <= 600; s += 60) {
        const d = (gs / 3600) * s;
        predicted.push([simT + s, x + Math.sin((hdg * Math.PI) / 180) * d, y + Math.cos((hdg * Math.PI) / 180) * d]);
      }
      const d: Disruption = { id, kind, x_nm: x, y_nm: y, radius_nm: 8, hdg_deg: hdg, gs_kt: gs, predicted_path: predicted };
      send({ type: "disruption", payload: d, t: simT });
    } else {
      const z: Zone = { id, x_nm: x, y_nm: y, radius_nm: 12, kind: "storm" };
      zones.push(z);
      send({ type: "state", payload: stateEvent(), t: simT });
      const d: Disruption = { id, kind, x_nm: x, y_nm: y, radius_nm: 12, hdg_deg: null, gs_kt: null, predicted_path: [] };
      send({ type: "disruption", payload: d, t: simT });
    }
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
        });
      });
    });
  };

  // ------------------------------------------------------------------ kickoff
  after(50, () => send({ type: "state", payload: stateEvent(), t: simT }));
  after(150, () => send({ type: "plan", payload: buildPlan("initial", directSet), t: simT }));
  after(300, scoreboard);
  after(400, () => tick());
  runScript();

  // ------------------------------------------------------------------ client messages
  const handle = (msg: ClientMessage) => {
    switch (msg.type) {
      case "set_tower":
        towerEnabled = msg.enabled;
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
      case "speak_card":
        if (cards.get(msg.id)?.status === "pending") happyPath(msg.id, 200);
        return;
      case "agent_text":
        after(900, () =>
          send({
            type: "agent_reply",
            payload: { text: `Mock world builder: "${msg.text}". No backend connected, so nothing changed.`, actions: [] },
            t: simT,
          }),
        );
        return;
      case "radio_text":
        send({ type: "transcript", payload: transmission("controller", msg.text.toLowerCase(), 1.0), t: simT });
        return;
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

