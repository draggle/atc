"use client";

import { createContext, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import { DICTATION_HOLD_MS, TowerStoreProvider, useTowerDispatch, useTowerState } from "@/lib/store";
import { connectTower, type TowerClient } from "@/lib/ws";
import { BOTTOM_ROW_H, EDGE, ROW_TOP, SIDE_W } from "@/lib/layout";
import { radio } from "@/lib/radio";
import type { ClientMessage } from "@/lib/types";
import TopBar from "./TopBar";
import InstructionCards from "./InstructionCards";
import AlertCard from "./AlertCard";
import Transcript from "./Transcript";
import ScoreboardPanel from "./ScoreboardPanel";
import SettingsSheet from "./SettingsSheet";
import SetupPanel from "./SetupPanel";
import Notices from "./Notices";
import BootScreen from "./BootScreen";
import CommandBar from "./CommandBar";
import AnswerDock from "./AnswerDock";

// MapLibre and deck.gl need a browser: no server rendering for the map.
const MapView = dynamic(() => import("./MapView"), {
  ssr: false,
  loading: () => <div className="absolute inset-0 flex items-center justify-center text-sm text-muted">Loading the map</div>,
});

interface ClientApi {
  send(msg: ClientMessage): void;
  sendBinary(buf: ArrayBuffer): void;
}

const ClientCtx = createContext<ClientApi>({ send: () => {}, sendBinary: () => {} });
export const useClient = () => useContext(ClientCtx);

function ClientProvider({ children }: { children: ReactNode }) {
  const dispatch = useTowerDispatch();
  const params = useSearchParams();
  const forceMock = params.get("mock") === "1";
  const ref = useRef<TowerClient | null>(null);

  useEffect(() => {
    const client = connectTower({
      forceMock,
      onEvent: (event) => {
        dispatch({ type: "event", event });
        // A dictation final is held on the command bar for a beat, then the slice is cleared (the
        // reducer checks the age, so a newer final is never cleared by an older timer).
        if (event.type === "dictation" && event.payload.final) {
          setTimeout(() => dispatch({ type: "dictation_clear" }), DICTATION_HOLD_MS + 20);
        }
      },
      onStatus: (connection) => dispatch({ type: "connection", connection }),
      onLive: () => dispatch({ type: "reset" }),
    });
    ref.current = client;
    return () => {
      client.close();
      ref.current = null;
      dispatch({ type: "reset" });
    };
  }, [forceMock, dispatch]);

  // Who is on the frequency right now, so the map and the transcript can show it.
  useEffect(() => radio?.onChange((clip) => dispatch({ type: "on_air", clip: clip && { speaker: clip.speaker, callsign: clip.callsign } })), [dispatch]);

  const api = useMemo<ClientApi>(
    () => ({
      send: (msg) => ref.current?.send(msg),
      sendBinary: (buf) => ref.current?.sendBinary(buf),
    }),
    [],
  );
  return <ClientCtx.Provider value={api}>{children}</ClientCtx.Provider>;
}

function Screen() {
  const { alerts, resolving } = useTowerState();
  return (
    <div className="relative h-screen w-screen overflow-hidden bg-bg text-fg">
      {/* The map is the screen. Everything else floats over it. */}
      <MapView />

      {/* No panel behind the bar: only a soft fade so the words read over bright basemap. */}
      <div className="pointer-events-none absolute top-0 left-0 right-0 h-16 z-20 bg-gradient-to-b from-bg/70 to-transparent" />
      <div className="absolute top-0 left-0 right-0 z-20">
        <TopBar />
      </div>

      {/* The right rail: the alert on top, then analytics. It stops where the bottom row starts. */}
      <div
        className="absolute top-[52px] right-2 z-10 flex flex-col gap-2 overflow-y-auto scroll-thin pr-0.5"
        style={{ bottom: ROW_TOP + EDGE, width: SIDE_W }}
      >
        {(alerts.length > 0 || resolving.length > 0) && <AlertCard />}
        <ScoreboardPanel />
      </div>

      {/* The bottom row, left to right: Frequency, the chat bar and its dock, Sent by squack. */}
      <div className="absolute left-2 z-10" style={{ bottom: EDGE, height: BOTTOM_ROW_H, width: SIDE_W }}>
        <Transcript />
      </div>
      <div className="absolute right-2 z-10" style={{ bottom: EDGE, height: BOTTOM_ROW_H, width: SIDE_W }}>
        <InstructionCards />
      </div>

      <AnswerDock />
      <CommandBar />
      <SettingsSheet />
      <SetupPanel />
      <Notices />
      <BootScreen />
    </div>
  );
}

export default function TowerApp() {
  return (
    <TowerStoreProvider>
      <ClientProvider>
        <Screen />
      </ClientProvider>
    </TowerStoreProvider>
  );
}
