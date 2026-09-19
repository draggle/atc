"""Searchable memory on Elasticsearch: the resolver's context layer.

Every transmission, clearance, verdict, resolver step and radar frame is indexed as it happens,
so the resolver agent can *search* the frequency instead of scanning Python lists:

- `history(callsign, n, query)`   BM25 over the radio log, filtered to one callsign, ranked by
                                  how well each prior exchange matches the garbled readback.
- `nearby(callsign, radius_nm)`   geo query: who is within N nautical miles right now.
- `track(callsign, seconds)`      time series: what the aircraft's altitude and heading did.
- `closest_waypoint(word)`        fuzzy match a misheard word against the sector's fixes.

Environment: `ELASTIC_URL` and `ELASTIC_API_KEY`. Without them `memory_from_env()` returns a
`NullMemory` whose searches return None, and every caller falls back to the in-memory path, so
the app runs exactly as before. Writes go through a background thread in batches and never block
the clock; reads are bounded by a 2 s timeout and any failure returns None (fallback), so a dead
cluster can slow the resolver but never stall the sim. Tier 1 never touches this module.
"""
from __future__ import annotations

import logging
import math
import os
import queue
import threading
import time
import uuid
from typing import Any

log = logging.getLogger("tower.memory")

INDEX_PREFIX = "tower"
INDICES: dict[str, dict[str, Any]] = {
    "transmissions": {"properties": {
        "session": {"type": "keyword"}, "id": {"type": "keyword"}, "t": {"type": "float"},
        "speaker": {"type": "keyword"}, "callsign": {"type": "keyword"},
        "text_raw": {"type": "text"}, "text_norm": {"type": "text"},
        "n_best": {"type": "text"}, "asr_confidence": {"type": "float"},
        "items": {"type": "object", "enabled": False}, "clearance_id": {"type": "keyword"}}},
    "clearances": {"properties": {
        "session": {"type": "keyword"}, "id": {"type": "keyword"}, "callsign": {"type": "keyword"},
        "status": {"type": "keyword"}, "issued_at": {"type": "float"}, "t": {"type": "float"},
        "phrase": {"type": "text"}, "items": {"type": "object", "enabled": False}}},
    "verdicts": {"properties": {
        "session": {"type": "keyword"}, "clearance_id": {"type": "keyword"},
        "callsign": {"type": "keyword"}, "result": {"type": "keyword"},
        "error_type": {"type": "keyword"}, "decided_by": {"type": "keyword"},
        "confidence": {"type": "float"}, "reason": {"type": "text"}, "t": {"type": "float"}}},
    "resolver_steps": {"properties": {
        "session": {"type": "keyword"}, "clearance_id": {"type": "keyword"},
        "step": {"type": "integer"}, "tool": {"type": "keyword"},
        "result_summary": {"type": "text"}, "t": {"type": "float"}}},
    "radar": {"properties": {
        "session": {"type": "keyword"}, "callsign": {"type": "keyword"}, "t": {"type": "float"},
        "x_nm": {"type": "float"}, "y_nm": {"type": "float"}, "pos": {"type": "geo_point"},
        "alt_ft": {"type": "float"}, "target_alt_ft": {"type": "float"},
        "hdg_deg": {"type": "float"}, "gs_kt": {"type": "float"},
        "is_intruder": {"type": "boolean"}}},
    "waypoints": {"properties": {
        "session": {"type": "keyword"}, "name": {"type": "text",
                                                 "fields": {"keyword": {"type": "keyword"}}},
        "x_nm": {"type": "float"}, "y_nm": {"type": "float"}, "pos": {"type": "geo_point"}}},
}

READ_TIMEOUT_S = 2.0
FLUSH_EVERY_S = 0.5
FLUSH_AT_DOCS = 200
RADAR_EVERY_REAL_S = 1.0


def index_name(kind: str) -> str:
    return f"{INDEX_PREFIX}-{kind}"


# ------------------------------------------------------------------------------ base and null


class Memory:
    """Interface. Searches return None when the memory cannot answer, and callers fall back."""

    enabled = False
    label = "in-memory"

    def new_session(self, name: str) -> str:
        return name

    def observe(self, ev: dict[str, Any]) -> None:
        return None

    def index_waypoints(self, waypoints: list[dict[str, Any]]) -> None:
        return None

    def history(self, callsign: str, n: int = 3, query: str | None = None) -> list[dict[str, Any]] | None:
        return None

    def nearby(self, callsign: str, radius_nm: float = 30.0) -> list[dict[str, Any]] | None:
        return None

    def track(self, callsign: str, seconds: float = 30.0) -> dict[str, Any] | None:
        return None

    def closest_waypoint(self, word: str) -> dict[str, Any] | None:
        return None

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "label": self.label}


class NullMemory(Memory):
    """No Elasticsearch configured. Every search says "cannot answer"."""


# ------------------------------------------------------------------------------ elasticsearch


class ElasticMemory(Memory):
    """Memory on an Elasticsearch cluster. `client` is an `elasticsearch.Elasticsearch` or any
    object with the same `indices.create`, `bulk`/`helpers.bulk` and `search` surface (tests
    inject a fake). `sync=True` writes on the calling thread, for tests and scripts; call `flush()` before searching."""

    enabled = True
    label = "Elasticsearch"

    def __init__(self, client: Any, *, sync: bool = False, session: str | None = None,
                 bulk_fn: Any = None) -> None:
        self.client = client
        self.sync = sync
        self._bulk_fn = bulk_fn or self._helpers_bulk
        self.session = session or f"session-{uuid.uuid4().hex[:8]}"
        self.docs_indexed = 0
        self.errors = 0
        self.last_error: str | None = None
        self._last_radar_real = 0.0
        self._q: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self.ensure_indices()
        if not sync:
            self._thread = threading.Thread(target=self._writer, name="tower-memory", daemon=True)
            self._thread.start()

    # -- setup -------------------------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> ElasticMemory | None:
        url, key = os.environ.get("ELASTIC_URL", "").strip(), os.environ.get("ELASTIC_API_KEY", "").strip()
        if not url or not key:
            return None
        try:
            from elasticsearch import Elasticsearch
        except ImportError:  # pragma: no cover - dependency listed in pyproject
            log.warning("ELASTIC_URL set but the elasticsearch package is not installed")
            return None
        client = Elasticsearch(url, api_key=key, request_timeout=READ_TIMEOUT_S, max_retries=1)
        try:
            client.info()
        except Exception as e:  # noqa: BLE001 - a dead cluster must not stop the app
            log.warning("Elasticsearch unreachable at %s: %s; running without memory", url, e)
            return None
        mem = cls(client)
        log.info("Elasticsearch memory on %s", url)
        return mem

    def ensure_indices(self) -> None:
        for kind, mapping in INDICES.items():
            try:
                self.client.options(ignore_status=400).indices.create(index=index_name(kind), mappings=mapping)
            except Exception as e:  # noqa: BLE001
                self._fail(f"create {kind}: {e}")

    def new_session(self, name: str) -> str:
        self.session = f"{name}-{uuid.uuid4().hex[:6]}"
        self._last_radar_real = 0.0
        return self.session

    # -- writes ------------------------------------------------------------------------------------

    def observe(self, ev: dict[str, Any]) -> None:
        typ, p, t = ev.get("type"), ev.get("payload") or {}, float(ev.get("t") or 0.0)
        if typ == "transcript":
            self._put("transmissions", {
                "id": p.get("id"), "t": p.get("t_end", t), "speaker": p.get("speaker"),
                "callsign": p.get("callsign"), "text_raw": p.get("text_raw"),
                "text_norm": p.get("text_norm"), "n_best": p.get("n_best") or [],
                "asr_confidence": p.get("asr_confidence"), "items": p.get("items")}, doc_id=p.get("id"))
        elif typ in ("clearance_opened", "clearance_updated"):
            self._put("clearances", {
                "id": p.get("id"), "callsign": p.get("callsign"), "status": p.get("status"),
                "issued_at": p.get("issued_at"), "t": t, "items": p.get("items"),
                "phrase": " ".join(f"{i.get('action') or i.get('type')} {i.get('value')}"
                                   for i in (p.get("items") or []))}, doc_id=p.get("id"))
        elif typ == "alert":
            self._put("verdicts", {
                "clearance_id": p.get("clearance_id"), "callsign": p.get("callsign"),
                "result": p.get("result"), "error_type": p.get("error_type"),
                "decided_by": p.get("decided_by"), "confidence": p.get("confidence"),
                "reason": p.get("reason"), "t": t})
        elif typ == "resolver_step":
            self._put("resolver_steps", {
                "clearance_id": p.get("clearance_id"), "step": p.get("step"), "tool": p.get("tool"),
                "result_summary": p.get("result_summary"), "t": t})
        elif typ == "radar":
            now = time.monotonic()
            if now - self._last_radar_real < RADAR_EVERY_REAL_S and not self.sync:
                return
            self._last_radar_real = now
            rt = float(p.get("t", t))
            for a in p.get("aircraft") or []:
                doc = {"callsign": a.get("callsign"), "t": rt, "x_nm": a.get("x_nm"), "y_nm": a.get("y_nm"),
                       "alt_ft": a.get("alt_ft"), "target_alt_ft": a.get("target_alt_ft"),
                       "hdg_deg": a.get("hdg_deg"), "gs_kt": a.get("gs_kt"),
                       "is_intruder": bool(a.get("is_intruder"))}
                if a.get("lat") is not None and a.get("lon") is not None:
                    doc["pos"] = {"lat": a["lat"], "lon": a["lon"]}
                self._put("radar", doc)

    def index_waypoints(self, waypoints: list[dict[str, Any]]) -> None:
        """Written on the calling thread and refreshed, so closest_waypoint works right after load."""
        actions = []
        for w in waypoints:
            doc = {"name": w.get("name"), "x_nm": w.get("x_nm"), "y_nm": w.get("y_nm")}
            if w.get("lat") is not None and w.get("lon") is not None:
                doc["pos"] = {"lat": w["lat"], "lon": w["lon"]}
            actions.append(self._action("waypoints", doc, doc_id=str(w.get("name"))))
        self._bulk(actions)
        self.refresh("waypoints")

    def _action(self, kind: str, doc: dict[str, Any], doc_id: str | None = None) -> dict[str, Any]:
        doc = {k: v for k, v in doc.items() if v is not None}
        doc["session"] = self.session
        action: dict[str, Any] = {"_index": index_name(kind), "_source": doc}
        if doc_id:
            action["_id"] = f"{self.session}:{doc_id}"
        return action

    def _put(self, kind: str, doc: dict[str, Any], doc_id: str | None = None) -> None:
        action = self._action(kind, doc, doc_id)
        if self.sync:
            self._bulk([action])
        else:
            self._q.put((kind, action))

    def _helpers_bulk(self, actions: list[dict[str, Any]]) -> int:
        from elasticsearch import helpers
        ok, _ = helpers.bulk(self.client, actions, raise_on_error=False, request_timeout=10)
        return int(ok)

    def _bulk(self, actions: list[dict[str, Any]]) -> None:
        if not actions:
            return
        try:
            self.docs_indexed += int(self._bulk_fn(actions))
        except Exception as e:  # noqa: BLE001
            self._fail(f"bulk: {e}")

    def refresh(self, *kinds: str) -> None:
        """Make everything written so far searchable now. New documents otherwise become visible
        within the cluster's refresh interval, a few seconds on Serverless, which the background
        stream accepts. Scripts, tests and the waypoint index at load call this."""
        index = ",".join(index_name(k) for k in kinds) if kinds else f"{INDEX_PREFIX}-*"
        try:
            self.client.options(ignore_status=404, request_timeout=10).indices.refresh(index=index)
        except Exception as e:  # noqa: BLE001
            self._fail(f"refresh: {e}")

    def _writer(self) -> None:
        batch: list[dict[str, Any]] = []
        last = time.monotonic()
        while True:
            try:
                item = self._q.get(timeout=FLUSH_EVERY_S)
            except queue.Empty:
                item = ()  # type: ignore[assignment]
            if item is None:
                self._bulk(batch)
                return
            if item:
                batch.append(item[1])
            if batch and (len(batch) >= FLUSH_AT_DOCS or time.monotonic() - last >= FLUSH_EVERY_S):
                self._bulk(batch)
                batch, last = [], time.monotonic()

    def flush(self) -> None:
        """Wait for queued writes to reach the cluster, then refresh so they are searchable."""
        if not self.sync:
            deadline = time.monotonic() + 3.0
            while not self._q.empty() and time.monotonic() < deadline:
                time.sleep(0.05)
            time.sleep(FLUSH_EVERY_S + 0.1)
        self.refresh()

    def close(self) -> None:
        if self._thread is not None:
            self._q.put(None)
            self._thread.join(timeout=5.0)
            self._thread = None

    # -- reads -------------------------------------------------------------------------------------

    def _search(self, kind: str, body: dict[str, Any]) -> list[dict[str, Any]] | None:
        try:
            resp = self.client.options(request_timeout=READ_TIMEOUT_S).search(index=index_name(kind), **body)
        except Exception as e:  # noqa: BLE001
            self._fail(f"search {kind}: {e}")
            return None
        hits = (resp.get("hits") or {}).get("hits") or [] if isinstance(resp, dict) else resp["hits"]["hits"]
        return [{**h.get("_source", {}), "_score": h.get("_score")} for h in hits]

    def _scoped(self, *clauses: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"term": {"session": self.session}}, *clauses]

    def history(self, callsign: str, n: int = 3, query: str | None = None) -> list[dict[str, Any]] | None:
        """The last n exchanges with a callsign. With `query`, BM25 ranks them by how well the
        garbled text matches each, so the exchange the pilot was probably answering comes first."""
        must = self._scoped({"term": {"callsign": callsign}})
        q: dict[str, Any] = {"bool": {"filter": must}}
        sort: list[Any] = [{"t": "desc"}]
        if query:
            q["bool"]["should"] = [{"multi_match": {"query": query, "fields": ["text_norm^2", "n_best"],
                                                    "fuzziness": "AUTO", "operator": "or"}}]
            sort = ["_score", {"t": "desc"}]
        hits = self._search("transmissions", {"query": q, "sort": sort, "size": max(1, n)})
        if hits is None:
            return None
        return [{"speaker": h.get("speaker"), "text": h.get("text_norm"), "items": h.get("items") or [],
                 "clearance_id": h.get("clearance_id"), "t": h.get("t"),
                 "score": round(float(h["_score"]), 2) if h.get("_score") is not None else None}
                for h in hits]

    def latest_state(self, callsign: str) -> dict[str, Any] | None:
        hits = self._search("radar", {"query": {"bool": {"filter": self._scoped({"term": {"callsign": callsign}})}},
                                      "sort": [{"t": "desc"}], "size": 1})
        return hits[0] if hits else None

    def nearby(self, callsign: str, radius_nm: float = 30.0) -> list[dict[str, Any]] | None:
        """Every other aircraft within radius_nm of the callsign's latest radar position, by geo
        query when positions carry lat/lon, else by flat distance on the sector plane."""
        me = self.latest_state(callsign)
        if me is None:
            return None
        t0 = float(me.get("t", 0.0))
        filt = self._scoped({"range": {"t": {"gte": t0 - 15.0}}})
        if me.get("pos"):
            filt.append({"geo_distance": {"distance": f"{radius_nm}nmi", "pos": me["pos"]}})
        else:
            filt.append({"range": {"x_nm": {"gte": me["x_nm"] - radius_nm, "lte": me["x_nm"] + radius_nm}}})
            filt.append({"range": {"y_nm": {"gte": me["y_nm"] - radius_nm, "lte": me["y_nm"] + radius_nm}}})
        hits = self._search("radar", {"query": {"bool": {"filter": filt}}, "sort": [{"t": "desc"}],
                                      "collapse": {"field": "callsign"}, "size": 50})
        if hits is None:
            return None
        out = []
        for h in hits:
            if h.get("callsign") == callsign:
                continue
            d = math.hypot(float(h.get("x_nm", 0)) - float(me.get("x_nm", 0)),
                           float(h.get("y_nm", 0)) - float(me.get("y_nm", 0)))
            if d <= radius_nm:
                out.append({"callsign": h.get("callsign"), "distance_nm": round(d, 1),
                            "alt_ft": h.get("alt_ft"), "hdg_deg": h.get("hdg_deg"),
                            "is_intruder": bool(h.get("is_intruder"))})
        return sorted(out, key=lambda a: a["distance_nm"])

    def track(self, callsign: str, seconds: float = 30.0) -> dict[str, Any] | None:
        """What the aircraft did over the last `seconds` of radar: altitude and heading trend."""
        me = self.latest_state(callsign)
        if me is None:
            return None
        t0 = float(me.get("t", 0.0))
        hits = self._search("radar", {"query": {"bool": {"filter": self._scoped(
            {"term": {"callsign": callsign}}, {"range": {"t": {"gte": t0 - seconds, "lte": t0}}})}},
            "sort": [{"t": "asc"}], "size": 500})
        if not hits:
            return None
        alts = [float(h.get("alt_ft", 0)) for h in hits]
        hdgs = [float(h.get("hdg_deg", 0)) for h in hits]
        delta = alts[-1] - alts[0]
        trend = "level" if abs(delta) < 100 else ("descending" if delta < 0 else "climbing")
        return {"callsign": callsign, "samples": len(hits), "seconds": round(hits[-1]["t"] - hits[0]["t"], 1),
                "alt_start_ft": round(alts[0]), "alt_end_ft": round(alts[-1]),
                "alt_min_ft": round(min(alts)), "alt_max_ft": round(max(alts)), "trend": trend,
                "hdg_start_deg": round(hdgs[0]), "hdg_end_deg": round(hdgs[-1]),
                "target_alt_ft": hits[-1].get("target_alt_ft")}

    def closest_waypoint(self, word: str) -> dict[str, Any] | None:
        """Fuzzy-match a misheard word against the sector's fix names."""
        word = (word or "").strip()
        if not word:
            return None
        hits = self._search("waypoints", {"query": {"bool": {
            "filter": self._scoped(),
            "should": [{"match": {"name": {"query": word, "fuzziness": "AUTO", "prefix_length": 1}}},
                       {"prefix": {"name.keyword": word.upper()[:2]}}],
            "minimum_should_match": 1}}, "size": 3})
        if not hits:
            return None if hits is None else {"query": word, "name": None, "candidates": []}
        return {"query": word, "name": hits[0].get("name"), "score": hits[0].get("_score"),
                "candidates": [h.get("name") for h in hits]}

    # -- misc --------------------------------------------------------------------------------------

    def _fail(self, msg: str) -> None:
        self.errors += 1
        self.last_error = msg
        if self.errors <= 5 or self.errors % 100 == 0:
            log.warning("memory: %s", msg)

    def status(self) -> dict[str, Any]:
        return {"enabled": True, "label": self.label, "session": self.session,
                "docs_indexed": self.docs_indexed, "errors": self.errors, "last_error": self.last_error}


def memory_from_env() -> Memory:
    return ElasticMemory.from_env() or NullMemory()
