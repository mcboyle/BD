import { describe, it, expect } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { DangerZone } from "./DangerZone";

// Polish pass item 3 — Danger Zone groups destructive controls behind a
// red-accented frame + warning. Presentational/grouping only.

describe("DangerZone", () => {
  it("renders the default title, a warning, and its children", () => {
    render(
      <DangerZone warning="These actions cannot be undone.">
        <button>Delete everything</button>
      </DangerZone>,
    );
    expect(screen.getByText("Danger zone")).toBeInTheDocument();
    expect(screen.getByText("These actions cannot be undone.")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Delete everything" }),
    ).toBeInTheDocument();
  });

  it("accepts a title override and exposes it as the region label", () => {
    render(
      <DangerZone title="Payload actions">
        <span>controls</span>
      </DangerZone>,
    );
    expect(
      screen.getByRole("region", { name: "Payload actions" }),
    ).toBeInTheDocument();
  });
});
