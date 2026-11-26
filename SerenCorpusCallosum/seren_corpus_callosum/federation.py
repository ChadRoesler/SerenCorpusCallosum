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
from .fusion import (FusedHit, Hit, _trim_with_quota, apply_floor, fit_in_budget, pack_to_budget, packet_cost,
                     row_cost, rrf_fuse)
from .fit import FitTracker, best_distance
from .health import HealthTracker
from .names import NameTally, name_tokens, row_names, shared_names

# A search packed to a token budget needs enough candidates to fill the page,
# whatever n_results says: a Loci one-liner is twenty-odd tokens, so this is
# what one candidate is assumed to cost when the budget sizes the per-store
# ask (each adapter still clamps the ask to its store's own cap).
_TOKENS_PER_CANDIDATE = 16
_LEAD_ROWS = 5      # a store's first rows speak for it when names are compared across stores (names.py)
_NAME_FACTS = 3     # the packet's first facts are the ones whose names are asked of the stores that keep topics
_NAME_STRONG = 1.0  # spreads inside its store's usual at which a fact may ask by name with nothing shared (fit.py)


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
    habit_floor: float = 0.0     # options.habit_floor: rows this many spreads past the store's usual best are set aside (see _set_aside)


def _as_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _same_words(text) -> str:
    """A row's text with its spacing and case taken out: equal for two rows
    that say the same thing word for word."""
    return " ".join(str(text or "").split()).casefold()


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
                 health_tracker: HealthTracker | None = None, fit: FitTracker | None = None,
                 names: NameTally | None = None):
        self._config = config
        self._transport = transport
        self._health = health_tracker or HealthTracker()
        # Which names are on nearly everything in this house (names.py), learned
        # from every packet and handed on across a /configure rebuild like the habit.
        self._names = names or NameTally()
        # Each store's habit (fit.py): what its best hit usually looks like,
        # learned from every question so a store that sets rows aside
        # (options.habit_floor) does not start cold. Handed on across a
        # /configure rebuild like the health tracker, for the same reason.
        self._fit = fit or FitTracker()
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
            self._stores.append(_BoundStore(adapter=adapter, floor=cfg.floor, weight=cfg.weight,
                                            habit_floor=max(0.0, _as_float((cfg.options or {}).get("habit_floor")) or 0.0)))

    @property
    def store_names(self) -> list[str]:
        return [b.adapter.name for b in self._stores]

    @property
    def health(self) -> HealthTracker:
        """The health tracker collecting per-store metrics."""
        return self._health

    @property
    def fit(self) -> FitTracker:
        """Each store's habit: what its best hit usually looks like."""
        return self._fit

    @property
    def names(self) -> NameTally:
        """Which names are on nearly everything here, and so say nothing."""
        return self._names

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
        fused = await self._fan(query, n_results, outcome, budget, first=True)

        # MULTI-HOP: fusion reorders a packet; it cannot ADD a document retrieval
        # never returned. A second round can. Off by default (Nano floor).
        hops = max(1, getattr(self._config, "hops", 1))
        if hops > 1 and fused:
            fused = await self._append_hop_hits(fused, query, n_results, hops, budget)

        # Every row says which names it shares with the other stores' lead rows
        # (names.py): the one thing two stores have in common whatever their
        # embedders. It changes no order; it is there to be read.
        self._mark_shares(fused)

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
        # And the same join from the other side: a FACT's names (its project and
        # key) asked of the stores that keep topics, so the episodes tagged with
        # the thing a fact is about come with it. Off unless name_budget > 0.
        if getattr(self._config, "name_budget", 0) > 0:
            fused = await self._append_name_edges(fused, budget, query)
        answered = [n for n, err in outcome.items() if err is None]
        failed = [(n, err) for n, err in outcome.items() if err is not None]
        return FanReport(hits=fused, answered=answered, failed=failed, skipped=self.skipped,
                         budget_tokens=budget, tokens=packet_cost(fused))

    def _mark_shares(self, fused: list[FusedHit]) -> None:
        """Put `shares` on every row that has a name in common with the lead
        rows of ANOTHER store: a memory's topic tags against a fact's project
        and key (names.py; Chad, 25 Nov 2026: "MEMORY HAS TOPIC A PARALLEL TO
        LOCIS KEYS"). A row that shares a name is on the subject the stores
        agree about. One that shares none says the stores disagree and no more
        than that. Names on nearly everything in this house are left out; the
        tally learns from the packet AFTER it is read."""
        mine = [(f, {n for n in row_names(f.hit) if not self._names.common(n)}) for f in fused]
        lead: dict[str, set[str]] = {}
        seated: dict[str, int] = {}
        for f, names in mine:
            s = f.hit.store
            if seated.get(s, 0) < _LEAD_ROWS:
                seated[s] = seated.get(s, 0) + 1
                lead.setdefault(s, set()).update(names)
        if len(lead) > 1:
            for f, names in mine:
                others = set().union(*(v for s, v in lead.items() if s != f.hit.store))
                both = shared_names(names, others)
                if both:
                    f.hit.metadata = {**(f.hit.metadata or {}), "shares": both}
        self._names.note(row_names(f.hit) for f in fused)

    async def _append_name_edges(self, fused: list[FusedHit], budget: Optional[int] = None,
                                 query: Optional[str] = None) -> list[FusedHit]:
        """Append the episodes tagged with a fact's NAME, marked 'name-edge'.

        The topic edges above start from the packet's memories: their tags are
        asked of the stores that keep topics. A fact carries no tag, so a fact
        never brought the episodes about the thing it names; when the question
        found the fact and not the story, the story stayed home. This asks the
        same /by_topic with a fact's names.

        WHICH FACTS, AND WHICH OF THEIR NAMES. The first cut asked every word
        of the first three facts' keys, and on a real memory the generic words
        won: 'build' out of borrow-dont-build-on brought two episodes about
        nothing of the kind, and a fact that had no business in the packet
        brought its own strangers. So a fact must be ON THE SUBJECT to bring
        anything, and there are two ways to be:
          by agreement  it shares a name with the other stores' lead rows
                        (`shares`). It asks its project, its key whole, and
                        the words it shares: the stores have already said
                        those are the ones this question is about.
          by strength   its distance sits at least _NAME_STRONG spreads inside
                        its store's own usual (fit.py). It asks its project,
                        its key whole, and every word of the key that is not
                        on everything here. This is the fact that answers the
                        question while the other store has nothing: the case
                        where the story most needs fetching.
        A project made only of names that are on everything is not asked.

        One ask per fact, the facts taking turns, so one cannot crowd out the
        rest. An edge says which fact brought it (edge_of) and which names
        matched (matched_topics). Up to name_budget of them, never one already
        in the packet, and on a page only what still fits. Exact tags only:
        /by_topic does not forgive a spelling, so 'license' does not find
        'licence'.

        ONE OF EACH (26 Nov 2026). The question goes along, so the store
        orders what is tagged by how near it sits to the question and not
        by its date alone; and a fact's turn goes first to a TIER the edges
        do not hold yet. With two edges that is the settled core and this
        week's note: the story and the news. Chad agreed to that, and to how
        the two are then ordered: the nearer first, and at a tie short, then
        near, then long."""
        capable = [b for b in self._stores if hasattr(b.adapter, "search_by_topic")]
        facts = [f for f in fused if f.rrf_score != 0.0 and (f.hit.metadata or {}).get("key") is not None][:_NAME_FACTS]
        if not capable or not facts:
            return fused
        asks: list[tuple[str, str, list[str]]] = []     # (fact id, its own store, the names it asks)
        for f in facts:
            md = f.hit.metadata or {}
            shares = [str(s) for s in md.get("shares") or []]
            close = self._fit.closeness(f.hit.store, f.hit.raw_distance)
            strong = close is not None and close >= _NAME_STRONG
            if not shares and not strong:
                continue
            project, key = str(md.get("project") or "").lower(), str(md.get("key") or "").lower()
            words = set(shares) | ({t for t in name_tokens(key) if not self._names.common(t)} if strong else set())
            p_words = name_tokens(project)
            offers = [project] if p_words and not all(self._names.common(t) for t in p_words) else []
            offers += [key] + sorted(words - {project, key})
            asks.append((f.hit.id, f.hit.store, offers))
        # A fact asks the OTHER stores by its name, never its own: since Loci
        # grew a lookup by name too (26 Nov 2026) its own store would answer
        # with the fact's siblings, and this edge is for the story, not for
        # more facts.
        calls = [(i, b) for i, (_fid, own, _names) in enumerate(asks) for b in capable if b.adapter.name != own]
        if not calls:
            return fused
        count = self._config.name_budget
        exclude = [f.hit.id for f in fused]
        # ask for more than will ride, so that more than one tier is there to choose from
        results = await asyncio.gather(*(self._safe_topic_search(b, asks[i][2], max(count * 3, 6), exclude, query)
                                         for i, b in calls))
        queues: list[list[Hit]] = [[] for _ in asks]
        for (i, _b), (_store, hits) in zip(calls, results):
            for h in hits:
                h.metadata = {**(h.metadata or {}), "source": "name-edge", "edge_of": asks[i][0]}
                queues[i].append(h)
        edges: list[Hit] = []
        seen: set[str] = set()
        tier_of = lambda h: str((h.metadata or {}).get("tier") or "")   # noqa: E731
        while len(edges) < count and any(queues):
            for q in queues:
                q[:] = [h for h in q if h.id not in seen]
                if not q:
                    continue
                held = {tier_of(e) for e in edges}
                h = next((x for x in q if tier_of(x) not in held), q[0])   # a tier not held yet, else its best
                q.remove(h)
                seen.add(h.id)
                edges.append(h)
                if len(edges) >= count:
                    break
        order = {"short": 0, "near": 1, "long": 2}
        edges.sort(key=lambda h: (round(h.raw_distance, 2) if h.raw_distance is not None else 9.0,
                                  order.get(tier_of(h), 3)))            # stable: no distances, no reordering
        edge_fused = [FusedHit(hit=h, rrf_score=0.0, store_rank=i + 1) for i, h in enumerate(edges)]
        if budget is not None:
            edge_fused, _ = fit_in_budget(edge_fused, self._remaining(fused, budget))
        return fused + edge_fused

    def _budget(self, budget_tokens: Optional[int]) -> Optional[int]:
        """The page size for one call: the caller's, else the config's default,
        else None (a row-count packet). A value <= 0 means none."""
        b = budget_tokens if budget_tokens is not None else getattr(self._config, "budget_tokens", None)
        return b if b is not None and b > 0 else None

    @staticmethod
    def _remaining(fused: list[FusedHit], budget: Optional[int]) -> Optional[int]:
        """Room left on the page after `fused`; None when there is no page."""
        return None if budget is None else max(0, budget - packet_cost(fused))

    def _note_habit(self, lists: dict[str, list[Hit]]) -> None:
        """Let each store's habit learn from this question: its best hit's
        distance (fit.py). Called AFTER anything that reads the habit, so a
        question is never held against itself."""
        for name, hits in lists.items():
            if hits:
                self._fit.note(name, best_distance(hits))

    def _set_aside(self, lists: dict[str, list[Hit]]) -> dict[str, list[Hit]]:
        """{store: the rows it sets aside for this question}, for the stores
        that asked (options.habit_floor, in spreads; 25 Nov 2026).

        A store returns its top rows whether or not it has anything to say: on
        one real page six of twelve rows were facts unrelated to the question,
        each scored 0.93 by its store. Moving SEATS between stores by how close
        each store's best hit came did not fix it (fit.py). This judges each
        ROW instead: one sitting more than habit_floor spreads past its store's
        own usual best hit is set aside, and leads nobody. It is not dropped:
        it follows the rows that are inside their habit and rides whenever the
        packet has room. A store's best row is never set aside, nor one with no
        distance to judge, nor anything while the store has no habit yet.

        Per store, and off unless asked, because it is only right for a store
        whose ranking follows distance and a memory asked about what it holds.
        Measured on Vellmarch's frozen answers: at 1.0 it would clear about
        half of Loci's rows and lose 0 to 2 in 100 of its answers on ordinary
        questions; the same floor on Memory, whose ranking deliberately departs
        from distance, would lose 13 to 24 in 100. And where a question
        describes a thing without naming it (the epithet set) the RIGHT fact
        sits as far out as junk does: a floor at 1.0 would set aside half of
        those answers. Distance cannot tell far-because-unrelated from
        far-because-asked-sideways. So: a knob for a house, not a default."""
        late: dict[str, list[Hit]] = {}
        for bound in self._stores:
            name, floor = bound.adapter.name, bound.habit_floor
            hits = lists.get(name) or []
            if floor <= 0 or len(hits) < 2:
                continue
            out = []
            for h in hits[1:]:
                c = self._fit.closeness(name, h.raw_distance)
                if c is not None and c < -floor:
                    h.metadata = {**(h.metadata or {}), "past_usual": round(-c, 2)}   # how far out, for whoever reads the packet
                    out.append(h)
            if out:
                late[name] = out
        return late

    async def _fan(self, query: str, n_results: Optional[int] = None,
                   outcome: Optional[dict[str, Optional[str]]] = None,
                   budget: Optional[int] = None,
                   first: bool = False) -> list[FusedHit]:
        """ONE retrieval round: fan out, floor, fuse. The unit a hop repeats.
        `outcome`, when given, collects each store's result this round: None
        for answered, the reason for failed. `budget` packs the round to a
        page (and sizes the ask to fill one) instead of trimming to n. `first`
        marks the first round of a search, never a hop's: the only round in
        which a store sets rows aside and the only one its habit learns from."""
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

        # Rows a store was told to set aside (options.habit_floor) are read off
        # BEFORE the habit learns from this question; hops never set aside.
        late = self._set_aside(ranked_lists) if first else {}
        if first:
            self._note_habit(ranked_lists)

        cap = n if budget is None else n_results     # a page replaces the DEFAULT row cap; a caller's explicit n still caps
        if not late:
            return rrf_fuse(
                ranked_lists,
                k=self._config.k,
                weights=weights,
                n_results=cap,
                fusion_mode=self._config.fusion_mode,
                min_per_store=self._config.min_per_store,
                budget_tokens=budget,
            )

        # Two fuses, one packet: the rows inside their store's habit lead, in
        # the turns RRF gives them; the rows set aside follow, in theirs, scored
        # just under the lead's last row so the packet still falls from top to
        # bottom. Then ONE trim or pack over the whole, so a set-aside row rides
        # whenever there is room, and min_per_store still seats every store.
        rank_of = {(name, h.id): i + 1 for name, hits in ranked_lists.items() for i, h in enumerate(hits)}
        gone = {(name, h.id) for name, hits in late.items() for h in hits}
        kept = {name: [h for h in hits if (name, h.id) not in gone] for name, hits in ranked_lists.items()}
        lead = rrf_fuse(kept, k=self._config.k, weights=weights, fusion_mode=self._config.fusion_mode)
        tail = rrf_fuse({name: late.get(name, []) for name in ranked_lists}, k=self._config.k, weights=weights,
                        fusion_mode=self._config.fusion_mode)
        low = min((f.rrf_score for f in lead), default=0.0)
        high = max((f.rrf_score for f in tail), default=0.0)
        if low > 0 and high > 0:
            for f in tail:
                f.rrf_score *= low / high * 0.999
        fused = lead + tail
        for f in fused:
            f.store_rank = rank_of.get((f.hit.store, f.hit.id), f.store_rank)   # a row still says its own rank in its store
        if budget is not None and budget > 0:
            return pack_to_budget(fused, budget, self._config.min_per_store, cap)
        return _trim_with_quota(fused, cap, self._config.min_per_store) if cap is not None else fused

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
        has it once already. Nor does an episode ride that repeats, word for
        word, a row or an edge already in the packet: a core that was restated
        keeps its first wording as an episode, and in match order that copy is
        the nearest thing to the question there is (25 Nov 2026, on a memory
        that grew: 139 and 233 tokens of one page spent on rows it already
        held). Edges never enter the floor or the fuse."""
        top = max(0, getattr(self._config, "satellite_top", 0))
        seen = {f.hit.id for f in rows}
        said = {_same_words(f.hit.content) for f in rows}
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
                words = _same_words(r.get("content"))
                if rid and rid not in seen and (not words or words not in said):
                    seen.add(rid)
                    said.add(words)
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
                            # the other cores this episode hangs on, when Memory says so:
                            # a pointer for a second look, where content would not be cheap
                            **({"also_core_ids": [str(c) for c in r["also_core_ids"]]}
                               if isinstance(r.get("also_core_ids"), list) and r["also_core_ids"] else {}),
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
        """Take every hit's inline `rests_on` off its metadata and hand them
        back by the citing fact's id. What stays on the row: how many there are
        (rests_on_n) and, since 25 Nov 2026, where each one lives
        (rests_on_keys, "project/key"). Chad: "if content is cheap to carry
        lets carry it, if its not then... we go to ids". The few reasons the
        packet has room for ride in full as citation edges; every reason is
        at least NAMED on the fact that rests on it, and a Loci key is a name
        a reader can make sense of before fetching it."""
        cited: dict[str, list[dict]] = {}
        for f in fused:
            meta = f.hit.metadata or {}
            got = meta.pop("rests_on", None)
            if isinstance(got, list):
                meta["rests_on_n"] = len(got)
                keys = [f"{r.get('project') or '*'}/{r['key']}" for r in got if isinstance(r, dict) and r.get("key")]
                if keys:
                    meta["rests_on_keys"] = keys
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
                                 n: int, exclude_ids: list[str], query: Optional[str] = None
                                 ) -> tuple[str, list[Hit]]:
        """Topic-edge twin of _safe_search: call one store's search_by_topic with
        a timeout; ANY failure -> empty list. Same graceful-degradation contract -
        an edge join that errors just yields no edges, never sinks the packet."""
        name = bound.adapter.name
        try:
            hits = await asyncio.wait_for(
                (bound.adapter.search_by_topic(topics, n, exclude_ids=exclude_ids, query=query) if query
                 else bound.adapter.search_by_topic(topics, n, exclude_ids=exclude_ids)),
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
        honest. A store without search_by_topic is skipped by capability
        check; if nothing's capable or there are no topics, the packet returns
        untouched. On a page (`budget`) an edge also has to fit in what the
        packet left of it.

        Since 26 Nov 2026:
          the question goes along, so a store orders what is tagged by how
          near it sits to the question and not by its date alone (a match is
          usually on one tag; by date, this week's notes beat every settled
          core). A store from before that ignores it.
          a fact store answers too (Loci's /by_name): the packet's topics are
          names, and the facts filed under them are the facts about what the
          packet's episodes are about.
          the stores take turns, so the first store's matches cannot take
          every seat before the second is heard.
          a topic made only of names that are on everything in this house
          (names.py) is not asked: it matches everything and says nothing."""
        topics = [t for t in _collect_topics(fused)
                  if not (name_tokens(t) and all(self._names.common(w) for w in name_tokens(t)))]
        if not topics:
            return fused
        capable = [b for b in self._stores
                   if hasattr(b.adapter, "search_by_topic")]
        if not capable:
            return fused
        count = self._config.edge_budget
        exclude = [f.hit.id for f in fused]
        results = await asyncio.gather(
            *(self._safe_topic_search(b, topics, count, exclude, query) for b in capable)
        )
        queues = [list(hits) for _name, hits in results]
        edges: list[Hit] = []
        while len(edges) < count and any(queues):
            for q in queues:
                if q and len(edges) < count:
                    edges.append(q.pop(0))
        edge_fused = [FusedHit(hit=h, rrf_score=0.0, store_rank=i + 1)
                      for i, h in enumerate(edges)]
        if budget is not None:
            edge_fused, _ = fit_in_budget(edge_fused, self._remaining(fused, budget))
        return fused + edge_fused
