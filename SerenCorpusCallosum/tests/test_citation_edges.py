"""
Citation edges: a fact brings the facts it rests on (24 Nov 2026).

Loci keeps citations ("this holds because that does") and draws them as the
skyways between towers, but no packet ever carried one: a fan that found a
fact handed over the fact and never its reasons. The twin of the satellite
edges, on the rigid side. Pinned here:

- the Loci adapter asks for citations inline (options.with_citations: false spares it)
- on: each fact hit's rests_on becomes MARKED edges (source='citation-edge',
  edge_of the citing fact, because the citation's reason), after the satellites
  and before the topic edges, round-robin across facts, budgeted, deduped
  against the packet; a count stays on the hit, the content is lifted off it
- on by default, like the satellites (Chad, 24 Nov 2026, once measured); off: stripped, nothing added
- a page counts them against its budget, and MAKES ROOM for them: a page is
  packed full, so an edge told to fit in what is left never rode (gauntlet
  stage C, 24 Nov 2026: citations 0, 3 and 8 identical to the digit on a page).
  The lowest-ranked rows give up exactly what the reasons cost, the lead does
  not move, and a page with no citing fact gives up nothing
- citation_top: only the top N facts of the packet bring their reasons (Chad's
  "topX loci facts get their reasons, and the weaker ones dont")
- the knobs are settable live and on the Knobs board
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from seren_corpus_callosum.config import _DEFAULT_CITATION_TOP as CITATION_TOP_DEFAULT  # noqa: E402
from seren_corpus_callosum.config import FederationConfig, StoreConfig  # noqa: E402
from seren_corpus_callosum.federation import Federation  # noqa: E402

MEM_RESP = {"hits": [
    {"tier": "long", "content": "the core", "topic": "cft", "score": 0.83, "raw_distance": 0.2, "id": "c1",
     "metadata": {"kind": "core"},
     "surroundings": {"satellites": 1, "recent": [{"id": "s1", "content": "its episode", "created_at": 1.0}]}},
]}
LOCI_RESP = {"finder": "hybrid", "hits": [
    {"id": "l1", "project": "w", "key": "lurg.title", "value": "Sovereign", "why": "raised in 245",
     "score": 0.9, "match_kind": "hybrid", "raw_distance": 0.1,
     "rests_on": [{"id": "l2", "project": "w", "key": "lurg.civ", "value": "Thoknakhkrug", "why": "born into it",
                   "because": "a sovereign rules their own"},
                  {"id": "l3", "project": "w", "key": "thok.capital", "value": "Mturenfort", "why": None,
                   "because": "held from the capital"}]},
    {"id": "l4", "project": "w", "key": "laio.title", "value": "High King", "why": None,
     "score": 0.7, "match_kind": "hybrid", "raw_distance": 0.4,
     "rests_on": [{"id": "l5", "project": "w", "key": "laio.civ", "value": "Rakbazbor", "why": None,
                   "because": "king of their own"},
                  {"id": "l1", "project": "w", "key": "lurg.title", "value": "Sovereign", "why": None,
                   "because": "already in the packet"}]},
]}


class Canned:
    def __init__(self):
        self.calls = []

    async def post_json(self, url, payload, headers=None):
        self.calls.append((url, payload))
        return {"http://mem/search": MEM_RESP, "http://loci/search": LOCI_RESP}[url]


def _fed(**ov) -> tuple[Federation, Canned]:
    t = Canned()
    cfg = FederationConfig(
        stores=[StoreConfig(name="mem", type="seren_memory", url="http://mem"),
                StoreConfig(name="loci", type="seren_loci", url="http://loci",
                            options=ov.pop("loci_options", {}))],
        k=60, n_results=10, fetch_multiplier=2, edges_enabled=False,
        satellite_edges_enabled=ov.pop("satellites", True), satellite_budget=3,
        citation_edges_enabled=ov.pop("citations", True), citation_budget=ov.pop("budget", 3),
        citation_top=ov.pop("top", 0), budget_tokens=ov.pop("budget_tokens", None))
    return Federation(cfg, t), t


def _edges(fused):
    return [f.hit for f in fused if f.rrf_score == 0.0]


def _deep_copy_resp():
    # the federation lifts rests_on off the hit's metadata; keep the canned response pristine per test
    import copy
    global LOCI_RESP
    LOCI_RESP = copy.deepcopy(_PRISTINE)


_PRISTINE = __import__("copy").deepcopy(LOCI_RESP)


def setup_function(_):
    _deep_copy_resp()


def test_the_loci_adapter_asks_for_citations_inline():
    fed, t = _fed()
    asyncio.run(fed.search("q"))
    loci = next(p for u, p in t.calls if u == "http://loci/search")
    assert loci["with_citations"] is True
    fed, t = _fed(loci_options={"with_citations": False})
    asyncio.run(fed.search("q"))
    assert next(p for u, p in t.calls if u == "http://loci/search")["with_citations"] is False


def test_cited_facts_ride_as_marked_edges_after_the_satellites():
    fused = asyncio.run(_fed()[0].search("q"))
    edges = _edges(fused)
    assert [e.metadata["source"] for e in edges] == ["satellite-edge", "citation-edge", "citation-edge",
                                                     "citation-edge"]
    cited = [e for e in edges if e.metadata["source"] == "citation-edge"]
    # round-robin across the citing facts: l1's first, l4's first, then l1's second; l1 itself is not repeated
    assert [e.id for e in cited] == ["l2", "l5", "l3"]
    civ = cited[0]
    assert civ.metadata["edge_of"] == "l1" and civ.metadata["because"] == "a sovereign rules their own"
    assert "Thoknakhkrug" in civ.content and "lurg.civ" in civ.content
    title = next(f.hit for f in fused if f.hit.id == "l1")
    assert "rests_on" not in title.metadata and title.metadata["rests_on_n"] == 2


def test_the_budget_caps_them():
    cited = [e for e in _edges(asyncio.run(_fed(budget=1)[0].search("q"))) if e.metadata["source"] == "citation-edge"]
    assert [e.id for e in cited] == ["l2"]


def test_off_strips_them_and_adds_nothing():
    fused = asyncio.run(_fed(citations=False)[0].search("q"))
    assert not [e for e in _edges(fused) if e.metadata["source"] == "citation-edge"]
    title = next(f.hit for f in fused if f.hit.id == "l1")
    assert "rests_on" not in title.metadata and title.metadata["rests_on_n"] == 2


def test_a_page_counts_them_against_its_budget():
    from seren_corpus_callosum.fusion import packet_cost
    for budget, want in ((30, 1), (40, 2), (4000, 3)):
        setup_function(None)
        fused = asyncio.run(_fed(satellites=False)[0].search("q", budget_tokens=budget))
        cited = [e for e in _edges(fused) if e.metadata["source"] == "citation-edge"]
        assert len(cited) == want, (budget, [e.id for e in cited])
        assert packet_cost(fused) <= budget, "a page never runs over"


def test_the_knob_is_live_and_on_the_board(tmp_path):
    from fastapi.testclient import TestClient
    from seren_corpus_callosum.app import create_app
    from seren_corpus_callosum.config import load_config
    app = create_app(load_config(str(tmp_path / "nope.yaml")))
    app.state.transport = Canned()
    with TestClient(app) as c:
        by = {r["key"]: r for r in c.get("/knobs").json()["federation"]}
        assert by["citation_edges_enabled"]["default"] is True and by["citation_budget"]["default"] == 3
        assert by["citation_edges_enabled"]["runtime_settable"] is True
        r = c.post("/configure", json={"citation_edges_enabled": True, "citation_budget": 5})
        assert r.status_code == 200
        by = {r["key"]: r for r in c.get("/knobs").json()["federation"]}
        assert by["citation_edges_enabled"]["live"] is True and by["citation_budget"]["live"] == 5
        assert by["citation_top"]["default"] == CITATION_TOP_DEFAULT and by["citation_top"]["runtime_settable"] is True
        assert c.post("/configure", json={"citation_top": 2}).status_code == 200
        by = {r["key"]: r for r in c.get("/knobs").json()["federation"]}
        assert by["citation_top"]["live"] == 2


def _many(n: int = 12) -> dict:
    """A Loci answer of n facts of one size; the first rests on two facts that are not among them."""
    hits = [{"id": f"f{i:02}", "project": "w", "key": f"fact.{i:02}", "value": f"value number {i:02} of the chronicle",
             "why": None, "score": 0.9 - i * 0.01, "match_kind": "hybrid", "raw_distance": 0.1 + i * 0.01}
            for i in range(n)]
    hits[0]["rests_on"] = [
        {"id": "r1", "project": "w", "key": "reason.one", "value": "the first reason", "why": None, "because": "it does"},
        {"id": "r2", "project": "w", "key": "reason.two", "value": "the second reason", "why": None, "because": "it does"}]
    return {"finder": "hybrid", "hits": hits}


def _page(budget: int, **ov):
    global LOCI_RESP
    LOCI_RESP = ov.pop("loci", None) or _many()
    return asyncio.run(_fed(satellites=False, **ov)[0].search("q", budget_tokens=budget))


def _rows(fused):
    return [f.hit.id for f in fused if f.rrf_score != 0.0]


def test_a_full_page_makes_room_for_its_top_facts_reasons():
    from seren_corpus_callosum.fusion import packet_cost
    budget = packet_cost(_page(4000, citations=False)[:8])      # a page eight rows fill exactly
    off, on = _page(budget, citations=False), _page(budget)
    assert packet_cost(off) == budget and not _edges(off), "full, with nothing left for an edge to fit in"
    assert [e.id for e in _edges(on)] == ["r1", "r2"], "the top fact's reasons ride anyway"
    assert packet_cost(on) <= budget, "a page never runs over"
    assert _rows(on) == _rows(off)[:len(_rows(on))] and len(_rows(on)) < len(_rows(off)), \
        "the tail gave way; the lead did not move"
    spare = budget - packet_cost(on)
    assert spare < packet_cost(off[-1:]), "it gave up what the reasons cost and no more than a row besides"


def test_a_page_with_no_citing_fact_gives_up_nothing():
    def bare():
        resp = _many()
        resp["hits"][0].pop("rests_on")
        return resp
    assert _rows(_page(200, loci=bare())) == _rows(_page(200, loci=bare(), citations=False))


def test_only_the_top_facts_bring_their_reasons():
    def cited(**ov):
        setup_function(None)
        return [e.id for e in _edges(asyncio.run(_fed(satellites=False, **ov)[0].search("q")))]
    assert cited(top=1) == ["l2", "l3"], "l1 leads the packet's facts; l4, further down, brings nothing"
    assert cited(top=2) == ["l2", "l5", "l3"]
    assert cited(top=0) == ["l2", "l5", "l3"], "0 is no gate"


def test_the_gate_holds_on_a_page_too():
    def gated():
        resp = _many()
        resp["hits"][3]["rests_on"] = resp["hits"][0].pop("rests_on")     # the reasons hang off the fourth fact now
        return resp
    assert [e.id for e in _edges(_page(4000, loci=gated(), top=3))] == []
    assert [e.id for e in _edges(_page(4000, loci=gated(), top=4))] == ["r1", "r2"]
