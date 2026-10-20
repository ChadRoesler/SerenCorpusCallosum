"""
seren_corpus_callosum.routes.knobs
═══════════════════════════════════════════════════════════════════════

    GET /knobs - every setting the callosum runs with, read off in one place,
                 each with the plain words for what it does.

Chad, 20 Oct 2026: "for the callosum, a way to view what the settings for
stores, the knobs that get twiddled, so it's easy for someone who isn't me to
be able to read off what they are without having to dig."

Three columns per knob, because three things can disagree: what the fan is
RUNNING with now (POST /configure changes it until the next restart), what the
yaml SAYS (what comes back after a restart), and the DEFAULT (what you get by
saying nothing). A row where live != yaml is a twiddle somebody made and did
not write down; a row where yaml != default is a choice somebody made on
purpose. Secrets never appear: a bearer is reported by WHERE it comes from
(inline / env / keyring / open), never by value.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from ..config import (
    _DEFAULT_AUTHORITY_MARGIN, _DEFAULT_EDGE_BUDGET, _DEFAULT_EDGES_ENABLED, _DEFAULT_FETCH_MULTIPLIER,
    _DEFAULT_FLOOR, _DEFAULT_FUSION_MODE, _DEFAULT_HOP_BUDGET, _DEFAULT_HOP_TERMS, _DEFAULT_HOPS, _DEFAULT_K,
    _DEFAULT_MIN_PER_STORE, _DEFAULT_N_RESULTS, _DEFAULT_SATELLITE_BUDGET, _DEFAULT_SATELLITE_EDGES,
    _DEFAULT_TIMEOUT_S, _DEFAULT_WEIGHT, FederationConfig, StoreConfig,
)
from .stores import SUPPORTED_KNOBS

router = APIRouter(tags=["knobs"])

# The federation knobs, in the order someone tuning the fan meets them: what
# comes back, how it is merged, what is added after the merge, the second
# round. `what` is the sentence for someone who is not Chad.
FEDERATION_KNOBS: list[tuple[str, Any, str]] = [
    ("n_results", _DEFAULT_N_RESULTS,
     "How many merged hits a search returns when the caller does not say."),
    ("fetch_multiplier", _DEFAULT_FETCH_MULTIPLIER,
     "Each store is asked for n_results times this, so the merge has candidates to choose from."),
    ("per_store_timeout_s", _DEFAULT_TIMEOUT_S,
     "A store slower than this many seconds is skipped for that one call; the others still answer."),
    ("fusion_mode", _DEFAULT_FUSION_MODE,
     "How stores are merged. rrf reads only each store's rank order, so it is right even when the stores use "
     "different embedders; rrf_pct and percentile also read the scores, as a common currency across N stores."),
    ("k", _DEFAULT_K,
     "The RRF damping constant: a hit is worth weight / (k + rank). Lower lets rank 1 dominate, higher "
     "flattens the ranks so rank 1 and rank 5 count nearly the same. 60 is the textbook value."),
    ("authority_margin", _DEFAULT_AUTHORITY_MARGIN,
     "When one store's top hit is this much more confident than every other store's, it is promoted to rank 1 "
     "whatever RRF says. 0 turns it off. Depends on the embedder; tune with brain_eval, not by eye."),
    ("min_per_store", _DEFAULT_MIN_PER_STORE,
     "Every store that answered keeps at least this many of its top hits through the final trim, so the packet "
     "stays a briefing (a fact and a scar) instead of all one kind. 0 is pure top-n."),
    ("edges_enabled", _DEFAULT_EDGES_ENABLED,
     "After the merge, a few entries that share a topic TAG with the packet are appended, marked topic-edge: "
     "the scar the wording buried. Off means a pure vector fan."),
    ("edge_budget", _DEFAULT_EDGE_BUDGET,
     "How many topic edges may be appended. They ride after the n_results packet, never in its slots."),
    ("satellite_edges_enabled", _DEFAULT_SATELLITE_EDGES,
     "A long-term core in the packet brings its newest satellites (the dated episodes behind it) and the core it "
     "superseded, marked satellite-edge / supersedes-edge, after the ranked hits."),
    ("satellite_budget", _DEFAULT_SATELLITE_BUDGET,
     "How many satellite entries may be appended in all, shared round-robin across the packet's cores so one busy "
     "core cannot take them all."),
    ("hops", _DEFAULT_HOPS,
     "Retrieval rounds. 1 is one pass. 2 lifts bridge terms from what round one found and fans them again, which "
     "reaches an answer that shares no word with the question. Costs a second fan; never on by accident."),
    ("hop_terms", _DEFAULT_HOP_TERMS,
     "How many bridge terms are lifted from round one to query round two (no model in the loop, just tokens)."),
    ("hop_budget", _DEFAULT_HOP_BUDGET,
     "How many hop hits may be appended, after the packet and the edges."),
]

STORE_KNOBS: list[tuple[str, Any, str]] = [
    ("weight", _DEFAULT_WEIGHT,
     "Cross-store trust: this store's ranks are multiplied by it in the merge. The one lever that says one "
     "store's opinion counts for more than another's."),
    ("floor", _DEFAULT_FLOOR,
     "Hits under this base relevance are dropped before the merge, so a store cannot win rank 1 with garbage. "
     "0 trusts the store's own ordering; ~0.3 for a noisy one."),
    ("enabled", True,
     "Off keeps the entry but takes the store out of the fan."),
]


def _auth_source(s: StoreConfig) -> str:
    if s.token_keyring:
        return "keyring"
    if s.token_env:
        return f"env ${s.token_env}"
    if s.token:
        return "inline"
    return "open"


def _server_auth(server: Any) -> str:
    if getattr(server, "bearer_token_keyring", ""):
        return "keyring"
    if getattr(server, "bearer_token_env", ""):
        return f"env ${server.bearer_token_env}"
    if getattr(server, "bearer_token", ""):
        return "inline"
    return "open"


def _read_yaml(path: str | None) -> dict[str, Any] | None:
    """The yaml as it reads NOW, or None when there is none to read. What
    comes back after a restart, beside what runs now."""
    if not path:
        return None
    p = Path(os.path.expanduser(path))
    if not p.is_file():
        return None
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - an unreadable yaml is reported as such, never a 500
        return None


def _row(key: str, live: Any, yaml_val: Any, default: Any, what: str, yaml_known: bool,
         runtime_settable: bool | None = None) -> dict[str, Any]:
    row = {"key": key, "live": live, "yaml": yaml_val if yaml_known else None, "default": default, "what": what,
           "is_default": live == default,
           "twiddled": (yaml_known and yaml_val != live)}
    if runtime_settable is not None:
        row["runtime_settable"] = runtime_settable
    return row


@router.get("/knobs")
async def knobs(request: Request) -> dict:
    cfg = request.app.state.config
    fed: FederationConfig = cfg.federation
    config_path = getattr(cfg, "config_path", None)
    data = _read_yaml(config_path)
    yaml_known = data is not None
    yaml_fed = (data or {}).get("federation") or {}
    yaml_fed = yaml_fed if isinstance(yaml_fed, dict) else {}
    yaml_stores = {str(s.get("name")): s for s in (yaml_fed.get("stores") or []) if isinstance(s, dict)}

    federation = [
        _row(key, getattr(fed, key), yaml_fed.get(key, default), default, what, yaml_known,
             runtime_settable=(key in SUPPORTED_KNOBS))
        for key, default, what in FEDERATION_KNOBS
    ]

    stores = []
    for s in fed.stores:
        ys = yaml_stores.get(s.name)
        stores.append({
            "name": s.name, "type": s.type, "url": s.url, "managed": s.managed,
            "where": "overlay (added from the UI)" if s.managed else ("yaml" if ys is not None or not yaml_known else "not in the yaml"),
            "auth": _auth_source(s),
            "options": dict(s.options or {}),
            "knobs": [
                _row(key, getattr(s, key), (ys or {}).get(key, default) if ys is not None else default, default, what,
                     yaml_known and ys is not None, runtime_settable=(key in ("weight", "floor")))
                for key, default, what in STORE_KNOBS
            ],
        })

    env_overrides = sorted(k for k in os.environ if k.startswith("SEREN_SCC_"))
    overlay = getattr(cfg, "runtime_stores_path", None)
    return {
        "ok": True,
        "where": {
            "config_path": config_path,
            "config_exists": yaml_known,
            "overlay_path": overlay,
            "overlay_exists": bool(overlay) and Path(os.path.expanduser(str(overlay))).is_file(),
            "env_overrides": env_overrides,       # names only: a value here may be a token
        },
        "server": {
            "host": cfg.server.host, "port": cfg.server.port, "auth": _server_auth(cfg.server),
            "tls": {k: v for k, v in vars(cfg.tls).items() if not isinstance(v, str) or "key" not in k.lower()},
        },
        "updates": {"enabled": cfg.updates.enabled, "check_interval_hours": cfg.updates.check_interval_hours,
                    "allow_prerelease": cfg.updates.allow_prerelease},
        "federation": federation,
        "stores": stores,
        "runtime_settable": SUPPORTED_KNOBS,
        "note": "live is what the fan runs with now; yaml is what comes back after a restart; default is what "
                "you get by saying nothing. POST /configure changes live only.",
    }
