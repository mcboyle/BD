import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// C7 11.1b: the auth.ts admin client functions must call the correct endpoints
// with the correct method/body so the sensitive routes stay SPA-wired.
import { setUserRole, setUserPassword, deleteUser } from "./auth";

const calls: { url: string; method: string; body?: unknown }[] = [];

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({
        url,
        method: init?.method ?? "GET",
        body: init?.body ? JSON.parse(init.body as string) : undefined,
      });
      return {
        ok: true,
        status: 200,
        json: async () => ({ ok: true }),
        headers: new Headers(),
      } as unknown as Response;
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

describe("auth admin client (C7 11.1b)", () => {
  const authCall = () => calls.find((c) => c.url.includes("/api/auth/users"))!;

  it("setUserRole POSTs to /role with the role body", async () => {
    await setUserRole("bob", "admin");
    const c = authCall();
    expect(c.url).toContain("/api/auth/users/bob/role");
    expect(c.method).toBe("POST");
    expect(c.body).toEqual({ role: "admin" });
  });

  it("setUserPassword POSTs to /password with the password body", async () => {
    await setUserPassword("carol", "newpw456");
    const c = authCall();
    expect(c.url).toContain("/api/auth/users/carol/password");
    expect(c.method).toBe("POST");
    expect(c.body).toEqual({ password: "newpw456" });
  });

  it("deleteUser DELETEs the user resource", async () => {
    await deleteUser("dave");
    const c = authCall();
    expect(c.url).toContain("/api/auth/users/dave");
    expect(c.method).toBe("DELETE");
  });

  it("URL-encodes the username", async () => {
    await setUserRole("a b/c", "viewer");
    const c = authCall();
    expect(c.url).toContain("/api/auth/users/a%20b%2Fc/role");
  });
});
