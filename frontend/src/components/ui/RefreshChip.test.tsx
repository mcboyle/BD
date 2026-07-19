import { describe, it, expect, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RefreshChip } from "./RefreshChip";

// Cut 2 — RefreshChip: a header chip showing the last-updated label + a manual
// refresh button. Presentational + a refetch callback; no data fetching itself.

describe("RefreshChip (Cut 2)", () => {
  it("shows the last-updated label", () => {
    render(<RefreshChip updatedAt={Date.now()} onRefresh={() => {}} />);
    // "Updated <relative>" — at minimum the word "Updated" is present.
    expect(screen.getByText(/updated/i)).toBeInTheDocument();
  });

  it("renders a refresh control and fires onRefresh on click", () => {
    const onRefresh = vi.fn();
    render(<RefreshChip updatedAt={Date.now()} onRefresh={onRefresh} />);
    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    expect(onRefresh).toHaveBeenCalled();
  });

  it("still renders the refresh control when there is no timestamp", () => {
    const onRefresh = vi.fn();
    render(<RefreshChip updatedAt={0} onRefresh={onRefresh} />);
    expect(
      screen.getByRole("button", { name: /refresh/i }),
    ).toBeInTheDocument();
  });
});
