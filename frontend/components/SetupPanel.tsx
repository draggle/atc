"use client";

import { useEffect, useMemo, useState } from "react";
import { useTowerDispatch, useTowerState } from "@/lib/store";
import { useClient } from "./TowerApp";
import type { ScenarioInfo } from "@/lib/types";

const DENSITIES = [1, 1.5, 2, 2.5] as const;
const CAPS = [40, 80, 120, 0] as const; // 0 = every flight

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
  const { sim, setupOpen, connection } = useTowerState();
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

  const [source, setSource] = useState<"sim" | "real">("sim");
  const [scenario, setScenario] = useState("");
  const [density, setDensity] = useState<number>(1);
  const [region, setRegion] = useState("");
  const [realName, setRealName] = useState("");
  const [cap, setCap] = useState<number>(80);
  const [loading, setLoading] = useState(false);

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

  // A new world_id means the load finished.
  useEffect(() => setLoading(false), [sim?.world_id]);

  if (!setupOpen) return null;
  const hasWorld = (sim?.lifecycle ?? "idle") !== "idle";
  const chosenSim = sims.find((s) => s.name === scenario);
  const chosenReal = reals.find((s) => s.name === realName);
  const willLoad =
    source === "sim"
      ? chosenSim ? Math.round(chosenSim.flights * density) : 0
      : chosenReal ? (cap > 0 ? Math.min(cap, chosenReal.flights) : chosenReal.flights) : 0;

  const load = () => {
    setLoading(true);
    if (source === "sim" && scenario) send({ type: "configure", source: "sim", scenario, density });
    else if (source === "real" && realName) send({ type: "configure", source: "real", scenario: realName, max_flights: cap > 0 ? cap : undefined });
    else setLoading(false);
  };

  const tab = (active: boolean) =>
    `flex-1 px-3 py-2 text-sm font-medium border-b-2 transition-colors ${active ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg"}`;
  const pick = (active: boolean) =>
    `text-left rounded-md border px-3 py-2 transition-colors ${active ? "border-accent/60 bg-accent/10" : "border-line bg-panel-2/70 hover:border-muted"}`;
  const seg = (active: boolean) => `px-3 py-1 font-mono text-sm ${active ? "bg-accent/20 text-accent" : "bg-panel-2/70 text-muted hover:text-fg"}`;

  return (
    <div className="absolute inset-0 z-40 flex items-center justify-center bg-bg/75 backdrop-blur-sm p-6">
      <div className="panel w-full max-w-2xl p-0 overflow-hidden max-h-[92vh] flex flex-col" role="dialog" aria-label="Set up the airspace">
        <div className="px-5 pt-5 pb-3">
          <div className="eyebrow mb-1">Tower</div>
          <h1 className="text-xl font-bold tracking-tight">Set up the airspace</h1>
          <p className="text-sm text-muted mt-1">Choose the traffic, load it, look at the plan, then press Start. Nothing moves until you do.</p>
        </div>

        <div className="flex px-5 border-b border-line">
          <button className={tab(source === "sim")} onClick={() => setSource("sim")}>Simulated traffic</button>
          <button className={tab(source === "real")} onClick={() => setSource("real")}>Real traffic</button>
        </div>

        <div className="overflow-y-auto scroll-thin">
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
              <div className="flex flex-col gap-2">
                <span className="eyebrow">Most flights to load</span>
                <div className="flex rounded-md border border-line overflow-hidden w-fit">
                  {CAPS.map((c) => (
                    <button key={c} onClick={() => setCap(c)} className={seg(cap === c)}>{c === 0 ? "All" : c}</button>
                  ))}
                </div>
              </div>
              <p className="text-xs text-muted leading-relaxed">
                These are airline flights that really crossed the region at cruise level that hour. Left alone, each one flies the track it actually flew. Tower&apos;s plan is drawn over it. Flight data: adsb.lol, open under ODbL and CC0.
              </p>
            </div>
          )}
        </div>

        <div className="px-5 py-4 border-t border-line flex items-center justify-between gap-3">
          <span className="text-xs font-mono text-muted">{willLoad > 0 ? `${willLoad} flights will load` : ""}</span>
          <div className="flex gap-2">
            {hasWorld && (
              <button onClick={() => dispatch({ type: "set_setup_open", open: false })} className="px-4 py-1.5 rounded-md border border-line bg-panel-2/70 text-sm text-muted hover:text-fg">
                Cancel
              </button>
            )}
            <button
              onClick={load}
              disabled={loading || willLoad === 0}
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
