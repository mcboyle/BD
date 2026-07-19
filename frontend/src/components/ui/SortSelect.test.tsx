import { describe, it, expect } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SortSelect } from "./SortSelect";

// P6-1 (data display) — sort control for non-tabular (<ul>) list pages where
// column headers don't fit. A field <select> + a direction toggle button,
// driving useTableSort.setSort.

const OPTS = [
  { key: "added", label: "Newest" },
  { key: "title", label: "Title" },
  { key: "size", label: "Size" },
];

describe("SortSelect (P6-1)", () => {
  it("renders the field options and a direction toggle", () => {
    render(<SortSelect options={OPTS} sortKey="added" dir="desc" onSet={() => {}} />);
    const sel = screen.getByLabelText(/sort by/i) as HTMLSelectElement;
    expect(sel).toBeInTheDocument();
    expect([...sel.options].map((o) => o.textContent)).toEqual([
      "Newest", "Title", "Size",
    ]);
    expect(screen.getByRole("button", { name: /direction/i })).toBeInTheDocument();
  });

  it("calls onSet with the chosen field (asc) on select change", () => {
    let got: [string | null, string] | null = null;
    render(
      <SortSelect
        options={OPTS}
        sortKey="added"
        dir="desc"
        onSet={(k, d) => (got = [k, d])}
      />,
    );
    fireEvent.change(screen.getByLabelText(/sort by/i), { target: { value: "size" } });
    expect(got).toEqual(["size", "asc"]);
  });

  it("flips direction via the toggle button keeping the field", () => {
    let got: [string | null, string] | null = null;
    render(
      <SortSelect
        options={OPTS}
        sortKey="size"
        dir="asc"
        onSet={(k, d) => (got = [k, d])}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /direction/i }));
    expect(got).toEqual(["size", "desc"]);
  });
});
