"""Searchable memory (tower/memory.py) against a small fake Elasticsearch, so it runs offline.

The fake answers the query shapes the module sends: bool filter with term / range / geo_distance,
should with multi_match / match (fuzzy) / prefix, sort, size, collapse. Real-cluster checks are in
tools/elastic_check.py and need ELASTIC_URL and ELASTIC_API_KEY.
"""
from __future__ import annotations

import asyncio
import math
from typing import Any

import pytest

from schemas import AircraftState, OpenClearance, Transmission, event
from tower.check import check
from tower.llm import MockLLM
from tower.memory import ElasticMemory, NullMemory, index_name, memory_from_env
from tower.normalize import normalize
from tower.parse import parse
from tower.pipeline import TowerCore
from tower.resolver.tools import SEARCH_TOOLS, TOOL_SCHEMAS, summarize
from world import World

# ------------------------------------------------------------------------------ fake cluster


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _haversine_nm(a: dict[str, float], b: dict[str, float]) -> float:
    r = 3440.065
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


class FakeES:
    """Enough of the Elasticsearch client for tower/memory.py."""

    def __init__(self) -> None:
        self.store: dict[str, dict[str, dict[str, Any]]] = {}
        self.created: list[str] = []
        self.searches: list[tuple[str, dict[str, Any]]] = []
        self.indices = self

    def options(self, **_: Any) -> FakeES:
        return self

    def create(self, index: str, mappings: dict[str, Any] | None = None) -> None:  # indices.create
        self.created.append(index)
        self.store.setdefault(index, {})

    def info(self) -> dict[str, Any]:
        return {"version": {"number": "fake"}}

    def refresh(self, index: str) -> None:  # indices.refresh
        self.refreshes = getattr(self, "refreshes", 0) + 1

    def bulk_actions(self, actions: list[dict[str, Any]]) -> int:
        for a in actions:
            idx = self.store.setdefault(a["_index"], {})
            doc_id = a.get("_id") or f"auto-{len(idx)}"
            idx[doc_id] = dict(a["_source"])
        return len(actions)

    def docs(self, kind: str) -> list[dict[str, Any]]:
        return list(self.store.get(index_name(kind), {}).values())

    # -- query evaluation ----------------------------------------------------------------------

    def _filter_ok(self, clause: dict[str, Any], d: dict[str, Any]) -> bool:
        if "term" in clause:
            (k, v), = clause["term"].items()
            return d.get(k) == v
        if "range" in clause:
            (k, spec), = clause["range"].items()
            if k not in d:
                return False
            x = float(d[k])
            return all((op != "gte" or x >= float(lim)) and (op != "lte" or x <= float(lim))
                       for op, lim in spec.items())
        if "geo_distance" in clause:
            spec = clause["geo_distance"]
            if "pos" not in d:
                return False
            radius = float(spec["distance"].replace("nmi", ""))
            return _haversine_nm(d["pos"], spec["pos"]) <= radius
        raise NotImplementedError(clause)

    def _score(self, clause: dict[str, Any], d: dict[str, Any]) -> float:
        if "multi_match" in clause:
            spec = clause["multi_match"]
            q = set(str(spec["query"]).lower().split())
            score = 0.0
            for f in spec["fields"]:
                name, _, boost = f.partition("^")
                val = d.get(name)
                text = " ".join(val) if isinstance(val, list) else str(val or "")
                overlap = len(q & set(text.lower().split()))
                score += overlap * float(boost or 1)
            return score
        if "match" in clause:
            (k, spec), = clause["match"].items()
            q = str(spec["query"]).lower()
            target = str(d.get(k) or "").lower()
            dist = _edit_distance(q, target)
            return max(0.0, 3.0 - dist) if dist <= 2 else 0.0
        if "prefix" in clause:
            (k, v), = clause["prefix"].items()
            base = k.replace(".keyword", "")
            return 0.5 if str(d.get(base) or "").upper().startswith(str(v).upper()) else 0.0
        raise NotImplementedError(clause)

    def search(self, index: str, **body: Any) -> dict[str, Any]:
        self.searches.append((index, body))
        q = body.get("query", {}).get("bool", {})
        hits = []
        for d in self.store.get(index, {}).values():
            if not all(self._filter_ok(c, d) for c in q.get("filter", [])):
                continue
            shoulds = q.get("should", [])
            score = sum(self._score(c, d) for c in shoulds)
            if shoulds and q.get("minimum_should_match") and score <= 0:
                continue
            hits.append((score, d))
        for key in reversed(body.get("sort", [])):
            if key == "_score":
                hits.sort(key=lambda h: h[0], reverse=True)
            else:
                (k, order), = key.items()
                hits.sort(key=lambda h: float(h[1].get(k, 0)), reverse=(order == "desc"))
        if body.get("collapse"):
            f, seen, out = body["collapse"]["field"], set(), []
            for h in hits:
                if h[1].get(f) not in seen:
                    seen.add(h[1].get(f))
                    out.append(h)
            hits = out
        hits = hits[: int(body.get("size", 10))]
        return {"hits": {"hits": [{"_source": d, "_score": s} for s, d in hits]}}


@pytest.fixture
def fake() -> FakeES:
    return FakeES()


@pytest.fixture
def mem(fake: FakeES) -> ElasticMemory:
    return ElasticMemory(fake, sync=True, session="test", bulk_fn=fake.bulk_actions)


def radar(t: float, *aircraft: tuple[str, float, float, float, float], target: float | None = None) -> dict[str, Any]:
    """(callsign, x, y, alt, hdg) at a common time, with lat/lon at 1 NM per 1/60 degree."""
    return event("radar", {"t": t, "aircraft": [
        {"callsign": cs, "x_nm": x, "y_nm": y, "alt_ft": alt, "target_alt_ft": target if target is not None else alt,
         "hdg_deg": hdg,
         "gs_kt": 420, "lat": 43.0 + y / 60.0, "lon": -79.0 + x / (60.0 * math.cos(math.radians(43.0)))}
        for cs, x, y, alt, hdg in aircraft]}, t=t)


# ------------------------------------------------------------------------------ null memory


def test_null_memory_answers_nothing_and_is_the_default_without_env(monkeypatch):
    monkeypatch.delenv("ELASTIC_URL", raising=False)
    monkeypatch.delenv("ELASTIC_API_KEY", raising=False)
    m = memory_from_env()
    assert isinstance(m, NullMemory) and not m.enabled
    m.observe(event("transcript", {"id": "t1"}))
    assert m.history("ACA123") is None and m.nearby("ACA123") is None
    assert m.track("ACA123") is None and m.closest_waypoint("estir") is None


def test_core_without_memory_uses_local_radar_for_the_new_tools():
    core = TowerCore(llm=MockLLM(), waypoints={"ESTIR": (0, 0)})
    states = [AircraftState(callsign="ACA123", x_nm=0, y_nm=0, alt_ft=25000, target_alt_ft=24000, hdg_deg=90, gs_kt=420, t=0),
              AircraftState(callsign="ACA133", x_nm=10, y_nm=0, alt_ft=30000, target_alt_ft=30000, hdg_deg=90, gs_kt=420, t=0),
              AircraftState(callsign="DAL456", x_nm=80, y_nm=0, alt_ft=30000, target_alt_ft=30000, hdg_deg=90, gs_kt=420, t=0)]
    core.tick(0.0, states)
    core.tick(10.0, [s.model_copy(update={"t": 10.0, "alt_ft": s.alt_ft - (500 if s.callsign == "ACA123" else 0)})
                     for s in states])
    near = core.resolver.tools.execute("nearby_aircraft", {"callsign": "ACA123", "radius_nm": 30})
    assert [a["callsign"] for a in near] == ["ACA133"] and near[0]["distance_nm"] == 10.0
    track = core.resolver.tools.execute("aircraft_track", {"callsign": "ACA123", "seconds": 30})
    assert track["trend"] == "descending" and track["samples"] == 2
    assert core.resolver.tools.source == "in-memory"
    assert "[" not in summarize("aircraft_track", track, core.resolver.tools.source)


def test_tool_schemas_include_the_search_tools():
    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert {"nearby_aircraft", "aircraft_track"} <= names
    assert SEARCH_TOOLS <= names


# ------------------------------------------------------------------------------ indexing


def test_observe_indexes_every_event_kind(mem: ElasticMemory, fake: FakeES):
    assert set(fake.created) >= {index_name(k) for k in ("transmissions", "clearances", "verdicts", "radar", "waypoints")}
    mem.observe(event("transcript", {"id": "t1", "t_end": 5.0, "speaker": "controller", "callsign": "ACA123",
                                     "text_raw": "air canada one two three descend flight level two four zero",
                                     "text_norm": "ACA123 descend flight level 240", "asr_confidence": 0.9}, t=5.0))
    mem.observe(event("clearance_opened", {"id": "c1", "callsign": "ACA123", "status": "open", "issued_at": 5.0,
                                           "items": [{"type": "altitude", "value": 240, "unit": "FL", "action": "descend"}]}, t=5.0))
    mem.observe(event("clearance_updated", {"id": "c1", "callsign": "ACA123", "status": "matched", "issued_at": 5.0,
                                            "items": [{"type": "altitude", "value": 240, "unit": "FL", "action": "descend"}]}, t=9.0))
    mem.observe(event("alert", {"clearance_id": "c1", "callsign": "ACA123", "result": "mismatch",
                                "error_type": "wrong_value", "decided_by": "rules", "confidence": 0.9,
                                "reason": "expected 240 heard 210"}, t=9.0))
    mem.observe(event("resolver_step", {"clearance_id": "c1", "step": 1, "tool": "relisten",
                                        "result_summary": "2 hypotheses"}, t=9.0))
    mem.observe(radar(0.0, ("ACA123", 0, 0, 25000, 90)))
    assert [d["text_norm"] for d in fake.docs("transmissions")] == ["ACA123 descend flight level 240"]
    clearances = fake.docs("clearances")
    assert len(clearances) == 1 and clearances[0]["status"] == "matched"  # same id: updated in place
    assert "descend 240" in clearances[0]["phrase"]
    assert fake.docs("verdicts")[0]["result"] == "mismatch"
    assert fake.docs("resolver_steps")[0]["tool"] == "relisten"
    r = fake.docs("radar")[0]
    assert r["callsign"] == "ACA123" and r["pos"]["lat"] == pytest.approx(43.0) and r["session"] == "test"
    assert mem.docs_indexed == 6 and mem.status()["errors"] == 0


def test_new_session_scopes_searches(mem: ElasticMemory, fake: FakeES):
    mem.observe(radar(0.0, ("ACA123", 0, 0, 25000, 90)))
    mem.new_session("demo")
    assert mem.session.startswith("demo-")
    assert mem.track("ACA123") is None  # the old session's frames are invisible
    mem.observe(radar(1.0, ("ACA123", 0, 0, 25000, 90)))
    assert mem.track("ACA123")["samples"] == 1


# ------------------------------------------------------------------------------ searches


def test_history_ranks_the_exchange_that_matches_the_garbled_readback(mem: ElasticMemory):
    for i, (t, text) in enumerate([(1.0, "ACA123 climb flight level 350"),
                                   (2.0, "ACA123 turn left heading 270"),
                                   (3.0, "ACA123 descend flight level 240")]):
        mem.observe(event("transcript", {"id": f"t{i}", "t_end": t, "speaker": "controller", "callsign": "ACA123",
                                         "text_raw": text, "text_norm": text}, t=t))
    plain = mem.history("ACA123", n=2)
    assert [h["text"] for h in plain] == ["ACA123 descend flight level 240", "ACA123 turn left heading 270"]
    ranked = mem.history("ACA123", n=3, query="left heading 2 7")
    assert ranked[0]["text"] == "ACA123 turn left heading 270" and ranked[0]["score"] > 0
    assert mem.history("DAL456") == []


def test_nearby_uses_the_latest_position_of_every_aircraft(mem: ElasticMemory):
    mem.observe(radar(0.0, ("ACA123", 0, 0, 25000, 90), ("ACA133", 5, 0, 30000, 90), ("DAL456", 60, 0, 30000, 90)))
    mem.observe(radar(5.0, ("ACA123", 0, 0, 25000, 90), ("ACA133", 25, 0, 30000, 90), ("DAL456", 60, 0, 30000, 90)))
    near = mem.nearby("ACA123", radius_nm=30)
    assert [a["callsign"] for a in near] == ["ACA133"]
    assert near[0]["distance_nm"] == pytest.approx(25.0, abs=0.2)  # the later frame, not the 5 NM one
    assert mem.nearby("NOPE1") is None


def test_track_reports_the_altitude_trend(mem: ElasticMemory):
    for t, alt in [(0.0, 25000), (10.0, 24600), (20.0, 24200)]:
        mem.observe(radar(t, ("ACA123", t, 0, alt, 90)))
    tr = mem.track("ACA123", seconds=30)
    assert tr["trend"] == "descending" and tr["alt_start_ft"] == 25000 and tr["alt_end_ft"] == 24200
    assert tr["samples"] == 3 and tr["seconds"] == 20.0
    assert mem.track("ACA123", seconds=5)["samples"] == 1


def test_closest_waypoint_fuzzy_matches_a_misheard_fix(mem: ElasticMemory, fake: FakeES):
    mem.index_waypoints([{"name": "ESTIR", "x_nm": 0, "y_nm": 0}, {"name": "PIKAR", "x_nm": 10, "y_nm": 0},
                         {"name": "BOSOX", "x_nm": 20, "y_nm": 0}])
    assert mem.closest_waypoint("estor")["name"] == "ESTIR"
    assert fake.refreshes >= 1  # waypoints are searchable as soon as index_waypoints returns
    assert mem.closest_waypoint("pikar")["name"] == "PIKAR"
    assert mem.closest_waypoint("")is None
    miss = mem.closest_waypoint("zzzzzzz")
    assert miss["name"] is None and miss["candidates"] == []


def test_search_failure_falls_back_instead_of_raising(mem: ElasticMemory, fake: FakeES):
    def boom(index: str, **body: Any) -> dict[str, Any]:
        raise ConnectionError("cluster gone")
    fake.search = boom  # type: ignore[method-assign]
    assert mem.history("ACA123") is None and mem.nearby("ACA123") is None
    assert mem.status()["errors"] == 2 and "cluster gone" in mem.status()["last_error"]


# ------------------------------------------------------------------------------ in the resolver


def test_resolver_trace_names_elasticsearch_and_searches_radar(mem: ElasticMemory):
    active = ["ACA123", "ACA133"]
    core = TowerCore(llm=MockLLM(), memory=mem)
    assert core.resolver.tools.source == "Elasticsearch"
    states = [AircraftState(callsign="ACA123", x_nm=0, y_nm=0, alt_ft=25000, target_alt_ft=21000, hdg_deg=90, gs_kt=420, t=0)]
    for t, alt in [(0.0, 25000), (10.0, 24400), (20.0, 23800)]:
        mem.observe(radar(t, ("ACA123", 0, 0, alt, 90), target=21000))
    core.tick(20.0, [states[0].model_copy(update={"t": 20.0, "alt_ft": 23800})])

    c_ext = parse(normalize("air canada one two three descend flight level two four zero"), active, "controller")
    c = OpenClearance(id="c1", callsign=c_ext.callsign, items=c_ext.items, issued_at=0.0)
    n_best = ["descend flight level 210 ACA123", "descend flight level 240 ACA123"]
    pilot = "descend flight level two one zero air canada one two three"
    tx = Transmission(id="t2", t_start=5, t_end=7, audio_ref="", text_raw=pilot, text_norm=normalize(pilot),
                      asr_confidence=0.7, speaker="pilot", n_best=[normalize(h) for h in n_best])
    ext = parse(tx.text_norm, active, "pilot", "t2")
    v = check(c, ext, tx, active=active)
    assert v.result == "ambiguous"

    res = core.resolver.resolve(c, v, tx, extra_context={"memory": mem.label})
    tools = [s.tool for s in res.steps]
    assert "aircraft_track" in tools
    track_step = next(s for s in res.steps if s.tool == "aircraft_track")
    assert track_step.result_summary.startswith("[Elasticsearch] descending")
    # the aircraft is aiming for 21,000 ft, the heard value, so radar settles it: alert
    assert res.steps[-1].tool == "raise_alert" and "21000" in res.verdict.reason


def test_garbled_fix_searches_waypoints_before_watching(mem: ElasticMemory):
    active = ["ACA123", "ACA133"]
    mem.index_waypoints([{"name": "ESTIR", "x_nm": 0, "y_nm": 0}, {"name": "PIKAR", "x_nm": 10, "y_nm": 0}])
    core = TowerCore(llm=MockLLM(), memory=mem, waypoints={"ESTIR": (0, 0), "PIKAR": (10, 0)})
    c_ext = parse(normalize("air canada one two three proceed direct estir"), active, "controller")
    c = OpenClearance(id="c1", callsign=c_ext.callsign, items=c_ext.items, issued_at=0.0)
    # Stock Whisper turns "ESTIR" into ordinary words; "estr" is one edit from a real fix name.
    pilot = normalize("proceeding direct at estr air canada one two three")
    tx = Transmission(id="t2", t_start=5, t_end=7, audio_ref="", text_raw=pilot, text_norm=pilot,
                      asr_confidence=0.9, speaker="pilot")
    v = check(c, parse(pilot, active, "pilot", "t2"), tx, active=active)
    assert v.result == "ambiguous" and "fix name was not understood" in v.reason, v.reason
    res = core.resolver.resolve(c, v, tx, extra_context={"memory": mem.label})
    # BM25 over the radio log for the missing item, then a fuzzy fix search, then radar
    assert [s.tool for s in res.steps] == ["frequency_history", "sanity_check", "watch"]
    assert all(s.result_summary.startswith("[Elasticsearch]") for s in res.steps[:2])
    assert res.steps[1].args == {"type": "route", "value": "at estr"}
    assert "at estr" in res.verdict.reason and res.watch_request is not None


def test_sanity_check_offers_the_closest_fix(mem: ElasticMemory):
    mem.index_waypoints([{"name": "ESTIR", "x_nm": 0, "y_nm": 0}])
    core = TowerCore(llm=MockLLM(), memory=mem, waypoints={"ESTIR": (0, 0)})
    out = core.resolver.tools.execute("sanity_check", {"type": "route", "value": "ESTOR"})
    assert not out["plausible"] and out["closest_waypoint"] == "ESTIR"
    assert "[Elasticsearch]" in summarize("sanity_check", out, "Elasticsearch")


# ------------------------------------------------------------------------------ in the world


def test_world_streams_events_into_memory(fake: FakeES):
    mem = ElasticMemory(fake, sync=True, bulk_fn=fake.bulk_actions)
    events: list[dict[str, Any]] = []
    w = World(events.append, synthesize=False, realtime=False, memory=mem)
    w.load("demo")
    assert mem.session.startswith("demo-")
    state = next(e for e in events if e["type"] == "state")["payload"]
    assert state["memory"] == "Elasticsearch"
    assert {d["name"] for d in fake.docs("waypoints")} == {wp.name for wp in w.scenario.waypoints if wp.kind != "hidden"}
    assert all("pos" in d for d in fake.docs("waypoints"))
    assert w.core.memory is mem and w.core.resolver.tools.source == "Elasticsearch"

    w.start()
    w.fleet.set_error_rate(0.0)
    cs = w.sim.aircraft()[0].callsign
    asyncio.run(w.controller_text(f"{cs} descend and maintain flight level two four zero"))
    asyncio.run(w.tick(1.0)); asyncio.run(w.tick(1.0))
    tx = fake.docs("transmissions")
    assert any(d["speaker"] == "controller" and d.get("callsign") == cs for d in tx)
    assert any(d["speaker"] == "pilot" for d in tx)
    assert any(d["callsign"] == cs and d["status"] == "matched" for d in fake.docs("clearances"))
    frames = fake.docs("radar")
    assert frames and all(d["session"] == mem.session and "pos" in d for d in frames)
    assert mem.nearby(cs, radius_nm=500) is not None
    assert w.core.resolver.tools.execute("aircraft_track", {"callsign": cs, "seconds": 60})["samples"] >= 1
