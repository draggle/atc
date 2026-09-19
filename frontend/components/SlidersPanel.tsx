"use client";

import { useEffect, useRef } from "react";
import { useTowerDispatch, useTowerState, type Sliders } from "@/lib/store";
import { useClient } from "./TowerApp";

function Slider({ label, value, min, max, step, fmt, onChange }: { label: string; value: number; min: number; max: number; step: number; fmt: (v: number) => string; onChange: (v: number) => void }) {
  return (
    <label className="grid grid-cols-[7rem_1fr_3.5rem] items-center gap-2 text-xs">
      <span className="text-muted">{label}</span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} className="w-full" />
      <span className="font-mono text-right tabular-nums">{fmt(value)}</span>
    </label>
  );
}

export default function SlidersPanel() {
  const { sliders } = useTowerState();
  const dispatch = useTowerDispatch();
  const { send } = useClient();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Debounce the network message; the local state updates immediately.
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => send({ type: "set_sliders", ...sliders }), 250);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [sliders, send]);

  const set = (patch: Partial<Sliders>) => dispatch({ type: "set_sliders", sliders: { ...sliders, ...patch } });

  return (
    <section className="panel p-2.5 shrink-0">
      <h2 className="text-xs uppercase tracking-wider text-muted mb-2">Chaos</h2>
      <div className="flex flex-col gap-2">
        <Slider label="Separation buffer" value={sliders.buffer_nm} min={0} max={10} step={0.5} fmt={(v) => `+${v.toFixed(1)} NM`} onChange={(v) => set({ buffer_nm: v })} />
        <Slider label="Pilot error rate" value={sliders.error_rate} min={0} max={0.5} step={0.05} fmt={(v) => `${Math.round(v * 100)}%`} onChange={(v) => set({ error_rate: v })} />
        <Slider label="Radio noise" value={sliders.noise} min={0} max={1} step={0.05} fmt={(v) => v.toFixed(2)} onChange={(v) => set({ noise: v })} />
      </div>
    </section>
  );
}
