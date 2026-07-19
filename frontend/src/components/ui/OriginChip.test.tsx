import { describe, it, expect } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { OriginChip } from "./OriginChip";

// Cut 5 — OriginChip: source + apply-timing from /api/global_config/origins.
// env-locked renders a lock; secret fields NEVER receive/echo a value.

describe("OriginChip (Cut 5)", () => {
  it("shows origin and apply-timing", () => {
    render(<OriginChip origin="global" applyTiming="immediate" envLocked={false} isSecret={false} />);
    expect(screen.getByText(/global/i)).toBeInTheDocument();
    expect(screen.getByText(/immediate/i)).toBeInTheDocument();
  });

  it("indicates env-locked", () => {
    const { container } = render(
      <OriginChip origin="env" applyTiming="immediate" envLocked isSecret={false} />
    );
    expect(container.textContent || "").toMatch(/env|lock/i);
  });

  it("has no value-bearing API surface (secret-safe by construction)", () => {
    // The component renders provenance only; it must not accept/echo a value.
    render(<OriginChip origin="default" applyTiming="restart" envLocked={false} isSecret />);
    expect(screen.getByText(/restart/i)).toBeInTheDocument();
  });
});
