"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { snapshotClock, useTowerDispatch, useTowerState } from "@/lib/store";
import { useClient } from "./TowerApp";
import VoiceToggle from "./VoiceToggle";
import Select, { type SelectOption } from "./Select";
import type { ScenarioInfo } from "@/lib/types";

const DENSITIES = [1, 1.5, 2, 2.5] as const;
/** The setup panel's own scenario: the backend generates it from these three numbers, and its name
 *  ("custom/24/busy/7") is the whole recipe, so Restart rebuilds the same sky. */
const CUSTOM = "custom";
const PACES = [
  { key: "calm", label: "Calm", every: "one every 2 min" },
  { key: "normal", label: "Normal", every: "one every 75 s" },
  { key: "busy", label: "Busy", every: "one every 40 s" },
] as const;
type Pace = (typeof PACES)[number]["key"];
const CAPS = [40, 80, 120, 0] as const; // 0 = every flight
const LIVE_WAIT_MS = 15000; // a live snapshot is one network fetch on the backend, about 8 s at worst

type Source = "sim" | "real" | "live";

const SOURCES: { key: Source; eyebrow: string; name: string; detail: string }[] = [
  { key: "real", eyebrow: "Recorded", name: "Real day", detail: "Airline flights that really crossed a region at cruise, one hour of them." },
  { key: "live", eyebrow: "Right now", name: "Live snapshot", detail: "One snapshot of the sky over a region as it is this minute." },
  { key: "sim", eyebrow: "Scripted", name: "Simulated", detail: "Synthetic traffic built to show conflicts, storms and readback errors." },
];

/** "Fri 18 Sep" — short enough to sit in a narrow dropdown beside the hour. */
function prettyDate(iso?: string): string {
  if (!iso) return "";
  const d = new Date(`${iso}T12:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  const weekday = d.toLocaleDateString("en-US", { weekday: "short", timeZone: "UTC" });
  const month = d.toLocaleDateString("en-US", { month: "short", timeZone: "UTC" });
  return `${weekday} ${d.getUTCDate()} ${month}`;
}

const hh = (h?: number) => String(h ?? 0).padStart(2, "0");

/** "Western Europe core (Maastricht, Rhine, Benelux)" -> "Western Europe core". */
const shortName = (label: string) => label.split(" (")[0];

const EASE = "cubic-bezier(0.22, 1, 0.36, 1)";
const OUT_MS = 120; // the old middle leaves
const IN_MS = 180; // the new middle arrives, after it
const CLOSE_MS = 160;

/**
 * The dialog's own motion, kept in this file so nothing else has to know about it: the sheet's
 * enter and leave, the height it eases between as the middle changes, and the cross-fade of the
 * middle itself. Every rule is off under prefers-reduced-motion.
 */
const CSS = `
.sp-scrim { transition: opacity 200ms ${EASE}; }
.sp-scrim[data-open="false"] { opacity: 0; transition-duration: ${CLOSE_MS}ms; }
.sp-dialog { transition: opacity 200ms ${EASE}, transform 200ms ${EASE}; }
.sp-dialog[data-open="false"] { opacity: 0; transform: scale(0.98); transition-duration: ${CLOSE_MS}ms; }
/* flow-root: the middle's 4px margin is its own, not one that collapses out and moves the sheet. */
.sp-h { display: flow-root; transition: height 260ms ${EASE}; }
/* The 4px is margin, not transform: a transform here would make this element the containing block
   for the Select popover's position: fixed, and misplace it for the length of the fade. */
.sp-swap { transition: opacity ${IN_MS}ms ${EASE}, margin-top ${IN_MS}ms ${EASE}; }
.sp-swap[data-phase="out"] { opacity: 0; margin-top: 4px; transition-duration: ${OUT_MS}ms; }
.sp-swap[data-phase="in"] { opacity: 0; margin-top: 4px; transition: none; }
@media (prefers-reduced-motion: reduce) {
  .sp-scrim, .sp-dialog, .sp-h, .sp-swap { transition: none !important; }
  .sp-scrim[data-open="false"], .sp-dialog[data-open="false"] { opacity: 1; transform: none; }
  .sp-swap[data-phase="out"], .sp-swap[data-phase="in"] { opacity: 1; margin-top: 0; }
}
`;

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const m = window.matchMedia("(prefers-reduced-motion: reduce)");
    const read = () => setReduced(m.matches);
    read();
    m.addEventListener("change", read);
    return () => m.removeEventListener("change", read);
  }, []);
  return reduced;
}

/**
 * The first thing you see. Pick where the traffic comes from and load it. Loading builds the world
 * and its plan and shows them on the map; nothing moves until Start.
 */
export default function SetupPanel() {
  const { sim, setupOpen, connection, notices, plan, aircraft } = useTowerState();
  const dispatch = useTowerDispatch();
  const { send } = useClient();
  const all = useMemo(() => sim?.scenarios ?? [], [sim?.scenarios]);
  const sims = useMemo(() => all.filter((s) => s.source !== "real"), [all]);
  const reals = useMemo(() => all.filter((s) => s.source === "real"), [all]);
  const regions = useMemo(() => {
    const by = new Map<string, { label: string; items: ScenarioInfo[] }>();
    for (const s of reals) {
      const key = s.meta?.region ?? s.name;
      if (!by.has(key)) by.set(key, { label: s.meta?.label ?? key, items: [] });
      by.get(key)!.items.push(s);
    }
    return Array.from(by.entries()).map(([key, v]) => ({ key, ...v }));
  }, [reals]);
  const liveRegions = useMemo(() => {
    const sent = Array.isArray(sim?.live_regions) ? sim.live_regions.filter((r) => r && typeof r.key === "string" && r.key) : [];
    if (sent.length > 0) return sent.map((r) => ({ key: r.key, label: typeof r.label === "string" && r.label ? r.label : r.key }));
    // An older backend does not say, so offer the regions it has recordings of.
    return regions.map((r) => ({ key: r.key, label: r.label }));
  }, [sim?.live_regions, regions]);

  const [source, setSource] = useState<Source>("sim");
  const [scenario, setScenario] = useState("");
  const [density, setDensity] = useState<number>(1);
  const [flights, setFlights] = useState<number>(12);
  const [pace, setPace] = useState<Pace>("normal");
  const [seed, setSeed] = useState<number>(1);
  const [region, setRegion] = useState("");
  const [realName, setRealName] = useState("");
  const [liveRegion, setLiveRegion] = useState("");
  const [cap, setCap] = useState<number>(80);
  const [loading, setLoading] = useState<Source | null>(null);
  const [stalled, setStalled] = useState(false);

  useEffect(() => {
    if (!scenario && sims.length > 0) setScenario(sims.find((s) => s.name === "demo")?.name ?? sims[0].name);
  }, [scenario, sims]);
  useEffect(() => {
    if (!region && regions.length > 0) setRegion(regions.find((r) => r.key === "europe-core")?.key ?? regions[0].key);
  }, [region, regions]);
  const windows = regions.find((r) => r.key === region)?.items ?? [];
  useEffect(() => {
    if (windows.length > 0 && !windows.some((w) => w.name === realName)) setRealName(windows[windows.length - 1].name);
  }, [windows, realName]);

  useEffect(() => {
    if (!liveRegions.some((r) => r.key === liveRegion) && liveRegions.length > 0) setLiveRegion(liveRegions.find((r) => r.key === "europe-core")?.key ?? liveRegions[0].key);
  }, [liveRegion, liveRegions]);

  // A new world_id means the load finished. So does an error notice: the load was refused.
  useEffect(() => {
    setLoading(null);
    setStalled(false);
  }, [sim?.world_id]);
  const lastError = notices.reduce((id, n) => (n.level === "error" ? n.id : id), 0);
  useEffect(() => setLoading(null), [lastError]);
  // Live mode has no progress event. Past LIVE_WAIT_MS the answer is not coming, so give the button back.
  useEffect(() => {
    if (loading !== "live") return;
    const h = setTimeout(() => {
      setLoading(null);
      setStalled(true);
    }, LIVE_WAIT_MS);
    return () => clearTimeout(h);
  }, [loading]);
  // Reopened over a live snapshot: show the card that says what is loaded.
  const isLive = sim?.source === "real" && sim.meta?.live === true;
  useEffect(() => {
    if (isLive) setSource("live");
  }, [isLive, sim?.world_id]);

  // Motion. Nothing below changes what is loaded or sent; it only changes when it is drawn.
  const reduced = useReducedMotion();
  // The sheet stays mounted for the length of its leave, so closing is not a cut.
  const [mounted, setMounted] = useState(setupOpen);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (setupOpen) {
      setMounted(true);
      const r = requestAnimationFrame(() => setOpen(true));
      return () => cancelAnimationFrame(r);
    }
    setOpen(false);
    if (reduced) {
      setMounted(false);
      return;
    }
    const t = setTimeout(() => setMounted(false), CLOSE_MS);
    return () => clearTimeout(t);
  }, [setupOpen, reduced]);

  // The middle lags the picked source by one fade, so the old content leaves before the new arrives.
  const [shown, setShown] = useState<Source>(source);
  const [phase, setPhase] = useState<"idle" | "out" | "in">("idle");
  useEffect(() => {
    if (shown === source) return;
    if (reduced) {
      setShown(source);
      return;
    }
    setPhase("out");
    const t = setTimeout(() => {
      setShown(source);
      setPhase("in");
    }, OUT_MS);
    return () => clearTimeout(t);
  }, [source, shown, reduced]);
  useEffect(() => {
    if (phase !== "in") return;
    // One frame at the offset, then release it so the transition has somewhere to go.
    const r = requestAnimationFrame(() => requestAnimationFrame(() => setPhase("idle")));
    return () => cancelAnimationFrame(r);
  }, [phase]);

  // The height the sheet eases to: whatever the middle measures, including a dropdown choice that
  // lengthens a summary line. Overflow is clipped only while it moves, so popovers are never cut.
  const innerRef = useRef<HTMLDivElement | null>(null);
  const measured = useRef<number | null>(null);
  const [height, setHeight] = useState<number | null>(null);
  const [moving, setMoving] = useState(false);
  useEffect(() => {
    const el = innerRef.current;
    if (!el || reduced || !mounted) return;
    const ro = new ResizeObserver(([entry]) => {
      // The border box, not the painted rect: while the sheet is still entering it is scaled, and a
      // scaled rect would settle the height 2% short of the content.
      const next = entry?.borderBoxSize?.[0]?.blockSize ?? el.offsetHeight;
      if (measured.current !== null && Math.abs(measured.current - next) < 0.5) return;
      if (measured.current !== null) setMoving(true);
      measured.current = next;
      setHeight(next);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [reduced, mounted]);
  useEffect(() => {
    if (mounted) return;
    measured.current = null;
    setHeight(null);
    setMoving(false);
  }, [mounted]);

  if (!mounted) return null;
  const hasWorld = (sim?.lifecycle ?? "idle") !== "idle";
  const chosenSim = sims.find((s) => s.name === scenario);
  const chosenReal = reals.find((s) => s.name === realName);
  const willLoad =
    source === "sim"
      ? scenario === CUSTOM ? flights : chosenSim ? Math.round(chosenSim.flights * density) : 0
      : source === "real" && chosenReal ? (cap > 0 ? Math.min(cap, chosenReal.flights) : chosenReal.flights) : 0;
  // Nobody knows how many flights are up there until the snapshot comes back.
  const canLoad = source === "live" ? liveRegions.some((r) => r.key === liveRegion) : willLoad > 0;
  const footer =
    source !== "live"
      ? willLoad > 0 ? `${willLoad} flights will load` : ""
      : loading === "live"
        ? "Taking a snapshot of the sky…"
        : stalled
          ? "The backend did not answer. Try again, or load a recorded hour from Real day."
          : canLoad ? (cap > 0 ? `Up to ${cap} flights will load` : "Every flight at cruise will load") : "";

  const load = () => {
    setStalled(false);
    setLoading(source);
    if (source === "sim" && scenario === CUSTOM) send({ type: "configure", source: "sim", scenario: CUSTOM, flights, pace, seed });
    else if (source === "sim" && scenario) send({ type: "configure", source: "sim", scenario, density });
    else if (source === "real" && realName) send({ type: "configure", source: "real", scenario: realName, max_flights: cap > 0 ? cap : undefined });
    else if (source === "live" && canLoad) send({ type: "configure", source: "live", region: liveRegion, max_flights: cap > 0 ? cap : undefined });
    else setLoading(null);
  };

  const snapshotAt = snapshotClock(sim?.meta?.snapshot_utc);
  const loadedFlights = plan?.paths.length || Object.values(aircraft).filter((a) => !a.is_intruder).length;
  const available = typeof sim?.meta?.flights_available === "number" && sim.meta.flights_available > loadedFlights ? sim.meta.flights_available : 0;

  // What the live snapshot on the map turned out to be. The sheet closes on load, so this is for whoever reopens it.
  const liveLoaded = isLive && (
    <p className="text-xs text-muted leading-relaxed">
      <span className="text-fg">On the map now: </span>
      {(sim?.meta?.label ?? sim?.meta?.region ?? "live snapshot").split(" (")[0]}
      {snapshotAt && <>, snapshot taken <span className="text-fg tabular-nums">{snapshotAt}</span></>}
      {loadedFlights > 0 && <>, <span className="text-fg tabular-nums">{loadedFlights}</span>{available > 0 && ` of ${available}`} flights</>}.
      {sim?.meta?.fallback === "saved_snapshot" && <span className="text-warn"> The live feed was unavailable, so this is the saved snapshot from that time.</span>}
      {typeof sim?.meta?.caveats === "string" && sim.meta.caveats && <span> {sim.meta.caveats}</span>}
    </p>
  );

  // A labelled row of short options. Four of them read faster as a segment than as a dropdown.
  const seg = (label: string, children: React.ReactNode) => (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs text-muted">{label}</span>
      <div className="seg self-start">{children}</div>
    </div>
  );
  const capControl = seg(
    "Most flights",
    CAPS.map((c) => (
      <button key={c} onClick={() => setCap(c)} aria-pressed={cap === c}>{c === 0 ? "All" : c}</button>
    )),
  );

  const scenarioOptions: SelectOption[] = [
    ...sims.map((s) => ({
      value: s.name,
      label: s.name.charAt(0).toUpperCase() + s.name.slice(1),
      detail: s.description || `${s.flights} flights`,
    })),
    // Beside the presets: your own sky.
    { value: CUSTOM, label: "Custom", detail: "How many aircraft, and how fast they arrive" },
  ];
  const regionOptions: SelectOption[] = regions.map((r) => ({
    value: r.key,
    label: shortName(r.label),
    detail: `${r.label} · ${r.items.length} ${r.items.length === 1 ? "hour" : "hours"}`,
  }));
  const liveRegionOptions: SelectOption[] = liveRegions.map((r) => ({
    value: r.key,
    label: shortName(r.label),
    detail: r.label,
  }));
  const windowOptions: SelectOption[] = windows.map((w) => ({
    value: w.name,
    label: `${prettyDate(w.meta?.date)}, ${hh(w.meta?.hour_utc)}:00Z`,
    detail: `${w.flights} flights · ${w.meta?.gates ?? 0} gates`,
  }));

  const attribution = <p className="text-[11px] text-muted">Flight data: adsb.lol, open under ODbL and CC0.</p>;
  const sourceLine = SOURCES.find((s) => s.key === shown)?.detail ?? "";

  return (
    <div className="absolute inset-0 z-40 flex items-center justify-center scrim sp-scrim p-6" data-open={open}>
      <style>{CSS}</style>
      <div className="panel sp-dialog w-full max-w-[520px] max-h-[92vh] flex flex-col overflow-hidden" data-open={open} role="dialog" aria-label="Choose a sky">
        <div className="px-5 pt-5 pb-3">
          <h1 className="text-base font-semibold tracking-tight">Choose a sky</h1>
        </div>

        <div className="px-5 grid grid-cols-3 gap-2">
          {SOURCES.map((s) => (
            <button key={s.key} onClick={() => setSource(s.key)} aria-pressed={source === s.key} style={{ padding: "8px 12px" }}
              className={`card-pick h-[56px] ${source === s.key ? "card-pick-on" : ""}`}>
              <div className="text-[11px] text-muted leading-tight">{s.eyebrow}</div>
              <div className="text-sm font-semibold mt-0.5 leading-tight">{s.name}</div>
            </button>
          ))}
        </div>
        {/* The sheet eases between the heights its three sources want, and the middle cross-fades. */}
        <div
          className="sp-h scroll-thin"
          // Clipped only while it moves. At rest it is exactly as tall as its content, so the auto
          // never shows a bar; it is there for a window too short to hold the sheet.
          style={{ height: reduced || height === null ? undefined : height, overflowY: moving ? "hidden" : "auto" }}
          onTransitionEnd={(e) => {
            if (e.propertyName === "height" && e.target === e.currentTarget) setMoving(false);
          }}
        >
          <div ref={innerRef} className="sp-swap" data-phase={phase}>
            <p className="px-5 pt-2 text-xs text-muted leading-snug">{sourceLine}</p>
            <div className="px-5 py-4 min-h-[9.5rem] flex flex-col gap-4">
              {shown === "sim" ? (
                sims.length === 0 ? (
                  <p className="text-sm text-muted">{connection === "connecting" ? "Connecting to the backend." : "No scenarios reported by the backend."}</p>
                ) : (
                  <>
                    <div className="grid grid-cols-2 gap-3 items-end">
                      <Select label="Scenario" options={scenarioOptions} value={scenario} onChange={setScenario} />
                      {scenario === CUSTOM
                        ? seg("How fast they arrive", PACES.map((p) => (
                            <button key={p.key} onClick={() => setPace(p.key)} aria-pressed={pace === p.key} title={p.every}>{p.label}</button>
                          )))
                        : seg("Traffic density", DENSITIES.map((d) => (
                            <button key={d} onClick={() => setDensity(d)} aria-pressed={density === d}>{d}x</button>
                          )))}
                    </div>
                    {scenario === CUSTOM ? (
                      <>
                        <label className="flex flex-col gap-1.5">
                          <span className="flex items-baseline justify-between text-xs text-muted">
                            <span>Aircraft</span>
                            <span className="font-mono tabular-nums text-fg">{flights}</span>
                          </span>
                          <input type="range" min={2} max={80} step={1} value={flights} onChange={(e) => setFlights(Number(e.target.value))} />
                        </label>
                        <div className="flex items-center gap-3">
                          <button type="button" onClick={() => setSeed((n) => n + 1)} className="btn">Shuffle</button>
                          <p className="text-xs text-muted leading-snug">
                            Draw <span className="font-mono text-fg">{seed}</span>, {PACES.find((p) => p.key === pace)?.every}, three already entering at Start. The same settings always give the same sky.
                          </p>
                        </div>
                      </>
                    ) : (
                      chosenSim?.description && <p className="text-xs text-muted leading-snug">{chosenSim.description}</p>
                    )}
                  </>
                )
              ) : shown === "live" ? (
                liveRegions.length === 0 ? (
                  <div className="text-sm text-muted flex flex-col gap-2">
                    {liveLoaded}
                    <p className="text-fg">{connection === "connecting" ? "Connecting to the backend." : "No regions to take a snapshot of."}</p>
                    <p className="text-xs">The backend did not list any live regions and has no recorded ones to fall back on. Pull the repo and restart it, or use Simulated.</p>
                  </div>
                ) : (
                  <>
                    {liveLoaded}
                    <div className="grid grid-cols-2 gap-3 items-end">
                      <Select label="Region" options={liveRegionOptions} value={liveRegion} onChange={setLiveRegion} />
                      {capControl}
                    </div>
                    <div className="flex flex-col gap-1">
                      <p className="text-xs text-muted leading-snug">From Start the simulator flies the snapshot, because real aircraft will not obey squack; squack&apos;s plan is drawn over each track.</p>
                      {attribution}
                    </div>
                  </>
                )
              ) : reals.length === 0 ? (
                <div className="text-sm text-muted flex flex-col gap-2">
                  <p className="text-fg">No real-traffic scenarios are installed.</p>
                  <p className="text-xs">Build them with backend/tools/real_extract.py and real_build.py, or pull the repo: the built files live in backend/scenarios/real.</p>
                </div>
              ) : (
                <>
                  <div className="grid grid-cols-2 gap-3">
                    <Select label="Region" options={regionOptions} value={region} onChange={setRegion} />
                    <Select label="Day and hour, UTC" options={windowOptions} value={realName} onChange={setRealName} />
                  </div>
                  {capControl}
                  <div className="flex flex-col gap-1">
                    <p className="text-xs text-muted leading-snug">Left alone each flight flies the track it actually flew; squack&apos;s plan is drawn over it.</p>
                    {attribution}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>

        <div className="px-5 py-3 border-t border-line flex items-center justify-between gap-3">
          <VoiceToggle />
          <div className="flex items-center gap-3 shrink-0">
            <span className={`text-xs ${stalled && source === "live" ? "text-warn" : "text-muted"}`} role="status">{footer}</span>
            {hasWorld && (
              <button onClick={() => dispatch({ type: "set_setup_open", open: false })} className="btn">
                Cancel
              </button>
            )}
            <button onClick={load} disabled={loading !== null || !canLoad} className="btn btn-primary">
              {loading ? "Loading" : "Load sky"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
