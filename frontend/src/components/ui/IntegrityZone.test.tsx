import { describe, it, expect } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { IntegrityZone } from "./IntegrityZone";

// Cut 1 substrate: amber (NOT red) grouping container for capture/redaction
// integrity controls. Presentational + grouping only — never lowers a guard or
// weakens redaction. Distinct from DangerZone (red/destructive).

describe("IntegrityZone", () => {
  it("renders a default title, a note, and its children", () => {
    render(
      <IntegrityZone note="These settings protect capture integrity.">
        <button>Toggle redaction</button>
      </IntegrityZone>,
    );
    expect(screen.getByText("Integrity")).toBeInTheDocument();
    expect(
      screen.getByText("These settings protect capture integrity."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Toggle redaction" }),
    ).toBeInTheDocument();
  });

  it("exposes a title override as the region label", () => {
    render(
      <IntegrityZone title="Redaction & capture integrity">
        <span>controls</span>
      </IntegrityZone>,
    );
    expect(
      screen.getByRole("region", { name: "Redaction & capture integrity" }),
    ).toBeInTheDocument();
  });
});
