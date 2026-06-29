"""
seren_corpus_callosum.models.schemas
════════════════════════════════════════════════════════════════════════

The HTTP contract for the callosum's /search. Request mirrors the family's
shape ({query, n_results}); the response is one merged, ranked list where
every hit carries full provenance - which store it came from, its rank there,
and both the cross-store RRF score and the within-store relevance - so the
merge is explainable, not a black box. `stores_searched` + `skipped` tell you
which hemispheres actually answered this turn.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str
    # None -> use the federation's configured default n_results.
    n_results: Optional[int] = None


class StoreCreate(BaseModel):
    """Body for POST /stores - add a store to the runtime overlay (and the live
    fan). type must be a known adapter type; name must be unique."""

    name: str
    type: str
    url: str
    weight: float = 1.0
    floor: float = 0.0
    # Optional bearer the store requires (the password-box value). The handler
    # routes it to the OS keychain when one exists (overlay keeps only a
    # token_keyring POINTER), or inline in the overlay as a plaintext escape
    # hatch on a node with no keychain. Never echoed back by GET /stores.
    token: Optional[str] = None


class FusedHitOut(BaseModel):
    """One merged hit with its provenance laid bare."""

    store: str                              # which configured store served it
    id: str                                 # the store-native id
    content: str                            # the surfaced text
    score: float                            # cross-store RRF score (the merge ranking key)
    store_rank: int                         # 1-based rank within its origin store
    base_relevance: float                   # within-store relevance (the floor signal)
    native_score: Optional[float] = None    # the store's own score (tier-weighted, etc.) - display
    raw_distance: Optional[float] = None     # raw cosine distance if the store exposed it - display
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConfigureRequest(BaseModel):
    """Body for POST /configure - dynamically tune federation parameters.

    All fields are optional; only supplied fields are changed.  Per-store
    overrides live in the ``stores`` list.
    """

    # Federation-level knobs
    k: Optional[int] = None
    fusion_mode: Optional[str] = None
    authority_margin: Optional[float] = None
    min_per_store: Optional[int] = None
    edges_enabled: Optional[bool] = None
    edge_budget: Optional[int] = None
    n_results: Optional[int] = None
    fetch_multiplier: Optional[int] = None
    per_store_timeout_s: Optional[float] = None

    # Per-store weight/floor overrides
    stores: Optional[list[dict[str, Any]]] = None


class SkippedStore(BaseModel):
    """A store that couldn't be bound at build time (e.g. unknown type)."""

    name: str
    reason: str


class SearchResponse(BaseModel):
    query: str
    hits: list[FusedHitOut]
    stores_searched: list[str]              # the stores that were actually fanned this call
    skipped: list[SkippedStore] = Field(default_factory=list)
