/**
 * Smooth motion between radar frames, in real-world coordinates.
 *
 * The backend sends one radar frame per tick. Between the last two frames we interpolate
 * prev -> cur (one tick of display latency, no snap-back). Past cur we dead-reckon along heading
 * and ground speed for a short time, then hold.
 */
import { DEFAULT_FRAME, nmToLatLon, type FrameLike } from "./geo";
import type { Track } from "./store";
import type { AircraftState } from "./types";

/** Longest we dead-reckon past the last frame before freezing the target (wall seconds). */
const MAX_EXTRAP_S = 1.5;
/** Sim seconds per wall second is estimated per track; clamp against a bad first sample. */
const MAX_RATE = 130;

export interface Shown extends AircraftState {
  lat: number;
  lon: number;
}

/** Real-world position of a state. Older backends send no lat/lon, so fall back to the frame. */
export function latLonOf(a: { x_nm: number; y_nm: number; lat?: number; lon?: number }, frame?: FrameLike | null): [number, number] {
  if (typeof a.lat === "number" && typeof a.lon === "number") return [a.lat, a.lon];
  return nmToLatLon(frame ?? DEFAULT_FRAME, a.x_nm, a.y_nm);
}

function lerpAngle(a: number, b: number, f: number): number {
  const d = ((b - a + 540) % 360) - 180;
  return (a + d * f + 360) % 360;
}

export function shown(tr: Track, now: number, frame?: FrameLike | null): Shown {
  const { cur, prev } = tr;
  const [clat, clon] = latLonOf(cur, frame);
  const base: Shown = { ...cur, lat: clat, lon: clon };
  if (!prev || cur.gs_kt <= 0) return base;
  const tickWall = (tr.curAt - tr.prevAt) / 1000;
  const tickSim = cur.t - prev.t;
  if (tickWall <= 0 || tickSim <= 0) return base;
  const elapsed = (now - tr.curAt) / 1000;
  if (elapsed < tickWall) {
    const f = Math.max(0, elapsed / tickWall);
    const [plat, plon] = latLonOf(prev, frame);
    return {
      ...cur,
      lat: plat + (clat - plat) * f,
      lon: plon + (clon - plon) * f,
      x_nm: prev.x_nm + (cur.x_nm - prev.x_nm) * f,
      y_nm: prev.y_nm + (cur.y_nm - prev.y_nm) * f,
      alt_ft: prev.alt_ft + (cur.alt_ft - prev.alt_ft) * f,
      hdg_deg: lerpAngle(prev.hdg_deg, cur.hdg_deg, f),
    };
  }
  // Past the newest frame: dead-reckon briefly so a late frame does not read as a stall.
  const rate = Math.min(MAX_RATE, tickSim / tickWall);
  const dt = Math.min(MAX_EXTRAP_S, elapsed - tickWall) * rate;
  const nm = (cur.gs_kt * dt) / 3600;
  const h = (cur.hdg_deg * Math.PI) / 180;
  const dLat = (nm * Math.cos(h)) / 60;
  const dLon = (nm * Math.sin(h)) / (60 * Math.max(0.2, Math.cos((clat * Math.PI) / 180)));
  return { ...base, lat: clat + dLat, lon: clon + dLon };
}
