"""
seren_corpus_callosum.routes.health
═══════════════════════════════════════════════════════════════════════

Per-store health metrics endpoint for the SCC viewer's Health tab.

    GET /health/stores  -  latency/error/health snapshot per store

Data is collected by the HealthTracker inside the Federation and exposed
here so the viewer can render per-store health panels. The tracker never
blocks the fan - this is visibility without overhead.
"""
from __future__ import annotations

import time
from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health/stores")
async def health_stores(request: Request) -> dict:
    """Return per-store health metrics collected by the Federation's
    HealthTracker. Returns a list of store snapshots plus a summary.

    Shape:
      { "stores": [...], "healthy": N, "degraded": N, "unhealthy": N,
        "unknown": N, "ts": timestamp }
    """
    fed = getattr(request.app.state, "federation", None)
    if fed is None:
        return {"stores": [], "healthy": 0, "degraded": 0,
                "unhealthy": 0, "unknown": 0, "ts": time.time()}

    snapshots = await fed.health.snapshot_all()
    counts: dict[str, int] = {"healthy": 0, "degraded": 0,
                              "unhealthy": 0, "unknown": 0}
    for s in snapshots:
        status = s.get("health_status", "unknown")
        counts[status] = counts.get(status, 0) + 1

    return {
        "stores": snapshots,
        **counts,
        "ts": time.time(),
    }
