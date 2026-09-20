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
import type { Map as MapLibreMap, StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import { alertFor, highlightMap, useTowerDispatch, useTowerState, visibleRisk } from "@/lib/store";
import { DEFAULT_FRAME, destinationPoint, latLonToNm, nmToLatLon, type FrameLike } from "@/lib/geo";
import { latLonOf, shown, type Shown } from "@/lib/interp";
import { describeIssue, flightLevel, type IssueFix } from "@/lib/issue";
import type { Disruption, DisruptionKind, DisruptionKindInfo, PlannedPath, RiskPair, Zone } from "@/lib/types";
import FlightStrip from "./FlightStrip";
import { useClient } from "./TowerApp";

type DropMode = "off" | DisruptionKind;

/** Used until the backend sends its own menu in `state.disruption_kinds` (and by the mock). */
const KINDS: DisruptionKindInfo[] = [
  { kind: "fighter", label: "Fighter jet", blurb: "Fast, straight through, not talking to anyone.", shape: "point" },
  { kind: "drone", label: "Drone", blurb: "Slow and small, loitering at cruise level.", shape: "point", menu: false },
  { kind: "balloon", label: "Balloon", blurb: "Drifting with the wind.", shape: "point", menu: false },
  { kind: "emergency", label: "Emergency aircraft", blurb: "One of our flights declares a mayday and descends.", shape: "point" },
  { kind: "unknown", label: "Unknown target", blurb: "No height, no identity. Blocked at every level.", shape: "point", menu: false },
  { kind: "storm", label: "Storm cell", blurb: "Drifts and swells.", shape: "circle" },
  { kind: "closed", label: "Closed airspace", blurb: "A block of levels shut for a while.", shape: "circle", menu: false },
  { kind: "rocket", label: "Rocket launch", blurb: "A tall column, gone in minutes.", shape: "circle" },
];
const ALL_LEVELS_FT = 90000;
type RGBA = [number, number, number, number];

const BASEMAP = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";
/** If the basemap cannot load (no network at the venue), the airspace still draws on plain ink. */
const FALLBACK_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "ink", type: "background", paint: { "background-color": "#0a0a0b" } }],
};

// squack: white ink at different opacities. Colour only when it means something: green = live or
// correct, red = wrong or a predicted conflict, amber = changed or being checked. Values match the
// tokens in globals.css (--ok #3ddc84, --warn #f5b83d, --bad #ff5a6e, --bg #0a0a0b, --muted #8a8f98).
const WHITE = [255, 255, 255] as const;
const RED = [255, 90, 110] as const;
const AMBER = [245, 184, 61] as const;
const GREEN = [61, 220, 132] as const;
const MUTED = [138, 143, 152] as const;
const white = (a: number): RGBA => [WHITE[0], WHITE[1], WHITE[2], Math.round(255 * a)];
const C = {
  flown: white(0.22),
  flownDim: white(0.14),
  tower: white(0.85),
  flash: [...AMBER, 255] as RGBA,
  rerouted: [...AMBER, 190] as RGBA, // still going round something that is still there
  aircraft: white(1),
  intruder: [...RED, 255] as RGBA,
  mayday: [...AMBER, 255] as RGBA,
  alert: [...RED, 255] as RGBA,
  resolving: [...AMBER, 255] as RGBA,
  watching: white(0.5),
  stem: white(0.2),
  waypoint: white(0.45),
  gate: white(0.8),
  sector: white(0.3),
  trail: white(0.28),
  ghost: white(0.12),
  ink: [10, 10, 11, 255] as RGBA,
  muted: [...MUTED, 255] as RGBA,
  zoneFill: white(0.06),
  zoneLine: white(0.3),
  onAir: GREEN,
  // The issue drawn beside a selected aircraft with a standing alert: white is what was cleared,
  // red is what was read back or flown instead.
  cleared: white(1),
  wrong: [...RED, 255] as RGBA,
  warn: [...AMBER, 255] as RGBA,
  pill: [10, 10, 11, 235] as RGBA,
  risk: [RED[0], RED[1], RED[2]] as [number, number, number],
};

const FT_TO_M = 0.3048;
/** "Understood" on the aircraft: how long the chip stays, and the last part of that it fades over. */
const ACK_SHOWS_MS = 7000;
const ACK_FADES_MS = 1500;
/** The cleared-heading vector drawn from an aircraft on an assigned heading: this many minutes of flight. */
const CLEARED_VECTOR_MIN = 2.5;
const angleBetween = (a: number, b: number) => Math.abs(((a - b + 540) % 360) - 180);
/** Cleared heading and level as a radar data block shows them, only while they differ from what is flown. */
function clearedLine(p: { hdg_deg: number; target_hdg_deg: number | null; alt_ft: number; target_alt_ft: number; manoeuvre?: string | null }): string {
  const out: string[] = [];
  if (p.manoeuvre) out.push(`⟳ ${p.manoeuvre.toUpperCase()}`);
  if (p.target_hdg_deg != null) out.push(`H${String(Math.round(p.target_hdg_deg) % 360 || 360).padStart(3, "0")}`);
  if (Math.abs(p.target_alt_ft - p.alt_ft) > 150) out.push(`${p.target_alt_ft > p.alt_ft ? "↑" : "↓"}${String(Math.round(p.target_alt_ft / 100)).padStart(3, "0")}`);
  return out.join(" ");
}
/** Hover text for a predicted-conflict wedge. */
function riskTip(p: RiskPair): string {
  const eta = p.t_first_s ?? p.eta_s;
  return `${p.a} / ${p.b}: predicted conflict\nLoS ${Math.round(p.p_max * 100)}% in ${Math.round(eta)} s\nmin separation (p5) ${p.min_sep_nm_p5.toFixed(1)} NM`;
}
/** Length of the cleared / read-back heading vectors. */
const HDG_VECTOR_NM = 25;
/** Focus fly-in: how close, how long, and the part of the screen the panels leave free (the top is deeper because altitude lifts everything). */
const FOCUS_ZOOM = 7.5;
const FOCUS_MIN_ZOOM = 5.6;
const FOCUS_MS = 1200;
const FOCUS_PADDING = { top: 210, bottom: 250, left: 380, right: 470 };
/** Web-mercator world coordinates in [0, 1], y down. */
const mercator = (lon: number, lat: number): [number, number] => [
  (lon + 180) / 360,
  (1 - Math.log(Math.tan(Math.PI / 4 + (Math.max(-85, Math.min(85, lat)) * Math.PI) / 360)) / Math.PI) / 2,
];
/** TextLayers default to ASCII only; the issue label also needs the separator and the ellipsis. */
const ISSUE_CHARS = Array.from({ length: 95 }, (_, i) => String.fromCharCode(32 + i)).join("") + "·…";

type IssueSeg = { path: [number, number, number][]; color: RGBA; width: number };
/** One aircraft's half of a predicted conflict: a wedge from where it is to the closest-approach point. */
type RiskCone = { key: string; callsign: string; pair: RiskPair; fade: number; polygon: [number, number, number][] };
type RiskLabel = { key: string; pair: RiskPair; fade: number; position: [number, number, number]; text: string };
/** Cone width, NM: half-width at the aircraft, and the least half-width at the CPA (the rollouts' spread when wider). */
const CONE_HALF_AT_AIRCRAFT_NM = 0.5;
const CONE_MIN_HALF_AT_CPA_NM = 1.5;

/**
 * The wedge as a 4-point polygon in lon/lat: narrow at the aircraft, as wide as the rollouts spread
 * at the closest approach. The bearing is taken in the sector frame (both ends are known there),
 * the corners are placed on the sphere so the shape is right at any latitude.
 */
function coneAt(frame: FrameLike, from: { lat: number; lon: number; x_nm: number; y_nm: number }, cpaNm: [number, number], spreadNm: number, z: number): [number, number, number][] {
  const [cpaLat, cpaLon] = nmToLatLon(frame, cpaNm[0], cpaNm[1]);
  const [fx, fy] = latLonToNm(frame, from.lat, from.lon);
  const bearing = (Math.atan2(cpaNm[0] - fx, cpaNm[1] - fy) * 180) / Math.PI;
  const halfCpa = Math.max(CONE_MIN_HALF_AT_CPA_NM, spreadNm);
  const corner = (lat: number, lon: number, side: number, half: number): [number, number, number] => {
    const [la, lo] = destinationPoint(lat, lon, bearing + side * 90, half);
    return [lo, la, z];
  };
  return [
    corner(from.lat, from.lon, -1, CONE_HALF_AT_AIRCRAFT_NM),
    corner(cpaLat, cpaLon, -1, halfCpa),
    corner(cpaLat, cpaLon, 1, halfCpa),
    corner(from.lat, from.lon, 1, CONE_HALF_AT_AIRCRAFT_NM),
  ];
}
type IssueMark = { position: [number, number, number]; color: RGBA; radius: number };
type IssueTag = { position: [number, number, number]; text: string; color: RGBA; offset: [number, number]; anchor: "start" | "middle" | "end" };
const TRAIL_POINTS = 48;
const TRAIL_EVERY_MS = 700;

// One atlas, every glyph pointing north: an airliner, a dart for a jet, a quadcopter, a balloon,
// and a hollow diamond for a return nobody can identify.
const ATLAS =
  "data:image/svg+xml;charset=utf-8," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="64" viewBox="0 0 320 64"><g fill="#fff">` +
      `<path d="M32 3 L36.5 22 L61 39 L61 45 L36.5 37 L35.5 52 L43.5 58 L43.5 61.5 L32 58.5 L20.5 61.5 L20.5 58 L28.5 52 L27.5 37 L3 45 L3 39 L27.5 22 Z"/>` +
      `<path d="M96 3 L117 60 L96 49 L75 60 Z"/>` +
      `<g transform="translate(128 0)"><circle cx="13" cy="13" r="10"/><circle cx="51" cy="13" r="10"/><circle cx="13" cy="51" r="10"/><circle cx="51" cy="51" r="10"/>` +
      `<path d="M10 16 L16 10 L54 48 L48 54 Z M48 10 L54 16 L16 54 L10 48 Z"/><rect x="24" y="22" width="16" height="20" rx="4"/></g>` +
      `<g transform="translate(192 0)"><circle cx="32" cy="23" r="21"/><path d="M21 41 L43 41 L37 53 L27 53 Z"/><rect x="26" y="54" width="12" height="9" rx="1.5"/></g>` +
      `<g transform="translate(256 0)"><path fill-rule="evenodd" d="M32 2 L62 32 L32 62 L2 32 Z M32 15 L49 32 L32 49 L15 32 Z"/><circle cx="32" cy="32" r="6"/></g>` +
      `</g></svg>`,
  );
const glyph = (i: number) => ({ x: i * 64, y: 0, width: 64, height: 64, mask: true, anchorX: 32, anchorY: 32 });
const ICONS = { plane: glyph(0), dart: glyph(1), drone: glyph(2), balloon: glyph(3), unknown: glyph(4) };
const iconOf = (p: { is_intruder: boolean; threat?: string | null }): keyof typeof ICONS =>
  !p.is_intruder || p.threat === "emergency" ? "plane"
    : p.threat === "drone" ? "drone" : p.threat === "balloon" ? "balloon" : p.threat === "unknown" ? "unknown" : "dart";
const tintOf = (p: { is_intruder: boolean; threat?: string | null }): RGBA =>
  p.threat === "emergency" ? C.mayday : C.intruder;

// Every zone is the same quiet white volume; what it is, is written on it.
const ZONE_LOOK: Record<string, { fill: RGBA; line: RGBA; top: number }> = {
  storm: { fill: C.zoneFill, line: C.zoneLine, top: 45000 },
  closed: { fill: C.zoneFill, line: C.zoneLine, top: 45000 },
  rocket: { fill: C.zoneFill, line: C.zoneLine, top: 60000 },
  intruder_buffer: { fill: white(0.04), line: white(0.22), top: 1500 },
};
const zoneLook = (kind: string) => ZONE_LOOK[kind] ?? ZONE_LOOK.storm;
const fl = (ft: number) => `FL${String(Math.round(ft / 100)).padStart(3, "0")}`;
const levelsOf = (z: { floor_ft?: number; ceiling_ft?: number }) =>
  (z.ceiling_ft ?? ALL_LEVELS_FT) >= ALL_LEVELS_FT ? "all levels" : `${fl(z.floor_ft ?? 0)} to ${fl(z.ceiling_ft ?? 0)}`;
const minutesLeft = (expires: number | null | undefined, t: number) =>
  expires == null ? "" : ` · ${Math.max(0, Math.ceil((expires - t) / 60))} min`;

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
const DIM_LAYER = "squack-dim";
function quietBasemap(map: MapLibreMap) {
  try {
    for (const layer of map.getStyle()?.layers ?? []) {
      if (layer.type === "symbol") {
        map.setPaintProperty(layer.id, "text-color", "#3a3d44");
        map.setPaintProperty(layer.id, "text-halo-color", "#0a0a0b");
        map.setPaintProperty(layer.id, "text-opacity", 0.7);
        map.setPaintProperty(layer.id, "icon-opacity", 0.25);
      } else if (layer.type === "line" && /road|highway|street|rail|tunnel|bridge/i.test(layer.id)) {
        map.setPaintProperty(layer.id, "line-opacity", 0.2);
      }
    }
    // A sheet of ground colour over the whole basemap: the coastlines stay legible, the white
    // traffic is the brightest thing on screen.
    if (!map.getLayer(DIM_LAYER)) {
      map.addLayer({ id: DIM_LAYER, type: "background", paint: { "background-color": "#0a0a0b", "background-opacity": 0.35 } });
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
  const { sim, tracks, plan, planView, view, flashUntil, disruptions, watching, selected, follow, ghosts, simClock, onAir, acks } = state;
  const talking = onAir?.callsign ?? null;

  const mapRef = useRef<MapRef | null>(null);
  const [mapStyle, setMapStyle] = useState<string | StyleSpecification>(BASEMAP);
  const [loaded, setLoaded] = useState(false);
  const [now, setNow] = useState(() => performance.now());
  // Altitude exaggeration and tilt / top down are set from the settings sheet (store.view).
  const exaggeration = view.exaggeration;
  const topDown = view.topDown;
  const topDownRef = useRef(topDown);
  topDownRef.current = topDown;
  const [dropMode, setDropMode] = useState<DropMode>("off");
  const [menuOpen, setMenuOpen] = useState(false);
  const [fontReady, setFontReady] = useState(false);
  // Agent mode (TRD 08, rung j): the Disrupt and View panels fold to a "···" until asked for, by a
  // click or by squack's `ui_command panel`. Normal mode never looks at these.
  const agentMode = state.uiMode === "agent";
  const [peek, setPeek] = useState<Record<string, boolean>>({});
  const folded = (name: string) => agentMode && !peek[name] && state.panel !== name;
  const fold = (name: string, label: string) => (
    <button type="button" className="glass pointer-events-auto btn w-fit text-muted" title={`Show ${label}`} aria-label={`Show ${label}`} onClick={() => setPeek((p) => ({ ...p, [name]: true }))}>···</button>
  );
  const unfold = (name: string) => { setPeek((p) => ({ ...p, [name]: false })); if (state.panel === name) dispatch({ type: "set_panel", panel: null }); };
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
    if (loaded) fit(topDownRef.current ? 0 : 52, topDownRef.current ? 0 : -14);
  }, [sim?.world_id, loaded, fit]);

  // Tilt or top down, chosen in the settings sheet: reframe the sector that way.
  useEffect(() => {
    if (loaded) fit(topDown ? 0 : 52, topDown ? 0 : -14);
  }, [topDown]); // eslint-disable-line react-hooks/exhaustive-deps

  // squack asked for a camera move (`ui_command camera`): apply it once, then tell the store.
  const cameraReq = state.cameraRequest;
  useEffect(() => {
    if (!cameraReq || !loaded) return;
    const map = mapRef.current;
    if (cameraReq.exaggeration !== undefined) dispatch({ type: "set_view", view: { exaggeration: cameraReq.exaggeration } });
    if (cameraReq.top_down) fit(0, 0);
    else if (map && (cameraReq.pitch !== undefined || cameraReq.bearing !== undefined)) {
      flyingUntil.current = performance.now() + 900;
      map.easeTo({ pitch: cameraReq.pitch ?? map.getPitch(), bearing: cameraReq.bearing ?? map.getBearing(), duration: 800 });
    }
    dispatch({ type: "camera_consumed", seq: cameraReq.seq });
  }, [cameraReq, loaded, fit, dispatch]);

  // No globe projection. With the deck.gl overlay it drops every aircraft icon, label and ring and
  // leaves only the lines, and at the scale of one sector the Earth looks flat anyway.

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

  // The issue, if any: only for the selected aircraft, only while an alert stands against it, and
  // only while it is on the radar. Derived from the interpolated plane, so it moves with it.
  const selectedPlane = selected ? planes.find((p) => p.callsign === selected) : undefined;
  const fixIndex = useMemo(() => {
    const out: Record<string, IssueFix> = {};
    for (const w of sim?.waypoints ?? []) {
      const [lat, lon] = latLonOf(w, frame);
      out[w.name.toUpperCase()] = { name: w.name, lon, lat };
    }
    return out;
  }, [sim?.waypoints, frame]);
  const standing = selectedPlane ? alertFor(state, selected) : undefined;
  const issue = standing && selectedPlane ? describeIssue(standing, selectedPlane, fixIndex) : null;
  const issueRef = useRef(issue);
  issueRef.current = issue;
  const planesRef = useRef(planes);
  planesRef.current = planes;

  // follow camera. Only the centre moves, so the zoom the user (or a focus fly-in) chose is kept.
  // After a focus fly-in the aircraft is held where that framing put it, not dragged to the middle.
  const flyingUntil = useRef(0);
  const focusFrame = useRef<{ callsign: string; offset: [number, number] }>({ callsign: "", offset: [0, 0] });
  const followed = follow ? selectedPlane : undefined;
  const followKey = followed ? Math.floor(now / 900) : 0;
  useEffect(() => {
    if (!followed || performance.now() < flyingUntil.current) return; // a fly-in is still running
    const offset: [number, number] = focusFrame.current.callsign === followed.callsign ? focusFrame.current.offset : [0, 0];
    mapRef.current?.easeTo({ center: [followed.lon, followed.lat], offset, duration: 850, easing: (t) => t });
  }, [followKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // "Take me to it": an alert card was clicked. Fly to the aircraft, close enough to read the
  // geometry. If the issue involves a fix, frame aircraft and fix together between the panels.
  const seenFocus = useRef(state.focusSeq);
  useEffect(() => {
    if (state.focusSeq === seenFocus.current) return;
    seenFocus.current = state.focusSeq;
    const map = mapRef.current?.getMap();
    const p = selected ? planesRef.current.find((x) => x.callsign === selected) : undefined;
    if (!map || !p || !Number.isFinite(p.lat) || !Number.isFinite(p.lon)) return; // left the radar: nothing to fly to
    try {
      const is = issueRef.current?.callsign === p.callsign ? issueRef.current : null;
      const fixes = [is?.expectedFix, is?.heardFix].filter((f): f is IssueFix => !!f);
      const bearing = map.getBearing();
      let zoom = FOCUS_ZOOM;
      // A level issue's label runs to the right of the aircraft: leave it room before the side panel.
      let offset: [number, number] = is?.kind === "level" ? [-110, 0] : [0, 0];
      if (fixes.length > 0) {
        const lons = [p.lon, ...fixes.map((f) => f.lon)];
        const lats = [p.lat, ...fixes.map((f) => f.lat)];
        const cam = map.cameraForBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: FOCUS_PADDING, bearing });
        const c = cam?.center as { lng?: number; lat?: number } | [number, number] | undefined;
        const [clon, clat] = Array.isArray(c) ? c : [c?.lng, c?.lat];
        if (cam && typeof cam.zoom === "number" && typeof clon === "number" && typeof clat === "number") {
          // The fit is where the camera would sit; the same view with the aircraft as the anchor
          // and a screen offset is what the follow camera can then hold without a jump.
          zoom = Math.max(FOCUS_MIN_ZOOM, Math.min(FOCUS_ZOOM, cam.zoom - 0.2));
          const world = 512 * 2 ** zoom;
          const [px, py] = mercator(p.lon, p.lat);
          const [cx, cy] = mercator(clon, clat);
          const dx = (px - cx) * world;
          const dy = (py - cy) * world;
          const th = (-bearing * Math.PI) / 180;
          offset = [dx * Math.cos(th) - dy * Math.sin(th), dx * Math.sin(th) + dy * Math.cos(th)];
        }
      }
      const pitch = map.getPitch();
      focusFrame.current = { callsign: p.callsign, offset };
      flyingUntil.current = performance.now() + FOCUS_MS + 150;
      map.flyTo({ center: [p.lon, p.lat], offset, zoom, pitch: pitch >= 35 ? pitch : 52, duration: FOCUS_MS, essential: true });
    } catch {
      flyingUntil.current = 0; // the follow camera still takes it there
    }
  }, [state.focusSeq]); // eslint-disable-line react-hooks/exhaustive-deps

  const highlights = useMemo(() => highlightMap(state), [state]);

  // ---------------------------------------------------------------- static-ish layers
  // Before Start every route draws, so the whole plan can be looked over. Once the clock runs and
  // the sky is busy, only flights that are airborne draw theirs, or the map becomes a solid mesh.
  const lifecycle = sim?.lifecycle ?? "running";
  const airborneKey = Object.keys(tracks).sort().join(",");
  const pathData = useMemo(() => {
    const busy = (plan?.paths.length ?? 0) > 40 && (lifecycle === "running" || lifecycle === "paused");
    const airborne = new Set(airborneKey ? airborneKey.split(",") : []);
    const show = (cs: string) => !busy || airborne.has(cs);
    // "changed": only the flights squack actually moved, so a reroute is not lost in eighty lines.
    const moved = new Set((plan?.paths ?? []).filter((p) => p.changes.some((c) => !c.startsWith("direct"))).map((p) => p.callsign));
    const want = (cs: string) => show(cs) && (planView !== "changed" || moved.has(cs) || cs === selected);
    const flown = planView === "tower" ? [] : (plan?.baseline_paths ?? []).filter((p) => want(p.callsign)).map((p) => ({ callsign: p.callsign, path: pathCoords(p, frame, zOf) }));
    const tower = planView === "today" ? [] : (plan?.paths ?? []).filter((p) => want(p.callsign)).map((p) => ({ callsign: p.callsign, path: pathCoords(p, frame, zOf) }));
    return { flown, tower };
  }, [plan, frame, zOf, lifecycle, airborneKey, planView, selected]);

  // Flights whose path goes round a disruption that is still active stay amber, so which lines
  // changed because of the storm is visible long after the flash has gone.
  const activeIds = Object.values(disruptions).filter((d) => d.active !== false).map((d) => d.id);
  const avoiding = new Set((plan?.paths ?? []).filter((p) => p.changes.some((c) => activeIds.some((id) => c.endsWith(` to clear ${id}`)))).map((p) => p.callsign));
  const avoidKey = Array.from(avoiding).sort().join(",");

  // What each rerouted flight WAS going to fly, and a ghost aircraft still flying it.
  const wall = Date.now();
  const ghostList = Object.values(ghosts).filter((g) => g.until > wall);
  const simNow = simClock.t + Math.min(Math.max(0, (now - simClock.at) / 1000), 1.5) * (lifecycle === "running" ? (sim?.speed ?? 1) : 0);
  const ghostPlanes = ghostList.flatMap((g) => {
    const p = g.path;
    const k = p.findIndex((v) => v[3] > simNow);
    if (k <= 0) return [];
    const [a, b] = [p[k - 1], p[k]];
    const f = (simNow - a[3]) / Math.max(1e-6, b[3] - a[3]);
    const lat = a[1] + (b[1] - a[1]) * f;
    const hdg = (Math.atan2((b[0] - a[0]) * Math.cos((lat * Math.PI) / 180), b[1] - a[1]) * 180) / Math.PI;
    return [{ callsign: g.callsign, lon: a[0] + (b[0] - a[0]) * f, lat, alt: a[2] + (b[2] - a[2]) * f, hdg, fade: Math.max(0, (g.until - wall) / (g.until - g.born)) }];
  });

  const circular = sim?.geo?.shape === "circle";
  const sectorRing = useMemo(() => {
    if (circular) return [{ path: ring(frame, 0, 0, half, 96).map(([lon, lat]) => [lon, lat, 0] as [number, number, number]) }];
    const c: [number, number][] = [[-half, -half], [half, -half], [half, half], [-half, half], [-half, -half]];
    return [{ path: c.map(([x, y]) => { const [lat, lon] = nmToLatLon(frame, x, y); return [lon, lat, 0] as [number, number, number]; }) }];
  }, [frame, half, circular]);

  // A zone is drawn between its own floor and ceiling, so a closed block of levels floats.
  const zoneData = useMemo(
    () => (sim?.zones ?? []).map((z: Zone) => {
      const floor = zOf(z.floor_ft ?? 0);
      const top = zOf(Math.min(z.ceiling_ft ?? ALL_LEVELS_FT, zoneLook(z.kind).top));
      const [lat, lon] = nmToLatLon(frame, z.x_nm, z.y_nm);
      return { ...z, floor, top, centre: [lon, lat, top] as [number, number, number],
        polygon: ring(frame, z.x_nm, z.y_nm, z.radius_nm).map(([lo, la]) => [lo, la, floor] as [number, number, number]) };
    }),
    [sim?.zones, frame, zOf],
  );

  // Predicted conflicts (TRD 07): two wedges per pair, rebuilt from the interpolated aircraft each
  // frame so they stay attached, and a label at the closest approach. The store decides which
  // pairs are on screen and how faded; this only draws them.
  const risks = visibleRisk(state, now);
  const riskCones: RiskCone[] = [];
  const riskLabels: RiskLabel[] = [];
  for (const { key, pair, fade } of risks) {
    const pa = planes.find((p) => p.callsign === pair.a);
    const pb = planes.find((p) => p.callsign === pair.b);
    if (!pa || !pb) continue;
    riskCones.push({ key: `${key}:a`, callsign: pair.a, pair, fade, polygon: coneAt(frame, pa, pair.cpa_xy, pair.spread_a_nm, zOf(pa.alt_ft)) });
    riskCones.push({ key: `${key}:b`, callsign: pair.b, pair, fade, polygon: coneAt(frame, pb, pair.cpa_xy, pair.spread_b_nm, zOf(pb.alt_ft)) });
    const [lat, lon] = nmToLatLon(frame, pair.cpa_xy[0], pair.cpa_xy[1]);
    const eta = pair.t_first_s ?? pair.eta_s;
    riskLabels.push({ key, pair, fade, position: [lon, lat, zOf((pa.alt_ft + pb.alt_ft) / 2)], text: `LoS ${Math.round(pair.p_max * 100)}% · ${Math.round(eta)} s` });
  }

  // Busy sky: one line per aircraft, full data block only for the ones that matter right now.
  const dense = planes.length > 22;
  const important = (p: Shown) => p.callsign === selected || !!highlights[p.callsign] || watching.includes(p.callsign) || p.is_intruder;
  const blockLines = (p: Shown): string[] =>
    p.threat === "unknown"
      ? [p.callsign, `no height ${Math.round(p.gs_kt)}`]
      : p.threat === "emergency"
        ? [`${p.callsign} MAYDAY`, `FL${String(Math.round(p.alt_ft / 100)).padStart(3, "0")} ↓ ${Math.round(p.gs_kt)}`]
        : dense && !important(p)
          ? [`${p.callsign} ${String(Math.round(p.alt_ft / 100)).padStart(3, "0")}`]
          : [p.callsign, `FL${String(Math.round(p.alt_ft / 100)).padStart(3, "0")} ${Math.round(p.gs_kt)}`, ...(!p.is_intruder && clearedLine(p) ? [clearedLine(p)] : [])];

  // ---------------------------------------------------------------- the issue, as drawable pieces
  // At most a dozen objects, rebuilt from the interpolated aircraft each frame so they move with it.
  const issueDraw: { cleared: IssueSeg[]; wrong: IssueSeg[]; marks: IssueMark[]; tags: IssueTag[]; label: IssueTag[] } = { cleared: [], wrong: [], marks: [], tags: [], label: [] };
  const wrongLevelCs = issue?.levelWrong && selectedPlane ? selectedPlane.callsign : null;
  if (issue && selectedPlane) {
    const p = selectedPlane;
    const z = zOf(p.alt_ft);
    const at: [number, number, number] = [p.lon, p.lat, z];
    const wrongWord = issue.wrongIs === "flown" ? "flying" : "read back";
    const faint = (c: RGBA): RGBA => [c[0], c[1], c[2], 110];
    const toneColor = issue.tone === "radar" ? C.cleared : issue.tone === "warn" ? C.warn : C.wrong;
    /** Where the issue's lines end, [lon, lat]: the one-line label goes on the other side of the aircraft. */
    const ends: [number, number][] = [];
    /** A tag at the far end of a line reads outward, away from the aircraft, so it never sits on its own line. */
    const outward = (lon: number, lat: number, text: string, color: RGBA, position: [number, number, number]): IssueTag => {
      let left = false;
      try {
        const map = mapRef.current;
        if (map) left = map.project([lon, lat]).x < map.project([p.lon, p.lat]).x;
      } catch {
        /* no map yet */
      }
      return { position, text, color, offset: [left ? -11 : 11, 0], anchor: left ? "end" : "start" };
    };

    if (issue.kind === "level" && issue.expectedAltFt !== undefined) {
      // On the aircraft's own stem: where it was cleared to, where it said it was going, and the gap.
      const level = (ft: number, color: RGBA, word: string, into: IssueSeg[]) => {
        const top: [number, number, number] = [p.lon, p.lat, zOf(ft)];
        into.push({ path: [at, top], color, width: 2 });
        issueDraw.marks.push({ position: top, color, radius: 5 });
        issueDraw.tags.push({ position: top, text: `${word} ${flightLevel(ft)}`, color, offset: [-12, 0], anchor: "end" });
      };
      if (issue.heardAltFt !== undefined) level(issue.heardAltFt, C.wrong, wrongWord, issueDraw.wrong);
      level(issue.expectedAltFt, C.cleared, "cleared", issueDraw.cleared);
    }

    if (issue.kind === "route") {
      const leg = (f: IssueFix, color: RGBA, word: string, into: IssueSeg[]) => {
        const end: [number, number, number] = [f.lon, f.lat, z];
        ends.push([f.lon, f.lat]);
        into.push({ path: [at, end], color, width: 2 });
        into.push({ path: [end, [f.lon, f.lat, 0]], color: faint(color), width: 1.2 }); // ties the line's end to the fix on the ground
        issueDraw.marks.push({ position: end, color, radius: 5 });
        issueDraw.tags.push(outward(f.lon, f.lat, `${word} ${f.name}`, color, end));
      };
      if (issue.heardFix) leg(issue.heardFix, C.wrong, wrongWord, issueDraw.wrong);
      if (issue.expectedFix) leg(issue.expectedFix, C.cleared, "cleared", issueDraw.cleared);
    }

    if (issue.kind === "heading" || issue.kind === "route") {
      const vector = (hdg: number, color: RGBA, word: string, into: IssueSeg[]) => {
        const [lat, lon] = destinationPoint(p.lat, p.lon, hdg, HDG_VECTOR_NM);
        const tip: [number, number, number] = [lon, lat, z];
        ends.push([lon, lat]);
        into.push({ path: [at, tip], color, width: 2 });
        issueDraw.marks.push({ position: tip, color, radius: 3.5 });
        issueDraw.tags.push(outward(lon, lat, `${word} ${String(Math.round(hdg) % 360).padStart(3, "0")}`, color, tip));
      };
      if (issue.heardHdg !== undefined) vector(issue.heardHdg, C.wrong, wrongWord, issueDraw.wrong);
      if (issue.kind === "heading" && issue.expectedHdg !== undefined) vector(issue.expectedHdg, C.cleared, "cleared", issueDraw.cleared);
    }

    // The one-line label. A level issue lives on the stem, so its label sits to the right, under
    // the data block. Lines fan out from the aircraft, so theirs is centred above or below it,
    // whichever side the lines do not run through on screen.
    if (issue.kind === "level") {
      issueDraw.label.push({ position: at, text: issue.label, color: toneColor, offset: [18, 30], anchor: "start" });
    } else {
      let down = 0;
      try {
        const map = mapRef.current;
        if (map && ends.length > 0) {
          const o = map.project([p.lon, p.lat]);
          for (const e of ends) {
            const q = map.project(e);
            down += (q.y - o.y) / (Math.hypot(q.x - o.x, q.y - o.y) || 1);
          }
        }
      } catch {
        /* no map yet: below is fine */
      }
      issueDraw.label.push({ position: at, text: issue.label, color: toneColor, offset: [0, down > 0 ? -36 : 36], anchor: "middle" });
    }
  }

  const flashSlot = Math.floor(now / 250);
  const wallNow = Date.now();
  const pulse = (now % 1300) / 1300;

  const layers: Layer[] = [
    new PathLayer({
      id: "sector",
      data: sectorRing,
      getPath: (d: { path: [number, number, number][] }) => d.path,
      getColor: C.sector,
      getWidth: 1,
      widthUnits: "pixels",
      extensions: [new PathStyleExtension({ dash: true })],
      getDashArray: [6, 5],
      dashJustified: true,
    }),

    new PolygonLayer({
      id: "zones",
      data: zoneData,
      getPolygon: (d: { polygon: [number, number, number][] }) => d.polygon,
      extruded: true,
      wireframe: true,
      getElevation: (d: { floor: number; top: number }) => d.top - d.floor,
      getFillColor: (d: Zone) => zoneLook(d.kind).fill,
      getLineColor: (d: Zone) => zoneLook(d.kind).line,
      pickable: true,
    }),
    new TextLayer({
      id: "zone-names",
      data: zoneData.filter((z) => z.kind !== "intruder_buffer"),
      getPosition: (d: { centre: [number, number, number] }) => d.centre,
      getText: (z: Zone) => `${z.id}\n${levelsOf(z)}${minutesLeft(z.expires_t, sim?.t ?? 0)}`,
      getSize: 10.5,
      getColor: C.muted,
      getTextAnchor: "middle",
      getAlignmentBaseline: "center",
      lineHeight: 1.2,
      fontFamily: fontReady ? '"B612 Mono", ui-monospace, monospace' : "ui-monospace, monospace",
      fontSettings: { sdf: true },
      outlineWidth: 4,
      outlineColor: C.ink,
      parameters: ALWAYS_ON_TOP,
      updateTriggers: { getText: Math.floor((sim?.t ?? 0) / 30) },
    }),

    new PathLayer({
      id: "flown",
      data: pathData.flown,
      getPath: (d: { path: [number, number, number][] }) => d.path,
      getColor: planView === "today" ? C.flown : C.flownDim,
      getWidth: planView === "today" ? 1.5 : 1,
      widthUnits: "pixels",
      extensions: [new PathStyleExtension({ dash: true })],
      getDashArray: [5, 4],
      dashJustified: true,
      updateTriggers: { getColor: planView, getWidth: planView },
    }),

    new PathLayer({
      id: "tower-plan",
      data: pathData.tower,
      getPath: (d: { path: [number, number, number][] }) => d.path,
      getColor: (d: { callsign: string }) => ((flashUntil[d.callsign] ?? 0) > wallNow ? C.flash : d.callsign === selected ? white(1) : avoiding.has(d.callsign) ? C.rerouted : C.tower),
      getWidth: (d: { callsign: string }) => ((flashUntil[d.callsign] ?? 0) > wallNow ? 3 : d.callsign === selected ? 2.5 : 1.5),
      widthUnits: "pixels",
      capRounded: true,
      jointRounded: true,
      updateTriggers: { getColor: [flashSlot, selected, avoidKey], getWidth: [flashSlot, selected] },
    }),

    // Where two flights may lose separation inside the next two minutes, and how sure the rollouts are.
    new PolygonLayer<RiskCone>({
      id: "risk-cones",
      data: riskCones,
      getPolygon: (d) => d.polygon,
      filled: true,
      stroked: true,
      extruded: false,
      getFillColor: (d) => [...C.risk, Math.round(255 * (0.08 + 0.3 * d.pair.p_max) * d.fade)] as RGBA,
      getLineColor: (d) => [...C.risk, Math.round(255 * Math.min(1, 0.35 + 0.6 * d.pair.p_max) * d.fade)] as RGBA,
      getLineWidth: 1,
      lineWidthUnits: "pixels",
      pickable: true,
      parameters: ALWAYS_ON_TOP,
      updateTriggers: { getPolygon: [now, exaggeration], getFillColor: now, getLineColor: now },
    }),
    new TextLayer<RiskLabel>({
      id: "risk-labels",
      data: riskLabels,
      getPosition: (d) => d.position,
      getText: (d) => d.text,
      getSize: 11,
      getColor: (d) => [...C.risk, Math.round(255 * d.fade)] as RGBA,
      getTextAnchor: "middle",
      getAlignmentBaseline: "center",
      characterSet: ISSUE_CHARS,
      fontFamily: fontReady ? '"B612 Mono", ui-monospace, monospace' : "ui-monospace, monospace",
      fontSettings: { sdf: true },
      outlineWidth: 4,
      outlineColor: C.ink,
      parameters: ALWAYS_ON_TOP,
      updateTriggers: { getPosition: [now, exaggeration], getText: now, getColor: now },
    }),

    // The shadow of a reroute: the path the flight was on, and a ghost still flying it, fading out.
    new PathLayer({
      id: "ghost-paths",
      data: ghostList,
      getPath: (g: { path: [number, number, number, number][] }) => g.path.map(([lon, lat, alt]) => [lon, lat, zOf(alt)] as [number, number, number]),
      getColor: (g: { born: number; until: number }) => [...WHITE, Math.round(C.ghost[3] * Math.max(0, (g.until - wall) / (g.until - g.born)))] as RGBA,
      getWidth: 1.5,
      widthUnits: "pixels",
      extensions: [new PathStyleExtension({ dash: true })],
      getDashArray: [2, 3],
      updateTriggers: { getColor: Math.floor(now / 500), getPath: exaggeration },
    }),
    new IconLayer({
      id: "ghost-planes",
      data: ghostPlanes,
      iconAtlas: ATLAS,
      iconMapping: ICONS,
      getIcon: () => "plane",
      getPosition: (g: { lon: number; lat: number; alt: number }) => [g.lon, g.lat, zOf(g.alt)],
      getAngle: (g: { hdg: number }) => -g.hdg,
      getSize: 24,
      sizeUnits: "pixels",
      billboard: false,
      getColor: (g: { fade: number }) => [...WHITE, Math.round(90 * g.fade)] as RGBA,
      parameters: ALWAYS_ON_TOP,
      updateTriggers: { getPosition: [now, exaggeration], getAngle: now, getColor: Math.floor(now / 500) },
    }),

    new PathLayer({
      id: "intruder-paths",
      data: Object.values(disruptions).filter((d) => (d.predicted_lonlat?.length ?? 0) > 1),
      getPath: (d: Disruption) => (d.predicted_lonlat ?? []).map(([lon, lat]) => [lon, lat, zOf(d.alt_ft ?? 30000)] as [number, number, number]),
      getColor: (d: Disruption) => (d.kind === "emergency" ? [...AMBER, 180] : [...RED, 180]),
      getWidth: 1.5,
      widthUnits: "pixels",
      extensions: [new PathStyleExtension({ dash: true })],
      getDashArray: [3, 3],
      updateTriggers: { getPath: exaggeration },
    }),

    new ScatterplotLayer({
      id: "waypoints",
      data: sim?.waypoints ?? [],
      getPosition: (w: { x_nm: number; y_nm: number; lat?: number; lon?: number }) => { const [lat, lon] = latLonOf(w, frame); return [lon, lat, 0]; },
      getRadius: (w: { kind?: string }) => (w.kind === "gate" ? 3.6 : 2.4),
      radiusUnits: "pixels",
      getFillColor: (w: { kind?: string }) => (w.kind === "gate" ? C.gate : C.waypoint),
      pickable: true,
    }),
    new TextLayer({
      id: "waypoint-names",
      data: sim?.waypoints ?? [],
      getPosition: (w: { x_nm: number; y_nm: number; lat?: number; lon?: number }) => { const [lat, lon] = latLonOf(w, frame); return [lon, lat, 0]; },
      getText: (w: { name: string }) => w.name,
      getSize: 10,
      getColor: C.muted,
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
      getWidth: 2,
      widthUnits: "pixels",
      capRounded: true,
    }),

    new LineLayer({
      id: "stems",
      data: planes,
      getSourcePosition: (p: Shown) => [p.lon, p.lat, 0],
      getTargetPosition: (p: Shown) => [p.lon, p.lat, zOf(p.alt_ft)],
      getColor: (p: Shown) => (p.callsign === wrongLevelCs ? ([...RED, 230] as RGBA) : p.is_intruder ? (p.threat === "emergency" ? [...AMBER, 110] : [...RED, 90]) : C.stem),
      getWidth: (p: Shown) => (p.callsign === wrongLevelCs ? 2 : 1),
      widthUnits: "pixels",
      updateTriggers: { getTargetPosition: exaggeration, getColor: wrongLevelCs, getWidth: wrongLevelCs },
    }),
    new ScatterplotLayer({
      id: "ground-marks",
      data: planes,
      getPosition: (p: Shown) => [p.lon, p.lat, 0],
      getRadius: 2,
      radiusUnits: "pixels",
      getFillColor: (p: Shown) => (p.is_intruder ? [...RED, 140] : white(0.4)),
    }),

    // The issue's geometry sits under the aircraft glyphs and over everything else. Empty unless
    // the selected aircraft has a standing alert.
    new PathLayer({
      id: "issue-wrong",
      data: issueDraw.wrong,
      getPath: (d: IssueSeg) => d.path,
      getColor: (d: IssueSeg) => d.color,
      getWidth: (d: IssueSeg) => d.width,
      widthUnits: "pixels",
      billboard: true,
      extensions: [new PathStyleExtension({ dash: true })],
      getDashArray: [5, 4],
      parameters: ALWAYS_ON_TOP,
    }),
    new PathLayer<IssueSeg>({
      id: "issue-cleared",
      data: issueDraw.cleared,
      getPath: (d) => d.path,
      getColor: (d) => d.color,
      getWidth: (d) => d.width,
      widthUnits: "pixels",
      billboard: true,
      capRounded: true,
      parameters: ALWAYS_ON_TOP,
    }),
    new ScatterplotLayer<IssueMark>({
      id: "issue-marks",
      data: issueDraw.marks,
      getPosition: (d) => d.position,
      getRadius: (d) => d.radius,
      radiusUnits: "pixels",
      filled: true,
      getFillColor: C.ink,
      stroked: true,
      getLineColor: (d) => d.color,
      getLineWidth: 2,
      lineWidthUnits: "pixels",
      billboard: true,
      parameters: ALWAYS_ON_TOP,
    }),

    // Where an aircraft on an assigned heading is going to point: drawn the instant the heading is
    // accepted, amber while it is still turning onto it, white once it is there. A turn at 1.5
    // degrees a second takes half a minute to see; this takes no time at all.
    new LineLayer({
      id: "cleared-vectors",
      data: planes.filter((p) => !p.is_intruder && p.target_hdg_deg != null),
      getSourcePosition: (p: Shown) => [p.lon, p.lat, zOf(p.alt_ft)],
      getTargetPosition: (p: Shown) => {
        const [lat, lon] = destinationPoint(p.lat, p.lon, p.target_hdg_deg ?? p.hdg_deg, (p.gs_kt * CLEARED_VECTOR_MIN) / 60);
        return [lon, lat, zOf(p.alt_ft)];
      },
      getColor: (p: Shown) => (angleBetween(p.hdg_deg, p.target_hdg_deg ?? p.hdg_deg) > 3 ? C.warn : white(0.55)),
      getWidth: (p: Shown) => (angleBetween(p.hdg_deg, p.target_hdg_deg ?? p.hdg_deg) > 3 ? 2 : 1.5),
      widthUnits: "pixels",
      parameters: ALWAYS_ON_TOP,
      updateTriggers: { getSourcePosition: [now, exaggeration], getTargetPosition: [now, exaggeration], getColor: now, getWidth: now },
    }),

    new ScatterplotLayer({
      id: "rings",
      data: planes.filter((p) => highlights[p.callsign] || watching.includes(p.callsign) || p.callsign === selected || p.callsign === talking),
      getPosition: (p: Shown) => [p.lon, p.lat, zOf(p.alt_ft)],
      filled: false,
      stroked: true,
      billboard: true,
      radiusUnits: "pixels",
      lineWidthUnits: "pixels",
      getLineWidth: 1.5,
      getRadius: (p: Shown) => (highlights[p.callsign] === "alert" ? 14 + pulse * 16 : highlights[p.callsign] === "resolving" ? 15 + pulse * 6 : p.callsign === talking ? 13 + pulse * 10 : 16),
      getLineColor: (p: Shown) => {
        const h = highlights[p.callsign];
        if (h === "alert") return [...RED, Math.round(255 * (1 - pulse * 0.8))] as RGBA;
        if (h === "resolving") return C.resolving;
        if (p.callsign === talking) return [...C.onAir, Math.round(255 * (1 - pulse * 0.6))] as RGBA; // on the air
        if (watching.includes(p.callsign)) return C.watching;
        return white(0.8);
      },
      parameters: ALWAYS_ON_TOP,
      updateTriggers: { getRadius: now, getLineColor: now, getPosition: exaggeration },
    }),

    new IconLayer({
      id: "aircraft",
      data: planes,
      iconAtlas: ATLAS,
      iconMapping: ICONS,
      getIcon: (p: Shown) => iconOf(p),
      getPosition: (p: Shown) => [p.lon, p.lat, zOf(p.alt_ft)],
      getAngle: (p: Shown) => (p.threat === "balloon" || p.threat === "unknown" ? 0 : -p.hdg_deg),
      getSize: (p: Shown) => (p.callsign === selected ? 34 : p.is_intruder ? 28 : dense ? 21 : 27),
      sizeUnits: "pixels",
      billboard: false,
      getColor: (p: Shown) => (p.is_intruder ? tintOf(p) : highlights[p.callsign] === "alert" ? C.alert : C.aircraft),
      pickable: true,
      parameters: ALWAYS_ON_TOP,
      updateTriggers: { getPosition: exaggeration, getSize: [selected, dense], getColor: [highlights] },
    }),
    // The data block: the first line (who) in white, the lines under it (where, how fast, what it was
    // told) muted. Two text layers, since a text object has one colour.
    new TextLayer({
      id: "data-blocks",
      data: planes,
      getPosition: (p: Shown) => [p.lon, p.lat, zOf(p.alt_ft)],
      getText: (p: Shown) => blockLines(p)[0],
      getSize: (p: Shown) => (dense && !important(p) ? 9.5 : 11),
      getColor: (p: Shown) => (p.is_intruder ? tintOf(p) : dense && !important(p) ? white(0.75) : white(0.96)),
      getPixelOffset: [20, -10],
      getTextAnchor: "start",
      getAlignmentBaseline: "top",
      fontFamily: fontReady ? '"B612 Mono", ui-monospace, monospace' : "ui-monospace, monospace",
      fontSettings: { sdf: true },
      outlineWidth: 4,
      outlineColor: C.ink,
      parameters: ALWAYS_ON_TOP,
      updateTriggers: { getPosition: exaggeration, getText: [dense, selected, highlights, watching, now], getSize: [dense, selected, highlights], getColor: [dense, selected, highlights] },
    }),
    new TextLayer({
      id: "data-blocks-2",
      data: planes.filter((p) => blockLines(p).length > 1),
      getPosition: (p: Shown) => [p.lon, p.lat, zOf(p.alt_ft)],
      getText: (p: Shown) => blockLines(p).slice(1).join("\n"),
      getSize: 11,
      getColor: (p: Shown) => (p.is_intruder ? ([...tintOf(p).slice(0, 3), 200] as RGBA) : C.muted),
      getPixelOffset: [20, 3],
      getTextAnchor: "start",
      getAlignmentBaseline: "top",
      lineHeight: 1.15,
      fontFamily: fontReady ? '"B612 Mono", ui-monospace, monospace' : "ui-monospace, monospace",
      fontSettings: { sdf: true },
      outlineWidth: 4,
      outlineColor: C.ink,
      parameters: ALWAYS_ON_TOP,
      updateTriggers: { getPosition: exaggeration, getText: [dense, selected, highlights, watching, now], getColor: [dense, selected, highlights] },
    }),

    // What squack just understood, on the aircraft itself, the moment the key is released. Green
    // pill, a few seconds, then it fades: the first answer to "did it hear me?".
    new TextLayer({
      id: "acks",
      data: planes.filter((p) => acks[p.callsign] && now - acks[p.callsign].at < ACK_SHOWS_MS),
      getPosition: (p: Shown) => [p.lon, p.lat, zOf(p.alt_ft)],
      getText: (p: Shown) => `✓ ${acks[p.callsign].text}`,
      getSize: 13,
      getColor: (p: Shown) => [10, 10, 11, Math.round(255 * Math.min(1, (ACK_SHOWS_MS - (now - acks[p.callsign].at)) / ACK_FADES_MS))] as RGBA,
      background: true,
      getBackgroundColor: (p: Shown) => [...C.onAir, Math.round(240 * Math.min(1, (ACK_SHOWS_MS - (now - acks[p.callsign].at)) / ACK_FADES_MS))] as RGBA,
      backgroundPadding: [7, 4],
      getPixelOffset: [0, -30],
      getTextAnchor: "middle",
      getAlignmentBaseline: "center",
      fontFamily: fontReady ? '"B612 Mono", ui-monospace, monospace' : "ui-monospace, monospace",
      fontWeight: 700,
      characterSet: "auto",
      parameters: ALWAYS_ON_TOP,
      updateTriggers: { getPosition: exaggeration, getText: [acks], getColor: now, getBackgroundColor: now },
    }),

    new TextLayer<IssueTag>({
      id: "issue-tags",
      data: issueDraw.tags,
      getPosition: (d) => d.position,
      getText: (d) => d.text,
      getColor: (d) => d.color,
      getSize: 10.5,
      getPixelOffset: (d) => d.offset,
      getTextAnchor: (d) => d.anchor,
      getAlignmentBaseline: "center",
      characterSet: ISSUE_CHARS,
      fontFamily: fontReady ? '"B612 Mono", ui-monospace, monospace' : "ui-monospace, monospace",
      fontSettings: { sdf: true },
      outlineWidth: 4,
      outlineColor: C.ink,
      parameters: ALWAYS_ON_TOP,
    }),
    // What is wrong, in one line, under the data block.
    new TextLayer<IssueTag>({
      id: "issue-label",
      data: issueDraw.label,
      getPosition: (d) => d.position,
      getText: (d) => d.text,
      getColor: (d) => d.color,
      getSize: 11.5,
      getPixelOffset: (d) => d.offset,
      getTextAnchor: (d) => d.anchor,
      getAlignmentBaseline: "center",
      characterSet: ISSUE_CHARS,
      fontFamily: fontReady ? '"B612 Mono", ui-monospace, monospace' : "ui-monospace, monospace",
      fontSettings: { sdf: true },
      background: true,
      getBackgroundColor: C.pill,
      getBorderColor: (d) => [d.color[0], d.color[1], d.color[2], 170] as RGBA,
      getBorderWidth: 1,
      backgroundPadding: [7, 4],
      parameters: ALWAYS_ON_TOP,
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
        // Aircraft and their lines are drawn at height, exaggerated: FL350 at 6x is 64 km up, and
        // in the tilted view that is about 40 NM up the screen from the ground beneath it. A click
        // read as a point on the ground therefore put the zone 40 NM from the line that was
        // clicked. Read it on the level the traffic is drawn at instead.
        const levels = planesRef.current.filter((p) => !p.is_intruder).map((p) => p.alt_ft).sort((m, n) => m - n);
        const level = levels.length ? levels[Math.floor(levels.length / 2)] : 0;
        let [lon, lat] = info.coordinate as [number, number];
        if (level > 0 && info.viewport) {
          const at = info.viewport.unproject([info.x, info.y], { targetZ: zOf(level) });
          if (Number.isFinite(at[0]) && Number.isFinite(at[1])) [lon, lat] = [at[0], at[1]];
        }
        const [x, y] = latLonToNm(frame, lat, lon);
        send({ type: "add_disruption", kind: dropMode, x_nm: Math.round(x * 10) / 10, y_nm: Math.round(y * 10) / 10 });
        setDropMode("off");
        setMenuOpen(false);
        return true;
      }
      if (selected) dispatch({ type: "select", callsign: null });
      return false;
    },
    [dispatch, dropMode, frame, send, selected, zOf],
  );

  const tooltip = useCallback((info: PickingInfo) => {
    if (!info.object) return null;
    const o = info.object as Partial<Shown> & { name?: string };
    const z = info.object as Zone;
    const text = info.layer?.id === "aircraft"
      ? `${o.callsign}  ${o.actype ?? ""}\nFL${Math.round((o.alt_ft ?? 0) / 100)}  ${Math.round(o.gs_kt ?? 0)} kt  hdg ${Math.round(o.hdg_deg ?? 0)}`
      : info.layer?.id === "zones"
        ? `${z.label || z.kind}  ${z.id}\n${Math.round(z.radius_nm)} NM radius, ${levelsOf(z)}`
        : info.layer?.id === "risk-cones"
          ? riskTip((info.object as RiskCone).pair)
          : (o.name ?? "");
    return text
      ? { text, style: { background: "var(--panel)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: "var(--radius)", fontFamily: "var(--font-mono)", fontSize: "11px", padding: "6px 8px", whiteSpace: "pre" } }
      : null;
  }, []);

  const kinds = (sim?.disruption_kinds?.length ? sim.disruption_kinds : KINDS).filter((k) => k.menu !== false);
  const active = Object.values(disruptions).filter((d) => d.active !== false);

  // Text chips are the shared .btn: a hairline, muted until chosen. .btn is unlayered CSS, so the
  // chosen state is an inline style rather than a utility class it would override.
  const ON = { color: "var(--fg)", borderColor: "rgba(236, 236, 236, 0.6)" } as const;
  /** An active disruption's id: red for a thing in the sky, amber for one of ours in trouble, white for a volume. */
  const disruptionTone = (d: Disruption) => (d.kind === "emergency" ? "text-warn" : d.shape === "point" ? "text-bad" : "text-fg");

  return (
    <div className={`absolute inset-0 ${dropMode !== "off" ? "cursor-crosshair" : ""}`}>
      <MapGL
        ref={mapRef}
        mapStyle={mapStyle}
        initialViewState={{ longitude: frame.lon0, latitude: frame.lat0, zoom: 5.4, pitch: 52, bearing: -14 }}
        maxPitch={78}
        attributionControl={{ compact: true }}
        onDragStart={() => {
          // Panning away by hand lets go of the aircraft (an alert card's focus turns follow on
          // unasked). Zoom, tilt and rotate keep following. The flight strip turns it back on.
          flyingUntil.current = 0;
          if (follow) dispatch({ type: "set_follow", on: false });
        }}
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

      {/* Disrupt: one control. Random puts something where it will matter; Choose lets you place a kind. */}
      {/* z-10: the deck.gl overlay canvas paints above unstacked siblings, so traffic drew over these panels */}
      <div className="pointer-events-none absolute z-10 left-2 top-[52px] bottom-[196px] w-[336px] flex flex-col gap-2 overflow-y-auto scroll-thin">
      {folded("disrupt") ? fold("disrupt", "the Disrupt control") : (
      <div className="glass pointer-events-auto px-2.5 py-2 relative">
        {agentMode && <button type="button" className="absolute top-1.5 right-2 text-[11px] text-muted hover:text-fg" onClick={() => unfold("disrupt")} aria-label="Hide">×</button>}
        <div className="flex items-center gap-2">
          <span className="eyebrow">Disrupt</span>
          <button
            className="btn"
            disabled={!sim?.scenario}
            title="A random kind, dropped on the path of a flight a few minutes ahead. Seeded: the same presses give the same result."
            onClick={() => { setDropMode("off"); setMenuOpen(false); send({ type: "add_disruption", kind: "random" }); }}
          >
            Random
          </button>
          <button className="btn" style={menuOpen || dropMode !== "off" ? ON : undefined} aria-pressed={menuOpen || dropMode !== "off"} onClick={() => { setMenuOpen((o) => !o); setDropMode("off"); }}>
            Choose
          </button>
          <span className="ml-auto text-[11px] text-muted">{planes.filter((p) => !p.is_intruder).length} aircraft</span>
        </div>

        {menuOpen && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {kinds.map((k) => (
              <button
                key={k.kind}
                title={k.blurb}
                className="btn"
                style={dropMode === k.kind ? ON : undefined}
                aria-pressed={dropMode === k.kind}
                onClick={() => setDropMode(dropMode === k.kind ? "off" : k.kind)}
              >
                {k.label}
              </button>
            ))}
          </div>
        )}
        {dropMode !== "off" && (
          <p className="mt-2 text-[11px] text-fg">
            {dropMode === "emergency" ? "Click near the flight that declares the emergency." : "Click the map to place it."}
            <span className="text-muted"> {kinds.find((k) => k.kind === dropMode)?.blurb}</span>
          </p>
        )}

        {active.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5 border-t border-line pt-2">
            {active.map((d) => (
              <span key={d.id} className="chip font-mono">
                <button className={disruptionTone(d)} title={d.label} onClick={() => d.shape === "point" && dispatch({ type: "select", callsign: d.id })}>
                  {d.id}
                </button>
                <span className="text-muted">{minutesLeft(d.expires_t, sim?.t ?? 0).replace(" · ", "") || d.label}</span>
                {d.kind !== "emergency" && (
                  <button className="text-muted hover:text-fg" title="Remove it" onClick={() => send({ type: "remove_disruption", id: d.id })}>✕</button>
                )}
              </span>
            ))}
          </div>
        )}
      </div>
      )}
      {/* Everything squack knows about the selected aircraft sits under the control, never over it. */}
      <FlightStrip />
      </div>

    </div>
  );
}
