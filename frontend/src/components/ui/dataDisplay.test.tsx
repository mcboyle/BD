import { describe, it, expect, beforeEach } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DensityToggle } from "./DensityToggle";
import { SortHeader } from "./SortHeader";
import { SkeletonRows } from "./SkeletonRows";

// P6-1 (data display) — shared controls: a density segmented toggle, a sortable
// column header (aria-sort + caret), and a row-shaped skeleton list.

describe("DensityToggle (P6-1)", () => {
  beforeEach(() => {
    try { window.localStorage.clear(); } catch { /* ignore */ }
  });

  it("carries the data-density-toggle marker", () => {
    const { container } = render(<DensityToggle />);
    expect(container.querySelector("[data-density-toggle]")).toBeInTheDocument();
  });

  it("offers a comfortable and a compact option", () => {
    render(<DensityToggle />);
    expect(screen.getByRole("button", { name: /comfortable/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /compact/i })).toBeInTheDocument();
  });

  it("reflects the active density via aria-pressed and flips on click", () => {
    render(<DensityToggle />);
    const compact = screen.getByRole("button", { name: /compact/i });
    expect(compact).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(compact);
    expect(compact).toHaveAttribute("aria-pressed", "true");
    expect(window.localStorage.getItem("bd-density")).toBe("compact");
  });
});

describe("SortHeader (P6-1)", () => {
  it("renders a th with aria-sort reflecting the active state", () => {
    const { rerender } = render(
      <table><thead><tr>
        <SortHeader sortKey="size" active={null} dir="asc" onToggle={() => {}}>
          Size
        </SortHeader>
      </tr></thead></table>,
    );
    let th = screen.getByRole("columnheader");
    expect(th).toHaveAttribute("aria-sort", "none");
    rerender(
      <table><thead><tr>
        <SortHeader sortKey="size" active="size" dir="desc" onToggle={() => {}}>
          Size
        </SortHeader>
      </tr></thead></table>,
    );
    th = screen.getByRole("columnheader");
    expect(th).toHaveAttribute("aria-sort", "descending");
  });

  it("calls onToggle with its sortKey when the header button is clicked", () => {
    let got = "";
    render(
      <table><thead><tr>
        <SortHeader sortKey="name" active={null} dir="asc" onToggle={(k) => (got = k)}>
          Name
        </SortHeader>
      </tr></thead></table>,
    );
    fireEvent.click(screen.getByRole("button", { name: /name/i }));
    expect(got).toBe("name");
  });
});

describe("SkeletonRows (P6-1)", () => {
  it("renders the requested number of row placeholders", () => {
    const { container } = render(<SkeletonRows count={5} />);
    expect(container.querySelectorAll(".animate-pulse")).toHaveLength(5);
  });

  it("marks the placeholders aria-hidden", () => {
    const { container } = render(<SkeletonRows count={2} />);
    container.querySelectorAll(".animate-pulse").forEach((el) => {
      expect(el).toHaveAttribute("aria-hidden", "true");
    });
  });
});
