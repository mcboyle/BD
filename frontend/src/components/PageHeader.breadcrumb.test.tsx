import { describe, it, expect } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PageHeader } from "./PageHeader";

// Cut A (Cut 1 adoption remainder) — PageHeader gains `backTo` + `breadcrumb`
// props so drill-in routes (Site › Site › Payload actions, Logs › Diff) carry a
// back affordance + trail in the header chrome itself, in BOTH compact and
// display variants. Presentational/navigational only — no gating, no new route.

function renderHeader(props: Partial<React.ComponentProps<typeof PageHeader>>) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <PageHeader title="Payload actions" {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PageHeader backTo + breadcrumb (Cut A)", () => {
  it("renders a back link to the given target with an accessible label", () => {
    renderHeader({ backTo: { to: "/sites/acme", label: "Back to site" } });
    const back = screen.getByRole("link", { name: "Back to site" });
    expect(back).toBeInTheDocument();
    expect(back).toHaveAttribute("href", "/sites/acme");
  });

  it("falls back to a default 'Back' label when none is given", () => {
    renderHeader({ backTo: { to: "/queue" } });
    expect(screen.getByRole("link", { name: /back/i })).toHaveAttribute(
      "href",
      "/queue",
    );
  });

  it("renders the breadcrumb trail when provided", () => {
    renderHeader({
      breadcrumb: <span>Sites › Acme › Payload actions</span>,
    });
    expect(
      screen.getByText("Sites › Acme › Payload actions"),
    ).toBeInTheDocument();
  });

  it("renders backTo in the display variant too", () => {
    renderHeader({
      variant: "display",
      backTo: { to: "/sites/acme", label: "Back to site" },
    });
    expect(
      screen.getByRole("link", { name: "Back to site" }),
    ).toHaveAttribute("href", "/sites/acme");
  });

  it("renders no back link when backTo is absent (unchanged callers)", () => {
    renderHeader({});
    expect(screen.queryByRole("link", { name: /back/i })).toBeNull();
  });
});
