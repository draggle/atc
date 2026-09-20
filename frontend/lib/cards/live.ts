"use client";

/**
 * Live bindings for cards. A card may say `live: {aircraft: "DAL789"}` or `live: {scoreboard: true}`;
 * these hooks resolve that from the store so the numbers stay current without another agent turn.
 * Also the one place a card's button is turned into something that happens.
 */
import { useCallback } from "react";
import { alertFor, useTowerDispatch, useTowerState } from "../store";
import type { AircraftState, Scoreboard } from "../types";
import type { CardAction, CardIssue, UiCommandName } from "./types";
import { useClient } from "@/components/TowerApp";

const UI_COMMANDS = new Set<string>(["focus", "follow", "camera", "line_view", "panel", "mode"]);

export function useLiveAircraft(callsign?: string | null): AircraftState | undefined {
  const { aircraft } = useTowerState();
  return callsign ? aircraft[callsign] ?? aircraft[callsign.toUpperCase()] : undefined;
}

/** The alert standing against this aircraft right now, in the shape an aircraft card's `issue` takes. */
export function useLiveIssue(callsign?: string | null): CardIssue | undefined {
  const state = useTowerState();
  const a = alertFor(state, callsign ?? null);
  if (!a || a.resolved) return undefined;
  const title = a.reason.startsWith("Radar:")
    ? "Not flying the clearance"
    : a.result === "missing" ? "No readback" : a.result === "mismatch" ? "Wrong readback" : a.result === "partial" ? "Partial readback" : "Unclear readback";
  return { title, expected: a.expected, heard: a.heard, reason: a.reason };
}

export function useLiveScoreboard(): Scoreboard | null {
  return useTowerState().scoreboard;
}

/** "FL350" from feet, or "3,500 ft" below the transition. */
export function fmtLevel(ft: number): string {
  return ft >= 18000 ? `FL${String(Math.round(ft / 100)).padStart(3, "0")}` : `${Math.round(ft).toLocaleString()} ft`;
}

/**
 * Run a card's button. A ui.* command is applied on the screen at once; anything else is a request
 * back to squack, sent as text so the agent loop stays the only thing that changes the world.
 */
export function useCardActions(): (action: CardAction) => void {
  const dispatch = useTowerDispatch();
  const { send } = useClient();
  return useCallback(
    (action: CardAction) => {
      const args = action.args ?? {};
      if (UI_COMMANDS.has(action.command)) {
        dispatch({ type: "ui_command", command: action.command as UiCommandName, args });
        return;
      }
      const text = typeof args.text === "string" ? args.text : `${action.command} ${Object.values(args).map(String).join(" ")}`.trim();
      dispatch({ type: "user_chat", text });
      send({ type: "agent_text", text });
    },
    [dispatch, send],
  );
}
