/**
 * Flat plane <-> Earth. Mirrors backend/sim/geoframe.py: azimuthal equidistant on a sphere.
 * Sector convention: x east, y north, nautical miles, centred on the frame.
 *
 * The backend sends lat and lon on every position; this exists for mock mode, for older backends,
 * and for turning a click on the map back into sector NM.
 */

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
