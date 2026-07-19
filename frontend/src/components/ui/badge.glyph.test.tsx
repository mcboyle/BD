import { describe, it, expect } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { Badge } from "./badge";

// Cut A (Cut 1 adoption remainder) — badge glyphs so color isn't the only
// signal. Badge gains an optional `glyph` slot (✓/!/⏸/✕/•) rendered decoratively
// (aria-hidden) before the label, so a status badge reads in monochrome / for
// color-vision-deficient users without changing the variant color semantics.

describe("Badge glyph affordance (Cut A)", () => {
  it("renders a provided glyph", () => {
    render(<Badge variant="success" glyph="✓">Active</Badge>);
    expect(screen.getByText("✓")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("marks the glyph decorative (aria-hidden) so SR reads only the label", () => {
    render(<Badge variant="destructive" glyph="✕">Failed</Badge>);
    const glyph = screen.getByText("✕");
    expect(glyph).toHaveAttribute("aria-hidden");
  });

  it("renders without a glyph (unchanged callers)", () => {
    const { container } = render(<Badge variant="secondary">12</Badge>);
    expect(screen.getByText("12")).toBeInTheDocument();
    // no stray decorative span when glyph is absent
    expect(container.querySelector("[aria-hidden]")).toBeNull();
  });
});
