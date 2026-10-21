"""
GET /knobs and the viewer's Knobs tab (20 Oct 2026). The ask: "a way to view
what the settings for stores, the knobs that get twiddled, so it's easy for
someone who isn't me to be able to read off what they are without having to
dig."

Pinned here:
- every federation knob comes back with live, yaml, default and a sentence;
  a knob POST /configure changed reads as twiddled (live != yaml), one the
  yaml set reads as not default
- a store's bearer is reported by where it comes from, never by value
- no yaml on disk: yaml is null and nothing is called twiddled
- the viewer serves the tab

Run: pytest tests/test_knobs.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

from seren_corpus_callosum.app import create_app  # noqa: E402
from seren_corpus_callosum.config import load_config  # noqa: E402


class FakeTransport:
    async def post_json(self, url, payload, headers=None):
        return {"hits": []}


YAML = """
server:
  port: 7423
federation:
  k: 40
  hops: 2
  stores:
    - name: mem
      type: seren_memory
      url: http://mem
      weight: 1.5
      bearer_token: hunter2
    - name: loci
      type: seren_loci
      url: http://loci
      floor: 0.3
      bearer_token_env: LOCI_TOKEN
"""


def _client(tmp_path, text=YAML):
    p = tmp_path / "seren-corpus-callosum.yaml"
    p.write_text(text, encoding="utf-8")
    cfg = load_config(str(p))
    app = create_app(cfg)
    app.state.transport = FakeTransport()
    return TestClient(app)


def test_every_knob_has_its_three_values_and_a_sentence(tmp_path):
    with _client(tmp_path) as c:
        out = c.get("/knobs").json()
        assert out["ok"] and out["where"]["config_exists"] is True
        by = {r["key"]: r for r in out["federation"]}
        assert by["k"]["live"] == 40 and by["k"]["yaml"] == 40 and by["k"]["default"] == 60
        assert by["k"]["is_default"] is False and by["k"]["twiddled"] is False
        assert by["hops"]["live"] == 2 and by["hops"]["runtime_settable"] is True
        assert by["n_results"]["is_default"] is True
        for r in out["federation"]:
            assert r["what"] and r["what"][0].isupper(), r["key"]
        assert "yaml is what comes back after a restart" in out["note"]


def test_a_configure_twiddle_shows_as_live_not_yaml(tmp_path):
    with _client(tmp_path) as c:
        assert c.post("/configure", json={"k": 25}).status_code == 200
        by = {r["key"]: r for r in c.get("/knobs").json()["federation"]}
        assert by["k"]["live"] == 25 and by["k"]["yaml"] == 40 and by["k"]["twiddled"] is True


def test_stores_say_where_their_bearer_comes_from_and_never_what_it_is(tmp_path):
    with _client(tmp_path) as c:
        out = c.get("/knobs").json()
        assert "hunter2" not in c.get("/knobs").text
        st = {s["name"]: s for s in out["stores"]}
        assert st["mem"]["auth"] == "inline" and st["loci"]["auth"] == "env $LOCI_TOKEN"
        assert st["mem"]["where"] == "yaml"
        w = {r["key"]: r for r in st["mem"]["knobs"]}["weight"]
        assert w["live"] == 1.5 and w["yaml"] == 1.5 and w["is_default"] is False and w["twiddled"] is False
        f = {r["key"]: r for r in st["loci"]["knobs"]}["floor"]
        assert f["live"] == 0.3 and f["what"].startswith("Hits under this base relevance")


def test_without_a_yaml_nothing_is_twiddled_and_the_default_is_still_said(tmp_path):
    cfg = load_config(str(tmp_path / "nope.yaml"))
    app = create_app(cfg)
    app.state.transport = FakeTransport()
    with TestClient(app) as c:
        out = c.get("/knobs").json()
        assert out["where"]["config_exists"] is False
        by = {r["key"]: r for r in out["federation"]}
        assert by["k"]["yaml"] is None and by["k"]["twiddled"] is False and by["k"]["default"] == 60


def test_the_viewer_serves_the_knobs_tab(tmp_path):
    with _client(tmp_path) as c:
        html = c.get("/viewer").text
        assert 'data-tab="knobs"' in html and 'id="knobs"' in html and "loadKnobs" in html
