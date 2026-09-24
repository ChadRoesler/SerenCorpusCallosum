"""
seren_corpus_callosum.routes.stores
═══════════════════════════════════════════════════════════════════════

The federation roster + its management surface.

    GET    /stores          - what the callosum is fanning, with bind status
    POST   /stores          - add a store (persists to the runtime overlay,
                              rebuilds the live fan - no restart)
    DELETE /stores/{name}    - remove a store (overlay stores only; base stores
                              are config-owned)

KEY DISTINCTION - this is read-only over DATA, not over CONFIG. The callosum
never writes into any store's memory; adding/removing a store just changes
WHICH stores it reads. That's config management, gated by the bearer token,
and fully consistent with "read-only by construction."

PROVENANCE OF A STORE:
    base     - declared in the hand-authored yaml. Config-owned; the UI won't
               touch it (DELETE refuses with a 400 pointing you at the yaml).
    managed  - added via this API; lives in the runtime overlay JSON. Removable
               here. Base wins on a name collision, always.

Mutations run under a single lock and rebuild the live Federation in place, so
an added store is fanned on the very next /search.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Body, HTTPException, Request

from seren_meninges import store_token, delete_token

from ..adapters import known_store_types
from ..config import StoreConfig
from ..federation import Federation
from ..models.schemas import StoreCreate
from ..overlay import add_to_overlay, remove_from_overlay

router = APIRouter(tags=["stores"])

log = logging.getLogger("seren_corpus_callosum.stores")

# Serialize roster mutations: read-modify-write of the overlay + federation
# rebuild must not interleave. Single process, so a plain asyncio.Lock is enough.
_MUTATE_LOCK = asyncio.Lock()


def _token_ref(name: str) -> str:
    """Keychain ref for a store's outbound bearer: 'service/username', where the
    service is the callosum and the username is the store name. resolve_token
    (read), store_token (write), and delete_token (cleanup) all use this shape."""
    return f"seren-corpus-callosum/{name}"


def _store_row(s: StoreConfig, bound: set, skipped: dict) -> dict:
    if s.name in bound:
        status = "active"
    elif not s.enabled:
        status = "disabled"
    else:
        status = f"skipped: {skipped.get(s.name, 'unbound')}"
    return {
        "name": s.name,
        "type": s.type,
        "url": s.url,
        "weight": s.weight,
        "floor": s.floor,
        "enabled": s.enabled,
        "managed": s.managed,   # True = overlay-added, removable from the UI
        "auth": bool(s.token or s.token_env or s.token_keyring),  # store has a bearer (no secret leaked)
        "status": status,
    }


# The knobs THIS build of the callosum actually honors on POST /configure.
#
# WHY ADVERTISE THIS AT ALL: a knob the SCC silently IGNORES produces identical
# results for every value you sweep. On an eval dashboard that flatline is
# indistinguishable from a real ceiling - and reading "tuning does nothing" as
# "the corpus is maxed out" is exactly the wrong conclusion. SerenProbe reads
# this list and REFUSES to sweep a knob we don't implement, so the harness can
# never mistake an ignored parameter for a finding. If you add a knob to
# ConfigureRequest, add it here. An honest capability list is the whole point.
SUPPORTED_KNOBS = [
    "k", "rrf_k", "fusion_mode", "authority_margin", "min_per_store",
    "edges_enabled", "edge_budget", "satellite_edges_enabled", "satellite_budget",
    "n_results", "fetch_multiplier",
    "per_store_timeout_s", "loci_weight", "loci_floor",
    "hops", "hop_terms", "hop_budget",
]


@router.get("/stores")
async def list_stores(request: Request) -> dict:
    cfg = request.app.state.config
    fed = request.app.state.federation
    bound = set(fed.store_names)
    skipped = {name: reason for name, reason in fed.skipped}
    return {
        "stores": [_store_row(s, bound, skipped) for s in cfg.federation.stores],
        "active": len(bound),
        "k": cfg.federation.k,
        "n_results": cfg.federation.n_results,
        "hops": cfg.federation.hops,
        "hop_terms": cfg.federation.hop_terms,
        "hop_budget": cfg.federation.hop_budget,
        "satellite_edges_enabled": cfg.federation.satellite_edges_enabled,
        "satellite_budget": cfg.federation.satellite_budget,
        "types": sorted(known_store_types()),  # for the UI's type dropdown
        "supported_knobs": SUPPORTED_KNOBS,    # what /configure will actually honor
    }


@router.post("/stores")
async def add_store(request: Request, req: StoreCreate = Body(...)) -> dict:
    cfg = request.app.state.config
    name = req.name.strip()
    url = req.url.strip().rstrip("/")
    if not name:
        raise HTTPException(400, "name is required")
    if not url:
        raise HTTPException(400, "url is required")
    if req.type not in known_store_types():
        raise HTTPException(
            400, f"unknown store type {req.type!r}; known types: {sorted(known_store_types())}")

    async with _MUTATE_LOCK:
        if any(s.name == name for s in cfg.federation.stores):
            raise HTTPException(409, f"a store named {name!r} already exists")

        store = {"name": name, "type": req.type, "url": url,
                 "weight": req.weight, "floor": req.floor}

        # Outbound auth. If a token was supplied (the password box), prefer the
        # OS keychain and persist only a POINTER; fall back to inline plaintext
        # on a node with no keychain (degrade-never-crash, trusted-LAN only).
        # The raw secret leaves memory when this block ends either way.
        token = (req.token or "").strip()
        if token:
            ref = _token_ref(name)
            if store_token(ref, token):
                store["token_keyring"] = ref          # secret lives in the keychain
            else:
                store["token"] = token                # inline escape hatch (plaintext)
                log.warning(
                    "no OS keychain on this node - storing %r's bearer INLINE "
                    "(plaintext) in the runtime overlay. Trusted-LAN only; use a "
                    "token_env/token_keyring pointer in yaml for the secure path.",
                    name)

        if cfg.runtime_stores_path:
            add_to_overlay(cfg.runtime_stores_path, store)

        cfg.federation.stores.append(StoreConfig.from_dict({**store, "managed": True}))
        fed = Federation(cfg.federation, request.app.state.transport,
                         health_tracker=getattr(request.app.state, "health", None))
        request.app.state.federation = fed

    return {"ok": True, "added": name, "active": len(fed.store_names)}


@router.delete("/stores/{name}")
async def remove_store(request: Request, name: str) -> dict:
    cfg = request.app.state.config

    async with _MUTATE_LOCK:
        match = next((s for s in cfg.federation.stores if s.name == name), None)
        if match is None:
            raise HTTPException(404, f"no store named {name!r}")
        if not match.managed:
            raise HTTPException(
                400, f"{name!r} is a base (config) store - remove it from "
                     f"seren-corpus-callosum.yaml, not from here")

        if cfg.runtime_stores_path:
            remove_from_overlay(cfg.runtime_stores_path, name)
        # Best-effort: wipe any keychain entry this store owned (no-op if it was
        # inline/open or the node has no keychain). Removing a store must never
        # orphan its secret.
        delete_token(_token_ref(name))

        cfg.federation.stores = [s for s in cfg.federation.stores if s.name != name]
        fed = Federation(cfg.federation, request.app.state.transport,
                         health_tracker=getattr(request.app.state, "health", None))
        request.app.state.federation = fed

    return {"ok": True, "removed": name, "active": len(fed.store_names)}
