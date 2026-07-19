import { describe, it, expect, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ConfirmDialog } from "./ConfirmDialog";

// Cut 1 substrate: shared destructive confirm (target + consequence + optional
// typed-confirm), generalizing the existing throttle/Class-B + payload bulk-
// delete confirms. No destructive default focus.

describe("ConfirmDialog", () => {
  it("shows the target and consequence when open", () => {
    render(
      <ConfirmDialog
        open
        target="all stored secrets"
        consequence="This permanently deletes them."
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText(/all stored secrets/)).toBeInTheDocument();
    expect(screen.getByText(/permanently deletes/)).toBeInTheDocument();
  });

  it("fires onConfirm when confirmed (no requireType)", () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        open
        target="the queue"
        consequence="Clears all jobs."
        onConfirm={onConfirm}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /confirm/i }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("gates nothing behind typed entry (token entry retired, v3.66.209)", () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        open
        target="site config"
        consequence="Irreversible."
        onConfirm={onConfirm}
        onCancel={() => {}}
      />,
    );
    // No token-entry field exists; confirm is immediately actionable.
    expect(screen.queryByRole("textbox")).toBeNull();
    const confirmBtn = screen.getByRole("button", { name: /confirm/i });
    expect(confirmBtn).toBeEnabled();
    fireEvent.click(confirmBtn);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("makes Cancel the default focus, not the destructive confirm (Tier A)", () => {
    render(
      <ConfirmDialog
        open
        target="x"
        consequence="y"
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    const confirmBtn = screen.getByRole("button", { name: /confirm/i });
    expect(confirmBtn).not.toHaveFocus();
  });
});
