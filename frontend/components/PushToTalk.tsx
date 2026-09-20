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
    <section className="panel p-2.5 shrink-0">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-xs uppercase tracking-wider text-muted">Talk</h2>
        <span className="text-[10px] text-muted">hold <kbd className="font-mono px-1 rounded bg-panel-2 border border-line">Space</kbd> radio · <kbd className="font-mono px-1 rounded bg-panel-2 border border-line">Shift+Space</kbd> headset</span>
      </div>

      {/* Big mic indicator */}
      <div className={`rounded-lg border-2 px-3 py-2 flex items-center gap-3 transition-colors ${isRadio ? "border-ok bg-ok/15" : isAgent ? "border-accent bg-accent/15" : "border-line bg-panel-2"}`}>
        <div className={`w-9 h-9 rounded-full flex items-center justify-center text-lg ${isRadio ? "bg-ok text-bg" : isAgent ? "bg-accent text-bg" : "bg-line text-muted"}`}>
          {isAgent ? "🎧" : "🎙"}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold">
            {isRadio ? "TRANSMITTING on frequency" : isAgent ? "TALKING to the agent" : "Mic idle"}
          </div>
          <div className="h-1.5 mt-1 rounded bg-line overflow-hidden">
            <div className={`h-full transition-[width] duration-75 ${isRadio ? "bg-ok" : "bg-accent"}`} style={{ width: `${Math.min(100, level * 160)}%` }} />
          </div>
          {micError && <div className="text-[10px] text-warn mt-1">No mic: {micError}. Use the typed fallback below.</div>}
          {connection === "mock" && active && <div className="text-[10px] text-muted mt-1">Mock mode: audio is captured but not sent anywhere.</div>}
        </div>
        <div className="flex flex-col gap-1">
          <button {...holdProps("radio")} className="px-2 py-1 rounded text-xs border border-ok/50 text-ok bg-ok/10 select-none touch-none">Hold: radio</button>
          <button {...holdProps("agent")} className="px-2 py-1 rounded text-xs border border-accent/50 text-accent bg-accent/10 select-none touch-none">Hold: headset</button>
        </div>
      </div>

      {/* Script the next pilot reply, so a catch happens on cue instead of by chance. One shot. */}
      <div className="mt-2">
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] uppercase tracking-wider text-muted">Next readback</span>
          <button
            onClick={() => { const m = !radioMuted; setRadioMuted(m); radio?.setMuted(m); }}
            title={radioMuted ? "The frequency is muted. Click to hear every transmission." : "Every transmission is played as it happens. Click to mute."}
            className={`text-[10px] px-2 py-0.5 rounded border ${radioMuted ? "border-line text-muted" : "border-ok/40 text-ok bg-ok/10"}`}
          >
            {radioMuted ? "frequency muted" : "frequency on"}
          </button>
        </div>
        <div className="grid grid-cols-5 gap-1">
          {([["random", "By chance"], ["correct", "Correct"], ["wrong_value", "Wrong value"], ["wrong_aircraft", "Wrong plane"], ["missing_readback", "No reply"]] as const).map(([mode, label]) => (
            <button
              key={mode}
              onClick={() => send({ type: "set_next_readback", mode })}
              title={mode === "random" ? "Use the pilot error slider" : "Applies to the next instruction only, then goes back to chance"}
              className={`px-1 py-1 rounded border text-[10px] leading-tight ${nextReadback === mode ? (mode === "random" || mode === "correct" ? "border-accent/50 text-accent bg-accent/15" : "border-bad/50 text-bad bg-bad/15") : "border-line text-muted bg-panel-2 hover:text-fg"}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Agent chat */}
      <div className="mt-2">
        <div className="text-[10px] uppercase tracking-wider text-muted mb-1">Headset · world builder</div>
        {chat.length > 0 && (
          <div className="max-h-28 overflow-y-auto scroll-thin flex flex-col gap-1 mb-1.5">
            {chat.map((c, i) => (
              <div key={i} className={`text-xs rounded-md px-2 py-1 max-w-[90%] ${c.role === "user" ? "self-end bg-accent/15 text-fg" : "self-start bg-panel-2 border border-line"}`}>
                {c.text}
                {c.actions && c.actions.length > 0 && <div className="mt-0.5 font-mono text-[10px] text-muted">{c.actions.join(" · ")}</div>}
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
          className="flex gap-1.5"
        >
          <input
            value={agentText}
            onChange={(e) => setAgentText(e.target.value)}
            placeholder="Load Toronto at 4pm, add a Porter flight from the east"
            className="flex-1 bg-panel-2 border border-line rounded-md px-2 py-1 text-xs outline-none focus:border-accent"
          />
          <button type="submit" className="px-2 py-1 rounded-md text-xs border border-accent/40 text-accent bg-accent/10">Send</button>
        </form>
      </div>

      {/* Typed radio fallback */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submitRadio();
        }}
        className="mt-2 flex gap-1.5"
      >
        <input
          value={radioText}
          onChange={(e) => setRadioText(e.target.value)}
          placeholder="Typed radio fallback (no mic): air canada one two three descend flight level two four zero"
          className="flex-1 bg-panel-2 border border-line rounded-md px-2 py-1 text-xs outline-none focus:border-ok"
        />
        <button type="submit" className="px-2 py-1 rounded-md text-xs border border-ok/40 text-ok bg-ok/10">Transmit</button>
      </form>
    </section>
  );
}
