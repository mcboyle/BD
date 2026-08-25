/**
 * Every state-changing request from the SPA carries X-CSRF-Token.
 *
 * WHY THIS EXISTS SEPARATELY FROM THE *.wired.test.tsx SPECS. Those specs mock
 * `@/lib/api-client` wholesale, so they can prove a component CALLED apiPost
 * with a given path and body, and nothing at all about what went over the wire.
 * An adversarial review of the batch-B replacement made exactly that point: the
 * row-194 spec pins a spy identity, which is the same class of assertion as the
 * grep it replaced, relocated rather than behavioralized.
 *
 * The spy assertion still catches row 194's evasion -- swapping apiPost for a
 * raw fetch means apiPostMock is never called -- but it catches it BY PROXY.
 * The property that actually matters is that the request carries the token, and
 * before this file NO frontend spec asserted it anywhere: a grep for
 * X-CSRF-Token across all 122 spec files returned nothing.
 *
 * The backend half IS covered -- tests/test_t11_approval_wired.py drives real
 * Flask routes and asserts a tokenless write is REJECTED. That proves the server
 * refuses; it cannot prove the client sends. This file is the other half.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const CSRF = "csrf-token-under-test";

describe("api-client CSRF contract", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.resetModules();
    fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/csrf")) {
        return new Response(JSON.stringify({ csrf_token: CSRF }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("attaches X-CSRF-Token to a POST", async () => {
    const { apiPost } = await import("@/lib/api-client");
    await apiPost("/api/sites/42/auto_submit_decision", { decision: "approve" });

    const csrfCalls = fetchMock.mock.calls.filter(
      ([url]) => String(url) === "/api/csrf",
    );
    expect(csrfCalls, "the wrapper did not source its token from exactly /api/csrf")
      .toHaveLength(1);

    const write = fetchMock.mock.calls.find(
      ([url]) => String(url) === "/api/sites/42/auto_submit_decision",
    );
    expect(write, "the decision write never reached fetch").toBeTruthy();

    const headers = (write![1] as RequestInit).headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe(CSRF);
  });

  it("sends the write same-origin so the session cookie is attached", async () => {
    const { apiPost } = await import("@/lib/api-client");
    await apiPost("/api/sites/42/auto_submit_decision", { decision: "decline" });
    const write = fetchMock.mock.calls.find(
      ([url]) => String(url) === "/api/sites/42/auto_submit_decision",
    );
    expect((write![1] as RequestInit).credentials).toBe("same-origin");
  });

  it("NEGATIVE CONTROL: a bare fetch carries no token, which is the evasion", async () => {
    // Row 194's evasion replaced apiPost with a raw fetch. This pins what that
    // actually costs, so the assertions above are not merely describing the
    // implementation they were written against: the same call made directly is
    // observably tokenless, and a reviewer can see the difference rather than
    // take it on faith.
    await fetch("/api/sites/42/auto_submit_decision", {
      method: "POST",
      body: JSON.stringify({ decision: "approve" }),
    });
    const raw = fetchMock.mock.calls.at(-1)!;
    const headers = ((raw[1] as RequestInit).headers ?? {}) as Record<
      string,
      string
    >;
    expect(headers["X-CSRF-Token"]).toBeUndefined();
  });
});
