"""
seren_corpus_callosum.mcp.tools
═══════════════════════════════

The one tool the callosum exposes: `search`. It wraps the Federation directly
(in-process; we're mounted INTO the same FastAPI app that owns it, so there's
no point HTTP-round-tripping ourselves).

STRUCTURE

`SccToolImpl` holds the tool as a method; `register_tools` wires it onto a
FastMCP instance via `@mcp.tool()`. The split exists for testability -
`await SccToolImpl(fed).search(...)` is directly callable in unit tests without
FastMCP, an MCP client, or an HTTP roundtrip. See `tests/test_mcp_tools.py`.

NAMING: the tool is `search` - bare, the umbrella verb. Next to SerenLoci's
`search_loci` and SerenMemory's `recall`/`search_memory`, a model reads the
trio cleanly: `search` = the whole brain, the hemisphere-specific tools = one
side. The callosum's tool matching its `/search` route (and the family's route
name) is the point: it presents the same interface it consumes.

ASYNC: the fan is parallel async, so `search` is `async def`. FastMCP awaits
coroutine tools, so the decoration is identical to a sync tool's.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    from ..federation import Federation


class SccToolImpl:
    """The tool implementation, callable both via FastMCP decoration (in
    production) and directly (in unit tests). The return shape is
    JSON-serialisable - the FastMCP layer serialises it on the way out."""

    def __init__(self, federation: "Federation") -> None:
        self.federation = federation

    async def search(self, query: str, n_results: int = 10) -> dict:
        """Search ALL of Seren's memory in one call - the default recall.

        Fans every configured store (the left brain's facts, the right brain's
        episodic memory, and any other store wired into this callosum), then
        merges them into one ranked list with Reciprocal Rank Fusion. RRF reads
        only each store's RANK ordering, never its raw scores, so the merge is
        correct even when stores run different embedders.

        Prefer this over the hemisphere-specific tools (`search_loci`,
        `recall`) unless you specifically want just one side. Every hit carries
        provenance - which `store` it came from, its `store_rank` there, the
        cross-store `score` it was ranked by, and the within-store
        `base_relevance` - so the merge is explainable. `stores_searched` and
        `skipped` tell you which stores actually answered; a slow or down store
        degrades the result, it never takes the call down.
        """
        fused = await self.federation.search(query, n_results=n_results)
        return {
            "query": query,
            "hits": [
                {
                    "store": f.hit.store,
                    "id": f.hit.id,
                    "content": f.hit.content,
                    "score": f.rrf_score,
                    "store_rank": f.store_rank,
                    "base_relevance": f.hit.base_relevance,
                    "native_score": f.hit.native_score,
                    "raw_distance": f.hit.raw_distance,
                    "metadata": f.hit.metadata,
                }
                for f in fused
            ],
            "stores_searched": self.federation.store_names,
            "skipped": [{"name": n, "reason": r} for n, r in self.federation.skipped],
        }


# ═══════════════════════════════════════════════════════════════════════
#  Registration entry point
# ═══════════════════════════════════════════════════════════════════════
def register_tools(mcp: FastMCP, federation: "Federation") -> SccToolImpl:
    """Attach the SccToolImpl tool(s) to the given FastMCP instance via the
    @mcp.tool() decorator. Returns the impl object so callers that need a handle
    (e.g. direct invocation in tests) can keep one."""
    impl = SccToolImpl(federation)

    mcp.tool()(impl.search)

    return impl
