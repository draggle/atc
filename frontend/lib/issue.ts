/**
 * An alert, turned into something the map can draw beside the aircraft it is about.
 *
 * Pure: no React, no deck.gl, no store. `describeIssue` never throws; on input it cannot make sense
 * of it returns a label-only issue, or null when there is nothing worth saying.
 *
 * Two kinds of alert arrive as the same payload (backend/tower/check.py and conform.py):
 *  - a readback alert: `heard` is what the pilot said;
 *  - a radar alert (`reason` starts "Radar:"): the readback was right, `heard` is what radar
 *    observed instead (an altitude in feet, or a heading, even when the clearance was a direct).
 */
import type { AircraftState, AlertPayload, Item } from "./types";

export type IssueKind = "level" | "route" | "heading" | "speed" | "other";
/** bad: red, wrong or missing readback. warn: amber, partial or still being checked. radar: cyan card, read back right but flying wrong. */
export type IssueTone = "bad" | "warn" | "radar";

export interface IssueFix {
  name: string;
  lon: number;
  lat: number;
}

export interface Issue {
  callsign: string;
  kind: IssueKind;
  tone: IssueTone;
  /** One short line, e.g. "cleared FL350 · read back FL330 · now FL340". */
  label: string;
  /** Nothing usable was read back: draw only the cleared geometry. */
  missing: boolean;
  /** What the "wrong" geometry is: what the pilot read back, or what radar sees the aircraft flying. */
  wrongIs: "readback" | "flown";
  expectedFix?: IssueFix;
  heardFix?: IssueFix;
  /** Degrees, compass: 0 north, 90 east. */
  expectedHdg?: number;
  heardHdg?: number;
  /** Feet. */
  expectedAltFt?: number;
  heardAltFt?: number;
  /** The aircraft is neither at the cleared level nor heading for it. */
  levelWrong?: boolean;
}

/** Fix name (upper case) -> position. */
export type FixIndex = Readonly<Record<string, IssueFix>>;

const SEP = " · ";
/** At the level, as far as the screen is concerned. Matches the backend's conformance tolerance. */
const AT_LEVEL_FT = 200;
const LABEL_CAP = 72;

/** Same test AlertCard uses for its cyan "read back right, flying wrong" card. */
export function isRadarReason(reason: unknown): boolean {
  return typeof reason === "string" && reason.startsWith("Radar:");
}

const pad3 = (n: number) => String(Math.round(n)).padStart(3, "0");
/** The map's flight-level style: FL350, FL070. */
export const flightLevel = (ft: number) => `FL${pad3(ft / 100)}`;
const hdgText = (deg: number) => pad3(((Math.round(deg) % 360) + 360) % 360);

/** A number out of a number or a string like "350", "FL350", "35,000". Null for "2?0" and friends. */
export function toNumber(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v !== "string") return null;
  const s = v.trim().replace(/^FL\s*/i, "").replace(/,/g, "");
  if (!/^-?\d+(\.\d+)?$/.test(s)) return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

/** Altitude item -> feet. Flight levels and feet both arrive; the unit says which, magnitude settles a missing or absurd unit. */
export function altitudeFt(item: Pick<Item, "value" | "unit"> | undefined | null): number | null {
  if (!item) return null;
  const n = toNumber(item.value);
  if (n === null || n < 0) return null;
  const isLevel = typeof item.value === "string" && /^\s*FL/i.test(item.value);
  if (item.unit === "ft" && !isLevel) return n;
  // "FL", a value written "FL350", or no unit: three digits is a level, more is feet.
  return n < 1000 ? n * 100 : n;
}

function fixName(v: unknown): string {
  return String(v ?? "").trim().toUpperCase().replace(/^(DIRECT|DCT)\s+/, "");
}

function kindOf(type: unknown): IssueKind {
  switch (type) {
    case "altitude": return "level";
    case "route": return "route";
    case "heading": return "heading";
    case "speed": return "speed";
    default: return "other";
  }
}

/** Do two items of the same type say the same thing? */
function sameValue(a: Item, b: Item): boolean {
  if (a.type === "altitude") {
    const fa = altitudeFt(a);
    const fb = altitudeFt(b);
    if (fa !== null && fb !== null) return fa === fb;
  }
  const na = toNumber(a.value);
  const nb = toNumber(b.value);
  if (na !== null && nb !== null) return a.type === "heading" ? (na - nb) % 360 === 0 : na === nb;
  return fixName(a.value) === fixName(b.value);
}

/** How one item reads in the label, in the map's words. */
function say(item: Item): string {
  try {
    const n = toNumber(item.value);
    switch (item.type) {
      case "altitude": {
        const ft = altitudeFt(item);
        return ft === null ? `"${String(item.value)}"` : flightLevel(ft);
      }
      case "heading": return n === null ? `hdg "${String(item.value)}"` : `hdg ${hdgText(n)}`;
      case "speed": return n === null ? `"${String(item.value)}"` : `${Math.round(n)} kt`;
      case "route": return `direct ${fixName(item.value)}`;
      case "frequency": return `${String(item.value)}${item.unit ? ` ${item.unit}` : ""}`;
      default: return `${String(item.type ?? "item").replace(/_/g, " ")} ${String(item.value)}${item.unit ? ` ${item.unit}` : ""}`;
    }
  } catch {
    return "?";
  }
}

const cap = (s: string) => (s.length > LABEL_CAP ? `${s.slice(0, LABEL_CAP - 1).trimEnd()}…` : s);
const isItem = (i: unknown): i is Item => {
  if (typeof i !== "object" || i === null) return false;
  const v = (i as { value?: unknown }).value;
  return typeof (i as { type?: unknown }).type === "string" && (typeof v === "number" || (typeof v === "string" && v.trim() !== ""));
};

export function describeIssue(
  alert: AlertPayload | null | undefined,
  aircraft: AircraftState | null | undefined,
  fixes: FixIndex | null | undefined,
): Issue | null {
  try {
    return describe(alert, aircraft ?? undefined, fixes ?? {});
  } catch {
    return null;
  }
}

function describe(alert: AlertPayload | null | undefined, ac: AircraftState | undefined, fixes: FixIndex): Issue | null {
  if (!alert || alert.result === "match") return null;
  const callsign = alert.callsign ?? ac?.callsign ?? "";
  const expected = (Array.isArray(alert.expected) ? alert.expected : []).filter(isItem);
  const heard = (Array.isArray(alert.heard) ? alert.heard : []).filter(isItem);
  const radar = isRadarReason(alert.reason);
  const tone: IssueTone = radar ? "radar" : alert.result === "mismatch" || alert.result === "missing" ? "bad" : "warn";
  const wrongIs = radar ? "flown" : "readback";

  if (expected.length === 0) {
    const reason = typeof alert.reason === "string" ? alert.reason.trim() : "";
    return reason ? { callsign, kind: "other", tone, label: cap(reason), missing: heard.length === 0, wrongIs } : null;
  }

  // The item the alert is about: the first cleared item that was not read back as cleared.
  const item = expected.find((e) => !heard.some((h) => h.type === e.type && sameValue(e, h))) ?? expected[0];
  const kind = kindOf(item.type);
  const said = heard.find((h) => h.type === item.type);

  const noReadback = alert.error_type === "missing_readback" || alert.result === "missing";
  const ackOnly = alert.error_type === "ack_only";
  const wrongAircraft = alert.error_type === "wrong_aircraft";
  // For a radar alert `heard` is an observation, never a readback, so nothing of it is drawn as one.
  const missing = radar ? false : noReadback || ackOnly || wrongAircraft || !said;

  const issue: Issue = { callsign, kind, tone, label: "", missing, wrongIs };

  // ---- geometry
  let nowText = "";
  if (kind === "level") {
    const cleared = altitudeFt(item);
    if (cleared !== null) issue.expectedAltFt = cleared;
    if (!missing && !radar) {
      const h = altitudeFt(said);
      if (h !== null && h !== cleared) issue.heardAltFt = h;
    }
    if (ac && typeof ac.alt_ft === "number" && Number.isFinite(ac.alt_ft)) {
      nowText = flightLevel(ac.alt_ft);
      if (cleared !== null) {
        const away = Math.abs(ac.alt_ft - cleared) > AT_LEVEL_FT;
        const aiming = typeof ac.target_alt_ft === "number" && Math.abs(ac.target_alt_ft - cleared) <= AT_LEVEL_FT / 2;
        // Radar already watched it fail to move; a readback alert trusts where the aircraft is aiming.
        issue.levelWrong = radar ? away : away && !aiming;
      }
    }
  } else if (kind === "route") {
    const want = fixes[fixName(item.value)];
    if (want) issue.expectedFix = want;
    if (!missing && !radar && said) {
      const got = fixes[fixName(said.value)];
      if (got && got.name !== want?.name) issue.heardFix = got;
    }
    if (radar && ac && Number.isFinite(ac.hdg_deg)) issue.heardHdg = ac.hdg_deg;
  } else if (kind === "heading") {
    const want = toNumber(item.value);
    if (want !== null) issue.expectedHdg = ((want % 360) + 360) % 360;
    if (radar) {
      const flown = ac && Number.isFinite(ac.hdg_deg) ? ac.hdg_deg : toNumber(said?.value);
      if (flown !== null && flown !== undefined) issue.heardHdg = flown;
    } else if (!missing) {
      const got = toNumber(said?.value);
      const norm = got === null ? null : ((got % 360) + 360) % 360;
      if (norm !== null && norm !== issue.expectedHdg) issue.heardHdg = norm;
    }
    if (ac && Number.isFinite(ac.hdg_deg)) nowText = `hdg ${hdgText(ac.hdg_deg)}`;
  } else if (kind === "speed") {
    if (ac && Number.isFinite(ac.gs_kt)) nowText = `${Math.round(ac.gs_kt)} kt`;
  }

  // ---- label
  const parts: string[] = [];
  if (wrongAircraft) parts.push("answered by another aircraft");
  else if (noReadback) parts.push("no readback");
  else if (ackOnly) parts.push("acknowledged only");
  parts.push(`cleared ${say(item)}`);
  if (radar) {
    if (kind === "route" || kind === "heading") {
      if (ac && Number.isFinite(ac.hdg_deg)) parts.push(`flying hdg ${hdgText(ac.hdg_deg)}`);
    } else if (nowText) parts.push(`${issue.levelWrong === false ? "now" : "still"} ${nowText}`);
  } else {
    if (!missing && said) {
      if (kind === "route") {
        const name = fixName(said.value);
        parts.push(fixes[name] ? `read back ${name}` : `read back "${name}" (not a fix here)`);
      } else {
        parts.push(`read back ${say(said).replace(/^hdg /, "")}`);
      }
    } else if (!noReadback && !ackOnly && !wrongAircraft) {
      parts.push("not read back");
    }
    if (nowText) parts.push(`${missing && issue.levelWrong ? "still" : "now"} ${nowText}`);
  }
  issue.label = cap(parts.join(SEP));
  return issue;
}
