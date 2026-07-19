import { describe, it, expect } from "vitest";
import { parsePathAllowlist } from "./pathAllowlist";

describe("parsePathAllowlist", () => {
  it("accepts absolute roots, trimming and dropping blank lines", () => {
    const r = parsePathAllowlist("  /srv/data \n\n/mnt/captures\n");
    expect(r).toEqual({ roots: ["/srv/data", "/mnt/captures"] });
  });
  it("treats an empty/whitespace value as an empty allowlist", () => {
    expect(parsePathAllowlist("   \n ")).toEqual({ roots: [] });
  });
  it("rejects a relative path", () => {
    const r = parsePathAllowlist("/ok\nrelative/path");
    expect("error" in r && r.error).toContain("relative/path");
  });
  it("rejects path traversal", () => {
    const r = parsePathAllowlist("/ok\n/bad/../escape");
    expect("error" in r).toBe(true);
  });
});
