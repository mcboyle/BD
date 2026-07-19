import { describe, it, expect, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { StickySaveBar } from "./StickySaveBar";

// Cut 5 — StickySaveBar: governs the global_config form's single save.
// position: sticky (mobile-drawer-safe), inert when clean, never `fixed`.

describe("StickySaveBar (Cut 5)", () => {
  it("is inert when there are no changes", () => {
    const { container } = render(
      <StickySaveBar changedCount={0} onSave={() => {}} onDiscard={() => {}} />
    );
    // Nothing actionable surfaces when clean.
    expect(screen.queryByRole("button", { name: /save/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /discard/i })).not.toBeInTheDocument();
    expect(container.textContent || "").not.toMatch(/unsaved/i);
  });

  it("shows the changed count and Save/Discard when dirty, firing callbacks", () => {
    const onSave = vi.fn();
    const onDiscard = vi.fn();
    render(<StickySaveBar changedCount={3} onSave={onSave} onDiscard={onDiscard} />);
    expect(screen.getByText(/3/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    fireEvent.click(screen.getByRole("button", { name: /discard/i }));
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onDiscard).toHaveBeenCalledTimes(1);
  });

  it("disables Save while saving and uses sticky (not fixed) positioning", () => {
    const { container } = render(
      <StickySaveBar changedCount={2} onSave={() => {}} onDiscard={() => {}} saving />
    );
    expect(screen.getByRole("button", { name: /sav/i })).toBeDisabled();
    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toMatch(/sticky/);
    expect(root.className).not.toMatch(/fixed/);
  });
});
