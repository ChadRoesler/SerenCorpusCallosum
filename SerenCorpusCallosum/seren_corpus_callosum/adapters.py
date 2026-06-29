"""
seren_corpus_callosum.adapters
════════════════════════════════════════════════════════════════════════

An adapter's ONE job: call a store's search and map its native response into
the common `Hit` shape, PRESERVING the store's ranking order (list position
is rank - fusion depends on it). Everything store-specific lives here and
nowhere else.

THE EXTENSIBILITY MODEL (why the gift is a config line):
    One adapter per store *protocol*, not per instance. `SerenMemoryAdapter`
    speaks the SerenMemory `/search` contract - so it serves EVERY
    SerenMemory-speaking instance you ever fan in. Adding a tenth dedicated
    memory is a config entry against this same adapter, zero new code.
    `SerenLociAdapter` speaks Loci's facts-search shape. A genuinely new
    kind of store gets a new adapter here and a registry entry below; that's
    the only time "hook in a store" costs code.

TRANSPORT IS INJECTED:
    Adapters take a `Transport` (an async `post_json`). Real wiring uses the
    httpx-backed one in transport.py; tests inject a fake that returns canned
    native responses. This keeps the response-mapping (the part we can verify
    against real bytes) testable in isolation from live HTTP.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from .config import StoreConfig
from .fusion import Hit, base_relevance_from_distance


@runtime_checkable
class Transport(Protocol):
    """Minimal async HTTP seam. Real impl in transport.py; fakes in tests."""

    async def post_json(self, url: str, payload: dict[str, Any],
                        headers: Optional[dict[str, str]] = None) -> dict[str, Any]:
        ...


class StoreAdapter(Protocol):
    """What the federation needs from any store. `name`, `weight`, and
    `floor` come straight from config; `search` returns ranked Hits."""

    name: str
    weight: float
    floor: float

    async def search(self, query: str, n: int) -> list[Hit]:
        ...


class _BaseAdapter:
    """Shared plumbing: holds config + transport, exposes name/weight/floor."""

    # Subclasses override: the request path and the response mapper.
    default_search_path = "/search"

    def __init__(self, cfg: StoreConfig, transport: Transport):
        self._cfg = cfg
        self._transport = transport
        self.name = cfg.name
        self.weight = cfg.weight
        self.floor = cfg.floor
        # Resolve this store's OUTBOUND bearer ONCE (the resolver may hit the OS
        # keychain - too slow to do per search). "" -> open store -> no header.
        # Same shared resolver the services use inbound, pointed outbound here.
        _tok = cfg.resolve_token()
        self._auth_headers: Optional[dict[str, str]] = (
            {"Authorization": f"Bearer {_tok}"} if _tok else None)

    @property
    def _search_url(self) -> str:
        # Path is overridable via options.search_path so a store on a
        # non-standard route is a config tweak, not a code edit.
        path = self._cfg.options.get("search_path", self.default_search_path)
        return f"{self._cfg.url}{path}"


class SerenMemoryAdapter(_BaseAdapter):
    """Speaks the SerenMemory HTTP contract (verified against its routes/search.py):

        POST /search  {query, n_results, include_short/near/long, include_superseded}
        -> {query, hits: [{tier, content, topic, score, raw_distance, id, metadata}], searched_tiers}

    The hits arrive already merged-and-ranked across SerenMemory's three
    tiers (its route does that internally), so from SCC's view this is ONE
    ranked source. We keep that order; we read raw_distance for our own
    base_relevance (the floor signal) and carry SerenMemory's tier-weighted
    `score` as native_score for display.
    """

    type = "seren_memory"
    default_topic_search_path = "/by_topic"

    async def search(self, query: str, n: int) -> list[Hit]:
        opts = self._cfg.options
        payload = {
            "query": query,
            "n_results": n,
            "include_short": bool(opts.get("include_short", True)),
            "include_near": bool(opts.get("include_near", True)),
            "include_long": bool(opts.get("include_long", True)),
            "include_superseded": bool(opts.get("include_superseded", False)),
        }
        resp = await self._transport.post_json(self._search_url, payload, headers=self._auth_headers)
        hits: list[Hit] = []
        for raw in (resp.get("hits") or []):
            try:
                dist = float(raw["raw_distance"])
            except (KeyError, TypeError, ValueError):
                # No distance -> can't floor meaningfully; treat as a perfect
                # match's neighbor so it isn't silently floored out. Display
                # still shows native_score.
                dist = 0.0
            meta = dict(raw.get("metadata") or {})
            meta.setdefault("tier", raw.get("tier"))
            meta.setdefault("topic", raw.get("topic"))
            hits.append(Hit(
                store=self.name,
                id=str(raw.get("id", "")),
                content=raw.get("content", ""),
                base_relevance=base_relevance_from_distance(dist),
                raw_distance=dist,
                native_score=_as_float_or_none(raw.get("score")),
                metadata=meta,
            ))
        return hits

    async def search_by_topic(self, topics: list[str], n: int,
                              exclude_ids: Optional[list[str]] = None) -> list[Hit]:
        """The ASSOCIATION edge: POST /by_topic (verified against SerenMemory's
        routes/search.py) - entries sharing a topic tag with the packet, by EXACT
        tag match, NOT vector similarity:

            POST /by_topic {topics, n_results, include_short/near/long,
                            include_superseded, exclude_ids}
            -> {topics, hits: [{tier, content, topic, matched_topics, overlap,
                                id, metadata}], searched_tiers}

        Mapped into Hits MARKED source='topic-edge' so a consumer sees they're
        here by association, not magnitude. base_relevance is 0.0 - there's no
        cosine for a tag match; the strength signal is `overlap`, surfaced as
        native_score and in metadata. exclude_ids passes the packet's ids through
        so the join returns only NEW context. Only SerenMemory speaks this; the
        federation calls it behind a capability check, so a store without it
        (Loci) is simply skipped.
        """
        opts = self._cfg.options
        path = opts.get("topic_search_path", self.default_topic_search_path)
        url = f"{self._cfg.url}{path}"
        payload = {
            "topics": list(topics),
            "n_results": n,
            "include_short": bool(opts.get("include_short", True)),
            "include_near": bool(opts.get("include_near", True)),
            "include_long": bool(opts.get("include_long", True)),
            "include_superseded": bool(opts.get("include_superseded", False)),
            "exclude_ids": list(exclude_ids or []),
        }
        resp = await self._transport.post_json(url, payload, headers=self._auth_headers)
        hits: list[Hit] = []
        for raw in (resp.get("hits") or []):
            meta = dict(raw.get("metadata") or {})
            meta.setdefault("tier", raw.get("tier"))
            meta.setdefault("topic", raw.get("topic"))
            meta["matched_topics"] = raw.get("matched_topics") or []
            meta["overlap"] = raw.get("overlap")
            meta["source"] = "topic-edge"   # provenance: association, not similarity
            hits.append(Hit(
                store=self.name,
                id=str(raw.get("id", "")),
                content=raw.get("content", ""),
                base_relevance=0.0,          # no cosine for a tag match; see overlap
                raw_distance=None,
                native_score=_as_float_or_none(raw.get("overlap")),
                metadata=meta,
            ))
        return hits


class SerenLociAdapter(_BaseAdapter):
    """Speaks Loci's facts-search shape (verified against a live search_loci call):

        -> {query, project, finder, hits: [{id, project, key, value, why,
            score, match_kind, source, raw_distance}]}

    A fact's surfaced content is `key = value because why` — the same recipe
    Loci's own `_finder_text` uses for embedding, so SCC fusion sees
    meaningful text from both stores. The raw key/value/why ride in metadata
    for callers that need to parse them separately. Loci's `score` IS the
    within-store base relevance already
    (1/(1+distance), and 1.0 for an exact-key hit), so we use it directly
    rather than recomputing - that way exact matches correctly read as 1.0.

    HTTP CONTRACT - CONFIRMED against seren_loci/routes/search.py +
    models/schemas.py: POST /search with SearchRequest
    {query, project?, n_results, include_fundamentals, include_superseded}
    -> SearchResponse {query, project, hits: [SearchHit], finder}. Loci's
    `score` is, in its own schema's words, "the SCC common currency":
    normalized 0..1, exact-key hit = 1.0 - so using it directly as
    base_relevance is the intended design, not a convenient guess. Path is
    still overridable via options.search_path for non-standard deployments.
    """

    type = "seren_loci"

    async def search(self, query: str, n: int) -> list[Hit]:
        opts = self._cfg.options
        payload: dict[str, Any] = {
            "query": query,
            "n_results": n,
            "include_fundamentals": bool(opts.get("include_fundamentals", True)),
            "include_superseded": bool(opts.get("include_superseded", False)),
        }
        # Optional project scope - None means "search every scope".
        if opts.get("project") is not None:
            payload["project"] = opts["project"]

        resp = await self._transport.post_json(self._search_url, payload, headers=self._auth_headers)
        hits: list[Hit] = []
        for raw in (resp.get("hits") or []):
            score = _as_float_or_none(raw.get("score"))
            base = score if score is not None else base_relevance_from_distance(
                _as_float_or_none(raw.get("raw_distance")) or 0.0)
            hits.append(Hit(
                store=self.name,
                id=str(raw.get("id", "")),
                # Construct meaningful content from key + value + why — same recipe
                # Loci's own _finder_text uses for embedding. Raw fields stay in metadata.
                content=_loci_content(raw),
                base_relevance=base,
                raw_distance=_as_float_or_none(raw.get("raw_distance")),
                native_score=score,
                metadata={
                    "project": raw.get("project"),
                    "key": raw.get("key"),
                    "why": raw.get("why"),
                    "match_kind": raw.get("match_kind"),
                    "source": raw.get("source"),
                },
            ))
        return hits


# Adapter registry: type string -> class. Adding a new store protocol means
# adding one class above and one line here. That's the whole extension point.
_REGISTRY: dict[str, type[_BaseAdapter]] = {
    SerenMemoryAdapter.type: SerenMemoryAdapter,
    SerenLociAdapter.type: SerenLociAdapter,
}


class UnknownStoreType(ValueError):
    """Raised when a StoreConfig.type has no registered adapter."""


def build_adapter(cfg: StoreConfig, transport: Transport) -> StoreAdapter:
    """Construct the adapter for a store config. Raises UnknownStoreType for
    an unregistered type - the federation catches this and skips the store
    (graceful degradation), so a typo in one entry never sinks the whole fan."""
    cls = _REGISTRY.get(cfg.type)
    if cls is None:
        raise UnknownStoreType(
            f"no adapter for store type {cfg.type!r} (have: {sorted(_REGISTRY)})")
    return cls(cfg, transport)  # type: ignore[return-value]


def register_adapter(type_key: str, cls: type[_BaseAdapter]) -> None:
    """Register a custom adapter type at runtime (for out-of-tree store kinds)."""
    _REGISTRY[type_key] = cls


def known_store_types() -> set[str]:
    """The store types with a registered adapter. Lets the add-store API reject
    an unknown type up front (a clean 400) instead of accepting it and having
    the fan silently skip it forever."""
    return set(_REGISTRY)


def _loci_content(raw: dict[str, Any]) -> str:
    """Build meaningful surfaced content from a Loci search hit.

    Loci stores facts as {key, value, why}. The raw `value` alone is often
    a bare scalar (\"3\", \"true\") that tells a reader nothing. We combine
    them the same way Loci's own _finder_text does — ``key = value because
    why`` — so SCC fusion sees text it can rank sensibly and callers get
    a self-explanatory result line. Missing fields degrade gracefully:
    no why -> just \"key = value\", no key -> just value.
    """
    key = raw.get("key") or ""
    val = raw.get("value") or ""
    why = raw.get("why")
    if key and val:
        parts = [key, "=", val]
    elif val:
        parts = [val]
    else:
        parts = [key] if key else [val]
    if why:
        parts.extend(["because", why])
    return " ".join(parts)


def _as_float_or_none(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
