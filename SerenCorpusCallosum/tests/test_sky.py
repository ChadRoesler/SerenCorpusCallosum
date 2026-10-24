"""
GET /sky (23 Oct 2026): both hemispheres in one picture for the viewer's Sky.

Pinned here:
- the city comes from the first Loci store (GET /city), the nebula from the
  first Memory store (GET /long/map), each with that store's own bearer
- a store that cannot be reached leaves its half empty and says why; a kind
  with no store says so; a transport that cannot GET says so
- the Sky's 3D library is served under /viewer/ (public like the page), and
  nothing else from there; /sky itself is behind the token
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

from seren_corpus_callosum.app import create_app  # noqa: E402
from seren_corpus_callosum.config import (  # noqa: E402
    CorpusCallosumConfig, FederationConfig, ServerConfig, StoreConfig)

CITY = {"towers": [{"project": "*", "floors": []}], "links": [], "counts": {"towers": 1, "floors": 0, "skyways": 0, "recheck": 0}}
NEBULA = {"nodes": [], "links": [], "counts": {"cores": 0, "satellites": 0, "bridges": 0}}


class MapTransport:
    def __init__(self, by_url):
        self.by_url, self.seen = by_url, []

    async def post_json(self, url, payload, headers=None):
        return {"hits": []}

    async def get_json(self, url, headers=None, timeout=None):
        self.seen.append((url, headers, timeout))
        v = self.by_url.get(url)
        if v is None:
            raise RuntimeError("refused")
        return v


class PostOnly:
    async def post_json(self, url, payload, headers=None):
        return {"hits": []}


def _cfg(stores, bearer=""):
    return CorpusCallosumConfig(server=ServerConfig(bearer_token=bearer), federation=FederationConfig(stores=stores))


STORES = [StoreConfig(name="mem", type="seren_memory", url="http://mem", token="m-tok"),
          StoreConfig(name="loci", type="seren_loci", url="http://loci", token="l-tok")]


def test_the_sky_has_the_city_below_and_the_nebula_above():
    tx = MapTransport({"http://loci/city": CITY, "http://mem/long/map": NEBULA})
    with TestClient(create_app(_cfg(STORES), transport=tx)) as c:
        out = c.get("/sky").json()
    assert out["city"] == CITY and out["nebula"] == NEBULA
    assert out["from"] == {"city": "loci", "nebula": "mem"} and out["failed"] == {}
    heads = {u: h for u, h, _ in tx.seen}
    assert heads["http://loci/city"] == {"Authorization": "Bearer l-tok"}, "each store's own bearer"
    assert heads["http://mem/long/map"] == {"Authorization": "Bearer m-tok"}


def test_half_a_sky_says_why():
    tx = MapTransport({"http://loci/city": CITY})
    with TestClient(create_app(_cfg(STORES), transport=tx)) as c:
        out = c.get("/sky").json()
    assert out["city"] == CITY and out["nebula"] is None
    assert out["failed"]["nebula"].startswith("mem: RuntimeError")
    with TestClient(create_app(_cfg(STORES[1:]), transport=tx)) as c:
        assert c.get("/sky").json()["failed"]["nebula"] == "no store of that kind is fanned"
    with TestClient(create_app(_cfg(STORES), transport=PostOnly())) as c:
        assert c.get("/sky").json()["failed"]["city"] == "this transport cannot fetch maps"


def test_the_skys_library_is_served_and_the_sky_is_behind_the_token():
    with TestClient(create_app(_cfg(STORES, bearer="sekret"), transport=PostOnly())) as c:
        h = {"Authorization": "Bearer sekret"}
        r = c.get("/viewer/vendor/three.min.js")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript"), \
            "a library, public like the page under /viewer/"
        assert c.get("/viewer/vendor/other.js", headers=h).status_code == 404
        assert c.get("/sky").status_code == 401
        assert 'data-tab="sky"' in c.get("/viewer").text
