"""
Satellites get room on a page (24 Nov 2026), the treatment the reasons got.

A core's episodes rode as edges under satellite_budget, a COUNT: three for the
whole packet however big the page, and on a page those three had to fit in
what the rows left. On the Vellmarch ladder detail questions scored the same at
1,000 and at 4,000 tokens on every rung. Chad: "we dont token budget but
result count budget". Pinned here:

- said nothing, the request Memory gets and the packet a search returns are what
  they were (satellite_share 0: the count, and only what the page has left)
- satellite_share: on a page the satellites are picked first and the lowest
  rows give way for exactly what they cost; the lead does not move; the count
  does not apply; a page with no satellites gives up nothing
- the share caps them, and with the reasons they never pass half a page
- satellite_top: only the top N cores bring theirs (Chad's top-X)
- with a share, an episode already riding on its core's row is not repeated
- satellites_per_core and satellite_order ride to Memory through its adapter,
  and a store's own options win; options.max_n raises a store's row ceiling,
  and the ceiling said nothing about stays 50
- the knobs are settable live and on the Knobs board
"""
from __future__ import annotations

import asyncio
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from seren_corpus_callosum.config import FederationConfig, StoreConfig  # noqa: E402
from seren_corpus_callosum.federation import Federation  # noqa: E402
from seren_corpus_callosum.fusion import Hit, packet_cost, row_cost  # noqa: E402


def _cores(n: int = 12, sats: dict | None = None, via: dict | None = None) -> dict:
    """A Memory answer of n cores of one size. sats: core index -> how many
    episodes it carries inline; via: core index -> the episode it matched through."""
    hits = []
    for i in range(n):
        h = {"tier": "long", "content": f"core number {i:02} of the chronicle of the realm", "topic": "t",
             "score": 0.9 - i * 0.01, "raw_distance": 0.1 + i * 0.01, "id": f"c{i:02}",
             "metadata": {"kind": "core"},
             "surroundings": {"satellites": (sats or {}).get(i, 0), "latest_satellite_at": None,
                              "supersedes": None, "superseded_by": None, "recent": []}}
        for j in range((sats or {}).get(i, 0)):
            h["surroundings"]["recent"].append(
                {"id": f"s{i:02}.{j}", "content": f"episode {j} of core {i:02}: what happened that season",
                 "created_at": 100.0 - j, "raw_distance": 0.2 + j * 0.05})
        if via and i in via:
            h["matched_via"] = dict(via[i])
        hits.append(h)
    return {"hits": hits, "searched_tiers": ["long"]}


LOCI = {"finder": "hybrid", "hits": [
    {"id": "f0", "project": "w", "key": "seat.holder", "value": "the old orc who rules from the lair", "why": None,
     "score": 0.95, "match_kind": "hybrid", "raw_distance": 0.05,
     "rests_on": [{"id": f"r{k}", "project": "w", "key": f"reason.{k}",
                   "value": f"reason number {k}: a long account of the war that put him there and who fell in it",
                   "why": None, "because": "it does"} for k in range(6)]}]}


class Canned:
    def __init__(self, mem: dict, loci: dict | None = None):
        self.mem, self.loci, self.calls = mem, loci, []

    async def post_json(self, url, payload, headers=None):
        self.calls.append((url, payload))
        return copy.deepcopy(self.mem if url.startswith("http://mem") else self.loci)


def _fed(mem: dict, loci: dict | None = None, mem_options: dict | None = None, **ov) -> tuple[Federation, Canned]:
    t = Canned(mem, loci)
    stores = [StoreConfig(name="mem", type="seren_memory", url="http://mem", options=mem_options or {})]
    if loci is not None:
        stores.append(StoreConfig(name="loci", type="seren_loci", url="http://loci"))
    cfg = FederationConfig(stores=stores, k=60, n_results=10, fetch_multiplier=2, edges_enabled=False,
                           citation_budget=ov.pop("citation_budget", 3), citation_top=0, **ov)
    return Federation(cfg, t), t


def _search(mem, budget=None, loci=None, **ov):
    return asyncio.run(_fed(mem, loci, **ov)[0].search("q", budget_tokens=budget))


def _rows(fused):
    return [f.hit.id for f in fused if f.rrf_score != 0.0]


def _edges(fused, source="satellite-edge"):
    return [f.hit for f in fused if f.rrf_score == 0.0 and f.hit.metadata.get("source") == source]


def _cost(hits):
    return sum(row_cost(h) for h in hits)


ROW = row_cost(Hit(store="mem", id="x", content="core number 00 of the chronicle of the realm", base_relevance=1.0))
EIGHT = ROW * 8                                    # a page eight of these rows fill exactly


# ── said nothing, nothing changed ────────────────────────────────────────────

def test_said_nothing_memory_is_asked_what_it_always_was():
    fed, t = _fed(_cores())
    asyncio.run(fed.search("q"))
    asked = t.calls[0][1]
    assert set(asked) == {"query", "n_results", "include_short", "include_near", "include_long",
                          "include_superseded", "with_surroundings"}, asked
    assert asked["n_results"] == 20, "n_results 10 x fetch_multiplier 2"


def test_share_zero_is_the_count_and_what_the_page_has_left():
    mem = _cores(sats={0: 2, 1: 2, 2: 2})
    full = _search(mem, EIGHT)
    assert len(_rows(full)) == 8 and not _edges(full), "a full page: a satellite told to fit in what is left never rides"
    roomy = _search(mem, 4000)
    assert [e.id for e in _edges(roomy)] == ["s00.0", "s01.0", "s02.0"], "three, round-robin, the count"


# ── the share ────────────────────────────────────────────────────────────────

def test_a_full_page_makes_room_for_its_cores_episodes():
    mem = _cores(sats={0: 2})
    off, on = _search(mem, EIGHT), _search(mem, EIGHT, satellite_share=0.3)     # 28 of 96 tokens: two episodes of 13
    assert [e.id for e in _edges(on)] == ["s00.0", "s00.1"], "the top core's episodes ride anyway"
    assert packet_cost(on) <= EIGHT, "a page never runs over"
    assert _rows(on) == _rows(off)[:len(_rows(on))] and len(_rows(on)) < len(_rows(off)), \
        "the tail gave way; the lead did not move"
    assert EIGHT - packet_cost(on) < ROW, "it gave up what the episodes cost and no more than a row besides"
    edge = _edges(on)[0]
    assert edge.metadata["edge_of"] == "c00" and edge.metadata["core_id"] == "c00" and edge.raw_distance == 0.2, \
        "marked as before, and the distance Memory sent in match order rides for display"
    core = next(f.hit for f in on if f.hit.id == "c00")
    assert "recent" not in core.metadata["surroundings"] and core.metadata["surroundings"]["satellites"] == 2


def test_an_episode_that_repeats_a_row_word_for_word_does_not_ride():
    # A core that was restated keeps its first wording as an episode. In match
    # order that copy is the nearest thing to the question there is, so it was
    # seated first, beside the row it copies (25 Nov 2026, seen on a real memory).
    mem = _cores(sats={0: 3})
    mem["hits"][0]["surroundings"]["recent"][0]["content"] = "  Core number 00 of the  chronicle of the realm "
    page = _search(mem, 4000, satellite_share=0.3)
    assert [e.id for e in _edges(page)] == ["s00.1", "s00.2"], "the copy stays home and the next episodes ride"
    assert [e.id for e in _edges(_search(mem))] == ["s00.1", "s00.2"], "the same on a packet of rows, where it would cost one of the three"
    twins = _cores(sats={0: 3})
    twins["hits"][0]["surroundings"]["recent"][1]["content"] = twins["hits"][0]["surroundings"]["recent"][0]["content"]
    assert [e.id for e in _edges(_search(twins, 4000, satellite_share=0.3))] == ["s00.0", "s00.2"], \
        "nor two episodes with the same words"


def test_a_page_with_no_satellites_gives_up_nothing():
    assert _rows(_search(_cores(), EIGHT, satellite_share=0.4)) == _rows(_search(_cores(), EIGHT))


def test_on_a_page_with_a_share_the_count_does_not_apply():
    mem = _cores(sats={0: 3, 1: 3})
    on = _search(mem, 4000, satellite_share=0.25, satellite_budget=3)
    assert len(_edges(on)) == 6, "six fit in a quarter of 4,000 tokens; the count of three is for a row-count packet"
    assert len(_edges(_search(mem, None, satellite_share=0.25, satellite_budget=3))) == 3, "which still counts"


def test_the_share_caps_what_they_take():
    mem = _cores(sats={i: 5 for i in range(12)})
    for share in (0.1, 0.25, 0.4):
        on = _search(mem, 1000, satellite_share=share)
        took = _cost(_edges(on))
        assert 0 < took <= int(1000 * share), (share, took)
        assert packet_cost(on) <= 1000


def test_a_share_over_one_half_is_one_half():
    mem = _cores(sats={i: 5 for i in range(12)})
    on = _search(mem, 600, satellite_share=0.9)
    assert _cost(_edges(on)) <= 300 and len(_rows(on)) >= 1


def test_satellites_and_reasons_together_leave_the_rows_half_the_page():
    mem = _cores(sats={i: 5 for i in range(12)})
    on = _search(mem, 600, loci=LOCI, satellite_share=0.5, citation_budget=8)
    sat, why = _cost(_edges(on)), _cost(_edges(on, "citation-edge"))
    assert why > 0 and sat > 0, "both ride"
    assert sat + why <= 300, (sat, why)
    assert packet_cost(on) <= 600
    order = [f.hit.metadata.get("source") for f in on if f.rrf_score == 0.0]
    assert order == sorted(order, key=lambda s: s != "satellite-edge"), "the satellites, then the reasons"


# ── the gate ─────────────────────────────────────────────────────────────────

def test_only_the_top_cores_bring_their_episodes():
    mem = _cores(sats={0: 1, 3: 1})

    def edges(budget, **ov):
        return [e.id for e in _edges(_search(mem, budget, **ov))]
    assert edges(None) == ["s00.0", "s03.0"]
    assert edges(None, satellite_top=3) == ["s00.0"], "c03 is the fourth core: it brings nothing"
    assert edges(None, satellite_top=4) == ["s00.0", "s03.0"]
    assert edges(4000, satellite_share=0.25, satellite_top=3) == ["s00.0"]
    assert edges(4000, satellite_share=0.25, satellite_top=0) == ["s00.0", "s03.0"], "0 is no gate"


# ── an episode the reader already has ────────────────────────────────────────

def test_with_a_share_the_episode_riding_on_its_cores_row_is_not_repeated():
    via = {"id": "s00.0", "content": "episode 0 of core 00: what happened that season", "created_at": 100.0,
           "raw_distance": 0.2, "tier": "long"}
    mem = _cores(sats={0: 2}, via={0: via})
    on = _search(mem, 4000, satellite_share=0.25)
    assert [e.id for e in _edges(on)] == ["s00.1"], "s00.0 is on c00's row as matched_via: the page does not pay twice"
    assert [e.id for e in _edges(_search(mem, None))][:1] == ["s00.0"], "a row-count packet is as it was"


# ── what rides to Memory ─────────────────────────────────────────────────────

def test_the_per_core_ask_and_the_order_ride_to_memory():
    fed, t = _fed(_cores(), satellites_per_core=5, satellite_order="match")
    asyncio.run(fed.search("q"))
    asked = t.calls[0][1]
    assert asked["surroundings_n"] == 5 and asked["surroundings_by"] == "match"
    fed, t = _fed(_cores(), mem_options={"surroundings_n": 8, "surroundings_by": "recent"},
                  satellites_per_core=5, satellite_order="match")
    asyncio.run(fed.search("q"))
    asked = t.calls[0][1]
    assert asked["surroundings_n"] == 8 and "surroundings_by" not in asked, "the store's own options win"


def test_a_stores_row_ceiling_is_fifty_until_the_store_says_otherwise():
    fed, t = _fed(_cores())
    asyncio.run(fed.search("q", budget_tokens=4000))
    assert t.calls[0][1]["n_results"] == 50, "4000/16 = 250 asked for; an older Memory answers 422 above 50"
    fed, t = _fed(_cores(), mem_options={"max_n": 200})
    asyncio.run(fed.search("q", budget_tokens=4000))
    assert t.calls[0][1]["n_results"] == 200


def test_an_order_nobody_knows_falls_back_at_load():
    cfg = FederationConfig.from_dict({"satellite_order": "sideways", "satellite_share": "lots", "satellite_top": -2})
    assert cfg.satellite_order == "recent" and cfg.satellite_share == 0.0 and cfg.satellite_top == 0
    assert FederationConfig.from_dict({"satellite_share": 0.8}).satellite_share == 0.5


# ── the board ────────────────────────────────────────────────────────────────

def test_the_knobs_are_live_and_on_the_board(tmp_path):
    from fastapi.testclient import TestClient
    from seren_corpus_callosum.app import create_app
    from seren_corpus_callosum.config import (_DEFAULT_SATELLITE_ORDER, _DEFAULT_SATELLITE_SHARE,
                                              _DEFAULT_SATELLITE_TOP, _DEFAULT_SATELLITES_PER_CORE, load_config)
    app = create_app(load_config(str(tmp_path / "nope.yaml")))
    app.state.transport = Canned(_cores())
    with TestClient(app) as c:
        by = {r["key"]: r for r in c.get("/knobs").json()["federation"]}
        for key, default in (("satellite_share", _DEFAULT_SATELLITE_SHARE), ("satellite_top", _DEFAULT_SATELLITE_TOP),
                             ("satellites_per_core", _DEFAULT_SATELLITES_PER_CORE),
                             ("satellite_order", _DEFAULT_SATELLITE_ORDER)):
            assert by[key]["default"] == default and by[key]["runtime_settable"] is True and by[key]["what"], key
        r = c.post("/configure", json={"satellite_share": 0.9, "satellite_top": 5, "satellites_per_core": 4,
                                       "satellite_order": "match"})
        assert r.status_code == 200
        assert r.json()["changed"] == {"satellite_share": 0.5, "satellite_top": 5, "satellites_per_core": 4,
                                       "satellite_order": "match"}, "the share is held to one half"
        by = {r["key"]: r for r in c.get("/knobs").json()["federation"]}
        assert by["satellite_share"]["live"] == 0.5 and by["satellite_order"]["live"] == "match"
        st = c.get("/stores").json()
        assert st["satellite_share"] == 0.5 and "satellite_top" in st["supported_knobs"]
        assert c.post("/configure", json={"satellite_order": "sideways"}).status_code == 422
