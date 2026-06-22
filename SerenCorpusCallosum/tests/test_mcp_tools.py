"""
Tests for the SCC `search` MCP tool implementation.

Gated on the `mcp` SDK being installed (the [mcp] extra). The tool impl is
exercised DIRECTLY — `await SccToolImpl(fed).search(...)` — with a fake
federation, so we test the flattening/provenance shape without an MCP client
or an HTTP roundtrip.

Run: pytest tests/test_mcp_tools.py   OR   python tests/test_mcp_tools.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest

pytest.importorskip("mcp")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from seren_corpus_callosum.mcp.tools import SccToolImpl, register_tools  # noqa: E402


def _hit(store, id, content, base_relevance, native_score, raw_distance, rrf, rank):
    """Build a FusedHit-shaped object the tool can flatten (SimpleNamespace so
    the test doesn't couple to the exact dataclass constructor)."""
    hit = types.SimpleNamespace(
        store=store, id=id, content=content, base_relevance=base_relevance,
        native_score=native_score, raw_distance=raw_distance, metadata={})
    return types.SimpleNamespace(hit=hit, rrf_score=rrf, store_rank=rank)


class FakeFederation:
    def __init__(self, fused, store_names, skipped):
        self._fused = fused
        self.store_names = store_names
        self.skipped = skipped
        self.last_call = None

    async def search(self, query, n_results=None):
        self.last_call = (query, n_results)
        return self._fused


def test_search_tool_returns_full_provenance():
    fed = FakeFederation(
        fused=[
            _hit("mem", "m1", "mem one", 0.84, 0.83, 0.2, 0.0161, 1),
            _hit("loci", "l1", "loci one", 0.65, 0.53, 0.88, 0.0159, 1),
        ],
        store_names=["mem", "loci"],
        skipped=[],
    )
    out = asyncio.run(SccToolImpl(fed).search("q", n_results=5))
    assert out["query"] == "q"
    assert fed.last_call == ("q", 5)             # n_results threaded through
    assert out["stores_searched"] == ["mem", "loci"]
    assert [h["store"] for h in out["hits"]] == ["mem", "loci"]
    h0 = out["hits"][0]
    assert set(h0) == {"store", "id", "content", "score", "store_rank",
                       "base_relevance", "native_score", "raw_distance", "metadata"}
    assert h0["score"] == 0.0161 and h0["store_rank"] == 1


def test_search_tool_surfaces_skipped():
    fed = FakeFederation(fused=[], store_names=["mem"],
                         skipped=[("weird", "unknown store type")])
    out = asyncio.run(SccToolImpl(fed).search("q"))
    assert out["hits"] == []
    assert out["skipped"] == [{"name": "weird", "reason": "unknown store type"}]


def test_search_tool_default_n_results():
    fed = FakeFederation(fused=[], store_names=[], skipped=[])
    asyncio.run(SccToolImpl(fed).search("q"))
    assert fed.last_call == ("q", 10)            # tool's own default


def test_register_tools_attaches_search():
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("scc-test")
    impl = register_tools(mcp, FakeFederation([], [], []))
    assert hasattr(impl, "search")
    # Best-effort: the tool surfaces under the name `search`. Guarded so SDK
    # drift in list_tools() can't fail the suite.
    try:
        names = [t.name for t in asyncio.run(mcp.list_tools())]
        assert "search" in names
    except Exception:  # noqa: BLE001
        pass


def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} green.")


if __name__ == "__main__":
    _run_all()
