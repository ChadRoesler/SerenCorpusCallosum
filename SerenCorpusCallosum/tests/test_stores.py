"""
Tests for the federation roster route (/stores), the viewer (/viewer), and the
add/remove-store management surface (POST/DELETE /stores).

TestClient + a fake transport, same pattern as test_app. Add/remove tests point
runtime_stores_path at a temp overlay so persistence is exercised for real
without touching any shared file.

Run: pytest tests/test_stores.py   OR   python tests/test_stores.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

from seren_corpus_callosum.app import create_app  # noqa: E402
from seren_corpus_callosum.config import (  # noqa: E402
    CorpusCallosumConfig, FederationConfig, ServerConfig, StoreConfig,
)
from seren_corpus_callosum.overlay import load_overlay  # noqa: E402


class FakeTransport:
    async def post_json(self, url, payload):
        return {"hits": []}


def _cfg(bearer="", overlay=None):
    return CorpusCallosumConfig(
        server=ServerConfig(bearer_token=bearer),
        federation=FederationConfig(stores=[
            StoreConfig(name="mem", type="seren_memory", url="http://mem"),
            StoreConfig(name="loci", type="seren_loci", url="http://loci"),
            StoreConfig(name="off", type="seren_memory", url="http://off", enabled=False),
            StoreConfig(name="weird", type="quantum_brain", url="http://weird"),
        ], k=60, n_results=10),
        runtime_stores_path=overlay,
    )


def _overlay():
    return os.path.join(tempfile.mkdtemp(), "runtime-stores.json")


# -- read --------------------------------------------------------------------
def test_stores_reports_bind_status():
    app = create_app(config=_cfg(), transport=FakeTransport())
    with TestClient(app) as client:
        data = client.get("/stores").json()
        by = {s["name"]: s for s in data["stores"]}
        assert by["mem"]["status"] == "active"
        assert by["loci"]["status"] == "active"
        assert by["off"]["status"] == "disabled"
        assert by["weird"]["status"].startswith("skipped")
        assert data["active"] == 2
        assert data["k"] == 60 and data["n_results"] == 10
        assert by["mem"]["type"] == "seren_memory" and "weight" in by["mem"]
        assert by["mem"]["managed"] is False
        assert "seren_memory" in data["types"] and "seren_loci" in data["types"]


def test_viewer_serves_html():
    app = create_app(config=_cfg(), transport=FakeTransport())
    with TestClient(app) as client:
        r = client.get("/viewer")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "The Bridge" in r.text and "SerenCorpusCallosum" in r.text


def test_viewer_is_public_but_stores_is_gated():
    app = create_app(config=_cfg(bearer="sekret"), transport=FakeTransport())
    with TestClient(app) as client:
        assert client.get("/viewer").status_code == 200
        assert client.get("/stores").status_code == 401
        ok = client.get("/stores", headers={"Authorization": "Bearer sekret"})
        assert ok.status_code == 200


# -- add ---------------------------------------------------------------------
def test_add_store_fans_live_and_persists():
    ov = _overlay()
    app = create_app(config=_cfg(overlay=ov), transport=FakeTransport())
    with TestClient(app) as client:
        r = client.post("/stores", json={"name": "extra", "type": "seren_memory", "url": "http://extra/"})
        assert r.status_code == 200
        assert r.json()["active"] == 3              # mem + loci + extra, fanned live
        # persisted to the overlay (url normalized, trailing slash stripped)
        ov_stores = load_overlay(ov)
        assert [s["name"] for s in ov_stores] == ["extra"]
        assert ov_stores[0]["url"] == "http://extra"
        # shows up managed + active in the roster
        by = {s["name"]: s for s in client.get("/stores").json()["stores"]}
        assert by["extra"]["managed"] is True and by["extra"]["status"] == "active"


def test_add_unknown_type_rejected():
    app = create_app(config=_cfg(overlay=_overlay()), transport=FakeTransport())
    with TestClient(app) as client:
        r = client.post("/stores", json={"name": "n", "type": "no_such_type", "url": "http://n"})
        assert r.status_code == 400


def test_add_duplicate_rejected():
    app = create_app(config=_cfg(overlay=_overlay()), transport=FakeTransport())
    with TestClient(app) as client:
        r = client.post("/stores", json={"name": "mem", "type": "seren_memory", "url": "http://dup"})
        assert r.status_code == 409


def test_add_blank_fields_rejected():
    app = create_app(config=_cfg(overlay=_overlay()), transport=FakeTransport())
    with TestClient(app) as client:
        assert client.post("/stores", json={"name": "  ", "type": "seren_memory", "url": "http://x"}).status_code == 400
        assert client.post("/stores", json={"name": "x", "type": "seren_memory", "url": "  "}).status_code == 400


# -- remove ------------------------------------------------------------------
def test_delete_managed_store():
    ov = _overlay()
    app = create_app(config=_cfg(overlay=ov), transport=FakeTransport())
    with TestClient(app) as client:
        client.post("/stores", json={"name": "extra", "type": "seren_loci", "url": "http://extra"})
        r = client.delete("/stores/extra")
        assert r.status_code == 200 and r.json()["active"] == 2
        assert load_overlay(ov) == []
        assert "extra" not in {s["name"] for s in client.get("/stores").json()["stores"]}


def test_delete_base_store_refused():
    app = create_app(config=_cfg(overlay=_overlay()), transport=FakeTransport())
    with TestClient(app) as client:
        r = client.delete("/stores/mem")           # base/config store
        assert r.status_code == 400


def test_delete_missing_404():
    app = create_app(config=_cfg(overlay=_overlay()), transport=FakeTransport())
    with TestClient(app) as client:
        assert client.delete("/stores/ghost").status_code == 404


def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} green.")


if __name__ == "__main__":
    _run_all()
