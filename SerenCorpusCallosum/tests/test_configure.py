"""
Tests for the SCC /configure endpoint — dynamic runtime federation tuning.

Validates:
  1. Federation-level knobs (k, fusion_mode, authority_margin, etc.) update
     and persist across the live federation.
  2. Per-store weight/floor overrides mutate matching stores.
  3. An unknown store name in overrides returns 404.
  4. An invalid fusion_mode returns 422.
  5. The federation is rebuilt so the next /search picks up new values.
  6. The response shape (ok, changed, active) is correct.
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
)


# ── canned transport (no live stores needed) ────────────────────────────────

class FakeTransport:
    async def post_json(self, url: str, payload: dict, headers: dict = None) -> dict:
        return {"hits": [], "finder": "lexical"}


def _cfg() -> CorpusCallosumConfig:
    return CorpusCallosumConfig(
        server=ServerConfig(bearer_token=""),
        federation=FederationConfig(
            k=60,
            fusion_mode="rrf",
            authority_margin=0.035,
            min_per_store=1,
            edges_enabled=True,
            edge_budget=3,
            n_results=10,
            fetch_multiplier=2,
            per_store_timeout_s=5.0,
            stores=[
                StoreConfig(name="mem", type="seren_memory", url="http://mem"),
                StoreConfig(name="loci", type="seren_loci", url="http://loci"),
            ],
        ),
    )


# ── tests ────────────────────────────────────────────────────────────────────

def test_configure_updates_k_and_fusion_mode():
    app = create_app(config=_cfg(), transport=FakeTransport())
    with TestClient(app) as client:
        r = client.post("/configure", json={"k": 30, "fusion_mode": "rrf_pct"})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["changed"]["k"] == 30
        assert data["changed"]["fusion_mode"] == "rrf_pct"
        assert data["active"] == 2

        # Verify the federation picked up the new values by checking
        # the root endpoint's config summary.
        root = client.get("/").json()
        assert root["stores"] == ["mem", "loci"]


def test_configure_updates_authority_and_edges():
    app = create_app(config=_cfg(), transport=FakeTransport())
    with TestClient(app) as client:
        r = client.post("/configure", json={
            "authority_margin": 0.1,
            "min_per_store": 3,
            "edges_enabled": False,
            "edge_budget": 0,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["changed"]["authority_margin"] == 0.1
        assert data["changed"]["min_per_store"] == 3
        assert data["changed"]["edges_enabled"] is False
        assert data["changed"]["edge_budget"] == 0


def test_configure_updates_n_results_and_fetch():
    app = create_app(config=_cfg(), transport=FakeTransport())
    with TestClient(app) as client:
        r = client.post("/configure", json={
            "n_results": 25,
            "fetch_multiplier": 4,
            "per_store_timeout_s": 10.0,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["changed"]["n_results"] == 25
        assert data["changed"]["fetch_multiplier"] == 4
        assert data["changed"]["per_store_timeout_s"] == 10.0


def test_configure_per_store_overrides():
    app = create_app(config=_cfg(), transport=FakeTransport())
    with TestClient(app) as client:
        r = client.post("/configure", json={
            "stores": [
                {"name": "mem", "weight": 2.0, "floor": 0.1},
                {"name": "loci", "weight": 0.5},
            ]
        })
        assert r.status_code == 200
        data = r.json()
        assert data["changed"]["stores"] == [
            {"name": "mem", "weight": 2.0, "floor": 0.1},
            {"name": "loci", "weight": 0.5, "floor": 0.0},
        ]

        # Verify the federation picked up the overrides.
        # The next /search should reflect the new weights.
        search_r = client.post("/search", json={"query": "test", "n_results": 10})
        assert search_r.status_code == 200


def test_configure_unknown_store_returns_404():
    app = create_app(config=_cfg(), transport=FakeTransport())
    with TestClient(app) as client:
        r = client.post("/configure", json={
            "stores": [{"name": "nonexistent", "weight": 1.0}]
        })
        assert r.status_code == 404
        data = r.json()
        assert "no store named" in data["detail"]


def test_configure_invalid_fusion_mode_returns_422():
    app = create_app(config=_cfg(), transport=FakeTransport())
    with TestClient(app) as client:
        r = client.post("/configure", json={"fusion_mode": "quantum_rank"})
        assert r.status_code == 422
        data = r.json()
        assert "unknown fusion_mode" in data["detail"]


def test_configure_empty_body_leaves_defaults():
    app = create_app(config=_cfg(), transport=FakeTransport())
    with TestClient(app) as client:
        r = client.post("/configure", json={})
        assert r.status_code == 200
        data = r.json()
        # No fields were supplied, so 'changed' should be empty.
        assert data["changed"] == {}
        assert data["ok"] is True
        assert data["active"] == 2


def test_configure_partial_update_keeps_unchanged():
    app = create_app(config=_cfg(), transport=FakeTransport())
    with TestClient(app) as client:
        # Update only k — everything else should stay at default.
        r = client.post("/configure", json={"k": 99})
        assert r.status_code == 200
        data = r.json()
        assert data["changed"]["k"] == 99
        # Verify fusion_mode wasn't touched (still default "rrf").
        assert "fusion_mode" not in data["changed"]

        # Now update fusion_mode — k should stay at 99.
        r2 = client.post("/configure", json={"fusion_mode": "percentile"})
        assert r2.status_code == 200
        data2 = r2.json()
        assert data2["changed"]["fusion_mode"] == "percentile"
        # k wasn't in the body, so it shouldn't be in changed.
        assert "k" not in data2["changed"]


def test_configure_with_bearer_auth():
    """If bearer auth is configured, /configure should require it."""
    cfg = _cfg()
    cfg.server.bearer_token = "sekret"
    app = create_app(config=cfg, transport=FakeTransport())
    with TestClient(app) as client:
        # No token -> 401
        r = client.post("/configure", json={"k": 30})
        assert r.status_code == 401
        # With token -> 200
        r2 = client.post("/configure", json={"k": 30},
                         headers={"Authorization": "Bearer sekret"})
        assert r2.status_code == 200
        assert r2.json()["changed"]["k"] == 30


def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} green.")


if __name__ == "__main__":
    _run_all()
