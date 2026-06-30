# SerenCorpusCallosum

**The callosum.** A read-only fan that federates *N* memory stores into one ranked recall surface. Left brain ([SerenLoci](https://github.com/ChadRoesler/SerenLoci) — structured facts) plus right brain ([SerenMemory](https://github.com/ChadRoesler/SerenMemory) — episodic memory) plus however many more you hook in — merged into a single ordered list you can hand to a model.

It owns no store of its own. It remembers nothing. It only fans, floors, and merges what the hemispheres hand back. That read-only-by-construction shape is the whole point: the callosum is glue, not a place data lives.

---

## Why Reciprocal Rank Fusion (and not "just sort by score")

Every store in this family can change its embedder independently — both SerenMemory and SerenLoci ship embedder-migration. The moment two stores run different embedders, their raw distances and scores live in **different, incomparable number spaces**. Sorting a merged list by those scores is comparing apples to a slightly different apple every time someone migrates a model.

So the callosum never reads magnitudes. It reads only each store's **rank ordering** — position 1, 2, 3 within its own results — and merges with Reciprocal Rank Fusion:

```
score(hit) = weight_of_store / (k + rank_in_store)      # k = 60
```

That's **embedder-agnostic by construction**: rescale any store's scores however you like, the *order* is untouched, so the merge is untouched. There's a test that proves exactly this (`test_embedder_change_does_not_perturb_order`) — multiply one store's magnitudes by an arbitrary factor and the fused ranking comes out byte-identical.

Two knobs, and only two:

- **`weight`** (per store) — the one cross-store trust lever. Trust facts more than episodes? Give Loci a higher weight.
- **`floor`** (per store) — a relevance floor applied *before* fusion, so "rank 1 of a bag of garbage" can't sneak to the top. Default 0 (trust the store's own ordering); raise toward ~0.3 if a store is noisy.

---

## Implementation architecture

### Federation engine

The `Federation` class owns the fan-out. On a search request it:

1. **Fans out** — dispatches the query to every active store in parallel, each via its adapter, with a per-store timeout.
2. **Floors** — discards hits whose `base_relevance` is below the store's configured floor *before* ranking.
3. **Ranks** — sorts each store's survivors by native score descending, assigns rank order.
4. **Fuses** — applies RRF across all stores: `Σ weight / (k + rank_in_store)`.
5. **Applies authority margin** — if `authority_margin > 0`, an exact-key hit (native_score = 1.0) gets boosted above the #1 RRF-ranked result by that margin.
6. **Truncates** — returns the top `n_results`.

A slow or down store degrades the result but never takes the call down with it. Dead stores are reported in the `skipped` field.

### Adapter layer

Each store type has a corresponding adapter class. Adapters translate the store's native response shape into a uniform `StoreHit`:

| Adapter | Store type | What it does |
|---------|-----------|-------------|
| `SerenMemoryAdapter` | `seren_memory` | Calls `POST /search` on the memory service, maps `content`/`score`/`metadata` into hits |
| `SerenLociAdapter` | `seren_loci` | Calls `POST /search` on the loci service, maps `content`/`score`/`metadata` from the fact's `value` and `why` |

One adapter covers every store that speaks the same protocol — adding a SerenMemory-speaking store is a config entry, not a code change.

### Overlay system

Stores come from two sources merged at startup:

- **Base stores** — declared in the hand-authored yaml config. Config-owned; the Bridge UI won't delete them (it points you back at the file instead).
- **Managed stores** — added via the Bridge viewer's **+ Add store** form, persisted in a separate `runtime-stores.json` **overlay** file. Removable from the UI with the **✕** button.

Base always wins a name collision, so the overlay can never quietly shadow something you hand-wrote. The overlay path defaults to a sibling of the config file; override with `SEREN_SCC_RUNTIME_STORES` env var.

### Edges system (optional)

When `edges_enabled: true`, the federation appends topic-association edges after fusion. For each hit in the fused list, the edges system looks up topically related entries from *other* stores and appends them as additional results (up to `edge_budget`). This surfaces cross-store connections — a Loci fact about a project's coding convention might pull in a Memory entry about a related discussion.

Edges are purely additive: they never replace or re-rank the fused list, only extend it. A disabled edges config or a store that doesn't support topic queries degrades gracefully.

---

## Dynamic runtime configuration

Federation parameters can be tuned at runtime without a restart via `POST /configure`:

```http
POST /configure
{
  "k": 30,
  "fusion_mode": "rrf_pct",
  "authority_margin": 0.1,
  "min_per_store": 3,
  "edges_enabled": false,
  "edge_budget": 0,
  "n_results": 25,
  "fetch_multiplier": 4,
  "per_store_timeout_s": 10.0,
  "stores": [
    {"name": "facts", "weight": 2.0, "floor": 0.1}
  ]
}
```

All fields are optional — only supplied fields are changed. The live Federation is rebuilt immediately so the next `/search` picks up the new values. Per-store overrides mutate `weight`/`floor` on matching stores; an unknown store name returns 404, an invalid `fusion_mode` returns 422.

---

## Tests

| Test file | What it covers |
|-----------|----------------|
| `tests/test_app.py` | 5 tests — HTTP search route, health/root, bearer auth, unknown-store survival, config loading from defaults/env |
| `tests/test_federation.py` | 8 tests — fan across stores, dead/slow store graceful degradation, per-store floor, weight-based ranking, unknown-store skip, empty config |
| `tests/test_fusion.py` | 28 tests — RRF fusion, embedder-agnostic ranking, percentile/rrf_pct modes, authority margin, exact-key promotion, per-store quota/min_per_store |
| `tests/test_edges.py` | 8 tests — topic-association edges appended after fusion, edge budget cap, disabled mode, Loci skipped (no topics), failure degrades gracefully |
| `tests/test_adapters.py` | 7 tests — SerenMemory + SerenLoci adapter response mapping, search-path override, dispatch by type, empty/missing hits safe |
| `tests/test_stores.py` | 10 tests — stores endpoint, bridge viewer, add/delete managed stores, unknown-type rejection, duplicate rejection, blank-field rejection, base-store delete refused, missing 404 |
| `tests/test_overlay.py` | 5 tests — runtime overlay load/add/remove, corrupt overlay degrades to empty, env override |
| `tests/test_mcp_mount.py` | 2 tests — MCP mount requires federation, succeeds and exposes session manager |
| `tests/test_mcp_tools.py` | 3 tests — MCP search tool returns full provenance, surfaces skipped stores, default n_results |
| `tests/test_configure.py` | 9 tests — federation-level knobs (k, fusion_mode, authority, edges, n_results, fetch, timeout), per-store weight/floor overrides, unknown store → 404, invalid fusion_mode → 422, empty body, partial updates keep untouched fields, bearer auth enforced |

```bash
pytest tests/
```

---

## The family

| Service | Role | Port |
|---------|------|------|
| [SerenMemory](https://github.com/ChadRoesler/SerenMemory) | right brain — episodic, consolidated memory | 7420 |
| [SerenMargin](https://github.com/ChadRoesler/SerenMargin) | private notes-to-self (opt-in) | 7421 |
| [SerenLoci](https://github.com/ChadRoesler/SerenLoci) | left brain — keyed, deterministic facts | 7422 |
| **SerenCorpusCallosum** | **the fan over all of them** | **7423** |

## License

GPL-3.0-or-later.
