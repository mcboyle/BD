import { describe, it, expect } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// Cut 6.5 — Needs-attention Home rollup. Aggregates EXISTING counts (failed
// runs, review backlog, expired cookies, drift) into linked rows; review -> Cockpit.
// Renders null when there is nothing to surface.
import { NeedsAttentionRollup } from "./NeedsAttentionRollup";

type Entry = { kind: string; label: string; count: number; href: string };

function mount(entries: Entry[]) {
  return render(
    <MemoryRouter>
      <NeedsAttentionRollup entries={entries} />
    </MemoryRouter>,
  );
}

describe("NeedsAttentionRollup (Cut 6.5)", () => {
  it("aggregates multiple sources into linked rows", () => {
    const { container } = mount([
      { kind: "failed", label: "Failed runs", count: 4, href: "/queue?status=failed" },
      { kind: "review", label: "Needs review", count: 2, href: "/cockpit/review" },
    ]);
    expect(screen.getByText(/failed runs/i)).toBeInTheDocument();
    expect(screen.getByText(/needs review/i)).toBeInTheDocument();
    expect(container.querySelectorAll("a").length).toBeGreaterThanOrEqual(2);
  });

  it("review row links into the Cockpit", () => {
    mount([{ kind: "review", label: "Needs review", count: 2, href: "/cockpit/review" }]);
    const link = screen.getByRole("link", { name: /needs review/i });
    expect(link.getAttribute("href")).toMatch(/cockpit/i);
  });

  it("renders null when there is nothing to surface", () => {
    const { container } = mount([]);
    expect(container.firstChild).toBeNull();
  });
});
