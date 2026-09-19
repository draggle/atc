"use client";

import { useEffect, useMemo, useState } from "react";
import { snapshotClock, useTowerDispatch, useTowerState } from "@/lib/store";
import { useClient } from "./TowerApp";
import type { ScenarioInfo } from "@/lib/types";

const DENSITIES = [1, 1.5, 2, 2.5] as const;
const CAPS = [40, 80, 120, 0] as const; // 0 = every flight
const LIVE_WAIT_MS = 15000; // a live snapshot is one network fetch on the backend, about 8 s at worst

type Source = "sim" | "real" | "live";

function prettyDate(iso?: string): string {
  if (!iso) return "";
  const d = new Date(`${iso}T12:00:00Z`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("en-CA", { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
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
  // Reopened over a live snapshot: show the tab that says what is loaded.
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
          ? "The backend did not answer. Try again, or load a recorded hour from Real traffic."
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

  const tab = (active: boolean) =>
    `flex-1 px-3 py-2 text-sm font-medium border-b-2 transition-colors ${active ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg"}`;
  const pick = (active: boolean) =>
    `text-left rounded-md border px-3 py-2 transition-colors ${active ? "border-accent/60 bg-accent/10" : "border-line bg-panel-2/70 hover:border-muted"}`;
  const seg = (active: boolean) => `px-3 py-1 font-mono text-sm ${active ? "bg-accent/20 text-accent" : "bg-panel-2/70 text-muted hover:text-fg"}`;

  // What the live snapshot on the map turned out to be. The panel closes on load, so this is for whoever reopens it.
  const liveLoaded = isLive && (
    <div className="rounded-md border border-accent/30 bg-accent/5 px-3 py-2 text-xs text-muted leading-relaxed">
      <span className="text-fg">On the map now: </span>
      {(sim?.meta?.label ?? sim?.meta?.region ?? "live snapshot").split(" (")[0]}
      {snapshotAt && <>, snapshot taken <span className="font-mono text-fg">{snapshotAt}</span></>}
      {loadedFlights > 0 && <>, <span className="font-mono text-fg">{loadedFlights}</span>{available > 0 && ` of ${available}`} flights</>}.
      {sim?.meta?.fallback === "saved_snapshot" && <span className="text-warn"> The live feed was unavailable, so this is the saved snapshot from that time.</span>}
      {typeof sim?.meta?.caveats === "string" && sim.meta.caveats && <span> {sim.meta.caveats}</span>}
    </div>
  );

  // Real and live traffic share one cap.
  const capControl = (
    <div className="flex flex-col gap-2">
      <span className="eyebrow">Most flights to load</span>
      <div className="flex rounded-md border border-line overflow-hidden w-fit">
        {CAPS.map((c) => (
          <button key={c} onClick={() => setCap(c)} aria-pressed={cap === c} className={seg(cap === c)}>{c === 0 ? "All" : c}</button>
        ))}
      </div>
    </div>
  );

  return (
    <div className="absolute inset-0 z-40 flex items-center justify-center bg-bg/75 backdrop-blur-sm p-6">
      <div className="panel w-full max-w-2xl p-0 overflow-hidden max-h-[92vh] flex flex-col" role="dialog" aria-label="Set up the airspace">
        <div className="px-5 pt-5 pb-3">
          <div className="eyebrow mb-1">Tower</div>
          <h1 className="text-xl font-bold tracking-tight">Set up the airspace</h1>
          <p className="text-sm text-muted mt-1">Choose the traffic, load it, look at the plan, then press Start. Nothing moves until you do.</p>
        </div>

        <div className="flex px-5 border-b border-line">
          <button className={tab(source === "sim")} aria-pressed={source === "sim"} onClick={() => setSource("sim")}>Simulated traffic</button>
          <button className={tab(source === "real")} aria-pressed={source === "real"} onClick={() => setSource("real")}>Real traffic</button>
          <button className={tab(source === "live")} aria-pressed={source === "live"} onClick={() => setSource("live")}>Live sky</button>
        </div>

        {/* One height for every tab, so the panel does not jump when you switch. */}
        <div className="h-[28rem] overflow-y-auto scroll-thin">
          {source === "sim" ? (
            <div className="p-5 flex flex-col gap-4">
              <div className="flex flex-col gap-2">
                <span className="eyebrow">Scenario</span>
                {sims.length === 0 && <p className="text-sm text-muted">{connection === "connecting" ? "Connecting to the backend." : "No scenarios reported by the backend."}</p>}
                {sims.map((s) => (
                  <button key={s.name} onClick={() => setScenario(s.name)} className={pick(scenario === s.name)}>
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="text-sm font-medium capitalize">{s.name}</span>
                      <span className="text-xs font-mono text-muted">{s.flights} flights</span>
                    </div>
                    {s.description && <p className="text-xs text-muted mt-0.5">{s.description}</p>}
                  </button>
                ))}
              </div>
              <div className="flex flex-col gap-2">
                <span className="eyebrow">Traffic density</span>
                <div className="flex rounded-md border border-line overflow-hidden w-fit">
                  {DENSITIES.map((d) => (
                    <button key={d} onClick={() => setDensity(d)} className={seg(density === d)}>{d}x</button>
                  ))}
                </div>
              </div>
            </div>
          ) : source === "live" ? (
            liveRegions.length === 0 ? (
              <div className="p-5 text-sm text-muted flex flex-col gap-2">
                {liveLoaded}
                <p className="text-fg">{connection === "connecting" ? "Connecting to the backend." : "No regions to take a snapshot of."}</p>
                <p>The backend did not list any live regions and has no recorded ones to fall back on. Pull the repo and restart it, or use Simulated traffic.</p>
              </div>
            ) : (
              <div className="p-5 flex flex-col gap-4">
                {liveLoaded}
                <div className="flex flex-col gap-2">
                  <span className="eyebrow">Region</span>
                  <div className="grid grid-cols-2 gap-2">
                    {liveRegions.map((r) => (
                      <button key={r.key} onClick={() => setLiveRegion(r.key)} aria-pressed={liveRegion === r.key} className={pick(liveRegion === r.key)}>
                        <div className="text-sm font-medium">{r.label}</div>
                        <div className="text-xs font-mono text-muted mt-0.5">as it is right now</div>
                      </button>
                    ))}
                  </div>
                </div>
                {capControl}
                <p className="text-xs text-muted leading-relaxed">
                  This is one snapshot of the airline traffic at cruise over the region right now. From the moment you press Start the simulator flies it, because real aircraft will not obey Tower. Each flight&apos;s dashed line is its current track projected to the region boundary. Tower&apos;s plan is drawn over it. Flight data: adsb.lol, open under ODbL and CC0.
                </p>
              </div>
            )
          ) : reals.length === 0 ? (
            <div className="p-5 text-sm text-muted flex flex-col gap-2">
              <p className="text-fg">No real-traffic scenarios are installed.</p>
              <p>Build them with backend/tools/real_extract.py and real_build.py, or pull the repo: the built files live in backend/scenarios/real.</p>
            </div>
          ) : (
            <div className="p-5 flex flex-col gap-4">
              <div className="flex flex-col gap-2">
                <span className="eyebrow">Region</span>
                <div className="grid grid-cols-2 gap-2">
                  {regions.map((r) => (
                    <button key={r.key} onClick={() => setRegion(r.key)} className={pick(region === r.key)}>
                      <div className="text-sm font-medium">{r.label}</div>
                      <div className="text-xs font-mono text-muted mt-0.5">{r.items.length} time windows</div>
                    </button>
                  ))}
                </div>
              </div>
              <div className="flex flex-col gap-2">
                <span className="eyebrow">Day and hour, UTC</span>
                <div className="grid grid-cols-2 gap-2">
                  {windows.map((w) => (
                    <button key={w.name} onClick={() => setRealName(w.name)} className={pick(realName === w.name)}>
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="text-sm font-medium">{prettyDate(w.meta?.date)}</span>
                        <span className="text-xs font-mono text-muted">{w.flights} flights</span>
                      </div>
                      <div className="text-xs font-mono text-muted mt-0.5">
                        {String(w.meta?.hour_utc ?? 0).padStart(2, "0")}:00 to {String((w.meta?.hour_utc ?? 0) + 1).padStart(2, "0")}:00 · {w.meta?.gates ?? 0} gates
                      </div>
                    </button>
                  ))}
                </div>
              </div>
              {capControl}
              <p className="text-xs text-muted leading-relaxed">
                These are airline flights that really crossed the region at cruise level that hour. Left alone, each one flies the track it actually flew. Tower&apos;s plan is drawn over it. Flight data: adsb.lol, open under ODbL and CC0.
              </p>
            </div>
          )}
        </div>

        <div className="px-5 py-4 border-t border-line flex items-center justify-between gap-3">
          <span className={`text-xs font-mono ${stalled && source === "live" ? "text-warn" : "text-muted"}`} role="status">{footer}</span>
          <div className="flex gap-2 shrink-0">
            {hasWorld && (
              <button onClick={() => dispatch({ type: "set_setup_open", open: false })} className="px-4 py-1.5 rounded-md border border-line bg-panel-2/70 text-sm text-muted hover:text-fg">
                Cancel
              </button>
            )}
            <button
              onClick={load}
              disabled={loading !== null || !canLoad}
              className="px-4 py-1.5 rounded-md border border-accent/50 bg-accent/20 text-accent text-sm font-medium hover:bg-accent/30 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {loading ? "Loading" : "Load"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
