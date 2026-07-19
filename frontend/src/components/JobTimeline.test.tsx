import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { JobTimeline } from "./JobTimeline";

// Cut 4 — run timeline + classified failure header over /api/runs/<id>/timeline.

function mockTimeline(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve(body) } as Response),
    ),
  );
}
function mount(runId: number) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <JobTimeline runId={runId} />
    </QueryClientProvider>,
  );
}
afterEach(() => vi.unstubAllGlobals());

describe("JobTimeline", () => {
  it("classifies a failed run from its reason_code and lists events", async () => {
    mockTimeline({
      ok: true,
      run: { id: 7, site_id: "s1", url: "http://x/y", status: "failed", reason_code: "auth" },
      events: [
        { id: 1, run_id: 7, ts: "2026-06-23T10:00:00", event_type: "start", detail: "http://x/y" },
        { id: 2, run_id: 7, ts: "2026-06-23T10:01:00", event_type: "finish", detail: "failed" },
      ],
    });
    mount(7);
    // classified header from the auth reason_code
    expect(await screen.findByText(/authentication failed/i)).toBeTruthy();
    expect(screen.getByText(/won't retry/i)).toBeTruthy();
    // events rendered
    expect(screen.getByText("start")).toBeTruthy();
    expect(screen.getByText("finish")).toBeTruthy();
  });
});
