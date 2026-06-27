"""
seren_corpus_callosum.fusion
════════════════════════════════════════════════════════════════════════

The merge heart. Pure, embedder-agnostic, no I/O, no transport - just the
math that turns "N stores each handed me their own ranked list" into "one
ranked list." Everything else in SCC (adapters, the fan-out, the route) is
plumbing around this function.

WHY RECIPROCAL RANK FUSION (read this - it's the load-bearing decision):
    Each store embeds with whatever model it's configured for, and that
    model CAN CHANGE (SerenMemory/Loci both ship an embedder-migration
    feature). So a cosine distance from store A and a cosine distance from
    store B live in DIFFERENT geometric spaces, and a distance today is not
    a distance tomorrow after a migration. Any merge that compares raw
    scores or distances ACROSS stores is comparing numbers that were never
    commensurable.

    RRF sidesteps that entirely. It reads only each store's *internal rank
    ordering* - position in the list the store handed us, which the store
    produced in its own consistent space. A hit's fused score is

        weight[store] / (k + rank_in_that_store)

    Rank is embedder-agnostic by construction: swap a store's embedder, and
    as long as it still ranks its own hits sensibly, the fusion is
    unaffected. That's the whole reason this is the right call - not despite
    the embedder being mutable, but BECAUSE it is.

NOTE ON THE CLASSIC-RRF DIFFERENCE:
    Textbook RRF fuses multiple retrievers over the SAME corpus, summing a
    doc's 1/(k+rank) across the lists it appears in. Here each store owns a
    DIFFERENT corpus - a given hit lives in exactly one store - so there's
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
    raw_distance: Optional[float] = None    # raw cosine distance if the store exposed it - display only
    native_score: Optional[float] = None    # the store's own score (e.g. Memory's tier-weighted) - display only
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
    hit is weak - rank-1-of-garbage still reads as rank 1 and interleaves
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


def _store_margin(hits: list[Hit]) -> Optional[float]:
    """A store's INTRA-store confidence: top hit's base_relevance minus its
    runner-up's, in the store's OWN embedder space (a within-store comparison,
    which IS meaningful - see apply_floor). Captures the SHAPE of the answer: a
    wide gap = 'I have a clear winner', a flat gap = 'I'm guessing'. That shape
    survives an embedder swap far better than raw magnitude, which is why
    comparing margins across stores is a defensible confidence signal even
    though comparing raw distances across stores is not. None if < 2 hits."""
    if len(hits) < 2:
        return None
    return hits[0].base_relevance - hits[1].base_relevance


def _most_confident_store(ranked_lists: dict[str, list[Hit]],
                          threshold: float) -> Optional[str]:
    """N-store generalization of the authority rule. Across ALL stores, the one
    whose intra-store margin is largest AND clears `threshold`. No store name is
    hardcoded - 'the authoritative store usually wins' is an OUTCOME of it being
    the most internally confident, not a privilege. Deterministic on margin ties
    (first in iteration order == configured store order)."""
    best_store: Optional[str] = None
    best_margin: Optional[float] = None
    for store, hits in ranked_lists.items():
        m = _store_margin(hits)
        if m is not None and m >= threshold and (best_margin is None or m > best_margin):
            best_store, best_margin = store, m
    return best_store


def _apply_authority(fused: list[FusedHit], ranked_lists: dict[str, list[Hit]],
                     threshold: float) -> list[FusedHit]:
    """Promote the most-confident store's top hit to fused rank 1, if any store
    clears the margin. Pure list surgery on the already-fused order - the merge
    math is untouched.

    Why an explicit promotion rather than a fusion weight: a confident
    authoritative fact and a genuinely relevant episode legitimately tie at each
    store's rank 1, and rank-only RRF then breaks that tie on store-iteration
    order - demoting the fact for a reason that has nothing to do with the
    answer. This restores the correct policy: when a store is clearly confident
    in its top answer, that answer LEADS the packet; the relevant context still
    rides right behind it.

    This is the ONE place fusion consults base_relevance for ORDERING, and only
    as an INTRA-store confidence signal (via _store_margin), never as a cross-
    store magnitude comparison - so the merge's embedder-immunity is preserved.

    Respects WEIGHT: authority only breaks the tie FOR THE LEAD. If a higher-
    weighted hit strictly outscores the confident store's top, that deliberate
    weight wins - authority resolves the arbitrary store-iteration tie among
    co-leaders, it never overrides a weight gap an operator chose."""
    cs = _most_confident_store(ranked_lists, threshold)
    if cs is None or not ranked_lists.get(cs) or not fused:
        return fused
    top = ranked_lists[cs][0]
    idx = next((i for i, f in enumerate(fused)
                if f.hit.store == cs and f.hit.id == top.id), None)
    if idx is None or idx == 0:
        return fused
    # Weight sets the pecking order; authority only breaks the tie FOR THE LEAD.
    # If something strictly outscores the confident store's top (a deliberate
    # weight gap), defer to it - don't override the operator's trust.
    if fused[idx].rrf_score < fused[0].rrf_score - 1e-9:
        return fused
    return [fused[idx]] + fused[:idx] + fused[idx + 1:]


def rrf_fuse(
    ranked_lists: dict[str, list[Hit]],
    *,
    k: int = 60,
    weights: Optional[dict[str, float]] = None,
    n_results: Optional[int] = None,
    fusion_mode: str = "rrf",
    authority_margin: float = 0.0,
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
            its weight - the lever for "trust store X more than Y." Defaults
            to 1.0 for any store not listed. This is the ONLY place cross-
            store preference is expressed, and it never touches magnitudes.
        n_results: trim the merged list to this many. None = return all.

    Returns:
        FusedHits sorted by rrf_score descending. Ties (a store's rank-i vs
        another store's rank-i at equal weight) are broken by a STABLE sort,
        so they fall back to the iteration order of `ranked_lists` then rank
        - deterministic and, crucially, embedder-agnostic. We never tie-break
        on base_relevance/raw_distance, because those aren't comparable across
        stores and letting them decide ties would re-import the very
        magnitude-incomparability RRF exists to avoid.
    """
    weights = weights or {}

    # Intra-store percentile (common currency) for the N-store modes: a store's
    # rank-1 of n -> 100, rank-n -> 100/n. Lets stores rank against each other by
    # POSITION, never by magnitudes that don't survive an embedder swap. Computed
    # once; only consulted by 'percentile' / 'rrf_pct'.
    pctl: dict[tuple[str, str], float] = {}
    for store, hits in ranked_lists.items():
        n = len(hits)
        for idx, hit in enumerate(hits):
            pctl[(store, hit.id)] = ((n - idx) / n) * 100.0 if n else 0.0

    # Build in (store-iteration-order, rank-ascending) order. Python's sort is
    # stable, so equal scores retain exactly this order - the deterministic,
    # magnitude-free tie-break. (fusion_mode='rrf' + authority_margin<=0 is
    # byte-identical to the original rank-only RRF.)
    fused: list[FusedHit] = []
    for store, hits in ranked_lists.items():
        w = weights.get(store, 1.0)
        for idx, hit in enumerate(hits):
            rank = idx + 1                       # list position IS the store's ranking
            if fusion_mode == "percentile":
                score = w * pctl[(store, hit.id)]      # common currency: 100 == every store's best
            else:                                       # 'rrf' and 'rrf_pct' both score on rank
                score = w / (k + rank)
            fused.append(FusedHit(hit=hit, rrf_score=score, store_rank=rank))

    # 'rrf_pct' keeps RRF's rank score but breaks ties on percentile instead of
    # store-iteration order (minimal surgery, still embedder-agnostic). Other
    # modes use the stable sort, preserving store-iteration-then-rank exactly.
    if fusion_mode == "rrf_pct":
        fused.sort(key=lambda f: (f.rrf_score, pctl[(f.hit.store, f.hit.id)]), reverse=True)
    else:
        fused.sort(key=lambda f: f.rrf_score, reverse=True)

    # Most-confident-store-wins authority: promote the clearly-confident store's
    # top hit to fused rank 1 BEFORE trimming, so the authoritative answer leads
    # the packet even if rank-only fusion buried it. Disabled at margin <= 0.
    if authority_margin > 0:
        fused = _apply_authority(fused, ranked_lists, authority_margin)

    if n_results is not None:
        fused = fused[:n_results]
    return fused
