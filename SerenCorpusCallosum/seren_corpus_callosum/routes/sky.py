"""
seren_corpus_callosum.routes.sky
════════════════════════════════════════════════════════════════════════

GET /sky - both hemispheres in one picture, for the viewer's Sky (23 Oct 2026).

The user: "callosum gets the city, and the nebula above it". The left brain's
city (Loci's GET /city: a tower per project, a floor per fact, skyways where
one fact rests on another) and the right brain's nebula (Memory's
GET /long/map: every core and satellite placed by what it means), fetched
through the same stores the callosum fans, with the same bearers. The first
enabled store of each kind is drawn; the viewer asks /search to bridge them.

Read-only like everything here: two GETs, nothing written anywhere. A store
that cannot be reached leaves its half of the picture empty and says why.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

router = APIRouter(tags=["sky"])

# what each kind of store draws, and where it keeps it
_DRAWS = {"seren_loci": ("city", "/city"), "seren_memory": ("nebula", "/long/map")}
MAP_TIMEOUT_S = 30.0          # a map is one read of a whole store, not a search


@router.get("/sky")
async def sky(request: Request):
    tx = request.app.state.transport
    out: dict = {"city": None, "nebula": None, "from": {}, "failed": {}}
    picks: dict[str, object] = {}
    for s in request.app.state.config.federation.enabled_stores:
        half = _DRAWS.get(s.type)
        if half and half[0] not in picks:
            picks[half[0]] = s
    get = getattr(tx, "get_json", None)

    async def fetch(half: str, s) -> None:
        if get is None:
            out["failed"][half] = "this transport cannot fetch maps"
            return
        tok = s.resolve_token()
        try:
            out[half] = await get(f"{s.url}{_DRAWS[s.type][1]}",
                                  headers={"Authorization": f"Bearer {tok}"} if tok else None,
                                  timeout=MAP_TIMEOUT_S)
            out["from"][half] = s.name
        except Exception as e:  # noqa: BLE001 - half a sky is still a sky
            out["failed"][half] = f"{s.name}: {type(e).__name__}: {e}"[:300]

    await asyncio.gather(*(fetch(h, s) for h, s in picks.items()))
    for half in ("city", "nebula"):
        if half not in picks:
            out["failed"].setdefault(half, "no store of that kind is fanned")
    return out
