/** Sector NM <-> canvas pixel mapping. x east, y north, origin bottom-left of the sector square. */
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
  return [p.ox + xNm * s, p.oy + p.size - yNm * s];
}

export function toNm(p: Projection, xPx: number, yPx: number): [number, number] {
  const s = p.sector / p.size;
  return [(xPx - p.ox) * s, (p.oy + p.size - yPx) * s];
}

export const nmToPx = (p: Projection, nm: number) => (nm * p.size) / p.sector;
