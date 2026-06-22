# SerenCorpusCallosum

**The callosum.** A read-only fan that federates *N* memory stores into one ranked recall surface. Left brain ([SerenCorpusCallosum](https://github.com/ChadRoesler/SerenCorpusCallosum) - structured facts) plus right brain ([SerenMemory](https://github.com/ChadRoesler/SerenMemory) - episodic memory) plus however many more you hook in - merged into a single ordered list you can hand to a model.

It owns no store of its own. It remembers nothing. It only fans, floors, and merges what the hemispheres hand back. That read-only-by-construction shape is the whole point: the callosum is glue, not a place data lives.

---

## Why Reciprocal Rank Fusion (and not "just sort by score")

Every store in this family can change its embedder independently - both SerenMemory and SerenCorpusCallosum ship embedder-migration. The moment two stores run different embedders, their raw distances and scores live in **different, incomparable number spaces**. Sorting a merged list by those scores is comparing apples to a slightly different apple every time someone migrates a model.

So the callosum never reads magnitudes. It reads only each store's **rank ordering** - position 1, 2, 3 within its own results - and merges with Reciprocal Rank Fusion:

```
score(hit) = weight_of_store / (k + rank_in_store)      # k = 60
```

That's **embedder-agnostic by construction**: rescale any store's scores however you like, the *order* is untouched, so the merge is untouched. There's a test that proves exactly this (`test_embedder_change_does_not_perturb_order`) - multiply one store's magnitudes by an arbitrary factor and the fused ranking comes out byte-identical.

Two knobs, and only two:

- **`weight`** (per store) - the one cross-store trust lever. Trust facts more than episodes? Give CorpusCallosum a higher weight.
- **`floor`** (per store) - a relevance floor applied *before* fusion, so "rank 1 of a bag of garbage" can't sneak to the top. Default 0 (trust the store's own ordering); raise toward ~0.3 if a store is noisy.

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
  "skipped": []
}
```

`score` is the cross-store RRF number it was ranked by; `store_rank` / `base_relevance` / `native_score` / `raw_distance` tell you where it came from and why it placed where it did. `stores_searched` and `skipped` tell you which hemispheres actually answered - a slow or down store degrades the result, it never takes the call down with it.

Plus `GET /` (service info + the stores it's fanning) and `GET /health`.

## The family

| Service | Role | Port |
|---|---|---|
| [SerenMemory](https://github.com/ChadRoesler/SerenMemory) | right brain - episodic, consolidated memory | 7420 |
| [SerenMargin](https://github.com/ChadRoesler/SerenMargin) | private notes-to-self (opt-in) | 7421 |
| [SerenLoci](https://github.com/ChadRoesler/SerenLoci) | left brain — keyed, deterministic facts | 7422 |
| **SerenCorpusCallosum** | **the fan over all of them** | **7423** |

## License

GPL-3.0-or-later.
