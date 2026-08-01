"""Shared test fixtures.

Only one thing lives here so far: the guarantee that the suite never reaches
the network. See offline_update_checks below.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def offline_update_checks(monkeypatch):
    """No test may talk to pypi.org.

    The info route carries the update status and update checking is ON by
    default, so without this every test that touches it would make a real
    network call - slow, flaky offline, and rude to someone else's server.

    Patching the CLASS method rather than an env var is deliberate: some tests
    build a config object directly instead of going through load_config, so an
    env override wouldn't reach them. The checker still runs and still returns
    a well-formed status - just status="error" instead of a real answer, which
    is exactly what a box with no internet would see.
    """
    try:
        from seren_meninges.updates import UpdateChecker
    except ImportError:
        return  # meninges without the updates module, nothing to muzzle

    async def _no_network(self, distribution):
        raise ConnectionError("network disabled in tests")

    # Must be patched BEFORE any UpdateChecker is constructed - __init__ binds
    # self._fetch = fetcher or self._fetch_from_index. autouse + function scope
    # puts it in place before the app lifespan runs.
    monkeypatch.setattr(UpdateChecker, "_fetch_from_index", _no_network)
