"""
seren_corpus_callosum.routes.configure
═══════════════════════════════════════════════════════════════════════

Dynamic runtime configuration for the SCC federation.

    POST /configure  - update k, fusion_mode, authority_margin,
                       min_per_store, edges_enabled, edge_budget,
                       and per-store weight/floor overrides

All parameters are optional; only supplied fields are changed. The live
Federation is rebuilt so the next /search picks up the new values without
a restart.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, HTTPException, Request

from ..config import FederationConfig
from ..federation import Federation
from ..models.schemas import ConfigureRequest

router = APIRouter(tags=["configure"])
log = logging.getLogger("seren_corpus_callosum.configure")


def _rebuild_federation(state, cfg: FederationConfig) -> Federation:
    """Build a fresh Federation from the updated config, reusing the transport."""
    return Federation(cfg, state.transport)


@router.post("/configure")
async def configure(request: Request, params: ConfigureRequest = Body(...)) -> dict:
    """Update runtime federation parameters.

    Only supplied fields are changed; omitted fields keep their current values.
    Per-store overrides in ``stores`` mutate weight/floor on matching stores.
    """
    cfg: FederationConfig = request.app.state.config.federation

    # -- Federation-level params --
    if params.k is not None:
        cfg.k = params.k
    if params.fusion_mode is not None:
        from ..fusion import _VALID_FUSION_MODES
        mode = params.fusion_mode
        if mode not in _VALID_FUSION_MODES:
            raise HTTPException(
                422, f"unknown fusion_mode {mode!r}; valid: {sorted(_VALID_FUSION_MODES)}"
            )
        cfg.fusion_mode = mode
    if params.authority_margin is not None:
        cfg.authority_margin = params.authority_margin
    if params.min_per_store is not None:
        cfg.min_per_store = params.min_per_store
    if params.edges_enabled is not None:
        cfg.edges_enabled = params.edges_enabled
    if params.edge_budget is not None:
        cfg.edge_budget = params.edge_budget
    if params.n_results is not None:
        cfg.n_results = params.n_results
    if params.fetch_multiplier is not None:
        cfg.fetch_multiplier = params.fetch_multiplier
    if params.per_store_timeout_s is not None:
        cfg.per_store_timeout_s = params.per_store_timeout_s

    # -- Per-store overrides --
    if params.stores:
        for override in params.stores:
            name = override.get("name", "")
            if not name:
                continue
            match = next((s for s in cfg.stores if s.name == name), None)
            if match is None:
                raise HTTPException(404, f"no store named {name!r}")
            w = override.get("weight")
            f = override.get("floor")
            if w is not None:
                match.weight = float(w)
            if f is not None:
                match.floor = float(f)

    # Rebuild the live federation so the new values take effect immediately.
    request.app.state.federation = _rebuild_federation(request.app.state, cfg)

    # Build a summary of what changed
    changed = {}
    if params.k is not None:
        changed["k"] = cfg.k
    if params.fusion_mode is not None:
        changed["fusion_mode"] = cfg.fusion_mode
    if params.authority_margin is not None:
        changed["authority_margin"] = cfg.authority_margin
    if params.min_per_store is not None:
        changed["min_per_store"] = cfg.min_per_store
    if params.edges_enabled is not None:
        changed["edges_enabled"] = cfg.edges_enabled
    if params.edge_budget is not None:
        changed["edge_budget"] = cfg.edge_budget
    if params.n_results is not None:
        changed["n_results"] = cfg.n_results
    if params.fetch_multiplier is not None:
        changed["fetch_multiplier"] = cfg.fetch_multiplier
    if params.per_store_timeout_s is not None:
        changed["per_store_timeout_s"] = cfg.per_store_timeout_s
    if params.stores:
        changed["stores"] = [
            {"name": s.name, "weight": s.weight, "floor": s.floor}
            for s in cfg.stores
            if any(o.get("name") == s.name for o in params.stores)
        ]

    return {"ok": True, "changed": changed, "active": len(request.app.state.federation.store_names)}
