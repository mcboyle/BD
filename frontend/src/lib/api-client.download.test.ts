/**
 * apiPostDownload must survive CSRF token rotation the way apiPost/apiPut/
 * apiPatch/apiDelete do: on 403, drop the cached token, refetch /api/csrf and
 * retry exactly once with the new token. Before O1671 r8c it threw on the
 * first 403, so a backup export failed whenever the session token had rotated
 * since the page cached it (AUDIT-13, Backup.tsx -> apiPostDownload).
 *
 * Kept separate from api-client.csrf.test.ts so that file stays untouched.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const STALE = "csrf-stale";
const FRESH = "csrf-fresh";
const PATH = "/api/backup/create";

function headerOf(init: RequestInit | undefined, name: string): string | undefined {
  return (init?.headers as Record<string, string> | undefined)?.[name];
}

describe("apiPostDownload CSRF retry", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let csrfTokens: string[];
  let createObjectURL: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.resetModules();
    csrfTokens = [STALE, FRESH];
    fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/csrf") {
        const tok = csrfTokens.length > 1 ? csrfTokens.shift()! : csrfTokens[0];
        return new Response(JSON.stringify({ csrf_token: tok }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (headerOf(init, "X-CSRF-Token") !== FRESH) {
        return new Response(JSON.stringify({ error: "csrf" }), {
          status: 403,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(new Blob(["archive-bytes"]), {
        status: 200,
        headers: { "Content-Disposition": 'attachment; filename="bd-backup.tar.gz"' },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    createObjectURL = vi.fn(() => "blob:test");
    vi.stubGlobal("URL", Object.assign(URL, {
      createObjectURL,
      revokeObjectURL: vi.fn(),
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const downloadPosts = () =>
    fetchMock.mock.calls.filter(([url]) => String(url) === PATH);

  it("refetches the token on 403 and retries once with the fresh token", async () => {
    const { apiPostDownload } = await import("@/lib/api-client");
    const outcome = await apiPostDownload(PATH, { scope: "all" }, "fallback.tar.gz")
      .then(() => "ok", (e: unknown) => e);

    const posts = downloadPosts();
    expect(posts, "DOWNLOAD-CSRF-NO-RETRY: expected stale POST then one retry")
      .toHaveLength(2);
    expect(outcome).toBe("ok");
    expect(headerOf(posts[0][1], "X-CSRF-Token")).toBe(STALE);
    expect(headerOf(posts[1][1], "X-CSRF-Token")).toBe(FRESH);
    expect(posts[1][1]?.body).toBe(JSON.stringify({ scope: "all" }));
    expect(createObjectURL).toHaveBeenCalledTimes(1);
  });

  it("does not retry when the refetched token is unchanged (negative control)", async () => {
    csrfTokens = [STALE];
    const { apiPostDownload, ApiError } = await import("@/lib/api-client");
    const p = apiPostDownload(PATH, {}, "fallback.tar.gz");
    await expect(p).rejects.toBeInstanceOf(ApiError);
    await expect(p).rejects.toMatchObject({ status: 403 });
    expect(downloadPosts()).toHaveLength(1);
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it("surfaces the retry's failure status instead of downloading", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/csrf") {
        const tok = csrfTokens.length > 1 ? csrfTokens.shift()! : csrfTokens[0];
        return new Response(JSON.stringify({ csrf_token: tok }), { status: 200 });
      }
      const status = headerOf(init, "X-CSRF-Token") === FRESH ? 500 : 403;
      return new Response(JSON.stringify({ error: "boom" }), { status });
    });
    const { apiPostDownload } = await import("@/lib/api-client");
    await expect(apiPostDownload(PATH, {}, "fallback.tar.gz"))
      .rejects.toMatchObject({ status: 500, body: { error: "boom" } });
    expect(downloadPosts()).toHaveLength(2);
    expect(createObjectURL).not.toHaveBeenCalled();
  });
});
