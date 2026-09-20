"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTowerDispatch, useTowerState } from "@/lib/store";
import { startCapture, type Capture } from "@/lib/audio";
import { radio } from "@/lib/radio";
import type { PttChannel } from "@/lib/types";
import { useClient } from "./TowerApp";

function isTyping(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || el.isContentEditable;
}

/** A quiet text link and a small outline chip, the same two shapes the other cards use. */
const LINK = "text-[11px] text-muted hover:text-fg underline decoration-dotted underline-offset-4";
const CHIP = "chip select-none";
const INPUT = "flex-1 min-w-0 bg-transparent border-b border-line px-0.5 py-1.5 text-xs text-fg placeholder:text-muted/60 outline-none focus:border-fg/50 transition-colors";

export default function PushToTalk() {
  const { send, sendBinary } = useClient();
  const { chat, connection, sim } = useTowerState();
  const nextReadback = sim?.next_readback ?? "random";
  const [radioMuted, setRadioMuted] = useState(false);
  useEffect(() => setRadioMuted(radio?.muted ?? false), []);
  const dispatch = useTowerDispatch();
  const [active, setActive] = useState<PttChannel | null>(null);
  const [level, setLevel] = useState(0);
  const [micError, setMicError] = useState<string | null>(null);
  const [agentText, setAgentText] = useState("");
  const [radioText, setRadioText] = useState("");
  const captureRef = useRef<Capture | null>(null);
  const activeRef = useRef<PttChannel | null>(null);
  const chatEnd = useRef<HTMLDivElement | null>(null);

  const start = useCallback(
    async (channel: PttChannel) => {
      if (activeRef.current) return;
      activeRef.current = channel;
      setActive(channel);
      send({ type: "ptt_start", channel });
      try {
        const cap = await startCapture((pcm, peak) => {
          sendBinary(pcm);
          setLevel(peak);
        });
        // Key may have been released while permission was pending.
        if (activeRef.current !== channel) {
          cap.stop();
          return;
        }
        captureRef.current = cap;
        setMicError(null);
      } catch (e) {
        setMicError(e instanceof Error ? e.message : "microphone unavailable");
      }
    },
    [send, sendBinary],
  );

  const stop = useCallback(() => {
    if (!activeRef.current) return;
    activeRef.current = null;
    captureRef.current?.stop();
    captureRef.current = null;
    setActive(null);
    setLevel(0);
    send({ type: "ptt_stop" });
  }, [send]);

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code !== "Space" || e.repeat || isTyping(e.target)) return;
      e.preventDefault();
      void start(e.shiftKey ? "agent" : "radio");
    };
    const up = (e: KeyboardEvent) => {
      if (e.code !== "Space") return;
      if (activeRef.current) e.preventDefault();
      stop();
    };
    const blur = () => stop();
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
      stop();
    };
  }, [start, stop]);

  useEffect(() => {
    chatEnd.current?.scrollIntoView({ block: "end" });
  }, [chat.length]);

  const submitAgent = () => {
    const text = agentText.trim();
    if (!text) return;
    dispatch({ type: "user_chat", text });
    send({ type: "agent_text", text });
    setAgentText("");
  };
  const submitRadio = () => {
    const text = radioText.trim();
    if (!text) return;
    send({ type: "radio_text", text });
    setRadioText("");
  };

  const holdProps = (channel: PttChannel) => ({
    onPointerDown: (e: React.PointerEvent) => {
      e.preventDefault();
      void start(channel);
    },
    onPointerUp: stop,
    onPointerLeave: stop,
    onPointerCancel: stop,
  });

  const isRadio = active === "radio";
  const isAgent = active === "agent";

  return (
    <section className="panel p-3 shrink-0">
      <div className="flex items-baseline justify-between mb-2.5">
        <h2 className="text-[13px] font-semibold text-fg">Talk</h2>
        <button
          onClick={() => { const m = !radioMuted; setRadioMuted(m); radio?.setMuted(m); }}
          title={radioMuted ? "The frequency is muted. Click to hear every transmission." : "Every transmission is played as it happens. Click to mute."}
          className={`text-[11px] underline decoration-dotted underline-offset-4 hover:text-fg ${radioMuted ? "text-muted" : "text-ok"}`}
        >
          {radioMuted ? "frequency muted" : "frequency on"}
        </button>
      </div>

      {/* The mic: an outline circle that fills green while you are on the air. Hold it, or hold Space. */}
      <div className="flex items-center gap-3">
        <button
          {...holdProps("radio")}
          aria-label="Hold to talk on the radio"
          className={`relative w-9 h-9 shrink-0 rounded-full border transition-colors touch-none select-none outline-none ${isRadio ? "border-ok bg-ok" : isAgent ? "border-fg bg-fg" : "border-line hover:border-fg/50"}`}
        >
          {/* Level shows as a ring that swells with your voice while transmitting. */}
          {active && <span className="absolute inset-0 rounded-full border border-fg/40 transition-transform duration-75" style={{ transform: `scale(${1 + Math.min(1, level * 1.6) * 0.35})` }} />}
          <span className={`absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-2 h-2 rounded-full ${active ? "bg-bg" : "bg-muted"}`} />
        </button>
        <div className="flex-1 min-w-0">
          <div className={`text-sm font-medium ${isRadio ? "text-ok" : "text-fg"}`}>
            {isRadio ? "Transmitting on frequency" : isAgent ? "Talking to the headset" : "Hold Space to talk"}
          </div>
          <div className="text-[11px] text-muted">
            <button {...holdProps("agent")} className="hover:text-fg underline decoration-dotted underline-offset-4 touch-none select-none">Shift+Space</button> for the headset
            {connection === "mock" && active && <span> · mock mode: audio is captured but not sent anywhere</span>}
          </div>
          {micError && <div className="text-[11px] text-warn mt-0.5">No mic: {micError}. Use the typed fallback below.</div>}
        </div>
      </div>

      {/* Script the next pilot reply, so a catch happens on cue instead of by chance. One shot. */}
      <div className="mt-3">
        <div className="text-[11px] text-muted mb-1.5">Next readback</div>
        <div className="flex flex-wrap gap-1.5">
          {([["random", "By chance"], ["correct", "Correct"], ["wrong_value", "Wrong value"], ["wrong_aircraft", "Wrong plane"], ["missing_readback", "No reply"]] as const).map(([mode, label]) => {
            const on = nextReadback === mode;
            const benign = mode === "random" || mode === "correct";
            return (
              <button
                key={mode}
                onClick={() => send({ type: "set_next_readback", mode })}
                title={mode === "random" ? "Use the pilot error slider" : "Applies to the next instruction only, then goes back to chance"}
                className={`${CHIP} ${on ? (benign ? "border-fg/60 text-fg" : "chip-bad") : "hover:text-fg hover:border-fg/40"}`}
              >
                {label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Agent chat */}
      <div className="mt-3">
        <div className="text-[11px] text-muted mb-1">Headset · world builder</div>
        {chat.length > 0 && (
          <div className="max-h-28 overflow-y-auto scroll-thin flex flex-col gap-1 mb-1.5">
            {chat.map((c, i) => (
              <div key={i} className={`text-xs rounded-md px-2 py-1 max-w-[90%] ${c.role === "user" ? "self-end bg-panel-2 text-fg" : "self-start border border-line text-fg/90"}`}>
                {c.text}
                {c.actions && c.actions.length > 0 && <div className="mt-0.5 text-[11px] text-muted" style={{ fontFamily: "var(--font-mono)" }}>{c.actions.join(" · ")}</div>}
              </div>
            ))}
            <div ref={chatEnd} />
          </div>
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            submitAgent();
          }}
          className="flex items-baseline gap-2"
        >
          <input
            value={agentText}
            onChange={(e) => setAgentText(e.target.value)}
            placeholder="Load Toronto at 4pm, add a Porter flight from the east"
            className={INPUT}
          />
          <button type="submit" className={LINK}>Send</button>
        </form>
      </div>

      {/* Typed radio fallback */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submitRadio();
        }}
        className="mt-2 flex items-baseline gap-2"
      >
        <input
          value={radioText}
          onChange={(e) => setRadioText(e.target.value)}
          placeholder="Typed radio fallback (no mic): air canada one two three descend flight level two four zero"
          className={INPUT}
        />
        <button type="submit" className={LINK}>Transmit</button>
      </form>
    </section>
  );
}
