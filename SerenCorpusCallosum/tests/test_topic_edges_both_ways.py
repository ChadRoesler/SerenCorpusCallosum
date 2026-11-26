"""
Topic edges, both ways (26 Nov 2026).

The topic edges asked the stores that keep topics "what else carries the
packet's tags", newest first, and only Memory could answer. Since Loci grew a
lookup by name (/by_name), a fact store answers too: the packet's topics are
names, and the facts filed under them are the facts about what the packet's
episodes are about. Pinned here:

- the Loci adapter asks /by_name and maps what comes back into marked edges
- the stores take turns, so the first store's matches cannot take every seat
- the question goes along to both, so each can order what matched by it
- a topic made only of names that are on everything in the house is not asked
- a Loci from before the door costs nothing: its edges are simply not there
- a fact asking by its own name (a name edge) never asks its own store
"""
from __future__ import annotations

import asyncio
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from seren_corpus_callosum.config import FederationConfig, StoreConfig  # noqa: E402
from seren_corpus_callosum.federation import Federation  # noqa: E402
from seren_corpus_callosum.names import NameTally  # noqa: E402

MEM = {"hits": [{"tier": "long", "content": f"episode {i}", "topic": t, "score": 0.9 - i * 0.01, "raw_distance": 0.4 + i * 0.01,
                 "id": f"m{i}", "metadata": {"kind": "core"}} for i, t in enumerate(["seren, kitchen", "stackchan, soma"])],
       "searched_tiers": ["long"]}
LOCI = {"finder": "hybrid", "hits": [{"id": "f0", "project": "seren-kitchen", "key": "own-mcp-door", "value": "v", "why": None,
                                      "score": 0.9, "match_kind": "hybrid", "raw_distance": 0.9}]}
EPISODES = [{"tier": "short", "content": f"tagged episode {i}", "topic": "kitchen", "matched_topics": ["kitchen"], "overlap": 1,
             "raw_distance": 0.3 + i * 0.1, "id": f"e{i}", "metadata": {}} for i in range(3)]
FACTS = [{"id": f"n{i}", "project": "seren-soma", "key": k, "value": "a fact about the body", "why": "because",
          "score": 0.5, "match_kind": "name", "raw_distance": 0.8, "matched_names": ["soma"]}
         for i, k in enumerate(["soma-build-recipe", "soma-is-the-body"])]


class Canned:
    def __init__(self, no_door: bool = False):
        self.no_door, self.topic_asks, self.name_asks = no_door, [], []

    async def post_json(self, url, payload, headers=None):
        if url.endswith("/by_topic"):
            self.topic_asks.append(payload)
            return {"topics": payload["topics"], "hits": copy.deepcopy(EPISODES), "searched_tiers": ["short"]}
        if url.endswith("/by_name"):
            if self.no_door:
                raise RuntimeError("404: this Loci has no such route")
            self.name_asks.append(payload)
            return {"names": payload["names"], "hits": copy.deepcopy(FACTS), "finder": "hybrid"}
        return copy.deepcopy(MEM if url.startswith("http://mem") else LOCI)


def _fed(t, names=None, **ov):
    cfg = FederationConfig(stores=[StoreConfig(name="mem", type="seren_memory", url="http://mem"),
                                   StoreConfig(name="loci", type="seren_loci", url="http://loci")],
                           k=60, n_results=10, fetch_multiplier=2,
                           satellite_edges_enabled=False, citation_edges_enabled=False, **ov)
    return Federation(cfg, t, names=names)


def _edges(fed, source="topic-edge"):
    return [f.hit for f in asyncio.run(fed.search_report("q")).hits if f.hit.metadata.get("source") == source]


def test_the_stores_take_turns_and_a_fact_store_answers_by_name():
    t = Canned()
    edges = _edges(_fed(t))                                   # topic edges are on by default, three of them
    assert [e.id for e in edges] == ["e0", "n0", "e1"], "an episode, a fact, an episode: nobody takes every seat"
    fact = edges[1]
    assert fact.store == "loci" and fact.content.startswith("soma-build-recipe = a fact about the body")
    assert fact.metadata["matched_topics"] == ["soma"] and fact.metadata["overlap"] == 1 and fact.base_relevance == 0.0
    assert fact.metadata["project"] == "seren-soma" and fact.metadata["key"] == "soma-build-recipe"
    assert t.name_asks[0]["names"] == t.topic_asks[0]["topics"] == ["seren", "kitchen", "stackchan", "soma"]
    assert t.name_asks[0]["query"] == t.topic_asks[0]["query"] == "q", "the question goes to both"
    assert set(t.name_asks[0]["exclude_ids"]) == {"m0", "m1", "f0"}, "never something already in the packet"


def test_a_topic_that_is_only_a_common_name_is_not_asked():
    tally = NameTally(warm=5, share=0.5)
    tally.note([{"seren", "x"}] * 6)
    t = Canned()
    _edges(_fed(t, names=tally))
    assert t.topic_asks[0]["topics"] == ["kitchen", "stackchan", "soma"] == t.name_asks[0]["names"], \
        "the house's own name is on everything and finds everything"


def test_a_loci_from_before_the_door_costs_nothing():
    t = Canned(no_door=True)
    edges = _edges(_fed(t))
    assert [e.id for e in edges] == ["e0", "e1", "e2"] and not t.name_asks, "the episodes ride as they always did"


def test_edges_off_asks_nobody():
    t = Canned()
    assert not _edges(_fed(t, edges_enabled=False)) and not t.topic_asks and not t.name_asks


def test_a_fact_asking_by_its_own_name_never_asks_its_own_store():
    t = Canned()
    edges = _edges(_fed(t, edges_enabled=False, name_budget=2), "name-edge")
    assert t.topic_asks and not t.name_asks, "f0 asked Memory by its name and did not ask Loci for its own siblings"
    assert [e.id for e in edges] == ["e0", "e1"] and all(e.metadata["edge_of"] == "f0" for e in edges)
