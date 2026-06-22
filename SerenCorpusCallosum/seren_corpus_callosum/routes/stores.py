"""
seren_corpus_callosum.routes.stores
═══════════════════════════════════════════════════════════════════════

GET /stores - what the callosum is fanning, for the viewer's Stores tab (and
any operator tooling). Reports each configured store with its merge knobs
(weight/floor) and its bind status:

    active           - bound and being fanned
    disabled         - present in config but enabled: false
    skipped: <why>   - couldn't be bound (e.g. unknown type)

Read-only, like everything else here. (The write path - adding a store from the
UI - is a separate, deliberate decision; see POST /stores when it lands.)
"""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["stores"])


@router.get("/stores")
async def list_stores(request: Request) -> dict:
    cfg = request.app.state.config
    fed = request.app.state.federation
    bound = set(fed.store_names)
    skipped = {name: reason for name, reason in fed.skipped}

    stores = []
    for s in cfg.federation.stores:
        if s.name in bound:
            status = "active"
        elif not s.enabled:
            status = "disabled"
        else:
            status = f"skipped: {skipped.get(s.name, 'unbound')}"
        stores.append({
            "name": s.name,
            "type": s.type,
            "url": s.url,
            "weight": s.weight,
            "floor": s.floor,
            "enabled": s.enabled,
            "status": status,
        })

    return {
        "stores": stores,
        "active": len(bound),
        "k": cfg.federation.k,
        "n_results": cfg.federation.n_results,
    }
