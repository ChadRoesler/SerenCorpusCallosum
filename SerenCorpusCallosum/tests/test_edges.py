"""
Tests for the topic-association EDGE addendum (Phase 2 of edges).

After the vector fan, federation appends a small MARKED set of entries that
share a topic TAG with the packet (exact-tag /by_topic, not similarity) - the
scar the wording buried. These check: edges land AFTER the packet, marked
source='topic-edge'; the join is threaded the packet's topics + ids (exclude);
edge_budget caps them; Loci (no /by_topic) is skipped; and every miss path
degrades to "no edges", never a crash.

Run: pytest tests/test_edges.py   OR   python tests/test_edges.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from seren_corpus_callosum.config import FederationConfig, StoreConfig  # noqa: E402
from seren_corpus_callosum.federation import Federation  # noqa: E402


# mem /search hits CARRY topics so the packet has a center to radiate from.
MEM_RESP = {"hits": [
    {"tier": "short", "content": "mem one", "topic": "cft, iam",
     "score": 0.83, "raw_distance": 0.2, "id": "m1", "metadata": {}},
    {"tier": "long", "content": "mem two", "topic": "cft, lambda",
     "score": 0.61, "raw_distance": 0.9, "id": "m2", "metadata": {}},
]}
LOCI_RESP = {"finder": "vector", "hits": [
    {"id": "l1", "project": "jetson", "key": "k1", "value": "loci one", "why": "w",
     "score": 0.53, "match_kind": "vector", "source": "model", "raw_distance": 0.88},
]}
# /by_topic shape (verified against SerenMemory routes/search.py).
BY_TOPIC_RESP = {"topics": ["cft"], "searched_tiers": ["short", "near", "long"], "hits": [
    {"tier": "long", "content": "the buried scar", "topic": "cft, scar",
     "matched_topics": ["cft"], "overlap": 1, "id": "e1", "metadata": {"source": "consolidator"}},
]}


class RecordingTransport:
    """Canned per-URL responses; records calls so we can assert the join's
    request shape. Uncanned URL -> RuntimeError (but the edge path catches it)."""

    def __init__(self, by_url):
        self.by_url = by_url
        self.calls = []  # (url, payload)

    async def post_json(self, url, payload, headers=None):
        self.calls.append((url, payload))
        v = self.by_url.get(url)
        if v is None:
            raise RuntimeError(f"no canned response for {url}")
        if isinstance(v, Exception):
            raise v
        return v

    def payload_for(self, url):
        return next((p for (u, p) in self.calls if u == url), None)

    def urls(self):
        return [u for (u, _) in self.calls]


def _cfg(**ov):
    return FederationConfig(
        stores=[
            StoreConfig(name="mem", type="seren_memory", url="http://mem"),
            StoreConfig(name="loci", type="seren_loci", url="http://loci"),
        ],
        k=60,
        edges_enabled=ov.get("edges_enabled", True),
        edge_budget=ov.get("edge_budget", 3),
    )


def test_edges_appended_and_marked():
    t = RecordingTransport({
        "http://mem/search": MEM_RESP,
        "http://loci/search": LOCI_RESP,
        "http://mem/by_topic": BY_TOPIC_RESP,
    })
    fused = asyncio.run(Federation(_cfg(), t).search("q", n_results=10))
    ids = [f"{f.hit.store}:{f.hit.id}" for f in fused]
    # the three vector hits come first (their interleave order is RRF's job,
    # covered in test_federation); the edge is the bounded addendum at the TAIL
    assert set(ids[:3]) == {"mem:m1", "loci:l1", "mem:m2"}, ids
    assert ids[-1] == "mem:e1", ids
    edge = fused[-1]
    assert edge.hit.metadata["source"] == "topic-edge"
    assert edge.hit.metadata["matched_topics"] == ["cft"]
    assert edge.hit.metadata["overlap"] == 1
    assert edge.rrf_score == 0.0
    assert edge.hit.native_score == 1.0   # overlap surfaced as the strength signal
    assert edge.hit.base_relevance == 0.0  # no cosine for a tag match


def test_topics_and_exclude_threaded():
    t = RecordingTransport({
        "http://mem/search": MEM_RESP,
        "http://loci/search": LOCI_RESP,
        "http://mem/by_topic": BY_TOPIC_RESP,
    })
    asyncio.run(Federation(_cfg(), t).search("q", n_results=10))
    p = t.payload_for("http://mem/by_topic")
    assert p is not None
    # center topics from the packet (deduped; loci carries none). Order is
    # irrelevant for an any-of tag match, so assert as a set.
    assert set(p["topics"]) == {"cft", "iam", "lambda"}, p["topics"]
    # excludes the packet ids so edges are genuinely NEW
    assert set(p["exclude_ids"]) == {"m1", "l1", "m2"}


def test_edge_budget_caps():
    many = {"hits": [
        {"tier": "long", "content": f"edge {i}", "topic": "cft",
         "matched_topics": ["cft"], "overlap": 1, "id": f"e{i}", "metadata": {}}
        for i in range(6)]}
    t = RecordingTransport({
        "http://mem/search": MEM_RESP,
        "http://loci/search": LOCI_RESP,
        "http://mem/by_topic": many,
    })
    fused = asyncio.run(Federation(_cfg(edge_budget=2), t).search("q", n_results=10))
    edges = [f for f in fused if f.hit.metadata.get("source") == "topic-edge"]
    assert len(edges) == 2
    assert t.payload_for("http://mem/by_topic")["n_results"] == 2   # bounded request


def test_edges_disabled_no_join():
    t = RecordingTransport({
        "http://mem/search": MEM_RESP,
        "http://loci/search": LOCI_RESP,
        "http://mem/by_topic": BY_TOPIC_RESP,
    })
    fused = asyncio.run(Federation(_cfg(edges_enabled=False), t).search("q", n_results=10))
    assert "http://mem/by_topic" not in t.urls()
    assert all(f.hit.metadata.get("source") != "topic-edge" for f in fused)


def test_loci_skipped_no_topic_capability():
    t = RecordingTransport({
        "http://mem/search": MEM_RESP,
        "http://loci/search": LOCI_RESP,
        "http://mem/by_topic": BY_TOPIC_RESP,
    })
    asyncio.run(Federation(_cfg(), t).search("q", n_results=10))
    assert "http://mem/by_topic" in t.urls()        # mem speaks /by_topic
    assert "http://loci/by_topic" not in t.urls()   # loci has no search_by_topic


def test_no_topics_no_join():
    no_topic = {"hits": [
        {"tier": "short", "content": "x", "score": 0.8, "raw_distance": 0.2,
         "id": "m1", "metadata": {}}]}
    t = RecordingTransport({
        "http://mem/search": no_topic,
        "http://loci/search": LOCI_RESP,
        "http://mem/by_topic": BY_TOPIC_RESP,
    })
    fused = asyncio.run(Federation(_cfg(), t).search("q", n_results=10))
    assert "http://mem/by_topic" not in t.urls()    # no center -> no join
    assert all(f.hit.metadata.get("source") != "topic-edge" for f in fused)


def test_edge_join_failure_degrades():
    t = RecordingTransport({
        "http://mem/search": MEM_RESP,
        "http://loci/search": LOCI_RESP,
        "http://mem/by_topic": ConnectionError("by_topic down"),
    })
    fused = asyncio.run(Federation(_cfg(), t).search("q", n_results=10))
    assert all(f.hit.metadata.get("source") != "topic-edge" for f in fused)
    assert len(fused) == 3   # m1, l1, m2 intact, no crash


def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} green.")


if __name__ == "__main__":
    _run_all()
