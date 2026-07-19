import { describe, it, expect } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// Cut 6.2 — read-only count tiles (queue / review / capture / template).
// Presentational: counts come from existing hooks at the call site; the tile
// strip just renders the numbers + links. Review tile links into the Cockpit.
import { CountTiles } from "./CountTiles";

function mount(counts: { queue: number; review: number; capture: number; template: number }) {
  return render(
    <MemoryRouter>
      <CountTiles counts={counts} />
    </MemoryRouter>,
  );
}

describe("CountTiles (Cut 6.2)", () => {
  it("renders all four counts", () => {
    mount({ queue: 7, review: 3, capture: 1, template: 12 });
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("review tile links into the Cockpit", () => {
    mount({ queue: 0, review: 5, capture: 0, template: 0 });
    const review = screen.getByRole("link", { name: /review/i });
    expect(review.getAttribute("href")).toMatch(/cockpit/i);
  });

  it("renders an explicit 0 (not blank) for an empty count", () => {
    mount({ queue: 0, review: 0, capture: 0, template: 0 });
    expect(screen.getAllByText("0").length).toBeGreaterThanOrEqual(4);
  });
});
