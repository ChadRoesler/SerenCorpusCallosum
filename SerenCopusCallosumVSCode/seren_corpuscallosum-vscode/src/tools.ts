import * as vscode from "vscode";
import { SerenClient, SerenApiError } from "./client";

// -- helpers ----------------------------------------------------------------

function ok(text: string): vscode.LanguageModelToolResult {
  return new vscode.LanguageModelToolResult([
    new vscode.LanguageModelTextPart(text),
  ]);
}

function err(e: unknown): vscode.LanguageModelToolResult {
  if (e instanceof SerenApiError) {
    return ok(`Error ${e.status}: ${JSON.stringify(e.body)}`);
  }
  if (e instanceof Error && e.name === "AbortError") {
    return ok("Cancelled by user/host.");
  }
  return ok(`Error: ${String(e)}`);
}

function json(data: unknown): vscode.LanguageModelToolResult {
  return ok(JSON.stringify(data, null, 2));
}

/** Bridge VS Code's CancellationToken to an AbortSignal so the underlying
 *  fetch can actually cancel. Without this, a hung fan means the tool call
 *  hangs forever regardless of VS Code's cancel button. */
function signalFromToken(token: vscode.CancellationToken): AbortSignal {
  const controller = new AbortController();
  if (token.isCancellationRequested) {
    controller.abort();
  } else {
    token.onCancellationRequested(() => controller.abort());
  }
  return controller.signal;
}

// -- seren_corpuscallosum_search --------------------------------------------
//
// The whole point of this extension: ONE tool that reaches the whole brain.
// SCC owns no data and exposes no CRUD - it fans the query across every
// configured store and hands back one RRF-merged list. (Store roster
// management lives in the Bridge viewer, not here - a model shouldn't be
// wiring in stores mid-completion.)

interface SearchInput {
  query: string;
  n_results?: number;
}

export class SearchTool implements vscode.LanguageModelTool<SearchInput> {
  constructor(private readonly client: SerenClient) {}

  async invoke(
    options: vscode.LanguageModelToolInvocationOptions<SearchInput>,
    token: vscode.CancellationToken
  ): Promise<vscode.LanguageModelToolResult> {
    const { query, n_results = 10 } = options.input;
    try {
      const result = await this.client.search(
        query, n_results, signalFromToken(token));
      return json(result);
    } catch (e) {
      return err(e);
    }
  }
}
