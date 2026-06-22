/**
 * Unit tests for SerenClient (the callosum's HTTP client).
 *
 * SCC is the read-only fan, so the client is tiny: one federated search plus a
 * liveness ping. These tests stub globalThis.fetch (no VS Code host, no live
 * service); the `vscode` module is aliased to test/mocks/vscode.ts in
 * vitest.config.ts so the import chain (client -> config -> vscode) resolves.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { SerenClient, SerenApiError } from "../seren_corpuscallosum-vscode/src/client";
import { SerenConfig } from "../seren_corpuscallosum-vscode/src/config";
import { SecretStorage } from "./mocks/vscode";

// -- helpers ------------------------------------------------------------------

function makeClient(endpoint = "http://localhost:7423"): SerenClient {
  const secrets = new SecretStorage();
  const config = new SerenConfig(secrets as any);
  // SerenConfig reads endpoint from the vscode stub (which returns defaults);
  // pin it to a known value so URL assertions are stable.
  Object.defineProperty(config, "endpoint", { get: () => endpoint });
  return new SerenClient(config);
}

function mockFetch(status: number, body: unknown): void {
  const response = new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
}

function lastFetch() {
  return (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
}

beforeEach(() => vi.restoreAllMocks());
afterEach(() => vi.restoreAllMocks());

// -- ping ---------------------------------------------------------------------

describe("ping", () => {
  it("returns true when /health responds", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
    expect(await makeClient().ping()).toBe(true);
  });

  it("returns false when fetch throws (service down)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ECONNREFUSED")));
    expect(await makeClient().ping()).toBe(false);
  });
});

// -- search -------------------------------------------------------------------

describe("search", () => {
  it("POSTs to /search with just query + n_results", async () => {
    mockFetch(200, { query: "cuda", hits: [], stores_searched: [], skipped: [] });
    const result = await makeClient().search("cuda runtime", 5);
    const [url, init] = lastFetch();
    expect(url).toBe("http://localhost:7423/search");
    expect(init.method).toBe("POST");
    // SCC only honors {query, n_results} - the client must not smuggle Loci-style
    // fields (project/include_*) that would just be silently dropped.
    expect(JSON.parse(init.body as string)).toEqual({
      query: "cuda runtime",
      n_results: 5,
    });
    expect(result).toEqual({ query: "cuda", hits: [], stores_searched: [], skipped: [] });
  });

  it("defaults n_results to 10", async () => {
    mockFetch(200, { hits: [] });
    await makeClient().search("anything");
    const [, init] = lastFetch();
    expect(JSON.parse(init.body as string)).toEqual({ query: "anything", n_results: 10 });
  });

  it("passes the fused, provenance-bearing response through untouched", async () => {
    const payload = {
      query: "q",
      hits: [
        { store: "facts", id: "1", content: "x", score: 0.0161, store_rank: 1, base_relevance: 0.84 },
      ],
      stores_searched: ["facts", "episodic"],
      skipped: [{ name: "down-store", reason: "timeout" }],
    };
    mockFetch(200, payload);
    expect(await makeClient().search("q")).toEqual(payload);
  });
});

// -- SerenApiError ------------------------------------------------------------

describe("SerenApiError", () => {
  it("is thrown on non-2xx responses", async () => {
    mockFetch(401, { error: "unauthorized" });
    await expect(makeClient().search("q")).rejects.toBeInstanceOf(SerenApiError);
  });

  it("carries status and body", async () => {
    mockFetch(401, { error: "unauthorized" });
    try {
      await makeClient().search("q");
    } catch (e) {
      expect(e).toBeInstanceOf(SerenApiError);
      expect((e as SerenApiError).status).toBe(401);
      expect((e as SerenApiError).body).toEqual({ error: "unauthorized" });
    }
  });
});
