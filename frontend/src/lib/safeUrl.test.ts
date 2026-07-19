import { describe, it, expect } from "vitest";

// F-FE06-01 — a review-queue item's URL is DATA and was rendered directly into
// an <a href> with no scheme validation. React does not sanitize href, so a
// `javascript:`/`data:` URL would execute on click. isHttpUrl is the shared
// allowlist gate: only http(s) URLs are safe to place in an href.
import { isHttpUrl } from "./safeUrl";

describe("isHttpUrl (F-FE06-01 href scheme allowlist)", () => {
  it("accepts http and https (any case)", () => {
    expect(isHttpUrl("http://example.com/x")).toBe(true);
    expect(isHttpUrl("https://example.com/x?y=1")).toBe(true);
    expect(isHttpUrl("HTTPS://EXAMPLE.COM")).toBe(true);
    expect(isHttpUrl("  https://example.com  ")).toBe(true); // surrounding ws tolerated
  });

  it("rejects dangerous and non-http schemes", () => {
    expect(isHttpUrl("javascript:alert(document.cookie)")).toBe(false);
    expect(isHttpUrl("data:text/html,<script>1</script>")).toBe(false);
    expect(isHttpUrl("vbscript:msgbox(1)")).toBe(false);
    expect(isHttpUrl("ftp://host/x")).toBe(false);
    expect(isHttpUrl("mailto:a@b.c")).toBe(false);
    expect(isHttpUrl("//protocol-relative.example")).toBe(false);
    expect(isHttpUrl("/relative/path")).toBe(false);
    expect(isHttpUrl("  javascript:alert(1)")).toBe(false); // leading ws must not smuggle a scheme
    expect(isHttpUrl("")).toBe(false);
  });

  it("rejects non-string input defensively", () => {
    // current.url is typed `unknown` at the call site
    expect(isHttpUrl(null as unknown as string)).toBe(false);
    expect(isHttpUrl(undefined as unknown as string)).toBe(false);
    expect(isHttpUrl(123 as unknown as string)).toBe(false);
  });
});
