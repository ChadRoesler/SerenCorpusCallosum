# Seren CorpusCallosum

Keyed, deterministic coding/logic memory for GitHub Copilot. Seren CorpusCallosum connects Copilot to a locally-hosted **left brain** - addressable facts that survive across sessions, with exactly one live value per key - without sending anything to a third party.

Where [Seren Memory](https://github.com/ChadRoesler/SerenMemory) is the *right* brain (fuzzy, episodic recall), Seren CorpusCallosum is the *left* brain: you ask for an address, you get **the** thing.

---

## How it works

Seren CorpusCallosum runs a small Python service on your machine (or your team's internal server) backed by a single SQLite file - no daemon, no second process, runs on a 4GB laptop. The extension registers a set of Copilot language model tools that Copilot calls automatically to read and write facts during normal conversations. Everything lives in a local database you own.

### The facts model

Every fact is an addressable locus:

```
{ project, key, value, why }
```

| Concept | What it means |
|---------|---------------|
| **key** | The address - a dotted identifier like `posh.brace_style` or `cuda.no_vmm`. |
| **value** | The fact itself. |
| **why** | The hard-won reason it's shaped this way. Searchable - "that CUDA thing" finds a fact whose *reason* mentions CUDA. |
| **project** | Scope. `*` = **fundamentals** (cross-project truths like `camelCase is life`); a concrete name = a per-project convention. |

**Strict supersede.** Set a new value for a key and the old one is *superseded* - kept as history, never blended. The database physically enforces one live value per `(project, key)`. A fact you replace doesn't get vibed together with the old one; it cleanly takes over, and the old value stays as an audit trail.

### Three ways in, cheapest first

- **Exact** - you know the key, you get the live value deterministically.
- **Lexical** - FTS5 full-text over key/value/why when you sort of remember the words. No GPU, no embeddings.
- **Vector** *(optional)* - a semantic finder for the associative jump, lit up only when an embedder is configured (`pip install 'seren-corpuscallosum[vector]'`).

---

## Requirements

- **GitHub Copilot** (Chat) - the extension registers Copilot language model tools; Copilot Chat is required to use them.
- **SerenCorpusCallosum service** - the Python backend, from the [SerenCorpusCallosum repository](https://github.com/ChadRoesler/SerenCorpusCallosum).

---

## Quick start

**1. Install the SerenCorpusCallosum service**

```bash
pip install 'seren-corpuscallosum[mcp]'        # add ,vector for the semantic finder
```

The floor (exact + lexical) needs no extras and no torch.

**2. Configure the extension**

Open **Settings** (`Ctrl+Shift+P` → `Open User Settings`) and set:

| Setting | Default | Description |
|---------|---------|-------------|
| `serenCorpusCallosum.endpoint` | `http://localhost:7423` | Base URL of the SerenCorpusCallosum service |
| `serenCorpusCallosum.startCommand` | `python -m seren_corpuscallosum` | Command used by **Start Service** |
| `serenCorpusCallosum.suppressStartPrompt` | `false` | Suppress the startup "not reachable" prompt |

**3. Set your bearer token (if auth is enabled)**

Run `Ctrl+Shift+P` → **Seren CorpusCallosum: Set Bearer Token**. The token is stored in the OS keychain, never in settings files.

**4. Check the status bar**

The `$(database) CorpusCallosum` item in the bottom-right shows service health at a glance: `CorpusCallosum ✓` reachable, `CorpusCallosum ✗` not (click to retry).

---

## Copilot tools

Once the service is running, Copilot can use these automatically. You can also reference them directly in chat with `#serenSetFact`, `#serenSearch`, etc.

| Tool | Reference | What it does |
|------|-----------|-------------|
| **Set Fact** | `#serenSetFact` | Set or replace a fact. Strict supersede - the old value becomes history, never blended. Always include `why`. |
| **Get Fact** | `#serenGetFact` | The live value for a `(project, key)`, deterministically. Returns `found: false` for a never-set or retired key. |
| **Search CorpusCallosum** | `#serenSearch` | Find facts when you don't know the exact key. Exact match leads at score 1.0; otherwise the finder (vector or lexical) runs. |
| **Forget Fact** | `#serenForgetFact` | Retire a key's live value - a flag, not a scalpel. Kept as history, key free to set again. |
| **Fact History** | `#serenHistory` | Every value a key has ever held, newest first - "what did we used to think, and why did it change". |
| **List Facts** | `#serenListFacts` | Survey a whole scope - a project, or everything. |

There's no consolidator, draft gate, or tiers here - that's the right brain. CorpusCallosum is deterministic facts, full stop.

---

## Commands

Open the Command Palette (`Ctrl+Shift+P`) and search **Seren CorpusCallosum**:

| Command | What it does |
|---------|-------------|
| **Seren CorpusCallosum: Set Bearer Token** | Store your auth token in the OS keychain |
| **Seren CorpusCallosum: Check Service Health** | Ping the service and update the status bar |
| **Seren CorpusCallosum: Start Service** | Launch the service using `serenCorpusCallosum.startCommand` |

---

## MCP transport (optional)

The service also exposes an MCP HTTP endpoint at `/mcp/` (install with the `[mcp]` extra). This lets a model connect directly via the VS Code or Visual Studio MCP client config without the extension, or use both at once.

**`.vscode/mcp.json`** (VS Code):
```json
{
  "servers": {
    "seren-corpuscallosum": {
      "type": "http",
      "url": "http://localhost:7423/mcp/",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}
```

Omit the `headers` block if you didn't set a bearer token. The endpoint exposes the same six tools: `set_fact`, `get_fact`, `search_corpuscallosum`, `forget_fact`, `fact_history`, `list_facts`.

---

## Source & issues

[github.com/ChadRoesler/SerenCorpusCallosum](https://github.com/ChadRoesler/SerenCorpusCallosum)
