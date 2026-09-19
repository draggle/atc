/**
 * WebSocket client with automatic mock fallback.
 *
 * Server -> client: Event envelopes {type, payload, t}.
 * Client -> server: JSON ClientMessage, or binary PCM16 mono 16 kHz frames while PTT is held.
 */
import { startMock, type MockHandle } from "./mock";
import type { ClientMessage, TowerEvent } from "./types";
import type { Connection } from "./store";

export const WS_URL = process.env.NEXT_PUBLIC_TOWER_WS ?? "ws://localhost:8000/ws";
export const HTTP_URL = process.env.NEXT_PUBLIC_TOWER_HTTP ?? "http://localhost:8000";

const CONNECT_TIMEOUT_MS = 1500;
const RECONNECT_MS = 3000;

export interface TowerClient {
  send(msg: ClientMessage): void;
  sendBinary(buf: ArrayBuffer): void;
  close(): void;
}

const EVENT_TYPES = new Set<string>([
  "transcript", "clearance_opened", "clearance_updated", "alert", "resolver_step", "stats",
  "radar", "plan", "plan_update", "instruction_card", "disruption", "scoreboard", "agent_reply", "state",
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
}): TowerClient {
  let ws: WebSocket | null = null;
  let mock: MockHandle | null = null;
  let closed = false;
  let everOpened = false;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  const startMockMode = () => {
    if (closed || mock) return;
    mock = startMock(opts.onEvent);
    opts.onStatus("mock");
  };

  const tryConnect = () => {
    if (closed) return;
    opts.onStatus("connecting");
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
        // Backend not reachable: fall back to the scripted mock so the screen is never blank.
        startMockMode();
      } else {
        opts.onStatus("closed");
        reconnectTimer = setTimeout(tryConnect, RECONNECT_MS);
      }
    };
  };

  if (opts.forceMock) startMockMode();
  else tryConnect();

  return {
    send(msg) {
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
