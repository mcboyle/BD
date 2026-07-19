import { describe, it, expect } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeMenu } from "./ThemeMenu";

// Cut 1 substrate: one theme control reused in the sidebar footer AND Settings
// (both just drive useTheme). inline = expanded inline control; compact =
// icon -> popover (collapsed sidebar / mobile). ThemeToggle becomes a thin
// re-export of the inline variant.

describe("ThemeMenu", () => {
  it("renders the quick theme switches in the inline variant", () => {
    render(<ThemeMenu variant="inline" />);
    expect(screen.getByRole("button", { name: /system/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /light/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /dark/i })).toBeInTheDocument();
  });

  it("compact variant renders a single trigger that opens the picker", () => {
    render(<ThemeMenu variant="compact" />);
    const trigger = screen.getByRole("button", { name: /theme/i });
    expect(trigger).toBeInTheDocument();
    // Quick switches are not visible until the trigger is opened.
    expect(screen.queryByRole("button", { name: /^light$/i })).toBeNull();
    fireEvent.click(trigger);
    expect(screen.getByRole("button", { name: /^light$/i })).toBeInTheDocument();
  });

  it("selecting a mode applies the theme (persists to storage)", () => {
    render(<ThemeMenu variant="inline" />);
    fireEvent.click(screen.getByRole("button", { name: /dark/i }));
    expect(localStorage.getItem("bd-theme")).toBe("dark");
  });
});
