import { describe, it, expect } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { Callout } from "./Callout";

// Slice 5 danger treatment + VISUAL_UNIFICATION callout convergence: one
// Info/Caution/Danger callout look. Soft tinted surface + colored hairline +
// icon + title + body. Presentational — grouping only, no behavior.

describe("Callout (Slice 5)", () => {
  it("renders the title and body", () => {
    render(
      <Callout tone="info" title="Heads up">
        <p>Some explanation.</p>
      </Callout>,
    );
    expect(screen.getByText("Heads up")).toBeInTheDocument();
    expect(screen.getByText("Some explanation.")).toBeInTheDocument();
  });

  it("uses role=note for info/caution and role=alert for danger", () => {
    const { rerender } = render(<Callout tone="caution" title="Careful" />);
    expect(screen.getByRole("note")).toBeInTheDocument();
    rerender(<Callout tone="danger" title="Destructive" />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("applies a tone-specific surface class", () => {
    const { container } = render(<Callout tone="danger" title="x" />);
    // danger uses the red-soft surface token
    expect(container.firstChild).toHaveClass("bg-red-soft");
  });
});
