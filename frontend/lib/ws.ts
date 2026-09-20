/**
 * WebSocket client with automatic mock fallback.
 *
 * Server -> client: Event envelopes {type, payload, t}.
 * Client -> server: JSON ClientMessage, or binary PCM16 mono 16 kHz frames while PTT is held.
 */
import { startMock, type MockHandle } from "./mock";
import type { ClientMessage, TowerEvent } from "./types";
import type { Connection } from "./store";

// 127.0.0.1, not localhost: uvicorn binds IPv4 only, and browsers try ::1 first for "localhost",
// which costs about 600 ms per connection and used to push the first attempt past the timeout.
export const WS_URL = process.env.NEXT_PUBLIC_TOWER_WS ?? "ws://127.0.0.1:8000/ws";
export const HTTP_URL = process.env.NEXT_PUBLIC_TOWER_HTTP ?? "http://127.0.0.1:8000";

const CONNECT_TIMEOUT_MS = 4000;
const RECONNECT_MS = 3000;

export interface TowerClient {
  send(msg: ClientMessage): void;
  sendBinary(buf: ArrayBuffer): void;
  close(): void;
}

const EVENT_TYPES = new Set<string>([
  "transcript", "clearance_opened", "clearance_updated", "alert", "resolver_step", "stats",
  "radar", "plan", "plan_update", "instruction_card", "disruption", "scoreboard", "agent_reply", "state", "notice",
  // Anything not listed here is dropped without a word. A new backend event needs a line here,
  // in EventMap (lib/types.ts), and in the reducer (lib/store.tsx).
  "radio_audio", "said_check", "alert_resolved", "risk", "dictation",
  // the squack agent (TRD 08)
  "agent_step", "answer", "ui_command",
]);

function parseEvent(raw: string): TowerEvent | null {
  try {
    const obj: unknown = JSON.parse(raw);
    if (typeof obj !== "object" || obj === null) return null;
    const rec = obj as Record<string, unknown>;
    if (typeof rec.type !== "string" || !EVENT_TYPES.has(rec.type) || typeof rec.payload !== "object") return null;
    return { type: rec.type, payload: rec.payload, t: typeof rec.t === "number" ? rec.t : 0 } as TowerEvent;
  } catch {
    return null;
  }
}

export function connectTower(opts: {
  forceMock: boolean;
  onEvent: (ev: TowerEvent) => void;
  onStatus: (s: Connection) => void;
  /** Called when the mock is replaced by the real backend, so the screen can drop mock state. */
  onLive?: () => void;
}): TowerClient {
  let ws: WebSocket | null = null;
  let mock: MockHandle | null = null;
  let closed = false;
  let everOpened = false;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  /** The region of the mock's live snapshot, kept so a reset stays a live snapshot. */
  let mockLive: string | undefined;

  const startMockMode = () => {
    if (closed || mock) return;
    mock = startMock(opts.onEvent);
    opts.onStatus("mock");
  };

  const tryConnect = () => {
    if (closed) return;
    if (!mock) opts.onStatus("connecting");
    let sock: WebSocket;
    try {
      sock = new WebSocket(WS_URL);
    } catch {
      startMockMode();
      return;
    }
    sock.binaryType = "arraybuffer";
    ws = sock;
    const timeout = setTimeout(() => {
      if (sock.readyState !== WebSocket.OPEN) {
        sock.close();
      }
    }, CONNECT_TIMEOUT_MS);

    sock.onopen = () => {
      clearTimeout(timeout);
      everOpened = true;
      if (mock) {
        // The backend came up while we were showing the mock. Drop the mock and go live.
        mock.stop();
        mock = null;
        opts.onLive?.();
      }
      opts.onStatus("live");
    };
    sock.onmessage = (m: MessageEvent<string | ArrayBuffer>) => {
      if (typeof m.data !== "string") return;
      const ev = parseEvent(m.data);
      if (ev) opts.onEvent(ev);
    };
    sock.onerror = () => {
      /* onclose follows */
    };
    sock.onclose = () => {
      clearTimeout(timeout);
      if (closed) return;
      ws = null;
      if (!everOpened) {
        // Backend not reachable yet: show the scripted mock so the screen is never blank, and keep
        // knocking. The mock is a stand-in, never a dead end.
        startMockMode();
      } else {
        opts.onStatus("closed");
      }
      reconnectTimer = setTimeout(tryConnect, RECONNECT_MS);
    };
  };

  if (opts.forceMock) startMockMode();
  else tryConnect();

  return {
    send(msg) {
      if (mock && (msg.type === "reset" || msg.type === "configure" || msg.type === "load_scenario")) {
        // The mock is a scripted closure: a new world means a new mock.
        mock.stop();
        if (msg.type !== "reset") mockLive = msg.type === "configure" && msg.source === "live" ? msg.region : undefined;
        const name = msg.type === "load_scenario" ? msg.name : msg.type === "configure" && msg.source !== "live" ? msg.scenario : undefined;
        mock = startMock(opts.onEvent, name, mockLive);
        return;
      }
      if (mock) mock.send(msg);
      else if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
    },
    sendBinary(buf) {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(buf);
    },
    close() {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      mock?.stop();
      mock = null;
      ws?.close();
      ws = null;
    },
  };
}
