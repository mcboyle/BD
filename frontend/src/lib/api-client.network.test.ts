/**
 * dsw-fix F10: a write whose request never completed (connection reset) used to
 * reject with the browser's bare "Failed to fetch", so the global mutation toast
 * named no action. It now names method + path. Controls: an HTTP failure stays an
 * ApiError carrying the server reason; an abort passes through untouched; the
 * global toast text (mutationErrorMessage) carries the action.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mutationErrorMessage } from "./mutationErrors";

const PATH = "/api/tags/add";

describe("network failure on a write names the action", () => {
  let mode: "reset" | "http500" | "abort" | "403-then-reset";

  let csrfCalls: number;

  beforeEach(() => {
    vi.resetModules();
    mode = "reset";
    csrfCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
        if (String(input) === "/api/csrf") {
          csrfCalls += 1;
          return new Response(JSON.stringify({ csrf_token: `t${csrfCalls}` }), { status: 200 });
        }
        if (mode === "403-then-reset") {
          mode = "reset";
          return new Response(JSON.stringify({ error: "csrf" }), { status: 403 });
        }
        if (mode === "reset") throw new TypeError("Failed to fetch");
        if (mode === "abort") throw new DOMException("The user aborted a request.", "AbortError");
        return new Response(JSON.stringify({ error: "boom" }), { status: 500 });
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("a reset connection rejects with method + path in the message", async () => {
    const { apiPost } = await import("./api-client");
    const err = await apiPost(PATH, { tag: "x" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(Error);
    expect((err as Error).message).toContain(`POST ${PATH}`);
    expect((err as Error).message).toContain("Failed to fetch");
    expect(mutationErrorMessage(err)).toContain(`POST ${PATH}`);
  });

  it("the network error is not an ApiError (no status, no server reason)", async () => {
    const { apiDelete, ApiError, NetworkError } = await import("./api-client");
    const err = await apiDelete("/api/library/7").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(NetworkError);
    expect(err).not.toBeInstanceOf(ApiError);
    expect((err as Error).message).toContain("DELETE /api/library/7");
  });

  it("the CSRF retry send names the action too", async () => {
    mode = "403-then-reset";
    const { apiPost, NetworkError } = await import("./api-client");
    const err = await apiPost(PATH, {}).catch((e: unknown) => e);
    expect(csrfCalls).toBe(2);
    expect(err).toBeInstanceOf(NetworkError);
    expect((err as Error).message).toContain(`POST ${PATH}`);
  });

  it("control: an HTTP 500 is still an ApiError with the server reason", async () => {
    mode = "http500";
    const { apiPost, ApiError } = await import("./api-client");
    const err = await apiPost(PATH, {}).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as Error).message).toBe(`POST ${PATH} → 500: boom`);
  });

  it("control: an abort passes through as the AbortError", async () => {
    mode = "abort";
    const { apiPost, NetworkError } = await import("./api-client");
    const err = await apiPost(PATH, {}).catch((e: unknown) => e);
    expect((err as Error).name).toBe("AbortError");
    expect(err).not.toBeInstanceOf(NetworkError);
  });
});
