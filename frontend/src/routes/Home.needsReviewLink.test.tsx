import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";

import { Home } from "./Home";
import { CountTiles } from "@/components/CountTiles";

// O1671 r8b f001 (harness-work/O1670/R4-s1-1754.md DELTA 1): the Home
// "NEEDS ATTENTION > N Needs review" link and the CountTiles review tile
// pointed at /cockpit/review. There is no such client route (App.tsx path="*"
// navigates to "/") and Flask 404s it directly, so the click bounced home.
// The review list lives at /needs-review (App.tsx <Route path="/needs-review">).

const REVIEW_ROUTE = "/needs-review";

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

const DASHBOARD = {
  ok: true,
  attention: [
    {
      site_id: "site-1",
      name: "Example Site",
      kind: "captcha_pending",
      label: "Captcha pending",
      since_ts: 0,
    },
  ],
  by_site: [{
    site_id: "site-1", name: "Example Site", avatar_color: "#123456",
    queued: 0, running: 0, today_done: 0,
  }],
  today: { done: 0, running: 0, failed: 0 },
  active_workers: 0, workers_active: 0, workers_total: 0, sites_count: 1, ts: 0,
};

function mockFetch() {
  return vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/dashboard/v2/sparkline")) {
      return Promise.resolve(jsonResponse({ ok: true, current: 0, history: [], ts: 0 }));
    }
    if (url.includes("/api/dashboard/v2")) return Promise.resolve(jsonResponse(DASHBOARD));
    if (url.includes("/api/queue/v2")) {
      return Promise.resolve(jsonResponse({ ok: true, running: [], waiting: [], done_today_count: 0, ts: 0 }));
    }
    if (url.includes("/api/history")) return Promise.resolve(jsonResponse([]));
    return Promise.resolve(jsonResponse({ ok: true }));
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("Needs review links open the review list", () => {
  it("Home NEEDS ATTENTION 'Needs review' row links to /needs-review", async () => {
    vi.stubGlobal("fetch", mockFetch());
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <Home />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const link = await screen.findByRole("link", { name: /needs review/i });
    expect(link.getAttribute("href")).toBe(REVIEW_ROUTE);
  });

  it("CountTiles review tile links to /needs-review", () => {
    render(
      <MemoryRouter>
        <CountTiles counts={{ queue: 0, review: 4, capture: 0, template: 0 }} />
      </MemoryRouter>,
    );
    const tile = screen.getByRole("link", { name: /review/i });
    expect(tile.getAttribute("href")).toBe(REVIEW_ROUTE);
  });

  it("the other tiles keep their routes", () => {
    render(
      <MemoryRouter>
        <CountTiles counts={{ queue: 1, review: 0, capture: 0, template: 0 }} />
      </MemoryRouter>,
    );
    const hrefs = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["/queue", REVIEW_ROUTE, "/capture", "/templates"]);
  });
});
