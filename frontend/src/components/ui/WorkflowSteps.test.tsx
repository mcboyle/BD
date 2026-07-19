import { describe, it, expect } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { WorkflowSteps } from "./WorkflowSteps";

// Slice 4d: the shared workflow step-indicator. Extracted from AddSiteWizard's
// local Stepper so every multi-step flow shows the same progress affordance.
// Accessible: the list is labelled, and the active step carries
// aria-current="step".

describe("WorkflowSteps (Slice 4d)", () => {
  it("renders every step label", () => {
    render(<WorkflowSteps steps={["About", "Selectors", "Confirm"]} current={1} />);
    for (const s of ["About", "Selectors", "Confirm"]) {
      expect(screen.getByText(s)).toBeInTheDocument();
    }
  });

  it("marks the current step with aria-current", () => {
    render(<WorkflowSteps steps={["About", "Selectors", "Confirm"]} current={1} />);
    const current = screen.getByText("Selectors").closest("li");
    expect(current).toHaveAttribute("aria-current", "step");
    expect(screen.getByText("About").closest("li")).not.toHaveAttribute("aria-current");
  });

  it("exposes an accessible progress label", () => {
    render(
      <WorkflowSteps
        steps={["A", "B"]}
        current={0}
        ariaLabel="Capture progress"
      />,
    );
    expect(screen.getByRole("list", { name: "Capture progress" })).toBeInTheDocument();
  });
});
