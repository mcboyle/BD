import { describe, it, expect } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { KPICard } from "./KPICard";
import type { KPISpec } from "@/lib/widgetCatalog";

// Behavioral coverage for the generic KPI renderer's null/empty handling.
// Replaces the legacy widgets.js cpu_ram null-guard source-grep
// (test_u47_widgets_data_psutil.py), which only proved the guard *text*
// existed. This proves the render contract: null/empty value -> em-dash
// placeholder, real value passes through, missing metric never throws.
function makeSpec(partial: Partial<KPISpec>): KPISpec {
  return { label: "CPU / RAM", value: null, ...partial };
}

describe("KPICard null/empty value handling", () => {
  it("renders an em-dash placeholder when value is null", () => {
    render(<KPICard spec={makeSpec({ value: null })} />);
    expect(screen.getByText("\u2014")).toBeInTheDocument();
  });

  it("renders an em-dash placeholder when value is an empty string", () => {
    render(<KPICard spec={makeSpec({ value: "" })} />);
    expect(screen.getByText("\u2014")).toBeInTheDocument();
  });

  it("renders the real value when present (no placeholder)", () => {
    render(<KPICard spec={makeSpec({ value: "42%" })} />);
    expect(screen.getByText("42%")).toBeInTheDocument();
    expect(screen.queryByText("\u2014")).not.toBeInTheDocument();
  });

  it("always renders the label", () => {
    render(<KPICard spec={makeSpec({ label: "Workers", value: "8" })} />);
    expect(screen.getByText("Workers")).toBeInTheDocument();
  });
});

// Carries forward the legacy app.js HTML-escaping source-greps retired in
// the P4-A app.js tranche (test_v3_43_47_audit.py escape family,
// `escapeHtml` greps in quick_wins/rate_limit/template_extractor/
// multi_provider). Those proved the legacy innerHTML path called an
// escapeHtml() helper. The SPA renders user-controlled strings through
// JSX text children, so escaping is structural — no helper, no innerHTML.
// This proves a markup-bearing value/label/extra is rendered as inert
// text, never parsed into DOM nodes.
describe("KPICard escapes user-controlled text (JSX structural)", () => {
  const XSS = "<img src=x onerror=alert(1)>";

  it("renders a markup-bearing value as literal text, not an element", () => {
    const { container } = render(<KPICard spec={makeSpec({ value: XSS })} />);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(XSS)).toBeInTheDocument();
  });

  it("does not parse markup in the label or extra fields", () => {
    const { container } = render(
      <KPICard
        spec={makeSpec({ label: XSS, value: "1", extra: "<script>x</script>" })}
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
  });
});
