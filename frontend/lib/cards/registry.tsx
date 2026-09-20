"use client";

/**
 * One component per card kind. Same language as the rail: flat panels, white ink, hairlines, B612
 * Mono for anything that has to line up, colour only where it means something. The alert card's
 * pieces (ItemList, its tones) are reused so an issue reads the same wherever it appears.
 */
import { useState, type ReactNode } from "react";
import { useTowerDispatch, useTowerState } from "../store";
import { ItemList } from "@/components/AlertCard";
import { fmtLevel, useLiveAircraft, useLiveIssue, useLiveScoreboard } from "./live";
import type {
  AircraftCard as AircraftCardT,
  CardDescriptor,
  CardIssue,
  ChartCard as ChartCardT,
  ComparisonCard as ComparisonCardT,
  ListCard as ListCardT,
  StepsCard as StepsCardT,
  TableCard as TableCardT,
  TextCard as TextCardT,
  Tone,
} from "./types";

export const MONO = { fontFamily: "var(--font-mono)" } as const;
const LINK = "text-xs text-muted hover:text-fg underline decoration-dotted underline-offset-4";
const TONE_TEXT: Record<Tone, string> = { ok: "text-ok", warn: "text-warn", bad: "text-bad" };
const TONE_DOT: Record<Tone, string> = { ok: "dot-ok", warn: "dot-warn", bad: "dot-bad" };
const TONE_BAR: Record<Tone, string> = { ok: "var(--ok)", warn: "var(--warn)", bad: "var(--bad)" };

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const fmtCell = (v: string | number) => (isNum(v) ? (Number.isInteger(v) ? String(v) : v.toFixed(Math.abs(v) < 10 ? 2 : 1)) : String(v));

/** A callsign that takes you to the aircraft. */
function Focus({ callsign, children }: { callsign: string; children?: ReactNode }) {
  const dispatch = useTowerDispatch();
  return (
    <button
      type="button"
      onClick={() => dispatch({ type: "focus", callsign })}
      title={`Show ${callsign} on the map`}
      className="underline decoration-dotted decoration-muted underline-offset-4 hover:decoration-fg text-fg"
      style={MONO}
    >
      {children ?? callsign}
    </button>
  );
}

// ---------------------------------------------------------------- text
export function TextCard({ card }: { card: TextCardT }) {
  return <p className="text-[13px] leading-snug text-fg/95 whitespace-pre-line">{card.text}</p>;
}

// ---------------------------------------------------------------- table
export function TableCard({ card }: { card: TableCardT }) {
  const { aircraft } = useTowerState();
  if (card.rows.length === 0) return <p className="text-xs text-muted">No rows.</p>;
  return (
    <div className="overflow-x-auto scroll-thin -mx-1">
      <table className="w-full text-[12px] border-collapse">
        <thead>
          <tr>
            {card.columns.map((c, i) => (
              <th key={i} className="text-left font-medium text-muted px-1 pb-1 border-b border-line whitespace-nowrap">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {card.rows.map((row, r) => (
            <tr key={r} className="border-b border-line/50 last:border-b-0">
              {row.map((cell, c) => {
                const focusable = c === card.focus_col && typeof cell === "string" && (cell in aircraft || /^[A-Z]{2,3}\d{1,4}[A-Z]?$/.test(cell));
                return (
                  <td key={c} className={`px-1 py-1 align-top ${isNum(cell) ? "tabular-nums text-right" : "text-left"} text-fg/95`} style={MONO}>
                    {focusable ? <Focus callsign={cell} /> : fmtCell(cell)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- list
export function ListCard({ card }: { card: ListCardT }) {
  if (card.items.length === 0) return <p className="text-xs text-muted">Nothing yet.</p>;
  return (
    <ul className="space-y-1.5">
      {card.items.map((it, i) => (
        <li key={i} className="flex items-start gap-2 text-[12px]">
          <i className={`dot mt-1.5 ${it.tone ? TONE_DOT[it.tone] : ""}`} />
          <div className="min-w-0 flex-1">
            <div className={`leading-snug ${it.tone ? TONE_TEXT[it.tone] : "text-fg"}`}>
              {it.title}
              {it.callsign && <span className="ml-2 text-[11px]"><Focus callsign={it.callsign} /></span>}
            </div>
            {it.detail && <div className="text-[11px] text-muted leading-snug">{it.detail}</div>}
          </div>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------- aircraft
/** The same block the flight strip shows, in the alert's tones: red for wrong, amber for unsure. */
export function IssueBlock({ issue }: { issue: CardIssue }) {
  const wrong = /wrong|no readback|not flying/i.test(issue.title);
  return (
    <div className={`rounded-md border border-line border-l-2 ${wrong ? "border-l-bad" : "border-l-warn"} bg-panel px-3 py-2`}>
      <div className={`text-sm font-semibold ${wrong ? "text-bad" : "text-warn"}`}>{issue.title}</div>
      <div className="mt-2 grid grid-cols-2 gap-3">
        <div className="min-w-0">
          <div className="text-[11px] text-muted mb-0.5">Expected</div>
          <ItemList items={issue.expected} tone="expected" size="sm" />
        </div>
        <div className="min-w-0">
          <div className="text-[11px] text-muted mb-0.5">Heard</div>
          <ItemList items={issue.heard} tone="heard" size="sm" />
        </div>
      </div>
      {issue.reason && <p className="mt-1.5 text-[11px] leading-snug text-muted">{issue.reason}</p>}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="min-w-0 flex items-baseline justify-between gap-3 py-1 border-b border-line/60">
      <span className="text-[11px] text-muted shrink-0">{label}</span>
      <span className="text-[13px] tabular-nums truncate text-fg" style={MONO}>{fmtCell(value)}</span>
    </div>
  );
}

export function AircraftCard({ card }: { card: AircraftCardT }) {
  const dispatch = useTowerDispatch();
  const { selected, follow } = useTowerState();
  const callsign = card.callsign.toUpperCase();
  const live = useLiveAircraft(card.live?.aircraft ?? callsign);
  const liveIssue = useLiveIssue(card.issue ? null : callsign);
  const issue = card.issue ?? liveIssue;
  const following = selected === callsign && follow;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <div className="text-base font-semibold text-fg" style={MONO}>{callsign}</div>
        {live ? (
          <div className="text-[12px] text-fg/90 tabular-nums" style={MONO}>
            {fmtLevel(live.alt_ft)}
            {live.target_alt_ft !== live.alt_ft && <span className="text-muted"> → {fmtLevel(live.target_alt_ft)}</span>}
            <span className="text-muted"> · </span>H{String(Math.round(live.hdg_deg) % 360 || 360).padStart(3, "0")}
            <span className="text-muted"> · </span>{Math.round(live.gs_kt)} kt
          </div>
        ) : (
          <span className="text-[11px] text-muted">not on the radar</span>
        )}
      </div>
      {live && <div className="text-[11px] text-muted mt-0.5">{live.actype}{live.route.length > 0 && <> · to {live.route[live.route.length - 1]}</>}</div>}
      {issue && <div className="mt-2"><IssueBlock issue={issue} /></div>}
      {card.fields && card.fields.length > 0 && (
        <div className="mt-2 grid grid-cols-1">
          {card.fields.map((f, i) => <Field key={i} label={f.label} value={f.value} />)}
        </div>
      )}
      <div className="mt-2 flex items-center gap-3">
        <button type="button" className={LINK} onClick={() => dispatch({ type: "focus", callsign })}>Focus</button>
        <button
          type="button"
          className={`${LINK} ${following ? "!text-fg" : ""}`}
          onClick={() => (following ? dispatch({ type: "set_follow", on: false }) : dispatch({ type: "focus", callsign }))}
        >
          {following ? "Following" : "Follow"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- comparison
export function ComparisonCard({ card }: { card: ComparisonCardT }) {
  return (
    <table className="w-full text-[12px] border-collapse">
      <thead>
        <tr className="text-muted">
          <th className="text-left font-medium px-1 pb-1 border-b border-line">&nbsp;</th>
          <th className="text-right font-medium px-1 pb-1 border-b border-line">before</th>
          <th className="text-right font-medium px-1 pb-1 border-b border-line">after</th>
          <th className="text-right font-medium px-1 pb-1 border-b border-line">Δ</th>
        </tr>
      </thead>
      <tbody>
        {card.rows.map((r, i) => {
          const delta = isNum(r.before) && isNum(r.after) ? r.after - r.before : null;
          return (
            <tr key={i} className="border-b border-line/50 last:border-b-0">
              <td className="px-1 py-1 text-fg/90">{r.label}</td>
              <td className="px-1 py-1 text-right tabular-nums text-muted" style={MONO}>{fmtCell(r.before)}{r.unit ? ` ${r.unit}` : ""}</td>
              <td className="px-1 py-1 text-right tabular-nums text-fg" style={MONO}>{fmtCell(r.after)}{r.unit ? ` ${r.unit}` : ""}</td>
              <td className="px-1 py-1 text-right tabular-nums text-muted" style={MONO}>
                {delta === null ? "" : `${delta > 0 ? "+" : ""}${fmtCell(delta)}`}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------- chart
/** Ink on the ground, tones only when a series carries meaning. */
const SERIES_INK = (t?: string) => (t ? TONE_BAR[t as keyof typeof TONE_BAR] ?? "var(--fg)" : "var(--fg)");

/** A number a person would read: 4 significant-ish digits, no trailing zeros. */
function tick(n: number): string {
  const a = Math.abs(n);
  if (a >= 1000) return Math.round(n).toLocaleString();
  if (a >= 10) return String(Math.round(n));
  if (a >= 1) return n.toFixed(1).replace(/\.0$/, "");
  return n.toFixed(2).replace(/0$/, "");
}

/** Four or five ticks that land on round numbers, and the scale they imply. */
function scale(lo: number, hi: number): { lo: number; hi: number; ticks: number[] } {
  if (!isFinite(lo) || !isFinite(hi)) return { lo: 0, hi: 1, ticks: [0, 1] };
  if (hi - lo < 1e-9) { const pad = Math.abs(hi) * 0.2 || 1; lo -= pad; hi += pad; }
  const raw = (hi - lo) / 4;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? mag * 10;
  const start = Math.floor(lo / step) * step;
  const end = Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  for (let v = start; v <= end + step / 2; v += step) ticks.push(Math.abs(v) < step / 1e6 ? 0 : v);
  return { lo: start, hi: end, ticks };
}

const NO_DATA = <p className="text-[12px] text-muted">No data to draw yet.</p>;

/** Line, histogram and scatter share one frame: hairline grid, mono ticks, marks on top. */
function Plot({ card }: { card: ChartCardT }) {
  const [hover, setHover] = useState<{ x: number; y: number; label: string } | null>(null);
  const kind = card.chart ?? "line";
  const series = (card.series ?? []).filter((s) => Array.isArray(s.points) && s.points.length > 0);
  if (series.length === 0) return NO_DATA;

  const W = 340, H = 168, L = 38, R = 8, T = 10, B = 22;
  const xs = series.flatMap((s) => s.points!.map((p) => p[0]));
  const ys = series.flatMap((s) => s.points!.map((p) => p[1]));
  const sx = scale(Math.min(...xs), Math.max(...xs));
  const sy = scale(kind === "hist" ? 0 : Math.min(...ys), Math.max(...ys));
  const px = (v: number) => L + ((v - sx.lo) / (sx.hi - sx.lo || 1)) * (W - L - R);
  const py = (v: number) => H - B - ((v - sy.lo) / (sy.hi - sy.lo || 1)) * (H - T - B);

  // A histogram's bars touch: width comes from the gap between bin starts.
  const binW = kind === "hist" && series[0].points!.length > 1
    ? Math.max(2, px(series[0].points![1][0]) - px(series[0].points![0][0]) - 1)
    : 10;

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img"
           aria-label={card.title ?? "chart"} style={{ display: "block", overflow: "visible" }}>
        {sy.ticks.map((t, i) => (
          <g key={`y${i}`}>
            <line x1={L} x2={W - R} y1={py(t)} y2={py(t)} stroke="var(--line)" strokeWidth="1" />
            <text x={L - 5} y={py(t) + 3} textAnchor="end" fill="var(--muted)" fontSize="9" style={MONO}>{tick(t)}</text>
          </g>
        ))}
        {sx.ticks.map((t, i) => (
          <text key={`x${i}`} x={px(t)} y={H - B + 12} textAnchor="middle" fill="var(--muted)" fontSize="9" style={MONO}>{tick(t)}</text>
        ))}
        {card.y_label && <text x={L - 30} y={T + 4} fill="var(--muted)" fontSize="9">{card.y_label}</text>}
        {card.x_label && <text x={W - R} y={H - 2} textAnchor="end" fill="var(--muted)" fontSize="9">{card.x_label}</text>}

        {series.map((s, si) => {
          const ink = SERIES_INK(s.tone);
          const pts = s.points!;
          if (kind === "hist") {
            return pts.map((p, i) => (
              <rect key={`${si}-${i}`} x={px(p[0])} y={py(p[1])} width={binW} height={Math.max(0, py(sy.lo) - py(p[1]))}
                    fill={ink} opacity={0.75}
                    onMouseEnter={() => setHover({ x: px(p[0]) + binW / 2, y: py(p[1]), label: `${tick(p[0])}: ${tick(p[1])}` })}
                    onMouseLeave={() => setHover(null)} />
            ));
          }
          if (kind === "scatter") {
            return pts.map((p, i) => (
              <circle key={`${si}-${i}`} cx={px(p[0])} cy={py(p[1])} r="3" fill={ink} opacity={0.85}
                      onMouseEnter={() => setHover({ x: px(p[0]), y: py(p[1]), label: `${s.label}: ${tick(p[0])}, ${tick(p[1])}` })}
                      onMouseLeave={() => setHover(null)} />
            ));
          }
          const d = pts.map((p, i) => `${i ? "L" : "M"}${px(p[0])} ${py(p[1])}`).join(" ");
          const last = pts[pts.length - 1];
          return (
            <g key={si}>
              {pts.length === 1
                ? <circle cx={px(last[0])} cy={py(last[1])} r="3.5" fill={ink} />
                : <path d={d} fill="none" stroke={ink} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" opacity={0.9} />}
              <circle cx={px(last[0])} cy={py(last[1])} r="2.5" fill={ink} />
              <text x={px(last[0]) - 4} y={py(last[1]) - 6} textAnchor="end" fill={ink} fontSize="9" style={MONO}>{tick(last[1])}</text>
            </g>
          );
        })}
      </svg>
      {series.length > 1 && (
        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
          {series.map((s, i) => (
            <span key={i} className="inline-flex items-center gap-1.5 text-[11px] text-muted">
              <span style={{ width: 8, height: 2, background: SERIES_INK(s.tone), display: "inline-block" }} />{s.label}
            </span>
          ))}
        </div>
      )}
      {hover && (
        <span className="pointer-events-none absolute px-1.5 py-0.5 rounded bg-panel-2 border border-line text-[10px] text-fg whitespace-nowrap"
              style={{ left: `${(hover.x / 340) * 100}%`, top: `${(hover.y / 168) * 100}%`, transform: "translate(-50%,-140%)", ...MONO }}>
          {hover.label}
        </span>
      )}
    </div>
  );
}

/** Horizontal bars, plain divs. White unless a bar carries a tone. */
function Bars({ card }: { card: ChartCardT }) {
  const series = (card.series ?? []).filter((s) => typeof s.value === "number");
  if (series.length === 0) return NO_DATA;
  const max = Math.max(1e-9, ...series.map((s) => Math.abs(s.value!)));
  return (
    <div className="space-y-1.5">
      {series.map((s, i) => (
        <div key={i} className="grid grid-cols-[minmax(0,9rem)_1fr_auto] items-center gap-2 text-[12px]">
          <span className="truncate text-fg/90" title={s.label}>{s.label}</span>
          <div className="h-2 rounded-sm bg-line/60 overflow-hidden">
            <div className="h-full rounded-sm" style={{ width: `${Math.max(1, (Math.abs(s.value!) / max) * 100)}%`, background: SERIES_INK(s.tone), opacity: s.tone ? 0.9 : 0.85 }} />
          </div>
          <span className={`tabular-nums ${s.tone ? TONE_TEXT[s.tone] : "text-fg"}`} style={MONO}>{fmtCell(s.value!)}{card.unit ? ` ${card.unit}` : ""}</span>
        </div>
      ))}
    </div>
  );
}

export function ChartCard({ card }: { card: ChartCardT }) {
  const kind = card.chart ?? "bars";
  return (
    <div>
      {kind === "bars" ? <Bars card={card} /> : <Plot card={card} />}
      {card.caption && <p className="mt-2 text-[11px] text-muted leading-snug">{card.caption}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- steps
export function StepsCard({ card, open: openInitial = true }: { card: StepsCardT; open?: boolean }) {
  const [open, setOpen] = useState(openInitial);
  const pending = card.steps.some((s) => !s.done);
  return (
    <div>
      <button type="button" onClick={() => setOpen(!open)} className="flex items-center gap-2 text-xs text-muted hover:text-fg">
        <span className="w-3 text-center">{open ? "▾" : "▸"}</span>
        <span>{pending ? "Working" : "What squack did"}</span>
        <span className="tabular-nums">· {card.steps.length} step{card.steps.length === 1 ? "" : "s"}</span>
        {pending && <span className="dot dot-warn animate-pulse" />}
      </button>
      {open && (
        <ol className="mt-2 space-y-1.5">
          {card.steps.map((s) => (
            <li key={s.n} className="text-xs grid grid-cols-[1.25rem_1fr] gap-1">
              <span className="text-muted tabular-nums">{s.n}.</span>
              <div>
                <span className="text-muted" style={MONO}>{s.tool}</span>
                {!s.done && <span className="dot dot-warn animate-pulse ml-2 align-middle" />}
                <div className="text-fg/90 mt-0.5">{s.summary}</div>
              </div>
            </li>
          ))}
          {card.steps.length === 0 && <li className="text-xs text-muted">Waiting for squack…</li>}
        </ol>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- scoreboard binding
/** `live: {scoreboard: true}` on any card adds the headline numbers, kept current from the store. */
export function LiveScoreboard() {
  const s = useLiveScoreboard();
  if (!s) return null;
  const fmt = (n: number | null | undefined, d = 1, suffix = "") => (n === null || n === undefined ? "—" : `${n.toFixed(d)}${suffix}`);
  return (
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 border-t border-line pt-2">
      <span className="stat">miles saved <b>{fmt(s.miles_saved)}</b></span>
      <span className="stat">LoS <b className={s.losses_of_separation > 0 ? "!text-bad" : ""}>{s.losses_of_separation}</b></span>
      <span className="stat">closest <b className={s.closest_approach_nm !== null && s.closest_approach_nm < 5 ? "!text-bad" : ""}>{fmt(s.closest_approach_nm, 1, " NM")}</b></span>
      <span className="stat">errors caught <b>{s.errors_caught} / {s.errors_injected}</b></span>
      {s.reaction_s != null && <span className="stat">first turn <b>{fmt(s.reaction_s, 0, " s")}</b></span>}
    </div>
  );
}

// ---------------------------------------------------------------- registry
export function renderBody(card: CardDescriptor): ReactNode {
  switch (card.kind) {
    case "text": return <TextCard card={card} />;
    case "table": return <TableCard card={card} />;
    case "list": return <ListCard card={card} />;
    case "aircraft": return <AircraftCard card={card} />;
    case "comparison": return <ComparisonCard card={card} />;
    case "chart": return <ChartCard card={card} />;
    case "steps": return <StepsCard card={card} />;
    default: {
      // An unknown kind renders as text with the JSON folded away, never as nothing.
      const unknown = card as { kind?: string; text?: string; title?: string };
      return (
        <div>
          <p className="text-[13px] text-fg/95">{unknown.text ?? unknown.title ?? `Unknown card kind "${unknown.kind}"`}</p>
          <details className="mt-1">
            <summary className="text-[11px] text-muted cursor-pointer">raw</summary>
            <pre className="mt-1 text-[11px] text-muted whitespace-pre-wrap break-all" style={MONO}>{JSON.stringify(card, null, 1)}</pre>
          </details>
        </div>
      );
    }
  }
}
