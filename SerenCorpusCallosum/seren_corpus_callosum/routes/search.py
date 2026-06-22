"""
seren_corpus_callosum.routes.search
════════════════════════════════════════════════════════════════════════

POST /search — the one route that matters. Hands the query to the Federation
on app.state, gets back the RRF-merged ranking, and flattens each FusedHit
into the wire shape with full provenance. Deliberately the same route name
the whole family uses, so the callosum presents the exact interface it
consumes.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Request

from ..models.schemas import FusedHitOut, SearchRequest, SearchResponse, SkippedStore

router = APIRouter(tags=["search"])


@router.post("/search")
async def search(request: Request, req: SearchRequest = Body(...)) -> SearchResponse:
    fed = request.app.state.federation
    fused = await fed.search(req.query, n_results=req.n_results)
    hits = [
        FusedHitOut(
            store=f.hit.store,
            id=f.hit.id,
            content=f.hit.content,
            score=f.rrf_score,
            store_rank=f.store_rank,
            base_relevance=f.hit.base_relevance,
            native_score=f.hit.native_score,
            raw_distance=f.hit.raw_distance,
            metadata=f.hit.metadata,
        )
        for f in fused
    ]
    return SearchResponse(
        query=req.query,
        hits=hits,
        stores_searched=fed.store_names,
        skipped=[SkippedStore(name=n, reason=r) for n, r in fed.skipped],
    )
