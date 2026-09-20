# TRD 08: The squack agent. A command bar that operates the airspace and the screen

Written Sunday Sept 20 on `joey/command-bar`, against 437 backend tests. The founder's brief: "start with a command bar on the bottom, build that out to go into an agent that can tool-call and run reasoning and action loops over the data, interact with the UI, create custom UI, codegen, run sims." This is that idea cut into rungs a teammate's Claude session can build today, with the ambitious parts named, costed, and mostly deferred. `docs/10-roadmap.md` stays the plan; this is the spec for one feature.

## 1. Vision

"Cursor for flight": you talk, and squack operates the airspace and the screen for you. One text field at the bottom of the map takes anything. Standard phraseology goes to the frequency and a plane turns. Anything else goes to an agent that can change the world, drive the map, answer questions from live state with a table or a chart instead of a paragraph, explain its own decisions from the planner's real reasons, and run the simulator in the background to put a number on a claim. A judge holding the mic can say anything and see something happen within a second or two, with a visible trace of what the agent did.

**The agent's one job:** turn a sentence into tool calls over the world, the screen and the simulator, and answer with a card, never touching the frequency itself.

## 2. What this builds on

| Piece | Where | What it gives the agent |
|---|---|---|
| World-builder agent | `backend/world_agent.py` | A tool loop on Baseten (max 3 calls, `_llm_agent`) plus a keyword router with no key (`_keyword_agent`). Tools: `load_scenario`, `spawn_flight`, `add_disruption`, `multiply_traffic`, `set`, `describe`. Backed by `World.tool_*` (`world.py:1883-1931`). Reached by `{"type":"agent_text"}` and the headset PTT channel; answers with `agent_reply {text, actions}` |
| Interpreter agent | `backend/tower/interpreter.py` | The pattern to copy: one Baseten call, `tool_choice="required"`, a compact `picture()` of the sky as bearing and distance, aircraft controls as tools, `unable` as the escape, validated by `freeform.valid`, run in a thread with a 6 s cap |
| Resolver tools and loop | `backend/tower/resolver/tools.py`, `resolver/agent.py` | Read-only radar and log tools already shaped as OpenAI schemas: `active_aircraft`, `frequency_history`, `aircraft_state`, `nearby_aircraft`, `aircraft_track`, `sanity_check`. The loop shape we reuse: `MAX_TOOL_CALLS = 4`, `BUDGET_S = 5.0`, one `resolver_step` event per call, `summarize()` for one-line trace text |
| Memory | `backend/tower/memory.py` | `Memory.observe` sees every emitted event; `history`, `nearby`, `track`, `closest_waypoint` on Elasticsearch when `ELASTIC_URL` is set, `NullMemory` otherwise. Query tools read memory first and fall back to in-process state |
| Eval harness | `backend/eval/montecarlo.py::run`, `eval/sweep.py::sweep`, `eval/run_eval.py` | Three arms, LoS per flight hour, closest approach p5/p50, miles vs fixed, conflicts predicted and resolved; the sweep returns `SweepRow`s and writes CSV and PNG. Pure functions on a `Scenario`, no World, so they run in a thread or subprocess |
| Risk and confidence | `backend/planner/risk.py`, `PlannedPath.runner_up_cost`, `InstructionCard.confidence/risk_after`, `risk` event | The material for a grounded "why" and "how sure" |
| Planner reasons | `PlannedPath.changes` (text like `dogleg ... to clear STORM1`), `PlannedPath.cost`, `InstructionCard.reason/cause/origin`, `Plan.trigger`, `plan_update {changed, reason, trigger}` | Every card already says why it exists. The explain tool reads these, it does not generate them |
| Client messages | `backend/app.py:256-331`, `frontend/lib/types.ts::ClientMessage` | `configure`, `start`, `pause`, `reset`, `set_speed`, `set_voice`, `set_tower`, `add_disruption` (with `target`), `remove_disruption`, `speak_card`, `set_sliders`, `set_next_readback`, `agent_text`, `radio_text` |
| Screen state | `frontend/lib/store.tsx` | `selected`, `follow`, `focusSeq`, `planView` (`today\|tower\|both\|changed`), `setupOpen`; the `focus` action already flies the camera. Event whitelist in `lib/ws.ts`, `EventMap` in `lib/types.ts` |
| Mic routing | `frontend/components/PushToTalk.tsx` | Space is radio, Shift+Space is headset; a typed `agentText` box exists and sends `agent_text`. The command bar replaces that box |

Nothing named "command bar" exists yet in `frontend/`.

## 3. The capability ladder

Effort is one person with a Claude session, including tests. Demo value is 1 to 5.

**(a) Command bar routing.** 2 h. Risk low. Value 3.
Says: "Air Canada one two three descend flight level three one zero" or "drop a storm on Delta 789".
Screen: one field docked at the bottom of the map, Enter sends. The bar shows which channel took it ("radio" or "squack") and the last reply as a one-line card. Standard phraseology is detected client-side by `lib/interp.ts` patterns (callsign plus an instruction verb) and sent as `radio_text`; everything else as `agent_text`. A prefix overrides: `/radio ...`, `/ask ...`. The mic keeps its two channels.
Tools: none new. Files: `frontend/components/CommandBar.tsx`, `lib/interp.ts` gains `looksLikePhraseology(text)`.

**(b) UI control tools.** 3 h. Risk low. Value 4.
Says: "focus on Delta 789", "follow it", "tilt the map", "show only what changed", "20x", "voice on", "open the scoreboard", "load Europe at four", "storm ahead of ACA133", "buffer 3 miles".
Screen: the map does it, and the bar shows "focused DAL789". A new `ui_command` event carries `{command, args}`; the store applies it with the existing actions (`focus`, `set_follow`, `set_plan_view`, `set_setup_open`) plus new ones for camera pitch, bearing, exaggeration and which panel is open. World-side tools call what `app.py` already exposes: `set_speed`, `set_voice`, `set_sliders`, `add_disruption(target=)`, `load`.
Tools: `ui.focus`, `ui.follow`, `ui.camera`, `ui.line_view`, `ui.panel`; `world.set_speed`, `world.set_voice`, `world.set_sliders`, `world.load`, `world.disrupt`, `world.remove_disruption`, `world.start_pause_reset`.

**(c) Query tools with a table card.** 3 h. Risk low. Value 4.
Says: "which flights are within ten miles of each other", "who is above FL350 heading west", "what did WJA456 read back", "list the cards still pending", "what is the closest pair right now".
Screen: a table card in the bar's answer area: columns and rows, click a callsign to focus. The trace line says "aircraft_list → 22 rows, filtered → 3".
Tools: `query.aircraft(filter)` over `radar_payload`, `query.pairs(max_nm)` over the separation monitor and `risk` report, `query.cards(status)`, `query.log(callsign, n, text)` via `Memory.history` then the in-process transcript, `query.scoreboard`. Filters are a small structured object `{field, op, value}`, not SQL.

**(d) Explain tools.** 2 h. Risk low. Value 5. The judge's question is always "why".
Says: "why did you turn Delta 789", "how confident are you in that card", "what did that storm cost".
Screen: an aircraft card: callsign, the card's `reason` and `cause`, the path's `changes` list, `cost` vs `runner_up_cost`, `confidence` with both terms ("risk after 0.03, margin 1.0"), miles added, and a Focus button. For a disruption: rerouted flights, extra miles summed from `PlannedPath.distance_nm` vs the baseline path, reaction time from the scoreboard.
Tools: `explain.card(callsign)`, `explain.flight(callsign)`, `explain.disruption(id)`, `explain.replan(last)`. All read existing fields; the model only phrases one sentence on top. Hard rule 10.

**(e) Sim tools with a chart card.** 4 h. Risk medium (CPU contention with the live clock, roadmap phase 5 warned the planner is wall-clock bounded). Value 5.
Says: "run the Monte Carlo on this scenario", "double the traffic and tell me if it still holds", "sweep density one to two".
Screen: the bar says "running 20 runs in the background, about 40 s", a progress line, then a chart card: LoS per flight hour by arm, closest p5, miles vs fixed, with the run's parameters in the caption. Never blocks the clock: `asyncio.to_thread` at minimum, a `multiprocessing` subprocess when the world is running so the tick loop does not compete for the GIL.
Tools: `sim.montecarlo(scenario, runs, density, error_rate, buffer_nm)`, `sim.sweep(scenario, densities, buffers, runs)`, `sim.status(job_id)`, `sim.cancel(job_id)`. A `sim_job` event carries progress and the result rows. Default runs 8, cap 20; the sweep caps at 4 points.

**(f) Reasoning and action loops.** 4 h. Risk medium (the model wanders; a 4-call cap makes it terse). Value 4.
Says: "find the two flights closest to each other and separate them, then tell me what it cost".
Screen: a steps card that fills in live: 1 `query.pairs` → ACA123/WJA456 at 6.1 NM; 2 `world.disrupt`? No: the agent may not issue a clearance, so "separate them" means `world.nudge(callsign)` which asks the planner to re-plan that flight with a bigger buffer (`_replan("agent", repin={cs})`, buffer temporarily +2 NM), which produces a card and, with voice off, a data-link instruction; 3 `explain.replan` → +4 NM. Same loop as `world_agent._llm_agent`, cap raised to 4 calls and 8 s, every call emitted as `agent_step`.
Tools: the union of (b) to (e) plus `world.nudge`. The value is the visible trace, not the cleverness.

**(g) Generative UI.** Registry 3 h (ships with c, d, e). Beyond the registry: cut. Value 4.
The agent never returns markup. It returns a card descriptor (section 5) and the frontend renders it from a registry of six components. This is the Vercel AI SDK generative UI idea (tool calls stream React components) with the component set fixed on our side and the model choosing kind and data. "Beyond the registry" means the model composing layouts or new components: not this weekend.

**(h) Codegen.** 6 h or more. Risk high. Value 2 for the demo. Cut, with one exception in section 7.
Writing a new disruption kind or an eval sweep as code, running tests, hot-loading: sandboxing and the failure modes on stage are the whole cost. The one narrow case worth having is a scenario YAML through the existing loader.

**(i) Memory.** 2 h on top of (c). Risk low if Elastic is up, otherwise it is the in-process log. Value 3.
Says: "what happened in the last ten minutes", "how many wrong readbacks so far", "what did I ask you earlier".
Screen: a list card of events in order: disruptions, replans, alerts, corrections, agent actions.
Tools: `query.timeline(since_s, kinds)` reading `Memory` indices (`verdicts`, `clearances`, `resolver_steps`) plus a new in-process ring buffer of the last 500 emitted events that `World._emit` appends to. The agent's own prior turns are kept in a per-connection list, last 10, sent with each request.

## 4. Architecture

```
CommandBar ──agent_text──> app.py ──> agent.run(text, world, ui_state)   [thread, off the clock]
                                          │  one loop on Baseten (RESOLVER_MODEL), tool_choice auto
                                          │  registry: world.* ui.* query.* explain.* sim.* (code.* stretch)
                                          ├─ agent_step  {step, tool, args, summary, ms}     per call
                                          ├─ ui_command  {command, args}                     when a ui.* tool runs
                                          ├─ sim_job     {id, status, progress, rows?}       from a background job
                                          └─ answer      {text, card: CardDescriptor, steps}  once, always
```

- **One loop, one provider.** `backend/agent/loop.py` generalises `world_agent._llm_agent`: same `LLM.chat(messages, tools=...)`, model from `RESOLVER_MODEL` on Baseten (hard rule 5). `world_agent.py` becomes a thin caller of the new loop so the headset channel and the bar share one brain.
- **Tool registry.** `backend/agent/tools/{world,ui,query,explain,sim}.py`, each exporting `SCHEMAS: list[dict]` and `run(world, name, args) -> Any`. A tool's result is JSON; `summarize(name, result)` gives the one-line trace, as in `resolver/tools.py`. `ui.*` tools do not touch `World`: they emit `ui_command` and return `{"ok": true}`; the frontend applies them. The agent is told the current screen state (`selected`, `planView`, `speed`, `voice`) in the system prompt so "follow it" resolves.
- **Off the clock.** `app.py` spawns `world.agent_request` with `_spawn` as today; the loop runs in `asyncio.to_thread`. It reads `World` state under the same snapshot functions the resolver uses (`radar_payload`, plan, cards); the only writes go through existing `World` methods that already take the lock. Sim jobs run in a subprocess; results come back over a queue polled once a second.
- **Caps.** 4 tool calls, 8 s wall time, 600 output tokens per model turn, 30 s for a sim job before it is reported as still running and the answer goes out without it. On any cap the loop ends in an `answer` that says what it managed (the resolver rule: never silent).
- **Safety rules.** The agent has no tool that opens a clearance: no `radio_text`, no `speak_card`. "Separate them" is `world.nudge`, which goes through the planner and produces a card. `set_sliders` clamps `buffer_nm` to the existing range; the 5 NM / 1,000 ft floor is in the planner, not in the agent (hard rule 8). Every world tool returns the inverse action (`{"undo": {"tool": "world.remove_disruption", "args": {...}}}`) and the last three are kept per connection, so "undo" is a tool call, not a world snapshot: there is no mid-run snapshot today (`reset` rebuilds from `_base_scenario`), and building one is section 8.
- **Without a Baseten key.** `backend/agent/router.py`: a keyword router for rungs (a) and (b) only, extended from `world_agent._keyword_agent`: focus, follow, speed, voice, line view, load, disrupt, sliders, start, pause, reset, describe. Query, explain and sim rungs return a `text` card: "needs BASETEN_API_KEY". Say so in the README row.
- **Events to add** (three places on the screen, per the convention): `agent_step`, `answer`, `ui_command`, `sim_job`. `agent_reply` stays for one release and is emitted alongside `answer`; remove it once the bar is the only caller.

## 5. Card descriptor

```json
{"kind": "table", "title": "Pairs under 10 NM", "columns": ["a", "b", "nm", "ft"],
 "rows": [["ACA123", "WJA456", 6.1, 0]], "focus_column": 0}
```

| kind | fields | renders as |
|---|---|---|
| `text` | `text` | one or two lines under the bar |
| `table` | `title, columns[], rows[][], focus_column?` | table; a cell in `focus_column` is a callsign button that dispatches `focus` |
| `list` | `title, items[{t, text, kind?}]` | timeline with sim clock stamps |
| `aircraft` | `callsign, level_ft, hdg, gs_kt, card?{phrase, reason, cause, confidence, risk_after, margin}, changes[], cost, runner_up_cost, extra_nm` | flight card with Focus and Follow buttons |
| `comparison` | `title, columns[], rows[][], highlight_row?` | two or three arms side by side (eval arms, before and after a replan) |
| `chart` | `title, x_label, y_label, series[{name, points[[x,y]]}], caption` | one line chart in the bar's answer area, drawn with plain SVG in `lib/cards/Chart.tsx` (no new dependency); colours from `eval/sweep.py::ARM_COLOR` |
| `steps` | `steps[{n, tool, summary, ms, status}]` | the trace; filled live from `agent_step`, finalised by `answer` |

`frontend/lib/cards/registry.tsx` maps `kind` to a component; an unknown kind renders as `text` with the JSON collapsed. Pydantic mirror in `backend/agent/cards.py`; every tool that answers builds one of these, the model never types JSON for a card.

## 6. Ninety-second demo, judge on the mic

1. "Load Europe at four in the afternoon." The map fills, bar says "159 flights, zero conflicts".
2. "Focus on Lufthansa four alfa bravo and follow it." Camera flies, chip on the bar.
3. "Put a storm ahead of it." Storm lands on its path, orange line flashes, card appears, plane turns (voice off).
4. "Why did you turn it?" Aircraft card: "dogleg 12 NM to clear STORM1, cost 84 s, runner-up 131 s, confidence 0.94, risk after 0.02".
5. "Who else is within ten miles of anyone?" Table, three rows, click one, camera goes.
6. "Lufthansa four alfa bravo, descend flight level three three zero." Routed to the radio, pilot reads back, card goes green. The judge just talked to a plane in the same box.
7. "Double the traffic and run the Monte Carlo." "Running 8 runs, about 30 s." Keep talking while it runs.
8. "What happened in the last five minutes?" List card: storm, six reroutes, one wrong readback caught in 4 s.
9. Chart card lands: LoS per flight hour by arm at 2x density. "Zero on Tower on, in the sim, eight runs."

Every line is one tool call except 7 and 8. Rehearse with the keyword router too, in case the key or the venue Wi-Fi fails: lines 1, 2, 3 and 6 still work.

## 7. Scope

**Ships today:** rungs (a) to (e), the card registry, the four events, the keyword fallback for (a) and (b). About 14 h of work split three ways, so an afternoon with three sessions in parallel. Tests: `tests/test_agent_tools.py` (every tool against a loaded `demo` world, no model), `tests/test_agent_loop.py` (a scripted `MockLLM` walking the loop, caps hit, answer always emitted), `tests/test_router.py`.

**Stretch:** (f) loops with the steps card, (i) timeline on the in-process ring buffer (Elastic optional), a `world.snapshot` for real undo.

**Cut:** (h) codegen, except one case: "make me a scenario with N flights crossing at FL350". `code.scenario(n, level_ft, crossing=True)` builds a `Scenario` with `sim.scenarios.generate`-style helpers, writes `backend/scenarios/agent/<slug>.yaml` through `scenarios.save`, validates it with `scenarios.load` and `plan()` (zero conflicts or it says how many), then `world.load`s it. No model-written Python, no subprocess, no whitelist to get wrong on stage. Why cut the rest: a code sandbox is a day of work whose best outcome is invisible to a judge and whose worst outcome is a traceback on the big screen. Generative UI beyond the registry is cut for the same reason: a fixed registry gives the whole visible effect.

## 8. Open questions for the founder

1. **Does the bar replace the headset PTT channel or sit beside it?** Recommend: the bar is the headset's text twin; Shift+Space still records and lands its transcript in the bar, so a judge can type or talk.
2. **Should phraseology detection be client-side patterns or ask the backend?** Recommend: client-side (`lib/interp.ts`) with the `/radio` and `/ask` prefixes as the override. Mis-routing a "why" question to the radio just gets a notice; mis-routing a clearance to the agent gets "I cannot issue clearances, say it on the radio", which is fine.
3. **Which model?** Recommend: `RESOLVER_MODEL` (Llama 3.3 70B on Baseten) for everything; one model, one warm endpoint. Try a smaller Baseten model only if p50 latency is over 2.5 s on the demo laptop.
4. **Sim jobs while the world runs: thread or subprocess?** Recommend: subprocess. The planner's wall-clock budget is per-call and a threaded 20-run Monte Carlo would starve the tick and the risk rollouts. The eval functions take a `Scenario`, which pickles.
5. **Undo: inverse actions or a world snapshot?** Recommend: inverse actions today (disruptions, sliders, speed, voice, load are all reversible by name); a real `World.snapshot()`/`restore()` is a deep copy of `Simulator`, plan, cards, clearances and monitors, about 3 h and worth it only if rewind and fork (TRD 06 F1 to F3) happen.
6. **Does the agent get `world.nudge`?** Recommend: yes, with the +2 NM temporary buffer and a card as the output. It is the only "act on traffic" verb and it stays inside the planner, so hard rules 8 and 10 hold. Without it rung (f) has nothing to act with.

## 9. Work split

| Brief | Owns | Delivers |
|---|---|---|
| **A. Backend loop and tools** | `backend/agent/{loop,router,cards}.py`, `backend/agent/tools/{world,ui,query,explain}.py`, `world_agent.py` (becomes a caller), `world.py` tool additions (`nudge`, event ring buffer, `undo` bookkeeping), `app.py` (`agent_text` routes to the new loop; `ui_state` passed with it), `schemas.py` (`EventType` gains `agent_step`, `answer`, `ui_command`, `sim_job`), `docs/08-ws-protocol.md`, `tests/test_agent_tools.py`, `test_agent_loop.py`, `test_router.py` | The loop against `MockLLM` and Baseten; every tool answering with a card descriptor; caps enforced; keyword router for (a) and (b) |
| **B. Frontend bar and cards** | `frontend/components/CommandBar.tsx`, `frontend/lib/cards/*` (registry and the six components, SVG chart), `lib/interp.ts` (`looksLikePhraseology`), `lib/ws.ts` whitelist, `lib/types.ts` (`EventMap`, `CardDescriptor`, `ClientMessage` gains `ui_state` on `agent_text`), `lib/store.tsx` (four reducers, camera and panel state, `ui_command` dispatch), `components/MapView.tsx` (camera props from the store), `components/PushToTalk.tsx` (typed box removed; headset transcript lands in the bar), `lib/mock.ts` (scripted `answer` and `agent_step` so the bar works with no backend) | Bar docked bottom-centre in the squack style (black ground, white ink, hairlines); routing; live steps card; all six card kinds rendered from mock |
| **C. Eval and sim tools** | `backend/agent/tools/sim.py`, `backend/agent/jobs.py` (subprocess runner, queue, `sim_job` events, cancel), `eval/montecarlo.py` and `eval/sweep.py` (a `progress` callback and a rows-only return, no PNG in the agent path), the chart-card builder from `SweepRow`s and `run()` output, `tests/test_agent_sim.py` (2 runs, 1 point, under 10 s), plus the one codegen case `code.scenario` and its test if time allows | "run the Monte Carlo" and "sweep density" answering with a chart card while the clock keeps ticking |

Interfaces fixed before anyone starts: the card descriptor in section 5, the four event payloads in section 4, the tool naming `group.verb`, and `agent_text` carrying `ui_state: {selected, planView, speed, voice}`.

**Integrator checks at the end:** full suite green (`pytest -q`, currently 437); `NEXT_DIST_DIR=.next-verify npm run build` clean; the nine demo lines in section 6 run against a live backend with a key and, separately, lines 1, 2, 3, 6 with no key; the clock never drops below the chosen speed while a sim job runs (watch `radar.clock_speed`); every `answer` arrives within 8 s or says why; `agent_reply` still reaches the old headset path; README status row and `docs/10-roadmap.md` get a phase 6h entry with the measured p50 latency of one tool call on the demo laptop.
