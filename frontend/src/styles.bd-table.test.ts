import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

// Cut 9.1 — the shared .bd-table class set must live in index.css (one place),
// give every adopting table a sticky header + hairline borders from the design
// tokens, and expose a compact-density hook.
const css = readFileSync(join(process.cwd(), "src/index.css"), "utf8");

describe(".bd-table shared class set (Cut 9.1)", () => {
  it("defines a .bd-table rule", () => {
    expect(css).toMatch(/\.bd-table\b/);
  });
  it("gives the header sticky top:0 positioning", () => {
    expect(css).toMatch(/\.bd-table\s+thead\s+th\s*\{[^}]*position:\s*sticky/s);
    expect(css).toMatch(/\.bd-table\s+thead\s+th\s*\{[^}]*top:\s*0/s);
  });
  it("draws row separators with the hairline token", () => {
    expect(css).toMatch(/\.bd-table\s+tbody\s+td\s*\{[^}]*var\(--hairline\)/s);
  });
  it("exposes a compact-density variant", () => {
    expect(css).toMatch(/\.bd-table\[data-density="compact"\]/);
  });
});
