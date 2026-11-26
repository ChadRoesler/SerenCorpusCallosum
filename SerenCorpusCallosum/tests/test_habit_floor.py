"""
A store may set rows aside (options.habit_floor, 25 Nov 2026).

A store returns its top rows whether or not it has anything to say; on one real
page six of twelve rows were facts unrelated to the question. Moving seats
between stores did not fix it (test_tilt.py). This judges each ROW against its
own store's habit: one sitting more than habit_floor spreads past the store's
usual best hit is set aside. Pinned here:

- said nothing, nothing is set aside, habit or no habit
- rows inside the habit lead in the turns RRF gives them; rows set aside follow,
  and still ride when the packet has room; the scores fall from top to bottom
  and none is 0.0 (that marks an edge); a row still says its own store rank and
  a set-aside one says how far out it sat
- a store's best row is never set aside, so a store with nothing near still
  shows its best
- a store with no habit yet sets nothing aside; a row with no distance stays
- only the store that asked is judged
- a page packs the same order
- the option is settable live per store, and 0 turns it off
"""
from __future__ import annotations

import asyncio
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from seren_corpus_callosum.config import FederationConfig, StoreConfig  # noqa: E402
from seren_corpus_callosum.federation import Federation  # noqa: E402
from seren_corpus_callosum.fit import FitTracker  # noqa: E402

MEM_USUAL = [0.50 + 0.01 * (i % 11 - 5) for i in range(40)]      # near 0.50, spread about 0.04
LOCI_USUAL = [1.00 + 0.02 * (i % 11 - 5) for i in range(40)]     # near 1.00, spread about 0.09


def _mem(best: float = 0.50, n: int = 12) -> dict:
    return {"hits": [{"tier": "long", "content": f"episode {i:02}: what happened that night on the deck", "topic": "t",
                      "score": 0.9 - i * 0.01, "raw_distance": best + i * 0.01, "id": f"m{i:02}",
                      "metadata": {"kind": "core"}} for i in range(n)], "searched_tiers": ["long"]}


def _loci(best: float = 1.00, n: int = 12, step: float = 0.02) -> dict:
    return {"finder": "hybrid", "hits": [{"id": f"f{i:02}", "project": "w", "key": f"fact.{i:02}",
                                          "value": "a thing the house holds to be true", "why": None, "score": 0.95,
                                          "match_kind": "hybrid", "raw_distance": best + i * step} for i in range(n)]}


class Canned:
    def __init__(self, mem: dict, loci: dict):
        self.mem, self.loci = mem, loci

    async def post_json(self, url, payload, headers=None):
        return copy.deepcopy(self.mem if url.startswith("http://mem") else self.loci)


def _fed(mem: dict, loci: dict, loci_floor: float | None = 1.1, mem_floor: float | None = None,
         habit: bool = True, **ov) -> Federation:
    opts = lambda f: {} if f is None else {"habit_floor": f}   # noqa: E731
    cfg = FederationConfig(stores=[StoreConfig(name="mem", type="seren_memory", url="http://mem", options=opts(mem_floor)),
                                   StoreConfig(name="loci", type="seren_loci", url="http://loci", options=opts(loci_floor))],
                           k=60, n_results=10, fetch_multiplier=4, edges_enabled=False,
                           satellite_edges_enabled=False, citation_edges_enabled=False, **ov)
    fit = FitTracker()
    if habit:
        fit.seed("mem", MEM_USUAL)
        fit.seed("loci", LOCI_USUAL)
    return Federation(cfg, Canned(mem, loci), fit=fit)


def _ask(fed, n=None, budget=None):
    return asyncio.run(fed.search_report("q", n_results=n, budget_tokens=budget)).hits


def _ids(hits):
    return [f.hit.id for f in hits]


def test_said_nothing_nothing_is_set_aside():
    plain = _ask(_fed(_mem(), _loci(), loci_floor=None, habit=False), n=24)
    warm = _ask(_fed(_mem(), _loci(), loci_floor=None), n=24)
    assert _ids(warm) == _ids(plain) and [f.rrf_score for f in warm] == [f.rrf_score for f in plain], "to the digit"
    assert _ids(plain)[:4] == ["m00", "f00", "m01", "f01"]
    assert not any("past_usual" in f.hit.metadata for f in warm)


def test_rows_inside_the_habit_lead_and_the_rest_follow():
    # Loci's usual best is 0.99 with a spread of 0.074, and its rows step out by 0.02
    # a rank: ranks 1 to 4 sit 0.13 to 0.94 spreads out (inside a floor of 1.1),
    # ranks 5 to 12 sit 1.21 and further (past it).
    hits = _ask(_fed(_mem(), _loci()), n=24)
    assert _ids(hits)[:8] == ["m00", "f00", "m01", "f01", "m02", "f02", "m03", "f03"], "the lead takes its turns"
    assert _ids(hits)[8:16] == [f"m{i:02}" for i in range(4, 12)], "then Memory has the floor to itself"
    assert _ids(hits)[16:] == [f"f{i:02}" for i in range(4, 12)], "and the rows set aside still ride, last"
    assert all(a.rrf_score >= b.rrf_score for a, b in zip(hits, hits[1:])) and all(f.rrf_score > 0 for f in hits)
    assert all(f.store_rank == int(f.hit.id[1:]) + 1 for f in hits), "a row still says its own rank in its store"
    aside = [f for f in hits if "past_usual" in f.hit.metadata]
    assert _ids(aside) == [f"f{i:02}" for i in range(4, 12)] and all(f.hit.metadata["past_usual"] > 1.1 for f in aside)
    short = _ask(_fed(_mem(), _loci()), n=14)
    assert [i for i in _ids(short) if i.startswith("f")] == ["f00", "f01", "f02", "f03"], "no room, no ride"


def test_a_store_with_nothing_near_still_shows_its_best_row():
    hits = _ask(_fed(_mem(), _loci(best=1.40)), n=10)      # every Loci row far past its usual
    assert _ids(hits)[:3] == ["m00", "f00", "m01"] and _ids(hits).count("f00") == 1
    assert [i for i in _ids(hits) if i.startswith("f")] == ["f00"], "its best row, in its usual seat, and no other"
    assert "past_usual" not in hits[1].hit.metadata


def test_no_habit_yet_and_no_distance_set_nothing_aside():
    cold = _ask(_fed(_mem(), _loci(best=1.40), habit=False), n=10)
    assert _ids(cold)[:4] == ["m00", "f00", "m01", "f01"]
    blind = _loci(best=1.40)
    for h in blind["hits"][1:4]:
        h["raw_distance"] = None                            # an exact key match with no vector behind it
    hits = _ask(_fed(_mem(), blind), n=10)
    assert [i for i in _ids(hits) if i.startswith("f")] == ["f00", "f01", "f02", "f03"], "what cannot be judged stays"


def test_only_the_store_that_asked_is_judged():
    far_mem = _mem(best=0.70)                               # Memory far past its own usual, and it did not ask
    hits = _ask(_fed(far_mem, _loci(), loci_floor=None), n=10)
    assert _ids(hits)[:4] == ["m00", "f00", "m01", "f01"]
    both = _ask(_fed(far_mem, _loci(), loci_floor=None, mem_floor=1.0), n=10)
    assert [i for i in _ids(both) if i.startswith("m")] == ["m00"], "when it does ask, it is"


def test_a_page_packs_the_same_order():
    small = _ids(_ask(_fed(_mem(), _loci()), budget=150))
    even = _ids(_ask(_fed(_mem(), _loci(), loci_floor=None), budget=150))
    assert small[:8] == ["m00", "f00", "m01", "f01", "m02", "f02", "m03", "f03"]
    assert [i for i in small if i.startswith("f")] == ["f00", "f01", "f02", "f03"], "a full page: the set-aside rows do not ride"
    assert sum(i.startswith("m") for i in small) > sum(i.startswith("m") for i in even), "the room they left went to Memory"
    roomy = _ids(_ask(_fed(_mem(), _loci()), budget=4000))
    assert len(roomy) == 24 and roomy[16:] == [f"f{i:02}" for i in range(4, 12)], "a page with room: they ride, last"


def test_the_habit_learns_from_each_question_after_it_is_read():
    fed = _fed(_mem(), _loci(), loci_floor=None, habit=False)
    _ask(fed, n=10)
    u = fed.fit.usual()
    assert u["mem"]["asked"] == 1 and u["loci"]["asked"] == 1 and not u["mem"]["warm"]
    assert abs(u["mem"]["usual"] - 0.50) < 1e-9 and abs(u["loci"]["usual"] - 1.00) < 1e-9, "each store's best hit, in its own unit"
    one = FitTracker(warm=1)
    assert one.closeness("mem", 0.3) is None, "nothing to hold the first question against"
    one.note("mem", 0.3)
    assert one.closeness("mem", 0.3) == 0.0, "a store that only ever answers the same says nothing by this"
    assert one.closeness("mem", None) is None and one.closeness("nobody", 0.3) is None
    warm = FitTracker()
    warm.seed("loci", LOCI_USUAL)
    assert warm.closeness("loci", 0.80) > 0 > warm.closeness("loci", 1.20), "closer than usual is positive"
    assert warm.closeness("loci", 9.0) == -3.0 and warm.closeness("loci", -9.0) == 3.0, "held to three spreads"


def test_it_is_settable_live_per_store(tmp_path):
    from fastapi.testclient import TestClient
    from seren_corpus_callosum.app import create_app
    from seren_corpus_callosum.config import StoreConfig as SC, load_config
    cfg = load_config(str(tmp_path / "nope.yaml"))
    cfg.federation.stores = [SC(name="loci", type="seren_loci", url="http://loci")]
    app = create_app(cfg)
    with TestClient(app) as c:
        app.state.federation.fit.seed("loci", LOCI_USUAL)
        r = c.post("/configure", json={"stores": [{"name": "loci", "habit_floor": 1.5}]})
        assert r.status_code == 200 and r.json()["changed"]["stores"][0]["habit_floor"] == 1.5
        assert app.state.federation._stores[0].habit_floor == 1.5
        assert app.state.federation.fit.usual()["loci"]["asked"] == len(LOCI_USUAL), "the rebuild kept what was learned"
        st = c.get("/stores").json()
        assert st["fit"]["loci"]["warm"] is True, "and a person can read each store's habit"
        assert "tilt" not in st and "tilt" not in st["supported_knobs"], "the tilt was tried and taken out (fit.py)"
        r = c.post("/configure", json={"stores": [{"name": "loci", "habit_floor": 0}]})
        assert r.json()["changed"]["stores"][0]["habit_floor"] == 0.0 and app.state.federation._stores[0].habit_floor == 0.0
        r = c.post("/configure", json={"stores": [{"name": "loci", "weight": 2.0}]})
        assert "habit_floor" not in r.json()["changed"]["stores"][0], "a row says it only when it was the thing set"
