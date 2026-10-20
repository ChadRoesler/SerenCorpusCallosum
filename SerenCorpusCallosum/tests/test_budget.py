"""
Tests for the packet as a PAGE: budget_tokens.

The reader of a packet is a model with a context budget - it reads tokens, not
rows. With budget_tokens the trim is that budget: a row's cost enters the
packing, the diversity floor seats each store on the page the reader actually
sees, the per-store ask is sized to fill the page, and the edges and hop hits
are packed into what is left of it instead of appended past the trim. Without
a budget everything is byte-identical to the row-count packet.

Run: pytest tests/test_budget.py   OR   python tests/test_budget.py
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from seren_corpus_callosum.app import create_app  # noqa: E402
from seren_corpus_callosum.config import (  # noqa: E402
    CorpusCallosumConfig, FederationConfig, ServerConfig, StoreConfig,
)
from seren_corpus_callosum.federation import Federation  # noqa: E402
from seren_corpus_callosum.fusion import (  # noqa: E402
    Hit, est_tokens, fit_in_budget, pack_to_budget, packet_cost, row_cost, rrf_fuse,
)


def _hit(store: str, hid: str, text: str, via: str | None = None, base: float = 0.5) -> Hit:
    h = Hit(store=store, id=hid, content=text, base_relevance=base)
    if via is not None:
        h.metadata["matched_via"] = {"id": f"{hid}-sat", "content": via}
    return h


def _order(fused) -> list[str]:
    return [f"{f.hit.store}:{f.hit.id}" for f in fused]


# ── the estimate ───────────────────────────────────────────────────────────

def test_est_tokens_four_chars_each_never_below_one():
    assert est_tokens("") == 1 and est_tokens(None) == 1
    assert est_tokens("abcd") == 1
    assert est_tokens("abcde") == 2


def test_row_cost_counts_the_satellite_a_core_matched_through():
    assert row_cost(_hit("m", "m1", "x" * 40)) == 11                   # 10 of text + 1 for the frame
    assert row_cost(_hit("m", "m2", "x" * 40, via="y" * 20)) == 16     # + the episode's 5
    assert row_cost(_hit("m", "m3", "x" * 40, via="")) == 11           # an empty via costs nothing


# ── the packer ─────────────────────────────────────────────────────────────

def test_page_is_packed_head_first_and_an_oversize_row_is_skipped_not_a_stop():
    mem = [_hit("m", f"m{i}", "m" * 400) for i in range(1, 4)]     # 101 tokens each
    loci = [_hit("l", f"l{i}", "l" * 40) for i in range(1, 6)]     # 11 tokens each
    # fused order: m1 l1 m2 l2 m3 l3 l4 l5. 160 tokens: m1+l1 = 112; m2 does not
    # fit and is SKIPPED; l2..l5 ride in behind it (156).
    fused = rrf_fuse({"m": mem, "l": loci}, k=60, budget_tokens=160, min_per_store=0)
    assert _order(fused) == ["m:m1", "l:l1", "l:l2", "l:l3", "l:l4", "l:l5"]
    assert packet_cost(fused) == 156 <= 160


def test_the_diversity_floor_seats_a_downweighted_store_on_the_page():
    mem = [_hit("m", f"m{i}", "m" * 400) for i in range(1, 4)]     # 101 each
    loci = [_hit("l", f"l{i}", "l" * 40) for i in range(1, 3)]     # 11 each
    # Loci at half weight sits below every mem row (k=60): m1 m2 m3 l1 l2.
    # 310 tokens fit exactly three cores, so with no floor the page is one class...
    naive = rrf_fuse({"m": mem, "l": loci}, k=60, weights={"l": 0.5},
                     budget_tokens=310, min_per_store=0)
    assert _order(naive) == ["m:m1", "m:m2", "m:m3"]
    # ...and with the floor Loci's best row has its seat on the page, m3 gives way.
    quota = rrf_fuse({"m": mem, "l": loci}, k=60, weights={"l": 0.5},
                     budget_tokens=310, min_per_store=1)
    assert _order(quota) == ["m:m1", "m:m2", "l:l1", "l:l2"]
    assert packet_cost(quota) <= 310


def test_the_reserved_seat_goes_to_the_stores_next_row_that_fits():
    # The down-weighted store's top row is too big for the page; its second fits.
    mem = [_hit("m", "m1", "m" * 40)]
    loci = [_hit("l", "l1", "l" * 4000), _hit("l", "l2", "l" * 40)]
    fused = rrf_fuse({"m": mem, "l": loci}, k=60, weights={"l": 0.5},
                     budget_tokens=30, min_per_store=1)
    assert _order(fused) == ["m:m1", "l:l2"]


def test_n_results_beside_a_budget_is_a_second_cap():
    loci = [_hit("l", f"l{i}", "l" * 40) for i in range(1, 10)]
    assert len(rrf_fuse({"l": loci}, k=60, n_results=3, budget_tokens=1000)) == 3
    assert len(rrf_fuse({"l": loci}, k=60, n_results=None, budget_tokens=40)) == 3   # 33 fit, 44 does not
    assert len(rrf_fuse({"l": loci}, k=60, n_results=8, budget_tokens=40)) == 3


def test_no_budget_is_the_row_count_trim():
    loci = [_hit("l", f"l{i}", "l" * 40) for i in range(1, 10)]
    plain = rrf_fuse({"l": loci}, k=60, n_results=3)
    assert _order(rrf_fuse({"l": loci}, k=60, n_results=3, budget_tokens=None)) == _order(plain)
    assert _order(rrf_fuse({"l": loci}, k=60, n_results=3, budget_tokens=0)) == _order(plain)


def test_the_authority_lead_still_leads_the_page():
    # loci's top is a perfect exact-key hit; authority promotes it to lead and the
    # page keeps it first (emitted in fused order).
    mem = [_hit("m", "m1", "m" * 40, base=0.6), _hit("m", "m2", "m" * 40, base=0.5)]
    loci = [_hit("l", "l1", "l" * 40, base=1.0), _hit("l", "l2", "l" * 40, base=0.4)]
    fused = rrf_fuse({"m": mem, "l": loci}, k=60, authority_margin=0.035, budget_tokens=25)
    assert _order(fused)[0] == "l:l1"
    assert packet_cost(fused) <= 25


def test_pack_and_fit_helpers_edges():
    rows = rrf_fuse({"l": [_hit("l", "l1", "l" * 40), _hit("l", "l2", "l" * 4)]}, k=60)
    assert pack_to_budget(rows, 5, 1) == rows[1:]            # only the 2-token row fits
    kept, left = fit_in_budget(rows, 100)
    assert kept == rows and left == 100 - 11 - 2
    assert fit_in_budget(rows, 0) == ([], 0)


# ── the fan on a page ──────────────────────────────────────────────────────

def _mem_hit(hid: str, text: str, **extra) -> dict:
    return {"tier": "long", "content": text, "score": 0.8, "raw_distance": 0.2, "id": hid,
            "metadata": {}, **extra}


MEM_RESP = {"hits": [
    _mem_hit("c1", "the core, forty chars of it, give or take",
             surroundings={"satellites": 2, "latest_satellite_at": 300.0, "supersedes": None,
                           "superseded_by": None,
                           "recent": [{"id": "s2", "content": "a short episode", "created_at": 300.0},
                                      {"id": "s1", "content": "e" * 2000, "created_at": 100.0}]}),
    _mem_hit("c2", "another core", raw_distance=0.9),     # a clear mem margin, so mem's c1 leads
]}
LOCI_RESP = {"finder": "vector", "hits": [
    {"id": "l1", "project": "*", "key": "k1", "value": "loci one", "why": "w",
     "score": 0.53, "match_kind": "vector", "source": "model", "raw_distance": 0.88},
    {"id": "l2", "project": "*", "key": "k2", "value": "loci two", "why": "w",
     "score": 0.45, "match_kind": "vector", "source": "model", "raw_distance": 1.22},
]}


class RecordingTransport:
    def __init__(self, by_url):
        self.by_url = by_url
        self.calls = []

    async def post_json(self, url, payload, headers=None):
        self.calls.append((url, payload))
        v = self.by_url.get(url)
        if v is None:
            raise RuntimeError(f"no canned response for {url}")
        if callable(v):
            return v(payload)
        return v

    def asked(self, url: str) -> int:
        return next(p["n_results"] for u, p in self.calls if u == url)


def _config(**kw) -> FederationConfig:
    return FederationConfig(
        stores=[StoreConfig(name="mem", type="seren_memory", url="http://mem"),
                StoreConfig(name="loci", type="seren_loci", url="http://loci")],
        k=60, edges_enabled=False, **kw)


def _canned() -> RecordingTransport:
    return RecordingTransport({"http://mem/search": MEM_RESP, "http://loci/search": LOCI_RESP})


def test_a_budget_sizes_the_ask_and_the_report_says_what_the_page_cost():
    t = _canned()
    report = asyncio.run(Federation(_config(), t).search_report("q", budget_tokens=1000))
    assert t.asked("http://loci/search") == 63          # 1000 / 16 candidates, rounded up
    assert t.asked("http://mem/search") == 50           # Memory's own cap
    assert report.budget_tokens == 1000
    assert report.tokens == packet_cost(report.hits) <= 1000
    assert _order(report.hits)[:4] == ["mem:c1", "loci:l1", "mem:c2", "loci:l2"]


def test_without_a_budget_the_ask_and_the_packet_are_as_before():
    t = _canned()
    report = asyncio.run(Federation(_config(), t).search_report("q", n_results=10))
    assert t.asked("http://loci/search") == 20          # n * fetch_multiplier
    assert report.budget_tokens is None
    assert report.tokens == packet_cost(report.hits)    # the estimate is reported either way
    assert _order(report.hits)[:4] == ["mem:c1", "loci:l1", "mem:c2", "loci:l2"]


def test_satellite_edges_are_packed_into_what_the_page_has_left():
    # Page of 60: the four ranked rows cost ~36; the short episode (5) fits, the
    # 2,000-char one (501) does not and is left out although the count allows it.
    report = asyncio.run(Federation(_config(), _canned()).search_report("q", budget_tokens=60))
    ids = _order(report.hits)
    assert "mem:s2" in ids and "mem:s1" not in ids
    assert report.tokens <= 60
    # Without a budget both ride along (count 3 allows them).
    plain = asyncio.run(Federation(_config(), _canned()).search_report("q"))
    assert {"mem:s2", "mem:s1"} <= set(_order(plain.hits))


def test_the_configs_default_page_applies_and_the_calls_own_budget_wins():
    cfg = _config(budget_tokens=12)          # room for the first core (11) and nothing else
    report = asyncio.run(Federation(cfg, _canned()).search_report("q"))
    assert _order(report.hits) == ["mem:c1"] and report.budget_tokens == 12
    report = asyncio.run(Federation(cfg, _canned()).search_report("q", budget_tokens=1000))
    assert report.budget_tokens == 1000 and len(report.hits) > 1
    assert FederationConfig.from_dict({"budget_tokens": 800}).budget_tokens == 800
    assert FederationConfig.from_dict({"budget_tokens": 0}).budget_tokens is None
    assert FederationConfig.from_dict({}).budget_tokens is None


def test_hop_hits_have_to_fit_the_page_too():
    # Round 1 finds c1; round 2 (the bridge query carries c1's text) finds a huge
    # doc and a small one. On a 40-token page only the small one rides in.
    def mem(payload):
        if "the core" in payload["query"] and payload["query"] != "q":
            return {"hits": [_mem_hit("h-big", "h" * 4000), _mem_hit("h-small", "a hop hit")]}
        return {"hits": [_mem_hit("c1", "the core, forty chars of it, give or take")]}
    t = RecordingTransport({"http://mem/search": mem, "http://loci/search": {"hits": []}})
    cfg = _config(hops=2, hop_budget=5, satellite_edges_enabled=False)
    report = asyncio.run(Federation(cfg, t).search_report("q", budget_tokens=40))
    assert _order(report.hits) == ["mem:c1", "mem:h-small"]
    assert report.hits[1].hit.metadata["hop"] == 2
    assert report.tokens <= 40
    plain = asyncio.run(Federation(cfg, t).search_report("q"))
    assert _order(plain.hits) == ["mem:c1", "mem:h-big", "mem:h-small"]


# ── the wire ───────────────────────────────────────────────────────────────

def _app_cfg(**fed) -> CorpusCallosumConfig:
    return CorpusCallosumConfig(server=ServerConfig(bearer_token=""), federation=_config(**fed))


def test_search_route_takes_a_budget_and_reports_the_cost():
    app = create_app(config=_app_cfg(), transport=_canned())
    with TestClient(app) as client:
        r = client.post("/search", json={"query": "q", "budget_tokens": 60})
        assert r.status_code == 200
        data = r.json()
        assert data["budget_tokens"] == 60 and 0 < data["tokens"] <= 60
        assert "mem:s1" not in [f"{h['store']}:{h['id']}" for h in data["hits"]]
        plain = client.post("/search", json={"query": "q"}).json()
        assert plain["budget_tokens"] is None and plain["tokens"] > 0
        assert client.post("/search", json={"query": "q", "budget_tokens": 0}).status_code == 422


def test_configure_sets_and_clears_the_default_page():
    app = create_app(config=_app_cfg(), transport=_canned())
    with TestClient(app) as client:
        assert client.get("/stores").json()["budget_tokens"] is None
        r = client.post("/configure", json={"budget_tokens": 60})
        assert r.status_code == 200 and r.json()["changed"]["budget_tokens"] == 60
        assert client.get("/stores").json()["budget_tokens"] == 60
        assert client.post("/search", json={"query": "q"}).json()["budget_tokens"] == 60
        r = client.post("/configure", json={"budget_tokens": 0})
        assert r.json()["changed"]["budget_tokens"] is None
        assert client.post("/search", json={"query": "q"}).json()["budget_tokens"] is None


def test_mcp_tool_passes_the_budget_through():
    pytest.importorskip("mcp")
    from seren_corpus_callosum.mcp.tools import SccToolImpl
    impl = SccToolImpl(Federation(_config(), _canned()))
    out = asyncio.run(impl.search("q", budget_tokens=60))
    assert out["budget_tokens"] == 60 and 0 < out["tokens"] <= 60
    out = asyncio.run(impl.search("q"))
    assert out["budget_tokens"] is None


if __name__ == "__main__":
    import inspect
    here = sys.modules[__name__]
    tests = [f for n, f in inspect.getmembers(here, inspect.isfunction) if n.startswith("test_")]
    for f in tests:
        f()
        print("ok", f.__name__)
    print(f"{len(tests)} passed")
