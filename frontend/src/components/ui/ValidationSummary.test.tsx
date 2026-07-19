import { describe, it, expect, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ValidationSummary } from "./ValidationSummary";

// Cut 5 — ValidationSummary: a top-of-page rollup of active field validation
// errors. Inert when there are none; lists each error and jumps to its field.

describe("ValidationSummary (Cut 5)", () => {
  it("renders nothing when there are no problems", () => {
    const { container } = render(<ValidationSummary problems={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("lists each problem with a count", () => {
    render(
      <ValidationSummary
        problems={[
          { field: "rate_limit_domain_overrides", label: "Domain overrides", message: "Invalid JSON" },
          { field: "path_allowlist", label: "Path allowlist", message: "Not absolute" },
        ]}
      />
    );
    expect(screen.getByText(/2/)).toBeInTheDocument();
    expect(screen.getByText(/invalid json/i)).toBeInTheDocument();
    expect(screen.getByText(/not absolute/i)).toBeInTheDocument();
  });

  it("invokes onJump(field) when a problem is clicked", () => {
    const onJump = vi.fn();
    render(
      <ValidationSummary
        problems={[{ field: "path_allowlist", label: "Path allowlist", message: "Not absolute" }]}
        onJump={onJump}
      />
    );
    fireEvent.click(screen.getByText(/path allowlist/i));
    expect(onJump).toHaveBeenCalledWith("path_allowlist");
  });
});
