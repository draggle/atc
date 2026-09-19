"use client";

import { useEffect, useMemo, useState } from "react";
import { useTowerDispatch, useTowerState } from "@/lib/store";
import { useClient } from "./TowerApp";

const DENSITIES = [1, 1.5, 2, 2.5] as const;

/**
 * The first thing you see. Pick where the traffic comes from and load it. Loading builds the world
 * and its plan and shows them on the map; nothing moves until Start.
 */
export default function SetupPanel() {
  const { sim, setupOpen, connection } = useTowerState();
  const dispatch = useTowerDispatch();
  const { send } = useClient();
  const scenarios = useMemo(() => sim?.scenarios ?? [], [sim?.scenarios]);
  const [source, setSource] = useState<"sim" | "real">("sim");
  const [scenario, setScenario] = useState<string>("");
  const [density, setDensity] = useState<number>(1);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!scenario && scenarios.length > 0) {
      setScenario(scenarios.find((s) => s.name === "demo")?.name ?? scenarios[0].name);
    }
  }, [scenario, scenarios]);

  // A new world_id means the load finished.
  useEffect(() => setLoading(false), [sim?.world_id]);

  if (!setupOpen) return null;
  const hasWorld = (sim?.lifecycle ?? "idle") !== "idle";
  const chosen = scenarios.find((s) => s.name === scenario);
  const flights = chosen ? Math.round(chosen.flights * density) : 0;

  const load = () => {
    if (!scenario) return;
    setLoading(true);
    send({ type: "configure", source: "sim", scenario, density });
  };

  const tab = (active: boolean) =>
    `flex-1 px-3 py-2 text-sm font-medium border-b-2 transition-colors ${active ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg"}`;

  return (
    <div className="absolute inset-0 z-40 flex items-center justify-center bg-bg/80 backdrop-blur-sm p-6">
      <div className="panel w-full max-w-xl p-0 overflow-hidden" role="dialog" aria-label="Set up the airspace">
        <div className="px-5 pt-5 pb-3">
          <h1 className="text-xl font-semibold tracking-tight">Set up the airspace</h1>
          <p className="text-sm text-muted mt-1">Choose the traffic, load it, look at the plan, then press Start. Nothing moves until you do.</p>
        </div>

        <div className="flex px-5 border-b border-line">
          <button className={tab(source === "sim")} onClick={() => setSource("sim")}>Simulated traffic</button>
          <button className={tab(source === "real")} onClick={() => setSource("real")}>Real traffic</button>
        </div>

        {source === "sim" ? (
          <div className="p-5 flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <span className="text-xs uppercase tracking-wider text-muted">Scenario</span>
              {scenarios.length === 0 && (
                <p className="text-sm text-muted">{connection === "connecting" ? "Connecting to the backend." : "No scenarios reported by the backend."}</p>
              )}
              {scenarios.map((s) => (
                <button
                  key={s.name}
                  onClick={() => setScenario(s.name)}
                  className={`text-left rounded-md border px-3 py-2 transition-colors ${scenario === s.name ? "border-accent/60 bg-accent/10" : "border-line bg-panel-2 hover:border-muted"}`}
                >
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-sm font-medium capitalize">{s.name}</span>
                    <span className="text-xs font-mono text-muted">{s.flights} flights</span>
                  </div>
                  {s.description && <p className="text-xs text-muted mt-0.5">{s.description}</p>}
                </button>
              ))}
            </div>

            <div className="flex flex-col gap-2">
              <span className="text-xs uppercase tracking-wider text-muted">Traffic density</span>
              <div className="flex rounded-md border border-line overflow-hidden text-sm w-fit">
                {DENSITIES.map((d) => (
                  <button
                    key={d}
                    onClick={() => setDensity(d)}
                    className={`px-3 py-1 font-mono ${density === d ? "bg-accent/20 text-accent" : "bg-panel-2 text-muted hover:text-fg"}`}
                  >
                    {d}x
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="p-5 text-sm text-muted flex flex-col gap-2">
            <p className="text-fg">Real traffic is not wired in yet.</p>
            <p>You will pick a region and a past day, and that day&apos;s actual flights will load with the tracks they really flew. See phase 4 of docs/10-roadmap.md.</p>
          </div>
        )}

        <div className="px-5 py-4 border-t border-line flex items-center justify-between gap-3">
          <span className="text-xs text-muted">{source === "sim" && chosen ? `${flights} flights will load` : ""}</span>
          <div className="flex gap-2">
            {hasWorld && (
              <button
                onClick={() => dispatch({ type: "set_setup_open", open: false })}
                className="px-4 py-1.5 rounded-md border border-line bg-panel-2 text-sm text-muted hover:text-fg"
              >
                Cancel
              </button>
            )}
            <button
              onClick={load}
              disabled={source !== "sim" || !scenario || loading}
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
