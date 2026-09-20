"use client";

import { useEffect, useRef, useState } from "react";
import { useTowerDispatch, useTowerState, type Sliders } from "@/lib/store";
import { radio } from "@/lib/radio";
import { useClient } from "./TowerApp";
import VoiceToggle from "./VoiceToggle";
import { scenarioLabel } from "./TopBar";

/** A thin white track and a small white thumb, drawn by us so it looks the same in every browser. */
const TRACK =
  "w-full h-4 bg-transparent appearance-none cursor-pointer " +
  "[&::-webkit-slider-runnable-track]:h-px [&::-webkit-slider-runnable-track]:bg-fg/35 " +
  "[&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:-mt-[5.5px] [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-fg " +
  "[&::-moz-range-track]:h-px [&::-moz-range-track]:bg-fg/35 " +
  "[&::-moz-range-thumb]:h-3 [&::-moz-range-thumb]:w-3 [&::-moz-range-thumb]:border-0 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:bg-fg";

const READBACKS = [
  ["random", "By chance"],
  ["correct", "Correct"],
  ["wrong_value", "Wrong value"],
  ["wrong_aircraft", "Wrong plane"],
  ["missing_readback", "No reply"],
] as const;

/** One row: a sentence on the left, its control on the right. */
function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 min-h-8">
      <span className="text-[13px] text-fg" title={hint}>{label}</span>
      <div className="flex items-center gap-1.5 shrink-0">{children}</div>
    </div>
  );
}

function Group({ title, caption, children }: { title: string; caption?: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2.5 pt-5 first:pt-0">
      <div className="flex items-baseline gap-2">
        <h2 className="text-xs font-semibold text-muted">{title}</h2>
        {caption && <span className="text-[11px] text-muted/70">{caption}</span>}
      </div>
      {children}
    </section>
  );
}

function Pill({ on, onClick, title, children }: { on: boolean; onClick: () => void; title?: string; children: React.ReactNode }) {
  return (
    <button onClick={onClick} aria-pressed={on} title={title} className={`pill ${on ? "pill-on" : ""}`}>
      {children}
    </button>
  );
}

function Slider({ label, value, min, max, step, fmt, onChange }: { label: string; value: number; min: number; max: number; step: number; fmt: (v: number) => string; onChange: (v: number) => void }) {
  return (
    <label className="grid grid-cols-[8rem_1fr_3.5rem] items-center gap-3 text-[13px]">
      <span className="text-fg">{label}</span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} className={TRACK} />
      <span className="font-mono text-xs text-right tabular-nums text-muted">{fmt(value)}</span>
    </label>
  );
}

/** Legend swatches, drawn as CSS so they match the map layers exactly. */
function swatch(kind: "solid" | "dashed" | "dotted" | "ring" | "wedge", color: string, opacity = 1) {
  return kind === "ring" ? (
    <span className="inline-block h-2.5 w-2.5 rounded-full border-[1.5px] shrink-0" style={{ borderColor: color, opacity }} />
  ) : kind === "wedge" ? (
    <span className="inline-block h-0 w-0 shrink-0" style={{ borderLeft: "10px solid transparent", borderBottom: `9px solid ${color}`, opacity }} />
  ) : (
    <span className="inline-block w-5 shrink-0 border-t" style={{ borderColor: color, borderTopStyle: kind, borderTopWidth: kind === "solid" ? 1.5 : 1, opacity }} />
  );
}

/**
 * Every control that is not a decision about the traffic lives here, in four groups: Sky, Radio,
 * Screen, Chaos. Always mounted (the slide-in needs it, and the slider debounce must outlive a
 * close); hidden with `inert` so nothing inside can take focus.
 */
export default function SettingsSheet() {
  const { sim, settingsOpen, connection, sliders, planView, view, scoreboard, riskPairs } = useTowerState();
  const dispatch = useTowerDispatch();
  const { send } = useClient();
  const open = settingsOpen;
  const close = () => dispatch({ type: "set_settings_open", open: false });

  const towerOn = sim?.tower_enabled ?? true;
  const speakReplies = sim?.speak_replies ?? true;
  const nextReadback = sim?.next_readback ?? "random";
  const [radioMuted, setRadioMuted] = useState(false);
  useEffect(() => setRadioMuted(radio?.muted ?? false), []);

  // Escape closes. Only while open, so the key is free for everything else.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  // Chaos sliders: the local state moves at once, the network message is debounced.
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => send({ type: "set_sliders", ...sliders }), 250);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [sliders, send]);
  const setSliders = (patch: Partial<Sliders>) => dispatch({ type: "set_sliders", sliders: { ...sliders, ...patch } });
  // A pilot can only get a readback wrong when there is one: spoken, at a speed speech can keep up with.
  const noReadbacks = (sim?.speed ?? 1) > 1.5;

  const conesNow = scoreboard?.cones_now ?? Object.values(riskPairs).filter((r) => r.gone === null).length;
  const real = sim?.source === "real";
  const live = real && sim?.meta?.live === true;

  return (
    <div className={`absolute inset-0 z-40 ${open ? "" : "pointer-events-none"}`} aria-hidden={!open} {...(open ? {} : { inert: true })}>
      <div className={`absolute inset-0 scrim scrim-fade ${open ? "opacity-100" : "opacity-0"}`} onClick={close} />
      <aside
        id="settings-sheet"
        role="dialog"
        aria-label="Settings"
        className={`sheet absolute top-0 right-0 bottom-0 w-[400px] max-w-full bg-panel border-l border-line flex flex-col ${open ? "" : "sheet-closed"}`}
      >
        <div className="flex items-center justify-between px-5 pt-5 pb-3">
          <h1 className="text-base font-semibold tracking-tight">Settings</h1>
          <button onClick={close} className="btn" aria-label="Close settings">Close</button>
        </div>

        <div className="flex-1 overflow-y-auto scroll-thin px-5 pb-5 flex flex-col divide-y divide-line">
          <Group title="Sky">
            <Row label={scenarioLabel(sim, connection)}>
              <button
                className="btn btn-round"
                title="Change the sky"
                aria-label="Change the sky"
                onClick={() => { close(); dispatch({ type: "set_setup_open", open: true }); }}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M12 20h9" />
                  <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
                </svg>
              </button>
            </Row>
          </Group>

          <Group title="Radio">
            <Row label="Action mode" hint="Manual: you say each instruction and the pilot reads it back. Autonomous: squack issues every instruction itself, the moment the plan changes.">
              <VoiceToggle />
            </Row>
            <Row label="squack checks readbacks">
              <Pill
                on={towerOn}
                title={towerOn ? "squack is checking every readback" : "squack is off: readbacks are not being checked"}
                onClick={() => {
                  const v = !towerOn;
                  dispatch({ type: "local_toggle", key: "tower_enabled", value: v });
                  send({ type: "set_tower", enabled: v });
                }}
              >
                {towerOn ? "on" : "off"}
              </Pill>
            </Row>
            <Row label="squack speaks its replies">
              <Pill
                on={speakReplies}
                title={speakReplies ? "squack says its answers on the frequency. Click to keep them on the card only." : "squack answers in text only. Click to hear it on the frequency."}
                onClick={() => send({ type: "set_speak_replies", enabled: !speakReplies })}
              >
                {speakReplies ? "on" : "off"}
              </Pill>
            </Row>
            <Row label="Hear the frequency">
              <Pill
                on={!radioMuted}
                title={radioMuted ? "The frequency is muted. Click to hear every transmission." : "Every transmission is played as it happens. Click to mute."}
                onClick={() => { const m = !radioMuted; setRadioMuted(m); radio?.setMuted(m); }}
              >
                {radioMuted ? "muted" : "on"}
              </Pill>
            </Row>
            <p className="text-xs text-muted">Talk from the bar at the bottom: hold Space for the radio, Shift+Space for squack.</p>
          </Group>

          <Group title="Screen">
            <Row label="View">
              <div className="seg">
                <button aria-pressed={!view.topDown} onClick={() => dispatch({ type: "set_view", view: { topDown: false } })}>Tilt</button>
                <button aria-pressed={view.topDown} onClick={() => dispatch({ type: "set_view", view: { topDown: true } })}>Top down</button>
              </div>
            </Row>
            <Row label="Two fingers" hint="On a trackpad: swing the view round the scene and tilt it, like a 3D viewer (pinch zooms). Zoom is the old behaviour, which is what a mouse wheel wants.">
              <div className="seg">
                <button aria-pressed={view.twoFingers === "orbit"} onClick={() => dispatch({ type: "set_view", view: { twoFingers: "orbit" } })}>Orbit</button>
                <button aria-pressed={view.twoFingers === "zoom"} onClick={() => dispatch({ type: "set_view", view: { twoFingers: "zoom" } })}>Zoom</button>
              </div>
            </Row>
            <Row label="Lines">
              <div className="seg">
                {(["today", "tower", "both", "changed"] as const).map((v) => (
                  <button
                    key={v}
                    aria-pressed={planView === v}
                    title={{ today: "Only the routes as filed, flown or projected", tower: "Only squack's paths", both: "Original routes underneath, squack's paths on top", changed: "Only the flights squack has moved, with what they were going to fly" }[v]}
                    onClick={() => dispatch({ type: "set_plan_view", view: v })}
                  >
                    {{ today: "Original", tower: "squack", both: "Both", changed: "Changed" }[v]}
                  </button>
                ))}
              </div>
            </Row>
            <Slider label="Altitude" value={view.exaggeration} min={1} max={14} step={1} fmt={(v) => `${v}x`} onChange={(v) => dispatch({ type: "set_view", view: { exaggeration: v } })} />
            {/* Legend: one swatch and one word per line, the swatches drawn in the layers' own colours. */}
            <ul className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted pt-1">
              <li className="flex items-center gap-2">{swatch("dashed", "rgb(132,146,162)")}{real ? (live ? "projected" : "flown") : "standard"}</li>
              <li className="flex items-center gap-2">{swatch("solid", "rgb(70,200,255)")}squack</li>
              <li className="flex items-center gap-2">{swatch("solid", "rgb(255,176,46)")}rerouted</li>
              <li className="flex items-center gap-2">{swatch("dotted", "rgb(226,232,240)", 0.6)}was going to fly</li>
              <li className="flex items-center gap-2">{swatch("ring", "rgb(255,77,94)")}alert</li>
              <li className="flex items-center gap-2">{swatch("ring", "rgb(255,176,46)")}checking</li>
              <li className="flex items-center gap-2">{swatch("ring", "rgb(34,211,238)")}watching</li>
              <li className="flex items-center gap-2">
                {swatch("wedge", "var(--bad)", 0.7)}predicted conflict
                {conesNow > 0 && <span className="ml-auto font-mono tabular-nums text-bad">{conesNow}</span>}
              </li>
            </ul>
            <p className="text-xs text-muted">
              {view.twoFingers === "orbit" ? "Drag to pan. Two fingers: swing round and tilt. Pinch to zoom." : "Drag to pan, scroll to zoom, right-drag to tilt and rotate."}
              {real && (live
                ? connection === "mock"
                  ? " Mock snapshot: scripted traffic, not the real sky. Start the backend for a live one."
                  : " Real flights, one snapshot, flown by the simulator from there. Dashed lines are each flight's track projected to the region boundary. Flight data: adsb.lol (ODbL, CC0)."
                : " Real flights: dashed lines are the tracks actually flown. Flight data: adsb.lol (ODbL, CC0). Gate names are ours.")}
            </p>
          </Group>

          <Group title="Chaos" caption="for the demo">
            <Slider label="Separation buffer" value={sliders.buffer_nm} min={0} max={10} step={0.5} fmt={(v) => `+${v.toFixed(1)} NM`} onChange={(v) => setSliders({ buffer_nm: v })} />
            <Slider label="Pilot error rate" value={sliders.error_rate} min={0} max={0.5} step={0.05} fmt={(v) => `${Math.round(v * 100)}%`} onChange={(v) => setSliders({ error_rate: v })} />
            <Slider label="Radio noise" value={sliders.noise} min={0} max={1} step={0.05} fmt={(v) => v.toFixed(2)} onChange={(v) => setSliders({ noise: v })} />
            <p className={`text-xs leading-snug ${noReadbacks && sliders.error_rate > 0 ? "text-warn" : "text-muted"}`}>
              {noReadbacks
                ? "Above 1.5x every instruction goes by data link: nothing is spoken, so no readback can go wrong. Drop to 1x to hear pilots."
                : "Pilot errors happen in spoken readbacks. Say a card and some will come back wrong."}
            </p>
            {/* Script the next pilot reply, so a catch happens on cue instead of by chance. One shot. */}
            <div className="flex flex-col gap-1.5 pt-1">
              <span className="text-[13px] text-fg">Next readback</span>
              <div className="flex flex-wrap gap-1.5">
                {READBACKS.map(([mode, label]) => {
                  const on = nextReadback === mode;
                  const benign = mode === "random" || mode === "correct";
                  return (
                    <button
                      key={mode}
                      onClick={() => send({ type: "set_next_readback", mode })}
                      aria-pressed={on}
                      title={mode === "random" ? "Use the pilot error slider" : "Applies to the next instruction only, then goes back to chance"}
                      className={`chip select-none ${on ? (benign ? "border-fg/60 text-fg" : "chip-bad") : "hover:text-fg hover:border-fg/40"}`}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
            </div>
          </Group>
        </div>

        <p className="px-5 py-3 border-t border-line text-[11px] text-muted leading-relaxed">
          Every row here can be said instead: &ldquo;autonomous&rdquo;, &ldquo;top down&rdquo;, &ldquo;error rate twenty percent&rdquo;.
        </p>
      </aside>
    </div>
  );
}
