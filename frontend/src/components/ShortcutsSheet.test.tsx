import { describe, it, expect, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ShortcutsSheet } from "./ShortcutsSheet";

// Cut 2 — ShortcutsSheet: the `?` cheat-sheet. Lists the keyboard layer's
// shortcuts (g-jumps, `/` filter, `?` help). Reuses the dialog primitive; Esc
// / overlay / close button dismisses via onOpenChange(false).

describe("ShortcutsSheet (Cut 2)", () => {
  it("renders the shortcut list when open", () => {
    render(<ShortcutsSheet open onOpenChange={() => {}} />);
    // The sheet documents the g-prefix jump and the / filter + ? help keys.
    expect(screen.getByText(/keyboard shortcuts/i)).toBeInTheDocument();
    expect(screen.getByText(/sites/i)).toBeInTheDocument();
    // The `/` filter shortcut and `?` help are listed.
    expect(screen.getByText(/filter/i)).toBeInTheDocument();
  });

  it("does not render its content when closed", () => {
    render(<ShortcutsSheet open={false} onOpenChange={() => {}} />);
    expect(screen.queryByText(/keyboard shortcuts/i)).not.toBeInTheDocument();
  });

  it("calls onOpenChange(false) when dismissed", () => {
    const onOpenChange = vi.fn();
    render(<ShortcutsSheet open onOpenChange={onOpenChange} />);
    // Esc closes (dialog primitive handles the key); simulate via the close affordance.
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
