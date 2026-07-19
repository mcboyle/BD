import { describe, it, expect } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { ThemeToggle } from "./ThemeToggle";

// Cut A (Cut 1 adoption remainder) — ThemeToggle becomes a thin re-export of
// ThemeMenu's `inline` variant: ONE theme control, no copied second picker.
// The inline variant renders the three quick switches (System / Light / Dark)
// plus the "More themes…" catalog select. This test pins that ThemeToggle now
// renders ThemeMenu's inline shape (catalog select present), which the legacy
// standalone picker did NOT expose as a labelled "Theme catalog" select.

describe("ThemeToggle is a ThemeMenu(inline) re-export (Cut A)", () => {
  it("renders the three quick switches", () => {
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: /system/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /light/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /dark/i })).toBeInTheDocument();
  });

  it("renders the ThemeMenu catalog select (its inline signature)", () => {
    render(<ThemeToggle />);
    // ThemeMenu inline labels its catalog dropdown "Theme catalog"; the legacy
    // standalone ThemeToggle did not. This is the discriminating signature.
    expect(
      screen.getByRole("combobox", { name: /theme catalog/i }),
    ).toBeInTheDocument();
  });
});
