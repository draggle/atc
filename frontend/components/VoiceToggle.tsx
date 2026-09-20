"use client";

import { useTowerDispatch, useTowerState } from "@/lib/store";
import { useClient } from "./TowerApp";

/**
 * The one switch, and it is the controller's at any moment.
 * Off: squack sends every instruction by data link, instantly. The path demo.
 * On: the real loop. You say each card, the pilot reads it back, squack checks both.
 * Shared by the top bar and the setup sheet so both drive the same state.
 */
export default function VoiceToggle() {
  const { sim } = useTowerState();
  const dispatch = useTowerDispatch();
  const { send } = useClient();
  const on = sim?.voice ?? !(sim?.auto_speak ?? false);
  return (
    <button
      onClick={() => {
        const next = !on;
        dispatch({ type: "local_toggle", key: "auto_speak", value: !next });
        send({ type: "set_voice", enabled: next });
      }}
      aria-pressed={on}
      className={`pill ${on ? "pill-on" : ""}`}
      title="Voice off: squack sends every instruction by data link the instant the plan changes. Voice on: you say each instruction, the pilot reads it back, and our Whisper model checks both. Voice runs at 1x."
    >
      Voice {on ? "on" : "off"}
    </button>
  );
}
