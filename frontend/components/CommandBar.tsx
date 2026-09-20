"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
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

const EXAMPLES = [
  "put a storm on Delta 789",
  "why did you turn United 210",
  "double the traffic",
  "show the scoreboard",
  "Air Canada one two three, descend flight level two four zero",
  "who is above three five zero",
  "run the Monte Carlo at twice the traffic",
  "load Europe at four",
];
const REPLY_TTL_MS = 14000;
const RESUME_MS = 1500;
const TYPE_MS = 28;
const HOLD_MS = 2000;
const FADE_MS = 220;
const GAP_MS = 260;
const EASE = "cubic-bezier(.22,1,.36,1)";

type Phase = "type" | "hold" | "fade" | "gap";

/**
 * Types `lines` into the placeholder area one at a time: a character every ~28 ms with a few ms of
 * jitter and a beat after commas, a hold, then the whole line fades out and the next begins.
 *
 * One requestAnimationFrame loop with a time budget, not chained timeouts, so late frames never
 * bunch: at most one character per frame, and the carried-over time is capped at one budget. The
 * text lives in this component's own state so a keystroke of the typewriter re-renders only this
 * span, never the whole bar. `enabled` false unmounts the sequence; true restarts it from the top.
 */
const Typewriter = memo(function Typewriter({ lines, enabled }: { lines: string[]; enabled: boolean }) {
  const [text, setText] = useState("");
  const [phase, setPhase] = useState<Phase>("type");

  useEffect(() => {
    if (!enabled || lines.length === 0) {
      setText("");
      setPhase("type");
      return;
    }
    let raf = 0;
    let line = 0;
    let n = 0;
    let phase: Phase = "type";
    let budget = 420; // first character waits a beat
    let acc = 0;
    let last = performance.now();
    const charBudget = (prev: string | undefined) => TYPE_MS + (prev === "," ? 120 : 0) + (Math.random() * 8 - 4);
    const frame = (t: number) => {
      acc += t - last;
      last = t;
      if (acc >= budget) {
        acc = Math.min(acc - budget, budget);
        const s = lines[line];
        if (phase === "type") {
          n += 1;
          setText(s.slice(0, n));
          if (n >= s.length) {
            phase = "hold";
            setPhase("hold");
            budget = HOLD_MS;
          } else {
            budget = charBudget(s[n - 1]);
          }
        } else if (phase === "hold") {
          phase = "fade";
          setPhase("fade");
          budget = FADE_MS;
        } else if (phase === "fade") {
          phase = "gap";
          setPhase("gap");
          setText("");
          n = 0;
          budget = GAP_MS;
        } else {
          line = (line + 1) % lines.length;
          phase = "type";
          setPhase("type");
          budget = charBudget(undefined);
        }
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [lines, enabled]);

  if (!enabled || !text) return null;
  const fading = phase === "fade";
  return (
    <span
      aria-hidden
      className="pointer-events-none absolute inset-0 flex items-center text-[14px] text-muted/60 whitespace-nowrap overflow-hidden"
      style={{
        opacity: fading ? 0 : 1,
        transform: fading ? "translateY(2px)" : "translateY(0)",
        transition: fading ? `opacity ${FADE_MS}ms ${EASE}, transform ${FADE_MS}ms ${EASE}` : "none",
      }}
    >
      <span className="truncate">{text}</span>
      {phase === "type" && <span className="inline-block w-px h-[1.05em] ml-px bg-muted/70 shrink-0" style={{ animation: "cb-caret 1.1s steps(1) infinite" }} />}
    </span>
  );
});

/** True when the viewer asked for less motion; the placeholder then sits still. */
function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  return reduced;
}

export default function CommandBar() {
  const { send, sendBinary } = useClient();
  const { chat, connection, aircraft, sim, dictation: heard } = useTowerState();
  const dispatch = useTowerDispatch();
  const [text, setText] = useState("");
  const [active, setActive] = useState<PttChannel | null>(null);
  const [level, setLevel] = useState(0);
  const [micError, setMicError] = useState<string | null>(null);
  const [replyAt, setReplyAt] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [focused, setFocused] = useState(false);
  // The typewriter pauses the moment anything is typed and comes back RESUME_MS after the field is
  // empty and unfocused again.
  const [paused, setPaused] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const captureRef = useRef<Capture | null>(null);
  const activeRef = useRef<PttChannel | null>(null);

  const callsigns = useMemo(() => Object.keys(aircraft), [aircraft]);
  const route = routeText(text, callsigns);
  // Live dictation: what the mic is hearing sits in the field, muted until the final (lib/store.tsx
  // clears it DICTATION_HOLD_MS after that). Typed text is untouched underneath.
  const dictation = heard && heard.text ? heard : null;
  const last = chat.length ? chat[chat.length - 1] : null;
  const showReply = last && last.role === "agent" && now - replyAt < REPLY_TTL_MS;

  // Keep the reply card's clock ticking only while a reply is showing.
  useEffect(() => {
    if (!last || last.role !== "agent") return;
    setReplyAt(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [last]);

  useEffect(() => {
    if (text) {
      setPaused(true);
      return;
    }
    if (focused) return;
    const id = setTimeout(() => setPaused(false), RESUME_MS);
    return () => clearTimeout(id);
  }, [text, focused]);

  const reduced = useReducedMotion();
  const typewriterOn = !active && !paused && !reduced && !text && !dictation;

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
  // The typewriter owns the placeholder area while it runs; the static line covers the mic, reduced
  // motion, and the gap before the sequence resumes.
  const staticPlaceholder = active
    ? isRadio
      ? "Transmitting on the frequency"
      : "squack is listening"
    : reduced
      ? `Try: ${EXAMPLES[0]}`
      : sim?.lifecycle === "running"
        ? "Hold Space to talk, or type. Aircraft hear phraseology; squack hears everything else."
        : "Ask squack to load a sky, or press Start";
  // Derived from focus and emptiness alone, so a keystroke never restarts the lift transition.
  const lifted = focused || text.length > 0 || dictation !== null;

  return (
    <div className="pointer-events-none absolute left-1/2 bottom-6 z-30 -translate-x-1/2 w-[min(680px,calc(100vw-32px))] flex flex-col items-center gap-2">
      {/* The reply card and the bar lift together on focus so they stay aligned. */}
      <div
        className="w-full flex flex-col items-center gap-2"
        style={{
          transform: lifted ? "translateY(-5px) scale(1.015)" : "translateY(0) scale(1)",
          transition: reduced ? "none" : `transform ${lifted ? 220 : 240}ms ${EASE}`,
          willChange: "transform",
        }}
      >
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
          onClick={(e) => {
            // A click anywhere on the bar that is not the mic focuses the field.
            if ((e.target as HTMLElement).closest("button")) return;
            inputRef.current?.focus();
          }}
          className="pointer-events-auto w-full flex items-center gap-3 rounded-[10px] border bg-panel px-3 py-2 cursor-text"
          style={{
            borderColor: lifted ? "rgba(236,236,236,0.28)" : "var(--line)",
            boxShadow: lifted ? "0 24px 60px -20px rgba(0,0,0,0.85)" : "0 24px 60px -20px rgba(0,0,0,0)",
            transition: reduced ? "none" : `border-color ${lifted ? 260 : 240}ms ${EASE}, box-shadow ${lifted ? 260 : 240}ms ${EASE}`,
          }}
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
          <div className="relative flex-1 min-w-0">
            <Typewriter lines={EXAMPLES} enabled={typewriterOn} />
            <input
              ref={inputRef}
              value={dictation ? dictation.text : text}
              onChange={(e) => setText(e.target.value)}
              onFocus={() => setFocused(true)}
              onBlur={() => setFocused(false)}
              placeholder={typewriterOn ? "" : staticPlaceholder}
              aria-label="Ask squack or say an instruction"
              className={`relative w-full bg-transparent text-[14px] placeholder:text-muted/60 outline-none ${dictation && !dictation.final ? "text-muted" : "text-fg"}`}
            />
          </div>
          {text.trim() ? (
            <span className={`text-[11px] shrink-0 ${route === "radio" ? "text-ok" : "text-muted"}`}>{route === "radio" ? "to the aircraft" : "to squack"}</span>
          ) : (
            <span className="text-[11px] text-muted shrink-0" style={{ fontFamily: "var(--font-mono)" }}>⌘K</span>
          )}
        </form>
      </div>

      {micError && <div className="pointer-events-auto text-[11px] text-warn">No mic: {micError}. Typing works.</div>}
      {connection === "mock" && active && <div className="text-[11px] text-muted">mock mode: audio is captured but not sent anywhere</div>}
      <button type="button" {...holdProps("agent")} className="sr-only">Hold to talk to squack</button>
    </div>
  );
}
