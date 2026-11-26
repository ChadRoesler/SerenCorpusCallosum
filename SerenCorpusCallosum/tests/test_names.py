"""
Names: what two stores have in common (25 Nov 2026).

Chad: "MEMORY HAS TOPIC A PARALLEL TO LOCIS KEYS". A memory's topic tags and a
fact's project and key are both names a person chose; they survive any
embedder, where every score and distance the stores report does not. Pinned:

- a row's names: its topic tags, a fact's project and key; '*' is no name
- one word spelled two ways is one name (licence / license, satellite /
  satallite); a short word must match exactly; two different things that only
  belong together stay two (serenchan / stackchan)
- every row says which names it shares with the other store's lead rows, and a
  row that shares none says nothing; it changes no order
- a name on nearly everything in the house is learned and left out, never listed
- name edges: off by default; on, a fact's project and key are asked of the
  stores that keep topics and the episodes tagged with them are appended,
  marked, saying which fact brought them; never one already in the packet
- the knob is live and on the board, and the tally survives a /configure
"""
from __future__ import annotations

import asyncio
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from seren_corpus_callosum.config import FederationConfig, StoreConfig  # noqa: E402
from seren_corpus_callosum.federation import Federation  # noqa: E402
from seren_corpus_callosum.fusion import Hit  # noqa: E402
from seren_corpus_callosum.names import NameTally, name_tokens, row_names, same_name, shared_names  # noqa: E402


def _mem(topics: list[str]) -> dict:
    return {"hits": [{"tier": "long", "content": f"episode {i}: what happened", "topic": t, "score": 0.9 - i * 0.01,
                      "raw_distance": 0.4 + i * 0.01, "id": f"m{i}", "metadata": {"kind": "core"}}
                     for i, t in enumerate(topics)], "searched_tiers": ["long"]}


def _loci(facts: list[tuple[str, str]]) -> dict:
    return {"finder": "hybrid", "hits": [{"id": f"f{i}", "project": p, "key": k, "value": "a thing held to be true",
                                          "why": None, "score": 0.9, "match_kind": "hybrid", "raw_distance": 0.9 + i * 0.01}
                                         for i, (p, k) in enumerate(facts)]}


class Canned:
    def __init__(self, mem: dict, loci: dict, tagged: list[dict] | None = None):
        self.mem, self.loci, self.tagged, self.asked = mem, loci, tagged or [], []

    async def post_json(self, url, payload, headers=None):
        if url.endswith("/by_topic"):
            self.asked.append(payload)
            want, gone = set(payload["topics"]), set(payload.get("exclude_ids") or [])
            hits = []
            for e in self.tagged:
                matched = [t for t in e["tags"] if t in want]
                if matched and e["id"] not in gone:
                    hits.append({"tier": e.get("tier", "long"), "content": e["content"], "topic": ", ".join(e["tags"]),
                                 "matched_topics": matched, "overlap": len(matched), "id": e["id"], "metadata": {},
                                 # a Memory that was handed the question says how near each entry sits to it
                                 "raw_distance": e.get("dist") if payload.get("query") else None})
            return {"topics": payload["topics"], "hits": hits[:payload["n_results"]], "searched_tiers": ["long"]}
        return copy.deepcopy(self.mem if url.startswith("http://mem") else self.loci)


def _fed(mem, loci, tagged=None, names=None, **ov):
    t = Canned(mem, loci, tagged)
    cfg = FederationConfig(stores=[StoreConfig(name="mem", type="seren_memory", url="http://mem"),
                                   StoreConfig(name="loci", type="seren_loci", url="http://loci")],
                           k=60, n_results=10, fetch_multiplier=2, edges_enabled=False,
                           satellite_edges_enabled=False, citation_edges_enabled=False, **ov)
    return Federation(cfg, t, names=names), t


def _ask(fed, n=10):
    return asyncio.run(fed.search_report("q", n_results=n)).hits


KITCHEN = _mem(["kitchen, fridge", "seren-kitchen, punch-list", "design"])
FACTS = _loci([("seren-kitchen", "own-mcp-door"), ("*", "updates-extra-retired"), ("wren", "left-handed-right-eye")])


def test_a_rows_names():
    assert name_tokens("own-mcp-door") == {"own", "mcp", "door"} and name_tokens("a, of, to") == set()
    assert row_names(Hit(store="m", id="x", content="", base_relevance=0, metadata={"topic": "Seren-Kitchen, punch list"})) == \
        {"seren", "kitchen", "punch", "list"}
    fact = Hit(store="l", id="y", content="", base_relevance=0, metadata={"project": "seren-kitchen", "key": "own-mcp-door"})
    assert row_names(fact) == {"seren", "kitchen", "own", "mcp", "door"}
    everywhere = Hit(store="l", id="z", content="", base_relevance=0, metadata={"project": "*", "key": "seren-license"})
    assert row_names(everywhere) == {"seren", "license"}, "'*' is true everywhere and names nothing"
    assert row_names(Hit(store="m", id="w", content="", base_relevance=0)) == set()


def test_one_word_spelled_two_ways_is_one_name():
    assert same_name("licence", "license") and same_name("satellite", "satallite") and same_name("callosum", "calosum")
    assert same_name("australian", "austrailian"), "two slips in a long word"
    assert same_name("kitchen", "kitchens") and same_name("facts", "fact"), "a plural"
    assert not same_name("store", "stone"), "a short word must match exactly"
    assert not same_name("serenchan", "stackchan"), "two things that belong together are still two things"
    assert not same_name("memory", "memoir") and not same_name("license", "silence")
    assert shared_names({"licence", "repos", "door"}, {"license", "kitchen"}) == ["licence"]


def test_every_row_says_which_names_it_shares_with_the_other_store():
    fed, _ = _fed(KITCHEN, FACTS)
    hits = _ask(fed)
    by = {f.hit.id: f.hit.metadata.get("shares") for f in hits}
    assert by["f0"] == ["kitchen", "seren"], "a kitchen fact beside kitchen memories"
    assert by["f1"] is None and by["f2"] is None, "a row that shares no name says nothing"
    assert by["m0"] == ["kitchen"] and by["m1"] == ["kitchen", "seren"] and by["m2"] is None
    plain, _ = _fed(KITCHEN, FACTS)
    assert [f.hit.id for f in hits] == [f.hit.id for f in _ask(plain)] and \
        [f.rrf_score for f in hits] == [f.rrf_score for f in _ask(plain)], "it changes no order and no score"
    alone, _ = _fed(KITCHEN, {"finder": "hybrid", "hits": []})
    assert all("shares" not in f.hit.metadata for f in _ask(alone)), "one store: nobody to share with"


def test_a_name_on_everything_is_learned_and_left_out():
    tally = NameTally(warm=5, share=0.5)
    assert not tally.common("seren"), "nothing is common before the house has been heard"
    tally.note([{"seren", "kitchen"}, {"seren", "loci"}, {"seren", "probe"}, {"seren", "soma"}, {"seren", "margin"}, {"kitchen"}])
    assert tally.common("seren") and not tally.common("kitchen")
    assert tally.usual()["common"] == ["seren"] and tally.usual()["warm"] is True
    fed, _ = _fed(KITCHEN, FACTS, names=tally)
    by = {f.hit.id: f.hit.metadata.get("shares") for f in _ask(fed)}
    assert by["f0"] == ["kitchen"] and by["m1"] == ["kitchen"], "the house's own name no longer counts as agreement"
    small = NameTally(window=2, warm=1, share=0.5)
    small.note([{"a"}, {"b"}, {"b"}])
    assert not small.common("a") and small.common("b"), "it forgets what fell out of the window"


TAGGED = [{"id": "e1", "tags": ["seren-kitchen", "public"], "content": "the day the kitchen went public"},
          {"id": "e2", "tags": ["mcp"], "content": "the night the MCP mount failed"},
          {"id": "e3", "tags": ["door"], "content": "a note about a door"},
          {"id": "e4", "tags": ["cribbage"], "content": "cribbage nights"},
          {"id": "m1", "tags": ["seren-kitchen"], "content": "already in the packet"}]


def _name_edges(hits):
    return [f.hit for f in hits if f.hit.metadata.get("source") == "name-edge"]


def test_a_fact_the_stores_agree_on_brings_the_episodes_tagged_with_its_name():
    off, t0 = _fed(KITCHEN, FACTS, TAGGED)
    assert not _name_edges(_ask(off)) and not t0.asked, "off unless asked for"
    fed, t = _fed(KITCHEN, FACTS, TAGGED, name_budget=2)
    hits = _ask(fed)
    edges = _name_edges(hits)
    assert [e.id for e in edges] == ["e1"], "the kitchen fact's project is a tag; 'mcp' and 'door' were never asked"
    assert all(f.rrf_score == 0.0 for f in hits if f.hit.metadata.get("source") == "name-edge"), "a marked edge, never ranked"
    assert edges[0].metadata["edge_of"] == "f0" and edges[0].metadata["matched_topics"] == ["seren-kitchen"]
    assert len(t.asked) == 1, "the two facts that share nothing with the memories ask nothing: they are not on the subject"
    assert t.asked[0]["topics"] == ["seren-kitchen", "own-mcp-door", "kitchen", "seren"], \
        "its project, its key whole, and the words the stores agree on; not every word of the key"
    assert "m1" in t.asked[0]["exclude_ids"], "never one already in the packet"
    assert [f.hit.id for f in hits if f.rrf_score != 0.0] == [f.hit.id for f in _ask(off) if f.rrf_score != 0.0], "the rows do not move"


def test_a_fact_far_inside_its_stores_habit_asks_by_name_with_nothing_shared():
    # The fact that answers the question while the other store has nothing on the subject: it shares no
    # name with anybody, and it is the one whose story most needs fetching.
    from seren_corpus_callosum.fit import FitTracker
    nothing = _mem(["design", "deploy"])
    facts = _loci([("*", "seren-license"), ("*", "borrow-dont-build-on")])
    facts["hits"][0]["raw_distance"], facts["hits"][1]["raw_distance"] = 0.85, 1.00
    tagged = [{"id": "e1", "tags": ["license", "agpl"], "content": "the night the licence changed"},
              {"id": "e2", "tags": ["build"], "content": "something tagged build that has nothing to do with it"}]
    fit = FitTracker()
    fit.seed("loci", [1.00 + 0.02 * (i % 11 - 5) for i in range(40)])          # usual best 0.99, spread 0.074
    cold, tc = _fed(nothing, facts, tagged, name_budget=2)
    assert not _name_edges(_ask(cold)) and not tc.asked, "no habit yet: nothing is called strong, and nothing is shared"
    t = Canned(nothing, facts, tagged)
    cfg = FederationConfig(stores=[StoreConfig(name="mem", type="seren_memory", url="http://mem"),
                                   StoreConfig(name="loci", type="seren_loci", url="http://loci")],
                           k=60, n_results=10, fetch_multiplier=2, edges_enabled=False,
                           satellite_edges_enabled=False, citation_edges_enabled=False, name_budget=2)
    edges = _name_edges(_ask(Federation(cfg, t, fit=fit)))
    assert [e.id for e in edges] == ["e1"] and edges[0].metadata["edge_of"] == "f0"
    assert len(t.asked) == 1 and t.asked[0]["topics"] == ["seren-license", "license", "seren"], \
        "strong: its key whole and every word of it; '*' is no project"
    # the fact sitting at its store's usual, sharing nothing, asked nothing: 'build' never went out


def test_the_facts_take_turns_and_only_the_first_three_ask():
    shared = _mem(["fact, number"])
    many = _loci([("p", f"fact-number-{i}") for i in range(6)])
    tagged = [{"id": f"a{i}", "tags": ["fact-number-0"], "content": "about the first"} for i in range(3)] + \
             [{"id": f"b{i}", "tags": ["fact-number-1"], "content": "about the second"} for i in range(3)]
    fed, t = _fed(shared, many, tagged, name_budget=3)
    edges = _name_edges(_ask(fed, n=12))
    assert [x["topics"][0] for x in t.asked] == ["fact-number-0", "fact-number-1", "fact-number-2"], "three asks, one a fact"
    assert [e.id for e in edges] == ["a0", "b0", "a1"], "the facts take turns, so one cannot crowd out the rest"
    assert [e.metadata["edge_of"] for e in edges] == ["f0", "f1", "f0"]


def _kitchen_tagged(*rows):
    """(id, tier, distance) in the order a Memory handed the question would send them: nearest first."""
    return [{"id": i, "tags": ["seren-kitchen"], "content": f"{tier} entry {i}", "tier": tier, "dist": d} for i, tier, d in rows]


def test_name_edges_take_one_of_each_tier_the_story_and_the_news():
    # Two this-week notes sit nearest; the settled core is third. Two edges: not the two notes.
    fed, t = _fed(KITCHEN, FACTS, _kitchen_tagged(("s1", "short", 0.40), ("s2", "short", 0.41), ("c1", "long", 0.60)), name_budget=2)
    edges = _name_edges(_ask(fed))
    assert [e.id for e in edges] == ["s1", "c1"], "the nearest, then one from a tier not held yet"
    assert [e.raw_distance for e in edges] == [0.40, 0.60] and [e.metadata["tier"] for e in edges] == ["short", "long"]
    assert t.asked[0]["query"] == "q", "the question goes along, so the store can order what is tagged by it"
    assert t.asked[0]["n_results"] >= 6, "and more is asked for than will ride, so there is a tier to choose from"
    # the settled core nearest: it rides first, and the note after it
    fed2, _ = _fed(KITCHEN, FACTS, _kitchen_tagged(("c1", "long", 0.30), ("s1", "short", 0.50), ("s2", "short", 0.52)), name_budget=2)
    assert [e.id for e in _name_edges(_ask(fed2))] == ["c1", "s1"]
    # a tie in distance: short before long (Chad: "a tie breaker is the db its from, short, near, long")
    fed3, _ = _fed(KITCHEN, FACTS, _kitchen_tagged(("c1", "long", 0.50), ("s1", "short", 0.50)), name_budget=2)
    assert [e.id for e in _name_edges(_ask(fed3))] == ["s1", "c1"]
    # only one tier has anything: its two best
    fed4, _ = _fed(KITCHEN, FACTS, _kitchen_tagged(("s1", "short", 0.40), ("s2", "short", 0.41)), name_budget=2)
    assert [e.id for e in _name_edges(_ask(fed4))] == ["s1", "s2"]
    # one edge: the nearest, whatever its tier
    fed5, _ = _fed(KITCHEN, FACTS, _kitchen_tagged(("s1", "short", 0.40), ("c1", "long", 0.60)), name_budget=1)
    assert [e.id for e in _name_edges(_ask(fed5))] == ["s1"]


def test_a_project_that_is_only_a_common_name_is_not_asked():
    tally = NameTally(warm=5, share=0.5)
    tally.note([{"wren", "x"}] * 6)
    fed, t = _fed(_mem(["nuc, deploy"]), _loci([("wren", "nuc-access")]), [], names=tally, name_budget=1)
    _ask(fed)
    assert t.asked[0]["topics"] == ["nuc-access", "nuc"], "the house's own name tags everything and finds nothing"


def test_the_knob_is_live_on_the_board_and_the_tally_survives_a_configure(tmp_path):
    from fastapi.testclient import TestClient
    from seren_corpus_callosum.app import create_app
    from seren_corpus_callosum.config import _DEFAULT_NAME_BUDGET, load_config
    app = create_app(load_config(str(tmp_path / "nope.yaml")))
    with TestClient(app) as c:
        row = {r["key"]: r for r in c.get("/knobs").json()["federation"]}["name_budget"]
        assert row["default"] == _DEFAULT_NAME_BUDGET == 0 and row["runtime_settable"] is True and row["what"]
        app.state.federation.names.note([{"seren"}] * 300)
        r = c.post("/configure", json={"name_budget": 2})
        assert r.status_code == 200 and r.json()["changed"] == {"name_budget": 2}
        st = c.get("/stores").json()
        assert st["name_budget"] == 2 and "name_budget" in st["supported_knobs"]
        assert st["names"]["rows"] == 300 and st["names"]["common"] == ["seren"], "the rebuild kept what the house had taught it"
        assert c.post("/configure", json={"name_budget": -4}).json()["changed"] == {"name_budget": 0}
