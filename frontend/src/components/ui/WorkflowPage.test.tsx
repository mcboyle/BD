import { describe, it, expect } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { WorkflowPage } from "./WorkflowPage";

// Cut 1 substrate: layout scaffold with named slots (purpose / inputs / plan /
// danger / result). Form-heavy pages become config, not bespoke layout. Cut 1
// lands the scaffold only — no page adopts it yet.

describe("WorkflowPage", () => {
  it("renders every provided slot", () => {
    render(
      <WorkflowPage
        purpose={<p>why this page exists</p>}
        inputs={<div>the inputs</div>}
        plan={<div>the preview</div>}
        danger={<div>the danger block</div>}
        result={<div>the result</div>}
      />,
    );
    expect(screen.getByText("why this page exists")).toBeInTheDocument();
    expect(screen.getByText("the inputs")).toBeInTheDocument();
    expect(screen.getByText("the preview")).toBeInTheDocument();
    expect(screen.getByText("the danger block")).toBeInTheDocument();
    expect(screen.getByText("the result")).toBeInTheDocument();
  });

  it("omits slots that are not provided (no empty wrappers)", () => {
    const { container } = render(
      <WorkflowPage purpose={<p>only purpose</p>} inputs={<div>inputs</div>} />,
    );
    expect(screen.getByText("only purpose")).toBeInTheDocument();
    // The danger/result/plan slots should not render any element.
    expect(container.querySelector('[data-slot="danger"]')).toBeNull();
    expect(container.querySelector('[data-slot="result"]')).toBeNull();
    expect(container.querySelector('[data-slot="plan"]')).toBeNull();
  });
});
