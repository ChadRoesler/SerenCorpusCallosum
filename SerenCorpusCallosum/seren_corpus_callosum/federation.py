"""
seren_corpus_callosum.federation
════════════════════════════════════════════════════════════════════════

The fan. Reads config, builds one adapter per enabled store, and on each
search: queries every store IN PARALLEL, applies each store's relevance
floor, and RRF-fuses the lot into one ranked list with provenance.

GRACEFUL DEGRADATION IS THE FLOOR, NOT A FEATURE (Nano-floor ethos):
    A store that's down, slow, mis-typed, or throwing just contributes an
    empty list. The merge proceeds with whoever answered. One sick store
    never sinks the fan - partial memory beats a 500. Every failure mode
    (timeout, transport error, unknown type, malformed response) collapses
    to "this store gave nothing this turn."

ORDER MATTERS:
    We build ranked_lists in enabled-store config order so the fusion's
    stable tie-break is deterministic and matches the operator's declared
    store order. Don't reorder.

HEALTH TRACKING:
    Every `_safe_search` call records latency + outcome in the shared
    HealthTracker (passed at construction). This feeds the /health/stores
    endpoint and the viewer's health panel without adding any blocking
    overhead to the fan path - recording is fire-and-forget.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

from .adapters import StoreAdapter, Transport, UnknownStoreType, build_adapter
from .config import FederationConfig
from .fusion import FusedHit, Hit, apply_floor, rrf_fuse
from .health import HealthTracker


@dataclass
class _BoundStore:
    """An adapter paired with the floor we'll apply to its hits."""

    adapter: StoreAdapter
    floor: float
    weight: float


def _split_topics(topic: Optional[str]) -> list[str]:
    """Comma-joined topic string -> normalized lowercase tags. Mirrors
    SerenMemory's own split (topics are stored as one 'a, b, c' string), kept
    local so the engine import path stays light - SCC never imports Memory."""
    if not topic:
        return []
    return [t.strip().lower() for t in str(topic).split(",") if t.strip()]


def _collect_topics(fused: list[FusedHit]) -> list[str]:
    """The 'center' the edges radiate from: every unique topic tag across the
    packet, in first-seen order. Reads metadata.topic off the hits we already
    have, so collecting the join keys costs no extra round-trip."""
    seen: set[str] = set()
    out: list[str] = []
    for f in fused:
        for tag in _split_topics(f.hit.metadata.get("topic")):
            if tag not in seen:
                seen.add(tag)
                out.append(tag)
    return out


class Federation:
    """Owns the adapters and runs the fan. Construct once, call search() many
    times. Stateless per-call beyond the adapters it holds.

    If *health_tracker* is provided, every _safe_search call records latency
    and outcome - feeding the /health/stores endpoint without blocking the fan.
    """

    def __init__(self, config: FederationConfig, transport: Transport,
                 health_tracker: HealthTracker | None = None):
        self._config = config
        self._transport = transport
        self._health = health_tracker or HealthTracker()
        self._stores: list[_BoundStore] = []
        self._skipped: list[tuple[str, str]] = []  # (store_name, reason) - for diagnostics

        for cfg in config.enabled_stores:
            try:
                adapter = build_adapter(cfg, transport)
            except UnknownStoreType as e:
                # A bad type is config error, not a crash. Record + skip so
                # /health-style introspection can surface it later.
                self._skipped.append((cfg.name, str(e)))
                continue
            self._stores.append(_BoundStore(adapter=adapter, floor=cfg.floor, weight=cfg.weight))

    @property
    def store_names(self) -> list[str]:
        return [b.adapter.name for b in self._stores]

    @property
    def health(self) -> HealthTracker:
        """The health tracker collecting per-store metrics."""
        return self._health

    @property
    def skipped(self) -> list[tuple[str, str]]:
        """Stores dropped at build time (unknown type, etc.) with reasons."""
        return list(self._skipped)

    async def search(self, query: str, n_results: Optional[int] = None) -> list[FusedHit]:
        """Fan the query across all bound stores and return the merged ranking.

        Over-fetches per store (n * fetch_multiplier) so the fusion has enough
        candidates, then trims to n_results after merging.

        With hops > 1, a SECOND retrieval round runs (see _append_hop_hits): the
        fan can then reach a document that shares no term with the original query.
        hops == 1 (the default) is byte-identical to the single-pass behavior.
        """
        fused = await self._fan(query, n_results)

        # MULTI-HOP: fusion reorders a packet; it cannot ADD a document retrieval
        # never returned. A second round can. Off by default (Nano floor).
        hops = max(1, getattr(self._config, "hops", 1))
        if hops > 1 and fused:
            fused = await self._append_hop_hits(fused, query, n_results, hops)

        # After the fan: append a small, MARKED addendum of topic-ASSOCIATION
        # edges - entries that share a topic tag with the packet but whose
        # wording put them out of vector reach (the scar). Bounded, deduped
        # against the packet, and only from stores that speak /by_topic.
        if self._config.edges_enabled and self._config.edge_budget > 0:
            fused = await self._append_topic_edges(fused, query)
        return fused

    async def _fan(self, query: str, n_results: Optional[int] = None) -> list[FusedHit]:
        """ONE retrieval round: fan out, floor, fuse. The unit a hop repeats."""
        if not self._stores:
            return []

        n = n_results if n_results is not None else self._config.n_results
        fetch_n = max(n * self._config.fetch_multiplier, n)

        # Fan out in parallel; each _safe_search resolves to (name, hits) and
        # never raises (failures become empty lists inside).
        results = await asyncio.gather(
            *(self._safe_search(b, query, fetch_n) for b in self._stores)
        )

        # Build ranked_lists + weights in store order (stable tie-break).
        ranked_lists: dict[str, list[Hit]] = {}
        weights: dict[str, float] = {}
        for bound, (name, hits) in zip(self._stores, results):
            ranked_lists[name] = apply_floor(hits, bound.floor)
            weights[name] = bound.weight

        return rrf_fuse(
            ranked_lists,
            k=self._config.k,
            weights=weights,
            n_results=n,
            fusion_mode=self._config.fusion_mode,
            authority_margin=self._config.authority_margin,
            min_per_store=self._config.min_per_store,
        )

    def _bridge_query(self, query: str, fused: list[FusedHit]) -> str:
        """Build the round-2 query from what round 1 came back with.

        This is PSEUDO-RELEVANCE FEEDBACK, the oldest trick in IR: assume the top
        hits are relevant, and fold their text back into the query. It costs one
        string join - no LLM in the retrieval loop, ever.

        Why not just lift 'important tokens'? Because we TRIED that and it fails
        on real data: frequency-ranking the round-1 packet for 'supply chain for
        the asper-k1 strain' lifts ['expression', 'product', 'production',
        'application'] - the SCHEMA boilerplate every Loci fact shares - and
        round 2 on those just returns more strains. The terms that actually
        bridge are the distinctive VALUES (cellulase, pEX-2A, glaA, A. sojae),
        and the cheapest reliable way to carry them is to carry the hit text
        itself and let the retriever weigh it.

        The original query stays in front so round 2 is an EXPANSION, not a
        replacement - we're widening the net, not moving it.
        """
        top = fused[: max(1, self._config.hop_terms)]
        parts = [query]
        for f in top:
            text = (f.hit.content or "").strip()
            if text:
                parts.append(text[:200])          # bound it: a runaway doc can't swamp the query
        return " ".join(parts)

    async def _append_hop_hits(self, fused: list[FusedHit], query: str,
                               n_results: Optional[int], hops: int) -> list[FusedHit]:
        """Run further retrieval rounds and append what only a HOP could reach.

        Each round re-fans an expanded query (original + round-N hit text) and
        keeps only documents we haven't already got. Hop hits ride AFTER the
        similarity-ranked packet - same discipline as topic edges - so a hop can
        never demote a direct hit. They're here because they were UNREACHABLE,
        not because they outranked anything.

        Every hop hit is MARKED (metadata.hop = which round found it, and the
        bridge query that found it), so the briefing stays explainable: you can
        always see why a document is in the packet.
        """
        budget = max(0, self._config.hop_budget)
        if budget <= 0:
            return fused

        seen = {f.hit.id for f in fused}
        packet = list(fused)
        frontier = fused

        for hop in range(2, hops + 1):
            bridge = self._bridge_query(query, frontier)
            more = await self._fan(bridge, n_results)
            fresh = [f for f in more if f.hit.id not in seen]
            if not fresh:
                break                              # nothing new: the hop is exhausted, stop early
            fresh = fresh[:budget]
            for f in fresh:
                seen.add(f.hit.id)
                # Provenance: WHICH round reached this, and via what bridge.
                f.hit.metadata = {**(f.hit.metadata or {}),
                                  "hop": hop, "hop_bridge": bridge[:120]}
            packet = packet + fresh
            budget -= len(fresh)
            frontier = fresh
            if budget <= 0:
                break
        return packet

    async def _safe_search(self, bound: _BoundStore, query: str, n: int) -> tuple[str, list[Hit]]:
        """Call one store with a timeout; ANY failure -> empty list. This is
        where graceful degradation actually lives.

        Records latency + outcome in `self._health` for the /health/stores
        endpoint. Fire-and-forget - the tracker never blocks the fan.
        """
        name = bound.adapter.name
        t0 = time.monotonic()
        try:
            hits = await asyncio.wait_for(
                bound.adapter.search(query, n),
                timeout=self._config.per_store_timeout_s,
            )
            # Defensive: an adapter that returns something weird is treated as
            # "gave nothing" rather than poisoning the merge.
            lat = time.monotonic() - t0
            if hits:
                await self._health.record_success(name, lat)
            else:
                # Empty result is still a successful call (the store answered,
                # it just had nothing to say). Record as success with a note.
                await self._health.record_success(name, lat)
            return name, list(hits) if hits else []
        except asyncio.TimeoutError as e:
            lat = time.monotonic() - t0
            await self._health.record_failure(name, f"timeout after {lat:.2f}s", lat)
            return name, []
        except Exception as e:  # noqa: BLE001 - a sick store degrades, it doesn't crash the fan
            lat = time.monotonic() - t0
            await self._health.record_failure(name, str(e)[:120], lat)
            return name, []

    async def _safe_topic_search(self, bound: _BoundStore, topics: list[str],
                                 n: int, exclude_ids: list[str]
                                 ) -> tuple[str, list[Hit]]:
        """Topic-edge twin of _safe_search: call one store's search_by_topic with
        a timeout; ANY failure -> empty list. Same graceful-degradation contract -
        an edge join that errors just yields no edges, never sinks the packet."""
        name = bound.adapter.name
        try:
            hits = await asyncio.wait_for(
                bound.adapter.search_by_topic(topics, n, exclude_ids=exclude_ids),
                timeout=self._config.per_store_timeout_s,
            )
            return name, list(hits) if hits else []
        except asyncio.TimeoutError:
            return name, []
        except Exception:  # noqa: BLE001 - a sick store degrades, it doesn't crash the fan
            return name, []

    async def _append_topic_edges(self, fused: list[FusedHit],
                                  query: str) -> list[FusedHit]:
        """Append the bounded, MARKED topic-association addendum to the packet.

        Reads the packet's center topics, fires one /by_topic per topic-capable
        store IN PARALLEL (exclude_ids = the packet's ids, so edges are only NEW
        context), and appends up to edge_budget results as edge FusedHits
        (rrf_score 0.0, marked source='topic-edge' by the adapter). Edges never
        enter the floor or the fuse - they're here by association, not magnitude,
        and they ride AFTER the similarity-ranked hits so the ranking stays
        honest. A store without search_by_topic (Loci) is skipped by capability
        check; if nothing's capable or there are no topics, the packet returns
        untouched."""
        topics = _collect_topics(fused)
        if not topics:
            return fused
        capable = [b for b in self._stores
                   if hasattr(b.adapter, "search_by_topic")]
        if not capable:
            return fused
        budget = self._config.edge_budget
        exclude = [f.hit.id for f in fused]
        results = await asyncio.gather(
            *(self._safe_topic_search(b, topics, budget, exclude) for b in capable)
        )
        edges: list[Hit] = []
        for _name, hits in results:
            edges.extend(hits)
        edges = edges[:budget]
        edge_fused = [FusedHit(hit=h, rrf_score=0.0, store_rank=i + 1)
                      for i, h in enumerate(edges)]
        return fused + edge_fused
