/**
 * o1826-c48 M205: every CSRF-aware write shares one refetch-retry helper, and
 * a write that still fails after the retry throws an ApiError that carries the
 * backend's JSON body. Before the merge apiPost/apiPut/apiPatch/apiDelete each
 * copied the retry block and threw `new ApiError(..., r2.status)` with no body,
 * so the toast lost the server's reason exactly on the rotated-token path.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const STALE = "csrf-stale";
const FRESH = "csrf-fresh";
const PATH = "/api/fixture/write";
const REASON = "fixture rejected the write";

function headerOf(init: RequestInit | undefined, name: string): string | undefined {
  return (init?.headers as Record<string, string> | undefined)?.[name];
}

describe("CSRF retry keeps the error body", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let csrfTokens: string[];

  beforeEach(() => {
    vi.resetModules();
    csrfTokens = [STALE, FRESH];
    fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/csrf") {
        const tok = csrfTokens.length > 1 ? csrfTokens.shift()! : csrfTokens[0];
        return new Response(JSON.stringify({ csrf_token: tok }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (headerOf(init, "X-CSRF-Token") !== FRESH) {
        return new Response(JSON.stringify({ error: "csrf" }), { status: 403 });
      }
      return new Response(JSON.stringify({ error: REASON }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const writes = () => fetchMock.mock.calls.filter(([url]) => String(url) === PATH);

  for (const method of ["POST", "PUT", "PATCH", "DELETE"] as const) {
    it(`${method} retry failure carries the backend body`, async () => {
      const api = await import("./api-client");
      const ac = new AbortController();
      const call = {
        POST: () => api.apiPost(PATH, { a: 1 }, ac.signal),
        PUT: () => api.apiPut(PATH, { a: 1 }, ac.signal),
        PATCH: () => api.apiPatch(PATH, { a: 1 }, ac.signal),
        DELETE: () => api.apiDelete(PATH, ac.signal),
      }[method];
      const err = await call().then(
        () => null,
        (e: unknown) => e,
      );
      expect(err).toBeInstanceOf(api.ApiError);
      const apiErr = err as InstanceType<typeof api.ApiError>;
      // Precondition: the retry really happened (stale 403, then fresh 400).
      expect(writes().map(([, init]) => headerOf(init, "X-CSRF-Token"))).toEqual([STALE, FRESH]);
      expect(apiErr.status).toBe(400);
      expect(apiErr.body, `M205-RETRY-BODY-DROPPED: ${method}`).toEqual({ error: REASON });
      expect(apiErr.message).toBe(`${method} ${PATH} → 400: ${REASON}`);
      // The retry keeps the caller's AbortSignal, so an abort still cancels it.
      const [firstInit, retryInit] = writes().map(([, init]) => init);
      expect(firstInit?.signal, `M205-RETRY-SIGNAL-DROPPED: ${method} first send`).toBe(ac.signal);
      expect(retryInit?.signal, `M205-RETRY-SIGNAL-DROPPED: ${method} retry`).toBe(ac.signal);
      if (method !== "DELETE") {
        // The retry keeps the original headers: without Content-Type the server reads no JSON body.
        expect(headerOf(retryInit, "Content-Type"), `M205-RETRY-CT-DROPPED: ${method} retry`).toBe(
          "application/json",
        );
        // The retry resends the same request body, not just method + signal.
        const [first, retry] = writes().map(([, init]) => init?.body);
        expect(first, `M205-RETRY-BODY-RESEND: ${method} first send`).toBe(JSON.stringify({ a: 1 }));
        expect(retry, `M205-RETRY-BODY-RESEND: ${method} retry`).toBe(first);
      }
    });
  }

  it("apiPostForm retry resends the same FormData with no Content-Type", async () => {
    const api = await import("./api-client");
    const form = new FormData();
    form.append("field", "value");
    const ac = new AbortController();
    const err = await api.apiPostForm(PATH, form, ac.signal).then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(api.ApiError);
    expect(writes().map(([, init]) => headerOf(init, "X-CSRF-Token"))).toEqual([STALE, FRESH]);
    const [first, retry] = writes().map(([, init]) => init);
    expect(first?.body, "M205-RETRY-BODY-RESEND: PostForm first send").toBe(form);
    expect(retry?.body, "M205-RETRY-BODY-RESEND: PostForm retry").toBe(form);
    expect(headerOf(retry, "Content-Type"), "PostForm retry must not set Content-Type").toBeUndefined();
    expect(retry?.signal, "M205-RETRY-SIGNAL-DROPPED: PostForm retry").toBe(ac.signal);
  });

  it("apiPostDownload retry resends the same JSON body", async () => {
    const api = await import("./api-client");
    const ac = new AbortController();
    const err = await api.apiPostDownload(PATH, { a: 1 }, "fallback.bin", ac.signal).then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(api.ApiError);
    expect((err as InstanceType<typeof api.ApiError>).body).toEqual({ error: REASON });
    expect(writes().map(([, init]) => headerOf(init, "X-CSRF-Token"))).toEqual([STALE, FRESH]);
    const [first, retry] = writes().map(([, init]) => init);
    expect(first?.body, "M205-RETRY-BODY-RESEND: PostDownload first send").toBe(JSON.stringify({ a: 1 }));
    expect(retry?.body, "M205-RETRY-BODY-RESEND: PostDownload retry").toBe(first?.body);
    expect(headerOf(retry, "Content-Type"), "M205-RETRY-CT-DROPPED: PostDownload retry").toBe(
      "application/json",
    );
    expect(retry?.signal, "M205-RETRY-SIGNAL-DROPPED: PostDownload retry").toBe(ac.signal);
  });
});
