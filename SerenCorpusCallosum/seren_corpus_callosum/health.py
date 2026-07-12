"""
seren_corpus_callosum.health
════════════════════════════════════════════════════════════════════════

Per-store health tracking - latency, error rate, success/failure counts,
and overall health status. Exposed via /health/stores so the viewer can
render a health dashboard panel.

Every `_safe_search` call records its outcome here, making the tracker
the single source of truth for per-store operational metrics.

Nano-floor ethos: the tracker is additive and never blocks a search.
If recording fails or the tracker isn't wired, the fan proceeds - partial
visibility beats no visibility.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StoreHealthRecord:
    """Per-store rolling health data.

    Fields are updated by the federation after each fan call. The viewer
    reads these to render latency/error/health panels.
    """
    name: str
    # - counts -
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    # - latency (seconds, rolling window) -
    last_latency: float = 0.0
    avg_latency: float = 0.0          # exponential moving average (α=0.2)
    max_latency: float = 0.0
    # - errors -
    last_error: str = ""
    error_count: int = 0
    consecutive_failures: int = 0
    # - timestamps -
    last_call_ts: float = 0.0
    last_success_ts: float = 0.0

    def record_success(self, latency: float) -> None:
        """Call after a successful store search."""
        self.total_calls += 1
        self.successful_calls += 1
        self.last_latency = latency
        self.avg_latency = 0.2 * latency + 0.8 * self.avg_latency  # EMA
        self.max_latency = max(self.max_latency, latency)
        self.consecutive_failures = 0
        self.last_call_ts = time.time()
        self.last_success_ts = self.last_call_ts

    def record_failure(self, error: str, latency: float) -> None:
        """Call after a failed store search (timeout, transport, or adapter
        error). The error string is kept as the last error for diagnostics."""
        self.total_calls += 1
        self.failed_calls += 1
        self.last_latency = latency
        self.avg_latency = 0.2 * latency + 0.8 * self.avg_latency
        self.max_latency = max(self.max_latency, latency)
        self.error_count += 1
        self.last_error = error[:120]  # truncate long error messages
        self.consecutive_failures += 1
        self.last_call_ts = time.time()

    @property
    def health_status(self) -> str:
        """Derived health: 'healthy', 'degraded', or 'unhealthy'.

        Healthy:   >90% success rate, no recent failures
        Degraded:  >70% success rate, or <3 consecutive failures
        Unhealthy: <=70% success rate, or >=3 consecutive failures
        """
        if self.total_calls == 0:
            return "unknown"
        success_rate = self.successful_calls / self.total_calls
        if success_rate >= 0.9 and self.consecutive_failures == 0:
            return "healthy"
        if success_rate >= 0.7 and self.consecutive_failures < 3:
            return "degraded"
        return "unhealthy"

    def snapshot(self) -> dict:
        """Return a JSON-friendly dict of current metrics."""
        return {
            "name": self.name,
            "total_calls": self.total_calls,
            "successful_calls": self.successful_calls,
            "failed_calls": self.failed_calls,
            "success_rate": round(self.successful_calls / max(self.total_calls, 1), 4),
            "avg_latency": round(self.avg_latency, 4),
            "max_latency": round(self.max_latency, 4),
            "last_latency": round(self.last_latency, 4),
            "error_count": self.error_count,
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "health_status": self.health_status,
            "last_call_ts": self.last_call_ts,
            "last_success_ts": self.last_success_ts,
        }


class HealthTracker:
    """Collects per-store health records. Threadsafe for async use.

    One tracker lives in app.state and is shared across all federation calls.
    """

    def __init__(self) -> None:
        self._records: dict[str, StoreHealthRecord] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, store_name: str) -> StoreHealthRecord:
        """Get the record for *store_name*, creating one if it doesn't exist."""
        async with self._lock:
            if store_name not in self._records:
                self._records[store_name] = StoreHealthRecord(name=store_name)
            return self._records[store_name]

    async def record_success(self, store_name: str, latency: float) -> None:
        """Convenience: record a successful call."""
        rec = await self.get_or_create(store_name)
        rec.record_success(latency)

    async def record_failure(self, store_name: str, error: str, latency: float) -> None:
        """Convenience: record a failed call."""
        rec = await self.get_or_create(store_name)
        rec.record_failure(error, latency)

    async def snapshot_all(self) -> list[dict]:
        """Return a snapshot dict for every tracked store, sorted by name."""
        async with self._lock:
            names = sorted(self._records.keys())
            return [self._records[n].snapshot() for n in names]

    async def snapshot(self, store_name: str) -> Optional[dict]:
        """Return a snapshot for one store, or None if untracked."""
        async with self._lock:
            rec = self._records.get(store_name)
            return rec.snapshot() if rec else None
