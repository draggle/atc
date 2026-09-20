/**
 * Card descriptors: what the squack agent answers with. The agent never returns markup; it picks a
 * kind and fills its data, and `registry.tsx` renders it. Mirror of `backend/agent/cards.py`
 * (TRD 08, section 5, with the field names A1 settled on). Change both together.
 */
import type { Item } from "../types";

export type Tone = "ok" | "warn" | "bad";

/** A button on a card. UI commands are applied on the screen; anything else goes back to squack as text. */
export interface CardAction {
  label: string;
  command: string;
  args?: Record<string, unknown>;
}

/** What every card may carry on top of its kind's own fields. */
export interface CardBase {
  title?: string;
  /** seconds on screen before the card fades */
  ttl_s?: number;
  /** keep these numbers current from the store without another agent turn */
  live?: { aircraft?: string; scoreboard?: boolean };
  actions?: CardAction[];
}

export interface TextCard extends CardBase {
  kind: "text";
  text: string;
}

export interface TableCard extends CardBase {
  kind: "table";
  columns: string[];
  rows: (string | number)[][];
  /** a cell in this column is a callsign: click to focus */
  focus_col?: number;
  /** a muted line under the table: the run parameters, the units, what was held fixed */
  caption?: string;
}

export interface ListItem {
  title: string;
  detail?: string;
  callsign?: string;
  tone?: Tone;
}

export interface ListCard extends CardBase {
  kind: "list";
  items: ListItem[];
}

/** The same shape the alert card shows: what was cleared, what was heard, and why it matters. */
export interface CardIssue {
  title: string;
  expected: Item[];
  heard: Item[];
  reason?: string;
}

export interface AircraftCard extends CardBase {
  kind: "aircraft";
  callsign: string;
  issue?: CardIssue;
  fields?: { label: string; value: string | number }[];
}

export interface ComparisonRow {
  label: string;
  before: string | number;
  after: string | number;
  unit?: string;
}

export interface ComparisonCard extends CardBase {
  kind: "comparison";
  rows: ComparisonRow[];
}

export interface StepsCard extends CardBase {
  kind: "steps";
  steps: { n: number; tool: string; summary: string; done: boolean }[];
}

export type CardDescriptor = TextCard | TableCard | ListCard | AircraftCard | ComparisonCard | StepsCard;

export type CardKind = CardDescriptor["kind"];

// ---------------------------------------------------------------------------
// Agent events (TRD 08, sections 4 and 10)
// ---------------------------------------------------------------------------

/** One tool call of an agent turn, emitted as it happens. */
export interface AgentStep {
  turn_id: string;
  step: number;
  tool: string;
  args: Record<string, unknown>;
  result_summary: string;
  elapsed_ms: number;
}

/** The agent's one sentence plus its cards. `for` says what woke it: a typed message or an environment event. */
export interface Answer {
  turn_id: string;
  text: string;
  cards: CardDescriptor[];
  /** Always "message": squack only ever replies to something the controller asked. */
  for: "message";
}

export type UiCommandName = "focus" | "follow" | "camera" | "line_view" | "panel";

/** A ui.* tool ran: the screen applies it. Nothing in the world changed. */
export interface UiCommand {
  command: UiCommandName;
  args: Record<string, unknown>;
}


