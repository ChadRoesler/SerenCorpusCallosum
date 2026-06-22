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

## ...or live, from the web UI

You don't have to edit the yaml and restart. The **Stores** tab in the viewer has an **+ Add store** form - name, type, url, weight, floor - and a store you add there is fanned on the *very next search*, no restart.

The trick that keeps this safe: UI-added stores don't touch your hand-authored config. They live in a separate, machine-managed `runtime-stores.json` **overlay** (a sibling of your config file by default; override with `SEREN_SCC_RUNTIME_STORES`). At startup the callosum merges the overlay *on top of* the base stores. Your yaml is never rewritten, never reformatted, never loses a comment.

- **base** stores - declared in your yaml. Config-owned; the UI won't delete them (it points you back at the file instead).
- **managed** stores - added via the UI/overlay. Removable from the UI with the **✕** button.
- **Base always wins** a name collision, so the overlay can never quietly shadow something you hand-wrote.

This is still read-only *over data* - adding or removing a store only changes *which* halls the callosum reads, never what's in them. It's config management, gated by the same bearer token as everything else.

## Install

```bash
pip install seren-corpus-callosum            # core: just the web stack + httpx
pip install 'seren-corpus-callosum[mcp]'     # + the `search` MCP tool surface
pip install 'seren-corpus-callosum[corp]'    # + OS-trust-store TLS for corp proxies
```

No `[vector]` extra, ever - the callosum embeds nothing, so it never pulls torch. It's the dep-lightest service in the family and has no Python upper bound.

### Or use the setup script

The family installer in [SerenSetupScripts](https://github.com/ChadRoesler/SerenSetupScripts) wires up a venv, the service, and a starter config that pre-fans your local CorpusCallosum + Memory:

```bash
./seren-corpus-callosum-setup.sh --mcp           # add --corp behind a TLS-inspecting proxy
```

```powershell
.\seren-corpus-callosum-setup.ps1 -Mcp           # add -Corp for the corp-proxy case
```

PyPI is the default install source; `--service` registers it to start on boot.

## Run

```bash
seren-corpus-callosum --config seren-corpus-callosum.yaml
# or: python -m seren_corpus_callosum -c seren-corpus-callosum.yaml
```

Defaults to `0.0.0.0:7423` (memory 7420 · margin 7421 · loci 7422 · **callosum 7423**). A missing config is fine - you get a valid service that simply fans across no stores until you add some.

## The web UI - "The Bridge"

Open **`http://localhost:7423/viewer`**. It's a single-file dark UI with a violet accent - violet because it's the bridge between the coral right brain (Memory) and the cyan left brain (CorpusCallosum):

- **Search** - type a query, watch it fan every store and come back one rank-fused list. Each hit is badged by the hemisphere it came from (coral / cyan / violet for "other"), with its `#rank` in its origin store and the full `rrf` / `rel` / `native` / `d=` provenance so you can see *why* it placed where it did.
- **Stores** - the live roster with bind status (active / disabled / skipped-with-reason), the add-store form, and ✕-remove on managed stores.
- **Overview** - counts + a plain-English explainer of the merge.

The page itself is public (no token needed to load it); if your callosum has auth on, click **🔑 token** and the UI sends it as a bearer header on every call. The token is kept in your browser's localStorage, nowhere else.

## The API

The route that matters:

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

Roster management (all bearer-gated):

```http
GET    /stores            # the fan + each store's bind status + the known types
POST   /stores            # add a store to the overlay, fanned live
       { "name": "project-xyz", "type": "seren_memory", "url": "http://localhost:7430" }
DELETE /stores/{name}     # remove a managed (overlay) store; base stores are refused
```

`POST /stores` rejects an unknown `type` (400) and a duplicate `name` (409) up front, so a typo fails loudly instead of becoming a store that silently never binds.

Plus `GET /` (service info + the stores it's fanning), `GET /health`, and `GET /viewer` (the UI above).

## The MCP `search` tool

Install the `[mcp]` extra and a `search` tool lights up at `/mcp` - the same in-process fan the HTTP API uses, callable directly by a connected model. It's the umbrella of the family's search trio:

| Tool | Reaches | Served by |
|---|---|---|
| `search` | **everything** - the whole fan, rank-fused | SerenCorpusCallosum |
| `search_loci` | keyed facts only | SerenCorpusCallosum |
| `search_memory` | episodic memory only | SerenMemory |

So a model can go broad (`search`) by default and reach for a specific hemisphere when it knows which one it wants. The callosum presents the very same `/search` interface it consumes - it's recursive by design, which means you can even fan one callosum into another.

> Note: SerenMemory currently exposes its search as `recall`; `search_memory` is the converging alias that completes the trio (added alongside, not a rename - existing `recall` callers keep working).

## The family

| Service | Role | Port |
|---|---|---|
| [SerenMemory](https://github.com/ChadRoesler/SerenMemory) | right brain - episodic, consolidated memory | 7420 |
| [SerenMargin](https://github.com/ChadRoesler/SerenMargin) | private notes-to-self (opt-in) | 7421 |
| [SerenLoci](https://github.com/ChadRoesler/SerenLoci) | left brain — keyed, deterministic facts | 7422 |
| **SerenCorpusCallosum** | **the fan over all of them** | **7423** |

## License

GPL-3.0-or-later.
