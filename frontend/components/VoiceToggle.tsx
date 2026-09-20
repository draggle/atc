"use client";

import { useTowerDispatch, useTowerState } from "@/lib/store";
import { useClient } from "./TowerApp";

/**
 * Action mode, the one switch, and it is the controller's at any moment. The wire is unchanged:
 * Manual is the old "Voice on" (`set_voice` true) and Autonomous the old "Voice off". Only the
 * words on screen changed; `auto_speak` and `voice` mean exactly what they always did.
 * Autonomous: squack sends every instruction by data link the instant the plan changes.
 * Manual: you say each card, the pilot reads it back, squack checks both.
 * Shared by the top bar, the settings sheet and the setup footer so all three drive one state.
 */
export default function VoiceToggle() {
  const { sim } = useTowerState();
  const dispatch = useTowerDispatch();
  const { send } = useClient();
  const on = sim?.voice ?? !(sim?.auto_speak ?? false); // on === Manual

  const set = (manual: boolean) => {
    if (manual === on) return;
    dispatch({ type: "local_toggle", key: "auto_speak", value: !manual });
    send({ type: "set_voice", enabled: manual });
  };

  return (
    <div className="seg seg-sm" role="group" aria-label="Action mode">
      <button
        type="button"
        aria-pressed={!on}
        onClick={() => set(false)}
        title="Autonomous: squack issues every instruction itself, the moment the plan changes."
      >
        Autonomous
      </button>
      <button
        type="button"
        aria-pressed={on}
        onClick={() => set(true)}
        title="You say each instruction and the pilot reads it back. Manual runs at 1x."
      >
        Manual
      </button>
    </div>
  );
}
