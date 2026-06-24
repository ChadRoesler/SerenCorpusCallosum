"""
Tests for seren_corpus_callosum.federation and config.

Exercises the whole stack with a fake transport: config -> adapters ->
parallel fan -> floor -> RRF merge, plus every graceful-degradation path
(a store that raises, one that times out, one with an unknown type).

Run: pytest tests/test_federation.py   OR   python tests/test_federation.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from seren_corpus_callosum.config import FederationConfig, StoreConfig  # noqa: E402
from seren_corpus_callosum.federation import Federation  # noqa: E402


MEM_RESP = {"hits": [
    {"tier": "short", "content": "mem one", "score": 0.83, "raw_distance": 0.2, "id": "m1", "metadata": {}},
    {"tier": "long", "content": "mem two", "score": 0.61, "raw_distance": 0.9, "id": "m2", "metadata": {}},
]}
LOCI_RESP = {"finder": "vector", "hits": [
    {"id": "l1", "project": "jetson", "key": "k1", "value": "loci one", "why": "w",
     "score": 0.53, "match_kind": "vector", "source": "model", "raw_distance": 0.88},
    {"id": "l2", "project": "*", "key": "k2", "value": "loci two", "why": "w",
     "score": 0.45, "match_kind": "vector", "source": "model", "raw_distance": 1.22},
]}


class FakeTransport:
    """by_url -> dict (canned response) | Exception (raise) | {'__sleep__': s}."""

    def __init__(self, by_url: dict):
        self.by_url = by_url

    async def post_json(self, url: str, payload: dict, headers: dict = None) -> dict:
        v = self.by_url.get(url)
        if v is None:
            raise RuntimeError(f"no canned response for {url}")
        if isinstance(v, Exception):
            raise v
        if isinstance(v, dict) and "__sleep__" in v:
            await asyncio.sleep(v["__sleep__"])
            return {"hits": []}
        return v


def _ids(fused) -> list[str]:
    return [f"{f.hit.store}:{f.hit.id}" for f in fused]


def _two_store_config(**overrides) -> FederationConfig:
    return FederationConfig(
        stores=[
            StoreConfig(name="mem", type="seren_memory", url="http://mem",
                        floor=overrides.get("mem_floor", 0.0)),
            StoreConfig(name="loci", type="seren_loci", url="http://loci",
                        floor=overrides.get("loci_floor", 0.0)),
        ],
        k=60,
        per_store_timeout_s=overrides.get("timeout", 5.0),
    )


def test_fans_and_interleaves_two_stores():
    t = FakeTransport({"http://mem/search": MEM_RESP, "http://loci/search": LOCI_RESP})
    fed = Federation(_two_store_config(), t)
    fused = asyncio.run(fed.search("q", n_results=10))
    # Equal weights, mem declared first -> mem wins rank ties.
    assert _ids(fused) == ["mem:m1", "loci:l1", "mem:m2", "loci:l2"]


def test_dead_store_degrades_gracefully():
    # CorpusCallosum raises; the fan returns mem's hits, no crash.
    t = FakeTransport({"http://mem/search": MEM_RESP,
                       "http://loci/search": ConnectionError("loci down")})
    fed = Federation(_two_store_config(), t)
    fused = asyncio.run(fed.search("q"))
    assert _ids(fused) == ["mem:m1", "mem:m2"]


def test_slow_store_times_out_and_degrades():
    # CorpusCallosum sleeps past the timeout; mem still answers.
    t = FakeTransport({"http://mem/search": MEM_RESP,
                       "http://loci/search": {"__sleep__": 0.5}})
    fed = Federation(_two_store_config(timeout=0.05), t)
    fused = asyncio.run(fed.search("q"))
    assert _ids(fused) == ["mem:m1", "mem:m2"]


def test_per_store_floor_drops_then_fan_proceeds():
    # CorpusCallosum floored at 0.9 -> both loci hits (0.53, 0.45) dropped -> mem only.
    t = FakeTransport({"http://mem/search": MEM_RESP, "http://loci/search": LOCI_RESP})
    fed = Federation(_two_store_config(loci_floor=0.9), t)
    fused = asyncio.run(fed.search("q"))
    assert _ids(fused) == ["mem:m1", "mem:m2"]


def test_unknown_store_type_is_skipped_not_fatal():
    cfg = FederationConfig(stores=[
        StoreConfig(name="mem", type="seren_memory", url="http://mem"),
        StoreConfig(name="weird", type="quantum_brain", url="http://weird"),
    ])
    t = FakeTransport({"http://mem/search": MEM_RESP})
    fed = Federation(cfg, t)
    assert fed.store_names == ["mem"]           # weird never bound
    assert len(fed.skipped) == 1 and fed.skipped[0][0] == "weird"
    fused = asyncio.run(fed.search("q"))
    assert _ids(fused) == ["mem:m1", "mem:m2"]   # fan still works


def test_no_stores_returns_empty():
    fed = Federation(FederationConfig(stores=[]), FakeTransport({}))
    assert asyncio.run(fed.search("q")) == []


def test_config_from_dict_is_lenient_and_dedups():
    cfg = FederationConfig.from_dict({
        "k": 30,
        "stores": [
            {"name": "a", "type": "seren_memory", "url": "http://a/"},  # trailing slash trimmed
            {"name": "a", "type": "seren_memory", "url": "http://dup"},  # dup name -> dropped
            {"name": "b", "type": "seren_loci", "url": "http://b", "weight": 2.0},
            {"type": "seren_memory"},  # malformed (no name/url) -> skipped, not fatal
        ],
    })
    assert cfg.k == 30
    assert [s.name for s in cfg.stores] == ["a", "b"]      # dup + malformed gone
    assert cfg.stores[0].url == "http://a"                 # trailing slash trimmed
    assert cfg.stores[1].weight == 2.0


def test_weight_lets_one_store_outrank_another():
    # Give loci 3x trust; its rank-1 should jump ahead of mem's rank-1.
    cfg = FederationConfig(stores=[
        StoreConfig(name="mem", type="seren_memory", url="http://mem", weight=1.0),
        StoreConfig(name="loci", type="seren_loci", url="http://loci", weight=3.0),
    ], k=60)
    t = FakeTransport({"http://mem/search": MEM_RESP, "http://loci/search": LOCI_RESP})
    fed = Federation(cfg, t)
    fused = asyncio.run(fed.search("q"))
    assert _ids(fused)[0] == "loci:l1"   # weight, not magnitude, moved it to the top


def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} green.")


if __name__ == "__main__":
    _run_all()
