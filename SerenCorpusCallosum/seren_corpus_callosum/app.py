"""
seren_corpus_callosum.app
════════════════════════════════════════════════════════════════════════

The FastAPI application for the corpus callosum. Wires the federation (built
from config), the /search route, optional bearer auth, and - when the [mcp]
extra is installed - an MCP surface so a connected model can call the fan
directly via the `search` tool.

ENDPOINTS:
    GET  /         - service info + the stores it's fanning
    GET  /health   - liveness
    POST /search   - fan across all stores, RRF-merged, ranked

Deliberately parallel to SerenLoci/SerenMemory: same create_app factory, same
lifespan-into-app.state shape, same conditional-MCP-mount with HTTP-only
fallback, same trusted-LAN bearer posture, same public-paths set. The tell of
what THIS service is: there's no store of its own. It owns nothing and
remembers nothing - it only fans, floors, and merges what the hemispheres
hand back. Read-only by construction.
"""
from __future__ import annotations

import time
import logging
from contextlib import asynccontextmanager, AsyncExitStack
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from .config import CorpusCallosumConfig, load_config
from .federation import Federation
from .routes import search as search_routes
from .routes import stores as stores_routes
from .routes import health as health_routes
from .routes import configure as configure_routes
from .health import HealthTracker
from seren_meninges import get_version
from seren_meninges.updates import updates_payload
from seren_meninges.auth import bearer_auth_middleware
from seren_meninges.viewer import render_from_dir
from seren_sinew.request_log import RequestLoggingMiddleware

# Reported version via the shared helper: installed-wheel metadata, falling back
# to the package __version__ for a source checkout. get_version never raises.
from . import __version__ as _fallback_version
APP_VERSION = get_version("seren-corpus-callosum", fallback=_fallback_version)
log = logging.getLogger("seren_corpus_callosum")

def create_app(config: CorpusCallosumConfig | None = None, transport=None) -> FastAPI:
    """Build the app. `transport` is injectable so tests can pass a fake
    (real deployments leave it None and get the httpx-backed HttpTransport)."""
    cfg = config or load_config()
    # Resolve the inbound bearer ONCE at startup (a keyring lookup per request
    # would be slow). The shared ServerConfig carries resolve_bearer for free.
    bearer = cfg.server.resolve_bearer()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # -- Startup --
        app.state.config = cfg
        app.state.health = HealthTracker()

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
            app.state.transport = tx  # held so add/remove store can rebuild the live federation

            federation = Federation(cfg.federation, tx, health_tracker=app.state.health)
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
            # group entered explicitly - a mounted sub-app's own lifespan doesn't
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

    # -- Bearer auth (shared) --
    # constant-time compare + public-paths policy live in SerenMeninges so every
    # service enforces auth identically; empty token => mounts but no-ops.
    app.add_middleware(bearer_auth_middleware(bearer))

    # -- Request logging (shared, Sinew) --
    # Added AFTER auth so it sits OUTERMOST (Starlette mounts LIFO): every
    # request is logged including auth-rejected 401s, to stderr + the rotating
    # ~/seren-logs/corpus-callosum-requests.log. env_prefix=SEREN_SCC namespaces
    # the knobs (SEREN_SCC_LOG_LEVEL / _LOG_QUERY).
    app.add_middleware(
        RequestLoggingMiddleware,
        service_name="seren-corpus-callosum",
        env_prefix="SEREN_SCC",
    )

    # -- Info routes --
    @app.get("/")
    async def root(request: Request):
        fed = getattr(request.app.state, "federation", None)
        return {
            "service": "SerenCorpusCallosum",
            "version": APP_VERSION,
            "stores": fed.store_names if fed else [],
            "skipped": [{"name": n, "reason": r} for n, r in (fed.skipped if fed else [])],
            "updates": await updates_payload(
                getattr(request.app.state, "updates", None),
                distribution="seren-corpus-callosum", installed=APP_VERSION),
        }

    @app.get("/health")
    async def health():
        return {"ok": True, "ts": time.time()}
    
    # ── Update checker ───────────────────────────────────────
    # "is there a newer seren-workbench". Cosmetic: it polls on a TTL,
    # never in the request path, and every failure mode is a status string
    # rather than an exception.
    #
    # The try/except guards the IMPORT, because a Meninges older than 2.0.0
    # has no updates module. The gate is DELIBERATELY VISIBLE - state stays
    # None and GET / reports status="unavailable" with a reason. A silent
    # fallback would render as "you're up to date", which is the exact
    # failure shape that let mcp 2.0.0 quietly delete this service's /mcp
    # endpoint without anything going red.
    try:
        from seren_meninges.updates import UpdateChecker
        app.state.updates = UpdateChecker(
            "seren-corpus-callosum",
            enabled=cfg.updates.enabled,
            index_url=cfg.updates.index_url,
            ttl_seconds=cfg.updates.check_interval_hours * 3600.0,
            allow_prerelease=cfg.updates.allow_prerelease,
            fallback_version=APP_VERSION,
        )
    # Catch EVERYTHING, not just ImportError. This whole feature is cosmetic -
    # seren_meninges/version.py states the contract: a version read must never
    # crash startup. A too-narrow catch here already bit us: cfg.updates was
    # missing, the AttributeError sailed past `except ImportError`, and five
    # services failed to boot on a feature that only draws a badge.
    except Exception as exc:
        app.state.updates = None
        log.info("update checking unavailable (%s)", exc)

    @app.get("/viewer")
    async def viewer():
        # The Bridge - violet UI, hemisphere-colored results. Snaps the leaf
        # fragment files in viewer/ui/ onto the shared SerenMeninges baseplate.
        # Public route (the HTML needs no auth); its API calls carry the token.
        html = render_from_dir(
            Path(__file__).parent / "viewer" / "ui",
            title="SerenCorpusCallosum",
            brand='Seren<b>CorpusCallosum</b> · The Bridge',
            subtitle=f"v{APP_VERSION} · one fan, every hall",
            accent="#9d7cff",
        )
        return HTMLResponse(html)

    # -- The fan + introspection --
    app.include_router(search_routes.router)
    app.include_router(stores_routes.router)
    app.include_router(health_routes.router)
    app.include_router(configure_routes.router)

    return app
