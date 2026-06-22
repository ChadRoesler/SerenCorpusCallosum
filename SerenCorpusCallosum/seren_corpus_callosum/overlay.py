"""
seren_corpus_callosum.overlay
════════════════════════════════════════════════════════════════════════

UI-added stores live HERE, not in your hand-authored yaml.

The base `seren-corpus-callosum.yaml` is yours — comments, ordering, intent,
all of it. When you add a store from the web UI, it goes into a separate,
machine-managed JSON file (`runtime-stores.json`, a sibling of the config by
default) that this module owns. At startup, load_config merges these on top of
the base stores (base wins on a name collision). The hand-written config is
never rewritten, never reformatted, never loses a comment.

That separation is the whole point: a tidy "add a store" feature must not be
allowed to clobber the operator's pristine config. Two surfaces, two owners.

Format: a JSON array of store objects, each `{name, type, url, weight, floor}`.
A missing or corrupt overlay degrades to "no overlay stores" — it never blocks
startup.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional


def overlay_path_for(config_path: Optional[str]) -> Path:
    """Where the runtime overlay lives. Explicit env override wins; otherwise
    it's a sibling of the config file (so a per-instance config gets its own
    per-instance overlay)."""
    env = os.environ.get("SEREN_SCC_RUNTIME_STORES")
    if env:
        return Path(os.path.expanduser(env))
    base = Path(os.path.expanduser(config_path)) if config_path else Path("seren-corpus-callosum.yaml")
    return base.parent / "runtime-stores.json"


def load_overlay(path: Any) -> list[dict]:
    """Read the overlay store list. Missing file -> []. Corrupt/unreadable ->
    [] (degrade, never crash startup over a managed scratch file)."""
    p = Path(path)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    return data if isinstance(data, list) else []


def save_overlay(path: Any, stores: list[dict]) -> None:
    """Write the overlay list atomically (temp + replace) so a crash mid-write
    can't leave a half-written file the next startup chokes on."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(stores, indent=2), encoding="utf-8")
    tmp.replace(p)


def add_to_overlay(path: Any, store: dict) -> list[dict]:
    """Add (or replace-by-name) a store in the overlay. Returns the new list."""
    stores = [s for s in load_overlay(path) if s.get("name") != store.get("name")]
    stores.append(store)
    save_overlay(path, stores)
    return stores


def remove_from_overlay(path: Any, name: str) -> bool:
    """Remove a store from the overlay by name. Returns True if something was
    removed, False if the name wasn't in the overlay (e.g. it's a base store)."""
    stores = load_overlay(path)
    kept = [s for s in stores if s.get("name") != name]
    if len(kept) == len(stores):
        return False
    save_overlay(path, kept)
    return True
