import { SerenConfig } from "./config";

export class SerenApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: unknown,
    message: string
  ) {
    super(message);
    this.name = "SerenApiError";
  }
}

/**
 * HTTP client for SerenCorpusCallosum - the callosum, the read-only fan over
 * every Seren memory store. It owns no data of its own, so this client is
 * deliberately small: one federated search, plus a liveness ping. There is no
 * set / get / forget here - SCC has nothing to write to. Keyed facts live in
 * SerenLoci, episodes in SerenMemory; the callosum only fans across them and
 * RRF-merges what they hand back.
 *
 * CONTRACT (verified against the SCC routes - don't drift):
 *   POST /search  { query, n_results }
 *     -> { query,
 *          hits: [{ store, id, content, score, store_rank, base_relevance,
 *                   native_score?, raw_distance?, metadata }],
 *          stores_searched, skipped }
 *     SCC's SearchRequest is just {query, n_results}; pydantic ignores extras,
 *     so sending more fields is silently dropped, not honored - don't bother.
 *   GET  /health   liveness.
 *   GET  /         service info + the stores it's fanning.
 *   GET  /viewer   the Bridge UI (opened by a command, not this client).
 *   GET/POST/DELETE /stores  roster management - deliberately NOT surfaced as
 *     an LM tool (you don't want a model wiring in stores mid-completion); it
 *     lives in the viewer, behind the token.
 *
 * Every request takes an optional AbortSignal so the VS Code cancellation
 * token from a tool's invoke() can actually cancel the in-flight fetch.
 */
export class SerenClient {
  constructor(private readonly config: SerenConfig) {}

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
    signal?: AbortSignal
  ): Promise<T> {
    const headers = await this.config.getHeaders();
    const response = await fetch(`${this.config.endpoint}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });

    let json: unknown;
    const ct = response.headers.get("content-type") ?? "";
    if (ct.includes("application/json")) {
      json = await response.json();
    } else {
      json = await response.text();
    }

    if (!response.ok) {
      throw new SerenApiError(
        response.status,
        json,
        `SerenCorpusCallosum ${method} ${path} failed: ${response.status}`
      );
    }
    return json as T;
  }

  private post<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    return this.request<T>("POST", path, body, signal);
  }

  // -- health -----------------------------------------------------------------

  async ping(): Promise<boolean> {
    try {
      await fetch(`${this.config.endpoint}/health`, { signal: AbortSignal.timeout(3000) });
      return true;
    } catch {
      return false;
    }
  }

  // -- the one tool that matters ----------------------------------------------

  /** Fan a query across every configured store and return the RRF-merged,
   *  ranked list with full provenance. */
  async search(query: string, n_results: number = 10, signal?: AbortSignal): Promise<unknown> {
    return this.post("/search", { query, n_results }, signal);
  }
}
