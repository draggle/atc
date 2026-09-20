/**
 * The frequency, out loud. Every clip the backend puts on the air (`radio_audio`) is played here,
 * one at a time and in order, the way a real frequency carries one voice at a time.
 *
 * Browsers only allow sound after the user has touched the page. Pressing Start or holding the
 * mic is enough. A blocked or missing clip is skipped, never retried, and never stalls the queue.
 */
import { HTTP_URL } from "./ws";

export interface RadioClip {
  speaker: "pilot" | "controller" | "squack";
  callsign: string | null;
  audio_ref: string;
  duration_s?: number;
}

type Listener = (now: RadioClip | null) => void;

const MUTE_KEY = "tower.radio.muted";
const MAX_QUEUE = 6; // a backlog older than this is history, not radio

class Radio {
  private queue: RadioClip[] = [];
  private current: HTMLAudioElement | null = null;
  private listeners = new Set<Listener>();
  muted = false;

  constructor() {
    try {
      this.muted = typeof window !== "undefined" && window.localStorage.getItem(MUTE_KEY) === "1";
    } catch {
      /* private window: start unmuted */
    }
  }

  onChange(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  setMuted(m: boolean) {
    this.muted = m;
    try {
      window.localStorage.setItem(MUTE_KEY, m ? "1" : "0");
    } catch {
      /* fine */
    }
    if (m) this.stop();
  }

  play(clip: RadioClip) {
    if (this.muted || typeof Audio === "undefined" || !clip.audio_ref) return;
    this.queue.push(clip);
    if (this.queue.length > MAX_QUEUE) this.queue.splice(0, this.queue.length - MAX_QUEUE);
    if (!this.current) this.next();
  }

  stop() {
    this.queue = [];
    if (this.current) {
      this.current.pause();
      this.current = null;
    }
    this.tell(null);
  }

  private tell(c: RadioClip | null) {
    this.listeners.forEach((fn) => fn(c));
  }

  private next() {
    const clip = this.queue.shift();
    if (!clip) {
      this.current = null;
      this.tell(null);
      return;
    }
    const el = new Audio(`${HTTP_URL}/audio/${clip.audio_ref}`);
    this.current = el;
    this.tell(clip);
    const done = () => {
      if (this.current === el) this.next();
    };
    el.addEventListener("ended", done, { once: true });
    el.addEventListener("error", done, { once: true });
    // Safety net: a clip that never reports "ended" must not hold the frequency for ever.
    window.setTimeout(done, ((clip.duration_s ?? 6) + 3) * 1000);
    el.play().catch(done);
  }
}

export const radio = typeof window === "undefined" ? (null as unknown as Radio) : new Radio();
