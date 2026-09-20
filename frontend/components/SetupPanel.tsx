"use client";

import { useEffect, useMemo, useState } from "react";
import { snapshotClock, useTowerDispatch, useTowerState } from "@/lib/store";
import { useClient } from "./TowerApp";
import VoiceToggle from "./VoiceToggle";
import type { ScenarioInfo } from "@/lib/types";

const DENSITIES = [1, 1.5, 2, 2.5] as const;
const CAPS = [40, 80, 120, 0] as const; // 0 = every flight
const LIVE_WAIT_MS = 15000; // a live snapshot is one network fetch on the backend, about 8 s at worst

type Source = "sim" | "real" | "live";

const SOURCES: { key: Source; eyebrow: string; name: string; detail: string }[] = [
  { key: "real", eyebrow: "Recorded", name: "Real day", detail: "Airline flights that really crossed a region at cruise, one hour of them." },
  { key: "live", eyebrow: "Right now", name: "Live snapshot", detail: "One snapshot of the sky over a region as it is this minute." },
  { key: "sim", eyebrow: "Scripted", name: "Simulated", detail: "Synthetic traffic built to show conflicts, storms and readback errors." },
];

function prettyDate(iso?: string): string {
  if (!iso) return "";
  const d = new Date(`${iso}T12:00:00Z`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("en-CA", { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

const hh = (h?: number) => String(h ?? 0).padStart(2, "0");

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

  if (!setupOpen) return null;
  const hasWorld = (sim?.lifecycle ?? "idle") !== "idle";
  const chosenSim = sims.find((s) => s.name === scenario);
  const chosenReal = reals.find((s) => s.name === realName);
  const willLoad =
    source === "sim"
      ? chosenSim ? Math.round(chosenSim.flights * density) : 0
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
    if (source === "sim" && scenario) send({ type: "configure", source: "sim", scenario, density });
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

  const field = (label: string, children: React.ReactNode) => (
    <div className="flex flex-col gap-2">
      <span className="text-xs text-muted">{label}</span>
      <div className="flex flex-wrap gap-1.5">{children}</div>
    </div>
  );
  const pill = (active: boolean, onClick: () => void, label: React.ReactNode, key: string | number) => (
    <button key={key} onClick={onClick} aria-pressed={active} className={`pill ${active ? "pill-on" : ""}`}>{label}</button>
  );

  // Real and live traffic share one cap.
  const capControl = field("Most flights to load", CAPS.map((c) => pill(cap === c, () => setCap(c), c === 0 ? "All" : c, c)));
  const chosenWindow = windows.find((w) => w.name === realName);

  return (
    <div className="absolute inset-0 z-40 flex items-center justify-center scrim p-6">
      <div className="panel w-full max-w-[720px] max-h-[92vh] flex flex-col overflow-hidden" role="dialog" aria-label="Choose a sky">
        <div className="px-6 pt-6 pb-4">
          <h1 className="text-lg font-semibold tracking-tight">Choose a sky</h1>
          <p className="text-sm text-muted mt-1">Pick where the traffic comes from and load it. Nothing moves until you press Start.</p>
        </div>

        <div className="px-6 grid grid-cols-3 gap-2">
          {SOURCES.map((s) => (
            <button key={s.key} onClick={() => setSource(s.key)} aria-pressed={source === s.key} className={`card-pick ${source === s.key ? "card-pick-on" : ""}`}>
              <div className="text-[11px] text-muted">{s.eyebrow}</div>
              <div className="text-sm font-semibold mt-1">{s.name}</div>
              <div className="text-xs text-muted mt-1 leading-relaxed">{s.detail}</div>
            </button>
          ))}
        </div>

        {/* One minimum height for every source, so the sheet does not jump when you switch. */}
        <div className="px-6 py-5 min-h-[15rem] overflow-y-auto scroll-thin flex flex-col gap-5">
          {source === "sim" ? (
            <>
              {sims.length === 0 ? (
                <p className="text-sm text-muted">{connection === "connecting" ? "Connecting to the backend." : "No scenarios reported by the backend."}</p>
              ) : (
                <>
                  {field("Scenario", sims.map((s) => pill(scenario === s.name, () => setScenario(s.name), <><span className="capitalize">{s.name}</span><span className="opacity-60 tabular-nums">{s.flights}</span></>, s.name)))}
                  {chosenSim?.description && <p className="text-xs text-muted leading-relaxed -mt-2">{chosenSim.description}</p>}
                </>
              )}
              {field("Traffic density", DENSITIES.map((d) => pill(density === d, () => setDensity(d), `${d}x`, d)))}
            </>
          ) : source === "live" ? (
            liveRegions.length === 0 ? (
              <div className="text-sm text-muted flex flex-col gap-2">
                {liveLoaded}
                <p className="text-fg">{connection === "connecting" ? "Connecting to the backend." : "No regions to take a snapshot of."}</p>
                <p>The backend did not list any live regions and has no recorded ones to fall back on. Pull the repo and restart it, or use Simulated.</p>
              </div>
            ) : (
              <>
                {liveLoaded}
                {field("Region", liveRegions.map((r) => pill(liveRegion === r.key, () => setLiveRegion(r.key), r.label, r.key)))}
                {capControl}
                <p className="text-xs text-muted leading-relaxed">
                  This is one snapshot of the airline traffic at cruise over the region right now. From the moment you press Start the simulator flies it, because real aircraft will not obey squack. Each flight&apos;s dashed line is its current track projected to the region boundary. squack&apos;s plan is drawn over it. Flight data: adsb.lol, open under ODbL and CC0.
                </p>
              </>
            )
          ) : reals.length === 0 ? (
            <div className="text-sm text-muted flex flex-col gap-2">
              <p className="text-fg">No real-traffic scenarios are installed.</p>
              <p>Build them with backend/tools/real_extract.py and real_build.py, or pull the repo: the built files live in backend/scenarios/real.</p>
            </div>
          ) : (
            <>
              {field("Region", regions.map((r) => pill(region === r.key, () => setRegion(r.key), <>{r.label}<span className="opacity-60 tabular-nums">{r.items.length}</span></>, r.key)))}
              {field(
                "Day and hour, UTC",
                windows.map((w) => pill(realName === w.name, () => setRealName(w.name), <>{prettyDate(w.meta?.date)}<span className="opacity-60 tabular-nums">{hh(w.meta?.hour_utc)}:00</span></>, w.name)),
              )}
              {chosenWindow && (
                <p className="text-xs text-muted -mt-2 tabular-nums">
                  {hh(chosenWindow.meta?.hour_utc)}:00 to {hh((chosenWindow.meta?.hour_utc ?? 0) + 1)}:00 · {chosenWindow.flights} flights · {chosenWindow.meta?.gates ?? 0} gates
                </p>
              )}
              {capControl}
              <p className="text-xs text-muted leading-relaxed">
                These are airline flights that really crossed the region at cruise level that hour. Left alone, each one flies the track it actually flew. squack&apos;s plan is drawn over it. Flight data: adsb.lol, open under ODbL and CC0.
              </p>
            </>
          )}
        </div>

        <div className="px-6 py-4 border-t border-line flex items-center justify-between gap-3">
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
