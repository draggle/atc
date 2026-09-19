"use client";

import { useEffect, useRef, useState } from "react";
import { highlightMap, useTowerState, type TowerState, type Track } from "@/lib/store";
import type { AircraftState } from "@/lib/types";
import { makeProjection, nmToPx, toNm, toPx, type Projection } from "@/lib/geo";
import { useClient } from "./TowerApp";

type DropMode = "intruder" | "storm";

const COLORS = {
  grid: "rgba(63,184,255,0.06)",
  border: "rgba(63,184,255,0.25)",
  waypoint: "rgba(215,221,230,0.55)",
  route: "rgba(215,221,230,0.10)",
  plan: "rgba(63,184,255,0.55)",
  baseline: "rgba(215,221,230,0.35)",
  flash: "rgba(245,185,66,0.95)",
  aircraft: "#d7dde6",
  intruder: "#ff4d5e",
  alert: "#ff4d5e",
  resolving: "#f5b942",
  watching: "#22d3ee",
  stem: "rgba(215,221,230,0.18)",
  storm: "rgba(168,85,247,0.18)",
  stormEdge: "rgba(168,85,247,0.55)",
  closed: "rgba(255,77,94,0.12)",
  closedEdge: "rgba(255,77,94,0.5)",
  buffer: "rgba(255,77,94,0.08)",
};

/** Longest we dead-reckon past the last radar tick before freezing the target (wall seconds). */
const MAX_EXTRAP_S = 1.5;
/** Sim seconds per wall second is estimated per track; clamp against a bad first sample. */
const MAX_RATE = 16;

function lerpAngle(a: number, b: number, f: number): number {
  const d = ((b - a + 540) % 360) - 180;
  return (a + d * f + 360) % 360;
}

/**
 * Position to draw right now. Between the last two ticks we interpolate prev -> cur (one tick
 * of display latency, no snap-back); once past cur we extrapolate along heading and ground speed
 * for at most MAX_EXTRAP_S wall seconds.
 */
function displayed(tr: Track, now: number): AircraftState {
  const { cur, prev } = tr;
  const elapsed = (now - tr.curAt) / 1000;
  if (!prev || cur.gs_kt <= 0) return cur;
  const tickWall = (tr.curAt - tr.prevAt) / 1000;
  const tickSim = cur.t - prev.t;
  if (tickWall <= 0 || tickSim <= 0) return cur;
  const rate = Math.min(MAX_RATE, tickSim / tickWall);
  if (elapsed < tickWall) {
    const f = elapsed / tickWall;
    return {
      ...cur,
      x_nm: prev.x_nm + (cur.x_nm - prev.x_nm) * f,
      y_nm: prev.y_nm + (cur.y_nm - prev.y_nm) * f,
      alt_ft: prev.alt_ft + (cur.alt_ft - prev.alt_ft) * f,
      hdg_deg: lerpAngle(prev.hdg_deg, cur.hdg_deg, f),
    };
  }
  const extra = Math.min(MAX_EXTRAP_S, elapsed - tickWall) * rate;
  const dist = (cur.gs_kt / 3600) * extra;
  const rad = (cur.hdg_deg * Math.PI) / 180;
  return { ...cur, x_nm: cur.x_nm + Math.sin(rad) * dist, y_nm: cur.y_nm + Math.cos(rad) * dist };
}

function draw(ctx: CanvasRenderingContext2D, w: number, h: number, s: TowerState, now: number) {
  ctx.clearRect(0, 0, w, h);
  const sector = s.sim?.sector_nm ?? 200;
  const p: Projection = makeProjection(w, h, sector);
  const hl = highlightMap(s);
  const watching = new Set(s.watching);
  const shown: AircraftState[] = Object.values(s.aircraft).map((a) => {
    const tr = s.tracks[a.callsign];
    return tr ? displayed(tr, now) : a;
  });

  // Sector square and grid
  ctx.save();
  ctx.strokeStyle = COLORS.border;
  ctx.lineWidth = 1;
  ctx.strokeRect(p.ox, p.oy, p.size, p.size);
  ctx.strokeStyle = COLORS.grid;
  const stepNm = sector / 10;
  for (let i = 1; i < 10; i++) {
    const [gx] = toPx(p, i * stepNm, 0);
    const [, gy] = toPx(p, 0, i * stepNm);
    ctx.beginPath();
    ctx.moveTo(gx, p.oy);
    ctx.lineTo(gx, p.oy + p.size);
    ctx.moveTo(p.ox, gy);
    ctx.lineTo(p.ox + p.size, gy);
    ctx.stroke();
  }
  ctx.fillStyle = "rgba(124,135,148,0.6)";
  ctx.font = "10px ui-monospace, monospace";
  ctx.fillText(`${sector} NM`, p.ox + 4, p.oy + p.size - 4);
  ctx.restore();

  // Zones (state zones + storm/closed disruptions)
  const zones = [...(s.sim?.zones ?? [])];
  for (const d of Object.values(s.disruptions)) {
    if (d.kind === "storm" || d.kind === "closed") {
      if (!zones.some((z) => z.id === d.id)) zones.push({ id: d.id, x_nm: d.x_nm, y_nm: d.y_nm, radius_nm: d.radius_nm, kind: d.kind });
    }
  }
  for (const z of zones) {
    const [zx, zy] = toPx(p, z.x_nm, z.y_nm);
    const r = nmToPx(p, z.radius_nm);
    ctx.beginPath();
    ctx.arc(zx, zy, r, 0, Math.PI * 2);
    ctx.fillStyle = z.kind === "storm" ? COLORS.storm : z.kind === "closed" ? COLORS.closed : COLORS.buffer;
    ctx.fill();
    ctx.strokeStyle = z.kind === "storm" ? COLORS.stormEdge : COLORS.closedEdge;
    ctx.setLineDash(z.kind === "intruder_buffer" ? [4, 4] : []);
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(215,221,230,0.6)";
    ctx.font = "10px ui-sans-serif, system-ui";
    ctx.textAlign = "center";
    ctx.fillText(z.kind.toUpperCase(), zx, zy + 3);
    ctx.textAlign = "left";
  }

  // Waypoints and faint route lines between consecutive route waypoints of each aircraft
  const wpMap = new Map((s.sim?.waypoints ?? []).map((wp) => [wp.name, wp]));
  ctx.strokeStyle = COLORS.route;
  ctx.lineWidth = 1;
  for (const a of shown) {
    if (a.is_intruder || a.route.length === 0) continue;
    ctx.beginPath();
    const [ax, ay] = toPx(p, a.x_nm, a.y_nm);
    ctx.moveTo(ax, ay);
    for (const name of a.route) {
      const wp = wpMap.get(name);
      if (!wp) continue;
      const [wx, wy] = toPx(p, wp.x_nm, wp.y_nm);
      ctx.lineTo(wx, wy);
    }
    ctx.stroke();
  }
  for (const wp of wpMap.values()) {
    const [wx, wy] = toPx(p, wp.x_nm, wp.y_nm);
    ctx.beginPath();
    ctx.arc(wx, wy, 2.5, 0, Math.PI * 2);
    ctx.fillStyle = COLORS.waypoint;
    ctx.fill();
    ctx.fillStyle = "rgba(124,135,148,0.9)";
    ctx.font = "10px ui-monospace, monospace";
    ctx.fillText(wp.name, wx + 5, wy - 4);
  }

  // Planned paths (Tower plan or Today baseline)
  const paths = s.planView === "tower" ? s.plan?.paths : (s.plan?.baseline_paths ?? s.plan?.paths);
  if (paths) {
    for (const path of paths) {
      if (path.samples.length < 2) continue;
      const flashEnd = s.flashUntil[path.callsign] ?? 0;
      const flashing = flashEnd > now;
      const phase = flashing ? 0.5 + 0.5 * Math.sin(now / 90) : 0;
      ctx.beginPath();
      path.samples.forEach(([, x, y], i) => {
        const [px, py] = toPx(p, x, y);
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.strokeStyle = flashing ? COLORS.flash : s.planView === "tower" ? COLORS.plan : COLORS.baseline;
      ctx.globalAlpha = flashing ? 0.4 + 0.6 * phase : 1;
      ctx.lineWidth = flashing ? 2 : 1;
      ctx.setLineDash(s.planView === "today" ? [6, 4] : []);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }
  }

  // Intruder predicted paths
  for (const d of Object.values(s.disruptions)) {
    if (d.kind !== "intruder" || d.predicted_path.length < 2) continue;
    ctx.beginPath();
    d.predicted_path.forEach(([, x, y], i) => {
      const [px, py] = toPx(p, x, y);
      if (i === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    });
    ctx.strokeStyle = COLORS.intruder;
    ctx.globalAlpha = 0.6;
    ctx.setLineDash([6, 6]);
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
  }

  // Aircraft
  ctx.font = "11px ui-monospace, monospace";
  for (const a of shown) {
    const [ax, ay] = toPx(p, a.x_nm, a.y_nm);
    const state = hl[a.callsign];
    const color = a.is_intruder ? COLORS.intruder : state === "alert" ? COLORS.alert : state === "resolving" ? COLORS.resolving : COLORS.aircraft;

    // 2.5D stem: length scales with altitude
    const stem = Math.min(26, (a.alt_ft / 40000) * 26);
    ctx.strokeStyle = COLORS.stem;
    ctx.beginPath();
    ctx.moveTo(ax, ay + stem);
    ctx.lineTo(ax, ay);
    ctx.stroke();
    ctx.beginPath();
    ctx.ellipse(ax, ay + stem, 4, 1.6, 0, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(215,221,230,0.25)";
    ctx.stroke();

    // Rings: red alert / amber resolving win; cyan dashed "radar watching" only when neither applies.
    if (!state && !a.is_intruder && watching.has(a.callsign)) {
      ctx.beginPath();
      ctx.arc(ax, ay, 12, 0, Math.PI * 2);
      ctx.strokeStyle = COLORS.watching;
      ctx.setLineDash([3, 3]);
      ctx.lineDashOffset = -(now / 60) % 6;
      ctx.lineWidth = 1;
      ctx.globalAlpha = 0.8;
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.lineDashOffset = 0;
      ctx.globalAlpha = 1;
    }
    if (state || a.is_intruder) {
      const pulse = 0.5 + 0.5 * Math.sin(now / 250);
      ctx.beginPath();
      ctx.arc(ax, ay, 10 + pulse * 4, 0, Math.PI * 2);
      ctx.strokeStyle = color;
      ctx.globalAlpha = 0.35 + 0.35 * pulse;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }

    // Triangle rotated to heading (0 = north/up)
    ctx.save();
    ctx.translate(ax, ay);
    ctx.rotate((a.hdg_deg * Math.PI) / 180);
    ctx.beginPath();
    ctx.moveTo(0, -7);
    ctx.lineTo(5, 6);
    ctx.lineTo(0, 3);
    ctx.lineTo(-5, 6);
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
    ctx.restore();

    // Data block
    const altH = Math.round(a.alt_ft / 100);
    const tgt = Math.round(a.target_alt_ft / 100);
    const arrow = tgt > altH ? "↑" : tgt < altH ? "↓" : "";
    const line1 = a.callsign;
    const line2 = `${String(altH).padStart(3, "0")}${arrow} ${Math.round(a.gs_kt)}`;
    ctx.fillStyle = color;
    ctx.fillText(line1, ax + 10, ay - 4);
    ctx.fillStyle = a.is_intruder || state ? color : "rgba(215,221,230,0.7)";
    ctx.fillText(line2, ax + 10, ay + 8);
    ctx.strokeStyle = "rgba(215,221,230,0.2)";
    ctx.beginPath();
    ctx.moveTo(ax + 3, ay);
    ctx.lineTo(ax + 9, ay - 2);
    ctx.stroke();
  }
}

export default function Radar() {
  const state = useTowerState();
  const { send } = useClient();
  const stateRef = useRef(state);
  stateRef.current = state;
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [mode, setMode] = useState<DropMode>("intruder");
  const [hover, setHover] = useState<[number, number] | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    let raf = 0;
    let w = 0;
    let h = 0;
    const resize = () => {
      const r = wrap.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      w = Math.max(1, Math.floor(r.width));
      h = Math.max(1, Math.floor(r.height));
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);
    resize();
    const loop = () => {
      draw(ctx, w, h, stateRef.current, performance.now());
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, []);

  const pointToNm = (e: React.MouseEvent<HTMLCanvasElement>): [number, number] | null => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const r = canvas.getBoundingClientRect();
    const p = makeProjection(r.width, r.height, stateRef.current.sim?.sector_nm ?? 200);
    const [x, y] = toNm(p, e.clientX - r.left, e.clientY - r.top);
    return [x, y];
  };

  const onClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const pt = pointToNm(e);
    if (!pt) return;
    const sector = stateRef.current.sim?.sector_nm ?? 200;
    if (pt[0] < 0 || pt[1] < 0 || pt[0] > sector || pt[1] > sector) return;
    send({ type: "add_disruption", kind: mode, x_nm: Math.round(pt[0] * 10) / 10, y_nm: Math.round(pt[1] * 10) / 10 });
  };

  const n = Object.keys(state.aircraft).length;
  const towerOff = state.sim !== null && !state.sim.tower_enabled;

  return (
    <div className="panel h-full w-full relative overflow-hidden" ref={wrapRef}>
      <canvas
        ref={canvasRef}
        className="block cursor-crosshair"
        onClick={onClick}
        onMouseMove={(e) => setHover(pointToNm(e))}
        onMouseLeave={() => setHover(null)}
      />
      {towerOff && (
        <div
          role="status"
          className="absolute inset-x-0 top-0 z-10 flex items-center justify-center gap-3 bg-zinc-700/90 border-b-2 border-zinc-400 py-1.5 text-sm font-semibold tracking-wide text-zinc-100"
        >
          <span className="inline-block w-2.5 h-2.5 rounded-full bg-zinc-300" />
          TOWER OFF: readbacks are not being checked
        </div>
      )}
      <div className={`absolute left-2 flex items-center gap-2 text-xs ${towerOff ? "top-11" : "top-2"}`}>
        <span className="text-muted">Click to drop</span>
        <div className="flex rounded-md border border-line overflow-hidden">
          {(["intruder", "storm"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`px-2 py-0.5 capitalize ${mode === m ? (m === "intruder" ? "bg-bad/20 text-bad" : "bg-purple-500/20 text-purple-300") : "bg-panel-2 text-muted hover:text-fg"}`}
            >
              {m}
            </button>
          ))}
        </div>
      </div>
      <div className={`absolute right-2 text-[10px] font-mono text-muted flex gap-3 ${towerOff ? "top-11" : "top-2"}`}>
        <span>{n} aircraft</span>
        {hover && <span>{hover[0].toFixed(0)}, {hover[1].toFixed(0)} NM</span>}
      </div>
      <div className="absolute bottom-2 right-2 text-[10px] text-muted flex gap-3">
        <span><span className="inline-block w-3 h-px bg-accent align-middle mr-1" />plan</span>
        <span><span className="inline-block w-3 border-t border-dashed border-fg/50 align-middle mr-1" />today</span>
        <span className="text-warn">flash = replanned</span>
        <span className="text-bad">red = alert</span>
        <span className="text-warn">amber = checking</span>
        <span className="text-cyan-400">cyan = radar watching</span>
      </div>
    </div>
  );
}
