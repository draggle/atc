# 11. Elastic memory: the resolver's searchable context layer

Written Saturday Sept 19, 2026, for the Elastic "Find the Signal" sponsor prize. Branch `kavir/elastic-memory`. This page is the handoff: what was built, why, how to turn it on, and what to check when merging.

## The one-paragraph version

Tower's resolver agent already investigates messy readbacks with tools: re-listen, who is on frequency, what was this aircraft told, what is it doing on radar. Before this change those tools read Python lists inside the process. Now every transmission, clearance, verdict, resolver step and radar frame is streamed into Elasticsearch as it happens, and the tools *search* it: BM25 text search over the radio log, a geo query for nearby aircraft, a time-series query for what the plane did, and a fuzzy match for misheard fix names. The agent's trace on screen names the source, for example `[Elasticsearch] descending over 20 s: 25000 to 24200 ft`. Without `ELASTIC_URL` nothing changes: the app runs exactly as before on the in-memory path.

## What the prize asks for, and where each thing is

| Elastic asked for | Where it is |
|---|---|
| Messy real-world data in | Whisper transcripts of noisy radio, radar frames, verdicts. `World._emit` pushes every WebSocket event into `Memory.observe` on its way out |
| An agent that decides what to retrieve and calls tools | The resolver in `backend/tower/resolver/`, a hand-rolled tool loop capped at 4 calls. It chooses among `frequency_history`, `nearby_aircraft`, `aircraft_track`, `sanity_check`, `relisten`, `active_aircraft`, `aircraft_state` |
| Elasticsearch as the context layer | `backend/tower/memory.py`. Five indices, `tower-transmissions`, `tower-clearances`, `tower-verdicts`, `tower-resolver-steps`, `tower-radar`, plus `tower-waypoints` |
| BM25 | `history(callsign, n, query)`: `multi_match` on `text_norm` and `n_best` with `fuzziness: AUTO`, so the exchange the garbled readback best matches ranks first |
| Geo query | `nearby(callsign, radius_nm)`: `geo_distance` on a `geo_point` around the aircraft's latest position, `collapse` by callsign for the newest frame each |
| Time series | `track(callsign, seconds)`: range on `t`, sorted, reduced to altitude and heading trend |
| Fuzzy matching | `closest_waypoint(word)`: fuzzy `match` plus prefix on fix names. Stock Whisper hears "ESTIR" as "estor" or "at better"; this finds ESTIR |
| Closing the loop with an action | The resolver ends every run in exactly one of alert, dismiss, uncertain or watch. With Elastic on, the keyless mock policy also reads the radar track and alerts or dismisses from it (`tower/llm.py`, section 2a) |

Not done, and honest about it: no dense vectors or reranking (the radio log is short phraseology where BM25 plus fuzziness does the job; vectors would add a model call to the hot path). No Elastic Agent Builder or Workflows: the agent loop is ours and runs on Baseten, per hard rule 5. No Kibana dashboard yet, though every index is there for one.

## Turn it on

1. Elastic Cloud, Serverless Elasticsearch project. Copy the Elasticsearch endpoint (not the Kibana one) and create an API key.
2. In `.env` at the repo root:
   ```
   ELASTIC_URL=https://<project>.es.<region>.gcp.elastic.cloud:443
   ELASTIC_API_KEY=<key>
   ```
3. `cd backend && uv pip install -e ".[dev]"` (adds the `elasticsearch` client), then prove it:
   ```
   .venv/bin/python tools/elastic_check.py
   ```
   It writes a tiny scripted session and runs all four searches. Every line should say `OK`.
4. Start the backend. The log says `Elasticsearch memory on https://...`, `/health` shows `memory.docs_indexed` climbing, and the `state` event carries `"memory": "Elasticsearch"`.
5. To see it in the trace: drag the pilot error rate up, wait for an amber "Checking" card, expand the agent trace. Steps that searched are prefixed `[Elasticsearch]`.

If the cluster is unreachable at start, the app logs one warning and runs without memory. If it dies mid-session, each search fails within 2 s, returns None, and the tool falls back to the in-memory answer, so the resolver slows but the sim never stalls.

## Files changed

| File | Change |
|---|---|
| `backend/tower/memory.py` | New. `Memory` interface, `NullMemory`, `ElasticMemory`, `memory_from_env()`. Writes batch on a background thread; reads have a 2 s timeout |
| `backend/tower/resolver/tools.py` | Two new tools, `nearby_aircraft` and `aircraft_track`, a `source` label, and `summarize()` prefixes search steps with it |
| `backend/tower/resolver/agent.py` | Passes the source into the step summary |
| `backend/tower/pipeline.py` | `TowerCore(memory=...)`. Tool backends ask memory first and fall back to local state. Keeps the last 120 radar frames per aircraft for the local `aircraft_track`. `sanity_check` adds the closest fix for a garbled route. Puts `memory` into the resolver's context |
| `backend/tower/llm.py` | `MockLLM` policy: with a memory, calls `aircraft_track` and `nearby_aircraft`, and decides from the radar trend when it settles the altitude |
| `backend/world.py` | `World(memory=...)`. All events pass through `memory.observe` before the WebSocket. New session per load, waypoints indexed with lat/lon, `memory` in the `state` event |
| `backend/app.py` | `/health` reports memory status |
| `backend/tools/elastic_check.py` | New. Live check against the real cluster |
| `backend/tests/test_memory.py` | 13 tests on a fake Elasticsearch that evaluates the exact query shapes used: filters, geo distance, fuzzy match, collapse, sort. Run offline |
| `backend/pyproject.toml` | `elasticsearch>=8.15` |
| `.env.example`, `README.md`, `CLAUDE.md` | The two variables, this doc in the map |

Every existing test still passes: 286 in `backend/`.

## Merging notes

- **No behaviour change without the env vars.** `NullMemory` answers None to everything and every caller falls back to the code that was there before. Tier 1 never touches memory.
- **Schema additions only.** `state` gains an optional `memory` string. `ResolverStep.result_summary` may now start with `[Elasticsearch]`. The frontend ignores unknown fields; `frontend/lib/types.ts` does not need to change, though a small badge in the TopBar reading `state.memory` would be a nice two-line addition.
- **Two new tool names in `TOOL_SCHEMAS`.** A real model on Baseten (`RESOLVER_MODEL`) will see them and may call them; both work with or without Elastic.
- **The radar index grows.** One doc per aircraft per real second while running. An hour of the 159-flight Europe scenario is about 570,000 small docs, well inside a serverless project. Each `load` starts a new `session` id so searches never see a previous world. Delete old sessions from Kibana if it ever matters: `DELETE tower-radar/_query { "query": { "term": { "session": "<id>" } } }`.
- **Verified live** Saturday evening against the Serverless project from a laptop: all four searches returned the right answers. One thing learned: new documents become searchable only after the cluster's refresh, a few seconds on Serverless. Background batches accept that lag (the resolver reads data that is seconds old anyway). Synchronous writes and the waypoint index at load use `refresh="wait_for"` so a script, a test, or the first resolver run after load sees what was just written. If `tools/elastic_check.py` ever prints `BAD`, look at the query in `memory.py` and the mapping in Kibana Dev Tools with `GET tower-*/_mapping`.

## What would make it stronger, in order

1. A `frontend` badge and a per-step icon for `[Elasticsearch]` steps in `AlertCard.tsx`, so the judge sees the search without reading text.
2. A Kibana dashboard on `tower-verdicts` and `tower-resolver-steps`: readback errors by type and airline, resolver decisions, seconds to alert. This is the "insights" idea from `docs/00-full-context.md` Part 11 with no new code.
3. Dense vectors on `text_norm` via an Elastic inference endpoint, and a hybrid `rrf` query in `history()`. Only if a paraphrased readback ("down to two four zero") measurably beats BM25 with fuzziness on the held-out synthetic pairs.
4. Resolver verdicts read back out of `tower-verdicts` as checker training rows (TRD 02 mentions this loop).
