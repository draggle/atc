"use client";

import { useEffect, useRef } from "react";
import { useTowerDispatch, useTowerState, type Sliders } from "@/lib/store";
import { useClient } from "./TowerApp";

/** A thin white track and a small white thumb, drawn by us so it looks the same in every browser. */
const TRACK =
  "w-full h-4 bg-transparent appearance-none cursor-pointer " +
  "[&::-webkit-slider-runnable-track]:h-px [&::-webkit-slider-runnable-track]:bg-fg/35 " +
  "[&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:-mt-[5.5px] [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-fg " +
  "[&::-moz-range-track]:h-px [&::-moz-range-track]:bg-fg/35 " +
  "[&::-moz-range-thumb]:h-3 [&::-moz-range-thumb]:w-3 [&::-moz-range-thumb]:border-0 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:bg-fg";

function Slider({ label, value, min, max, step, fmt, onChange }: { label: string; value: number; min: number; max: number; step: number; fmt: (v: number) => string; onChange: (v: number) => void }) {
  return (
    <label className="grid grid-cols-[7.5rem_1fr_3.5rem] items-center gap-3 text-xs">
      <span className="text-fg">{label}</span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} className={TRACK} />
      <span className="font-mono text-right tabular-nums text-muted">{fmt(value)}</span>
    </label>
  );
}

export default function SlidersPanel() {
  const { sliders, sim } = useTowerState();
  // A pilot can only get a readback wrong when there is one: spoken, at a speed speech can keep up with.
  const noReadbacks = (sim?.speed ?? 1) > 1.5;
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
    <section className="panel px-3 py-2.5 shrink-0">
      <h2 className="text-sm font-semibold text-fg mb-2">Chaos</h2>
      <div className="flex flex-col gap-2.5">
        <Slider label="Separation buffer" value={sliders.buffer_nm} min={0} max={10} step={0.5} fmt={(v) => `+${v.toFixed(1)} NM`} onChange={(v) => set({ buffer_nm: v })} />
        <Slider label="Pilot error rate" value={sliders.error_rate} min={0} max={0.5} step={0.05} fmt={(v) => `${Math.round(v * 100)}%`} onChange={(v) => set({ error_rate: v })} />
        <Slider label="Radio noise" value={sliders.noise} min={0} max={1} step={0.05} fmt={(v) => v.toFixed(2)} onChange={(v) => set({ noise: v })} />
      </div>
      <p className={`mt-2.5 text-xs leading-snug ${noReadbacks && sliders.error_rate > 0 ? "text-warn" : "text-muted"}`}>
        {noReadbacks
          ? "Above 1.5x every instruction goes by data link: nothing is spoken, so no readback can go wrong. Drop to 1x to hear pilots."
          : "Pilot errors happen in spoken readbacks. Say a card, or switch to Auto, and some will come back wrong."}
      </p>
    </section>
  );
}
