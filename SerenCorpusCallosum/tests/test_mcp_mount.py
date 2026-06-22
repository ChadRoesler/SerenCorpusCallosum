"""
Tests for the SCC MCP mount (seren_corpus_callosum.mcp.server.mount_mcp_routes).

Gated on the `mcp` SDK. Verifies the federation precondition, that a mount
produces a session_manager (the thing app.py's lifespan must run), and that the
/mcp route actually lands on the app.

Run: pytest tests/test_mcp_mount.py   OR   python tests/test_mcp_mount.py
"""
from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("mcp")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI  # noqa: E402

from seren_corpus_callosum.mcp.server import mount_mcp_routes  # noqa: E402


class FakeFederation:
    store_names: list = []
    skipped: list = []

    async def search(self, query, n_results=None):
        return []


def test_mount_requires_federation_on_state():
    app = FastAPI()
    raised = False
    try:
        mount_mcp_routes(app)
    except RuntimeError:
        raised = True
    assert raised, "mount must refuse when app.state.federation is unset"


def test_mount_succeeds_and_exposes_session_manager():
    app = FastAPI()
    app.state.federation = FakeFederation()
    mcp = mount_mcp_routes(app)
    # app.py's lifespan reaches for this to run the transport's task group.
    assert getattr(mcp, "session_manager", None) is not None
    # The /mcp route landed on the app.
    assert any(getattr(r, "path", "").startswith("/mcp") for r in app.routes)


def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} green.")


if __name__ == "__main__":
    _run_all()
