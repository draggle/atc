"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTowerDispatch, useTowerState } from "@/lib/store";
import { startCapture, type Capture } from "@/lib/audio";
import type { PttChannel } from "@/lib/types";
import { useClient } from "./TowerApp";

/**
 * The one place you talk. Bottom centre, always there.
 *
 * Two audiences, one bar. Hold Space (or the mic) to talk on the radio; hold Shift+Space to talk to
 * squack. Typed text is routed by what it looks like: phraseology addressed to an aircraft goes out
 * on the radio, everything else goes to squack. squack's reply lands as a card above the bar and
 * fades on its own. Cmd/Ctrl+K focuses the input from anywhere.
 */

function isTyping(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || el.isContentEditable;
}

const RADIO_VERBS = /\b(descend|climb|maintain|heading|turn|direct|speed|contact|squawk|cleared|hold short|line up|reduce|increase|proceed|resume|fly|level off|say again|disregard)\b/i;
const DIGIT_WORDS = /\b(zero|one|two|three|four|five|six|seven|eight|niner|nine|tree|fife)\b/i;
const SQUACK_WORDS = /\b(squack|load|scenario|storm|fighter|drone|balloon|rocket|traffic|double|scoreboard|show|why|what|how many|which|zoom|follow|tilt|top down|error rate|noise|buffer|voice|pause|start|reset|speed up|slow down the clock|undo)\b/i;

/** Does this line read like a transmission to an aircraft, or a request to squack? */
export function routeText(text: string, callsigns: string[]): "radio" | "agent" {
  const t = text.trim().toLowerCase();
  if (!t) return "agent";
  if (/^(hey |ok |okay )?squack\b/.test(t)) return "agent";
  const hasCallsign = callsigns.some((cs) => t.includes(cs.toLowerCase()));
  if (hasCallsign && RADIO_VERBS.test(t)) return "radio";
  if (RADIO_VERBS.test(t) && DIGIT_WORDS.test(t) && !SQUACK_WORDS.test(t)) return "radio";
  if (hasCallsign && !SQUACK_WORDS.test(t)) return "radio";
  return "agent";
}

const HINTS = ["put a storm on Delta 789", "why did you turn United 210", "double the traffic", "show the scoreboard"];
const REPLY_TTL_MS = 14000;

export default function CommandBar() {
  const { send, sendBinary } = useClient();
  const { chat, connection, aircraft, sim } = useTowerState();
  const dispatch = useTowerDispatch();
  const [text, setText] = useState("");
  const [active, setActive] = useState<PttChannel | null>(null);
  const [level, setLevel] = useState(0);
  const [micError, setMicError] = useState<string | null>(null);
  const [replyAt, setReplyAt] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const inputRef = useRef<HTMLInputElement | null>(null);
  const captureRef = useRef<Capture | null>(null);
  const activeRef = useRef<PttChannel | null>(null);

  const callsigns = useMemo(() => Object.keys(aircraft), [aircraft]);
  const route = routeText(text, callsigns);
  const last = chat.length ? chat[chat.length - 1] : null;
  const showReply = last && last.role === "agent" && now - replyAt < REPLY_TTL_MS;

  // Keep the reply card's clock ticking only while a reply is showing.
  useEffect(() => {
    if (!last || last.role !== "agent") return;
    setReplyAt(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [last]);

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
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        inputRef.current?.focus();
        return;
      }
      if (e.key === "Escape" && isTyping(e.target)) {
        (e.target as HTMLElement).blur();
        return;
      }
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

  const submit = () => {
    const line = text.trim();
    if (!line) return;
    if (routeText(line, callsigns) === "radio") {
      send({ type: "radio_text", text: line });
    } else {
      dispatch({ type: "user_chat", text: line });
      send({ type: "agent_text", text: line.replace(/^(hey |ok |okay )?squack[,:]?\s*/i, "") });
    }
    setText("");
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
  const placeholder = active
    ? isRadio
      ? "Transmitting on the frequency"
      : "squack is listening"
    : sim?.lifecycle === "running"
      ? "Hold Space to talk, or type. Aircraft hear phraseology; squack hears everything else."
      : "Ask squack to load a sky, or press Start";

  return (
    <div className="pointer-events-none absolute left-1/2 bottom-6 z-30 -translate-x-1/2 w-[min(680px,calc(100vw-32px))] flex flex-col items-center gap-2">
      {/* squack's last answer, above the bar, gone on its own */}
      {showReply && last && (
        <div className="pointer-events-auto w-full panel px-4 py-3 text-[13px] leading-snug text-fg/95">
          <div className="flex items-start gap-3">
            <span className="text-muted shrink-0 text-[11px] mt-0.5">squack</span>
            <div className="min-w-0 flex-1">
              <div>{last.text}</div>
              {last.actions && last.actions.length > 0 && (
                <div className="mt-1 text-[11px] text-muted" style={{ fontFamily: "var(--font-mono)" }}>{last.actions.join(" · ")}</div>
              )}
            </div>
            <button onClick={() => setReplyAt(0)} aria-label="Dismiss" className="text-muted hover:text-fg text-[13px] leading-none">×</button>
          </div>
        </div>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        className="pointer-events-auto w-full flex items-center gap-3 rounded-[10px] border border-line bg-panel px-3 py-2"
      >
        <button
          type="button"
          {...holdProps("radio")}
          aria-label="Hold to talk on the radio"
          className={`relative w-8 h-8 shrink-0 rounded-full border transition-colors touch-none select-none outline-none ${isRadio ? "border-ok bg-ok" : isAgent ? "border-fg bg-fg" : "border-line hover:border-fg/50"}`}
        >
          {active && <span className="absolute inset-0 rounded-full border border-fg/40 transition-transform duration-75" style={{ transform: `scale(${1 + Math.min(1, level * 1.6) * 0.35})` }} />}
          <span className={`absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-2 h-2 rounded-full ${active ? "bg-bg" : "bg-muted"}`} />
        </button>
        <input
          ref={inputRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={placeholder}
          aria-label="Ask squack or say an instruction"
          className="flex-1 min-w-0 bg-transparent text-[14px] text-fg placeholder:text-muted/60 outline-none"
        />
        {text.trim() ? (
          <span className={`text-[11px] shrink-0 ${route === "radio" ? "text-ok" : "text-muted"}`}>{route === "radio" ? "to the aircraft" : "to squack"}</span>
        ) : (
          <span className="text-[11px] text-muted shrink-0" style={{ fontFamily: "var(--font-mono)" }}>⌘K</span>
        )}
      </form>

      {!text && !active && !showReply && (
        <div className="pointer-events-auto flex flex-wrap justify-center gap-1.5">
          {HINTS.map((h) => (
            <button key={h} type="button" onClick={() => { setText(h); inputRef.current?.focus(); }} className="chip text-[11px] hover:text-fg hover:border-fg/40">
              {h}
            </button>
          ))}
        </div>
      )}
      {micError && <div className="pointer-events-auto text-[11px] text-warn">No mic: {micError}. Typing works.</div>}
      {connection === "mock" && active && <div className="text-[11px] text-muted">mock mode: audio is captured but not sent anywhere</div>}
      <button type="button" {...holdProps("agent")} className="sr-only">Hold to talk to squack</button>
    </div>
  );
}
