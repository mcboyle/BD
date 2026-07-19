import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { QueuePreflightStrip } from "./QueuePreflightStrip";

// Cut 4 — the preflight strip renders the aggregated checks and an overall
// Ready / Not ready headline from GET /api/queue/preflight. Read-only.

function mockPreflight(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(body),
      } as Response),
    ),
  );
}

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <QueuePreflightStrip refetchMs={0} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("QueuePreflightStrip", () => {
  it("shows 'Not ready' and the failing check when ready=false", async () => {
    mockPreflight({
      ok: true,
      ready: false,
      checks: [
        { key: "auth_health", label: "Auth health", status: "fail", detail: "1 site needs re-login" },
        { key: "download_dir", label: "Download directory", status: "ok", detail: "writable" },
      ],
    });
    mount();
    expect(await screen.findByText(/not ready/i)).toBeTruthy();
    expect(screen.getByText("Auth health")).toBeTruthy();
    expect(screen.getByText("Download directory")).toBeTruthy();
  });

  it("shows 'Ready to run' when ready=true", async () => {
    mockPreflight({
      ok: true,
      ready: true,
      checks: [
        { key: "auth_health", label: "Auth health", status: "ok", detail: "all healthy" },
      ],
    });
    mount();
    expect(await screen.findByText(/ready to run/i)).toBeTruthy();
  });
});
