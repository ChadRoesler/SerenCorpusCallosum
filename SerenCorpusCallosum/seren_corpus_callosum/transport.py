"""
seren_corpus_callosum.transport
════════════════════════════════════════════════════════════════════════

The real async HTTP transport (httpx). Adapters depend on the `Transport`
protocol, not on this concrete class, so tests swap in a fake and this file
only matters when SCC talks to live stores.

httpx is imported lazily inside the constructor so the rest of the package
(config, fusion, adapters mapping logic) imports and tests without httpx
installed. Add httpx to the package's runtime deps when you wire live calls.
"""
from __future__ import annotations

from typing import Any, Optional


class HttpTransport:
    """Async POST-JSON over httpx, with a shared client + sane timeout.

    Use as an async context manager so the connection pool is reused across
    the fan and cleanly closed:

        async with HttpTransport(timeout=5.0) as t:
            fed = Federation(config, t)
            hits = await fed.search("...")
    """

    def __init__(self, timeout: float = 5.0):
        try:
            import httpx  # type: ignore
        except ImportError as e:  # pragma: no cover - import-guard
            raise ImportError(
                "HttpTransport needs httpx. `pip install httpx`, or inject a "
                "custom Transport. (The fake transport in tests needs nothing.)"
            ) from e
        self._httpx = httpx
        self._timeout = timeout
        self._client: Optional[Any] = None

    async def __aenter__(self) -> "HttpTransport":
        self._client = self._httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        # Lazy client so the transport also works without the context-manager
        # form (one-shot client per call - fine for low-traffic homelab use).
        if self._client is None:
            async with self._httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()
