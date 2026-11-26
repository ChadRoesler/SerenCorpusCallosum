"""
Pointers on the docket (25 Nov 2026): what does not ride in full is at least named.

Chad: "if content is cheap to carry lets carry it, if its not then... we go to
ids, only cause a second look up for an id may be good to dredge through". The
content half already existed (a core's episodes and a fact's reasons ride as
marked edges, within what the packet has room for). Pinned here, the id half:

- an episode that hangs on other cores too says which, on its satellite edge,
  when Memory says so (also_core_ids); one with a single home carries no such key
- a fact names every fact it rests on by address (rests_on_keys), whether that
  reason rides in full or not, and still says how many there are
- a row that rests on nothing, and a Memory that sends no such ids, read as before
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import test_satellite_room as room  # noqa: E402


def test_an_episode_on_two_cores_says_which_other_core():
    mem = room._cores(sats={0: 2})
    mem["hits"][0]["surroundings"]["recent"][0]["also_core_ids"] = ["c07", "c09"]
    edges = room._edges(room._search(mem))
    assert [e.id for e in edges] == ["s00.0", "s00.1"]
    assert edges[0].metadata["edge_of"] == "c00" and edges[0].metadata["also_core_ids"] == ["c07", "c09"]
    assert "also_core_ids" not in edges[1].metadata, "one home: nothing to point at"
    page = room._edges(room._search(mem, room.EIGHT, satellite_share=0.3))
    assert page[0].metadata["also_core_ids"] == ["c07", "c09"], "the same on a page"


def test_a_memory_that_sends_no_such_ids_reads_as_before():
    edges = room._edges(room._search(room._cores(sats={0: 2})))
    assert all("also_core_ids" not in e.metadata for e in edges)
    assert set(edges[0].metadata) == {"tier", "kind", "core_id", "superseded_by", "topic", "created_at", "source", "edge_of"}


def test_a_fact_names_everything_it_rests_on():
    fused = room._search(room._cores(), loci=room.LOCI)
    fact = next(f.hit for f in fused if f.hit.id == "f0")
    assert fact.metadata["rests_on_n"] == 6
    assert fact.metadata["rests_on_keys"] == [f"w/reason.{k}" for k in range(6)], "all six, by address"
    assert "rests_on" not in fact.metadata, "the content still comes off the row"
    riding = room._edges(fused, "citation-edge")
    assert [e.id for e in riding] == ["r0", "r1", "r2"], "three ride in full; the other three are named and no more"
    core = next(f.hit for f in fused if f.hit.id == "c00")
    assert "rests_on_keys" not in core.metadata and "rests_on_n" not in core.metadata, "a row that rests on nothing says nothing"
