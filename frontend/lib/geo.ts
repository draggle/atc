/** Sector NM <-> canvas pixel mapping. x east, y north, sector square centred on the origin (NM from -sector/2 to +sector/2). */
export interface Projection {
  size: number; // px of the square
  ox: number; // px offset of the square within the canvas
  oy: number;
  sector: number; // NM
}

export function makeProjection(width: number, height: number, sector: number): Projection {
  const pad = 16;
  const size = Math.max(10, Math.min(width, height) - pad * 2);
  return { size, ox: (width - size) / 2, oy: (height - size) / 2, sector };
}

export function toPx(p: Projection, xNm: number, yNm: number): [number, number] {
  const s = p.size / p.sector;
  const h = p.sector / 2;
  return [p.ox + (xNm + h) * s, p.oy + p.size - (yNm + h) * s];
}

export function toNm(p: Projection, xPx: number, yPx: number): [number, number] {
  const s = p.sector / p.size;
  const h = p.sector / 2;
  return [(xPx - p.ox) * s - h, (p.oy + p.size - yPx) * s - h];
}

export const nmToPx = (p: Projection, nm: number) => (nm * p.size) / p.sector;


// ---------------------------------------------------------------------------
// Flat plane <-> Earth. Mirrors backend/sim/geoframe.py: azimuthal equidistant on a sphere.
// The backend sends lat and lon on every position; this exists for mock mode and for turning a
// click on the map back into sector NM.
// ---------------------------------------------------------------------------

export const EARTH_RADIUS_NM = 3440.065;
const RAD = Math.PI / 180;

export interface FrameLike {
  lat0: number;
  lon0: number;
}

export const DEFAULT_FRAME: FrameLike & { projection: "aeqd"; name: string } = {
  lat0: 43.6777,
  lon0: -79.6248,
  projection: "aeqd",
  name: "Toronto Pearson (CYYZ)",
};

/** Sector NM (x east, y north) -> [lat, lon] in degrees. */
export function nmToLatLon(frame: FrameLike, xNm: number, yNm: number): [number, number] {
  const rho = Math.hypot(xNm, yNm);
  if (rho < 1e-12) return [frame.lat0, frame.lon0];
  const phi0 = frame.lat0 * RAD;
  const c = rho / EARTH_RADIUS_NM;
  const sinC = Math.sin(c);
  const cosC = Math.cos(c);
  const phi = Math.asin(Math.min(1, Math.max(-1, cosC * Math.sin(phi0) + (yNm * sinC * Math.cos(phi0)) / rho)));
  const lam = frame.lon0 * RAD + Math.atan2(xNm * sinC, rho * Math.cos(phi0) * cosC - yNm * Math.sin(phi0) * sinC);
  const lon = ((((lam / RAD + 180) % 360) + 360) % 360) - 180;
  return [phi / RAD, lon];
}

/** [lat, lon] in degrees -> sector NM [x east, y north]. */
export function latLonToNm(frame: FrameLike, lat: number, lon: number): [number, number] {
  const phi = lat * RAD;
  const phi0 = frame.lat0 * RAD;
  const dlam = (lon - frame.lon0) * RAD;
  const cosC = Math.min(1, Math.max(-1, Math.sin(phi0) * Math.sin(phi) + Math.cos(phi0) * Math.cos(phi) * Math.cos(dlam)));
  const c = Math.acos(cosC);
  const sinC = Math.sin(c);
  const k = sinC > 1e-12 ? c / sinC : 1;
  return [
    EARTH_RADIUS_NM * k * Math.cos(phi) * Math.sin(dlam),
    EARTH_RADIUS_NM * k * (Math.cos(phi0) * Math.sin(phi) - Math.sin(phi0) * Math.cos(phi) * Math.cos(dlam)),
  ];
}
