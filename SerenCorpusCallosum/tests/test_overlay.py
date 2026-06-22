"""
Tests for the runtime overlay (seren_corpus_callosum.overlay).

The overlay is the machine-managed JSON file UI-added stores live in, so the
hand-authored yaml stays pristine. These cover the round-trip + the degrade-
never-crash reads.

Run: pytest tests/test_overlay.py   OR   python tests/test_overlay.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from seren_corpus_callosum.overlay import (  # noqa: E402
    add_to_overlay, load_overlay, overlay_path_for, remove_from_overlay, save_overlay,
)


def _tmp():
    d = tempfile.mkdtemp()
    return os.path.join(d, "runtime-stores.json")


def test_missing_overlay_is_empty():
    assert load_overlay("/nonexistent/runtime-stores.json") == []


def test_add_load_remove_roundtrip():
    p = _tmp()
    add_to_overlay(p, {"name": "x", "type": "seren_memory", "url": "http://x"})
    add_to_overlay(p, {"name": "y", "type": "seren_loci", "url": "http://y"})
    names = [s["name"] for s in load_overlay(p)]
    assert names == ["x", "y"]
    assert remove_from_overlay(p, "x") is True
    assert [s["name"] for s in load_overlay(p)] == ["y"]
    assert remove_from_overlay(p, "nope") is False    # not there -> False


def test_add_replaces_by_name():
    p = _tmp()
    add_to_overlay(p, {"name": "x", "type": "seren_memory", "url": "http://old"})
    add_to_overlay(p, {"name": "x", "type": "seren_memory", "url": "http://new"})
    stores = load_overlay(p)
    assert len(stores) == 1 and stores[0]["url"] == "http://new"


def test_corrupt_overlay_degrades_to_empty():
    p = _tmp()
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("{ this is not json")
    assert load_overlay(p) == []        # never raises, never blocks startup


def test_overlay_path_env_override():
    os.environ["SEREN_SCC_RUNTIME_STORES"] = "/tmp/custom-overlay.json"
    try:
        assert str(overlay_path_for("anywhere.yaml")) == "/tmp/custom-overlay.json"
    finally:
        del os.environ["SEREN_SCC_RUNTIME_STORES"]
    assert overlay_path_for("/etc/seren/seren-corpus-callosum.yaml").name == "runtime-stores.json"


def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} green.")


if __name__ == "__main__":
    _run_all()
