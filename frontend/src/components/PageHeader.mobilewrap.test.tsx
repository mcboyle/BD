import { describe, it, expect } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PageHeader } from "./PageHeader";

// Slice 4c (carried): the /queue PageHeader trailing action row (Add URLs /
// Queue ops / Select / Start all) overflowed at 390px — the trailing slot was
// shrink-0 on a non-wrapping flex row, so a wide action set pushed past the
// viewport with no wrap path. The trailing slot must be able to drop to its own
// full-width row on mobile (so its buttons have room to wrap) while staying
// inline on desktop.

function renderHeader(trailing: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <PageHeader title="Queue" trailing={trailing} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PageHeader trailing mobile-wrap (Slice 4c)", () => {
  it("gives the trailing slot a mobile full-width drop row", () => {
    renderHeader(<button>Add URLs</button>);
    const wrapper = screen.getByText("Add URLs").parentElement!;
    // On mobile the trailing wrapper takes the full width of its own row so a
    // wide action group can wrap instead of overflowing the viewport.
    expect(wrapper.className).toMatch(/max-sm:w-full/);
  });
});
