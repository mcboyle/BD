import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter, Route, Routes } from "react-router";

import { SiteRow } from "@/components/SiteRow";
import type { SiteEntryV2 } from "@/lib/api-types";
import { DEFAULT_WIDGET_IDS } from "@/lib/widgetCatalog";
import { Home } from "./Home";
import { Maintenance } from "./Maintenance";
import { SiteDetail } from "./SiteDetail";

// O1826 BRIEF-27 (TECH_DEBT M215 / M218 / M200).
//  M215 Home's Reset button compared the selection length with a literal 4
//       while the catalog default holds five widgets.
//  M218 Maintenance's Status panel turned every failed GET into null.
//  M200 every SiteRow mounted ReadinessBadge, one readiness GET per row.

function respond(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

function urlOf(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input.toString();
}

function mount(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("M215 Home Reset button", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = urlOf(input);
        if (url.includes("/api/dashboard/v2/sparkline")) {
          return Promise.resolve(respond(200, { ok: true, current: 0, history: [], ts: 0 }));
        }
        if (url.includes("/api/dashboard/v2")) {
          return Promise.resolve(respond(200, {
            ok: true, attention: [], by_site: [], today: { done: 0, running: 0, failed: 0 },
            active_workers: 0, workers_active: 0, workers_total: 1, sites_count: 1, ts: 0,
          }));
        }
        return Promise.resolve(respond(200, { ok: true }));
      }),
    );
  });

  async function resetButton() {
    mount(<Home />);
    fireEvent.click(await screen.findByRole("button", { name: "Customize dashboard" }));
    return screen.getByRole("button", {
      name: "Reset dashboard layout and widget selection to defaults",
    });
  }

  it("is disabled at the default five-widget selection", async () => {
    expect(DEFAULT_WIDGET_IDS, "M215-DEFAULT-COUNT").toHaveLength(5);
    expect(await resetButton(), "M215-RESET-ENABLED-AT-DEFAULTS").toBeDisabled();
  });

  it("stays enabled for a custom four-widget selection", async () => {
    window.localStorage.setItem(
      "bd-widget-selection",
      JSON.stringify(DEFAULT_WIDGET_IDS.slice(0, 4)),
    );
    expect(await resetButton(), "M215-NEG-FOUR-CUSTOM-NOT-RESETTABLE").toBeEnabled();
  });

  it("stays enabled for a custom selection of default length", async () => {
    window.localStorage.setItem(
      "bd-widget-selection",
      JSON.stringify([...DEFAULT_WIDGET_IDS].reverse()),
    );
    expect(await resetButton(), "M215-NEG-REORDERED-NOT-RESETTABLE").toBeEnabled();
  });

  it("stays enabled for a dragged layout with the default widgets", async () => {
    window.localStorage.setItem(
      "bd-dashboard-layout",
      JSON.stringify({ lg: [{ i: "attention", x: 0, y: 0, w: 6, h: 4 }] }),
    );
    expect(await resetButton(), "M215-NEG-DRAGGED-LAYOUT-NOT-RESETTABLE").toBeEnabled();
  });
});

describe("M225 SiteDetail KPI tiles (Home's buildKpiTiles)", () => {
  function siteRow() {
    return {
      site_id: "bk", name: "blacked", avatar_color: "#000", state: "idle", hold_reason: "",
      auth_state: "ok", captcha_pending: false, awaiting_manual_login: false,
      downloaded_total: 0, active_workers: 0, last_event_ts: 0, last_event_age: "",
    };
  }

  it("renders a selected catalog KPI with its value and an edit-mode drag handle", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = urlOf(input);
        if (url.startsWith("/api/widgets/data")) {
          return Promise.resolve(respond(200, { ok: true, data: { done_today: 4242 }, ts: 0 }));
        }
        if (url === "/api/widgets/bk") {
          return Promise.resolve(respond(200, { scope: "site", widgets: [{ id: "done_today", size: "sm" }] }));
        }
        if (url === "/api/sites/v2") {
          return Promise.resolve(respond(200, { ok: true, sites: [siteRow()], count: 1, ts: 0 }));
        }
        return Promise.resolve(respond(200, {}));
      }),
    );
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/sites/bk"]}>
          <Routes><Route path="/sites/:siteId" element={<SiteDetail />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const value = await screen.findByText("4242");
    const tile = value.closest(".dashboard-tile-wrap");
    expect(tile, "M225-KPI-TILE-NOT-RENDERED").not.toBeNull();
    expect(tile, "M225-KPI-TILE-WRONG-WIDGET").toHaveTextContent("Done today");
    expect(tile!.querySelector(".dashboard-tile-handle"), "M225-HANDLE-OUTSIDE-EDIT-MODE").toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Customize dashboard" }));
    expect(
      screen.getByText("4242").closest(".dashboard-tile-wrap")!.querySelector(".dashboard-tile-handle"),
      "M225-KPI-TILE-NO-DRAG-HANDLE",
    ).not.toBeNull();
  });
});

describe("M218 Maintenance Status panel", () => {
  it("labels a failed status GET instead of rendering null", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = urlOf(input);
        if (url.includes("/api/auth_health/status")) return Promise.resolve(respond(503, {}));
        if (url.includes("/api/selector_drift/status")) return Promise.resolve(respond(200, { drift: 0 }));
        if (url.includes("/api/daily_budget/status")) return Promise.resolve(respond(200, null));
        return new Promise<Response>(() => {});
      }),
    );
    mount(<Maintenance />);
    await screen.findByText(/"drift": 0/);
    const failed = screen.queryByText(/Unavailable: GET \/api\/auth_health\/status → 503/);
    expect(failed, "M218-FAILED-GET-NOT-LABELLED").not.toBeNull();
    expect(failed, "M218-LABEL-NOT-ALERT").toHaveAttribute("role", "alert");
    // A source that answered with an empty payload is not an error.
    expect(
      screen.queryAllByText(/^Unavailable:/),
      "M218-NEG-EMPTY-PAYLOAD-LABELLED-AS-ERROR",
    ).toHaveLength(1);
  });
});

describe("M200 SiteRow readiness fetch", () => {
  const ROWS = 50;
  let observers: { cb: IntersectionObserverCallback; el: Element[] }[];

  function ioEntry(target: Element, isIntersecting: boolean): IntersectionObserverEntry {
    return { isIntersecting, target } as unknown as IntersectionObserverEntry;
  }

  function site(i: number): SiteEntryV2 {
    return {
      site_id: `s${i}`, name: `site ${i}`, avatar_color: "#123456", state: "idle",
      auth_state: "unknown", captcha_pending: false, downloaded_total: 0,
      active_workers: 0, last_event_ts: 0, last_event_age: "",
    };
  }

  function stubFetch() {
    const fetchMock = vi.fn((_input: RequestInfo | URL) =>
      Promise.resolve(respond(200, { ok: true, checks: [] })),
    );
    vi.stubGlobal("fetch", fetchMock);
    return () => fetchMock.mock.calls.filter(([input]) => /\/readiness$/.test(urlOf(input))).length;
  }

  function mountRows() {
    mount(<>{Array.from({ length: ROWS }, (_, i) => <SiteRow key={i} site={site(i)} />)}</>);
  }

  beforeEach(() => {
    observers = [];
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        entry: { cb: IntersectionObserverCallback; el: Element[] };
        constructor(cb: IntersectionObserverCallback) {
          this.entry = { cb, el: [] };
          observers.push(this.entry);
        }
        observe(el: Element) {
          this.entry.el.push(el);
          // A real observer reports each observed target once, right away,
          // with its current state: an off-screen row arrives as
          // isIntersecting:false.
          queueMicrotask(() => {
            if (!this.entry.el.includes(el)) return;
            this.entry.cb(
              [ioEntry(el, false)],
              this as unknown as IntersectionObserver,
            );
          });
        }
        disconnect() { this.entry.el = []; }
        unobserve() {}
        takeRecords() { return []; }
      },
    );
  });

  it("issues no readiness GET for rows that are not visible", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const readinessGets = stubFetch();
    mountRows();
    // A deferred or debounced per-row fetch must not slip past the check.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(readinessGets(), "M200-OFFSCREEN-ROWS-FETCHED").toBe(0);
  });

  it("issues readiness GETs only for the rows that scroll into view", async () => {
    const readinessGets = stubFetch();
    mountRows();
    // Let the initial isIntersecting:false reports land first.
    await act(async () => {
      await Promise.resolve();
    });
    const rows = [0, 1, 2].map((i) => screen.getByRole("button", { name: `Open site ${i}` }));
    const watching = rows.map((row) => observers.filter((o) => o.el.includes(row)));
    expect(watching.map((w) => w.length), "M200-ROW-NOT-OBSERVED").toEqual([1, 1, 1]);
    act(() => {
      rows.forEach((row, i) => {
        watching[i][0].cb(
          [ioEntry(row, true)],
          {} as IntersectionObserver,
        );
      });
    });
    await waitFor(() => expect(readinessGets(), "M200-VISIBLE-ROWS-NOT-FETCHED").toBe(3));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(readinessGets(), "M200-OFFSCREEN-ROWS-FETCHED-AFTER-SCROLL").toBe(3);
  });

  it("fetches per row when IntersectionObserver is unavailable", async () => {
    vi.stubGlobal("IntersectionObserver", undefined);
    const readinessGets = stubFetch();
    mountRows();
    await waitFor(() => expect(readinessGets(), "M200-NEG-NO-IO-FALLBACK").toBe(ROWS));
  });
});
