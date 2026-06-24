"""
seren_corpus_callosum
═════════════════════

The read-only corpus callosum: a fan that federates N memory stores into one
ranked recall surface. Left brain (SerenLoci, structured facts) + right brain
(SerenMemory, episodic) + however many more you hook in - merged by
Reciprocal Rank Fusion, which is embedder-agnostic by construction so the
merge survives any store changing its embedder underneath it.

Public surface:
    Hit, FusedHit                  - the result shapes
    rrf_fuse, apply_floor          - the pure merge core
    StoreConfig, FederationConfig  - what to fan + how to merge
    Federation                     - the orchestrator
    HttpTransport                  - live wiring (needs httpx)
    build_adapter, register_adapter - the extension point
"""
from __future__ import annotations

# Version flows from the git tag via setuptools-scm (written to _version.py at
# build time, read here). Fallback only fires in a bare source checkout that
# was never built. Mirrors SerenLoci so the family exposes __version__ alike.
try:
    from ._version import version as __version__
except Exception:  # noqa: BLE001 - source checkout without a build
    __version__ = "0.0.0+unknown"

from .config import (
    CorpusCallosumConfig,
    FederationConfig,
    ServerConfig,
    StoreConfig,
    TlsConfig,
    load_config,
)
from .federation import Federation
from .fusion import (
    FusedHit,
    Hit,
    apply_floor,
    base_relevance_from_distance,
    rrf_fuse,
)
from .adapters import (
    SerenLociAdapter,
    SerenMemoryAdapter,
    StoreAdapter,
    Transport,
    UnknownStoreType,
    build_adapter,
    register_adapter,
)

__all__ = [
    "__version__",
    "Hit",
    "FusedHit",
    "rrf_fuse",
    "apply_floor",
    "base_relevance_from_distance",
    "StoreConfig",
    "FederationConfig",
    "ServerConfig",
    "TlsConfig",
    "CorpusCallosumConfig",
    "load_config",
    "Federation",
    "StoreAdapter",
    "Transport",
    "SerenMemoryAdapter",
    "SerenLociAdapter",
    "build_adapter",
    "register_adapter",
    "UnknownStoreType",
]
