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
from .adapters import _loci_content as _cited_content   # a cited fact reads like any Loci hit: key = value because why
from .config import FederationConfig
from .fusion import FusedHit, Hit, apply_floor, fit_in_budget, pack_to_budget, packet_cost, row_cost, rrf_fuse
from .health import HealthTracker

# A search packed to a token budget needs enough candidates to fill the page,
# whatever n_results says: a Loci one-liner is twenty-odd tokens, so this is
# what one candidate is assumed to cost when the budget sizes the per-store
# ask (each adapter still clamps the ask to its store's own cap).
_TOKENS_PER_CANDIDATE = 16


@dataclass
class FanReport:
    """What one search actually did. `answered` are the stores whose call
    came back this turn (empty is still an answer); `failed` are the ones that
    did not, with why (timeout, refused, a 4xx/5xx); `skipped` were never bound
    at all. A response that could not tell a timeout from 'knows nothing' is
    how two 401s once read as an empty brain."""

    hits: list[FusedHit]
    answered: list[str]
    failed: list[tuple[str, str]]
    skipped: list[tuple[str, str]]
    budget_tokens: Optional[int] = None     # the page size this packet was packed to, if any
    tokens: int = 0                         # the packet's estimated cost to the reader (fusion.packet_cost)


@dataclass
class _BoundStore:
    """An adapter paired with the floor we'll apply to its hits."""

    adapter: StoreAdapter
    floor: float
    weight: float


def _as_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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
            # The satellite knobs that are Memory's to answer (how many per
            # core, in which order) ride to it through its adapter. An adapter
            # that has no such ask (Loci) has no such attribute.
            if hasattr(adapter, "surroundings_n"):
                adapter.surroundings_n = getattr(config, "satellites_per_core", None)
                adapter.surroundings_by = getattr(config, "satellite_order", None)
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

    async def search(self, query: str, n_results: Optional[int] = None,
                     budget_tokens: Optional[int] = None) -> list[FusedHit]:
        """Fan the query across all bound stores and return the merged ranking.
        The list only; search_report says which stores answered."""
        return (await self.search_report(query, n_results, budget_tokens)).hits

    async def search_report(self, query: str, n_results: Optional[int] = None,
                            budget_tokens: Optional[int] = None) -> FanReport:
        """Fan the query across all bound stores: the merged ranking plus which
        stores answered and which failed.

        Over-fetches per store (n * fetch_multiplier, clamped to what the store
        accepts) so the fusion has enough candidates, then trims to n_results
        after merging.

        With budget_tokens (this call's, else the config's default) the packet
        is a PAGE instead: packed to that many estimated tokens of text (see
        fusion.pack_to_budget), the per-store ask sized to fill it, and the hop
        hits and the edges packed into what is left of the page rather than
        appended past the trim. n_results given beside a budget is a second cap
        on the rows. No budget anywhere is byte-identical to the row-count
        packet.

        With hops > 1, a SECOND retrieval round runs (see _append_hop_hits): the
        fan can then reach a document that shares no term with the original query.
        hops == 1 (the default) is byte-identical to the single-pass behavior.
        """
        budget = self._budget(budget_tokens)
        outcome: dict[str, Optional[str]] = {}
        fused = await self._fan(query, n_results, outcome, budget)

        # MULTI-HOP: fusion reorders a packet; it cannot ADD a document retrieval
        # never returned. A second round can. Off by default (Nano floor).
        hops = max(1, getattr(self._config, "hops", 1))
        if hops > 1 and fused:
            fused = await self._append_hop_hits(fused, query, n_results, hops, budget)

        # What rides WITH the rows, chosen FIRST, because on a page it needs
        # room made for it: a page is packed full, so an edge told to fit in
        # what is left never rides.
        #   A fact's reasons: the facts it rests on (Loci citations, the
        #   skyways). Measured 24 Nov 2026: citations at 0, 3 and 8 were
        #   identical to the digit on a page, and took the blind skyway
        #   questions from 0.03 to 1.00 on a row-count packet.
        #   A core's surroundings: its satellites (the episodes behind it) and
        #   the core it superseded. The story behind a hit, not competitors
        #   for its slot. With satellite_share they get room the same way; at
        #   0 they fit in what the page has left, as before.
        # The lowest-ranked rows give up exactly what these cost, and nothing
        # when no row brings any. Both rode along inline on the hit; off, the
        # inline content is stripped and only the counts stay.
        fused, satellites, reasons = self._make_room_for_edges(fused, budget, n_results)

        # Marked edges after the ranked packet: the satellites, then the reasons.
        fused = (fused + [FusedHit(hit=h, rrf_score=0.0, store_rank=i + 1) for i, h in enumerate(satellites)]
                 + [FusedHit(hit=h, rrf_score=0.0, store_rank=i + 1) for i, h in enumerate(reasons)])

        # After the fan: append a small, MARKED addendum of topic-ASSOCIATION
        # edges - entries that share a topic tag with the packet but whose
        # wording put them out of vector reach (the scar). Bounded, deduped
        # against the packet, and only from stores that speak /by_topic.
        if self._config.edges_enabled and self._config.edge_budget > 0:
            fused = await self._append_topic_edges(fused, query, budget)
        answered = [n for n, err in outcome.items() if err is None]
        failed = [(n, err) for n, err in outcome.items() if err is not None]
        return FanReport(hits=fused, answered=answered, failed=failed, skipped=self.skipped,
                         budget_tokens=budget, tokens=packet_cost(fused))

    def _budget(self, budget_tokens: Optional[int]) -> Optional[int]:
        """The page size for one call: the caller's, else the config's default,
        else None (a row-count packet). A value <= 0 means none."""
        b = budget_tokens if budget_tokens is not None else getattr(self._config, "budget_tokens", None)
        return b if b is not None and b > 0 else None

    @staticmethod
    def _remaining(fused: list[FusedHit], budget: Optional[int]) -> Optional[int]:
        """Room left on the page after `fused`; None when there is no page."""
        return None if budget is None else max(0, budget - packet_cost(fused))

    async def _fan(self, query: str, n_results: Optional[int] = None,
                   outcome: Optional[dict[str, Optional[str]]] = None,
                   budget: Optional[int] = None) -> list[FusedHit]:
        """ONE retrieval round: fan out, floor, fuse. The unit a hop repeats.
        `outcome`, when given, collects each store's result this round: None
        for answered, the reason for failed. `budget` packs the round to a
        page (and sizes the ask to fill one) instead of trimming to n."""
        if not self._stores:
            return []

        n = n_results if n_results is not None else self._config.n_results
        fetch_n = max(n * self._config.fetch_multiplier, n)
        if budget is not None:
            # A page wants enough candidates to fill it, whatever n says.
            fetch_n = max(fetch_n, -(-budget // _TOKENS_PER_CANDIDATE))

        # Fan out in parallel; each _safe_search resolves to (name, hits, error)
        # and never raises (failures become empty lists inside).
        results = await asyncio.gather(
            *(self._safe_search(b, query, fetch_n) for b in self._stores)
        )

        # Build ranked_lists + weights in store order (stable tie-break).
        ranked_lists: dict[str, list[Hit]] = {}
        weights: dict[str, float] = {}
        for bound, (name, hits, err) in zip(self._stores, results):
            ranked_lists[name] = apply_floor(hits, bound.floor)
            weights[name] = bound.weight
            if outcome is not None:
                outcome[name] = err

        return rrf_fuse(
            ranked_lists,
            k=self._config.k,
            weights=weights,
            # A page replaces the DEFAULT row cap; a caller's explicit n still caps.
            n_results=n if budget is None else n_results,
            fusion_mode=self._config.fusion_mode,
            min_per_store=self._config.min_per_store,
            budget_tokens=budget,
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
                               n_results: Optional[int], hops: int,
                               budget: Optional[int] = None) -> list[FusedHit]:
        """Run further retrieval rounds and append what only a HOP could reach.

        Each round re-fans an expanded query (original + round-N hit text) and
        keeps only documents we haven't already got. Hop hits ride AFTER the
        similarity-ranked packet - same discipline as topic edges - so a hop can
        never demote a direct hit. They're here because they were UNREACHABLE,
        not because they outranked anything.

        Every hop hit is MARKED (metadata.hop = which round found it, and the
        bridge query that found it), so the briefing stays explainable: you can
        always see why a document is in the packet.

        On a page (`budget`), a hop hit also has to FIT in what is left of it.
        """
        room = max(0, self._config.hop_budget)
        if room <= 0:
            return fused

        seen = {f.hit.id for f in fused}
        packet = list(fused)
        frontier = fused

        for hop in range(2, hops + 1):
            bridge = self._bridge_query(query, frontier)
            more = await self._fan(bridge, n_results, budget=budget)
            fresh = [f for f in more if f.hit.id not in seen]
            if not fresh:
                break                              # nothing new: the hop is exhausted, stop early
            fresh = fresh[:room]
            if budget is not None:
                fresh, _ = fit_in_budget(fresh, self._remaining(packet, budget))
                if not fresh:
                    break                          # the page is full
            for f in fresh:
                seen.add(f.hit.id)
                # Provenance: WHICH round reached this, and via what bridge.
                f.hit.metadata = {**(f.hit.metadata or {}),
                                  "hop": hop, "hop_bridge": bridge[:120]}
            packet = packet + fresh
            room -= len(fresh)
            frontier = fresh
            if room <= 0:
                break
        return packet

    async def _safe_search(self, bound: _BoundStore, query: str, n: int
                           ) -> tuple[str, list[Hit], Optional[str]]:
        """Call one store with a timeout; ANY failure -> empty list plus the
        reason. This is where graceful degradation actually lives: the packet
        goes on without the store, and the report says the store was not
        there - an empty answer and a failed call are different facts.

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
            # Empty result is still a successful call (the store answered, it
            # just had nothing to say). An adapter that returns something weird
            # is treated as "gave nothing" rather than poisoning the merge.
            lat = time.monotonic() - t0
            await self._health.record_success(name, lat)
            return name, list(hits) if hits else [], None
        except asyncio.TimeoutError:
            lat = time.monotonic() - t0
            reason = f"timeout after {lat:.2f}s"
            await self._health.record_failure(name, reason, lat)
            return name, [], reason
        except Exception as e:  # noqa: BLE001 - a sick store degrades, it doesn't crash the fan
            lat = time.monotonic() - t0
            reason = f"{type(e).__name__}: {str(e)[:120]}"
            await self._health.record_failure(name, reason, lat)
            return name, [], reason

    @staticmethod
    def _lift_surroundings(fused: list[FusedHit]) -> dict[tuple[str, str], list[tuple[str, dict]]]:
        """Take every core hit's inline surroundings off its metadata (the
        counts stay, so the hit still says a story exists) and hand them back
        by (store, core id), each a queue in the order Memory sent them: its
        satellites (`recent`), then the core it replaced (`supersedes_entry`)."""
        lifted: dict[tuple[str, str], list[tuple[str, dict]]] = {}
        for f in fused:
            sur = (f.hit.metadata or {}).get("surroundings")
            if not isinstance(sur, dict):
                continue
            recent = sur.pop("recent", None) or []
            sup = sur.pop("supersedes_entry", None)
            q: list[tuple[str, dict]] = [("satellite-edge", r) for r in recent if isinstance(r, dict)]
            if isinstance(sup, dict):
                q.append(("supersedes-edge", sup))
            if q:
                lifted[(f.hit.store, f.hit.id)] = q
        return lifted

    def _pick_satellites(self, rows: list[FusedHit], lifted: dict[tuple[str, str], list[tuple[str, dict]]],
                         count: Optional[int] = None, cap: Optional[int] = None,
                         skip_riding: bool = False) -> list[Hit]:
        """The surroundings that ride with `rows`, as MARKED edges: rrf_score
        0.0 once wrapped, source='satellite-edge' / 'supersedes-edge', `edge_of`
        naming the core. Round-robin across the packet's cores in packet order,
        each core's own queue in the order Memory sent it, so one
        well-evidenced core cannot crowd the rest out; none that is already a
        row. `count` stops the pick at that many edges (satellite_budget, a
        row-count packet), `cap` at that many tokens (a page). With
        satellite_top > 0 only the top that-many CORES of the packet bring
        theirs: a core far down the page is less likely to be what was asked
        about. `skip_riding` leaves out an episode that already rides on its
        core's row as matched_via: on a page that paid for room, the reader
        has it once already. Edges never enter the floor or the fuse."""
        top = max(0, getattr(self._config, "satellite_top", 0))
        seen = {f.hit.id for f in rows}
        if skip_riding:
            for f in rows:
                via = (f.hit.metadata or {}).get("matched_via")
                if isinstance(via, dict) and via.get("id"):
                    seen.add(str(via["id"]))
        queues: list[tuple[Hit, list[tuple[str, dict]]]] = []
        cores = 0
        for f in rows:
            if not isinstance((f.hit.metadata or {}).get("surroundings"), dict):
                continue                              # not a core row (a fact, a short, an edge)
            cores += 1
            if top and cores > top:
                break
            q = lifted.get((f.hit.store, f.hit.id))
            if q:
                queues.append((f.hit, list(q)))
        edges: list[Hit] = []
        spent = 0
        while queues and (count is None or len(edges) < count) and (cap is None or spent < cap):
            nxt: list[tuple[Hit, list[tuple[str, dict]]]] = []
            for core, q in queues:
                if count is not None and len(edges) >= count:
                    break
                kind, r = q.pop(0)
                rid = str(r.get("id") or "")
                if rid and rid not in seen:
                    seen.add(rid)
                    edge = Hit(
                        store=core.store,
                        id=rid,
                        content=str(r.get("content") or ""),
                        base_relevance=0.0,          # here by association with the core, not by cosine
                        raw_distance=_as_float(r.get("raw_distance")),   # set when Memory sent it in match order: display only
                        native_score=None,
                        metadata={
                            "tier": "long",
                            "kind": "satellite" if kind == "satellite-edge" else "core",
                            "core_id": core.id if kind == "satellite-edge" else None,
                            "superseded_by": core.id if kind == "supersedes-edge" else None,
                            "topic": (core.metadata or {}).get("topic"),
                            "created_at": r.get("created_at"),
                            "source": kind,
                            "edge_of": core.id,
                        },
                    )
                    cost = row_cost(edge)
                    if cap is None or spent + cost <= cap:
                        edges.append(edge)
                        spent += cost
                if q:
                    nxt.append((core, q))
            queues = nxt
        return edges

    @staticmethod
    def _lift_citations(fused: list[FusedHit]) -> dict[str, list[dict]]:
        """Take every hit's inline `rests_on` off its metadata (a count stays)
        and hand them back by the citing fact's id."""
        cited: dict[str, list[dict]] = {}
        for f in fused:
            meta = f.hit.metadata or {}
            got = meta.pop("rests_on", None)
            if isinstance(got, list):
                meta["rests_on_n"] = len(got)
                q = [r for r in got if isinstance(r, dict) and r.get("id")]
                if q:
                    cited[f.hit.id] = q
        return cited

    def _pick_reasons(self, rows: list[FusedHit], cited: dict[str, list[dict]],
                      cap: Optional[int] = None) -> list[Hit]:
        """The cited facts that ride with `rows`: up to citation_budget of them,
        round-robin across the citing facts in packet order, none that is
        already a row. With citation_top > 0 only the top that-many FACTS of
        the packet may bring their reasons: a fact far down the page is less
        likely to be what was asked about, and its reasons cost room the
        ranked rows could have had. `cap` stops the pick at that many tokens."""
        count, top = self._config.citation_budget, max(0, getattr(self._config, "citation_top", 0))
        seen = {f.hit.id for f in rows}
        queues: list[tuple[Hit, list[dict]]] = []
        facts = 0
        for f in rows:
            meta = f.hit.metadata or {}
            if meta.get("key") is None or meta.get("project") is None:
                continue                              # not a fact row (a memory, or an edge)
            facts += 1
            if top and facts > top:
                break
            if f.hit.id in cited:
                queues.append((f.hit, list(cited[f.hit.id])))
        edges: list[Hit] = []
        spent = 0
        while queues and len(edges) < count:
            nxt: list[tuple[Hit, list[dict]]] = []
            for fact, q in queues:
                if len(edges) >= count:
                    break
                r = q.pop(0)
                rid = str(r["id"])
                if rid not in seen:
                    edge = Hit(
                        store=fact.store,
                        id=rid,
                        content=_cited_content(r),
                        base_relevance=0.0,          # here because the fact rests on it, not by cosine
                        raw_distance=None,
                        native_score=None,
                        metadata={
                            "project": r.get("project"),
                            "key": r.get("key"),
                            "why": r.get("why"),
                            "because": r.get("because"),
                            "source": "citation-edge",
                            "edge_of": fact.id,
                        },
                    )
                    cost = row_cost(edge)
                    if cap is None or spent + cost <= cap:
                        seen.add(rid)
                        edges.append(edge)
                        spent += cost
                if q:
                    nxt.append((fact, q))
            queues = nxt
        return edges

    def _make_room_for_edges(self, fused: list[FusedHit], budget: Optional[int],
                             n_results: Optional[int] = None
                             ) -> tuple[list[FusedHit], list[Hit], list[Hit]]:
        """(the ranked rows, the satellites that ride with them, the reasons).

        With no page there is nothing to make room in: both are appended past
        the rows, as every edge is, the satellites capped by satellite_budget
        and the reasons by citation_budget.

        On a page the rows were packed FULL, so the lowest-ranked of them give
        way until what rides fits: the page is re-packed to (budget - what the
        edges cost), which keeps the diversity floor and the fused order.
        Shrinking changes who is on the page (a cited fact that had been a row
        goes back among the reasons, a core that gave way takes its satellites
        with it), so the pick is taken again until it settles. The rows keep
        at least half the page: the reasons may take up to half, and the
        satellites up to satellite_share of it and never more than what the
        reasons left of that half. A page whose rows bring nothing is returned
        as it came.

        satellite_share 0 is the behaviour before 24 Nov 2026 for satellites:
        the reasons get their room, and up to satellite_budget satellites fit
        in whatever the page has left after that."""
        cfg = self._config
        cited = self._lift_citations(fused)
        lifted = self._lift_surroundings(fused)
        cite_on = bool(cfg.citation_edges_enabled and cfg.citation_budget > 0 and cited)
        sat_on = bool(cfg.satellite_edges_enabled and cfg.satellite_budget > 0 and lifted)
        if not cite_on and not sat_on:
            return fused, [], []

        def cost(hits: list[Hit]) -> int:
            return sum(row_cost(h) for h in hits)

        if budget is None:
            return (fused,
                    self._pick_satellites(fused, lifted, count=cfg.satellite_budget) if sat_on else [],
                    self._pick_reasons(fused, cited) if cite_on else [])

        half = budget // 2
        share = min(0.5, max(0.0, float(getattr(cfg, "satellite_share", 0.0) or 0.0)))
        room_for_sats = sat_on and share > 0
        rows = fused
        satellites: list[Hit] = []
        reasons: list[Hit] = []
        settled = False
        for _ in range(4):
            reasons = self._pick_reasons(rows, cited, half) if cite_on else []
            need = cost(reasons)
            if room_for_sats:
                satellites = self._pick_satellites(rows, lifted, cap=max(0, min(int(budget * share), half - need)),
                                                   skip_riding=True)
                need += cost(satellites)
            if need <= budget - packet_cost(rows):
                settled = True
                break
            rows = pack_to_budget(rows, budget - need, cfg.min_per_store, n_results)
        left = max(0, budget - packet_cost(rows))
        if not settled:
            # four rounds did not settle it: take what fits in what there is
            reasons = self._pick_reasons(rows, cited, min(half, left)) if cite_on else []
            if room_for_sats:
                satellites = self._pick_satellites(
                    rows, lifted, cap=max(0, min(int(budget * share), half - cost(reasons), left - cost(reasons))),
                    skip_riding=True)
        if sat_on and not room_for_sats:
            # no share: the count, and only what the rows and the reasons left
            satellites = self._pick_satellites(rows, lifted, count=cfg.satellite_budget,
                                               cap=max(0, left - cost(reasons)))
        return rows, satellites, reasons

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

    async def _append_topic_edges(self, fused: list[FusedHit], query: str,
                                  budget: Optional[int] = None) -> list[FusedHit]:
        """Append the bounded, MARKED topic-association addendum to the packet.

        Reads the packet's center topics, fires one /by_topic per topic-capable
        store IN PARALLEL (exclude_ids = the packet's ids, so edges are only NEW
        context), and appends up to edge_budget results as edge FusedHits
        (rrf_score 0.0, marked source='topic-edge' by the adapter). Edges never
        enter the floor or the fuse - they're here by association, not magnitude,
        and they ride AFTER the similarity-ranked hits so the ranking stays
        honest. A store without search_by_topic (Loci) is skipped by capability
        check; if nothing's capable or there are no topics, the packet returns
        untouched. On a page (`budget`) an edge also has to fit in what the
        packet left of it."""
        topics = _collect_topics(fused)
        if not topics:
            return fused
        capable = [b for b in self._stores
                   if hasattr(b.adapter, "search_by_topic")]
        if not capable:
            return fused
        count = self._config.edge_budget
        exclude = [f.hit.id for f in fused]
        results = await asyncio.gather(
            *(self._safe_topic_search(b, topics, count, exclude) for b in capable)
        )
        edges: list[Hit] = []
        for _name, hits in results:
            edges.extend(hits)
        edges = edges[:count]
        edge_fused = [FusedHit(hit=h, rrf_score=0.0, store_rank=i + 1)
                      for i, h in enumerate(edges)]
        if budget is not None:
            edge_fused, _ = fit_in_budget(edge_fused, self._remaining(fused, budget))
        return fused + edge_fused
