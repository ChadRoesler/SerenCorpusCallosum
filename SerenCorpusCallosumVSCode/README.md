# SerenCorpusCallosum

**The callosum.** A read-only fan that federates *N* memory stores into one ranked recall surface. Left brain ([SerenLoci](https://github.com/ChadRoesler/SerenLoci) — structured facts) plus right brain ([SerenMemory](https://github.com/ChadRoesler/SerenMemory) — episodic memory) plus however many more you hook in — merged into a single ordered list you can hand to a model.

It owns no store of its own. It remembers nothing. It only fans, floors, and merges what the hemispheres hand back. That read-only-by-construction shape is the whole point: the callosum is glue, not a place data lives.

---

## How it works

The callosum never reads raw scores — every store can change its embedder independently, so distances live in incomparable number spaces. Instead it reads **rank ordering** (position 1, 2, 3 within each store's results) and merges with **Reciprocal Rank Fusion**:

```
score(hit) = weight_of_store / (k + rank_in_store)
```

That's embedder-agnostic by construction: rescale any store's scores however you like, the *order* is untouched, so the merge is untouched.

**Base + overlay config.** Stores declared in your yaml are *base* stores — never rewritten by the UI. Stores added via the Bridge viewer live in a separate `runtime-stores.json` overlay. Base always wins a name collision, so the overlay can never quietly shadow something you hand-wrote.

---

## Requirements

- **GitHub Copilot** (Chat) — the extension registers one language-model tool; Copilot Chat is required to use it.
- **SerenCorpusCallosum service** — the Python backend, from the [SerenCorpusCallosum repository](https://github.com/ChadRoesler/SerenCorpusCallosum).

---

## Quick start

**1. Install the SerenCorpusCallosum service**

```bash
pip install seren-corpus-callosum            # core: just the web stack + httpx
pip install 'seren-corpus-callosum[mcp]'     # + the `search` MCP tool surface
```

No `[vector]` extra, ever — the callosum embeds nothing, so it never pulls torch.

**2. Configure the extension**

Open **Settings** (`Ctrl+Shift+P` → `Open User Settings`) and set:

| Setting | Default | Description |
|---------|---------|-------------|
| `serenCorpusCallosum.endpoint` | `http://localhost:7423` | Base URL of the SerenCorpusCallosum service |
| `serenCorpusCallosum.startCommand` | `seren-corpus-callosum` | Command used by **Start Service** |
| `serenCorpusCallosum.suppressStartPrompt` | `false` | Suppress the startup "not reachable" prompt |

**3. Set your bearer token (if auth is enabled)**

Run `Ctrl+Shift+P` → **Seren CorpusCallosum: Set Bearer Token**. The token is stored in the OS keychain, never in settings files.

**4. Check the status bar**

The `$(git-merge) CorpusCallosum` item in the bottom-right shows service health at a glance: `CorpusCallosum ✓` reachable, `CorpusCallosum ✗` not (click to retry).

---

## Copilot tools

Once the service is running, Copilot gets one tool. You can also reference it directly in chat with `#serenSearch`.

| Tool | Reference | What it does |
|------|-----------|-------------|
| **Search Brain** | `#serenSearch` | Fans your query across every configured store and returns a single rank-fused list, each hit tagged with its origin store, its rank within that store, and its scores. |

It's deliberately the only tool. SCC owns no data, so there's nothing to set, get, or forget here — that's what the SerenLoci and SerenMemory extensions are for. Managing *which* stores get fanned (add/remove) lives in the Bridge viewer, behind the bearer token, not as a model tool.

---

## Commands

Open the Command Palette (`Ctrl+Shift+P`) and search **Seren CorpusCallosum**:

| Command | What it does |
|---------|-------------|
| **Seren CorpusCallosum: Set Bearer Token** | Store your auth token in the OS keychain |
| **Seren CorpusCallosum: Check Service Health** | Ping the service and update the status bar |
| **Seren CorpusCallosum: Open the Bridge (viewer)** | Open the web UI to manage stores and search by hand |
| **Seren CorpusCallosum: Start Service** | Launch the service using `serenCorpusCallosum.startCommand` |

---

## MCP transport (optional)

The service also exposes an MCP HTTP endpoint at `/mcp/` (install with the `[mcp]` extra). This lets a model connect directly via the VS Code or Visual Studio MCP client config without the extension, or use both at once.

**`.vscode/mcp.json`** (VS Code):
```json
{
  "servers": {
    "seren-corpus-callosum": {
      "type": "http",
      "url": "http://localhost:7423/mcp/",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}
```

Omit the `headers` block if you didn't set a bearer token. The endpoint exposes a single `search` tool — the same in-process fan the HTTP API uses.

---

## The family

| Service | Role | Port |
|---------|------|------|
| [SerenMemory](https://github.com/ChadRoesler/SerenMemory) | right brain — episodic, consolidated memory | 7420 |
| [SerenMargin](https://github.com/ChadRoesler/SerenMargin) | private notes-to-self (opt-in) | 7421 |
| [SerenLoci](https://github.com/ChadRoesler/SerenLoci) | left brain — keyed, deterministic facts | 7422 |
| **SerenCorpusCallosum** | **the fan over all of them** | **7423** |

---

## Source & issues

[github.com/ChadRoesler/SerenCorpusCallosum](https://github.com/ChadRoesler/SerenCorpusCallosum)
