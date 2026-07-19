import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

// Cut 9.x — a11y/responsive audit-and-fill. The audit found a strong base
// (clickable rows keyboard-OK, icon buttons labeled, theme reachable via
// Settings on mobile); these are the three genuine gaps it surfaced.
const css = readFileSync(join(process.cwd(), "src/index.css"), "utf8");

describe("a11y/responsive fills (Cut 9)", () => {
  it("honors prefers-reduced-motion (neutralizes animation + transition)", () => {
    expect(css).toMatch(/@media\s*\(prefers-reduced-motion:\s*reduce\)/);
    expect(css).toMatch(
      /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*?animation-duration:\s*0\.01ms\s*!important/,
    );
    expect(css).toMatch(
      /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*?transition-duration:\s*0\.01ms\s*!important/,
    );
  });

  it("lets wide tables scroll horizontally on narrow viewports", () => {
    expect(css).toMatch(
      /@media\s*\(max-width:\s*480px\)\s*\{[\s\S]*?\.bd-table\s*\{[\s\S]*?overflow-x:\s*auto/,
    );
  });

  it("BulkActionBar wraps its controls on narrow widths", () => {
    const bar = readFileSync(
      join(process.cwd(), "src/components/BulkActionBar.tsx"),
      "utf8",
    );
    expect(bar).toMatch(/flex-wrap/);
  });
});
