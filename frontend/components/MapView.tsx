"use client";

/**
 * The airspace, on a real map, in three dimensions.
 *
 * MapLibre draws the world; deck.gl draws everything that flies over it. Positions arrive from the
 * backend as lat/lon (docs/08-ws-protocol.md, Geography). Altitude is real but exaggerated, because
 * 35,000 ft is about 6 NM and a sector is hundreds of NM wide: without exaggeration the third
 * dimension would be invisible.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import MapGL, { useControl, type MapRef } from "react-map-gl/maplibre";
import { MapLibreOverlay, type MapLibreOverlayProps } from "@deck.gl/maplibre";
import { IconLayer, LineLayer, PathLayer, PolygonLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import { PathStyleExtension } from "@deck.gl/extensions";
import type { Layer, PickingInfo } from "@deck.gl/core";
import type { StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import { highlightMap, useTowerDispatch, useTowerState } from "@/lib/store";
import { DEFAULT_FRAME, latLonToNm, nmToLatLon, type FrameLike } from "@/lib/geo";
import { latLonOf, shown, type Shown } from "@/lib/interp";
import type { PlannedPath, Zone } from "@/lib/types";
import { useClient } from "./TowerApp";

type DropMode = "off" | "intruder" | "storm";
type RGBA = [number, number, number, number];

const BASEMAP = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";
/** If the basemap cannot load (no network at the venue), the airspace still draws on plain ink. */
const FALLBACK_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "ink", type: "background", paint: { "background-color": "#04060a" } }],
};

const C = {
  flown: [132, 146, 162, 150] as RGBA,
  flownDim: [132, 146, 162, 70] as RGBA,
  tower: [70, 200, 255, 215] as RGBA,
  flash: [255, 176, 46, 255] as RGBA,
  aircraft: [224, 232, 242, 255] as RGBA,
  intruder: [255, 77, 94, 255] as RGBA,
  alert: [255, 77, 94, 255] as RGBA,
  resolving: [255, 176, 46, 255] as RGBA,
  watching: [34, 211, 238, 255] as RGBA,
  stem: [224, 232, 242, 60] as RGBA,
  waypoint: [160, 174, 190, 190] as RGBA,
  sector: [70, 200, 255, 90] as RGBA,
  trail: [224, 232, 242, 90] as RGBA,
  ink: [4, 6, 10, 255] as RGBA,
};

const FT_TO_M = 0.3048;
const NM_TO_M = 1852;
const TRAIL_POINTS = 48;
const TRAIL_EVERY_MS = 700;

// Two glyphs in one atlas, both pointing north: an airliner and a dart for anything uncooperative.
const ATLAS =
  "data:image/svg+xml;charset=utf-8," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="128" height="64" viewBox="0 0 128 64"><g fill="#fff">` +
      `<path d="M32 3 L36.5 22 L61 39 L61 45 L36.5 37 L35.5 52 L43.5 58 L43.5 61.5 L32 58.5 L20.5 61.5 L20.5 58 L28.5 52 L27.5 37 L3 45 L3 39 L27.5 22 Z"/>` +
      `<path d="M96 3 L117 60 L96 49 L75 60 Z"/>` +
      `</g></svg>`,
  );
const ICONS = {
  plane: { x: 0, y: 0, width: 64, height: 64, mask: true, anchorX: 32, anchorY: 32 },
  dart: { x: 64, y: 0, width: 64, height: 64, mask: true, anchorX: 32, anchorY: 32 },
};

const ALWAYS_ON_TOP = { depthCompare: "always", depthWriteEnabled: false } as const;

function DeckOverlay(props: MapLibreOverlayProps) {
  const overlay = useControl<MapLibreOverlay>(() => new MapLibreOverlay({ ...props, interleaved: false }));
  overlay.setProps(props);
  return null;
}

/**
 * The basemap is context, not content. Dim its place names and roads so the traffic, which is the
 * only thing that matters, is the brightest thing on screen.
 */
function quietBasemap(map: { getStyle(): { layers?: { id: string; type: string }[] } | undefined; setPaintProperty(id: string, prop: string, value: unknown): void }) {
  try {
    for (const layer of map.getStyle()?.layers ?? []) {
      if (layer.type === "symbol") {
        map.setPaintProperty(layer.id, "text-color", "#4a5666");
        map.setPaintProperty(layer.id, "text-halo-color", "#04060a");
        map.setPaintProperty(layer.id, "text-opacity", 0.85);
        map.setPaintProperty(layer.id, "icon-opacity", 0.35);
      } else if (layer.type === "line" && /road|highway|street|rail|tunnel|bridge/i.test(layer.id)) {
        map.setPaintProperty(layer.id, "line-opacity", 0.25);
      }
    }
  } catch {
    /* a style without these layers is fine */
  }
}

/** A circle of radius r NM around (x, y) in the sector plane, as lon/lat ring. */
function ring(frame: FrameLike, x: number, y: number, rNm: number, n = 56): [number, number][] {
  const out: [number, number][] = [];
  for (let k = 0; k <= n; k++) {
    const a = (k / n) * Math.PI * 2;
    const [lat, lon] = nmToLatLon(frame, x + Math.cos(a) * rNm, y + Math.sin(a) * rNm);
    out.push([lon, lat]);
  }
  return out;
}

function pathCoords(p: PlannedPath, frame: FrameLike, zOf: (ft: number) => number): [number, number, number][] {
  if (p.lonlat && p.lonlat.length > 1) return p.lonlat.map(([lon, lat, alt]) => [lon, lat, zOf(alt)]);
  return p.samples.map(([, x, y, alt]) => {
    const [lat, lon] = nmToLatLon(frame, x, y);
    return [lon, lat, zOf(alt)];
  });
}

export default function MapView() {
  const state = useTowerState();
  const dispatch = useTowerDispatch();
  const { send } = useClient();
  const { sim, tracks, plan, planView, flashUntil, disruptions, watching, selected, follow } = state;

  const mapRef = useRef<MapRef | null>(null);
  const [mapStyle, setMapStyle] = useState<string | StyleSpecification>(BASEMAP);
  const [loaded, setLoaded] = useState(false);
  const [now, setNow] = useState(() => performance.now());
  const [exaggeration, setExaggeration] = useState(6);
  const [dropMode, setDropMode] = useState<DropMode>("off");
  const [globe, setGlobe] = useState(false);
  const [fontReady, setFontReady] = useState(false);
  const trails = useRef(new Map<string, { at: number; pts: [number, number, number][] }>());

  const frame: FrameLike = sim?.geo ?? DEFAULT_FRAME;
  const half = sim?.geo?.half_nm ?? (sim?.sector_nm ?? 200) / 2;
  const zOf = useCallback((ft: number) => ft * FT_TO_M * exaggeration, [exaggeration]);

  // ---------------------------------------------------------------- clock for motion and pulses
  useEffect(() => {
    let raf = 0;
    let last = 0;
    const loop = (t: number) => {
      if (t - last > 33) {
        last = t;
        setNow(performance.now());
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  useEffect(() => {
    let alive = true;
    document.fonts?.ready.then(() => alive && setFontReady(true));
    return () => {
      alive = false;
    };
  }, []);

  // ---------------------------------------------------------------- camera
  const fit = useCallback(
    (pitch = 52, bearing = -14) => {
      const map = mapRef.current;
      if (!map) return;
      const sw = nmToLatLon(frame, -half, -half);
      const ne = nmToLatLon(frame, half, half);
      map.fitBounds(
        [
          [Math.min(sw[1], ne[1]), Math.min(sw[0], ne[0])],
          [Math.max(sw[1], ne[1]), Math.max(sw[0], ne[0])],
        ],
        { padding: { top: 96, bottom: 236, left: pitch === 0 ? 350 : 60, right: 440 }, pitch, bearing, duration: 1400 },
      );
    },
    [frame.lat0, frame.lon0, half], // eslint-disable-line react-hooks/exhaustive-deps
  );

  // A new world: drop the old trails and frame the new sector.
  useEffect(() => {
    trails.current.clear();
    if (loaded) fit();
  }, [sim?.world_id, loaded, fit]);

  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!map || !loaded) return;
    try {
      map.setProjection({ type: globe ? "globe" : "mercator" });
    } catch {
      /* older styles without projection support: stay flat */
    }
  }, [globe, loaded]);

  // ---------------------------------------------------------------- aircraft, smoothed
  const planes: Shown[] = useMemo(() => Object.values(tracks).map((tr) => shown(tr, now, frame)), [tracks, now, frame]);

  // trails: sample each aircraft's displayed position at a steady cadence
  for (const p of planes) {
    const t = trails.current.get(p.callsign) ?? { at: 0, pts: [] };
    if (now - t.at >= TRAIL_EVERY_MS) {
      t.at = now;
      t.pts.push([p.lon, p.lat, p.alt_ft]);
      if (t.pts.length > TRAIL_POINTS) t.pts.shift();
      trails.current.set(p.callsign, t);
    }
  }
  for (const cs of Array.from(trails.current.keys())) if (!tracks[cs]) trails.current.delete(cs);

  // follow camera
  const followed = follow && selected ? planes.find((p) => p.callsign === selected) : undefined;
  const followKey = followed ? Math.floor(now / 900) : 0;
  useEffect(() => {
    if (followed) mapRef.current?.easeTo({ center: [followed.lon, followed.lat], duration: 850, easing: (t) => t });
  }, [followKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const highlights = useMemo(() => highlightMap(state), [state]);

  // ---------------------------------------------------------------- static-ish layers
  const pathData = useMemo(() => {
    const flown = (plan?.baseline_paths ?? []).map((p) => ({ callsign: p.callsign, path: pathCoords(p, frame, zOf) }));
    const tower = (plan?.paths ?? []).map((p) => ({ callsign: p.callsign, path: pathCoords(p, frame, zOf) }));
    return { flown, tower };
  }, [plan, frame, zOf]);

  const sectorRing = useMemo(() => {
    const c: [number, number][] = [[-half, -half], [half, -half], [half, half], [-half, half], [-half, -half]];
    return [{ path: c.map(([x, y]) => { const [lat, lon] = nmToLatLon(frame, x, y); return [lon, lat, 0] as [number, number, number]; }) }];
  }, [frame, half]);

  const zoneData = useMemo(
    () => (sim?.zones ?? []).map((z: Zone) => ({ ...z, polygon: ring(frame, z.x_nm, z.y_nm, z.radius_nm) })),
    [sim?.zones, frame],
  );

  const flashSlot = Math.floor(now / 250);
  const wallNow = Date.now();
  const pulse = (now % 1300) / 1300;

  const layers: Layer[] = [
    new PathLayer({
      id: "sector",
      data: sectorRing,
      getPath: (d: { path: [number, number, number][] }) => d.path,
      getColor: C.sector,
      getWidth: 1.5,
      widthUnits: "pixels",
      extensions: [new PathStyleExtension({ dash: true })],
      getDashArray: [6, 5],
      dashJustified: true,
    }),

    new PolygonLayer({
      id: "zones",
      data: zoneData,
      getPolygon: (d: { polygon: [number, number][] }) => d.polygon,
      extruded: true,
      wireframe: true,
      getElevation: (d: Zone) => (d.kind === "intruder_buffer" ? zOf(1500) : zOf(45000)),
      getFillColor: (d: Zone) => (d.kind === "storm" ? [168, 85, 247, 46] : d.kind === "closed" ? [255, 77, 94, 40] : [255, 77, 94, 22]),
      getLineColor: (d: Zone) => (d.kind === "storm" ? [190, 130, 255, 150] : [255, 77, 94, 150]),
      updateTriggers: { getElevation: exaggeration },
    }),

    new PathLayer({
      id: "flown",
      data: pathData.flown,
      getPath: (d: { path: [number, number, number][] }) => d.path,
      getColor: planView === "today" ? C.flown : C.flownDim,
      getWidth: planView === "today" ? 2 : 1.2,
      widthUnits: "pixels",
      extensions: [new PathStyleExtension({ dash: true })],
      getDashArray: [5, 4],
      dashJustified: true,
      updateTriggers: { getColor: planView, getWidth: planView },
    }),

    new PathLayer({
      id: "tower-plan",
      data: planView === "tower" ? pathData.tower : [],
      getPath: (d: { path: [number, number, number][] }) => d.path,
      getColor: (d: { callsign: string }) => ((flashUntil[d.callsign] ?? 0) > wallNow ? C.flash : d.callsign === selected ? [255, 255, 255, 235] : C.tower),
      getWidth: (d: { callsign: string }) => ((flashUntil[d.callsign] ?? 0) > wallNow ? 4 : d.callsign === selected ? 3 : 1.8),
      widthUnits: "pixels",
      capRounded: true,
      jointRounded: true,
      updateTriggers: { getColor: [flashSlot, selected], getWidth: [flashSlot, selected] },
    }),

    new PathLayer({
      id: "intruder-paths",
      data: Object.values(disruptions).filter((d) => (d.predicted_lonlat?.length ?? 0) > 1),
      getPath: (d: { predicted_lonlat?: [number, number, number][] }) => (d.predicted_lonlat ?? []).map(([lon, lat]) => [lon, lat, zOf(30000)] as [number, number, number]),
      getColor: [255, 77, 94, 170],
      getWidth: 1.6,
      widthUnits: "pixels",
      extensions: [new PathStyleExtension({ dash: true })],
      getDashArray: [3, 3],
      updateTriggers: { getPath: exaggeration },
    }),

    new ScatterplotLayer({
      id: "waypoints",
      data: sim?.waypoints ?? [],
      getPosition: (w: { x_nm: number; y_nm: number; lat?: number; lon?: number }) => { const [lat, lon] = latLonOf(w, frame); return [lon, lat, 0]; },
      getRadius: 2.4,
      radiusUnits: "pixels",
      getFillColor: C.waypoint,
      pickable: true,
    }),
    new TextLayer({
      id: "waypoint-names",
      data: sim?.waypoints ?? [],
      getPosition: (w: { x_nm: number; y_nm: number; lat?: number; lon?: number }) => { const [lat, lon] = latLonOf(w, frame); return [lon, lat, 0]; },
      getText: (w: { name: string }) => w.name,
      getSize: 10,
      getColor: [150, 164, 180, 210],
      getPixelOffset: [0, -11],
      fontFamily: fontReady ? '"B612 Mono", ui-monospace, monospace' : "ui-monospace, monospace",
      fontSettings: { sdf: true },
      outlineWidth: 3,
      outlineColor: C.ink,
      parameters: ALWAYS_ON_TOP,
    }),

    new PathLayer({
      id: "trails",
      data: Array.from(trails.current.entries()).filter(([, t]) => t.pts.length > 1).map(([callsign, t]) => ({ callsign, path: t.pts.map(([lon, lat, ft]) => [lon, lat, zOf(ft)] as [number, number, number]) })),
      getPath: (d: { path: [number, number, number][] }) => d.path,
      getColor: C.trail,
      getWidth: 2.5,
      widthUnits: "pixels",
      capRounded: true,
    }),

    new LineLayer({
      id: "stems",
      data: planes,
      getSourcePosition: (p: Shown) => [p.lon, p.lat, 0],
      getTargetPosition: (p: Shown) => [p.lon, p.lat, zOf(p.alt_ft)],
      getColor: (p: Shown) => (p.is_intruder ? [255, 77, 94, 90] : C.stem),
      getWidth: 1,
      widthUnits: "pixels",
      updateTriggers: { getTargetPosition: exaggeration },
    }),
    new ScatterplotLayer({
      id: "ground-marks",
      data: planes,
      getPosition: (p: Shown) => [p.lon, p.lat, 0],
      getRadius: 2,
      radiusUnits: "pixels",
      getFillColor: (p: Shown) => (p.is_intruder ? [255, 77, 94, 140] : [224, 232, 242, 110]),
    }),

    new ScatterplotLayer({
      id: "rings",
      data: planes.filter((p) => highlights[p.callsign] || watching.includes(p.callsign) || p.callsign === selected),
      getPosition: (p: Shown) => [p.lon, p.lat, zOf(p.alt_ft)],
      filled: false,
      stroked: true,
      billboard: true,
      radiusUnits: "pixels",
      lineWidthUnits: "pixels",
      getLineWidth: 2,
      getRadius: (p: Shown) => (highlights[p.callsign] === "alert" ? 14 + pulse * 16 : highlights[p.callsign] === "resolving" ? 15 + pulse * 6 : 16),
      getLineColor: (p: Shown) => {
        const h = highlights[p.callsign];
        if (h === "alert") return [255, 77, 94, Math.round(255 * (1 - pulse * 0.8))] as RGBA;
        if (h === "resolving") return C.resolving;
        if (watching.includes(p.callsign)) return C.watching;
        return [255, 255, 255, 200] as RGBA;
      },
      parameters: ALWAYS_ON_TOP,
      updateTriggers: { getRadius: now, getLineColor: now, getPosition: exaggeration },
    }),

    new IconLayer({
      id: "aircraft",
      data: planes,
      iconAtlas: ATLAS,
      iconMapping: ICONS,
      getIcon: (p: Shown) => (p.is_intruder ? "dart" : "plane"),
      getPosition: (p: Shown) => [p.lon, p.lat, zOf(p.alt_ft)],
      getAngle: (p: Shown) => -p.hdg_deg,
      getSize: (p: Shown) => (p.callsign === selected ? 34 : 27),
      sizeUnits: "pixels",
      billboard: false,
      getColor: (p: Shown) => (p.is_intruder ? C.intruder : highlights[p.callsign] === "alert" ? C.alert : C.aircraft),
      pickable: true,
      parameters: ALWAYS_ON_TOP,
      updateTriggers: { getPosition: exaggeration, getSize: selected, getColor: [highlights] },
    }),
    new TextLayer({
      id: "data-blocks",
      data: planes,
      getPosition: (p: Shown) => [p.lon, p.lat, zOf(p.alt_ft)],
      getText: (p: Shown) => `${p.callsign}\nFL${String(Math.round(p.alt_ft / 100)).padStart(3, "0")} ${Math.round(p.gs_kt)}`,
      getSize: 11,
      getColor: (p: Shown) => (p.is_intruder ? C.intruder : [224, 232, 242, 245]),
      getPixelOffset: [20, -4],
      getTextAnchor: "start",
      getAlignmentBaseline: "center",
      lineHeight: 1.15,
      fontFamily: fontReady ? '"B612 Mono", ui-monospace, monospace' : "ui-monospace, monospace",
      fontSettings: { sdf: true },
      outlineWidth: 4,
      outlineColor: C.ink,
      parameters: ALWAYS_ON_TOP,
      updateTriggers: { getPosition: exaggeration },
    }),
  ];

  // ---------------------------------------------------------------- interaction
  const onDeckClick = useCallback(
    (info: PickingInfo) => {
      if (info.layer?.id === "aircraft" && info.object) {
        dispatch({ type: "select", callsign: (info.object as Shown).callsign });
        return true;
      }
      if (dropMode !== "off" && info.coordinate) {
        const [lon, lat] = info.coordinate as [number, number];
        const [x, y] = latLonToNm(frame, lat, lon);
        send({ type: "add_disruption", kind: dropMode, x_nm: Math.round(x * 10) / 10, y_nm: Math.round(y * 10) / 10 });
        setDropMode("off");
        return true;
      }
      if (selected) dispatch({ type: "select", callsign: null });
      return false;
    },
    [dispatch, dropMode, frame, send, selected],
  );

  const tooltip = useCallback((info: PickingInfo) => {
    if (!info.object) return null;
    const o = info.object as Partial<Shown> & { name?: string };
    const text = info.layer?.id === "aircraft"
      ? `${o.callsign}  ${o.actype ?? ""}\nFL${Math.round((o.alt_ft ?? 0) / 100)}  ${Math.round(o.gs_kt ?? 0)} kt  hdg ${Math.round(o.hdg_deg ?? 0)}`
      : (o.name ?? "");
    return text
      ? { text, style: { background: "rgba(8,11,17,0.92)", color: "#dbe3ec", border: "1px solid rgba(70,200,255,0.25)", borderRadius: "6px", fontFamily: "var(--font-mono)", fontSize: "11px", padding: "6px 8px", whiteSpace: "pre" } }
      : null;
  }, []);

  const chip = (active: boolean, tone: "accent" | "bad" | "violet" = "accent") => {
    const on = { accent: "bg-accent/20 text-accent border-accent/50", bad: "bg-bad/20 text-bad border-bad/50", violet: "bg-purple-500/20 text-purple-300 border-purple-400/50" }[tone];
    return `px-2.5 py-1 rounded-md border text-xs font-medium transition-colors ${active ? on : "bg-panel-2/70 text-muted border-line hover:text-fg"}`;
  };

  return (
    <div className={`absolute inset-0 ${dropMode !== "off" ? "cursor-crosshair" : ""}`}>
      <MapGL
        ref={mapRef}
        mapStyle={mapStyle}
        initialViewState={{ longitude: frame.lon0, latitude: frame.lat0, zoom: 5.4, pitch: 52, bearing: -14 }}
        maxPitch={78}
        attributionControl={{ compact: true }}
        onLoad={(e) => {
          quietBasemap(e.target);
          setLoaded(true);
        }}
        onError={() => {
          // Basemap unreachable: keep working on plain ink rather than a blank screen.
          if (mapStyle === BASEMAP && !loaded) setMapStyle(FALLBACK_STYLE);
        }}
        style={{ width: "100%", height: "100%" }}
      >
        <DeckOverlay layers={layers} onClick={onDeckClick} getTooltip={tooltip} getCursor={({ isHovering }) => (dropMode !== "off" ? "crosshair" : isHovering ? "pointer" : "grab")} />
      </MapGL>

      {/* drop a disruption */}
      <div className="glass absolute left-2 top-[68px] flex items-center gap-2 px-2.5 py-2">
        <span className="eyebrow">Disrupt</span>
        <button className={chip(dropMode === "intruder", "bad")} onClick={() => setDropMode(dropMode === "intruder" ? "off" : "intruder")}>Intruder</button>
        <button className={chip(dropMode === "storm", "violet")} onClick={() => setDropMode(dropMode === "storm" ? "off" : "storm")}>Storm</button>
        <span className="text-[11px] text-muted pl-1">{dropMode === "off" ? `${planes.length} aircraft` : "click the map to place it"}</span>
      </div>

      {/* view */}
      <div className="glass absolute left-2 bottom-[196px] flex flex-col gap-2 px-2.5 py-2 w-[320px]">
        <div className="flex items-center gap-2">
          <span className="eyebrow">View</span>
          <button className={chip(false)} onClick={() => fit(52, -14)}>Tilt</button>
          <button className={chip(false)} onClick={() => fit(0, 0)}>Top down</button>
          <button className={chip(globe)} onClick={() => setGlobe((g) => !g)}>Globe</button>
        </div>
        <label className="flex items-center gap-2 text-[11px] text-muted">
          <span className="eyebrow w-[70px]">Altitude</span>
          <input type="range" min={1} max={14} step={1} value={exaggeration} onChange={(e) => setExaggeration(Number(e.target.value))} className="flex-1" />
          <span className="font-mono w-7 text-right text-fg/80">{exaggeration}x</span>
        </label>
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] font-mono text-muted pt-0.5">
          <span><span style={{ color: "rgb(132,146,162)" }}>╌╌</span> standard</span>
          <span><span style={{ color: "rgb(70,200,255)" }}>──</span> Tower</span>
          <span><span style={{ color: "rgb(255,176,46)" }}>──</span> replanned</span>
          <span><span style={{ color: "rgb(255,77,94)" }}>◯</span> alert</span>
          <span><span style={{ color: "rgb(255,176,46)" }}>◯</span> checking</span>
          <span><span style={{ color: "rgb(34,211,238)" }}>◯</span> watching</span>
        </div>
        <p className="text-[10px] text-muted/80">Drag to pan, scroll to zoom, right-drag to tilt and rotate.</p>
      </div>
    </div>
  );
}
