"""
Tests for the federation-introspection route (/stores) and the viewer (/viewer).

TestClient + a fake transport, same pattern as test_app. /stores reports each
configured store's bind status; /viewer serves the packaged HTML.

Run: pytest tests/test_stores.py   OR   python tests/test_stores.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

from seren_corpus_callosum.app import create_app  # noqa: E402
from seren_corpus_callosum.config import (  # noqa: E402
    CorpusCallosumConfig, FederationConfig, ServerConfig, StoreConfig,
)


class FakeTransport:
    async def post_json(self, url, payload):
        return {"hits": []}


def _cfg():
    return CorpusCallosumConfig(
        server=ServerConfig(),
        federation=FederationConfig(stores=[
            StoreConfig(name="mem", type="seren_memory", url="http://mem"),
            StoreConfig(name="loci", type="seren_loci", url="http://loci"),
            StoreConfig(name="off", type="seren_memory", url="http://off", enabled=False),
            StoreConfig(name="weird", type="quantum_brain", url="http://weird"),
        ], k=60, n_results=10),
    )


def test_stores_reports_bind_status():
    app = create_app(config=_cfg(), transport=FakeTransport())
    with TestClient(app) as client:
        data = client.get("/stores").json()
        by = {s["name"]: s for s in data["stores"]}
        assert by["mem"]["status"] == "active"
        assert by["loci"]["status"] == "active"
        assert by["off"]["status"] == "disabled"
        assert by["weird"]["status"].startswith("skipped")
        assert data["active"] == 2          # mem + loci bound
        assert data["k"] == 60 and data["n_results"] == 10
        # provenance/merge knobs surfaced for the UI
        assert by["mem"]["type"] == "seren_memory" and "weight" in by["mem"]


def test_viewer_serves_html():
    app = create_app(config=_cfg(), transport=FakeTransport())
    with TestClient(app) as client:
        r = client.get("/viewer")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        body = r.text
        assert "The Bridge" in body and "SerenCorpusCallosum" in body


def test_viewer_is_public_but_stores_is_gated():
    cfg = _cfg(); cfg.server.bearer_token = "sekret"
    app = create_app(config=cfg, transport=FakeTransport())
    with TestClient(app) as client:
        assert client.get("/viewer").status_code == 200          # public HTML
        assert client.get("/stores").status_code == 401          # gated data
        ok = client.get("/stores", headers={"Authorization": "Bearer sekret"})
        assert ok.status_code == 200


def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} green.")


if __name__ == "__main__":
    _run_all()
