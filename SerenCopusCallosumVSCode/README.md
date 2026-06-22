# Seren CorpusCallosum — VS Code extension

Gives GitHub Copilot (and you) **one federated search across your whole Seren memory brain.**

[SerenCorpusCallosum](https://github.com/ChadRoesler/SerenCorpusCallosum) is the *callosum* — a read-only fan that federates every Seren memory store into one ranked recall surface. This extension surfaces that fan to Copilot as a single language-model tool: ask once, and it reaches keyed facts ([SerenLoci](https://github.com/ChadRoesler/SerenLoci)), episodic memory ([SerenMemory](https://github.com/ChadRoesler/SerenMemory)), and any other stores you've wired in — then merges the hits into one list with Reciprocal Rank Fusion.

## What it adds

**One language-model tool — `#serenSearch`** (`seren_corpuscallosum_search`):

> Search the whole brain. Fans your query across every configured store and returns a single rank-fused list, each hit tagged with its origin store, its rank within that store, and its scores. Copilot reaches for this when answering something that might lean on a recorded convention, a hard-won fact, or past context — and it doesn't have to know *which* store the answer lives in.

It's deliberately the *only* tool. SCC owns no data, so there's nothing to set, get, or forget here — that's what the SerenLoci and SerenMemory extensions are for. Managing *which* stores get fanned (add/remove) lives in the **Bridge viewer**, behind the token, not as a model tool — you don't want Copilot wiring in stores mid-completion.

**Commands** (`Ctrl+Shift+P`):

| Command | What it does |
|---|---|
| `Seren CorpusCallosum: Set Bearer Token` | Store the bearer token in the OS keychain (never in settings) |
| `Seren CorpusCallosum: Check Service Health` | Ping the service; updates the status-bar indicator |
| `Seren CorpusCallosum: Open the Bridge (viewer)` | Open the web UI to manage stores + search by hand |
| `Seren CorpusCallosum: Start Service` | Launch the service in a terminal if it's not reachable |

A status-bar item (`$(git-merge) CorpusCallosum`) shows reachability at a glance.

## Setup

1. **Run the service.** Install and start [SerenCorpusCallosum](https://github.com/ChadRoesler/SerenCorpusCallosum) (`pip install seren-corpus-callosum`, then `seren-corpus-callosum`). It defaults to `http://localhost:7423`.
2. **Install this extension.** It activates on startup and points at `http://localhost:7423` by default — change `serenCorpusCallosum.endpoint` if yours runs elsewhere.
3. **If your service has auth on**, run *Set Bearer Token*. The token lives in the OS keychain (VS Code `SecretStorage`), is sent as `Authorization: Bearer …`, and is never written to settings or synced.

## Settings

| Setting | Default | Notes |
|---|---|---|
| `serenCorpusCallosum.endpoint` | `http://localhost:7423` | Base URL of the service |
| `serenCorpusCallosum.startCommand` | `seren-corpus-callosum` | Used by *Start Service*. **Application-scoped** so a workspace `.vscode/settings.json` can't hijack it |
| `serenCorpusCallosum.suppressStartPrompt` | `false` | Silence the "service not reachable — start it?" prompt |

> The bearer token is **not** a setting — it's in the OS keychain. Use the *Set Bearer Token* command.

## The family

| Service | Role | Extension tool(s) |
|---|---|---|
| [SerenMemory](https://github.com/ChadRoesler/SerenMemory) | right brain — episodic memory | `recall`, … |
| [SerenLoci](https://github.com/ChadRoesler/SerenLoci) | left brain — keyed facts | `search_loci`, `set_fact`, … |
| **SerenCorpusCallosum** | **the fan over all of them** | **`serenSearch` — the whole brain** |

## License

GPL-3.0-or-later.
