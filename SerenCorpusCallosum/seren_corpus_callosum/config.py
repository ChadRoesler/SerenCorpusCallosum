"""
seren_corpus_callosum.config
════════════════════════════════════════════════════════════════════════

What stores SCC fans across, and the knobs for how it merges them. Open
schema, lenient parse, Nano-floor defaults at the call site - same shape as
McpConfig in SerenMcpServer, on purpose: a missing or half-written config
should degrade to something sensible, never crash the fan.

THE GIFT, IN CONFIG FORM:
    Adding a memory store is a `stores:` entry. Because SerenMemory is a
    protocol, every SerenMemory-speaking instance uses the same adapter
    type ("seren_memory") - so "spin me up a dedicated memory for XYZ and
    fan it in" is literally:

        stores:
          - name: xyz
            type: seren_memory
            url: http://localhost:7430

    No new code. That's the whole point of this file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .overlay import load_overlay, overlay_path_for

# The shared server/tls config blocks - ONE definition for the whole family.
# SCC is dataclass-based, so (unlike pydantic Loci) it adopts these DIRECTLY:
# its old local ServerConfig/TlsConfig were the same shape, so they ARE these
# now. SCC gains the bearer-token pointers (env/keyring) + resolve_bearer free,
# and stays pydantic-free (the engine import path stays light).
from seren_meninges import ServerConfig, TlsConfig


# Nano-floor defaults. Tunable, but these just-work on cheap hardware.
_DEFAULT_K = 60                 # RRF damping constant (canonical)
_DEFAULT_N_RESULTS = 10         # merged hits returned by default
_DEFAULT_FETCH_MULTIPLIER = 2   # over-fetch per store so the merge has candidates
_DEFAULT_TIMEOUT_S = 5.0        # per-store call timeout - a slow store degrades, never blocks
_DEFAULT_WEIGHT = 1.0           # equal cross-store trust until told otherwise
_DEFAULT_FLOOR = 0.0            # 0 = trust the store's own ordering; raise to ~0.3 if noisy


@dataclass
class StoreConfig:
    """One store to fan into the merge."""

    name: str                       # provenance label + tie-break order key (must be unique)
    type: str                       # adapter registry key: "seren_memory" | "seren_loci"
    url: str                        # base URL of the store's HTTP API
    weight: float = _DEFAULT_WEIGHT  # RRF trust multiplier
    floor: float = _DEFAULT_FLOOR    # per-store base_relevance floor, applied pre-fusion
    enabled: bool = True             # flip off without deleting the entry
    managed: bool = False            # True = added via the UI (lives in the runtime overlay, removable from the UI)
    options: dict[str, Any] = field(default_factory=dict)  # adapter-specific extras (e.g. loci project scope)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StoreConfig":
        # Lenient: name/type/url are required to mean anything; everything
        # else falls back to a Nano-floor default. We don't raise on extra
        # keys - open schema - we stash unknowns in options so adapters can
        # read them and we never lose operator intent.
        known = {"name", "type", "url", "weight", "floor", "enabled", "managed", "options"}
        extras = {k: v for k, v in d.items() if k not in known}
        opts = dict(d.get("options") or {})
        opts.update(extras)
        return cls(
            name=str(d["name"]),
            type=str(d["type"]),
            url=str(d["url"]).rstrip("/"),
            weight=float(d.get("weight", _DEFAULT_WEIGHT)),
            floor=float(d.get("floor", _DEFAULT_FLOOR)),
            enabled=bool(d.get("enabled", True)),
            managed=bool(d.get("managed", False)),
            options=opts,
        )


@dataclass
class FederationConfig:
    """The whole fan: which stores, and how to merge them."""

    stores: list[StoreConfig] = field(default_factory=list)
    k: int = _DEFAULT_K
    n_results: int = _DEFAULT_N_RESULTS
    fetch_multiplier: int = _DEFAULT_FETCH_MULTIPLIER
    per_store_timeout_s: float = _DEFAULT_TIMEOUT_S

    @property
    def enabled_stores(self) -> list[StoreConfig]:
        return [s for s in self.stores if s.enabled]

    @classmethod
    def from_dict(cls, d: Optional[dict[str, Any]]) -> "FederationConfig":
        """Build from a parsed dict. Tolerates None and missing keys - a
        config with no stores yields an empty (but valid) federation that
        simply returns no hits, rather than exploding at startup."""
        d = d or {}
        raw_stores = d.get("stores") or []
        stores: list[StoreConfig] = []
        seen: set[str] = set()
        for entry in raw_stores:
            try:
                sc = StoreConfig.from_dict(entry)
            except (KeyError, TypeError, ValueError):
                # A single malformed store entry is skipped, not fatal. The
                # rest of the fan still works - graceful degradation starts
                # at config-parse time, not just at call time.
                continue
            if sc.name in seen:
                # Duplicate name would scramble provenance + tie-break order.
                # Last-wins is silent and dangerous (the SerenMemory dup-block
                # bug); here we keep the first and drop the rest, deterministically.
                continue
            seen.add(sc.name)
            stores.append(sc)
        return cls(
            stores=stores,
            k=int(d.get("k", _DEFAULT_K)),
            n_results=int(d.get("n_results", _DEFAULT_N_RESULTS)),
            fetch_multiplier=int(d.get("fetch_multiplier", _DEFAULT_FETCH_MULTIPLIER)),
            per_store_timeout_s=float(d.get("per_store_timeout_s", _DEFAULT_TIMEOUT_S)),
        )

    @classmethod
    def from_yaml(cls, path: str) -> "FederationConfig":
        """Load from a YAML file. Missing file or unparseable YAML -> empty
        federation (lenient, like McpConfig). Import yaml lazily so the core
        package doesn't hard-depend on PyYAML for the in-memory path."""
        try:
            import yaml  # type: ignore
            with open(path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            return cls.from_dict(data if isinstance(data, dict) else {})
        except FileNotFoundError:
            return cls.from_dict({})
        except Exception:  # noqa: BLE001 - malformed yaml degrades to empty, never crashes the fan
            return cls.from_dict({})


# ════════════════════════════════════════════════════════════════════════
# Service-layer config - the deployment shell around the pure fusion engine.
# Kept as dataclasses too, so importing the engine (fusion/adapters/federation)
# never drags in pydantic. The yaml SHAPE matches the family - server / <data>
# / tls sections, where SCC's <data> section is `federation:` (its stores are
# its storage) - even though the impl is dataclass rather than pydantic. The
# operator-visible surface is identical; only an internal dependency differs.
# ════════════════════════════════════════════════════════════════════════


# ServerConfig and TlsConfig are imported from SerenMeninges (top of file).
# SCC's local copies were byte-identical dataclasses, so adopting the shared
# ones is a pure delete - SCC gains the bearer-token POINTERS (env/keyring) and
# resolve_bearer() for free, and a shared-shape fix lands here automatically.
# Port stays leaf-owned: load_config passes default_port=7423 to from_dict.


@dataclass
class CorpusCallosumConfig:
    """The whole service: server + tls + the federation it fans across."""

    server: ServerConfig = field(default_factory=lambda: ServerConfig(port=7423))
    tls: TlsConfig = field(default_factory=TlsConfig)
    federation: FederationConfig = field(default_factory=FederationConfig)
    # Where UI-added stores persist (the runtime overlay). Set by load_config;
    # the POST/DELETE /stores handlers write here.
    runtime_stores_path: Optional[str] = None


def _apply_env_overrides(cfg: "CorpusCallosumConfig") -> "CorpusCallosumConfig":
    """SEREN_SCC_* env wins last, same precedence shape as the family's
    SEREN_<X>_*. (SCC is the project's established short name for the callosum;
    swap the prefix here if you'd rather spell it out.)"""
    env = os.environ
    if v := env.get("SEREN_SCC_HOST"):
        cfg.server.host = v
    if v := env.get("SEREN_SCC_PORT"):
        cfg.server.port = int(v)
    if v := env.get("SEREN_SCC_BEARER_TOKEN"):
        cfg.server.bearer_token = v
    if v := env.get("SEREN_SCC_BEARER_TOKEN_ENV"):
        cfg.server.bearer_token_env = v
    if v := env.get("SEREN_SCC_BEARER_TOKEN_KEYRING"):
        cfg.server.bearer_token_keyring = v
    if v := env.get("SEREN_SCC_TRUST_SYSTEM_STORE"):
        cfg.tls.trust_system_store = v.lower() in ("1", "true", "yes", "on")
    return cfg


def load_config(path: Optional[str] = None) -> "CorpusCallosumConfig":
    """Defaults -> yaml -> env (later wins), parallel to seren_loci.load_config.
    A missing file is fine: defaults + env is a valid zero-config run - it just
    fans across no stores until you add some. yaml is imported lazily so the
    engine import path stays light."""
    data: dict[str, Any] = {}
    candidate = path or os.environ.get("SEREN_SCC_CONFIG") or "seren-corpus-callosum.yaml"
    cfg_path = Path(os.path.expanduser(candidate))
    if cfg_path.is_file():
        try:
            import yaml  # type: ignore
            with open(cfg_path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except Exception:  # noqa: BLE001 - unreadable config degrades to defaults, never crashes
            data = {}
    fed = FederationConfig.from_dict(data.get("federation"))

    # -- runtime overlay --
    # UI-added stores live in a separate machine-managed JSON file so the
    # hand-authored yaml stays pristine. Merge them on top of the base stores
    # here (base WINS on a name collision), flagged managed=True so the UI and
    # DELETE know they're the removable ones.
    overlay_file = overlay_path_for(candidate)
    seen = {s.name for s in fed.stores}
    for entry in load_overlay(overlay_file):
        try:
            sc = StoreConfig.from_dict({**entry, "managed": True})
        except (KeyError, TypeError, ValueError):
            continue  # a malformed overlay entry is skipped, never fatal
        if sc.name in seen:
            continue
        seen.add(sc.name)
        fed.stores.append(sc)

    cfg = CorpusCallosumConfig(
        server=ServerConfig.from_dict(data.get("server"), default_port=7423),
        tls=TlsConfig.from_dict(data.get("tls")),
        federation=fed,
        runtime_stores_path=str(overlay_file),
    )
    return _apply_env_overrides(cfg)
