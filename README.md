# SerenCorpusCallosum

**The callosum.** A read-only fan that federates *N* memory stores into one ranked recall surface. Left brain ([SerenLoci](https://github.com/ChadRoesler/SerenLoci) - structured facts) plus right brain ([SerenMemory](https://github.com/ChadRoesler/SerenMemory) - episodic memory) plus however many more you hook in - merged into a single ordered list you can hand to a model.

It owns no store of its own. It remembers nothing. It only fans, floors, and merges what the hemispheres hand back. That read-only-by-construction shape is the whole point: the callosum is glue, not a place data lives.

---

## Why Reciprocal Rank Fusion (and not "just sort by score")

Every store in this family can change its embedder independently - both SerenMemory and SerenLoci ship embedder-migration. The moment two stores run different embedders, their raw distances and scores live in **different, incomparable number spaces**. Sorting a merged list by those scores is comparing apples to a slightly different apple every time someone migrates a model.

So the callosum never reads magnitudes. It reads only each store's **rank ordering** - position 1, 2, 3 within its own results - and merges with Reciprocal Rank Fusion:

```
score(hit) = weight_of_store / (k + rank_in_store)      # k = 60
```

That's **embedder-agnostic by construction**: rescale any store's scores however you like, the *order* is untouched, so the merge is untouched. There's a test that proves exactly this (`test_embedder_change_does_not_perturb_order`) - multiply one store's magnitudes by an arbitrary factor and the fused ranking comes out byte-identical.

Two per-store knobs decide how much a store is trusted:

- **`weight`** (per store) - the one cross-store trust lever. Trust facts more than episodes? Give Loci a higher weight.
- **`floor`** (per store) - a relevance floor applied *before* fusion, so "rank 1 of a bag of garbage" can't sneak to the top. Default 0 (trust the store's own ordering); raise toward ~0.3 if a store is noisy.

The federation has more knobs than that (fifteen honoured by `POST /configure`, listed by `GET /stores` as `supported_knobs`): the RRF constant, the fusion mode, the authority margin, the per-store seat quota, the two edge families, the hop settings, the fetch sizes and the timeout. The defaults below are the Nano-floor choices; [SerenProbe](https://github.com/ChadRoesler/SerenProbe) exists to sweep them against a real corpus.

## The gift, in config form

Because SerenMemory is a *protocol*, not a single instance, **one adapter covers every SerenMemory-speaking store.** Adding a memory is a config entry, not a code change:

```yaml
federation:
  stores:
    - name: facts
      type: seren_loci
      url: http://localhost:7422
    - name: episodic
      type: seren_memory
      url: http://localhost:7420
    - name: project-xyz          # spin up a dedicated memory, fan it in:
      type: seren_memory         # same adapter type, zero new code
      url: http://localhost:7430
```

That's the design goal stated plainly: "give me a store for XYZ and wire it in" should be one line in a file.

## Install

```bash
pip install seren-corpus-callosum            # core: just the web stack + httpx
pip install 'seren-corpus-callosum[mcp]'     # + the `search` MCP tool surface
pip install 'seren-corpus-callosum[corp]'    # + OS-trust-store TLS for corp proxies
```

No `[vector]` extra, ever - the callosum embeds nothing, so it never pulls torch. It's the dep-lightest service in the family and has no Python upper bound.

## Run

```bash
seren-corpus-callosum --config seren-corpus-callosum.yaml
# or: python -m seren_corpus_callosum -c seren-corpus-callosum.yaml
```

Defaults to `0.0.0.0:7423` (memory 7420 · margin 7421 · loci 7422 · **callosum 7423**). A missing config is fine - you get a valid service that simply fans across no stores until you add some.

```bash
# Search across all fanned stores - one query, rank-fused results
curl -X POST localhost:7423/search \
  -H 'content-type: application/json' \
  -d '{"query":"cuda runtime","n_results":10}'

# Check health and which stores are being fanned
curl localhost:7423/health
```

## Config

`seren-corpus-callosum.yaml` (all optional - defaults are a working zero-config dev setup).
Env vars (`SEREN_SCC_*`) override the file.

The values shown are the code's defaults (they match `seren-corpus-callosum.yaml.sample`).

```yaml
server:
  host: 127.0.0.1         # beyond loopback needs a bearer (or allow_open_lan: true, on purpose)
  port: 7423
  bearer_token: ""        # or bearer_token_env / bearer_token_keyring - a pointer, not the secret
federation:
  k: 60                   # RRF constant - lower = more aggressive rank weighting
  fusion_mode: rrf        # rrf | rrf_pct | percentile
  authority_margin: 0.035 # a store clearly confident in its top hit leads the packet; 0 disables
  min_per_store: 1        # each answering store keeps at least this many seats through the trim; 0 disables
  n_results: 10           # merged hits returned by default
  fetch_multiplier: 2     # over-fetch per store (clamped to what the store accepts: Memory takes at most 50)
  per_store_timeout_s: 5.0
  edges_enabled: true     # topic-association edges after the packet (Memory's /by_topic)
  edge_budget: 3
  satellite_edges_enabled: true   # a core's newest satellites and the core it superseded, after the packet
  satellite_budget: 3
  hops: 1                 # retrieval rounds; 2 fans a query expanded with round-1 text (hop_terms 4, hop_budget 5)
  stores:
    - name: facts
      type: seren_loci
      url: http://localhost:7422
      weight: 1.0
      floor: 0.0
      token_env: SEREN_LOCI_TOKEN   # the bearer presented TO this store (also token / token_keyring,
                                    # or bearer_token* - same pointers as the server block). A store
                                    # installed with --gen-token 401s without one.
    - name: episodic
      type: seren_memory
      url: http://localhost:7420
      weight: 1.0
      floor: 0.0
tls:
  trust_system_store: false   # true (+ [corp]) for TLS-intercepting corp proxies
```

---

## The API

One route that matters:

```http
POST /search
{ "query": "that CUDA thing on the Xavier", "n_results": 10 }
```

Every hit comes back with full provenance, so the merge is explainable rather than a black box:

```json
{
  "query": "that CUDA thing on the Xavier",
  "hits": [
    {
      "store": "facts",          "id": "…",
      "content": "…",
      "score": 0.0161,            "store_rank": 1,
      "base_relevance": 0.84,
      "native_score": 1.0,        "raw_distance": null,
      "metadata": {}
    }
  ],
  "stores_searched": ["facts", "episodic"],
  "failed": [],
  "skipped": []
}
```

`score` is the cross-store RRF number it was ranked by; `store_rank` / `base_relevance` / `native_score` / `raw_distance` tell you where it came from and why it placed where it did.

Three lists say what the fan actually did, because a timeout and "knows nothing" are different facts:

- `stores_searched` - the stores that **answered** this call. An empty answer is still an answer.
- `failed` - stores that were fanned and did not answer, each with why (`timeout after 5.00s`, `ConnectionError: refused`, `HTTPStatusError: 401 ...`). Their hits are missing from the packet. A slow or down store degrades the result, it never takes the call down with it - but the response says so.
- `skipped` - stores never bound at all (a config error such as an unknown `type`).

`GET /health/stores` keeps the running tally per store (latency, success rate, last error); it survives `POST /configure` and store changes.

### A core and its surroundings

Long-term in SerenMemory is a **core and its surroundings**: recall returns cores, and each core hit carries `metadata.surroundings` - how many satellites (supporting episodes, with dates) stand behind it, when the latest landed, what it superseded and what superseded it. With `satellite_edges_enabled` (the default) the fan asks Memory for those inline and appends the newest satellites, and the superseded core, **after** the ranked packet as marked edges:

```json
{ "store": "episodic", "id": "…", "content": "the episode", "score": 0.0, "store_rank": 1,
  "metadata": { "source": "satellite-edge", "edge_of": "<core id>", "core_id": "<core id>",
                "kind": "satellite", "created_at": 1758600000.0 } }
```

`source` is `satellite-edge` or `supersedes-edge`; `edge_of` names the core. Edges score 0.0 and never compete for the packet's slots. The budget is shared round-robin across the packet's cores, newest satellite first, so one well-evidenced core cannot crowd the rest out. Topic edges (`source: topic-edge`) come after them and never repeat one. Turned off, the counts stay on the core and nothing is appended; set `options.with_surroundings: false` on the store to spare a Memory its long-tier read as well.

Plus `GET /` (service info + the stores it's fanning) and `GET /health`.

### Dynamic runtime configuration (`POST /configure`)

Federation parameters can be tuned at runtime without a restart:

```http
POST /configure
{
  "k": 30,
  "fusion_mode": "rrf_pct",
  "authority_margin": 0.1,
  "min_per_store": 3,
  "edges_enabled": true,
  "edge_budget": 3,
  "satellite_edges_enabled": true,
  "satellite_budget": 3,
  "n_results": 25,
  "fetch_multiplier": 4,
  "per_store_timeout_s": 10.0,
  "stores": [
    {"name": "facts", "weight": 2.0, "floor": 0.1}
  ]
}
```

All fields are optional - only supplied fields are changed. The live Federation is
rebuilt immediately so the next `/search` picks up the new values. Per-store
overrides mutate `weight`/`floor` on matching stores; an unknown store name
returns 404, an invalid `fusion_mode` returns 422.

---

## Tests

| Test file | What it covers |
|-----------|----------------|
| `tests/test_app.py` | 5 tests - HTTP search route, health/root, bearer auth, unknown-store survival, config loading from defaults/env |
| `tests/test_federation.py` | 9 tests - fan across stores, dead/slow store graceful degradation, per-store floor, weight-based ranking, unknown-store skip, empty config, a store's bearer spelled the family way |
| `tests/test_fusion.py` | 28 tests - RRF fusion, embedder-agnostic ranking, percentile/rrf_pct modes, authority margin, exact-key promotion, per-store quota/min_per_store |
| `tests/test_edges.py` | 7 tests - topic-association edges appended after fusion, edge budget cap, disabled mode, Loci skipped (no topics), failure degrades gracefully |
| `tests/test_honest_fan.py` | 13 tests - the over-fetch clamped to what a store accepts; `stores_searched` = answered and `failed` names who did not with why (route and MCP tool); the health record survives /configure and store changes; a new timeout reaches the transport; satellite and superseded-core edges (order, budget, round-robin, dedup against topic edges, off = stripped) |
| `tests/test_adapters.py` | 6 tests - SerenMemory + SerenLoci adapter response mapping, search-path override, dispatch by type, empty/missing hits safe |
| `tests/test_stores.py` | 10 tests - stores endpoint, bridge viewer, add/delete managed stores, unknown-type rejection, duplicate rejection, blank-field rejection, base-store delete refused, missing 404 |
| `tests/test_overlay.py` | 5 tests - runtime overlay load/add/remove, corrupt overlay degrades to empty, env override |
| `tests/test_mcp_mount.py` | 2 tests - MCP mount requires federation, succeeds and exposes session manager |
| `tests/test_mcp_tools.py` | 4 tests - MCP search tool returns full provenance, surfaces skipped stores, default n_results |
| `tests/test_configure.py` | 9 tests - federation-level knobs (k, fusion_mode, authority, edges, n_results, fetch, timeout), per-store weight/floor overrides, unknown store → 404, invalid fusion_mode → 422, empty body, partial updates keep untouched fields, bearer auth enforced |
| `tests/test_exposure_is_wired.py` | 3 tests - a host beyond loopback with no bearer refuses to start |

```bash
pytest tests/
```

## The family

| Service | Role | Port |
|---|---|---|
| [SerenMemory](https://github.com/ChadRoesler/SerenMemory) | right brain - episodic, consolidated memory | 7420 |
| [SerenMargin](https://github.com/ChadRoesler/SerenMargin) | private notes-to-self (opt-in) | 7421 |
| [SerenLoci](https://github.com/ChadRoesler/SerenLoci) | left brain - keyed, deterministic facts | 7422 |
| **SerenCorpusCallosum** | **the fan over all of them** | **7423** |

## License

GPL-3.0-or-later.
