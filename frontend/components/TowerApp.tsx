"use client";

import { createContext, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import { TowerStoreProvider, useTowerDispatch, useTowerState } from "@/lib/store";
import { connectTower, type TowerClient } from "@/lib/ws";
import type { ClientMessage } from "@/lib/types";
import TopBar from "./TopBar";
import Radar from "./Radar";
import InstructionCards from "./InstructionCards";
import AlertCard from "./AlertCard";
import Transcript from "./Transcript";
import ScoreboardPanel from "./ScoreboardPanel";
import SlidersPanel from "./SlidersPanel";
import PushToTalk from "./PushToTalk";
import SetupPanel from "./SetupPanel";
import Notices from "./Notices";

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
      onEvent: (event) => dispatch({ type: "event", event }),
      onStatus: (connection) => dispatch({ type: "connection", connection }),
    });
    ref.current = client;
    return () => {
      client.close();
      ref.current = null;
      dispatch({ type: "reset" });
    };
  }, [forceMock, dispatch]);

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
  const { alerts, resolving, sim } = useTowerState();
  const lifecycle = sim?.lifecycle ?? (sim?.scenario ? "running" : "idle");
  return (
    <div className="relative h-screen w-screen flex flex-col gap-2 p-2 bg-bg text-fg">
      <SetupPanel />
      <Notices />
      <TopBar />
      <div className="flex-1 min-h-0 grid gap-2" style={{ gridTemplateColumns: "minmax(0,1fr) 400px" }}>
        <div className="min-h-0 flex flex-col gap-2">
          <div className="relative flex-1 min-h-0">
            <Radar />
            {(lifecycle === "ready" || lifecycle === "paused" || lifecycle === "ended") && (
              <div className="pointer-events-none absolute bottom-10 left-1/2 -translate-x-1/2 rounded-md border border-line bg-panel/90 px-4 py-2 text-sm text-muted">
                {lifecycle === "ready" && <>World loaded. Look over the plan, then press <span className="text-ok font-medium">Start</span>.</>}
                {lifecycle === "paused" && <>Paused. Press <span className="text-ok font-medium">Resume</span> to continue.</>}
                {lifecycle === "ended" && <>Every flight has left the sector. Press <span className="text-fg font-medium">Reset</span> to run it again.</>}
              </div>
            )}
          </div>
          <div className="h-48 shrink-0">
            <Transcript />
          </div>
        </div>
        <div className="min-h-0 flex flex-col gap-2 overflow-y-auto scroll-thin pr-1">
          {(alerts.length > 0 || resolving.length > 0) && <AlertCard />}
          <InstructionCards />
          <PushToTalk />
          <ScoreboardPanel />
          <SlidersPanel />
        </div>
      </div>
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
