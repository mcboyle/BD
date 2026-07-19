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
