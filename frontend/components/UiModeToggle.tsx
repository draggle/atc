"use client";

import { useTowerDispatch, useTowerState } from "@/lib/store";
import type { UiMode } from "@/lib/cards/types";
import { useClient } from "./TowerApp";

/**
 * normal: the hand-laid-out panels. squack decides: a stage of at most three cards that the agent
 * fills. Same backend, same events; the backend is told so it can wake the agent on events.
 * Never the default at open: the demo starts normal and the judge flips it.
 */
export default function UiModeToggle() {
  const { uiMode } = useTowerState();
  const dispatch = useTowerDispatch();
  const { send } = useClient();
  const set = (mode: UiMode) => {
    if (mode === uiMode) return;
    dispatch({ type: "set_ui_mode", mode });
    send({ type: "set_ui_mode", mode });
  };
  return (
    <div className="seg seg-sm" role="group" aria-label="Screen mode" data-testid="ui-mode">
      <button type="button" aria-pressed={uiMode === "normal"} onClick={() => set("normal")} title="The usual panels: alerts, instructions, scoreboard, sliders.">
        normal
      </button>
      <button type="button" aria-pressed={uiMode === "agent"} onClick={() => set("agent")} title="squack composes the screen: it puts up to three cards on the stage when something matters.">
        squack decides
      </button>
    </div>
  );
}
