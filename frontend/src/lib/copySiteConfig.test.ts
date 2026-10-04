import { describe, it, expect } from "vitest";

// Cut 6.7 — copy-whole-site-config. Pure helper that serialises a site config
// for the clipboard with SECRET VALUES OMITTED (refs/keys may remain, values
// never). Mirrors the export-redaction discipline used elsewhere in the SPA.
import { buildSiteConfigClipboard } from "./copySiteConfig";

describe("buildSiteConfigClipboard (Cut 6.7)", () => {
  const site = {
    site_id: "example",
    base_url: "https://example.com",
    concurrency: 4,
    password: "hunter2",
    api_token: "tok_live_abcdef",
    cookie: "session=deadbeef",
    notes: "public note",
  };

  it("includes non-secret config", () => {
    const out = buildSiteConfigClipboard(site);
    expect(out).toContain("example");
    expect(out).toContain("https://example.com");
    expect(out).toContain("public note");
  });

  it("never emits secret VALUES", () => {
    const out = buildSiteConfigClipboard(site);
    expect(out).not.toContain("hunter2");
    expect(out).not.toContain("tok_live_abcdef");
    expect(out).not.toContain("deadbeef");
  });

  it("returns a non-empty serialisation", () => {
    expect(buildSiteConfigClipboard(site).length).toBeGreaterThan(0);
  });
});

// F-FE09-01 — the clipboard redaction keyword set drifted narrower than the
// server I0008 SoT: the OAuth/CSRF token class was copied in plaintext. "Safe
// tokens only": add the always-secret names; do NOT add the ambiguous code/state
// (they are often benign config fields and would be over-redacted).
describe("buildSiteConfigClipboard OAuth/CSRF token class (F-FE09-01)", () => {
  const REDACTED = "<omitted>";

  it("redacts the always-secret token class that used to leak", () => {
    const cfg = {
      csrf: "s1", xsrf: "s2", bearer: "s3", otp: "s4", nonce: "s5",
      challenge: "s6", captcha: "s7", jwt: "s8", signature: "s9",
    };
    const out = JSON.parse(buildSiteConfigClipboard(cfg));
    for (const k of Object.keys(cfg)) {
      expect(out[k], `${k} value must be redacted`).toBe(REDACTED);
    }
  });

  it("still redacts the original secret keys", () => {
    const cfg = { password: "p", api_token: "t", session_cookie: "c", auth_key: "k" };
    const out = JSON.parse(buildSiteConfigClipboard(cfg));
    for (const k of Object.keys(cfg)) expect(out[k]).toBe(REDACTED);
  });

  it("does NOT over-redact benign fields (incl. the ambiguous code/state)", () => {
    const cfg = { code: "200", state: "CA", resolution: "1080p", format: "mp4", label: "x" };
    const out = JSON.parse(buildSiteConfigClipboard(cfg));
    for (const [k, v] of Object.entries(cfg)) {
      expect(out[k], `${k} must NOT be redacted`).toBe(v);
    }
  });
});

// o1671-a13-copysiteconfig-nested-secrets (O1671 AUDIT-13, MED, security). The copy
// walked Object.entries(site) ONE level deep: a nested block under a benign key
// ("login", "accounts", "profiles") was serialised verbatim, so passwords, cookies
// and tokens inside it reached the clipboard in plaintext while the same keys at
// the top level were redacted. Contract: the secret-key predicate applies at every
// depth, through objects AND arrays, and the output keeps its shape.
describe("buildSiteConfigClipboard nested secrets (o1671-a13)", () => {
  const REDACTED = "<omitted>";

  it("redacts secret keys inside a nested object under a benign key", () => {
    const cfg = { login: { user: "bob", password: "hunter2", cookie: "session=deadbeef" } };
    const text = buildSiteConfigClipboard(cfg);
    expect(text).not.toContain("hunter2");
    expect(text).not.toContain("deadbeef");
    const out = JSON.parse(text);
    expect(out.login.user).toBe("bob");
    expect(out.login.password).toBe(REDACTED);
    expect(out.login.cookie).toBe(REDACTED);
  });

  it("redacts secret keys inside objects held in arrays, keeping the array shape", () => {
    const cfg = { accounts: [{ user: "a", api_token: "tok_a" }, { user: "b", api_token: "tok_b" }] };
    const text = buildSiteConfigClipboard(cfg);
    expect(text).not.toContain("tok_a");
    expect(text).not.toContain("tok_b");
    const out = JSON.parse(text);
    expect(Array.isArray(out.accounts)).toBe(true);
    expect(out.accounts.map((a: { user: string }) => a.user)).toEqual(["a", "b"]);
    expect(out.accounts.every((a: { api_token: string }) => a.api_token === REDACTED)).toBe(true);
  });

  it("redacts at any depth", () => {
    const cfg = { profiles: { main: { headers: [{ name: "x", bearer: "deep-secret" }] } } };
    const text = buildSiteConfigClipboard(cfg);
    expect(text).not.toContain("deep-secret");
    expect(JSON.parse(text).profiles.main.headers[0]).toEqual({ name: "x", bearer: REDACTED });
  });

  it("control: the same keys at the top level were already redacted (probe can say yes)", () => {
    const out = JSON.parse(buildSiteConfigClipboard({ password: "hunter2", cookie: "c", api_token: "t" }));
    expect(out).toEqual({ password: REDACTED, cookie: REDACTED, api_token: REDACTED });
  });

  it("negative control: benign nested values and scalars keep their shape and value", () => {
    const cfg = {
      retry: { attempts: 3, backoff_ms: 250, enabled: true, tag: null },
      formats: ["mp4", "webm"],
      limits: [{ per_host: 2 }, { per_site: 4 }],
      label: "x",
    };
    expect(JSON.parse(buildSiteConfigClipboard(cfg))).toEqual(cfg);
  });

  it("a secret key whose value is a whole block is replaced by the marker, never serialised", () => {
    const cfg = { credentials: { user: "bob", note: "do-not-leak" } };
    const text = buildSiteConfigClipboard(cfg);
    expect(text).not.toContain("do-not-leak");
    expect(JSON.parse(text).credentials).toBe(REDACTED);
  });
});
