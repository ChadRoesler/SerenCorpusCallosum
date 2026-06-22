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

from fastapi import APIRouter, Body, HTTPException, Request

from ..adapters import known_store_types
from ..config import StoreConfig
from ..federation import Federation
from ..models.schemas import StoreCreate
from ..overlay import add_to_overlay, remove_from_overlay

router = APIRouter(tags=["stores"])

# Serialize roster mutations: read-modify-write of the overlay + federation
# rebuild must not interleave. Single process, so a plain asyncio.Lock is enough.
_MUTATE_LOCK = asyncio.Lock()


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
        "status": status,
    }


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
        "types": sorted(known_store_types()),  # for the UI's type dropdown
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
        if cfg.runtime_stores_path:
            add_to_overlay(cfg.runtime_stores_path, store)

        cfg.federation.stores.append(StoreConfig.from_dict({**store, "managed": True}))
        fed = Federation(cfg.federation, request.app.state.transport)
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

        cfg.federation.stores = [s for s in cfg.federation.stores if s.name != name]
        fed = Federation(cfg.federation, request.app.state.transport)
        request.app.state.federation = fed

    return {"ok": True, "removed": name, "active": len(fed.store_names)}
