"""
The fan tells the truth about who answered, asks a store for no more than it
accepts, keeps its health record across a rebuild, and hangs a core's
surroundings off the packet.

- cap: the over-fetch (n * fetch_multiplier) is clamped to what the store's
  contract accepts (SerenMemory: 50), on /search and on /by_topic, so the
  README's own numbers no longer 422 Memory out of every search
- honest: stores_searched is the stores that ANSWERED; a timeout, a refused
  connection or an HTTP error puts the store in `failed` with why - on the
  HTTP route and the MCP tool alike; an empty answer is still an answer
- tracker: POST /configure and POST/DELETE /stores rebuild the fan WITH the
  shared health tracker, so /health/stores does not reset; a new
  per_store_timeout_s reaches the transport
- satellite edges: a core hit's inline surroundings become MARKED edges after
  the packet (newest first, round-robin across cores, budgeted, deduped), the
  counts stay on the core, the content is lifted off it; the topic-edge join
  excludes them; off = stripped, no edges
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

from seren_corpus_callosum.app import create_app  # noqa: E402
from seren_corpus_callosum.config import (  # noqa: E402
    CorpusCallosumConfig, FederationConfig, ServerConfig, StoreConfig,
)
from seren_corpus_callosum.federation import Federation  # noqa: E402
from seren_corpus_callosum.mcp.tools import SccToolImpl  # noqa: E402


MEM_RESP = {"hits": [
    {"tier": "long", "content": "the core", "topic": "cft", "score": 0.83, "raw_distance": 0.2, "id": "c1",
     "metadata": {"kind": "core", "evidence_count": 3},
     "surroundings": {"satellites": 3, "latest_satellite_at": 300.0, "supersedes": "old1", "superseded_by": None,
                      "recent": [{"id": "s3", "content": "third time", "created_at": 300.0},
                                 {"id": "s2", "content": "second time", "created_at": 200.0},
                                 {"id": "s1", "content": "first time", "created_at": 100.0}],
                      "supersedes_entry": {"id": "old1", "content": "what I used to think", "created_at": 50.0}}},
    {"tier": "long", "content": "another core", "topic": "cft", "score": 0.6, "raw_distance": 0.5, "id": "c2",
     "metadata": {"kind": "core"},
     "surroundings": {"satellites": 1, "latest_satellite_at": 400.0, "supersedes": None, "superseded_by": None,
                      "recent": [{"id": "t1", "content": "its one episode", "created_at": 400.0}]}},
    {"tier": "short", "content": "a fragment", "topic": "cft", "score": 0.5, "raw_distance": 0.7, "id": "f1",
     "metadata": {}},
]}
LOCI_RESP = {"finder": "hybrid", "hits": [
    {"id": "l1", "project": "*", "key": "k1", "value": "loci one", "why": "w",
     "score": 0.53, "match_kind": "hybrid", "source": "model", "raw_distance": 0.88},
]}
BY_TOPIC_RESP = {"topics": ["cft"], "searched_tiers": ["short", "near", "long"], "hits": [
    {"tier": "long", "content": "the buried scar", "topic": "cft, scar",
     "matched_topics": ["cft"], "overlap": 1, "id": "e1", "metadata": {}},
]}


class RecordingTransport:
    def __init__(self, by_url):
        self.by_url = by_url
        self.calls = []
        self.timeouts = []

    async def post_json(self, url, payload, headers=None):
        self.calls.append((url, payload))
        v = self.by_url.get(url)
        if v is None:
            raise RuntimeError(f"no canned response for {url}")
        if isinstance(v, Exception):
            raise v
        if isinstance(v, dict) and "__sleep__" in v:
            await asyncio.sleep(v["__sleep__"])
            return {"hits": []}
        return v

    def set_timeout(self, t):
        self.timeouts.append(t)

    def payload_for(self, url):
        return next((p for (u, p) in self.calls if u == url), None)


def _fed_cfg(**ov) -> FederationConfig:
    return FederationConfig(
        stores=[StoreConfig(name="mem", type="seren_memory", url="http://mem"),
                StoreConfig(name="loci", type="seren_loci", url="http://loci")],
        k=60, n_results=ov.get("n_results", 10), fetch_multiplier=ov.get("fetch_multiplier", 2),
        per_store_timeout_s=ov.get("timeout", 5.0),
        edges_enabled=ov.get("edges_enabled", False), edge_budget=3,
        satellite_edges_enabled=ov.get("satellite_edges_enabled", True),
        satellite_budget=ov.get("satellite_budget", 3),
    )


def _app_cfg(fed: FederationConfig) -> CorpusCallosumConfig:
    return CorpusCallosumConfig(server=ServerConfig(bearer_token=""), federation=fed)


# ── the cap ──────────────────────────────────────────────────────────────────

def test_memory_is_asked_for_no_more_than_it_accepts():
    t = RecordingTransport({"http://mem/search": MEM_RESP, "http://loci/search": LOCI_RESP,
                            "http://mem/by_topic": BY_TOPIC_RESP})
    fed = Federation(_fed_cfg(n_results=25, fetch_multiplier=3, edges_enabled=True), t)
    asyncio.run(fed.search("q"))
    assert t.payload_for("http://mem/search")["n_results"] == 50, "25 x 3 = 75 would 422 Memory (le=50)"
    assert t.payload_for("http://loci/search")["n_results"] == 75, "Loci states no cap"
    assert t.payload_for("http://mem/by_topic")["n_results"] <= 50


# ── honest ───────────────────────────────────────────────────────────────────

def test_a_store_that_did_not_answer_is_named_with_why():
    t = RecordingTransport({"http://mem/search": MEM_RESP,
                            "http://loci/search": ConnectionError("refused")})
    fed = Federation(_fed_cfg(), t)
    rep = asyncio.run(fed.search_report("q"))
    assert rep.answered == ["mem"]
    assert rep.failed == [("loci", "ConnectionError: refused")]
    assert [f.hit.id for f in rep.hits][:3] == ["c1", "c2", "f1"], "the packet goes on without it"


def test_a_timeout_is_a_failure_and_an_empty_answer_is_not():
    t = RecordingTransport({"http://mem/search": {"__sleep__": 0.5},
                            "http://loci/search": {"finder": "hybrid", "hits": []}})
    fed = Federation(_fed_cfg(timeout=0.05), t)
    rep = asyncio.run(fed.search_report("q"))
    assert rep.answered == ["loci"], "nothing to say is still an answer"
    assert len(rep.failed) == 1 and rep.failed[0][0] == "mem" and rep.failed[0][1].startswith("timeout")


def test_the_route_and_the_tool_carry_failed():
    t = RecordingTransport({"http://mem/search": MEM_RESP,
                            "http://loci/search": RuntimeError("HTTP 401 Unauthorized")})
    app = create_app(_app_cfg(_fed_cfg()), transport=t)
    with TestClient(app) as tc:
        r = tc.post("/search", json={"query": "q"}).json()
        assert r["stores_searched"] == ["mem"]
        assert r["failed"] == [{"name": "loci", "reason": "RuntimeError: HTTP 401 Unauthorized"}]
        assert r["skipped"] == []
        out = asyncio.run(SccToolImpl(app.state.federation).search("q"))
        assert out["stores_searched"] == ["mem"] and out["failed"][0]["name"] == "loci"


# ── the tracker survives a rebuild ───────────────────────────────────────────

def test_configure_and_store_changes_keep_the_health_record():
    t = RecordingTransport({"http://mem/search": MEM_RESP,
                            "http://loci/search": ConnectionError("down")})
    app = create_app(_app_cfg(_fed_cfg()), transport=t)
    with TestClient(app) as tc:
        tc.post("/search", json={"query": "q"})
        before = {s["name"]: s for s in tc.get("/health/stores").json()["stores"]}
        assert before["loci"]["failed_calls"] == 1 and before["mem"]["successful_calls"] == 1

        assert tc.post("/configure", json={"k": 30}).json()["ok"]
        tc.post("/search", json={"query": "q"})
        after = {s["name"]: s for s in tc.get("/health/stores").json()["stores"]}
        assert after["loci"]["failed_calls"] == 2, "a /configure rebuild used to reset the record"
        assert after["mem"]["successful_calls"] == 2

        assert tc.post("/stores", json={"name": "extra", "type": "seren_memory", "url": "http://extra"}).json()["ok"]
        tc.post("/search", json={"query": "q"})
        after2 = {s["name"]: s for s in tc.get("/health/stores").json()["stores"]}
        assert after2["loci"]["failed_calls"] == 3 and after2["extra"]["failed_calls"] == 1
        assert tc.delete("/stores/extra").json()["ok"]
        assert {s["name"]: s for s in tc.get("/health/stores").json()["stores"]}["loci"]["failed_calls"] == 3


def test_a_new_timeout_reaches_the_transport():
    t = RecordingTransport({"http://mem/search": MEM_RESP, "http://loci/search": LOCI_RESP})
    app = create_app(_app_cfg(_fed_cfg()), transport=t)
    with TestClient(app) as tc:
        assert tc.post("/configure", json={"per_store_timeout_s": 12.5}).json()["changed"]["per_store_timeout_s"] == 12.5
        assert t.timeouts == [12.5]
        assert app.state.federation._config.per_store_timeout_s == 12.5


def test_http_transport_set_timeout_follows_on_the_live_client():
    import httpx
    from seren_corpus_callosum.transport import HttpTransport

    async def run():
        async with HttpTransport(timeout=5.0) as tx:
            tx.set_timeout(9.0)
            assert tx._client.timeout == httpx.Timeout(9.0)
    asyncio.run(run())


# ── satellite edges ──────────────────────────────────────────────────────────

def test_a_core_brings_its_surroundings_as_marked_edges():
    t = RecordingTransport({"http://mem/search": MEM_RESP, "http://loci/search": LOCI_RESP})
    fed = Federation(_fed_cfg(satellite_budget=3), t)
    fused = asyncio.run(fed.search("q", n_results=10))
    assert t.payload_for("http://mem/search")["with_surroundings"] is True
    ranked = [f for f in fused if f.rrf_score > 0]
    edges = [f for f in fused if f.rrf_score == 0.0]
    assert [f.hit.id for f in ranked] == ["c1", "l1", "c2", "f1"]
    # round-robin across cores in packet order, newest first: c1's s3, c2's t1, then c1's s2
    assert [f.hit.id for f in edges] == ["s3", "t1", "s2"]
    assert fused.index(edges[0]) == len(ranked), "edges ride AFTER the ranked packet"
    s3 = edges[0].hit
    assert s3.metadata["source"] == "satellite-edge" and s3.metadata["edge_of"] == "c1"
    assert s3.metadata["core_id"] == "c1" and s3.metadata["kind"] == "satellite" and s3.metadata["topic"] == "cft"
    assert s3.content == "third time" and s3.base_relevance == 0.0 and s3.store == "mem"
    core = ranked[0].hit.metadata["surroundings"]
    assert core == {"satellites": 3, "latest_satellite_at": 300.0, "supersedes": "old1", "superseded_by": None}, \
        "the counts stay on the core; the content was lifted into the edges"


def test_the_superseded_core_rides_as_an_edge_after_the_satellites():
    t = RecordingTransport({"http://mem/search": MEM_RESP, "http://loci/search": LOCI_RESP})
    fed = Federation(_fed_cfg(satellite_budget=10), t)
    fused = asyncio.run(fed.search("q", n_results=10))
    edges = [f.hit for f in fused if f.rrf_score == 0.0]
    assert [e.id for e in edges] == ["s3", "t1", "s2", "s1", "old1"]
    old = edges[-1]
    assert old.metadata["source"] == "supersedes-edge" and old.metadata["superseded_by"] == "c1"
    assert old.metadata["kind"] == "core" and old.content == "what I used to think"


def test_satellite_edges_off_strips_the_content_and_adds_nothing():
    t = RecordingTransport({"http://mem/search": MEM_RESP, "http://loci/search": LOCI_RESP})
    fed = Federation(_fed_cfg(satellite_edges_enabled=False), t)
    fused = asyncio.run(fed.search("q", n_results=10))
    assert all(f.rrf_score > 0 for f in fused)
    sur = fused[0].hit.metadata["surroundings"]
    assert "recent" not in sur and "supersedes_entry" not in sur and sur["satellites"] == 3


def test_topic_edges_never_repeat_a_satellite_and_come_after_them():
    by_topic = {"topics": ["cft"], "searched_tiers": ["long"], "hits": [
        {"tier": "long", "content": "third time", "topic": "cft", "matched_topics": ["cft"], "overlap": 1,
         "id": "s3", "metadata": {}},
        {"tier": "long", "content": "the buried scar", "topic": "cft, scar", "matched_topics": ["cft"],
         "overlap": 1, "id": "e1", "metadata": {}},
    ]}
    t = RecordingTransport({"http://mem/search": MEM_RESP, "http://loci/search": LOCI_RESP,
                            "http://mem/by_topic": by_topic})
    fed = Federation(_fed_cfg(edges_enabled=True, satellite_budget=2), t)
    fused = asyncio.run(fed.search("q", n_results=10))
    assert "s3" in t.payload_for("http://mem/by_topic")["exclude_ids"], "the join is told what the packet already holds"
    tail = [(f.hit.id, f.hit.metadata.get("source")) for f in fused if f.rrf_score == 0.0]
    assert tail[:2] == [("s3", "satellite-edge"), ("t1", "satellite-edge")]
    assert ("e1", "topic-edge") in tail and tail.index(("e1", "topic-edge")) > 1


def test_satellite_knobs_are_configurable_and_advertised():
    t = RecordingTransport({"http://mem/search": MEM_RESP, "http://loci/search": LOCI_RESP})
    app = create_app(_app_cfg(_fed_cfg()), transport=t)
    with TestClient(app) as tc:
        st = tc.get("/stores").json()
        assert st["satellite_edges_enabled"] is True and st["satellite_budget"] == 3
        assert "satellite_budget" in st["supported_knobs"]
        r = tc.post("/configure", json={"satellite_budget": 1}).json()
        assert r["changed"] == {"satellite_budget": 1}
        hits = tc.post("/search", json={"query": "q"}).json()["hits"]
        assert [h["id"] for h in hits if h["score"] == 0.0] == ["s3"]
        tc.post("/configure", json={"satellite_edges_enabled": False})
        hits = tc.post("/search", json={"query": "q"}).json()["hits"]
        assert all(h["score"] > 0 for h in hits)
        assert hits[0]["metadata"]["surroundings"]["satellites"] == 3


def test_a_memory_without_surroundings_is_unchanged():
    old = {"hits": [{"tier": "long", "content": "x", "score": 0.8, "raw_distance": 0.2, "id": "m1", "metadata": {}}]}
    t = RecordingTransport({"http://mem/search": old, "http://loci/search": LOCI_RESP})
    fed = Federation(_fed_cfg(), t)
    fused = asyncio.run(fed.search("q"))
    assert [f.hit.id for f in fused] == ["m1", "l1"] and "surroundings" not in fused[0].hit.metadata
