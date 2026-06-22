"""
seren_corpus_callosum.app
════════════════════════════════════════════════════════════════════════

The FastAPI application for the corpus callosum. Wires the federation (built
from config), the /search route, optional bearer auth, and — when the [mcp]
extra is installed — an MCP surface so a connected model can call the fan
directly via the `search` tool.

ENDPOINTS:
    GET  /         - service info + the stores it's fanning
    GET  /health   - liveness
    POST /search   - fan across all stores, RRF-merged, ranked

Deliberately parallel to SerenLoci/SerenMemory: same create_app factory, same
lifespan-into-app.state shape, same conditional-MCP-mount with HTTP-only
fallback, same trusted-LAN bearer posture, same public-paths set. The tell of
what THIS service is: there's no store of its own. It owns nothing and
remembers nothing — it only fans, floors, and merges what the hemispheres
hand back. Read-only by construction.
"""
from __future__ import annotations

import hmac
import time
from contextlib import asynccontextmanager, AsyncExitStack
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .config import CorpusCallosumConfig, load_config
from .federation import Federation
from .routes import search as search_routes
from .routes import stores as stores_routes

# The viewer HTML ships inside the package (package-data glob in pyproject).
# Served at GET /viewer; 404s gracefully if it wasn't packaged.
_VIEWER_PATH = Path(__file__).parent / "viewer" / "callosum.html"


# Single source of truth for the reported version: the installed wheel's
# metadata when present, a harmless placeholder for an editable checkout.
# Never let version lookup break startup.
try:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError
    try:
        APP_VERSION = _pkg_version("seren-corpus-callosum")
    except PackageNotFoundError:
        APP_VERSION = "0+unknown"
except Exception:  # noqa: BLE001
    APP_VERSION = "0+unknown"


def create_app(config: CorpusCallosumConfig | None = None, transport=None) -> FastAPI:
    """Build the app. `transport` is injectable so tests can pass a fake
    (real deployments leave it None and get the httpx-backed HttpTransport)."""
    cfg = config or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # -- Startup --
        app.state.config = cfg

        async with AsyncExitStack() as stack:
            # Build the transport. Lazy-import HttpTransport so injecting a fake
            # (tests) never drags in httpx; a real run gets one client held open
            # for the app's lifetime and closed on shutdown.
            tx = transport
            if tx is None:
                from .transport import HttpTransport
                tx = HttpTransport(timeout=cfg.federation.per_store_timeout_s)
            if hasattr(tx, "__aenter__"):
                tx = await stack.enter_async_context(tx)

            federation = Federation(cfg.federation, tx)
            app.state.federation = federation
            print(f"[seren-corpus-callosum] fanning {len(federation.store_names)} "
                  f"store(s): {federation.store_names}")
            if federation.skipped:
                print(f"[seren-corpus-callosum] skipped (unbindable): {federation.skipped}")

            # -- Optional MCP server --
            # Mounted ONLY if the [mcp] extra is installed AND the surface module
            # exists. Same shape as the family: a missing package (or not-yet-
            # written module) falls back to pure-HTTP mode without crashing. When
            # seren_corpus_callosum.mcp.server lands, the `search` tool lights up
            # for free.
            try:
                from .mcp.server import mount_mcp_routes
                mcp_server = mount_mcp_routes(app)
            except ImportError as exc:
                mcp_server = None
                print(f"[seren-corpus-callosum] MCP surface not available; "
                      f"HTTP-only mode ({exc})")
            except Exception as exc:  # noqa: BLE001
                mcp_server = None
                print(f"[seren-corpus-callosum] MCP mount failed: {exc!r} - "
                      f"continuing without MCP")

            # The streamable-HTTP transport needs its session manager's task
            # group entered explicitly — a mounted sub-app's own lifespan doesn't
            # fire under Starlette. (Same fix the rest of the family carries.)
            session_manager = getattr(mcp_server, "session_manager", None)
            if session_manager is not None:
                await stack.enter_async_context(session_manager.run())
                print("[seren-corpus-callosum] MCP session manager running")

            yield

        # -- Shutdown -- (stacks unwound: transport client closed, MCP stopped)
        print("[seren-corpus-callosum] shut down")

    app = FastAPI(
        title="SerenCorpusCallosum",
        description="Read-only N-store memory federation for Seren - the callosum.",
        version=APP_VERSION,
        lifespan=lifespan,
    )

    # -- Optional bearer auth --
    # Same trusted-LAN posture as the rest of Seren: a set token is enforced on
    # everything except the public shell (/, /health); empty = no auth.
    @app.middleware("http")
    async def bearer_auth(request: Request, call_next):
        token = cfg.server.bearer_token
        if token:
            public = request.url.path in ("/", "/health", "/viewer")
            if not public:
                auth = request.headers.get("authorization", "")
                expected = f"Bearer {token}"
                # Constant-time compare so the 401 path doesn't leak how many
                # leading bytes matched. Encode so non-ASCII can't raise.
                if not hmac.compare_digest(auth.encode("utf-8"),
                                           expected.encode("utf-8")):
                    return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    # -- Info routes --
    @app.get("/")
    async def root(request: Request):
        fed = getattr(request.app.state, "federation", None)
        return {
            "service": "SerenCorpusCallosum",
            "version": APP_VERSION,
            "stores": fed.store_names if fed else [],
            "skipped": [{"name": n, "reason": r} for n, r in (fed.skipped if fed else [])],
        }

    @app.get("/health")
    async def health():
        return {"ok": True, "ts": time.time()}

    @app.get("/viewer")
    async def viewer():
        # The Bridge - violet UI, hemisphere-colored results. Public route (the
        # HTML itself needs no auth); its API calls carry the bearer token.
        if _VIEWER_PATH.is_file():
            return HTMLResponse(_VIEWER_PATH.read_text(encoding="utf-8"))
        return JSONResponse(
            {"error": "viewer not packaged with this install"}, status_code=404)

    # -- The fan + introspection --
    app.include_router(search_routes.router)
    app.include_router(stores_routes.router)

    return app
