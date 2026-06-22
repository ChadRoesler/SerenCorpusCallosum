"""
seren_corpus_callosum.fusion
════════════════════════════════════════════════════════════════════════

The merge heart. Pure, embedder-agnostic, no I/O, no transport — just the
math that turns "N stores each handed me their own ranked list" into "one
ranked list." Everything else in SCC (adapters, the fan-out, the route) is
plumbing around this function.

WHY RECIPROCAL RANK FUSION (read this — it's the load-bearing decision):
    Each store embeds with whatever model it's configured for, and that
    model CAN CHANGE (SerenMemory/Loci both ship an embedder-migration
    feature). So a cosine distance from store A and a cosine distance from
    store B live in DIFFERENT geometric spaces, and a distance today is not
    a distance tomorrow after a migration. Any merge that compares raw
    scores or distances ACROSS stores is comparing numbers that were never
    commensurable.

    RRF sidesteps that entirely. It reads only each store's *internal rank
    ordering* — position in the list the store handed us, which the store
    produced in its own consistent space. A hit's fused score is

        weight[store] / (k + rank_in_that_store)

    Rank is embedder-agnostic by construction: swap a store's embedder, and
    as long as it still ranks its own hits sensibly, the fusion is
    unaffected. That's the whole reason this is the right call — not despite
    the embedder being mutable, but BECAUSE it is.

NOTE ON THE CLASSIC-RRF DIFFERENCE:
    Textbook RRF fuses multiple retrievers over the SAME corpus, summing a
    doc's 1/(k+rank) across the lists it appears in. Here each store owns a
    DIFFERENT corpus — a given hit lives in exactly one store — so there's
    no cross-list summation. The 1/(k+rank) score is used as a cross-source
    *interleaver*: every store's rank-1 is treated as equally good a priori
    (we trust each store's "this is my best" equally, not their magnitudes),
    rank-2s interleave next, and so on. Per-store `weights` are how you say
    "I trust store X more than store Y" without ever touching magnitudes.

WHAT THIS FILE DELIBERATELY DOES NOT DO:
    It never reads raw_distance or native_score for ordering. Those ride
    along in the Hit for display and for the per-store floor, but the FUSION
    touches only rank + weight. That keeps the embedder-immunity airtight:
    if magnitude never enters the sort, a changing embedder can't perturb
    the merged order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Hit:
    """One normalized result from a single store, after its adapter has
    mapped the store's native response into this common shape.

    IMPORTANT: the LIST ORDER a store's hits arrive in *is* that store's
    ranking. Fusion derives rank from position, so an adapter MUST preserve
    the store's returned order. Don't pre-sort hits by base_relevance unless
    that genuinely is the store's own ordering (it isn't for SerenMemory,
    whose tier/evidence weighting intentionally diverges from raw cosine).
    """

    store: str                       # provenance: which configured store this came from
    id: str                          # the store-native id (namespaced by `store` for global uniqueness)
    content: str                     # the surfaced text (memory content / fact value)
    base_relevance: float            # 1/(1+distance), within-store, in (0,1]. Used by the FLOOR only.
    raw_distance: Optional[float] = None    # raw cosine distance if the store exposed it — display only
    native_score: Optional[float] = None    # the store's own score (e.g. Memory's tier-weighted) — display only
    metadata: dict = field(default_factory=dict)   # passthrough: tier, why, match_kind, evidence_count, ...


@dataclass
class FusedHit:
    """A Hit plus the cross-store bookkeeping the fusion produced."""

    hit: Hit
    rrf_score: float                 # weight[store] / (k + store_rank)
    store_rank: int                  # 1-based rank within its own store (provenance / explainability)


def base_relevance_from_distance(distance: float) -> float:
    """The one relevance transform both SerenMemory and Loci already use:
    base = 1 / (1 + distance). Monotonic in distance, lands in (0, 1].
    Provided here so adapters compute it identically and the floor compares
    apples to apples WITHIN a store."""
    return 1.0 / (1.0 + max(distance, 0.0))


def apply_floor(hits: list[Hit], min_base_relevance: float) -> list[Hit]:
    """Drop hits below a within-store relevance floor, BEFORE fusion.

    Why a floor at all: RRF rank-boosts each store's top hit even when that
    hit is weak — rank-1-of-garbage still reads as rank 1 and interleaves
    with everyone else's genuinely-good rank 1. The floor stops a store from
    injecting noise into the merge just because the noise was locally
    top-ranked. It compares base_relevance, which IS meaningful within a
    single store (same embedder space throughout that store).

    A value <= 0 disables the floor (trust the store's own ordering wholesale).

    HONEST CAVEAT for stores whose native ranking diverges from raw cosine:
    SerenMemory deliberately boosts a high-evidence long-term fact above a
    fresher short-term hit even when the long-term fact's raw cosine is
    mediocre. Flooring such a store on base_relevance can therefore drop a
    hit the store *intended* to rank highly. For those stores either set the
    floor low/zero (defer to the store) or, later, floor on native_score via
    a per-store policy. v1 keeps it simple: one base_relevance floor per store.
    """
    if min_base_relevance <= 0:
        return list(hits)
    return [h for h in hits if h.base_relevance >= min_base_relevance]


def rrf_fuse(
    ranked_lists: dict[str, list[Hit]],
    *,
    k: int = 60,
    weights: Optional[dict[str, float]] = None,
    n_results: Optional[int] = None,
) -> list[FusedHit]:
    """Interleave independent stores' ranked lists by Reciprocal Rank Fusion.

    Args:
        ranked_lists: {store_name: [Hit, ...]} where each list is ALREADY in
            that store's ranking order (position 0 == the store's best hit).
            Iteration order of this dict is the deterministic tie-break order
            (build it from your configured-store order).
        k: RRF damping constant. Larger k flattens the advantage of early
            ranks; 60 is the canonical default from the original RRF paper
            and is robust. Expose it as config if you want to tune.
        weights: optional {store_name: float}. A store's hits are scaled by
            its weight — the lever for "trust store X more than Y." Defaults
            to 1.0 for any store not listed. This is the ONLY place cross-
            store preference is expressed, and it never touches magnitudes.
        n_results: trim the merged list to this many. None = return all.

    Returns:
        FusedHits sorted by rrf_score descending. Ties (a store's rank-i vs
        another store's rank-i at equal weight) are broken by a STABLE sort,
        so they fall back to the iteration order of `ranked_lists` then rank
        — deterministic and, crucially, embedder-agnostic. We never tie-break
        on base_relevance/raw_distance, because those aren't comparable across
        stores and letting them decide ties would re-import the very
        magnitude-incomparability RRF exists to avoid.
    """
    weights = weights or {}
    fused: list[FusedHit] = []

    # Build in (store-iteration-order, rank-ascending) order. Python's sort is
    # stable, so equal rrf_scores will retain exactly this order — that's our
    # deterministic, magnitude-free tie-break.
    for store, hits in ranked_lists.items():
        w = weights.get(store, 1.0)
        for idx, hit in enumerate(hits):
            rank = idx + 1                       # list position IS the store's ranking
            score = w / (k + rank)
            fused.append(FusedHit(hit=hit, rrf_score=score, store_rank=rank))

    fused.sort(key=lambda f: f.rrf_score, reverse=True)

    if n_results is not None:
        fused = fused[:n_results]
    return fused
