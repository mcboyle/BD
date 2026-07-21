import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { LEGACY_WIDGET_IDS } from "@/hooks/useDashboardLayout";
import { WIDGETS } from "@/lib/widgetCatalog";
import { Home } from "./Home";

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

function mockDashboardFetch() {
  return vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();

    if (url.includes("/api/dashboard/v2/sparkline")) {
      return Promise.resolve(jsonResponse({ ok: true, current: 0, history: [], ts: 0 }));
    }
    if (url.includes("/api/dashboard/v2")) {
      return Promise.resolve(jsonResponse({
        ok: true,
        attention: [{
          site_id: "site-1",
          name: "Example Site",
          kind: "captcha_pending",
          label: "Captcha pending",
          since_ts: 0,
        }],
        by_site: [{
          site_id: "site-1",
          name: "Example Site",
          avatar_color: "#123456",
          queued: 0,
          running: 0,
          today_done: 1,
        }],
        today: { done: 1, running: 0, failed: 0 },
        active_workers: 0,
        workers_active: 0,
        workers_total: 1,
        sites_count: 1,
        ts: 0,
      }));
    }
    if (url.includes("/api/widgets/data")) {
      return Promise.resolve(jsonResponse({ ok: true, data: {}, ts: 0 }));
    }
    if (url.includes("/api/queue/v2")) {
      return Promise.resolve(jsonResponse({
        ok: true,
        running: [],
        waiting: [],
        done_today_count: 1,
        ts: 0,
      }));
    }
    if (url.includes("/api/history")) {
      return Promise.resolve(jsonResponse([{ id: 1, status: "done" }]));
    }
    return Promise.resolve(jsonResponse({ ok: true }));
  });
}

function mountHome() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Home />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

describe("Home dashboard widget coverage", () => {
  it("renders all 36 selected catalog widgets and all five legacy widgets", async () => {
    window.localStorage.clear();
    window.localStorage.setItem(
      "bd-widget-selection",
      JSON.stringify(WIDGETS.map((widget) => widget.id)),
    );
    vi.stubGlobal("fetch", mockDashboardFetch());

    mountHome();

    expect(await screen.findByText("By site", { exact: true })).toBeInTheDocument();
    for (const widget of WIDGETS) {
      expect(
        screen.getAllByText(widget.spec({}).label, { exact: true }).length,
      ).toBeGreaterThan(0);
    }

    fireEvent.click(screen.getByRole("button", { name: "Customize dashboard" }));
    await waitFor(() => {
      expect(
        screen.getAllByRole("button", { name: /Drag to reorder .* tile/ }),
      ).toHaveLength(WIDGETS.length + LEGACY_WIDGET_IDS.length);
    });
    for (const id of LEGACY_WIDGET_IDS) {
      expect(
        screen.getByRole("button", { name: `Drag to reorder ${id} tile` }),
      ).toBeInTheDocument();
    }
  });

  it("updates the rendered tiles when the picker changes the selection", async () => {
    window.localStorage.clear();
    vi.stubGlobal("fetch", mockDashboardFetch());

    mountHome();
    expect(await screen.findByText("By site", { exact: true })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Customize dashboard" }));
    fireEvent.click(screen.getByRole("button", { name: "Open widget library" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Add Top studio to dashboard" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    expect(await screen.findByText("Top studio", { exact: true })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Drag to reorder lib_top_studio tile" }),
    ).toBeInTheDocument();
  });
});
