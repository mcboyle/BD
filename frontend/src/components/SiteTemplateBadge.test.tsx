import { describe, it, expect } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";

// Cut 6.7 — site template badge. Renders a small badge naming the site's
// template when one is set; renders nothing when the site has no template.
import { SiteTemplateBadge } from "./SiteTemplateBadge";

describe("SiteTemplateBadge (Cut 6.7)", () => {
  it("renders the template name when present", () => {
    render(<SiteTemplateBadge templateName="gallery-v3" />);
    expect(screen.getByText(/gallery-v3/)).toBeInTheDocument();
  });

  it("renders nothing when there is no template", () => {
    const { container } = render(<SiteTemplateBadge templateName={null} />);
    expect(container.firstChild).toBeNull();
  });
});
