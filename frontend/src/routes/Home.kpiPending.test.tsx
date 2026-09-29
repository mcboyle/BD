import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";

import { Home } from "./Home";

// dl95-app-B6-2-live-1 (harness-work/DOT95-LANE/live-dl95-app-B6-2/LIVE-RESULT-B6-B.md#TILES).
// On test2 under load, GET /api/widgets/data took 17-69 s (journal "slow request" 00:40-00:41Z) while
// /api/dashboard/v2 answered at once. A Home load in that window drew ACTIVITY with data and the KPI tiles
// as "--" (DONE TODAY / THROUGHPUT / QUEUE DEPTH / DISK FREE) and ACTION REQUIRED as "0": the tiles
// rendered the empty pre-first-response object as if it were the measurement. Until the first snapshot
// lands a tile must say it is loading, never "--" or a fabricated 0; once a snapshot lands, a failed
// or slow poll must not blank it again.

const KPI_IDS = ["done_today", "throughput", "queue_depth", "disk_free", "action_req"];
const KPI_LABELS = ["Done today", "Throughput", "Queue depth", "Disk free", "Action required"];
const SNAPSHOT = {
  done_today: 7,
  throughput_fmt: "5.6 MB/s",
  queue_depth: 18,
  disk_free_fmt: "1.5 TB",
  action_req: 6,
};

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status < 400,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

const DASHBOARD = {
  ok: true,
  attention: [],
  by_site: [{
    site_id: "site-1", name: "Example Site", avatar_color: "#123456",
    queued: 18, running: 16, today_done: 7,
  }],
  today: { done: 7, running: 16, failed: 0 },
  active_workers: 16, workers_active: 16, workers_total: 120, sites_count: 1, ts: 0,
};

// widgetReplies: one entry per /api/widgets/data call, in order; the last one repeats.
function mockFetch(widgetReplies: Array<() => Promise<Response>>) {
  let n = 0;
  return vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/dashboard/v2/sparkline")) {
      return Promise.resolve(jsonResponse({ ok: true, current: 0, history: [], ts: 0 }));
    }
    if (url.includes("/api/dashboard/v2")) return Promise.resolve(jsonResponse(DASHBOARD));
    if (url.includes("/api/widgets/data")) {
      const reply = widgetReplies[Math.min(n, widgetReplies.length - 1)];
      n += 1;
      return reply();
    }
    if (url.includes("/api/queue/v2")) {
      return Promise.resolve(jsonResponse({ ok: true, running: [], waiting: [], done_today_count: 7, ts: 0 }));
    }
    if (url.includes("/api/history")) return Promise.resolve(jsonResponse([]));
    return Promise.resolve(jsonResponse({ ok: true }));
  });
}

function mountHome() {
  window.localStorage.setItem("bd-widget-selection", JSON.stringify(KPI_IDS));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Home />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// The KPI card whose eyebrow is exactly `label` (the legacy sparkline's "Throughput · last hour"
// heading is a different string).
function kpiCard(label: string): HTMLElement {
  const eyebrow = screen.getAllByText(label, { exact: true })
    .find((el) => el.classList.contains("eyebrow"));
  if (!eyebrow?.parentElement) throw new Error(`no KPI card labelled ${label}`);
  return eyebrow.parentElement;
}

afterEach(() => {
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

describe("dl95-app-B6-2-live-1: Home KPI tiles while /api/widgets/data is slow", () => {
  it("shows a loading state, not '--' or a fabricated 0, until the first snapshot lands", async () => {
    let release: (r: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => { release = resolve; });
    vi.stubGlobal("fetch", mockFetch([() => pending]));

    mountHome();
    // ACTIVITY (dashboard/v2) has data while the widget snapshot is still in flight.
    expect(await screen.findByText("By site", { exact: true })).toBeInTheDocument();

    for (const label of KPI_LABELS) {
      const card = kpiCard(label);
      expect(card.textContent, label).not.toContain("—");
      expect(card, label).toHaveAttribute("aria-busy", "true");
    }
    expect(within(kpiCard("Action required")).queryByText("0")).toBeNull();

    release(jsonResponse({ ok: true, data: SNAPSHOT, ts: 1 }));

    await waitFor(() => expect(kpiCard("Done today")).toHaveTextContent("7"));
    expect(kpiCard("Throughput")).toHaveTextContent("5.6 MB/s");
    expect(kpiCard("Queue depth")).toHaveTextContent("18");
    expect(kpiCard("Disk free")).toHaveTextContent("1.5 TB");
    expect(kpiCard("Action required")).toHaveTextContent("6");
    for (const label of KPI_LABELS) {
      expect(kpiCard(label), label).not.toHaveAttribute("aria-busy");
    }
  });

  it("keeps the last snapshot when a later poll fails", async () => {
    const fetchMock = mockFetch([
      () => Promise.resolve(jsonResponse({ ok: true, data: SNAPSHOT, ts: 1 })),
      () => Promise.resolve(jsonResponse({ ok: false, error: "over budget" }, 503)),
    ]);
    vi.stubGlobal("fetch", fetchMock);

    mountHome();
    await waitFor(() => expect(kpiCard("Done today")).toHaveTextContent("7"));

    // Busy snapshot (queue_depth > 0) polls every 3 s; wait for the failing second poll.
    const widgetCalls = () => fetchMock.mock.calls
      .filter(([u]) => String(u).includes("/api/widgets/data")).length;
    await waitFor(() => expect(widgetCalls()).toBeGreaterThanOrEqual(2), { timeout: 6000 });
    await new Promise((r) => setTimeout(r, 50));

    expect(kpiCard("Done today")).toHaveTextContent("7");
    expect(kpiCard("Queue depth")).toHaveTextContent("18");
    expect(kpiCard("Disk free")).toHaveTextContent("1.5 TB");
    expect(kpiCard("Done today").textContent).not.toContain("—");
  }, 10_000);
});
