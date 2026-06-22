"""
Tests for the SCC service shell (app + /search route + config.load_config).

Uses FastAPI's TestClient with a fake transport injected into create_app, so
the whole HTTP path — lifespan, federation build, route, response shape, auth
— runs for real without any live store.

Run: pytest tests/test_app.py   OR   python tests/test_app.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

from seren_corpus_callosum.app import create_app  # noqa: E402
from seren_corpus_callosum.config import (  # noqa: E402
    CorpusCallosumConfig,
    FederationConfig,
    ServerConfig,
    StoreConfig,
    load_config,
)


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
    def __init__(self, by_url: dict):
        self.by_url = by_url

    async def post_json(self, url: str, payload: dict) -> dict:
        v = self.by_url.get(url)
        if v is None:
            raise RuntimeError(f"no canned response for {url}")
        return v


def _canned() -> FakeTransport:
    return FakeTransport({"http://mem/search": MEM_RESP, "http://loci/search": LOCI_RESP})


def _cfg(bearer: str = "") -> CorpusCallosumConfig:
    return CorpusCallosumConfig(
        server=ServerConfig(bearer_token=bearer),
        federation=FederationConfig(stores=[
            StoreConfig(name="mem", type="seren_memory", url="http://mem"),
            StoreConfig(name="loci", type="seren_loci", url="http://loci"),
        ], k=60),
    )


def test_search_route_fans_and_returns_provenance():
    app = create_app(config=_cfg(), transport=_canned())
    with TestClient(app) as client:
        r = client.post("/search", json={"query": "q", "n_results": 10})
        assert r.status_code == 200
        data = r.json()
        assert [f"{h['store']}:{h['id']}" for h in data["hits"]] == \
            ["mem:m1", "loci:l1", "mem:m2", "loci:l2"]
        assert data["stores_searched"] == ["mem", "loci"]
        h0 = data["hits"][0]
        # Full provenance on the wire.
        for field in ("store", "id", "content", "score", "store_rank",
                      "base_relevance", "native_score", "raw_distance", "metadata"):
            assert field in h0
        assert h0["store_rank"] == 1


def test_health_and_root():
    app = create_app(config=_cfg(), transport=_canned())
    with TestClient(app) as client:
        assert client.get("/health").json()["ok"] is True
        root = client.get("/").json()
        assert root["service"] == "SerenCorpusCallosum"
        assert root["stores"] == ["mem", "loci"]


def test_bearer_auth_enforced_but_health_public():
    app = create_app(config=_cfg(bearer="sekret"), transport=_canned())
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200          # public
        assert client.post("/search", json={"query": "q"}).status_code == 401  # needs token
        ok = client.post("/search", json={"query": "q"},
                         headers={"Authorization": "Bearer sekret"})
        assert ok.status_code == 200


def test_unknown_store_surfaces_in_root_and_search_still_works():
    cfg = CorpusCallosumConfig(federation=FederationConfig(stores=[
        StoreConfig(name="mem", type="seren_memory", url="http://mem"),
        StoreConfig(name="weird", type="quantum_brain", url="http://weird"),
    ]))
    app = create_app(config=cfg, transport=_canned())
    with TestClient(app) as client:
        root = client.get("/").json()
        assert root["stores"] == ["mem"]
        assert root["skipped"] and root["skipped"][0]["name"] == "weird"
        data = client.post("/search", json={"query": "q"}).json()
        assert [f"{h['store']}:{h['id']}" for h in data["hits"]] == ["mem:m1", "mem:m2"]


def test_load_config_defaults_and_env(monkeypatch=None):
    # No file, no env -> family defaults (port 7423, empty federation).
    cfg = load_config(path="/nonexistent/seren-corpus-callosum.yaml")
    assert cfg.server.port == 7423
    assert cfg.server.host == "0.0.0.0"
    assert cfg.federation.stores == []
    # Env override wins.
    os.environ["SEREN_SCC_PORT"] = "9999"
    try:
        cfg2 = load_config(path="/nonexistent/seren-corpus-callosum.yaml")
        assert cfg2.server.port == 9999
    finally:
        del os.environ["SEREN_SCC_PORT"]


def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} green.")


if __name__ == "__main__":
    _run_all()
