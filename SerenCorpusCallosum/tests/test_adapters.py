"""
Tests for seren_corpus_callosum.adapters.

Canned responses use the REAL shapes verified this session: SerenMemory's
SearchHit (from reading routes/search.py) and Loci's search_loci output
(from a live call). If the mapping is right against these, it's right
against the stores.

Run: pytest tests/test_adapters.py   OR   python tests/test_adapters.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from seren_corpus_callosum.adapters import (  # noqa: E402
    SerenLociAdapter,
    SerenMemoryAdapter,
    UnknownStoreType,
    build_adapter,
)
from seren_corpus_callosum.config import StoreConfig  # noqa: E402


class FakeTransport:
    """Returns a canned dict per URL, or raises if the mapped value is an
    Exception. Records calls so tests can assert request shape."""

    def __init__(self, by_url: dict):
        self.by_url = by_url
        self.calls: list[tuple[str, dict]] = []

    async def post_json(self, url: str, payload: dict, headers: dict = None) -> dict:
        self.calls.append((url, payload))
        val = self.by_url[url]
        if isinstance(val, Exception):
            raise val
        return val


# Real SerenMemory /search response shape (already tier-merged + ranked).
MEM_RESP = {
    "query": "cuda",
    "searched_tiers": ["short", "near", "long"],
    "hits": [
        {"tier": "short", "content": "fresh working note", "topic": "t1",
         "score": 0.83, "raw_distance": 0.2, "id": "m1", "metadata": {"source": "user"}},
        {"tier": "long", "content": "durable consolidated fact", "topic": "t2",
         "score": 0.61, "raw_distance": 0.9, "id": "m2", "metadata": {"evidence_count": 4}},
    ],
}

# Real Loci search_loci response shape (from a live call this session).
LOCI_RESP = {
    "query": "cuda", "project": None, "finder": "hybrid",
    "hits": [
        {"id": "l1", "project": "jetson", "key": "cuda-no-vmm",
         "value": "Set GGML_CUDA_NO_VMM=ON at COMPILE time.", "why": "Not honored at runtime.",
         "score": 0.531575, "match_kind": "hybrid", "source": "model", "raw_distance": 0.881202},
        {"id": "l2", "project": "*", "key": "make-it-first",
         "value": "Make it first, then make it good.", "why": "Perfectionism gates shipping.",
         "score": 0.45, "match_kind": "hybrid", "source": "model", "raw_distance": 1.222},
    ],
}


def test_memory_adapter_maps_real_shape():
    t = FakeTransport({"http://mem/search": MEM_RESP})
    cfg = StoreConfig(name="mem", type="seren_memory", url="http://mem")
    adapter = SerenMemoryAdapter(cfg, t)
    hits = asyncio.run(adapter.search("cuda", 5))

    assert [h.id for h in hits] == ["m1", "m2"]          # order preserved == rank preserved
    assert hits[0].content == "fresh working note"
    assert hits[0].store == "mem"
    assert abs(hits[0].base_relevance - (1 / 1.2)) < 1e-6  # 1/(1+raw_distance)
    assert hits[0].native_score == 0.83                   # tier-weighted score carried for display
    assert hits[0].metadata["tier"] == "short"
    assert hits[1].metadata["tier"] == "long"
    # Request shape sanity.
    url, payload = t.calls[0]
    assert url == "http://mem/search"
    assert payload["query"] == "cuda" and payload["n_results"] == 5
    assert payload["include_superseded"] is False


def test_loci_adapter_maps_real_shape():
    t = FakeTransport({"http://loci/search": LOCI_RESP})
    cfg = StoreConfig(name="loci", type="seren_loci", url="http://loci")
    adapter = SerenLociAdapter(cfg, t)
    hits = asyncio.run(adapter.search("cuda", 5))

    assert [h.id for h in hits] == ["l1", "l2"]
    assert hits[0].content == "cuda-no-vmm Set GGML_CUDA_NO_VMM=ON at COMPILE time. Not honored at runtime."   # value is the content
    assert hits[0].base_relevance == 0.531575             # Loci's score used directly (exact=1.0 safe)
    assert hits[0].raw_distance == 0.881202
    assert hits[0].metadata["key"] == "cuda-no-vmm"
    assert hits[0].metadata["why"] == "Not honored at runtime."
    assert hits[0].metadata["match_kind"] == "hybrid"


def test_loci_adapter_passes_project_scope_when_set():
    t = FakeTransport({"http://loci/search": LOCI_RESP})
    cfg = StoreConfig(name="loci", type="seren_loci", url="http://loci",
                      options={"project": "jetson"})
    asyncio.run(SerenLociAdapter(cfg, t).search("cuda", 3))
    _, payload = t.calls[0]
    assert payload["project"] == "jetson"


def test_search_path_override_via_options():
    t = FakeTransport({"http://loci/search_loci": LOCI_RESP})
    cfg = StoreConfig(name="loci", type="seren_loci", url="http://loci",
                      options={"search_path": "/search_loci"})
    asyncio.run(SerenLociAdapter(cfg, t).search("cuda", 3))
    assert t.calls[0][0] == "http://loci/search_loci"   # overridden path used


def test_build_adapter_dispatch_and_unknown():
    t = FakeTransport({})
    mem = build_adapter(StoreConfig(name="m", type="seren_memory", url="http://m"), t)
    assert isinstance(mem, SerenMemoryAdapter)
    loci = build_adapter(StoreConfig(name="l", type="seren_loci", url="http://l"), t)
    assert isinstance(loci, SerenLociAdapter)
    try:
        build_adapter(StoreConfig(name="x", type="quantum_brain", url="http://x"), t)
        assert False, "expected UnknownStoreType"
    except UnknownStoreType:
        pass


def test_empty_or_missing_hits_is_safe():
    t = FakeTransport({"http://mem/search": {"query": "q", "hits": []},
                       "http://mem2/search": {"query": "q"}})  # no hits key at all
    a1 = SerenMemoryAdapter(StoreConfig(name="mem", type="seren_memory", url="http://mem"), t)
    a2 = SerenMemoryAdapter(StoreConfig(name="mem2", type="seren_memory", url="http://mem2"), t)
    assert asyncio.run(a1.search("q", 5)) == []
    assert asyncio.run(a2.search("q", 5)) == []


def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} green.")


if __name__ == "__main__":
    _run_all()
